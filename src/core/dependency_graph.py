from dataclasses import dataclass, field
from ..core.schemas import TeamManifest


@dataclass
class DependencyGraph:
    teams: dict[str, TeamManifest] = field(default_factory=dict)

    def build(self, manifests: list[TeamManifest]) -> None:
        self.teams = {m.team: m for m in manifests}

    def dependents_of(self, team_name: str) -> list[TeamManifest]:
        return [
            t for t in self.teams.values()
            if any(d.team == team_name for d in t.dependencies)
        ]

    def dependencies_of(self, team_name: str) -> list[TeamManifest]:
        team = self.teams.get(team_name)
        if not team:
            return []
        result = []
        for dep in team.dependencies:
            if dep.team in self.teams:
                result.append(self.teams[dep.team])
        return result

    def find_orphaned_dependencies(self) -> list[tuple[str, str]]:
        """Returns (team, missing_dep_team) pairs where a dependency references a non-existent team."""
        issues = []
        for team in self.teams.values():
            for dep in team.dependencies:
                if dep.team not in self.teams:
                    issues.append((team.team, dep.team))
        return issues

    def find_shared_components(self) -> dict[str, list[str]]:
        """Returns components owned by multiple teams — potential drift candidates."""
        component_owners: dict[str, list[str]] = {}
        for team in self.teams.values():
            for c in team.components.code + team.components.design:
                component_owners.setdefault(c.name, []).append(team.team)
        return {k: v for k, v in component_owners.items() if len(v) > 1}

    def to_dict(self) -> dict:
        return {
            "teams": list(self.teams.keys()),
            "edges": [
                {"from": t.team, "to": d.team, "reason": d.reason, "components": d.components}
                for t in self.teams.values()
                for d in t.dependencies
            ],
            "shared_components": self.find_shared_components(),
            "orphaned_dependencies": self.find_orphaned_dependencies(),
        }
