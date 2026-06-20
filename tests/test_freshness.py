from datetime import date, timedelta
from types import SimpleNamespace

from src.agent import freshness


def _team(days_ago):
    lv = None if days_ago is None else date.today() - timedelta(days=days_ago)
    return SimpleNamespace(last_verified=lv)


def test_fresh():
    f = freshness.assess(_team(3))
    assert f.label == "fresh" and f.score == 1.0
    assert freshness.is_fresh(_team(3))


def test_aging_still_passes_gate():
    f = freshness.assess(_team(20))
    assert f.label == "aging"
    assert freshness.is_fresh(_team(20))


def test_stale_fails_gate():
    f = freshness.assess(_team(45))
    assert f.label == "stale"
    assert not freshness.is_fresh(_team(45))
    assert "out of date" in f.note


def test_unverified_fails_gate():
    f = freshness.assess(_team(None))
    assert f.label == "unverified" and f.score == 0.0
    assert not freshness.is_fresh(_team(None))
    assert "refresh-manifest" in f.note
