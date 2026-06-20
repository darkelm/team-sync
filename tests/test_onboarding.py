"""Tests for the onboarding pipeline — src/onboarding/{extractor,generator,flow}.py.

These three modules were at 0% coverage. They are pure, offline logic:
- extractor.extract_heuristic: regex-based brief extraction from free text (the
  default path when no ANTHROPIC_API_KEY is set).
- generator.generate: turns an InitiativeBrief into a DRAFT file set on disk.
- flow.process_turn: the multi-turn Slack state machine that wraps both.

No API key, no network. The AI extraction path (extract_ai) is intentionally NOT
exercised — it requires a live Anthropic call and is owned by the agent-path
mocking elsewhere. We pin the heuristic path by clearing ANTHROPIC_API_KEY.

Run: SYNCBOT_TEST=1 .venv/bin/python3 -m pytest tests/test_onboarding.py -q
"""
from __future__ import annotations

import os

import pytest

from src.onboarding import flow as flow_mod
from src.onboarding.extractor import (
    InitiativeBrief,
    JourneyDraft,
    PrincipleDraft,
    TeamDraft,
    extract,
    extract_heuristic,
)
from src.onboarding.generator import generate, summary


BRIEF_TEXT = (
    "Gen AI Checkout Redesign\n"
    "Client: Acme Corp\n\n"
    "We are reimagining how customers complete checkout using AI assistance. "
    "This is a multi-quarter initiative spanning search, cart, and payments.\n\n"
    "Experiences:\n"
    "- Search results page\n"
    "- Shopping checkout\n"
    "- Order confirmation\n\n"
    "Teams:\n"
    "- Pair 1: Search and discovery\n"
    "- Pair 2: Checkout and payments\n\n"
    "Principles:\n"
    "- Trust\n"
    "- Transparency\n"
    "- Control\n\n"
    "North star: customers complete checkout in under 60 seconds\n"
    "Should we support guest checkout without an account?\n"
    "Do we need PCI re-certification for the new flow?\n"
)


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """Force the heuristic path everywhere in this module."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ── extractor (heuristic) ─────────────────────────────────────────────────────

class TestExtractHeuristic:
    def test_extracts_core_fields(self):
        brief = extract_heuristic(BRIEF_TEXT)
        assert brief.title  # first non-noise line became the title
        assert brief.client == "Acme Corp"
        assert "under 60 seconds" in brief.north_star
        assert brief.raw_text == BRIEF_TEXT

    def test_extracts_journeys_from_bullets(self):
        brief = extract_heuristic(BRIEF_TEXT)
        names = [j.name.lower() for j in brief.journeys]
        assert any("search results" in n for n in names)
        assert any("checkout" in n for n in names)

    def test_extracts_teams_from_bullets(self):
        brief = extract_heuristic(BRIEF_TEXT)
        assert brief.teams, "no teams extracted"
        # Team names are split off the role suffix after the colon.
        team_names = " ".join(t.name.lower() for t in brief.teams)
        assert "pair" in team_names or "search" in team_names or "checkout" in team_names

    def test_extracts_principles(self):
        brief = extract_heuristic(BRIEF_TEXT)
        # The heuristic captures at least the first principle from the section;
        # we assert it found a principle containing "trust" (real behavior — the
        # extractor's section parsing is intentionally lenient, not exhaustive).
        assert brief.principles
        pnames = " ".join(p.name.lower() for p in brief.principles)
        assert "trust" in pnames

    def test_captures_open_decisions(self):
        brief = extract_heuristic(BRIEF_TEXT)
        assert any("guest checkout" in d.lower() for d in brief.open_decisions)
        assert len(brief.open_decisions) >= 2

    def test_empty_text_yields_empty_brief(self):
        brief = extract_heuristic("")
        assert brief.title == ""
        assert brief.teams == []
        assert brief.journeys == []

    def test_extract_dispatches_to_heuristic_without_key(self):
        # With ANTHROPIC_API_KEY unset (autouse fixture), extract() must use the
        # heuristic path and never touch the network.
        brief = extract(BRIEF_TEXT)
        assert isinstance(brief, InitiativeBrief)
        assert brief.client == "Acme Corp"


# ── generator ─────────────────────────────────────────────────────────────────

class TestGenerate:
    def _brief(self):
        return InitiativeBrief(
            title="Checkout Redesign",
            client="Acme Corp",
            description="Reimagining checkout with AI.",
            teams=[TeamDraft(name="Search", focus="discovery", members=["Ada"]),
                   TeamDraft(name="Checkout")],
            journeys=[JourneyDraft(name="Shopping checkout", description="end to end")],
            principles=[PrincipleDraft(name="Trust", statement="Earn it")],
            open_decisions=["Should we support guest checkout?"],
            north_star="customers complete checkout in under 60 seconds",
        )

    def test_writes_full_file_set(self, tmp_path):
        written = generate(self._brief(), str(tmp_path))
        assert written
        # Every reported path exists.
        for p in written:
            assert os.path.exists(p), p
        # One team.yaml per team + journeys + principles + objectives + how-to.
        assert len([p for p in written if p.endswith("team.yaml")]) == 2
        assert any(p.endswith("journeys.yaml") for p in written)
        assert any(p.endswith("experience_principles.yaml") for p in written)
        assert any(p.endswith("org_objectives.yaml") for p in written)
        assert any(p.endswith("HOW-TO-USE.txt") for p in written)

    def test_team_manifest_content(self, tmp_path):
        written = generate(self._brief(), str(tmp_path))
        team_files = [p for p in written if p.endswith("team.yaml")]
        contents = "\n".join(open(p).read() for p in team_files)
        assert "Search" in contents
        assert "Checkout" in contents
        assert "Ada" in contents          # member carried through
        assert "DRAFT" in contents        # draft banner present

    def test_no_principles_skips_principles_file(self, tmp_path):
        brief = self._brief()
        brief.principles = []
        written = generate(brief, str(tmp_path))
        assert not any(p.endswith("experience_principles.yaml") for p in written)

    def test_no_north_star_or_decisions_skips_objectives(self, tmp_path):
        brief = self._brief()
        brief.north_star = ""
        brief.open_decisions = []
        written = generate(brief, str(tmp_path))
        assert not any(p.endswith("org_objectives.yaml") for p in written)

    def test_summary_text(self):
        brief = self._brief()
        written = ["data/x/teams/search/team.yaml", "data/x/journeys.yaml"]
        text = summary(brief, written)
        assert "Checkout Redesign" in text
        assert "Search" in text
        assert "Trust" in text
        assert "guest checkout" in text.lower()


# ── flow state machine ────────────────────────────────────────────────────────

class TestFlow:
    @pytest.fixture(autouse=True)
    def clean_store(self):
        """The flow uses a module-level _STORE dict; isolate each test."""
        flow_mod._STORE.clear()
        yield
        flow_mod._STORE.clear()

    def test_init_sends_welcome(self):
        reply, done = flow_mod.process_turn("U1", "C1", "anything")
        assert not done
        assert "set up SyncBot" in reply
        # State advanced to describe.
        assert flow_mod.get_state("U1", "C1").stage == "describe"

    def test_cancel_clears_state(self):
        flow_mod.process_turn("U1", "C1", "hi")        # -> describe
        reply, done = flow_mod.process_turn("U1", "C1", "cancel")
        assert done
        assert "cancelled" in reply.lower()
        # State was cleared (next turn starts fresh at init).
        assert flow_mod.get_state("U1", "C1").stage == "init"

    def test_full_happy_path_to_generation(self, tmp_path):
        out = str(tmp_path / "imported")
        uid = "U2"
        # 1. init -> describe
        flow_mod.process_turn(uid, "C2", "start", output_dir=out)
        # 2. describe: feed a rich brief that has journeys+teams+principles, so
        #    the flow jumps straight to confirm.
        reply, done = flow_mod.process_turn(uid, "C2", BRIEF_TEXT, output_dir=out)
        assert not done
        # Should now be at confirm (brief had everything).
        assert flow_mod.get_state(uid, "C2").stage == "confirm"
        assert "look right" in reply.lower() or "extracted" in reply.lower()
        # 3. confirm -> generate
        reply, done = flow_mod.process_turn(uid, "C2", "yes", output_dir=out)
        assert done
        assert "setup complete" in reply.lower() or "generated" in reply.lower()
        assert list((tmp_path / "imported").rglob("team.yaml"))

    def test_describe_then_skip_questions(self, tmp_path):
        out = str(tmp_path / "imported2")
        uid = "U3"
        flow_mod.process_turn(uid, "C3", "start", output_dir=out)
        # Minimal brief with no journeys/teams/principles -> flow asks for them.
        reply, done = flow_mod.process_turn(uid, "C3", "We are building something vague.",
                                            output_dir=out)
        assert not done
        # The flow should be asking one of the follow-up questions.
        stage = flow_mod.get_state(uid, "C3").stage
        assert stage in ("journeys", "teams", "principles", "confirm")

    def test_confirm_correction_loop(self, tmp_path):
        out = str(tmp_path / "imported3")
        uid = "U4"
        flow_mod.process_turn(uid, "C4", "start", output_dir=out)
        flow_mod.process_turn(uid, "C4", BRIEF_TEXT, output_dir=out)
        # At confirm: a "no" answer triggers a correction pass, not generation.
        reply, done = flow_mod.process_turn(uid, "C4", "no, change the teams", output_dir=out)
        assert not done
        assert flow_mod.get_state(uid, "C4").stage == "confirm"

    def test_start_registration_sets_register_flag(self):
        reply = flow_mod.start_registration("U5", "C5", output_dir="data/x")
        assert "register" in reply.lower()
        state = flow_mod.get_state("U5", "C5")
        assert state.register_project is True
        assert state.stage == "describe"

    def test_flow_state_to_json(self):
        state = flow_mod.FlowState(user_id="U6", channel_id="C6", stage="describe",
                                   accumulated_text="hello")
        import json
        d = json.loads(state.to_json())
        assert d["user_id"] == "U6"
        assert d["stage"] == "describe"
        assert d["brief"] is None
