"""Tests for the RESOLUTION WORKFLOW added to src/agent/propose.py.

The validated team need: design↔code divergences must be RESOLVED (tracked to "done"),
not just detected. These tests cover the small, append-only state machine layered over the
proposals ledger:
  - open → claim → resolve and open → accept;
  - `current()` fold correctness (latest transition wins; multiple proposals; OPEN default);
  - resolution provenance names the HUMAN (decided_by type "human"), never pending;
  - illegal-transition handling (raises a clear ValueError);
  - append-only audit retained (every transition still in `all()`; proposals never mutated);
  - progress counts by status;
  - backward-compat (a proposal with no transitions reads OPEN; append/recent/all unchanged).

Hermetic (SYNCBOT_TEST=1 via conftest): every store uses an explicit tmp path so tests
NEVER touch the real data/proposals.jsonl.
"""
from __future__ import annotations

import pytest

from src.agent.membrane import Actor, Lane
from src.agent.propose import (
    DivergenceFinding,
    ProposalStore,
    ProposalStatus,
    ProposalTransition,
    ResolutionKind,
    CurrentProposal,
    classify_divergences,
    claim,
    resolve,
    accept,
    progress,
)

FIXED_NOW = "2026-06-19T00:00:00.000Z"

CORY = Actor(type="human", id="cory")
DANA = Actor(type="human", id="dana")
SYNCBOT = Actor(type="agent", id="syncbot", model="claude-opus-4-8")


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


def _store(tmp_path) -> ProposalStore:
    return ProposalStore(path=str(tmp_path / "proposals.jsonl"))


def _seed_one(store, component="PriceTag") -> str:
    """Open one proposal in the store; return its item_ref."""
    p = classify_divergences([_finding(component=component)], now=lambda: FIXED_NOW)[0]
    store.append(p)
    return p.item_ref


# =============================================================================
# 1) Happy paths — open → claim → resolve, and open → accept
# =============================================================================

class TestResolutionHappyPaths:
    def test_open_then_claim_then_resolve(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)

        # Fresh proposal reads OPEN (no transitions yet).
        assert store.current(ref).status == ProposalStatus.OPEN

        t1 = claim(store, ref, CORY, now=lambda: FIXED_NOW)
        assert isinstance(t1, ProposalTransition)
        assert t1.from_status == ProposalStatus.OPEN
        assert t1.to_status == ProposalStatus.CLAIMED
        view = store.current(ref)
        assert view.status == ProposalStatus.CLAIMED
        assert view.assignee == "cory"

        t2 = resolve(store, ref, CORY, ResolutionKind.CODE_UPDATED, note="adopted in DS",
                     now=lambda: FIXED_NOW)
        assert t2.from_status == ProposalStatus.CLAIMED
        assert t2.to_status == ProposalStatus.RESOLVED
        assert t2.resolution_kind == ResolutionKind.CODE_UPDATED

        view = store.current(ref)
        assert view.status == ProposalStatus.RESOLVED
        assert view.resolution_kind == ResolutionKind.CODE_UPDATED
        assert view.resolved_by == "cory"
        assert view.assignee == "cory"  # carried forward from the claim

    def test_open_then_accept_wont_fix(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)

        t = accept(store, ref, DANA, note="intentional brand exception", now=lambda: FIXED_NOW)
        assert t.to_status == ProposalStatus.ACCEPTED
        assert t.resolution_kind == ResolutionKind.DIVERGENCE_ACCEPTED

        view = store.current(ref)
        assert view.status == ProposalStatus.ACCEPTED
        assert view.resolution_kind == ResolutionKind.DIVERGENCE_ACCEPTED
        assert view.resolved_by == "dana"

    def test_resolve_directly_from_open_without_claim(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        t = resolve(store, ref, DANA, ResolutionKind.DESIGN_UPDATED, now=lambda: FIXED_NOW)
        assert t.from_status == ProposalStatus.OPEN
        assert store.current(ref).status == ProposalStatus.RESOLVED


# =============================================================================
# 2) current() fold correctness
# =============================================================================

class TestCurrentFold:
    def test_no_transitions_reads_open(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        view = store.current(ref)
        assert view.status == ProposalStatus.OPEN
        assert view.assignee is None
        assert view.resolution_kind is None
        assert view.proposal["component"] == "PriceTag"

    def test_latest_transition_wins(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        claim(store, ref, CORY, now=lambda: FIXED_NOW)
        resolve(store, ref, CORY, ResolutionKind.CODE_UPDATED, now=lambda: FIXED_NOW)
        # Despite multiple appended transitions, the fold yields the latest state.
        assert store.current(ref).status == ProposalStatus.RESOLVED

    def test_current_no_arg_returns_all_proposals_oldest_first(self, tmp_path):
        store = _store(tmp_path)
        store.append_all(classify_divergences(
            [_finding(component="A"), _finding(component="B"), _finding(component="C")],
            now=lambda: FIXED_NOW))
        claim(store, "proposal:B", CORY, now=lambda: FIXED_NOW)
        accept(store, "proposal:C", DANA, now=lambda: FIXED_NOW)

        views = store.current()
        assert all(isinstance(v, CurrentProposal) for v in views)
        assert [v.item_ref for v in views] == ["proposal:A", "proposal:B", "proposal:C"]
        by_ref = {v.item_ref: v for v in views}
        assert by_ref["proposal:A"].status == ProposalStatus.OPEN
        assert by_ref["proposal:B"].status == ProposalStatus.CLAIMED
        assert by_ref["proposal:C"].status == ProposalStatus.ACCEPTED

    def test_current_unknown_ref_is_none(self, tmp_path):
        store = _store(tmp_path)
        _seed_one(store)
        assert store.current("proposal:DoesNotExist") is None

    def test_current_on_missing_store_file_is_empty(self, tmp_path):
        store = _store(tmp_path)  # nothing appended; file does not exist
        assert store.current() == []
        assert store.current("proposal:Anything") is None


# =============================================================================
# 3) Resolution provenance names the human (decided_by type "human", never pending)
# =============================================================================

class TestResolutionProvenance:
    def test_resolve_stamps_human_decider_in_provenance(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        resolve(store, ref, CORY, ResolutionKind.CODE_UPDATED, now=lambda: FIXED_NOW)

        prov_rows = [r for r in store.all()
                     if r.get("kind") != "transition"
                     and r.get("itemRef") == ref
                     and r.get("lane") == Lane.PROPOSE.value
                     and r.get("decidedBy", {}).get("type") == "human"]
        assert len(prov_rows) == 1
        assert prov_rows[0]["decidedBy"] == {"type": "human", "who": "cory"}
        assert prov_rows[0]["lane"] == "propose"

    def test_accept_stamps_human_decider_in_provenance(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        accept(store, ref, DANA, now=lambda: FIXED_NOW)
        human_rows = [r for r in store.all()
                      if r.get("decidedBy", {}).get("type") == "human"]
        assert any(r["decidedBy"] == {"type": "human", "who": "dana"} for r in human_rows)

    def test_claim_does_not_close_so_no_human_provenance_yet(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        claim(store, ref, CORY, now=lambda: FIXED_NOW)
        # CLAIMED is not terminal: no human-decided provenance row should appear yet.
        human_closers = [r for r in store.all()
                         if r.get("kind") != "transition"
                         and r.get("decidedBy", {}).get("type") == "human"]
        assert human_closers == []


# =============================================================================
# 4) Illegal-transition handling — raises a clear ValueError
# =============================================================================

class TestIllegalTransitions:
    def test_resolving_an_already_resolved_item_raises(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        resolve(store, ref, CORY, ResolutionKind.CODE_UPDATED, now=lambda: FIXED_NOW)
        with pytest.raises(ValueError):
            resolve(store, ref, CORY, ResolutionKind.CODE_UPDATED, now=lambda: FIXED_NOW)

    def test_claiming_a_resolved_item_raises(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        resolve(store, ref, CORY, ResolutionKind.CODE_UPDATED, now=lambda: FIXED_NOW)
        with pytest.raises(ValueError):
            claim(store, ref, DANA, now=lambda: FIXED_NOW)

    def test_claiming_an_already_claimed_item_raises(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        claim(store, ref, CORY, now=lambda: FIXED_NOW)
        with pytest.raises(ValueError):
            claim(store, ref, DANA, now=lambda: FIXED_NOW)

    def test_accepting_a_resolved_item_raises(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        resolve(store, ref, CORY, ResolutionKind.DESIGN_UPDATED, now=lambda: FIXED_NOW)
        with pytest.raises(ValueError):
            accept(store, ref, DANA, now=lambda: FIXED_NOW)

    def test_non_human_actor_is_rejected(self, tmp_path):
        # "The loop never decides": an agent cannot drive a transition.
        store = _store(tmp_path)
        ref = _seed_one(store)
        with pytest.raises(ValueError):
            claim(store, ref, SYNCBOT, now=lambda: FIXED_NOW)

    def test_illegal_transition_does_not_append_a_row(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        resolve(store, ref, CORY, ResolutionKind.CODE_UPDATED, now=lambda: FIXED_NOW)
        before = len(store.all())
        with pytest.raises(ValueError):
            claim(store, ref, DANA, now=lambda: FIXED_NOW)
        assert len(store.all()) == before  # nothing written on the rejected transition


# =============================================================================
# 5) Append-only audit retained — transitions persist; proposals never mutated
# =============================================================================

class TestAppendOnlyAudit:
    def test_all_transitions_remain_in_the_ledger(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        claim(store, ref, CORY, now=lambda: FIXED_NOW)
        resolve(store, ref, CORY, ResolutionKind.CODE_UPDATED, now=lambda: FIXED_NOW)

        trans = store.transitions()
        assert [t["toStatus"] for t in trans] == ["claimed", "resolved"]
        # Both transitions are also present in the raw, complete ledger.
        assert len(store.transitions()) == 2

    def test_opened_proposal_row_is_not_rewritten(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        original = store.proposals()[0]
        claim(store, ref, CORY, now=lambda: FIXED_NOW)
        resolve(store, ref, CORY, ResolutionKind.CODE_UPDATED, now=lambda: FIXED_NOW)
        # Exactly ONE opened-proposal row, still PENDING / unchanged (audit is append-only).
        opened = store.proposals()
        assert len(opened) == 1
        assert opened[0] == original
        assert opened[0]["decidedBy"] == {"type": "pending"}

    def test_proposals_and_transitions_helpers_partition_the_ledger(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        claim(store, ref, CORY, now=lambda: FIXED_NOW)
        # all() == proposals() + (interleaved) transitions() + provenance rows.
        # Here: 1 opened proposal + 1 transition (claim is non-terminal, no provenance row).
        assert len(store.proposals()) == 1
        assert len(store.transitions()) == 1
        assert len(store.all()) == 2

    def test_transition_to_dict_carries_actor_and_kind(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        resolve(store, ref, CORY, ResolutionKind.CODE_UPDATED, note="n", now=lambda: FIXED_NOW)
        row = store.transitions()[0]
        assert row["kind"] == "transition"
        assert row["itemRef"] == ref
        assert row["actor"] == {"type": "human", "id": "cory"}
        assert row["resolutionKind"] == "code_updated"
        assert row["note"] == "n"
        assert row["at"] == FIXED_NOW


# =============================================================================
# 6) Progress counts
# =============================================================================

class TestProgress:
    def test_progress_counts_by_status(self, tmp_path):
        store = _store(tmp_path)
        store.append_all(classify_divergences(
            [_finding(component=c) for c in ("A", "B", "C", "D", "E")],
            now=lambda: FIXED_NOW))
        claim(store, "proposal:B", CORY, now=lambda: FIXED_NOW)
        resolve(store, "proposal:C", CORY, ResolutionKind.CODE_UPDATED, now=lambda: FIXED_NOW)
        accept(store, "proposal:D", DANA, now=lambda: FIXED_NOW)
        # A and E untouched ⇒ open=2, claimed=1, resolved=1, accepted=1.
        assert progress(store) == {"open": 2, "claimed": 1, "resolved": 1, "accepted": 1}

    def test_progress_method_matches_function(self, tmp_path):
        store = _store(tmp_path)
        _seed_one(store)
        assert store.progress() == progress(store)

    def test_progress_on_empty_store_is_all_zero(self, tmp_path):
        store = _store(tmp_path)
        assert progress(store) == {"open": 0, "claimed": 0, "resolved": 0, "accepted": 0}


# =============================================================================
# 7) Backward compatibility — existing API unchanged
# =============================================================================

class TestBackwardCompat:
    def test_proposal_with_no_transitions_reads_open(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        assert store.current(ref).status == ProposalStatus.OPEN

    def test_append_recent_all_unchanged_for_proposals(self, tmp_path):
        store = _store(tmp_path)
        store.append_all(classify_divergences(
            [_finding(component="A"), _finding(component="B")], now=lambda: FIXED_NOW))
        # recent/all still return proposal rows in append order (v1 behavior preserved).
        assert [r["component"] for r in store.recent(10)] == ["A", "B"]
        assert [r["component"] for r in store.all()] == ["A", "B"]

    def test_recent_returns_raw_rows_including_transitions(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        claim(store, ref, CORY, now=lambda: FIXED_NOW)
        # recent tails the FULL ledger; newest row is the transition.
        last = store.recent(1)[0]
        assert last["kind"] == "transition"

    def test_proposals_helper_ignores_transitions(self, tmp_path):
        store = _store(tmp_path)
        ref = _seed_one(store)
        claim(store, ref, CORY, now=lambda: FIXED_NOW)
        assert [r["component"] for r in store.proposals()] == ["PriceTag"]
