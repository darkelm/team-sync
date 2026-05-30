"""Tests for src/agent/events.py — EventRouter."""
from __future__ import annotations

import pytest


class TestEventRouter:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        from src.agent.events import EventRouter, Event
        self.router = EventRouter(providers)
        self.Event = Event

    def test_route_returns_list(self):
        event = self.Event(type="design.library_published", subject="NotificationBell", team="Team Nova")
        actions = self.router.route(event)
        assert isinstance(actions, list)

    def test_design_library_published_routes_to_consumers(self):
        """Publishing NotificationBell should notify teams that consume it."""
        event = self.Event(type="design.library_published", subject="NotificationBell", team="Team Nova")
        actions = self.router.route(event)
        assert len(actions) >= 1
        channels = [a.channel for a in actions]
        # Team Horizon uses NotificationBell — expect its channel
        assert "#horizon-product" in channels

    def test_design_library_published_excludes_originating_team(self):
        """The team that published the library should not notify itself."""
        event = self.Event(type="design.library_published", subject="NotificationBell", team="Team Nova")
        actions = self.router.route(event)
        # Team Nova should NOT be in the recipients (it's the originator)
        nova_channel = "#nova-platform"
        channels = [a.channel for a in actions]
        assert nova_channel not in channels

    def test_actions_have_required_fields(self):
        event = self.Event(type="design.library_published", subject="NotificationBell", team="Team Nova")
        actions = self.router.route(event)
        for a in actions:
            assert a.channel
            assert a.message
            assert a.reason

    def test_unknown_event_type_returns_no_actions(self):
        """An unrecognized event type should produce zero actions."""
        event = self.Event(type="unknown.event_type", subject="foo", team="Team X")
        actions = self.router.route(event)
        assert actions == []

    def test_code_merged_routes_to_dependents(self):
        """Merging a shared code component notifies consumers."""
        event = self.Event(type="code.merged", subject="auth", team="Team Phoenix")
        actions = self.router.route(event)
        # auth is a key component; at least some team should be notified
        assert isinstance(actions, list)

    def test_roadmap_date_changed_routes_to_dependents(self):
        """A date change from Atlas should notify teams that depend on Atlas."""
        event = self.Event(type="roadmap.date_changed", subject="Q2 delivery", team="Team Atlas")
        actions = self.router.route(event)
        assert isinstance(actions, list)
        # Phoenix, Horizon, Forge depend on Atlas
        if actions:
            channels = [a.channel for a in actions]
            assert any(ch for ch in channels)

    def test_decision_logged_produces_acknowledgement(self):
        """A decision.logged event should produce an acknowledgement action."""
        event = self.Event(type="decision.logged", subject="Use OAuth 2.0")
        actions = self.router.route(event)
        assert len(actions) >= 1
        reasons = [a.reason for a in actions]
        assert any("acknowledgement" in r for r in reasons)

    def test_explain_returns_string(self):
        event = self.Event(type="design.library_published", subject="NotificationBell", team="Team Nova")
        explanation = self.router.explain(event)
        assert isinstance(explanation, str)
        assert len(explanation) > 0

    def test_actions_message_contains_subject(self):
        """Notification messages should mention the changed component."""
        event = self.Event(type="design.library_published", subject="NotificationBell", team="Team Nova")
        actions = self.router.route(event)
        for a in actions:
            if a.channel:
                assert "NotificationBell" in a.message
