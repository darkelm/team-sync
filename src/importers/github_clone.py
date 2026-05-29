"""Import merged PRs from a local Git clone — no GitHub API access required.

Reads `git log` of merge commits, which works on any clone the user already
has (or can `git clone` once). Captures merge title, author, date, branch, and
the files changed, then maps components by matching changed paths against the
team manifests' component paths.
"""
from __future__ import annotations
import subprocess
from datetime import datetime
from typing import Optional
from ..core.schemas import PullRequest, PRStatus


def _run(repo_path: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True, text=True, check=False,
    )
    return result.stdout


def import_github_clone(
    repo_path: str,
    team: str,
    component_paths: Optional[dict[str, str]] = None,
    days: int = 90,
) -> list[PullRequest]:
    """component_paths: {component_name: path_prefix} for mapping changed files to components."""
    component_paths = component_paths or {}

    # Merge commits with a parseable record separator
    log = _run(
        repo_path,
        "log", "--merges", f"--since={days}.days.ago",
        "--pretty=format:%H%x1f%an%x1f%aI%x1f%s%x1f%b%x1e",
    )
    prs: list[PullRequest] = []
    for record in log.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        fields = record.split("\x1f")
        if len(fields) < 4:
            continue
        sha, author, iso_date, subject = fields[0], fields[1], fields[2], fields[3]
        body = fields[4] if len(fields) > 4 else ""

        # Files changed in this merge
        files_out = _run(repo_path, "diff-tree", "--no-commit-id", "--name-only", "-r", sha)
        files = [f for f in files_out.splitlines() if f.strip()]

        components = sorted({
            name for name, prefix in component_paths.items()
            if any(f.startswith(prefix) for f in files)
        })

        try:
            merged_at = datetime.fromisoformat(iso_date)
        except ValueError:
            merged_at = None

        # PR number from "Merge pull request #N" if present
        import re
        m = re.search(r"#(\d+)", subject)
        pr_id = m.group(1) if m else sha[:7]

        prs.append(PullRequest(
            id=pr_id,
            title=subject,
            description=body.strip(),
            status=PRStatus.merged,
            author=author,
            team=team,
            base_branch="main",
            head_branch="",
            files_changed=files,
            components_touched=components,
            created_at=merged_at or datetime.now(),
            merged_at=merged_at,
        ))
    return prs
