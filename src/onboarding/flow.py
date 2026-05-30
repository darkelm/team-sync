"""Conversational onboarding flow — someone describes their initiative in Slack.

State machine: the bot asks a few plain-language questions, accumulates the
answers, extracts a brief, and generates the setup. No YAML. No terminal.
Works across multiple Slack turns; state keyed by user/channel.

Questions asked (only what's missing after extraction):
  1. What's the initiative? (client, project name, description)
  2. What experiences / journeys are you designing? (if not extracted)
  3. Who are the teams / pairs? (if not extracted)
  4. What are the shared evaluation criteria / principles? (if not extracted)
  5. Where should I write the files? (output dir, defaults sensibly)

Any question can be skipped with "skip" or "not sure" — gaps become TODOs.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from .extractor import InitiativeBrief, extract


@dataclass
class FlowState:
    user_id: str
    channel_id: str
    stage: str = "init"           # init | describe | journeys | teams | principles | confirm | done
    accumulated_text: str = ""
    brief: InitiativeBrief | None = None
    output_dir: str = "data/imported"
    register_project: bool = False  # True = also create config + register in ProjectRegistry

    def to_json(self) -> str:
        d = dict(
            user_id=self.user_id, channel_id=self.channel_id,
            stage=self.stage, accumulated_text=self.accumulated_text,
            output_dir=self.output_dir,
            brief=None,  # serialized separately below
        )
        return json.dumps(d)


_STORE: dict[str, FlowState] = {}  # keyed by user_id; production would use Redis/DB


def _key(user_id: str) -> str:
    return user_id


def get_state(user_id: str, channel_id: str) -> FlowState:
    return _STORE.get(_key(user_id)) or FlowState(user_id=user_id, channel_id=channel_id)


def clear_state(user_id: str) -> None:
    _STORE.pop(_key(user_id), None)


def _skip(text: str) -> bool:
    return any(w in text.lower() for w in ["skip", "not sure", "don't know", "unsure", "pass", "n/a"])


def _done_or_enough(brief: InitiativeBrief) -> bool:
    return bool(brief.title or brief.description) and bool(brief.journeys or brief.teams)


QUESTIONS = {
    "init": (
        "👋 Let's set up SyncBot for your initiative. Tell me about it — paste an RFP, "
        "a brief doc, a Figma board summary, or just describe the work in plain language. "
        "There's no wrong format.\n\n"
        "_(Or say `cancel` to stop.)_"
    ),
    "journeys": (
        "Got it. What are the main *experiences or journeys* you're designing? "
        "(e.g. 'Search results page, Shopping checkout, Onboarding flow') — "
        "you can list them or describe them. Say `skip` if you're not sure yet."
    ),
    "teams": (
        "Who are the *teams or pairs* working on this? Names are fine — "
        "'Pair 1: Search', 'Platform squad', etc. Say `skip` if not decided."
    ),
    "principles": (
        "Do you have *shared evaluation criteria or design principles* all the work is judged against? "
        "(e.g. 'Trust, Control, Transparency') — say `skip` if not yet."
    ),
    "confirm": None,  # generated dynamically
}


def process_turn(user_id: str, channel_id: str, text: str, output_dir: str = "data/imported") -> tuple[str, bool]:
    """Process one Slack turn. Returns (reply, is_done)."""
    if text.strip().lower() in ("cancel", "stop", "quit"):
        clear_state(user_id)
        return "Onboarding cancelled. Say `@syncbot set up my initiative` anytime to start again.", True

    state = get_state(user_id, channel_id)
    state.output_dir = output_dir

    # ── init: send the welcome question ──────────────────────────────────────
    if state.stage == "init":
        state.stage = "describe"
        _STORE[_key(user_id)] = state
        return QUESTIONS["init"], False

    # ── describe: receive the first blob, extract a brief ────────────────────
    if state.stage == "describe":
        state.accumulated_text += "\n" + text
        state.brief = extract(state.accumulated_text)
        # Decide what's still missing
        if not state.brief.journeys:
            state.stage = "journeys"
            _STORE[_key(user_id)] = state
            return QUESTIONS["journeys"], False
        elif not state.brief.teams:
            state.stage = "teams"
            _STORE[_key(user_id)] = state
            return QUESTIONS["teams"], False
        elif not state.brief.principles:
            state.stage = "principles"
            _STORE[_key(user_id)] = state
            return QUESTIONS["principles"], False
        else:
            state.stage = "confirm"
            _STORE[_key(user_id)] = state
            return _confirm_prompt(state.brief), False

    # ── journeys ─────────────────────────────────────────────────────────────
    if state.stage == "journeys":
        if not _skip(text):
            state.accumulated_text += f"\nExperiences/journeys: {text}"
            state.brief = extract(state.accumulated_text)
        state.stage = "teams" if not state.brief.teams else "principles" if not state.brief.principles else "confirm"
        _STORE[_key(user_id)] = state
        if state.stage == "teams":
            return QUESTIONS["teams"], False
        elif state.stage == "principles":
            return QUESTIONS["principles"], False
        return _confirm_prompt(state.brief), False

    # ── teams ─────────────────────────────────────────────────────────────────
    if state.stage == "teams":
        if not _skip(text):
            state.accumulated_text += f"\nTeams/pairs: {text}"
            state.brief = extract(state.accumulated_text)
        state.stage = "principles" if not state.brief.principles else "confirm"
        _STORE[_key(user_id)] = state
        if state.stage == "principles":
            return QUESTIONS["principles"], False
        return _confirm_prompt(state.brief), False

    # ── principles ────────────────────────────────────────────────────────────
    if state.stage == "principles":
        if not _skip(text):
            state.accumulated_text += f"\nDesign principles/criteria: {text}"
            state.brief = extract(state.accumulated_text)
        state.stage = "confirm"
        _STORE[_key(user_id)] = state
        return _confirm_prompt(state.brief), False

    # ── confirm → generate (+ optional project registration) ─────────────────
    if state.stage == "confirm":
        if any(w in text.lower() for w in ["yes", "go", "looks good", "correct", "do it", "yep", "yeah", "ok"]):
            from .generator import generate, summary
            written = generate(state.brief, state.output_dir)
            msg = summary(state.brief, written)

            # Full project registration: config file + ProjectRegistry + channel mapping
            if state.register_project and state.brief.title:
                reg_msg = _register_project(state.brief, state.output_dir, state.channel_id)
                msg += f"\n\n{reg_msg}"

            clear_state(user_id)
            return msg, True
        elif any(w in text.lower() for w in ["no", "change", "edit", "wrong", "fix"]):
            # Allow a correction pass
            state.accumulated_text += f"\nCorrection: {text}"
            state.brief = extract(state.accumulated_text)
            state.stage = "confirm"
            _STORE[_key(user_id)] = state
            return _confirm_prompt(state.brief) + "\n\n_Say 'yes' to generate, or describe what needs changing._", False
        else:
            return "Say *yes* to generate the setup, or tell me what needs correcting.", False

    return "Something went wrong. Say `@syncbot register project` to start over.", True


def _register_project(brief: "InitiativeBrief", output_dir: str, channel_id: str) -> str:
    """Create the config YAML and register the project in the ProjectRegistry.

    Called after the setup files are already generated. Writes a config-<slug>.yaml
    pointing at the generated data, then registers it so the channel is immediately live.
    """
    import re
    import yaml as _yaml

    title = brief.title or brief.client or "project"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    config_path = f"config-{slug}.yaml"
    teams_dir = os.path.join(output_dir, slug, "teams")

    # Write the config YAML
    config = {
        "providers": {
            "jira": "local", "confluence": "local",
            "github": "local", "slack": "live", "figma": "local",
        },
        "data": {
            "synthetic_path": os.path.join(output_dir, slug),
            "teams_dir": teams_dir,
        },
        "digest": {"schedule": "0 9 * * 1", "timezone": "America/New_York"},
        "leadership": {
            "unit_label": "team",
            "portfolio_label": "portfolio",
            "exec_channel": "",
        },
        "drift": {"scan_on_pr": True, "scan_schedule": "0 8 * * *"},
    }
    with open(config_path, "w") as f:
        _yaml.dump(config, f, sort_keys=False)

    # Register in ProjectRegistry and map the current channel
    from src.projects import ProjectRegistry
    reg = ProjectRegistry()
    reg.register(name=title, config=config_path, channels=[channel_id] if channel_id else [])

    lines = [
        f"🗂️ *Project registered: {title}*",
        f"  • Config: `{config_path}` (written ✓)",
        f"  • Data: `{os.path.join(output_dir, slug)}/`",
        f"  • This channel is now scoped to *{title}* — all queries here use this project's data only.",
        f"  • Other channels stay on their own projects.",
        "",
        "_To add more channels: `@syncbot register this channel for " + title + "`_",
    ]
    return "\n".join(lines)


def start_registration(user_id: str, channel_id: str, output_dir: str = "data/imported") -> str:
    """Begin a project registration flow (creates config + registers in ProjectRegistry)."""
    state = FlowState(user_id=user_id, channel_id=channel_id,
                      output_dir=output_dir, register_project=True)
    state.stage = "describe"
    _STORE[_key(user_id)] = state
    return (
        "👋 Let's register a new project. Tell me about it — paste an RFP, a brief doc, "
        "a Figma board link summary, or just describe the work.\n\n"
        "I'll set up the team structure, journeys, and principles, create the config, "
        "and register this channel automatically.\n\n"
        "_(Say `cancel` to stop.)_"
    )


def _confirm_prompt(brief: InitiativeBrief) -> str:
    lines = ["*Here's what I extracted — does this look right?*\n"]
    lines.append(f"📌 *Initiative:* {brief.title or brief.client or '(unnamed)'}")
    if brief.description:
        lines.append(f"_{brief.description[:200]}_")
    if brief.journeys:
        lines.append(f"\n🗺️ *Journeys/experiences ({len(brief.journeys)}):* {', '.join(j.name for j in brief.journeys)}")
    if brief.teams:
        lines.append(f"👥 *Teams ({len(brief.teams)}):* {', '.join(t.name for t in brief.teams)}")
    if brief.principles:
        lines.append(f"🎯 *Principles ({len(brief.principles)}):* {', '.join(p.name for p in brief.principles)}")
    if brief.open_decisions:
        lines.append(f"⚠️ *Open decisions captured ({len(brief.open_decisions)})*")
    lines.append("\nSay *yes* to generate the setup files, or correct anything above.")
    return "\n".join(lines)
