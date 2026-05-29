"""MeetingAnalyzer — turn a transcript into structured, searchable knowledge.

Extracts decisions, action items, cross-team commitments, and risks. Uses
heuristics today; the same interface swaps to the Claude agent for far better
extraction once a key is available (see analyze_with_claude hook).
"""
from __future__ import annotations
import re
from datetime import date
from ..core.schemas import MeetingNotes, ActionItem, DecisionLog
from ..importers.transcript import Segment
from ..providers.factory import Providers


DECISION_CUES = [
    "we decided", "we've decided", "let's go with", "we'll go with", "going with",
    "the decision is", "we agreed", "we're agreed", "let's use", "we'll use",
    "let's standardize on", "final call", "we'll adopt", "let's adopt",
    "we should go with", "agreed to", "consensus is", "we'll move forward with",
]
ACTION_CUES = [
    "i'll", "i will", "will take", "can you", "could you", "needs to", "need to",
    "action item", "take an action", "let's have", "to follow up", "will own",
    "will handle", "by eod", "by end of", "by friday", "by monday", "by next",
]
RISK_CUES = [
    "risk", "blocked", "blocker", "concern", "concerned", "worried", "depends on",
    "dependency", "might break", "could break", "at risk", "behind schedule",
]
DUE_PAT = re.compile(
    r"\bby\s+(eod|end of (?:day|week|sprint|month)|next \w+|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"\w+ \d{1,2}(?:st|nd|rd|th)?)", re.I,
)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


class MeetingAnalyzer:
    def __init__(self, providers: Providers):
        self.p = providers
        self.team_names = [t.team for t in providers.manifests.get_all_teams()]
        self.people = {}
        for t in providers.manifests.get_all_teams():
            for m in [t.owner, *t.members]:
                self.people[m.name.lower()] = m.name

    def _find_owner(self, sentence: str, speaker: str) -> str | None:
        low = sentence.lower()
        # First person ("I'll / I will") → the speaker owns it, even if another
        # name is mentioned as the target ("I'll reach out to Jordan").
        if re.search(r"\bi'?ll\b|\bi will\b", low) and speaker != "Unknown":
            return speaker
        # Direct address ("Priya, you'll …") or a named owner
        for name_l, name in self.people.items():
            first = name_l.split()[0]
            if name_l in low or re.search(rf"\b{re.escape(first)}\b", low):
                return name
        return None

    def _teams_in(self, text: str) -> list[str]:
        low = text.lower()
        return [t for t in self.team_names if t.lower() in low]

    def _heuristic_extract(self, segments: list[Segment], team: str, meeting_date: date,
                           participants: list[str]):
        """Keyword/cue-based extraction — the always-available fallback."""
        decisions: list[DecisionLog] = []
        actions: list[ActionItem] = []
        risks: list[str] = []
        seen_decisions, seen_actions, seen_risks = set(), set(), set()

        for seg in segments:
            for sent in _sentences(seg.text):
                low = sent.lower()
                if any(c in low for c in DECISION_CUES) and low[:80] not in seen_decisions:
                    seen_decisions.add(low[:80])
                    decisions.append(DecisionLog(
                        id=f"DEC-MTG-{len(decisions)+1}", title=sent[:80], decision=sent,
                        rationale="Captured from meeting transcript; confirm with attendees.",
                        decided_by=participants, date=meeting_date, status="draft",
                        related_components=[], team=team,
                    ))
                if any(c in low for c in ACTION_CUES) and low[:80] not in seen_actions:
                    seen_actions.add(low[:80])
                    due_m = DUE_PAT.search(sent)
                    actions.append(ActionItem(
                        owner=self._find_owner(sent, seg.speaker), task=sent,
                        due=due_m.group(0) if due_m else None, quote=f"{seg.speaker}: {sent}",
                    ))
                if any(c in low for c in RISK_CUES) and low[:80] not in seen_risks:
                    seen_risks.add(low[:80])
                    risks.append(sent)
        return decisions, actions, risks

    def analyze(self, segments: list[Segment], team: str, title: str,
                meeting_date: date | None = None) -> MeetingNotes:
        meeting_date = meeting_date or date.today()
        participants = sorted({s.speaker for s in segments if s.speaker != "Unknown"})
        full_text = " ".join(s.text for s in segments)
        teams_mentioned = sorted(set(self._teams_in(full_text)) - {team})

        # AI extraction when a key is present (schema-identical); heuristic otherwise / on failure.
        decisions = actions = risks = None
        from .ai_enhance import ai_available
        if ai_available():
            try:
                from .ai_enhance import extract_meeting
                decisions, actions, risks = extract_meeting(segments, team, meeting_date, participants)
                extraction = "ai"
            except Exception as e:
                print(f"[meeting] AI extraction failed, using heuristics: {e}", flush=True)
        if decisions is None:
            decisions, actions, risks = self._heuristic_extract(segments, team, meeting_date, participants)
            extraction = "heuristic"

        summary = (f"{title} — {len(decisions)} decisions, {len(actions)} action items, "
                   f"{len(risks)} risks (via {extraction}). Participants: {', '.join(participants) or 'n/a'}.")

        return MeetingNotes(
            id=re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "meeting",
            title=title, date=meeting_date, team=team,
            participants=participants, teams_mentioned=teams_mentioned,
            decisions=decisions, action_items=actions, risks=risks, summary=summary,
        )

    def format_slack_summary(self, notes: MeetingNotes) -> str:
        lines = [f"*📝 Meeting notes — {notes.title}*", f"_{notes.date} · {notes.team}_", ""]
        if notes.participants:
            lines.append(f"*Participants:* {', '.join(notes.participants)}")
        if notes.teams_mentioned:
            lines.append(f"*Other teams mentioned:* {', '.join(notes.teams_mentioned)} "
                         f"— consider looping them in.")
        lines.append("")
        if notes.decisions:
            lines.append(f"*✅ Decisions ({len(notes.decisions)})*")
            for d in notes.decisions:
                lines.append(f"  • {d.decision}")
            lines.append("")
        if notes.action_items:
            lines.append(f"*📌 Action items ({len(notes.action_items)})*")
            for a in notes.action_items:
                who = a.owner or "unassigned"
                due = f" _(due {a.due})_" if a.due else ""
                lines.append(f"  • *{who}*: {a.task}{due}")
            lines.append("")
        if notes.risks:
            lines.append(f"*⚠️ Risks / blockers ({len(notes.risks)})*")
            for r in notes.risks:
                lines.append(f"  • {r}")
            lines.append("")
        if notes.decisions:
            lines.append("_Decisions captured as draft logs — now searchable via "
                         "`@syncbot what was decided about …`_")
        return "\n".join(lines)

    def to_confluence_pages(self, notes: MeetingNotes) -> list[dict]:
        """Convert decisions into ConfluencePage-shaped dicts so they become searchable."""
        pages = []
        for d in notes.decisions:
            pages.append({
                "id": f"{notes.id}-{d.id}",
                "title": f"[Meeting] {d.title}",
                "space": notes.team,
                "team": notes.team,
                "content_summary": d.decision,
                "tags": ["decision-log", "meeting"],
                "last_updated": str(notes.date),
                "author": ", ".join(notes.participants[:2]),
                "url": f"meeting://{notes.id}",
                "decision_log": {
                    "id": d.id, "title": d.title, "decision": d.decision,
                    "rationale": d.rationale, "alternatives_considered": [],
                    "decided_by": d.decided_by, "date": str(d.date),
                    "status": d.status, "related_tickets": [],
                    "related_components": d.related_components, "team": notes.team,
                },
            })
        return pages
