"""ManifestRefresher — keep manifests honest over time.

Re-scans the same sources and diffs reality against the current manifest:
new/removed components, owner changes, new members, newly-implied dependencies.
Surfaces proposed updates for human review so manifests never silently rot.
"""
from __future__ import annotations
from dataclasses import dataclass, field as dc_field
from .builder import ManifestBuilder


@dataclass
class ManifestDiff:
    team: str
    components_added: list[tuple[str, str]] = dc_field(default_factory=list)   # (name, provenance)
    components_removed: list[str] = dc_field(default_factory=list)
    owner_change: tuple[str, str, str] | None = None                          # (old, new, provenance)
    members_added: list[tuple[str, str]] = dc_field(default_factory=list)
    dependencies_added: list[tuple[str, str]] = dc_field(default_factory=list)
    sources_used: list[str] = dc_field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.components_added or self.components_removed
                    or self.owner_change or self.members_added or self.dependencies_added)


class ManifestRefresher:
    def __init__(self, team: str, known_teams: list[str] | None = None):
        self.team = team
        self.known_teams = known_teams or []

    def diff(self, current: dict, sources: list[str]) -> ManifestDiff:
        b = ManifestBuilder(self.team, known_teams=self.known_teams)
        for s in sources:
            b.add_source(s)
        cs = b.cs
        d = ManifestDiff(team=self.team, sources_used=b.sources_used)

        # Components
        cur_comps = {c.get("name", "").lower()
                     for c in (current.get("components") or {}).get("code", [])}
        fresh = {}
        for fc in cs.collection("components.code[]"):
            fresh[fc.value.get("name", "").lower()] = fc
        for name, fc in fresh.items():
            if name and name not in cur_comps:
                d.components_added.append((fc.value.get("name", ""), f"{fc.note} [{','.join(fc.sources)}]"))
        for name in cur_comps:
            if name and name not in fresh:
                d.components_removed.append(name)

        # Owner
        cur_owner = ((current.get("owner") or {}).get("name") or "").lower()
        fresh_owner = cs.scalar("owner")
        if fresh_owner and cur_owner and fresh_owner.value.get("name", "").lower() != cur_owner:
            # only propose if the fresh signal is explicit (roster/codeowners)
            if any(s in ("roster", "codeowners") for s in fresh_owner.sources):
                d.owner_change = (
                    (current.get("owner") or {}).get("name", ""),
                    fresh_owner.value.get("name", ""),
                    f"{fresh_owner.note} [{','.join(fresh_owner.sources)}]",
                )

        # Members
        cur_members = {(m.get("name") or "").lower() for m in current.get("members", [])}
        for fm in cs.collection("members[]"):
            nm = fm.value.get("name", "")
            if nm and nm.lower() not in cur_members:
                d.members_added.append((nm, f"{fm.note} [{','.join(fm.sources)}]"))

        # Dependencies
        cur_deps = {(dep.get("team") or "").lower() for dep in current.get("dependencies", [])}
        for fd in cs.collection("dependencies[]"):
            tm = fd.value.get("team", "")
            if tm and tm.lower() not in cur_deps and tm.lower() != self.team.lower():
                d.dependencies_added.append((tm, f"{fd.note} [{','.join(fd.sources)}]"))

        return d
