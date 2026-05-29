# SyncBot — Multi-Team Coordination Agent

Keeps design and dev teams in sync on large projects. Detects drift, predicts conflicts, surfaces decisions, and delivers weekly briefings — automatically.

Built to prove out with synthetic data first, then swap in real integrations via a provider adapter pattern (one config line per integration).

---

## The Problem

On large projects with multiple teams:
- Teams build the same component slightly differently (design drift, code drift)
- Breaking changes ship without the dependent teams knowing
- No one knows who owns what, or where the decision log is
- Cross-team dependencies exist but aren't visible until something breaks
- New team members take weeks to get context that doesn't live anywhere in one place

---

## What This Does

### Automatic (no one has to ask)
- **Monday digest** — each team's Slack channel gets a weekly summary: what changed in systems they depend on, open conflicts, design system updates
- **Dependency alerts** — PR merged touching a shared component → Slack alert fires to dependent teams
- **Design system change alerts** — Figma library updated → affected teams notified
- **Conflict detection** — breaking changes flagged when they lack a decision log
- **Drift scanning** — daily scan for diverging component implementations across teams

### On Demand (via Slack or CLI)
- `@syncbot who owns the auth component?`
- `@syncbot when does Team Atlas ship?`
- `@syncbot what was decided about the OAuth migration?`
- `@syncbot get me up to speed on Team Horizon` (tailored for dev or designer)
- `@syncbot is my design in sync with the design system?`
- `@syncbot scan for conflicts`
- `@syncbot prep me for my cross-team sync tomorrow`

### For Designers Specifically
- Figma as a first-class data source — not an afterthought
- Design drift detection across team Figma files vs the canonical library
- Design decision log separate from dev decisions
- Onboarding pulls Figma files + design system docs, not just code

---

## Stack

| Integration | Status | Provider |
|---|---|---|
| Jira | ✅ Live | `tyshawdesign.atlassian.net` |
| Confluence | ✅ Live | `tyshawdesign.atlassian.net` |
| GitHub | 🔜 Ready to activate | needs PAT |
| Slack | 🔜 Ready to activate | needs app token |
| Figma | 🔜 Ready to activate | needs access token |
| Claude agent | 🔜 Optional | needs Anthropic API key |

Switching any integration from synthetic to live is one line in `config.yaml`.

---

## Architecture

```
┌─────────────────────────────────────────┐
│         Skill / Agent Layer             │  ← same for Claude, Replit, Gemini, etc.
│   (CLI, Slack bot, Claude chat agent)   │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│         Data Interface Layer            │  ← never changes
│  (provider base classes + tool schemas) │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│            Provider Layer               │  ← swap local ↔ live via config
│  ┌──────────────┐  ┌──────────────────┐ │
│  │ LocalProvider│  │  LiveAPIProvider  │ │
│  │ (files/JSON) │  │ (Jira, GH, Slack) │ │
│  └──────────────┘  └──────────────────┘ │
└─────────────────────────────────────────┘
```

---

## The 5 Layers

### ✅ Layer 1 — Foundation
The `team.yaml` manifest standard — every team maintains one file:

```yaml
team: Team Phoenix
owner: { name: Sarah Chen, slack_handle: "@sarah.chen", ... }
slack_channel: "#phoenix-team"
jira_project: PHX
confluence_space: PHOENIX
figma_files:
  - name: Phoenix Auth Flows
    url: figma.com/file/...
components:
  code: [auth, login, token-manager]
  design: [LoginFlow, AuthModal]
dependencies:
  - team: Team Atlas
    reason: Consumes user-profile API
    components: [user-profile-api]
quarter_goals:
  - Ship OAuth 2.0 PKCE by Q2
```

CLI: `syncbot validate` · `syncbot graph` · `syncbot who-owns <component>` · `syncbot when-ships <team>`

### ✅ Layer 2 — Query Layer (Reactive)
Answers questions on demand. Pulls from manifests, Jira, Confluence, GitHub, and Figma.

Tools: `who_owns` · `when_ships` · `find_decision` · `get_team_context` · `design_sync_status` · `scan_conflicts` · `get_dependency_graph`

Works as CLI commands today. Powers the `@syncbot` Slack bot and Claude chat agent when those are activated.

### ✅ Layer 3 — Watch Layer (Proactive)
Runs automatically and surfaces issues without anyone asking:
- Design drift: Figma components diverging from library
- Code drift: same component claimed by multiple teams
- Missing decision logs: breaking changes with no Confluence record
- Cross-team PR impact: merged PRs touching shared components
- Dependency alerts: recent merges affecting dependent teams

Weekly digest generator: per-team Slack message with dev section + design section.

### 🔜 Layer 4 — Prediction Layer
- Scans open Jira tickets across teams for planned work collisions before they start
- Pre-meeting briefings generated on demand
- "You and Team Atlas are both planning API gateway changes in Q3 — coordinate now"

*Conflict prediction is built and running against synthetic data. Needs real Jira tickets to prove out.*

### 🔜 Layer 5 — Onboarding
- `@syncbot get me up to speed on Team X`
- Tailored by role: dev gets code components + tickets + recent PRs; designer gets Figma files + design system + design decisions
- Works today in CLI mode against synthetic data

---

## Current Status

### Done
- [x] Full project scaffold (Python, Pydantic schemas, pyproject.toml)
- [x] Provider adapter pattern — local and live implementations for all 5 integrations
- [x] Synthetic org: 5 teams, realistic Jira tickets, Confluence pages, PRs, Figma components — with intentional drift and conflicts baked in
- [x] Live Jira + Confluence connected (`tyshawdesign.atlassian.net`)
- [x] CLI: `validate`, `graph`, `who-owns`, `when-ships`, `decisions`, `scan`
- [x] Drift detector: design drift, code drift, missing decision logs, cross-team PR impact
- [x] Conflict predictor: planned work collisions across teams
- [x] Digest generator: per-team weekly Slack message (dev + design sections)
- [x] Claude agent query layer: 7 tools wired, full agentic loop
- [x] Slack manifest ready to paste in
- [x] Pushed to GitHub: [darkelm/team-sync](https://github.com/darkelm/team-sync)
- [x] Slack bot — LIVE and responding (`@syncbot scan for conflicts` works in workspace PrototypeToolsPilot)
- [x] Live Jira + Confluence providers hardened for empty/restricted instances
- [x] Cloud deployment config ready (Railway/Render/Fly — see DEPLOY.md)
- [x] Proactive: weekly digest scheduler, conflict prediction, cross-team meeting briefings (all live in Slack)
- [x] **Connectors-off import path** — Jira CSV, Confluence HTML/MD, GitHub clone → normalized JSON (the enterprise wedge; see Connectors-off mode)
- [x] Competitive analysis + strategy (POSITIONING.md)

### Next
- [ ] **Claude agent** — natural-language understanding (needs Anthropic API key; replaces keyword matching — highest-leverage upgrade)
- [ ] **Deploy to Railway** — bot currently only runs while a local terminal session is alive; deploy as a background worker for 24/7 uptime (config + guide ready in DEPLOY.md, needs browser steps + rotated tokens)
- [ ] GitHub live provider — activate (PAT needed)
- [ ] Figma live provider — activate (access token needed)
- [ ] Claude agent — activate conversational layer (Anthropic API key needed) for freeform natural-language queries
- [ ] Real Jira/Confluence data — create real team manifests pointing at actual GAPTT/GAPTS projects
- [ ] Scheduled digest — run automatically every Monday morning
- [ ] GitHub PR webhook — trigger dependency alert on merge

### Future
- [ ] Figma webhook — trigger design drift scan when library component updates
- [ ] Pre-meeting briefing command
- [ ] Layer 4: full prediction layer against real ticket data
- [ ] Layer 5: role-aware onboarding assistant
- [ ] Port to other platforms (Replit, Gemini) via the same provider interface

---

## Setup

```bash
# Clone and install
git clone https://github.com/darkelm/team-sync.git
cd team-sync
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Configure
cp .env.example .env
# Fill in .env with your tokens

# Run (works on synthetic data with no tokens)
python3 demo.py

# CLI
.venv/bin/python3 -m src.cli.main validate
.venv/bin/python3 -m src.cli.main graph
.venv/bin/python3 -m src.cli.main scan
```

### Activating live integrations

Edit `config.yaml`:
```yaml
providers:
  jira: live        # needs ATLASSIAN_URL, ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN
  confluence: live  # same credentials as jira
  github: live      # needs GITHUB_TOKEN, GITHUB_ORG
  slack: live       # needs SLACK_BOT_TOKEN, SLACK_APP_TOKEN
  figma: live       # needs FIGMA_ACCESS_TOKEN
```

### Slack app setup
Use the manifest at `slack/manifest.json` — paste into `api.slack.com/apps` → Create App → From Manifest.

---

## Connectors-off mode (the enterprise wedge)

The differentiator: SyncBot works **before IT approves a single API connector**, using the exports anyone can pull today.

```bash
# Jira: Issue Navigator → Export → CSV
syncbot import jira export.csv --team "Team Phoenix" --slug team-phoenix

# Confluence: Space Settings → Export → HTML (unzip), or any folder of .md/.html
syncbot import confluence ./phoenix-space --team "Team Phoenix" --slug team-phoenix --space PHX

# GitHub: any local clone — reads merge history via git log, no token
syncbot import github ../phoenix-repo --team "Team Phoenix" --slug team-phoenix --days 90
```

Each importer normalizes the export into the same JSON the local providers read, so every feature (drift, conflicts, digests, briefings) works identically — no live access required. When connectors are eventually approved, flip the provider to `live` and the same features run against the API instead.

---

## Portability

The skill/agent layer is platform-agnostic. The provider interfaces are the contract — any platform can call them. To port to Replit, Gemini, or another AI platform: reuse the provider layer and tool schemas, write a thin wrapper for that platform's agent runtime.
