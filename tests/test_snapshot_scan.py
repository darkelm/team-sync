"""
Tests for snapshot_scan.py — nightly diff → Event emission.

Strategy
--------
- Build minimal in-memory snapshot directories using tmp_path.
- Monkeypatch EventRouter.dispatch to a stub (no real Slack calls).
- Assert that the right Events are emitted for:
    - New Jira tickets  → work.created
    - Changed due dates → roadmap.date_changed
    - New Figma components → design.component_changed (change=added)
    - Removed Figma components → design.component_changed (change=removed)
    - No change → no events
    - Empty old snapshot → treats everything as new
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from snapshot_scan import (
    _diff_figma_components,
    _diff_jira_tickets,
    run_snapshot_scan,
)


# ---------------------------------------------------------------------------
# Helpers to build snapshot directories
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _team_yaml(team_name: str, jira_project: str = "TST") -> str:
    return f"""team: {team_name}
description: Test team
owner:
  name: Owner Name
  role: Lead
  slack_handle: "@owner"
  email: owner@test.co
slack_channel: "#test-channel"
jira_project: {jira_project}
confluence_space: TEST
"""


# ---------------------------------------------------------------------------
# Unit tests for _diff_jira_tickets
# ---------------------------------------------------------------------------

class TestDiffJiraTickets:
    def test_new_ticket_emits_work_created(self):
        old = [{"id": "T-1", "title": "Existing ticket", "due_date": "2026-06-01"}]
        new = [
            {"id": "T-1", "title": "Existing ticket", "due_date": "2026-06-01"},
            {"id": "T-2", "title": "Brand new ticket", "due_date": ""},
        ]
        events = _diff_jira_tickets(old, new, "Team Test")
        assert len(events) == 1
        assert events[0].type == "work.created"
        assert events[0].subject == "Brand new ticket"
        assert events[0].team == "Team Test"
        assert events[0].source == "snapshot"

    def test_no_new_tickets_no_events(self):
        tickets = [{"id": "T-1", "title": "Same ticket", "due_date": "2026-06-01"}]
        events = _diff_jira_tickets(tickets, tickets, "Team Test")
        assert events == []

    def test_changed_due_date_emits_roadmap_date_changed(self):
        old = [{"id": "T-1", "title": "Q2 milestone", "due_date": "2026-06-01"}]
        new = [{"id": "T-1", "title": "Q2 milestone", "due_date": "2026-06-15"}]
        events = _diff_jira_tickets(old, new, "Team Test")
        assert len(events) == 1
        e = events[0]
        assert e.type == "roadmap.date_changed"
        assert e.subject == "Q2 milestone"
        assert e.metadata["due_date_old"] == "2026-06-01"
        assert e.metadata["due_date_new"] == "2026-06-15"

    def test_same_due_date_no_event(self):
        old = [{"id": "T-1", "title": "Ticket", "due_date": "2026-06-01"}]
        new = [{"id": "T-1", "title": "Ticket", "due_date": "2026-06-01"}]
        events = _diff_jira_tickets(old, new, "Team Test")
        assert events == []

    def test_null_due_date_not_emitted(self):
        """If new due_date is empty/null, don't fire roadmap event."""
        old = [{"id": "T-1", "title": "Ticket", "due_date": "2026-06-01"}]
        new = [{"id": "T-1", "title": "Ticket", "due_date": ""}]
        events = _diff_jira_tickets(old, new, "Team Test")
        # "" is falsy → no event
        assert events == []

    def test_multiple_new_tickets(self):
        old = []
        new = [
            {"id": "T-1", "title": "First", "due_date": ""},
            {"id": "T-2", "title": "Second", "due_date": ""},
            {"id": "T-3", "title": "Third", "due_date": ""},
        ]
        events = _diff_jira_tickets(old, new, "Team Test")
        assert len(events) == 3
        types = {e.type for e in events}
        assert types == {"work.created"}

    def test_new_ticket_and_due_date_change_combined(self):
        old = [{"id": "T-1", "title": "Old ticket", "due_date": "2026-06-01"}]
        new = [
            {"id": "T-1", "title": "Old ticket", "due_date": "2026-06-20"},  # changed
            {"id": "T-2", "title": "New ticket", "due_date": ""},            # new
        ]
        events = _diff_jira_tickets(old, new, "Team Test")
        types = [e.type for e in events]
        assert "work.created" in types
        assert "roadmap.date_changed" in types
        assert len(events) == 2

    def test_empty_old_treats_everything_as_new(self):
        new = [
            {"id": "T-1", "title": "A", "due_date": "2026-06-01"},
            {"id": "T-2", "title": "B", "due_date": ""},
        ]
        events = _diff_jira_tickets([], new, "Team Test")
        work_created = [e for e in events if e.type == "work.created"]
        assert len(work_created) == 2

    def test_metadata_includes_issue_key(self):
        old = []
        new = [{"id": "PHX-42", "title": "Some story", "status": "todo", "due_date": ""}]
        events = _diff_jira_tickets(old, new, "Team Phoenix")
        assert events[0].metadata["issue_key"] == "PHX-42"


# ---------------------------------------------------------------------------
# Unit tests for _diff_figma_components
# ---------------------------------------------------------------------------

class TestDiffFigmaComponents:
    def test_new_component_emits_added(self):
        old = [{"id": "comp-1", "name": "Button", "file_name": "DS"}]
        new = [
            {"id": "comp-1", "name": "Button", "file_name": "DS"},
            {"id": "comp-2", "name": "Modal", "file_name": "DS"},
        ]
        events = _diff_figma_components(old, new, "Team Nova")
        assert len(events) == 1
        e = events[0]
        assert e.type == "design.component_changed"
        assert e.subject == "Modal"
        assert e.metadata["change"] == "added"
        assert e.team == "Team Nova"

    def test_removed_component_emits_removed(self):
        old = [
            {"id": "comp-1", "name": "Button", "file_name": "DS"},
            {"id": "comp-2", "name": "Deprecated", "file_name": "DS"},
        ]
        new = [{"id": "comp-1", "name": "Button", "file_name": "DS"}]
        events = _diff_figma_components(old, new, "Team Nova")
        assert len(events) == 1
        assert events[0].metadata["change"] == "removed"
        assert events[0].subject == "Deprecated"

    def test_no_change_no_events(self):
        comps = [{"id": "comp-1", "name": "Button", "file_name": "DS"}]
        events = _diff_figma_components(comps, comps, "Team Nova")
        assert events == []

    def test_empty_old_all_new(self):
        new = [
            {"id": "c1", "name": "A", "file_name": "F"},
            {"id": "c2", "name": "B", "file_name": "F"},
        ]
        events = _diff_figma_components([], new, "Team Nova")
        assert len(events) == 2
        assert all(e.metadata["change"] == "added" for e in events)

    def test_fallback_to_name_key(self):
        """Components without 'id' should be keyed by 'name'."""
        old = [{"name": "ButtonBase", "file_name": "DS"}]
        new = [
            {"name": "ButtonBase", "file_name": "DS"},
            {"name": "IconButton", "file_name": "DS"},
        ]
        events = _diff_figma_components(old, new, "Team Nova")
        assert len(events) == 1
        assert events[0].subject == "IconButton"


# ---------------------------------------------------------------------------
# Integration tests for run_snapshot_scan
# ---------------------------------------------------------------------------

class TestRunSnapshotScan:
    @pytest.fixture()
    def dispatch_stub(self, monkeypatch):
        from src.agent.events import EventRouter
        calls: list[Any] = []

        def _stub(self, event):
            calls.append(event)
            return 1

        monkeypatch.setattr(EventRouter, "dispatch", _stub)
        return calls

    @pytest.fixture()
    def minimal_snapshot(self, tmp_path):
        """Create old and new snapshot directories for a single synthetic team."""
        old_root = tmp_path / "old"
        new_root = tmp_path / "new"

        # Old snapshot: one team, one ticket, one component
        _write_yaml(old_root / "team-test" / "team.yaml", _team_yaml("Team Test", "TST"))
        _write_json(old_root / "team-test" / "jira_tickets.json", [
            {"id": "TST-1", "title": "Old ticket", "due_date": "2026-06-01"},
        ])
        _write_json(old_root / "team-test" / "figma_components.json", [
            {"id": "c-old", "name": "OldButton", "file_name": "DS"},
        ])

        # New snapshot: same ticket with changed date, one new ticket, one new component
        _write_yaml(new_root / "team-test" / "team.yaml", _team_yaml("Team Test", "TST"))
        _write_json(new_root / "team-test" / "jira_tickets.json", [
            {"id": "TST-1", "title": "Old ticket", "due_date": "2026-06-15"},  # date changed
            {"id": "TST-2", "title": "New feature ticket", "due_date": ""},   # new
        ])
        _write_json(new_root / "team-test" / "figma_components.json", [
            {"id": "c-old", "name": "OldButton", "file_name": "DS"},   # unchanged
            {"id": "c-new", "name": "NewCard", "file_name": "DS"},     # added
        ])

        return old_root, new_root

    def test_run_scan_emits_correct_events(
        self, providers, dispatch_stub, minimal_snapshot
    ):
        old_dir, new_dir = minimal_snapshot
        total = run_snapshot_scan(str(old_dir), str(new_dir), providers)

        # Should have dispatched 3 events: date_changed + work.created + design.component_changed
        assert len(dispatch_stub) == 3
        types = {e.type for e in dispatch_stub}
        assert "roadmap.date_changed" in types
        assert "work.created" in types
        assert "design.component_changed" in types
        # total dispatched = 3 (one notification per event via stub)
        assert total == 3

    def test_run_scan_returns_dispatch_count(
        self, providers, dispatch_stub, minimal_snapshot
    ):
        old_dir, new_dir = minimal_snapshot
        total = run_snapshot_scan(str(old_dir), str(new_dir), providers)
        assert isinstance(total, int)
        assert total >= 0

    def test_run_scan_empty_old_dir(self, providers, dispatch_stub, tmp_path):
        """When old_dir doesn't exist, treat everything as new."""
        new_root = tmp_path / "new"
        _write_yaml(new_root / "team-test" / "team.yaml", _team_yaml("Team Test", "TST"))
        _write_json(new_root / "team-test" / "jira_tickets.json", [
            {"id": "TST-1", "title": "First ticket", "due_date": ""},
        ])
        _write_json(new_root / "team-test" / "figma_components.json", [
            {"id": "c1", "name": "Button", "file_name": "DS"},
        ])
        old_root = tmp_path / "nonexistent"
        run_snapshot_scan(str(old_root), str(new_root), providers)
        # 1 work.created + 1 design.component_changed
        assert len(dispatch_stub) == 2

    def test_run_scan_no_changes_no_events(self, providers, dispatch_stub, tmp_path):
        """Identical old and new → zero events."""
        tickets = [{"id": "TST-1", "title": "Ticket", "due_date": "2026-06-01"}]
        comps = [{"id": "c1", "name": "Button", "file_name": "DS"}]

        for root_name in ("old", "new"):
            root = tmp_path / root_name
            _write_yaml(root / "team-test" / "team.yaml", _team_yaml("Team Test", "TST"))
            _write_json(root / "team-test" / "jira_tickets.json", tickets)
            _write_json(root / "team-test" / "figma_components.json", comps)

        total = run_snapshot_scan(
            str(tmp_path / "old"), str(tmp_path / "new"), providers
        )
        assert total == 0
        assert dispatch_stub == []

    def test_run_scan_on_synthetic_org(self, providers, dispatch_stub, tmp_path):
        """
        Smoke-test: run against the real synthetic teams_dir with an empty old
        snapshot.  Every component and ticket in the synthetic org will appear
        as 'new'.  Just assert it runs without error and dispatched > 0.
        """
        import os
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        teams_dir = os.path.join(repo_root, "data", "synthetic", "teams")
        empty_old = str(tmp_path / "empty_old")
        total = run_snapshot_scan(empty_old, teams_dir, providers)
        assert isinstance(total, int)
        # With empty old dir, every ticket+component in synthetic org is "new"
        assert len(dispatch_stub) > 0
