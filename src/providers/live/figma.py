import os
import httpx
from datetime import datetime, timezone
from typing import Optional
from ...core.schemas import FigmaComponent, DesignStatus, DriftIssue
from ..base import FigmaProvider


class LiveFigmaProvider(FigmaProvider):
    def __init__(self):
        self.token = os.environ["FIGMA_ACCESS_TOKEN"]
        self.headers = {"X-Figma-Token": self.token}

    def _get(self, path: str) -> dict:
        r = httpx.get(f"https://api.figma.com/v1{path}", headers=self.headers)
        r.raise_for_status()
        return r.json()

    def _to_component(self, item: dict, file_id: str, file_name: str, team: str) -> FigmaComponent:
        return FigmaComponent(
            id=item["key"],
            name=item["name"],
            file_id=file_id,
            file_name=file_name,
            team=team,
            description=item.get("description", ""),
            status=DesignStatus.dev_ready,
            last_modified=datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")),
            is_library_component=True,
        )

    def get_components(self, team: Optional[str] = None) -> list[FigmaComponent]:
        # Requires file IDs from manifests — wired up via orchestrator
        return []

    def get_library_components(self) -> list[FigmaComponent]:
        return []

    def get_components_by_name(self, name: str) -> list[FigmaComponent]:
        return []

    def get_drift_issues(self) -> list[DriftIssue]:
        return []
