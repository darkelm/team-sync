"""Tests for src/agent/propose.py — the divergence classifier (the `propose` lane engine).

Conformance + behavior, ported from token-sync's `packages/governance/src/index.test.ts`
(the §8 proposal transform, §9 `proposalProvenance`, §10 `sampleAuto`). The asserted
behaviors that MUST match the oracle:
  - a divergence finding → an OPEN Proposal in `Lane.PROPOSE`, decided_by PENDING
    ("the loop never decides");
  - provenance is stamped in the propose lane, passed_floor True by definition;
  - the proposer is honored when injected, else attributed to the design owner;
  - `sample_audit` clamps the rate, guarantees at-least-one, samples WITHOUT replacement;
  - ProposalStore round-trips, tolerates a corrupt line, and respects the env path.

Hermetic (SYNCBOT_TEST=1 via conftest): the store path is redirected to a tmp file / env
var so tests NEVER touch the real data/proposals.jsonl.
"""
from __future__ import annotations

from src.agent.membrane import Actor, Lane
from src.agent.propose import (
    DivergenceFinding,
    Proposal,
    classify_divergences,
    proposal_provenance,
    sample_audit,
    ProposalStore,
    PROPOSALS_PATH,
    DEFAULT_DESIGNER,
    DEFAULT_SAMPLE_RATE,
)

FIXED_NOW = "2026-06-19T00:00:00.000Z"


def _finding(**over) -> DivergenceFinding:
    base = dict(
        component="PriceTag",
        divergence_notes="custom corner radius, no library match",
        design_owner="dana",
        code_owner="cory",
        figma_url="https://figma.com/file/abc?node-id=1:2",
        reach=3,
    )
    base.update(over)
    return DivergenceFinding(**base)


# =============================================================================
# 1) A divergence finding → an OPEN Proposal in the propose lane (PENDING)
# =============================================================================

class TestClassifyDivergences:
    def test_finding_becomes_a_propose_lane_proposal(self):
        proposals = classify_divergences([_finding()], now=lambda: FIXED_NOW)
        assert len(proposals) == 1
        p = proposals[0]
        assert isinstance(p, Proposal)
        assert p.provenance.lane == Lane.PROPOSE
        assert p.component == "PriceTag"

    def test_proposal_is_pending_the_loop_never_decides(self):
        p = classify_divergences([_finding()], now=lambda: FIXED_NOW)[0]
        # The defining guarantee: an open proposal is PENDING — no rule, no loop decided.
        assert p.decided_by == {"type": "pending"}
        assert p.provenance.decided_by == {"type": "pending"}

    def test_provenance_clears_floor_by_definition(self):
        # A design-side divergence is not a code-floor failure (oracle TS index.ts:808).
        p = classify_divergences([_finding()], now=lambda: FIXED_NOW)[0]
        assert p.provenance.passed_floor is True

    def test_owners_notes_url_and_reach_carry_through(self):
        p = classify_divergences([_finding()], now=lambda: FIXED_NOW)[0]
        assert p.design_owner == "dana"
        assert p.code_owner == "cory"
        assert p.divergence_notes == "custom corner radius, no library match"
        assert p.figma_url == "https://figma.com/file/abc?node-id=1:2"
        assert p.reach == 3
        assert p.provenance.reach == 3

    def test_item_ref_is_deterministic_and_content_derived(self):
        # Mirrors the oracle's defaultProposalId (TS index.ts:774): stable across runs,
        # no random source.
        a = classify_divergences([_finding()], now=lambda: FIXED_NOW)[0]
        b = classify_divergences([_finding()], now=lambda: FIXED_NOW)[0]
        assert a.item_ref == b.item_ref == "proposal:PriceTag"
        assert a.provenance.item_ref == "proposal:PriceTag"

    def test_at_is_the_injected_clock(self):
        p = classify_divergences([_finding()], now=lambda: FIXED_NOW)[0]
        assert p.at == FIXED_NOW
        assert p.provenance.at == FIXED_NOW

    def test_empty_findings_yield_no_proposals(self):
        assert classify_divergences([], now=lambda: FIXED_NOW) == []

    def test_many_findings_each_become_a_proposal(self):
        findings = [_finding(component=f"C{i}") for i in range(4)]
        proposals = classify_divergences(findings, now=lambda: FIXED_NOW)
        assert [p.component for p in proposals] == ["C0", "C1", "C2", "C3"]
        assert all(p.provenance.lane == Lane.PROPOSE for p in proposals)
        assert all(p.decided_by == {"type": "pending"} for p in proposals)


# =============================================================================
# 2) Proposer attribution (oracle §8: DEFAULT_DESIGNER; injection wins)
# =============================================================================

class TestProposer:
    def test_injected_proposer_is_honored(self):
        agent = Actor(type="agent", id="syncbot", model="claude-opus-4-8")
        p = classify_divergences([_finding()], proposed_by=agent, now=lambda: FIXED_NOW)[0]
        assert p.proposed_by == agent
        assert p.provenance.proposed_by == agent

    def test_absent_proposer_attributes_to_design_owner(self):
        # A divergence is the designer's act — attributed to the design owner (a human).
        p = classify_divergences([_finding(design_owner="dana")], now=lambda: FIXED_NOW)[0]
        assert p.proposed_by == Actor(type="human", id="dana")

    def test_no_design_owner_falls_back_to_generic_designer(self):
        p = classify_divergences([_finding(design_owner="")], now=lambda: FIXED_NOW)[0]
        assert p.proposed_by == DEFAULT_DESIGNER
        assert p.proposed_by == Actor(type="human", id="designer")


# =============================================================================
# 3) proposal_provenance (oracle §9, TS index.ts:806)
# =============================================================================

class TestProposalProvenance:
    def test_open_proposal_is_pending_in_the_propose_lane(self):
        rec = proposal_provenance("proposal:X", DEFAULT_DESIGNER, reach=2, at=FIXED_NOW)
        assert rec.lane == Lane.PROPOSE
        assert rec.decided_by == {"type": "pending"}
        assert rec.passed_floor is True
        assert rec.reach == 2
        assert rec.at == FIXED_NOW

    def test_resolved_decider_is_carried_when_supplied(self):
        # Once a human resolves it, decided_by becomes {human, who} (oracle TS index.ts:811).
        decided = {"type": "human", "who": "cory"}
        rec = proposal_provenance("proposal:X", DEFAULT_DESIGNER, reach=None, at=FIXED_NOW,
                                  decided_by=decided)
        assert rec.decided_by == decided
        assert rec.reach is None  # unknown reach carries through as None


# =============================================================================
# 4) sample_audit (oracle §10, sampleAuto, TS index.ts:841)
# =============================================================================

class TestSampleAudit:
    def test_empty_input_returns_empty(self):
        assert sample_audit([], 0.5) == []

    def test_rate_at_or_below_zero_returns_empty(self):
        items = list(range(10))
        assert sample_audit(items, 0) == []
        assert sample_audit(items, -1) == []

    def test_rate_at_or_above_one_returns_all(self):
        items = list(range(5))
        assert sample_audit(items, 1) == items
        assert sample_audit(items, 5) == items  # clamped to 1

    def test_at_least_one_when_any_exist(self):
        # round(20 * 0.01) == 0, but the floor of one keeps the tier audited (TS index.ts:855).
        items = list(range(20))
        out = sample_audit(items, 0.01, random=lambda: 0.0)
        assert len(out) == 1

    def test_target_count_is_round_of_len_times_rate(self):
        items = list(range(10))
        out = sample_audit(items, 0.3, random=lambda: 0.0)
        assert len(out) == round(10 * 0.3) == 3

    def test_samples_without_replacement(self):
        items = list(range(10))
        out = sample_audit(items, 0.5, random=lambda: 0.0)
        assert len(out) == len(set(id(x) for x in out)) == 5  # no element appears twice

    def test_deterministic_pick_with_injected_rng(self):
        # random()==0.0 always picks index 0 of the shrinking pool → 0,1,2,... in order.
        items = ["a", "b", "c", "d", "e"]
        out = sample_audit(items, 0.6, random=lambda: 0.0)
        assert out == ["a", "b", "c"]  # round(5*0.6)=3, popping index 0 each time

    def test_default_rate_constant(self):
        assert DEFAULT_SAMPLE_RATE == 0.1


# =============================================================================
# 5) ProposalStore — mirrors provenance.py (round-trip, corrupt-line, env path)
# =============================================================================

def _store(tmp_path) -> ProposalStore:
    return ProposalStore(path=str(tmp_path / "proposals.jsonl"))


def _proposals():
    findings = [_finding(component="A"), _finding(component="B")]
    return classify_divergences(findings, now=lambda: FIXED_NOW)


class TestProposalStoreModuleConstant:
    def test_default_path_is_under_data(self):
        assert PROPOSALS_PATH == "data/proposals.jsonl"

    def test_default_instance_uses_constant(self, monkeypatch):
        monkeypatch.delenv("SYNCBOT_PROPOSALS_PATH", raising=False)
        assert ProposalStore().path == PROPOSALS_PATH


class TestProposalStoreRoundTrip:
    def test_append_all_then_recent_preserves_order_newest_last(self, tmp_path):
        store = _store(tmp_path)
        store.append_all(_proposals())
        recent = store.recent(10)
        assert [r["component"] for r in recent] == ["A", "B"]

    def test_round_trip_preserves_pending_and_lane(self, tmp_path):
        store = _store(tmp_path)
        store.append_all(_proposals())
        rec = store.recent(1)[0]
        assert rec["lane"] == "propose"
        assert rec["decidedBy"] == {"type": "pending"}
        assert rec["provenance"]["lane"] == "propose"
        assert rec["provenance"]["passedFloor"] is True

    def test_append_accepts_a_proposal_object(self, tmp_path):
        store = _store(tmp_path)
        store.append(_proposals()[0])
        rec = store.recent(1)[0]
        assert rec["component"] == "A"
        assert rec["designOwner"] == "dana"
        assert rec["codeOwner"] == "cory"

    def test_recent_n_zero_or_negative_returns_empty(self, tmp_path):
        store = _store(tmp_path)
        store.append_all(_proposals())
        assert store.recent(0) == []
        assert store.recent(-3) == []

    def test_recent_returns_at_most_n_newest_last(self, tmp_path):
        store = _store(tmp_path)
        store.append_all(classify_divergences(
            [_finding(component=f"C{i}") for i in range(5)], now=lambda: FIXED_NOW))
        recent = store.recent(2)
        assert [r["component"] for r in recent] == ["C3", "C4"]  # newest last

    def test_recent_on_missing_file_is_empty(self, tmp_path):
        store = _store(tmp_path)  # nothing appended; file does not exist
        assert store.recent(10) == []
        assert store.all() == []


class TestProposalStoreDurability:
    def test_proposals_survive_a_fresh_store_instance(self, tmp_path):
        path = str(tmp_path / "proposals.jsonl")
        ProposalStore(path=path).append_all(_proposals())
        reread = ProposalStore(path=path).all()
        assert [r["component"] for r in reread] == ["A", "B"]


class TestProposalStoreCorruptionTolerance:
    def test_a_corrupt_line_is_skipped_not_fatal(self, tmp_path):
        path = str(tmp_path / "proposals.jsonl")
        store = ProposalStore(path=path)
        store.append(classify_divergences([_finding(component="good1")], now=lambda: FIXED_NOW)[0])
        with open(path, "a") as f:
            f.write("{not valid json\n")
        store.append(classify_divergences([_finding(component="good2")], now=lambda: FIXED_NOW)[0])
        comps = [r["component"] for r in store.all()]
        assert comps == ["good1", "good2"]  # corrupt line skipped, rest intact


class TestProposalStoreEnvPath:
    def test_env_var_redirects_the_store(self, tmp_path, monkeypatch):
        # The env path MUST be honored — and tests MUST NOT write the real data/ file.
        env_path = str(tmp_path / "from_env.jsonl")
        monkeypatch.setenv("SYNCBOT_PROPOSALS_PATH", env_path)
        store = ProposalStore()  # no explicit arg → env path
        assert store.path == env_path
        store.append_all(_proposals())
        assert [r["component"] for r in ProposalStore().all()] == ["A", "B"]

    def test_explicit_arg_beats_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SYNCBOT_PROPOSALS_PATH", str(tmp_path / "env.jsonl"))
        explicit = str(tmp_path / "explicit.jsonl")
        assert ProposalStore(path=explicit).path == explicit
