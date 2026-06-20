"""Tests for the OPTIONAL Atlassian (Rovo) Remote MCP Server seam.

The integration is scaffolding: a config helper + a registration descriptor +
a documented wire-in TODO. It is DISABLED BY DEFAULT and only becomes active when
AI mode is on (ANTHROPIC_API_KEY set) AND both a URL and an OAuth token resolve.

These tests are fully HERMETIC:
- SYNCBOT_TEST=1 (set by conftest) keeps Providers/Slack offline.
- We monkeypatch env vars; we NEVER make a real network or OAuth call.
- SyncBot's Anthropic client is replaced with a no-op fake so __init__ needs no
  API key and constructs offline.

The crux: the DEFAULT (no env/config) path is a complete no-op — helper returns
None/False, no registration descriptor, SyncBot still constructs fine. When the
config IS present and AI mode is simulated, the helpers report enabled and the
registration descriptor matches the verified Messages-API MCP-connector shape.

Run: SYNCBOT_TEST=1 .venv/bin/python3 -m pytest tests/test_atlassian_mcp.py -q
"""
from __future__ import annotations

import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.yaml")

# Env vars the seam reads. Cleared before every test so order can't leak state.
MCP_ENV = ("ATLASSIAN_MCP_URL", "ATLASSIAN_MCP_TOKEN", "ANTHROPIC_API_KEY")

URL = "https://mcp.atlassian.example/v1/sse"
TOKEN = "fake-oauth-token-not-real"  # noqa: S105 — obviously fake, never used live


@pytest.fixture(autouse=True)
def _clean_mcp_env(monkeypatch):
    """Guarantee a known-empty starting point: no MCP config, no AI mode."""
    for var in MCP_ENV:
        monkeypatch.delenv(var, raising=False)
    yield


class FakeAnthropic:
    """Stands in for anthropic.Anthropic so SyncBot.__init__ needs no API key and
    makes no network call. We only need construction to succeed."""

    def __init__(self, *args, **kwargs):
        self.messages = object()


@pytest.fixture()
def make_bot(monkeypatch):
    """Construct a SyncBot offline (fake Anthropic client). Returns the bot."""
    from src.agent import syncbot as syncbot_mod

    def _make():
        monkeypatch.setattr(syncbot_mod.anthropic, "Anthropic", FakeAnthropic)
        return syncbot_mod.SyncBot(CONFIG_PATH)

    return _make


def _enable(monkeypatch):
    """Simulate AI mode on + a fully-specified MCP config via env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("ATLASSIAN_MCP_URL", URL)
    monkeypatch.setenv("ATLASSIAN_MCP_TOKEN", TOKEN)


# ── 1. OFF by default ────────────────────────────────────────────────────────

class TestDisabledByDefault:
    def test_config_helper_returns_none(self):
        from src.agent.syncbot import atlassian_mcp_config
        assert atlassian_mcp_config(CONFIG_PATH) is None

    def test_enabled_helper_returns_false(self):
        from src.agent.syncbot import atlassian_mcp_enabled
        assert atlassian_mcp_enabled(CONFIG_PATH) is False

    def test_registration_is_none_no_op(self, make_bot):
        bot = make_bot()
        assert bot.atlassian_mcp_registration() is None

    def test_syncbot_constructs_fine_when_disabled(self, make_bot):
        # The default path must not break SyncBot construction at all.
        bot = make_bot()
        assert bot.config_path == CONFIG_PATH
        assert isinstance(bot.tools, list) and bot.tools


# ── 2. Partial config is still OFF (needs URL *and* token *and* AI mode) ──────

class TestPartialConfigStaysOff:
    def test_ai_mode_only_no_creds_off(self, monkeypatch):
        from src.agent.syncbot import atlassian_mcp_config, atlassian_mcp_enabled
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        assert atlassian_mcp_config(CONFIG_PATH) is None
        assert atlassian_mcp_enabled(CONFIG_PATH) is False

    def test_url_without_token_off(self, monkeypatch):
        from src.agent.syncbot import atlassian_mcp_config, atlassian_mcp_enabled
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        monkeypatch.setenv("ATLASSIAN_MCP_URL", URL)
        assert atlassian_mcp_config(CONFIG_PATH) is None
        assert atlassian_mcp_enabled(CONFIG_PATH) is False

    def test_creds_present_but_ai_mode_off(self, monkeypatch):
        """Config fully specified but no ANTHROPIC_API_KEY ⇒ config resolves yet the
        integration is NOT enabled (AI mode is the activation gate)."""
        from src.agent.syncbot import atlassian_mcp_config, atlassian_mcp_enabled
        monkeypatch.setenv("ATLASSIAN_MCP_URL", URL)
        monkeypatch.setenv("ATLASSIAN_MCP_TOKEN", TOKEN)
        # Config alone is resolvable...
        assert atlassian_mcp_config(CONFIG_PATH) == {
            "url": URL, "token": TOKEN, "name": "atlassian-rovo",
        }
        # ...but without AI mode the integration stays OFF.
        assert atlassian_mcp_enabled(CONFIG_PATH) is False

    def test_registration_none_when_ai_mode_off(self, make_bot, monkeypatch):
        monkeypatch.setenv("ATLASSIAN_MCP_URL", URL)
        monkeypatch.setenv("ATLASSIAN_MCP_TOKEN", TOKEN)
        bot = make_bot()
        assert bot.atlassian_mcp_registration() is None


# ── 3. ON when AI mode + full config are simulated ───────────────────────────

class TestEnabled:
    def test_config_helper_returns_creds(self, monkeypatch):
        from src.agent.syncbot import atlassian_mcp_config
        _enable(monkeypatch)
        cfg = atlassian_mcp_config(CONFIG_PATH)
        assert cfg == {"url": URL, "token": TOKEN, "name": "atlassian-rovo"}

    def test_enabled_helper_true(self, monkeypatch):
        from src.agent.syncbot import atlassian_mcp_enabled
        _enable(monkeypatch)
        assert atlassian_mcp_enabled(CONFIG_PATH) is True

    def test_registration_descriptor_shape(self, make_bot, monkeypatch):
        """The descriptor must match the verified Messages-API MCP-connector wire
        shape (beta header + url server def + mcp_toolset)."""
        from src.agent.syncbot import ATLASSIAN_MCP_BETA, ATLASSIAN_MCP_SERVER_NAME
        _enable(monkeypatch)
        bot = make_bot()
        reg = bot.atlassian_mcp_registration()
        assert reg is not None

        assert reg["beta"] == ATLASSIAN_MCP_BETA == "mcp-client-2025-11-20"

        server = reg["mcp_server"]
        assert server == {
            "type": "url",
            "url": URL,
            "name": ATLASSIAN_MCP_SERVER_NAME,
            "authorization_token": TOKEN,
        }

        toolset = reg["toolset"]
        assert toolset["type"] == "mcp_toolset"
        # API rule: toolset.mcp_server_name must match the server's name.
        assert toolset["mcp_server_name"] == server["name"] == "atlassian-rovo"

    def test_config_block_url_with_token_env(self, monkeypatch, tmp_path):
        """The config.yaml fallback supplies the URL and names the token env var;
        the secret itself still comes from env, never the file."""
        import yaml
        from src.agent.syncbot import atlassian_mcp_config, atlassian_mcp_enabled

        cfg_file = str(tmp_path / "config.yaml")
        with open(cfg_file, "w") as f:
            yaml.dump({
                "providers": {"jira": "local", "confluence": "local",
                              "github": "local", "slack": "local", "figma": "local"},
                "atlassian_mcp": {"url": URL, "token_env": "ATLASSIAN_MCP_TOKEN"},
            }, f)

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        monkeypatch.setenv("ATLASSIAN_MCP_TOKEN", TOKEN)
        # No ATLASSIAN_MCP_URL env — URL comes from the file block.

        cfg = atlassian_mcp_config(cfg_file)
        assert cfg == {"url": URL, "token": TOKEN, "name": "atlassian-rovo"}
        assert atlassian_mcp_enabled(cfg_file) is True


# ── 4. No real network/OAuth call is ever made ───────────────────────────────

class TestNoNetwork:
    def test_helpers_make_no_http_call(self, monkeypatch):
        """Defensive: blow up if any urllib/socket call is attempted by the seam."""
        import socket

        def _boom(*a, **k):
            raise AssertionError("Atlassian MCP seam attempted a real network call")

        monkeypatch.setattr(socket, "create_connection", _boom)
        _enable(monkeypatch)

        from src.agent.syncbot import atlassian_mcp_config, atlassian_mcp_enabled
        assert atlassian_mcp_config(CONFIG_PATH) is not None
        assert atlassian_mcp_enabled(CONFIG_PATH) is True
