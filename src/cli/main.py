import json
import sys
from pathlib import Path
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint

app = typer.Typer(help="SyncBot CLI — validate team manifests and explore the dependency graph.")
console = Console()


def _get_providers(config: str = "config.yaml"):
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from src.providers.factory import Providers
    return Providers(config)


@app.command()
def validate(
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
):
    """Validate all team manifests and report issues."""
    providers = _get_providers(config)
    teams = providers.manifests.get_all_teams()

    if not teams:
        console.print("[red]No team manifests found.[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold]Found {len(teams)} team manifests[/bold]\n")

    all_valid = True
    for team in teams:
        issues = []
        if not team.slack_channel:
            issues.append("Missing slack_channel")
        if not team.jira_project:
            issues.append("Missing jira_project")
        if not team.components.code and not team.components.design:
            issues.append("No components defined")
        if not team.quarter_goals:
            issues.append("No quarter_goals defined")

        if issues:
            all_valid = False
            console.print(f"[yellow]⚠  {team.team}[/yellow]")
            for issue in issues:
                console.print(f"   [dim]→ {issue}[/dim]")
        else:
            console.print(f"[green]✓  {team.team}[/green]")

    if all_valid:
        console.print("\n[green bold]All manifests valid.[/green bold]")
    else:
        console.print("\n[yellow]Some manifests have issues — review above.[/yellow]")


@app.command()
def graph(
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
    output: str = typer.Option("table", help="Output format: table or json"),
):
    """Show the team dependency graph."""
    from src.core.dependency_graph import DependencyGraph
    providers = _get_providers(config)
    teams = providers.manifests.get_all_teams()

    dg = DependencyGraph()
    dg.build(teams)

    if output == "json":
        print(json.dumps(dg.to_dict(), indent=2))
        return

    table = Table(title="Team Dependency Graph", show_lines=True)
    table.add_column("Team", style="cyan bold")
    table.add_column("Depends On", style="yellow")
    table.add_column("Depended On By", style="green")

    for team in teams:
        deps = [d.team for d in team.dependencies]
        dependents = [t.team for t in dg.dependents_of(team.team)]
        table.add_row(
            team.team,
            "\n".join(deps) if deps else "—",
            "\n".join(dependents) if dependents else "—",
        )

    console.print(table)

    shared = dg.find_shared_components()
    if shared:
        console.print(f"\n[yellow bold]⚠  Shared components (potential drift):[/yellow bold]")
        for comp, owners in shared.items():
            console.print(f"   [yellow]{comp}[/yellow] owned by: {', '.join(owners)}")


@app.command()
def who_owns(
    component: str = typer.Argument(..., help="Component name to look up"),
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
):
    """Find which team owns a component."""
    providers = _get_providers(config)
    team = providers.manifests.find_component_owner(component)
    if team:
        console.print(Panel(
            f"[bold]{component}[/bold] is owned by [cyan]{team.team}[/cyan]\n"
            f"Owner: {team.owner.name} ({team.owner.slack_handle})\n"
            f"Slack: {team.slack_channel}",
            title="Component Owner"
        ))
    else:
        console.print(f"[red]No team claims ownership of '{component}'[/red]")


@app.command()
def when_ships(
    team: str = typer.Argument(..., help="Team name"),
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
):
    """Show upcoming deliverables for a team."""
    providers = _get_providers(config)
    tickets = providers.jira.get_upcoming_deliverables(team)

    if not tickets:
        console.print(f"[dim]No upcoming deliverables found for {team}.[/dim]")
        return

    table = Table(title=f"Upcoming Deliverables — {team}", show_lines=True)
    table.add_column("Ticket", style="cyan")
    table.add_column("Title")
    table.add_column("Status", style="yellow")
    table.add_column("Due", style="green")
    table.add_column("Priority", style="red")

    for t in sorted(tickets, key=lambda x: x.due_date or "9999-12-31"):
        table.add_row(t.id, t.title, t.status.value, str(t.due_date), t.priority.value)

    console.print(table)


@app.command()
def decisions(
    query: str = typer.Argument(..., help="Search term"),
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
):
    """Search decision logs."""
    providers = _get_providers(config)
    pages = providers.confluence.search_pages(query)
    decision_pages = [p for p in pages if p.decision_log or "decision" in p.tags]

    if not decision_pages:
        console.print(f"[dim]No decision logs found for '{query}'.[/dim]")
        return

    for page in decision_pages:
        dl = page.decision_log
        if dl:
            console.print(Panel(
                f"[bold]{dl.title}[/bold]\n"
                f"Decision: {dl.decision}\n"
                f"Why: {dl.rationale}\n"
                f"Decided by: {', '.join(dl.decided_by)}\n"
                f"Date: {dl.date}  Status: {dl.status}\n"
                f"URL: {page.url}",
                title=f"[cyan]{page.team}[/cyan] Decision Log"
            ))
        else:
            console.print(Panel(
                f"[bold]{page.title}[/bold]\n{page.content_summary}\n{page.url}",
                title=f"[yellow]{page.team}[/yellow] (no formal decision log)"
            ))


@app.command()
def scan(
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
):
    """Scan for drift, conflicts, and missing decision logs."""
    from src.agent.detector import DriftDetector
    providers = _get_providers(config)
    detector = DriftDetector(providers)
    issues = detector.run_all()

    if not issues:
        console.print("[green]No issues detected.[/green]")
        return

    console.print(f"\n[bold red]Found {len(issues)} issues:[/bold red]\n")
    for issue in issues:
        color = {"critical": "red", "high": "yellow", "medium": "cyan", "low": "dim"}.get(issue.severity.value, "white")
        console.print(Panel(
            f"{issue.description}\n\n[bold]Teams:[/bold] {', '.join(issue.teams_involved)}\n"
            f"[bold]Action:[/bold] {issue.suggested_action}",
            title=f"[{color}][{issue.severity.value.upper()}] {issue.title}[/{color}]"
        ))


def _teams_dir(config: str) -> str:
    import yaml
    with open(config) as f:
        cfg = yaml.safe_load(f)
    return cfg.get("data", {}).get("teams_dir", "./data/synthetic/teams")


def _slugify(team: str) -> str:
    """Team Phoenix -> team-phoenix"""
    import re
    return re.sub(r"[^a-z0-9]+", "-", team.lower()).strip("-")


def _detect_source(path: str) -> str:
    """Figure out what kind of export this is from the path itself."""
    import os
    if os.path.isfile(path) and path.lower().endswith(".csv"):
        return "jira"
    if os.path.isdir(path):
        if os.path.isdir(os.path.join(path, ".git")):
            return "github"
        for root, _, files in os.walk(path):
            if any(f.lower().endswith((".md", ".markdown", ".html", ".htm")) for f in files):
                return "confluence"
    return "unknown"


def _do_import(source: str, path: str, team: str, config: str):
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from src.importers.writer import write_team_json
    slug = _slugify(team)
    teams_dir = _teams_dir(config)

    if source == "jira":
        from src.importers.jira_csv import import_jira_csv
        tickets = import_jira_csv(path, team)
        out = write_team_json(tickets, teams_dir, slug, "jira_tickets.json")
        console.print(f"[green]✓[/green] {len(tickets)} Jira tickets → {out}")
    elif source == "confluence":
        from src.importers.confluence_export import import_confluence_export
        pages = import_confluence_export(path, team)
        decisions = sum(1 for p in pages if p.decision_log)
        out = write_team_json(pages, teams_dir, slug, "confluence_pages.json")
        console.print(f"[green]✓[/green] {len(pages)} Confluence pages ({decisions} decision logs) → {out}")
    elif source == "github":
        from src.importers.github_clone import import_github_clone
        manifest = _get_providers(config).manifests.get_team(team)
        component_paths = {c.name: c.path for c in manifest.components.code} if manifest else {}
        prs = import_github_clone(path, team, component_paths)
        out = write_team_json(prs, teams_dir, slug, "pull_requests.json")
        console.print(f"[green]✓[/green] {len(prs)} merged PRs → {out}")
    else:
        console.print(f"[red]Couldn't tell what kind of export '{path}' is.[/red]")
        console.print("[dim]Expected: a .csv (Jira), a folder of .md/.html (Confluence), or a git clone (GitHub).[/dim]")


@app.command("import", help="Import an export. Auto-detects Jira CSV / Confluence folder / GitHub clone.")
def import_cmd(
    path: str = typer.Argument(None, help="Path to the export (CSV, folder, or git clone). Omit for a guided wizard."),
    team: str = typer.Option(None, "--team", "-t", help="Team name (slug is derived automatically)."),
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
):
    """
    Smart import — you don't pick the type or a slug; it figures both out.

      syncbot import export.csv --team "Team Phoenix"     # one-liner
      syncbot import                                       # guided wizard
    """
    # Wizard mode when essentials are missing
    if not path:
        console.print("[bold]SyncBot import wizard[/bold]  [dim](drop in a Jira CSV, a Confluence export folder, or a git clone)[/dim]\n")
        path = typer.prompt("Path to your export")
    source = _detect_source(path)
    if source == "unknown":
        console.print(f"[red]Couldn't recognize '{path}'.[/red] Expected a .csv, a folder of docs, or a git clone.")
        raise typer.Exit(1)

    pretty = {"jira": "Jira tickets (CSV)", "confluence": "Confluence pages", "github": "GitHub merge history"}[source]
    console.print(f"Detected: [cyan]{pretty}[/cyan]")

    if not team:
        team = typer.prompt("Which team is this for? (e.g. Team Phoenix)")

    console.print(f"Team: [cyan]{team}[/cyan]  →  folder [dim]{_slugify(team)}[/dim]\n")
    _do_import(source, path, team, config)


if __name__ == "__main__":
    app()
