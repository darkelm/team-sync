"""Tests for src/agent/alignment.py — AlignmentChecker."""
from __future__ import annotations

import pytest


class TestAlignmentChecker:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        from src.agent.alignment import AlignmentChecker
        self.ac = AlignmentChecker(providers)

    def test_run_returns_report(self):
        from src.agent.alignment import AlignmentReport
        report = self.ac.run()
        assert isinstance(report, AlignmentReport)

    def test_linked_goals_non_empty(self):
        """Synthetic org goals should ladder to at least one objective."""
        report = self.ac.run()
        assert len(report.linked) >= 1

    def test_linked_goals_golden_count(self):
        report = self.ac.run()
        assert len(report.linked) == 13

    def test_orphan_goals_golden_count(self):
        report = self.ac.run()
        assert len(report.orphans) == 2

    def test_overlaps_non_empty(self):
        """At least 2 teams pursue the same objective → overlap."""
        report = self.ac.run()
        assert len(report.overlaps) >= 1

    def test_overlaps_golden_count(self):
        report = self.ac.run()
        assert len(report.overlaps) == 3

    def test_coverage_is_dict(self):
        report = self.ac.run()
        assert isinstance(report.objective_coverage, dict)

    def test_goal_links_have_team_and_goal(self):
        report = self.ac.run()
        for link in report.linked:
            assert link.team
            assert link.goal
            assert link.objective_id is not None

    def test_orphan_links_have_no_objective(self):
        report = self.ac.run()
        for link in report.orphans:
            assert link.objective_id is None
            assert link.objective_title is None
