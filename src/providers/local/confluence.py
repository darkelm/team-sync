import json
import os
from typing import Optional
from ...core.schemas import ConfluencePage
from ..base import ConfluenceProvider


class LocalConfluenceProvider(ConfluenceProvider):
    def __init__(self, teams_dir: str):
        self.teams_dir = teams_dir
        self._pages: list[ConfluencePage] = []

    def _load(self) -> list[ConfluencePage]:
        if self._pages:
            return self._pages
        for entry in os.scandir(self.teams_dir):
            if entry.is_dir():
                # Curated docs + decisions captured from meeting transcripts
                for fname in ("confluence_pages.json", "meeting_decisions.json"):
                    path = os.path.join(entry.path, fname)
                    if os.path.exists(path):
                        with open(path) as f:
                            for item in json.load(f):
                                self._pages.append(ConfluencePage(**item))
        return self._pages

    def get_pages(self, space: Optional[str] = None, team: Optional[str] = None) -> list[ConfluencePage]:
        pages = self._load()
        if space:
            pages = [p for p in pages if p.space == space]
        if team:
            pages = [p for p in pages if team.lower() in p.team.lower()]
        return pages

    def search_pages(self, query: str, team: Optional[str] = None) -> list[ConfluencePage]:
        q = query.lower()
        pages = self._load()
        if team:
            pages = [p for p in pages if team.lower() in p.team.lower()]
        return [
            p for p in pages
            if q in p.title.lower() or q in p.content_summary.lower()
            or any(q in tag.lower() for tag in p.tags)
        ]

    def get_decision_logs(self, team: Optional[str] = None, component: Optional[str] = None) -> list[ConfluencePage]:
        pages = [p for p in self._load() if p.decision_log is not None]
        if team:
            pages = [p for p in pages if team.lower() in p.team.lower()]
        if component:
            comp_lower = component.lower()
            pages = [
                p for p in pages
                if p.decision_log and any(comp_lower in c.lower() for c in p.decision_log.related_components)
            ]
        return pages
