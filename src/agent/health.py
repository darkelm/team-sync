"""Leadership rollup — team health framed for MDs/leadership, not ICs.

Deterministic read (🟢 on-track / 🟡 at-risk / 🔴 blocked) over signals we
already compute, plus top risks in plain language, week-over-week trajectory,
and who to talk to. No per-component noise. AI-optional: heuristic phrasing by
default; the structure is identical with or without a key.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from .detector import DriftDetector
from .plain import labels
from ..providers.factory import Providers

SNAPSHOT_PATH = "data/health_snapshots.json"  # default; overridden per-project


def _snapshot_path(config: str = "config.yaml") -> str:
    """Per-project snapshot path so multiple projects don't collide."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", config.lower().replace(".yaml", "")).strip("-")
    return f"data/{slug}-health-snapshots.json" if slug != "config" else SNAPSHOT_PATH

STATUS_LABEL = {"green": "🟢 On track", "amber": "🟡 At risk", "red": "🔴 Blocked"}


@dataclass
class TeamHealth:
    team: str
    status: str            # green | amber | red
    headline: str
    risks: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    contact: str = ""

    @property
    def label(self) -> str:
        return STATUS_LABEL.get(self.status, self.status)


class HealthAssessor:
    def __init__(self, providers: Providers, config: str = "config.yaml"):
        self.p = providers
        self.detector = DriftDetector(providers)
        self.labels = labels(config)
        self._snap_path = _snapshot_path(config)
        self._snapshots = self._load_snapshots()

    # ── persistence (week-over-week trajectory) ───────────────────────────────

    def _load_snapshots(self) -> dict:
        if os.path.exists(self._snap_path):
            try:
                with open(self._snap_path) as f:
                    return json.load(f)
            except (OSError, ValueError) as e:
                # Snapshot file exists but couldn't be read/parsed — losing the
                # week-over-week trajectory silently would hide real corruption.
                print(f"[health] could not load snapshots from {self._snap_path}, resetting: {e}", flush=True)
                return {}
        return {}

    def _save_snapshots(self) -> None:
        os.makedirs(os.path.dirname(self._snap_path) or ".", exist_ok=True)
        with open(self._snap_path, "w") as f:
            json.dump(self._snapshots, f, indent=2)

    # ── assessment ────────────────────────────────────────────────────────────

    def assess(self, team_name: str, record: bool = True) -> Optional[TeamHealth]:
        team = self.p.manifests.get_team(team_name)
        if not team:
            return None
        name = team.team
        today = date.today()

        issues = [i for i in self.detector.run_all() if name in i.teams_involved]
        crit = [i for i in issues if i.severity.value == "critical"]
        high = [i for i in issues if i.severity.value == "high"]
        drift = [i for i in issues if i.type in ("design_drift", "code_drift")]
        missing_decisions = [i for i in issues if i.type == "missing_decision_log"]
        predictions = [c for c in self.detector.predict_conflicts() if name in c.teams_involved]

        tickets = self.p.jira.get_tickets(name)
        overdue = [t for t in tickets if t.due_date and t.due_date < today and t.status.value != "done"]
        due_soon_unstarted = [
            t for t in tickets
            if t.due_date and today <= t.due_date <= today + timedelta(days=14)
            and t.status.value in ("backlog", "todo")
        ]
        stale = team.last_verified is None or team.last_verified < today - timedelta(days=30)

        # Status
        if crit or overdue:
            status = "red"
        elif high or predictions or due_soon_unstarted or drift:
            status = "amber"
        else:
            status = "green"

        # Risks — plain language, leadership-facing, prioritized, top 3
        risks: list[str] = []
        if overdue:
            ex = overdue[0].title
            risks.append(f"{len(overdue)} deliverable(s) past their due date — e.g. \"{ex}\"")
        for c in predictions:
            others = [t for t in c.teams_involved if t != name]
            risks.append(f"Possible collision with {', '.join(others) or 'another team'} on shared work — coordinate before it blocks")
        if due_soon_unstarted:
            risks.append(f"{len(due_soon_unstarted)} item(s) due within 2 weeks not yet started")
        if missing_decisions:
            risks.append(f"{len(missing_decisions)} significant change(s) planned without a written decision record")
        if drift:
            risks.append(f"{len(drift)} inconsistency(ies) with the shared design system")
        if stale:
            risks.append("Team profile hasn't been verified in 30+ days — info may be out of date")
        risks = risks[:3]

        # Headline
        if status == "green":
            headline = f"{name} is on track — no blocking risks right now."
        elif status == "red":
            why = "deliverables past due" if overdue else "a critical blocker"
            headline = f"{name} is blocked — {why}. Needs attention this week."
        else:
            headline = f"{name} is mostly on track with {len(risks)} risk(s) worth watching."

        contact = f"{team.owner.name} ({team.owner.slack_handle})"

        changes = self._diff_snapshot(name, status, risks)
        if record:
            self._snapshots[name] = {"date": str(today), "status": status, "risks": risks}
            self._save_snapshots()

        return TeamHealth(team=name, status=status, headline=headline,
                          risks=risks, changes=changes, contact=contact)

    def _diff_snapshot(self, name: str, status: str, risks: list[str]) -> list[str]:
        prev = self._snapshots.get(name)
        if not prev:
            return ["First health check — no prior snapshot to compare."]
        out = []
        if prev.get("status") != status:
            out.append(f"Status moved {STATUS_LABEL.get(prev.get('status',''), prev.get('status',''))} → {STATUS_LABEL.get(status, status)} since last check ({prev.get('date')}).")
        prev_risks = set(prev.get("risks", []))
        for r in risks:
            if r not in prev_risks:
                out.append(f"New: {r}")
        for r in prev_risks:
            if r not in risks:
                out.append(f"Resolved: {r}")
        return out or ["No change since last check."]

    def portfolio(self) -> list[TeamHealth]:
        order = {"red": 0, "amber": 1, "green": 2}
        healths = [h for t in self.p.manifests.get_all_teams() if (h := self.assess(t.team))]
        healths.sort(key=lambda h: order.get(h.status, 9))
        return healths

    # ── rendering (leadership-framed Slack text) ──────────────────────────────

    def format_team(self, h: TeamHealth) -> str:
        lines = [f"*{h.label} — {h.team}*", h.headline, ""]
        if h.risks:
            lines.append("*Top risks*")
            lines += [f"  • {r}" for r in h.risks]
            lines.append("")
        if h.changes:
            lines.append("*Since last check*")
            lines += [f"  • {c}" for c in h.changes]
            lines.append("")
        lines.append(f"_Who to talk to: {h.contact}_")
        return "\n".join(lines)

    def format_portfolio(self) -> str:
        healths = self.portfolio()
        if not healths:
            return "No teams to report on yet."
        counts = {"red": 0, "amber": 0, "green": 0}
        for h in healths:
            counts[h.status] = counts.get(h.status, 0) + 1
        title = self.labels["portfolio"].title()
        lines = [
            f"*📊 {title} status*",
            f"🔴 {counts['red']} blocked · 🟡 {counts['amber']} at risk · 🟢 {counts['green']} on track",
            "",
        ]
        for h in healths:
            risk = f" — {h.risks[0]}" if h.risks else ""
            lines.append(f"{h.label}  *{h.team}*{risk}")
        lines.append("")
        lines.append("_Ask `@syncbot how's <team> doing?` for the detail on any one._")
        return "\n".join(lines)
