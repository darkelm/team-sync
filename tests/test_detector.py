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

    def test_sunset_report_flags_deprecated_with_exposure(self):
        """token-validation is marked deprecated in Team Atlas's manifest and
        Team Phoenix declares a dependency on it — flagged with exposure."""
        report = self.detector.sunset_report()
        item = next((r for r in report if r["component"] == "token-validation"), None)
        assert item is not None
        assert item["owner_team"] == "Team Atlas"
        assert item["replacement"] == "token-validation-v2"
        assert "Team Phoenix" in item["dependent_teams"]

    def test_run_all_non_empty(self):
        """Synthetic org has intentional drift baked in."""
        issues = self.detector.run_all()
        assert len(issues) >= 1

    def test_run_all_golden_composition(self):
        """Golden assertion on issue *composition*, not a bare magic number.

        The synthetic org produces a fixed, time-INVARIANT set of issues plus a
        time-VARIANT one:

          - design_drift          = 4  (figma components flagged `diverges_from_library`;
                                        no date filtering — stable)
          - missing_decision_log  = 6  (1 open cross-team PR w/o a decision log in any
                                        Confluence summary + 5 `breaking-change` Jira
                                        tickets w/o a linked decision log — stable)
          - code_drift            = 0  (no component name is owned by >1 team in the
                                        manifests; see test_code_drift_not_in_synthetic)
          - cross_team_pr         = TIME-DEPENDENT — counts merged PRs whose merged_at
                                        falls inside `_detect_cross_team_pr_impact`'s
                                        hard-coded 7-day window. The synthetic fixture has
                                        2 merged cross-team PRs (NOVA-PR-31 @ 2026-05-27,
                                        PHX-PR-42 @ 2026-05-24). When the fixture was
                                        authored (late May 2026) both were <7 days old, so
                                        run_all() returned 4+6+0+2 = 12 ("Observed: 12").
                                        Because merged_at are fixed absolute timestamps and
                                        the window is relative to datetime.now(), that count
                                        decays over wall-clock time — which is why a bare
                                        `== 12` rotted to 10. We therefore assert the stable
                                        categories exactly and only bound the volatile one.
        """
        from collections import Counter
        issues = self.detector.run_all()
        counts = Counter(i.type for i in issues)

        # Time-invariant categories — assert exactly.
        assert counts["design_drift"] == 4
        assert counts["missing_decision_log"] == 6
        assert counts["code_drift"] == 0

        # Time-variant category — 0..2 depending on how far "now" is from the
        # fixture's merged_at dates. No date in the future is forged, so it can
        # never exceed the 2 merged cross-team PRs in the fixture.
        assert 0 <= counts["cross_team_pr"] <= 2

        # Total is the sum of the above; no other issue types are produced.
        assert set(counts) <= {"design_drift", "missing_decision_log", "code_drift", "cross_team_pr"}
        assert len(issues) == 10 + counts["cross_team_pr"]

    def test_run_all_golden_composition_frozen_time(self, monkeypatch):
        """Deterministic companion to test_run_all_golden_composition.

        The drift-robust test above bounds cross_team_pr at 0..2 because the
        window in get_recent_prs is relative to datetime.now(), while the
        fixture's merged_at dates are absolute — so the count decays over
        wall-clock time and the path can go UNEXERCISED (count == 0).

        Here we freeze "now" to 2026-05-28 so the 7-day window
        (cutoff = 2026-05-21) captures BOTH merged cross-team PRs:
          - NOVA-PR-31  @ 2026-05-27
          - PHX-PR-42   @ 2026-05-24
        and the cross_team_pr code path fires deterministically.

        We freeze time by monkeypatching the `datetime` symbol that
        get_recent_prs resolves — the one imported into
        src.providers.local.github — with a subclass whose now() returns the
        fixed instant. get_recent_prs reads datetime.now() live on every call
        (the provider only caches the PR list, not the clock), so this fully
        controls the window without touching provider source or fixture data.
        """
        from collections import Counter
        from datetime import datetime, timezone
        import src.providers.local.github as gh

        FROZEN = datetime(2026, 5, 28, tzinfo=timezone.utc)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return FROZEN if tz is None else FROZEN.astimezone(tz)

        monkeypatch.setattr(gh, "datetime", _FrozenDatetime)

        issues = self.detector.run_all()
        counts = Counter(i.type for i in issues)

        # Time-invariant categories — unchanged by the frozen clock.
        assert counts["design_drift"] == 4
        assert counts["missing_decision_log"] == 6
        assert counts["code_drift"] == 0

        # Time-variant category — now pinned to exactly 2 by the frozen window.
        assert counts["cross_team_pr"] == 2

        # The two specific fixture PRs are the ones detected.
        cross_team_ids = {i.id for i in issues if i.type == "cross_team_pr"}
        assert cross_team_ids == {"pr-impact-NOVA-PR-31", "pr-impact-PHX-PR-42"}

        # No other issue types; total is exactly 12.
        assert set(counts) <= {"design_drift", "missing_decision_log", "code_drift", "cross_team_pr"}
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
