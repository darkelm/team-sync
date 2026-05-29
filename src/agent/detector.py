"""Drift and conflict detection — runs across all providers to find issues."""
from datetime import datetime, timezone
from ..core.schemas import DriftIssue, ConflictPrediction, DriftSeverity
from ..providers.factory import Providers


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
        decision_pages = {p.id for p in all_pages if p.decision_log}

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
                    suggested_action=f"Create a decision log in Confluence documenting why this cross-team change was made.",
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
