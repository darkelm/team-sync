# TENANCY.md — Multi-project today, one-tenant-per-client tomorrow

> **Status: DEFERRED decision / future work — NOT yet scheduled.**
> This is a design spec for a migration the owner has explicitly *deferred*.
> Nothing here is implemented or in flight. It exists so the move from the
> current **multi-project-in-one-workspace** model to a **one-tenant-per-client**
> model is documented and ready to execute when a real client engagement
> requires it. It does **not** authorize or schedule any code change.
>
> Trigger to revisit: the first concurrent engagement where two clients' data
> would otherwise share one process, one operator, or one Slack workspace.

---

## 1. Current model — multi-project in one workspace

SyncBot today runs **one process** that can serve **multiple engagements at
once**, isolating them by Slack channel. The isolation primitive is the
`ProjectRegistry` in [`src/projects.py`](../src/projects.py).

### How it works

- **Channel → project routing.** `ProjectRegistry.for_channel(channel_id,
  channel_name)` walks the registered `Project` list and returns the first whose
  `matches_channel()` hits — by exact channel id/name or by regex
  `channel_patterns`. No match falls back to a synthetic `default` project bound
  to `config.yaml`. The registry is loaded from `data/project_registry.json`
  (absent today, so everything currently resolves to `default`).
- **Per-project provider bundles.** Each `Project` carries its own `config` path
  (e.g. `config-google.yaml`, `config-workday-redesign-employee-expe.yaml`).
  `Project.providers()` lazily constructs and caches a `Providers(self.config)`
  instance. Because `Providers` reads `data.teams_dir` from that config
  (`./data/google-gen-ai/teams` vs `./data/synthetic/teams`), each project reads
  a **different data tree**. A Google query never touches Workday's manifests.
- **Per-project engine cache.** [`bootstrap.py`](../bootstrap.py)
  `_project_engines(channel_id, channel_name)` resolves the project, then builds
  (once, memoized in `_ENGINE_CACHE` keyed by config path) the full bundle of
  ~11 engines (`DriftDetector`, `DigestGenerator`, `BriefingGenerator`,
  `CollaboratorDiscovery`, `ReuseRadar`, `AlignmentChecker`,
  `FindabilityLocator`, `HealthAssessor`, `StrategyLens`, `EventRouter`) all
  wired to that project's `Providers`.
- **Project-scoped keyword queries.** `router.py`'s `handle_query()` runs against
  *whatever bundle it is handed*. Slack channel handlers pass the per-project
  bundle from `_project_engines()`, so even the no-LLM keyword path stays scoped
  to the channel's project. Unmatched queries are logged with their `project`
  tag (`data/unmatched_queries.jsonl`).
- **Per-project state files.** `Project.state_path(filename)` namespaces state
  under `data/<slug>/…` (slug derived from the project name), so snapshots,
  prefs, etc. don't collide across engagements.

### Where isolation IS enforced

- **The interactive Slack path only.** Every inbound Slack message is mapped to a
  project via `for_channel(...)` and answered using **only that project's**
  providers, engines, and data tree. This is the one surface where the *caller's
  identity (the channel)* selects the tenant automatically.

### Where isolation is NOT enforced

- **MCP server — config-selected, not per-caller-authorized.**
  [`mcp_server.py`](../mcp_server.py) constructs a single global
  `Providers(SYNCBOT_CONFIG)` at startup (`SYNCBOT_CONFIG` defaults to
  `config.yaml`) and every tool runs against that one bundle. The server is
  *selectable* per project (point `SYNCBOT_CONFIG` at `config-<client>.yaml`) but
  there is **no per-call tenant check** — whoever can reach the server gets that
  project's full data. There is no `ProjectRegistry` lookup in the MCP path.
- **CLI — config-selected, not per-caller-authorized.** `bootstrap.py`'s
  module-level default engines (`providers`, `detector`, …) are built from
  `config.yaml`; the CLI answers against whatever config it is pointed at. Same
  property as MCP: the *operator* chooses the tenant, the *caller* is not
  authorized per request.

> Current mitigation (per [SECURITY.md](../SECURITY.md#multi-tenant--project-isolation)):
> for any engagement using MCP or the CLI, run **one server / CLI context per
> engagement** (separate config, separate process) rather than relying on
> in-process isolation. That mitigation is exactly what the target architecture
> makes the *only* mode of operation.

---

## 2. Why migrate

The current model is **multi-tenant within one process and one Slack
workspace**. The general failure mode of any multi-tenant system applies: **the
isolation is only as strong as its weakest enforced boundary, and one flaw
exposes every tenant at once.** Concretely for SyncBot —

- A routing bug, a mis-scoped `channel_pattern`, or a missing registry entry
  silently falls back to the `default` project rather than failing closed.
- The MCP/CLI surfaces don't carry the channel-based isolation at all; a single
  mis-set `SYNCBOT_CONFIG`, or any caller who can reach the process, reads
  whatever tenant that process is pointed at.
- One operator's machine (or one Railway worker) holds **all** clients' imported
  data in a single `data/` tree and **all** clients' live-provider credentials in
  a single `.env`. A leak of that one secret set, or that one host, is a leak of
  every client simultaneously.
- Cross-client contamination is a *blast-radius* problem, not just a bug: for
  consulting engagements, two clients' data sharing one process is frequently a
  contractual / data-classification non-starter regardless of whether a bug ever
  fires.

**When it matters vs when it doesn't:**

| Context | Multi-project-in-one-workspace acceptable? |
|---|---|
| Internal demos, the synthetic org, a single pilot | **Yes** — convenient, low risk, no real cross-client data |
| One real client, one workspace, one operator | Yes, with the per-context discipline already documented |
| **Two or more real client engagements running concurrently** | **No** — migrate to one tenant per client |
| Any client whose data classification forbids co-residency | **No** — migrate (or run a dedicated context) before onboarding |

The point of the migration is to make "run one isolated context per client" the
**enforced architecture**, not an operator convention that a single mistake can
defeat.

---

## 3. Target architecture — one isolated deployment per client

**One tenant = one client engagement = one fully isolated deployment.** There is
no shared process and no shared registry; isolation is a deployment boundary, not
an in-process lookup.

A "tenant" boundary owns, end to end and shared with no other tenant:

| Dimension | Per-tenant isolation |
|---|---|
| **Config** | One `config.yaml` (the only config the deploy knows about). No `config-<client>.yaml` selection, because there is nothing to select. |
| **Secrets / `.env`** | One secret set, scoped to that client's providers only — its own Atlassian/GitHub/Figma/Slack tokens, its own (optional) `ANTHROPIC_API_KEY`, its own webhook secrets. A leak exposes one client. |
| **Data dir** | One `data/` tree (imported exports, snapshots, prefs, unmatched-query log). Never shared. |
| **Slack app / workspace** | One Slack app install in the client's own workspace. No cross-workspace channel routing. |
| **Process** | One running process (Railway service / container / worker), with its own webhook receiver. No co-tenancy. |

The boundary is the **deployment**: separate host environment (or at minimum
separate process + separate secret injection + separate data volume) per client.
In this model the channel→project router is unnecessary — every message in the
deploy already belongs to the one tenant. MCP and CLI inherit isolation for free,
because the process they run in only ever holds one tenant's data and
credentials. The "weakest boundary" concern collapses: there is no in-process
boundary left to be the weak link.

---

## 4. Migration path

Effort estimates are rough order-of-magnitude for one engineer, assuming the
synthetic/single-tenant happy path already works.

| # | Step | What changes (code / config) | Rough effort |
|---|---|---|---|
| 1 | **Define the per-client deployment bundle** | Decide the unit of a tenant deploy: one repo deploy (or branch/dir) per client carrying exactly one `config.yaml`, one `.env`, one `data/` volume, one Slack app. Document it (deploy template + Railway/host service-per-client convention). No app-logic change; mostly DEPLOY.md + a template. | S (~0.5 day) |
| 2 | **Single-project-per-deploy: retire the shared `ProjectRegistry` from the hot path** | In `bootstrap.py`, build one engine bundle from `config.yaml` and serve it for *all* messages; drop `_project_engines()`/`_ENGINE_CACHE` channel resolution and the `project_registry` import. In `src/projects.py`, keep the `Project`/`state_path` helpers if useful but stop using `for_channel()` to pick a tenant. In `router.py` / `slack_bot.py`, remove the per-channel bundle plumbing and pass the single bundle. Channel handling becomes "is this a channel I serve?" not "which tenant is this?". | M (~1–2 days) |
| 3 | **Secret isolation** | Enforce one secret set per deploy: secrets injected by the host (Railway service vars / vault), never a shared `.env`; per-client tokens scoped to that client's providers only; per-client webhook secrets. Aligns with the existing [SECURITY.md "Before a client engagement" checklist](../SECURITY.md#before-a-client-engagement--checklist). Mostly process/config, plus removing any code that reaches for a non-tenant config. | S–M (~1 day) |
| 4 | **MCP scoping** | With one config per deploy, `mcp_server.py`'s `Providers(SYNCBOT_CONFIG)` already resolves to the single tenant. Harden: drop the `SYNCBOT_CONFIG` *fallback-to-`config.yaml`* convenience that implies selectability, fail fast if the tenant config is missing, and document that one MCP server == one tenant. (Per-caller authZ remains out of scope — see §5.) | S (~0.5 day) |
| 5 | **CLI scoping** | Same property: the CLI runs inside a tenant deploy and uses that deploy's `config.yaml`. Remove or hard-pin any "point me at any config" affordance that could cross tenants; make the active config explicit at startup. | S (~0.5 day) |
| 6 | **Data-tree convergence** | Collapse per-project `data/<slug>/…` namespacing to a single tenant `data/` tree (no slug layer needed). Migrate any existing per-project state into the right per-tenant deploy. One-time data move + small `state_path` simplification. | S (~0.5 day) |
| 7 | **Provisioning + teardown runbook** | Document/script "stand up a new client tenant" (clone deploy template, mint scoped secrets, install Slack app, seed `data/`) and "tear down" (revoke secrets, delete volume). Make onboarding a checklist, not tribal knowledge. | S–M (~1 day) |
| 8 | **Update docs** | Reconcile [SECURITY.md](../SECURITY.md) (the "MCP/CLI isolation is a tracked hardening item" note becomes "closed — one tenant per deploy"), README, DEPLOY.md, and this file's status header. | S (~0.5 day) |

**Total rough effort:** ~5–7 engineer-days, dominated by step 2 (untangling the
per-channel bundle plumbing) and the per-client provisioning runbook (step 7).
Lower if `ProjectRegistry` is kept as a dormant helper rather than fully removed.

**Backward compatibility:** the synthetic/demo single-config path is already the
common case, so internal/demo usage is unaffected. Existing live engagements
already follow the "one context per engagement" mitigation, so migrating them is
a deploy split, not a behavior change.

---

## 5. Explicitly out of scope

This migration is **deployment-level tenant isolation for a consultancy IP tool**
— not a product-grade multi-tenant SaaS control plane. The following are
**deliberately excluded**:

- **SSO / federated identity** for SyncBot itself.
- **RBAC / per-caller authorization** inside a tenant (who-can-call-which-tool).
  Isolation here is *between* clients via separate deploys, not *within* a client
  via roles. The Slack scopes and provider-account least-privilege already
  defined in [SECURITY.md](../SECURITY.md) remain the access model.
- **SOC 2 / formal compliance certification**, audit logging pipelines,
  tenant-aware billing, or a self-serve tenant-provisioning UI.
- **A shared multi-tenant backend with row-level isolation** — that is the
  *opposite* direction (more shared infrastructure); this spec moves toward
  *less* shared infrastructure on purpose.

Those belong to a product company building a hosted multi-tenant service. SyncBot
is consultancy IP run per engagement; the right isolation primitive is a separate
deployment, which is cheaper and strictly safer than building tenant-aware
authorization into one shared process.

---

## Appendix — file map (current code referenced above)

- [`src/projects.py`](../src/projects.py) — `Project`, `ProjectRegistry`, `for_channel()`, `state_path()`.
- [`bootstrap.py`](../bootstrap.py) — `_project_engines()`, `_ENGINE_CACHE`, default engine bundle, `project_registry`.
- [`mcp_server.py`](../mcp_server.py) — global `Providers(SYNCBOT_CONFIG)`; project-selectable, not per-caller-scoped.
- [`router.py`](../router.py) — `handle_query()` runs against whatever bundle it is handed (per-project on the Slack path).
- [`src/providers/factory.py`](../src/providers/factory.py) — `Providers(config_path)`; `data.teams_dir` is the per-config data boundary.
- [`SECURITY.md`](../SECURITY.md) — "Multi-tenant / project isolation" note (Slack enforced; MCP/CLI tracked hardening item).
- Config examples: `config.yaml`, `config-google.yaml`, `config-workday-redesign-employee-expe.yaml`.
