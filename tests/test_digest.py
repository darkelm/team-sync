"""Tests for src/agent/digest.py — DigestGenerator."""
from __future__ import annotations

import pytest


class TestDigestGenerator:
    @pytest.fixture(autouse=True)
    def setup(self, providers, tmp_state):
        """Use tmp_state to prevent writing notification_prefs.json to real data/."""
        from src.agent.digest import DigestGenerator
        self.gen = DigestGenerator(providers)

    def test_generate_for_team_returns_digest(self):
        from src.core.schemas import TeamDigest
        digest = self.gen.generate_for_team("Team Phoenix")
        assert isinstance(digest, TeamDigest)
        assert digest.team == "Team Phoenix"

    def test_generate_for_team_has_week_of(self):
        from datetime import date
        digest = self.gen.generate_for_team("Team Phoenix")
        assert isinstance(digest.week_of, date)

    def test_generate_for_team_lists_are_lists(self):
        digest = self.gen.generate_for_team("Team Phoenix")
        assert isinstance(digest.dev_updates, list)
        assert isinstance(digest.design_updates, list)
        assert isinstance(digest.action_items, list)
        assert isinstance(digest.open_conflicts, list)
        assert isinstance(digest.predicted_conflicts, list)

    def test_generate_for_unknown_team_raises(self):
        with pytest.raises(ValueError):
            self.gen.generate_for_team("Team NonExistent")

    def test_format_slack_message_returns_string(self):
        digest = self.gen.generate_for_team("Team Phoenix")
        msg = self.gen.format_slack_message(digest)
        assert isinstance(msg, str)
        assert "Team Phoenix" in msg

    def test_format_slack_message_has_header(self):
        digest = self.gen.generate_for_team("Team Phoenix")
        msg = self.gen.format_slack_message(digest)
        assert "Weekly Sync Digest" in msg

    def test_signature_is_string(self):
        digest = self.gen.generate_for_team("Team Phoenix")
        sig = self.gen._signature(digest)
        assert isinstance(sig, str)
        assert len(sig) == 16

    def test_signature_deterministic(self):
        """Same digest should always produce the same signature."""
        digest = self.gen.generate_for_team("Team Phoenix")
        sig1 = self.gen._signature(digest)
        sig2 = self.gen._signature(digest)
        assert sig1 == sig2

    def test_all_teams_generate_without_error(self, providers, tmp_state):
        from src.agent.digest import DigestGenerator
        gen = DigestGenerator(providers)
        teams = providers.manifests.get_all_teams()
        for team in teams:
            digest = gen.generate_for_team(team.team)
            assert digest.team == team.team
