# Deployment Compliance — three gates for an enterprise security review

For an enterprise (e.g. Accenture) IT / security review where third-party MCPs and
marketplace plugins are likely **restricted**, and external LLM egress **may be**
restricted. This brief exists to unblock a deployment by separating the risk
decisions that are usually collapsed into one reflexive "no."

> **What this document does and does not claim.** It does **not** assert any
> specific Accenture (or other enterprise) policy — we don't know it. It states,
> per deployment mode, **what each mode asks of your security team**, so the right
> people can make the right call. Every behavioral claim below is grounded in the
> code (paths cited) and tagged `[V]` (verified in code/docs) or `[S]` (our
> synthesis). For *what crosses the boundary* in each mode see
> [SECURITY.md](../SECURITY.md); this doc is the **approval-decision** companion to it.

---

## 1. The three gates (do not collapse them)

The single most common review error is treating "an AI tool with MCP" as one yes/no.
team-sync deliberately separates **three independent risk decisions**. Each can be
answered differently; turning one off does not force the others off.

| Gate | What it is | Default in team-sync | Scrutiny | If your security team says no |
|---|---|---|---|---|
| **A. Third-party vendor MCP / marketplace plugin** | A *remote, vendor-hosted* connector with broad OAuth into your tenant — e.g. the **Atlassian Rovo Remote MCP** (72+ tools, OAuth 2.1, read **and** write). [V] `docs/atlassian-mcp.md` | **OFF.** Optional, AI-mode-only, scaffolded seam — a complete no-op with no config. [V] `src/agent/syncbot.py` `atlassian_mcp_enabled()` | **Highest.** Vendor-hosted, broad tenant OAuth, write-capable. | Leave it off (the default). Nothing else changes — the app runs fully without it. |
| **B. team-sync as a self-hosted app** | **Your own** application: scoped provider API tokens + inbound webhooks, data under your control on a host you run (e.g. a single Railway worker). No vendor sits in the path. [V] `src/providers/live/*` | This is the deployment target. | **Moderate** — a normal "self-hosted app calling APIs with service-account tokens" review, not a marketplace-plugin review. | This is the gate to actually negotiate. It is a *different, more-approvable* posture than Gate A — see §2/§3. |
| **C. External LLM egress (AI mode)** | The optional Claude/Anthropic API call that powers the natural-language front-end. Gated **solely** on `ANTHROPIC_API_KEY`. [V] `src/agent/syncbot.py:84-87`, `src/agent/ai_enhance.py:22` | **OFF** unless a key is set. | **Separate** data-classification decision (does this data class get to leave to a third-party model?). | Run **keyword mode** — no key, **zero** external AI calls. The deterministic core (§2) still works. |

**Why this matters `[S]`:** a blanket "no third-party AI" is usually really a *Gate A*
or *Gate C* concern. Neither forces Gate B off. team-sync's most-approvable
configuration — **self-hosted app (B) + keyword mode (C off) + Rovo MCP off (A off)**
— is a conventional internal app reading your own systems with scoped tokens, with
**no** external AI and **no** vendor connector. Start the conversation there.

---

## 2. The keyword-only, zero-egress posture (what works with no key, no MCP)

With **no `ANTHROPIC_API_KEY`** (Gate C off) **and** the Rovo MCP off (Gate A off),
team-sync runs as a **deterministic, pure-code product**. The AI layer is an
*enhancement on top of* engines that already work — not a dependency of them.
[V] `src/agent/syncbot.py` docstring ("the Slack bot falls back to keyword matching"),
`src/agent/health.py` ("AI-optional… the structure is identical with or without a key").

What works with **zero external AI calls** (all in `src/agent/`):

- **Routing / keyword answering** — deterministic question→tool dispatch (the keyword fallback in the Slack bot).
- **Digests & briefings** — `digest.py`, `briefing.py`.
- **The full governance membrane** — `membrane.py`: the **reach / floor / tier / lanes** model (lanes `blocked` / `review` / `digest` / `auto` / `propose`), with **append-only provenance** written by `provenance.py`. The membrane is **pure** (no IO, no network). [V]
- **Who-owns / dependency maps / conflict prediction** — `detector.py`, `discovery.py`, `tools.py`.
- **Up-to-speed / onboarding answers** — `briefing.py`, `audience.py`.
- **Manifest health (`doctor`)** — `manifest_health.py` `check_manifests`: pure, read-only graph self-check. [V]
- **Freshness / staleness, strategic alignment, findability, similarity/reuse** — `freshness.py`, `alignment.py`, `findability.py`, `similarity.py`.

**Plain statement `[S]`:** the **majority of team-sync's utility needs no external
AI at all.** AI mode adds a natural-language front-end and a quality-lift on
extraction (`ai_enhance.py`); it does not gate the coordination engines. An
enterprise that forbids external LLM egress for this data class still gets the
governance membrane, ownership graph, drift/conflict detection, digests, briefings,
and manifest health — deterministically.

---

## 3. Data-flow / egress map per mode

What actually leaves your perimeter, smallest blast radius first. (See
[SECURITY.md › What leaves the environment, by mode](../SECURITY.md#what-leaves-the-environment-by-mode)
for the credential-level detail.)

| Configuration | External AI egress | Provider egress | What leaves your perimeter |
|---|---|---|---|
| **Keyword + local/imported providers** (all providers `local`, no key) | **None** | **None** | **Nothing.** Runs entirely off `data/synthetic/` or `data/imported/` (your own exports). No outbound network call for its core function. [V] `src/providers/factory.py` defaults every provider to `local` |
| **Keyword + live REST providers** (some providers `live`, no key) | **None** | Outbound to the vendors you enable, **only** | Scoped REST calls **to those vendors' own clouds**: Jira/Confluence → `{ATLASSIAN_URL}/rest/api/3` and `/wiki/rest/api`; GitHub → `api.github.com`; Slack/Figma APIs. Plain `httpx` + scoped account tokens — **not** a vendor MCP. [V] `src/providers/live/{jira,confluence,github}.py` |
| **AI mode** (`ANTHROPIC_API_KEY` set) | **Adds Anthropic API** | (as configured above) | The above **plus**: the user's query and the *retrieved, structured coordination data* used to answer it (owners, tickets, decisions, drift findings) sent to the Anthropic API. The engines still run **locally**; only the reasoning round-trip's inputs/outputs cross. [V] `SECURITY.md › AI mode` |
| **+ Atlassian Rovo MCP on** (Gate A) | (Anthropic, as above) | **Adds a vendor-hosted OAuth connector** | A remote, Atlassian-hosted MCP endpoint reachable by the model under **OAuth 2.1**, scoped to the connected service account's permissions (bot reach == that account's reach). Per `docs/atlassian-mcp.md`, the connector descriptor carries the **OAuth token + tool metadata** to Anthropic's Messages API; it is a tool *source* for the model, gated read-only by default and routed through the membrane's `review` lane before any write. **Off by default; write-loop not yet wired.** [V] `docs/atlassian-mcp.md`, `src/agent/syncbot.py` |

**The two switches are independent `[V]` (`SECURITY.md`):** turning on live providers
(Gate B detail) does not turn on Anthropic egress (Gate C); setting the key does not
require live providers. You can run AI mode entirely against local/imported data, or
run live providers with no AI at all.

---

## 4. Provenance durability (set the path to a persistent volume)

The governance membrane writes an **append-only audit log** — one
`ProvenanceRecord` per routing decision (proposer, lane, decider, reach,
floor-pass, timestamp) — so you can answer "who decided this, and when" weeks
later. [V] `src/agent/provenance.py`

The store path defaults to **`data/provenance.jsonl`** and is **overridable** via the
`SYNCBOT_PROVENANCE_PATH` environment variable. [V] `src/agent/provenance.py:24-50`

> ⚠️ **On an ephemeral host (e.g. Railway without a mounted volume) the audit trail
> is lost on every redeploy.** [V] (`provenance.py` module docstring). For any
> deployment where the audit log matters for compliance, set
> `SYNCBOT_PROVENANCE_PATH` to a path on a **mounted persistent volume**
> (e.g. `/data/provenance.jsonl` on a Railway volume) so the log survives redeploys.

This is a one-line deploy-config decision, but a compliance-relevant one: without
it, the "never just the loop decided" guarantee has no durable evidence after a
restart.

---

## 5. The manifest is the asset — keep it fresh

The `team.yaml` manifests are team-sync's differentiated IP: the dependency graph
beneath **every** answer (who-owns, dependency maps, conflict prediction, digests).
"The team.yaml manifests *are* team-sync's moat… every answer is only as good as the
graph beneath it." [V] `src/agent/manifest_health.py` module docstring.

A stale graph degrades the product quietly — confident answers built on rotted data.
The maintenance tool for this is the **manifest-health / `doctor`** check
(`src/agent/manifest_health.py` `check_manifests`): a pure, read-only self-check that
flags dangling dependencies, orphan components, missing fields, self-dependencies,
and stale/unverified manifests. [V]

**Operational note `[S]`:** treat manifest freshness as a recurring maintenance task,
not a one-time setup. Run `doctor` regularly (and after any team reorg or large
merge) and act on `error`/`warn` findings — the asset only holds its value if it
stays trustworthy. (The `doctor` command surface is being finalized; reference it as
the manifest-health maintenance tool.)

---

## 6. What to confirm with your security team — checklist

Take these four questions to the review. They map 1:1 to the gates above and let
each be answered on its own merits.

- [ ] **Self-hosted app (Gate B):** Can we run **our own** application that reads
      Jira / Confluence / GitHub / Figma / Slack via **scoped service-account API
      tokens** and receives **inbound webhooks**, hosted on a worker we control
      (e.g. Railway), with data staying in our `data/` tree? *(This is a
      conventional internal-app review — not a marketplace-plugin one.)*
- [ ] **External LLM egress (Gate C):** Is sending this data class (queries +
      retrieved coordination data: owners, tickets, decisions, drift) to the
      **Anthropic API** permitted? If **no** → we run **keyword mode** (no key,
      zero external AI calls) and still get the deterministic core (§2).
- [ ] **Third-party vendor MCP (Gate A):** Is the **Atlassian Rovo Remote MCP** on
      any approved allow-list for our tenant? If not approved → it stays **off**
      (the default); nothing else is affected. If approved → start **read-only**
      and keep writes behind the membrane `review` lane.
- [ ] **Provenance storage (§4):** Where will the append-only audit log live? Set
      `SYNCBOT_PROVENANCE_PATH` to a **mounted persistent volume** if the audit
      trail must survive redeploys.

Pair this with the **[SECURITY.md › Before a client engagement checklist](../SECURITY.md#before-a-client-engagement--checklist)**
(token rotation, least-privilege service accounts, secret store / vault, webhook
secret uniqueness) — that checklist covers the *credential hygiene*; this one covers
the *deployment-mode approval decision*.

> **House conventions reminder `[S]`:** no client-confidential data in shared/IP
> repos — the shipped data is synthetic (`data/synthetic/`); a client's own
> exports land in `data/imported/` and stay on the host. Surface gaps for a human;
> do not auto-resolve a policy question on the client's behalf.
