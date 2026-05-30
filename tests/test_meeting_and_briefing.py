"""Tests for src/agent/meeting.py, src/agent/briefing.py, and src/agent/plain.py."""
from __future__ import annotations

import os
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANSCRIPT_TXT = os.path.join(REPO_ROOT, "data", "exports", "samples", "design-review-2026-05-28.txt")


class TestMeetingAnalyzer:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        from src.agent.meeting import MeetingAnalyzer
        self.analyzer = MeetingAnalyzer(providers)

    def test_analyze_returns_meeting_notes(self):
        from src.importers.transcript import parse_transcript
        segs = parse_transcript(TRANSCRIPT_TXT)
        notes = self.analyzer.analyze(segs, "Team Phoenix", "Design Review")
        assert notes is not None
        assert notes.team == "Team Phoenix"
        assert notes.title == "Design Review"

    def test_analyze_extracts_decisions(self):
        from src.importers.transcript import parse_transcript
        segs = parse_transcript(TRANSCRIPT_TXT)
        notes = self.analyzer.analyze(segs, "Team Phoenix", "Design Review")
        assert len(notes.decisions) == 3

    def test_analyze_extracts_action_items(self):
        from src.importers.transcript import parse_transcript
        segs = parse_transcript(TRANSCRIPT_TXT)
        notes = self.analyzer.analyze(segs, "Team Phoenix", "Design Review")
        assert len(notes.action_items) == 9

    def test_analyze_extracts_risks(self):
        from src.importers.transcript import parse_transcript
        segs = parse_transcript(TRANSCRIPT_TXT)
        notes = self.analyzer.analyze(segs, "Team Phoenix", "Design Review")
        assert len(notes.risks) == 5

    def test_to_confluence_pages_returns_list(self):
        from src.importers.transcript import parse_transcript
        segs = parse_transcript(TRANSCRIPT_TXT)
        notes = self.analyzer.analyze(segs, "Team Phoenix", "Design Review")
        pages = self.analyzer.to_confluence_pages(notes)
        assert isinstance(pages, list)
        assert len(pages) == 3

    def test_empty_segments_returns_empty_notes(self):
        notes = self.analyzer.analyze([], "Team Phoenix", "Empty Meeting")
        assert notes is not None
        assert notes.decisions == []
        assert notes.action_items == []


class TestBriefingGenerator:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        from src.agent.briefing import BriefingGenerator
        self.gen = BriefingGenerator(providers)

    def test_cross_team_briefing_returns_string(self):
        result = self.gen.cross_team_briefing(["Team Phoenix", "Team Atlas"])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_cross_team_briefing_mentions_teams(self):
        result = self.gen.cross_team_briefing(["Team Phoenix", "Team Atlas"])
        assert "Phoenix" in result or "Atlas" in result

    def test_cross_team_briefing_one_team(self):
        """Single team briefing should not raise."""
        result = self.gen.cross_team_briefing(["Team Phoenix"])
        assert isinstance(result, str)

    def test_cross_team_briefing_empty_list(self):
        """Empty team list should not raise."""
        result = self.gen.cross_team_briefing([])
        assert isinstance(result, str)


class TestPlainLabels:
    def test_labels_returns_dict(self, config_path):
        from src.agent.plain import labels
        result = labels(config_path)
        assert isinstance(result, dict)

    def test_labels_has_unit(self, config_path):
        from src.agent.plain import labels
        result = labels(config_path)
        assert "unit" in result
        assert result["unit"] == "team"

    def test_labels_has_portfolio(self, config_path):
        from src.agent.plain import labels
        result = labels(config_path)
        assert "portfolio" in result
        assert result["portfolio"] == "portfolio"
