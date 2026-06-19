#!/usr/bin/env python3
"""SyncBot MCP server — exposes the coordination engine to any MCP client.

This is the portability layer: Claude Desktop, Cursor, Cline, Gemini, or any
MCP-compatible AI gets all 20 coordination tools (plus the import_export
and emit_event utility tools) — grounded in the same providers and the
same `execute_tool` handlers that back the Slack agent.
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


@mcp.tool()
def import_export(path: str, team: str) -> str:
    """Import an export into a team — Jira CSV, Confluence export folder, git clone, or meeting transcript. Auto-detects the type. `path` is a local path the server can read."""
    from src.ingest import ingest_path
    return ingest_path(path, team)


@mcp.tool()
def team_health(team_name: str) -> str:
    """Leadership-framed health of one team: on-track/at-risk/blocked, top risks in plain language, what changed, who to talk to."""
    return _run("team_health", team_name=team_name)


@mcp.tool()
def portfolio_status() -> str:
    """Leadership rollup across all teams: how many blocked/at-risk/on-track, with each team's headline risk. No per-component detail."""
    return _run("portfolio_status")


@mcp.tool()
def journey_status(journey_name: str = "") -> str:
    """Assess an end-to-end experience/journey (onboarding, checkout, notifications) that spans teams: coherence across teams, inconsistencies, ownership gaps, experience owner, north-star. Omit journey_name to list all journeys."""
    return _run("journey_status", journey_name=journey_name or None)


@mcp.tool()
def experience_principles() -> str:
    """Report whether the org is upholding its experience/design principles, mapping live signals to each principle."""
    return _run("experience_principles")


@mcp.tool()
def outcome_status(outcome_name: str = "") -> str:
    """Show measurable outcomes the org is pursuing: metric, target, owner, and whether open work ladders to each. Flags outcomes with no supporting tickets. Omit outcome_name to list all."""
    return _run("outcome_status", outcome_name=outcome_name or None)


@mcp.tool()
def research_insights(topic: str = "") -> str:
    """Surface research insights relevant to a topic or journey. Flags contradictory findings on the same theme. Omit topic to list all insights."""
    return _run("research_insights", topic=topic or "")


@mcp.tool()
def emit_event(event_type: str, subject: str = "", team: str = "") -> str:
    """Fire a trigger event and preview who would be proactively notified. Source-agnostic — any signal (design.library_published, research.study_added, roadmap.date_changed, work.created, code.merged, calendar.cross_team_sync, etc.) can wake the coordination engine, not just code changes."""
    from src.agent.events import EventRouter, Event
    return EventRouter(providers).explain(Event(type=event_type, subject=subject, team=team, source="mcp"))


if __name__ == "__main__":
    mcp.run()
