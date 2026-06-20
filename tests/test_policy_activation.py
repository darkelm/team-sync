"""Tests for the per-team governance GOVERNOR — config opt-in → live routing.

Covers the three layers that turn the membrane from a notifier into a per-team
governor:

  1. policy_loader.policy_for_team: a team with `governance:` toggles resolves to a
     PERMISSIVE policy; a team with no toggles, an unknown team, or a broken lookup
     resolves to the conservative `default_policy()` (and never raises).
  2. EventRouter.dispatch with policy=None: resolves the ORIGINATING team's policy
     automatically — the demo team's low-reach raw `changed` event auto-flows (ping
     suppressed), a toggle-free team's identical event still routes to review.
  3. Explicit policy still wins (the orchestrator can override config).
  4. The `policy` CLI command runs and previews resolved lanes.

Hermetic (SYNCBOT_TEST=1, local providers, provenance redirected to tmp).
"""
from __future__ import annotations

import json
import os

import pytest

from src.agent.membrane import Lane, DesignerToggles, compile_policy_from_toggles, default_policy
from src.agent import policy_loader

# The demo team manifests.py opts into small-tweaks autonomy. Keep this in lockstep
# with src/providers/local/manifests.py `_SYNTHETIC_GOVERNANCE`.
DEMO_TEAM = "Team Forge"
# A leaf component owned by the demo team that no other team consumes (reach 0) and
# stays raw-tier → a `changed` event on it is autonomy-eligible under small_tweaks_flow.
DEMO_LOW_REACH_SUBJECT = "StatusIndicator"
# A toggle-free team (no `governance:` opt-in) — its policy stays the all-review default.
TOGGLE_FREE_TEAM = "Team Atlas"


@pytest.fixture
def prov_path(tmp_path, monkeypatch):
    """Redirect the module-level provenance path so dispatch writes to tmp."""
    p = str(tmp_path / "provenance.jsonl")
    monkeypatch.setattr("src.agent.provenance.PROVENANCE_PATH", p)
    return p


def _records(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# ── policy_for_team ──────────────────────────────────────────────────────────


class TestPolicyForTeam:
    def test_team_with_toggles_resolves_permissive_policy(self, providers):
        """The demo team's `governance:` opt-in compiles to a policy that includes an
        AUTO grant — distinct from the rule-less, all-review default_policy()."""
        policy = policy_loader.policy_for_team(DEMO_TEAM, providers)
        assert policy.default_lane == Lane.REVIEW  # default lane stays conservative
        # The small_tweaks_flow grant is present (the ONLY thing that makes auto reachable).
        assert any(r.lane == Lane.AUTO for r in policy.rules)
        # It is exactly the policy small_tweaks_flow compiles to.
        expected = compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True))
        assert policy == expected

    def test_team_without_toggles_resolves_default_policy(self, providers):
        """A team that authors no `governance:` opt-in falls back to the conservative
        default_policy() (no rules, everything → review)."""
        policy = policy_loader.policy_for_team(TOGGLE_FREE_TEAM, providers)
        assert policy == default_policy()
        assert policy.rules == []

    def test_unknown_team_resolves_default_policy(self, providers):
        """An unknown team name degrades to the safe default — never raises."""
        policy = policy_loader.policy_for_team("Team Does-Not-Exist", providers)
        assert policy == default_policy()

    def test_empty_team_and_none_providers_resolve_default(self, providers):
        """Empty team name or missing providers ⇒ default_policy(), no raise."""
        assert policy_loader.policy_for_team("", providers) == default_policy()
        assert policy_loader.policy_for_team(DEMO_TEAM, None) == default_policy()

    def test_broken_provider_degrades_to_default(self):
        """Any lookup failure degrades to default_policy() rather than propagating."""
        class Boom:
            class manifests:
                @staticmethod
                def get_team(name):
                    raise RuntimeError("provider exploded")
        assert policy_loader.policy_for_team(DEMO_TEAM, Boom) == default_policy()


# ── dispatch resolves the team's policy when policy=None ─────────────────────


class TestDispatchResolvesTeamPolicy:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        from src.agent.events import EventRouter, Event
        self.router = EventRouter(providers)
        self.Event = Event

    def test_demo_team_low_reach_change_auto_flows(self, prov_path):
        """No policy passed → dispatch resolves the demo team's opt-in. A low-reach,
        raw-tier `changed` event auto-flows: the ping is SUPPRESSED (sent == 0) and the
        decision is recorded as auto."""
        event = self.Event(type="code.merged", subject=DEMO_LOW_REACH_SUBJECT, team=DEMO_TEAM)
        sent = self.router.dispatch(event)  # policy=None → policy_for_team(Team Forge)
        assert sent == 0  # auto lane → no live ping
        recs = _records(prov_path)
        assert len(recs) == 1
        assert recs[0]["lane"] == Lane.AUTO.value

    def test_toggle_free_team_still_routes_review(self, prov_path):
        """The same shape of event from a toggle-free team resolves to default_policy()
        and routes to review (recorded as review). Proves autonomy is opt-in, not global."""
        event = self.Event(type="code.merged", subject="DataTable", team=TOGGLE_FREE_TEAM)
        self.router.dispatch(event)  # policy=None → default_policy() for Atlas
        recs = _records(prov_path)
        assert len(recs) == 1
        assert recs[0]["lane"] == Lane.REVIEW.value

    def test_explicit_policy_still_wins(self, prov_path):
        """An explicitly-passed policy overrides per-team resolution: handing the demo
        team the conservative default_policy() forces review even though its config
        would otherwise grant auto."""
        event = self.Event(type="code.merged", subject=DEMO_LOW_REACH_SUBJECT, team=DEMO_TEAM)
        self.router.dispatch(event, default_policy())  # explicit conservative override
        recs = _records(prov_path)
        assert len(recs) == 1
        assert recs[0]["lane"] == Lane.REVIEW.value

    def test_explicit_permissive_policy_overrides_toggle_free_team(self, prov_path):
        """The mirror: a toggle-free team handed an explicit permissive policy auto-flows
        — the explicit policy wins over the team's (absent) opt-in."""
        policy = compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True))
        # Atlas-owned, low-reach… use a subject Atlas owns that no one else consumes.
        event = self.Event(type="code.merged", subject="FilterPanel", team=TOGGLE_FREE_TEAM)
        sent = self.router.dispatch(event, policy)
        assert sent == 0
        recs = _records(prov_path)
        assert recs[0]["lane"] == Lane.AUTO.value


# ── CLI `policy` command ─────────────────────────────────────────────────────


class TestPolicyCommand:
    def test_policy_command_runs_for_demo_team(self):
        from typer.testing import CliRunner
        from src.cli.main import app
        runner = CliRunner()
        result = runner.invoke(app, ["policy", DEMO_TEAM])
        assert result.exit_code == 0, result.output
        # The demo team's opt-in is reported and at least one scenario auto-flows.
        # "auto-flows" is the lane GLOSS — it only renders when an AUTO lane row exists
        # (the static footer mentions "AUTO" too, so assert on the row-only gloss).
        assert "small_tweaks_flow" in result.output
        assert "auto-flows" in result.output
        assert "needs human sign-off" in result.output  # guard rails still review

    def test_policy_command_toggle_free_team_all_review(self):
        from typer.testing import CliRunner
        from src.cli.main import app
        runner = CliRunner()
        result = runner.invoke(app, ["policy", TOGGLE_FREE_TEAM])
        assert result.exit_code == 0, result.output
        assert "default_policy" in result.output
        # No AUTO lane row renders → its gloss is absent (the footer's "AUTO" mention
        # is static text, so assert on the row-only gloss instead).
        assert "auto-flows" not in result.output

    def test_policy_command_unknown_team_errors(self):
        from typer.testing import CliRunner
        from src.cli.main import app
        runner = CliRunner()
        result = runner.invoke(app, ["policy", "Team Does-Not-Exist"])
        assert result.exit_code == 1
