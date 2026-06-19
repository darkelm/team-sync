"""
snapshot_scan.py — Nightly snapshot diff → Event emission (C1).

This is the "connectors-off" proactivity path: instead of real-time webhooks,
it diffs two snapshots of the teams data directory and fires Events for
anything that changed between them.

Use-case
--------
Run this on a nightly cron (or APScheduler job) to catch changes that didn't
come through a webhook:
  - New Jira tickets created since last night → work.created
  - Due dates shifted → roadmap.date_changed
  - New Figma components appearing in a team file → design.component_changed
  - Figma components removed (may matter to dependents)

SEPARATE PROCESS NOTE
---------------------
Like webhook_server.py, this file is independent of slack_bot.py.  It can
run as a one-off cron job or be scheduled inside the bot process via
APScheduler.  Both files share the same EventRouter brain — they just produce
Events through different means.

Usage
-----
CLI (compare current teams_dir against a prior snapshot):
    python snapshot_scan.py --since /path/to/old-snapshot-dir

Programmatic (from a scheduler):
    from snapshot_scan import run_snapshot_scan
    from src.providers.factory import Providers
    providers = Providers("config.yaml")
    run_snapshot_scan(old_dir="/tmp/last-night", new_dir="./data/synthetic/teams", providers=providers)

Snapshot format
---------------
A snapshot directory mirrors the teams_dir layout exactly:
    <snapshot>/
        team-phoenix/
            jira_tickets.json
            figma_components.json
        team-atlas/
            ...

To take a snapshot, simply copy the teams_dir:
    cp -r data/synthetic/teams /tmp/snapshot-$(date +%Y%m%d)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from src.agent.events import Event, EventRouter
from src.providers.factory import Providers

log = logging.getLogger("snapshot_scan")
logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------------------------
# Low-level JSON loaders
# ---------------------------------------------------------------------------

def _load_json(path: str | Path) -> list[dict]:
    """Load a JSON file as a list; return [] if missing or malformed."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        log.warning("Could not read %s", path)
        return []


# ---------------------------------------------------------------------------
# Per-team diff helpers — each returns a list of Events
# ---------------------------------------------------------------------------

def _diff_jira_tickets(
    old_tickets: list[dict],
    new_tickets: list[dict],
    team: str,
) -> list[Event]:
    """
    Diff two lists of Jira ticket dicts and return Events for:
    - New tickets (work.created)
    - Due-date changes on existing tickets (roadmap.date_changed)

    Keyed by ticket 'id' field.
    """
    events: list[Event] = []
    old_by_id = {t["id"]: t for t in old_tickets if "id" in t}
    new_by_id = {t["id"]: t for t in new_tickets if "id" in t}

    # --- New tickets ---
    for tid, ticket in new_by_id.items():
        if tid not in old_by_id:
            events.append(Event(
                type="work.created",
                subject=ticket.get("title", tid),
                source="snapshot",
                team=team,
                metadata={
                    "issue_key": tid,
                    "status": ticket.get("status", ""),
                    "priority": ticket.get("priority", ""),
                    "epic": ticket.get("epic", ""),
                },
            ))
            log.debug("work.created team=%s ticket=%s", team, tid)

    # --- Due-date changes on existing tickets ---
    for tid, new_ticket in new_by_id.items():
        old_ticket = old_by_id.get(tid)
        if old_ticket is None:
            continue  # already handled as new
        old_due = old_ticket.get("due_date", "")
        new_due = new_ticket.get("due_date", "")
        # Only fire if a due date is set and it actually changed
        if new_due and old_due != new_due:
            events.append(Event(
                type="roadmap.date_changed",
                subject=new_ticket.get("title", tid),
                source="snapshot",
                team=team,
                metadata={
                    "issue_key": tid,
                    "due_date_old": old_due,
                    "due_date_new": new_due,
                },
            ))
            log.debug("roadmap.date_changed team=%s ticket=%s %s→%s", team, tid, old_due, new_due)

    return events


def _diff_figma_components(
    old_components: list[dict],
    new_components: list[dict],
    team: str,
) -> list[Event]:
    """
    Diff two lists of Figma component dicts and return Events for:
    - New components appearing (design.component_changed with subtype "added")
    - Components removed (design.component_changed with subtype "removed") —
      useful to warn dependents.

    Keyed by component 'id' field; falls back to 'name' if id is absent.
    """
    events: list[Event] = []

    def _key(c: dict) -> str:
        return c.get("id") or c.get("name", "")

    old_by_key = {_key(c): c for c in old_components if _key(c)}
    new_by_key = {_key(c): c for c in new_components if _key(c)}

    # --- Added components ---
    for k, comp in new_by_key.items():
        if k not in old_by_key:
            events.append(Event(
                type="design.component_changed",
                subject=comp.get("name", k),
                source="snapshot",
                team=team,
                metadata={
                    "change": "added",
                    "file_name": comp.get("file_name", ""),
                    "component_id": comp.get("id", ""),
                },
            ))
            log.debug("design.component_changed added team=%s comp=%s", team, comp.get("name", k))

    # --- Removed components ---
    for k, comp in old_by_key.items():
        if k not in new_by_key:
            events.append(Event(
                type="design.component_changed",
                subject=comp.get("name", k),
                source="snapshot",
                team=team,
                metadata={
                    "change": "removed",
                    "file_name": comp.get("file_name", ""),
                    "component_id": comp.get("id", ""),
                },
            ))
            log.debug("design.component_changed removed team=%s comp=%s", team, comp.get("name", k))

    return events


# ---------------------------------------------------------------------------
# Core public API
# ---------------------------------------------------------------------------

def run_snapshot_scan(
    old_dir: str,
    new_dir: str,
    providers: Providers,
) -> int:
    """
    Diff two team data directories (old_dir vs new_dir), emit Events for each
    detected change, and dispatch them through the EventRouter.

    Returns the total number of Slack notifications dispatched.

    Parameters
    ----------
    old_dir  : path to the prior snapshot (read-only)
    new_dir  : path to the current teams_dir (read-only)
    providers: initialised Providers instance (controls Slack backend etc.)

    Diff mapping
    ------------
    jira_tickets.json
        new id         → work.created
        changed due_date → roadmap.date_changed

    figma_components.json
        new component  → design.component_changed (metadata.change="added")
        missing comp   → design.component_changed (metadata.change="removed")
    """
    router = EventRouter(providers)
    total_dispatched = 0
    total_events = 0

    old_root = Path(old_dir)
    new_root = Path(new_dir)

    if not new_root.exists():
        log.error("new_dir does not exist: %s", new_dir)
        return 0

    if not old_root.exists():
        log.warning("old_dir does not exist: %s — treating as empty baseline", old_dir)

    # Scan teams present in the new snapshot; compare against old.
    for new_team_dir in sorted(new_root.iterdir()):
        if not new_team_dir.is_dir():
            continue

        team_slug = new_team_dir.name
        old_team_dir = old_root / team_slug

        # Resolve team name from the manifest so Event.team is human-readable
        manifest_path = new_team_dir / "team.yaml"
        team_name = _resolve_team_name(manifest_path, providers)

        # ── Jira tickets ─────────────────────────────────────────────────
        old_tickets = _load_json(old_team_dir / "jira_tickets.json") if old_root.exists() else []
        new_tickets = _load_json(new_team_dir / "jira_tickets.json")
        jira_events = _diff_jira_tickets(old_tickets, new_tickets, team_name)

        # ── Figma components ──────────────────────────────────────────────
        old_components = _load_json(old_team_dir / "figma_components.json") if old_root.exists() else []
        new_components = _load_json(new_team_dir / "figma_components.json")
        figma_events = _diff_figma_components(old_components, new_components, team_name)

        # ── Dispatch all events for this team ─────────────────────────────
        for event in jira_events + figma_events:
            n = router.dispatch(event)
            total_dispatched += n
            total_events += 1
            log.info(
                "dispatched event type=%s subject=%r team=%s notifications=%d",
                event.type, event.subject, event.team, n,
            )

    log.info(
        "snapshot_scan complete: %d events emitted, %d Slack notifications dispatched",
        total_events, total_dispatched,
    )
    return total_dispatched


def _resolve_team_name(manifest_path: Path, providers: Providers) -> str:
    """
    Read the 'team' field directly from a team.yaml file.
    Falls back to the directory slug if the file is missing.
    """
    if manifest_path.exists():
        try:
            import yaml
            with open(manifest_path) as f:
                data = yaml.safe_load(f)
            return data.get("team", manifest_path.parent.name)
        except Exception as e:
            # The file exists but couldn't be parsed — falling back to the dir
            # slug silently could mis-attribute events, so surface it.
            log.warning("Could not parse team name from %s, using dir slug: %s", manifest_path, e)
    return manifest_path.parent.name


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_cli_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Snapshot diff scanner — compare a prior snapshot of the teams "
            "data directory against the current one and emit Events for "
            "any changes detected."
        )
    )
    p.add_argument(
        "--since",
        required=True,
        metavar="DIR",
        help="Path to the prior snapshot directory (the 'old' state).",
    )
    p.add_argument(
        "--teams-dir",
        metavar="DIR",
        default=None,
        help=(
            "Path to the current teams directory (defaults to the value in "
            "config.yaml → data.teams_dir, or ./data/synthetic/teams)."
        ),
    )
    p.add_argument(
        "--config",
        metavar="FILE",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml in cwd).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the Events that would be emitted without dispatching them "
            "to Slack."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    providers = Providers(args.config)

    # Resolve teams_dir
    if args.teams_dir:
        teams_dir = args.teams_dir
    else:
        from src.providers.factory import load_config
        cfg = load_config(args.config)
        teams_dir = cfg.get("data", {}).get("teams_dir", "./data/synthetic/teams")

    if args.dry_run:
        print(f"[dry-run] Would diff {args.since!r} → {teams_dir!r}")
        _dry_run(args.since, teams_dir, providers)
        return

    total = run_snapshot_scan(
        old_dir=args.since,
        new_dir=teams_dir,
        providers=providers,
    )
    print(f"Done. {total} Slack notification(s) dispatched.")


def _dry_run(old_dir: str, new_dir: str, providers: Providers) -> None:
    """Print events without dispatching to Slack."""
    old_root = Path(old_dir)
    new_root = Path(new_dir)

    for new_team_dir in sorted(new_root.iterdir()):
        if not new_team_dir.is_dir():
            continue
        team_slug = new_team_dir.name
        old_team_dir = old_root / team_slug
        manifest_path = new_team_dir / "team.yaml"
        team_name = _resolve_team_name(manifest_path, providers)

        old_tickets = _load_json(old_team_dir / "jira_tickets.json") if old_root.exists() else []
        new_tickets = _load_json(new_team_dir / "jira_tickets.json")
        jira_events = _diff_jira_tickets(old_tickets, new_tickets, team_name)

        old_comps = _load_json(old_team_dir / "figma_components.json") if old_root.exists() else []
        new_comps = _load_json(new_team_dir / "figma_components.json")
        figma_events = _diff_figma_components(old_comps, new_comps, team_name)

        for event in jira_events + figma_events:
            print(f"  EVENT  type={event.type!r}  subject={event.subject!r}  team={event.team!r}  metadata={event.metadata}")


if __name__ == "__main__":
    main()
