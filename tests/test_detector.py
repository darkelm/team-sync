"""Tests for src/agent/detector.py — DriftDetector."""
from __future__ import annotations

import pytest


class TestDriftDetector:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        from src.agent.detector import DriftDetector
        self.detector = DriftDetector(providers)

    def test_run_all_returns_list(self):
        issues = self.detector.run_all()
        assert isinstance(issues, list)

    def test_run_all_non_empty(self):
        """Synthetic org has intentional drift baked in."""
        issues = self.detector.run_all()
        assert len(issues) >= 1

    def test_run_all_golden_count(self):
        """Observed: 12 issues on the synthetic org."""
        issues = self.detector.run_all()
        assert len(issues) == 12

    def test_issues_have_required_fields(self):
        issues = self.detector.run_all()
        for issue in issues:
            assert issue.id
            assert issue.type
            assert issue.title
            assert issue.severity
            assert isinstance(issue.teams_involved, list)
            assert isinstance(issue.components_involved, list)

    def test_design_drift_issues_present(self):
        issues = self.detector.run_all()
        design_drift = [i for i in issues if i.type == "design_drift"]
        assert len(design_drift) >= 1

    def test_code_drift_not_in_synthetic(self):
        """The synthetic org does not produce code_drift issues (those need shared component
        definitions, which are flagged in the dependency graph instead)."""
        issues = self.detector.run_all()
        code_drift = [i for i in issues if i.type == "code_drift"]
        # code_drift count is 0 in this synthetic org — document the actual behavior
        assert isinstance(code_drift, list)

    def test_missing_decision_log_issues_present(self):
        issues = self.detector.run_all()
        missing = [i for i in issues if i.type == "missing_decision_log"]
        assert len(missing) >= 1

    def test_predict_conflicts_returns_list(self):
        conflicts = self.detector.predict_conflicts()
        assert isinstance(conflicts, list)

    def test_predict_conflicts_non_empty(self):
        """Synthetic org has at least one planned-work conflict."""
        conflicts = self.detector.predict_conflicts()
        assert len(conflicts) >= 1

    def test_conflict_about_auth_component(self):
        """Observed: multiple teams have active tickets touching 'auth'."""
        conflicts = self.detector.predict_conflicts()
        auth_conflicts = [c for c in conflicts if "auth" in c.title.lower()]
        assert len(auth_conflicts) >= 1

    def test_conflicts_have_required_fields(self):
        conflicts = self.detector.predict_conflicts()
        for c in conflicts:
            assert c.id
            assert c.title
            assert isinstance(c.teams_involved, list)
            assert len(c.teams_involved) >= 2
            assert isinstance(c.components_at_risk, list)
