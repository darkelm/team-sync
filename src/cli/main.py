import json
import os
import sys
from pathlib import Path
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

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
    from datetime import date, timedelta
    stale_after = date.today() - timedelta(days=30)

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
        if team.last_verified is None:
            issues.append("Never verified — run `syncbot refresh-manifest`")
        elif team.last_verified < stale_after:
            issues.append(f"Stale — last verified {team.last_verified} (>30 days ago)")

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
        console.print("\n[yellow bold]⚠  Shared components (potential drift):[/yellow bold]")
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


@app.command("export-skill", help="Generate a Claude Skill knowledge pack from a team's manifest.")
def export_skill_cmd(
    team: str = typer.Argument(..., help="Team name (e.g. 'Team Phoenix')."),
    out: str = typer.Option("./skills", "--out", "-o", help="Directory to write the <slug>-context/ package into."),
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
):
    """
    Turn a team's manifest (the single source of truth) into a Claude Skill so
    any Claude session "knows" that team's design/coordination context.

      syncbot export-skill "Team Phoenix" -o ./skills
    """
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from src.skill_export import export_skill

    providers = _get_providers(config)
    manifest = providers.manifests.get_team(team)
    if not manifest:
        console.print(f"[red]No manifest found for '{team}'.[/red] Run `syncbot validate` to list teams.")
        raise typer.Exit(1)

    dependents = providers.manifests.get_dependents(manifest.team)
    figma_components = providers.figma.get_components(manifest.team)
    pkg = export_skill(
        manifest,
        out,
        dependents=dependents,
        confluence=providers.confluence,
        figma_components=figma_components,
    )

    decisions_line = (
        "\n  • references/decisions.md"
        if (pkg / "references" / "decisions.md").exists()
        else ""
    )
    console.print(Panel(
        f"[green]✓[/green] Skill pack for [cyan]{manifest.team}[/cyan] written to [bold]{pkg}[/bold]\n"
        f"  • SKILL.md  (frontmatter + body)\n"
        f"  • references/components.md\n"
        f"  • references/ownership.md\n"
        f"  • references/dependencies.md"
        f"{decisions_line}",
        title="export-skill"
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


@app.command("import", help="Import an export. Auto-detects Jira CSV / Confluence folder / GitHub clone / transcript.")
def import_cmd(
    path: str = typer.Argument(None, help="Path to the export (CSV, folder, git clone, or transcript). Omit for a guided wizard."),
    team: str = typer.Option(None, "--team", "-t", help="Team name (slug is derived automatically)."),
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
):
    """
    Smart import — delegates to the channel-neutral ingest core (same code path
    the Slack upload and MCP import use).

      syncbot import export.csv --team "Team Phoenix"     # one-liner
      syncbot import                                       # guided wizard
    """
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from src.ingest import detect_source, ingest_path, slugify

    if not path:
        console.print("[bold]SyncBot import wizard[/bold]  [dim](drop in a Jira CSV, a Confluence export folder, a git clone, or a transcript)[/dim]\n")
        path = typer.prompt("Path to your export")
    source = detect_source(path)
    if source == "unknown":
        console.print(f"[red]Couldn't recognize '{path}'.[/red] Expected a .csv, a folder of docs, a git clone, or a transcript.")
        raise typer.Exit(1)
    pretty = {"jira": "Jira tickets (CSV)", "confluence": "Confluence pages",
              "github": "GitHub merge history", "transcript": "Meeting transcript"}[source]
    console.print(f"Detected: [cyan]{pretty}[/cyan]")
    if not team:
        team = typer.prompt("Which team is this for? (e.g. Team Phoenix)")
    console.print(f"Team: [cyan]{team}[/cyan]  →  folder [dim]{slugify(team)}[/dim]\n")
    console.print(ingest_path(path, team, config))


@app.command("build-manifest", help="Draft a team.yaml from whatever sources you have (repo, CODEOWNERS, roster CSV, Jira CSV, transcript).")
def build_manifest(
    sources: list[str] = typer.Argument(None, help="Any mix of: a repo path, CODEOWNERS, a roster/Jira CSV, a transcript."),
    team: str = typer.Option(None, "--team", "-t", help="Team name."),
    out: str = typer.Option(None, "--out", "-o", help="Write the draft here (default: print)."),
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
):
    """
    Multi-source manifest builder. Examples:

      syncbot build-manifest ../their-repo roster.csv --team "Payments"
      syncbot build-manifest ../repo design-review.txt --team "Payments" -o data/imported/teams/payments/team.yaml
      syncbot build-manifest            # guided wizard
    """
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from src.builder.builder import ManifestBuilder

    if not sources:
        console.print("[bold]Manifest builder[/bold] [dim](point me at any sources you have)[/dim]\n")
        raw = typer.prompt("Source paths (space-separated: repo, CODEOWNERS, roster.csv, transcript…)")
        sources = raw.split()
    if not team:
        team = typer.prompt("Team name")

    # known teams help the transcript adapter spot dependencies
    known = []
    try:
        known = [t.team for t in _get_providers(config).manifests.get_all_teams()]
    except Exception as e:
        # Non-fatal: builder still runs without known teams, but dependency
        # detection in the transcript adapter is weaker — make the gap visible.
        console.print(f"[dim][cli] couldn't load known teams (dependency hints disabled): {e}[/dim]")

    builder = ManifestBuilder(team, known_teams=known)
    for s in sources:
        builder.add_source(s)
    result = builder.build()

    console.print(f"\n[green]Drafted manifest for[/green] [cyan]{team}[/cyan] "
                  f"[dim]using: {', '.join(result.sources_used) or 'no recognized sources'}[/dim]")
    if result.conflicts:
        console.print(f"[yellow]⚠ Conflicts to resolve: {', '.join(result.conflicts)}[/yellow]")
    if result.gaps:
        console.print(f"[yellow]Gaps to fill (no source found): {', '.join(result.gaps)}[/yellow]")

    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w") as f:
            f.write(result.yaml_text)
        console.print(f"\n[green]✓[/green] Draft written to {out}")
        console.print("[dim]Review the comments, confirm inferred fields, fill TODOs, then `syncbot validate`.[/dim]")
    else:
        console.print("")
        print(result.yaml_text)


@app.command("refresh-manifest", help="Diff a team's manifest against fresh source scans and propose updates.")
def refresh_manifest(
    sources: list[str] = typer.Argument(..., help="Same sources you'd build from (repo, CSV, transcript…)."),
    team: str = typer.Option(..., "--team", "-t", help="Team name (must already have a manifest)."),
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
):
    """Show what's changed in reality since the manifest was written."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from src.builder.refresher import ManifestRefresher

    providers = _get_providers(config)
    manifest = providers.manifests.get_team(team)
    if not manifest:
        console.print(f"[red]No existing manifest for '{team}'. Use `build-manifest` first.[/red]")
        raise typer.Exit(1)

    current = json.loads(manifest.model_dump_json())
    known = [t.team for t in providers.manifests.get_all_teams()]
    diff = ManifestRefresher(manifest.team, known_teams=known).diff(current, list(sources))

    console.print(f"\n[bold]Manifest refresh — {manifest.team}[/bold] "
                  f"[dim](sources: {', '.join(diff.sources_used) or 'none'})[/dim]\n")
    if not diff.has_changes:
        console.print("[green]✓ Manifest still matches reality. Nothing to update.[/green]")
        return

    if diff.owner_change:
        old, new, note = diff.owner_change
        console.print(f"[yellow]Owner change:[/yellow] {old} → [bold]{new}[/bold]  [dim]{note}[/dim]")
    if diff.components_added:
        console.print("\n[green]Components to add:[/green]")
        for name, note in diff.components_added:
            console.print(f"  + {name}  [dim]{note}[/dim]")
    if diff.components_removed:
        console.print("\n[red]Components in manifest but not found in sources (verify if removed):[/red]")
        for name in diff.components_removed:
            console.print(f"  - {name}")
    if diff.members_added:
        console.print("\n[green]People to add:[/green]")
        for name, note in diff.members_added:
            console.print(f"  + {name}  [dim]{note}[/dim]")
    if diff.dependencies_added:
        console.print("\n[green]Possible new dependencies:[/green]")
        for tm, note in diff.dependencies_added:
            console.print(f"  + {tm}  [dim]{note}[/dim]")
    console.print("\n[dim]Review and apply the changes you confirm, then bump last_verified.[/dim]")


@app.command("simulate-event", help="Fire a trigger event and see who'd be notified (proactive engine).")
def simulate_event(
    event_type: str = typer.Argument(..., help="e.g. design.library_published, research.study_added, roadmap.date_changed, work.created"),
    subject: str = typer.Option("", "--subject", "-s", help="What changed (component, study topic, ticket title…)"),
    team: str = typer.Option("", "--team", "-t", help="Originating team, if known"),
    send: bool = typer.Option(False, "--send", help="Actually post to Slack (else just preview)"),
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
):
    """Source-agnostic: any signal can wake the system. This previews/dispatches one."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from src.agent.events import EventRouter, Event, TRIGGER_CATALOG
    if event_type not in TRIGGER_CATALOG:
        console.print("[yellow]Unknown event type. Known triggers:[/yellow]")
        for k, v in TRIGGER_CATALOG.items():
            console.print(f"  • [cyan]{k}[/cyan] — {v}")
    router = EventRouter(_get_providers(config))
    ev = Event(type=event_type, subject=subject, team=team, source="cli")
    if send:
        n = router.dispatch(ev)
        console.print(f"[green]✓[/green] Dispatched — {n} notification(s) posted.")
    else:
        console.print(router.explain(ev))


@app.command("onboard", help="Set up a new initiative from ANY source — RFP, doc, transcript, or Figma content.")
def onboard(
    source: str = typer.Argument(None, help="Path to a file (RFP, brief, transcript, export) or '-' to read from stdin."),
    out: str = typer.Option("data/imported", "--out", "-o", help="Directory to write setup files into."),
    config: str = typer.Option("config.yaml", help="Path to config.yaml"),
):
    """
    Universal initiative onboarding — any format works.

      syncbot onboard brief.pdf.txt --out data/google-initiative
      syncbot onboard transcript.vtt --out data/my-project
      cat rfp.txt | syncbot onboard - --out data/client-x
      syncbot onboard            # interactive guided flow (asks you questions)
    """
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from src.onboarding.extractor import extract
    from src.onboarding.generator import generate

    if source == "-":
        text = _sys.stdin.read()
    elif source and os.path.isfile(source):
        with open(source, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    elif source:
        # treat the argument as literal text (e.g. a URL text passed by wrapper)
        text = source
    else:
        # interactive mode — ask questions in the terminal
        console.print("[bold]SyncBot initiative onboarding[/bold]  [dim](any format — paste an RFP, brief, or just describe the work)[/dim]\n")
        lines = []
        console.print("Paste your initiative brief (or describe it). Press Enter twice when done:\n")
        while True:
            try:
                line = input()
            except EOFError:
                # Intentional: EOF (Ctrl-D / piped input ending) is the normal
                # way to finish interactive paste — not an error to report.
                break
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        text = "\n".join(lines)

    if not text.strip():
        console.print("[red]No content provided.[/red]")
        raise typer.Exit(1)

    console.print("[dim]Extracting initiative structure…[/dim]")
    brief = extract(text)

    console.print("\n[bold]Extracted:[/bold]")
    console.print(f"  Initiative: [cyan]{brief.title or brief.client or '(unnamed)'}[/cyan]")
    if brief.teams:
        console.print(f"  Teams: {', '.join(t.name for t in brief.teams)}")
    if brief.journeys:
        console.print(f"  Journeys: {', '.join(j.name for j in brief.journeys)}")
    if brief.principles:
        console.print(f"  Principles: {', '.join(p.name for p in brief.principles)}")
    if brief.open_decisions:
        console.print(f"  Open decisions: {len(brief.open_decisions)}")

    console.print("")
    confirmed = typer.confirm("Generate setup files?", default=True)
    if not confirmed:
        console.print("[dim]Cancelled — nothing written.[/dim]")
        raise typer.Exit(0)

    written = generate(brief, out)
    console.print(f"\n[green]✓[/green] {len(written)} files written to [cyan]{out}[/cyan]")
    for path in written:
        console.print(f"  • {path}")
    console.print("\n[dim]Fields marked TODO need your input. Then `syncbot validate` to confirm.[/dim]")


if __name__ == "__main__":
    app()
