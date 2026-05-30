---
description: Assess an end-to-end customer journey (onboarding, checkout, notifications) that spans multiple teams — coherence, inconsistencies, ownership gaps, and north-star alignment.
argument-hint: journey name (e.g. "onboarding", "checkout") — omit to list all journeys
allowed-tools:
  [
    "mcp__team-sync__journey_status",
    "mcp__team-sync__experience_principles",
    "mcp__team-sync__who_owns"
  ]
---

# Journey Status

1. If an argument was provided, call `journey_status` with `journey_name` set to that argument.
2. If no argument was provided, call `journey_status` with no `journey_name` to list all known journeys, then ask the user which one to assess.

Present the result with:
- **Journey owner** and teams involved
- **Coherence score** or qualitative assessment
- **Inconsistencies** — what conflicts across team touchpoints
- **Ownership gaps** — parts of the journey with no clear owner
- **North-star** — the intended experience

If inconsistencies are found, offer to call `experience_principles` to check whether the org's design principles are being upheld, or `who_owns` to clarify ownership of a disputed touchpoint.
