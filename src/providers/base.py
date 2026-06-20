"""Abstract provider interfaces. The agent layer only calls these — never the implementations directly."""
from abc import ABC, abstractmethod
from typing import Optional
from ..core.schemas import (
    TeamManifest, Ticket, ConfluencePage, PullRequest,
    FigmaComponent, DriftIssue,
    FigmaDevStatus, FigmaComment, FigmaChange,
)


class JiraProvider(ABC):
    @abstractmethod
    def get_tickets(self, team: Optional[str] = None, status: Optional[str] = None) -> list[Ticket]: ...

    @abstractmethod
    def get_ticket(self, ticket_id: str) -> Optional[Ticket]: ...

    @abstractmethod
    def get_tickets_by_component(self, component: str) -> list[Ticket]: ...

    @abstractmethod
    def get_upcoming_deliverables(self, team: str) -> list[Ticket]: ...


class ConfluenceProvider(ABC):
    @abstractmethod
    def get_pages(self, space: Optional[str] = None, team: Optional[str] = None) -> list[ConfluencePage]: ...

    @abstractmethod
    def search_pages(self, query: str, team: Optional[str] = None) -> list[ConfluencePage]: ...

    @abstractmethod
    def get_decision_logs(self, team: Optional[str] = None, component: Optional[str] = None) -> list[ConfluencePage]: ...


class GitHubProvider(ABC):
    @abstractmethod
    def get_pull_requests(self, team: Optional[str] = None, status: Optional[str] = None) -> list[PullRequest]: ...

    @abstractmethod
    def get_recent_prs(self, days: int = 7) -> list[PullRequest]: ...

    @abstractmethod
    def get_prs_touching_component(self, component: str) -> list[PullRequest]: ...


class FigmaProvider(ABC):
    @abstractmethod
    def get_components(self, team: Optional[str] = None) -> list[FigmaComponent]: ...

    @abstractmethod
    def get_library_components(self) -> list[FigmaComponent]: ...

    @abstractmethod
    def get_components_by_name(self, name: str) -> list[FigmaComponent]: ...

    @abstractmethod
    def get_drift_issues(self) -> list[DriftIssue]: ...

    # ── Figma-native coordination signals ────────────────────────────────────
    @abstractmethod
    def get_dev_status(self, team: Optional[str] = None) -> list[FigmaDevStatus]:
        """Dev-handoff readiness (ready-for-dev status + linked tickets) per frame."""
        ...

    @abstractmethod
    def get_open_comments(self, team: Optional[str] = None) -> list[FigmaComment]:
        """Open (unresolved) Figma comment threads, surfacing high-priority ones."""
        ...

    @abstractmethod
    def get_recent_changes(self, team: Optional[str] = None, days: int = 7) -> list[FigmaChange]:
        """Recent version/frame changes on each team's Figma file within `days`."""
        ...


class SlackProvider(ABC):
    @abstractmethod
    def post_message(self, channel: str, text: str, blocks: Optional[list] = None) -> bool: ...

    @abstractmethod
    def post_digest(self, channel: str, digest_text: str) -> bool: ...


class ManifestProvider(ABC):
    @abstractmethod
    def get_all_teams(self) -> list[TeamManifest]: ...

    @abstractmethod
    def get_team(self, team_name: str) -> Optional[TeamManifest]: ...

    @abstractmethod
    def find_component_owner(self, component_name: str) -> Optional[TeamManifest]: ...

    @abstractmethod
    def get_dependents(self, team_name: str) -> list[TeamManifest]: ...
