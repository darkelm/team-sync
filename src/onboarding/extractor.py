"""Initiative brief extractor — normalizes ANY source into a structured brief."""
from __future__ import annotations
import re
from dataclasses import dataclass, field

@dataclass
class TeamDraft:
    name: str
    members: list[str] = field(default_factory=list)
    focus: str = ""
    tools: list[str] = field(default_factory=list)

@dataclass
class JourneyDraft:
    name: str
    description: str = ""
    teams: list[str] = field(default_factory=list)
    north_star: str = ""

@dataclass
class PrincipleDraft:
    name: str
    statement: str = ""
    keywords: list[str] = field(default_factory=list)

@dataclass
class InitiativeBrief:
    title: str = ""
    client: str = ""
    description: str = ""
    teams: list[TeamDraft] = field(default_factory=list)
    journeys: list[JourneyDraft] = field(default_factory=list)
    principles: list[PrincipleDraft] = field(default_factory=list)
    open_decisions: list[str] = field(default_factory=list)
    north_star: str = ""
    raw_text: str = ""

_TEAM_CUES = re.compile(r"(?:^|\n)\s*(?:pair|team|squad|group|pod)\s*\d*[:\-–—]\s*(.+?)(?:\n|$)", re.I)
_PRINCIPLE_CUES = re.compile(r"(?:principle|criteria|criterion|standard)[s]?\s*[:\-–—]\s*(.+?)(?:\n|$)", re.I)
_JOURNEY_CUES = re.compile(r"(?:experience|journey|flow|feature|concept)\s*\d+[:\-–—]\s*(.+?)(?:\n|$)", re.I)
_NORTH_STAR = re.compile(r"(?:north star|success looks like|what good looks like|our goal|the goal)[:\-–—]?\s*(.+?)(?:\n|$)", re.I)
_CLIENT = re.compile(r"(?:\*\*client\*\*|client)[:\-–—]\s*([A-Z][A-Za-z0-9 &]+?)(?:\n|,|\.|$)", re.I)

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().strip("*_#`")

def _is_noise(s: str) -> bool:
    if not s or len(s) < 4 or len(s) > 100: return True
    if re.search(r"\?|size:|how many|TODO|e\.g\.", s, re.I): return True
    if sum(c.isdigit() for c in s) > len(s) * 0.3: return True
    return False

def extract_heuristic(text: str) -> InitiativeBrief:
    brief = InitiativeBrief(raw_text=text)
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if 4 < len(line) < 120 and not _is_noise(line):
            brief.title = _clean(line); break
    m = _CLIENT.search(text)
    if m: brief.client = _clean(m.group(1))
    m = _NORTH_STAR.search(text)
    if m: brief.north_star = _clean(m.group(1))
    seen_j: set = set()
    for m in _JOURNEY_CUES.finditer(text):
        name = _clean(m.group(1))[:80]
        if not _is_noise(name) and name.lower() not in seen_j:
            seen_j.add(name.lower())
            brief.journeys.append(JourneyDraft(name=name))
    seen_t: set = set()
    for m in _TEAM_CUES.finditer(text):
        name = _clean(m.group(1))[:60]
        if _is_noise(name) or name.lower() in seen_t: continue
        seen_t.add(name.lower())
        brief.teams.append(TeamDraft(name=name))
    seen_p: set = set()
    for m in _PRINCIPLE_CUES.finditer(text):
        raw = _clean(m.group(1))
        candidates = [_clean(x) for x in re.split(r",\s*", raw)] if "," in raw else [raw]
        for name in candidates:
            name = name[:80]
            if not _is_noise(name) and name.lower() not in seen_p:
                seen_p.add(name.lower())
                brief.principles.append(PrincipleDraft(name=name, keywords=[w.lower() for w in name.split() if len(w) > 3]))
    for line in text.splitlines():
        line = line.strip().lstrip("-•*").strip()
        # Skip if it looks like a journey heading (Experience N: ...)
        if re.match(r'Experience\s*\d+:', line, re.I):
            continue
        if line.endswith("?") and 10 < len(line) < 200:
            brief.open_decisions.append(line)
    paras = [p.strip() for p in re.split(r"\n{2,}", text) if len(p.strip()) > 60]
    brief.description = paras[0][:400] if paras else ""
    return brief

_AI_SCHEMA_PROMPT = """Extract a structured brief from the following initiative description.
Return JSON matching this exact schema (omit fields you can't find; use empty lists/strings):
{
  "title": "string", "client": "string", "description": "string (1-3 sentences)",
  "north_star": "string",
  "teams": [{"name": "string", "members": ["string"], "focus": "string"}],
  "journeys": [{"name": "string", "description": "string", "teams": ["string"], "north_star": "string"}],
  "principles": [{"name": "string", "statement": "string", "keywords": ["string"]}],
  "open_decisions": ["string"]
}
Initiative description:
"""

def extract_ai(text: str) -> InitiativeBrief:
    import os, json, anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8"),
        max_tokens=2048, thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": _AI_SCHEMA_PROMPT + text[:8000]}],
    )
    text_out = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", text_out)
    raw = m.group(1) if m else text_out
    data = json.loads(raw)
    brief = InitiativeBrief(raw_text=text)
    brief.title = data.get("title", ""); brief.client = data.get("client", "")
    brief.description = data.get("description", ""); brief.north_star = data.get("north_star", "")
    brief.open_decisions = data.get("open_decisions", [])
    brief.teams = [TeamDraft(**t) for t in data.get("teams", [])]
    brief.journeys = [JourneyDraft(**j) for j in data.get("journeys", [])]
    brief.principles = [PrincipleDraft(**p) for p in data.get("principles", [])]
    return brief

def extract(text: str) -> InitiativeBrief:
    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        try: return extract_ai(text)
        except Exception as e: print(f"[onboarding] AI extraction failed, using heuristics: {e}", flush=True)
    return extract_heuristic(text)
