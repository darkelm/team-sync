#!/usr/bin/env python3
"""SyncBot keyword router.

The deterministic, no-LLM fallback brain: maps a natural-ish phrase to a reply
by scanning for keywords and running the matching engine. handle_query() runs
against whatever engine bundle it's handed (default = config.yaml's engines);
channel handlers pass a per-project bundle so keyword queries stay scoped.

router.py imports its shared state from bootstrap (providers, the default engine
bundle, the project registry, the agent flag) and the rest from src.*. It must
NOT import slack_bot.py, so there is no import cycle.
"""
import os
import re
import json
from collections import Counter
from datetime import datetime, timezone

from bootstrap import (
    AGENT,
    _DEFAULT_ENGINES,
    project_registry,
    providers,
)
from src.agent.events import Event

# Queries that fall through to the keyword fallback are appended here, so the
# team's misses become a backlog of phrasings/capabilities worth adding.
UNMATCHED_LOG = "data/unmatched_queries.jsonl"


def _log_unmatched(text: str, project: str = "default") -> None:
    """Record a query the keyword router couldn't answer (best-effort)."""
    try:
        os.makedirs(os.path.dirname(UNMATCHED_LOG) or ".", exist_ok=True)
        with open(UNMATCHED_LOG, "a") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "text": text.strip(),
                "project": project,
            }) + "\n")
    except OSError as e:
        print(f"[router] couldn't log unmatched query: {e}", flush=True)


def _format_unmatched(limit: int = 12) -> str:
    """Rank logged unmatched queries by frequency — the add-a-command backlog."""
    if not os.path.exists(UNMATCHED_LOG):
        return ("No unmatched questions logged yet. 🎉 "
                "When I can't answer something, it lands here as a backlog.")
    entries = []
    try:
        with open(UNMATCHED_LOG) as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    except OSError as e:
        return f"Couldn't read the unmatched-query log: {e}"
    texts = [e["text"] for e in entries if e.get("text")]
    if not texts:
        return "No unmatched questions logged yet. 🎉"
    counts = Counter(t.lower() for t in texts)
    lines = [f"*📋 Top unmatched questions* — {len(texts)} total, your add-a-command backlog:\n"]
    for text, n in counts.most_common(limit):
        lines.append(f"• {f'*{n}×* ' if n > 1 else ''}“{text}”")
    lines.append("\n_Add a trigger in `router.py` (or turn on the AI key) to handle the common ones._")
    return "\n".join(lines)


def _match_teams(text: str) -> list[str]:
    """Teams referenced in text — full/short name or fuzzy (tolerates typos)."""
    from src.agent.fuzzy import resolve_teams
    return resolve_teams(providers, text)


def _load_meeting_notes(providers_=None) -> list[dict]:
    import glob, json
    # Scope to the given project's teams dir; fall back to config.yaml for
    # callers without a providers bundle (e.g. the default path).
    teams_dir = getattr(providers_, "teams_dir", None)
    if not teams_dir:
        import yaml
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


# Role-tailored highlight shown above the full help list (4.3).
_ROLE_HELP = {
    "designer": "*For design work:* `is <team>'s design in sync` · `has anyone built <thing>` · `where do I find <thing>` · `what was decided about <topic>`",
    "pm": "*For delivery:* `when does <team> ship` · `get me up to speed on <team>` · `predict conflicts` · `dependencies for <team>`",
    "lead": "*Leadership view:* `portfolio status` · `how's <team> doing` · `check alignment` · `journeys`",
    "dev": "*For build work:* `who owns <component>` · `scan for conflicts` · `dependencies for <team>` · `what was decided about <topic>`",
}

HELP_BODY = (
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
    "• `@syncbot digest for <team>` — weekly digest preview (inline, no posting)\n"
    "• `@syncbot send <team>'s digest here` — deliver that team's digest to this channel\n"
    "• `@syncbot stop sending digests here` — undo the above\n"
    "• `@syncbot where do digests go` — list digest delivery channels\n"
    "• `@syncbot post digests` — send digests to all configured channels now\n"
    "• `@syncbot mute digests for <team>` / `resume digests for <team>` — pause control\n"
    "• `@syncbot only alert <team> on high` — set digest severity threshold\n"
    "• `@syncbot dependencies for <team>` — dependency map"
)


def handle_query(text: str, eng: dict | None = None, role: str = "ic") -> str:
    # Unpack the engine bundle into locals so the body below is project-scoped
    # without per-reference edits; defaults to config.yaml's engines.
    eng = eng or _DEFAULT_ENGINES
    providers = eng["providers"]
    detector = eng["detector"]
    digest_gen = eng["digest_gen"]
    briefing_gen = eng["briefing_gen"]
    discovery = eng["discovery"]
    reuse_radar = eng["reuse_radar"]
    alignment = eng["alignment"]
    locator = eng["locator"]
    health = eng["health"]
    strategy = eng["strategy"]
    router = eng["router"]
    q = text.lower()

    # Multi-project management
    if q.strip() in ("projects", "project status") or "which project" in q or "register project" in q:
        if "register" in q:
            return ("To register a new project:\n"
                    "```python figma_webhook_setup.py``` or ask your SyncBot admin to run:\n"
                    "`python -c \"from src.projects import ProjectRegistry; "
                    "ProjectRegistry().register('Name', 'config-name.yaml', channel_patterns=['pattern'])\"``")
        return project_registry.summary()

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

    # Proactive trigger preview — "what happens if X changes" (source-agnostic)
    if q.startswith("simulate") or "what happens if" in q or "what would happen if" in q:
        # Infer the event type from the phrasing; subject = a known component if present.
        comp = None
        for t in providers.manifests.get_all_teams():
            for c in t.components.code + t.components.design:
                if c.name.lower() in q:
                    comp = c.name
                    break
            if comp:
                break
        if "research" in q or "study" in q:
            ev = Event(type="research.study_added", subject=re.sub(r".*(research|study)\b", "", text, flags=re.I).strip(" :?.") or "new research")
        elif comp and any(w in q for w in ["design", "library", "publish", "component", "updates the", "design system"]):
            ev = Event(type="design.library_published", subject=comp, team="Team Nova")
        elif re.search(r"\b(slips?|delay|delayed|roadmap|timeline|late|due date|ships? late|misses)\b", q):
            teams = _match_teams(text)
            ev = Event(type="roadmap.date_changed", subject=comp or "a deliverable", team=teams[0] if teams else "")
        elif comp:
            ev = Event(type="design.library_published", subject=comp, team="Team Nova")
        else:
            return ("Try: `@syncbot what happens if the design system updates DataTable` "
                    "or `@syncbot what happens if Team Atlas slips the gateway migration`")
        preview = router.explain(ev)
        return preview + "\n\n_This is the proactive engine — any signal (design publish, new research, date slip, new work…) can trigger it, not just code._"

    # Outcomes — "are we hitting our outcomes", "north star", "outcome status"
    if (any(w in q for w in ["outcome", "outcomes", "north star", "north stars", "hitting our"])
            and "research" not in q):
        import re as _re
        # Check if a specific outcome name was given
        named_outcome = None
        for o in strategy.outcome_list:
            if o.name.lower() in q or o.id.lower() in q:
                named_outcome = o.name
                break
        if named_outcome:
            assessment = strategy.assess_outcome(named_outcome)
            return strategy.format_outcome(assessment) if assessment else f"Outcome '{named_outcome}' not found."
        return strategy.outcomes()

    # Research insights — "research on X", "insights about X", "what do we know about X"
    # Careful: keep phrases explicit so we don't collide with the "research" keyword in
    # the simulate branch (which checks "research in q or study in q" inside a separate
    # if-block that only fires on "simulate"/"what happens if" queries).
    if (any(w in q for w in ["research on", "insights about", "insight on", "what do we know about",
                              "what does the research say", "any research on", "research insight",
                              "contradictions in research", "contradictory research",
                              "conflicting research", "conflicting findings"])):
        import re as _re
        # Extract topic: strip the trigger phrase, use what remains
        topic = _re.sub(
            r".*(research on|insights about|insight on|what do we know about|"
            r"what does the research say(?: about)?|any research on|research insight|"
            r"contradictions in research|contradictory research|"
            r"conflicting research|conflicting findings)\s*",
            "", text, flags=_re.I
        ).strip(" ?.,")
        if not topic or topic == text:
            # Show contradictions if that's what they asked for
            if any(w in q for w in ["contradiction", "contradictory", "conflicting"]):
                return strategy.format_contradictions()
            return strategy.format_insights("")  # list all
        return strategy.format_insights(topic)

    # Experience strategy — journeys + principles (above components/screens)
    if "principle" in q or "experience vision" in q or "design vision" in q or ("aligned" in q and "vision" in q):
        return strategy.principle_report()
    if "journey" in q or "journeys" in q or "end to end" in q or "end-to-end" in q or "experience" in q:
        # specific journey if named, else list
        named = next((j.name for j in strategy.journeys if j.name.lower() in q), None)
        if named:
            return strategy.format_journey(strategy.assess_journey(named))
        if "journeys" in q or "list" in q or "all" in q or "experiences" in q:
            return strategy.format_journeys()
        return strategy.format_journeys()

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
        notes = _load_meeting_notes(providers)
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
    if any(w in q for w in ["decision", "decided", "decide", "why did", "why was", "why we", "rationale"]):
        # extract search phrase, then also try individual significant words so
        # "what did we decide about the design system v3 tokens" still matches.
        phrase = re.sub(r".*(about|for|on|regarding)\s+", "", text, flags=re.I).strip("?.,")
        stop = {"the", "and", "why", "what", "did", "we", "a", "an", "of", "to", "about", "decide", "decided", "is", "for"}
        terms = [w.strip("?.,") for w in (phrase or text).split() if w.lower() not in stop and len(w) > 2]
        seen_ids, decision_pages = set(), []
        for term in [phrase] + terms:
            for p in providers.confluence.search_pages(term):
                if p.decision_log and p.id not in seen_ids:
                    seen_ids.add(p.id)
                    decision_pages.append(p)
        if not decision_pages:
            return f"No decision logs found for `{phrase or text}`. It may not have been formally documented yet."
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

    # Reuse radar — has someone already built/designed this?
    if any(w in q for w in ["already built", "already exist", "reuse", "anyone built", "has anyone",
                            "similar to", "already solved", "already designed", "already designed somewhere",
                            "does this exist", "is there already"]):
        # Match against the whole message (subject often precedes the trigger,
        # e.g. "is the notification bell already designed?"). Strip filler words.
        import re as _re
        desc = _re.sub(r"\b(is|the|a|an|already|built|designed|exists?|somewhere|anyone|has|does|this|there|"
                       r"reuse|solved|similar|to|do|we|have)\b", " ", text, flags=_re.I)
        desc = _re.sub(r"\s+", " ", desc).strip(" ?.") or text
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

    # Cross-team meeting briefing (only acts on 2+ teams; otherwise falls through
    # so e.g. "is Team X's design in sync" reaches the design-sync handler)
    if any(w in q for w in ["prep", "brief", "briefing", "meeting", "agenda"]) or "sync with" in q:
        team_names = _match_teams(text)
        if len(team_names) >= 2:
            return briefing_gen.cross_team_briefing(team_names)
        if any(w in q for w in ["prep", "brief", "briefing", "agenda"]):
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

    # Where are digests delivered? (discoverability for the targeting feature)
    if any(p in q for p in ["where do digests", "where are digests", "where do the digests",
                            "digest channel", "digest target", "where digests go", "digests go"]):
        targets = digest_gen.prefs.digest_targets()
        if not targets:
            return ("No digest delivery channels are set yet. In any channel, say "
                    "`@syncbot send <team>'s digest here` and I'll deliver there from then on. "
                    "Until then each team falls back to its configured channel.")
        lines = ["*📬 Digest delivery targets*\n"]
        for team, ch in targets.items():
            lines.append(f"• *{team}* → {ch}")
        lines.append("\n_Change with_ `@syncbot send <team>'s digest here` _·_ "
                     "`@syncbot stop sending digests here`.")
        return "\n".join(lines)

    # Post digests to all team channels on demand
    if any(w in q for w in ["post digest", "send digest", "digest all", "post all", "broadcast"]):
        res = digest_gen.post_all_digests(force=True)
        sent, failed, paused = res["sent"], res["failed"], res["paused"]
        lines = []
        if sent:
            lines.append(f"✅ Posted to {len(sent)} channel(s): "
                         + ", ".join(ch for _, ch in sent))
        if failed:
            lines.append(f"⚠️ Couldn't deliver to {len(failed)} channel(s): "
                         + ", ".join(f"{ch} (not found, or I'm not in it)" for _, ch in failed))
        if paused:
            lines.append(f"⏸️ Skipped (digests paused): {', '.join(paused)}")
        if not lines:
            return "No teams are configured to receive digests yet."
        if failed and not sent:
            lines.append("\n_Fix it from Slack — no config editing:_ go to the channel you want "
                         "and say `@syncbot send <team>'s digest here` (or `send all digests here`). "
                         "_Tip:_ `@syncbot digest for <team>` previews a digest inline without posting.")
        return "\n".join(lines)

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

    # Backlog: what the team asked that I couldn't answer.
    if any(p in q for p in ["unmatched", "missed question", "what did people ask",
                            "what couldn't you answer", "what could you not answer",
                            "what did i miss"]):
        return _format_unmatched()

    # Help / fallback — honest about keyword mode + which mode you're in,
    # and lead with the commands most relevant to the reader's role.
    explicit_help = any(w in q for w in ["help", "what can you do", "what can i ask", "commands", "menu"])
    mode = "🧠 AI (natural language)" if AGENT else "⌨️ keyword matching"
    highlight = _ROLE_HELP.get(role)
    if explicit_help:
        head = f"*SyncBot commands*  ·  mode: {mode}\n"
        head += f"{highlight}\n\n_Full list:_\n" if highlight else "\n"
        return head + HELP_BODY
    # Genuinely unmatched — log it for the backlog, then be honest.
    proj = eng.get("project")
    _log_unmatched(text, proj.name if proj else "default")
    return (f"I didn't catch that. I'm in *{mode}* mode, so I match specific phrasings — "
            f"here's what I understand:\n\n" + HELP_BODY)
