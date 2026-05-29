"""Tool definitions for the Claude agent — the query layer."""
import json
from ..providers.factory import Providers
from ..core.dependency_graph import DependencyGraph
from .detector import DriftDetector


def build_tools(providers: Providers) -> list[dict]:
    return [
        {
            "name": "who_owns",
            "description": "Find which team owns a component (code or design). Use when asked 'who owns X', 'which team is responsible for Y', or 'who do I talk to about Z'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "component_name": {"type": "string", "description": "Name of the component, service, or feature"}
                },
                "required": ["component_name"]
            }
        },
        {
            "name": "when_ships",
            "description": "Get upcoming deliverables and delivery dates for a team. Use when asked 'when does X team deliver Y', 'what is team X shipping', 'what's the roadmap for team X'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "team_name": {"type": "string", "description": "Name of the team"}
                },
                "required": ["team_name"]
            }
        },
        {
            "name": "find_decision",
            "description": "Search decision logs and documentation. Use when asked 'what was decided about X', 'why did we choose Y', 'is there a decision log for Z'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term or topic"},
                    "team": {"type": "string", "description": "Optional team filter"}
                },
                "required": ["query"]
            }
        },
        {
            "name": "get_team_context",
            "description": "Get a full context briefing for a team — members, components, dependencies, recent PRs, open tickets. Use for onboarding ('I'm new to team X') or cross-team meeting prep.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "team_name": {"type": "string", "description": "Name of the team"},
                    "audience": {"type": "string", "enum": ["dev", "designer", "pm", "all"], "description": "Tailor the output for a specific role"}
                },
                "required": ["team_name"]
            }
        },
        {
            "name": "design_sync_status",
            "description": "Check if a team's Figma components are in sync with the design system. Use when asked 'is my design in sync', 'what has drifted from the design system', 'which components are out of date'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "team_name": {"type": "string", "description": "Team to check. Leave empty to check all teams."}
                },
                "required": []
            }
        },
        {
            "name": "scan_conflicts",
            "description": "Scan for current drift issues, missing decision logs, and predicted work conflicts across all teams. Use when asked 'what's broken', 'what conflicts exist', 'what should I know about this week'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "include_predictions": {"type": "boolean", "description": "Include predicted future conflicts"}
                },
                "required": []
            }
        },
        {
            "name": "get_dependency_graph",
            "description": "Get the cross-team dependency map. Use when asked 'which teams depend on X', 'what does team Y depend on', 'show me the dependency graph'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "team_name": {"type": "string", "description": "Optional — filter to a specific team's dependencies"}
                },
                "required": []
            }
        }
    ]


def execute_tool(name: str, inputs: dict, providers: Providers) -> str:
    if name == "who_owns":
        team = providers.manifests.find_component_owner(inputs["component_name"])
        if not team:
            return f"No team claims ownership of '{inputs['component_name']}'."
        return json.dumps({
            "team": team.team,
            "owner": team.owner.name,
            "slack_handle": team.owner.slack_handle,
            "slack_channel": team.slack_channel,
            "code_components": [c.name for c in team.components.code],
            "design_components": [c.name for c in team.components.design],
        })

    elif name == "when_ships":
        tickets = providers.jira.get_upcoming_deliverables(inputs["team_name"])
        if not tickets:
            return f"No upcoming deliverables with due dates found for {inputs['team_name']}."
        return json.dumps([{
            "id": t.id, "title": t.title, "status": t.status.value,
            "due_date": str(t.due_date), "priority": t.priority.value,
            "epic": t.epic
        } for t in sorted(tickets, key=lambda x: x.due_date or "9999")])

    elif name == "find_decision":
        pages = providers.confluence.search_pages(inputs["query"], inputs.get("team"))
        if not pages:
            return f"No pages found for '{inputs['query']}'."
        results = []
        for p in pages[:5]:
            entry = {"title": p.title, "team": p.team, "url": p.url, "summary": p.content_summary}
            if p.decision_log:
                dl = p.decision_log
                entry["decision"] = dl.decision
                entry["rationale"] = dl.rationale
                entry["decided_by"] = dl.decided_by
                entry["date"] = str(dl.date)
                entry["status"] = dl.status
            results.append(entry)
        return json.dumps(results)

    elif name == "get_team_context":
        team = providers.manifests.get_team(inputs["team_name"])
        if not team:
            return f"Team '{inputs['team_name']}' not found."
        audience = inputs.get("audience", "all")
        tickets = providers.jira.get_tickets(team.team)
        recent_prs = [p for p in providers.github.get_pull_requests(team.team) if p.status.value in ("open", "merged")][:5]
        decisions = providers.confluence.get_decision_logs(team.team)

        ctx = {
            "team": team.team,
            "description": team.description,
            "owner": {"name": team.owner.name, "slack": team.owner.slack_handle},
            "slack_channel": team.slack_channel,
            "quarter_goals": team.quarter_goals,
            "dependencies": [{"team": d.team, "reason": d.reason} for d in team.dependencies],
        }

        if audience in ("dev", "all"):
            ctx["code_components"] = [{"name": c.name, "description": c.description} for c in team.components.code]
            ctx["open_tickets"] = [{"id": t.id, "title": t.title, "status": t.status.value, "priority": t.priority.value} for t in tickets if t.status.value != "done"][:8]
            ctx["recent_prs"] = [{"id": p.id, "title": p.title, "status": p.status.value} for p in recent_prs]

        if audience in ("designer", "all"):
            ctx["design_components"] = [{"name": c.name, "description": c.description} for c in team.components.design]
            ctx["figma_files"] = [{"name": f.name, "url": f.url} for f in team.figma_files]
            design_decisions = [p for p in decisions if p.decision_log and any("design" in tag for tag in p.tags)]
            ctx["design_decisions"] = [{"title": p.title, "decision": p.decision_log.decision if p.decision_log else "", "url": p.url} for p in design_decisions[:3]]

        return json.dumps(ctx)

    elif name == "design_sync_status":
        team_filter = inputs.get("team_name")
        components = providers.figma.get_components(team_filter)
        drifted = [c for c in components if c.diverges_from_library]
        synced = [c for c in components if not c.diverges_from_library and not c.is_library_component]

        return json.dumps({
            "total_components_checked": len(components),
            "drifted": [{"name": c.name, "team": c.team, "notes": c.divergence_notes} for c in drifted],
            "synced": [{"name": c.name, "team": c.team} for c in synced],
        })

    elif name == "scan_conflicts":
        detector = DriftDetector(providers)
        issues = detector.run_all()
        result = {"issues": [{"id": i.id, "type": i.type, "severity": i.severity.value, "title": i.title, "description": i.description, "teams": i.teams_involved, "action": i.suggested_action} for i in issues]}
        if inputs.get("include_predictions"):
            conflicts = detector.predict_conflicts()
            result["predicted_conflicts"] = [{"id": c.id, "title": c.title, "description": c.description, "teams": c.teams_involved, "tickets": c.tickets_involved, "severity": c.severity.value, "action": c.suggested_action} for c in conflicts]
        return json.dumps(result)

    elif name == "get_dependency_graph":
        dg = DependencyGraph()
        dg.build(providers.manifests.get_all_teams())
        team_filter = inputs.get("team_name")

        if team_filter:
            team = providers.manifests.get_team(team_filter)
            if not team:
                return f"Team '{team_filter}' not found."
            return json.dumps({
                "team": team.team,
                "depends_on": [{"team": d.team, "reason": d.reason, "components": d.components} for d in team.dependencies],
                "depended_on_by": [{"team": t.team} for t in dg.dependents_of(team.team)],
            })

        return json.dumps(dg.to_dict())

    return f"Unknown tool: {name}"
