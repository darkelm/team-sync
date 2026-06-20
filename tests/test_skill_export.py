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
