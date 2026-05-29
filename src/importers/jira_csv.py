"""Import a Jira CSV export into normalized Ticket JSON.

No API access required — works with the standard CSV any Jira user can export
from the issue navigator (Export > Export Excel CSV / CSV all fields).

Jira CSV quirks handled:
- Multi-value fields (Labels, Components, linked issues) repeat the same
  column header multiple times; we collect every matching column.
- Column names vary by instance; we match case-insensitively on known aliases.
- Status/priority strings are normalized into our enums by keyword.
"""
from __future__ import annotations
import csv
from datetime import datetime, date
from typing import Optional
from ..core.schemas import Ticket, TicketStatus, TicketPriority


# Header aliases (lowercased). First match wins for single-value fields;
# all matches are collected for multi-value fields.
SINGLE = {
    "id": ["issue key", "key"],
    "title": ["summary"],
    "description": ["description"],
    "status": ["status"],
    "priority": ["priority"],
    "assignee": ["assignee"],
    "due_date": ["due date", "duedate"],
    "created_at": ["created"],
    "updated_at": ["updated"],
    "epic": ["epic link", "parent", "parent summary"],
}
MULTI = {
    "labels": ["labels"],
    "components": ["component/s", "components", "component"],
    "linked_tickets": ["inward issue link", "outward issue link", "linked issues"],
}


def _norm_status(raw: str) -> TicketStatus:
    s = (raw or "").strip().lower()
    if not s:
        return TicketStatus.backlog
    if "progress" in s:
        return TicketStatus.in_progress
    if "review" in s:
        return TicketStatus.in_review
    if "block" in s:
        return TicketStatus.blocked
    if "done" in s or "closed" in s or "resolved" in s or "complete" in s:
        return TicketStatus.done
    if "backlog" in s:
        return TicketStatus.backlog
    if "to do" in s or "todo" in s or "open" in s or "selected" in s:
        return TicketStatus.todo
    return TicketStatus.todo


def _norm_priority(raw: str) -> TicketPriority:
    p = (raw or "").strip().lower()
    if "blocker" in p or "critical" in p or "highest" in p:
        return TicketPriority.critical
    if "high" in p or "major" in p:
        return TicketPriority.high
    if "low" in p or "minor" in p or "trivial" in p:
        return TicketPriority.low
    return TicketPriority.medium


def _parse_dt(raw: str) -> Optional[datetime]:
    if not raw or not raw.strip():
        return None
    for fmt in ("%d/%b/%y %I:%M %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    # last resort: date only
    for fmt in ("%d/%b/%y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def _parse_date(raw: str) -> Optional[date]:
    dt = _parse_dt(raw)
    return dt.date() if dt else None


def _build_index(header: list[str]) -> tuple[dict, dict]:
    """Map each logical field to column index(es)."""
    lower = [h.strip().lower() for h in header]
    single_idx: dict[str, int] = {}
    for field, aliases in SINGLE.items():
        for alias in aliases:
            if alias in lower:
                single_idx[field] = lower.index(alias)
                break
    multi_idx: dict[str, list[int]] = {}
    for field, aliases in MULTI.items():
        idxs = [i for i, h in enumerate(lower) if h in aliases]
        if idxs:
            multi_idx[field] = idxs
    return single_idx, multi_idx


def import_jira_csv(csv_path: str, team: str) -> list[Ticket]:
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return []

    header, data_rows = rows[0], rows[1:]
    single_idx, multi_idx = _build_index(header)
    now = datetime.now()
    tickets: list[Ticket] = []

    for row in data_rows:
        if not any(cell.strip() for cell in row):
            continue

        def get(field: str, default: str = "") -> str:
            i = single_idx.get(field)
            return row[i].strip() if i is not None and i < len(row) else default

        def get_multi(field: str) -> list[str]:
            vals = []
            for i in multi_idx.get(field, []):
                if i < len(row) and row[i].strip():
                    vals.append(row[i].strip())
            return vals

        ticket_id = get("id")
        if not ticket_id:
            continue

        tickets.append(Ticket(
            id=ticket_id,
            title=get("title") or ticket_id,
            description=get("description"),
            status=_norm_status(get("status")),
            priority=_norm_priority(get("priority")),
            assignee=get("assignee") or None,
            team=team,
            labels=get_multi("labels"),
            due_date=_parse_date(get("due_date")),
            created_at=_parse_dt(get("created_at")) or now,
            updated_at=_parse_dt(get("updated_at")) or now,
            linked_tickets=get_multi("linked_tickets"),
            components=get_multi("components"),
            epic=get("epic") or None,
        ))

    return tickets
