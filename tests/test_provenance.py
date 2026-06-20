"""Tests for src/agent/provenance.py — the durable, append-only provenance store.

Ports the relevant token-sync store behaviors (append + recent, newest last, n<=0 ⇒ [])
and adds the durability-across-instances guarantee the JSONL file gives (the whole reason
this is durable, not in-memory). The store path is redirected to a tmp file so tests never
touch the real data/provenance.jsonl.
"""
from __future__ import annotations

from src.agent.provenance import ProvenanceStore, PROVENANCE_PATH
from src.agent.membrane import (
    RouteItem, RouteContext, Actor, Lane,
    default_policy, compile_policy_from_toggles, DesignerToggles, route,
    TEAM_REACH_CEILING,
)

FIXED_NOW = "2026-06-19T00:00:00.000Z"


def _store(tmp_path) -> ProvenanceStore:
    return ProvenanceStore(path=str(tmp_path / "provenance.jsonl"))


def _decisions():
    items = [
        RouteItem("k1", "base/space/sm", "changed"),
        RouteItem("k2", "brand/color/primary", "changed"),
    ]
    return route(
        items,
        RouteContext(blast_radius={"k1": 1, "k2": 1}, p1_keys=[]),
        default_policy(),
        now=lambda: FIXED_NOW,
    )


class TestModuleConstant:
    def test_default_path_is_under_data(self):
        # Mirrors the preferences.py data/ idiom; tests redirect via the path arg.
        assert PROVENANCE_PATH == "data/provenance.jsonl"

    def test_default_instance_uses_constant(self):
        assert ProvenanceStore().path == PROVENANCE_PATH


class TestAppendAndRecent:
    def test_append_then_recent_preserves_order_newest_last(self, tmp_path):
        store = _store(tmp_path)
        store.append_decisions(_decisions())
        recent = store.recent(10)
        assert [r["itemRef"] for r in recent] == ["k1", "k2"]

    def test_recent_returns_at_most_n_newest_last(self, tmp_path):
        store = _store(tmp_path)
        for i in range(5):
            store.append({"itemRef": f"k{i}", "proposedBy": {"type": "agent", "id": "syncbot"},
                          "lane": "review", "decidedBy": {"type": "pending"},
                          "reach": 0, "passedFloor": True, "at": FIXED_NOW})
        recent = store.recent(2)
        assert [r["itemRef"] for r in recent] == ["k3", "k4"]  # newest last

    def test_recent_n_zero_or_negative_returns_empty(self, tmp_path):
        store = _store(tmp_path)
        store.append_decisions(_decisions())
        assert store.recent(0) == []
        assert store.recent(-5) == []

    def test_recent_on_missing_file_is_empty(self, tmp_path):
        store = _store(tmp_path)  # nothing appended yet, file does not exist
        assert store.recent(10) == []
        assert store.all() == []

    def test_append_accepts_a_provenance_record_object(self, tmp_path):
        store = _store(tmp_path)
        decisions = _decisions()
        # append() must accept the dataclass directly (normalizes via .to_dict()).
        store.append(decisions[0].provenance)
        recent = store.recent(1)
        assert recent[0]["itemRef"] == "k1"
        assert recent[0]["lane"] == "review"


class TestDurability:
    def test_records_survive_a_fresh_store_instance(self, tmp_path):
        """The durability guarantee: a NEW store over the same path reads prior records
        (this is the whole point vs. token-sync's in-memory-per-run store)."""
        path = str(tmp_path / "provenance.jsonl")
        ProvenanceStore(path=path).append_decisions(_decisions())
        # Simulate a separate process / later invocation:
        reread = ProvenanceStore(path=path).recent(10)
        assert [r["itemRef"] for r in reread] == ["k1", "k2"]

    def test_appends_accumulate_across_instances(self, tmp_path):
        path = str(tmp_path / "provenance.jsonl")
        ProvenanceStore(path=path).append({"itemRef": "a", "proposedBy": {"type": "agent", "id": "syncbot"},
                                           "lane": "auto", "decidedBy": {"type": "rule", "ruleId": "rule#0"},
                                           "reach": 0, "passedFloor": True, "at": FIXED_NOW})
        ProvenanceStore(path=path).append({"itemRef": "b", "proposedBy": {"type": "agent", "id": "syncbot"},
                                           "lane": "review", "decidedBy": {"type": "pending"},
                                           "reach": 2, "passedFloor": True, "at": FIXED_NOW})
        assert [r["itemRef"] for r in ProvenanceStore(path=path).all()] == ["a", "b"]


class TestCorruptionTolerance:
    def test_a_corrupt_line_is_skipped_not_fatal(self, tmp_path):
        path = str(tmp_path / "provenance.jsonl")
        store = ProvenanceStore(path=path)
        store.append({"itemRef": "good1", "proposedBy": {"type": "agent", "id": "syncbot"},
                      "lane": "review", "decidedBy": {"type": "pending"},
                      "reach": 0, "passedFloor": True, "at": FIXED_NOW})
        # Inject a corrupt line between good records.
        with open(path, "a") as f:
            f.write("{not valid json\n")
        store.append({"itemRef": "good2", "proposedBy": {"type": "agent", "id": "syncbot"},
                      "lane": "auto", "decidedBy": {"type": "rule", "ruleId": "rule#0"},
                      "reach": 0, "passedFloor": True, "at": FIXED_NOW})
        refs = [r["itemRef"] for r in store.all()]
        assert refs == ["good1", "good2"]  # corrupt line skipped, rest intact


class TestRoundTripWithMembrane:
    def test_auto_decision_round_trips_with_full_provenance(self, tmp_path):
        """End-to-end: an auto decision's provenance persists and reads back with the
        decider named (the audit guarantee made durable)."""
        store = _store(tmp_path)
        agent = Actor(type="agent", id="syncbot", model="claude-opus-4-8")
        items = [RouteItem("k1", "base/space/sm", "changed")]
        decisions = route(
            items,
            RouteContext(blast_radius={"k1": TEAM_REACH_CEILING}, p1_keys=[]),
            compile_policy_from_toggles(DesignerToggles(small_tweaks_flow=True)),
            proposed_by=agent, now=lambda: FIXED_NOW,
        )
        assert decisions[0].lane == Lane.AUTO
        store.append_decisions(decisions)
        rec = store.recent(1)[0]
        assert rec["lane"] == "auto"
        assert rec["decidedBy"] == {"type": "rule", "ruleId": decisions[0].rule_id}
        assert rec["proposedBy"] == {"type": "agent", "id": "syncbot", "model": "claude-opus-4-8"}
        assert rec["reach"] == TEAM_REACH_CEILING
        assert rec["passedFloor"] is True
        assert rec["at"] == FIXED_NOW
