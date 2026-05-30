"""Cross-team meeting briefing — synthesizes everything relevant to a sync between teams."""
from ..providers.factory import Providers
from .detector import DriftDetector


class BriefingGenerator:
    def __init__(self, providers: Providers):
        self.p = providers
        self.detector = DriftDetector(providers)

    def cross_team_briefing(self, team_names: list[str]) -> str:
        teams = [t for t in (self.p.manifests.get_team(n) for n in team_names) if t]
        if len(teams) < 2:
            found = [t.team for t in teams]
            return (
                f"Need at least two valid teams for a cross-team briefing. "
                f"Recognized: {', '.join(found) if found else 'none'}."
            )

        team_set = {t.team for t in teams}
        lines = ["*🤝 Cross-Team Sync Briefing*", f"_{' × '.join(t.team for t in teams)}_", ""]

        # 1. Direct dependencies between the teams in the room
        inter_deps = []
        for t in teams:
            for dep in t.dependencies:
                if dep.team in team_set:
                    inter_deps.append(f"• *{t.team}* → *{dep.team}*: {dep.reason}"
                                      + (f" ({', '.join(dep.components)})" if dep.components else ""))
        lines.append("*Dependencies between you*")
        lines.extend(inter_deps if inter_deps else ["• No direct dependencies recorded."])
        lines.append("")

        # 2. Components more than one of these teams touches (via tickets)
        comp_to_teams: dict[str, set] = {}
        comp_to_tickets: dict[str, list] = {}
        for t in teams:
            for ticket in self.p.jira.get_tickets(t.team):
                if ticket.status.value == "done":
                    continue
                for comp in ticket.components:
                    comp_to_teams.setdefault(comp, set()).add(t.team)
                    comp_to_tickets.setdefault(comp, []).append(ticket)
        overlapping = {c: ts for c, ts in comp_to_teams.items() if len(ts) > 1}
        lines.append("*Components you're both working on*")
        if overlapping:
            for comp, ts in overlapping.items():
                ticket_ids = ", ".join(f"`{tk.id}`" for tk in comp_to_tickets[comp])
                lines.append(f"• *{comp}* — {', '.join(sorted(ts))} ({ticket_ids})")
        else:
            lines.append("• No overlapping components in active tickets.")
        lines.append("")

        # 3. Open cross-team tickets that link these teams' work
        linked = []
        all_team_tickets = {t.team: self.p.jira.get_tickets(t.team) for t in teams}
        seen = set()
        for t in teams:
            for ticket in all_team_tickets[t.team]:
                if "cross-team" in ticket.labels and ticket.status.value != "done" and ticket.id not in seen:
                    seen.add(ticket.id)
                    linked.append(f"• `{ticket.id}` {ticket.title} [{ticket.status.value}, {ticket.priority.value}]")
        lines.append("*Open cross-team tickets*")
        lines.extend(linked if linked else ["• None flagged."])
        lines.append("")

        # 4. Predicted conflicts involving these teams
        predictions = [c for c in self.detector.predict_conflicts() if team_set & set(c.teams_involved)]
        lines.append("*⚠️ Predicted conflicts*")
        if predictions:
            for c in predictions:
                lines.append(f"• {c.title} — {', '.join(c.teams_involved)}\n    → {c.suggested_action}")
        else:
            lines.append("• None predicted.")
        lines.append("")

        # 5. Recent cross-impacting PRs
        recent = [p for p in self.p.github.get_recent_prs(days=14)
                  if p.team in team_set or (team_set & set(p.cross_team_impact))]
        lines.append("*Recent PRs affecting the group (14d)*")
        if recent:
            for p in recent:
                impact = f" → affects {', '.join(p.cross_team_impact)}" if p.cross_team_impact else ""
                lines.append(f"• [{p.team}] {p.title}{impact}")
        else:
            lines.append("• None.")
        lines.append("")

        # 6. Suggested agenda
        lines.append("*Suggested agenda*")
        agenda = []
        if predictions:
            agenda.append("Resolve predicted conflicts above before they become blockers")
        if overlapping:
            agenda.append("Align on ownership of shared components")
        if linked:
            agenda.append("Status check on open cross-team tickets")
        if not agenda:
            agenda.append("No urgent coordination items — quick sync should suffice")
        lines.extend([f"{i}. {item}" for i, item in enumerate(agenda, 1)])

        return "\n".join(lines)
