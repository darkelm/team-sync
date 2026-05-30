"""Tests for src/agent/discovery.py — CollaboratorDiscovery + ReuseRadar."""
from __future__ import annotations

import pytest


class TestCollaboratorDiscovery:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        from src.agent.discovery import CollaboratorDiscovery
        self.cd = CollaboratorDiscovery(providers)

    def test_find_suggestions_returns_list(self):
        sugg = self.cd.find_suggestions()
        assert isinstance(sugg, list)

    def test_finds_horizon_forge_unlinked(self):
        """Horizon and Forge have related work but are NOT listed as dependencies."""
        sugg = self.cd.find_suggestions()
        unlinked = [s for s in sugg if not s.already_linked]
        pairs = {frozenset([s.team_a, s.team_b]) for s in unlinked}
        assert frozenset(["Team Horizon", "Team Forge"]) in pairs, (
            "Horizon↔Forge should be the key unlinked discovery"
        )

    def test_linked_pairs_are_marked(self):
        """All suggestions include an already_linked flag."""
        sugg = self.cd.find_suggestions()
        for s in sugg:
            assert isinstance(s.already_linked, bool)

    def test_suggestion_has_evidence(self):
        sugg = self.cd.find_suggestions()
        for s in sugg:
            assert len(s.evidence) >= 1

    def test_unlinked_sorted_first(self):
        """Unlinked suggestions must appear before linked ones."""
        sugg = self.cd.find_suggestions()
        if len(sugg) < 2:
            return
        flags = [s.already_linked for s in sugg]
        # Once we see True (linked), we should not see False (unlinked) after
        seen_linked = False
        for f in flags:
            if seen_linked:
                assert f is True, "Linked suggestions should be sorted after unlinked"
            if f:
                seen_linked = True


class TestReuseRadar:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        from src.agent.discovery import ReuseRadar
        self.rr = ReuseRadar(providers)

    def test_search_returns_list(self):
        results = self.rr.search("notification bell")
        assert isinstance(results, list)

    def test_notification_bell_found(self):
        """NotificationBell is a real component — should match 'notification bell'."""
        results = self.rr.search("notification bell")
        names = [m.name for m in results]
        assert any("NotificationBell" in n for n in names), (
            f"Expected NotificationBell in results; got {names}"
        )

    def test_notification_bell_owning_team(self):
        """NotificationBell is owned by Team Nova (highest score)."""
        results = self.rr.search("notification bell")
        assert len(results) >= 1
        # The highest-scored match should be Team Nova
        assert results[0].owning_team == "Team Nova"

    def test_results_sorted_by_score_desc(self):
        results = self.rr.search("notification bell")
        scores = [m.score for m in results]
        assert scores == sorted(scores, reverse=True)

    def test_exclude_team_filters(self):
        """Results should not include the excluded team's components."""
        results = self.rr.search("notification bell", exclude_team="Team Nova")
        nova_results = [m for m in results if m.owning_team == "Team Nova"]
        assert len(nova_results) == 0

    def test_no_match_returns_empty_or_small(self):
        """A completely irrelevant query returns few or no results."""
        results = self.rr.search("blockchain quantum nft")
        # May return some, but score should be < 0.4 (heuristic)
        assert len(results) <= 5
