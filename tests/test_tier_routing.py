"""Tests for the real (non-placeholder) TIER signal in the governance membrane.

Before this work, `EventRouter._event_route_item()` defaulted EVERY event to tier
"raw" unless `event.metadata["tier"]` overrode it. Now the tier is derived from the
actual component (its manifest `tier`) named by `event.subject`, while an explicit
metadata tier still wins. These tests assert that contract end to end:

  (a) a brand-tier component resolves to tier "brand" via _event_route_item/tier_of,
  (b) a raw component resolves to "raw",
  (c) an explicit metadata["tier"] still overrides the lookup,
  (d) an unknown component falls back to "raw" without error,
  plus an end-to-end check that the resolved tier actually changes a routing
  outcome under a tier-keyed policy (compile_policy_from_toggles).

Hermetic: runs over the synthetic org via the `providers` fixture (SYNCBOT_TEST=1,
no API keys). Synthetic tiers are stamped at load by LocalManifestProvider.
"""
from __future__ import annotations

import pytest

from src.agent import membrane
from src.agent.events import EventRouter, Event


FIXED_NOW = "2026-06-19T00:00:00.000Z"
OPTS = {"now": lambda: FIXED_NOW}


class TestTierResolution:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        self.router = EventRouter(providers)

    def _tier_of_event(self, event: Event) -> str:
        """The membrane's view of an event's tier: build the RouteItem the way the
        router does, then read the tier back off the path head via tier_of()."""
        item = self.router._event_route_item(event)
        return membrane.tier_of(item)

    # (a) a brand-tier component resolves to tier "brand" ----------------------
    def test_brand_component_resolves_to_brand(self):
        # "Button" is a Team Nova design-system primitive → stamped "brand".
        event = Event(type="design.library_published", subject="Button", team="Team Nova")
        assert self._tier_of_event(event) == "brand"

    def test_design_system_resolves_to_brand(self):
        event = Event(type="code.merged", subject="design-system", team="Team Nova")
        assert self._tier_of_event(event) == "brand"

    # shared-tier sanity (the middle bucket) -----------------------------------
    def test_shared_component_resolves_to_shared(self):
        # NotificationBell is consumed across teams → stamped "shared".
        event = Event(type="design.library_published", subject="NotificationBell", team="Team Nova")
        assert self._tier_of_event(event) == "shared"

    # (b) a raw component resolves to "raw" ------------------------------------
    def test_raw_component_resolves_to_raw(self):
        # "Badge" is a leaf component with no tier stamp → "raw".
        event = Event(type="design.library_published", subject="Badge", team="Team Nova")
        assert self._tier_of_event(event) == "raw"

    # (c) explicit metadata["tier"] still overrides the lookup -----------------
    def test_metadata_tier_overrides_brand_component(self):
        # Subject would resolve to "brand" from the manifest, but metadata wins.
        event = Event(
            type="design.library_published",
            subject="Button",
            team="Team Nova",
            metadata={"tier": "raw"},
        )
        assert self._tier_of_event(event) == "raw"

    def test_metadata_tier_overrides_raw_component_upward(self):
        # And it overrides in the other direction too (raw subject → forced brand).
        event = Event(
            type="design.library_published",
            subject="Badge",
            team="Team Nova",
            metadata={"tier": "brand"},
        )
        assert self._tier_of_event(event) == "brand"

    # (d) unknown component falls back to "raw" without error ------------------
    def test_unknown_component_falls_back_to_raw(self):
        event = Event(type="design.library_published", subject="TotallyNotARealComponent", team="Team X")
        assert self._tier_of_event(event) == "raw"

    def test_empty_subject_falls_back_to_raw_without_error(self):
        event = Event(type="decision.logged", subject="")
        # _event_route_item must not raise on an empty subject.
        item = self.router._event_route_item(event)
        assert membrane.tier_of(item) == "raw"


class TestTierChangesRoutingOutcome:
    """End-to-end: the resolved tier actually changes the lane under a tier-keyed
    policy. With `brand_changes_always_ask` on, a brand-tier event is forced to
    REVIEW even when an auto-granting toggle would otherwise let a low-reach change
    flow — but the same policy lets a low-reach RAW change reach AUTO."""

    @pytest.fixture(autouse=True)
    def setup(self, providers):
        self.router = EventRouter(providers)

    def test_brand_event_routes_to_review_while_raw_event_can_auto(self):
        policy = membrane.compile_policy_from_toggles(
            membrane.DesignerToggles(
                brand_changes_always_ask=True,
                small_tweaks_flow=True,  # the ONLY way anything reaches auto
            )
        )

        # Brand-tier subject → the brand "always ask" rule forces REVIEW.
        brand_event = Event(type="design.library_published", subject="Button", team="Team Nova")
        brand_decision = self.router.route_lane(brand_event, policy, **OPTS)
        assert brand_decision.lane == membrane.Lane.REVIEW

        # Raw-tier subject confined to a low reach → earns AUTO under the same policy.
        raw_event = Event(type="design.component_changed", subject="Badge", team="Team Nova")
        raw_decision = self.router.route_lane(raw_event, policy, **OPTS)
        assert raw_decision.lane == membrane.Lane.AUTO

    def test_metadata_override_to_brand_forces_review(self):
        """Proves the override path feeds routing, not just tier_of: forcing a raw
        leaf to brand via metadata flips its lane from AUTO to REVIEW."""
        policy = membrane.compile_policy_from_toggles(
            membrane.DesignerToggles(brand_changes_always_ask=True, small_tweaks_flow=True)
        )
        # Same raw subject, but metadata declares it brand → must REVIEW now.
        event = Event(
            type="design.component_changed",
            subject="Badge",
            team="Team Nova",
            metadata={"tier": "brand"},
        )
        decision = self.router.route_lane(event, policy, **OPTS)
        assert decision.lane == membrane.Lane.REVIEW
