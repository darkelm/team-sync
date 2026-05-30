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
        },
        {
            "name": "find_collaborators",
            "description": "Discover teams doing related work who may not realize they should be collaborating. Use for 'who should I talk to', 'who else is working on this', 'are we duplicating effort', 'what collaborations are we missing'.",
            "input_schema": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "reuse_radar",
            "description": "Check whether a component, design, or research already exists before a team builds it. Use for 'has anyone already built X', 'does Y already exist', 'is someone already doing Z'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What the user is about to build or research"},
                    "exclude_team": {"type": "string", "description": "Optional team to exclude (the asking team)"}
                },
                "required": ["description"]
            }
        },
        {
            "name": "check_alignment",
            "description": "Check whether team goals ladder up to company objectives, and which objectives multiple teams pursue. Use for 'are we aligned', 'do our goals map to strategy', 'what's not tied to a company objective'.",
            "input_schema": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "find_resource",
            "description": "Locate where something lives — research repos, brand assets, prototypes, design system, roadmaps, docs. Use for 'where do I find X', 'where is Y', 'where can I get Z'.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What the user is looking for"}},
                "required": ["query"]
            }
        },
        {
            "name": "predict_conflicts",
            "description": "Forecast collisions in planned work before teams start building. Use for 'what conflicts are coming', 'will any teams collide', 'predict problems'.",
            "input_schema": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "cross_team_briefing",
            "description": "Generate a meeting briefing for a sync between two or more teams — shared dependencies, overlapping components, open cross-team tickets, predicted conflicts, recent PRs, suggested agenda. Use for 'prep me for a sync with X and Y', 'brief me for the meeting'.",
            "input_schema": {
                "type": "object",
                "properties": {"teams": {"type": "array", "items": {"type": "string"}, "description": "Two or more team names"}},
                "required": ["teams"]
            }
        },
        {
            "name": "get_action_items",
            "description": "List open action items captured from ingested meeting transcripts. Use for 'what are my action items', 'what did we commit to', 'follow-ups from the meeting'.",
            "input_schema": {
                "type": "object",
                "properties": {"team": {"type": "string", "description": "Optional team filter"}},
                "required": []
            }
        },
        {
            "name": "team_health",
            "description": "Leadership-framed health of one team: on-track/at-risk/blocked, top risks in plain language, what changed since last check, who to talk to. Use for 'how's team X doing', 'is X on track', 'health of X'. Best for PM/MD/leadership questions.",
            "input_schema": {
                "type": "object",
                "properties": {"team_name": {"type": "string"}},
                "required": ["team_name"]
            }
        },
        {
            "name": "portfolio_status",
            "description": "Leadership rollup across ALL teams: how many are blocked/at-risk/on-track and the headline risk for each. Use for 'how are we doing overall', 'portfolio status', 'exec summary'. No per-component detail — built for leadership.",
            "input_schema": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "journey_status",
            "description": "Assess an end-to-end EXPERIENCE/JOURNEY (e.g. onboarding, checkout, notifications) that spans teams: is it coherent across the teams that shape it, what's inconsistent, ownership gaps, the experience owner and north-star. Use for experience-strategy questions like 'how's the onboarding journey?', 'is checkout consistent across teams?'. Omit journey_name to list all journeys.",
            "input_schema": {
                "type": "object",
                "properties": {"journey_name": {"type": "string", "description": "Journey name; omit to list all"}},
                "required": []
            }
        },
        {
            "name": "experience_principles",
            "description": "Report whether the org is upholding its experience/design principles, mapping live signals (inconsistencies, undocumented decisions, collisions) to each principle. Use for 'are we living up to our experience principles', 'design vision adherence'.",
            "input_schema": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "outcome_status",
            "description": "Show the measurable outcomes the org is pursuing: metric, target, owner, and whether open work ladders to each outcome. Flag outcomes with no supporting tickets. Use for 'are we hitting our outcomes', 'north star metrics', 'outcome status for <name>', 'what are our outcomes'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "outcome_name": {"type": "string", "description": "Outcome name or id; omit for all outcomes"}
                },
                "required": []
            }
        },
        {
            "name": "research_insights",
            "description": "Surface research insights relevant to a topic or journey. Flag contradictory findings on the same theme. Use for 'what's the research on X', 'insights about onboarding', 'what do we know about notifications', 'any contradictions in the research'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic, journey name, or theme to search for"}
                },
                "required": []
            }
        }
    ]


def execute_tool(name: str, inputs: dict, providers: Providers) -> str:
    if name == "who_owns":
        from .fuzzy import component_owner
        team, suggestions = component_owner(providers, inputs["component_name"])
        if not team:
            if suggestions:
                return json.dumps({
                    "owner": None,
                    "did_you_mean": [{"component": c, "team": tm} for c, tm in suggestions],
                })
            return f"No team owns '{inputs['component_name']}' yet."
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

    elif name == "find_collaborators":
        from .discovery import CollaboratorDiscovery
        suggestions = CollaboratorDiscovery(providers).find_suggestions()
        return json.dumps([{
            "team_a": s.team_a, "team_b": s.team_b, "already_linked": s.already_linked,
            "reason": s.reason, "evidence": s.evidence,
        } for s in suggestions])

    elif name == "reuse_radar":
        from .discovery import ReuseRadar
        matches = ReuseRadar(providers).search(inputs["description"], inputs.get("exclude_team", ""))
        if not matches:
            return f"Nothing similar found for '{inputs['description']}'. Looks net-new."
        return json.dumps([{
            "kind": m.kind, "name": m.name, "owning_team": m.owning_team,
            "match_terms": m.overlap, "detail": m.detail,
        } for m in matches])

    elif name == "check_alignment":
        from .alignment import AlignmentChecker
        r = AlignmentChecker(providers).run()
        return json.dumps({
            "objectives_pursued_by_multiple_teams": [
                {"objective": title, "teams": teams} for title, _id, teams in r.overlaps
            ],
            "goals_not_linked_to_any_objective": [
                {"team": o.team, "goal": o.goal} for o in r.orphans
            ],
            "linked_goal_count": len(r.linked),
            "orphan_goal_count": len(r.orphans),
        })

    elif name == "find_resource":
        from .findability import FindabilityLocator
        results = FindabilityLocator(providers).find(inputs["query"])
        if not results:
            return f"Couldn't locate anything for '{inputs['query']}'."
        return json.dumps([{
            "label": r.label, "name": r.name, "team": r.team, "url": r.url,
        } for r in results])

    elif name == "predict_conflicts":
        detector = DriftDetector(providers)
        conflicts = detector.predict_conflicts()
        return json.dumps([{
            "title": c.title, "description": c.description, "teams": c.teams_involved,
            "tickets": c.tickets_involved, "severity": c.severity.value, "action": c.suggested_action,
        } for c in conflicts])

    elif name == "cross_team_briefing":
        from .briefing import BriefingGenerator
        return BriefingGenerator(providers).cross_team_briefing(inputs["teams"])

    elif name == "get_action_items":
        import glob
        import os
        import yaml
        with open("config.yaml") as f:
            teams_dir = yaml.safe_load(f).get("data", {}).get("teams_dir", "./data/synthetic/teams")
        team_filter = (inputs.get("team") or "").lower()
        items = []
        for path in glob.glob(os.path.join(teams_dir, "*", "meeting_notes.json")):
            with open(path) as f:
                for note in json.load(f):
                    if team_filter and team_filter not in note.get("team", "").lower():
                        continue
                    for a in note.get("action_items", []):
                        items.append({
                            "meeting": note.get("title"), "team": note.get("team"),
                            "owner": a.get("owner"), "task": a.get("task"), "due": a.get("due"),
                        })
        return json.dumps(items) if items else "No action items found in ingested meetings."

    elif name == "team_health":
        from .health import HealthAssessor
        h = HealthAssessor(providers).assess(inputs["team_name"])
        if not h:
            return f"Team '{inputs['team_name']}' not found."
        return json.dumps({
            "team": h.team, "status": h.status, "headline": h.headline,
            "risks": h.risks, "changes": h.changes, "contact": h.contact,
        })

    elif name == "portfolio_status":
        from .health import HealthAssessor
        healths = HealthAssessor(providers).portfolio()
        return json.dumps([{
            "team": h.team, "status": h.status, "headline": h.headline,
            "top_risk": h.risks[0] if h.risks else None,
        } for h in healths])

    elif name == "journey_status":
        from .strategy import StrategyLens
        s = StrategyLens(providers)
        jn = inputs.get("journey_name")
        if jn:
            h = s.assess_journey(jn)
            if not h:
                return f"No journey named '{jn}'. Known journeys: {', '.join(j.name for j in s.journeys)}."
            return json.dumps({
                "journey": h.name, "status": h.status, "owner": h.owner, "north_star": h.north_star,
                "teams": h.teams, "inconsistencies": h.inconsistencies,
                "collisions": h.collisions, "ownership_gaps": h.ownership_gaps,
            })
        return json.dumps([{"journey": j.name, "status": s.assess_journey(j.name).status,
                            "description": j.description} for j in s.journeys])

    elif name == "experience_principles":
        from .strategy import StrategyLens
        return StrategyLens(providers).principle_report()

    elif name == "outcome_status":
        from .strategy import StrategyLens
        s = StrategyLens(providers)
        on = inputs.get("outcome_name")
        if on:
            assessment = s.assess_outcome(on)
            if not assessment:
                known = ", ".join(o.name for o in s.outcome_list)
                return f"No outcome named '{on}'. Known outcomes: {known}."
            return json.dumps(assessment)
        return s.outcomes()

    elif name == "research_insights":
        from .strategy import StrategyLens
        s = StrategyLens(providers)
        topic = inputs.get("topic", "")
        if not topic:
            # Return a list of all insights with their themes
            return json.dumps([{
                "id": ri.id, "title": ri.title, "themes": ri.themes,
                "journeys": ri.journeys, "source": ri.source, "date": str(ri.date),
            } for ri in s.insights])
        return s.format_insights(topic)

    return f"Unknown tool: {name}"
