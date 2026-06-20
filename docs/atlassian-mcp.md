# Atlassian (Rovo) Remote MCP Server — operator guide

Status: **OPTIONAL · AI-mode-only · OFF by default.** This is a scaffolded seam, not
a live integration. With no configuration, it is a complete no-op and nothing about
SyncBot's current behavior changes.

## What it is

Atlassian's **Rovo Remote MCP Server** (GA Feb 2026) exposes **72+ tools** across
**Jira, Confluence, and Compass** over **OAuth 2.1**, honoring the connected
account's permissions. It supports **read and write** — creating/updating issues and
pages, linking them, and running bulk natural-language workflows. [V] verified
against Atlassian's announcement and Anthropic's MCP-connector docs.

An MCP server is consumed by an MCP-capable **agent**. In team-sync that agent is
**SyncBot** (model `claude-opus-4-8`), which is active only when `ANTHROPIC_API_KEY`
is set. So this integration lives in `src/agent/syncbot.py`, **not** in the
deterministic provider layer.

## Division of labor — REST providers vs MCP

| | REST providers (`src/providers/live/jira.py`, `confluence.py`) | Atlassian Rovo MCP (this) |
|---|---|---|
| Role | **Deterministic backbone** — always on | **AI-mode supercharger** — opt-in |
| Driven by | Code (`Providers`), explicit calls | The Claude agent, natural language |
| Auth | API token (`ATLASSIAN_API_TOKEN`) | OAuth 2.1 (`ATLASSIAN_MCP_TOKEN`) |
| Surface | Curated read endpoints we wrote | 72+ Atlassian tools, read **and write** |
| When | Digests, drift scans, briefings — every run | Only when AI mode is on **and** configured |

The MCP does **not** replace the REST providers. They remain the stable, predictable
data path for everything the app does deterministically. The MCP adds natural-language
querying and (gated) write actions on top, only when explicitly enabled.

## Writes go through the governance membrane — this is not a bypass

**Principle:** any agent-**proposed** Atlassian write (create / update / link /
transition) MUST route through the governance membrane's **`review` lane**
(`Lane.REVIEW` in `src/agent/membrane.py`) for human sign-off **before** it fires.
The MCP connector is a tool source, not an end-run around governance.

This is **not implemented yet** — the write-approval loop is future work. What exists
today is the **seam**:

- `SyncBot.atlassian_mcp_registration()` returns a `toolset` descriptor. That toolset
  is the chokepoint where Atlassian **write** tools should later be **denylisted at
  the connector** (`configs: {<write_tool>: {enabled: false}}`, the read-only pattern
  from Anthropic's docs) and/or wrapped so a proposed write becomes a `RouteItem`
  routed to `Lane.REVIEW` instead of being called directly by the model.
- Until then, the descriptor is built and tested but **not attached** to any live
  request, so no write can fire.

Recommended initial posture when this is switched on: **read-only** (denylist every
write tool at the connector) until the membrane write-gate is wired.

## How to enable

1. **AI mode must be on.** Set `ANTHROPIC_API_KEY` (this is what makes SyncBot the
   active responder instead of keyword matching). Without it the integration stays
   off regardless of MCP config.

2. **Provision a least-privilege Atlassian service account.** The MCP respects the
   connected account's permissions, so **the bot's reach == that account's reach.**
   Use a dedicated service account scoped to only the projects/spaces it needs — not a
   human admin account. This is the single most important control: it bounds blast
   radius at the identity layer.

3. **Complete the OAuth 2.1 flow** for that account against the Rovo MCP endpoint and
   obtain an access token. team-sync does **not** run the OAuth flow — you supply an
   already-obtained token and are responsible for refreshing it. (For testing, the
   `npx @modelcontextprotocol/inspector` "Quick OAuth Flow" can mint a token.)

4. **Provide the URL + token.** Either via environment (preferred — env wins):

   ```bash
   ATLASSIAN_MCP_URL=https://mcp.atlassian.com/v1/sse     # the Rovo MCP endpoint
   ATLASSIAN_MCP_TOKEN=<oauth-2.1-access-token>
   ```

   …or a `config.yaml` block for the URL (the token is **always** read from an env
   var named by `token_env`, never stored in the file):

   ```yaml
   atlassian_mcp:
     url: https://mcp.atlassian.com/v1/sse
     token_env: ATLASSIAN_MCP_TOKEN   # env var that holds the OAuth token
   ```

"Enabled" requires **both** a URL and a resolvable token **and** AI mode on. Anything
less ⇒ off.

## How it's wired in code

- `atlassian_mcp_config()` — returns `{url, token, name}` when fully specified, else
  `None`. Reads secrets only from env.
- `atlassian_mcp_enabled()` — `True` only when AI mode is on **and** config is present.
  This is the one switch every wire-in point checks.
- `SyncBot.atlassian_mcp_registration()` — returns the request-ready descriptor
  (`beta`, `mcp_server`, `toolset`) using the **verified** Messages-API MCP-connector
  shape (beta header `mcp-client-2025-11-20`), or `None` when disabled.
- `SyncBot.ask()` — contains the single, clearly-marked **TODO wire-in point**. The
  current loop uses the stable `client.messages.create(...)`, which does not accept
  `mcp_servers`; attaching the connector requires moving that one call to
  `client.beta.messages.create(..., betas=[...], mcp_servers=[...], tools=[...])`.
  Because the AI loop is parked, the descriptor is built and tested but intentionally
  not attached yet — so the disabled default and current behavior are unchanged.

## Verified request shape (for when this is switched on)

```python
reg = self.atlassian_mcp_registration()   # None when disabled
if reg is not None:
    response = self.client.beta.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=system,
        betas=[reg["beta"]],                 # "mcp-client-2025-11-20"
        mcp_servers=[reg["mcp_server"]],     # {"type":"url","url":..,"name":..,"authorization_token":..}
        tools=[*self.tools, reg["toolset"]], # {"type":"mcp_toolset","mcp_server_name":..}
        messages=self.history,
    )
```
