#!/usr/bin/env python3
"""Shared, non-Slack-specific module state for SyncBot.

This is the foundation layer: it loads env, runs the provider preflight, and
constructs the default engine bundle + per-project engine cache that both the
keyword router (router.py) and the Slack handlers (slack_bot.py) build on.

Import order matters and is preserved from the original slack_bot.py:
  load_dotenv() → sys.path setup → _preflight() → providers/engine construction.

bootstrap.py imports ONLY from src.* (and stdlib/dotenv). It must NOT import
router.py or slack_bot.py, so there is no import cycle.
"""
import os
import sys
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, ".")

from src.providers.factory import Providers
from src.agent.detector import DriftDetector
from src.agent.digest import DigestGenerator
from src.agent.briefing import BriefingGenerator
from src.agent.discovery import CollaboratorDiscovery, ReuseRadar
from src.agent.alignment import AlignmentChecker
from src.agent.findability import FindabilityLocator

# ── Multi-project registry ────────────────────────────────────────────────────
# Every query is scoped to a project. Google channels → Google data only.
# Workday channels → Workday data only. They never share data or notifications.
from src.projects import ProjectRegistry
project_registry = ProjectRegistry(default_config="config.yaml")

_ENGINE_CACHE: dict[str, dict] = {}


def _project_engines(channel_id: str = "", channel_name: str = ""):
    """Return all engines scoped to the project this channel belongs to.

    Memoized per project config: the bundle (and its providers, which cache
    manifests) is built once and reused, instead of reconstructing 11 engine
    objects on every message.
    """
    project = project_registry.for_channel(channel_id, channel_name)
    config = project.config
    cached = _ENGINE_CACHE.get(config)
    if cached is not None:
        return cached
    p = project.providers()
    bundle = {
        "providers": p,
        "project": project,
        "detector": DriftDetector(p),
        "digest_gen": DigestGenerator(p),
        "briefing_gen": BriefingGenerator(p),
        "discovery": CollaboratorDiscovery(p),
        "reuse_radar": ReuseRadar(p),
        "alignment": AlignmentChecker(p),
        "locator": FindabilityLocator(p),
        "health": HealthAssessor(p, config),
        "strategy": StrategyLens(p, config),
        "router": EventRouter(p),
    }
    _ENGINE_CACHE[config] = bundle
    return bundle

def _preflight(config_path: str = "config.yaml") -> None:
    """Fail fast with a clear message when a provider is set to 'live' but its
    token(s) are missing — instead of a cryptic KeyError inside a constructor."""
    import yaml
    required = {
        "jira": ["ATLASSIAN_URL", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"],
        "confluence": ["ATLASSIAN_URL", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"],
        "github": ["GITHUB_TOKEN"],
        "slack": ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"],
        "figma": ["FIGMA_ACCESS_TOKEN"],
    }
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
    except OSError:
        return
    problems = []
    for name, mode in (cfg.get("providers") or {}).items():
        if mode == "live":
            missing = [v for v in required.get(name, []) if not os.environ.get(v)]
            if missing:
                problems.append((name, missing))
    if problems and not os.getenv("SYNCBOT_TEST"):
        lines = ["[preflight] These providers are set to 'live' in config.yaml but are missing tokens:"]
        for name, missing in problems:
            lines.append(f"  • {name}: missing {', '.join(missing)}")
        lines.append("Fix: add the token(s) to .env, or set the provider back to 'local' in config.yaml.")
        raise SystemExit("\n".join(lines))


_preflight("config.yaml")

# Default engines (no channel context — used by CLI, MCP, startup checks)
providers = Providers("config.yaml")
detector = DriftDetector(providers)
digest_gen = DigestGenerator(providers)
briefing_gen = BriefingGenerator(providers)
discovery = CollaboratorDiscovery(providers)
reuse_radar = ReuseRadar(providers)
alignment = AlignmentChecker(providers)
locator = FindabilityLocator(providers)
from src.agent.health import HealthAssessor
health = HealthAssessor(providers)
from src.agent.strategy import StrategyLens
strategy = StrategyLens(providers)
from src.agent.events import EventRouter, Event
router = EventRouter(providers)

# The default engine bundle (config.yaml). handle_query() runs against whatever
# bundle it's handed; channel handlers pass a per-project bundle from
# _project_engines() so keyword queries are scoped to the channel's project.
_DEFAULT_ENGINES = {
    "providers": providers, "project": None,
    "detector": detector, "digest_gen": digest_gen, "briefing_gen": briefing_gen,
    "discovery": discovery, "reuse_radar": reuse_radar, "alignment": alignment,
    "locator": locator, "health": health, "strategy": strategy, "router": router,
}

# Natural-language agent (Claude). Activates only if an API key is present;
# otherwise the bot uses keyword matching (handle_query).
AGENT = None
if os.environ.get("ANTHROPIC_API_KEY"):
    try:
        from src.agent.syncbot import SyncBot
        AGENT = SyncBot("config.yaml")
    except Exception as e:
        print(f"[agent] Claude agent unavailable, using keyword mode: {e}", flush=True)


from src.agent.audience import AudienceStore, agent_hint, is_non_technical, parse_role_command
from src.agent.plain import plainify

audience = AudienceStore()

# Public surface. These names are constructed/imported here and consumed by
# router.py and slack_bot.py, which import them FROM bootstrap — so they look
# unused locally but are deliberate re-exports of the shared module state.
__all__ = [
    "AGENT",
    "AudienceStore",
    "Event",
    "EventRouter",
    "HealthAssessor",
    "Providers",
    "StrategyLens",
    "agent_hint",
    "alignment",
    "audience",
    "briefing_gen",
    "detector",
    "digest_gen",
    "discovery",
    "health",
    "is_non_technical",
    "locator",
    "parse_role_command",
    "plainify",
    "project_registry",
    "providers",
    "reuse_radar",
    "router",
    "strategy",
    "_DEFAULT_ENGINES",
    "_ENGINE_CACHE",
    "_preflight",
    "_project_engines",
]
