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
from .discovery import ReuseRadar
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
    # Strategy signals — detected from natural conversation in meetings
    "strategy.metric_revealed": "A stakeholder revealed what success is being measured against",
    "strategy.pivot": "A team changed direction — dependent pairs need to know",
    "strategy.differentiation_risk": "The experience isn't feeling distinctive — affects all pairs",
    "strategy.concept_breakthrough": "A creative direction worth sharing across the initiative",
    "strategy.duplicate_work": "Two teams are exploring the same space without knowing it",
}


# Map the team-sync event vocabulary onto membrane change-kinds (contract §10, Q4).
# The membrane rule shape matches `kind` as a plain string, so these can be the oracle's
# token kinds (added/removed/changed/renamed) — keeping policies portable — or a team
# vocabulary later. Default for an unmapped type is "changed" (a value-level edit).
_EVENT_KIND: dict[str, str] = {
    "design.library_published": "changed",
    "design.component_changed": "changed",
    "code.merged": "changed",
    "work.created": "added",
    "research.study_added": "added",
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

    def reach(self, component: str, exclude_team: str = "") -> int:
        """Graded consequence signal for the membrane: the number of OTHER teams that
        touch a component (contract §10, reach = |consumers|). Reuses the existing
        `_consumers_of` (the reach numerator, already written) and returns its
        cardinality. This is what the membrane's `blast_radius` is fed."""
        return len(self._consumers_of(component, exclude=exclude_team))

    # ── lane mapping (membrane integration, additive — see membrane.py) ──────────
    #
    # These build a membrane RouteContext for ONE event and call membrane.route(),
    # returning the (single) RoutingDecision. They DO NOT change `route()` above —
    # the existing notify-all behavior is untouched. The orchestrator wires the lane
    # decision into the live event path (e.g. gate dispatch on lane, persist provenance).

    def _tier_for_subject(self, subject: str) -> str:
        """Look up the governance tier of the component named by an event subject.

        Reads the component's `tier` (CodeComponent/DesignComponent) off the team
        manifests the router can reach. Returns "raw" when the subject is empty,
        the component isn't found, or it carries no tier. Robust by design — a bad
        subject, an unknown team, or a missing manifest must never raise here
        (tier inference is best-effort; the safe default is the least-autonomous
        bucket)."""
        if not subject:
            return "raw"
        target = subject.lower()
        try:
            for team in self.p.manifests.get_all_teams():
                for comp in team.components.code + team.components.design:
                    if comp.name.lower() == target:
                        return (getattr(comp, "tier", None) or "raw").lower()
        except Exception as e:
            # Best-effort: any manifest/provider failure degrades to the safe
            # default rather than breaking routing.
            print(f"[events] _tier_for_subject({subject!r}) failed: {e}", flush=True)
        return "raw"

    def _event_route_item(self, event: "Event"):
        """Build the membrane RouteItem for an event. `key` = the component subject;
        `path` carries a tier head (so `tier_of` can bucket it); `kind` maps the
        event type to a change kind.

        Tier resolution: an explicit `event.metadata["tier"]` ALWAYS wins; when
        absent, the tier is derived from the actual component (the manifest
        `tier` for the component named by `event.subject`), falling back to "raw"
        for unknown components. All overridable via event.metadata ("tier", "kind")."""
        from . import membrane
        meta_tier = event.metadata.get("tier")
        tier = (meta_tier or self._tier_for_subject(event.subject)).lower()
        # The membrane reads tier off the path head; encode the event's consequence
        # tier as that head so tier_of() resolves it (contract §7/§10).
        path = f"{tier}/{event.subject}" if event.subject else tier
        kind = event.metadata.get("kind") or _EVENT_KIND.get(event.type, "changed")
        return membrane.RouteItem(key=event.subject, path=path, kind=kind, mode=event.metadata.get("mode"))

    def route_lane(self, event: "Event", policy=None, *, proposed_by=None, now=None):
        """Map an event to a membrane lane decision (thin adapter, contract §6).

        Computes reach via `self.reach(...)`, assembles a RouteContext (P1 floor from
        `event.metadata['p1']`, novelty from `event.metadata['novel']`, confidence left
        ABSENT — team-sync wires none, which is NEUTRAL by design), and calls
        `membrane.route()`. Returns the single RoutingDecision for this event.

        `policy` defaults to the conservative `default_policy()` (everything → review)
        — autonomy must be granted by an explicit policy the orchestrator passes in.
        """
        from . import membrane
        if policy is None:
            policy = membrane.default_policy()
        item = self._event_route_item(event)
        # The reach resolver's trust signal feeds confidence (the adaptation layer,
        # contract §10): an UNTRUSTED resolution (the webhook's Files-API fail-safe
        # sets metadata.resolution == "review" when it couldn't resolve the touched
        # component) is PRESENT-low confidence, which vetoes auto and routes to review
        # WITH honest provenance. "resolved" or absent stays NEUTRAL (no veto) so a
        # cleanly-resolved low-reach change can still earn auto under a policy.
        confidence = {item.key: 0.0} if event.metadata.get("resolution") == "review" else None
        ctx = membrane.RouteContext(
            blast_radius={item.key: self.reach(event.subject, exclude_team=event.team)},
            p1_keys=[item.key] if event.metadata.get("p1") else [],
            confidence=confidence,
            novel_keys=[item.key] if event.metadata.get("novel") else None,
        )
        decisions = membrane.route([item], ctx, policy, proposed_by=proposed_by, now=now)
        return decisions[0]

    def _teams_on_same_journey(self, team_name: str) -> list[str]:
        """All teams that share at least one journey with this team."""
        try:
            from .strategy import StrategyLens
            lens = StrategyLens(self.p)
            result = set()
            for j in lens.journeys:
                if team_name in j.teams:
                    result.update(j.teams)
            result.discard(team_name)
            return sorted(result)
        except Exception as e:
            # Missing strategy files degrade to [] inside StrategyLens itself,
            # so reaching here is a real failure that drops same-journey routing.
            print(f"[events] _teams_on_same_journey: StrategyLens unavailable: {e}", flush=True)
            return []

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
            # Rich metadata from Figma webhook (version notes, journeys, principles)
            version_notes = event.metadata.get("version_notes", "")
            journeys = event.metadata.get("journeys_affected", [])
            principles = event.metadata.get("principles_relevant", [])
            originator = event.team or "the owning team"

            for team in self._consumers_of(event.subject, exclude=event.team):
                # Design-language message: decision-first, not artifact-first
                if version_notes:
                    # Designer wrote a note → this is a real decision, give full context
                    msg = f"🎨 *Design direction update — {event.subject}*\n"
                    msg += f"{originator} published a change with this note:\n"
                    msg += f"_{version_notes}_\n"
                    if journeys:
                        msg += f"\nThis affects: *{', '.join(journeys)}*"
                    if principles:
                        msg += f"\nPrinciples in play: {', '.join(principles)}"
                    msg += "\n\nWorth a quick look before your next design session."
                else:
                    # No notes → minimal, non-intrusive
                    journey_ctx = f" (part of the {', '.join(journeys)} journey)" if journeys else ""
                    msg = (f"ℹ️ *{event.subject}*{journey_ctx} was updated by {originator}. "
                           f"No release notes — check with them if it affects your direction.")
                a.append(TriggerAction(self._channel(team), msg,
                                       reason=f"{team} works on a journey that uses {event.subject}"))

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

        # ── Strategy signals (broadcast across the initiative) ────────────────
        # These route to ALL teams, not just adjacent ones — the whole initiative
        # needs to know when the direction shifts, a metric is revealed, or a
        # creative concept is worth sharing. Design language throughout.

        elif t == "strategy.metric_revealed":
            msg = (f"📊 *Client metric just revealed in {event.team or 'a meeting'}:*\n"
                   f"_{event.subject}_\n\n"
                   f"This changes what success looks like. Worth checking whether your "
                   f"current design direction serves this outcome.")
            for team in self.p.manifests.get_all_teams():
                if team.team != event.team:
                    a.append(TriggerAction(self._channel(team.team), msg,
                                           reason="initiative-wide metric alignment"))

        elif t == "strategy.pivot":
            msg = (f"🔄 *{event.team or 'A team'} just changed direction:*\n"
                   f"_{event.subject}_\n\n"
                   f"If your work connects to theirs, it's worth a quick sync.")
            for dep in self.p.manifests.get_dependents(event.team):
                a.append(TriggerAction(self._channel(dep.team), msg,
                                       reason=f"{dep.team} is connected to {event.team}"))
            # Also alert all teams on shared journeys
            for team in self._teams_on_same_journey(event.team):
                if team != event.team:
                    a.append(TriggerAction(self._channel(team), msg,
                                           reason="shares a journey with the pivoting team"))

        elif t == "strategy.differentiation_risk":
            msg = (f"⚠️ *Differentiation concern raised — {event.team or 'a team'} flagged this:*\n"
                   f"_{event.subject}_\n\n"
                   f"Worth pausing as an initiative to ask: what makes each experience "
                   f"feel genuinely distinctive? This concern may apply across all pairs.")
            for team in self.p.manifests.get_all_teams():
                if team.team != event.team:
                    a.append(TriggerAction(self._channel(team.team), msg,
                                           reason="initiative-wide differentiation signal"))

        elif t == "strategy.concept_breakthrough":
            msg = (f"💡 *Creative direction from {event.team or 'a team'} worth sharing:*\n"
                   f"_{event.subject}_\n\n"
                   f"This kind of conceptual framing can be generative for other journeys too. "
                   f"Take a look before your next ideation session.")
            for team in self.p.manifests.get_all_teams():
                if team.team != event.team:
                    a.append(TriggerAction(self._channel(team.team), msg,
                                           reason="concept worth sharing across the initiative"))

        elif t == "strategy.duplicate_work":
            # Find who else is working on the same thing and tell both sides
            similar = ReuseRadar(self.p).search(event.subject, exclude_team=event.team)
            if similar and event.team:
                other_teams = sorted({m.owning_team for m in similar if m.owning_team != event.team})
                team_list = ", ".join(other_teams) if other_teams else "another team"
                msg_originator = (f"♻️ *Heads-up:* {team_list} appears to be exploring something "
                                  f"similar to your current direction ({event.subject}). "
                                  f"Worth comparing notes before you go too far.")
                a.append(TriggerAction(self._channel(event.team), msg_originator,
                                       reason="duplicate-work detection"))
                for other in other_teams:
                    msg_other = (f"♻️ *Heads-up:* {event.team} just flagged they're exploring "
                                 f"something similar to your work ({event.subject}). "
                                 f"Worth comparing notes.")
                    a.append(TriggerAction(self._channel(other), msg_other,
                                           reason="duplicate-work detection"))

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

    def dispatch(self, event: Event, policy=None) -> int:
        """Post notifications, gated by the membrane lane; record provenance.

        Every event is routed to a lane (`route_lane`) and the decision is recorded
        append-only ("who/what decided, and why"). The lane then gates the live ping:
        `auto`/`digest` do NOT ping (autonomy / batched recap); `review`/`blocked`/
        `propose` notify as before. With the conservative `default_policy` (everything
        → review) behaviour is unchanged — autonomy only comes from a human-granted
        toggle policy passed in as `policy`.
        """
        from . import membrane
        from .provenance import ProvenanceStore
        decision = self.route_lane(event, policy)
        try:
            ProvenanceStore().append(decision.provenance)
        except OSError as e:
            print(f"[events] provenance append failed: {e}", flush=True)
        if decision.lane in (membrane.Lane.AUTO, membrane.Lane.DIGEST):
            return 0  # autonomy / batched recap — no live ping
        sent = 0
        for x in self.route(event):
            if x.channel:
                self.p.slack.post_message(x.channel, x.message)
                sent += 1
        return sent
