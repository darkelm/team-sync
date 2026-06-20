"""SyncBot — the Claude-powered conversational agent.

Activates when ANTHROPIC_API_KEY is set; the Slack bot falls back to keyword
matching otherwise. Uses Opus 4.8 with adaptive thinking and prompt caching on
the stable system+tools prefix (re-sent on every tool round-trip).
"""
import os
import anthropic
from ..providers.factory import Providers, load_config
from .tools import build_tools, execute_tool

# Default to the most capable model; override with ANTHROPIC_MODEL if needed.
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")

# ── Atlassian (Rovo) Remote MCP Server — OPTIONAL, AI-mode-only, OFF by default ──
#
# WHAT: Atlassian's Rovo Remote MCP Server (GA Feb 2026) exposes 72+ tools across
# Jira / Confluence / Compass over OAuth 2.1, honoring the connected account's
# permissions. It supports READ *and* WRITE (create/update issues & pages, link
# them, run bulk natural-language workflows). [V] — verified against Atlassian +
# Anthropic MCP-connector docs (beta header `mcp-client-2025-11-20`).
#
# WHERE IT BELONGS: an MCP server is consumed by an MCP-capable *agent*, so this
# is SyncBot's concern (model claude-opus-4-8, active only when ANTHROPIC_API_KEY
# is set) — NOT the deterministic provider layer. The REST providers
# (src/providers/live/jira.py, confluence.py) stay the always-on deterministic
# backbone; this MCP is an AI-mode "supercharger" for natural-language + writes.
#
# ⚠️ GOVERNANCE — NOT A BYPASS (see docs/atlassian-mcp.md, src/agent/membrane.py):
# Any agent-PROPOSED Atlassian WRITE (create/update/link/transition) MUST route
# through the governance membrane's `review` lane (Lane.REVIEW) for human sign-off
# BEFORE it fires. The MCP connector is not an end-run around that. The
# write-approval loop is NOT implemented here yet; the seam below is built so
# write tools can be gated (denylisted at the connector / wrapped) when it is.
#
# Connector wire-shape (verified) per request:
#   mcp_servers=[{"type":"url","url":<URL>,"name":"atlassian-rovo",
#                 "authorization_token":<OAUTH_TOKEN>}]
#   tools=[{"type":"mcp_toolset","mcp_server_name":"atlassian-rovo", ...}]
#   betas=["mcp-client-2025-11-20"]   (via client.beta.messages.create)

# Stable identifier this server is referenced by in the request (mcp_servers[].name
# must match tools[].mcp_server_name — an API validation rule).
ATLASSIAN_MCP_SERVER_NAME = "atlassian-rovo"

# Required beta header value for the Messages-API MCP connector. [V]
ATLASSIAN_MCP_BETA = "mcp-client-2025-11-20"


def atlassian_mcp_config(config_path: str = "config.yaml") -> dict | None:
    """Return the Atlassian MCP connection config IFF it is fully specified, else None.

    Two sources, env wins over file (env is the deploy-time secret surface):
      - env:    ATLASSIAN_MCP_URL  +  ATLASSIAN_MCP_TOKEN  (OAuth 2.1 access token)
      - config: an `atlassian_mcp:` block in config.yaml with `url:` (+ optional
                `token_env:` naming the env var that holds the OAuth token).

    "Fully specified" means BOTH a URL and an OAuth token are resolvable. Anything
    less ⇒ None (integration OFF). This function does NOT check whether AI mode is
    on — that gate lives in :func:`atlassian_mcp_enabled`, so callers can inspect
    config independently of activation. No network/OAuth call is ever made here.
    """
    url = os.environ.get("ATLASSIAN_MCP_URL")
    token = os.environ.get("ATLASSIAN_MCP_TOKEN")

    # config.yaml block is a fallback for the URL (and lets ops name a custom token
    # env var). Secrets themselves are never read from the file — only from env.
    if url is None or token is None:
        try:
            cfg = load_config(config_path) or {}
        except OSError:
            cfg = {}
        block = cfg.get("atlassian_mcp") or {}
        if url is None:
            url = block.get("url")
        if token is None and block.get("token_env"):
            token = os.environ.get(block["token_env"])

    if not url or not token:
        return None
    return {"url": url, "token": token, "name": ATLASSIAN_MCP_SERVER_NAME}


def ai_mode_enabled() -> bool:
    """AI mode is ON exactly when an Anthropic API key is present — the same gate
    bootstrap.py uses to construct SyncBot at all (else the bot is keyword-only)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def atlassian_mcp_enabled(config_path: str = "config.yaml") -> bool:
    """True only when AI mode is on AND the MCP config is fully specified.

    This is the single switch every wire-in point must check. With no env/config
    (the default) it returns False and the whole integration is a no-op."""
    return ai_mode_enabled() and atlassian_mcp_config(config_path) is not None

SYSTEM_PROMPT = """You are SyncBot, a multi-team coordination assistant for a software organization.

You help designers, developers, and PMs stay in sync across teams. You can answer questions about:
- Who owns which components (code and design) and who to talk to
- When teams are delivering work, and their roadmap
- What decisions have been made and why (including decisions captured from meeting transcripts)
- What's drifting between teams (design drift, code drift) and what conflicts are predicted
- Which teams are doing related work and should be collaborating
- Whether a component/research already exists before a team rebuilds it (reuse)
- Whether team goals ladder up to company objectives (strategic alignment)
- Where to find things (research repos, brand assets, prototypes, docs)
- Open action items from recent meetings
- How to get up to speed on a team, or prep for a cross-team meeting

You have access to data from Jira, Confluence, GitHub, Figma, meeting transcripts, and team manifests via tools.

When answering:
- Be specific — name actual teams, tickets, people, and components.
- Choose the right tool(s) for the question; you may call several before answering.
- Surface urgency when it's real (compliance deadlines, breaking changes, cross-team blockers).
- For designers, emphasize Figma sync status and design decisions; for devs, PRs, tickets, and technical decisions.
- Suggest concrete next actions when issues are found.
- If the data doesn't cover something, say so plainly and point to who might know — never invent owners, dates, or decisions.
- Keep answers concise but complete — a busy engineer or designer is reading this in Slack. Use short Slack-friendly formatting.
"""


class SyncBot:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.providers = Providers(config_path)
        self.tools = build_tools(self.providers)
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.history: list[dict] = []

    # ── Atlassian MCP registration seam ──────────────────────────────────────
    #
    # The current ask() loop calls the STABLE `client.messages.create(...)`, which
    # does not accept `mcp_servers`. Wiring the remote MCP connector requires the
    # BETA endpoint (`client.beta.messages.create(..., betas=[ATLASSIAN_MCP_BETA])`).
    # The AI agent loop is parked, so rather than silently switch the live loop onto
    # a beta path, this method RETURNS the registration descriptor and the wire-in is
    # left as a single, clearly-marked TODO in ask(). When config is absent the whole
    # thing is a no-op: descriptor is None and nothing changes.

    def atlassian_mcp_registration(self) -> dict | None:
        """Build the Atlassian MCP registration descriptor, or None when disabled.

        Returns a dict with the EXACT request-ready pieces the Messages-API MCP
        connector expects (verified shape):
          {
            "beta":        ATLASSIAN_MCP_BETA,           # → betas=[...]
            "mcp_server":  {"type":"url","url":..,"name":..,"authorization_token":..},
            "toolset":     {"type":"mcp_toolset","mcp_server_name":..},
          }

        Returns None unless :func:`atlassian_mcp_enabled` is True (AI mode on AND
        URL+OAuth token resolvable). The default (no config) path returns None, so
        callers add nothing to the request and behavior is unchanged.

        ⚠️ WRITES-THROUGH-THE-MEMBRANE (not enforced yet, seam only): the `toolset`
        is the chokepoint where Atlassian WRITE tools should later be denylisted
        (`configs: {<write_tool>: {enabled: false}}`) or wrapped so an agent-proposed
        write is routed to the membrane's `review` lane for human approval BEFORE it
        fires — instead of being called directly by the model. See docs/atlassian-mcp.md.
        """
        cfg = atlassian_mcp_config(self.config_path)
        if cfg is None or not ai_mode_enabled():
            return None
        return {
            "beta": ATLASSIAN_MCP_BETA,
            "mcp_server": {
                "type": "url",
                "url": cfg["url"],
                "name": cfg["name"],
                "authorization_token": cfg["token"],
            },
            "toolset": {
                "type": "mcp_toolset",
                "mcp_server_name": cfg["name"],
                # TODO (writes-through-the-membrane): when the write-approval loop
                # lands, populate `configs` here to disable Atlassian WRITE tools at
                # the connector (read-only by default), and route proposed writes
                # through src.agent.membrane → Lane.REVIEW for human sign-off.
            },
        }

    def ask(self, question: str, verbose: bool = False) -> str:
        self.history.append({"role": "user", "content": question})

        # Cache the stable prefix (tools + system) — re-sent on every tool round-trip.
        system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

        # ── Atlassian MCP wire-in point (TODO) ──────────────────────────────
        # When the AI loop is un-parked AND we move this call to the beta endpoint,
        # this is the ONE place to attach the Atlassian Rovo MCP connector:
        #
        #   reg = self.atlassian_mcp_registration()
        #   if reg is not None:
        #       response = self.client.beta.messages.create(
        #           ..., betas=[reg["beta"]],
        #           mcp_servers=[reg["mcp_server"]],
        #           tools=[*self.tools, reg["toolset"]],
        #       )
        #
        # Until then the descriptor is built (and tested) but NOT attached, so the
        # default disabled path — and the current live behavior — are unchanged.
        # Do NOT call writes directly from this loop: agent-proposed Atlassian writes
        # must go through src.agent.membrane (Lane.REVIEW) first. See docs/atlassian-mcp.md.

        while True:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=system,
                tools=self.tools,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                messages=self.history,
            )

            if response.stop_reason == "tool_use":
                # Preserve full content (including thinking blocks) for the tool-use round-trip.
                self.history.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        if verbose:
                            print(f"[tool] {block.name}({block.input})", flush=True)
                        result = execute_tool(block.name, block.input, self.providers)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                self.history.append({"role": "user", "content": tool_results})
            else:
                text = "\n".join(b.text for b in response.content if getattr(b, "type", "") == "text")
                self.history.append({"role": "assistant", "content": response.content})
                return text.strip()

    def reset(self) -> None:
        self.history = []
