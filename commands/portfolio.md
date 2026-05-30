---
description: Leadership rollup across all teams — how many are blocked, at-risk, or on-track, with each team's headline risk.
allowed-tools:
  [
    "mcp__team-sync__portfolio_status",
    "mcp__team-sync__team_health"
  ]
---

# Portfolio Status

Call `portfolio_status` to get the org-wide rollup across all teams.

Present the results as:
1. A summary line: "X on-track / Y at-risk / Z blocked"
2. Each team's status with its headline risk, sorted by severity (blocked first, then at-risk, then on-track)
3. A brief call-to-action: for any blocked team, suggest calling `/team-sync:whoowns` or `/team-sync:conflicts` to investigate further.

If the user names a specific team (e.g. `/portfolio Team Phoenix`), call `team_health` for that team instead of `portfolio_status`.
