# Adoption Plan — removing the barriers to a real pilot

The product capability is ahead of the adoption ergonomics. The path from "install" to "first value" still runs through hand-written manifests and a terminal — that's where a pilot stalls. This plan fixes all five barriers, in priority order.

**Consultancy constraint (drives the whole design):** every engagement has a *different* mix of tools. Sometimes there's no Jira. Sometimes only a repo and a Confluence space. Sometimes only meeting recordings and a roster spreadsheet. So nothing can depend on a single source — the system must **fuse whatever is available, degrade gracefully, and show its work**.

**Guiding principle — AI-optional architecture:** the engines are the product. Every capability has a working **non-AI implementation** (deterministic code or heuristics); AI is an *enhancement on top*, never the only path. AI supercharges the system two ways — a natural-language interface on the front, and (where added) a quality lift to the heuristics that returns the **same schema** the non-AI path produces. The system also supercharges AI back: it is the grounding layer that keeps any model honest. No feature may live only in the AI path.

---

## The five barriers (ranked)

| # | Barrier | Severity |
|---|---|---|
| 1 | Manifest authoring (cold-start cost) | 🔴 Critical |
| 2 | Data + manifest staleness | 🔴 Critical |
| 3 | Brittle NLU + command discoverability | 🟠 High |
| 4 | Notification fatigue | 🟠 High |
| 5 | Hosting + IT/Slack approval | 🟡 Medium |

> Phases 1–5 remove adoption barriers. Phases 3.5 and 6 are **capability multipliers** drawn from Anthropic's current model guidance — woven in where they reinforce the principle above rather than bolted on at the end.

---

## Phase 1 — Multi-Source Manifest Builder (kills barrier #1) ✅ BUILT

**Goal:** turn "hand-write a YAML file" into "point me at whatever you have, I'll draft it, you correct it."

**Status:** Built. `syncbot build-manifest <sources...> --team "X"` fuses repo structure, git history, CODEOWNERS, roster CSV, Jira CSV, and meeting transcripts into a provenance-annotated draft `team.yaml`. Explicit sources beat inferred; gaps flagged as TODOs. Adapters for Confluence/Figma/Slack are the next additions.

### Source adapters (use any subset that exists)
Each adapter extracts candidate manifest fields *with provenance and confidence*. The builder fuses them. Ranked by how authoritative each signal is:

| Source | What it infers | Confidence |
|---|---|---|
| **CODEOWNERS file** | component ownership, team boundaries | highest (explicit) |
| **Org roster / spreadsheet** (CSV/XLSX) | teams, members, roles, channels | highest (explicit) |
| **Repo folder structure** | components and their paths | high |
| **Git history** | owners & members (who commits where), activity | high |
| **Dependency files** (package.json, imports, go.mod, CODEOWNERS) | cross-component dependencies | high |
| **Jira CSV** (if present) | components, assignees, project keys | high |
| **Confluence / Notion export** | team pages, ownership statements, decision logs | medium |
| **Figma workspace** | design files, library, design components | medium |
| **Slack channels + membership** | team channels, members | medium |
| **README / docs** | team descriptions, mission, ownership notes | medium |
| **Meeting transcripts** | who speaks for what area, working groups | low (corroborating) |
| **Directory / email signatures** | names, roles, reporting lines | low |

### How fusion works
1. Each adapter emits `field → value → (source, confidence)` records.
2. Builder merges per field: **explicit sources win** (CODEOWNERS, roster) over **inferred** (git, transcripts). Conflicts are surfaced, not silently resolved.
3. Output is a **draft `team.yaml` with inline provenance comments**, e.g.:
   ```yaml
   owner:
     name: Sarah Chen   # inferred: 62% of commits in src/auth (git) — CONFIRM
   components:
     code:
       - name: auth     # from repo folder src/auth + CODEOWNERS
   dependencies:
     - team: Team Atlas # inferred: src/auth imports @atlas/user-profile — CONFIRM
   # TODO (no source found): quarter_goals, slack_channel
   ```
4. **Human-in-the-loop:** the consultant reviews, corrects low-confidence fields, fills `TODO`s the data couldn't supply (strategy, goals).
5. **Gap-filling wizard:** for fields no source covers, an interactive prompt asks only the missing questions — never re-asking what was inferred.

### Why this fits a consultancy
- Works on **any subset** of sources — a repo alone produces a usable draft; add a roster and it gets sharper.
- **Provenance = trust.** Consultants can defend every field to a client ("this came from your CODEOWNERS, this we inferred and need you to confirm").
- Re-runnable per engagement; no assumption that Jira/Atlassian exists.

---

## Phase 2 — Living manifests (kills barrier #2, staleness) ✅ BUILT

- **Manifest drift detection:** `syncbot refresh-manifest <sources> --team X` re-scans and diffs reality against the manifest — new/removed components, owner changes (only when an explicit source disagrees), new members, newly-implied dependencies — each with provenance.
- **Freshness metadata:** `last_verified` on every manifest; `syncbot validate` flags manifests never verified or older than 30 days.
- **Next:** wire refresh into the scheduler for automatic nightly re-scan + a "manifest drift" line in the weekly digest.

---

## Phase 3 — Understanding + discoverability (kills barrier #3) ✅ BUILT

- **Claude agent** ✅ — Opus 4.8 with adaptive thinking, prompt caching on the system+tools prefix, and all 14 capabilities exposed as tools. Activates automatically when `ANTHROPIC_API_KEY` is set; the bot falls back to keyword matching (and on any agent error) otherwise. Set the key — no other change needed. Also the path to better transcript extraction and semantic duplicate detection.
- **Bot self-introduction** ✅ — posts a short "here's what I can do" with example questions when added to a channel. Solves discovery.
- **Graceful fallback** ✅ — agent errors fall back to keyword answers rather than dead-ending.
- **Next:** richer "I don't know but here's who might" responses.

---

## Phase 3.5 — Structured outputs: make the AI-enhanced heuristics reliable (capability multiplier) ✅ BUILT

Uses Anthropic **structured outputs** (`messages.parse()` with Pydantic) so Claude returns the **exact same schema** the heuristics produce — a drop-in quality lift, not a parallel codepath. The pattern is established in `src/agent/ai_enhance.py`: AI selected only when a key is present, schema-identical results, automatic fallback to the heuristic on any failure.

- **Meeting extraction** ✅ — `MeetingAnalyzer.analyze` now prefers AI (clean decisions-vs-discussion, owners resolved, due dates) returning the same `DecisionLog` / `ActionItem` objects; falls back to the regex extractor with no key or on error. The summary records which path ran (`via ai` / `via heuristic`).
- **Semantic reuse / duplicate detection** — next, same pattern: real meaning ("alert badge" ≈ "notification bell") returning the same `ReuseMatch` shape as the Jaccard version.
- **Manifest inference** ✅ — `ManifestBuilder` reads the repo README and, when a key is present, AI-fills the team `description` and per-component descriptions (instead of TODO/"Code module at …"); silently skipped without a key, so the draft is identical otherwise.
- **Citations** (optional add-on): cite Confluence source spans — reinforces provenance/trust.
- **Discipline upheld:** each keeps its heuristic implementation; structured-output AI is selected only when a key is present. Schema parity is the rule.

---

## Phase 4 — Notification tuning (kills barrier #4, fatigue) ✅ BUILT

- **Per-team preferences** ✅ — `src/agent/preferences.py` stores per-team settings in `data/notification_prefs.json` (tunable from Slack, no manifest edits).
- **Severity threshold** ✅ — `@syncbot only alert <team> on high` filters digest issues/predictions below the chosen level.
- **Pause / resume** ✅ — `@syncbot mute digests for <team>` / `resume digests for <team>`; paused teams are skipped.
- **Section toggles** ✅ — dev/design sections can be turned off per team.
- **Quality gate** ✅ — a digest only sends if its actionable content changed since the last one (`post_all_digests` skips no-change teams; on-demand `post digests` forces send).
- **Next — Batch API (capability multiplier):** run scheduled/bulk AI work (many-team digests, nightly drift summaries) through Anthropic's Batch API at 50% cost once digests are AI-written and running at scale.

---

## Phase 5 — Make it real (kills barrier #5) ◑ STARTED

- **No-terminal setup — channel-neutral** ✅ — import logic lives in one channel-agnostic core (`src/ingest.py`); every surface is a thin adapter that hands it `(filename, bytes/path, team)`. Built adapters: **CLI** (delegates to the core), **Slack file upload** (DM the bot a file with "for Team X" → it imports; needs the `files:read` scope, now in the manifest), and **MCP** (`import_export` tool — any MCP client can trigger an import). A Teams/Discord/web-upload adapter is ~30 lines against the same core — Slack is not load-bearing.
- **Deploy to Railway** ✅ config ready (`railway.json`, `Procfile`, `runtime.txt`, `requirements.txt`); ⬜ the browser steps + env vars are yours (see DEPLOY.md).
- **Next — IT/Slack approval kit:** minimal-permission manifest, a one-pager on data handling (connectors-off posture, read-only, nothing leaves the environment), and the export-based path for locked-down orgs.

> **Scales beyond Slack by design.** The three things that matter — the answer engine (`handle_query`/`execute_tool`), the ingest core (`src/ingest.py`), and the tool surface (`execute_tool`) — are all channel-neutral. Slack, MCP, and the CLI are adapters over the same code; adding Teams or a web UI means writing an adapter, not re-implementing the product.

---

## Phase 6 — MCP server: supercharge *any* AI with our system (capability multiplier) ✅ BUILT

The strategic capstone, and the concrete delivery of the original cross-platform vision ("transferable to Replit, Codex, Lovable, Gemini"). The 14 tools are now a **Model Context Protocol server** (`mcp_server.py`, built on the official MCP SDK / FastMCP, with `.mcp.json` for one-step client setup) so Claude Desktop, Cursor, Cline, Gemini, and any MCP client get the entire coordination engine — no rewrite, no per-platform port.

- **Portability:** MCP is the open standard built exactly for this; one server, every compatible AI surface.
- **Grounding for any model:** any connected LLM gets our ground truth (manifests, tickets, drift, decisions) instead of hallucinating org facts — the "supercharge AI back" half, generalized beyond our own bot.
- **Reuses everything:** the same `execute_tool` handlers back both the Slack agent and the MCP server. No new intelligence — a new doorway to the same brain.
- **AI-optional still holds:** MCP exposes the tools; the engines behind them remain pure code that works without any model.

---

## Phase 7 — Audience-aware experience: serve leadership, friendly for everyone ✅ BUILT (dashboard intentionally out of scope)

- **7.A Audience auto-routing** ✅ — `src/agent/audience.py`: per-user role (with per-channel defaults), set via `@syncbot I'm a designer` / `set my role to MD`. Every answer auto-frames by role — non-technical roles get de-jargoned output; the Claude agent gets an audience hint so it leads with the right things (health/risk for leadership, design language for designers). Default "ic" = no behavior change.
- **7.B Leadership rollup** ✅ — `src/agent/health.py` assesses each team 🟢/🟡/🔴 from signals we already compute, with top-3 plain-language risks, week-over-week trajectory, and who to talk to. No per-component noise. Surfaces: `how's <team> doing?`, `portfolio status`, plus `team_health`/`portfolio_status` as agent + MCP tools. Terminology configurable (`config.yaml → leadership`).
- **7.B (proactive) Weekly exec digest** ✅ — the scheduler posts the portfolio rollup to `leadership.exec_channel` every Monday alongside the team digests (skipped if unset).
- **7.C Plain language** ✅ — `src/agent/plain.py` de-jargons output for non-technical readers.
- **7.D Web dashboard** — intentionally **out of scope** (leadership is served via Slack + MCP).

---

### Original plan (for reference)

**The gap (honest):** the product speaks *dev dialect* — components, PRs, drift, tickets. ICs (designers/devs/PMs) tolerate or navigate it; **MDs and leadership bounce off it** because they want *rollups and trajectory* ("is Phoenix on track? where are the risks?"), not *lookups and mechanics*. We already hold all the data to answer leadership questions — what's missing is a **framing layer**. This phase adds that, and makes every answer match how each persona thinks.

### Persona × need matrix (what "friendly" means per role)

| Persona | What they ask | What they need back |
|---|---|---|
| **Designer** | "is my design in sync? what was decided? has this been built?" | design-system status, decisions, reuse, findability — *design language, not "PRs"* |
| **Dev** | "who owns X? what depends on this? what's drifting?" | ownership, dependencies, drift, tickets — technical is fine |
| **PM** | "when does X ship? what's blocked? what changed?" | delivery dates, cross-team blockers, action items — *outcome language* |
| **MD / Leadership** | "is the engagement on track? top risks? what slipped?" | **portfolio/team health, top 3 risks, week-over-week trajectory — zero per-component noise** |
| **New joiner** | "get me up to speed on team X" | synthesized onboarding (already built) |

### Part A — Audience model (cross-cutting foundation)
- A lightweight role signal: per-user or per-channel role (e.g. `#exec-*` channels default to leadership), overridable by `@syncbot I'm a designer`. Default to "IC" when unknown.
- One answer, rendered for the audience: jargon level, terseness, outcome-vs-mechanics. Reuses the existing `audience` param; extend it through the bot, agent, and digests.
- **Discipline:** the *data* is identical across audiences; only framing changes.

### Part B — Leadership rollup (the big unlock)
A deterministic **team health** read (AI-optional for crisp phrasing), built from signals we already compute:
- critical/high open issues, predicted conflicts, breaking changes without decision logs,
- deliverables overdue or due-soon-but-not-started (due date vs status), design/code drift count, stale manifests, cross-team blockers.

Roll into a status — 🟢 on-track / 🟡 at-risk / 🔴 blocked — with a one-line *why*, **top 3 risks in plain language**, **what changed since last week** (week-over-week deltas), and *who to talk to*.

Surfaces (all channel-neutral): `@syncbot how's Team X doing?`, `@syncbot portfolio status` (all teams, one screen), a separate **weekly exec digest** (own cadence/audience), and an `execute_tool`/MCP tool so any AI surface can pull it.

### Part C — Plain-language layer (friendly for everyone)
- A small translation map applied on render for non-technical audiences: *drift → inconsistency, PR → code change, component → feature/area, manifest → team profile*.
- Audit all bot copy for dev-isms; lead with outcomes, not mechanics.
- Keep the forgiving behaviors already built (fuzzy matching, "did you mean", graceful unknowns, threads).

### Part D — Read-only visual surface (bigger; stage last)
A simple web dashboard for leadership who won't open Slack threads — portfolio health board, risk heat, trajectory. Real work; honestly Phase 7.D / a Phase 8. Reuses the same engine + the MCP/`execute_tool` data, so it's a *view*, not new logic.

### Sequencing within Phase 7
```
A  Audience model ........ small; unblocks B and C
B  Leadership rollup ..... biggest unlock; framing over existing data (heuristic; AI-crisper)
C  Plain-language layer .. makes B and everything else friendly for non-technical roles
D  Web dashboard ......... optional, larger; a view over the same engine
```
Recommended: **B first** (the leadership rollup is the highest-value gap), with a thin slice of **A** to carry the audience flag, then **C** to de-jargon. **D** only if a non-Slack leadership surface is required.

### Definition of "friendly for everyone"
A designer, a PM, and an MD can each ask the system something in their own words and get an answer framed the way *they* think — the designer hears design language, the MD hears health and risk with no component-level noise — all from the same grounded data, with no dead-ends and no jargon they don't speak.

---

## Sequencing

```
Phase 1    Manifest Builder ........ unblocks every pilot (do first)        ✅
Phase 2    Living manifests ........ keeps trust over time                  ✅
Phase 3    Claude agent + UX ....... makes it feel intelligent              ✅
Phase 3.5  Structured outputs ...... AI supercharges the heuristics (parity)
Phase 4    Notification tuning ..... keeps the proactive value alive
           + Batch API ............. cheap scheduled AI at scale
Phase 5    Deploy + no-terminal .... makes it production-real                ◑
Phase 6    MCP server .............. supercharge any AI; portability unlock   ✅
Phase 7    Audience-aware .......... serve leadership; friendly for everyone  ✅ (no dashboard)
```

Phases 1–4, 6 done; 3.5 done; 5 = no-terminal done + Railway is yours. **Phase 7 is the next build** — the leadership rollup (7.B) is the biggest remaining value gap, with a thin audience flag (7.A) and the plain-language layer (7.C). All preserve the AI-optional + channel-neutral discipline.

---

## Definition of "pilot-ready"

A consultant can walk into an engagement with **any** mix of tools, point SyncBot at what exists, get a reviewable draft of the org's team map in minutes, correct it, and have the team asking questions in Slack the same day — with every answer traceable to a source and never going stale silently. The same engine is reachable from any MCP-compatible AI surface, and works fully with no AI at all.
