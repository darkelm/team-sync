"""Lightweight instrumentation + data-quality state for SyncBot.

Two jobs:
  • Stale flags — when a teammate says "this is wrong" (`mark <team> stale`), we
    record it so freshness reflects human signal, not just manifest age.
  • Stats — a roll-up of the team's misses (unmatched queries) + flagged teams,
    so feedback becomes a concrete iteration backlog (`@syncbot stats`).
"""
from __future__ import annotations
import json
import os
from collections import Counter

STALE_FLAGS = "data/stale_flags.json"


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def mark_stale(team: str, by: str = "") -> str:
    data = _load(STALE_FLAGS)
    data[team] = {"by": by}
    _save(STALE_FLAGS, data)
    return (f"📌 Flagged *{team}*'s data as stale — answers about it will warn "
            f"until someone runs `refresh-manifest`. Thanks for the signal.")


def clear_stale(team: str) -> str:
    data = _load(STALE_FLAGS)
    existed = data.pop(team, None) is not None
    _save(STALE_FLAGS, data)
    return f"✅ Cleared the stale flag on *{team}*." if existed else f"*{team}* wasn't flagged."


def is_flagged(team: str) -> bool:
    return team in _load(STALE_FLAGS)


def flagged_teams() -> list[str]:
    return list(_load(STALE_FLAGS).keys())


def stats(unmatched_log: str) -> str:
    """Roll-up of misses + flagged teams — the iteration backlog in one place."""
    lines = ["*📊 SyncBot stats — your iteration backlog*\n"]
    flagged = flagged_teams()
    if flagged:
        lines.append(f"*⚠️ Teams flagged stale ({len(flagged)}):* {', '.join(flagged)} "
                     f"— run `refresh-manifest` to clear.")
    texts: list[str] = []
    if os.path.exists(unmatched_log):
        try:
            with open(unmatched_log) as f:
                for line in f:
                    try:
                        t = json.loads(line).get("text")
                    except ValueError:
                        continue
                    if t:
                        texts.append(t.lower())
        except OSError:
            texts = []
    if texts:
        c = Counter(texts)
        lines.append(f"\n*🔍 Top unanswered questions ({len(texts)} total):*")
        for t, n in c.most_common(8):
            lines.append(f"• {f'{n}× ' if n > 1 else ''}“{t}”")
    if len(lines) == 1:
        lines.append("Nothing logged yet — no misses, no stale flags. 🎉")
    return "\n".join(lines)
