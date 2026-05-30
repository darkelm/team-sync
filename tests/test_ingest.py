"""Tests for src/ingest.py — channel-neutral ingest core."""
from __future__ import annotations

import os
import json


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES_DIR = os.path.join(REPO_ROOT, "data", "exports", "samples")
JIRA_CSV = os.path.join(SAMPLES_DIR, "jira_export_sample.csv")
TRANSCRIPT_TXT = os.path.join(SAMPLES_DIR, "design-review-2026-05-28.txt")


class TestDetectSource:
    def test_jira_csv_detected(self):
        from src.ingest import detect_source
        assert detect_source(JIRA_CSV) == "jira"

    def test_transcript_detected(self):
        from src.ingest import detect_source
        assert detect_source(TRANSCRIPT_TXT) == "transcript"

    def test_git_repo_detected(self):
        from src.ingest import detect_source
        # REPO_ROOT is a git clone
        assert detect_source(REPO_ROOT) == "github"

    def test_unknown_returns_unknown(self, tmp_path):
        from src.ingest import detect_source
        f = tmp_path / "random.bin"
        f.write_bytes(b"\x00\x01\x02")
        assert detect_source(str(f)) == "unknown"

    def test_confluence_folder_detected(self, tmp_path):
        from src.ingest import detect_source
        (tmp_path / "page.md").write_text("# Meeting notes")
        assert detect_source(str(tmp_path)) == "confluence"


class TestSlugify:
    def test_basic(self):
        from src.ingest import slugify
        assert slugify("Team Phoenix") == "team-phoenix"

    def test_spaces_and_caps(self):
        from src.ingest import slugify
        assert slugify("My Test Team") == "my-test-team"

    def test_special_chars(self):
        from src.ingest import slugify
        result = slugify("Team A/B!@")
        assert result == "team-a-b"


class TestIngestUpload:
    def test_jira_csv_round_trip(self, tmp_ingest_config):
        """Uploading a Jira CSV should import tickets and confirm the summary."""
        from src.ingest import ingest_upload
        with open(JIRA_CSV, "rb") as f:
            data = f.read()
        result = ingest_upload("jira_export_sample.csv", data, "Team Phoenix", tmp_ingest_config)
        assert "4 Jira tickets" in result
        assert "Team Phoenix" in result

    def test_jira_csv_writes_json_file(self, tmp_ingest_config):
        """The imported JSON file should exist in the teams dir."""
        import yaml as _yaml
        from src.ingest import ingest_upload
        with open(JIRA_CSV, "rb") as f:
            data = f.read()
        ingest_upload("jira_export_sample.csv", data, "Team Phoenix", tmp_ingest_config)
        with open(tmp_ingest_config) as f:
            cfg = _yaml.safe_load(f)
        out = os.path.join(cfg["data"]["teams_dir"], "team-phoenix", "jira_tickets.json")
        assert os.path.exists(out)
        with open(out) as f:
            tickets = json.load(f)
        assert len(tickets) == 4

    def test_transcript_round_trip(self, tmp_ingest_config):
        """Uploading a transcript should produce a meeting summary result."""
        from src.ingest import ingest_upload
        with open(TRANSCRIPT_TXT, "rb") as f:
            data = f.read()
        result = ingest_upload("design-review-2026-05-28.txt", data, "Team Phoenix", tmp_ingest_config)
        assert "analyzed" in result.lower() or "Analyzed" in result

    def test_unrecognized_file_returns_message(self, tmp_ingest_config):
        from src.ingest import ingest_upload
        result = ingest_upload("random.bin", b"\x00\x01\x02\x03", "Team X", tmp_ingest_config)
        assert "couldn't recognize" in result.lower()


class TestIngestPath:
    def test_jira_csv_path(self, tmp_ingest_config):
        from src.ingest import ingest_path
        result = ingest_path(JIRA_CSV, "Team Phoenix", tmp_ingest_config)
        assert "4 Jira tickets" in result

    def test_confluence_folder_path(self, tmp_path, tmp_ingest_config):
        from src.ingest import ingest_path
        (tmp_path / "decision.html").write_text(
            "<html><title>ADR</title><body>decision: use OAuth</body></html>"
        )
        result = ingest_path(str(tmp_path), "Team Phoenix", tmp_ingest_config)
        assert "Confluence" in result or "page" in result.lower()
