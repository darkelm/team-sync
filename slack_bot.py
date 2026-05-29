#!/usr/bin/env python3
"""
SyncBot Slack bot — listens for @syncbot mentions and responds with real data.
Uses Socket Mode so no public URL or ngrok needed.

Usage:
    python3 slack_bot.py
"""
import os
import sys
import re
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, ".")

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.providers.factory import Providers
from src.agent.detector import DriftDetector
from src.agent.digest import DigestGenerator
from src.agent.briefing import BriefingGenerator
from src.agent.scheduler import DigestScheduler
from src.agent.discovery import CollaboratorDiscovery, ReuseRadar
from src.agent.alignment import AlignmentChecker
from src.agent.findability import FindabilityLocator

app = App(token=os.environ["SLACK_BOT_TOKEN"])
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


def answer(text: str, role: str = "ic") -> str:
    """Answer a question, framed for the audience's role (data is identical; framing differs)."""
    reply = None
    if AGENT is not None:
        try:
            AGENT.reset()
            hint = agent_hint(role)
            reply = AGENT.ask(f"{hint}\n\n{text}" if hint else text)
        except Exception as e:
            print(f"[agent] error, falling back to keywords: {e}", flush=True)
    if not reply:
        reply = handle_query(text)
    # De-jargon for non-technical audiences (keyword path; the agent already got the hint).
    if is_non_technical(role) and AGENT is None:
        reply = plainify(reply)
    return reply


def _match_teams(text: str) -> list[str]:
    """Teams referenced in text — full/short name or fuzzy (tolerates typos)."""
    from src.agent.fuzzy import resolve_teams
    return resolve_teams(providers, text)


def strip_mention(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


def _load_meeting_notes() -> list[dict]:
    import glob, json, yaml
    with open("config.yaml") as f:
        teams_dir = yaml.safe_load(f).get("data", {}).get("teams_dir", "./data/synthetic/teams")
    notes = []
    for path in glob.glob(os.path.join(teams_dir, "*", "meeting_notes.json")):
        try:
            with open(path) as f:
                notes.extend(json.load(f))
        except (OSError, ValueError):
            continue
    return notes


def handle_query(text: str) -> str:
    q = text.lower()

    # Status / health — what's connected, how fresh, AI on?
    if q.strip() in ("status", "health") or any(w in q for w in ["are you connected", "what's connected", "whats connected", "syncbot status", "system status"]):
        teams = providers.manifests.get_all_teams()
        verified = sum(1 for t in teams if t.last_verified)
        prov = lambda k: os.getenv(f"{k}_PROVIDER", "local")
        lines = [
            "*🩺 SyncBot status*",
            f"• Teams tracked: *{len(teams)}* ({verified} verified)",
            f"• Understanding: *{'Claude agent (natural language)' if AGENT else 'keyword matching'}*",
            "• Data sources: "
            + ", ".join(f"{k} _{prov(k.upper())}_" for k in ["jira", "confluence", "github", "figma"]),
            f"• Slack: _live_",
            "",
            "_Ask `@syncbot help` for what I can do._",
        ]
        return "\n".join(lines)

    # Leadership rollup — portfolio + per-team health (Phase 7, leadership-framed)
    if any(w in q for w in ["portfolio", "exec summary", "exec status", "how are we doing",
                            "overall status", "everything on track", "leadership view"]):
        return health.format_portfolio()
    if (("how" in q and "doing" in q) or "health of" in q or "how is" in q and "doing" in q
            or "on track" in q):
        teams = _match_teams(text)
        if teams:
            h = health.assess(teams[0])
            return health.format_team(h) if h else f"Couldn't find that team."
        return "Which team? e.g. `@syncbot how's Team Phoenix doing?` — or `@syncbot portfolio status` for everyone."

    # Notification preferences — pause/resume/severity (Phase 4 tuning)
    if any(w in q for w in ["mute", "pause digest", "snooze", "resume digest", "unmute",
                            "only ping", "only alert", "only notify", "digest severity", "set severity"]):
        teams = _match_teams(text)
        if not teams:
            return "Which team's notifications? e.g. `@syncbot mute digests for Team Horizon` or `@syncbot only alert Team Atlas on high`"
        target = teams[0]
        prefs = digest_gen.prefs
        if any(w in q for w in ["resume", "unmute"]):
            return prefs.resume(target)
        if any(w in q for w in ["mute", "pause", "snooze"]):
            return prefs.pause(target)
        for level in ("critical", "high", "medium", "low"):
            if level in q:
                return prefs.set_severity(target, level)
        return "Tell me a level (low/medium/high/critical), e.g. `only alert Team Atlas on high`."

    # Action items from ingested meetings
    if any(w in q for w in ["action item", "action items", "my actions", "what do i owe", "follow up", "follow-up", "to-do from", "todos from"]):
        notes = _load_meeting_notes()
        if not notes:
            return "No meeting notes ingested yet. Import a transcript to capture action items."
        teams = _match_teams(text)
        lines = ["*📌 Action items from recent meetings*\n"]
        count = 0
        for n in notes:
            if teams and n.get("team") not in teams:
                continue
            items = n.get("action_items", [])
            if not items:
                continue
            lines.append(f"_{n.get('title')} ({n.get('team')})_")
            for a in items:
                who = a.get("owner") or "unassigned"
                due = f" (due {a['due']})" if a.get("due") else ""
                lines.append(f"  • *{who}*: {a.get('task')}{due}")
                count += 1
            lines.append("")
        return "\n".join(lines) if count else "No action items found in ingested meetings."

    # Findability — where do I find X?
    if any(w in q for w in ["where do i find", "where is", "where can i find", "where are", "where's", "looking for", "find the"]):
        import re as _re
        query = _re.sub(r".*(where do i find|where can i find|where is|where are|where's|looking for|find the)\b",
                        "", text, flags=_re.I).strip(" ?.the")
        query = query or text
        results = locator.find(query)
        if not results:
            return f"Couldn't locate anything for “{query}”. It may not be registered yet — ask the owning team to add it."
        lines = [f"*📍 Where to find “{query}”:*\n"]
        for r in results[:6]:
            lines.append(f"• *{r.label}:* <{r.url}|{r.name}> — owned by {r.team}")
        return "\n".join(lines)

    # Who owns X
    if any(w in q for w in ["who owns", "who is responsible", "who do i talk to about", "owner of"]):
        from src.agent.fuzzy import component_owner
        words = text.split()
        component = words[-1].strip("?.,")
        team, suggestions = component_owner(providers, component)
        if team:
            return (
                f"*{component}* is owned by *{team.team}*\n"
                f"Owner: {team.owner.name} ({team.owner.slack_handle})\n"
                f"Channel: {team.slack_channel}"
            )
        if suggestions:
            opts = ", ".join(f"*{c}* ({tm})" for c, tm in suggestions)
            return f"No exact match for `{component}`. Did you mean: {opts}?"
        return f"No team owns `{component}` yet. Ask in #general, or the data owner may need to add it to a team manifest."

    # When does X ship
    if any(w in q for w in ["when does", "when is", "shipping", "deliver", "deliverables"]):
        matched = _match_teams(text)
        for team_name in matched:
            team = providers.manifests.get_team(team_name)
            if team:
                tickets = providers.jira.get_upcoming_deliverables(team.team)
                if not tickets:
                    return f"No upcoming deliverables with due dates found for *{team.team}*."
                lines = [f"*Upcoming deliverables — {team.team}*"]
                for t in sorted(tickets, key=lambda x: x.due_date or "9999"):
                    lines.append(f"• `{t.id}` {t.title} — due {t.due_date} [{t.status.value}]")
                return "\n".join(lines)
        return "Which team? Try: `@syncbot when does Team Atlas ship`"

    # Decision log search
    if any(w in q for w in ["decision", "decided", "why did", "why was", "rationale"]):
        # extract search term — words after "about" or "for" or just the last few words
        search = re.sub(r".*(about|for|on|regarding)\s+", "", text, flags=re.I).strip("?.,")
        pages = providers.confluence.search_pages(search)
        decision_pages = [p for p in pages if p.decision_log]
        if not decision_pages:
            return f"No decision logs found for `{search}`. It may not have been formally documented yet."
        lines = []
        for p in decision_pages[:3]:
            dl = p.decision_log
            lines.append(
                f"*{dl.title}*\n"
                f"Decision: {dl.decision}\n"
                f"Why: {dl.rationale}\n"
                f"By: {', '.join(dl.decided_by)} on {dl.date}\n"
                f"<{p.url}|View in Confluence>"
            )
        return "\n\n".join(lines)

    # Collaborator discovery — who should be talking
    if any(w in q for w in ["who should i talk", "who should we talk", "collaborat", "discover", "who else is working", "connect me", "missing"]):
        suggestions = discovery.find_suggestions()
        unlinked = [s for s in suggestions if not s.already_linked]
        if not suggestions:
            return "No related-work connections detected right now."
        lines = ["*🔗 Collaboration opportunities*\n"]
        if unlinked:
            lines.append("*Teams doing related work but NOT connected:*")
            for s in unlinked[:5]:
                lines.append(f"• *{s.team_a}* ↔ *{s.team_b}*\n    {s.evidence[0]}")
        linked = [s for s in suggestions if s.already_linked]
        if linked:
            lines.append("\n_Already connected (keep in sync):_ "
                         + ", ".join(f"{s.team_a}↔{s.team_b}" for s in linked[:4]))
        return "\n".join(lines)

    # Reuse radar — has someone already built this?
    if any(w in q for w in ["already built", "already exist", "reuse", "anyone built", "has anyone", "similar to", "already solved", "already designed"]):
        # crude payload extraction: text after common lead-ins
        import re as _re
        desc = _re.sub(r".*(built|exist[s]?|designed|solved|similar to|reuse|anyone|has anyone)\b", "", text, flags=_re.I).strip(" ?.")
        desc = desc or text
        matches = reuse_radar.search(desc)
        if not matches:
            return f"Nothing similar found for “{desc}”. Looks net-new — good to proceed."
        lines = [f"*♻️ Possible existing work for “{desc}”:*\n"]
        for m in matches[:6]:
            kind = {"component": "🧩 component", "design": "🎨 design", "ticket": "🎫 ticket"}.get(m.kind, m.kind)
            lines.append(f"• {kind} *{m.name}* — owned by {m.owning_team} "
                         f"(match: {', '.join(m.overlap[:4])})")
        lines.append("\n_Check with the owning team before building from scratch._")
        return "\n".join(lines)

    # Strategic alignment
    if any(w in q for w in ["alignment", "aligned", "objective", "strategy", "strategic", "okr", "company goal", "ladder"]):
        report = alignment.run()
        lines = ["*🎯 Strategic alignment check*\n"]
        if report.overlaps:
            lines.append("*Objectives multiple teams are pursuing (coordinate):*")
            for title, oid, teams in report.overlaps:
                lines.append(f"• {title} → {', '.join(teams)}")
            lines.append("")
        if report.orphans:
            lines.append("*⚠️ Goals not linked to any company objective:*")
            for o in report.orphans[:6]:
                lines.append(f"• [{o.team}] {o.goal}")
            lines.append("")
        coverage = len(report.linked)
        total = coverage + len(report.orphans)
        pct = round(100 * coverage / total) if total else 100
        lines.append(f"_{coverage}/{total} team goals ({pct}%) ladder up to a company objective._")
        return "\n".join(lines)

    # Cross-team meeting briefing
    if any(w in q for w in ["prep", "brief", "briefing", "meeting", "sync with", "agenda"]):
        team_names = _match_teams(text)
        if len(team_names) >= 2:
            return briefing_gen.cross_team_briefing(team_names)
        return ("For a cross-team briefing, name at least two teams. "
                "Try: `@syncbot prep me for a sync with Team Atlas and Team Forge`")

    # Predicted / future conflicts
    if any(w in q for w in ["predict", "predicted", "future conflict", "upcoming conflict", "collision", "before they"]):
        predictions = detector.predict_conflicts()
        if not predictions:
            return "No conflicts predicted across planned work. 🎉"
        lines = [f"*🔮 {len(predictions)} predicted conflict(s):*\n"]
        for c in predictions:
            emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}.get(c.severity.value, "•")
            lines.append(f"{emoji} *{c.title}*\n   Teams: {', '.join(c.teams_involved)}\n"
                         f"   Tickets: {', '.join(c.tickets_involved)}\n   → {c.suggested_action}")
        return "\n".join(lines)

    # Post digests to all team channels on demand
    if any(w in q for w in ["post digest", "send digest", "digest all", "post all", "broadcast"]):
        teams = providers.manifests.get_all_teams()
        digest_gen.post_all_digests(force=True)
        return f"Posted weekly digests to {len(teams)} team channels."

    # Scan / conflicts
    if any(w in q for w in ["scan", "conflict", "issues", "what's broken", "whats broken", "problems"]):
        issues = detector.run_all()
        if not issues:
            return "No issues detected across all teams."
        lines = [f"*Found {len(issues)} issues:*\n"]
        for issue in issues[:8]:
            emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}.get(issue.severity.value, "•")
            lines.append(f"{emoji} *{issue.title}*\n   Teams: {', '.join(issue.teams_involved)}\n   → {issue.suggested_action}")
        if len(issues) > 8:
            lines.append(f"\n_...and {len(issues) - 8} more. Run `syncbot scan` in the terminal for the full list._")
        return "\n".join(lines)

    # Design sync / drift
    if any(w in q for w in ["design sync", "figma", "drift", "in sync", "design system"]):
        for team in providers.manifests.get_all_teams():
            if team.team.lower() in q:
                components = providers.figma.get_components(team.team)
                drifted = [c for c in components if c.diverges_from_library]
                if not drifted:
                    return f"*{team.team}*'s Figma components look in sync with the design system. ✓"
                lines = [f"*{team.team}* has {len(drifted)} component(s) out of sync:\n"]
                for c in drifted:
                    lines.append(f"• *{c.name}* — {c.divergence_notes}")
                return "\n".join(lines)
        # No team specified — check all
        all_components = providers.figma.get_components()
        drifted = [c for c in all_components if c.diverges_from_library]
        if not drifted:
            return "All Figma components are in sync with the design system. ✓"
        lines = [f"*{len(drifted)} component(s) drifted from the design system:*\n"]
        for c in drifted:
            lines.append(f"• *{c.name}* ({c.team}) — {c.divergence_notes}")
        return "\n".join(lines)

    # Get me up to speed / onboarding
    if any(w in q for w in ["up to speed", "onboard", "new to", "tell me about", "context on"]):
        for team in providers.manifests.get_all_teams():
            if team.team.lower() in q:
                t = team
                tickets = [tk for tk in providers.jira.get_tickets(t.team) if tk.status.value != "done"][:5]
                deps = [d.team for d in t.dependencies]
                dependents = providers.manifests.get_dependents(t.team)

                lines = [
                    f"*{t.team}* — {t.description}\n",
                    f"*Owner:* {t.owner.name} ({t.owner.slack_handle})",
                    f"*Channel:* {t.slack_channel}",
                    f"*Depends on:* {', '.join(deps) if deps else 'none'}",
                    f"*Teams that depend on them:* {', '.join(d.team for d in dependents) if dependents else 'none'}",
                    f"\n*This quarter:*",
                ]
                for goal in t.quarter_goals:
                    lines.append(f"• {goal}")
                if tickets:
                    lines.append(f"\n*Open tickets ({len(tickets)}):*")
                    for tk in tickets:
                        lines.append(f"• `{tk.id}` {tk.title} [{tk.priority.value}]")
                if t.figma_files:
                    lines.append(f"\n*Figma:*")
                    for f in t.figma_files:
                        lines.append(f"• <{f.url}|{f.name}>")
                return "\n".join(lines)
        return "Which team? Try: `@syncbot get me up to speed on Team Phoenix`"

    # Digest
    if any(w in q for w in ["digest", "weekly", "summary", "this week"]):
        for team in providers.manifests.get_all_teams():
            if team.team.lower() in q:
                d = digest_gen.generate_for_team(team.team)
                return digest_gen.format_slack_message(d)
        return "Which team's digest? Try: `@syncbot digest for Team Horizon`"

    # Dependency graph
    if any(w in q for w in ["depend", "dependency", "dependencies"]):
        for team in providers.manifests.get_all_teams():
            if team.team.lower() in q:
                t = team
                deps = [f"• *{d.team}* — {d.reason}" for d in t.dependencies]
                dependents = [f"• *{d.team}*" for d in providers.manifests.get_dependents(t.team)]
                lines = [f"*{t.team} dependencies:*\n"]
                lines.append("*Depends on:*")
                lines.extend(deps if deps else ["• none"])
                lines.append("\n*Depended on by:*")
                lines.extend(dependents if dependents else ["• none"])
                return "\n".join(lines)

    # Help / fallback
    return (
        "*SyncBot commands:*\n\n"
        "• `@syncbot who owns <component>` — find component owner\n"
        "• `@syncbot where do I find <thing>` — locate research, assets, files, docs\n"
        "• `@syncbot action items for <team>` — open actions from ingested meetings\n"
        "• `@syncbot when does <team> ship` — upcoming deliverables\n"
        "• `@syncbot what was decided about <topic>` — search decision logs\n"
        "• `@syncbot scan for conflicts` — current drift and conflict report\n"
        "• `@syncbot predict conflicts` — forecast collisions in planned work\n"
        "• `@syncbot who should I talk to` — discover teams doing related work\n"
        "• `@syncbot has anyone built <thing>` — reuse radar before you start\n"
        "• `@syncbot check alignment` — goals laddering up to company objectives\n"
        "• `@syncbot prep me for a sync with <team> and <team>` — meeting briefing\n"
        "• `@syncbot get me up to speed on <team>` — team briefing\n"
        "• `@syncbot is <team>'s design in sync` — Figma drift check\n"
        "• `@syncbot digest for <team>` — weekly digest preview\n"
        "• `@syncbot post digests` — send digests to all team channels now\n"
        "• `@syncbot mute digests for <team>` / `resume digests for <team>` — pause control\n"
        "• `@syncbot only alert <team> on high` — set digest severity threshold\n"
        "• `@syncbot dependencies for <team>` — dependency map"
    )


INTRO = (
    "👋 *Hi, I'm SyncBot* — I help keep teams in sync.\n"
    "Ask me anything in plain language, for example:\n"
    "• _who owns the auth component?_\n"
    "• _when does Team Atlas ship?_\n"
    "• _who should I be talking to?_\n"
    "• _has anyone already built a notification bell?_\n"
    "• _where do I find the user research?_\n"
    "• _prep me for a sync with Team Atlas and Team Forge_\n"
    "Type `@syncbot help` anytime for the full list."
)

BOT_USER_ID = None


def _handle_role_command(text: str, event) -> str | None:
    """If the user is setting their role, record it and return a confirmation."""
    role = parse_role_command(text)
    if role and event.get("user"):
        return audience.set_user(event["user"], role)
    return None


@app.event("app_mention")
def handle_mention(event, say):
    # Reply in-thread to keep channels tidy.
    thread_ts = event.get("thread_ts") or event.get("ts")
    text = strip_mention(event.get("text", ""))
    if not text:
        say(handle_query("help"), thread_ts=thread_ts)
        return
    role_msg = _handle_role_command(text, event)
    if role_msg:
        say(role_msg, thread_ts=thread_ts)
        return
    role = audience.role_for(event.get("user", ""), event.get("channel", ""))
    say(answer(text, role), thread_ts=thread_ts)


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
        say(answer(text, role), thread_ts=event.get("thread_ts"))


@app.event("member_joined_channel")
def handle_join(event, say):
    # Introduce SyncBot when it's added to a channel (discoverability).
    if BOT_USER_ID and event.get("user") == BOT_USER_ID:
        say(INTRO)


if __name__ == "__main__":
    print("SyncBot starting (Socket Mode)...")
    mode = "Claude agent (natural language)" if AGENT else "keyword matching"
    print(f"Providers: Jira={os.getenv('JIRA_PROVIDER','local')} | Confluence={os.getenv('CONFLUENCE_PROVIDER','local')} | Slack=live")
    print(f"Understanding: {mode}")
    try:
        BOT_USER_ID = app.client.auth_test()["user_id"]
    except Exception:
        pass

    # Start the proactive weekly-digest scheduler in the background
    digest_scheduler = DigestScheduler(providers, "config.yaml")
    digest_scheduler.start()

    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
