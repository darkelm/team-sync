"""Per-team notification preferences — keeps the proactive value from becoming noise.

Stored in a small JSON file the Slack bot can read and write, so teams can tune
cadence/severity/pause without touching manifests or code.
"""
from __future__ import annotations
import json
import os
from datetime import date
from typing import Optional

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

DEFAULTS = {
    "min_severity": "low",      # only surface issues at or above this
    "paused_until": None,        # ISO date string while muted
    "sections": {"dev": True, "design": True},
    "last_signature": None,      # quality gate — skip a digest identical to the last
    "digest_channel": None,      # Slack channel ID to deliver to (overrides manifest slack_channel)
    "digest_channel_name": None, # human-readable name for display
}


class NotificationPreferences:
    def __init__(self, path: str = "data/notification_prefs.json"):
        self.path = path
        self._data: dict = {}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    self._data = json.load(f)
            except (OSError, ValueError):
                self._data = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)

    def get(self, team: str) -> dict:
        prefs = dict(DEFAULTS)
        prefs.update(self._data.get(team, {}))
        return prefs

    # ── tuning (called by Slack commands) ─────────────────────────────────────

    def set_severity(self, team: str, level: str) -> str:
        level = level.lower()
        if level not in SEVERITY_RANK:
            return f"Unknown severity '{level}'. Use: low, medium, high, critical."
        self._data.setdefault(team, {})["min_severity"] = level
        self._save()
        return f"Digest severity for *{team}* set to *{level}* — you'll only be alerted at {level}+ from now on."

    def pause(self, team: str, until: Optional[str] = None) -> str:
        self._data.setdefault(team, {})["paused_until"] = until or "2999-01-01"
        self._save()
        return f"Digests for *{team}* are paused" + (f" until {until}." if until else " until you resume.")

    def resume(self, team: str) -> str:
        self._data.setdefault(team, {})["paused_until"] = None
        self._save()
        return f"Digests for *{team}* resumed."

    def set_section(self, team: str, section: str, on: bool) -> str:
        self._data.setdefault(team, {}).setdefault("sections", dict(DEFAULTS["sections"]))[section] = on
        self._save()
        return f"{'Enabled' if on else 'Disabled'} the *{section}* section for *{team}*'s digest."

    # ── digest delivery target (Slack-native: "send <team> digest here") ───────

    def set_digest_channel(self, team: str, channel_id: str, channel_name: Optional[str] = None) -> None:
        entry = self._data.setdefault(team, {})
        entry["digest_channel"] = channel_id
        entry["digest_channel_name"] = channel_name or channel_id
        self._save()

    def clear_digest_channel(self, team: str) -> bool:
        """Remove the override. Returns True if one was set."""
        entry = self._data.setdefault(team, {})
        had = bool(entry.get("digest_channel"))
        entry["digest_channel"] = None
        entry["digest_channel_name"] = None
        self._save()
        return had

    def get_digest_channel(self, team: str) -> Optional[str]:
        return self.get(team)["digest_channel"]

    def digest_targets(self) -> dict:
        """team -> display name, for every team with an explicit digest channel."""
        return {t: (d.get("digest_channel_name") or d.get("digest_channel"))
                for t, d in self._data.items() if d.get("digest_channel")}

    # ── gates (called by the digest generator) ────────────────────────────────

    def is_paused(self, team: str) -> bool:
        until = self.get(team)["paused_until"]
        if not until:
            return False
        try:
            return date.today() <= date.fromisoformat(until)
        except ValueError:
            return True

    def severity_ok(self, team: str, severity: str) -> bool:
        return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK[self.get(team)["min_severity"]]

    def changed_since_last(self, team: str, signature: str) -> bool:
        return self.get(team)["last_signature"] != signature

    def record_signature(self, team: str, signature: str) -> None:
        self._data.setdefault(team, {})["last_signature"] = signature
        self._save()
