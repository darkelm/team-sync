"""Parse meeting transcripts into speaker segments — no API access required.

Handles the formats the common tools export:
- WebVTT (.vtt) — Zoom, Google Meet, Teams
- SubRip (.srt) — generic captions
- Plain text (.txt) — "Speaker: text" lines (Otter, Fireflies, manual notes)

Output: a list of {speaker, text} segments with consecutive same-speaker lines merged.
"""
from __future__ import annotations
import re
from dataclasses import dataclass


@dataclass
class Segment:
    speaker: str
    text: str


_TS = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->")
_VTT_SPEAKER = re.compile(r"<v\s+([^>]+)>(.*)", re.I)
_INLINE_SPEAKER = re.compile(r"^([A-Z][A-Za-z .'-]{1,40}?):\s*(.*)$")
_CUE_NUM = re.compile(r"^\d+$")


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)  # strip any leftover tags
    return text.strip()


def parse_transcript(path: str) -> list[Segment]:
    with open(path, encoding="utf-8", errors="ignore") as f:
        lines = f.read().splitlines()

    raw: list[Segment] = []
    current_speaker = "Unknown"

    for line in lines:
        s = line.strip()
        if not s or s.upper() == "WEBVTT" or _TS.match(s) or _CUE_NUM.match(s):
            continue

        m = _VTT_SPEAKER.search(s)
        if m:
            current_speaker = m.group(1).strip()
            body = _clean(m.group(2))
            if body:
                raw.append(Segment(current_speaker, body))
            continue

        m = _INLINE_SPEAKER.match(s)
        if m and len(m.group(1).split()) <= 4:
            current_speaker = m.group(1).strip()
            body = _clean(m.group(2))
            if body:
                raw.append(Segment(current_speaker, body))
            continue

        # continuation of the current speaker
        body = _clean(s)
        if body:
            raw.append(Segment(current_speaker, body))

    # Merge consecutive same-speaker segments
    merged: list[Segment] = []
    for seg in raw:
        if merged and merged[-1].speaker == seg.speaker:
            merged[-1] = Segment(seg.speaker, f"{merged[-1].text} {seg.text}")
        else:
            merged.append(seg)
    return merged


def looks_like_transcript(path: str) -> bool:
    if path.lower().endswith((".vtt", ".srt")):
        return True
    if path.lower().endswith(".txt"):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                head = f.read(2000)
        except OSError:
            # Intentional: this is a content-sniff predicate — an unreadable
            # file simply isn't classified as a transcript (caller handles it).
            return False
        # Heuristic: several "Speaker:" lines
        speaker_lines = sum(1 for ln in head.splitlines() if _INLINE_SPEAKER.match(ln.strip()))
        return speaker_lines >= 3
    return False
