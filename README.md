# SyncBot — Multi-Team Coordination Agent

Keeps design and dev teams in sync on large projects. Detects drift, predicts conflicts, surfaces decisions, and delivers weekly briefings — automatically.

Built to prove out with synthetic data first, then swap in real integrations via a provider adapter pattern (one config line per integration).

### Documentation map
- **README** (this file) — the vision: what it does, the 5 layers, architecture, status
- **[IDEAS.md](IDEAS.md)** — research-backed capability roadmap + the trigger/channel map
- **[POSITIONING.md](POSITIONING.md)** — competitive landscape and strategy
- **[VS-COPILOT.md](VS-COPILOT.md)** — how this differs from Copilot / Claude-in-Slack / ChatGPT connectors (the most-asked question)
- **[ADOPTION.md](ADOPTION.md)** — the plan to remove pilot barriers (manifest builder, freshness, NLU, fatigue, hosting)
- **[DEPLOY.md](DEPLOY.md)** — cloud deployment (Railway/Render/Fly)
- **[SPECS.md](SPECS.md)** — detailed implementation specs for all remaining work
- **[DEMO.md](DEMO.md)** — live demo cheat sheet

> How they fit: README is the *what*, ADOPTION is the *how people start*. The Manifest Builder (ADOPTION Phase 1) is the on-ramp to **Layer 1 — Foundation** below; everything else builds on it.

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
- [x] Research-backed capability roadmap + trigger map (IDEAS.md)
- [x] **Collaborator Discovery** — finds teams doing related work who aren't connected (live in Slack)
- [x] **Reuse Radar** — "has anyone already built/researched this?" before you start (live in Slack)
- [x] **Strategic Alignment Checker** — flags goals not laddering up to company objectives (live in Slack)

- [x] **Findability Locator** — "where do I find X" across resources, Figma, roadmaps, docs (live in Slack)
- [x] Fully team-agnostic — no hardcoded team names; design-system owner detected from data
- [x] Expanded trigger map incl. meeting transcripts, whiteboards, analytics, customer feedback (IDEAS.md)

- [x] **Meeting-transcript ingestion** — parses VTT/SRT/TXT, extracts decisions/action items/cross-team flags/risks; decisions become searchable (closes the "decided verbally, lost forever" gap)
- [x] **Multi-Source Manifest Builder** (ADOPTION Phase 1) — fuses repo/git/CODEOWNERS/roster/Jira/transcript into a provenance-annotated draft `team.yaml`; kills the cold-start barrier
- [x] **Living manifests** (ADOPTION Phase 2) — `refresh-manifest` diffs reality vs manifest and proposes updates; `last_verified` freshness stamps; staleness flagged in `validate`

- [x] **Claude agent** (ADOPTION Phase 3) — Opus 4.8 + adaptive thinking + prompt caching; all 14 capabilities exposed as tools; auto-activates when `ANTHROPIC_API_KEY` is set, keyword fallback otherwise; bot self-introduces on channel join
- [x] **MCP server** (ADOPTION Phase 6) — all 14 tools exposed via Model Context Protocol; usable from Claude Desktop, Cursor, Cline, Gemini; the cross-platform portability unlock

- [x] **Structured outputs — meeting extraction + semantic reuse** (ADOPTION Phase 3.5) — `messages.parse()` returns the same `DecisionLog`/`ActionItem`/`ReuseMatch` schemas as the heuristics; AI when a key is present, heuristic fallback otherwise (`src/agent/ai_enhance.py`)
- [x] **Notification tuning** (ADOPTION Phase 4) — per-team severity thresholds, pause/resume, section toggles, and a quality gate (digest only sends if something changed); tunable from Slack

- [x] **No-terminal, channel-neutral import** (ADOPTION Phase 5) — one ingest core (`src/ingest.py`); adapters for CLI, Slack file upload, and MCP (`import_export`); adding Teams/web is a thin adapter, not a rewrite
- [x] **Audience-aware experience** (ADOPTION Phase 7) — leadership rollup (`how's <team> doing?` / `portfolio status` — health, risks, trajectory, no component noise), weekly exec digest, per-user role framing (`@syncbot I'm a designer`), and a plain-language layer for non-technical readers (web dashboard intentionally out of scope)
- [x] **Strategy & Experience layer** — coordination *above* components: **Journeys** (end-to-end experiences spanning teams — coherence, inconsistencies, ownership gaps, experience owner, north-star) and **Experience Principles** (maps live signals to whether the org is upholding its design vision). Surfaces via Slack, agent tools, and MCP (`journey_status`, `experience_principles`)

### Next
- [ ] Add the Anthropic API key to flip the live bot + meeting extraction + reuse into AI mode (no code change)
- [ ] Deploy to Railway (browser steps + env vars — config ready)
- [ ] Phase 3.5 cont. (manifest field inference) + Batch API + IT/Slack approval kit
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

## Starting on a new engagement — the Manifest Builder

The fastest path from zero to working. Instead of hand-writing a `team.yaml`, point the builder at **whatever the client has** — it fuses every source, shows its provenance, and drafts a reviewable manifest. Works with any subset (a repo alone is enough; more sources = sharper draft).

```bash
# Any mix of: a repo, a CODEOWNERS file, a roster/Jira CSV, a meeting transcript
syncbot build-manifest ../client-repo roster.csv design-review.txt --team "Payments" \
  -o data/imported/teams/payments/team.yaml

# Or guided:
syncbot build-manifest
```

The draft annotates every field with where it came from and how confident it is, and lists `TODO`s for anything no source covered (goals, strategy):

```yaml
owner:
  name: Dana Whitfield   # roster role: Design Lead [roster]  ← explicit beats inferred
members:
  - name: Ty Shaw        # 100% of recent commits [git]
components:
  code:
    - name: auth         # from repo folder src/auth [repo]
quarter_goals: []        # TODO: ask the team (strategy — no source can infer this)
```

Review it, confirm inferred fields, fill the `TODO`s, then `syncbot validate`. See [ADOPTION.md](ADOPTION.md) for the full multi-source design.

---

## Connectors-off mode (the enterprise wedge)

The differentiator: SyncBot works **before IT approves a single API connector**, using the exports anyone can pull today.

> **Who does this?** Only one person, once per team, at setup. Everyone else just talks to the bot in Slack — they never touch a command line.

There's **one command**. It auto-detects whether you handed it a Jira CSV, a Confluence export folder, or a git clone, and derives the team folder name for you:

```bash
syncbot import export.csv --team "Team Phoenix"      # one-liner
syncbot import                                        # guided wizard (just asks 2 questions)
```

Where to get each export (no admin access needed):
- **Jira:** Issue Navigator → Export → CSV
- **Confluence:** Space Settings → Export → HTML, then unzip (or any folder of `.md`/`.html`)
- **GitHub:** any local `git clone` — reads merge history, no token

Each import normalizes into the same JSON the local providers read, so every feature (drift, conflicts, digests, briefings) works identically — no live access required. When connectors are eventually approved, flip the provider to `live` and the same features run against the API instead.

---

## Use it from any AI — the MCP server

The portability layer. SyncBot's full coordination engine (all 14 tools) is exposed as a **Model Context Protocol server**, so Claude Desktop, Cursor, Cline, Gemini, or any MCP-compatible client gets the same grounded tools that back the Slack bot — no rewrite.

```bash
# Run directly (stdio)
python mcp_server.py
```

To connect a client, point it at [`.mcp.json`](.mcp.json) (Claude Code/Cursor read this format directly):

```json
{
  "mcpServers": {
    "team-sync": { "command": "./.venv/bin/python3", "args": ["./mcp_server.py"] }
  }
}
```

The same `execute_tool` handlers back both the Slack agent and the MCP server — one brain, many doorways. Any connected model gets the org's ground truth (owners, tickets, drift, decisions) instead of hallucinating it. The engines behind the tools are pure code, so the server is useful even to clients without their own model. Requires Python 3.10+ (the MCP SDK's floor).

## Portability

The agent layer is platform-agnostic and the provider interfaces are the contract. The MCP server above is the primary portability path; for a fully native integration on another platform, reuse the provider layer and `execute_tool` and write a thin wrapper for that platform's runtime.
