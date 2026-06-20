"""
Tests for the governance membrane FLOOR signal (`p1` → Lane.BLOCKED).

The floor rule (mirrors detector._detect_missing_decision_logs): a change is a
floor violation when it is a BREAKING CHANGE (carries the ``breaking-change``
label) AND has NO decision log in Confluence referencing it.

Coverage:
  (a) the detector helper `is_floor_violation` — True for breaking + no-log,
      False for non-breaking or breaking-with-a-log;
  (b) the webhook sets metadata["p1"]=True on the emitted code.merged event(s)
      for a merged breaking-change PR with no decision log, and does NOT set it
      for a normal merge or a breaking change that HAS a decision log;
  (c) end-to-end: a p1 event routes to Lane.BLOCKED via EventRouter.route_lane.

Hermetic: SYNCBOT_TEST=1 (set in conftest), local providers, no Slack, no network.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.agent.detector import is_floor_violation, BREAKING_CHANGE_LABEL
from src.core.schemas import ConfluencePage, DecisionLog


# ---------------------------------------------------------------------------
# Builders + a minimal fake Confluence (only `.search_pages` is exercised)
# ---------------------------------------------------------------------------

def _decision_log(key: str) -> DecisionLog:
    return DecisionLog(
        id=f"DL-{key}",
        title=f"Decision: {key}",
        decision="Ship it, here is why.",
        rationale="Weighed the alternatives.",
        alternatives_considered=["do nothing"],
        decided_by=["alice"],
        date=date(2026, 5, 1),
        status="approved",
        related_tickets=[key],
        related_components=["login"],
        team="Team Phoenix",
    )


def _page(key: str, *, with_log: bool) -> ConfluencePage:
    return ConfluencePage(
        id=f"PG-{key}",
        title=f"Notes on {key}",
        space="ENG",
        team="Team Phoenix",
        content_summary=f"Discussion of {key}",
        tags=[key.lower()],
        last_updated=date(2026, 5, 2),
        author="alice",
        url=f"https://confluence/{key}",
        decision_log=_decision_log(key) if with_log else None,
    )


class FakeConfluence:
    """Returns the given pages for ANY search_key (the helper filters by .decision_log)."""

    def __init__(self, pages: list[ConfluencePage]):
        self._pages = pages

    def search_pages(self, query, team=None):  # noqa: ANN001
        return list(self._pages)


# ---------------------------------------------------------------------------
# (a) detector helper — is_floor_violation
# ---------------------------------------------------------------------------

class TestIsFloorViolation:
    def test_breaking_change_no_log_is_violation(self):
        conf = FakeConfluence([_page("PHX-102", with_log=False)])
        assert is_floor_violation(["breaking-change", "infra"], "PHX-102", conf) is True

    def test_breaking_change_no_pages_at_all_is_violation(self):
        conf = FakeConfluence([])
        assert is_floor_violation(["breaking-change"], "PHX-102", conf) is True

    def test_breaking_change_with_decision_log_is_not_violation(self):
        conf = FakeConfluence([_page("PHX-102", with_log=True)])
        assert is_floor_violation(["breaking-change"], "PHX-102", conf) is False

    def test_non_breaking_change_is_not_violation(self):
        # No breaking-change label → never a floor violation, even with no logs.
        conf = FakeConfluence([_page("PHX-102", with_log=False)])
        assert is_floor_violation(["infra", "q3"], "PHX-102", conf) is False

    def test_breaking_change_with_one_logged_page_among_many_is_not_violation(self):
        conf = FakeConfluence([
            _page("PHX-102", with_log=False),
            _page("PHX-102b", with_log=True),
        ])
        assert is_floor_violation(["breaking-change"], "PHX-102", conf) is False

    def test_label_constant_is_breaking_change(self):
        assert BREAKING_CHANGE_LABEL == "breaking-change"


# ---------------------------------------------------------------------------
# (c) end-to-end: a p1 event routes to Lane.BLOCKED
# ---------------------------------------------------------------------------

class TestP1RoutesToBlocked:
    def test_p1_event_routes_to_blocked_lane(self, providers):
        from src.agent.events import Event, EventRouter
        from src.agent.membrane import Lane

        router = EventRouter(providers)
        event = Event(
            type="code.merged",
            subject="login",
            source="github",
            team="Team Phoenix",
            metadata={"p1": True},
        )
        decision = router.route_lane(event)
        assert decision.lane == Lane.BLOCKED
        assert decision.provenance.passed_floor is False

    def test_no_p1_does_not_route_to_blocked(self, providers):
        from src.agent.events import Event, EventRouter
        from src.agent.membrane import Lane

        router = EventRouter(providers)
        event = Event(
            type="code.merged",
            subject="login",
            source="github",
            team="Team Phoenix",
            metadata={"p1": False},
        )
        decision = router.route_lane(event)
        assert decision.lane != Lane.BLOCKED


# ---------------------------------------------------------------------------
# (b) webhook — sets metadata["p1"] correctly on merged PRs
# ---------------------------------------------------------------------------

_GITHUB_SECRET = "test-github-secret"


def _github_sig(body: bytes, secret: str = _GITHUB_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture(scope="module")
def app(providers):
    import webhook_server as ws

    ws._providers = providers
    ws._seen_delivery_ids.clear()
    return ws.app


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_seen_ids():
    import webhook_server as ws

    ws._seen_delivery_ids.clear()
    yield
    ws._seen_delivery_ids.clear()


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", _GITHUB_SECRET)


@pytest.fixture()
def dispatch_stub(monkeypatch):
    """Capture every Event dispatched (no real Slack)."""
    from src.agent.events import EventRouter

    calls: list = []

    def _stub(self, event):  # noqa: ANN001
        calls.append(event)
        return 1

    monkeypatch.setattr(EventRouter, "dispatch", _stub)
    return calls


@pytest.fixture()
def mock_pr_files(app):
    """Mock providers.github.get_pr_files (local provider lacks it)."""
    import webhook_server as ws

    state: dict = {"files": [], "raise": None}

    def _fake_get_pr_files(owner, repo, number, timeout=5.0):
        if state["raise"] is not None:
            raise state["raise"]
        return state["files"]

    ws._providers.github.get_pr_files = _fake_get_pr_files  # type: ignore[attr-defined]

    def _set(files=None, raise_exc=None):
        state["files"] = files or []
        state["raise"] = raise_exc

    yield _set

    try:
        del ws._providers.github.get_pr_files  # type: ignore[attr-defined]
    except AttributeError:
        pass


@pytest.fixture()
def mock_confluence(app, monkeypatch):
    """Override providers.confluence.search_pages so the floor check is deterministic.

    Call the returned setter with a list of ConfluencePage to make EVERY search
    return them; default is no pages (i.e. no decision log found → floor fires for
    a breaking change).
    """
    import webhook_server as ws

    state: dict = {"pages": []}

    def _fake_search_pages(query, team=None):  # noqa: ANN001
        return list(state["pages"])

    monkeypatch.setattr(ws._providers.confluence, "search_pages", _fake_search_pages)

    def _set(pages=None):
        state["pages"] = pages or []

    return _set


def _merged_pr_payload(*, labels=None, title="feat: implement PKCE [PHX-102]", repo="phoenix-auth") -> dict:
    return {
        "action": "closed",
        "pull_request": {
            "number": 42,
            "title": title,
            "merged": True,
            "merged_by": {"login": "marcus.webb"},
            "labels": [{"name": lab} for lab in (labels or [])],
        },
        "repository": {"name": repo, "owner": {"login": "acme"}, "full_name": f"acme/{repo}"},
    }


def _post(client, payload, delivery_id):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _github_sig(body),
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": delivery_id,
        },
    )


class TestWebhookFloorSignal:
    def test_breaking_change_no_log_sets_p1_true(
        self, client, dispatch_stub, mock_pr_files, mock_confluence
    ):
        mock_pr_files(["src/auth/login/page.tsx"])
        mock_confluence([])  # no decision log anywhere
        payload = _merged_pr_payload(labels=["breaking-change"])
        resp = _post(client, payload, "floor-bc-nolog-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["floor"] is True
        assert data["dispatched"] >= 1
        # p1 set TRUE consistently on every emitted event.
        assert dispatch_stub, "expected at least one dispatched event"
        for event in dispatch_stub:
            assert event.metadata.get("p1") is True

    def test_breaking_change_with_log_does_not_set_p1(
        self, client, dispatch_stub, mock_pr_files, mock_confluence
    ):
        mock_pr_files(["src/auth/login/page.tsx"])
        mock_confluence([_page("PHX-102", with_log=True)])  # decision log exists
        payload = _merged_pr_payload(labels=["breaking-change"])
        resp = _post(client, payload, "floor-bc-log-01")
        assert resp.status_code == 200
        assert resp.json()["floor"] is False
        for event in dispatch_stub:
            assert event.metadata.get("p1") is False

    def test_normal_merge_does_not_set_p1(
        self, client, dispatch_stub, mock_pr_files, mock_confluence
    ):
        mock_pr_files(["src/auth/login/page.tsx"])
        mock_confluence([])
        payload = _merged_pr_payload(labels=["enhancement"])  # not breaking
        resp = _post(client, payload, "floor-normal-01")
        assert resp.status_code == 200
        assert resp.json()["floor"] is False
        for event in dispatch_stub:
            assert event.metadata.get("p1") is False

    def test_p1_set_on_every_component_event(
        self, client, dispatch_stub, mock_pr_files, mock_confluence
    ):
        """A breaking-change PR touching multiple components → p1=True on EACH event."""
        mock_pr_files(["src/auth/tokens/refresh.ts", "src/auth/__init__.py"])
        mock_confluence([])
        payload = _merged_pr_payload(labels=["breaking-change"])
        resp = _post(client, payload, "floor-multi-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["floor"] is True
        assert data["dispatched"] == 2  # one event per component
        assert len(dispatch_stub) == 2
        for event in dispatch_stub:
            assert event.metadata.get("p1") is True

    def test_p1_set_on_conservative_fallback_event(
        self, client, dispatch_stub, mock_pr_files, mock_confluence
    ):
        """A breaking-change PR touching no owned paths still emits ONE event with p1=True."""
        mock_pr_files(["docs/README.md"])  # nothing owned
        mock_confluence([])
        payload = _merged_pr_payload(labels=["breaking-change"])
        resp = _post(client, payload, "floor-fallback-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["components_touched"] == []
        assert data["dispatched"] == 1
        assert data["floor"] is True
        assert dispatch_stub[0].metadata.get("p1") is True

    def test_confluence_failure_degrades_to_p1_false(
        self, client, dispatch_stub, mock_pr_files, app, monkeypatch
    ):
        """If the Confluence lookup raises, p1 defaults to False and the webhook still 200s."""
        import webhook_server as ws

        mock_pr_files(["src/auth/login/page.tsx"])

        def _boom(query, team=None):  # noqa: ANN001
            raise RuntimeError("confluence down")

        monkeypatch.setattr(ws._providers.confluence, "search_pages", _boom)
        payload = _merged_pr_payload(labels=["breaking-change"])
        resp = _post(client, payload, "floor-degrade-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["floor"] is False          # degraded safely
        assert data["dispatched"] >= 1         # NOT dropped
        for event in dispatch_stub:
            assert event.metadata.get("p1") is False

    def test_end_to_end_webhook_p1_routes_to_blocked(
        self, client, mock_pr_files, mock_confluence, monkeypatch
    ):
        """End-to-end: a real (un-stubbed) dispatch of a floored merge lands in Lane.BLOCKED."""
        from src.agent.events import EventRouter
        from src.agent.membrane import Lane

        mock_pr_files(["src/auth/login/page.tsx"])
        mock_confluence([])

        lanes: list = []
        real_route_lane = EventRouter.route_lane

        def _spy_route_lane(self, event, policy=None, **kw):
            decision = real_route_lane(self, event, policy, **kw)
            lanes.append(decision.lane)
            return decision

        # Don't actually post to Slack: stub the slack provider's post_message.
        import webhook_server as ws
        monkeypatch.setattr(ws._providers.slack, "post_message", lambda *a, **k: None)
        monkeypatch.setattr(EventRouter, "route_lane", _spy_route_lane)

        payload = _merged_pr_payload(labels=["breaking-change"])
        resp = _post(client, payload, "floor-e2e-01")
        assert resp.status_code == 200
        assert resp.json()["floor"] is True
        assert Lane.BLOCKED in lanes
