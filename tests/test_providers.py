"""Tests for the local providers and provider factory."""
from __future__ import annotations



class TestLocalManifestProvider:
    def test_get_all_teams_returns_five(self, providers):
        teams = providers.manifests.get_all_teams()
        assert len(teams) == 5

    def test_get_team_by_name(self, providers):
        team = providers.manifests.get_team("Team Phoenix")
        assert team is not None
        assert team.team == "Team Phoenix"

    def test_get_team_nonexistent_returns_none(self, providers):
        result = providers.manifests.get_team("Team NonExistent")
        assert result is None

    def test_find_component_owner(self, providers):
        team = providers.manifests.find_component_owner("auth")
        assert team is not None
        assert team.team == "Team Phoenix"

    def test_find_component_owner_missing(self, providers):
        result = providers.manifests.find_component_owner("nonexistent_component_xyz")
        assert result is None

    def test_get_dependents(self, providers):
        """Teams that depend on Atlas."""
        deps = providers.manifests.get_dependents("Team Atlas")
        dep_names = [t.team for t in deps]
        assert "Team Horizon" in dep_names
        assert "Team Phoenix" in dep_names


class TestLocalJiraProvider:
    def test_get_tickets_returns_list(self, providers):
        tickets = providers.jira.get_tickets()
        assert isinstance(tickets, list)
        assert len(tickets) > 0

    def test_get_tickets_for_team(self, providers):
        tickets = providers.jira.get_tickets("Team Phoenix")
        assert all(t.team == "Team Phoenix" for t in tickets)

    def test_ticket_has_required_fields(self, providers):
        tickets = providers.jira.get_tickets()
        t = tickets[0]
        assert t.id
        assert t.title
        assert t.status
        assert t.priority
        assert t.team


class TestLocalConfluenceProvider:
    def test_get_pages_returns_list(self, providers):
        pages = providers.confluence.get_pages()
        assert isinstance(pages, list)
        assert len(pages) > 0

    def test_search_pages_returns_list(self, providers):
        results = providers.confluence.search_pages("auth")
        assert isinstance(results, list)

    def test_page_has_required_fields(self, providers):
        pages = providers.confluence.get_pages()
        p = pages[0]
        assert p.id
        assert p.title
        assert p.team


class TestLocalGitHubProvider:
    def test_get_pull_requests_returns_list(self, providers):
        prs = providers.github.get_pull_requests()
        assert isinstance(prs, list)

    def test_get_recent_prs_returns_list(self, providers):
        prs = providers.github.get_recent_prs(days=30)
        assert isinstance(prs, list)


class TestLocalFigmaProvider:
    def test_get_library_components_returns_list(self, providers):
        components = providers.figma.get_library_components()
        assert isinstance(components, list)

    def test_get_drift_issues_returns_list(self, providers):
        issues = providers.figma.get_drift_issues()
        assert isinstance(issues, list)
        assert len(issues) >= 1

    def test_get_components_by_name(self, providers):
        results = providers.figma.get_components_by_name("NotificationBell")
        assert isinstance(results, list)


class TestProvidersFactory:
    def test_factory_creates_local_providers(self, providers):
        """With local config, all providers should be local implementations."""
        from src.providers.local.manifests import LocalManifestProvider
        from src.providers.local.jira import LocalJiraProvider
        from src.providers.local.confluence import LocalConfluenceProvider
        from src.providers.local.github import LocalGitHubProvider
        from src.providers.local.figma import LocalFigmaProvider
        assert isinstance(providers.manifests, LocalManifestProvider)
        assert isinstance(providers.jira, LocalJiraProvider)
        assert isinstance(providers.confluence, LocalConfluenceProvider)
        assert isinstance(providers.github, LocalGitHubProvider)
        assert isinstance(providers.figma, LocalFigmaProvider)
