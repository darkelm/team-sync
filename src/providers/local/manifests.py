import os
import yaml
from typing import Optional
from ...core.schemas import TeamManifest
from ..base import ManifestProvider


class LocalManifestProvider(ManifestProvider):
    def __init__(self, teams_dir: str):
        self.teams_dir = teams_dir
        self._cache: dict[str, TeamManifest] = {}

    def _load_all(self) -> dict[str, TeamManifest]:
        if self._cache:
            return self._cache
        for entry in os.scandir(self.teams_dir):
            if entry.is_dir():
                manifest_path = os.path.join(entry.path, "team.yaml")
                if os.path.exists(manifest_path):
                    with open(manifest_path) as f:
                        data = yaml.safe_load(f)
                    manifest = TeamManifest(**data)
                    self._cache[manifest.team.lower()] = manifest
        return self._cache

    def get_all_teams(self) -> list[TeamManifest]:
        return list(self._load_all().values())

    def get_team(self, team_name: str) -> Optional[TeamManifest]:
        teams = self._load_all()
        return teams.get(team_name.lower()) or next(
            (t for t in teams.values() if team_name.lower() in t.team.lower()), None
        )

    def find_component_owner(self, component_name: str) -> Optional[TeamManifest]:
        name_lower = component_name.lower()
        for team in self._load_all().values():
            for c in team.components.code:
                if name_lower in c.name.lower():
                    return team
            for c in team.components.design:
                if name_lower in c.name.lower():
                    return team
        return None

    def get_dependents(self, team_name: str) -> list[TeamManifest]:
        name_lower = team_name.lower()
        return [
            t for t in self._load_all().values()
            if any(d.team.lower() == name_lower for d in t.dependencies)
        ]
