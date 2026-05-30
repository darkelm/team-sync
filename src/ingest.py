"""Channel-neutral ingest core.

Every surface — Slack file upload, a web upload, an MCP client, or the CLI —
calls into here. This is the "no-terminal setup scales beyond Slack" guarantee:
the import logic lives in one channel-agnostic place; each channel is a thin
adapter that hands us (filename, bytes or path, team) and shows the summary we
return. Nothing here knows or cares which channel it came from.
"""
from __future__ import annotations
import os
import re
import tempfile
import yaml


def teams_dir(config: str = "config.yaml") -> str:
    with open(config) as f:
        cfg = yaml.safe_load(f)
    return cfg.get("data", {}).get("teams_dir", "./data/synthetic/teams")


def slugify(team: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", team.lower()).strip("-")


def detect_source(path: str) -> str:
    """Classify an export by its path/content: jira | confluence | github | transcript | unknown."""
    from src.importers.transcript import looks_like_transcript
    if os.path.isfile(path):
        if looks_like_transcript(path):
            return "transcript"
        if path.lower().endswith(".csv"):
            return "jira"
    if os.path.isdir(path):
        if os.path.isdir(os.path.join(path, ".git")):
            return "github"
        for _, _, files in os.walk(path):
            if any(f.lower().endswith((".md", ".markdown", ".html", ".htm")) for f in files):
                return "confluence"
    return "unknown"


def ingest_path(path: str, team: str, config: str = "config.yaml") -> str:
    """Import an export from a local path. Returns a human-readable summary."""
    from src.providers.factory import Providers
    from src.importers.writer import write_team_json

    source = detect_source(path)
    slug = slugify(team)
    tdir = teams_dir(config)

    if source == "jira":
        from src.importers.jira_csv import import_jira_csv
        tickets = import_jira_csv(path, team)
        write_team_json(tickets, tdir, slug, "jira_tickets.json")
        return f"✓ Imported {len(tickets)} Jira tickets for {team}."

    if source == "confluence":
        from src.importers.confluence_export import import_confluence_export
        pages = import_confluence_export(path, team)
        decisions = sum(1 for p in pages if p.decision_log)
        write_team_json(pages, tdir, slug, "confluence_pages.json")
        return f"✓ Imported {len(pages)} Confluence pages ({decisions} decision logs) for {team}."

    if source == "github":
        from src.importers.github_clone import import_github_clone
        manifest = Providers(config).manifests.get_team(team)
        component_paths = {c.name: c.path for c in manifest.components.code} if manifest else {}
        prs = import_github_clone(path, team, component_paths)
        write_team_json(prs, tdir, slug, "pull_requests.json")
        return f"✓ Imported {len(prs)} merged PRs for {team}."

    if source == "transcript":
        import json
        from src.importers.transcript import parse_transcript
        from src.agent.meeting import MeetingAnalyzer
        segments = parse_transcript(path)
        title = os.path.splitext(os.path.basename(path))[0].replace("-", " ").replace("_", " ").title()
        analyzer = MeetingAnalyzer(Providers(config))
        notes = analyzer.analyze(segments, team, title)
        os.makedirs(os.path.join(tdir, slug), exist_ok=True)
        with open(os.path.join(tdir, slug, "meeting_decisions.json"), "w") as f:
            json.dump(analyzer.to_confluence_pages(notes), f, indent=2, default=str)
        write_team_json([notes], tdir, slug, "meeting_notes.json")
        return (f"✓ Analyzed meeting '{title}' for {team}: {len(notes.decisions)} decisions, "
                f"{len(notes.action_items)} action items, {len(notes.risks)} risks. "
                f"Decisions are now searchable.")

    return ("Couldn't recognize that file as a Jira CSV, Confluence export, git clone, "
            "or meeting transcript. Nothing imported.")


def ingest_upload(filename: str, data: bytes, team: str, config: str = "config.yaml") -> str:
    """Import an uploaded file (bytes). Writes to a temp file preserving the extension, then ingests."""
    suffix = os.path.splitext(filename)[1] or ".txt"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        return ingest_path(tmp_path, team, config)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
