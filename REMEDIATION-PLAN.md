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

## Phase 0 — Make iteration safe
- ✅ 0.1 Handler/router test net — 28 hermetic golden/smoke tests inc. duplicate-handler guard (`d5232c3`, `352e4d6`)
- ✅ 0.2 Suite green — `test_run_all_golden_count` was a stale time-relative fixture; made drift-robust (`d5232c3`)
- ✅ 0.3 `make check` = `scripts/lint.py` (duplicate-def guard) + full pytest (`748416e`)
- ⬜ 0.4 Reinstall the Slack app to activate the manifest changes *(manual — owner)*

## Phase 1 — Kill the silent-failure class
- ✅ 1.1 Triaged swallowed exceptions: log-and-degrade vs annotated-intentional (`cfb85f2`, `ca7db9a`)
- ✅ 1.2 Startup preflight: clear message when a provider is `live` but its token is missing (`748416e`)

## Phase 2 — Make the router refactor-safe
- ⬜ 2.1 Refactor `handle_query` if/elif ladder → ordered registry *(see "Remaining" below)*
- ✅ 2.2 Honest keyword-mode fallback + visible mode indicator in help/intro (`748416e`)

## Phase 3 — Tenant isolation everywhere
- ✅ 3.1 MCP server project-selectable via `SYNCBOT_CONFIG` (`88c430e`)
- ⬜ 3.2 Enforce entitlement at the tool layer *(see "Remaining" below)*
- ⬜ 3.3 Scope `_load_meeting_notes` to the project *(see "Remaining" below)*
- ⬜ 3.4 Per-project notification prefs *(see "Remaining" below)*

## Phase 4 — Onboarding & usability
- ✅ 4.1 Single source of truth for provider toggles (config.yaml) (`88c430e`)
- ✅ 4.2 Right-sized the "no-terminal" / "automatic" claims (`f304552`)
- ⬜ 4.3 Role-framed help (designer / leadership / dev) *(see "Remaining" below)*
- ✅ 4.4 Surface manifest staleness in digests (`88c430e`)

## Phase 5 — Docs & governance
- ✅ 5.1 Reconciled tool count: 20 coordination tools (22 on MCP) (`f304552`)
- ✅ 5.2 `SECURITY.md` data-flow / posture doc (`f304552`)
- ✅ 5.3 Token-handling hygiene note in SECURITY.md (`f304552`)

---

## Remaining: the "project-aware keyword mode" keystone

Items **2.1, 3.2, 3.3, 3.4, 4.3** are entangled and best done as ONE focused,
well-tested change rather than piecemeal. Why: in keyword mode, `handle_query`
uses module-level engines (default `config.yaml`) and is **not** project-scoped —
so true per-project isolation (3.2/3.3) and per-project prefs (3.4) require
threading a per-project engine bundle (the existing `_project_engines`) through
`handle_query` and the digest/role handlers. Doing 3.4 alone would *break* the
new digest-targeting feature (interactive writes default prefs, scheduler reads
per-project). The router registry (2.1) and role-framed help (4.3) ride on the
same signature change.

Recommended approach: make `handle_query(text, engines)` and `answer(...)`
project-aware, restructure the dispatch into an ordered `(matcher, handler)`
registry as part of it, and add a test that registers a second project and
asserts a query in that channel returns that project's data. The Phase 0 test
net guards the default path during the refactor.

This is the one remaining large rock; everything else in the plan is done.
