"""Drift and conflict detection — runs across all providers to find issues."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from ..core.schemas import DriftIssue, ConflictPrediction, DriftSeverity
from ..providers.factory import Providers
from .freshness import is_fresh


# ── Notification discipline (alert gating) ────────────────────────────────────
#
# run_all()/predict_conflicts() are the RAW scan — they return everything found,
# and the golden detector tests assert exact counts on that raw output. The gate
# below is applied LATER, at the notification/digest selection layer, so a noisy
# raw scan still becomes a disciplined alert stream. An item only earns a
# proactive alert when ALL FOUR conditions hold:
#
#   1. cross-team      — involves >= 2 teams (single-team churn isn't coordination)
#   2. high-confidence — severity at/above the team's threshold (prefs.severity_ok)
#   3. actionable      — has a non-empty suggested_action (there's something to do)
#   4. fresh           — every involved team's manifest is reasonably fresh
#                        (freshness.is_fresh); never alert off rotted ownership data
#
# Keep it additive and configurable: callers can relax individual conditions
# (e.g. require_cross_team=False) without touching the raw scan.


@dataclass
class AlertGateResult:
    """The verdict for one issue plus the per-condition reasoning ('why you got this')."""
    passed: bool
    reasons: dict          # condition -> bool (cross_team, high_confidence, actionable, fresh)
    explanation: str       # short human line, cheap to surface in a digest/log

    def __bool__(self) -> bool:
        return self.passed


def evaluate_alert_gate(
    issue,
    providers: Providers,
    prefs,
    team_name: str | None = None,
    *,
    require_cross_team: bool = True,
    require_high_confidence: bool = True,
    require_actionable: bool = True,
    require_fresh: bool = True,
) -> AlertGateResult:
    """Decide whether `issue` deserves a proactive alert, and explain why.

    Works for both DriftIssue and ConflictPrediction (both expose
    `teams_involved`, `severity`, and `suggested_action`).

    `team_name` scopes the severity check to one team's threshold. When omitted,
    severity passes if it clears the threshold for ANY involved team (the most
    permissive interpretation — a critical-to-someone alert still fires).
    """
    teams = list(getattr(issue, "teams_involved", []) or [])
    severity = getattr(getattr(issue, "severity", None), "value", None)
    action = (getattr(issue, "suggested_action", "") or "").strip()

    # 1. cross-team — >= 2 distinct teams.
    cross_team = len({t for t in teams if t}) >= 2

    # 2. high-confidence — severity clears the team's configured threshold.
    if team_name is not None:
        high_confidence = bool(severity) and prefs.severity_ok(team_name, severity)
    else:
        high_confidence = bool(severity) and any(
            prefs.severity_ok(t, severity) for t in teams
        ) if teams else False

    # 3. actionable — there is a concrete suggested action.
    actionable = bool(action)

    # 4. fresh — every involved team's manifest is reasonably fresh. An alert that
    #    spans a stale team is suppressed: we won't push coordination off rotted data.
    fresh = True
    if teams:
        for t in teams:
            team_obj = providers.manifests.get_team(t)
            if team_obj is None or not is_fresh(team_obj):
                fresh = False
                break
    else:
        fresh = False  # an alert involving no team can't be freshness-verified

    reasons = {
        "cross_team": cross_team,
        "high_confidence": high_confidence,
        "actionable": actionable,
        "fresh": fresh,
    }

    # A condition only blocks if it's required. This keeps the gate configurable.
    checks = {
        "cross_team": (cross_team, require_cross_team),
        "high_confidence": (high_confidence, require_high_confidence),
        "actionable": (actionable, require_actionable),
        "fresh": (fresh, require_fresh),
    }
    failed = [name for name, (ok, required) in checks.items() if required and not ok]
    passed = not failed

    if passed:
        explanation = "cross-team + high-confidence + actionable + fresh"
    else:
        labels = {
            "cross_team": "single-team",
            "high_confidence": "below severity threshold",
            "actionable": "no suggested action",
            "fresh": "stale/unverified manifest",
        }
        explanation = "suppressed: " + ", ".join(labels[n] for n in failed)

    return AlertGateResult(passed=passed, reasons=reasons, explanation=explanation)


class DriftDetector:
    def __init__(self, providers: Providers):
        self.p = providers

    def run_all(self) -> list[DriftIssue]:
        issues = []
        issues.extend(self._detect_design_drift())
        issues.extend(self._detect_code_drift())
        issues.extend(self._detect_missing_decision_logs())
        issues.extend(self._detect_cross_team_pr_impact())
        return issues

    def predict_conflicts(self) -> list[ConflictPrediction]:
        conflicts = []
        conflicts.extend(self._predict_planned_work_conflicts())
        return conflicts

    def sunset_report(self) -> list[dict]:
        """Components marked deprecated in their manifest, each with sunset date,
        replacement, and the teams still depending on it (migration exposure).

        Separate from run_all() — this reads manifest lifecycle metadata (RFC
        8594-style Sunset semantics), not live drift, so the raw-scan golden
        counts are untouched. This is the design-side deprecation lifecycle the
        field says nobody does: detect who's on a sunsetting component and size
        the migration before it breaks.
        """
        teams = self.p.manifests.get_all_teams()
        report = []
        for team in teams:
            for c in list(team.components.code) + list(team.components.design):
                if not getattr(c, "deprecated", False):
                    continue
                # Teams whose declared dependency on this owner covers this
                # component (named explicitly, or an unscoped whole-team dep).
                exposed = [
                    other.team for other in teams
                    for dep in other.dependencies
                    if dep.team == team.team and (not dep.components or c.name in dep.components)
                ]
                report.append({
                    "component": c.name,
                    "owner_team": team.team,
                    "sunset_date": c.sunset_date,
                    "replacement": c.replacement,
                    "dependent_teams": sorted(set(exposed)),
                })
        report.sort(key=lambda r: r["sunset_date"] or date.max)
        return report

    def _detect_design_drift(self) -> list[DriftIssue]:
        return self.p.figma.get_drift_issues()

    def _detect_code_drift(self) -> list[DriftIssue]:
        issues = []
        shared = {}
        for team in self.p.manifests.get_all_teams():
            for c in team.components.code:
                shared.setdefault(c.name.lower(), []).append((team.team, c))

        for name, owners in shared.items():
            if len(owners) > 1:
                teams = [o[0] for o in owners]
                issues.append(DriftIssue(
                    id=f"code-drift-{name}",
                    type="code_drift",
                    severity=DriftSeverity.high,
                    title=f"Multiple teams own '{name}'",
                    description=f"Component '{name}' is claimed by {len(teams)} teams: {', '.join(teams)}. "
                                f"This risks diverging implementations.",
                    teams_involved=teams,
                    components_involved=[name],
                    detected_at=datetime.now(timezone.utc),
                    suggested_action="Designate a single owning team and have others consume it as a dependency.",
                ))
        return issues

    def _detect_missing_decision_logs(self) -> list[DriftIssue]:
        issues = []
        all_pages = self.p.confluence.get_pages()

        for pr in self.p.github.get_pull_requests(status="open"):
            if pr.cross_team_impact and not any(
                t.lower() in " ".join(p.content_summary.lower() for p in all_pages)
                for t in pr.components_touched
            ):
                issues.append(DriftIssue(
                    id=f"no-decision-{pr.id}",
                    type="missing_decision_log",
                    severity=DriftSeverity.medium,
                    title=f"No decision log for cross-team PR: {pr.title}",
                    description=f"PR {pr.id} from {pr.team} impacts {', '.join(pr.cross_team_impact)} "
                                f"but no decision log exists for this change.",
                    teams_involved=[pr.team] + pr.cross_team_impact,
                    components_involved=pr.components_touched,
                    detected_at=datetime.now(timezone.utc),
                    suggested_action="Create a decision log in Confluence documenting why this cross-team change was made.",
                ))

        # Flag tickets with breaking-change label and no linked decision log
        for ticket in self.p.jira.get_tickets():
            if "breaking-change" in ticket.labels:
                pages = self.p.confluence.search_pages(ticket.id)
                decision_pages_for_ticket = [p for p in pages if p.decision_log]
                if not decision_pages_for_ticket:
                    issues.append(DriftIssue(
                        id=f"no-decision-ticket-{ticket.id}",
                        type="missing_decision_log",
                        severity=DriftSeverity.high,
                        title=f"Breaking change without decision log: {ticket.id}",
                        description=f"'{ticket.title}' is marked as a breaking change but has no formal decision log in Confluence.",
                        teams_involved=[ticket.team],
                        components_involved=ticket.components,
                        detected_at=datetime.now(timezone.utc),
                        suggested_action="Write a decision log in Confluence before this ships.",
                    ))
        return issues

    def _detect_cross_team_pr_impact(self) -> list[DriftIssue]:
        issues = []
        recent_prs = self.p.github.get_recent_prs(days=7)
        for pr in recent_prs:
            if pr.cross_team_impact:
                issues.append(DriftIssue(
                    id=f"pr-impact-{pr.id}",
                    type="cross_team_pr",
                    severity=DriftSeverity.medium,
                    title=f"Merged PR may affect dependent teams: {pr.title}",
                    description=f"{pr.team} merged '{pr.title}' which touches components used by: "
                                f"{', '.join(pr.cross_team_impact)}. Verify dependent teams are aware.",
                    teams_involved=[pr.team] + pr.cross_team_impact,
                    components_involved=pr.components_touched,
                    detected_at=datetime.now(timezone.utc),
                    suggested_action=f"Notify {', '.join(pr.cross_team_impact)} and confirm no action needed.",
                ))
        return issues

    def _predict_planned_work_conflicts(self) -> list[ConflictPrediction]:
        conflicts = []
        all_tickets = self.p.jira.get_tickets()
        component_tickets: dict[str, list] = {}

        for ticket in all_tickets:
            for comp in ticket.components:
                component_tickets.setdefault(comp, []).append(ticket)

        for comp, tickets in component_tickets.items():
            teams_planning_work = list({t.team for t in tickets if t.status.value in ("todo", "in_progress")})
            if len(teams_planning_work) > 1:
                ticket_ids = [t.id for t in tickets if t.team in teams_planning_work]
                conflicts.append(ConflictPrediction(
                    id=f"conflict-{comp}",
                    title=f"Multiple teams planning changes to '{comp}'",
                    description=f"{', '.join(teams_planning_work)} all have active tickets touching '{comp}'. "
                                f"Risk of conflicting changes.",
                    teams_involved=teams_planning_work,
                    tickets_involved=ticket_ids,
                    components_at_risk=[comp],
                    severity=DriftSeverity.high,
                    suggested_action="Schedule a cross-team sync to coordinate changes to this component.",
                ))
        return conflicts
