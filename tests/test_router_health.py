"""Golden + smoke tests for the two graph-trust router commands:

  - `doctor` / `manifest health` — runs check_manifests, renders a Slack report.
  - `decisions` / `why` — renders the membrane's provenance audit trail.

Hermetic, following tests/test_router.py: Slack is stubbed, all state files are
redirected to tmp, and the provenance store path is monkeypatched so the governance
log reads from a tmp JSONL (never the real data/provenance.jsonl).
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture()
def bot(monkeypatch, tmp_path):
    # Env defaults (SYNCBOT_TEST, dummy tokens) come from conftest so the import below
    # stays offline. Mirrors the fixture in tests/test_router.py.
    import slack_bot as b
    import router
    from src.agent.preferences import NotificationPreferences
    from src.agent import instrumentation
    from src.agent import provenance

    monkeypatch.setattr(b.providers.slack, "post_digest", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(b.providers.slack, "post_message", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(b.digest_gen, "prefs", NotificationPreferences(path=str(tmp_path / "prefs.json")))
    monkeypatch.setattr(router, "UNMATCHED_LOG", str(tmp_path / "unmatched.jsonl"))
    monkeypatch.setattr(instrumentation, "STALE_FLAGS", str(tmp_path / "stale_flags.json"))
    monkeypatch.setattr(b, "_channel_display_name", lambda cid: cid)
    # Redirect the provenance store to a tmp file so the governance log is isolated and
    # starts empty. The router builds ProvenanceStore() with no arg ⇒ it reads this path.
    prov_path = str(tmp_path / "provenance.jsonl")
    monkeypatch.setattr(provenance, "PROVENANCE_PATH", prov_path)
    monkeypatch.delenv("SYNCBOT_PROVENANCE_PATH", raising=False)
    b._prov_path = prov_path  # expose for tests that want to seed records
    return b


# ── doctor / manifest health ──────────────────────────────────────────────────

DOCTOR_PHRASES = ["doctor", "manifest health", "health check", "validate manifests",
                  "check manifests", "graph health"]


@pytest.mark.parametrize("phrase", DOCTOR_PHRASES)
def test_doctor_triggers_and_reports(bot, phrase):
    out = bot.handle_query(phrase)
    assert isinstance(out, str) and out.strip(), f"{phrase!r} returned empty"
    low = out.lower()
    # Either clean (✅) or a findings report — both are valid 'health' shapes.
    assert "manifest" in low or "graph" in low or "health" in low
    assert "team" in low  # always reports how many teams were checked


def test_doctor_clean_graph_shows_check(bot, monkeypatch):
    """With a graph that has no errors/warnings, the report leads with a ✅."""
    import router
    from src.agent.manifest_health import HealthReport
    monkeypatch.setattr(router.manifest_health, "check_manifests",
                        lambda _p: HealthReport(findings=[], teams_checked=3))
    out = bot.handle_query("doctor")
    assert "✅" in out and "clean" in out.lower()


def test_doctor_with_findings_groups_by_severity(bot, monkeypatch):
    import router
    from src.agent.manifest_health import HealthReport, Finding
    rep = HealthReport(
        findings=[
            Finding("error", "dangling-dep-team", "Team A → Team Ghost",
                    "Team A depends on 'Team Ghost', but no such team exists."),
            Finding("warn", "staleness", "Team B", "Team B is unverified."),
        ],
        teams_checked=2,
    )
    monkeypatch.setattr(router.manifest_health, "check_manifests", lambda _p: rep)
    out = bot.handle_query("manifest health")
    low = out.lower()
    assert "error" in low and "warning" in low
    assert "team ghost" in low
    assert "1 error" in low  # the count line


def test_doctor_does_not_collide_with_team_health(bot):
    """'how's Team Phoenix doing' must still reach the leadership per-team health
    handler, not the graph doctor."""
    out = bot.handle_query("how's Team Phoenix doing").lower()
    assert "phoenix" in out


# ── decisions / why (governance log) ───────────────────────────────────────────

GOV_PHRASES = ["recent decisions", "decision log", "governance log", "audit trail",
               "what got blocked", "decisions"]


@pytest.mark.parametrize("phrase", GOV_PHRASES)
def test_governance_log_triggers(bot, phrase):
    out = bot.handle_query(phrase)
    assert isinstance(out, str) and out.strip(), f"{phrase!r} returned empty"
    assert "governance log" in out.lower()


def test_governance_log_empty_is_honest(bot):
    """Fresh store ⇒ a plain 'nothing recorded yet' message, not a crash or a lie."""
    out = bot.handle_query("recent decisions").lower()
    assert "governance log" in out
    assert "no routing decisions recorded yet" in out


def test_governance_log_renders_records_in_plain_language(bot):
    """Seed the tmp provenance store and assert the audit trail is human-readable:
    lane → verb, reach, and decider all surfaced."""
    rows = [
        {"itemRef": "NotificationBell", "proposedBy": {"type": "agent", "id": "syncbot"},
         "lane": "review", "decidedBy": {"type": "rule", "ruleId": "rule#0"},
         "reach": 2, "passedFloor": True, "at": "2026-06-19T00:00:00+00:00"},
        {"itemRef": "Button", "proposedBy": {"type": "agent", "id": "syncbot"},
         "lane": "blocked", "decidedBy": {"type": "pending"},
         "reach": 3, "passedFloor": False, "at": "2026-06-19T01:00:00+00:00"},
    ]
    with open(bot._prov_path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    out = bot.handle_query("recent decisions")
    low = out.lower()
    assert "notificationbell" in low
    assert "review" in low and "reach 2" in low
    assert "rule#0" in out
    assert "button" in low and "blocked" in low
    assert "no decision logged yet" in low  # the pending decider gloss
    # newest first: Button (01:00) should appear before NotificationBell (00:00)
    assert out.index("Button") < out.index("NotificationBell")


def test_governance_log_does_not_swallow_confluence_decision_search(bot):
    """The broad 'what was decided about <topic>' Confluence search must still win —
    the governance triggers are scoped narrowly so topic lookups aren't captured."""
    out = bot.handle_query("what was decided about OAuth")
    assert "governance log" not in out.lower()
    assert "pkce" in out.lower()  # the synthetic OAuth decision log
