"""Tests for the Figma-native coordination signals on the LOCAL provider.

These exercise the new provider methods — get_dev_status, get_open_comments,
get_recent_changes — against the synthetic figma_dev_status.json fixtures.
They run fully offline (no FIGMA_ACCESS_TOKEN, no HTTP).

The synthetic signal fixtures are dated late May 2026, so recent-changes tests
that need the dates *inside* the window use a wide `days` value (the fixture is
absolute-dated, wall-clock "now" moves on), and the cutoff behaviour is tested
with a deterministic frozen clock.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.schemas import (
    DevReadiness, FigmaDevStatus, FigmaComment, FigmaChange, TicketPriority,
)


@pytest.fixture
def figma(providers):
    return providers.figma


# ---------------------------------------------------------------------------
# get_dev_status
# ---------------------------------------------------------------------------

class TestGetDevStatus:
    def test_returns_list_of_dev_status(self, figma):
        statuses = figma.get_dev_status()
        assert isinstance(statuses, list)
        assert len(statuses) >= 1
        assert all(isinstance(s, FigmaDevStatus) for s in statuses)

    def test_ready_for_dev_present(self, figma):
        statuses = figma.get_dev_status()
        ready = [s for s in statuses if s.readiness == DevReadiness.ready_for_dev]
        assert len(ready) >= 1

    def test_blocked_status_present(self, figma):
        statuses = figma.get_dev_status()
        blocked = [s for s in statuses if s.readiness == DevReadiness.blocked]
        assert len(blocked) >= 1

    def test_linked_tickets_carried(self, figma):
        """At least one ready-for-dev frame carries linked tickets."""
        statuses = figma.get_dev_status("Team Phoenix")
        with_tickets = [s for s in statuses if s.linked_tickets]
        assert with_tickets
        # The session-expired frame is wired to PHX-118 / PHX-119 in the fixture.
        all_tickets = {t for s in statuses for t in s.linked_tickets}
        assert "PHX-118" in all_tickets

    def test_team_filter_applied(self, figma):
        statuses = figma.get_dev_status("Phoenix")
        assert statuses
        assert all(s.team == "Team Phoenix" for s in statuses)

    def test_team_filter_excludes_others(self, figma):
        phoenix = figma.get_dev_status("Phoenix")
        atlas = figma.get_dev_status("Atlas")
        phx_nodes = {s.node_id for s in phoenix}
        atl_nodes = {s.node_id for s in atlas}
        assert phx_nodes.isdisjoint(atl_nodes)

    def test_required_fields(self, figma):
        for s in figma.get_dev_status():
            assert s.node_id
            assert s.name
            assert s.team
            assert isinstance(s.readiness, DevReadiness)
            assert isinstance(s.last_modified, datetime)
            assert isinstance(s.linked_tickets, list)


# ---------------------------------------------------------------------------
# get_open_comments
# ---------------------------------------------------------------------------

class TestGetOpenComments:
    def test_returns_list_of_comments(self, figma):
        comments = figma.get_open_comments()
        assert isinstance(comments, list)
        assert len(comments) >= 1
        assert all(isinstance(c, FigmaComment) for c in comments)

    def test_resolved_comments_filtered_out(self, figma):
        comments = figma.get_open_comments()
        assert all(not c.resolved for c in comments)
        # phx-cmt-003 is resolved in the fixture and must not appear.
        ids = {c.id for c in comments}
        assert "phx-cmt-003" not in ids

    def test_high_priority_present(self, figma):
        comments = figma.get_open_comments()
        high = [c for c in comments if c.priority == TicketPriority.high]
        assert len(high) >= 1

    def test_sorted_highest_priority_first(self, figma):
        """High-priority blockers should sort ahead of low-priority nits."""
        rank = {
            TicketPriority.critical: 0, TicketPriority.high: 1,
            TicketPriority.medium: 2, TicketPriority.low: 3,
        }
        comments = figma.get_open_comments()
        ranks = [rank[c.priority] for c in comments]
        assert ranks == sorted(ranks)

    def test_team_filter_applied(self, figma):
        comments = figma.get_open_comments("Phoenix")
        assert comments
        assert all(c.team == "Team Phoenix" for c in comments)

    def test_required_fields(self, figma):
        for c in figma.get_open_comments():
            assert c.id
            assert c.team
            assert c.message
            assert isinstance(c.created_at, datetime)
            assert isinstance(c.priority, TicketPriority)


# ---------------------------------------------------------------------------
# get_recent_changes
# ---------------------------------------------------------------------------

class TestGetRecentChanges:
    def test_returns_list_of_changes(self, figma):
        # Wide window so the late-May fixture dates fall inside it regardless of
        # how far wall-clock "now" has moved past them.
        changes = figma.get_recent_changes(days=3650)
        assert isinstance(changes, list)
        assert len(changes) >= 1
        assert all(isinstance(c, FigmaChange) for c in changes)

    def test_sorted_newest_first(self, figma):
        changes = figma.get_recent_changes(days=3650)
        times = [c.changed_at for c in changes]
        assert times == sorted(times, reverse=True)

    def test_team_filter_applied(self, figma):
        changes = figma.get_recent_changes("Phoenix", days=3650)
        assert changes
        assert all(c.team == "Team Phoenix" for c in changes)

    def test_window_excludes_old_changes(self, figma, monkeypatch):
        """With a frozen clock and a narrow 7-day window, the late-May fixture
        changes fall outside the window and are excluded."""
        import src.providers.local.figma as fig

        FROZEN = datetime(2026, 6, 19, tzinfo=timezone.utc)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return FROZEN if tz is None else FROZEN.astimezone(tz)

        monkeypatch.setattr(fig, "datetime", _FrozenDatetime)
        # cutoff = 2026-06-12; all fixture changes are <= 2026-05-27 -> excluded.
        assert figma.get_recent_changes(days=7) == []

    def test_window_includes_recent_changes(self, figma, monkeypatch):
        """Same frozen clock, but a window wide enough to capture late-May."""
        import src.providers.local.figma as fig

        FROZEN = datetime(2026, 6, 19, tzinfo=timezone.utc)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return FROZEN if tz is None else FROZEN.astimezone(tz)

        monkeypatch.setattr(fig, "datetime", _FrozenDatetime)
        # cutoff = 2026-05-19; captures the 2026-05-22/26/27 fixture changes.
        changes = figma.get_recent_changes(days=31)
        assert len(changes) >= 1

    def test_required_fields(self, figma):
        for c in figma.get_recent_changes(days=3650):
            assert c.id
            assert c.team
            assert c.label
            assert isinstance(c.changed_at, datetime)


# ---------------------------------------------------------------------------
# Guardrail: new signals must not disturb existing component/drift behaviour
# ---------------------------------------------------------------------------

class TestDriftUnaffected:
    def test_design_drift_still_four(self, figma):
        """Loading the new signals must not change the drift count."""
        drift = figma.get_drift_issues()
        assert len(drift) == 4

    def test_components_unchanged(self, figma):
        components = figma.get_components()
        # The synthetic org has 8 figma components across the five teams.
        assert len(components) == 8
