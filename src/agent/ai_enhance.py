"""AI enhancement layer — structured outputs that return the SAME schemas the
heuristics produce.

Principle (see ADOPTION.md): every capability has a non-AI implementation; the
functions here are an optional quality lift selected only when a key is present,
and they emit the exact same objects (MeetingNotes parts, etc.) so callers don't
branch on data shape. Any failure falls back to the heuristic path.
"""
from __future__ import annotations
import os
from datetime import date
from typing import Optional
from pydantic import BaseModel

from ..core.schemas import DecisionLog, ActionItem
from ..importers.transcript import Segment

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")


def ai_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ── Meeting extraction (structured output) ────────────────────────────────────

class _AIDecision(BaseModel):
    title: str
    decision: str
    rationale: str

class _AIAction(BaseModel):
    owner: Optional[str]
    task: str
    due: Optional[str]

class _AIMeetingExtract(BaseModel):
    decisions: list[_AIDecision]
    action_items: list[_AIAction]
    risks: list[str]


def extract_meeting(
    segments: list[Segment], team: str, meeting_date: date, participants: list[str]
) -> tuple[list[DecisionLog], list[ActionItem], list[str]]:
    """Claude extracts decisions/actions/risks with a validated schema.

    Returns the same (decisions, action_items, risks) tuple shape the heuristic
    extractor produces. Raises on any failure so the caller can fall back.
    """
    import anthropic

    transcript = "\n".join(f"{s.speaker}: {s.text}" for s in segments)
    prompt = (
        "Extract the real DECISIONS, ACTION ITEMS, and RISKS from this meeting transcript.\n"
        "- Decisions: only genuine commitments the group settled on — not options discussed and dropped. "
        "Give a short title, the decision itself, and the rationale (why).\n"
        "- Action items: concrete follow-ups. Identify the owner (the person responsible — for \"I'll…\" "
        "it's the speaker) and any due date mentioned. Leave owner/due null if not stated.\n"
        "- Risks: blockers, dependencies, or concerns raised.\n"
        f"Known participants: {', '.join(participants) or 'unknown'}.\n\n"
        f"Transcript:\n{transcript}"
    )

    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
        output_format=_AIMeetingExtract,
    )
    data = resp.parsed_output
    if data is None:
        raise ValueError("AI extraction returned no parsed output")

    decisions = [
        DecisionLog(
            id=f"DEC-MTG-{i+1}",
            title=d.title[:80],
            decision=d.decision,
            rationale=d.rationale or "Captured from meeting transcript; confirm with attendees.",
            decided_by=participants,
            date=meeting_date,
            status="draft",
            team=team,
        )
        for i, d in enumerate(data.decisions)
    ]
    actions = [ActionItem(owner=a.owner, task=a.task, due=a.due) for a in data.action_items]
    return decisions, actions, list(data.risks)


# ── Semantic reuse matching (structured output) ───────────────────────────────

class _AIReuseHit(BaseModel):
    index: int
    reason: str

class _AIReuseResult(BaseModel):
    matches: list[_AIReuseHit]


def semantic_reuse(description: str, catalog: list[dict]) -> list[tuple[dict, str]]:
    """Claude judges which catalog items are *semantically* similar to `description`.

    `catalog` items are dicts with name/kind/team/detail. Returns (item, reason)
    pairs grounded by index (so names can't be hallucinated). Raises on failure.
    """
    import anthropic

    lines = [
        f"{i}. [{c.get('kind')}] {c.get('name')} (owned by {c.get('team')}) — {c.get('detail','')}"
        for i, c in enumerate(catalog)
    ]
    prompt = (
        "A team is about to build or research the following:\n"
        f"\"{description}\"\n\n"
        "Here is a catalog of existing components, designs, and work items across the org. "
        "Return the indices of items that are SEMANTICALLY similar — i.e. solve the same problem "
        "even if named differently (e.g. 'alert badge' ≈ 'notification bell'). For each, give a "
        "one-line reason. Only include genuine matches; return an empty list if nothing is close.\n\n"
        + "\n".join(lines)
    )

    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        output_format=_AIReuseResult,
    )
    data = resp.parsed_output
    if data is None:
        raise ValueError("AI reuse matching returned no parsed output")
    out = []
    for hit in data.matches:
        if 0 <= hit.index < len(catalog):
            out.append((catalog[hit.index], hit.reason))
    return out


# ── Manifest field inference (structured output) ──────────────────────────────

class _AICompDesc(BaseModel):
    name: str
    description: str

class _AIManifest(BaseModel):
    team_description: str
    component_descriptions: list[_AICompDesc]


def infer_manifest(team: str, context_text: str, component_names: list[str]) -> dict:
    """Infer the manifest fields no deterministic source can supply well — the team's
    one-line description and a real description per component — from README/doc text.

    Returns {"description": str, "component_descriptions": {name: desc}}. Raises on failure.
    """
    import anthropic

    prompt = (
        f"You are documenting the team '{team}' for an internal team directory.\n"
        f"Components owned by this team: {', '.join(component_names) or 'unknown'}.\n\n"
        "From the context below, write:\n"
        "1. team_description: one concise sentence on what this team owns/does.\n"
        "2. component_descriptions: for each component above, a one-line description of what it does "
        "(infer from the name + context; keep it factual, no guessing at features not implied).\n\n"
        f"Context:\n{context_text[:6000]}"
    )

    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        output_format=_AIManifest,
    )
    data = resp.parsed_output
    if data is None:
        raise ValueError("AI manifest inference returned no parsed output")
    return {
        "description": data.team_description.strip(),
        "component_descriptions": {c.name: c.description.strip() for c in data.component_descriptions},
    }
