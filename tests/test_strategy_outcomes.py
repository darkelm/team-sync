"""Tests for Outcomes + Research Insights in src/agent/strategy.py (C2 spec)."""
from __future__ import annotations

import json
import pytest


class TestOutcomesLoad:
    @pytest.fixture(autouse=True)
    def setup(self, providers, config_path):
        from src.agent.strategy import StrategyLens
        self.sl = StrategyLens(providers, config_path)

    def test_outcomes_loaded(self):
        assert len(self.sl.outcome_list) == 5

    def test_outcome_ids(self):
        ids = [o.id for o in self.sl.outcome_list]
        assert "OUT-1" in ids
        assert "OUT-5" in ids

    def test_outcome_fields(self):
        o = next(o for o in self.sl.outcome_list if o.id == "OUT-1")
        assert o.name == "Reduce new-user time-to-value"
        assert o.metric  # non-empty
        assert o.target  # non-empty
        assert o.owner == "Team Phoenix"
        assert "OBJ-4" in o.related_objectives
        assert "Onboarding" in o.related_journeys

    def test_outcome_lists_are_lists(self):
        for o in self.sl.outcome_list:
            assert isinstance(o.related_objectives, list)
            assert isinstance(o.related_journeys, list)


class TestInsightsLoad:
    @pytest.fixture(autouse=True)
    def setup(self, providers, config_path):
        from src.agent.strategy import StrategyLens
        self.sl = StrategyLens(providers, config_path)

    def test_insights_loaded(self):
        assert len(self.sl.insights) == 5

    def test_insight_ids(self):
        ids = [ri.id for ri in self.sl.insights]
        assert "RI-1" in ids
        assert "RI-5" in ids

    def test_insight_fields(self):
        ri = next(ri for ri in self.sl.insights if ri.id == "RI-1")
        assert ri.title
        assert ri.summary
        assert ri.source
        assert ri.date is not None
        assert isinstance(ri.themes, list)
        assert isinstance(ri.journeys, list)
        assert isinstance(ri.teams, list)

    def test_insights_have_url(self):
        for ri in self.sl.insights:
            assert isinstance(ri.url, str)  # may be empty but must be a string


class TestInsightsFor:
    @pytest.fixture(autouse=True)
    def setup(self, providers, config_path):
        from src.agent.strategy import StrategyLens
        self.sl = StrategyLens(providers, config_path)

    def test_insights_for_onboarding_returns_results(self):
        results = self.sl.insights_for("onboarding")
        assert len(results) >= 1

    def test_ri1_in_onboarding_results(self):
        results = self.sl.insights_for("onboarding")
        ids = [ri.id for ri in results]
        # RI-1 is tagged with 'onboarding' journey and themes
        assert "RI-1" in ids

    def test_ri2_in_onboarding_results(self):
        results = self.sl.insights_for("onboarding")
        ids = [ri.id for ri in results]
        assert "RI-2" in ids

    def test_insights_for_notifications(self):
        results = self.sl.insights_for("notifications")
        ids = [ri.id for ri in results]
        # RI-1, RI-2, and RI-4 all touch notifications
        assert len(results) >= 1
        assert any(r in ids for r in ("RI-1", "RI-2", "RI-4"))

    def test_insights_for_dashboard(self):
        results = self.sl.insights_for("dashboard")
        ids = [ri.id for ri in results]
        assert "RI-3" in ids

    def test_insights_for_unknown_topic_returns_empty(self):
        results = self.sl.insights_for("xyzquuxnonexistent99")
        assert results == []

    def test_results_are_research_insight_objects(self):
        from src.core.schemas import ResearchInsight
        for ri in self.sl.insights_for("onboarding"):
            assert isinstance(ri, ResearchInsight)


class TestContradictions:
    @pytest.fixture(autouse=True)
    def setup(self, providers, config_path):
        from src.agent.strategy import StrategyLens
        self.sl = StrategyLens(providers, config_path)

    def test_contradictions_returns_list(self):
        result = self.sl.contradictions()
        assert isinstance(result, list)

    def test_ri1_ri2_contradiction_detected(self):
        """RI-1 (negative: drop-off) and RI-2 (positive: retention lift) share
        themes onboarding/account-setup/notifications and should be flagged."""
        result = self.sl.contradictions()
        ids_pairs = [frozenset([c["insight_a"], c["insight_b"]]) for c in result]
        assert frozenset(["RI-1", "RI-2"]) in ids_pairs

    def test_contradiction_has_required_keys(self):
        result = self.sl.contradictions()
        for c in result:
            for key in ("insight_a", "insight_b", "title_a", "title_b", "shared_themes", "note"):
                assert key in c

    def test_shared_themes_are_meaningful(self):
        result = self.sl.contradictions()
        for c in result:
            assert len(c["shared_themes"]) >= 2


class TestAssessOutcome:
    @pytest.fixture(autouse=True)
    def setup(self, providers, config_path):
        from src.agent.strategy import StrategyLens
        self.sl = StrategyLens(providers, config_path)

    def test_assess_outcome_returns_dict(self):
        result = self.sl.assess_outcome("time-to-value")
        assert isinstance(result, dict)

    def test_assess_outcome_has_required_keys(self):
        result = self.sl.assess_outcome("time-to-value")
        for key in ("id", "name", "metric", "target", "owner", "related_objectives",
                    "related_journeys", "supporting_work", "flags", "relevant_insights"):
            assert key in result

    def test_assess_outcome_correct_id(self):
        result = self.sl.assess_outcome("time-to-value")
        assert result["id"] == "OUT-1"

    def test_assess_outcome_none_for_unknown(self):
        result = self.sl.assess_outcome("xyznonexistentoutcome99")
        assert result is None

    def test_assess_outcome_notification_delivery(self):
        result = self.sl.assess_outcome("notification delivery")
        assert result is not None
        assert result["id"] == "OUT-2"

    def test_assess_outcome_flags_is_list(self):
        result = self.sl.assess_outcome("design system adoption")
        assert isinstance(result["flags"], list)

    def test_assess_outcome_supporting_work_is_list(self):
        result = self.sl.assess_outcome("time-to-value")
        assert isinstance(result["supporting_work"], list)

    def test_assess_outcome_relevant_insights_is_list(self):
        result = self.sl.assess_outcome("time-to-value")
        assert isinstance(result["relevant_insights"], list)


class TestOutcomesFormat:
    @pytest.fixture(autouse=True)
    def setup(self, providers, config_path):
        from src.agent.strategy import StrategyLens
        self.sl = StrategyLens(providers, config_path)

    def test_outcomes_returns_string(self):
        result = self.sl.outcomes()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_outcomes_contains_all_ids(self):
        result = self.sl.outcomes()
        for o in self.sl.outcome_list:
            assert o.id in result

    def test_format_outcome_returns_string(self):
        assessment = self.sl.assess_outcome("time-to-value")
        result = self.sl.format_outcome(assessment)
        assert isinstance(result, str)
        assert "OUT-1" in result

    def test_format_insights_returns_string(self):
        result = self.sl.format_insights("onboarding")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_insights_surfaces_contradiction_notice(self):
        """When there are contradictory findings, format_insights should note them."""
        result = self.sl.format_insights("onboarding")
        # RI-1 vs RI-2 contradiction should be surfaced in the onboarding insight report
        assert "RI-1" in result or "RI-2" in result

    def test_format_contradictions_returns_string(self):
        result = self.sl.format_contradictions()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_contradictions_mentions_ri1_ri2(self):
        result = self.sl.format_contradictions()
        assert "RI-1" in result
        assert "RI-2" in result


class TestJourneyInformingInsights:
    """Verify that format_journey now surfaces informing research insights."""
    @pytest.fixture(autouse=True)
    def setup(self, providers, config_path):
        from src.agent.strategy import StrategyLens
        self.sl = StrategyLens(providers, config_path)

    def test_format_journey_onboarding_mentions_insights(self):
        h = self.sl.assess_journey("Onboarding")
        text = self.sl.format_journey(h)
        # At least one RI id should appear in the formatted onboarding journey
        assert any(f"RI-{n}" in text for n in range(1, 6))

    def test_format_journey_dashboard_mentions_ri3(self):
        h = self.sl.assess_journey("Core Dashboard")
        text = self.sl.format_journey(h)
        assert "RI-3" in text


class TestToolsIntegration:
    """Check that execute_tool routes correctly for outcome_status and research_insights."""
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        from src.agent.tools import execute_tool
        self.execute = lambda name, **kwargs: execute_tool(name, kwargs, providers)

    def test_outcome_status_all(self):
        result = self.execute("outcome_status")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_outcome_status_named(self):
        result = self.execute("outcome_status", outcome_name="time-to-value")
        # Named outcome returns JSON dict
        parsed = json.loads(result)
        assert parsed["id"] == "OUT-1"

    def test_outcome_status_unknown(self):
        result = self.execute("outcome_status", outcome_name="xyznonexistent")
        assert "No outcome named" in result

    def test_research_insights_with_topic(self):
        result = self.execute("research_insights", topic="onboarding")
        assert isinstance(result, str)
        assert "RI-1" in result or "RI-2" in result

    def test_research_insights_no_topic(self):
        result = self.execute("research_insights")
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 5  # all 5 synthetic insights

    def test_research_insights_no_topic_has_expected_keys(self):
        result = self.execute("research_insights")
        parsed = json.loads(result)
        for item in parsed:
            assert "id" in item
            assert "title" in item
            assert "themes" in item


class TestSchemas:
    """Basic schema validation for Outcome and ResearchInsight."""

    def test_outcome_schema(self):
        from src.core.schemas import Outcome
        o = Outcome(
            id="OUT-X",
            name="Test Outcome",
            metric="Some metric",
            target="Some target",
            owner="Test Team",
        )
        assert o.related_objectives == []
        assert o.related_journeys == []

    def test_research_insight_schema(self):
        from src.core.schemas import ResearchInsight
        import datetime
        ri = ResearchInsight(
            id="RI-X",
            title="Test Insight",
            summary="A summary.",
            source="Test Study",
            date=datetime.date(2026, 1, 1),
        )
        assert ri.themes == []
        assert ri.journeys == []
        assert ri.teams == []
        assert ri.url == ""
