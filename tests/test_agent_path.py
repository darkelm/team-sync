"""Tests for the Claude-agent path — src/agent/syncbot.py (the SyncBot agentic
loop) and src/agent/tools.py (execute_tool dispatch).

The existing net (test_router.py) covers ONLY the keyword fallback. When
ANTHROPIC_API_KEY is set the Slack bot routes through SyncBot, which calls the
Anthropic API and dispatches tools via execute_tool. That path was untested.

These tests are fully OFFLINE:
- We monkeypatch `src.agent.syncbot.anthropic.Anthropic` with a scripted fake
  client, so NO real network call is made and NO API key is required. Even
  SyncBot.__init__ (which constructs anthropic.Anthropic) hits the fake.
- The fake replays a canned sequence of API responses: first a `tool_use` stop,
  then a final `end_turn` text stop. This drives the real agentic loop in
  SyncBot.ask end-to-end, including the real execute_tool dispatch against the
  synthetic providers (tools are NOT mocked — only the LLM is).

Run: SYNCBOT_TEST=1 .venv/bin/python3 -m pytest tests/test_agent_path.py -q
"""
from __future__ import annotations

import json
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.yaml")


# ── Scripted fake Anthropic client ───────────────────────────────────────────
# Mimics just enough of the anthropic Messages API surface that SyncBot.ask
# touches: a `.messages.create(...)` returning an object with `.content` (a list
# of blocks) and `.stop_reason`. Blocks expose `.type`, and for tool_use also
# `.name`, `.input`, `.id`; for text, `.text`.


class FakeBlock:
    def __init__(self, type, text=None, name=None, input=None, id=None):
        self.type = type
        self.text = text
        self.name = name
        self.input = input
        self.id = id


class FakeResponse:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, scripted, calls):
        self._scripted = scripted
        self._calls = calls

    def create(self, **kwargs):
        # Record the call so tests can assert on wiring (model, tools, system,
        # and the messages history that the loop accumulates).
        self._calls.append(kwargs)
        idx = len(self._calls) - 1
        if idx >= len(self._scripted):
            raise AssertionError(
                f"FakeMessages.create called {idx + 1}x but only "
                f"{len(self._scripted)} responses scripted (possible loop bug)"
            )
        return self._scripted[idx]


class FakeAnthropic:
    """Replaces anthropic.Anthropic. `scripted` is the queue of FakeResponses to
    hand back in order; `calls` is a shared list capturing each create() kwargs."""

    def __init__(self, scripted, calls):
        self.scripted = scripted
        self.calls = calls

    def __call__(self, *args, **kwargs):
        # SyncBot.__init__ does anthropic.Anthropic(api_key=...). Constructing
        # the fake just returns a client whose .messages is our scripted stub.
        client = type("FakeClient", (), {})()
        client.messages = FakeMessages(self.scripted, self.calls)
        client.init_kwargs = kwargs
        return client


@pytest.fixture()
def make_bot(monkeypatch):
    """Factory: build a SyncBot whose Anthropic client replays `scripted`
    responses. Returns (bot, calls) where `calls` accumulates create() kwargs."""
    from src.agent import syncbot as syncbot_mod

    def _make(scripted):
        calls: list[dict] = []
        monkeypatch.setattr(syncbot_mod.anthropic, "Anthropic", FakeAnthropic(scripted, calls))
        # Guarantee no key is needed even at construction.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        bot = syncbot_mod.SyncBot(CONFIG_PATH)
        return bot, calls

    return _make


# ── 1+2. Agentic loop: tool_use round-trip -> final text ─────────────────────

class TestAgentLoop:
    def test_ask_dispatches_tool_and_returns_final_text(self, make_bot):
        """End-to-end: the model asks for who_owns(auth), the loop runs the REAL
        execute_tool against synthetic providers, feeds the result back, and the
        model returns final text. We assert the loop wiring and that the tool was
        actually dispatched (not mocked)."""
        scripted = [
            FakeResponse(
                content=[
                    FakeBlock("text", text="Let me check ownership."),
                    FakeBlock("tool_use", name="who_owns",
                              input={"component_name": "auth"}, id="tu_1"),
                ],
                stop_reason="tool_use",
            ),
            FakeResponse(
                content=[FakeBlock("text", text="Team Phoenix owns auth.")],
                stop_reason="end_turn",
            ),
        ]
        bot, calls = make_bot(scripted)

        answer = bot.ask("who owns auth?")

        # Final text returned from the end_turn response.
        assert answer == "Team Phoenix owns auth."

        # The loop made exactly two API calls (one tool round-trip, one final).
        assert len(calls) == 2

        # First call carried the system prompt and the full tool list (the
        # stable cached prefix).
        first = calls[0]
        assert first["model"] == syncbot_model()
        assert isinstance(first["tools"], list) and len(first["tools"]) >= 10
        assert any(t["name"] == "who_owns" for t in first["tools"])

        # The second call's messages history must contain the REAL tool_result
        # produced by execute_tool — proving the tool was actually dispatched and
        # grounded against the synthetic org (Team Phoenix owns auth).
        second_history = calls[1]["messages"]
        tool_result_block = _find_tool_result(second_history, "tu_1")
        assert tool_result_block is not None, "tool_result for tu_1 not fed back to model"
        payload = json.loads(tool_result_block["content"])
        assert payload["team"] == "Team Phoenix"

    def test_ask_no_tool_returns_text_directly(self, make_bot):
        """If the first response is already end_turn, ask() returns its text with
        no tool dispatch and exactly one API call."""
        scripted = [
            FakeResponse(
                content=[FakeBlock("text", text="Hi, I'm SyncBot.")],
                stop_reason="end_turn",
            ),
        ]
        bot, calls = make_bot(scripted)

        answer = bot.ask("hello")

        assert answer == "Hi, I'm SyncBot."
        assert len(calls) == 1

    def test_ask_handles_multiple_tool_rounds(self, make_bot):
        """The loop must survive more than one tool round-trip: tool_use ->
        tool_use -> end_turn (three API calls, two real dispatches)."""
        scripted = [
            FakeResponse(
                content=[FakeBlock("tool_use", name="who_owns",
                                   input={"component_name": "auth"}, id="tu_a")],
                stop_reason="tool_use",
            ),
            FakeResponse(
                content=[FakeBlock("tool_use", name="get_dependency_graph",
                                   input={"team_name": "Team Phoenix"}, id="tu_b")],
                stop_reason="tool_use",
            ),
            FakeResponse(
                content=[FakeBlock("text", text="Done.")],
                stop_reason="end_turn",
            ),
        ]
        bot, calls = make_bot(scripted)

        answer = bot.ask("who owns auth and what does Phoenix depend on?")

        assert answer == "Done."
        assert len(calls) == 3
        # Both real tool results made it back into the conversation.
        assert _find_tool_result(calls[1]["messages"], "tu_a") is not None
        assert _find_tool_result(calls[2]["messages"], "tu_b") is not None
        dep = json.loads(_find_tool_result(calls[2]["messages"], "tu_b")["content"])
        assert dep["team"] == "Team Phoenix"

    def test_history_persists_across_calls(self, make_bot):
        """ask() accumulates conversation history; reset() clears it."""
        scripted = [
            FakeResponse(content=[FakeBlock("text", text="A.")], stop_reason="end_turn"),
            FakeResponse(content=[FakeBlock("text", text="B.")], stop_reason="end_turn"),
        ]
        bot, _ = make_bot(scripted)

        bot.ask("first")
        # user + assistant from first turn.
        assert len(bot.history) == 2
        bot.ask("second")
        # two more entries appended.
        assert len(bot.history) == 4
        bot.reset()
        assert bot.history == []


# ── 3. Tool layer: execute_tool against synthetic providers (real dispatch) ───
# This backs BOTH the agent and the MCP server. We assert grounded output, not
# mocked — these run the actual engines over the synthetic org.

class TestExecuteToolGrounded:
    def test_who_owns_auth_grounded(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("who_owns", {"component_name": "auth"}, providers)
        data = json.loads(result)
        assert data["team"] == "Team Phoenix"
        assert data["owner"]  # a real owner name, not empty
        assert "auth" in [c.lower() for c in data["code_components"]] or data["code_components"]

    def test_get_dependency_graph_filtered_grounded(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("get_dependency_graph", {"team_name": "Team Phoenix"}, providers)
        data = json.loads(result)
        assert data["team"] == "Team Phoenix"
        # Phoenix has at least one declared dependency in the synthetic org.
        assert isinstance(data["depends_on"], list)
        assert len(data["depends_on"]) >= 1

    def test_get_team_context_grounded(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("get_team_context", {"team_name": "Team Phoenix"}, providers)
        data = json.loads(result)
        assert data["team"] == "Team Phoenix"
        assert data["owner"]["name"]
        assert "quarter_goals" in data

    def test_unknown_tool_grounded(self, providers):
        from src.agent.tools import execute_tool
        result = execute_tool("does_not_exist", {}, providers)
        assert "Unknown tool" in result


# ── helpers ──────────────────────────────────────────────────────────────────

def syncbot_model() -> str:
    from src.agent import syncbot as syncbot_mod
    return syncbot_mod.MODEL


def _find_tool_result(history: list[dict], tool_use_id: str):
    """Walk the messages history for the tool_result block matching tool_use_id."""
    for msg in history:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if (isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        and block.get("tool_use_id") == tool_use_id):
                    return block
    return None
