# Adoption Plan — removing the barriers to a real pilot

The product capability is ahead of the adoption ergonomics. The path from "install" to "first value" still runs through hand-written manifests and a terminal — that's where a pilot stalls. This plan fixes all five barriers, in priority order.

**Consultancy constraint (drives the whole design):** every engagement has a *different* mix of tools. Sometimes there's no Jira. Sometimes only a repo and a Confluence space. Sometimes only meeting recordings and a roster spreadsheet. So nothing can depend on a single source — the system must **fuse whatever is available, degrade gracefully, and show its work**.

---

## The five barriers (ranked)

| # | Barrier | Severity |
|---|---|---|
| 1 | Manifest authoring (cold-start cost) | 🔴 Critical |
| 2 | Data + manifest staleness | 🔴 Critical |
| 3 | Brittle NLU + command discoverability | 🟠 High |
| 4 | Notification fatigue | 🟠 High |
| 5 | Hosting + IT/Slack approval | 🟡 Medium |

---

## Phase 1 — Multi-Source Manifest Builder (kills barrier #1)

**Goal:** turn "hand-write a YAML file" into "point me at whatever you have, I'll draft it, you correct it."

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

## Phase 2 — Living manifests (kills barrier #2, staleness)

- **Scheduled re-sync:** re-run available adapters nightly/weekly; **diff against the current manifest**; propose updates ("3 new components in `src/`; owner of `billing` shifted based on recent commits").
- **Manifest drift detection:** flag manifests that diverge from reality (repo has a component the manifest doesn't; a listed owner has no recent activity).
- **Freshness metadata:** every manifest records `last_verified`; answers state their age ("as of last sync 2 days ago").

---

## Phase 3 — Understanding + discoverability (kills barrier #3)

- **Claude agent** (needs key): replaces keyword matching so any phrasing works; also upgrades transcript extraction and semantic duplicate detection. Single highest-leverage upgrade.
- **Graceful unknowns:** never a dead end. "I don't have that yet — based on the manifests, the closest owner is X. Want me to flag it for the data owner?"
- **Bot self-introduction:** when invited to a channel, posts a short "here's what I can do" with example questions. Solves discovery.
- **Provenance in answers:** every answer can cite its source + freshness, building trust from the first interaction.

---

## Phase 4 — Notification tuning (kills barrier #4, fatigue)

- **Per-team digest preferences:** cadence, sections, and a **severity threshold** ("only ping me on high/critical").
- **Suppress empty/noisy sections** (partly done) so digests are signal-dense.
- **Snooze / opt-out / @-only-on-critical** controls via a Slack command.
- **Quality gate:** a digest only sends if it has something genuinely new since last time.

---

## Phase 5 — Make it real (kills barrier #5)

- **Deploy to Railway** (config ready) so it runs 24/7 independent of a laptop.
- **No-terminal setup:** let the setup person drag an export into Slack (`@syncbot import` with an attachment) or a tiny upload page — so even the 1% never needs a terminal.
- **IT/Slack approval kit:** minimal-permission Slack manifest, a one-pager on data handling (connectors-off posture, read-only, no data leaves the environment), and the export-based path for locked-down orgs.

---

## Sequencing

```
Phase 1  Manifest Builder ........ unblocks every pilot (do first)
Phase 2  Living manifests ........ keeps trust over time
Phase 3  Claude agent + UX ....... makes it feel intelligent
Phase 4  Notification tuning ..... keeps the proactive value alive
Phase 5  Deploy + no-terminal .... makes it production-real
```

Phase 1 is the gate — without low-friction manifests, nothing else gets used. Everything in Phase 1 reuses the importers and providers already built.

---

## Definition of "pilot-ready"

A consultant can walk into an engagement with **any** mix of tools, point SyncBot at what exists, get a reviewable draft of the org's team map in minutes, correct it, and have the team asking questions in Slack the same day — with every answer traceable to a source and never going stale silently.
