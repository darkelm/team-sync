"""Freshness / decay scoring for manifest-derived answers.

A coordination tool that answers confidently from stale ownership data is worse
than no tool. This scores how trustworthy a team's manifest data is, so every
answer can be stamped with its freshness and the proactive layer can suppress
alerts built on rotted data.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date

AGING_DAYS = 14
STALE_DAYS = 30


@dataclass
class Freshness:
    score: float   # 0.0 (unverified) .. 1.0 (fresh)
    label: str     # "fresh" | "aging" | "stale" | "unverified"
    note: str      # short human line to stamp onto an answer


def assess(team) -> Freshness:
    """Score a team manifest by how recently it was verified."""
    last_verified = getattr(team, "last_verified", None)
    if not last_verified:
        return Freshness(0.0, "unverified",
                         "⚠️ _ownership unverified — run `refresh-manifest`_")
    age = (date.today() - last_verified).days
    if age <= AGING_DAYS:
        return Freshness(1.0, "fresh", f"_✓ verified {age}d ago_")
    if age <= STALE_DAYS:
        return Freshness(0.6, "aging", f"_verified {age}d ago_")
    return Freshness(0.3, "stale",
                     f"⚠️ _last verified {age}d ago (>{STALE_DAYS}d) — may be out of date_")


def is_fresh(team) -> bool:
    """Gate for the proactive layer: only act on reasonably-fresh data."""
    return assess(team).score >= 0.6


def stamp(team) -> str:
    """The freshness note to append to an answer about this team."""
    return assess(team).note
