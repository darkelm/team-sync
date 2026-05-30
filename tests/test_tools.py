"""Tests for src/agent/tools.py — execute_tool and build_tools.

We call execute_tool directly (no Claude agent, no API key).
"""
from __future__ import annotations

import json


class TestBuildTools:
    def test_build_tools_returns_list(self, providers):
        from src.agent.tools import build_tools
        tools = build_tools(providers)
        assert isinstance(tools, list)
        assert len(tools) >= 10

    def test_tool_schema_structure(self, providers):
        from src.agent.tools import build_tools
        tools = build_tools(providers)
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool


class TestExecuteToolWhoOwns:
    def test_who_owns_known_component(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("who_owns", {"component_name": "auth"}, providers)
        data = json.loads(result)
        assert data["team"] == "Team Phoenix"
        assert "owner" in data

    def test_who_owns_unknown_component(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("who_owns", {"component_name": "nonexistent_xyz"}, providers)
        # Returns a string message or JSON with did_you_mean
        assert isinstance(result, str)

    def test_who_owns_typo_resolves(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("who_owns", {"component_name": "authh"}, providers)
        data = json.loads(result)
        assert data["team"] == "Team Phoenix"


class TestExecuteToolFindDecision:
    def test_find_decision_returns_json(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("find_decision", {"query": "auth"}, providers)
        assert isinstance(result, str)

    def test_find_decision_no_results(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("find_decision", {"query": "xyzzy_nonexistent_abc"}, providers)
        assert isinstance(result, str)


class TestExecuteToolGetTeamContext:
    def test_get_team_context_known_team(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("get_team_context", {"team_name": "Team Phoenix"}, providers)
        data = json.loads(result)
        assert data["team"] == "Team Phoenix"
        assert "owner" in data

    def test_get_team_context_unknown_team(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("get_team_context", {"team_name": "Team Nonexistent"}, providers)
        assert "not found" in result.lower()

    def test_get_team_context_designer_audience(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("get_team_context", {"team_name": "Team Phoenix", "audience": "designer"}, providers)
        data = json.loads(result)
        assert "figma_files" in data or "design_components" in data


class TestExecuteToolScanConflicts:
    def test_scan_conflicts_returns_issues(self, providers, tmp_state):
        from src.agent.tools import execute_tool
        result = execute_tool("scan_conflicts", {}, providers)
        data = json.loads(result)
        assert "issues" in data
        assert len(data["issues"]) >= 1

    def test_scan_conflicts_with_predictions(self, providers, tmp_state):
        from src.agent.tools import execute_tool
        result = execute_tool("scan_conflicts", {"include_predictions": True}, providers)
        data = json.loads(result)
        assert "predicted_conflicts" in data


class TestExecuteToolDependencyGraph:
    def test_get_dependency_graph_all(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("get_dependency_graph", {}, providers)
        data = json.loads(result)
        assert "teams" in data
        assert len(data["teams"]) == 5

    def test_get_dependency_graph_filtered(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("get_dependency_graph", {"team_name": "Team Phoenix"}, providers)
        data = json.loads(result)
        assert data["team"] == "Team Phoenix"

    def test_get_dependency_graph_unknown_team(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("get_dependency_graph", {"team_name": "Ghost Team"}, providers)
        assert "not found" in result.lower()


class TestExecuteToolFindCollaborators:
    def test_find_collaborators_returns_list(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("find_collaborators", {}, providers)
        data = json.loads(result)
        assert isinstance(data, list)


class TestExecuteToolReuseRadar:
    def test_reuse_radar_finds_component(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("reuse_radar", {"description": "notification bell"}, providers)
        # May return JSON list or a "nothing found" string
        assert isinstance(result, str)
        if result.startswith("["):
            data = json.loads(result)
            assert len(data) >= 1

    def test_reuse_radar_nothing_found(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("reuse_radar", {"description": "blockchain nft quantum"}, providers)
        assert isinstance(result, str)


class TestExecuteToolCheckAlignment:
    def test_check_alignment_returns_json(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("check_alignment", {}, providers)
        data = json.loads(result)
        assert "linked_goal_count" in data
        assert data["linked_goal_count"] == 13


class TestExecuteToolFindResource:
    def test_find_resource_returns_result(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("find_resource", {"query": "auth"}, providers)
        assert isinstance(result, str)

    def test_find_resource_no_result(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("find_resource", {"query": "xyzzy_nothing_abc"}, providers)
        assert "couldn't" in result.lower() or result.startswith("[")


class TestExecuteToolPredictConflicts:
    def test_predict_conflicts_returns_list(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("predict_conflicts", {}, providers)
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) >= 1


class TestExecuteToolCrossTeamBriefing:
    def test_cross_team_briefing_returns_string(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("cross_team_briefing", {"teams": ["Team Phoenix", "Team Atlas"]}, providers)
        assert isinstance(result, str)
        assert len(result) > 0


class TestExecuteToolTeamHealth:
    def test_team_health_known_team(self, providers, tmp_state):
        from src.agent.tools import execute_tool
        result = execute_tool("team_health", {"team_name": "Team Phoenix"}, providers)
        data = json.loads(result)
        assert data["team"] == "Team Phoenix"
        assert "status" in data

    def test_team_health_unknown_team(self, providers, tmp_state):
        from src.agent.tools import execute_tool
        result = execute_tool("team_health", {"team_name": "Ghost Team"}, providers)
        assert "not found" in result.lower()


class TestExecuteToolPortfolioStatus:
    def test_portfolio_status_returns_five(self, providers, tmp_state):
        from src.agent.tools import execute_tool
        result = execute_tool("portfolio_status", {}, providers)
        data = json.loads(result)
        assert len(data) == 5

    def test_portfolio_status_has_status_field(self, providers, tmp_state):
        from src.agent.tools import execute_tool
        result = execute_tool("portfolio_status", {}, providers)
        data = json.loads(result)
        for item in data:
            assert item["status"] in {"green", "amber", "red"}


class TestExecuteToolJourneyStatus:
    def test_journey_status_all_journeys(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("journey_status", {}, providers)
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 3

    def test_journey_status_specific_journey(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("journey_status", {"journey_name": "Onboarding"}, providers)
        data = json.loads(result)
        assert data["journey"] == "Onboarding"

    def test_journey_status_not_found(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("journey_status", {"journey_name": "NonexistentJourneyXYZ"}, providers)
        assert "No journey" in result


class TestExecuteToolExperiencePrinciples:
    def test_experience_principles_returns_string(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("experience_principles", {}, providers)
        assert isinstance(result, str)
        assert len(result) > 0


class TestExecuteToolDesignSyncStatus:
    def test_design_sync_status_returns_json(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("design_sync_status", {}, providers)
        data = json.loads(result)
        assert "total_components_checked" in data
        assert "drifted" in data


class TestExecuteToolUnknown:
    def test_unknown_tool_returns_message(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("totally_fake_tool", {}, providers)
        assert "Unknown tool" in result
