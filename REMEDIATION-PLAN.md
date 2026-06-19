# SyncBot Remediation Plan

Hardening pass to make SyncBot easy to use, scalable beyond Slack, understandable,
and safe to refine while testing with a team.

**Out of scope (deliberately):** AI-key enablement (`ANTHROPIC_API_KEY`) and Railway/cloud
hosting. Everything else is in scope.

Status: ✅ done · 🔄 in progress · ⬜ todo

---

## Already fixed earlier this session
- ✅ Duplicate `answer()` that shadowed the project-scoped version (`41e8971`)
- ✅ Silent digest-delivery failure → honest per-channel reporting (`a2d3d37`)
- ✅ Live Jira/Confluence error logging in `_get` (`a2d3d37`)
- ✅ Self-intro event wired into the Slack manifest (`a2d3d37`) — *needs app reinstall*
- ✅ Tightened Slack scopes to least privilege (`a2d3d37`) — *needs app reinstall*
- ✅ Slack-native digest targeting `send <team> digest here` (`6e21d57`)
- ✅ Registry-aware multi-project digest scheduler (`6637a51`)

---

## Phase 0 — Make iteration safe *(foundation; blocks Phases 2–3)*
- ⬜ 0.1 Handler/router test net: golden routing tests for all commands + digest targeting + `answer()`; smoke test for duplicate handler names + non-empty returns
- ⬜ 0.2 Get the suite green: diagnose `test_run_all_golden_count` (expects 12, actual 10) — real regression vs stale fixture
- ⬜ 0.3 Local guard rails: `make check` running pytest + AST lint (no duplicate defs, no silent except in handler files)
- ⬜ 0.4 Reinstall the Slack app to activate the manifest changes *(manual — owner)*

## Phase 1 — Kill the silent-failure class
- ⬜ 1.1 Triage the ~20 remaining swallowed exceptions: log-and-degrade vs annotated-intentional
- ⬜ 1.2 Startup preflight: assert each `provider: live` has its token; friendly error instead of raw `KeyError`

## Phase 2 — Make the router refactor-safe
- ⬜ 2.1 Refactor `handle_query` if/elif ladder → ordered `(name, matcher, handler)` registry (kills substring shadowing)
- ⬜ 2.2 Honest keyword-mode fallback + visible mode indicator in help/intro/status

## Phase 3 — Tenant isolation everywhere
- ⬜ 3.1 Make the MCP server project-aware (not hardwired to `config.yaml`)
- ⬜ 3.2 Enforce entitlement at the tool layer; route Slack/MCP/CLI through one guarded path
- ⬜ 3.3 Scope + index `_load_meeting_notes` (no per-query filesystem glob on the default config)
- ⬜ 3.4 Per-project notification prefs (no team-name collision across clients)

## Phase 4 — Onboarding & usability
- ⬜ 4.1 Single source of truth for provider toggles (config.yaml vs `.env`)
- ⬜ 4.2 Right-size the "no-terminal" / connectors-off claims to match reality
- ⬜ 4.3 Role-framed help (designer / leadership / dev)
- ⬜ 4.4 Surface manifest staleness (`last_verified` age) in digests + `validate`

## Phase 5 — Docs & governance
- ⬜ 5.1 Reconcile tool-count/capability claims (14 vs 20) across README/plugin.json/code
- ⬜ 5.2 Data-flow / security posture doc for the IT approval kit
- ⬜ 5.3 Token-handling hygiene note (rotation plan, `.env` hygiene)
