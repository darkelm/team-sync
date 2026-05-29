import json
import os
from typing import Optional
from ...core.schemas import FigmaComponent, DriftIssue
from ..base import FigmaProvider


class LocalFigmaProvider(FigmaProvider):
    def __init__(self, teams_dir: str):
        self.teams_dir = teams_dir
        self._components: list[FigmaComponent] = []

    def _load(self) -> list[FigmaComponent]:
        if self._components:
            return self._components
        for entry in os.scandir(self.teams_dir):
            if entry.is_dir():
                path = os.path.join(entry.path, "figma_components.json")
                if os.path.exists(path):
                    with open(path) as f:
                        for item in json.load(f):
                            self._components.append(FigmaComponent(**item))
        return self._components

    def get_components(self, team: Optional[str] = None) -> list[FigmaComponent]:
        components = self._load()
        if team:
            components = [c for c in components if team.lower() in c.team.lower()]
        return components

    def get_library_components(self) -> list[FigmaComponent]:
        return [c for c in self._load() if c.is_library_component]

    def get_components_by_name(self, name: str) -> list[FigmaComponent]:
        name_lower = name.lower()
        return [c for c in self._load() if name_lower in c.name.lower()]

    def get_drift_issues(self) -> list[DriftIssue]:
        from datetime import datetime, timezone
        issues = []
        diverged = [c for c in self._load() if c.diverges_from_library]
        for component in diverged:
            issues.append(DriftIssue(
                id=f"design-drift-{component.id}",
                type="design_drift",
                severity="medium",
                title=f"Design drift: {component.name}",
                description=component.divergence_notes or f"{component.name} diverges from the shared design system library.",
                teams_involved=[component.team],
                components_involved=[component.name],
                detected_at=datetime.now(timezone.utc),
                suggested_action="Sync with Team Nova's design system library or raise a design review.",
            ))
        return issues
