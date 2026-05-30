"""Tests for src/importers/ — jira_csv, confluence_export, transcript, github_clone."""
from __future__ import annotations

import os
import tempfile


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES_DIR = os.path.join(REPO_ROOT, "data", "exports", "samples")
JIRA_CSV = os.path.join(SAMPLES_DIR, "jira_export_sample.csv")
TRANSCRIPT_TXT = os.path.join(SAMPLES_DIR, "design-review-2026-05-28.txt")


# ---------------------------------------------------------------------------
# jira_csv
# ---------------------------------------------------------------------------

class TestJiraCsv:
    def test_import_returns_tickets(self):
        from src.importers.jira_csv import import_jira_csv
        tickets = import_jira_csv(JIRA_CSV, "Team Phoenix")
        assert len(tickets) == 4, "sample CSV has 4 tickets"

    def test_first_ticket_fields(self):
        from src.importers.jira_csv import import_jira_csv
        from src.core.schemas import TicketStatus, TicketPriority
        tickets = import_jira_csv(JIRA_CSV, "Team Phoenix")
        t = tickets[0]
        assert t.id == "GAPTT-1"
        assert t.title == "Set up design token pipeline"
        assert t.status == TicketStatus.in_progress
        assert t.priority == TicketPriority.high
        assert "design-system" in t.labels
        assert "tokens" in t.components

    def test_team_assigned(self):
        from src.importers.jira_csv import import_jira_csv
        tickets = import_jira_csv(JIRA_CSV, "Team Phoenix")
        assert all(t.team == "Team Phoenix" for t in tickets)

    def test_status_normalization(self):
        from src.importers.jira_csv import _norm_status
        from src.core.schemas import TicketStatus
        assert _norm_status("In Progress") == TicketStatus.in_progress
        assert _norm_status("Done") == TicketStatus.done
        assert _norm_status("Closed") == TicketStatus.done
        assert _norm_status("Blocked") == TicketStatus.blocked
        assert _norm_status("To Do") == TicketStatus.todo
        assert _norm_status("Backlog") == TicketStatus.backlog

    def test_priority_normalization(self):
        from src.importers.jira_csv import _norm_priority
        from src.core.schemas import TicketPriority
        assert _norm_priority("High") == TicketPriority.high
        assert _norm_priority("Highest") == TicketPriority.critical
        assert _norm_priority("Blocker") == TicketPriority.critical
        assert _norm_priority("Low") == TicketPriority.low
        assert _norm_priority("Medium") == TicketPriority.medium

    def test_empty_csv_returns_empty(self):
        from src.importers.jira_csv import import_jira_csv
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
            f.write("")
            tmp = f.name
        try:
            result = import_jira_csv(tmp, "Team X")
            assert result == []
        finally:
            os.unlink(tmp)

    def test_date_parsing(self):
        from src.importers.jira_csv import _parse_date
        assert _parse_date("") is None
        assert _parse_date("2026-05-01") is not None
        assert _parse_date("garbage") is None


# ---------------------------------------------------------------------------
# confluence_export
# ---------------------------------------------------------------------------

class TestConfluenceExport:
    def test_import_html_and_md(self, tmp_path):
        from src.importers.confluence_export import import_confluence_export
        (tmp_path / "decision.html").write_text(
            "<html><title>OAuth Decision</title>"
            "<body>decision: Use OAuth 2.0. rationale: security best practice</body></html>"
        )
        (tmp_path / "notes.md").write_text("# Meeting Notes\nWe met to discuss the API.")

        pages = import_confluence_export(str(tmp_path), "Team Phoenix")
        assert len(pages) == 2

    def test_decision_log_detected(self, tmp_path):
        from src.importers.confluence_export import import_confluence_export
        (tmp_path / "decision.html").write_text(
            "<html><title>ADR: Auth Strategy</title>"
            "<body>decided to use JWT. rationale: simplicity</body></html>"
        )
        pages = import_confluence_export(str(tmp_path), "Team Phoenix")
        assert pages[0].decision_log is not None
        assert pages[0].decision_log.team == "Team Phoenix"

    def test_non_decision_page(self, tmp_path):
        from src.importers.confluence_export import import_confluence_export
        (tmp_path / "general.md").write_text("# General Meeting\nWe discussed scope.")
        pages = import_confluence_export(str(tmp_path), "Team Phoenix")
        assert pages[0].decision_log is None

    def test_ignores_non_doc_files(self, tmp_path):
        from src.importers.confluence_export import import_confluence_export
        (tmp_path / "logo.png").write_bytes(b"\x89PNG")
        (tmp_path / "page.html").write_text("<html><title>Test</title><body>Hello</body></html>")
        pages = import_confluence_export(str(tmp_path), "Team X")
        # Only the .html file should be imported
        assert len(pages) == 1

    def test_empty_folder_returns_empty(self, tmp_path):
        from src.importers.confluence_export import import_confluence_export
        pages = import_confluence_export(str(tmp_path), "Team X")
        assert pages == []


# ---------------------------------------------------------------------------
# transcript parser
# ---------------------------------------------------------------------------

class TestTranscriptParser:
    def test_parse_returns_segments(self):
        from src.importers.transcript import parse_transcript
        segs = parse_transcript(TRANSCRIPT_TXT)
        assert len(segs) == 14, "design-review transcript has 14 merged segments"

    def test_first_speaker(self):
        from src.importers.transcript import parse_transcript
        segs = parse_transcript(TRANSCRIPT_TXT)
        assert segs[0].speaker == "Amara Osei"

    def test_looks_like_transcript_positive(self):
        from src.importers.transcript import looks_like_transcript
        assert looks_like_transcript(TRANSCRIPT_TXT) is True

    def test_looks_like_transcript_negative_csv(self):
        from src.importers.transcript import looks_like_transcript
        assert looks_like_transcript(JIRA_CSV) is False

    def test_vtt_detected(self):
        from src.importers.transcript import looks_like_transcript
        with tempfile.NamedTemporaryFile(suffix=".vtt", mode="w", delete=False) as f:
            f.write("WEBVTT\n\n")
            tmp = f.name
        try:
            assert looks_like_transcript(tmp) is True
        finally:
            os.unlink(tmp)

    def test_segments_have_text(self):
        from src.importers.transcript import parse_transcript
        segs = parse_transcript(TRANSCRIPT_TXT)
        assert all(s.text.strip() for s in segs)

    def test_consecutive_same_speaker_merged(self):
        """Two consecutive lines from the same speaker must become one segment."""
        from src.importers.transcript import parse_transcript
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("Alice: Hello everyone.\nAlice: Let's get started.\nBob: Sure.\n")
            tmp = f.name
        try:
            segs = parse_transcript(tmp)
            # Only 2 speakers: Alice (merged) and Bob
            assert len(segs) == 2
            assert segs[0].speaker == "Alice"
            assert "Hello" in segs[0].text and "started" in segs[0].text
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# github_clone
# ---------------------------------------------------------------------------

class TestGithubClone:
    def test_non_merge_repo_returns_empty(self):
        """On a repo with no merge commits, import_github_clone returns []."""
        from src.importers.github_clone import import_github_clone
        prs = import_github_clone(REPO_ROOT, "Team Phoenix", {})
        # This repo may or may not have merges — the call must not raise
        assert isinstance(prs, list)

    def test_returns_list_type(self, tmp_path):
        """On a non-git directory, git returns empty output → result is []."""
        from src.importers.github_clone import import_github_clone
        # tmp_path is not a git repo; git log returns empty; should return []
        result = import_github_clone(str(tmp_path), "Team X", {})
        assert result == []
