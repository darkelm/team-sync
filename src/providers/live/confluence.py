import os
import httpx
from typing import Optional
from datetime import date
from ...core.schemas import ConfluencePage
from ..base import ConfluenceProvider


class LiveConfluenceProvider(ConfluenceProvider):
    def __init__(self):
        self.base_url = os.environ["ATLASSIAN_URL"].rstrip("/")
        self.email = os.environ["ATLASSIAN_EMAIL"]
        self.token = os.environ["ATLASSIAN_API_TOKEN"]
        self.auth = (self.email, self.token)

    def _get(self, path: str, params: dict = None) -> dict:
        r = httpx.get(f"{self.base_url}/wiki/rest/api{path}", params=params, auth=self.auth)
        r.raise_for_status()
        return r.json()

    def _to_page(self, item: dict, team: str = "") -> ConfluencePage:
        return ConfluencePage(
            id=item["id"],
            title=item["title"],
            space=item.get("space", {}).get("key", ""),
            team=team,
            content_summary=item.get("excerpt", ""),
            last_updated=date.fromisoformat(item["version"]["when"][:10]),
            author=item.get("version", {}).get("by", {}).get("displayName", ""),
            url=f"{self.base_url}/wiki{item['_links']['webui']}",
            tags=[label["name"] for label in item.get("metadata", {}).get("labels", {}).get("results", [])],
        )

    def get_pages(self, space: Optional[str] = None, team: Optional[str] = None) -> list[ConfluencePage]:
        params = {"expand": "version,metadata.labels,space", "limit": 50}
        if space:
            params["spaceKey"] = space
        data = self._get("/content", params)
        return [self._to_page(p, team or "") for p in data.get("results", [])]

    def search_pages(self, query: str, team: Optional[str] = None) -> list[ConfluencePage]:
        cql = f'type=page AND text~"{query}"'
        data = self._get("/search", {"cql": cql, "expand": "version,metadata.labels,space", "limit": 20})
        return [self._to_page(p, team or "") for p in data.get("results", [])]

    def get_decision_logs(self, team: Optional[str] = None, component: Optional[str] = None) -> list[ConfluencePage]:
        query = f'label="decision-log"'
        if component:
            query += f' AND text~"{component}"'
        data = self._get("/search", {"cql": query, "expand": "version,metadata.labels,space", "limit": 20})
        return [self._to_page(p, team or "") for p in data.get("results", [])]
