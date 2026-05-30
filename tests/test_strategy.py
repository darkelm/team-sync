"""Tests for src/agent/strategy.py — StrategyLens."""
from __future__ import annotations

import pytest


class TestStrategyLens:
    @pytest.fixture(autouse=True)
    def setup(self, providers, config_path):
        from src.agent.strategy import StrategyLens
        self.sl = StrategyLens(providers, config_path)

    def test_journeys_loaded(self):
        assert len(self.sl.journeys) == 3

    def test_journey_names(self):
        names = [j.name for j in self.sl.journeys]
        assert "Onboarding" in names
        assert "Notifications" in names
        assert "Core Dashboard" in names

    def test_principles_loaded(self):
        assert len(self.sl.principles) == 4

    def test_principle_names(self):
        names = [p.name for p in self.sl.principles]
        assert "One consistent visual language" in names
        assert "Decisions are documented and shared" in names

    def test_get_journey_by_name(self):
        journey = self.sl.get_journey("Onboarding")
        assert journey is not None
        assert journey.name == "Onboarding"

    def test_get_journey_case_insensitive(self):
        journey = self.sl.get_journey("onboarding")
        assert journey is not None

    def test_get_journey_nonexistent(self):
        journey = self.sl.get_journey("Nonexistent Journey XYZ")
        assert journey is None

    def test_assess_journey_returns_health(self):
        from src.agent.strategy import JourneyHealth
        h = self.sl.assess_journey("Onboarding")
        assert isinstance(h, JourneyHealth)
        assert h.status in {"green", "amber", "red"}
        assert h.name == "Onboarding"

    def test_assess_journey_none_for_missing(self):
        result = self.sl.assess_journey("NonexistentJourney")
        assert result is None

    def test_format_journey_is_string(self):
        h = self.sl.assess_journey("Onboarding")
        text = self.sl.format_journey(h)
        assert isinstance(text, str)
        assert "Onboarding" in text

    def test_format_journeys_is_string(self):
        text = self.sl.format_journeys()
        assert isinstance(text, str)
        assert len(text) > 0

    def test_principle_report_is_string(self):
        text = self.sl.principle_report()
        assert isinstance(text, str)
        assert len(text) > 0
