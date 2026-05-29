"""Strategy & Experience lens — coordination at experience altitude.

Reads journey-level consistency (not just per-component drift) and measures live
signals against the org's experience principles. Same disciplined pattern as the
rest: deterministic, AI-optional, channel-neutral.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
import yaml

from ..core.schemas import Journey, ExperiencePrinciple
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

    def format_journey(self, h: JourneyHealth) -> str:
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
        if h.status == "green":
            lines.append("_This experience is coherent across teams right now._")
        return "\n".join(lines)

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
