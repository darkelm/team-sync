"""Tests for the live Figma provider's per-component divergence signal
(src/providers/live/figma.py).

Phase A1 stopped hardcoding ``diverges_from_library=False`` on the components
returned by ``get_components``. The divergence decision now lives in a single
shared classifier (``_divergence_flavour`` / ``_judge_divergence``) consumed by
BOTH ``get_drift_issues`` (DriftIssues) and ``get_components`` (the per-component
flag the governance membrane's novel/propose path reads). These tests prove:

  (a) a team component DETACHED/UNLINKED from a same-named library component is
      stamped diverges_from_library=True with non-empty divergence_notes;
  (b) a component with no same-named library match (genuinely novel) is stamped
      False — these heuristics only ever flag name-matches, so a no-match is the
      true "clean" case;
  (c) get_drift_issues still emits its expected issues (no regression);
  (d) an API failure degrades the flag to False without raising.

HTTP is intercepted at the ``httpx.get`` boundary (mirroring the discipline in
test_live_atlassian.py — no real token, no network). The Figma ``_get`` calls
``httpx.get(url, headers=..., timeout=...)``, so the fake dispatches on the URL.

Run: SYNCBOT_TEST=1 .venv/bin/python3 -m pytest tests/test_live_figma.py -q
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

import src.providers.live.figma as figma_mod
from src.providers.live.figma import LiveFigmaProvider


# ── Fake httpx response ──────────────────────────────────────────────────────
# Mimics just the surface the provider's _get touches: .raise_for_status(),
# .json(). (Figma's _get does not read .status_code/.text on success.)


class FakeHTTPResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None
            )

    def json(self):
        return self._payload


# ── Canned Figma REST payloads (/v1/files/{key}/components etc.) ──────────────

LIBRARY_FILE_KEY = "lib-key-001"
TEAM_FILE_KEY = "team-key-phx"

# GET /v1/files/{LIBRARY_FILE_KEY}/components
LIBRARY_COMPONENTS = {
    "name": "Design System Library",
    "meta": {
        "components": [
            {
                "key": "lib-btn-001",
                "name": "Button",
                "description": "Primary button",
                "updated_at": "2026-05-27T16:00:00Z",
                "component_set_id": "",
            },
            {
                "key": "lib-notif-001",
                "name": "NotificationBell",
                "description": "Bell with badge",
                "updated_at": "2026-04-10T10:00:00Z",
                "component_set_id": "",
            },
        ]
    },
}

# GET /v1/files/{TEAM_FILE_KEY}/components
#  - NotificationBell: same name as library, DIFFERENT key -> H1 detached (True)
#  - LoginForm:  no library match -> truly novel -> no divergence (False)
#  - SparklineChart: no library match -> truly novel -> no divergence (False)
#
# NOTE on what "clean" means under these heuristics: a non-library team
# component that shares a library NAME is ALWAYS a divergence (detached, stale,
# or — at minimum — a naming shadow; H3 has the highest false-positive rate and
# is documented as such). So the genuine non-divergent cases are components with
# NO same-named library component. We model two of them below.
TEAM_COMPONENTS = {
    "name": "Phoenix Auth Flows",
    "meta": {
        "components": [
            {
                "key": "phx-notif-custom-001",
                "name": "NotificationBell",
                "description": "Local copy of the bell",
                "updated_at": "2026-05-10T14:00:00Z",
                "component_set_id": "",
            },
            {
                "key": "phx-login-001",
                "name": "LoginForm",
                "description": "Team-only component, no library equivalent",
                "updated_at": "2026-06-01T09:00:00Z",
                "component_set_id": "",
            },
            {
                "key": "phx-spark-001",
                "name": "SparklineChart",
                "description": "Team-only component, no library equivalent",
                "updated_at": "2026-06-05T09:00:00Z",
                "component_set_id": "",
            },
        ]
    },
}


# ── Fixtures / helpers ───────────────────────────────────────────────────────

@pytest.fixture()
def figma_env(monkeypatch):
    monkeypatch.setenv("FIGMA_ACCESS_TOKEN", "fake-token")
    monkeypatch.setenv("FIGMA_LIBRARY_FILE_KEY", LIBRARY_FILE_KEY)


def _make_manifests():
    """Fake ManifestProvider exposing one team with one figma file."""
    team = MagicMock()
    team.team = "Team Phoenix"
    figma_file = MagicMock()
    figma_file.url = f"https://figma.com/file/{TEAM_FILE_KEY}/phoenix-auth-flows"
    figma_file.name = "Phoenix Auth Flows"
    team.figma_files = [figma_file]
    team.components = MagicMock()
    team.components.design = []
    team.design_system_library = None

    manifests = MagicMock()
    manifests.get_all_teams.return_value = [team]
    return manifests


def _route(url, status_for=None):
    """Map a Figma REST URL to a canned FakeHTTPResponse.

    status_for: optional dict {url_substring: status_code} to force an HTTP
    error on a specific endpoint (used to prove graceful degradation).
    """
    status_for = status_for or {}
    for substr, code in status_for.items():
        if substr in url:
            return FakeHTTPResponse({}, status_code=code, text="error")

    if f"/files/{LIBRARY_FILE_KEY}/components" in url:
        return FakeHTTPResponse(LIBRARY_COMPONENTS)
    if f"/files/{TEAM_FILE_KEY}/components" in url:
        return FakeHTTPResponse(TEAM_COMPONENTS)
    # component_sets and any other endpoint: empty meta is harmless.
    return FakeHTTPResponse({"meta": {}})


def _patch_httpx(monkeypatch, status_for=None):
    def fake_get(url, headers=None, timeout=None):
        return _route(url, status_for=status_for)
    monkeypatch.setattr(figma_mod.httpx, "get", fake_get)


def _provider(monkeypatch, status_for=None):
    _patch_httpx(monkeypatch, status_for=status_for)
    return LiveFigmaProvider(manifests=_make_manifests())


def _by_name(comps):
    return {c.name: c for c in comps}


# ── (a) Detached/unlinked -> diverges_from_library=True + notes ──────────────

class TestDivergenceStamping:
    def test_detached_component_flagged_true_with_notes(self, figma_env, monkeypatch):
        comps = _provider(monkeypatch).get_components()
        bell = _by_name(comps)["NotificationBell"]
        assert bell.diverges_from_library is True
        assert bell.divergence_notes  # non-empty
        # Note names the flavour and the library component it diverges from.
        assert "Detached" in bell.divergence_notes
        assert "NotificationBell" in bell.divergence_notes

    # ── (b) clean / no-match -> False ───────────────────────────────────────

    def test_component_without_library_match_not_flagged(self, figma_env, monkeypatch):
        comps = _provider(monkeypatch).get_components()
        for name in ("LoginForm", "SparklineChart"):
            comp = _by_name(comps)[name]
            # No same-named library component -> genuinely novel, not divergence.
            assert comp.diverges_from_library is False, name
            assert comp.divergence_notes is None, name

    def test_all_three_components_returned(self, figma_env, monkeypatch):
        comps = _provider(monkeypatch).get_components()
        assert len(comps) == 3
        assert all(not c.is_library_component for c in comps)


# ── (c) get_drift_issues still produces its expected issues ───────────────────

class TestDriftIssuesNoRegression:
    def test_detached_issue_emitted(self, figma_env, monkeypatch):
        issues = _provider(monkeypatch).get_drift_issues()
        detached = [i for i in issues if "detached" in i.id]
        assert len(detached) == 1
        assert detached[0].type == "design_drift"
        assert detached[0].severity.value == "high"
        assert "NotificationBell" in detached[0].components_involved

    def test_novel_components_produce_no_issue(self, figma_env, monkeypatch):
        issues = _provider(monkeypatch).get_drift_issues()
        involved = {c for i in issues for c in i.components_involved}
        # Components with no library name-match are not drift.
        assert "LoginForm" not in involved
        assert "SparklineChart" not in involved

    def test_only_the_detached_component_drifts(self, figma_env, monkeypatch):
        issues = _provider(monkeypatch).get_drift_issues()
        assert len(issues) == 1


# ── (d) API failure degrades to flag=False without raising ────────────────────

class TestGracefulDegradation:
    def test_library_fetch_failure_leaves_flag_false(self, figma_env, monkeypatch):
        # Force the library endpoint to 500. The team-component fetch still
        # succeeds, so components come back — but unstamped (safe default).
        provider = _provider(
            monkeypatch,
            status_for={f"/files/{LIBRARY_FILE_KEY}/components": 500},
        )
        comps = provider.get_components()  # must not raise
        assert len(comps) == 3
        assert all(c.diverges_from_library is False for c in comps)
        assert all(c.divergence_notes is None for c in comps)

    def test_drift_issues_empty_when_library_unavailable(self, figma_env, monkeypatch):
        provider = _provider(
            monkeypatch,
            status_for={f"/files/{LIBRARY_FILE_KEY}/components": 500},
        )
        # No library index -> no drift can be inferred, and no exception.
        assert provider.get_drift_issues() == []
