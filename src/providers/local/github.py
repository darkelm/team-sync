import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from ...core.schemas import PullRequest, PRStatus
from ..base import GitHubProvider


class LocalGitHubProvider(GitHubProvider):
    def __init__(self, teams_dir: str):
        self.teams_dir = teams_dir
        self._prs: list[PullRequest] = []

    def _load(self) -> list[PullRequest]:
        if self._prs:
            return self._prs
        for entry in os.scandir(self.teams_dir):
            if entry.is_dir():
                path = os.path.join(entry.path, "pull_requests.json")
                if os.path.exists(path):
                    with open(path) as f:
                        for item in json.load(f):
                            self._prs.append(PullRequest(**item))
        return self._prs

    def get_pull_requests(self, team: Optional[str] = None, status: Optional[str] = None) -> list[PullRequest]:
        prs = self._load()
        if team:
            prs = [p for p in prs if team.lower() in p.team.lower()]
        if status:
            prs = [p for p in prs if p.status.value == status]
        return prs

    def get_recent_prs(self, days: int = 7) -> list[PullRequest]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return [
            p for p in self._load()
            if p.status == PRStatus.merged and p.merged_at and p.merged_at >= cutoff
        ]

    def get_prs_touching_component(self, component: str) -> list[PullRequest]:
        comp_lower = component.lower()
        return [
            p for p in self._load()
            if any(comp_lower in c.lower() for c in p.components_touched)
        ]
