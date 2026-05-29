#!/usr/bin/env python3
"""SyncBot MCP server — exposes the coordination engine to any MCP client.

This is the portability layer: Claude Desktop, Cursor, Cline, Gemini, or any
MCP-compatible AI gets all 14 coordination tools — grounded in the same
providers and the same `execute_tool` handlers that back the Slack agent.
No new intelligence; a new doorway to the same brain.

Run (stdio):  python mcp_server.py
"""
import os
import sys
from pathlib import Path

# Resolve everything relative to the repo root so config.yaml and data/ paths
# work regardless of the client's working directory.
ROOT = Path(__file__).parent.resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from mcp.server.fastmcp import FastMCP
from src.providers.factory import Providers
from src.agent.tools import execute_tool

mcp = FastMCP("team-sync")
providers = Providers("config.yaml")


def _run(tool: str, **inputs) -> str:
    return execute_tool(tool, inputs, providers)


# ── Reactive lookups ──────────────────────────────────────────────────────────

@mcp.tool()
def who_owns(component_name: str) -> str:
    """Find which team owns a component (code or design), and who to talk to."""
    return _run("who_owns", component_name=component_name)

@mcp.tool()
def when_ships(team_name: str) -> str:
    """Get a team's upcoming deliverables and delivery dates."""
    return _run("when_ships", team_name=team_name)

@mcp.tool()
def find_decision(query: str, team: str = "") -> str:
    """Search decision logs and docs (including decisions captured from meetings)."""
    return _run("find_decision", query=query, team=team or None)

@mcp.tool()
def get_team_context(team_name: str, audience: str = "all") -> str:
    """Full context briefing for a team (audience: dev | designer | pm | all)."""
    return _run("get_team_context", team_name=team_name, audience=audience)

@mcp.tool()
def design_sync_status(team_name: str = "") -> str:
    """Check whether a team's Figma components are in sync with the design system (omit team for all)."""
    return _run("design_sync_status", team_name=team_name or None)

@mcp.tool()
def get_dependency_graph(team_name: str = "") -> str:
    """Cross-team dependency map (omit team for the whole graph)."""
    return _run("get_dependency_graph", team_name=team_name or None)

@mcp.tool()
def find_resource(query: str) -> str:
    """Locate where something lives — research repos, brand assets, prototypes, design system, roadmaps, docs."""
    return _run("find_resource", query=query)

@mcp.tool()
def get_action_items(team: str = "") -> str:
    """List open action items captured from ingested meeting transcripts (optional team filter)."""
    return _run("get_action_items", team=team or None)


# ── Proactive / analytical ────────────────────────────────────────────────────

@mcp.tool()
def scan_conflicts(include_predictions: bool = False) -> str:
    """Scan for current drift, missing decision logs, and cross-team PR impact; optionally include predicted conflicts."""
    return _run("scan_conflicts", include_predictions=include_predictions)

@mcp.tool()
def predict_conflicts() -> str:
    """Forecast collisions in planned work before teams start building."""
    return _run("predict_conflicts")

@mcp.tool()
def find_collaborators() -> str:
    """Discover teams doing related work who may not realize they should be collaborating."""
    return _run("find_collaborators")

@mcp.tool()
def reuse_radar(description: str, exclude_team: str = "") -> str:
    """Check whether a component, design, or research already exists before a team builds it."""
    return _run("reuse_radar", description=description, exclude_team=exclude_team)

@mcp.tool()
def check_alignment() -> str:
    """Check whether team goals ladder up to company objectives, and which objectives multiple teams pursue."""
    return _run("check_alignment")

@mcp.tool()
def cross_team_briefing(teams: list[str]) -> str:
    """Generate a meeting briefing for a sync between two or more teams (dependencies, overlaps, open tickets, conflicts, agenda)."""
    return _run("cross_team_briefing", teams=teams)


if __name__ == "__main__":
    mcp.run()
