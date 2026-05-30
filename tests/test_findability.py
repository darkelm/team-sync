"""Tests for src/agent/findability.py — FindabilityLocator."""
from __future__ import annotations

import pytest


class TestFindabilityLocator:
    @pytest.fixture(autouse=True)
    def setup(self, providers):
        from src.agent.findability import FindabilityLocator
        self.fl = FindabilityLocator(providers)

    def test_find_returns_list(self):
        results = self.fl.find("auth")
        assert isinstance(results, list)

    def test_auth_finds_figma_file(self):
        """Phoenix Auth Flows is a Figma file that should match 'auth'."""
        results = self.fl.find("auth")
        assert len(results) >= 1
        kinds = [r.kind for r in results]
        assert "figma" in kinds

    def test_results_have_required_fields(self):
        results = self.fl.find("auth")
        for r in results:
            assert r.label
            assert r.name
            assert r.team
            assert r.url
            assert 0.0 <= r.score <= 1.0
            assert r.kind

    def test_results_sorted_by_score(self):
        results = self.fl.find("auth")
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_deduplication(self):
        """No URL should appear more than once in results."""
        results = self.fl.find("design system")
        urls = [r.url for r in results]
        assert len(urls) == len(set(urls))

    def test_below_threshold_returns_empty(self):
        """An arbitrary nonsense query should return nothing or very few results."""
        results = self.fl.find("xyzzy_frobble_nonexistent_thing_12345")
        assert len(results) == 0

    def test_max_results_cap(self):
        """Should return at most 8 results."""
        results = self.fl.find("team")
        assert len(results) <= 8
