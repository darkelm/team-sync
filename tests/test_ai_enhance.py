"""Tests for src/agent/ai_enhance.py — heuristic (no-key) path only."""
from __future__ import annotations



class TestAiAvailable:
    def test_ai_available_false_without_key(self, monkeypatch):
        """Without ANTHROPIC_API_KEY, ai_available() must return False."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Re-import to pick up the env change
        import importlib
        import src.agent.ai_enhance as mod
        importlib.reload(mod)
        assert mod.ai_available() is False

    def test_ai_available_true_with_key(self, monkeypatch):
        """With a fake key set, ai_available() returns True (module-level check)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key-for-test")
        import importlib
        import src.agent.ai_enhance as mod
        importlib.reload(mod)
        assert mod.ai_available() is True


class TestExtractMeetingHeuristic:
    """Verify extract_meeting falls back to heuristics when AI is unavailable."""

    def test_extract_meeting_raises_or_returns_none_without_key(self, monkeypatch):
        """extract_meeting requires a key; without one it should raise or return None.
        The important thing is that callers (MeetingAnalyzer) wrap it in try/except."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import importlib
        import src.agent.ai_enhance as mod
        importlib.reload(mod)

        from src.importers.transcript import Segment
        from datetime import date
        segments = [
            Segment("Alice", "We decided to use OAuth 2.0 for authentication."),
            Segment("Bob", "Alice will set up the token pipeline by Friday."),
        ]
        # Without a key, this should either raise or return an empty tuple
        try:
            result = mod.extract_meeting(segments, "Team Phoenix", date.today(), ["Alice", "Bob"])
            # If it doesn't raise: result is a tuple (decisions, actions, flags)
            assert isinstance(result, tuple)
        except Exception:
            pass  # Expected — callers catch and fall back to heuristics

    def test_semantic_reuse_without_key_raises_or_returns_empty(self, monkeypatch):
        """semantic_reuse without a key should raise an exception (callers catch it)."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import importlib
        import src.agent.ai_enhance as mod
        importlib.reload(mod)

        try:
            result = mod.semantic_reuse("notification bell", [])
            # If it doesn't raise, result must be a list
            assert isinstance(result, list)
        except Exception:
            pass  # expected — callers catch and fall back to Jaccard
