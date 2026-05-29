"""Candidate model + fusion.

Every source adapter emits Candidates — a proposed value for a manifest field,
tagged with where it came from and how confident we are. The builder fuses
candidates per field: explicit sources (CODEOWNERS, roster) beat inferred ones
(git, transcripts), and genuine conflicts are surfaced, never silently resolved.
"""
from __future__ import annotations
from dataclasses import dataclass, field as dc_field
from typing import Any

# Confidence tiers by source authority
CONFIDENCE = {
    "codeowners": 0.95,
    "roster": 0.95,
    "manifest": 0.9,
    "repo": 0.7,
    "git": 0.65,
    "jira": 0.6,
    "confluence": 0.55,
    "figma": 0.55,
    "slack": 0.5,
    "transcript": 0.4,
    "directory": 0.4,
}


@dataclass
class Candidate:
    field: str          # logical field, e.g. "owner", "members[]", "components.code[]", "dependencies[]"
    value: Any
    source: str         # adapter name
    note: str = ""      # human-readable provenance for the YAML comment
    confidence: float = 0.5

    @classmethod
    def make(cls, field: str, value: Any, source: str, note: str = "") -> "Candidate":
        return cls(field=field, value=value, source=source, note=note,
                   confidence=CONFIDENCE.get(source, 0.5))


@dataclass
class FusedField:
    value: Any
    sources: list[str] = dc_field(default_factory=list)
    note: str = ""
    confidence: float = 0.0
    conflict: bool = False


def _key_of(value: Any) -> str:
    """Stable identity for dedeuping list items."""
    if isinstance(value, dict):
        return (value.get("name") or value.get("team") or str(value)).lower()
    return str(value).lower()


class CandidateSet:
    def __init__(self) -> None:
        self.items: list[Candidate] = []

    def add(self, c: Candidate) -> None:
        self.items.append(c)

    def extend(self, cs: list[Candidate]) -> None:
        self.items.extend(cs)

    def scalar(self, field: str) -> FusedField | None:
        """Best single value for a scalar field; flags conflict if high-confidence sources disagree."""
        cands = [c for c in self.items if c.field == field]
        if not cands:
            return None
        cands.sort(key=lambda c: c.confidence, reverse=True)
        best = cands[0]
        # conflict if another source within 0.2 confidence proposes a different value
        conflict = any(
            _key_of(c.value) != _key_of(best.value) and best.confidence - c.confidence < 0.2
            for c in cands[1:]
        )
        return FusedField(
            value=best.value,
            sources=sorted({c.source for c in cands}),
            note=best.note,
            confidence=best.confidence,
            conflict=conflict,
        )

    def collection(self, field: str) -> list[FusedField]:
        """Union of list items, deduped by identity, each carrying merged provenance."""
        cands = [c for c in self.items if c.field == field]
        by_key: dict[str, list[Candidate]] = {}
        for c in cands:
            by_key.setdefault(_key_of(c.value), []).append(c)

        fused: list[FusedField] = []
        for key, group in by_key.items():
            group.sort(key=lambda c: c.confidence, reverse=True)
            top = group[0]
            fused.append(FusedField(
                value=top.value,
                sources=sorted({c.source for c in group}),
                note=top.note,
                confidence=max(c.confidence for c in group),
            ))
        fused.sort(key=lambda f: f.confidence, reverse=True)
        return fused
