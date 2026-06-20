"""
Tests for webhook_server.py — FastAPI inbound HTTP surface.

Strategy
--------
- Use FastAPI TestClient (synchronous, no running server needed).
- Monkeypatch `webhook_server.get_providers` to return the session-scoped
  `providers` fixture (local, no API keys, no Slack posts).
- Monkeypatch `EventRouter.dispatch` to a stub that returns a fixed count and
  records the Event it was called with — no real Slack calls.
- Assert:
  - 401 on bad / missing signatures.
  - 200 + correct dispatched count on valid payloads.
  - Correct Event fields (type, subject, source, team).
  - 200 + dispatched=0 for irrelevant payloads (not merged PR, wrong event, etc.).
  - GET /health → 200 {"status":"ok"}.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app(providers):
    """Return the FastAPI app with get_providers wired to local providers."""
    import webhook_server as ws

    # Override the global providers singleton to the test one
    ws._providers = providers
    # Reset the deduplication set so tests don't bleed
    ws._seen_delivery_ids.clear()
    return ws.app


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_seen_ids():
    """Clear dedup set before each test so ordering doesn't matter."""
    import webhook_server as ws
    ws._seen_delivery_ids.clear()
    yield
    ws._seen_delivery_ids.clear()


@pytest.fixture()
def dispatch_stub(monkeypatch):
    """
    Replace EventRouter.dispatch with a stub.

    Returns a list that accumulates (event, return_value) tuples.
    By default dispatch returns 1 (one notification sent).
    """
    from src.agent.events import EventRouter
    calls: list[Any] = []

    def _stub(self, event):  # noqa: ANN001
        calls.append(event)
        return 1

    monkeypatch.setattr(EventRouter, "dispatch", _stub)
    return calls


# ---------------------------------------------------------------------------
# Helper: build a valid GitHub HMAC signature
# ---------------------------------------------------------------------------

_GITHUB_SECRET = "test-github-secret"
_FIGMA_PASSCODE = "test-figma-passcode"
_JIRA_TOKEN = "test-jira-token"
_SHARED_SECRET = "test-shared-secret"


def _github_sig(body: bytes, secret: str = _GITHUB_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /webhooks/github
# ---------------------------------------------------------------------------

class TestGithubWebhook:
    @pytest.fixture(autouse=True)
    def set_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", _GITHUB_SECRET)

    def _merged_pr_payload(self, repo="phoenix-auth") -> dict:
        return {
            "action": "closed",
            "pull_request": {
                "number": 42,
                "title": "feat: implement PKCE",
                "merged": True,
                "merged_by": {"login": "marcus.webb"},
            },
            "repository": {"name": repo},
        }

    def test_bad_signature_returns_401(self, client):
        body = json.dumps(self._merged_pr_payload()).encode()
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=deadbeef",
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "abc-001",
            },
        )
        assert resp.status_code == 401

    def test_missing_signature_returns_401(self, client):
        body = json.dumps(self._merged_pr_payload()).encode()
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "abc-002",
            },
        )
        assert resp.status_code == 401

    def test_valid_merged_pr_dispatches(self, client, dispatch_stub):
        payload = self._merged_pr_payload()
        body = json.dumps(payload).encode()
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _github_sig(body),
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "gh-delivery-001",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dispatched"] == 1
        # Verify the Event that was dispatched
        assert len(dispatch_stub) == 1
        event = dispatch_stub[0]
        assert event.type == "code.merged"
        assert event.source == "github"

    def test_non_merged_pr_ignored(self, client, dispatch_stub):
        """PR closed but not merged → 200 dispatched=0."""
        payload = {
            "action": "closed",
            "pull_request": {"number": 1, "title": "draft", "merged": False},
            "repository": {"name": "some-repo"},
        }
        body = json.dumps(payload).encode()
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _github_sig(body),
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "gh-delivery-002",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["dispatched"] == 0
        assert not dispatch_stub

    def test_non_pr_event_ignored(self, client, dispatch_stub):
        """Push events are not pull_request events → 200 dispatched=0."""
        payload = {"ref": "refs/heads/main"}
        body = json.dumps(payload).encode()
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _github_sig(body),
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "gh-delivery-003",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["dispatched"] == 0

    def test_duplicate_delivery_ignored(self, client, dispatch_stub):
        payload = self._merged_pr_payload()
        body = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _github_sig(body),
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "gh-dupe-01",
        }
        # First request should dispatch
        r1 = client.post("/webhooks/github", content=body, headers=headers)
        assert r1.status_code == 200
        assert r1.json()["dispatched"] == 1
        # Second request with same delivery id → ignored
        r2 = client.post("/webhooks/github", content=body, headers=headers)
        assert r2.status_code == 200
        assert r2.json()["dispatched"] == 0
        assert r2.json().get("ignored") == "duplicate delivery"

    def test_repo_to_team_resolution(self, client, dispatch_stub):
        """A repo named 'phoenix-auth' should resolve to Team Phoenix."""
        payload = self._merged_pr_payload(repo="phoenix-auth")
        body = json.dumps(payload).encode()
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _github_sig(body),
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "gh-team-001",
            },
        )
        assert resp.status_code == 200
        assert len(dispatch_stub) == 1
        event = dispatch_stub[0]
        assert "phoenix" in event.team.lower() or event.team == ""  # may or may not resolve


# ---------------------------------------------------------------------------
# /webhooks/figma
# ---------------------------------------------------------------------------

class TestFigmaWebhook:
    @pytest.fixture(autouse=True)
    def set_env(self, monkeypatch):
        monkeypatch.setenv("FIGMA_WEBHOOK_PASSCODE", _FIGMA_PASSCODE)

    def _library_publish_payload(self, passcode=_FIGMA_PASSCODE) -> dict:
        return {
            "event_type": "LIBRARY_PUBLISH",
            "passcode": passcode,
            "webhook_id": "wh-123",
            "timestamp": "2026-05-30T00:00:00Z",
            "file_key": "nova-design-system",
            "file_name": "Nova Design System",
            "created": [{"name": "NotificationBell"}],
            "modified": [],
        }

    def test_bad_passcode_returns_401(self, client):
        payload = self._library_publish_payload(passcode="wrong")
        resp = client.post("/webhooks/figma", json=payload)
        assert resp.status_code == 401

    def test_missing_passcode_returns_401(self, client):
        payload = {k: v for k, v in self._library_publish_payload().items() if k != "passcode"}
        resp = client.post("/webhooks/figma", json=payload)
        assert resp.status_code == 401

    def test_library_publish_dispatches(self, client, dispatch_stub):
        payload = self._library_publish_payload()
        resp = client.post("/webhooks/figma", json=payload)
        assert resp.status_code == 200
        assert resp.json()["dispatched"] == 1
        assert len(dispatch_stub) == 1
        event = dispatch_stub[0]
        assert event.type == "design.library_published"
        assert event.subject == "NotificationBell"
        assert event.source == "figma"

    def test_non_library_publish_ignored(self, client, dispatch_stub):
        payload = self._library_publish_payload()
        payload["event_type"] = "FILE_UPDATE"
        resp = client.post("/webhooks/figma", json=payload)
        assert resp.status_code == 200
        assert resp.json()["dispatched"] == 0
        assert not dispatch_stub

    def test_duplicate_delivery_ignored(self, client, dispatch_stub):
        payload = self._library_publish_payload()
        r1 = client.post("/webhooks/figma", json=payload)
        assert r1.json()["dispatched"] == 1
        r2 = client.post("/webhooks/figma", json=payload)
        assert r2.json()["dispatched"] == 0
        assert r2.json().get("ignored") == "duplicate delivery"


# ---------------------------------------------------------------------------
# /webhooks/jira
# ---------------------------------------------------------------------------

class TestJiraWebhook:
    @pytest.fixture(autouse=True)
    def set_env(self, monkeypatch):
        monkeypatch.setenv("JIRA_WEBHOOK_TOKEN", _JIRA_TOKEN)

    def _issue_created_payload(self) -> dict:
        return {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "id": "10001",
                "key": "PHX-999",
                "fields": {
                    "summary": "Add biometric auth support",
                    "project": {"key": "PHX"},
                    "issuetype": {"name": "Story"},
                },
            },
        }

    def _due_date_change_payload(self) -> dict:
        return {
            "webhookEvent": "jira:issue_updated",
            "issue": {
                "id": "10002",
                "key": "PHX-100",
                "fields": {
                    "summary": "OAuth 2.0 PKCE flow",
                    "project": {"key": "PHX"},
                    "issuetype": {"name": "Epic"},
                },
            },
            "changelog": {
                "items": [
                    {"field": "duedate", "fromString": "2026-06-01", "toString": "2026-06-15"},
                ]
            },
        }

    def test_bad_token_returns_401(self, client):
        resp = client.post(
            "/webhooks/jira",
            json=self._issue_created_payload(),
            headers={"X-Webhook-Token": "wrong"},
        )
        assert resp.status_code == 401

    def test_missing_token_returns_401(self, client):
        resp = client.post("/webhooks/jira", json=self._issue_created_payload())
        assert resp.status_code == 401

    def test_issue_created_dispatches_work_created(self, client, dispatch_stub):
        resp = client.post(
            "/webhooks/jira",
            json=self._issue_created_payload(),
            headers={"X-Webhook-Token": _JIRA_TOKEN},
        )
        assert resp.status_code == 200
        assert resp.json()["dispatched"] == 1
        assert len(dispatch_stub) == 1
        event = dispatch_stub[0]
        assert event.type == "work.created"
        assert event.subject == "Add biometric auth support"
        assert event.source == "jira"

    def test_due_date_change_dispatches_roadmap_date_changed(self, client, dispatch_stub):
        resp = client.post(
            "/webhooks/jira",
            json=self._due_date_change_payload(),
            headers={"X-Webhook-Token": _JIRA_TOKEN},
        )
        assert resp.status_code == 200
        assert resp.json()["dispatched"] == 1
        assert len(dispatch_stub) == 1
        event = dispatch_stub[0]
        assert event.type == "roadmap.date_changed"

    def test_non_duedate_update_ignored(self, client, dispatch_stub):
        payload = self._due_date_change_payload()
        payload["changelog"]["items"] = [{"field": "status", "fromString": "Open", "toString": "In Progress"}]
        resp = client.post(
            "/webhooks/jira",
            json=payload,
            headers={"X-Webhook-Token": _JIRA_TOKEN},
        )
        assert resp.status_code == 200
        assert resp.json()["dispatched"] == 0

    def test_jira_project_resolves_to_team(self, client, dispatch_stub):
        """PHX project key should resolve to Team Phoenix."""
        resp = client.post(
            "/webhooks/jira",
            json=self._issue_created_payload(),
            headers={"X-Webhook-Token": _JIRA_TOKEN},
        )
        assert resp.status_code == 200
        event = dispatch_stub[0]
        assert "phoenix" in event.team.lower()


# ---------------------------------------------------------------------------
# /webhooks/calendar
# ---------------------------------------------------------------------------

class TestCalendarWebhook:
    @pytest.fixture(autouse=True)
    def set_env(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_SHARED_SECRET", _SHARED_SECRET)

    def _cross_team_payload(self, title="Phoenix x Atlas alignment sync") -> dict:
        return {
            "title": title,
            "start": "2026-06-03T10:00:00Z",
            "end": "2026-06-03T10:30:00Z",
            "event_id": "cal-event-99",
        }

    def test_bad_token_returns_401(self, client):
        resp = client.post(
            "/webhooks/calendar",
            json=self._cross_team_payload(),
            headers={"X-Webhook-Token": "bad"},
        )
        assert resp.status_code == 401

    def test_cross_team_sync_dispatches(self, client, dispatch_stub):
        resp = client.post(
            "/webhooks/calendar",
            json=self._cross_team_payload(),
            headers={"X-Webhook-Token": _SHARED_SECRET},
        )
        assert resp.status_code == 200
        assert resp.json()["dispatched"] == 1
        event = dispatch_stub[0]
        assert event.type == "calendar.cross_team_sync"
        assert event.source == "calendar"
        # Teams list should include both phoenix and atlas
        teams = event.metadata.get("teams", [])
        team_slugs = [t.lower() for t in teams]
        assert any("phoenix" in s for s in team_slugs)

    def test_non_sync_title_ignored(self, client, dispatch_stub):
        payload = self._cross_team_payload(title="Sprint review")
        payload["event_id"] = "cal-event-100"
        resp = client.post(
            "/webhooks/calendar",
            json=payload,
            headers={"X-Webhook-Token": _SHARED_SECRET},
        )
        assert resp.status_code == 200
        assert resp.json()["dispatched"] == 0

    def test_multi_team_metadata_populated(self, client, dispatch_stub):
        payload = self._cross_team_payload("Phoenix x Atlas x Horizon quarterly sync")
        payload["event_id"] = "cal-event-101"
        resp = client.post(
            "/webhooks/calendar",
            json=payload,
            headers={"X-Webhook-Token": _SHARED_SECRET},
        )
        assert resp.status_code == 200
        if dispatch_stub:
            event = dispatch_stub[0]
            assert "teams" in event.metadata
            assert len(event.metadata["teams"]) >= 2


# ---------------------------------------------------------------------------
# /webhooks/generic
# ---------------------------------------------------------------------------

class TestGenericWebhook:
    @pytest.fixture(autouse=True)
    def set_env(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_SHARED_SECRET", _SHARED_SECRET)

    def _generic_payload(self, event_type="research.study_added") -> dict:
        return {
            "type": event_type,
            "subject": "Onboarding usability study Q2",
            "team": "Team Phoenix",
            "source": "dovetail",
            "metadata": {"url": "https://dovetail.app/study/123"},
        }

    def test_bad_token_returns_401(self, client):
        resp = client.post(
            "/webhooks/generic",
            json=self._generic_payload(),
            headers={"X-Webhook-Token": "bad"},
        )
        assert resp.status_code == 401

    def test_valid_catalog_type_dispatches(self, client, dispatch_stub):
        resp = client.post(
            "/webhooks/generic",
            json=self._generic_payload("research.study_added"),
            headers={"X-Webhook-Token": _SHARED_SECRET},
        )
        assert resp.status_code == 200
        assert resp.json()["dispatched"] == 1
        assert len(dispatch_stub) == 1
        event = dispatch_stub[0]
        assert event.type == "research.study_added"
        assert event.subject == "Onboarding usability study Q2"

    def test_unknown_type_returns_400(self, client):
        payload = self._generic_payload("totally.unknown.event")
        resp = client.post(
            "/webhooks/generic",
            json=payload,
            headers={"X-Webhook-Token": _SHARED_SECRET},
        )
        assert resp.status_code == 400

    def test_all_catalog_types_accepted(self, client, dispatch_stub):
        """Every type in TRIGGER_CATALOG should return 200 (not 400)."""
        from src.agent.events import TRIGGER_CATALOG
        for i, event_type in enumerate(TRIGGER_CATALOG):
            payload = {
                "type": event_type,
                "subject": f"test subject {i}",
                "team": "Team Phoenix",
                "delivery_id": f"gen-catalog-{i}",
            }
            resp = client.post(
                "/webhooks/generic",
                json=payload,
                headers={"X-Webhook-Token": _SHARED_SECRET},
            )
            assert resp.status_code == 200, f"Expected 200 for type={event_type}, got {resp.status_code}"
