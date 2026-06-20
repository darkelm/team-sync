"""Import a Confluence space export (HTML or Markdown) into ConfluencePage JSON.

No API access required — works with the space export ZIP Confluence produces
(Space Settings > Export > HTML/PDF/XML), unzipped to a folder, or any folder
of .md / .html pages.

Decision logs are auto-detected: pages whose title or body contains
"decision" are parsed into a DecisionLog with a best-effort rationale.
"""
from __future__ import annotations
import os
import re
from datetime import date
from html.parser import HTMLParser
from ..core.schemas import ConfluencePage, DecisionLog


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return " ".join(self.parts)


def _strip_html(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return p.text()


def _title_from(content: str, fallback: str) -> str:
    m = re.search(r"<title>(.*?)</title>", content, re.I | re.S)
    if m:
        return _strip_html(m.group(1)).strip()
    m = re.search(r"^#\s+(.+)$", content, re.M)
    if m:
        return m.group(1).strip()
    m = re.search(r"<h1[^>]*>(.*?)</h1>", content, re.I | re.S)
    if m:
        return _strip_html(m.group(1)).strip()
    return fallback


def _looks_like_decision(title: str, body: str) -> bool:
    t = title.lower()
    return "decision" in t or "adr" in t or "rfc" in t or "decided" in body.lower()[:500]


def _extract_section(body: str, *keywords: str) -> str:
    """Pull the sentence(s) following a keyword like 'rationale' or 'decision'."""
    for kw in keywords:
        m = re.search(rf"{kw}[:\s]+(.+?)(?:\.|$)", body, re.I)
        if m:
            return m.group(1).strip()[:500]
    return ""


def import_confluence_export(folder: str, team: str, space: str = "") -> list[ConfluencePage]:
    pages: list[ConfluencePage] = []
    for root, _, files in os.walk(folder):
        for fname in files:
            if not fname.lower().endswith((".html", ".htm", ".md", ".markdown")):
                continue
            path = os.path.join(root, fname)
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    raw = f.read()
            except OSError:
                continue

            is_html = fname.lower().endswith((".html", ".htm"))
            body = _strip_html(raw) if is_html else re.sub(r"[#*_`>]", "", raw)
            title = _title_from(raw, os.path.splitext(fname)[0])
            mtime = date.fromtimestamp(os.path.getmtime(path))

            decision_log = None
            if _looks_like_decision(title, body):
                decision = _extract_section(body, "decision", "we decided", "chose to") or body[:200]
                rationale = _extract_section(body, "rationale", "because", "reason", "why") or "Not explicitly stated in export."
                decision_log = DecisionLog(
                    id=f"DEC-{team[:3].upper()}-{len(pages)+1}",
                    title=title,
                    decision=decision,
                    rationale=rationale,
                    decided_by=[],
                    date=mtime,
                    status="approved",
                    team=team,
                )

            pages.append(ConfluencePage(
                id=os.path.splitext(fname)[0],
                title=title,
                space=space or team,
                team=team,
                content_summary=body[:400],
                tags=["decision-log"] if decision_log else [],
                last_updated=mtime,
                author="",
                url=f"file://{path}",
                decision_log=decision_log,
            ))
    return pages
