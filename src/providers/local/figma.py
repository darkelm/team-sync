import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from ...core.schemas import (
    FigmaComponent, DriftIssue,
    FigmaDevStatus, FigmaComment, FigmaChange,
)
from ..base import FigmaProvider


class LocalFigmaProvider(FigmaProvider):
    def __init__(self, teams_dir: str):
        self.teams_dir = teams_dir
        self._components: list[FigmaComponent] = []
        self._loaded = False
        # Figma-native coordination signals — loaded lazily from the optional
        # per-team figma_dev_status.json file (additive; absent files are fine).
        self._dev_status: list[FigmaDevStatus] = []
        self._comments: list[FigmaComment] = []
        self._changes: list[FigmaChange] = []
        self._signals_loaded = False

    def _load(self) -> list[FigmaComponent]:
        if self._loaded:
            return self._components
        for entry in os.scandir(self.teams_dir):
            if entry.is_dir():
                path = os.path.join(entry.path, "figma_components.json")
                if os.path.exists(path):
                    with open(path) as f:
                        for item in json.load(f):
                            self._components.append(FigmaComponent(**item))
        self._loaded = True
        return self._components

    def _load_signals(self) -> None:
        """Load the additive Figma-native coordination signals.

        Each team directory may contain a figma_dev_status.json with file-level
        metadata plus three lists: dev_status, comments, changes. The file is
        optional — teams without it simply contribute no signals. The shared
        file_id/file_name/team header is stamped onto every nested record so the
        per-record schemas stay self-describing.
        """
        if self._signals_loaded:
            return
        for entry in os.scandir(self.teams_dir):
            if not entry.is_dir():
                continue
            path = os.path.join(entry.path, "figma_dev_status.json")
            if not os.path.exists(path):
                continue
            with open(path) as f:
                doc = json.load(f)
            header = {
                "file_id": doc.get("file_id", ""),
                "file_name": doc.get("file_name", ""),
                "team": doc.get("team", ""),
            }
            for item in doc.get("dev_status", []):
                self._dev_status.append(FigmaDevStatus(**{**header, **item}))
            for item in doc.get("comments", []):
                self._comments.append(FigmaComment(**{**header, **item}))
            for item in doc.get("changes", []):
                self._changes.append(FigmaChange(**{**header, **item}))
        self._signals_loaded = True

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
                suggested_action="Re-sync with the shared design system library or raise a design review.",
            ))
        return issues

    # ── Figma-native coordination signals ────────────────────────────────────

    def get_dev_status(self, team: Optional[str] = None) -> list[FigmaDevStatus]:
        """Dev-handoff readiness (ready-for-dev status + linked tickets) per frame."""
        self._load_signals()
        statuses = self._dev_status
        if team:
            statuses = [s for s in statuses if team.lower() in s.team.lower()]
        return statuses

    def get_open_comments(self, team: Optional[str] = None) -> list[FigmaComment]:
        """Open (unresolved) Figma comment threads.

        Resolved threads are filtered out — they no longer block coordination.
        Returned highest-priority first so high-priority blockers surface.
        """
        self._load_signals()
        priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        comments = [c for c in self._comments if not c.resolved]
        if team:
            comments = [c for c in comments if team.lower() in c.team.lower()]
        return sorted(
            comments,
            key=lambda c: (priority_rank.get(c.priority.value, 99), c.created_at),
        )

    def get_recent_changes(self, team: Optional[str] = None, days: int = 7) -> list[FigmaChange]:
        """Recent version/frame changes within the last `days`, newest first."""
        self._load_signals()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        changes = [c for c in self._changes if c.changed_at >= cutoff]
        if team:
            changes = [c for c in changes if team.lower() in c.team.lower()]
        return sorted(changes, key=lambda c: c.changed_at, reverse=True)
