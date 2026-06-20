"""Tests for the knowledge-pack generator (src/skill_export.py).

Generates a Claude Skill package from a synthetic team manifest and asserts the
standard structure, valid YAML frontmatter, and that the body + references carry
the team's real content.
"""
from __future__ import annotations

import yaml

from src.skill_export import export_skill, slugify


def _phoenix(providers):
    m = providers.manifests.get_team("Team Phoenix")
    assert m is not None
    return m


def _atlas(providers):
    """Team Atlas has decision logs + a deprecated component (token-validation)."""
    m = providers.manifests.get_team("Team Atlas")
    assert m is not None
    return m


def _parse_frontmatter(skill_md_text: str) -> dict:
    """Pull the YAML frontmatter block delimited by leading '---' fences."""
    assert skill_md_text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    _, fm, _body = skill_md_text.split("---\n", 2)
    return yaml.safe_load(fm)


def test_package_structure_exists(providers, tmp_path):
    manifest = _phoenix(providers)
    pkg = export_skill(manifest, tmp_path)

    assert pkg.is_dir()
    assert pkg.name == "team-phoenix-context"
    assert (pkg / "SKILL.md").is_file()
    refs = pkg / "references"
    assert (refs / "components.md").is_file()
    assert (refs / "ownership.md").is_file()
    assert (refs / "dependencies.md").is_file()


def test_skill_frontmatter_valid(providers, tmp_path):
    manifest = _phoenix(providers)
    pkg = export_skill(manifest, tmp_path)
    text = (pkg / "SKILL.md").read_text()

    fm = _parse_frontmatter(text)
    assert fm["name"] == "team-phoenix-context"
    assert isinstance(fm["description"], str) and fm["description"]
    # trigger keywords required by the brief
    desc = fm["description"].lower()
    for kw in ("team phoenix", "ownership", "components", "dependencies", "design system"):
        assert kw in desc, f"missing trigger keyword: {kw}"
    assert len(fm["description"]) <= 220


def test_skill_body_mentions_team_and_components(providers, tmp_path):
    manifest = _phoenix(providers)
    pkg = export_skill(manifest, tmp_path)
    text = (pkg / "SKILL.md").read_text()

    assert "Team Phoenix" in text
    # at least one real code component and one design component name surface
    assert "auth" in text
    assert "LoginFlow" in text
    # quarter goals + slack channel make it into the body
    assert "#phoenix-team" in text
    assert any(g.split()[0] in text for g in manifest.quarter_goals)


def test_reference_files_have_content(providers, tmp_path):
    manifest = _phoenix(providers)
    dependents = providers.manifests.get_dependents(manifest.team)
    pkg = export_skill(manifest, tmp_path, dependents=dependents)
    refs = pkg / "references"

    components = (refs / "components.md").read_text()
    assert "token-manager" in components
    assert "AuthModal" in components

    ownership = (refs / "ownership.md").read_text()
    assert manifest.owner.name in ownership
    assert manifest.owner.email in ownership
    assert manifest.members[0].name in ownership

    deps = (refs / "dependencies.md").read_text()
    # outbound dependency
    assert "Team Atlas" in deps
    # inbound: at least one dependent team is listed when passed in
    if dependents:
        assert any(d.team in deps for d in dependents)


def test_slugify():
    assert slugify("Team Phoenix") == "team-phoenix"
    assert slugify("Team  Nova!!") == "team-nova"


def test_export_returns_package_path(providers, tmp_path):
    manifest = _phoenix(providers)
    pkg = export_skill(manifest, tmp_path)
    assert pkg == tmp_path / "team-phoenix-context"


# ── context-stack extensions ───────────────────────────────────────────────────

def test_no_decisions_file_without_provider(providers, tmp_path):
    """Backward compatible: with no confluence/decisions input, no decisions.md
    is written and the existing references are unchanged."""
    manifest = _phoenix(providers)
    pkg = export_skill(manifest, tmp_path)
    assert not (pkg / "references" / "decisions.md").exists()


def test_decision_record_generated_from_confluence(providers, tmp_path):
    """A team with decision logs gets references/decisions.md sourced from the
    ConfluenceProvider's get_decision_logs(team)."""
    manifest = _atlas(providers)
    # Sanity: the provider actually has a decision log for Atlas.
    logs = providers.confluence.get_decision_logs(team=manifest.team)
    assert logs, "fixture expects Atlas to have at least one decision log"

    pkg = export_skill(manifest, tmp_path, confluence=providers.confluence)
    decisions_md = pkg / "references" / "decisions.md"
    assert decisions_md.is_file()

    text = decisions_md.read_text()
    assert "Decision record" in text
    # The real Atlas decision (DEC-ATL-001) surfaces: title, rationale, deciders.
    assert "event-driven architecture" in text
    assert "Rationale:" in text
    assert "Jordan Kim" in text
    # Alternatives considered are rendered when present.
    assert "Alternatives considered:" in text
    assert "DEC-ATL-001" in text


def test_decision_record_via_explicit_decisions_list(providers, tmp_path):
    """Callers can pass pre-fetched DecisionLogs directly (precedence over a
    provider)."""
    manifest = _atlas(providers)
    pages = providers.confluence.get_decision_logs(team=manifest.team)
    decisions = [p.decision_log for p in pages if p.decision_log]
    assert decisions

    pkg = export_skill(manifest, tmp_path, decisions=decisions)
    text = (pkg / "references" / "decisions.md").read_text()
    assert decisions[0].title in text


def test_skill_body_references_decisions_and_system_model(providers, tmp_path):
    manifest = _atlas(providers)
    pkg = export_skill(manifest, tmp_path, confluence=providers.confluence)
    text = (pkg / "SKILL.md").read_text()

    # System-model framing paragraph (reasoning, not just a list).
    assert "## System model" in text
    assert "Team Atlas owns the" in text
    # It should explain how the parts fit (components + dependency edges).
    assert "It owns code (" in text
    assert "ripple along those edges" in text
    # Recent-decisions teaser + pointer to the full record.
    assert "## Recent decisions" in text
    assert "references/decisions.md" in text


def test_components_md_per_component_and_deprecation(providers, tmp_path):
    """Enriched components.md: per-component headings + deprecation lifecycle for
    Atlas's deprecated token-validation component."""
    manifest = _atlas(providers)
    pkg = export_skill(manifest, tmp_path)
    text = (pkg / "references" / "components.md").read_text()

    # Per-component template headings + fields.
    assert "### api-gateway" in text
    assert "### token-validation" in text
    assert "**Path:**" in text
    assert "**Description:**" in text
    # Design components get their own sections too, with Figma node ids.
    assert "### DataTable" in text
    assert "456:111" in text

    # Deprecation lifecycle rendered only for the deprecated component.
    assert "DEPRECATED" in text
    assert "sunset 2026-09-01" in text
    assert "token-validation-v2" in text  # replacement


def test_components_md_divergence_when_figma_record_supplied(providers, tmp_path):
    """Library divergence is noted only when a Figma record carries it (the
    manifest's DesignComponent has no divergence field)."""
    from src.core.schemas import DesignStatus, FigmaComponent
    from datetime import datetime

    manifest = _atlas(providers)
    diverging = FigmaComponent(
        id="fc-1",
        name="DataTable",
        file_id="def456",
        file_name="Atlas Data Dashboards",
        team=manifest.team,
        description="Reusable data table",
        status=DesignStatus.in_progress,
        last_modified=datetime(2026, 5, 20, 12, 0, 0),
        diverges_from_library=True,
        divergence_notes="Local padding override not in Nova DS",
    )
    pkg = export_skill(manifest, tmp_path, figma_components=[diverging])
    text = (pkg / "references" / "components.md").read_text()
    assert "Diverges from library" in text
    assert "Local padding override not in Nova DS" in text
