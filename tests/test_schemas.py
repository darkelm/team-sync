"""Tests for src/core/schemas.py — data model correctness."""
from __future__ import annotations

from datetime import date, datetime


class TestTicketSchema:
    def test_ticket_status_values(self):
        from src.core.schemas import TicketStatus
        expected = {"todo", "in_progress", "in_review", "blocked", "done", "backlog"}
        actual = {s.value for s in TicketStatus}
        assert expected == actual

    def test_ticket_priority_values(self):
        from src.core.schemas import TicketPriority
        expected = {"critical", "high", "medium", "low"}
        actual = {p.value for p in TicketPriority}
        assert expected == actual

    def test_ticket_creation(self):
        from src.core.schemas import Ticket, TicketStatus, TicketPriority
        from datetime import datetime
        now = datetime.now()
        t = Ticket(
            id="PHX-1",
            title="Test ticket",
            description="A test",
            status=TicketStatus.todo,
            priority=TicketPriority.medium,
            team="Team Phoenix",
            created_at=now,
            updated_at=now,
        )
        assert t.id == "PHX-1"
        assert t.status == TicketStatus.todo


class TestPullRequestSchema:
    def test_pr_status_values(self):
        from src.core.schemas import PRStatus
        assert PRStatus.open.value == "open"
        assert PRStatus.merged.value == "merged"

    def test_pr_creation(self):
        from src.core.schemas import PullRequest, PRStatus
        pr = PullRequest(
            id="123",
            title="Test PR",
            description="",
            status=PRStatus.merged,
            author="alice",
            team="Team Phoenix",
            base_branch="main",
            head_branch="feat/test",
            created_at=datetime.now(),
        )
        assert pr.id == "123"
        assert pr.status == PRStatus.merged


class TestDriftSeveritySchema:
    def test_severity_values(self):
        from src.core.schemas import DriftSeverity
        values = {s.value for s in DriftSeverity}
        assert "critical" in values
        assert "high" in values
        assert "medium" in values
        assert "low" in values


class TestJourneySchema:
    def test_journey_creation(self):
        from src.core.schemas import Journey
        j = Journey(
            name="Test Journey",
            description="A test journey",
            teams=["Team Phoenix", "Team Atlas"],
            components=["auth", "login"],
            owner="Team Phoenix",
            north_star="Seamless auth",
        )
        assert j.name == "Test Journey"
        assert len(j.teams) == 2

    def test_journey_loaded_from_yaml(self):
        """Journeys loaded by StrategyLens should be valid Journey objects."""
        import os
        import yaml
        REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        journeys_path = os.path.join(REPO_ROOT, "data", "synthetic", "journeys.yaml")
        with open(journeys_path) as f:
            data = yaml.safe_load(f)
        from src.core.schemas import Journey
        journeys = [Journey(**j) for j in data.get("journeys", [])]
        assert len(journeys) == 3
        names = [j.name for j in journeys]
        assert "Onboarding" in names


class TestExperiencePrincipleSchema:
    def test_principle_loaded_from_yaml(self):
        import os
        import yaml
        REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(REPO_ROOT, "data", "synthetic", "experience_principles.yaml")
        with open(path) as f:
            data = yaml.safe_load(f)
        from src.core.schemas import ExperiencePrinciple
        principles = [ExperiencePrinciple(**p) for p in data.get("principles", [])]
        assert len(principles) == 4


class TestDecisionLogSchema:
    def test_decision_log_creation(self):
        from src.core.schemas import DecisionLog
        dl = DecisionLog(
            id="DEC-1",
            title="Use OAuth",
            decision="We will use OAuth 2.0",
            rationale="Industry standard",
            decided_by=["Alice", "Bob"],
            date=date.today(),
            status="approved",
            team="Team Phoenix",
        )
        assert dl.id == "DEC-1"
        assert dl.status == "approved"
