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

## Deploying inside an enterprise that restricts MCPs / LLM egress

This document is the **data-flow** posture. Its companion,
**[`docs/deployment-compliance.md`](docs/deployment-compliance.md)**, is the
**approval-decision** brief for an enterprise (e.g. Accenture) IT review where
third-party MCPs/plugins are restricted and external LLM egress may be. It
separates the three risk decisions that usually get collapsed into one "no":

1. **Third-party vendor MCP / marketplace plugin** (the optional Atlassian Rovo MCP — highest scrutiny; **off by default**),
2. **team-sync as a self-hosted app** (your own app, scoped tokens + webhooks — a more-approvable posture),
3. **External LLM egress** (AI mode → Anthropic; a separate gate — if disallowed, **keyword mode** runs with zero external AI calls).

It also covers the keyword-only zero-egress feature set, a per-mode egress map, the
`SYNCBOT_PROVENANCE_PATH` durability note, and a short "what to confirm with
security" checklist. **Read it alongside this file** before a deployment review.

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

This is the operational runbook for every secret SyncBot uses: what each one is,
the blast radius if it leaks, how to rotate it, where it should live, and the
checklist to run before a client engagement. For *what data crosses the boundary*
once these credentials are in use, see [What leaves the environment, by mode](#what-leaves-the-environment-by-mode)
and the [TL;DR table](#tldr-for-reviewers) — this section is not duplicated there;
it covers credentials, not data flow.

### Secret inventory

All secret names come from [`.env.example`](.env.example). Only the secrets for
the providers you set to `live` in `config.yaml` are required; in keyword +
connectors-off mode the system can run with none of the provider tokens set.

There are two distinct credential families:

1. **Outbound provider credentials** — SyncBot uses these to *call* a provider
   API (read Jira, post to Slack, etc.). Held by `slack_bot.py` / the providers
   layer.
2. **Inbound webhook secrets** — used by `webhook_server.py` (a **separate
   process** from the Slack bot) to *verify* that an incoming push event really
   came from the provider. These authenticate the sender, they don't grant
   SyncBot any access; the risk if they leak is **spoofed inbound events**, not
   data exfiltration.

#### Outbound provider credentials

| Secret(s) | Provider | What it grants | Blast radius if leaked |
|---|---|---|---|
| `ATLASSIAN_API_TOKEN` (with `ATLASSIAN_URL`, `ATLASSIAN_EMAIL`) | Atlassian (Jira + Confluence) | API access **as that user**, across every Jira project and Confluence space the account can see — an Atlassian API token inherits the full account permissions; it is not scopable per-project. | Read (and, if the account can write, write/delete) of all client tickets, boards, and Confluence pages the account can reach. High — treat as account-equivalent. Use a dedicated least-privilege service account, never a person's. |
| `GITHUB_TOKEN` (with `GITHUB_ORG`) | GitHub | Repo/API access at whatever scopes the PAT (or fine-grained token / App installation token) was minted with. | Read of code, PRs, and metadata for every repo the token can reach; if write scopes were granted, code push / release / settings changes. Scope tightly — read-only, only the repos in scope. |
| `SLACK_BOT_TOKEN` (`xoxb-…`) | Slack | The bot's workspace access, bounded by the least-privilege scopes in [`slack/manifest.json`](slack/manifest.json) (post messages, resolve names, read DMs to the bot — **no** channel-history harvesting, **no** admin). See [Slack scopes](#slack-scopes-least-privilege). | Post as the bot and read what those scopes allow in the installed workspace. Bounded by scope, but still a foothold in the client workspace — rotate on any suspicion. |
| `SLACK_APP_TOKEN` (`xapp-…`) | Slack | Socket Mode connection token (`connections:write`) — opens the websocket the bot listens on. | Lets a holder open the event socket for the app. Rotate via the app's App-Level Tokens page. |
| `SLACK_SIGNING_SECRET` | Slack | Verifies that inbound Slack requests are genuinely from Slack (request signing). Not used in Socket Mode, but present for the HTTP path. | If leaked, an attacker could forge requests that look like they came from Slack on the HTTP event path. Rotate from the app's Basic Information page. |
| `FIGMA_ACCESS_TOKEN` | Figma | Personal access token — read access to files/projects the issuing account can see (used to read design-system libraries and team manifests). | Read of all Figma files the account can reach. Use a dedicated account scoped to only the relevant files; tokens are account-wide, so isolate the account. |
| `ANTHROPIC_API_KEY` (optional `ANTHROPIC_MODEL`) | Anthropic | Calls to the Claude API on the practice's billing account; **also the switch that turns on AI mode** (see [AI mode](#ai-mode-when-anthropic_api_key-is-set)). | Billable API usage on the key's account, and — separately — note that *setting* this key changes the data-egress posture (queries + retrieved coordination data go to Anthropic). Treat unsetting it as both a cost control and a data-classification control. |

#### Inbound webhook secrets (`webhook_server.py`)

| Secret | Set where | Verification mechanism | Blast radius if leaked |
|---|---|---|---|
| `GITHUB_WEBHOOK_SECRET` | GitHub App / repo webhook settings | HMAC-SHA256 over the raw request body | A holder could forge GitHub push events into SyncBot (spoofed drift/notification triggers). No outbound access granted. |
| `FIGMA_WEBHOOK_PASSCODE` | Figma webhook subscription (also `FIGMA_WEBHOOK_PASSCODE` for the C1 receiver) | `passcode` field in the JSON payload | Forged Figma push events. No outbound access granted. |
| `JIRA_WEBHOOK_TOKEN` | Jira automation webhook config | Shared-secret header `X-Webhook-Token` | Forged Jira events. No outbound access granted. |
| `WEBHOOK_SHARED_SECRET` | Calendar + generic webhook senders | Shared-secret header `X-Webhook-Token` | Forged calendar / generic events. No outbound access granted. |

> The webhook secrets authenticate *senders*. They do not give the holder any
> read access to client data — the worst case is spoofed inbound events, which
> should be treated as an integrity / nuisance risk, not a data-leak risk.

`FIGMA_LIBRARY_FILE_KEY`, `GITHUB_ORG`, `ATLASSIAN_URL`/`EMAIL`, the `*_PROVIDER`
overrides, and the data-path vars are **configuration, not secrets** — they are
not credentials and don't need rotation (though they may still be
engagement-specific and should stay out of the repo).

### Rotation plan

**Cadence (recommended):** rotate all live-provider credentials on a fixed
cadence — **90 days** as a default, shorter if the client's policy requires it —
and **immediately** on any of: a token touching a developer laptop before go-live,
a suspected exposure, or an offboarding of anyone who had access.

**How to rotate each, in the provider's UI:**

- **Atlassian (`ATLASSIAN_API_TOKEN`):** Atlassian account → *Security* →
  *API tokens* → create a new token, update `.env` / the secret store, then
  revoke the old token. Prefer a dedicated service account so rotation never
  disrupts a person's access.
- **GitHub (`GITHUB_TOKEN`):** *Settings* → *Developer settings* →
  *Personal access tokens* (use **fine-grained** tokens with an expiry, or a
  GitHub App installation token) → regenerate / mint new, update the store,
  revoke the old. Set an expiry so rotation is enforced, not optional.
- **Slack (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`):**
  in the Slack app config (api.slack.com/apps → the SyncBot app):
  - Bot token — *OAuth & Permissions* → reinstall / rotate.
  - App-level token — *Basic Information* → *App-Level Tokens*.
  - Signing secret — *Basic Information* → *App Credentials* → roll secret.
  - **Enable token rotation** (see below) so bot tokens become short-lived and
    refresh automatically rather than being long-lived static strings.
- **Figma (`FIGMA_ACCESS_TOKEN`):** Figma → *Settings* → *Security* →
  *Personal access tokens* → generate new, update the store, revoke the old.
- **Anthropic (`ANTHROPIC_API_KEY`):** Anthropic Console → *API Keys* →
  create a new key, update the store, then disable/delete the old key.
- **Webhook secrets (`GITHUB_WEBHOOK_SECRET`, `FIGMA_WEBHOOK_PASSCODE`,
  `JIRA_WEBHOOK_TOKEN`, `WEBHOOK_SHARED_SECRET`):** rotate the value in the
  provider's webhook config (locations in the inbound table above) **and** the
  matching value in SyncBot's secret store in the same change — they must stay in
  sync or events stop verifying.

**Slack token rotation is currently OFF.** The manifest sets
`token_rotation_enabled: false` ([`slack/manifest.json`](slack/manifest.json),
`settings` block). This is acceptable for a local pilot but **not for a real
client engagement** — leaving it off means the bot token is a long-lived static
credential. **Recommended:** set `token_rotation_enabled: true` and implement
refresh-token handling so bot tokens are short-lived and rotate automatically.

### Storage

- **Local / POC:** secrets in `.env` is **fine**. `.env` is **gitignored**
  (`.gitignore` line 1, confirmed) and `.env.example` ships only variable names,
  no values — no tokens are committed. This is the current state and is
  appropriate for a single operator's machine running the local process.
- **Real engagement:** do **not** rely on a flat `.env` and do **not** commit it.
  Use a proper secret store:
  - **Host environment injection** — Railway/Render service variables (or the
    equivalent on whatever host runs the single worker), so secrets are injected
    at runtime and never written to disk in the repo.
  - **A vault** — the client's secrets manager (e.g. HashiCorp Vault, AWS/GCP/Azure
    secret manager) where the client's policy requires centralized control,
    auditing, and rotation hooks.
  - **Never bake tokens into container images** or build artifacts.
- **Least privilege per token:** scope every credential to the minimum it needs
  — dedicated read-only service accounts for Atlassian/GitHub/Figma (never a
  person's personal token), the minimal Slack scope set already defined in the
  manifest, and a per-engagement Anthropic key if cost/usage isolation matters.
  Tokens that can only read can't be used to write or delete if they leak.

### Before a client engagement — checklist

- [ ] **Rotate every token** off any shared, demo, or dev value. Assume anything
      that has touched a laptop or a pilot is burned; mint fresh credentials for
      the engagement.
- [ ] **Use dedicated least-privilege service accounts** for Atlassian, GitHub,
      and Figma (read-only where possible) — not personal accounts. Confirm the
      Slack scopes still match the least-privilege set in the manifest.
- [ ] **Enable Slack token rotation** (`token_rotation_enabled: true`) and verify
      refresh handling works end-to-end.
- [ ] **Move secrets into the host secret store or the client's vault**; confirm
      `.env` is not committed and is not present in any image or artifact.
- [ ] **Set expiries and a rotation cadence** (90 days or per client policy) for
      every credential that supports one.
- [ ] **Confirm webhook secrets are unique per engagement** and synced between
      each provider's webhook config and SyncBot's store.
- [ ] **Document the data-egress posture for the chosen operating mode** — i.e.
      whether `ANTHROPIC_API_KEY` will be set (AI mode) and which providers are
      `live` — per [What leaves the environment, by mode](#what-leaves-the-environment-by-mode),
      and get it signed off against the client's data classification.

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
