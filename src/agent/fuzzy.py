"""Typo-tolerant matching + suggestions — makes the bot forgiving instead of rigid.

Pure stdlib (difflib). Used by both the keyword bot and the agent tools so a
misspelled team/component still resolves, and a true miss returns helpful
"did you mean" suggestions instead of a dead end.
"""
from __future__ import annotations
import difflib
import re
from typing import Optional


def fuzzy_pick(query: str, options: list[str], n: int = 3, cutoff: float = 0.6) -> list[str]:
    """Return up to n options closest to query, preserving original casing."""
    lower = {o.lower(): o for o in options}
    return [lower[h] for h in difflib.get_close_matches(query.lower(), list(lower), n=n, cutoff=cutoff)]


def resolve_teams(providers, text: str) -> list[str]:
    """Find teams referenced in text — full name, short name (no 'Team ' prefix), or fuzzy."""
    teams = providers.manifests.get_all_teams()
    q = text.lower()
    found: list[tuple[int, str]] = []
    for t in teams:
        full = t.team.lower()
        short = full.replace("team ", "").strip()
        idx = q.find(full)
        if idx < 0 and len(short) > 3:
            idx = q.find(short)
        if idx >= 0:
            found.append((idx, t.team))
    if found:
        return [name for _, name in sorted(found)]

    # Fuzzy fallback — catch "Phenix" → "Phoenix"
    short_map = {t.team.lower().replace("team ", "").strip(): t.team for t in teams}
    out: list[str] = []
    for word in re.findall(r"[a-z0-9]+", q):
        for hit in difflib.get_close_matches(word, list(short_map), n=1, cutoff=0.8):
            if short_map[hit] not in out:
                out.append(short_map[hit])
    return out


def _component_catalog(providers) -> list[tuple[str, str]]:
    catalog = []
    for t in providers.manifests.get_all_teams():
        for c in t.components.code + t.components.design:
            catalog.append((c.name, t.team))
    return catalog


def component_owner(providers, name: str):
    """Return (owning_team_or_None, suggestions).

    Exact/substring first; then a strong fuzzy match auto-resolves a typo
    ('authh' → 'auth'); otherwise returns weaker suggestions for a 'did you mean'.
    """
    team = providers.manifests.find_component_owner(name)
    if team:
        return team, []

    catalog = _component_catalog(providers)
    names = [c for c, _ in catalog]
    owner_map = {c.lower(): tm for c, tm in catalog}

    strong = fuzzy_pick(name, names, n=1, cutoff=0.85)
    if strong:
        return providers.manifests.get_team(owner_map[strong[0].lower()]), []

    suggestions = [(s, owner_map[s.lower()]) for s in fuzzy_pick(name, names, n=3, cutoff=0.5)]
    return None, suggestions
