"""Strategy & Experience lens — coordination at experience altitude.

Reads journey-level consistency (not just per-component drift) and measures live
signals against the org's experience principles. Same disciplined pattern as the
rest: deterministic, AI-optional, channel-neutral.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
import yaml

from ..core.schemas import Journey, ExperiencePrinciple, Outcome, ResearchInsight
from .detector import DriftDetector
from .similarity import tokenize
from ..providers.factory import Providers


def _org_dir(config: str) -> str:
    with open(config) as f:
        cfg = yaml.safe_load(f)
    teams_dir = cfg.get("data", {}).get("teams_dir", "./data/synthetic/teams")
    return os.path.dirname(teams_dir)


@dataclass
class JourneyHealth:
    name: str
    status: str           # green | amber | red
    description: str
    owner: str
    north_star: str
    teams: list[str]
    inconsistencies: list[str] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)
    ownership_gaps: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return {"green": "🟢 Coherent", "amber": "🟡 Needs attention", "red": "🔴 Fragmented"}[self.status]


class StrategyLens:
    def __init__(self, providers: Providers, config: str = "config.yaml"):
        self.p = providers
        self.detector = DriftDetector(providers)
        org = _org_dir(config)
        self.journeys: list[Journey] = self._load(os.path.join(org, "journeys.yaml"), "journeys", Journey)
        self.principles: list[ExperiencePrinciple] = self._load(
            os.path.join(org, "experience_principles.yaml"), "principles", ExperiencePrinciple)
        self.outcome_list: list[Outcome] = self._load(
            os.path.join(org, "outcomes.yaml"), "outcomes", Outcome)
        self.insights: list[ResearchInsight] = self._load(
            os.path.join(org, "research_insights.yaml"), "insights", ResearchInsight)

    def _load(self, path: str, key: str, cls):
        if not os.path.exists(path):
            return []
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return [cls(**item) for item in data.get(key, [])]

    def get_journey(self, name: str) -> Journey | None:
        n = name.lower()
        return next((j for j in self.journeys if n in j.name.lower() or j.name.lower() in n), None)

    # ── journey consistency ───────────────────────────────────────────────────

    def assess_journey(self, name: str) -> JourneyHealth | None:
        j = self.get_journey(name)
        if not j:
            return None
        comp_set = {c.lower() for c in j.components}
        team_set = set(j.teams)

        # Inconsistencies: design/code drift on components that make up this journey
        inconsistencies = []
        for issue in self.detector.run_all():
            if issue.type in ("design_drift", "code_drift") and any(
                c.lower() in comp_set for c in issue.components_involved
            ):
                inconsistencies.append(f"{issue.title} — {issue.description}")

        # Collisions: predicted conflicts among the teams that share this journey
        collisions = []
        for c in self.detector.predict_conflicts():
            if team_set & set(c.teams_involved):
                collisions.append(c.title)

        # Ownership gaps: components in the journey that no team claims
        ownership_gaps = [c for c in j.components if self.p.manifests.find_component_owner(c) is None]

        if inconsistencies or ownership_gaps:
            status = "red" if len(inconsistencies) >= 2 else "amber"
        elif collisions:
            status = "amber"
        else:
            status = "green"

        return JourneyHealth(
            name=j.name, status=status, description=j.description, owner=j.owner,
            north_star=j.north_star, teams=j.teams,
            inconsistencies=inconsistencies, collisions=collisions, ownership_gaps=ownership_gaps,
        )

    def format_journeys(self) -> str:
        if not self.journeys:
            return "No journeys defined yet. Add them in journeys.yaml."
        lines = ["*🗺️ Experience journeys*\n"]
        for j in self.journeys:
            h = self.assess_journey(j.name)
            lines.append(f"{h.label}  *{j.name}* — {j.description}")
        lines.append("\n_Ask `@syncbot how's the onboarding journey?` for the detail on any one._")
        return "\n".join(lines)

    # ── principle adherence ───────────────────────────────────────────────────

    def principle_report(self) -> str:
        """Map live signals (inconsistencies, undocumented decisions, collisions) to principles."""
        if not self.principles:
            return "No experience principles defined yet. Add them in experience_principles.yaml."
        issues = self.detector.run_all()
        preds = self.detector.predict_conflicts()
        # text blobs for matching
        signals = [(f"{i.title} {i.description} {i.type}", i.title) for i in issues]
        signals += [(f"{c.title} {c.description} collision", c.title) for c in preds]

        lines = ["*🎯 Experience principles — are we upholding them?*\n"]
        for p in self.principles:
            kw = set()
            for k in p.keywords:
                kw |= tokenize(k)
            violations = []
            seen = set()
            for blob, title in signals:
                if tokenize(blob) & kw and title not in seen:
                    seen.add(title)
                    violations.append(title)
            mark = "🟢" if not violations else ("🟡" if len(violations) <= 2 else "🔴")
            lines.append(f"{mark} *{p.name}* — {p.statement}")
            if violations:
                lines.append(f"    _{len(violations)} signal(s) working against this:_ " + "; ".join(violations[:3]))
        return "\n".join(lines)

    # ── outcomes ─────────────────────────────────────────────────────────────

    def _known_team_names(self) -> set[str]:
        return {t.team for t in self.p.manifests.get_all_teams()}

    def _team_goals_for_outcome(self, outcome: Outcome) -> list[str]:
        """Return the team quarter-goals that keyword-match this outcome."""
        probe = tokenize(f"{outcome.name} {outcome.metric} {' '.join(outcome.related_journeys)}")
        matching_goals: list[str] = []
        for team in self.p.manifests.get_all_teams():
            for goal in team.quarter_goals:
                if tokenize(goal) & probe:
                    matching_goals.append(f"[{team.team}] {goal}")
        return matching_goals

    def _work_laddering_to_outcome(self, outcome: Outcome) -> list[str]:
        """Return open ticket titles that relate to this outcome (keyword match)."""
        probe = tokenize(f"{outcome.name} {outcome.metric} {' '.join(outcome.related_journeys)}")
        matching: list[str] = []
        for team in self.p.manifests.get_all_teams():
            for ticket in self.p.jira.get_tickets(team.team):
                t_tokens = tokenize(f"{ticket.title} {ticket.description}")
                if t_tokens & probe and ticket.status.value not in ("done",):
                    matching.append(f"[{ticket.id}] {ticket.title} ({team.team})")
        return matching[:5]

    def assess_outcome(self, name: str) -> dict | None:
        """Return a structured assessment of a single outcome."""
        n = name.lower()
        outcome = next((o for o in self.outcome_list if n in o.name.lower() or o.name.lower() in n), None)
        if not outcome:
            return None

        known_teams = self._known_team_names()
        owner_is_team = outcome.owner in known_teams
        # Check whether the owner team has the outcome's journey in their goals/components
        supporting_work = self._work_laddering_to_outcome(outcome)

        flags: list[str] = []
        if not owner_is_team:
            flags.append(f"Owner '{outcome.owner}' is not a known team — may be an individual without a team manifest.")
        if not supporting_work:
            flags.append("No open tickets found laddering up to this outcome — check whether work is tracked.")

        relevant_insights = self.insights_for(outcome.name)

        return {
            "id": outcome.id,
            "name": outcome.name,
            "metric": outcome.metric,
            "target": outcome.target,
            "owner": outcome.owner,
            "related_objectives": outcome.related_objectives,
            "related_journeys": outcome.related_journeys,
            "supporting_work": supporting_work,
            "flags": flags,
            "relevant_insights": [{"id": ri.id, "title": ri.title} for ri in relevant_insights],
        }

    def outcomes(self) -> str:
        """Format all outcomes as a Slack-ready report."""
        if not self.outcome_list:
            return "No outcomes defined yet. Add them in outcomes.yaml."
        lines = ["*📊 Outcomes — are we hitting our north stars?*\n"]
        known_teams = self._known_team_names()
        for o in self.outcome_list:
            owner_flag = "" if o.owner in known_teams else " ⚠️ _owner not a known team_"
            lines.append(f"• *{o.name}* ({o.id})")
            lines.append(f"    Metric: {o.metric}")
            lines.append(f"    Target: _{o.target}_")
            lines.append(f"    Owner: {o.owner}{owner_flag}")
            if o.related_journeys:
                lines.append(f"    Journeys: {', '.join(o.related_journeys)}")
            supporting = self._work_laddering_to_outcome(o)
            if supporting:
                lines.append(f"    Supporting work: {supporting[0]}" + (f" (+{len(supporting)-1} more)" if len(supporting) > 1 else ""))
            else:
                lines.append("    ⚠️ _No open tickets laddering to this outcome_")
            lines.append("")
        lines.append("_Ask `@syncbot outcome status for <name>` for a detailed assessment._")
        return "\n".join(lines)

    def format_outcome(self, assessment: dict) -> str:
        """Format a single outcome assessment as Slack text."""
        lines = [f"*📊 Outcome: {assessment['name']}* ({assessment['id']})\n"]
        lines.append(f"*Metric:* {assessment['metric']}")
        lines.append(f"*Target:* _{assessment['target']}_")
        lines.append(f"*Owner:* {assessment['owner']}")
        if assessment["related_objectives"]:
            lines.append(f"*Related objectives:* {', '.join(assessment['related_objectives'])}")
        if assessment["related_journeys"]:
            lines.append(f"*Related journeys:* {', '.join(assessment['related_journeys'])}")
        lines.append("")
        if assessment["supporting_work"]:
            lines.append("*Work laddering to this outcome:*")
            for w in assessment["supporting_work"]:
                lines.append(f"  • {w}")
        else:
            lines.append("*⚠️ No open tickets found laddering to this outcome.*")
        if assessment["flags"]:
            lines.append("")
            lines.append("*Flags:*")
            for f in assessment["flags"]:
                lines.append(f"  ⚠️ {f}")
        if assessment["relevant_insights"]:
            lines.append("")
            lines.append("*Research informing this outcome:*")
            for ri in assessment["relevant_insights"]:
                lines.append(f"  • [{ri['id']}] {ri['title']}")
        return "\n".join(lines)

    # ── research insights ─────────────────────────────────────────────────────

    def insights_for(self, topic: str) -> list[ResearchInsight]:
        """Return insights relevant to a topic or journey name (keyword match)."""
        probe = tokenize(topic)
        results: list[tuple[float, ResearchInsight]] = []
        for ri in self.insights:
            blob = f"{ri.title} {ri.summary} {' '.join(ri.themes)} {' '.join(ri.journeys)}"
            score = len(tokenize(blob) & probe)
            if score > 0:
                results.append((score, ri))
        results.sort(key=lambda x: -x[0])
        return [ri for _, ri in results]

    def format_insights(self, topic: str) -> str:
        """Format research insights for a topic as Slack text."""
        found = self.insights_for(topic)
        if not found:
            return f"No research insights found for '{topic}'. Add them in research_insights.yaml."
        lines = [f"*🔬 Research insights — '{topic}'*\n"]
        for ri in found:
            lines.append(f"*[{ri.id}] {ri.title}*")
            lines.append(f"  _{ri.summary[:200].rstrip()}{'…' if len(ri.summary) > 200 else ''}_")
            lines.append(f"  Source: {ri.source} ({ri.date})")
            if ri.url:
                lines.append(f"  <{ri.url}|Full report>")
            lines.append("")
        contradictions = self.contradictions()
        relevant_contradictions = [c for c in contradictions if any(t in tokenize(topic) for t in tokenize(" ".join(c["shared_themes"])))]
        if relevant_contradictions:
            lines.append("*⚠️ Contradictory findings on this topic:*")
            for c in relevant_contradictions:
                lines.append(f"  • [{c['insight_a']}] vs [{c['insight_b']}] — shared themes: {', '.join(c['shared_themes'])}")
                lines.append(f"    _{c['note']}_")
        return "\n".join(lines)

    def contradictions(self) -> list[dict]:
        """Flag pairs of insights with opposing findings on the same themes.

        Heuristic: two insights share ≥2 themes AND one contains a positive/
        negative signal word while the other contains the opposing signal.
        Honest that this is approximate — a Claude agent would do better.
        """
        POSITIVE = {"increase", "higher", "improve", "lift", "better", "boost", "engagement", "retention", "drive"}
        NEGATIVE = {"drop", "abandon", "lower", "friction", "worse", "decrease", "erode", "off", "barrier"}

        def polarity(text: str) -> str:
            tokens = tokenize(text)
            pos_hits = len(tokens & POSITIVE)
            neg_hits = len(tokens & NEGATIVE)
            if pos_hits == 0 and neg_hits == 0:
                return "neutral"
            # Dominant polarity wins; tie → mixed
            if pos_hits > neg_hits:
                return "positive"
            if neg_hits > pos_hits:
                return "negative"
            return "mixed"

        results: list[dict] = []
        seen: set[frozenset] = set()
        for i, a in enumerate(self.insights):
            for b in self.insights[i + 1:]:
                themes_a = set(a.themes)
                themes_b = set(b.themes)
                shared = themes_a & themes_b
                if len(shared) < 2:
                    continue
                pol_a = polarity(f"{a.title} {a.summary}")
                pol_b = polarity(f"{b.title} {b.summary}")
                if {pol_a, pol_b} == {"positive", "negative"}:
                    key = frozenset([a.id, b.id])
                    if key not in seen:
                        seen.add(key)
                        results.append({
                            "insight_a": a.id,
                            "insight_b": b.id,
                            "title_a": a.title,
                            "title_b": b.title,
                            "shared_themes": sorted(shared),
                            "note": (
                                f"[{a.id}] reports a {pol_a} signal; [{b.id}] reports a {pol_b} signal "
                                f"on the same themes. Recommend triangulating before deciding."
                            ),
                        })
        return results

    def format_contradictions(self) -> str:
        """Format contradiction report as Slack text."""
        contradictions = self.contradictions()
        if not contradictions:
            return "No contradictory research findings detected across the insight library."
        lines = ["*⚠️ Contradictory research findings*\n",
                 "_These insight pairs report opposing signals on the same themes. "
                 "Triangulate before deciding._\n"]
        for c in contradictions:
            lines.append(f"• *[{c['insight_a']}]* {c['title_a']}")
            lines.append(f"  vs *[{c['insight_b']}]* {c['title_b']}")
            lines.append(f"  Shared themes: {', '.join(c['shared_themes'])}")
            lines.append(f"  _{c['note']}_")
            lines.append("")
        return "\n".join(lines)

    def format_journey(self, h: JourneyHealth) -> str:
        """Format a journey health report, optionally annotating with informing insights."""
        lines = [f"*{h.label} — {h.name} journey*", h.description, ""]
        lines.append(f"*North star:* {h.north_star}")
        lines.append(f"*Experience owner:* {h.owner}")
        lines.append(f"*Teams that shape it:* {', '.join(h.teams)}")
        lines.append("")
        if h.inconsistencies:
            lines.append(f"*⚠️ Inconsistencies across the journey ({len(h.inconsistencies)})*")
            lines += [f"  • {i}" for i in h.inconsistencies[:4]]
            lines.append("")
        if h.collisions:
            lines.append("*Cross-team collisions to coordinate*")
            lines += [f"  • {c}" for c in h.collisions[:3]]
            lines.append("")
        if h.ownership_gaps:
            lines.append(f"*Ownership gaps:* {', '.join(h.ownership_gaps)} — no team clearly owns these.")
            lines.append("")
        # Wire in informing research insights
        informing = self.insights_for(h.name)
        if informing:
            lines.append(f"*Research informing this journey ({len(informing)} insight(s)):*")
            for ri in informing[:3]:
                lines.append(f"  • [{ri.id}] {ri.title}")
            lines.append("")
        if h.status == "green":
            lines.append("_This experience is coherent across teams right now._")
        return "\n".join(lines)
