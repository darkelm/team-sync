# SECURITY.md — SyncBot data-flow & security posture

For an Accenture client IT / security review. This document states plainly what
data SyncBot holds, where it lives, and **what leaves the environment in each
operating mode**. Read the "Operating modes" table first — it is the answer to
the most common review question ("does our data go to a third-party LLM?").

> Status note: SyncBot currently runs as a **local process**, not a deployed
> service. There is no hosted backend holding client data; everything below
> describes a process running on an operator's machine (or, once deployed, a
> single Railway worker). Token rotation is **not yet enabled** — see
> [Token handling](#token-handling).

---

## TL;DR for reviewers

| Mode | Third-party LLM (Anthropic API)? | Live provider APIs (Slack/Atlassian/GitHub/Figma)? | What leaves the environment |
|---|---|---|---|
| **Keyword mode** (no `ANTHROPIC_API_KEY`) | **No** | Only those set to `live` in `config.yaml` | Calls to the providers you configure — nothing else |
| **AI mode** (`ANTHROPIC_API_KEY` set) | **Yes** | Only those set to `live` in `config.yaml` | The above **plus** the user's query and the retrieved coordination data (owners, tickets, decisions, drift) sent to the Anthropic API |
| **Connectors-off mode** (all providers `local`) | Only if AI mode is also on | **No** | Nothing, unless AI mode is on — works entirely off local exports |

The single most important fact: **in AI mode, queries and the coordination data
retrieved to answer them are sent to the Anthropic API.** This is not buried in a
log somewhere — it is the design. In keyword mode, no third-party LLM is involved
at all.

---

## What data lives where

SyncBot reads from a provider layer that can be pointed at either **local files**
or **live APIs**, per source, in `config.yaml`.

- **Local / synthetic data** — under `data/`:
  - `data/synthetic/` — the built-in synthetic org (5 teams, fake Jira/Confluence/GitHub/Figma). No real client data. Safe to ship and demo.
  - `data/imported/` — normalized JSON produced by the connectors-off import path from a client's own exports (Jira CSV, Confluence export, git clone, transcripts). **This can contain real client data** and stays on the local disk / deployed worker; it is never sent anywhere except (in AI mode) to the Anthropic API when it is retrieved to answer a query.
  - `data/exports/` — sample exports for testing.
  - Per-engagement state files (`data/notification_prefs.json`, `data/health_snapshots.json`, `data/audience_prefs.json`, `data/project_registry.json`) — local only, and gitignored where they may hold engagement-specific values.
- **Live provider data** — when a provider is set to `live`, SyncBot calls that provider's API at query time (e.g. Jira/Confluence on `*.atlassian.net`, the Slack API, GitHub, Figma) using credentials in `.env`. Data is fetched on demand; SyncBot is not a datastore for it.

There is **no SyncBot-operated cloud database**. Persistent data is the local
`data/` tree on whatever host runs the process.

---

## What leaves the environment, by mode

### Keyword mode (default when no Anthropic key is present)
- The bot answers via deterministic keyword routing and the pure-code engines.
- **No third-party LLM is contacted.** No query text and no retrieved data goes to Anthropic.
- The only outbound calls are to the providers you explicitly set to `live` in `config.yaml` (e.g. posting a reply via the Slack API, reading Jira). If every provider is `local`, nothing leaves at all.

### AI mode (when `ANTHROPIC_API_KEY` is set)
- The Slack bot / agent activates the Claude-powered conversational layer (`src/agent/syncbot.py`), plus AI-enhanced extraction in `src/agent/ai_enhance.py`.
- **What is sent to the Anthropic API:**
  - the user's natural-language **query**;
  - the **tool results** retrieved to answer it — i.e. real coordination data: team **owners** and contacts, **tickets** and delivery dates, **decisions** and rationale from decision logs, drift/conflict findings, and (for meeting/manifest enhancement) transcript and README text.
- The engines themselves run locally; AI is an enhancement layer on top. Tool execution happens on the host; only the *inputs and outputs* of that reasoning round-trip cross to the API.
- **Review implication:** if the client's data classification forbids sending owners/tickets/decisions to a third-party model, run in **keyword mode** (do not set `ANTHROPIC_API_KEY`). All core lookups, drift, conflict, digest, and briefing features still work — only the natural-language front-end and the AI quality-lift on extraction are lost.
- The Anthropic API is the only third-party LLM used. Default model id is `claude-opus-4-8` (overridable via `ANTHROPIC_MODEL`). No data is sent to any other model provider.

### Connectors-off mode (all providers `local`)
- SyncBot runs entirely off **local exports** the client can pull today (Jira CSV, Confluence export, git clone, transcripts), normalized into `data/imported/`.
- **No live provider API is required or contacted** — this is the path for orgs that have not (or will not) approve API connectors.
- Privacy posture: with all providers `local` **and** no Anthropic key, the process makes **no outbound network calls** for its core function. Adding the Anthropic key re-introduces the AI-mode egress described above; adding live providers re-introduces provider calls. The two switches are independent.

---

## Slack scopes (least-privilege)

The app requests a deliberately minimal bot scope set, defined in
[`slack/manifest.json`](slack/manifest.json). No write scopes beyond posting
messages, no admin scopes, no broad history scopes for public channels.

| Scope | Why it's needed |
|---|---|
| `app_mentions:read` | Receive `@syncbot …` mentions (the primary trigger). |
| `channels:read` | Resolve public channel names/ids to scope a query to the right project. |
| `chat:write` | Post answers and digests. |
| `chat:write.customize` | Post with the bot's display name/icon (branding only). |
| `files:read` | Read a file a user DMs the bot for the no-terminal data-import path. |
| `groups:read` | Resolve **private** channel names/ids (same project-scoping need as `channels:read`). |
| `im:history` | Read the DM thread so the bot can follow a direct conversation / file upload. |
| `im:read` | Detect and open DM channels. |
| `im:write` | Send DMs (e.g. import confirmations, role setup). |
| `mpim:read` | Resolve multi-person DM channels for scoping. |
| `users:read` | Resolve user ids to names/roles for audience framing and owner contacts. |

Notably **absent**: no `channels:history` / `groups:history` (the bot does not
vacuum channel history), no `users:read.email`, no admin or workspace-management
scopes. Event subscriptions are limited to `app_mention`, `message.im`, and
`member_joined_channel`. Interactivity is disabled; the app uses Socket Mode.

---

## Token handling

- **Secrets live in `.env`**, which is **gitignored** (see `.gitignore` line 1). `.env.example` documents the variable names with no values. No tokens are committed.
- Tokens used: Atlassian (`ATLASSIAN_*`), `GITHUB_TOKEN`, Slack (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`), `FIGMA_ACCESS_TOKEN`, `ANTHROPIC_API_KEY` — only the ones for providers set to `live` are needed.
- **Token rotation is currently OFF.** The Slack manifest sets `token_rotation_enabled: false` (see `slack/manifest.json`). This is acceptable for a local pilot but **not for a real client engagement.**
- **Recommended rotation plan before any real engagement:**
  1. Enable Slack token rotation (`token_rotation_enabled: true`) and implement refresh-token handling, or move to short-lived tokens.
  2. Move all secrets out of a flat `.env` into the host's secret manager (Railway/Render variables, or the client's vault) once deployed; do not bake tokens into images.
  3. Scope Atlassian/GitHub/Figma tokens to read-only, least-privilege service accounts; set expiries and a rotation cadence (e.g. 90 days).
  4. Rotate any token that has touched a developer laptop before go-live.

---

## Multi-tenant / project isolation

SyncBot supports parallel client engagements, each with its own config, providers,
and state (`src/projects.py`).

- **Enforced on the interactive Slack path.** Every incoming Slack message is mapped to a project via `ProjectRegistry.for_channel(channel_id, channel_name)`, and the query is answered using **only that project's** providers and data. A Google channel gets Google data; it cannot read another engagement's manifests.
- **Known item — MCP server and CLI are being hardened separately.** The MCP server (`mcp_server.py`) initializes a single global `Providers("config.yaml")` and is **not** project-scoped per call; the CLI answers against whatever config it is pointed at. In a multi-tenant deployment these two surfaces do not yet carry the same per-channel isolation the Slack path enforces. **Mitigation today:** run one MCP server / CLI context per engagement (separate config, separate process), rather than relying on in-process isolation. Bringing MCP/CLI to parity with the Slack path's isolation is a tracked hardening item.

---

## Summary for the reviewer

- No SyncBot-operated cloud datastore; client data stays in the local `data/` tree on the host that runs the process.
- Keyword mode contacts no third-party LLM. AI mode sends queries + retrieved coordination data to the Anthropic API — by design, and only when a key is set.
- Connectors-off mode needs no live provider API and (with no Anthropic key) makes no outbound calls for its core function.
- Slack scopes are least-privilege; no channel-history harvesting, no admin scopes.
- `.env` is gitignored; **token rotation is off and must be enabled, with secrets moved to a vault, before a real engagement.**
- Project isolation is enforced on the Slack path; MCP/CLI isolation is a known, separately-tracked hardening item — run one context per engagement until closed.
