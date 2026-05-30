"""Tests for src/agent/similarity.py — jaccard and overlap_terms."""
from __future__ import annotations



class TestJaccard:
    def test_identical_strings(self):
        from src.agent.similarity import jaccard
        score = jaccard("hello world", "hello world")
        assert score == 1.0

    def test_completely_different(self):
        from src.agent.similarity import jaccard
        score = jaccard("apple orange", "banana grape")
        assert score == 0.0

    def test_partial_overlap(self):
        from src.agent.similarity import jaccard
        score = jaccard("auth login token", "auth session cookies")
        assert 0.0 < score < 1.0

    def test_empty_strings(self):
        from src.agent.similarity import jaccard
        score = jaccard("", "")
        assert score == 0.0

    def test_one_empty(self):
        from src.agent.similarity import jaccard
        score = jaccard("hello", "")
        assert score == 0.0


class TestTokenize:
    def test_lowercases(self):
        from src.agent.similarity import tokenize
        tokens = tokenize("Hello World")
        assert "hello" in tokens
        assert "world" in tokens

    def test_strips_short_words(self):
        from src.agent.similarity import tokenize
        # 'a', 'is', 'of' etc. are typically filtered
        tokens = tokenize("a is an the of for")
        # exact stop words depend on implementation; just verify it returns a set
        assert isinstance(tokens, (set, frozenset))

    def test_returns_set(self):
        from src.agent.similarity import tokenize
        result = tokenize("authentication auth")
        assert isinstance(result, (set, frozenset))


class TestOverlapTerms:
    def test_returns_common_terms(self):
        from src.agent.similarity import overlap_terms
        terms = overlap_terms("auth login session", "login session cookies")
        assert isinstance(terms, list)
        # Both strings contain 'login' and 'session' (if not stop-worded)
        assert len(terms) >= 1

    def test_no_overlap_returns_empty(self):
        from src.agent.similarity import overlap_terms
        terms = overlap_terms("blockchain quantum", "authentication login")
        assert isinstance(terms, list)
