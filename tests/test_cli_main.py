"""Tests for the SyncBot CLI — src/cli/main.py (the typer app).

This module was at 0% coverage: the CLI is exercised only by humans. These tests
drive every command through typer's CliRunner against the synthetic org (local
providers, no API keys, no network), writing any generated files into tmp dirs.
We assert exit codes and that the rendered output contains the grounded facts
from the synthetic data (e.g. Team Phoenix owns auth), so the commands are
actually executing their engines — not just importing.

Run: SYNCBOT_TEST=1 .venv/bin/python3 -m pytest tests/test_cli_main.py -q
"""
from __future__ import annotations

import json
import os

from typer.testing import CliRunner

from src.cli.main import app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.yaml")

runner = CliRunner()


def _cfg():
    return ["--config", CONFIG_PATH]


# ── validate ──────────────────────────────────────────────────────────────────

class TestValidate:
    def test_runs_and_lists_teams(self):
        result = runner.invoke(app, ["validate", *_cfg()])
        assert result.exit_code == 0
        # The synthetic org has multiple teams; the header reports the count.
        assert "team manifests" in result.stdout


# ── graph ───────────────────────────────────────────────────────────────────

class TestGraph:
    def test_table_output(self):
        result = runner.invoke(app, ["graph", *_cfg()])
        assert result.exit_code == 0
        assert "Team Dependency Graph" in result.stdout

    def test_json_output_is_valid(self):
        result = runner.invoke(app, ["graph", "--output", "json", *_cfg()])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        # to_dict() yields a graph structure; just assert it parsed to a dict.
        assert isinstance(data, dict)


# ── who_owns ──────────────────────────────────────────────────────────────────

class TestWhoOwns:
    def test_known_component_grounded(self):
        result = runner.invoke(app, ["who-owns", "auth", *_cfg()])
        assert result.exit_code == 0
        assert "Team Phoenix" in result.stdout

    def test_unknown_component(self):
        result = runner.invoke(app, ["who-owns", "does-not-exist-xyz", *_cfg()])
        assert result.exit_code == 0
        assert "No team claims ownership" in result.stdout


# ── when_ships ────────────────────────────────────────────────────────────────

class TestWhenShips:
    def test_runs_for_known_team(self):
        result = runner.invoke(app, ["when-ships", "Team Phoenix", *_cfg()])
        assert result.exit_code == 0
        # Either deliverables table or the graceful "none found" message.
        assert ("Upcoming Deliverables" in result.stdout
                or "No upcoming deliverables" in result.stdout)

    def test_unknown_team_is_graceful(self):
        result = runner.invoke(app, ["when-ships", "Nonexistent Team", *_cfg()])
        assert result.exit_code == 0
        assert "No upcoming deliverables" in result.stdout


# ── decisions ─────────────────────────────────────────────────────────────────

class TestDecisions:
    def test_search_runs(self):
        result = runner.invoke(app, ["decisions", "auth", *_cfg()])
        assert result.exit_code == 0
        # Output is either decision panels or the graceful empty message.
        assert result.stdout  # something was rendered

    def test_no_results_is_graceful(self):
        result = runner.invoke(app, ["decisions", "zzz-no-such-decision", *_cfg()])
        assert result.exit_code == 0
        assert "No decision logs found" in result.stdout


# ── scan ──────────────────────────────────────────────────────────────────────

class TestScan:
    def test_scan_runs(self):
        result = runner.invoke(app, ["scan", *_cfg()])
        assert result.exit_code == 0
        # Either issues were found or the clean message.
        assert ("issues" in result.stdout or "No issues detected" in result.stdout)


# ── export-skill ──────────────────────────────────────────────────────────────

class TestExportSkill:
    def test_writes_skill_pack(self, tmp_path):
        out = str(tmp_path / "skills")
        result = runner.invoke(app, ["export-skill", "Team Phoenix", "-o", out, *_cfg()])
        assert result.exit_code == 0
        assert "Skill pack" in result.stdout
        # SKILL.md should exist under the package dir.
        skill_files = list(tmp_path.rglob("SKILL.md"))
        assert skill_files, "export-skill did not write a SKILL.md"

    def test_unknown_team_exits_1(self, tmp_path):
        out = str(tmp_path / "skills")
        result = runner.invoke(app, ["export-skill", "Ghost Team", "-o", out, *_cfg()])
        assert result.exit_code == 1
        assert "No manifest found" in result.stdout


# ── import ────────────────────────────────────────────────────────────────────

class TestImport:
    def test_unknown_source_exits_1(self, tmp_path):
        bogus = tmp_path / "mystery.bin"
        bogus.write_text("not a recognizable export")
        result = runner.invoke(app, ["import", str(bogus), "--team", "Team Phoenix", *_cfg()])
        assert result.exit_code == 1
        assert "Couldn't recognize" in result.stdout

    def test_jira_csv_detected_and_ingested(self, tmp_path):
        # A minimal Jira-style CSV: detection keys off the .csv + header shape.
        csv = tmp_path / "export.csv"
        csv.write_text(
            "Issue key,Summary,Status,Priority,Assignee,Created,Updated\n"
            "PHX-1,Wire up auth,In Progress,High,Ada,2026-06-01,2026-06-10\n"
        )
        teams_out = tmp_path / "teams"
        teams_out.mkdir()
        # Use a tmp config so the ingest writes into tmp, not the real data dir.
        import yaml
        cfg = {
            "providers": {"jira": "local", "confluence": "local", "github": "local",
                          "slack": "local", "figma": "local"},
            "data": {"synthetic_path": str(tmp_path), "teams_dir": str(teams_out)},
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(cfg))
        result = runner.invoke(app, ["import", str(csv), "--team", "Team Phoenix",
                                     "--config", str(cfg_file)])
        # Detection must succeed (no "Couldn't recognize"), exit 0.
        assert result.exit_code == 0, result.stdout
        assert "Couldn't recognize" not in result.stdout


# ── build-manifest ────────────────────────────────────────────────────────────

class TestBuildManifest:
    def test_build_from_roster_csv_prints_yaml(self, tmp_path):
        roster = tmp_path / "roster.csv"
        roster.write_text("name,role,email\nAda Lovelace,Engineer,ada@acme.com\n")
        result = runner.invoke(app, ["build-manifest", str(roster),
                                     "--team", "Payments", *_cfg()])
        assert result.exit_code == 0
        assert "Drafted manifest for" in result.stdout
        assert "Payments" in result.stdout

    def test_build_writes_to_out_file(self, tmp_path):
        roster = tmp_path / "roster.csv"
        roster.write_text("name,role,email\nAda Lovelace,Engineer,ada@acme.com\n")
        out = tmp_path / "out" / "team.yaml"
        result = runner.invoke(app, ["build-manifest", str(roster),
                                     "--team", "Payments", "-o", str(out), *_cfg()])
        assert result.exit_code == 0
        assert out.exists()
        assert "Draft written to" in result.stdout


# ── refresh-manifest ──────────────────────────────────────────────────────────

class TestRefreshManifest:
    def test_unknown_team_exits_1(self, tmp_path):
        roster = tmp_path / "roster.csv"
        roster.write_text("name,role,email\nAda,Engineer,ada@acme.com\n")
        result = runner.invoke(app, ["refresh-manifest", str(roster),
                                     "--team", "Ghost Team", *_cfg()])
        assert result.exit_code == 1
        assert "No existing manifest" in result.stdout

    def test_known_team_runs(self, tmp_path):
        roster = tmp_path / "roster.csv"
        roster.write_text("name,role,email\nAda,Engineer,ada@acme.com\n")
        result = runner.invoke(app, ["refresh-manifest", str(roster),
                                     "--team", "Team Phoenix", *_cfg()])
        assert result.exit_code == 0
        assert "Manifest refresh" in result.stdout


# ── simulate-event ────────────────────────────────────────────────────────────

class TestSimulateEvent:
    def test_known_event_previews(self):
        result = runner.invoke(app, ["simulate-event", "design.library_published",
                                     "-s", "Button", "-t", "Team Phoenix", *_cfg()])
        assert result.exit_code == 0
        assert result.stdout  # explain() rendered something

    def test_unknown_event_lists_catalog(self):
        result = runner.invoke(app, ["simulate-event", "not.a.real.event", *_cfg()])
        assert result.exit_code == 0
        assert "Unknown event type" in result.stdout


# ── onboard ───────────────────────────────────────────────────────────────────

class TestOnboard:
    BRIEF = (
        "Client: Acme Corp\n"
        "We are redesigning the checkout experience.\n\n"
        "Experiences:\n"
        "- Search results page\n"
        "- Shopping checkout\n\n"
        "Teams:\n"
        "- Pair 1: Search\n"
        "- Pair 2: Checkout\n\n"
        "Principles:\n"
        "- Trust\n"
        "- Transparency\n\n"
        "North star: customers complete checkout faster\n"
        "Should we support guest checkout?\n"
    )

    def test_onboard_from_literal_text_generates_files(self, tmp_path):
        out = str(tmp_path / "imported")
        # Pass the brief as a literal argument (not a file path) and confirm
        # generation via stdin "y".
        result = runner.invoke(app, ["onboard", self.BRIEF, "-o", out, *_cfg()],
                               input="y\n")
        assert result.exit_code == 0, result.stdout
        assert "files written to" in result.stdout
        # At least one team.yaml was generated.
        assert list((tmp_path / "imported").rglob("team.yaml"))

    def test_onboard_from_file(self, tmp_path):
        brief_file = tmp_path / "brief.txt"
        brief_file.write_text(self.BRIEF)
        out = str(tmp_path / "imported2")
        result = runner.invoke(app, ["onboard", str(brief_file), "-o", out, *_cfg()],
                               input="y\n")
        assert result.exit_code == 0, result.stdout
        assert "Extracted" in result.stdout

    def test_onboard_decline_writes_nothing(self, tmp_path):
        out = str(tmp_path / "imported3")
        result = runner.invoke(app, ["onboard", self.BRIEF, "-o", out, *_cfg()],
                               input="n\n")
        assert result.exit_code == 0
        assert "Cancelled" in result.stdout
        assert not os.path.exists(out)

    def test_onboard_empty_content_exits_1(self, tmp_path):
        out = str(tmp_path / "imported4")
        result = runner.invoke(app, ["onboard", "   ", "-o", out, *_cfg()])
        assert result.exit_code == 1
        assert "No content provided" in result.stdout
