"""Golden + smoke tests for the Slack-facing layer (handle_query / answer /
digest targeting).

This is the net under the most-edited, most-fragile part of the codebase — the
keyword router and message handlers. It is deliberately BEHAVIORAL (asserts on
stable substrings of the reply, not internal structure) so it survives a router
refactor. Every assertion below was confirmed against the synthetic org.

Hermetic: Slack delivery is stubbed (nothing is ever posted), notification prefs
are redirected to a tmp file, and channel-name resolution is stubbed so no
network call is made. slack_bot is imported INSIDE the fixture (after SYNCBOT_TEST
is set) so module construction stays offline.
"""
from __future__ import annotations
import ast
import os
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def bot(monkeypatch, tmp_path):
    # Env defaults (SYNCBOT_TEST, dummy tokens) come from conftest so the import
    # below stays offline and never perturbs the session-scoped providers fixture.
    import slack_bot as b
    import router
    from src.agent.preferences import NotificationPreferences
    from src.agent import instrumentation

    # Never hit real Slack.
    monkeypatch.setattr(b.providers.slack, "post_digest", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(b.providers.slack, "post_message", lambda *a, **k: True, raising=False)
    # Isolate all state files to tmp so tests don't pollute data/.
    monkeypatch.setattr(b.digest_gen, "prefs", NotificationPreferences(path=str(tmp_path / "prefs.json")))
    monkeypatch.setattr(b.digest_gen, "ALERT_DEDUP_PATH", str(tmp_path / "dedup.json"))
    monkeypatch.setattr(router, "UNMATCHED_LOG", str(tmp_path / "unmatched.jsonl"))
    monkeypatch.setattr(instrumentation, "STALE_FLAGS", str(tmp_path / "stale_flags.json"))
    # No network for channel-name resolution.
    monkeypatch.setattr(b, "_channel_display_name", lambda cid: cid)
    return b


# ── Golden routing: phrase -> reply must contain a stable marker ──────────────

GOLDEN = [
    ("who owns auth",                                   ["Team Phoenix"]),
    ("who owns DataTable",                              ["Team Nova"]),
    ("where do I find the design system",              ["design system"]),
    ("action items for Team Nova",                      ["action item", "Nova"]),
    ("when does Team Phoenix ship",                     ["PHX-"]),
    ("what was decided about OAuth",                    ["PKCE"]),
    ("scan for conflicts",                              ["drift"]),
    ("predict conflicts",                               ["predict"]),
    ("who should I talk to",                            ["collaborat"]),
    ("has anyone built a notification bell",            ["NotificationBell"]),
    ("check alignment",                                 ["alignment"]),
    ("prep me for a sync with Team Atlas and Team Forge", ["Atlas"]),
    ("get me up to speed on Team Horizon",              ["Horizon"]),
    ("is Team Horizon's design in sync",               ["sync"]),
    ("digest for Team Horizon",                         ["Digest"]),
    ("dependencies for Team Phoenix",                   ["Depends on", "Atlas"]),
    ("status",                                          ["status", "Teams tracked"]),
    ("where do digests go",                             ["digest"]),
]


@pytest.mark.parametrize("phrase,markers", GOLDEN)
def test_router_golden(bot, phrase, markers):
    out = bot.handle_query(phrase)
    assert isinstance(out, str) and out.strip(), f"{phrase!r} returned empty"
    low = out.lower()
    for m in markers:
        assert m.lower() in low, f"{phrase!r} -> missing {m!r}\n{out}"


def test_mute_then_resume(bot):
    assert "paused" in bot.handle_query("mute digests for Team Horizon").lower()
    assert "resume" in bot.handle_query("resume digests for Team Horizon").lower()


def test_set_severity(bot):
    out = bot.handle_query("only alert Team Atlas on high").lower()
    assert "severity" in out and "high" in out


def test_unknown_falls_back_to_help(bot):
    out = bot.handle_query("xyzzy please do a flibberjig")
    assert isinstance(out, str) and out.strip()


def test_help_is_role_framed(bot):
    assert "design" in bot.handle_query("help", role="designer").lower()
    assert "leadership view" in bot.handle_query("help", role="lead").lower()
    # default role gets no tailored highlight, just the full list
    assert "Full list" not in bot.handle_query("help")


def test_unmatched_query_is_logged_and_surfaced(bot):
    import router
    import json
    import os
    out = bot.handle_query("please reticulate the splines")
    assert "didn't catch that" in out.lower()
    assert os.path.exists(router.UNMATCHED_LOG)
    logged = [json.loads(line)["text"] for line in open(router.UNMATCHED_LOG)]
    assert "please reticulate the splines" in logged
    # the backlog command surfaces it, ranked
    assert "reticulate the splines" in bot.handle_query("unmatched").lower()


def test_help_is_not_logged_as_unmatched(bot):
    import router
    import os
    bot.handle_query("help")
    assert (not os.path.exists(router.UNMATCHED_LOG)
            or "help" not in open(router.UNMATCHED_LOG).read().lower())


# ── Trust: freshness stamping, stale flags, stats ────────────────────────────

def test_team_answers_are_freshness_stamped(bot):
    out = bot.handle_query("who owns auth").lower()
    assert "team phoenix" in out
    assert any(w in out for w in ["verified", "unverified", "out of date", "flagged stale"])


def test_mark_stale_then_answer_warns_then_clears(bot):
    assert "flagged" in bot.handle_query("mark Team Phoenix stale").lower()
    assert "flagged stale" in bot.handle_query("who owns auth").lower()  # auth → Team Phoenix
    assert "cleared" in bot.handle_query("Team Phoenix is verified").lower()
    assert "flagged stale" not in bot.handle_query("who owns auth").lower()


def test_stats_rolls_up_misses_and_flags(bot):
    bot.handle_query("please reticulate the splines")  # an unmatched miss
    bot.handle_query("mark Team Nova stale")
    out = bot.handle_query("stats").lower()
    assert "backlog" in out
    assert "team nova" in out          # flagged team surfaced
    assert "reticulate" in out         # unmatched query surfaced


def test_design_status_surfaces_figma_signals(bot):
    # Team Phoenix has synthetic figma_dev_status.json (ready-for-dev + comments).
    out = bot.handle_query("dev status for Team Phoenix").lower()
    assert "design status" in out
    assert any(w in out for w in ["handoff", "comment", "ready_for_dev", "change"])


# ── Digest targeting (needs the raw event for the channel id) ─────────────────

def _ev(text, channel="C_TEST"):
    return {"channel": channel, "user": "U_TEST", "text": text}


def test_targeting_registers_team(bot):
    out = bot._handle_digest_targeting("send Team Nova's digest here", _ev("x"))
    assert "Team Nova" in out and "delivered" in out.lower()
    assert bot.digest_gen.prefs.get_digest_channel("Team Nova") == "C_TEST"


def test_targeting_prompts_without_team(bot):
    out = bot._handle_digest_targeting("send my digest here", _ev("x"))
    assert "which team" in out.lower()


def test_targeting_all(bot):
    bot._handle_digest_targeting("send all digests here", _ev("x"))
    assert len(bot.digest_gen.prefs.digest_targets()) == 5


def test_targeting_stop(bot):
    bot._handle_digest_targeting("send all digests here", _ev("x"))
    out = bot._handle_digest_targeting("stop sending digests here", _ev("x"))
    assert "stopped" in out.lower()
    assert bot.digest_gen.prefs.digest_targets() == {}


def test_plain_send_digest_falls_through(bot):
    # plain "send digest" (no "here") must NOT be captured by targeting; it
    # belongs to the broadcast handler.
    assert bot._handle_digest_targeting("send digest", _ev("x")) is None
    assert bot._handle_digest_targeting("digest for Team Nova", _ev("x")) is None


# ── Smoke: structural guards against the bug classes we already hit ───────────

def test_no_duplicate_toplevel_defs():
    """Guards the answer()-shadowing bug class (commit 41e8971)."""
    tree = ast.parse(open(os.path.join(REPO, "slack_bot.py")).read())
    names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate top-level defs in slack_bot.py: {dupes}"


def test_every_golden_command_returns_nonempty_str(bot):
    for phrase, _ in GOLDEN:
        out = bot.handle_query(phrase)
        assert isinstance(out, str) and out.strip(), f"{phrase!r} returned empty/non-str"


def test_query_is_project_scoped(bot, monkeypatch):
    """A query routed with another project's engine bundle must not see the
    default (synthetic) project's teams — keyword-mode multi-tenant isolation."""
    default_out = bot.handle_query("get me up to speed on Team Nova")
    assert "Amara" in default_out  # Nova's owner in the synthetic org

    # Register a second project (no file write) and build its engine bundle.
    monkeypatch.setattr(bot.project_registry, "_save", lambda *a, **k: None)
    bot.project_registry.register("GoogleTest", "config-google.yaml", channels=["C_GTEST"])
    try:
        eng = bot._project_engines("C_GTEST")
        scoped_out = bot.handle_query("get me up to speed on Team Nova", eng=eng)
        assert "Amara" not in scoped_out  # isolated: this project has no Team Nova
    finally:
        bot.project_registry.projects = [
            p for p in bot.project_registry.projects if p.name != "GoogleTest"
        ]
