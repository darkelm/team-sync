"""Manifest health check — keep the dependency graph TRUSTWORTHY.

The team.yaml manifests *are* team-sync's moat: every answer (who-owns, dependency
maps, conflict prediction, digests) is only as good as the graph beneath it. When a
manifest goes stale, references a team that doesn't exist, or names a component nobody
owns, the whole product degrades quietly — confident answers built on rotted data.

This module is the graph's self-check. :func:`check_manifests` walks every manifest and
returns a structured :class:`HealthReport` of findings, grouped by severity, that the
`doctor` Slack command renders. It is PURE and READ-ONLY: it never writes, never raises
on a malformed manifest — a manifest it can't even read becomes an ``error`` finding, not
an exception (so one bad file can't take the whole check down).

Checks implemented:
  - **dangling-dep-team**  — a ``TeamDependency.team`` naming a team that doesn't exist.
  - **dangling-dep-component** — a ``TeamDependency.components`` entry the named team
    doesn't actually own.
  - **orphan-component**   — a component referenced as a dependency that NO team owns.
  - **missing-field**      — a required-ish field is empty: no owner, no slack_channel,
    no jira_project/confluence_space, no components, etc.
  - **self-dependency**    — a team that lists itself as a dependency (a graph smell).
  - **staleness**          — a manifest whose ``last_verified`` is aging/stale (via
    freshness.py) or absent ("unverified").
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import freshness

# Severity vocabulary, ordered worst→best for rendering.
SEVERITIES = ("error", "warn", "info")


@dataclass(frozen=True)
class Finding:
    """One thing wrong (or worth knowing) about the graph.

    - ``severity`` — "error" | "warn" | "info".
    - ``kind``     — a stable machine tag (see module docstring) for tests/grouping.
    - ``subject``  — the team and/or component the finding is about (human-readable).
    - ``message``  — a one-line human explanation, Slack-ready.
    """
    severity: str
    kind: str
    subject: str
    message: str


@dataclass
class HealthReport:
    """The structured result of a graph check. ``findings`` is the full list; the
    convenience properties group/count them for the renderer."""
    findings: list[Finding] = field(default_factory=list)
    teams_checked: int = 0

    @property
    def ok(self) -> bool:
        """Clean = no error/warn findings (info-only is still 'healthy enough')."""
        return not any(f.severity in ("error", "warn") for f in self.findings)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warn"]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "info"]

    def by_severity(self, severity: str) -> list[Finding]:
        return [f for f in self.findings if f.severity == severity]


def _safe_get_all_teams(providers) -> tuple[list, list[Finding]]:
    """Load every manifest, turning any load failure into an ``error`` finding instead
    of letting it raise — the no-raise-on-malformed guarantee at the load boundary."""
    try:
        return list(providers.manifests.get_all_teams()), []
    except Exception as e:  # malformed yaml, schema validation, IO — never propagate.
        return [], [Finding(
            "error", "load-failure", "manifests",
            f"Couldn't load one or more manifests: {e}",
        )]


def _owned_components(teams) -> dict[str, str]:
    """Map every owned component name (lowercased) → the team that owns it. Tolerates a
    manifest with a missing/garbled components block."""
    owners: dict[str, str] = {}
    for t in teams:
        try:
            comps = list(t.components.code) + list(t.components.design)
        except Exception:
            continue
        for c in comps:
            name = getattr(c, "name", None)
            if name:
                owners.setdefault(name.lower(), getattr(t, "team", "?"))
    return owners


def _check_one_team(team, team_names: set[str], owners: dict[str, str]) -> list[Finding]:
    """All findings for a single manifest. Wrapped by the caller so a raise here can't
    sink the whole report (defensive — the checks below already guard with getattr)."""
    findings: list[Finding] = []
    name = getattr(team, "team", None) or "(unnamed team)"

    # --- missing required-ish fields -----------------------------------------
    if not getattr(team, "owner", None) or not getattr(getattr(team, "owner", None), "name", None):
        findings.append(Finding("error", "missing-field", name,
                                 f"{name} has no owner — nobody is accountable for this manifest."))
    if not getattr(team, "slack_channel", None):
        findings.append(Finding("warn", "missing-field", name,
                                 f"{name} has no slack_channel — answers can't point people anywhere."))
    if not getattr(team, "jira_project", None):
        findings.append(Finding("warn", "missing-field", name,
                                 f"{name} has no jira_project — deliverable/ticket lookups will come up empty."))
    if not getattr(team, "confluence_space", None):
        findings.append(Finding("info", "missing-field", name,
                                 f"{name} has no confluence_space — decision-log search can't reach it."))

    try:
        owned = list(team.components.code) + list(team.components.design)
    except Exception:
        owned = []
    if not owned:
        findings.append(Finding("warn", "missing-field", name,
                                 f"{name} owns no components — it's invisible to who-owns and reuse checks."))

    # --- dangling dependency refs + orphans ----------------------------------
    for dep in getattr(team, "dependencies", None) or []:
        dep_team = getattr(dep, "team", None)
        if not dep_team:
            findings.append(Finding("warn", "missing-field", name,
                                     f"{name} has a dependency with no team named."))
            continue
        if dep_team.lower() == name.lower():
            findings.append(Finding("warn", "self-dependency", name,
                                     f"{name} lists itself as a dependency — that's a graph smell, drop it."))
        elif dep_team.lower() not in team_names:
            findings.append(Finding("error", "dangling-dep-team", f"{name} → {dep_team}",
                                     f"{name} depends on '{dep_team}', but no such team exists in the graph."))
        for comp in getattr(dep, "components", None) or []:
            owner = owners.get(comp.lower())
            if owner is None:
                findings.append(Finding("error", "orphan-component", f"{name} → {comp}",
                                        f"{name} depends on component '{comp}', but no team owns it (orphan)."))
            elif dep_team and owner.lower() != dep_team.lower():
                findings.append(Finding("warn", "dangling-dep-component", f"{name} → {dep_team}/{comp}",
                                        f"{name} expects '{comp}' from {dep_team}, but it's actually owned by {owner}."))

    # --- staleness -----------------------------------------------------------
    fresh = freshness.assess(team)
    if fresh.label == "unverified":
        findings.append(Finding("warn", "staleness", name,
                                 f"{name} is unverified (no last_verified) — its data is untrusted."))
    elif fresh.label == "stale":
        findings.append(Finding("warn", "staleness", name,
                                 f"{name} is stale — {fresh.note.strip(' _')}."))
    elif fresh.label == "aging":
        findings.append(Finding("info", "staleness", name,
                                 f"{name} is aging — {fresh.note.strip(' _')}; re-verify soon."))

    return findings


def check_manifests(providers) -> HealthReport:
    """Validate the whole dependency graph and return a structured :class:`HealthReport`.

    Pure and read-only. NEVER raises on a malformed manifest: a load failure or a
    per-team crash is collected as an ``error`` finding so the team always gets a report.
    """
    teams, load_findings = _safe_get_all_teams(providers)
    report = HealthReport(findings=list(load_findings), teams_checked=len(teams))

    team_names = {getattr(t, "team", "").lower() for t in teams if getattr(t, "team", None)}
    owners = _owned_components(teams)

    for team in teams:
        try:
            report.findings.extend(_check_one_team(team, team_names, owners))
        except Exception as e:  # defensive: one team's bug never sinks the report.
            nm = getattr(team, "team", "(unknown team)")
            report.findings.append(Finding("error", "check-failure", nm,
                                            f"Couldn't fully check {nm}: {e}"))

    return report
