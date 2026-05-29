"""SyncBot — the Claude-powered conversational agent.

Activates when ANTHROPIC_API_KEY is set; the Slack bot falls back to keyword
matching otherwise. Uses Opus 4.8 with adaptive thinking and prompt caching on
the stable system+tools prefix (re-sent on every tool round-trip).
"""
import os
import anthropic
from ..providers.factory import Providers
from .tools import build_tools, execute_tool

# Default to the most capable model; override with ANTHROPIC_MODEL if needed.
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")

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
        self.providers = Providers(config_path)
        self.tools = build_tools(self.providers)
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.history: list[dict] = []

    def ask(self, question: str, verbose: bool = False) -> str:
        self.history.append({"role": "user", "content": question})

        # Cache the stable prefix (tools + system) — re-sent on every tool round-trip.
        system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

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
