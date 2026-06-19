import os
import httpx
from typing import Optional
from ...core.schemas import Ticket, TicketStatus, TicketPriority
from ..base import JiraProvider
from datetime import datetime


class LiveJiraProvider(JiraProvider):
    def __init__(self):
        self.base_url = os.environ["ATLASSIAN_URL"].rstrip("/")
        self.email = os.environ["ATLASSIAN_EMAIL"]
        self.token = os.environ["ATLASSIAN_API_TOKEN"]
        self.auth = (self.email, self.token)

    def _get(self, path: str, params: dict = None) -> dict:
        r = httpx.get(f"{self.base_url}/rest/api/3{path}", params=params, auth=self.auth)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError:
            # Surface live-API failures (401 bad token, 403 restricted, 429 rate limit)
            # so they don't silently read as "no data" upstream.
            print(f"[jira] GET {path} -> HTTP {r.status_code}: {r.text[:200]}", flush=True)
            raise
        return r.json()

    def _to_ticket(self, item: dict) -> Ticket:
        fields = item["fields"]
        return Ticket(
            id=item["key"],
            title=fields["summary"],
            description=(fields.get("description") or {}).get("text", "") if isinstance(fields.get("description"), dict) else str(fields.get("description") or ""),
            status=TicketStatus(fields["status"]["statusCategory"]["key"].replace(" ", "_").lower()),
            priority=TicketPriority(fields["priority"]["name"].lower()) if fields.get("priority") else TicketPriority.medium,
            assignee=fields.get("assignee", {}).get("displayName") if fields.get("assignee") else None,
            team=fields.get("project", {}).get("key", ""),
            labels=fields.get("labels", []),
            due_date=fields.get("duedate"),
            created_at=datetime.fromisoformat(fields["created"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(fields["updated"].replace("Z", "+00:00")),
            components=[c["name"] for c in fields.get("components", [])],
        )

    def _search(self, jql: str, max_results: int = 100) -> list[Ticket]:
        try:
            data = self._get("/search", {"jql": jql, "maxResults": max_results})
            return [self._to_ticket(i) for i in data.get("issues", [])]
        except Exception:
            return []

    def get_tickets(self, team: Optional[str] = None, status: Optional[str] = None) -> list[Ticket]:
        jql = f'project = "{team}"' if team else 'order by updated DESC'
        if status:
            jql += f' AND status = "{status}"'
        return self._search(jql)

    def get_ticket(self, ticket_id: str) -> Optional[Ticket]:
        try:
            data = self._get(f"/issue/{ticket_id}")
            return self._to_ticket(data)
        except Exception:
            return None

    def get_tickets_by_component(self, component: str) -> list[Ticket]:
        return self._search(f'component = "{component}"', max_results=50)

    def get_upcoming_deliverables(self, team: str) -> list[Ticket]:
        jql = f'project = "{team}" AND status in ("To Do", "In Progress", "In Review") AND dueDate is not EMPTY ORDER BY dueDate ASC'
        data = self._get("/search", {"jql": jql, "maxResults": 20})
        return [self._to_ticket(i) for i in data.get("issues", [])]
