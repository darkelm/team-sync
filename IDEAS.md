# Ideas & Roadmap

Capabilities beyond the core, grounded in research on how design + dev teams actually fail to coordinate. Plus a map of the channels/products that should *trigger* SyncBot.

---

## Problem-backed capabilities

Each is tied to a documented failure mode and built on primitives we already have (manifests, dependency graph, detector, digest, Slack bot).

### 1. Reuse Radar — "has someone already solved this?"
**Problem:** Silos cause *"duplicated efforts and missed opportunities"* ([Slack](https://slack.com/blog/collaboration/working-in-silos)); design systems accumulate *"duplicate components"* ([UXPin](https://www.uxpin.com/studio/blog/design-system-governance/)).
**What it does:** Before a team starts work, surfaces similar existing components, research, or tickets elsewhere in the org.
**Built on:** component-owner lookup + similarity matching across tickets/Figma/components.
**Effort:** easy/medium · **Status:** ✅ built

### 2. Collaborator Discovery — "you should talk to X"
**Problem:** Teams *"operate independently with little communication"*; the fix is *"teams actually knowing what other teams are working on"* ([Product School](https://productschool.com/blog/leadership/siloed-teams), [180ops](https://www.180ops.com/blog/okr-best-practices-how-to-drive-alignment-and-results/)).
**What it does:** Detects teams doing related work who *don't* list each other as dependencies, and recommends the connection.
**Built on:** component/ticket/dependency overlap we already compute — surfaces the links teams haven't noticed.
**Effort:** easy · **Status:** ✅ built

### 3. Strategic Alignment Checker
**Problem:** *"65% of teams say their OKRs are not clearly linked to company goals"* ([Profit.co](https://www.profit.co/blog/okr-university/how-to-align-okrs-across-a-large-enterprise-without-losing-momentum/)).
**What it does:** Flags team goals not linked to any org objective, and two teams pursuing overlapping goals independently.
**Built on:** `quarter_goals` in manifests + an org objectives file.
**Effort:** easy · **Status:** ✅ built

### 4. Findability Locator — "where do I find X?"
**Problem:** Research and assets *"scattered in folders"*; KM is about *"the right knowledge to the right people at the right time"* ([Hack Design](https://www.hackdesign.org/toolkit/obsidian/), [SpringerLink](https://link.springer.com/chapter/10.1007/978-981-99-0428-0_59)).
**What it does:** Federated "where does this live" — canonical Figma file, research repo, brand assets.
**Built on:** a `resources:` registry per team + a Slack query.
**Effort:** easy/medium · **Status:** planned

### 5. Design System Adoption Scorecard
**Problem:** Adoption is uneven; *"handed-down systems get seen as a restriction"* ([Netguru](https://www.netguru.com/blog/design-system-adoption-pitfalls)).
**What it does:** Per-team adoption % and drift trend, surfaced as a friendly nudge + contribution prompt (not a mandate).
**Built on:** existing drift detection, aggregated into a score over time.
**Effort:** medium · **Status:** planned

### 6. Duplicate-Work Detection (semantic)
**Problem:** Conceptually identical work under different names ("notification bell" vs "alert indicator").
**What it does:** Semantic similarity across work items, not keyword matching.
**Built on:** the Claude agent (this is where it earns its keep).
**Effort:** medium (needs Anthropic key) · **Status:** planned

---

## Trigger map — what should wake SyncBot

The connectors-off philosophy means triggers come in three flavors: **webhooks** (often allowed even when full API read is locked down), **scheduled scans** of exports/snapshots, and **manual** (drop a file / ask in Slack).

### Input / signal sources (where coordination signal originates)

| Source | Event that should trigger us | Action |
|---|---|---|
| **Figma / FigJam** | Library component published | Notify consuming teams; scan drift |
| | New file/frame named like an existing one | Reuse Radar — "similar design exists" |
| | Comment mentioning another team | Collaborator Discovery |
| **Jira / Linear** | Ticket created / moved to In Progress | Reuse Radar + duplicate-work check + suggest collaborators |
| | `breaking-change` label added | Require/flag decision log; alert dependents |
| | Sprint/PI planning opens | Run conflict prediction across planned work |
| **Confluence / Notion** | New page tagged decision/ADR | Link to related tickets; fill decision-log gaps |
| | Research study published | Reuse Radar; notify teams researching similar |
| **GitHub / GitLab** | PR opened touching shared component | Pre-merge cross-team impact check |
| | PR merged | Dependency alert to dependents |
| **Slack / Teams** | New `#team-*` channel created | Onboard team; prompt for a manifest |
| | Message asking "who owns / where is…" | Answer inline (already live) |
| **Calendar (Google/Outlook)** | Event titled like a cross-team sync | Auto-post a meeting briefing ~1hr before |
| | Quarter boundary | Strategic Alignment check |
| **Roadmap tools (Productboard/Aha)** | Delivery date shifts | Notify dependent teams |
| **Research repos (Dovetail/Maze)** | New study tagged | Reuse Radar; surface to teams in that problem space |
| **Google Drive / Docs** | New design brief / research deck | Index for Findability; link to owning team |
| **Storybook / Tokens Studio** | Token or component version bump | Notify consumers; drift scan |
| **Design handoff (Zeplin/Abstract)** | Handoff marked ready | Update handoff-status answers |

### Output / delivery channels (where we reach people)

- **Slack / Teams** — primary: digests, alerts, Q&A (Slack live today)
- **Email** — weekly digest for non-Slack-native stakeholders (PMs, leadership)
- **Figma plugin panel** — "who else uses this component / is it in sync" where designers already work
- **Calendar** — auto-attach briefings to cross-team meetings
- **PR comments** — cross-team impact warning inline in GitHub/GitLab
- **IDE** — surface ownership/decisions where devs work

### Realistic trigger tiers (given enterprise access constraints)

1. **Easiest to enable:** Slack events, Figma webhooks, GitHub webhooks — often approved when full API read is not.
2. **Scheduled snapshot scans:** re-import exports nightly; run drift/conflict/alignment on the fresh snapshot. Works with zero live access.
3. **Manual / pull:** ask in Slack, drop a file. Always available.

> Design principle: every trigger maps to an existing detector or query — we don't add intelligence per channel, we just add *new ways to wake the same brain*.

---

## Suggested build order

1. ✅ Collaborator Discovery · Strategic Alignment · Reuse Radar (no new credentials)
2. Findability Locator (small manifest addition)
3. Calendar-triggered briefings + Figma webhook (highest-value new triggers)
4. Claude agent → unlocks semantic duplicate-work detection
5. Adoption Scorecard (trend data over time)
6. Email + Figma-plugin output channels
