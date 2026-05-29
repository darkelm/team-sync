"""Weekly digest generator — produces per-team summaries for Slack."""
from datetime import date, datetime, timezone, timedelta
from ..core.schemas import TeamDigest
from ..providers.factory import Providers
from .detector import DriftDetector


class DigestGenerator:
    def __init__(self, providers: Providers):
        self.p = providers
        self.detector = DriftDetector(providers)

    def generate_for_team(self, team_name: str) -> TeamDigest:
        team = self.p.manifests.get_team(team_name)
        if not team:
            raise ValueError(f"Team '{team_name}' not found")

        week_of = date.today()
        recent_prs = self.p.github.get_recent_prs(days=7)
        all_issues = self.detector.run_all()
        team_issues = [i for i in all_issues if team_name in i.teams_involved]
        predictions = [c for c in self.detector.predict_conflicts() if team_name in c.teams_involved]

        # Dev updates: PRs from dependencies that might affect this team
        dep_teams = [d.team for d in team.dependencies]
        dep_prs = [p for p in recent_prs if p.team in dep_teams]
        dev_updates = [f"[{p.team}] Merged: {p.title}" for p in dep_prs]

        # Design updates: Figma drift issues affecting this team
        design_issues = [i for i in team_issues if i.type == "design_drift"]
        design_updates = [f"Design drift detected: {i.title}" for i in design_issues]

        # Check for design system updates from Nova
        nova_prs = [p for p in recent_prs if p.team == "Team Nova"]
        for pr in nova_prs:
            design_updates.append(f"[Design System] Nova merged: {pr.title} — review your Figma files")

        dep_changes = []
        for dep_pr in dep_prs:
            if dep_pr.cross_team_impact and team_name in dep_pr.cross_team_impact:
                dep_changes.append(f"⚠ {dep_pr.team} merged changes that affect your team: {dep_pr.title}")

        action_items = []
        for issue in team_issues:
            action_items.append(f"[{issue.severity.value.upper()}] {issue.suggested_action}")
        for conflict in predictions:
            action_items.append(f"[PREDICTED] {conflict.suggested_action}")

        return TeamDigest(
            team=team_name,
            week_of=week_of,
            dev_updates=dev_updates,
            design_updates=design_updates,
            dependency_changes=dep_changes,
            open_conflicts=team_issues,
            predicted_conflicts=predictions,
            action_items=action_items,
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

        lines.append("")
        lines.append("_Ask @syncbot anything: `@syncbot who owns auth` | `@syncbot when does Team Atlas ship` | `@syncbot scan for conflicts`_")

        return "\n".join(lines)

    def post_all_digests(self) -> None:
        teams = self.p.manifests.get_all_teams()
        for team in teams:
            digest = self.generate_for_team(team.team)
            message = self.format_slack_message(digest)
            self.p.slack.post_digest(team.slack_channel, message)
