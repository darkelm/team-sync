---
description: Scan for current cross-team conflicts — design drift, missing decision logs, PR impact on shared components — and optionally predict upcoming collisions.
argument-hint: "predict" to include predicted future conflicts (optional)
allowed-tools:
  [
    "mcp__team-sync__scan_conflicts",
    "mcp__team-sync__predict_conflicts",
    "mcp__team-sync__find_collaborators"
  ]
---

# Conflict Scan

1. Call `scan_conflicts`. If the argument is "predict" or contains the word "predict", pass `include_predictions=true`.
2. If the user did not pass "predict" but the scan returns active conflicts, offer to also run `predict_conflicts` to forecast future collisions.

Present results grouped by severity:
- **Active conflicts** — drift, missing decisions, PR impact (resolve these now)
- **Predicted conflicts** — planned work collisions (plan around these)

For each conflict:
- Name the teams and components involved
- State the type of conflict (design drift / missing decision / shared component PR / planned collision)
- Suggest the next action (e.g. "set up a sync between Team A and Team B", "log a decision", "add Team C as a reviewer")

If no conflicts are found, call `find_collaborators` to surface teams that may benefit from proactive coordination even without active conflicts.
