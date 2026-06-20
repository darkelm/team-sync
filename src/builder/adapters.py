"""Source adapters — each turns one kind of evidence into manifest Candidates.

All adapters are optional and independent. Point the builder at whatever a given
engagement has; missing sources simply contribute nothing.
"""
from __future__ import annotations
import os
import re
import subprocess
import csv
from collections import Counter
from .candidates import Candidate


# ── Repo folder structure → components ────────────────────────────────────────

_SRC_DIRS = ["src", "packages", "app", "apps", "lib", "libs", "services", "modules"]
_IGNORE = {"node_modules", "__pycache__", ".git", "dist", "build", "vendor", "test", "tests", "__tests__"}


class RepoStructureAdapter:
    source = "repo"

    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    def extract(self) -> list[Candidate]:
        out: list[Candidate] = []
        for src in _SRC_DIRS:
            base = os.path.join(self.repo_path, src)
            if not os.path.isdir(base):
                continue
            for entry in sorted(os.scandir(base), key=lambda e: e.name):
                if entry.is_dir() and entry.name not in _IGNORE and not entry.name.startswith("."):
                    rel = os.path.join(src, entry.name)
                    out.append(Candidate.make(
                        "components.code[]",
                        {"name": entry.name, "path": rel, "description": f"Code module at {rel}"},
                        self.source,
                        note=f"from repo folder {rel}",
                    ))
        return out


# ── Git history → owner + members ─────────────────────────────────────────────

class GitHistoryAdapter:
    source = "git"

    def __init__(self, repo_path: str, max_commits: int = 500):
        self.repo_path = repo_path
        self.max_commits = max_commits

    def _log(self) -> list[str]:
        r = subprocess.run(
            ["git", "-C", self.repo_path, "log", f"-{self.max_commits}", "--format=%an"],
            capture_output=True, text=True, check=False,
        )
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]

    def extract(self) -> list[Candidate]:
        authors = self._log()
        if not authors:
            return []
        counts = Counter(authors)
        total = sum(counts.values())
        out: list[Candidate] = []
        ranked = counts.most_common()
        # Top committer = candidate owner
        top_name, top_n = ranked[0]
        out.append(Candidate.make(
            "owner",
            {"name": top_name, "role": "Lead (inferred)", "slack_handle": "", "email": ""},
            self.source,
            note=f"{round(100*top_n/total)}% of recent commits",
        ))
        # Everyone with meaningful contribution = candidate members
        for name, n in ranked:
            if n / total >= 0.05:
                out.append(Candidate.make(
                    "members[]",
                    {"name": name, "role": "Contributor (inferred)", "slack_handle": "", "email": ""},
                    self.source,
                    note=f"{round(100*n/total)}% of recent commits",
                ))
        return out


# ── CODEOWNERS → ownership + members ──────────────────────────────────────────

class CodeownersAdapter:
    source = "codeowners"
    LOCATIONS = ["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"]

    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    def _find(self) -> str | None:
        for loc in self.LOCATIONS:
            p = os.path.join(self.repo_path, loc)
            if os.path.isfile(p):
                return p
        return None

    def extract(self) -> list[Candidate]:
        path = self._find()
        if not path:
            return []
        out: list[Candidate] = []
        owners_seen: set[str] = set()
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                pattern, owners = parts[0], parts[1:]
                for o in owners:
                    handle = o.lstrip("@")
                    if handle not in owners_seen:
                        owners_seen.add(handle)
                        out.append(Candidate.make(
                            "members[]",
                            {"name": handle, "role": "Owner (CODEOWNERS)", "slack_handle": f"@{handle}", "email": ""},
                            self.source,
                            note=f"CODEOWNERS for {pattern}",
                        ))
                # component-ownership hint from the path pattern
                comp = pattern.strip("/*").split("/")[-1]
                if comp and owners:
                    out.append(Candidate.make(
                        "components.code[]",
                        {"name": comp, "path": pattern.strip("/"), "description": f"Owned via CODEOWNERS ({', '.join(owners)})"},
                        self.source,
                        note=f"CODEOWNERS entry {pattern}",
                    ))
        return out


# ── Roster spreadsheet (CSV) → owner + members + channel ──────────────────────

class RosterAdapter:
    source = "roster"

    def __init__(self, csv_path: str, team: str):
        self.csv_path = csv_path
        self.team = team.lower()

    def extract(self) -> list[Candidate]:
        out: list[Candidate] = []
        with open(self.csv_path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return []
        cols = {k.lower(): k for k in rows[0].keys()}

        def col(*names):
            for n in names:
                if n in cols:
                    return cols[n]
            return None

        name_c = col("name", "full name", "person")
        role_c = col("role", "title", "position")
        email_c = col("email", "e-mail")
        slack_c = col("slack", "slack handle", "handle")
        team_c = col("team", "squad", "group")
        chan_c = col("slack channel", "channel")

        for row in rows:
            if team_c and self.team not in (row.get(team_c, "") or "").lower():
                continue
            name = (row.get(name_c) or "").strip() if name_c else ""
            if not name:
                continue
            role = (row.get(role_c) or "").strip() if role_c else ""
            member = {
                "name": name, "role": role or "Member",
                "slack_handle": (row.get(slack_c) or "").strip() if slack_c else "",
                "email": (row.get(email_c) or "").strip() if email_c else "",
            }
            out.append(Candidate.make("members[]", member, self.source, note="from roster"))
            if role and re.search(r"lead|manager|head|director|principal", role, re.I):
                out.append(Candidate.make("owner", member, self.source, note=f"roster role: {role}"))
            if chan_c and row.get(chan_c):
                out.append(Candidate.make("slack_channel", row[chan_c].strip(), self.source, note="from roster"))
        return out


# ── Meeting transcript → corroborating members + team mentions ────────────────

class TranscriptAdapter:
    source = "transcript"

    def __init__(self, path: str, known_teams: list[str] | None = None):
        self.path = path
        self.known_teams = known_teams or []

    def extract(self) -> list[Candidate]:
        from ..importers.transcript import parse_transcript
        segs = parse_transcript(self.path)
        out: list[Candidate] = []
        speakers = sorted({s.speaker for s in segs if s.speaker != "Unknown"})
        for sp in speakers:
            out.append(Candidate.make(
                "members[]",
                {"name": sp, "role": "Participant (from meeting)", "slack_handle": "", "email": ""},
                self.source, note=f"spoke in {os.path.basename(self.path)}",
            ))
        text = " ".join(s.text for s in segs).lower()
        for t in self.known_teams:
            if t.lower() in text:
                out.append(Candidate.make(
                    "dependencies[]",
                    {"team": t, "reason": "mentioned in meeting — confirm relationship", "components": []},
                    self.source, note=f"mentioned in {os.path.basename(self.path)}",
                ))
        return out


# ── Jira CSV → components + members ───────────────────────────────────────────

class JiraAdapter:
    source = "jira"

    def __init__(self, csv_path: str, team: str):
        self.csv_path = csv_path
        self.team = team

    def extract(self) -> list[Candidate]:
        from ..importers.jira_csv import import_jira_csv
        tickets = import_jira_csv(self.csv_path, self.team)
        out: list[Candidate] = []
        comps, assignees = set(), set()
        for t in tickets:
            for c in t.components:
                if c not in comps:
                    comps.add(c)
                    out.append(Candidate.make(
                        "components.code[]",
                        {"name": c, "path": "", "description": "Referenced in Jira tickets"},
                        self.source, note="Jira component field",
                    ))
            if t.assignee and t.assignee not in assignees:
                assignees.add(t.assignee)
                out.append(Candidate.make(
                    "members[]",
                    {"name": t.assignee, "role": "Contributor (Jira)", "slack_handle": "", "email": ""},
                    self.source, note="Jira assignee",
                ))
        return out
