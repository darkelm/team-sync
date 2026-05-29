import json
import os
from datetime import datetime
from typing import Optional
from ...core.schemas import Ticket, TicketStatus
from ..base import JiraProvider


class LocalJiraProvider(JiraProvider):
    def __init__(self, teams_dir: str):
        self.teams_dir = teams_dir
        self._tickets: list[Ticket] = []

    def _load(self) -> list[Ticket]:
        if self._tickets:
            return self._tickets
        for entry in os.scandir(self.teams_dir):
            if entry.is_dir():
                path = os.path.join(entry.path, "jira_tickets.json")
                if os.path.exists(path):
                    with open(path) as f:
                        for item in json.load(f):
                            self._tickets.append(Ticket(**item))
        return self._tickets

    def get_tickets(self, team: Optional[str] = None, status: Optional[str] = None) -> list[Ticket]:
        tickets = self._load()
        if team:
            tickets = [t for t in tickets if team.lower() in t.team.lower()]
        if status:
            tickets = [t for t in tickets if t.status.value == status]
        return tickets

    def get_ticket(self, ticket_id: str) -> Optional[Ticket]:
        return next((t for t in self._load() if t.id == ticket_id), None)

    def get_tickets_by_component(self, component: str) -> list[Ticket]:
        comp_lower = component.lower()
        return [t for t in self._load() if any(comp_lower in c.lower() for c in t.components)]

    def get_upcoming_deliverables(self, team: str) -> list[Ticket]:
        active = {TicketStatus.todo, TicketStatus.in_progress, TicketStatus.in_review}
        return [
            t for t in self._load()
            if team.lower() in t.team.lower() and t.status in active and t.due_date
        ]
