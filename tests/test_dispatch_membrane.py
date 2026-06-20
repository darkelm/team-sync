"""Integration tests for EventRouter.dispatch — the governance membrane wired into
the live notification path.

Verifies the four things the wiring promises:
  1. Every dispatch records exactly one provenance entry (the audit trail).
  2. The conservative default policy still notifies (everything → review) — behaviour
     is unchanged until a human grants autonomy.
  3. An `auto`-lane decision SUPPRESSES the live ping (autonomy = no interruption).
  4. The reach resolver's untrusted-resolution signal (metadata.resolution == "review")
     pulls an otherwise-auto change back to review — with HONEST provenance (the record
     says "review", not "auto"), proving the membrane is the sole decider.

Provenance is redirected to a tmp file so these never touch the real data/provenance.jsonl.
"""
from __future__ import annotations

import json
import os

import pytest

from src.agent.membrane import Lane, DesignerToggles, compile_policy_from_toggles


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


class TestDispatchMembrane:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        from src.agent.events import EventRouter, Event
        self.router = EventRouter(providers)
        self.Event = Event

    def test_default_policy_notifies_and_records_review(self, prov_path):
        """No policy → default_policy (everything → review): a published library still
        pings its consumers AND exactly one provenance record lands on the review lane."""
        event = self.Event(type="design.library_published", subject="NotificationBell", team="Team Nova")
        sent = self.router.dispatch(event)  # policy=None → default_policy()
        assert sent >= 1  # NotificationBell has consumers — notifications go out
        recs = _records(prov_path)
        assert len(recs) == 1
        assert recs[0]["lane"] == Lane.REVIEW.value

    def test_auto_lane_suppresses_ping_but_still_records(self, prov_path):
        """A low-reach 'changed' merge under a small-tweaks-flow policy routes to auto:
        NO live ping (sent == 0), but the decision is still recorded as auto."""
        policy = compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True))
        # A component no other team consumes → reach 0 → under the ceiling → auto.
        event = self.Event(type="code.merged", subject="ZZUnconsumedWidget", team="Team Phoenix")
        sent = self.router.dispatch(event, policy)
        assert sent == 0  # auto lane → suppressed, no interruption
        recs = _records(prov_path)
        assert len(recs) == 1
        assert recs[0]["lane"] == Lane.AUTO.value

    def test_untrusted_resolution_pulls_auto_back_to_review(self, prov_path):
        """Same low-reach merge + same auto-granting policy, but the webhook couldn't
        trust the component resolution (metadata.resolution == 'review'). That is
        present-low confidence → the membrane vetoes auto and decides review, and the
        provenance record honestly says 'review' (not 'auto')."""
        policy = compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True))
        event = self.Event(
            type="code.merged",
            subject="ZZUnconsumedWidget",
            team="Team Phoenix",
            metadata={"resolution": "review"},
        )
        self.router.dispatch(event, policy)
        recs = _records(prov_path)
        assert len(recs) == 1
        # The crisp distinction vs. the previous test: identical inputs except the
        # untrusted-resolution signal flips the membrane's decision auto → review.
        assert recs[0]["lane"] == Lane.REVIEW.value

    def test_resolved_signal_stays_neutral_and_can_auto(self, prov_path):
        """The counterpart: an explicitly RESOLVED reach is neutral (no veto), so the
        same low-reach merge still earns auto under the policy. Guards against treating
        any resolution value as a veto."""
        policy = compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True))
        event = self.Event(
            type="code.merged",
            subject="ZZUnconsumedWidget",
            team="Team Phoenix",
            metadata={"resolution": "resolved"},
        )
        sent = self.router.dispatch(event, policy)
        assert sent == 0  # auto → suppressed
        recs = _records(prov_path)
        assert recs[0]["lane"] == Lane.AUTO.value
