"""Weekly digest generator — produces per-team summaries for Slack."""
from datetime import date, timedelta
from typing import Optional
from ..core.schemas import TeamDigest
from ..providers.factory import Providers
from .detector import DriftDetector, evaluate_alert_gate
from ..log import get_logger

log = get_logger(__name__)


class DigestGenerator:
    def __init__(self, providers: Providers, *, apply_alert_gate: bool = True):
        self.p = providers
        self.detector = DriftDetector(providers)
        from .preferences import NotificationPreferences
        self.prefs = NotificationPreferences()
        # When True, proactive alert items (open conflicts, predictions, action
        # items) are filtered through the four-condition notification gate. The
        # raw scan in DriftDetector.run_all() is untouched — gating happens only
        # at this selection layer. Disable for an unfiltered "everything" view.
        self.apply_alert_gate = apply_alert_gate

    def _passes_alert_gate(self, issue, team_name: str) -> bool:
        """Whether a single issue/prediction should fire as a proactive alert for `team_name`.

        Four conditions, all required: cross-team, high-confidence (>= team
        threshold), actionable, and fresh (every involved team's manifest is not
        stale). Severity is also already checked by callers via prefs.severity_ok;
        re-checking here keeps the gate self-contained and correct in isolation.
        """
        if not self.apply_alert_gate:
            return True
        return evaluate_alert_gate(issue, self.p, self.prefs, team_name).passed

    def _gate_reason(self, issue, team_name: str) -> str:
        """The 'why you got this' line for an issue that passed the gate."""
        return evaluate_alert_gate(issue, self.p, self.prefs, team_name).explanation

    # ── per-alert de-duplication ──────────────────────────────────────────────
    #
    # The whole-digest quality gate (NotificationPreferences.last_signature) skips
    # a digest that's byte-identical to the last one. That's coarse: one new alert
    # re-sends every old alert with it. This extends the same idea to the alert
    # level — each fired alert gets a stable signature, and an alert whose
    # signature was sent on the *previous* run is dropped from this run so the
    # same nag doesn't repeat run-over-run. State lives in a small sidecar JSON so
    # it survives the per-run, fresh-DigestGenerator lifecycle the scheduler uses.

    ALERT_DEDUP_PATH = "data/alert_dedup.json"

    @staticmethod
    def _alert_signature(team_name: str, item) -> str:
        import hashlib
        ident = getattr(item, "id", "") or getattr(item, "title", "")
        return hashlib.sha256(f"{team_name}|{ident}".encode()).hexdigest()[:16]

    def _load_dedup(self) -> dict:
        import json
        import os
        if not os.path.exists(self.ALERT_DEDUP_PATH):
            return {}
        try:
            with open(self.ALERT_DEDUP_PATH) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_dedup(self, data: dict) -> None:
        import json
        import os
        os.makedirs(os.path.dirname(self.ALERT_DEDUP_PATH) or ".", exist_ok=True)
        with open(self.ALERT_DEDUP_PATH, "w") as f:
            json.dump(data, f, indent=2)

    def _dedup_digest(self, digest: TeamDigest, seen: dict) -> tuple[TeamDigest, set]:
        """Drop alerts already sent on the previous run; return (filtered, this-run sigs).

        `seen[team]` is the list of alert signatures sent on the previous run.
        Returns the digest with repeated alerts removed plus the full set of
        signatures fired *this* run (to persist for the next comparison).
        """
        prev = set(seen.get(digest.team, []))
        kept_conflicts, kept_predictions, fired = [], [], set()
        for issue in digest.open_conflicts:
            sig = self._alert_signature(digest.team, issue)
            fired.add(sig)
            if sig not in prev:
                kept_conflicts.append(issue)
        for conflict in digest.predicted_conflicts:
            sig = self._alert_signature(digest.team, conflict)
            fired.add(sig)
            if sig not in prev:
                kept_predictions.append(conflict)
        deduped = digest.model_copy(update={
            "open_conflicts": kept_conflicts,
            "predicted_conflicts": kept_predictions,
        })
        return deduped, fired

    def _design_system_team(self) -> Optional[str]:
        """Detect which team owns the design system library — no hardcoded names.

        Signal 1: the team that owns Figma library components.
        Signal 2: the team whose own Figma file is the shared design_system_library.
        """
        libs = self.p.figma.get_library_components()
        if libs:
            from collections import Counter
            return Counter(c.team for c in libs).most_common(1)[0][0]
        for t in self.p.manifests.get_all_teams():
            if t.design_system_library and any(f.url == t.design_system_library for f in t.figma_files):
                return t.team
        return None

    def generate_for_team(self, team_name: str) -> TeamDigest:
        team = self.p.manifests.get_team(team_name)
        if not team:
            raise ValueError(f"Team '{team_name}' not found")

        prefs = self.prefs.get(team_name)
        week_of = date.today()
        recent_prs = self.p.github.get_recent_prs(days=7)
        all_issues = self.detector.run_all()
        # Severity-filtered slice for the summary sections (design updates, etc.).
        team_issues = [i for i in all_issues if team_name in i.teams_involved
                       and self.prefs.severity_ok(team_name, i.severity.value)]
        predictions = [c for c in self.detector.predict_conflicts()
                       if team_name in c.teams_involved
                       and self.prefs.severity_ok(team_name, c.severity.value)]

        # Notification discipline: only items clearing the four-condition gate
        # (cross-team + high-confidence + actionable + fresh) become proactive
        # alerts. This is the selection layer; run_all() above is unchanged.
        alert_issues = [i for i in team_issues if self._passes_alert_gate(i, team_name)]
        alert_predictions = [c for c in predictions if self._passes_alert_gate(c, team_name)]

        show_dev = prefs["sections"].get("dev", True)
        show_design = prefs["sections"].get("design", True)

        # Dev updates: PRs from dependencies that might affect this team
        dep_teams = [d.team for d in team.dependencies]
        dep_prs = [p for p in recent_prs if p.team in dep_teams]
        dev_updates = [f"[{p.team}] Merged: {p.title}" for p in dep_prs] if show_dev else []

        # Design updates: Figma drift issues affecting this team
        design_issues = [i for i in team_issues if i.type == "design_drift"]
        design_updates = ([f"Design drift detected: {i.title}" for i in design_issues]
                          if show_design else [])

        # Check for design system updates from whichever team owns the library
        ds_team = self._design_system_team()
        if show_design and ds_team and ds_team != team_name:
            for pr in (p for p in recent_prs if p.team == ds_team):
                design_updates.append(f"[Design System] {ds_team} merged: {pr.title} — review your Figma files")

        dep_changes = []
        for dep_pr in dep_prs:
            if dep_pr.cross_team_impact and team_name in dep_pr.cross_team_impact:
                dep_changes.append(f"⚠ {dep_pr.team} merged changes that affect your team: {dep_pr.title}")

        # Action items are proactive nudges, so they ride the gate too — and we
        # cheaply stamp the "why you got this" reasoning onto each one.
        action_items = []
        for issue in alert_issues:
            why = self._gate_reason(issue, team_name)
            action_items.append(f"[{issue.severity.value.upper()}] {issue.suggested_action} _(why: {why})_")
        for conflict in alert_predictions:
            why = self._gate_reason(conflict, team_name)
            action_items.append(f"[PREDICTED] {conflict.suggested_action} _(why: {why})_")

        # Manifest freshness — surface stale ownership/dep data so the digest
        # isn't quietly authoritative on rotted manifests.
        if team.last_verified is None:
            staleness = ("⚠️ This team's manifest has never been verified — run "
                         "`syncbot refresh-manifest` so ownership and dependencies are trustworthy.")
        elif team.last_verified < date.today() - timedelta(days=30):
            staleness = (f"⚠️ This team's manifest was last verified {team.last_verified} "
                         "(>30 days ago) — run `syncbot refresh-manifest`.")
        else:
            staleness = None

        return TeamDigest(
            team=team_name,
            week_of=week_of,
            dev_updates=dev_updates,
            design_updates=design_updates,
            dependency_changes=dep_changes,
            open_conflicts=alert_issues,
            predicted_conflicts=alert_predictions,
            action_items=action_items,
            staleness=staleness,
        )

    def format_slack_message(self, digest: TeamDigest) -> str:
        lines = [
            f"*📋 Weekly Sync Digest — {digest.team}*",
            f"_Week of {digest.week_of}_",
            "",
        ]

        if digest.dev_updates:
            lines.append("*🔧 Dev Updates from Dependencies*")
            lines.extend([f"  • {u}" for u in digest.dev_updates])
            lines.append("")

        if digest.design_updates:
            lines.append("*🎨 Design Updates*")
            lines.extend([f"  • {u}" for u in digest.design_updates])
            lines.append("")

        if digest.dependency_changes:
            lines.append("*⚠️ Changes Affecting Your Team*")
            lines.extend([f"  • {c}" for c in digest.dependency_changes])
            lines.append("")

        if digest.open_conflicts:
            lines.append(f"*🚨 Open Issues ({len(digest.open_conflicts)})*")
            for issue in digest.open_conflicts[:3]:
                lines.append(f"  • [{issue.severity.value.upper()}] {issue.title}")
            lines.append("")

        if digest.predicted_conflicts:
            lines.append(f"*🔮 Predicted Conflicts ({len(digest.predicted_conflicts)})*")
            for conflict in digest.predicted_conflicts[:3]:
                lines.append(f"  • {conflict.title}")
            lines.append("")

        if digest.action_items:
            lines.append("*✅ Action Items*")
            lines.extend([f"  • {a}" for a in digest.action_items[:5]])

        if digest.staleness:
            lines.append("")
            lines.append(digest.staleness)

        lines.append("")
        lines.append("_Ask @syncbot anything: `@syncbot who owns <component>` | `@syncbot when does <team> ship` | `@syncbot scan for conflicts`_")

        return "\n".join(lines)

    def _signature(self, digest: TeamDigest) -> str:
        """Stable fingerprint of a digest's actionable content — for the quality gate."""
        import hashlib
        parts = (
            sorted(i.id for i in digest.open_conflicts)
            + sorted(c.id for c in digest.predicted_conflicts)
            + sorted(digest.dependency_changes)
        )
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    def post_all_digests(self, force: bool = False) -> dict:
        """Post each team's digest, respecting pause and the quality gate.

        force=True bypasses the gate (for on-demand `post digests`).

        Returns a result summary so callers can report honestly:
        {"sent": [(team, channel)], "failed": [(team, channel)],
         "paused": [team], "unchanged": [team]}. A digest is only marked
        sent (and its signature recorded) if delivery actually succeeded.
        """
        results = {"sent": [], "failed": [], "paused": [], "unchanged": []}
        # Per-alert dedup state from the previous run (skipped under force, which
        # is the on-demand "give me everything now" path).
        dedup = {} if force else self._load_dedup()
        for team in self.p.manifests.get_all_teams():
            name = team.team
            if self.prefs.is_paused(name):
                log.info("%s is paused — skipping.", name)
                results["paused"].append(name)
                continue
            digest = self.generate_for_team(name)
            # Drop alerts already sent last run so the same nag doesn't repeat.
            fired = None
            if not force:
                digest, fired = self._dedup_digest(digest, dedup)
            sig = self._signature(digest)
            if not force and not self.prefs.changed_since_last(name, sig):
                log.info("%s — nothing new since last digest, skipping.", name)
                results["unchanged"].append(name)
                continue
            # A Slack-registered channel ("send <team> digest here") overrides the
            # manifest's slack_channel. Channel IDs are robust to renames.
            target = self.prefs.get_digest_channel(name) or team.slack_channel
            display = self.prefs.get(name).get("digest_channel_name") or target
            ok = self.p.slack.post_digest(target, self.format_slack_message(digest))
            if ok:
                self.prefs.record_signature(name, sig)
                if fired is not None:
                    dedup[name] = sorted(fired)
                results["sent"].append((name, display))
            else:
                log.warning("%s — delivery to %s FAILED.", name, display)
                results["failed"].append((name, display))
        if not force:
            self._save_dedup(dedup)
        return results
