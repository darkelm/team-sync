"""SyncBot — the Claude-powered conversational agent."""
import os
import anthropic
from ..providers.factory import Providers
from .tools import build_tools, execute_tool

SYSTEM_PROMPT = """You are SyncBot, a multi-team coordination assistant for a software organization.

You help designers, developers, and PMs stay in sync across teams by answering questions about:
- Who owns which components (code and design)
- When teams are delivering work
- What decisions have been made and why
- What's drifting between teams (design drift, code drift)
- What conflicts are predicted
- How to get up to speed on a team

You have access to real data from: Jira (tickets), Confluence (docs, decision logs), GitHub (PRs), Figma (design components), and team manifests.

When answering:
- Be specific — name actual teams, tickets, people, and components
- Surface urgency when it's real (compliance deadlines, breaking changes, cross-team blockers)
- For designers, emphasize Figma sync status and design decisions
- For devs, emphasize PRs, tickets, and technical decisions
- Suggest next actions when issues are found
- Keep answers concise but complete — a busy engineer or designer is reading this
"""


class SyncBot:
    def __init__(self, config_path: str = "config.yaml"):
        self.providers = Providers(config_path)
        self.tools = build_tools(self.providers)
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.history: list[dict] = []

    def ask(self, question: str, verbose: bool = False) -> str:
        self.history.append({"role": "user", "content": question})

        while True:
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=self.tools,
                messages=self.history,
            )

            if response.stop_reason == "tool_use":
                tool_results = []
                assistant_content = response.content
                self.history.append({"role": "assistant", "content": assistant_content})

                for block in response.content:
                    if block.type == "tool_use":
                        if verbose:
                            print(f"[tool] {block.name}({block.input})")
                        result = execute_tool(block.name, block.input, self.providers)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                self.history.append({"role": "user", "content": tool_results})

            else:
                text = next((b.text for b in response.content if hasattr(b, "text")), "")
                self.history.append({"role": "assistant", "content": text})
                return text

    def reset(self) -> None:
        self.history = []
