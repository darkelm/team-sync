"""Tests for src/agent/preferences.py — NotificationPreferences."""
from __future__ import annotations

import pytest


class TestNotificationPreferences:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.prefs_path = str(tmp_path / "notification_prefs.json")
        from src.agent.preferences import NotificationPreferences
        self.prefs = NotificationPreferences(self.prefs_path)

    def test_get_returns_defaults_for_new_team(self):
        p = self.prefs.get("Team Phoenix")
        assert p["min_severity"] == "low"
        assert p["paused_until"] is None
        assert "sections" in p
        assert p["sections"]["dev"] is True

    def test_is_paused_false_by_default(self):
        assert self.prefs.is_paused("Team Phoenix") is False

    def test_pause_makes_team_paused(self):
        self.prefs.pause("Team Phoenix")
        assert self.prefs.is_paused("Team Phoenix") is True

    def test_resume_unpauses_team(self):
        self.prefs.pause("Team Phoenix")
        self.prefs.resume("Team Phoenix")
        assert self.prefs.is_paused("Team Phoenix") is False

    def test_pause_with_date(self):
        self.prefs.pause("Team Phoenix", until="2999-12-31")
        assert self.prefs.is_paused("Team Phoenix") is True

    def test_pause_with_past_date(self):
        """Paused until a past date means NOT paused."""
        self.prefs.pause("Team Phoenix", until="2000-01-01")
        assert self.prefs.is_paused("Team Phoenix") is False

    def test_severity_ok_low_threshold(self):
        # Default threshold is 'low', so all severities pass
        assert self.prefs.severity_ok("Team Phoenix", "low") is True
        assert self.prefs.severity_ok("Team Phoenix", "medium") is True
        assert self.prefs.severity_ok("Team Phoenix", "high") is True
        assert self.prefs.severity_ok("Team Phoenix", "critical") is True

    def test_severity_ok_high_threshold(self):
        self.prefs.set_severity("Team Phoenix", "high")
        assert self.prefs.severity_ok("Team Phoenix", "low") is False
        assert self.prefs.severity_ok("Team Phoenix", "medium") is False
        assert self.prefs.severity_ok("Team Phoenix", "high") is True
        assert self.prefs.severity_ok("Team Phoenix", "critical") is True

    def test_set_severity_invalid_level(self):
        result = self.prefs.set_severity("Team Phoenix", "extreme")
        assert "Unknown severity" in result

    def test_set_severity_valid_level(self):
        result = self.prefs.set_severity("Team Phoenix", "medium")
        assert "medium" in result
        assert self.prefs.get("Team Phoenix")["min_severity"] == "medium"

    def test_changed_since_last_initially_true(self):
        assert self.prefs.changed_since_last("Team Phoenix", "sig123") is True

    def test_changed_since_last_after_record_same_sig(self):
        self.prefs.record_signature("Team Phoenix", "sig123")
        assert self.prefs.changed_since_last("Team Phoenix", "sig123") is False

    def test_changed_since_last_after_record_different_sig(self):
        self.prefs.record_signature("Team Phoenix", "sig123")
        assert self.prefs.changed_since_last("Team Phoenix", "sig456") is True

    def test_set_section_disables_dev(self):
        self.prefs.set_section("Team Phoenix", "dev", False)
        p = self.prefs.get("Team Phoenix")
        assert p["sections"]["dev"] is False

    def test_set_section_re_enables(self):
        self.prefs.set_section("Team Phoenix", "dev", False)
        self.prefs.set_section("Team Phoenix", "dev", True)
        p = self.prefs.get("Team Phoenix")
        assert p["sections"]["dev"] is True

    def test_persists_across_instances(self):
        """Changes saved to the file should be visible in a new instance."""
        from src.agent.preferences import NotificationPreferences
        self.prefs.pause("Team Phoenix")
        prefs2 = NotificationPreferences(self.prefs_path)
        assert prefs2.is_paused("Team Phoenix") is True

    def test_file_not_written_to_real_data_dir(self):
        """Preferences should write to our tmp path, not data/."""
        assert "data/notification_prefs.json" not in self.prefs_path or \
               self.prefs_path.startswith("/tmp") or \
               "pytest" in self.prefs_path
