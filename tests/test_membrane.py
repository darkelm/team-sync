"""Conformance oracle for the governance membrane port (src/agent/membrane.py).

Ported from token-sync's `packages/governance/src/index.test.ts`. The behaviors that
MUST match the TS original exactly (contract §10):
  - precedence order (floor → novel/low-confidence → policy → default),
  - most-specific-wins + earliest-index tie-break,
  - `reach_over` = STRICTLY greater,
  - confidence VETO-when-present / NEUTRAL-when-absent (absent confidence STILL earns auto),
  - the conservative default (no rules ⇒ everything review),
  - provenance always names a decider.

The one DELIBERATE divergence from the TS oracle is the reach ceiling: the team port uses
TEAM_REACH_CEILING (1), not the token-scale 5. Tests reference the constant, not 5, so the
recalibration is honored without breaking conformance on the LOGIC.
"""
from __future__ import annotations

from src.agent.membrane import (
    Lane,
    RouteItem,
    RouteContext,
    ReviewRule,
    ReviewPolicy,
    Actor,
    DesignerToggles,
    default_policy,
    default_toggles,
    compile_policy_from_toggles,
    route,
    select_rule,
    can_auto_flow,
    tier_of,
    LOW_CONFIDENCE_THRESHOLD,
    TEAM_REACH_CEILING,
)

FIXED_NOW = "2026-06-19T00:00:00.000Z"
OPTS = {"now": lambda: FIXED_NOW}


def ctx(**over) -> RouteContext:
    base = {"blast_radius": {}, "p1_keys": []}
    base.update(over)
    return RouteContext(**base)


# =============================================================================
# 1) Default policy => everything routes to review
# =============================================================================

class TestDefaultPolicy:
    def test_is_fully_gated(self):
        p = default_policy()
        assert p.rules == []
        assert p.default_lane == Lane.REVIEW

    def test_routes_every_change_to_review(self):
        items = [
            RouteItem("k1", "base/space/sm", "changed", mode="default"),
            RouteItem("k2", "brand/color/primary", "changed"),
            RouteItem("k3", "sys/color/bg", "added"),
            RouteItem("k4", "base/radius/lg", "renamed"),
        ]
        decisions = route(items, ctx(blast_radius={"k1": 0, "k2": 500, "k3": 1, "k4": 3}),
                          default_policy(), **OPTS)
        assert [d.lane for d in decisions] == [Lane.REVIEW] * 4
        # routed by default/precedence, not a rule
        assert all(d.rule_id is None for d in decisions)


# =============================================================================
# 2) Floor (P1) => blocked, ahead of policy
# =============================================================================

class TestFloorPrecedence:
    def test_p1_floor_blocks_even_when_a_rule_would_allow_auto(self):
        items = [RouteItem("k1", "base/space/sm", "changed")]
        policy = compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True))
        decisions = route(items, ctx(p1_keys=["k1"], blast_radius={"k1": 0}), policy, **OPTS)
        assert decisions[0].lane == Lane.BLOCKED
        assert decisions[0].provenance.passed_floor is False
        # blocked items await a human to clear the floor
        assert decisions[0].provenance.decided_by == {"type": "pending"}


# =============================================================================
# 3) Low-reach value tweak + small_tweaks_flow => auto
# =============================================================================

class TestEarnedAutonomy:
    def test_low_reach_changed_flows_to_auto(self):
        items = [RouteItem("k1", "base/space/sm", "changed", mode="default")]
        policy = compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True))
        # No confidence signal wired — the policy + low-reach gates earn auto.
        decisions = route(items, ctx(blast_radius={"k1": TEAM_REACH_CEILING}), policy, **OPTS)
        assert decisions[0].lane == Lane.AUTO
        assert decisions[0].rule_id is not None
        assert decisions[0].item_ref == "k1#default"  # mode-scoped itemRef

    def test_raw_tweaks_flow_to_auto_when_spacing_flow_on(self):
        items = [RouteItem("k1", "base/space/sm", "changed")]
        policy = compile_policy_from_toggles(DesignerToggles(spacing_tweaks_flow=True))
        decisions = route(items, ctx(blast_radius={"k1": TEAM_REACH_CEILING}), policy, **OPTS)
        assert decisions[0].lane == Lane.AUTO

    def test_high_reach_does_not_reach_auto_even_with_flow_on(self):
        items = [RouteItem("k1", "base/space/sm", "changed")]
        policy = compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True))
        decisions = route(items, ctx(blast_radius={"k1": TEAM_REACH_CEILING + 1}), policy, **OPTS)
        assert decisions[0].lane == Lane.REVIEW


# =============================================================================
# 4) High-reach / brand / rename / new / low-confidence / novel => review
# =============================================================================

class TestConsequentialAsks:
    def _flow_policy(self) -> ReviewPolicy:
        return compile_policy_from_toggles(DesignerToggles(
            brand_changes_always_ask=True,
            new_tokens_always_ask=True,
            renames_removals_always_ask=True,
            small_tweaks_flow=True,
            spacing_tweaks_flow=True,
        ))

    def test_brand_tier_change_reviews_even_with_flow_on(self):
        items = [RouteItem("k1", "brand/color/primary", "changed")]
        decisions = route(items, ctx(blast_radius={"k1": TEAM_REACH_CEILING}), self._flow_policy(), **OPTS)
        assert decisions[0].lane == Lane.REVIEW

    def test_new_token_added_reviews(self):
        items = [RouteItem("k1", "base/space/xl", "added")]
        decisions = route(items, ctx(blast_radius={"k1": 0}), self._flow_policy(), **OPTS)
        assert decisions[0].lane == Lane.REVIEW

    def test_rename_and_removal_review(self):
        items = [
            RouteItem("k1", "base/space/sm", "renamed"),
            RouteItem("k2", "base/space/md", "removed"),
        ]
        decisions = route(items, ctx(blast_radius={"k1": 1, "k2": 1}), self._flow_policy(), **OPTS)
        assert [d.lane for d in decisions] == [Lane.REVIEW, Lane.REVIEW]

    def test_low_confidence_reviews_even_when_rule_would_auto(self):
        items = [RouteItem("k1", "base/space/sm", "changed")]
        decisions = route(
            items,
            ctx(blast_radius={"k1": TEAM_REACH_CEILING}, confidence={"k1": LOW_CONFIDENCE_THRESHOLD - 0.01}),
            compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True)),
            **OPTS,
        )
        assert decisions[0].lane == Lane.REVIEW

    def test_novel_value_reviews_even_when_rule_would_auto(self):
        items = [RouteItem("k1", "base/space/sm", "changed")]
        decisions = route(
            items,
            ctx(blast_radius={"k1": TEAM_REACH_CEILING}, novel_keys=["k1"]),
            compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True)),
            **OPTS,
        )
        assert decisions[0].lane == Lane.REVIEW

    def test_high_reach_with_no_flow_rule_reviews_via_default(self):
        items = [RouteItem("k1", "base/space/sm", "changed")]
        decisions = route(items, ctx(blast_radius={"k1": 999}), default_policy(), **OPTS)
        assert decisions[0].lane == Lane.REVIEW


# =============================================================================
# 4b) Sparse / MISSING signals — v1 semantics (the CRITICAL absent-confidence cases)
# =============================================================================

class TestSparseSignalsFailSafe:
    def _auto_granting_policy(self) -> ReviewPolicy:
        return compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True))

    def test_a_no_confidence_entry_still_earns_auto(self):
        """ABSENT confidence is NEUTRAL in v1 — it must NOT make the FLOW toggle inert.
        This is the load-bearing case the scoping doc had backwards."""
        items = [RouteItem("k1", "base/space/sm", "changed")]
        # ctx() supplies NO confidence map at all — confidence is absent for k1.
        decisions = route(items, ctx(blast_radius={"k1": TEAM_REACH_CEILING}),
                          self._auto_granting_policy(), **OPTS)
        assert decisions[0].lane == Lane.AUTO

    def test_a2_absent_confidence_falls_to_policy_default_not_unconditional_review(self):
        """Absent confidence must not FORCE review on its own — with the auto grant
        denied it lands on the (non-review) default. Proves absence DEMOTES, not forces."""
        items = [RouteItem("k1", "base/space/sm", "changed")]
        policy = ReviewPolicy(rules=[], default_lane=Lane.DIGEST)
        decisions = route(items, ctx(blast_radius={"k1": TEAM_REACH_CEILING}), policy, **OPTS)
        assert decisions[0].lane == Lane.DIGEST

    def test_b_absent_novel_keys_routes_normally(self):
        items = [RouteItem("k1", "base/space/sm", "changed")]
        decisions = route(
            items, ctx(blast_radius={"k1": TEAM_REACH_CEILING}, confidence={"k1": 0.95}),
            self._auto_granting_policy(), **OPTS,
        )
        assert decisions[0].lane == Lane.AUTO

    def test_b2_empty_novel_keys_identical_to_absent(self):
        items = [RouteItem("k1", "base/space/sm", "changed")]
        with_empty = route(
            items, ctx(blast_radius={"k1": TEAM_REACH_CEILING}, confidence={"k1": 0.95}, novel_keys=[]),
            self._auto_granting_policy(), **OPTS,
        )
        with_absent = route(
            items, ctx(blast_radius={"k1": TEAM_REACH_CEILING}, confidence={"k1": 0.95}),
            self._auto_granting_policy(), **OPTS,
        )
        assert with_empty[0].lane == Lane.AUTO
        assert with_absent[0].lane == with_empty[0].lane

    def test_c_present_low_confidence_vetoes_auto(self):
        """The veto-when-present half: a PRESENT low reading pulls an otherwise-auto
        change back to review."""
        items = [RouteItem("k1", "base/space/sm", "changed")]
        decisions = route(
            items,
            ctx(blast_radius={"k1": TEAM_REACH_CEILING}, confidence={"k1": LOW_CONFIDENCE_THRESHOLD - 0.01}),
            self._auto_granting_policy(), **OPTS,
        )
        assert decisions[0].lane == Lane.REVIEW

    def test_confidence_exactly_at_threshold_still_earns_auto(self):
        """Boundary: confidence at the floor is high enough (< THRESHOLD is low; >= ok)."""
        items = [RouteItem("k1", "base/space/sm", "changed")]
        decisions = route(
            items,
            ctx(blast_radius={"k1": TEAM_REACH_CEILING}, confidence={"k1": LOW_CONFIDENCE_THRESHOLD}),
            self._auto_granting_policy(), **OPTS,
        )
        assert decisions[0].lane == Lane.AUTO

    def test_can_auto_flow_unit_absent_confidence_is_true(self):
        """Direct unit check on the gate: absent confidence ⇒ neutral ⇒ True at low reach."""
        assert can_auto_flow(TEAM_REACH_CEILING, None) is True
        assert can_auto_flow(TEAM_REACH_CEILING + 1, None) is False  # high reach never auto
        assert can_auto_flow(0, LOW_CONFIDENCE_THRESHOLD - 0.01) is False  # present-low vetoes
        assert can_auto_flow(0, LOW_CONFIDENCE_THRESHOLD) is True  # present-ok


# =============================================================================
# 5) compile_policy_from_toggles — conservative default routes nothing to auto
# =============================================================================

class TestCompilePolicyFromToggles:
    def test_default_toggles_route_nothing_to_auto(self):
        policy = compile_policy_from_toggles()  # == default_toggles()
        items = [
            RouteItem("k1", "base/space/sm", "changed"),
            RouteItem("k2", "brand/color/primary", "changed"),
            RouteItem("k3", "base/radius/lg", "added"),
        ]
        decisions = route(items, ctx(blast_radius={"k1": 0, "k2": 0, "k3": 0}), policy, **OPTS)
        assert not any(d.lane == Lane.AUTO for d in decisions)
        assert all(d.lane == Lane.REVIEW for d in decisions)

    def test_default_toggles_only_asks_on(self):
        t = default_toggles()
        assert t.brand_changes_always_ask is True
        assert t.new_tokens_always_ask is True
        assert t.renames_removals_always_ask is True
        assert t.small_tweaks_flow is False
        assert t.spacing_tweaks_flow is False

    def test_brand_change_asked_even_when_small_flow_on_most_specific_wins(self):
        policy = compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True, brand_changes_always_ask=True))
        items = [RouteItem("k1", "brand/color/primary", "changed")]
        decisions = route(items, ctx(blast_radius={"k1": TEAM_REACH_CEILING}), policy, **OPTS)
        assert decisions[0].lane == Lane.REVIEW


# =============================================================================
# 6) select_rule — most-specific wins, earliest-index ties, reach_over strictly greater
# =============================================================================

class TestSelectRule:
    def test_most_specific_wins(self):
        # A 2-condition rule (auto) and a catch-all 0-condition rule (review): the more
        # specific one wins for a matching item.
        policy = ReviewPolicy(rules=[
            ReviewRule(lane=Lane.REVIEW),  # catch-all, specificity 0
            ReviewRule(tier="raw", kind=["changed"], lane=Lane.AUTO),  # specificity 2
        ], default_lane=Lane.REVIEW)
        item = RouteItem("k1", "base/space/sm", "changed")
        match = select_rule(policy, item, reach=0, is_novel=False)
        assert match is not None
        rule, rule_id = match
        assert rule.lane == Lane.AUTO
        assert rule_id == "rule#1"

    def test_earliest_index_breaks_specificity_ties(self):
        # Two equally specific (1-condition) matching rules: the EARLIER one wins.
        policy = ReviewPolicy(rules=[
            ReviewRule(kind=["changed"], lane=Lane.DIGEST),  # index 0
            ReviewRule(kind=["changed"], lane=Lane.AUTO),    # index 1 (same specificity)
        ], default_lane=Lane.REVIEW)
        item = RouteItem("k1", "base/space/sm", "changed")
        match = select_rule(policy, item, reach=0, is_novel=False)
        assert match is not None
        rule, rule_id = match
        assert rule.lane == Lane.DIGEST
        assert rule_id == "rule#0"

    def test_reach_over_is_strictly_greater(self):
        # reach_over=1 matches reach 2 but NOT reach 1 (strictly greater).
        rule = ReviewRule(reach_over=1, lane=Lane.REVIEW)
        policy = ReviewPolicy(rules=[rule], default_lane=Lane.DIGEST)
        item = RouteItem("k1", "base/space/sm", "changed")
        assert select_rule(policy, item, reach=2, is_novel=False) is not None
        assert select_rule(policy, item, reach=1, is_novel=False) is None  # equal, not greater
        assert select_rule(policy, item, reach=0, is_novel=False) is None

    def test_no_candidate_returns_none(self):
        policy = ReviewPolicy(rules=[ReviewRule(tier="brand", lane=Lane.REVIEW)], default_lane=Lane.DIGEST)
        item = RouteItem("k1", "base/space/sm", "changed")  # tier raw, not brand
        assert select_rule(policy, item, reach=0, is_novel=False) is None

    def test_novel_condition_matches_on_novelty(self):
        rule = ReviewRule(novel=True, lane=Lane.REVIEW)
        policy = ReviewPolicy(rules=[rule], default_lane=Lane.DIGEST)
        item = RouteItem("k1", "base/space/sm", "changed")
        assert select_rule(policy, item, reach=0, is_novel=True) is not None
        assert select_rule(policy, item, reach=0, is_novel=False) is None


class TestTierOf:
    def test_tier_head_segment_mapping(self):
        assert tier_of(RouteItem("k", "brand/color/x", "changed")) == "brand"
        assert tier_of(RouteItem("k", "sys/color/x", "changed")) == "shared"
        assert tier_of(RouteItem("k", "system/color/x", "changed")) == "shared"
        assert tier_of(RouteItem("k", "semantic/x", "changed")) == "shared"
        assert tier_of(RouteItem("k", "shared/x", "changed")) == "shared"
        assert tier_of(RouteItem("k", "base/x", "changed")) == "raw"
        assert tier_of(RouteItem("k", "palette/x", "changed")) == "raw"
        assert tier_of(RouteItem("k", "", "changed")) == "raw"


# =============================================================================
# 7) Provenance shape — names proposer + decider (always)
# =============================================================================

class TestProvenanceNamesDecider:
    def test_rule_decision_names_proposer_and_rule(self):
        agent = Actor(type="agent", id="syncbot", model="claude-opus-4-8")
        items = [RouteItem("k1", "base/space/sm", "changed")]
        decisions = route(
            items,
            ctx(blast_radius={"k1": TEAM_REACH_CEILING}, confidence={"k1": 0.9}),
            compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True)),
            proposed_by=agent, **OPTS,
        )
        rec = decisions[0].provenance
        assert rec.proposed_by == agent
        assert rec.lane == Lane.AUTO
        assert rec.decided_by == {"type": "rule", "ruleId": decisions[0].rule_id}
        assert rec.passed_floor is True
        assert rec.reach == TEAM_REACH_CEILING
        assert rec.at == FIXED_NOW

    def test_every_decision_names_a_concrete_decider(self):
        """The 'never just the loop decided' invariant: decided_by is always rule|human|pending."""
        items = [
            RouteItem("k1", "base/space/sm", "changed"),   # default → pending
            RouteItem("k2", "brand/color/primary", "changed"),  # blocked via floor → pending
            RouteItem("k3", "base/space/md", "changed"),   # auto via rule → rule
        ]
        policy = compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True))
        decisions = route(
            items,
            ctx(blast_radius={"k1": 0, "k2": 0, "k3": TEAM_REACH_CEILING}, p1_keys=["k2"]),
            policy, **OPTS,
        )
        for d in decisions:
            assert d.provenance.decided_by.get("type") in ("rule", "human", "pending")
            assert d.provenance.proposed_by is not None

    def test_default_proposer_is_named_agent(self):
        items = [RouteItem("k1", "base/space/sm", "changed")]
        decisions = route(items, ctx(blast_radius={"k1": 0}), default_policy(), **OPTS)
        rec = decisions[0].provenance
        assert rec.proposed_by.type == "agent"
        assert rec.proposed_by.id == "syncbot"
