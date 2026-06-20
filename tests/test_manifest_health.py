"""Tests for src/agent/manifest_health.py — the dependency-graph self-check.

Covers each finding type (dangling team ref, orphan component, dangling component
owner, missing fields, self-dependency, staleness) plus the load-shape contract and
the no-raise-on-malformed guarantee. Uses lightweight fakes (SimpleNamespace) for the
malformed cases and the real synthetic providers for the clean-shape assertion, so the
checks are exercised both against hand-built edge cases and the real org.
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from src.agent import manifest_health
from src.agent.manifest_health import check_manifests, HealthReport


# ── Fakes ─────────────────────────────────────────────────────────────────────

def _comp(name):
    return SimpleNamespace(name=name)


def _components(code=(), design=()):
    return SimpleNamespace(code=[_comp(c) for c in code], design=[_comp(d) for d in design])


def _dep(team, components=()):
    return SimpleNamespace(team=team, components=list(components), reason="x")


def _team(name, *, owner="Owner", slack="#c", jira="J", confluence="S",
          code=("widget",), design=(), deps=(), days_ago=1):
    lv = None if days_ago is None else date.today() - timedelta(days=days_ago)
    return SimpleNamespace(
        team=name,
        owner=SimpleNamespace(name=owner) if owner else None,
        slack_channel=slack,
        jira_project=jira,
        confluence_space=confluence,
        components=_components(code=code, design=design),
        dependencies=list(deps),
        last_verified=lv,
    )


def _providers(teams):
    """A stand-in providers bundle with just the .manifests.get_all_teams() seam used."""
    return SimpleNamespace(manifests=SimpleNamespace(get_all_teams=lambda: teams))


def _kinds(report):
    return {f.kind for f in report.findings}


# ── Clean graph ─────────────────────────────────────────────────────────────

def test_clean_graph_is_ok():
    a = _team("Team A", code=("widget",), deps=())
    b = _team("Team B", code=("gadget",), deps=(_dep("Team A", ["widget"]),))
    report = check_manifests(_providers([a, b]))
    assert isinstance(report, HealthReport)
    assert report.teams_checked == 2
    assert report.ok, [f.message for f in report.findings]
    assert not report.errors and not report.warnings


def test_synthetic_org_loads_and_reports_shape(providers):
    """Against the real synthetic org: returns a HealthReport with the right shape.
    (The synthetic manifests are ~3 weeks old, so 'aging' notes are expected — we
    assert the structure and that nothing crashed, not a specific clean/dirty verdict.)"""
    report = check_manifests(providers)
    assert isinstance(report, HealthReport)
    assert report.teams_checked == 5
    for f in report.findings:
        assert f.severity in manifest_health.SEVERITIES
        assert f.kind and f.subject and f.message


# ── Each finding type ─────────────────────────────────────────────────────────

def test_dangling_dep_team():
    a = _team("Team A", deps=(_dep("Team Ghost", []),))
    report = check_manifests(_providers([a]))
    assert "dangling-dep-team" in _kinds(report)
    assert any(f.severity == "error" and "Team Ghost" in f.message for f in report.findings)


def test_orphan_component():
    # Team A depends on a component no team owns.
    a = _team("Team A", code=("widget",), deps=(_dep("Team B", ["nonexistent"]),))
    b = _team("Team B", code=("gadget",))
    report = check_manifests(_providers([a, b]))
    assert "orphan-component" in _kinds(report)
    assert any(f.severity == "error" and "nonexistent" in f.message for f in report.findings)


def test_dangling_dep_component_wrong_owner():
    # Component exists but is owned by a DIFFERENT team than the dep claims.
    a = _team("Team A", code=("widget",), deps=(_dep("Team B", ["widget"]),))
    b = _team("Team B", code=("gadget",))
    report = check_manifests(_providers([a, b]))
    assert "dangling-dep-component" in _kinds(report)
    assert any(f.severity == "warn" and "Team A" in f.subject for f in report.findings)


def test_missing_owner_is_error():
    a = _team("Team A", owner=None)
    report = check_manifests(_providers([a]))
    assert any(f.kind == "missing-field" and f.severity == "error" and "owner" in f.message
               for f in report.findings)


def test_missing_slack_channel_is_warn():
    a = _team("Team A", slack="")
    report = check_manifests(_providers([a]))
    assert any(f.kind == "missing-field" and f.severity == "warn" and "slack_channel" in f.message
               for f in report.findings)


def test_empty_components_is_warn():
    a = _team("Team A", code=(), design=())
    report = check_manifests(_providers([a]))
    assert any(f.kind == "missing-field" and "no components" in f.message for f in report.findings)


def test_self_dependency():
    a = _team("Team A", deps=(_dep("Team A", []),))
    report = check_manifests(_providers([a]))
    assert "self-dependency" in _kinds(report)


def test_unverified_staleness():
    a = _team("Team A", days_ago=None)
    report = check_manifests(_providers([a]))
    assert any(f.kind == "staleness" and "unverified" in f.message for f in report.findings)


def test_stale_staleness():
    a = _team("Team A", days_ago=90)
    report = check_manifests(_providers([a]))
    assert any(f.kind == "staleness" and f.severity == "warn" for f in report.findings)


# ── No-raise-on-malformed guarantee ────────────────────────────────────────────

def test_load_failure_becomes_finding_not_exception():
    def boom():
        raise ValueError("malformed team.yaml")
    providers = SimpleNamespace(manifests=SimpleNamespace(get_all_teams=boom))
    report = check_manifests(providers)  # must not raise
    assert any(f.kind == "load-failure" and f.severity == "error" for f in report.findings)


def test_garbled_team_object_does_not_raise():
    # A team object missing nearly everything — getattr guards must hold.
    junk = SimpleNamespace()  # no team, owner, components, etc.
    report = check_manifests(_providers([junk]))  # must not raise
    assert isinstance(report, HealthReport)
    # It at least flags the absent owner.
    assert any(f.kind in ("missing-field", "check-failure") for f in report.findings)


def test_per_team_crash_is_collected_not_propagated(monkeypatch):
    good = _team("Team Good")
    monkeypatch.setattr(manifest_health, "_check_one_team",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaboom")))
    report = check_manifests(_providers([good]))  # must not raise
    assert any(f.kind == "check-failure" for f in report.findings)
