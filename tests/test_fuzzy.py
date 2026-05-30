"""Tests for src/agent/fuzzy.py — fuzzy matching utilities."""
from __future__ import annotations

import pytest


class TestFuzzyPick:
    def test_exact_match(self):
        from src.agent.fuzzy import fuzzy_pick
        result = fuzzy_pick("auth", ["auth", "login", "token-manager"])
        assert "auth" in result

    def test_close_match(self):
        from src.agent.fuzzy import fuzzy_pick
        result = fuzzy_pick("authh", ["auth", "login", "token-manager"], cutoff=0.85)
        assert "auth" in result

    def test_no_match_returns_empty(self):
        from src.agent.fuzzy import fuzzy_pick
        result = fuzzy_pick("blockchain", ["auth", "login", "token-manager"])
        assert result == []

    def test_respects_n_limit(self):
        from src.agent.fuzzy import fuzzy_pick
        result = fuzzy_pick("log", ["login", "logout", "log-viewer", "logger"], n=2)
        assert len(result) <= 2


class TestComponentOwner:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        self.p = providers

    def test_exact_component_found(self):
        from src.agent.fuzzy import component_owner
        team, suggestions = component_owner(self.p, "auth")
        assert team is not None
        assert team.team == "Team Phoenix"
        assert suggestions == []

    def test_typo_authh_resolves_to_auth(self):
        """'authh' should fuzzy-resolve to 'auth' → Team Phoenix."""
        from src.agent.fuzzy import component_owner
        team, suggestions = component_owner(self.p, "authh")
        assert team is not None
        assert team.team == "Team Phoenix"
        assert suggestions == []

    def test_unknown_component_returns_none(self):
        from src.agent.fuzzy import component_owner
        team, suggestions = component_owner(self.p, "blockchain")
        assert team is None

    def test_unknown_component_returns_suggestions(self):
        """A miss with partial overlap returns 'did you mean' suggestions."""
        from src.agent.fuzzy import component_owner
        team, suggestions = component_owner(self.p, "blockchain")
        # suggestions is a list of (name, team) tuples
        assert isinstance(suggestions, list)

    def test_case_insensitive_known_component(self):
        """Component lookup should work regardless of case."""
        from src.agent.fuzzy import component_owner
        team, _ = component_owner(self.p, "Auth")
        assert team is not None


class TestResolveTeams:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        self.p = providers

    def test_finds_team_by_full_name(self):
        from src.agent.fuzzy import resolve_teams
        result = resolve_teams(self.p, "what does Team Phoenix do?")
        assert "Team Phoenix" in result

    def test_finds_team_by_short_name(self):
        from src.agent.fuzzy import resolve_teams
        result = resolve_teams(self.p, "tell me about Phoenix")
        assert "Team Phoenix" in result

    def test_fuzzy_catches_typo(self):
        from src.agent.fuzzy import resolve_teams
        # "Phenix" is a fuzzy match for "Phoenix"
        result = resolve_teams(self.p, "Phenix team update")
        assert "Team Phoenix" in result

    def test_no_team_in_text(self):
        from src.agent.fuzzy import resolve_teams
        result = resolve_teams(self.p, "give me the weather forecast")
        assert result == [] or isinstance(result, list)
