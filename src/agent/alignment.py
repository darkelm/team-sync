"""Strategic Alignment Checker.

Ladders team goals up to company objectives, then flags:
- orphan goals: team goals not clearly linked to any objective (the 65% problem)
- overlapping goals: multiple teams independently pursuing the same objective
  in a way that may warrant coordination
"""
import os
from dataclasses import dataclass, field
from typing import Optional
import yaml
from .similarity import tokenize
from ..providers.factory import Providers


@dataclass
class GoalLink:
    team: str
    goal: str
    objective_id: Optional[str]
    objective_title: Optional[str]
    confidence: float


@dataclass
class AlignmentReport:
    linked: list[GoalLink] = field(default_factory=list)
    orphans: list[GoalLink] = field(default_factory=list)
    objective_coverage: dict[str, list[str]] = field(default_factory=dict)  # obj_id -> [teams]
    overlaps: list[tuple[str, str, list[str]]] = field(default_factory=list)  # (obj_title, obj_id, teams)


class AlignmentChecker:
    def __init__(self, providers: Providers, objectives_path: str = "data/synthetic/org_objectives.yaml"):
        self.p = providers
        self.objectives = []
        if os.path.exists(objectives_path):
            with open(objectives_path) as f:
                self.objectives = yaml.safe_load(f).get("objectives", [])

    def _match_goal(self, goal: str) -> tuple[Optional[dict], float]:
        goal_tokens = tokenize(goal)
        best, best_score = None, 0.0
        for obj in self.objectives:
            kw_tokens = set()
            for kw in obj.get("keywords", []):
                kw_tokens |= tokenize(kw)
            if not kw_tokens:
                continue
            hits = len(goal_tokens & kw_tokens)
            score = hits / max(1, len(kw_tokens) ** 0.5)  # reward hits, soft-normalize
            if hits and score > best_score:
                best, best_score = obj, score
        return best, best_score

    def run(self) -> AlignmentReport:
        report = AlignmentReport()
        for team in self.p.manifests.get_all_teams():
            for goal in team.quarter_goals:
                obj, score = self._match_goal(goal)
                link = GoalLink(
                    team=team.team, goal=goal,
                    objective_id=obj["id"] if obj else None,
                    objective_title=obj["title"] if obj else None,
                    confidence=round(score, 2),
                )
                if obj:
                    report.linked.append(link)
                    report.objective_coverage.setdefault(obj["id"], [])
                    if team.team not in report.objective_coverage[obj["id"]]:
                        report.objective_coverage[obj["id"]].append(team.team)
                else:
                    report.orphans.append(link)

        # Overlaps: an objective pursued by 2+ teams
        for obj in self.objectives:
            teams = report.objective_coverage.get(obj["id"], [])
            if len(teams) > 1:
                report.overlaps.append((obj["title"], obj["id"], teams))

        return report
