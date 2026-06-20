# Live Figma Ingestion & the `propose` Lane — Execution Plan

Status: **drafted, parked.** This is the plan to close the last two placeholder
inputs in the governance membrane. Tags: `[V]` verified in code · `[S]` synthesis.

## What this unlocks

The membrane has two inputs still fed by placeholders:

- **`novel`** — "this design diverged from the published library" (a human should look).
- **`propose`** — the divergence lane: design and code disagree, so the change becomes
  a **joint artifact** owned by both sides, *not* a one-way routed notification.

Reach, confidence, floor/p1, and tier are all wired and real. These two are the
remainder, and both need a real **design-vs-library / design-vs-code divergence
signal** — which is what live Figma provides.

## Current state (grounded)

More is already built than the "blocked on Figma" framing suggested:

- `[V]` **Schema is ready.** `FigmaComponent` already carries `diverges_from_library`
  and `divergence_notes` ([src/core/schemas.py:308](../src/core/schemas.py)).
- `[V]` **Live provider is substantially built.** [src/providers/live/figma.py](../src/providers/live/figma.py)
  has auth (`FIGMA_ACCESS_TOKEN`), library/team file maps, component mapping, and a
  `get_drift_issues()` that already runs **three drift heuristics** (detached/unlinked,
  etc.) inferred from structural/temporal metadata — because the Figma REST API exposes
  no first-class "instance diverged from main" signal.
- `[V]` **The one stub:** `_map_component` hardcodes `diverges_from_library=False`
  (line ~341), so the per-component flag never reflects the heuristics the same file
  already computes. *The detection largely exists; it isn't propagated or wired.*
- `[V]` **Synthetic fixture exists.** The local provider populates the flag + notes
  (the Atlas `DataTable` row-hover divergence), so downstream code and tests already
  have a real divergence to exercise — no live creds needed to build the logic.
- `[V]` **Membrane is ready to receive it.** `Lane.PROPOSE` and the `novel` condition
  exist ([src/agent/membrane.py](../src/agent/membrane.py)); `route_lane` already reads
  `event.metadata["novel"]` — the wire is in, nothing sets it (exactly the state `p1`
  was in before the floor work). `PROPOSE` is documented as "produced by the divergence
  classifier, not `route()`" — and that classifier is **not yet ported**.
- `[V]` **The classifier to port is known and bounded:** `classifyFindings` /
  `proposalProvenance` live in `token-sync/packages/governance/src/index.ts` — the
  *same* file the router was ported from.
- `[V]` **The ingestion "ear" exists:** `webhook_figma` in [webhook_server.py](../webhook_server.py).

## The key structural insight

**Most of this is buildable and testable NOW, against synthetic/mocked Figma data —
decoupled from live credentials and egress.** It mirrors exactly what we did with the
Atlassian REST providers: build the logic, prove it with mock-API tests, then flip live
creds later. Only the live-API calibration and the egress gate actually wait for
un-parking. So the plan front-loads the value into a "buildable now" phase.

---

## Phase A — buildable now (no live creds, no egress)

Three disjoint workstreams, the same shape as prior batches.

**A1 · Propagate divergence → the flag.**
Connect the existing `get_drift_issues` heuristics (plus a same-name library-vs-team
value compare) to `FigmaComponent.diverges_from_library` + `divergence_notes` — in
`_map_component` or a post-pass over fetched components. Define "divergence" concretely:
(i) detached/unlinked (heuristic exists); (ii) instance overrides beyond tokens;
(iii) a design value ≠ the library/code value. Carry the existing false-positive profile
forward as `divergence_notes`. Test with the mock-API pattern from `test_live_atlassian`.

**A2 · Wire `novel` → membrane.**
In the `webhook_figma` / ingestion path, set `event.metadata["novel"] =
component.diverges_from_library` — mirroring exactly how the floor set `p1`. `route_lane`
already consumes it; novel routes conservatively to `review` today (correct default).

**A3 · Port the propose classifier.**
Bring `classifyFindings` / `proposalProvenance` from
`token-sync/packages/governance/src/index.ts` into a Python module (`src/agent/propose.py`).
It turns a divergence into a **`PROPOSE` decision**: a joint artifact, not a one-way
routed change, with provenance. The distinction to preserve:
- `novel → review` = "new/unverified, a human should look."
- `propose` = "two sources of truth conflict — make it a *shared* decision."
Keep token-sync's audit-sampler (`sampleAuto`) so propose volume stays sane; never auto.

**A4 · The propose surface.**
When a change lands in `propose`: create a joint-artifact record linking the Figma node +
the code component + **both** owners; notify the design owner *and* the code owner (not
one-way); record provenance. Add a `@syncbot proposals` keyword command (parallels the
new `doctor` / governance-log commands). This is the visible payoff — the membrane
producing collaboration, not just alerts.

**A5 · Tests.**
Synthetic `DataTable` divergence → `novel` set → (with classifier) `PROPOSE`; golden
routing tests; mock-API tests for the live provider's flag propagation; frozen-time where
staleness matters.

## Phase B — needs un-parking (live creds + the egress gate)

**B1 · Credentials.** Provision a **least-privilege** Figma token (`FIGMA_ACCESS_TOKEN`)
and set `FIGMA_LIBRARY_FILE_KEY` (or `design_system_library` in team manifests). This is
the Figma egress gate — see [deployment-compliance.md](deployment-compliance.md).

**B2 · Calibrate against a real file.** The REST API has no first-class divergence
signal, so the heuristics need tuning against the real false-positive rate. Start
conservative (novel → review, never auto) until the signal is trusted.

**B3 · Optional supercharger.** Figma MCP / Code Connect gives a richer design↔code
mapping (a cleaner join key for design-vs-code divergence). Same gate/posture as the
Atlassian MCP — optional, off by default.

**B4 · Durability + narrative.** Provenance volume is already env-ready
(`SYNCBOT_PROVENANCE_PATH`); the AI-mode narrative on a proposal is optional.

## Risks / unknowns

- **No first-class divergence signal** in the Figma REST API → heuristic detection needs
  real-data calibration. False positives = noise = lost trust. Conservative first.
- **Design-vs-code divergence needs a join key** (Code Connect, or the manifest's
  design/code component pairing). Define it before A1's part (iii).
- **Propose can be noisy** — gate with the audit sampler; keep it `review`/`propose`,
  never `auto`.
- **Same compliance gates** (token scope, Figma egress) as the three-gates model.

## Effort / sequencing

- **Phase A:** ~3 disjoint workstreams (flag-propagation + mock tests · classifier port ·
  propose surface + command). Buildable now; mirrors the floor/tier/policy batches.
- **Phase B:** hours of config + calibration once creds and egress are approved.

Net: the moment Figma is un-parked, Phase A is already done and tested — B is a credential
flip plus calibration, not a build.
