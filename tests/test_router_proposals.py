"""Golden + behavior tests for the `proposals` router command — the surface that
shows OPEN design↔code proposals (the joint artifacts the propose lane produces).

The defining property under test: a proposal reads as a SHARED decision, not a one-way
alert. Both owners (design + code) are named, the divergence note is shown, and only
PENDING (open) proposals appear — a human-resolved one drops off.

Hermetic, following tests/test_router_health.py: Slack is stubbed, all state files are
redirected to tmp, and the proposal store path is monkeypatched so the command reads from
a tmp JSONL (NEVER the real data/proposals.jsonl).
"""
from __future__ import annotations

import pytest

from src.agent.membrane import Actor
from src.agent.propose import (
    DivergenceFinding,
    ProposalStore,
    classify_divergences,
)

FIXED_NOW = "2026-06-19T00:00:00.000Z"


@pytest.fixture()
def bot(monkeypatch, tmp_path):
    # Env defaults (SYNCBOT_TEST, dummy tokens) come from conftest so the import below
    # stays offline. Mirrors the fixture in tests/test_router_health.py.
    import slack_bot as b
    import router
    from src.agent.preferences import NotificationPreferences
    from src.agent import instrumentation
    from src.agent import propose

    monkeypatch.setattr(b.providers.slack, "post_digest", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(b.providers.slack, "post_message", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(b.digest_gen, "prefs", NotificationPreferences(path=str(tmp_path / "prefs.json")))
    monkeypatch.setattr(router, "UNMATCHED_LOG", str(tmp_path / "unmatched.jsonl"))
    monkeypatch.setattr(instrumentation, "STALE_FLAGS", str(tmp_path / "stale_flags.json"))
    monkeypatch.setattr(b, "_channel_display_name", lambda cid: cid)
    # Redirect the proposal store to a tmp file so the command is isolated and starts
    # empty. The router builds ProposalStore() with no arg ⇒ it reads this path. Clear the
    # env override too so a stray export can't point us at the real ledger.
    prop_path = str(tmp_path / "proposals.jsonl")
    monkeypatch.setattr(propose, "PROPOSALS_PATH", prop_path)
    monkeypatch.delenv("SYNCBOT_PROPOSALS_PATH", raising=False)
    b._prop_path = prop_path  # expose for tests that want to seed proposals
    return b


def _finding(**over) -> DivergenceFinding:
    base = dict(
        component="PriceTag",
        divergence_notes="custom corner radius, no library match",
        design_owner="dana",
        code_owner="cory",
        figma_url="https://figma.com/file/abc?node-id=1:2",
        reach=3,
    )
    base.update(over)
    return DivergenceFinding(**base)


def _seed(bot, findings):
    """Build OPEN (pending) proposals from findings and append them to the tmp store the
    router reads. Goes through classify_divergences + ProposalStore.append so the stored
    rows are real propose-lane artifacts, not hand-rolled dicts."""
    proposals = classify_divergences(findings, now=lambda: FIXED_NOW)
    ProposalStore(path=bot._prop_path).append_all(proposals)


# ── triggering ─────────────────────────────────────────────────────────────────

PROPOSAL_PHRASES = ["proposals", "open proposals", "design code conflicts",
                    "design-dev conflicts", "what's diverging", "divergences",
                    "joint artifacts"]


@pytest.mark.parametrize("phrase", PROPOSAL_PHRASES)
def test_proposals_command_triggers(bot, phrase):
    # Seed one open proposal so a triggered command renders the artifact header (an empty
    # store would yield the honest empty message, which is also a valid trigger but tested
    # separately below).
    _seed(bot, [_finding()])
    out = bot.handle_query(phrase)
    assert isinstance(out, str) and out.strip(), f"{phrase!r} returned empty"
    low = out.lower()
    assert "proposal" in low or "joint" in low, f"{phrase!r} did not reach the proposals surface\n{out}"


def test_proposals_does_not_collide_with_design_sync(bot):
    """'is Team Horizon's design in sync' must still reach the design-sync/drift handler,
    not the proposals surface — the propose triggers are scoped to design↔code phrasings."""
    out = bot.handle_query("is Team Horizon's design in sync").lower()
    assert "sync" in out
    assert "joint decision" not in out


def test_proposals_does_not_collide_with_scan_conflicts(bot):
    """A plain 'scan for conflicts' must still reach the drift/scan detector, not the
    propose surface."""
    out = bot.handle_query("scan for conflicts").lower()
    assert "joint decision" not in out


# ── rendering: a JOINT artifact (both owners, the divergence) ────────────────────

def test_renders_component_both_owners_and_divergence(bot):
    _seed(bot, [_finding(component="PriceTag", design_owner="dana", code_owner="cory",
                         divergence_notes="custom corner radius, no library match")])
    out = bot.handle_query("open proposals")
    low = out.lower()
    # the component
    assert "pricetag" in low
    # BOTH owners named — the shared-decision property
    assert "dana" in low and "cory" in low
    # the divergence note, verbatim-ish
    assert "custom corner radius" in low
    # explicit joint-decision framing (not a one-way alert)
    assert "joint" in low


def test_renders_figma_link_when_present(bot):
    _seed(bot, [_finding(figma_url="https://figma.com/file/abc?node-id=1:2")])
    out = bot.handle_query("proposals")
    assert "https://figma.com/file/abc?node-id=1:2" in out


def test_newest_first(bot):
    # Stored newest-LAST; the surface shows newest-FIRST.
    _seed(bot, [_finding(component="OldOne")])
    _seed(bot, [_finding(component="NewOne")])
    out = bot.handle_query("proposals")
    assert out.index("NewOne") < out.index("OldOne")


# ── empty store: honest message ──────────────────────────────────────────────────

def test_empty_store_is_honest(bot):
    """Fresh store ⇒ a plain 'no open proposals' message, not a crash or an invented row."""
    out = bot.handle_query("proposals")
    assert out == "No open design↔code proposals right now."


# ── a resolved proposal is NOT shown as open ─────────────────────────────────────

def test_resolved_proposal_is_not_shown_as_open(bot):
    """Once a human resolves a proposal (decidedBy type 'human'), it is no longer an open
    joint artifact and must drop off this surface — only PENDING ones are shown."""
    # One open (pending) proposal and one resolved one, written directly so we control the
    # decider. The resolved row carries decidedBy {human, who} on BOTH the top level and
    # the embedded provenance (mirroring how a resolver would stamp it).
    open_p = classify_divergences([_finding(component="StillOpen")], now=lambda: FIXED_NOW)[0]
    resolved_p = classify_divergences([_finding(component="AlreadyDecided")], now=lambda: FIXED_NOW)[0]
    resolved_row = resolved_p.to_dict()
    resolved_row["decidedBy"] = {"type": "human", "who": "cory"}
    resolved_row["provenance"]["decidedBy"] = {"type": "human", "who": "cory"}

    store = ProposalStore(path=bot._prop_path)
    store.append(open_p)
    store.append(resolved_row)  # ProposalStore.append accepts a plain dict in proposal shape

    out = bot.handle_query("open proposals")
    assert "StillOpen" in out
    assert "AlreadyDecided" not in out
    assert "1 awaiting" in out  # only the one open proposal is counted


def test_only_resolved_proposals_yields_empty_message(bot):
    """If every proposal in the store has been resolved, the surface is honestly empty."""
    resolved = classify_divergences([_finding(component="DoneDeal")], now=lambda: FIXED_NOW)[0]
    row = resolved.to_dict()
    row["decidedBy"] = {"type": "human", "who": "cory"}
    ProposalStore(path=bot._prop_path).append(row)
    out = bot.handle_query("proposals")
    assert out == "No open design↔code proposals right now."


def test_injected_proposer_agent_does_not_break_rendering(bot):
    """A proposer can be an agent Actor; the owners (design/code) are separate fields and
    must still both render."""
    agent = Actor(type="agent", id="syncbot", model="claude-opus-4-8")
    proposals = classify_divergences([_finding(design_owner="dana", code_owner="cory")],
                                     proposed_by=agent, now=lambda: FIXED_NOW)
    ProposalStore(path=bot._prop_path).append_all(proposals)
    out = bot.handle_query("proposals").lower()
    assert "dana" in out and "cory" in out
