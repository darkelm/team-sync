#!/usr/bin/env python3
"""
SyncBot Slack bot — listens for @syncbot mentions and responds with real data.
Uses Socket Mode so no public URL or ngrok needed.

Usage:
    python3 slack_bot.py

This module is the Slack-facing layer only. Shared engine state lives in
bootstrap.py (imported first, so load_dotenv / preflight / provider construction
all run before anything here touches them) and the keyword brain lives in
router.py. Both are re-exported here so existing callers/tests that reach for
`slack_bot.<name>` keep working.
"""
import os
import re

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Shared, non-Slack module state (env load, preflight, providers, engines, agent,
# registry, audience). Imported before anything below reads it so side-effect
# ordering (load_dotenv → _preflight → provider construction) is preserved.
from bootstrap import (
    AGENT,
    agent_hint,
    audience,
    digest_gen,
    is_non_technical,
    parse_role_command,
    plainify,
    project_registry,
    providers,
    _project_engines,
)
from src.agent.scheduler import DigestScheduler
from src.log import configure_logging, get_logger

log = get_logger("syncbot")

# Keyword router (the no-LLM brain). handle_query and _match_teams are used by
# the handlers below; re-exported so `slack_bot.handle_query` still resolves.
from router import handle_query, _match_teams

# Re-exported shared state. handle_query / providers / digest_gen /
# project_registry / _project_engines are pulled in from router/bootstrap so
# existing callers and the test suite can keep reaching for `slack_bot.<name>`.
__all__ = [
    "answer",
    "app",
    "digest_gen",
    "handle_query",
    "handle_dm",
    "handle_join",
    "handle_mention",
    "project_registry",
    "providers",
    "strip_mention",
    "_channel_display_name",
    "_handle_channel_registration",
    "_handle_digest_targeting",
    "_handle_role_command",
    "_ingest_slack_files",
    "_match_teams",
    "_project_engines",
]

# token_verification calls Slack's auth.test at construction; disable it under
# SYNCBOT_TEST so the module can be imported offline by the test suite.
app = App(token=os.environ.get("SLACK_BOT_TOKEN", "xoxb-test"),
          token_verification_enabled=not os.getenv("SYNCBOT_TEST"))


def answer(text: str, role: str = "ic", project_config: str = "config.yaml",
           eng: dict | None = None, actor: str = "") -> str:
    """Answer a question, scoped to a project and framed for the audience's role.

    project_config isolates the AI-agent path; eng is the per-project keyword
    engine bundle (from _project_engines) so keyword queries are scoped too —
    Google channels get Google data, Workday channels get Workday data.
    """
    reply = None
    if AGENT is not None:
        try:
            # Per-project agent instance so context is scoped
            from src.agent.syncbot import SyncBot
            agent = SyncBot(project_config)
            hint = agent_hint(role)
            reply = agent.ask(f"{hint}\n\n{text}" if hint else text)
        except Exception as e:
            log.warning("agent error, falling back to keywords: %s", e)
    if not reply:
        reply = handle_query(text, eng, role, actor)
    if is_non_technical(role) and AGENT is None:
        reply = plainify(reply)
    return reply


def _handle_channel_registration(text: str, event) -> str | None:
    """Handle channel registration commands. Returns a reply or None if not a reg command."""
    q = text.lower().strip()
    channel_id = event.get("channel", "")

    # ── "which project is this?" ──────────────────────────────────────────────
    if any(w in q for w in ["which project", "what project", "which engagement",
                            "am i in", "is this channel"]):
        project = project_registry.for_channel(channel_id)
        if project.name == "default":
            return ("This channel isn't assigned to a project yet.\n"
                    "To register it: `@syncbot register this channel for Google Gen AI`")
        return (f"This channel is part of *{project.name}* (using `{project.config}`).\n"
                f"All queries here are scoped to that project only.")

    # ── "register this channel for [project]" ─────────────────────────────────
    if "register this channel" in q or "add this channel" in q:
        # Extract project name — everything after "for" or "as"
        m = re.search(r"(?:for|as)\s+(.+)$", text, re.I)
        project_name = m.group(1).strip().strip('"\'') if m else ""

        if not project_name:
            # List existing projects to pick from
            names = [p.name for p in project_registry.all_projects()]
            if names:
                return (f"Which project? Say: `@syncbot register this channel for [name]`\n"
                        f"Current projects: *{', '.join(names)}*\n"
                        f"Or start a new one: `@syncbot register this channel for My New Project`")
            return ("No projects yet. Say: `@syncbot register this channel for My New Project`\n"
                    "I'll start the setup flow.")

        # Find an existing project (fuzzy match on name)
        existing = next(
            (p for p in project_registry.all_projects()
             if project_name.lower() in p.name.lower() or p.name.lower() in project_name.lower()),
            None
        )

        # Fetch channel name from Slack if possible
        channel_name = channel_id
        try:
            info = app.client.conversations_info(channel=channel_id)
            channel_name = "#" + info["channel"]["name"]
        except Exception:
            pass

        if existing:
            # Add this channel to the existing project
            if channel_id not in existing.channels:
                existing.channels.append(channel_id)
                project_registry._save()
            return (f"✅ *{channel_name}* is now registered to *{existing.name}*.\n"
                    f"All queries from this channel will use `{existing.config}` — "
                    f"scoped to that project's teams, journeys, and principles only.\n"
                    f"Other projects can't see this channel's data.")
        else:
            # New project name — start the full registration flow inline
            # Store the project name so the flow can use it, then hand off
            from src.onboarding.flow import start_registration, _STORE, FlowState
            state = FlowState(user_id=event.get("user", ""), channel_id=channel_id,
                              stage="describe", register_project=True)
            # Pre-seed with the project name so the extractor picks it up
            state.accumulated_text = f"Project name: {project_name}\nClient: {project_name}\n"
            _STORE[event.get("user", "")] = state
            return (f"Got it — let's set up *{project_name}* as a new project.\n"
                    f"Tell me about the work — paste an RFP, brief, transcript, or just describe it.\n"
                    f"I'll extract the structure, create the config, and register {channel_name} automatically.\n\n"
                    f"_(Say `cancel` to stop.)_")

    # ── "unregister this channel" ─────────────────────────────────────────────
    if "unregister this channel" in q or "remove this channel" in q:
        removed_from = None
        for p in project_registry.all_projects():
            if channel_id in p.channels:
                p.channels.remove(channel_id)
                removed_from = p.name
        if removed_from:
            project_registry._save()
            return f"✅ This channel is no longer registered to *{removed_from}*. It will use the default config."
        return "This channel wasn't registered to any specific project."

    return None


def _channel_display_name(channel_id: str) -> str:
    """Resolve a channel ID to a #name for display; fall back to the ID."""
    try:
        info = app.client.conversations_info(channel=channel_id)
        return "#" + info["channel"]["name"]
    except Exception:
        return channel_id


def _handle_digest_targeting(text: str, event) -> str | None:
    """Slack-native digest delivery targeting. Returns a reply or None.

    Lets anyone make the current channel a team's digest destination without
    editing team.yaml — e.g. `@syncbot send Team Nova's digest here`,
    `@syncbot send all digests here`, `@syncbot stop sending digests here`.
    The mapping (team -> channel ID) is stored in notification prefs and wins
    over the manifest's slack_channel. Plain `send digest` (no "here") falls
    through to the broadcast handler.
    """
    q = text.lower().strip()
    if "digest" not in q:
        return None
    channel_id = event.get("channel", "")
    is_here = "here" in q or "this channel" in q
    is_send = any(w in q for w in ["send", "deliver", "post", "route"])
    is_stop = any(w in q for w in ["stop", "don't", "dont", "no longer", "unsubscribe", "remove", "cancel"])
    prefs = digest_gen.prefs

    # Teams visible in THIS channel's project — used for inference and prompts.
    proj = project_registry.for_channel(channel_id)
    proj_teams = [t.team for t in proj.providers().manifests.get_all_teams()]

    # STOP delivering here
    if is_stop and is_here:
        named = _match_teams(text)
        candidates = named or proj_teams
        cleared = [t for t in candidates if prefs.get_digest_channel(t) == channel_id and prefs.clear_digest_channel(t)]
        if cleared:
            return f"✅ Stopped delivering digests to this channel for: *{', '.join(cleared)}*."
        return "No team digests were being delivered to this channel."

    # SET delivery here
    if is_send and is_here:
        channel_name = _channel_display_name(channel_id)
        if "all" in q and "digest" in q:
            teams = proj_teams
        else:
            teams = _match_teams(text)
            if not teams:
                if len(proj_teams) == 1:
                    teams = proj_teams
                else:
                    example = proj_teams[0] if proj_teams else "Team Nova"
                    return ("Which team's digest should I deliver here?\n"
                            f"e.g. `@syncbot send {example}'s digest here`"
                            + (f"\nTeams: {', '.join(proj_teams)}" if proj_teams else "")
                            + "\nOr `@syncbot send all digests here` for every team.")
        if not teams:
            return "No teams are configured yet."
        for t in teams:
            prefs.set_digest_channel(t, channel_id, channel_name)
        return (f"✅ Digests for *{', '.join(teams)}* will be delivered to {channel_name} from now on.\n"
                f"_Automatically on the weekly schedule, or right now with_ `@syncbot send digest`.\n"
                f"_Stop anytime:_ `@syncbot stop sending digests here`.")

    return None


def strip_mention(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


INTRO = (
    f"👋 *Hi, I'm SyncBot* — I help keep teams in sync.  "
    f"_(mode: {'🧠 AI' if AGENT else '⌨️ keyword'})_\n"
    "Ask me anything in plain language, for example:\n"
    "• _who owns the auth component?_\n"
    "• _who should I be talking to?_\n"
    "• _has anyone already designed a trust signal?_\n"
    "• _where do I find the user research?_\n"
    "• _how's the Agentic Shopping journey?_\n"
    "• _prep me for a sync with Pair 1 and Pair 2_\n\n"
    "*First time here?* Register this channel to a project:\n"
    "`@syncbot register this channel for Google Gen AI`\n"
    "Type `@syncbot help` anytime for the full list."
)

BOT_USER_ID = None


def _handle_role_command(text: str, event) -> str | None:
    """If the user is setting their role, record it and return a confirmation."""
    role = parse_role_command(text)
    if role and event.get("user"):
        return audience.set_user(event["user"], role)
    return None


def _check_onboarding(text: str, event, say, thread_ts: str) -> bool:
    """Handle onboarding and project registration flow turns. Returns True if consumed."""
    from src.onboarding.flow import get_state, process_turn, start_registration
    user_id = event.get("user", "")
    channel_id = event.get("channel", "")
    q = text.lower()
    state = get_state(user_id, channel_id)
    in_flight = state.stage not in ("init", "done")

    # ── Project registration (creates config + registers in ProjectRegistry) ──
    is_register_trigger = any(w in q for w in [
        "register project", "register a project", "register new project",
        "set up a project", "create a project", "add a project",
    ])
    if is_register_trigger and not in_flight:
        reply = start_registration(user_id, channel_id)
        say(reply, thread_ts=thread_ts)
        return True

    # ── Content onboarding (setup files only, no registry) ───────────────────
    is_onboard_trigger = any(w in q for w in [
        "set up my initiative", "new initiative", "new engagement",
        "start a new", "set up a new", "onboard my", "onboard this",
        "new initiative",
    ])

    if not is_register_trigger and not is_onboard_trigger and not in_flight:
        return False

    reply, done = process_turn(user_id, channel_id, text if in_flight else "/start")
    say(reply, thread_ts=thread_ts)
    return True


@app.event("app_mention")
def handle_mention(event, say):
    thread_ts = event.get("thread_ts") or event.get("ts")
    text = strip_mention(event.get("text", ""))
    if not text:
        say(handle_query("help"), thread_ts=thread_ts)
        return
    # Channel registration commands (before role/onboarding — they need the raw event)
    reg_reply = _handle_channel_registration(text, event)
    if reg_reply:
        say(reg_reply, thread_ts=thread_ts)
        return
    # Digest delivery targeting ("send <team> digest here") — needs the raw event
    # for the channel ID. Plain "send digest" (no "here") falls through to broadcast.
    dig_reply = _handle_digest_targeting(text, event)
    if dig_reply:
        say(dig_reply, thread_ts=thread_ts)
        return
    role_msg = _handle_role_command(text, event)
    if role_msg:
        say(role_msg, thread_ts=thread_ts)
        return
    if _check_onboarding(text, event, say, thread_ts):
        return
    channel_id = event.get("channel", "")
    role = audience.role_for(event.get("user", ""), channel_id)
    project = project_registry.for_channel(channel_id)
    say(answer(text, role, project_config=project.config,
               eng=_project_engines(channel_id), actor=event.get("user", "")), thread_ts=thread_ts)


def _ingest_slack_files(event) -> str:
    """No-terminal import: download attached files and run them through the channel-neutral core."""
    import httpx
    from src.ingest import ingest_upload
    files = event.get("files") or []
    teams = _match_teams(event.get("text", ""))
    if not teams:
        return ("Attach the export *and* name the team — e.g. send the file with "
                "the message _\"import for Team Phoenix\"_.")
    team = teams[0]
    token = os.environ["SLACK_BOT_TOKEN"]
    results = []
    for f in files:
        name = f.get("name", "upload")
        url = f.get("url_private_download") or f.get("url_private")
        try:
            r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, follow_redirects=True)
            r.raise_for_status()
            results.append(ingest_upload(name, r.content, team))
        except Exception as e:
            results.append(f"Couldn't import {name}: {e} (the bot may need the `files:read` scope).")
    return "\n".join(results)


@app.event("message")
def handle_dm(event, say):
    if event.get("bot_id"):
        return
    # No-terminal import: a file attached in a DM (or any channel the bot sees)
    if event.get("files"):
        say(_ingest_slack_files(event), thread_ts=event.get("ts"))
        return
    if event.get("channel_type") == "im":
        text = event.get("text", "")
        role_msg = _handle_role_command(text, event)
        if role_msg:
            say(role_msg, thread_ts=event.get("thread_ts"))
            return
        role = audience.role_for(event.get("user", ""), event.get("channel", ""))
        say(answer(text, role, actor=event.get("user", "")), thread_ts=event.get("thread_ts"))


@app.event("member_joined_channel")
def handle_join(event, say):
    # Introduce SyncBot when it's added to a channel (discoverability).
    if BOT_USER_ID and event.get("user") == BOT_USER_ID:
        say(INTRO)


if __name__ == "__main__":
    configure_logging()  # rotating file (data/syncbot.log) + console — the pilot audit trail
    mode = "Claude agent (natural language)" if AGENT else "keyword matching"
    log.info("SyncBot starting (Socket Mode)...")
    log.info("Providers: Jira=%s | Confluence=%s | Slack=live",
             os.getenv('JIRA_PROVIDER', 'local'), os.getenv('CONFLUENCE_PROVIDER', 'local'))
    log.info("Understanding: %s", mode)
    try:
        BOT_USER_ID = app.client.auth_test()["user_id"]
    except Exception as e:
        log.warning("auth_test failed — bot can't identify itself, "
                    "self-intro on channel join disabled: %s", e)

    # Start the proactive weekly-digest scheduler in the background.
    # Registry-aware: runs digests for every registered project + the default.
    digest_scheduler = DigestScheduler(project_registry, "config.yaml")
    digest_scheduler.start()

    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
