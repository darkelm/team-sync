"""Knowledge-pack generator: a team manifest → a Claude Skill package.

SyncBot already exposes the ACTION layer (MCP tools in `mcp_server.py`). This is
the KNOWLEDGE half of the one-source-to-both-layers idea: it turns the same
per-team manifest into a Claude **Skill** so any Claude session "knows" that
team's design / coordination context.

The manifest is the single source of truth; this module reshapes it (no new
facts) into the standard Skill package structure:

    <team-slug>-context/
      SKILL.md            # YAML frontmatter (name, trigger-keyword description) + body
      references/
        components.md     # code + design components, with descriptions
        ownership.md      # owner, channel, members
        dependencies.md   # what this team depends on / who depends on it

SKILL.md stays tight (progressive disclosure) and points to references/ for the
detail. `export_skill(team_manifest, out_dir) -> Path` writes the package and
returns the package directory.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .core.schemas import TeamManifest


def slugify(team: str) -> str:
    """'Team Phoenix' -> 'team-phoenix'. Mirrors src.ingest.slugify."""
    return re.sub(r"[^a-z0-9]+", "-", team.lower()).strip("-")


def _esc(text: str) -> str:
    """Make a value safe for a single-line double-quoted YAML scalar."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def _build_description(manifest: TeamManifest, skill_name: str) -> str:
    """A one-liner naming the team + trigger keywords, kept usable (~200 chars).

    Trigger keywords required by the brief: the team name, "ownership",
    "components", "dependencies", "design system".
    """
    team = manifest.team
    # A short identity phrase from the manifest description (first clause).
    blurb = manifest.description.split(".")[0].strip()
    desc = (
        f"{team} context: ownership, components, dependencies, and design system. "
        f"Use when working on {team} or {blurb}."
    )
    if len(desc) > 220:
        # Fall back to the keyword-only form if the blurb pushes it too long.
        desc = (
            f"{team} context: ownership, components, dependencies, and design "
            f"system for {team}."
        )
    return desc


# ── reference files ──────────────────────────────────────────────────────────

def _components_md(manifest: TeamManifest) -> str:
    c = manifest.components
    lines = [f"# {manifest.team} — Components", ""]

    lines.append("## Code components")
    if c.code:
        for comp in c.code:
            lines.append(f"- **{comp.name}** (`{comp.path}`) — {comp.description}")
    else:
        lines.append("_None recorded in the manifest._")
    lines.append("")

    lines.append("## Design components")
    if c.design:
        for comp in c.design:
            node = f" [node {comp.figma_node_id}]" if comp.figma_node_id else ""
            lines.append(f"- **{comp.name}**{node} — {comp.description}")
    else:
        lines.append("_None recorded in the manifest._")
    lines.append("")
    return "\n".join(lines)


def _ownership_md(manifest: TeamManifest) -> str:
    o = manifest.owner
    lines = [
        f"# {manifest.team} — Ownership",
        "",
        f"- **Owner:** {o.name} — {o.role} ({o.slack_handle}, {o.email})",
        f"- **Slack channel:** {manifest.slack_channel}",
        f"- **Jira project:** {manifest.jira_project}",
        f"- **Confluence space:** {manifest.confluence_space}",
        "",
        "## Members",
    ]
    if manifest.members:
        for m in manifest.members:
            lines.append(f"- **{m.name}** — {m.role} ({m.slack_handle}, {m.email})")
    else:
        lines.append("_No additional members recorded._")
    lines.append("")
    return "\n".join(lines)


def _dependencies_md(manifest: TeamManifest, dependents: list[TeamManifest]) -> str:
    lines = [f"# {manifest.team} — Dependencies", ""]

    lines.append(f"## {manifest.team} depends on")
    if manifest.dependencies:
        for d in manifest.dependencies:
            comps = f" — components: {', '.join(d.components)}" if d.components else ""
            lines.append(f"- **{d.team}** — {d.reason}{comps}")
    else:
        lines.append("_No declared dependencies._")
    lines.append("")

    lines.append(f"## Teams that depend on {manifest.team}")
    if dependents:
        for dep in dependents:
            why = [
                d for d in dep.dependencies
                if d.team.lower() == manifest.team.lower()
            ]
            reason = f" — {why[0].reason}" if why else ""
            lines.append(f"- **{dep.team}**{reason}")
    else:
        lines.append("_No teams declare a dependency on this team (per current manifests)._")
    lines.append("")
    return "\n".join(lines)


# ── SKILL.md ─────────────────────────────────────────────────────────────────

def _skill_md(manifest: TeamManifest, skill_name: str, dependents: list[TeamManifest]) -> str:
    description = _build_description(manifest, skill_name)

    front = [
        "---",
        f"name: {skill_name}",
        f'description: "{_esc(description)}"',
        "---",
        "",
    ]

    body: list[str] = []
    body.append(f"# {manifest.team} — context pack")
    body.append("")
    body.append(manifest.description)
    body.append("")

    # Ownership + channel (compact; detail in references/ownership.md)
    o = manifest.owner
    body.append("## Ownership")
    body.append(f"- **Owner:** {o.name} ({o.slack_handle})")
    body.append(f"- **Slack:** {manifest.slack_channel}")
    body.append(f"- **Jira:** {manifest.jira_project} · **Confluence:** {manifest.confluence_space}")
    body.append("")

    # Compact components overview
    code, design = manifest.components.code, manifest.components.design
    body.append("## Components (overview)")
    if code:
        names = ", ".join(c.name for c in code)
        body.append(f"- **Code:** {names}")
    if design:
        names = ", ".join(c.name for c in design)
        body.append(f"- **Design:** {names}")
    if not code and not design:
        body.append("- _No components recorded._")
    body.append("- See `references/components.md` for paths, Figma nodes, and descriptions.")
    body.append("")

    # Dependencies (compact)
    body.append("## Dependencies")
    if manifest.dependencies:
        body.append("- **Depends on:** " + ", ".join(d.team for d in manifest.dependencies))
    if dependents:
        body.append("- **Depended on by:** " + ", ".join(d.team for d in dependents))
    if not manifest.dependencies and not dependents:
        body.append("- _No declared dependencies in either direction._")
    body.append("- See `references/dependencies.md` for reasons and shared components.")
    body.append("")

    # Quarter goals
    if manifest.quarter_goals:
        body.append("## Quarter goals")
        for g in manifest.quarter_goals:
            body.append(f"- {g}")
        body.append("")

    # Design system + Figma refs
    if manifest.design_system_library or manifest.figma_files:
        body.append("## Design system & Figma")
        if manifest.design_system_library:
            body.append(f"- **Design system library:** {manifest.design_system_library}")
        for f in manifest.figma_files:
            updated = f" (updated {f.last_updated})" if f.last_updated else ""
            body.append(f"- **{f.name}:** {f.url}{updated}")
        body.append("")

    # Resources
    if manifest.resources:
        body.append("## Resources")
        for r in manifest.resources:
            note = f" — {r.description}" if r.description else ""
            body.append(f"- **{r.name}** ({r.type}): {r.url}{note}")
        body.append("")

    body.append("## More detail")
    body.append("- `references/ownership.md` — owner, channel, full member roster")
    body.append("- `references/components.md` — every code + design component")
    body.append("- `references/dependencies.md` — inbound + outbound dependencies")
    if manifest.last_verified:
        body.append("")
        body.append(f"_Manifest last verified {manifest.last_verified}._")
    body.append("")

    return "\n".join(front) + "\n".join(body)


def export_skill(
    team_manifest: TeamManifest,
    out_dir,
    dependents: Optional[list[TeamManifest]] = None,
) -> Path:
    """Write a Claude Skill knowledge pack for a team manifest.

    Args:
        team_manifest: the single source of truth for this team.
        out_dir: directory the `<team-slug>-context/` package is written into.
        dependents: optional list of teams that depend on this one (so the
            dependencies reference can show the inbound edges). When omitted,
            only the manifest's own outbound dependencies are documented.

    Returns:
        Path to the package directory (`<out_dir>/<team-slug>-context`).
    """
    dependents = dependents or []
    slug = slugify(team_manifest.team)
    skill_name = f"{slug}-context"

    out_dir = Path(out_dir)
    pkg = out_dir / skill_name
    refs = pkg / "references"
    refs.mkdir(parents=True, exist_ok=True)

    (pkg / "SKILL.md").write_text(
        _skill_md(team_manifest, skill_name, dependents), encoding="utf-8"
    )
    (refs / "components.md").write_text(_components_md(team_manifest), encoding="utf-8")
    (refs / "ownership.md").write_text(_ownership_md(team_manifest), encoding="utf-8")
    (refs / "dependencies.md").write_text(
        _dependencies_md(team_manifest, dependents), encoding="utf-8"
    )

    return pkg
