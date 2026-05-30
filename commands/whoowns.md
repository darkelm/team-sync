---
description: Find which team owns a component, feature, or surface — and who to talk to.
argument-hint: component or feature name (e.g. "auth", "checkout button", "design tokens")
allowed-tools:
  [
    "mcp__team-sync__who_owns",
    "mcp__team-sync__get_team_context",
    "mcp__team-sync__get_dependency_graph"
  ]
---

# Who Owns

1. Call `who_owns` with `component_name` set to the argument provided.
2. Present:
   - The **owning team** and their primary point of contact
   - Any **co-owners or consumers** of the component
   - The **component type** (code, design, or both)

3. If the user wants more context about the owning team, call `get_team_context` with that team name.
4. If the component is shared by multiple teams, offer to call `get_dependency_graph` to show the full dependency map.

If the component is not found, let the user know and suggest using `find_resource` with a broader search term.
