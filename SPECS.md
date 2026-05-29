# Implementation Specs — remaining work (everything except the web dashboard)

Detailed, pick-up-and-build specs for what's left. Each spec: **Goal · Why · Approach · Files · Interfaces · Acceptance criteria · Effort · Dependencies**. Discipline that applies to all of them: **AI-optional** (works without a key), **channel-neutral** (logic behind `execute_tool` / providers, not baked into Slack), **schema-parity** (AI returns the same shapes as heuristics).

Effort key: S = <½ day · M = 1–2 days · L = 3–5 days · XL = >1 week.

---

# A. Operational (run it for real — mostly config/runbook)

## A1. Enable the Claude agent (API key) — S
- **Goal:** flip the live bot + AI-enhanced extraction from keyword/heuristic into natural-language mode.
- **Approach:** add `ANTHROPIC_API_KEY` (and optional `ANTHROPIC_MODEL`) to `.env`; restart `slack_bot.py`. No code change — `slack_bot.AGENT` auto-initializes, `ai_enhance.ai_available()` flips true.
- **Acceptance:** startup log reads `Understanding: Claude agent`; `ai_enhance.extract_meeting` runs on transcript import; a deliberately off-script question (e.g. "which squad builds the login screen?") returns a correct answer instead of the help fallback.
- **Dependencies:** an Anthropic key.

## A2. Deploy to Railway (24/7) — S/M
- **Goal:** the bot + scheduler run independent of a laptop.
- **Approach:** Railway → New Project → Deploy from `darkelm/team-sync`. It reads `Procfile` (`worker: python slack_bot.py`), `runtime.txt` (3.11.9), `requirements.txt`. Set env vars in the Railway dashboard (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`, provider modes, optional `ANTHROPIC_API_KEY`, Atlassian creds). Socket Mode = no public URL needed.
- **Files:** none new (config exists); optionally add a `railway.json` with restart policy.
- **Acceptance:** bot stays online after closing your laptop; Monday digest fires on schedule; `@syncbot status` responds.
- **Note:** the **MCP server is a separate process** (`mcp_server.py`) — runs wherever the MCP client lives, usually locally via `.mcp.json`; it is not the Railway worker.

## A3. Slack app re-install for `files:read` — S
- **Goal:** enable the drag-a-file-into-Slack import.
- **Approach:** the scope is already in `slack/manifest.json`; in `api.slack.com/apps` → your app → **Reinstall to Workspace** to grant it.
- **Acceptance:** DM the bot a Jira CSV with "import for Team Phoenix" → it imports (no `files:read` error in logs).

## A4. Token rotation + secrets hygiene — S
- **Goal:** revoke credentials exposed in chat history; establish a rotation path.
- **Approach:** regenerate Atlassian API token (`id.atlassian.com/manage-profile/security/api-tokens`) and Slack tokens (`api.slack.com/apps` → your app); update `.env` / Railway env. Confirm `.env` is gitignored (it is). Document a quarterly rotation reminder.
- **Acceptance:** old tokens revoked; bot still connects with new ones; `git log -p` shows no secrets ever committed.

## A5. Exec digest channel — S
- **Goal:** turn on the Monday leadership rollup post.
- **Approach:** set `leadership.exec_channel` in `config.yaml` to a real channel; invite the bot there.
- **Acceptance:** on the digest cron, `scheduler._run_exec_digest` posts `StrategyLens`/`HealthAssessor` portfolio rollup to that channel.

---

# B. Real-data wiring (beyond synthetic)

## B1. Figma live provider — L (the real meat)
- **Goal:** replace the `LiveFigmaProvider` stub (returns `[]`) with a real implementation so design drift/consistency runs on actual Figma data.
- **Why:** design is the differentiator; today only the local synthetic Figma data exercises it.
- **Approach (Figma REST API, token in `FIGMA_ACCESS_TOKEN`):**
  - `get_library_components()` → `GET /v1/files/{library_file_key}/components` for the file in `design_system_library`; map each to `FigmaComponent(is_library_component=True)`. Use `/v1/files/{key}/component_sets` for variants.
  - `get_components(team)` → for each team's `figma_files`, `GET /v1/files/{key}/components`; map to `FigmaComponent` with `team`, `file_id`, `file_name`, `last_modified`.
  - `get_components_by_name(name)` → filter the above by name (case-insensitive).
  - `get_drift_issues()` → the hard part. Heuristics, best-effort, documented as such:
    1. **Detached/unlinked:** a team file has a component whose name matches a library component but its `key`/`component_set_id` is not the library's (i.e. a local copy, not an instance of the published component) → `diverges_from_library=True`.
    2. **Stale:** team file's component `updated_at` predates the library component's `updated_at` → out of sync.
    3. **Naming-only match with no library link** → custom implementation.
  - `used_by_teams` derived from manifests: which teams reference the component in `components.design` or `figma_files`.
- **Files:** rewrite `src/providers/live/figma.py`; add response caching (Figma rate limits ~ per-minute). Add `FIGMA_LIBRARY_FILE_KEY` to `.env.example` or derive from a team's `design_system_library`.
- **Interfaces:** must satisfy the `FigmaProvider` ABC unchanged (so `figma: live` in `config.yaml` just works).
- **Acceptance:** with `FIGMA_PROVIDER=live` and a real token, `design_sync_status` and journey inconsistencies populate from live Figma; drift heuristics flag at least detached + stale; no crash on rate limit (graceful `[]` + log, like the Jira/Confluence pattern).
- **Risk/honesty:** Figma's API exposes *published components* and file structure but not a first-class "this instance diverged from the main component" flag — drift is inferred. Document the heuristics and their false-positive/negative profile.

## B2. GitHub live provider — exercise end-to-end — M
- **Goal:** verify/finish `LiveGitHubProvider` against a real org.
- **Approach:** with `GITHUB_TOKEN` + `GITHUB_ORG`, test `get_recent_prs`, `get_pull_requests`, `get_prs_touching_component`. Map `components_touched` by matching changed file paths against each team manifest's `components.code[].path` (the data is there; wire the mapping in the live provider as the local one does). Handle pagination + rate limits (graceful fallback).
- **Files:** `src/providers/live/github.py` (component-path mapping + pagination).
- **Acceptance:** `github: live`, real org → `scan_conflicts` and `code.merged` events fire from real merges; component mapping resolves at least exact path-prefix matches.

## B3. Real team manifests onboarding — M
- **Goal:** stand up an actual engagement's team map (vs. the synthetic 5).
- **Approach:** run `syncbot build-manifest <repo> <roster.csv> [transcript]` per team → review the provenance-annotated draft → fill TODOs → `syncbot validate`. Point `config.yaml → data.teams_dir` at a new dir (e.g. `data/<client>/teams`) so synthetic and real stay separate. Add org files (`journeys.yaml`, `experience_principles.yaml`, `org_objectives.yaml`) for the engagement.
- **Acceptance:** `validate` passes on the real manifests; `portfolio status` reflects the real org.

---

# C. Capability gaps from the vision

## C1. Trigger "ears" — webhook receiver + nightly snapshot diff — L
- **Goal:** make the (already-built) `EventRouter` fire on its own, from real signals — not just `simulate-event`.
- **Why:** "it watches and warns you" is only real once something feeds events automatically.
- **Approach — two adapters, both just emit `Event` and call `EventRouter.dispatch`:**
  - **Webhook receiver** (`webhook_server.py`, FastAPI or Flask, separate process/Railway service):
    - `POST /webhooks/figma` — verify Figma webhook passcode; on `LIBRARY_PUBLISH` → `Event("design.library_published", subject=<component/file>, source="figma", team=<owner>)`.
    - `POST /webhooks/github` — verify `X-Hub-Signature-256` (HMAC, `GITHUB_WEBHOOK_SECRET`); on `pull_request.closed&merged` → `Event("code.merged", subject=<component>, source="github", team=<repo→team>)`.
    - `POST /webhooks/jira` — Jira automation webhook; on issue created → `Event("work.created", …)`; on duedate change → `Event("roadmap.date_changed", …)`.
    - `POST /webhooks/calendar` — Google/Outlook push (or a Zapier relay); on event titled like a cross-team sync → `Event("calendar.cross_team_sync", metadata={teams, channel})`.
    - `POST /webhooks/generic` — signed JSON `{type, subject, team, metadata}` for anything else (Dovetail, Productboard, Notion) via Zapier/Make.
    - Each handler: verify signature → normalize → `router.dispatch(event)` → `200`.
  - **Nightly snapshot diff** (`snapshot_scan.py`, cron/APScheduler job): re-run available imports into a temp dir, diff against current data (new/removed components, changed due dates, new tickets), emit the corresponding events. This is the connectors-off way to get proactivity without webhooks.
- **Files:** `webhook_server.py`, `snapshot_scan.py`; add `GITHUB_WEBHOOK_SECRET`, `FIGMA_WEBHOOK_PASSCODE` to `.env.example`; signature-verification utils.
- **Interfaces:** reuse `EventRouter` unchanged; map repo→team and file→team via manifests.
- **Acceptance:** a real merged PR / published Figma library posts the right Slack notifications without anyone asking; bad signatures rejected with `401`; replayed deliveries deduped by delivery id.
- **Security:** HTTPS only; verify every signature; never log payloads with secrets; rate-limit.

## C2. Outcomes + Research insights as first-class objects — M
- **Goal:** finish the Strategy & Experience layer (Journeys + Principles already done).
- **Approach (mirror the Journey/Principle pattern exactly):**
  - **Schemas** in `src/core/schemas.py`:
    - `Outcome(id, name, metric, target, owner, related_objectives: list[str], related_journeys: list[str])`
    - `ResearchInsight(id, title, summary, source, date, themes: list[str], journeys: list[str], teams: list[str], url)`
  - **Data:** `data/synthetic/outcomes.yaml`, `data/synthetic/research_insights.yaml` (org-level, like `journeys.yaml`).
  - **Engine** in `src/agent/strategy.py` (extend `StrategyLens`):
    - `outcomes()` / `assess_outcome(name)` — ladder goals→outcomes (deeper than today's keyword objective match); flag outcomes with no owning team or no supporting work.
    - `insights_for(topic|journey)` — surface relevant research; `contradictions()` — flag insights with opposing findings on the same theme (semantic/keyword).
    - Wire into Reuse Radar so "has anyone researched X?" hits insights, and into journeys (a journey shows the insights that inform it).
  - **Surfaces:** bot ("what's the research on onboarding?", "are we hitting our outcomes?"), `execute_tool` tools `outcome_status` + `research_insights`, matching MCP tools.
- **Acceptance:** journeys display informing insights; `outcome_status` shows metric/target/owner + whether work ladders to it; contradictory insights are flagged. AI-optional (heuristic match; structured-output upgrade later).
- **Effort:** M.

## C3. Native surfaces (Figma plugin · GitHub PR check · Teams adapter) — each M–L
All three reuse the channel-neutral core (`handle_query`/`execute_tool`/`EventRouter`) via a thin adapter + a small HTTP gateway.

- **C3a. Figma plugin panel — L**
  - **Goal:** designers see "who else uses this component / is it in sync / who owns it" inside Figma.
  - **Approach:** a Figma plugin (`manifest.json`, `code.ts`, `ui.html`). On selecting a component, the plugin calls a small **HTTP gateway** (`api_server.py`, FastAPI) exposing `POST /tool {name, inputs}` → `execute_tool`. Panel renders `design_sync_status` + `who_owns` + `reuse_radar` for the selection.
  - **Files:** `figma-plugin/` (TS plugin), `api_server.py` (auth via a static token).
  - **Acceptance:** selecting a drifted component in Figma shows the divergence + canonical owner.
- **C3b. GitHub PR check — M**
  - **Goal:** cross-team impact surfaced *in the PR*, before merge.
  - **Approach:** a GitHub Action (`.github/workflows/syncbot-impact.yml`) that on PR calls the gateway's `scan_conflicts`/dependency logic for the touched components and posts a PR comment + a neutral check run.
  - **Acceptance:** a PR touching a shared component gets an auto-comment naming the dependent teams.
- **C3c. Teams adapter — L**
  - **Goal:** the bot in Microsoft Teams.
  - **Approach:** an Azure Bot Service / Bot Framework app whose message handler calls `answer(text, role)` — the same brain as Slack. Needs a public messaging endpoint (no Socket Mode equivalent), an app manifest, and admin consent. Outbound digests/alerts via Teams Incoming Webhooks or Graph.
  - **Acceptance:** `@SyncBot portfolio status` works in a Teams channel with identical answers to Slack.
  - **Note:** heavier than Slack purely due to Azure/admin gatekeeping; the answer logic is reused as-is.

## C4. AI citations (provenance in answers) — M
- **Goal:** doc-sourced answers cite the exact source span ("here's the line it came from").
- **Approach:** in the agent path, when answering from Confluence/decision content, pass the source text as Anthropic **document blocks with `citations: {enabled: true}`** and surface returned citations. Heuristic path appends a source URL. Strengthens the trust story; reinforces "grounded, not guessed."
- **Files:** `src/agent/syncbot.py` (document blocks for `find_decision` context), render citations in the reply.
- **Acceptance:** "what was decided about v3 tokens?" returns the decision **with a citation/link** to the source record.
- **Dependencies:** Anthropic key.

---

# D. Productization / hardening

## D1. Test suite — M (highest credibility-per-effort)
- **Goal:** lock in correctness; safe to keep extending.
- **Approach:** pytest under `tests/`, fixtures load the synthetic org (`Providers("config.yaml")` with local providers). Cover:
  - `importers/` — Jira CSV (repeated columns, status/priority normalization, date formats), Confluence MD/HTML, transcript parser, github clone (mock `git`).
  - `core/dependency_graph` — dependents, shared components, orphans.
  - `agent/detector` — drift/conflict/missing-decision counts on the synthetic org (golden values).
  - `agent/discovery, alignment, findability, health, strategy, events, fuzzy` — known outputs (e.g. Horizon↔Forge unlinked; portfolio counts; design.library_published routes to consumers; "authh"→auth).
  - `builder` + `refresher` — fusion precedence (roster beats git), drift diff.
  - `ingest` — path + bytes round-trip; detection.
  - AI paths: mock the Anthropic client; assert schema-parity (AI result is the same type as heuristic) and graceful fallback on exception.
- **Files:** `tests/test_*.py`, `tests/conftest.py`; `.github/workflows/ci.yml` (pytest + ruff on push).
- **Acceptance:** `pytest` green; CI runs on push; ≥70% coverage on `src/`.

## D2. Package as a Claude Code skill/plugin — M (closes the original ask)
- **Goal:** deliver what you first asked for — an installable **skill/plugin**, not just a runnable app.
- **Approach:**
  - **Plugin** (`.claude-plugin/plugin.json`): name, version, description; bundle the MCP server via `.mcp.json` (already present) so installing the plugin wires all 20 tools; declare commands.
  - **Skill** (`skills/team-sync/SKILL.md`): when-to-use description + instructions so Claude knows to reach for SyncBot tools for coordination questions; reference the MCP tools.
  - **Commands** (`commands/`): e.g. `/portfolio`, `/journey`, `/whoowns` as slash commands mapping to tools.
  - **Marketplace**: a `marketplace.json` so it's installable via `/plugin marketplace add darkelm/team-sync`.
- **Files:** `.claude-plugin/plugin.json`, `skills/team-sync/SKILL.md`, `commands/*.md`, `marketplace.json`.
- **Acceptance:** `claude` user can install the plugin from the repo and immediately use the tools/commands; `plugin-validator` passes.
- **Dependencies:** the MCP server (done).

## D3. Enterprise-readiness — XL (only when commercializing)
- **Goal:** the POC→product gap.
- **Scope (checklist, each its own effort):**
  - **AuthN/Z:** SSO (SAML/OIDC) for any web/gateway surface; per-team RBAC on who can mute digests / set roles / import.
  - **Audit logging:** every write (imports, prefs, role changes, dispatched notifications) to an append-only log.
  - **Data residency & retention:** configurable storage location; retention/redaction for transcripts and decisions; PII handling policy (transcripts especially).
  - **Secrets:** move from `.env` to a secrets manager (Vault/AWS/GCP); rotation.
  - **Tenancy:** namespacing so one deployment can serve multiple orgs/engagements without data bleed.
  - **Compliance:** SOC 2 control mapping; DPA; pen-test.
- **Acceptance:** security review checklist passes; documented data-handling one-pager (the IT/Slack approval kit referenced in ADOPTION Phase 5).

## D4. IT / Slack approval kit — S/M
- **Goal:** ease enterprise rollout conversations.
- **Approach:** a one-pager: connectors-off posture (works off exports), read-only by default, no data leaves the environment, minimal Slack scopes (list them from `slack/manifest.json`), Socket Mode (no inbound ports). Plus a "minimum access to go live" matrix.
- **Acceptance:** a security reviewer can approve from the doc without a meeting.

---

# E. Deferred-but-specced (not the dashboard)

## E1. Batch API for scheduled/bulk AI — M
- **Goal:** 50% cost on non-interactive AI work once digests/extraction are AI-written at scale.
- **Approach:** route the Monday many-team digest generation, nightly drift summaries, and bulk transcript backfills through Anthropic's **Message Batches API** (`client.messages.batches.create`) instead of per-item `messages.create`. Poll for completion in the scheduler; fall back to per-item on failure.
- **Files:** `src/agent/batch.py`; call it from `scheduler._run_digests` when `ai_available()` and team count is large.
- **Acceptance:** weekly AI digests for N teams run as one batch at ~50% cost; identical output to per-item.
- **Dependencies:** Anthropic key; only worthwhile once digests are AI-written.

---

## Suggested order (value per effort)
1. **A1 + A2** (key + deploy) — unlocks everything already built. *(S)*
2. **D1 tests** — credibility + safety to extend. *(M)*
3. **C1 trigger ears** — makes proactivity real. *(L)*
4. **B1 Figma live** — proves the design differentiator on real data. *(L)*
5. **D2 package as plugin** — closes the original ask. *(M)*
6. **C2 outcomes + insights** — completes the strategy layer. *(M)*
7. Everything else as the engagement demands.
