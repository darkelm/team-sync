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
        components.md     # code + design components (per-component template)
        ownership.md      # owner, channel, members
        dependencies.md   # what this team depends on / who depends on it
        decisions.md      # decision record (only when decision logs exist)

This reaches toward the "context stack" pattern for design-system docs that
serve an AI agent: docs as an active context engine rather than a flat list.
Concretely we add what team-sync's data genuinely supports —

  * a **system-model** framing paragraph in SKILL.md (the team's purpose + how
    its components and dependencies fit together — reasoning, not just values);
  * an enriched, per-component `components.md` that follows the standard
    component template *only where real fields back each heading* (name, path,
    description, Figma node, deprecation lifecycle, library divergence);
  * a `references/decisions.md` **decision record** sourced from the team's
    decision logs (the "decisions captured as they happen" layer) — pulled via
    a ConfluenceProvider's `get_decision_logs(team)`.

We add no facts the manifest/providers don't carry: no token architecture, no
component anatomy. SKILL.md stays tight (progressive disclosure) and points to
references/ for the detail. `export_skill(team_manifest, out_dir) -> Path`
writes the package and returns the package directory.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Protocol

from .core.schemas import (
    DecisionLog,
    FigmaComponent,
    TeamManifest,
)


class _ConfluenceLike(Protocol):
    """The slice of ConfluenceProvider this module needs: fetch decision logs.

    Typed structurally so callers can pass the real provider (or a stub) without
    importing the provider package here.
    """

    def get_decision_logs(
        self, team: Optional[str] = None, component: Optional[str] = None
    ) -> list: ...


def slugify(team: str) -> str:
    """'Team Phoenix' -> 'team-phoenix'. Mirrors src.ingest.slugify."""
    return re.sub(r"[^a-z0-9]+", "-", team.lower()).strip("-")


def _esc(text: str) -> str:
    """Make a value safe for a single-line double-quoted YAML scalar."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def _collect_decisions(
    manifest: TeamManifest,
    confluence: Optional[_ConfluenceLike],
    decisions: Optional[list[DecisionLog]],
) -> list[DecisionLog]:
    """Gather this team's decision logs for the decision record.

    Precedence: an explicit `decisions` list wins (caller already has them);
    otherwise we pull from a ConfluenceProvider via `get_decision_logs(team)`,
    which returns ConfluencePages carrying a `.decision_log`. We unwrap those
    to the DecisionLog objects. Newest first; no synthesis, just the records.
    """
    if decisions is not None:
        logs = list(decisions)
    elif confluence is not None:
        logs = []
        for page in confluence.get_decision_logs(team=manifest.team):
            dl = getattr(page, "decision_log", None) or (
                page if isinstance(page, DecisionLog) else None
            )
            if dl is not None:
                logs.append(dl)
    else:
        return []
    # Most recent decisions first — the agent reads the current state of the
    # context stack from the top.
    return sorted(logs, key=lambda d: d.date, reverse=True)


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

def _deprecation_lines(comp) -> list[str]:
    """Render the deprecation lifecycle for a component, only if it is set.

    Code and design components share the same `deprecated`/`sunset_date`/
    `replacement` shape, so one helper covers both.
    """
    if not getattr(comp, "deprecated", False):
        return []
    lines = ["  - **Lifecycle:** DEPRECATED"]
    if comp.sunset_date:
        lines[0] += f" — sunset {comp.sunset_date.isoformat()}"
    if comp.replacement:
        lines.append(f"  - **Replacement:** migrate to `{comp.replacement}`")
    return lines


def _divergence_lines(comp, divergence: dict[str, FigmaComponent]) -> list[str]:
    """Note library divergence for a design component, if a Figma record carries it.

    The manifest's DesignComponent has no divergence field; that lives on
    FigmaComponent (`diverges_from_library`/`divergence_notes`). When the caller
    threads those through (keyed by component name), surface it — otherwise stay
    silent rather than invent it.
    """
    fc = divergence.get(comp.name)
    if fc is None or not getattr(fc, "diverges_from_library", False):
        return []
    note = f" — {fc.divergence_notes}" if getattr(fc, "divergence_notes", None) else ""
    return [f"  - **Diverges from library**{note}"]


def _components_md(
    manifest: TeamManifest,
    divergence: Optional[dict[str, FigmaComponent]] = None,
) -> str:
    """Per-component reference, reaching toward the standard component template.

    Each component gets its own heading with the fields the manifest actually
    carries — name, path/Figma node, description, and the deprecation lifecycle
    (deprecated / sunset_date / replacement) — plus library divergence when a
    Figma record supplies it. Template sections the data can't back (anatomy,
    props, states, tokens) are intentionally omitted rather than stubbed.
    """
    divergence = divergence or {}
    c = manifest.components
    lines = [
        f"# {manifest.team} — Components",
        "",
        "_One section per component. Only fields the manifest (and linked Figma "
        "records) actually carry are shown — no placeholder anatomy/props/token "
        "sections._",
        "",
    ]

    lines.append("## Code components")
    lines.append("")
    if c.code:
        for comp in c.code:
            lines.append(f"### {comp.name}")
            lines.append(f"- **Path:** `{comp.path}`")
            lines.append(f"- **Description:** {comp.description}")
            lines.extend(_deprecation_lines(comp))
            lines.append("")
    else:
        lines.append("_None recorded in the manifest._")
        lines.append("")

    lines.append("## Design components")
    lines.append("")
    if c.design:
        for comp in c.design:
            lines.append(f"### {comp.name}")
            if comp.figma_node_id:
                lines.append(f"- **Figma node:** `{comp.figma_node_id}`")
            lines.append(f"- **Description:** {comp.description}")
            lines.extend(_deprecation_lines(comp))
            lines.extend(_divergence_lines(comp, divergence))
            lines.append("")
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


def _decisions_md(manifest: TeamManifest, decisions: list[DecisionLog]) -> str:
    """The decision record — decisions logged as they happen, newest first.

    This is the context-stack's living "why" layer: not just what the team
    built but the rulings that constrain it (and what was rejected). Sourced
    from the team's decision logs; we render only the fields each log carries.
    """
    lines = [
        f"# {manifest.team} — Decision record",
        "",
        "_Decisions captured as they happen, most recent first. Each entry is a "
        "logged ruling — what was decided, why, what was rejected, and what it "
        "touches — so an agent reasons from the team's actual rulings, not "
        "guesses._",
        "",
    ]
    for d in decisions:
        status = f" · _{d.status}_" if d.status else ""
        lines.append(f"## {d.title}{status}")
        lines.append(f"- **Decision:** {d.decision}")
        lines.append(f"- **Rationale:** {d.rationale}")
        if d.alternatives_considered:
            lines.append(
                "- **Alternatives considered:** "
                + "; ".join(d.alternatives_considered)
            )
        if d.decided_by:
            lines.append("- **Decided by:** " + ", ".join(d.decided_by))
        lines.append(f"- **Date:** {d.date.isoformat()}")
        refs = []
        if d.related_components:
            refs.append("components: " + ", ".join(d.related_components))
        if d.related_tickets:
            refs.append("tickets: " + ", ".join(d.related_tickets))
        if refs:
            lines.append("- **Affects:** " + " · ".join(refs))
        lines.append(f"- **Log id:** `{d.id}`")
        lines.append("")
    return "\n".join(lines)


# ── SKILL.md ─────────────────────────────────────────────────────────────────

def _system_model(manifest: TeamManifest, dependents: list[TeamManifest]) -> str:
    """A short framing paragraph: the team's purpose + how its parts fit.

    The context-stack idea is that foundation docs should carry the system
    MODEL and reasoning, not just a flat list of values. We synthesize this only
    from manifest facts (purpose, what it builds, who it leans on / serves) so
    the agent gets the shape of the system before the detail.
    """
    # Use the lead clause of the description as the purpose phrase; many
    # manifests write "<Domain> — owns <X>", so split on the em-dash to avoid a
    # run-on with the "It owns ..." sentence below.
    purpose = re.split(r"\s[—-]\s", manifest.description, maxsplit=1)[0].strip().rstrip(".")
    sentences = [f"{manifest.team} owns the {purpose.lower()} part of the system."]

    code = manifest.components.code
    design = manifest.components.design
    if code or design:
        built = []
        if code:
            built.append("code (" + ", ".join(c.name for c in code) + ")")
        if design:
            built.append("design (" + ", ".join(c.name for c in design) + ")")
        sentences.append("It owns " + " and ".join(built) + ".")

    rel = []
    if manifest.dependencies:
        rel.append("leans on " + ", ".join(d.team for d in manifest.dependencies))
    if dependents:
        rel.append("is leaned on by " + ", ".join(d.team for d in dependents))
    if rel:
        sentences.append(
            "In the wider system it " + " and ".join(rel)
            + " — so changes here ripple along those edges."
        )

    deprecated = [c for c in code if getattr(c, "deprecated", False)] + [
        c for c in design if getattr(c, "deprecated", False)
    ]
    if deprecated:
        sentences.append(
            "Note: " + ", ".join(c.name for c in deprecated)
            + " is on a deprecation path — prefer its replacement (see components.md)."
        )

    return " ".join(sentences)


def _skill_md(
    manifest: TeamManifest,
    skill_name: str,
    dependents: list[TeamManifest],
    decisions: Optional[list[DecisionLog]] = None,
) -> str:
    decisions = decisions or []
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

    # System model — reasoning/shape of the system before the flat lists, so the
    # agent understands how this team's parts and dependencies fit together.
    body.append("## System model")
    body.append(_system_model(manifest, dependents))
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

    # Recent decisions (compact; the full record is references/decisions.md).
    if decisions:
        body.append("## Recent decisions")
        for d in decisions[:3]:
            body.append(f"- **{d.title}** ({d.date.isoformat()}) — {d.decision}")
        body.append("- See `references/decisions.md` for the full decision record.")
        body.append("")

    body.append("## More detail")
    body.append("- `references/ownership.md` — owner, channel, full member roster")
    body.append("- `references/components.md` — every code + design component")
    body.append("- `references/dependencies.md` — inbound + outbound dependencies")
    if decisions:
        body.append("- `references/decisions.md` — decision record (decided rulings + rationale)")
    if manifest.last_verified:
        body.append("")
        body.append(f"_Manifest last verified {manifest.last_verified}._")
    body.append("")

    return "\n".join(front) + "\n".join(body)


def export_skill(
    team_manifest: TeamManifest,
    out_dir,
    dependents: Optional[list[TeamManifest]] = None,
    confluence: Optional[_ConfluenceLike] = None,
    decisions: Optional[list[DecisionLog]] = None,
    figma_components: Optional[list[FigmaComponent]] = None,
) -> Path:
    """Write a Claude Skill knowledge pack for a team manifest.

    Args:
        team_manifest: the single source of truth for this team.
        out_dir: directory the `<team-slug>-context/` package is written into.
        dependents: optional list of teams that depend on this one (so the
            dependencies reference can show the inbound edges). When omitted,
            only the manifest's own outbound dependencies are documented.
        confluence: optional ConfluenceProvider. When supplied (and `decisions`
            is not), the team's decision logs are pulled via
            `get_decision_logs(team)` to build `references/decisions.md` — the
            context-stack's "decisions captured as they happen" layer.
        decisions: optional pre-fetched DecisionLogs; takes precedence over
            `confluence`. Lets callers that already hold the logs avoid a fetch.
        figma_components: optional FigmaComponents for this team. Used only to
            note library divergence (`diverges_from_library`/`divergence_notes`)
            on matching design components — never to invent component data.

    The decisions and divergence inputs are optional, so the existing
    `export_skill(manifest, out_dir, dependents=...)` call site keeps working
    unchanged; `references/decisions.md` is written only when logs are found.

    Returns:
        Path to the package directory (`<out_dir>/<team-slug>-context`).
    """
    dependents = dependents or []
    slug = slugify(team_manifest.team)
    skill_name = f"{slug}-context"

    decision_logs = _collect_decisions(team_manifest, confluence, decisions)
    divergence = {fc.name: fc for fc in (figma_components or [])}

    out_dir = Path(out_dir)
    pkg = out_dir / skill_name
    refs = pkg / "references"
    refs.mkdir(parents=True, exist_ok=True)

    (pkg / "SKILL.md").write_text(
        _skill_md(team_manifest, skill_name, dependents, decision_logs),
        encoding="utf-8",
    )
    (refs / "components.md").write_text(
        _components_md(team_manifest, divergence), encoding="utf-8"
    )
    (refs / "ownership.md").write_text(_ownership_md(team_manifest), encoding="utf-8")
    (refs / "dependencies.md").write_text(
        _dependencies_md(team_manifest, dependents), encoding="utf-8"
    )
    # Only emit the decision record when the team genuinely has logged decisions.
    if decision_logs:
        (refs / "decisions.md").write_text(
            _decisions_md(team_manifest, decision_logs), encoding="utf-8"
        )

    return pkg
