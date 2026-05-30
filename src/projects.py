"""Multi-project support — strict isolation between parallel engagements.

Each project has its own config, its own Providers instance, its own state
files, and its own set of channels. A Google query never touches Workday data.

ProjectRegistry maps Slack channel IDs → project configs. The bot looks up
the project for every incoming message and passes the right Providers to all
engines. Isolation is automatic once the mapping is set.

Usage:
    registry = ProjectRegistry()
    project = registry.for_channel(channel_id)
    providers = project.providers()
"""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

REGISTRY_PATH = "data/project_registry.json"


@dataclass
class Project:
    """One engagement / client project."""
    name: str                          # "Google Gen AI", "Workday Redesign"
    config: str                        # path to config-<slug>.yaml
    channels: list[str] = field(default_factory=list)   # Slack channel IDs or names
    channel_patterns: list[str] = field(default_factory=list)  # regex patterns

    def matches_channel(self, channel_id: str, channel_name: str = "") -> bool:
        if channel_id in self.channels or channel_name in self.channels:
            return True
        for pattern in self.channel_patterns:
            if re.search(pattern, channel_id, re.I) or re.search(pattern, channel_name, re.I):
                return True
        return False

    def providers(self):
        """Cached per-project Providers instance — lazy init, one per project."""
        if not hasattr(self, "_providers"):
            from src.providers.factory import Providers
            object.__setattr__(self, "_providers", Providers(self.config))
        return self._providers

    def state_path(self, filename: str) -> str:
        """Per-project path for any state file (snapshots, prefs, etc.)."""
        slug = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")
        base = os.path.join("data", slug)
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, filename)


class ProjectRegistry:
    """Maps Slack channels to projects. Falls back to the default config."""

    def __init__(self, registry_path: str = REGISTRY_PATH, default_config: str = "config.yaml"):
        self.default_config = default_config
        self.projects: list[Project] = []
        self._default: Project | None = None
        self._load(registry_path)

    def _load(self, path: str) -> None:
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            for p in data.get("projects", []):
                self.projects.append(Project(
                    name=p["name"],
                    config=p["config"],
                    channels=p.get("channels", []),
                    channel_patterns=p.get("channel_patterns", []),
                ))
        except (OSError, ValueError, KeyError) as e:
            print(f"[projects] Registry load failed: {e}", flush=True)

    def register(self, name: str, config: str,
                 channels: list[str] | None = None,
                 channel_patterns: list[str] | None = None) -> Project:
        """Add or replace a project at runtime."""
        # Remove existing entry with same name
        self.projects = [p for p in self.projects if p.name != name]
        project = Project(
            name=name, config=config,
            channels=channels or [],
            channel_patterns=channel_patterns or [],
        )
        self.projects.append(project)
        self._save()
        return project

    def _save(self) -> None:
        os.makedirs(os.path.dirname(REGISTRY_PATH) or ".", exist_ok=True)
        data = {"projects": [
            {"name": p.name, "config": p.config,
             "channels": p.channels, "channel_patterns": p.channel_patterns}
            for p in self.projects
        ]}
        with open(REGISTRY_PATH, "w") as f:
            json.dump(data, f, indent=2)

    def for_channel(self, channel_id: str = "", channel_name: str = "") -> Project:
        """Return the project for this channel, or a default project."""
        for project in self.projects:
            if project.matches_channel(channel_id, channel_name):
                return project
        # Fall back: return a default project using config.yaml
        if self._default is None:
            self._default = Project(name="default", config=self.default_config)
        return self._default

    def all_projects(self) -> list[Project]:
        return list(self.projects)

    def summary(self) -> str:
        if not self.projects:
            return "No projects registered yet. All queries use the default config."
        lines = [f"*{len(self.projects)} project(s) registered:*\n"]
        for p in self.projects:
            channel_count = len(p.channels) + len(p.channel_patterns)
            lines.append(f"  • *{p.name}* — `{p.config}` ({channel_count} channel mapping(s))")
        return "\n".join(lines)
