"""Audience model — who's asking, so the same data is framed the way they think.

A lightweight role signal (per-user, with per-channel defaults). The *data* never
changes by audience; only framing does: non-technical roles get de-jargoned
output, and the Claude agent gets a framing hint. Default role is "ic" (an
individual contributor) when unknown — no behavior change from before.
"""
from __future__ import annotations
import json
import os
import re

STORE_PATH = "data/audience_prefs.json"

# Map many spellings → a canonical role.
_CANON = {
    "designer": "designer", "design": "designer",
    "dev": "dev", "developer": "dev", "engineer": "dev", "eng": "dev",
    "pm": "pm", "product": "pm", "product manager": "pm",
    "lead": "lead", "manager": "lead", "md": "lead", "managing director": "lead",
    "leadership": "lead", "exec": "lead", "executive": "lead", "director": "lead",
}
NON_TECHNICAL = {"designer", "pm", "lead"}

_HINTS = {
    "designer": "[Audience: a designer. Lead with design-system status, design decisions, and reuse; avoid code/PR/ticket jargon.]",
    "pm": "[Audience: a PM. Lead with delivery dates, blockers, and what changed; frame in outcomes, not mechanics.]",
    "lead": "[Audience: leadership/MD. Answer with health, top risks, and trajectory in plain language. No per-component, PR, or ticket detail unless explicitly asked.]",
}


def agent_hint(role: str) -> str:
    return _HINTS.get(role, "")


def is_non_technical(role: str) -> bool:
    return role in NON_TECHNICAL


def parse_role_command(text: str) -> str | None:
    """If text sets a role ('I'm a designer', 'set my role to MD'), return the canonical role."""
    m = re.search(r"(?:i'?m|i am|my role is|set my role to|role:)\s+(?:an?\s+)?([a-z ]+)", text, re.I)
    if not m:
        return None
    phrase = m.group(1).strip().lower()
    for key in sorted(_CANON, key=lambda k: -len(k)):
        if phrase.startswith(key):
            return _CANON[key]
    return None


class AudienceStore:
    def __init__(self, path: str = STORE_PATH):
        self.path = path
        self._data = {"users": {}, "channels": {}}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    self._data = json.load(f)
            except (OSError, ValueError) as e:
                # Store file exists but couldn't be read/parsed — silently
                # dropping saved user/channel roles would hide real corruption.
                print(f"[audience] could not load audience store from {path}, using empty: {e}", flush=True)

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)

    def set_user(self, user_id: str, role: str) -> str:
        self._data.setdefault("users", {})[user_id] = role
        self._save()
        pretty = {"designer": "designer", "dev": "developer", "pm": "PM", "lead": "leadership"}.get(role, role)
        return f"Got it — I'll frame answers for a *{pretty}* from now on."

    def set_channel(self, channel_id: str, role: str) -> str:
        self._data.setdefault("channels", {})[channel_id] = role
        self._save()
        return f"This channel will default to *{role}* framing."

    def role_for(self, user_id: str = "", channel_id: str = "") -> str:
        """User role wins; then channel default; then 'ic' (no special framing)."""
        if user_id and user_id in self._data.get("users", {}):
            return self._data["users"][user_id]
        if channel_id and channel_id in self._data.get("channels", {}):
            return self._data["channels"][channel_id]
        return "ic"
