"""Tests for the live Atlassian providers — LiveJiraProvider and
LiveConfluenceProvider (src/providers/live/jira.py, src/providers/live/confluence.py).

Both providers were at 0% coverage: they are pure HTTP→schema parsers that the
rest of the suite never exercises because the synthetic org uses the `local`
providers. These tests intercept HTTP at the `httpx.get` boundary (mirroring the
mocking discipline in test_figma_live.py — no real token, no network) and feed
canned JSON that mirrors the real Jira Cloud / Confluence Cloud REST shapes, then
assert the providers parse it into the correct pydantic schemas.

Run: SYNCBOT_TEST=1 .venv/bin/python3 -m pytest tests/test_live_atlassian.py -q
"""
from __future__ import annotations

from datetime import date, datetime

import httpx
import pytest

from src.core.schemas import TicketPriority, TicketStatus


# ── Fake httpx response ──────────────────────────────────────────────────────
# Mimics just the surface the providers touch: .raise_for_status(), .json(),
# and the .status_code/.text used in the warning path on an HTTP error.


class FakeHTTPResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None
            )

    def json(self):
        return self._payload


@pytest.fixture()
def atlassian_env(monkeypatch):
    """Set the three env vars both providers read in __init__."""
    monkeypatch.setenv("ATLASSIAN_URL", "https://acme.atlassian.net/")
    monkeypatch.setenv("ATLASSIAN_EMAIL", "bot@acme.com")
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "fake-token")


def _patch_httpx(monkeypatch, module, handler):
    """Replace the module-level `httpx.get` the provider calls with `handler`,
    which receives (url, params, auth) and returns a FakeHTTPResponse."""
    def fake_get(url, params=None, auth=None):
        return handler(url, params, auth)
    monkeypatch.setattr(module.httpx, "get", fake_get)


# ── Canned Jira Cloud /rest/api/3 payloads ───────────────────────────────────

def _jira_issue(key="PHX-1", summary="Wire up auth", status_key="in_progress",
                priority="High", assignee="Ada Lovelace", project="PHX",
                description="Some desc", labels=None, due="2026-07-01",
                components=None):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": description,
            "status": {"statusCategory": {"key": status_key}},
            "priority": {"name": priority},
            "assignee": {"displayName": assignee} if assignee else None,
            "project": {"key": project},
            "labels": labels if labels is not None else ["backend"],
            "duedate": due,
            "created": "2026-06-01T09:00:00.000Z",
            "updated": "2026-06-10T15:30:00.000Z",
            "components": [{"name": c} for c in (components or ["auth"])],
        },
    }


JIRA_SEARCH_RESPONSE = {"issues": [_jira_issue(), _jira_issue(key="PHX-2", summary="Add MFA")]}


# ── LiveJiraProvider ──────────────────────────────────────────────────────────

class TestLiveJiraProvider:
    def _provider(self):
        from src.providers.live.jira import LiveJiraProvider
        return LiveJiraProvider()

    def test_init_reads_env_and_strips_trailing_slash(self, atlassian_env):
        p = self._provider()
        assert p.base_url == "https://acme.atlassian.net"   # trailing slash stripped
        assert p.email == "bot@acme.com"
        assert p.auth == ("bot@acme.com", "fake-token")

    def test_get_tickets_parses_issues(self, atlassian_env, monkeypatch):
        import src.providers.live.jira as jira_mod
        captured = {}

        def handler(url, params, auth):
            captured["url"] = url
            captured["params"] = params
            return FakeHTTPResponse(JIRA_SEARCH_RESPONSE)

        _patch_httpx(monkeypatch, jira_mod, handler)
        p = self._provider()
        tickets = p.get_tickets(team="PHX", status="In Progress")

        # URL + JQL wiring.
        assert captured["url"].endswith("/rest/api/3/search")
        assert 'project = "PHX"' in captured["params"]["jql"]
        assert 'status = "In Progress"' in captured["params"]["jql"]

        # Parsing into the Ticket schema.
        assert len(tickets) == 2
        t = tickets[0]
        assert t.id == "PHX-1"
        assert t.title == "Wire up auth"
        assert t.status == TicketStatus.in_progress
        assert t.priority == TicketPriority.high
        assert t.assignee == "Ada Lovelace"
        assert t.team == "PHX"
        assert t.labels == ["backend"]
        assert t.due_date == date(2026, 7, 1)
        assert t.created_at == datetime.fromisoformat("2026-06-01T09:00:00.000+00:00")
        assert t.components == ["auth"]

    def test_get_tickets_no_team_uses_order_by(self, atlassian_env, monkeypatch):
        import src.providers.live.jira as jira_mod
        captured = {}

        def handler(url, params, auth):
            captured["params"] = params
            return FakeHTTPResponse({"issues": []})

        _patch_httpx(monkeypatch, jira_mod, handler)
        p = self._provider()
        assert p.get_tickets() == []
        assert "order by updated DESC" in captured["params"]["jql"]

    def test_get_ticket_single(self, atlassian_env, monkeypatch):
        import src.providers.live.jira as jira_mod
        captured = {}

        def handler(url, params, auth):
            captured["url"] = url
            return FakeHTTPResponse(_jira_issue(key="PHX-42", summary="Single"))

        _patch_httpx(monkeypatch, jira_mod, handler)
        p = self._provider()
        t = p.get_ticket("PHX-42")
        assert captured["url"].endswith("/rest/api/3/issue/PHX-42")
        assert t is not None
        assert t.id == "PHX-42"
        assert t.title == "Single"

    def test_get_ticket_handles_error_returns_none(self, atlassian_env, monkeypatch):
        import src.providers.live.jira as jira_mod

        def handler(url, params, auth):
            return FakeHTTPResponse({}, status_code=404, text="Not found")

        _patch_httpx(monkeypatch, jira_mod, handler)
        p = self._provider()
        assert p.get_ticket("NOPE-1") is None

    def test_get_tickets_by_component(self, atlassian_env, monkeypatch):
        import src.providers.live.jira as jira_mod
        captured = {}

        def handler(url, params, auth):
            captured["params"] = params
            return FakeHTTPResponse(JIRA_SEARCH_RESPONSE)

        _patch_httpx(monkeypatch, jira_mod, handler)
        p = self._provider()
        tickets = p.get_tickets_by_component("auth")
        assert 'component = "auth"' in captured["params"]["jql"]
        assert captured["params"]["maxResults"] == 50
        assert len(tickets) == 2

    def test_get_upcoming_deliverables(self, atlassian_env, monkeypatch):
        import src.providers.live.jira as jira_mod
        captured = {}

        def handler(url, params, auth):
            captured["params"] = params
            return FakeHTTPResponse({"issues": [_jira_issue(key="PHX-7")]})

        _patch_httpx(monkeypatch, jira_mod, handler)
        p = self._provider()
        tickets = p.get_upcoming_deliverables("PHX")
        assert 'project = "PHX"' in captured["params"]["jql"]
        assert "dueDate is not EMPTY" in captured["params"]["jql"]
        assert len(tickets) == 1
        assert tickets[0].id == "PHX-7"

    def test_search_swallows_error_and_returns_empty(self, atlassian_env, monkeypatch):
        """_search catches HTTP errors (so a bad query reads as 'no tickets')."""
        import src.providers.live.jira as jira_mod

        def handler(url, params, auth):
            return FakeHTTPResponse({}, status_code=401, text="Unauthorized")

        _patch_httpx(monkeypatch, jira_mod, handler)
        p = self._provider()
        assert p.get_tickets(team="PHX") == []

    def test_priority_defaults_to_medium_when_missing(self, atlassian_env, monkeypatch):
        import src.providers.live.jira as jira_mod
        issue = _jira_issue(key="PHX-9")
        issue["fields"]["priority"] = None
        issue["fields"]["assignee"] = None

        def handler(url, params, auth):
            return FakeHTTPResponse({"issues": [issue]})

        _patch_httpx(monkeypatch, jira_mod, handler)
        p = self._provider()
        t = p.get_tickets(team="PHX")[0]
        assert t.priority == TicketPriority.medium
        assert t.assignee is None

    def test_description_dict_form_parsed(self, atlassian_env, monkeypatch):
        """Jira's ADF description can arrive as a dict with a `text` key."""
        import src.providers.live.jira as jira_mod
        issue = _jira_issue(key="PHX-10")
        issue["fields"]["description"] = {"text": "rich text body"}

        def handler(url, params, auth):
            return FakeHTTPResponse({"issues": [issue]})

        _patch_httpx(monkeypatch, jira_mod, handler)
        p = self._provider()
        t = p.get_tickets(team="PHX")[0]
        assert t.description == "rich text body"


# ── Canned Confluence Cloud /wiki/rest/api payloads ───────────────────────────

def _confluence_page(pid="123", title="Auth Decision", space="ENG",
                     excerpt="We chose JWT", when="2026-05-20T12:00:00.000Z",
                     author="Grace Hopper", webui="/spaces/ENG/pages/123",
                     labels=None):
    if labels is None:
        labels = ["decision-log"]
    return {
        "id": pid,
        "title": title,
        "space": {"key": space},
        "excerpt": excerpt,
        "version": {"when": when, "by": {"displayName": author}},
        "_links": {"webui": webui},
        "metadata": {
            "labels": {"results": [{"name": n} for n in labels]}
        },
    }


CONFLUENCE_CONTENT_RESPONSE = {
    "results": [_confluence_page(), _confluence_page(pid="456", title="Runbook", labels=["ops"])]
}


# ── LiveConfluenceProvider ────────────────────────────────────────────────────

class TestLiveConfluenceProvider:
    def _provider(self):
        from src.providers.live.confluence import LiveConfluenceProvider
        return LiveConfluenceProvider()

    def test_init_reads_env(self, atlassian_env):
        p = self._provider()
        assert p.base_url == "https://acme.atlassian.net"
        assert p.auth == ("bot@acme.com", "fake-token")

    def test_get_pages_parses_and_maps(self, atlassian_env, monkeypatch):
        import src.providers.live.confluence as conf_mod
        captured = {}

        def handler(url, params, auth):
            captured["url"] = url
            captured["params"] = params
            return FakeHTTPResponse(CONFLUENCE_CONTENT_RESPONSE)

        _patch_httpx(monkeypatch, conf_mod, handler)
        p = self._provider()
        pages = p.get_pages(space="ENG", team="Team Phoenix")

        assert captured["url"].endswith("/wiki/rest/api/content")
        assert captured["params"]["spaceKey"] == "ENG"

        assert len(pages) == 2
        pg = pages[0]
        assert pg.id == "123"
        assert pg.title == "Auth Decision"
        assert pg.space == "ENG"
        assert pg.team == "Team Phoenix"
        assert pg.content_summary == "We chose JWT"
        assert pg.last_updated == date(2026, 5, 20)
        assert pg.author == "Grace Hopper"
        # URL composed from base_url + /wiki + webui link.
        assert pg.url == "https://acme.atlassian.net/wiki/spaces/ENG/pages/123"
        assert pg.tags == ["decision-log"]

    def test_get_pages_without_space_omits_spacekey(self, atlassian_env, monkeypatch):
        import src.providers.live.confluence as conf_mod
        captured = {}

        def handler(url, params, auth):
            captured["params"] = params
            return FakeHTTPResponse({"results": []})

        _patch_httpx(monkeypatch, conf_mod, handler)
        p = self._provider()
        assert p.get_pages() == []
        assert "spaceKey" not in captured["params"]

    def test_search_pages_builds_cql(self, atlassian_env, monkeypatch):
        import src.providers.live.confluence as conf_mod
        captured = {}

        def handler(url, params, auth):
            captured["url"] = url
            captured["params"] = params
            return FakeHTTPResponse(CONFLUENCE_CONTENT_RESPONSE)

        _patch_httpx(monkeypatch, conf_mod, handler)
        p = self._provider()
        pages = p.search_pages("auth", team="Team Phoenix")
        assert captured["url"].endswith("/wiki/rest/api/search")
        assert 'text~"auth"' in captured["params"]["cql"]
        assert "type=page" in captured["params"]["cql"]
        assert len(pages) == 2
        assert all(pg.team == "Team Phoenix" for pg in pages)

    def test_get_decision_logs_with_component(self, atlassian_env, monkeypatch):
        import src.providers.live.confluence as conf_mod
        captured = {}

        def handler(url, params, auth):
            captured["params"] = params
            return FakeHTTPResponse({"results": [_confluence_page()]})

        _patch_httpx(monkeypatch, conf_mod, handler)
        p = self._provider()
        pages = p.get_decision_logs(team="Team Phoenix", component="auth")
        assert 'label="decision-log"' in captured["params"]["cql"]
        assert 'text~"auth"' in captured["params"]["cql"]
        assert len(pages) == 1

    def test_get_decision_logs_no_component(self, atlassian_env, monkeypatch):
        import src.providers.live.confluence as conf_mod
        captured = {}

        def handler(url, params, auth):
            captured["params"] = params
            return FakeHTTPResponse({"results": []})

        _patch_httpx(monkeypatch, conf_mod, handler)
        p = self._provider()
        assert p.get_decision_logs() == []
        # No component -> no text~ clause appended.
        assert "text~" not in captured["params"]["cql"]

    def test_get_pages_swallows_error_returns_empty(self, atlassian_env, monkeypatch):
        import src.providers.live.confluence as conf_mod

        def handler(url, params, auth):
            return FakeHTTPResponse({}, status_code=403, text="Forbidden")

        _patch_httpx(monkeypatch, conf_mod, handler)
        p = self._provider()
        assert p.get_pages(space="ENG") == []

    def test_search_swallows_error_returns_empty(self, atlassian_env, monkeypatch):
        import src.providers.live.confluence as conf_mod

        def handler(url, params, auth):
            return FakeHTTPResponse({}, status_code=500, text="Server error")

        _patch_httpx(monkeypatch, conf_mod, handler)
        p = self._provider()
        assert p.search_pages("auth") == []

    def test_get_decision_logs_swallows_error_returns_empty(self, atlassian_env, monkeypatch):
        import src.providers.live.confluence as conf_mod

        def handler(url, params, auth):
            return FakeHTTPResponse({}, status_code=429, text="Too many requests")

        _patch_httpx(monkeypatch, conf_mod, handler)
        p = self._provider()
        assert p.get_decision_logs(team="Team Phoenix", component="auth") == []

    def test_page_without_labels_yields_empty_tags(self, atlassian_env, monkeypatch):
        import src.providers.live.confluence as conf_mod
        page = _confluence_page(labels=[])

        def handler(url, params, auth):
            return FakeHTTPResponse({"results": [page]})

        _patch_httpx(monkeypatch, conf_mod, handler)
        p = self._provider()
        pages = p.get_pages(space="ENG")
        assert pages[0].tags == []
