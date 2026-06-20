import os
import httpx
from datetime import datetime, timedelta, timezone
from typing import Optional
from ...core.schemas import PullRequest, PRStatus
from ..base import GitHubProvider
from ...log import get_logger

log = get_logger(__name__)


class LiveGitHubProvider(GitHubProvider):
    def __init__(self):
        self.token = os.environ["GITHUB_TOKEN"]
        self.org = os.environ.get("GITHUB_ORG", "")
        self.headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}

    def _get(self, path: str, params: dict = None) -> list | dict:
        r = httpx.get(f"https://api.github.com{path}", params=params, headers=self.headers)
        r.raise_for_status()
        return r.json()

    def get_pr_files(self, owner: str, repo: str, number: int, timeout: float = 5.0) -> list[str]:
        """
        Return the real changed-file paths for a merged PR via the GitHub Files API.

        GET /repos/{owner}/{repo}/pulls/{number}/files, paginated (100/page).
        GitHub PR/push webhook payloads do NOT carry file paths, so this is the
        only reliable source for resolving which components a merge touched.

        On any HTTP/transport failure this logs and re-raises so the caller can
        decide how to degrade — it never silently returns a partial/empty list
        that would masquerade as "nothing changed".
        """
        files: list[str] = []
        page = 1
        while True:
            try:
                r = httpx.get(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/files",
                    params={"per_page": 100, "page": page},
                    headers=self.headers,
                    timeout=timeout,
                )
                r.raise_for_status()
            except httpx.HTTPError as e:
                log.warning(
                    "github get_pr_files failed owner=%s repo=%s pr=%s page=%d: %s",
                    owner, repo, number, page, e,
                )
                raise
            batch = r.json()
            if not batch:
                break
            files.extend(f["filename"] for f in batch if f.get("filename"))
            if len(batch) < 100:
                break
            page += 1
        return files

    def _to_pr(self, item: dict, team: str = "") -> PullRequest:
        return PullRequest(
            id=str(item["number"]),
            title=item["title"],
            description=item.get("body") or "",
            status=PRStatus.merged if item.get("merged_at") else PRStatus(item["state"]),
            author=item["user"]["login"],
            team=team,
            base_branch=item["base"]["ref"],
            head_branch=item["head"]["ref"],
            created_at=datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")),
            merged_at=datetime.fromisoformat(item["merged_at"].replace("Z", "+00:00")) if item.get("merged_at") else None,
            linked_tickets=[lbl["title"] for lbl in item.get("labels", [])],
        )

    def get_pull_requests(self, team: Optional[str] = None, status: Optional[str] = None) -> list[PullRequest]:
        repos = self._get(f"/orgs/{self.org}/repos", {"per_page": 50}) if self.org else []
        prs = []
        for repo in repos:
            raw = self._get(f"/repos/{self.org}/{repo['name']}/pulls", {"state": status or "all", "per_page": 50})
            prs.extend([self._to_pr(p, repo["name"]) for p in raw])
        return prs

    def get_recent_prs(self, days: int = 7) -> list[PullRequest]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        repos = self._get(f"/orgs/{self.org}/repos", {"per_page": 50}) if self.org else []
        prs = []
        for repo in repos:
            raw = self._get(f"/repos/{self.org}/{repo['name']}/pulls", {"state": "closed", "per_page": 50})
            for p in raw:
                if p.get("merged_at") and datetime.fromisoformat(p["merged_at"].replace("Z", "+00:00")) >= cutoff:
                    prs.append(self._to_pr(p, repo["name"]))
        return prs

    def get_prs_touching_component(self, component: str) -> list[PullRequest]:
        comp_lower = component.lower()
        return [p for p in self.get_pull_requests() if comp_lower in p.description.lower() or comp_lower in p.title.lower()]
