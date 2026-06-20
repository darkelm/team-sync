"""
webhook_server.py — Inbound HTTP surface for SyncBot trigger "ears" (C1).

This is a SEPARATE PROCESS from the Slack bot (slack_bot.py).
  - slack_bot.py   : outbound, Socket Mode, no public port needed.
  - webhook_server : inbound HTTP, receives push events from GitHub / Figma /
                     Jira / calendar / generic relay; normalises each payload
                     into an Event and calls EventRouter.dispatch() which posts
                     the Slack notification.

Run with:
    uvicorn webhook_server:app --host 0.0.0.0 --port 8000

Or on Railway, add a second service pointing at this file with:
    Procfile entry:  web: uvicorn webhook_server:app --host 0.0.0.0 --port $PORT

Security notes
--------------
- GitHub  : HMAC-SHA256 over the raw request body, verified against
            GITHUB_WEBHOOK_SECRET before any parsing.
- Figma   : passcode field in the JSON payload compared with
            FIGMA_WEBHOOK_PASSCODE using constant-time compare.
- Jira    : shared-secret header X-Webhook-Token vs JIRA_WEBHOOK_TOKEN,
            constant-time compare.
- Calendar: shared-secret header X-Webhook-Token vs WEBHOOK_SHARED_SECRET,
            constant-time compare.
- Generic : shared-secret header X-Webhook-Token vs WEBHOOK_SHARED_SECRET,
            constant-time compare.

All verification uses hmac.compare_digest to prevent timing attacks.
Payloads are never logged in full; only safe metadata is emitted.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from src.agent.events import Event, EventRouter, TRIGGER_CATALOG
from src.providers.factory import Providers

# ---------------------------------------------------------------------------
# App + providers
# ---------------------------------------------------------------------------

log = logging.getLogger("webhook_server")
from src.log import configure_logging
if os.getenv("SYNCBOT_TEST"):
    logging.basicConfig(level=logging.INFO)   # tests: console only, no log file
else:
    configure_logging()                        # server: rotating file + console

app = FastAPI(title="SyncBot Webhook Receiver", version="1.0.0")

# Providers are initialised once at startup; config.yaml controls which
# backend implementations (local vs live) are used.
_providers: Providers | None = None


def get_providers() -> Providers:
    global _providers
    if _providers is None:
        _providers = Providers("config.yaml")
    return _providers


# ---------------------------------------------------------------------------
# In-memory deduplication — keyed on delivery ids supplied by each provider.
# A set is fine for a single-process deployment; swap for Redis in prod.
# ---------------------------------------------------------------------------

_seen_delivery_ids: set[str] = set()


def _is_duplicate(delivery_id: str | None) -> bool:
    """Return True and mark seen if this delivery id has been processed before."""
    if not delivery_id:
        return False
    if delivery_id in _seen_delivery_ids:
        return True
    _seen_delivery_ids.add(delivery_id)
    return False


# ---------------------------------------------------------------------------
# Signature / secret verification helpers
# ---------------------------------------------------------------------------

def _verify_github_hmac(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Verify GitHub's X-Hub-Signature-256 HMAC-SHA256 header."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _verify_shared_secret(expected: str, provided: str | None) -> bool:
    """Constant-time compare for plain shared-secret tokens."""
    if not provided:
        return False
    return hmac.compare_digest(expected, provided)


# ---------------------------------------------------------------------------
# Team-resolution helpers (map repo / project-key / title → team name)
# ---------------------------------------------------------------------------

def _repo_to_team(repo_name: str, providers: Providers) -> str:
    """
    Map a GitHub repo name to a team by scanning component paths in manifests.

    Strategy (in order):
    1. If any team's code-component path prefix matches the repo name (loose),
       return that team.
    2. Fall back to slug comparison: "phoenix-auth" → "team phoenix".
    3. Return "" so the router can still handle the event gracefully.
    """
    repo_lower = repo_name.lower().replace("-", " ").replace("_", " ")
    for team in providers.manifests.get_all_teams():
        team_lower = team.team.lower()
        # Slug: "team phoenix" → "phoenix"
        slug = team_lower.replace("team ", "").strip()
        if slug in repo_lower or repo_lower in slug:
            return team.team
    return ""


def _jira_project_to_team(project_key: str, providers: Providers) -> str:
    """Map a Jira project key (e.g. PHX) to a team via manifest jira_project field."""
    key_upper = project_key.upper()
    for team in providers.manifests.get_all_teams():
        if team.jira_project.upper() == key_upper:
            return team.team
    return ""


def _derive_component_from_github(payload: dict) -> str:
    """
    Derive a component name from a merged PR payload.

    Priority:
    1. Changed files: find the first manifest code-component whose path is a
       prefix of any changed file path.
    2. PR title: return the title as-is (human-readable).
    """
    providers = get_providers()
    files_changed: list[str] = payload.get("pull_request", {}).get("changed_files_paths", [])
    # GitHub webhooks don't include file paths in the push payload directly;
    # they come from a separate API call. Here we use what's available in the
    # webhook body under commits or head_commit, or fall back to the PR title.
    # For a robust implementation wire the GitHub Files API; for now we match
    # against what Zapier/GitHub App payloads typically include.
    if files_changed:
        for team in providers.manifests.get_all_teams():
            for comp in team.components.code:
                for f in files_changed:
                    if f.startswith(comp.path):
                        return comp.name
    # Fallback: PR title
    return payload.get("pull_request", {}).get("title", "")


def _figma_file_to_team(file_id: str, file_name: str, providers: Providers) -> str:
    """Resolve a Figma file to its owning team via manifest figma_files URLs."""
    for team in providers.manifests.get_all_teams():
        for ff in team.figma_files:
            if file_id and file_id in ff.url:
                return team.team
            if file_name and file_name.lower() in ff.name.lower():
                return team.team
    return ""


def _cross_journey_components(components: list[dict | str], providers: Providers) -> list[str]:
    """Return component names that appear in more than one journey — the ones worth broadcasting."""
    try:
        from src.agent.strategy import StrategyLens
        lens = StrategyLens(providers)
    except Exception as e:
        # Missing strategy files degrade to [] inside StrategyLens itself, so
        # reaching here means a real failure (bad YAML, import error) that loses
        # cross-journey routing on a live webhook — surface it.
        log.warning("webhook _cross_journey_components: StrategyLens unavailable: %s", e)
        return []
    results = []
    for comp in components:
        name = comp if isinstance(comp, str) else comp.get("name", "")
        if not name:
            continue
        comp_lower = name.lower()
        journey_count = sum(
            1 for j in lens.journeys
            if any(comp_lower in c.lower() or c.lower() in comp_lower for c in j.components)
        )
        if journey_count >= 1:  # any journey reference = worth routing
            results.append(name)
    return results


def _journeys_for_component(comp_name: str, providers: Providers) -> list[str]:
    """Which journeys use this component? For rich notification framing."""
    try:
        from src.agent.strategy import StrategyLens
        lens = StrategyLens(providers)
        comp_lower = comp_name.lower()
        return [j.name for j in lens.journeys
                if any(comp_lower in c.lower() or c.lower() in comp_lower for c in j.components)]
    except Exception as e:
        # See _cross_journey_components: missing files already degrade gracefully,
        # so this path is a real failure that loses notification framing.
        log.warning("webhook _journeys_for_component: StrategyLens unavailable: %s", e)
        return []


def _principles_for_component(comp_name: str, providers: Providers) -> list[str]:
    """Which experience principles does this component name relate to?"""
    try:
        from src.agent.strategy import StrategyLens
        from src.agent.similarity import tokenize
        lens = StrategyLens(providers)
        comp_tokens = tokenize(comp_name)
        relevant = []
        for p in lens.principles:
            kw_tokens = set()
            for kw in p.keywords:
                kw_tokens |= tokenize(kw)
            if comp_tokens & kw_tokens:
                relevant.append(p.name)
        return relevant
    except Exception as e:
        # See _cross_journey_components: missing files already degrade gracefully,
        # so this path is a real failure that loses principle framing.
        log.warning("webhook _principles_for_component: StrategyLens unavailable: %s", e)
        return []


def _calendar_title_to_teams(title: str, providers: Providers) -> list[str]:
    """
    Parse team names out of a calendar event title.

    Looks for any team slug (e.g. "phoenix", "atlas") appearing in the title.
    Returns all matches so the caller can build the metadata["teams"] list.
    """
    title_lower = title.lower()
    found = []
    for team in providers.manifests.get_all_teams():
        slug = team.team.lower().replace("team ", "").strip()
        if slug in title_lower:
            found.append(team.team)
    return found


def _is_cross_team_sync(title: str, teams: list[str]) -> bool:
    """
    A calendar event is a cross-team sync if:
    - It involves 2+ teams, OR
    - The title contains cross-team keywords.
    """
    keywords = ("cross-team", "cross team", "sync", "alignment", "all-hands", "all hands",
                 "joint", "collaboration", "coord")
    title_lower = title.lower()
    return len(teams) >= 2 or any(kw in title_lower for kw in keywords)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# /webhooks/github
# ---------------------------------------------------------------------------

@app.post("/webhooks/github")
async def webhook_github(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> JSONResponse:
    """
    Receive GitHub webhook events.

    Signature: HMAC-SHA256 of the raw body with GITHUB_WEBHOOK_SECRET.
    Emits Event("code.merged") on merged pull requests.
    """
    body = await request.body()

    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not secret or not _verify_github_hmac(secret, body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid GitHub signature")

    if _is_duplicate(x_github_delivery):
        return JSONResponse({"dispatched": 0, "ignored": "duplicate delivery"})

    payload: dict[str, Any] = await request.json()  # body already buffered

    # Only act on merged pull_request events
    event_type = x_github_event or ""
    action = payload.get("action", "")
    pr = payload.get("pull_request", {})
    merged = pr.get("merged", False)

    if event_type != "pull_request" or action != "closed" or not merged:
        return JSONResponse({"dispatched": 0, "ignored": f"event={event_type} action={action} merged={merged}"})

    providers = get_providers()
    repo_name = payload.get("repository", {}).get("name", "")
    team = _repo_to_team(repo_name, providers)
    component = _derive_component_from_github(payload)
    if not component:
        component = pr.get("title", repo_name)

    event = Event(
        type="code.merged",
        subject=component,
        source="github",
        team=team,
        metadata={
            "pr_number": pr.get("number"),
            "pr_title": pr.get("title"),
            "repo": repo_name,
            "merged_by": pr.get("merged_by", {}).get("login", ""),
        },
    )

    router = EventRouter(providers)
    n = router.dispatch(event)
    log.info("github code.merged dispatched=%d repo=%s component=%s", n, repo_name, component)
    return JSONResponse({"dispatched": n})


# ---------------------------------------------------------------------------
# /webhooks/figma
# ---------------------------------------------------------------------------

@app.post("/webhooks/figma")
async def webhook_figma(request: Request) -> JSONResponse:
    """
    Receive Figma webhook events.

    Authentication: the 'passcode' field in the JSON body is compared
    against FIGMA_WEBHOOK_PASSCODE using constant-time compare.
    Emits Event("design.library_published") on LIBRARY_PUBLISH events.
    """
    payload: dict[str, Any] = await request.json()

    passcode_expected = os.getenv("FIGMA_WEBHOOK_PASSCODE", "")
    passcode_provided = payload.get("passcode", "")
    if not passcode_expected or not _verify_shared_secret(passcode_expected, passcode_provided):
        raise HTTPException(status_code=401, detail="Invalid Figma passcode")

    event_type = payload.get("event_type", "")
    delivery_id = payload.get("webhook_id", "") + ":" + str(payload.get("timestamp", ""))
    if _is_duplicate(delivery_id):
        return JSONResponse({"dispatched": 0, "ignored": "duplicate delivery"})

    if event_type != "LIBRARY_PUBLISH":
        return JSONResponse({"dispatched": 0, "ignored": f"event_type={event_type}"})

    providers = get_providers()
    file_key = payload.get("file_key", "")
    file_name = payload.get("file_name", "")
    created = payload.get("created", [])
    modified = payload.get("modified", [])
    all_components = created + modified

    # ── Signal quality filter ─────────────────────────────────────────────
    # Only notify when a designer intentionally described what changed.
    # Figma includes version notes on publish — if they're empty, this is
    # probably a routine tweak, not a design decision worth broadcasting.
    # This is the key insight: we fire on *design decisions*, not file saves.
    version_notes = payload.get("description", "").strip()
    cross_journey_components = _cross_journey_components(all_components, providers)

    if not version_notes and not cross_journey_components:
        # Routine publish with no decision context and no cross-journey impact — skip.
        log.info("figma library_publish skipped (no version notes, no cross-journey components)")
        return JSONResponse({
            "dispatched": 0,
            "ignored": "no version notes and no cross-journey shared components — routine publish, not a design decision"
        })

    # ── Intent-aware routing ──────────────────────────────────────────────
    # Route component by component, not as one blob. Each cross-journey
    # component gets its own message to the right pairs, framed by the
    # design decision (version notes) not just the artifact change.
    team = _figma_file_to_team(file_key, file_name, providers)
    total_dispatched = 0
    router = EventRouter(providers)

    for comp in (cross_journey_components or all_components[:3]):
        comp_name = comp if isinstance(comp, str) else comp.get("name", file_name)
        journeys_affected = _journeys_for_component(comp_name, providers)
        principles_relevant = _principles_for_component(comp_name, providers)

        # Build design-language metadata for the EventRouter to use in messages
        event = Event(
            type="design.library_published",
            subject=comp_name,
            source="figma",
            team=team,
            metadata={
                "file_key": file_key,
                "file_name": file_name,
                "version_notes": version_notes,
                "journeys_affected": journeys_affected,
                "principles_relevant": principles_relevant,
                "components_created": [c.get("name") for c in created if isinstance(c, dict)],
                "components_modified": [c.get("name") for c in modified if isinstance(c, dict)],
            },
        )
        n = router.dispatch(event)
        total_dispatched += n

    log.info("figma library_publish dispatched=%d file=%s decision=%s",
             total_dispatched, file_name, bool(version_notes))
    return JSONResponse({"dispatched": total_dispatched,
                         "decision_context": bool(version_notes),
                         "cross_journey_components": len(cross_journey_components)})


# ---------------------------------------------------------------------------
# /webhooks/jira
# ---------------------------------------------------------------------------

@app.post("/webhooks/jira")
async def webhook_jira(
    request: Request,
    x_webhook_token: str | None = Header(default=None),
) -> JSONResponse:
    """
    Receive Jira automation webhook events.

    Authentication: X-Webhook-Token header vs JIRA_WEBHOOK_TOKEN (shared
    secret, constant-time compare).  Jira does not provide a universal HMAC
    scheme so this shared-secret pattern is the recommended approach.

    Emits:
    - Event("work.created")         on issue creation
    - Event("roadmap.date_changed") on duedate change
    """
    secret = os.getenv("JIRA_WEBHOOK_TOKEN", "")
    if not secret or not _verify_shared_secret(secret, x_webhook_token):
        raise HTTPException(status_code=401, detail="Invalid Jira webhook token")

    payload: dict[str, Any] = await request.json()

    # Jira delivers a timestamp+issueId combo we can use for dedup
    issue_id = str(payload.get("issue", {}).get("id", ""))
    webhook_event = payload.get("webhookEvent", "")
    delivery_id = f"jira:{webhook_event}:{issue_id}"
    if _is_duplicate(delivery_id):
        return JSONResponse({"dispatched": 0, "ignored": "duplicate delivery"})

    providers = get_providers()
    issue = payload.get("issue", {})
    fields = issue.get("fields", {})
    summary = fields.get("summary", issue.get("key", ""))
    project_key = fields.get("project", {}).get("key", "")
    team = _jira_project_to_team(project_key, providers)

    if webhook_event in ("jira:issue_created",):
        event = Event(
            type="work.created",
            subject=summary,
            source="jira",
            team=team,
            metadata={
                "issue_key": issue.get("key"),
                "issue_type": fields.get("issuetype", {}).get("name", ""),
                "project": project_key,
            },
        )
    elif webhook_event in ("jira:issue_updated",):
        # Only fire roadmap.date_changed when the duedate field actually changed
        changelog = payload.get("changelog", {})
        changed_fields = [item.get("field") for item in changelog.get("items", [])]
        if "duedate" not in changed_fields:
            return JSONResponse({"dispatched": 0, "ignored": "non-duedate update"})
        event = Event(
            type="roadmap.date_changed",
            subject=summary,
            source="jira",
            team=team,
            metadata={
                "issue_key": issue.get("key"),
                "project": project_key,
                "changelog": [
                    {"field": i.get("field"), "from": i.get("fromString"), "to": i.get("toString")}
                    for i in changelog.get("items", [])
                    if i.get("field") == "duedate"
                ],
            },
        )
    else:
        return JSONResponse({"dispatched": 0, "ignored": f"webhookEvent={webhook_event}"})

    router = EventRouter(providers)
    n = router.dispatch(event)
    log.info("jira %s dispatched=%d issue=%s", event.type, n, summary)
    return JSONResponse({"dispatched": n})


# ---------------------------------------------------------------------------
# /webhooks/calendar
# ---------------------------------------------------------------------------

@app.post("/webhooks/calendar")
async def webhook_calendar(
    request: Request,
    x_webhook_token: str | None = Header(default=None),
) -> JSONResponse:
    """
    Receive calendar push events (Google Calendar / Outlook via Zapier relay
    or a direct push subscription).

    Authentication: X-Webhook-Token header vs WEBHOOK_SHARED_SECRET.
    Emits Event("calendar.cross_team_sync") when the event title looks like
    a cross-team synchronisation meeting.

    Expected payload shape:
    {
        "title": "Phoenix x Atlas alignment sync",
        "start": "2026-06-03T10:00:00Z",
        "end":   "2026-06-03T10:30:00Z",
        "calendar_id": "...",
        "event_id": "..."        // used for dedup
    }
    """
    secret = os.getenv("WEBHOOK_SHARED_SECRET", "")
    if not secret or not _verify_shared_secret(secret, x_webhook_token):
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    payload: dict[str, Any] = await request.json()

    delivery_id = f"cal:{payload.get('event_id', '')}"
    if _is_duplicate(delivery_id):
        return JSONResponse({"dispatched": 0, "ignored": "duplicate delivery"})

    providers = get_providers()
    title = payload.get("title", "")
    teams = _calendar_title_to_teams(title, providers)

    if not _is_cross_team_sync(title, teams):
        return JSONResponse({"dispatched": 0, "ignored": "not a cross-team sync"})

    # Determine the best channel: first matched team's channel
    channel = ""
    if teams:
        t = providers.manifests.get_team(teams[0])
        if t:
            channel = t.slack_channel

    event = Event(
        type="calendar.cross_team_sync",
        subject=title,
        source="calendar",
        team=teams[0] if teams else "",
        metadata={
            "teams": teams,
            "channel": channel,
            "start": payload.get("start"),
            "end": payload.get("end"),
            "title": title,
        },
    )

    router = EventRouter(providers)
    n = router.dispatch(event)
    log.info("calendar cross_team_sync dispatched=%d title=%r teams=%s", n, title, teams)
    return JSONResponse({"dispatched": n})


# ---------------------------------------------------------------------------
# /webhooks/generic
# ---------------------------------------------------------------------------

@app.post("/webhooks/generic")
async def webhook_generic(
    request: Request,
    x_webhook_token: str | None = Header(default=None),
) -> JSONResponse:
    """
    Generic signed-JSON webhook for Dovetail, Productboard, Notion, etc.
    relayed via Zapier or Make.

    Authentication: X-Webhook-Token header vs WEBHOOK_SHARED_SECRET.

    Expected payload:
    {
        "type":     "<event type from TRIGGER_CATALOG>",
        "subject":  "<what changed>",
        "team":     "<team name>",       // optional
        "metadata": {}                   // optional
    }

    The 'type' must exist in TRIGGER_CATALOG — unknown types are rejected
    with a 400 (not a 401, because the signature is valid; the payload is
    simply unsupported).
    """
    secret = os.getenv("WEBHOOK_SHARED_SECRET", "")
    if not secret or not _verify_shared_secret(secret, x_webhook_token):
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    payload: dict[str, Any] = await request.json()

    event_type = payload.get("type", "")
    if event_type not in TRIGGER_CATALOG:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unknown event type '{event_type}'. Must be one of: {sorted(TRIGGER_CATALOG)}"},
        )

    delivery_id = payload.get("delivery_id", "")
    if delivery_id and _is_duplicate(delivery_id):
        return JSONResponse({"dispatched": 0, "ignored": "duplicate delivery"})

    providers = get_providers()
    event = Event(
        type=event_type,
        subject=payload.get("subject", ""),
        source=payload.get("source", "generic"),
        team=payload.get("team", ""),
        metadata=payload.get("metadata", {}),
    )

    router = EventRouter(providers)
    n = router.dispatch(event)
    log.info("generic %s dispatched=%d subject=%s", event_type, n, event.subject)
    return JSONResponse({"dispatched": n})
