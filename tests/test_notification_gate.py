"""Tests for notification discipline (alert gating).

The four-condition gate (cross-team + high-confidence + actionable + fresh)
lives in src/agent/detector.evaluate_alert_gate and is applied at the
notification/digest selection layer — NOT inside DriftDetector.run_all(), whose
raw counts are asserted by the golden detector tests. These tests prove each
condition independently suppresses an alert, and that a fully-qualifying alert
passes. They build small fakes so every condition can be controlled in isolation
(no dependence on the synthetic fixture's clock or data).
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.agent.detector import evaluate_alert_gate
from src.agent.freshness import AGING_DAYS, STALE_DAYS


# ── fakes ─────────────────────────────────────────────────────────────────────

class FakeSeverity:
    """Stands in for DriftSeverity — only .value is read by the gate."""
    def __init__(self, value: str):
        self.value = value


def make_issue(*, teams, severity="high", action="Schedule a cross-team sync."):
    """A DriftIssue/ConflictPrediction-shaped object (duck-typed for the gate)."""
    return SimpleNamespace(
        id="issue-1",
        title="Multiple teams planning changes to 'auth'",
        teams_involved=list(teams),
        severity=FakeSeverity(severity) if severity else None,
        suggested_action=action,
    )


def make_team(name: str, days_ago: int | None):
    """A manifest-shaped team with last_verified `days_ago` days in the past.

    days_ago=None  -> unverified (never fresh)
    days_ago<=14   -> fresh
    14<days_ago<=30-> aging (still fresh: is_fresh >= 0.6)
    days_ago>30    -> stale (not fresh)
    """
    lv = None if days_ago is None else date.today() - timedelta(days=days_ago)
    return SimpleNamespace(team=name, last_verified=lv)


class FakeManifests:
    def __init__(self, teams: dict):
        self._teams = teams

    def get_team(self, name: str):
        return self._teams.get(name)


class FakeProviders:
    def __init__(self, teams: dict):
        self.manifests = FakeManifests(teams)


class FakePrefs:
    """Minimal NotificationPreferences stand-in — only severity_ok is used here."""
    def __init__(self, min_rank: int = 2):  # default threshold = "high"
        self._rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        self._min = min_rank

    def severity_ok(self, team: str, severity: str) -> bool:
        return self._rank.get(severity, 0) >= self._min


# A standard environment: two fresh teams, threshold = high.
def fresh_env():
    teams = {
        "Team A": make_team("Team A", days_ago=2),
        "Team B": make_team("Team B", days_ago=2),
    }
    return FakeProviders(teams), FakePrefs(min_rank=2)


# ── the qualifying case ───────────────────────────────────────────────────────

def test_cross_team_high_sev_actionable_fresh_passes():
    providers, prefs = fresh_env()
    issue = make_issue(teams=["Team A", "Team B"], severity="high",
                       action="Schedule a cross-team sync.")
    result = evaluate_alert_gate(issue, providers, prefs, "Team A")
    assert result.passed is True
    assert bool(result) is True
    assert all(result.reasons.values())
    assert "cross-team" in result.explanation


# ── each condition suppresses independently ───────────────────────────────────

def test_single_team_suppressed():
    providers, prefs = fresh_env()
    issue = make_issue(teams=["Team A"], severity="high")
    result = evaluate_alert_gate(issue, providers, prefs, "Team A")
    assert result.passed is False
    assert result.reasons["cross_team"] is False
    assert "single-team" in result.explanation


def test_low_severity_suppressed():
    providers, prefs = fresh_env()  # threshold is "high"
    issue = make_issue(teams=["Team A", "Team B"], severity="low")
    result = evaluate_alert_gate(issue, providers, prefs, "Team A")
    assert result.passed is False
    assert result.reasons["high_confidence"] is False
    assert "severity" in result.explanation


def test_non_actionable_suppressed():
    providers, prefs = fresh_env()
    issue = make_issue(teams=["Team A", "Team B"], severity="high", action="   ")
    result = evaluate_alert_gate(issue, providers, prefs, "Team A")
    assert result.passed is False
    assert result.reasons["actionable"] is False
    assert "no suggested action" in result.explanation


def test_stale_team_suppressed():
    # Team B's manifest is well past STALE_DAYS -> not fresh -> alert suppressed.
    teams = {
        "Team A": make_team("Team A", days_ago=2),
        "Team B": make_team("Team B", days_ago=STALE_DAYS + 10),
    }
    providers = FakeProviders(teams)
    prefs = FakePrefs(min_rank=2)
    issue = make_issue(teams=["Team A", "Team B"], severity="high")
    result = evaluate_alert_gate(issue, providers, prefs, "Team A")
    assert result.passed is False
    assert result.reasons["fresh"] is False
    assert "stale" in result.explanation


def test_unverified_team_suppressed():
    teams = {
        "Team A": make_team("Team A", days_ago=2),
        "Team B": make_team("Team B", days_ago=None),  # never verified
    }
    providers = FakeProviders(teams)
    prefs = FakePrefs(min_rank=2)
    issue = make_issue(teams=["Team A", "Team B"], severity="high")
    result = evaluate_alert_gate(issue, providers, prefs, "Team A")
    assert result.passed is False
    assert result.reasons["fresh"] is False


def test_missing_team_manifest_suppressed():
    # An involved team that has no manifest at all cannot be freshness-verified.
    teams = {"Team A": make_team("Team A", days_ago=2)}  # Team B missing
    providers = FakeProviders(teams)
    prefs = FakePrefs(min_rank=2)
    issue = make_issue(teams=["Team A", "Team B"], severity="high")
    result = evaluate_alert_gate(issue, providers, prefs, "Team A")
    assert result.passed is False
    assert result.reasons["fresh"] is False


# ── aging-but-not-stale is still fresh (boundary) ─────────────────────────────

def test_aging_team_still_passes():
    days = (AGING_DAYS + STALE_DAYS) // 2  # between aging and stale -> is_fresh True
    teams = {
        "Team A": make_team("Team A", days_ago=days),
        "Team B": make_team("Team B", days_ago=days),
    }
    providers = FakeProviders(teams)
    prefs = FakePrefs(min_rank=2)
    issue = make_issue(teams=["Team A", "Team B"], severity="high")
    result = evaluate_alert_gate(issue, providers, prefs, "Team A")
    assert result.passed is True
    assert result.reasons["fresh"] is True


# ── configurability: a relaxed gate lets a single-team alert through ───────────

def test_require_cross_team_can_be_relaxed():
    providers, prefs = fresh_env()
    issue = make_issue(teams=["Team A"], severity="high")
    strict = evaluate_alert_gate(issue, providers, prefs, "Team A")
    relaxed = evaluate_alert_gate(issue, providers, prefs, "Team A",
                                  require_cross_team=False)
    assert strict.passed is False
    assert relaxed.passed is True
    # the reason flag still reports the underlying fact, even when not required
    assert relaxed.reasons["cross_team"] is False


# ── integration: the gate filters real digest selection ───────────────────────

class TestDigestGateIntegration:
    """Confirm DigestGenerator applies the gate at the selection layer and that
    run_all() (the raw scan) is unaffected — using the real synthetic providers."""

    @pytest.fixture(autouse=True)
    def setup(self, providers, tmp_state):
        from src.agent.digest import DigestGenerator
        self.providers = providers
        self.gated = DigestGenerator(providers, apply_alert_gate=True)
        self.ungated = DigestGenerator(providers, apply_alert_gate=False)

    def test_raw_scan_unchanged_by_gate(self):
        """The gate must not touch run_all()'s raw output."""
        from src.agent.detector import DriftDetector
        before = len(DriftDetector(self.providers).run_all())
        # generating a gated digest must not mutate the raw scan
        self.gated.generate_for_team("Team Phoenix")
        after = len(DriftDetector(self.providers).run_all())
        assert before == after

    def test_gated_alerts_are_subset_of_ungated(self):
        for team in (t.team for t in self.providers.manifests.get_all_teams()):
            gated = self.gated.generate_for_team(team)
            ungated = self.ungated.generate_for_team(team)
            assert len(gated.open_conflicts) <= len(ungated.open_conflicts)
            assert len(gated.predicted_conflicts) <= len(ungated.predicted_conflicts)

    def test_every_gated_alert_is_cross_team_and_actionable(self):
        for team in (t.team for t in self.providers.manifests.get_all_teams()):
            digest = self.gated.generate_for_team(team)
            for issue in digest.open_conflicts + digest.predicted_conflicts:
                assert len(set(issue.teams_involved)) >= 2
                assert issue.suggested_action.strip()
