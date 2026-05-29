"""Manifest Builder — fuse multi-source candidates into a reviewable draft team.yaml.

Point it at whatever sources exist for a team; it runs the matching adapters,
fuses the candidates (explicit beats inferred, conflicts surfaced), and renders
a YAML draft with inline provenance comments and TODOs for gaps.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field as dc_field
from .candidates import CandidateSet
from .adapters import (
    RepoStructureAdapter, GitHistoryAdapter, CodeownersAdapter,
    RosterAdapter, TranscriptAdapter, JiraAdapter,
)


@dataclass
class BuildResult:
    yaml_text: str
    gaps: list[str] = dc_field(default_factory=list)
    sources_used: list[str] = dc_field(default_factory=list)
    conflicts: list[str] = dc_field(default_factory=list)


def detect_source_kind(path: str) -> str:
    """Classify a builder input path."""
    if os.path.isfile(path):
        base = os.path.basename(path).lower()
        if base == "codeowners":
            return "codeowners"
        if path.lower().endswith((".vtt", ".srt")):
            return "transcript"
        if path.lower().endswith(".csv"):
            return "csv"  # disambiguated by header (roster vs jira)
        if path.lower().endswith(".txt"):
            from ..importers.transcript import looks_like_transcript
            return "transcript" if looks_like_transcript(path) else "unknown"
    if os.path.isdir(path):
        return "repo"
    return "unknown"


def _csv_is_jira(path: str) -> bool:
    try:
        with open(path, encoding="utf-8-sig") as f:
            header = f.readline().lower()
        return "issue key" in header or "summary" in header
    except OSError:
        return False


class ManifestBuilder:
    def __init__(self, team: str, known_teams: list[str] | None = None):
        self.team = team
        self.known_teams = known_teams or []
        self.cs = CandidateSet()
        self.sources_used: list[str] = []
        self.context_text = ""  # README/doc text for optional AI enrichment

    def add_source(self, path: str) -> None:
        kind = detect_source_kind(path)
        if kind == "repo":
            self.cs.extend(RepoStructureAdapter(path).extract()); self._mark("repo")
            self.cs.extend(GitHistoryAdapter(path).extract()); self._mark("git")
            co = CodeownersAdapter(path).extract()
            if co:
                self.cs.extend(co); self._mark("codeowners")
            self._capture_readme(path)
        elif kind == "codeowners":
            self.cs.extend(CodeownersAdapter(os.path.dirname(path) or ".").extract()); self._mark("codeowners")
        elif kind == "transcript":
            self.cs.extend(TranscriptAdapter(path, self.known_teams).extract()); self._mark("transcript")
        elif kind == "csv":
            if _csv_is_jira(path):
                self.cs.extend(JiraAdapter(path, self.team).extract()); self._mark("jira")
            else:
                self.cs.extend(RosterAdapter(path, self.team).extract()); self._mark("roster")

    def _mark(self, s: str) -> None:
        if s not in self.sources_used:
            self.sources_used.append(s)

    def _capture_readme(self, repo_path: str) -> None:
        for name in ("README.md", "README.rst", "README.txt", "README"):
            p = os.path.join(repo_path, name)
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8", errors="ignore") as f:
                        self.context_text = f.read()
                except OSError:
                    pass
                return

    def _ai_enrich(self) -> dict:
        """Optional: infer team + component descriptions from README text. Empty dict if unavailable."""
        from ..agent.ai_enhance import ai_available
        if not ai_available() or not self.context_text:
            return {}
        try:
            from ..agent.ai_enhance import infer_manifest
            comp_names = [fc.value.get("name", "") for fc in self.cs.collection("components.code[]")]
            return infer_manifest(self.team, self.context_text, comp_names)
        except Exception as e:
            print(f"[builder] AI enrichment skipped: {e}", flush=True)
            return {}

    # ── rendering ────────────────────────────────────────────────────────────

    def build(self) -> BuildResult:
        gaps: list[str] = []
        conflicts: list[str] = []
        lines: list[str] = [f"team: {self.team}"]

        # Optional AI enrichment (description + component descriptions) from README text.
        ai = self._ai_enrich()
        ai_comp_desc = ai.get("component_descriptions", {})

        # description — AI-inferred from README when available, else a gap to fill.
        if ai.get("description"):
            lines.append(f'description: "{ai["description"]}"  # inferred from README [ai] — confirm')
        else:
            lines.append('description: ""  # TODO: one-line team mission')
            gaps.append("description")

        # owner
        owner = self.cs.scalar("owner")
        if owner:
            o = owner.value
            tag = "  # ⚠ CONFLICT — multiple sources disagree, confirm" if owner.conflict else f"  # {owner.note} [{','.join(owner.sources)}]"
            lines.append("owner:")
            lines.append(f"  name: {o.get('name','')}{tag}")
            lines.append(f"  role: {o.get('role','') or 'TODO'}")
            lines.append(f'  slack_handle: "{o.get("slack_handle","")}"')
            lines.append(f"  email: {o.get('email','') or 'TODO'}")
            if owner.conflict:
                conflicts.append("owner")
            if not o.get("slack_handle") or not o.get("email"):
                gaps.append("owner contact details")
        else:
            lines += ['owner:', '  name: ""  # TODO', '  role: ""', '  slack_handle: ""', '  email: ""']
            gaps.append("owner")

        # members
        members = self.cs.collection("members[]")
        lines.append("members:")
        if members:
            for m in members:
                v = m.value
                lines.append(f"  - name: {v.get('name','')}  # {m.note} [{','.join(m.sources)}]")
                lines.append(f"    role: {v.get('role','') or 'TODO'}")
                lines.append(f'    slack_handle: "{v.get("slack_handle","")}"')
                lines.append(f"    email: {v.get('email','') or 'TODO'}")
        else:
            lines.append("  []  # TODO: no member sources provided")
            gaps.append("members")

        # channels / projects / spaces — rarely inferable
        chan = self.cs.scalar("slack_channel")
        if chan:
            lines.append(f"slack_channel: \"{chan.value}\"  # {chan.note}")
        else:
            lines.append('slack_channel: ""  # TODO: team Slack channel')
            gaps.append("slack_channel")
        lines.append('jira_project: ""  # TODO if using Jira')
        lines.append('confluence_space: ""  # TODO if using Confluence')

        # components.code
        code = self.cs.collection("components.code[]")
        lines.append("components:")
        lines.append("  code:")
        if code:
            # dedupe by name keeping best provenance
            seen = set()
            for c in code:
                v = c.value
                if v.get("name","").lower() in seen:
                    continue
                seen.add(v.get("name","").lower())
                lines.append(f"    - name: {v.get('name','')}  # {c.note} [{','.join(c.sources)}]")
                lines.append(f"      path: {v.get('path','') or 'TODO'}")
                desc = ai_comp_desc.get(v.get("name", ""), v.get("description", ""))
                lines.append(f"      description: {desc}")
        else:
            lines.append("    []  # TODO: no code sources provided")
            gaps.append("components.code")
        lines.append("  design: []  # TODO: design components (Figma) — add manually or via Figma source")
        gaps.append("components.design")

        # dependencies
        deps = self.cs.collection("dependencies[]")
        lines.append("dependencies:")
        if deps:
            for d in deps:
                v = d.value
                lines.append(f"  - team: {v.get('team','')}  # {d.note} [{','.join(d.sources)}] — CONFIRM")
                lines.append(f"    reason: {v.get('reason','')}")
                lines.append(f"    components: {v.get('components',[])}")
        else:
            lines.append("  []  # TODO: cross-team dependencies (or none)")
            gaps.append("dependencies")

        # roadmap + goals + resources — strategy, always human-supplied
        lines.append('roadmap_link: ""  # TODO')
        lines.append("quarter_goals: []  # TODO: this quarter's goals (strategy — ask the team)")
        lines.append("resources: []  # TODO: research repos, brand assets, prototypes, docs")
        gaps += ["quarter_goals", "resources"]

        from datetime import date as _date
        lines.append(f"last_verified: {_date.today()}  # auto-stamped; re-run `syncbot refresh-manifest` to update")

        header = [
            f"# DRAFT manifest for {self.team} — generated by SyncBot Manifest Builder",
            f"# Sources used: {', '.join(self.sources_used) or 'none'}",
            "# Review every line: '# inferred' fields need confirming; 'TODO' fields need filling.",
            "",
        ]
        return BuildResult(
            yaml_text="\n".join(header + lines) + "\n",
            gaps=gaps, sources_used=list(self.sources_used), conflicts=conflicts,
        )
