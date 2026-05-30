"""Tests for src/core/dependency_graph.py."""
from __future__ import annotations

import pytest


class TestDependencyGraph:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        from src.core.dependency_graph import DependencyGraph
        self.g = DependencyGraph()
        self.g.build(providers.manifests.get_all_teams())

    def test_all_five_teams_loaded(self):
        assert len(self.g.teams) == 5

    def test_team_names_present(self):
        assert "Team Phoenix" in self.g.teams
        assert "Team Atlas" in self.g.teams
        assert "Team Nova" in self.g.teams
        assert "Team Horizon" in self.g.teams
        assert "Team Forge" in self.g.teams

    def test_dependents_of_atlas(self):
        """Horizon, Forge, and Phoenix all depend on Atlas."""
        deps = [t.team for t in self.g.dependents_of("Team Atlas")]
        assert "Team Horizon" in deps
        assert "Team Forge" in deps
        assert "Team Phoenix" in deps

    def test_dependents_of_unknown_team(self):
        assert self.g.dependents_of("Team Nonexistent") == []

    def test_dependencies_of_atlas(self):
        """Atlas depends on Nova."""
        deps = [t.team for t in self.g.dependencies_of("Team Atlas")]
        assert "Team Nova" in deps

    def test_dependencies_of_unknown_team(self):
        assert self.g.dependencies_of("Team Nonexistent") == []

    def test_shared_components_returns_dict(self):
        shared = self.g.find_shared_components()
        assert isinstance(shared, dict)

    def test_shared_components_has_known_duplicates(self):
        """DataTable and NotificationBell are owned by multiple teams."""
        shared = self.g.find_shared_components()
        assert "DataTable" in shared
        assert len(shared["DataTable"]) >= 2
        assert "NotificationBell" in shared
        assert len(shared["NotificationBell"]) >= 2

    def test_orphaned_dependencies_empty(self):
        """Synthetic org has no references to nonexistent teams."""
        orphans = self.g.find_orphaned_dependencies()
        assert orphans == []

    def test_orphaned_dependencies_detects_missing(self, providers):
        """Inject a bad dependency and confirm it surfaces."""
        from src.core.dependency_graph import DependencyGraph
        from src.core.schemas import TeamManifest, TeamDependency, TeamComponents, TeamMember
        fake_team = TeamManifest(
            team="Ghost Team",
            description="Ghost team for testing",
            owner=TeamMember(name="Ghost Owner", role="eng", slack_handle="@ghost", email="ghost@test.co"),
            slack_channel="#ghost",
            jira_project="GHOST",
            confluence_space="GHOST",
            components=TeamComponents(code=[], design=[]),
            dependencies=[TeamDependency(team="Missing Team", reason="test", components=[])],
        )
        g2 = DependencyGraph()
        g2.build(providers.manifests.get_all_teams() + [fake_team])
        orphans = g2.find_orphaned_dependencies()
        assert ("Ghost Team", "Missing Team") in orphans

    def test_to_dict_structure(self):
        d = self.g.to_dict()
        assert "teams" in d
        assert "edges" in d
        assert "shared_components" in d
        assert "orphaned_dependencies" in d
        assert len(d["teams"]) == 5
