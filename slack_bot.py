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

app = App(token=os.environ["SLACK_BOT_TOKEN"])
providers = Providers("config.yaml")
detector = DriftDetector(providers)
digest_gen = DigestGenerator(providers)
briefing_gen = BriefingGenerator(providers)


def _match_teams(text: str) -> list[str]:
    """Return the names of all known teams mentioned in the text, in order of appearance."""
    q = text.lower()
    found = [(q.index(t.team.lower()), t.team) for t in providers.manifests.get_all_teams()
             if t.team.lower() in q]
    return [name for _, name in sorted(found)]


def strip_mention(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


def handle_query(text: str) -> str:
    q = text.lower()

    # Who owns X
    if any(w in q for w in ["who owns", "who is responsible", "who do i talk to about", "owner of"]):
        words = text.split()
        # grab the last meaningful word as the component name
        component = words[-1].strip("?.,")
        team = providers.manifests.find_component_owner(component)
        if team:
            return (
                f"*{component}* is owned by *{team.team}*\n"
                f"Owner: {team.owner.name} ({team.owner.slack_handle})\n"
                f"Channel: {team.slack_channel}"
            )
        return f"No team claims ownership of `{component}`. Check the manifests or ask in #general."

    # When does X ship
    if any(w in q for w in ["when does", "when is", "shipping", "deliver", "deliverables"]):
        for team in providers.manifests.get_all_teams():
            if team.team.lower() in q:
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
        digest_gen.post_all_digests()
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
        "• `@syncbot when does <team> ship` — upcoming deliverables\n"
        "• `@syncbot what was decided about <topic>` — search decision logs\n"
        "• `@syncbot scan for conflicts` — current drift and conflict report\n"
        "• `@syncbot predict conflicts` — forecast collisions in planned work\n"
        "• `@syncbot prep me for a sync with <team> and <team>` — meeting briefing\n"
        "• `@syncbot get me up to speed on <team>` — team briefing\n"
        "• `@syncbot is <team>'s design in sync` — Figma drift check\n"
        "• `@syncbot digest for <team>` — weekly digest preview\n"
        "• `@syncbot post digests` — send digests to all team channels now\n"
        "• `@syncbot dependencies for <team>` — dependency map"
    )


@app.event("app_mention")
def handle_mention(event, say):
    text = strip_mention(event.get("text", ""))
    if not text:
        say(handle_query("help"))
        return
    say(handle_query(text))


@app.event("message")
def handle_dm(event, say):
    if event.get("channel_type") == "im" and not event.get("bot_id"):
        text = event.get("text", "")
        say(handle_query(text))


if __name__ == "__main__":
    print("SyncBot starting (Socket Mode)...")
    print(f"Providers: Jira={os.getenv('JIRA_PROVIDER','local')} | Confluence={os.getenv('CONFLUENCE_PROVIDER','local')} | Slack=live")

    # Start the proactive weekly-digest scheduler in the background
    digest_scheduler = DigestScheduler(providers, "config.yaml")
    digest_scheduler.start()

    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
