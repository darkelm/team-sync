---
name: team-sync
description: This skill should be used when the user asks cross-team coordination questions such as "who owns X", "which team builds Y", "is the checkout journey consistent across teams", "what's the portfolio health", "are we blocking another team", "find a decision about Z", "when does team X ship", "are our goals aligned", "find collaborators for this work", "has anyone built this before", "check design system sync", "generate a cross-team briefing", "what conflicts exist", or "predict upcoming collisions". Routes these questions to the SyncBot MCP tools for grounded, manifest-backed answers.
---

# SyncBot Team Coordination

SyncBot is a multi-team coordination engine that answers questions about ownership, delivery, design consistency, strategic alignment, and cross-team dependencies. It is grounded in team manifests, Jira exports, Confluence docs, GitHub activity, and Figma components — not guessing.

## When to Use SyncBot Tools

Use the `team-sync` MCP tools for any of the following:

- **Ownership questions**: who owns a component, feature, or surface
- **Delivery questions**: what a team is shipping and when
- **Dependency questions**: what teams depend on each other, shared components
- **Design-system questions**: whether Figma components are in sync with the library
- **Journey questions**: whether an end-to-end customer journey is consistent across all teams that touch it
- **Portfolio questions**: leadership-level health rollup across all teams
- **Conflict detection**: current drift, missing decision logs, PR impact on shared components
- **Conflict prediction**: forecast collisions in planned work before teams start building
- **Alignment questions**: whether team goals ladder to company objectives
- **Collaborator discovery**: find teams doing related work who should be talking
- **Reuse radar**: check whether a component, design, or research already exists
- **Decision search**: find decisions captured from meetings or documentation
- **Resource location**: find where research repos, brand assets, prototypes, or docs live
- **Meeting action items**: retrieve open action items from ingested transcripts
- **Team context briefing**: full context for a team (for a specific role: dev, designer, pm)
- **Cross-team briefing**: meeting briefing for a sync between two or more teams
- **Event simulation**: preview who would be notified when a trigger event fires

## Tool Reference

| Tool | When to call it |
|------|----------------|
| `who_owns` | "who owns X", "which team is responsible for Y" |
| `when_ships` | "when does team X ship", "what's X delivering" |
| `find_decision` | "what was decided about X", "find the decision on Y" |
| `get_team_context` | "brief me on team X", "what does team X do" |
| `design_sync_status` | "are Figma components in sync", "design drift" |
| `get_dependency_graph` | "who depends on whom", "dependency map" |
| `find_resource` | "where is the design system", "where does research live" |
| `get_action_items` | "open action items", "what did we commit to" |
| `scan_conflicts` | "any conflicts right now", "what's drifted" |
| `predict_conflicts` | "predict collisions", "future conflicts" |
| `find_collaborators` | "who else is working on X", "find collaborators" |
| `reuse_radar` | "has this been built before", "check for existing work" |
| `check_alignment` | "are our goals aligned", "strategic alignment" |
| `cross_team_briefing` | "prep a cross-team sync", "meeting brief for X and Y" |
| `import_export` | "import this Jira CSV", "ingest this transcript" |
| `team_health` | "team X health", "is team X on track" |
| `portfolio_status` | "portfolio health", "org rollup", "exec summary" |
| `journey_status` | "is the onboarding journey consistent", "journey health" |
| `experience_principles` | "are we upholding our design principles" |
| `emit_event` | "simulate a trigger", "what happens if X is published" |

## Instructions

1. Identify which SyncBot tool best answers the user's question using the table above.
2. Call the tool with the appropriate inputs. Most tools accept a `team_name`, `component_name`, or a free-text `query`.
3. Present the result in plain language. SyncBot returns structured text — quote key findings and highlight owners, risks, or blockers.
4. If the result surfaces a conflict or risk, offer to call `scan_conflicts` or `predict_conflicts` for deeper analysis, or `cross_team_briefing` to generate a meeting agenda.
5. If a team or component is not found, suggest running `portfolio_status` to see all known teams, or `get_dependency_graph` to see the full component map.
