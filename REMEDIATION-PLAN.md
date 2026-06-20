# SyncBot Remediation Plan

Hardening pass to make SyncBot easy to use, scalable beyond Slack, understandable,
and safe to refine while testing with a team.

**Out of scope (deliberately):** AI-key enablement (`ANTHROPIC_API_KEY`) and Railway/cloud
hosting. Everything else is in scope.

Status: ✅ done · ⏸️ deferred (with rationale) · ⬜ todo (manual)

---

## Already fixed earlier this session
- ✅ Duplicate `answer()` shadowing fix (`41e8971`)
- ✅ Silent digest-delivery failure → honest reporting (`a2d3d37`)
- ✅ Live Jira/Confluence error logging (`a2d3d37`)
- ✅ Self-intro event + tightened Slack scopes in manifest (`a2d3d37`) — *needs app reinstall*
- ✅ Slack-native digest targeting (`6e21d57`)
- ✅ Registry-aware multi-project digest scheduler (`6637a51`)

## Phase 0 — Make iteration safe
- ✅ 0.1 Router test net — 30 hermetic tests inc. duplicate-handler + project-isolation guards
- ✅ 0.2 Suite green (stale time-relative fixture made drift-robust)
- ✅ 0.3 `make check` = duplicate-def lint + full pytest
- ⬜ 0.4 Reinstall the Slack app to activate manifest changes *(manual — owner)*

## Phase 1 — Kill the silent-failure class
- ✅ 1.1 Swallowed exceptions → log-and-degrade / annotated-intentional
- ✅ 1.2 Startup preflight for live providers missing tokens

## Phase 2 — Make the router refactor-safe
- ✅ 2.1 (core) `handle_query` is engine-parametrized + guarded by the test net & lint.
  ⏸️ Converting the if/elif ladder to an ordered `(matcher, handler)` registry is
  deferred as optional polish — the test net + duplicate-def lint already prevent
  the shadowing failure mode this was meant to address.
- ✅ 2.2 Honest keyword-mode fallback + visible mode indicator

## Phase 3 — Tenant isolation everywhere
- ✅ 3.1 MCP server project-selectable via `SYNCBOT_CONFIG`
- ✅ 3.2 Keyword queries scoped to the channel's project (per-project engine bundle)
- ✅ 3.3 `_load_meeting_notes` scoped to the project's teams_dir
- ⏸️ 3.4 Per-project notification prefs — deferred. Prefs are global today (keyed by
  team name), which is consistent across the interactive + scheduler paths and works
  fine for a single engagement. Only matters once two projects share a team *name*;
  implementing it now would churn the digest-targeting feature + its tests for an
  edge case. Revisit when a second client project is onboarded.

## Phase 4 — Onboarding & usability
- ✅ 4.1 Single source of truth for provider toggles (config.yaml)
- ✅ 4.2 Right-sized the "no-terminal" / "automatic" claims
- ✅ 4.3 Role-framed help (designer / PM / lead / dev)
- ✅ 4.4 Manifest staleness surfaced in digests

## Phase 5 — Docs & governance
- ✅ 5.1 Tool count reconciled (20 coordination, 22 on MCP)
- ✅ 5.2 `SECURITY.md` data-flow / posture doc
- ✅ 5.3 Token-handling hygiene note

---

**Net (Phase 0–5):** every plan item is done except 0.4 (your manual Slack reinstall)
and one deferral (3.4 per-project prefs, rationale above).

---

## Hardening pass 2 (strong / scalable / modular / performant)

- ✅ CI gate: CI now runs the duplicate-def guard (`scripts/lint.py`) + `ruff check
  src/ tests/`; `make check` mirrors it. All 51 ruff findings cleared.
- ✅ Perf: per-project engine bundle memoized; local providers (jira/confluence/
  figma/github) read disk once.
- ✅ Coverage: AI-agent path tested (mocked Anthropic) + tool-dispatch tests;
  deterministic frozen-time cross-team-PR detector test.
- ✅ Modular (2.1): `slack_bot.py` god-file (1005 → 409 lines) split into
  `bootstrap.py` (engine state) + `router.py` (keyword brain), one-directional
  layering, no circular imports. (The keyword router is now isolated/testable; an
  ordered matcher-registry inside it remains optional polish.)
- ✅ Docs: SECURITY.md secret inventory + rotation runbook + pre-engagement checklist.
- ⏸️ Type checking (mypy): recommended; blocked in the offline sandbox (can't
  install/verify). Add a lenient mypy config + CI step in a connected env.
- ⏸️ Structured logging: deferred to pair with hosting (its payoff is prod
  observability; `print(..., flush=True)` is fine for the local process).

Suite: **429 tests, hermetic, green.** `make check` + CI gate it.

---

## Phase T — Trust & Fit (from the deep-research review)

Addresses the research's make-or-break themes: freshness, answer trust, alert
noise, design-surface fit, and measurement.

- ✅ **Freshness engine** (`freshness.py`) — decay score per team (fresh/aging/stale/unverified).
- ✅ **Answer stamping + confidence** — who-owns / up-to-speed answers carry a freshness line; low-confidence matches say so.
- ✅ **`mark <team> stale` + `stats`** (`instrumentation.py`) — turn "this is wrong" into a signal freshness respects; misses + flagged teams roll up as one iteration backlog.
- ✅ **Notification discipline** — a proactive alert fires only when cross-team + high-confidence + actionable + fresh; per-alert dedup.
- ✅ **Figma-native ingestion** — `get_dev_status` / `get_open_comments` / `get_recent_changes` (ready-for-dev, comments, version changes) + `@syncbot dev status for <team>`. Live activates on a Figma token.
- ✅ **Tenancy migration spec** — `docs/TENANCY.md` (one-tenant-per-client), documented as deferred future work.

Suite: **472 tests, hermetic, green.**

### Parked (owner's call)
- Railway hosting (the research's #1 pilot prerequisite) — deferred.
- AI / natural-language mode (Anthropic key) — deferred.
- mypy gate — config added, not wired (needs a connected env to verify).
- One-tenant-per-client migration — spec'd, not built.
- Structured logging — pairs with hosting.
