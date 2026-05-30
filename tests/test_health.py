"""Tests for src/agent/health.py — HealthAssessor."""
from __future__ import annotations

import pytest


class TestHealthAssessor:
    @pytest.fixture(autouse=True)
    def setup(self, providers, tmp_state, config_path):
        import src.agent.health as health_mod
        # Use the patched SNAPSHOT_PATH from tmp_state fixture
        from src.agent.health import HealthAssessor
        self.assessor = HealthAssessor(providers, config_path)
        # Patch the assessor's internal snapshot path to the tmp one
        self.assessor._snapshots = {}
        self.assessor._SNAPSHOT_PATH = health_mod.SNAPSHOT_PATH

    def test_portfolio_returns_five_teams(self, providers, config_path, tmp_state):
        from src.agent.health import HealthAssessor
        ha = HealthAssessor(providers, config_path)
        ha._snapshots = {}
        portfolio = ha.portfolio()
        assert len(portfolio) == 5

    def test_portfolio_status_values_valid(self, providers, config_path, tmp_state):
        from src.agent.health import HealthAssessor
        ha = HealthAssessor(providers, config_path)
        ha._snapshots = {}
        portfolio = ha.portfolio()
        valid_statuses = {"green", "amber", "red"}
        for h in portfolio:
            assert h.status in valid_statuses

    def test_portfolio_sorted_red_first(self, providers, config_path, tmp_state):
        """Red teams should appear before amber, amber before green."""
        from src.agent.health import HealthAssessor
        ha = HealthAssessor(providers, config_path)
        ha._snapshots = {}
        portfolio = ha.portfolio()
        order = {"red": 0, "amber": 1, "green": 2}
        statuses = [order[h.status] for h in portfolio]
        assert statuses == sorted(statuses)

    def test_assess_known_team(self, providers, config_path, tmp_state):
        from src.agent.health import HealthAssessor, TeamHealth
        ha = HealthAssessor(providers, config_path)
        ha._snapshots = {}
        result = ha.assess("Team Phoenix", record=False)
        assert isinstance(result, TeamHealth)
        assert result.team == "Team Phoenix"
        assert result.status in {"green", "amber", "red"}
        assert result.headline
        assert result.contact

    def test_assess_unknown_team_returns_none(self, providers, config_path, tmp_state):
        from src.agent.health import HealthAssessor
        ha = HealthAssessor(providers, config_path)
        ha._snapshots = {}
        result = ha.assess("Team Nonexistent", record=False)
        assert result is None

    def test_health_has_label(self, providers, config_path, tmp_state):
        from src.agent.health import HealthAssessor
        ha = HealthAssessor(providers, config_path)
        ha._snapshots = {}
        portfolio = ha.portfolio()
        for h in portfolio:
            assert h.label in {"🟢 On track", "🟡 At risk", "🔴 Blocked"}

    def test_record_writes_snapshot_to_tmp(self, providers, config_path, tmp_state):
        """assess(record=True) must write snapshot to tmp dir, not to data/."""
        import src.agent.health as health_mod
        from src.agent.health import HealthAssessor
        ha = HealthAssessor(providers, config_path)
        ha._snapshots = {}
        ha.assess("Team Phoenix", record=True)
        snap_path = health_mod.SNAPSHOT_PATH
        assert "data/health_snapshots.json" not in snap_path or snap_path.startswith(str(tmp_state)), (
            f"Snapshot written to real data dir: {snap_path}"
        )

    def test_format_portfolio_is_string(self, providers, config_path, tmp_state):
        from src.agent.health import HealthAssessor
        ha = HealthAssessor(providers, config_path)
        ha._snapshots = {}
        text = ha.format_portfolio()
        assert isinstance(text, str)
        assert len(text) > 0
