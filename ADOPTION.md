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

## Phase 3.5 — Structured outputs: make the AI-enhanced heuristics reliable (capability multiplier) ◑ STARTED

Uses Anthropic **structured outputs** (`messages.parse()` with Pydantic) so Claude returns the **exact same schema** the heuristics produce — a drop-in quality lift, not a parallel codepath. The pattern is established in `src/agent/ai_enhance.py`: AI selected only when a key is present, schema-identical results, automatic fallback to the heuristic on any failure.

- **Meeting extraction** ✅ — `MeetingAnalyzer.analyze` now prefers AI (clean decisions-vs-discussion, owners resolved, due dates) returning the same `DecisionLog` / `ActionItem` objects; falls back to the regex extractor with no key or on error. The summary records which path ran (`via ai` / `via heuristic`).
- **Semantic reuse / duplicate detection** — next, same pattern: real meaning ("alert badge" ≈ "notification bell") returning the same `ReuseMatch` shape as the Jaccard version.
- **Manifest inference** — next: AI proposes fields as `Candidate` objects with confidence, fused exactly like the deterministic adapters.
- **Citations** (optional add-on): cite Confluence source spans — reinforces provenance/trust.
- **Discipline upheld:** each keeps its heuristic implementation; structured-output AI is selected only when a key is present. Schema parity is the rule.

---

## Phase 4 — Notification tuning (kills barrier #4, fatigue)

- **Per-team digest preferences:** cadence, sections, and a **severity threshold** ("only ping me on high/critical").
- **Suppress empty/noisy sections** (partly done) so digests are signal-dense.
- **Snooze / opt-out / @-only-on-critical** controls via a Slack command.
- **Quality gate:** a digest only sends if it has something genuinely new since last time.
- **Batch API (capability multiplier):** run the scheduled/bulk AI work — many-team digests, nightly drift summaries, bulk transcript processing — through Anthropic's Batch API at 50% cost. Only matters once digests are AI-written and running at scale; pure cost optimization for the proactive layer.

---

## Phase 5 — Make it real (kills barrier #5)

- **Deploy to Railway** (config ready) so it runs 24/7 independent of a laptop.
- **No-terminal setup:** let the setup person drag an export into Slack (`@syncbot import` with an attachment) or a tiny upload page — so even the 1% never needs a terminal.
- **IT/Slack approval kit:** minimal-permission Slack manifest, a one-pager on data handling (connectors-off posture, read-only, no data leaves the environment), and the export-based path for locked-down orgs.

---

## Phase 6 — MCP server: supercharge *any* AI with our system (capability multiplier) ✅ BUILT

The strategic capstone, and the concrete delivery of the original cross-platform vision ("transferable to Replit, Codex, Lovable, Gemini"). The 14 tools are now a **Model Context Protocol server** (`mcp_server.py`, built on the official MCP SDK / FastMCP, with `.mcp.json` for one-step client setup) so Claude Desktop, Cursor, Cline, Gemini, and any MCP client get the entire coordination engine — no rewrite, no per-platform port.

- **Portability:** MCP is the open standard built exactly for this; one server, every compatible AI surface.
- **Grounding for any model:** any connected LLM gets our ground truth (manifests, tickets, drift, decisions) instead of hallucinating org facts — the "supercharge AI back" half, generalized beyond our own bot.
- **Reuses everything:** the same `execute_tool` handlers back both the Slack agent and the MCP server. No new intelligence — a new doorway to the same brain.
- **AI-optional still holds:** MCP exposes the tools; the engines behind them remain pure code that works without any model.

---

## Sequencing

```
Phase 1    Manifest Builder ........ unblocks every pilot (do first)        ✅
Phase 2    Living manifests ........ keeps trust over time                  ✅
Phase 3    Claude agent + UX ....... makes it feel intelligent              ✅
Phase 3.5  Structured outputs ...... AI supercharges the heuristics (parity)
Phase 4    Notification tuning ..... keeps the proactive value alive
           + Batch API ............. cheap scheduled AI at scale
Phase 5    Deploy + no-terminal .... makes it production-real
Phase 6    MCP server .............. supercharge any AI; the portability unlock
```

Adoption phases (1, 2, 4, 5) and capability multipliers (3, 3.5, 6) interleave. Phases 1–3 are done. Recommended next: **Phase 6 (MCP)** for the strategic unlock, or **Phase 3.5 (structured outputs)** to make AI-enhanced extraction reliable — both preserve AI-optional discipline.

---

## Definition of "pilot-ready"

A consultant can walk into an engagement with **any** mix of tools, point SyncBot at what exists, get a reviewable draft of the org's team map in minutes, correct it, and have the team asking questions in Slack the same day — with every answer traceable to a source and never going stale silently. The same engine is reachable from any MCP-compatible AI surface, and works fully with no AI at all.
