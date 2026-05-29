"""Source-agnostic event/trigger engine — the proactive layer.

Any signal becomes a normalized Event; the router maps it to actions (who to
notify, what to run) by reusing the engines we already have. PRs are just ONE
event type among many — design, research, strategy, delivery, and meeting
events are first-class. The "ears" (a webhook receiver, a nightly snapshot diff,
a manual trigger) are thin adapters that all produce Events and call `route`.

No source is privileged; adding a new trigger source = emit an Event.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from .detector import DriftDetector
from .discovery import ReuseRadar, CollaboratorDiscovery
from .briefing import BriefingGenerator
from .strategy import StrategyLens
from .similarity import jaccard
from ..providers.factory import Providers


@dataclass
class Event:
    type: str                 # dotted: design.library_published, research.study_added, roadmap.date_changed, work.created, code.merged, meeting.transcript_added, decision.logged, calendar.cross_team_sync
    subject: str = ""         # what changed: a component, study topic, ticket title, journey, etc.
    source: str = ""          # figma, dovetail, jira, github, calendar, manual, snapshot…
    team: str = ""            # originating team, if known
    metadata: dict = field(default_factory=dict)


@dataclass
class TriggerAction:
    channel: str
    message: str
    reason: str               # which rule fired (for transparency)


# Catalog of recognized triggers — deliberately spanning the whole org, not just code.
TRIGGER_CATALOG = {
    "design.library_published": "A shared design-system component was published/updated",
    "design.component_changed": "A design component changed",
    "research.study_added": "New user research was published",
    "roadmap.date_changed": "A delivery/roadmap date shifted",
    "delivery.date_changed": "A delivery date shifted",
    "work.created": "A new ticket/epic/initiative was created",
    "decision.logged": "A decision record was written",
    "meeting.transcript_added": "A meeting transcript was ingested",
    "calendar.cross_team_sync": "A cross-team meeting was scheduled",
    "code.merged": "Code touching a shared component was merged",
}


class EventRouter:
    def __init__(self, providers: Providers):
        self.p = providers
        self.detector = DriftDetector(providers)
        self.strategy = StrategyLens(providers)

    # ── affected-audience helpers (the reusable core) ─────────────────────────

    def _channel(self, team_name: str) -> str:
        t = self.p.manifests.get_team(team_name)
        return t.slack_channel if t else ""

    def _consumers_of(self, component: str, exclude: str = "") -> list[str]:
        """Every team that touches a component — owns it, lists it, uses the design
        version, depends on its owner, or shares a journey with it."""
        comp = component.lower()
        teams = set()
        for t in self.p.manifests.get_all_teams():
            names = [c.name.lower() for c in t.components.code + t.components.design]
            if comp in names:
                teams.add(t.team)
            for dep in t.dependencies:
                if any(comp == c.lower() for c in dep.components):
                    teams.add(t.team)
        # Figma "used_by_teams"
        for fc in self.p.figma.get_components_by_name(component):
            teams.update(fc.used_by_teams)
        # Journeys that include this component
        for j in self.strategy.journeys:
            if comp in {c.lower() for c in j.components}:
                teams.update(j.teams)
        teams.discard(exclude)
        return sorted(teams)

    def _teams_in_problem_space(self, topic: str, exclude: str = "") -> list[str]:
        """Teams whose active work is semantically near a topic (for research/new-work events)."""
        out = []
        for t in self.p.manifests.get_all_teams():
            if t.team == exclude:
                continue
            blob = " ".join(
                f"{tk.title} {tk.description}" for tk in self.p.jira.get_tickets(t.team)
                if tk.status.value != "done"
            )
            blob += " " + " ".join(c.name + " " + c.description for c in t.components.design + t.components.code)
            if jaccard(topic, blob) >= 0.04 or any(w in blob.lower() for w in topic.lower().split() if len(w) > 4):
                out.append(t.team)
        return out

    # ── the router ────────────────────────────────────────────────────────────

    def route(self, event: Event) -> list[TriggerAction]:
        t = event.type
        a: list[TriggerAction] = []

        if t in ("design.library_published", "design.component_changed", "code.merged"):
            kind = "Design system" if t.startswith("design") else "A change"
            verb = "published/updated" if t.startswith("design") else "merged"
            for team in self._consumers_of(event.subject, exclude=event.team):
                a.append(TriggerAction(
                    self._channel(team),
                    f"🔔 *{kind} update:* *{event.subject}* was {verb}"
                    + (f" by {event.team}" if event.team else "")
                    + f". Your team uses it — review for impact.",
                    reason=f"{team} consumes {event.subject}",
                ))

        elif t == "research.study_added":
            for team in self._teams_in_problem_space(event.subject, exclude=event.team):
                a.append(TriggerAction(
                    self._channel(team),
                    f"🔬 *New research:* “{event.subject}” may inform your current work — "
                    f"check it before you design/build further.",
                    reason=f"{team} works in this problem space",
                ))

        elif t in ("roadmap.date_changed", "delivery.date_changed"):
            for dep in self.p.manifests.get_dependents(event.team):
                a.append(TriggerAction(
                    self._channel(dep.team),
                    f"📅 *Timeline change:* {event.team} shifted *{event.subject}*. "
                    f"You depend on them — check your plan.",
                    reason=f"{dep.team} depends on {event.team}",
                ))

        elif t in ("work.created", "ticket.created", "epic.created"):
            # Duplicate-work check + collaboration nudge — design/strategy relevant, not code-specific
            matches = ReuseRadar(self.p).search(event.subject, exclude_team=event.team)
            if matches and event.team:
                names = ", ".join(f"{m.name} ({m.owning_team})" for m in matches[:3])
                a.append(TriggerAction(
                    self._channel(event.team),
                    f"♻️ Heads-up before starting *{event.subject}* — similar work already exists: {names}. "
                    f"Worth a look so we don't duplicate.",
                    reason="possible duplicate of existing work",
                ))

        elif t == "calendar.cross_team_sync":
            teams = event.metadata.get("teams", [])
            channel = event.metadata.get("channel") or (self._channel(teams[0]) if teams else "")
            if len(teams) >= 2 and channel:
                a.append(TriggerAction(
                    channel,
                    BriefingGenerator(self.p).cross_team_briefing(teams),
                    reason="auto-briefing for an upcoming cross-team sync",
                ))

        elif t == "decision.logged":
            a.append(TriggerAction("", f"✅ Decision recorded: {event.subject} — now searchable.",
                                   reason="acknowledgement"))

        # Unknown types simply produce no actions (safe default).
        return [x for x in a if x.channel or x.reason == "acknowledgement"]

    def explain(self, event: Event) -> str:
        """Human-readable preview of what an event would trigger (for CLI/demo)."""
        actions = self.route(event)
        if not actions:
            return f"Event `{event.type}` ({event.subject}) → no teams affected."
        lines = [f"⚡ Event `{event.type}` — {event.subject} → {len(actions)} notification(s):\n"]
        for x in actions:
            tgt = x.channel or "(log)"
            lines.append(f"  → {tgt}  [{x.reason}]\n      {x.message.splitlines()[0]}")
        return "\n".join(lines)

    def dispatch(self, event: Event) -> int:
        """Actually post the notifications via the Slack provider. Returns count sent."""
        sent = 0
        for x in self.route(event):
            if x.channel:
                self.p.slack.post_message(x.channel, x.message)
                sent += 1
        return sent
