"""Divergence classifier — the engine for the `propose` lane.

SPEC OF RECORD: token-sync's `packages/governance/src/index.ts` §8 (Proposals),
                §9 (`proposalProvenance`), §10 (`sampleAuto`).
ORACLE:         the SAME file `membrane.py` was ported from — conventions match.

PHILOSOPHY — a divergence is a JOINT ARTIFACT, not a one-way routed change. When a
designer deliberately diverges from the system (an off-system value with no matching
token / library component), the membrane does NOT route a change and does NOT decide:
it mints a :class:`Proposal` — a thing a designer proposes and a dev evaluates — and
records it as PENDING in the `propose` lane. "The loop never decides": a proposal stays
PENDING until a human resolves it (accept/decline). This is the propose-lane counterpart
to `membrane.route()` (which routes ACTUAL changes into the other four lanes).

DECOUPLING (contract §2, the injected-seam discipline): the classifier takes
:class:`DivergenceFinding`s, NOT `FigmaComponent`s. The integration layer builds the
findings from FigmaComponents (`diverges_from_library` / `divergence_notes` /
`used_by_teams` → reach), so the classifier never imports the Figma schema.

What ports VERBATIM from the oracle:
  - the DIVERGENCE → open `Proposal` transform (`classifyFindings`, TS index.ts:731);
  - `proposalProvenance` (TS index.ts:806): lane=propose, decided_by pending-while-open,
    passed_floor=True by definition, reach from the code-side slot;
  - `sampleAuto` (TS index.ts:841): clamp rate to [0,1], at-least-one when any exist,
    partial Fisher–Yates WITHOUT replacement — ported as :func:`sample_audit`.

What is intentionally NOT ported (see module note in the report):
  - the DRIFT / `BindSuggestion` branch of `classifyFindings`. team-sync's findings are
    ALREADY divergences (the integration layer only builds a DivergenceFinding for a true
    off-system value), so there is no `suggestedToken` near-miss to bind here. The bind
    branch belongs to the compliance surface, not the propose lane.

Provenance persistence lives behind an injected seam (:class:`ProposalStore`), MIRRORING
`provenance.py` exactly — the classifier itself is PURE.
"""
from __future__ import annotations

import json
import os
import random as _random_module
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from .membrane import Actor, Lane, ProvenanceRecord, ReviewPolicy


# =============================================================================
# SECTION 1 — DivergenceFinding (the input)  (decouples from the Figma schema)
# =============================================================================

@dataclass(frozen=True)
class DivergenceFinding:
    """One off-system divergence to turn into a joint proposal. Decoupled from the
    Figma schema on purpose: the integration layer builds these from FigmaComponents
    (a component whose ``diverges_from_library`` is set), so the classifier depends on
    NOTHING Figma-specific.

    This is the team-domain analog of the oracle's ``ComplianceFindingLike`` (TS
    index.ts:630), but it carries the design↔code OWNERS (the two sides of the joint
    artifact) instead of a node/property pair.

      - ``component``        — the component name the divergence is on (the item ref base).
      - ``divergence_notes`` — the designer's plain-language note on WHY they diverged.
      - ``design_owner``     — who owns the design side (the proposer's side).
      - ``code_owner``       — who owns the code side (the side that evaluates).
      - ``figma_url``        — optional deep link to the divergent component.
      - ``reach``            — optional consequence signal (number of OTHER consumers),
                               the code-side weight a dev reads. Missing ⇒ unknown.
    """
    component: str
    divergence_notes: str
    design_owner: str
    code_owner: str
    figma_url: Optional[str] = None
    reach: Optional[int] = None


# =============================================================================
# SECTION 2 — Proposal (the joint artifact)  (oracle §8; provenance §9)
# =============================================================================

# What a proposal can become: a brand-new shared component, or a reusable pattern.
# (Oracle ProposalKind = 'token' | 'pattern'; team-domain analog.) Default "component".
ProposalKind = str


def _default_now() -> str:
    """The only place a clock is read — injectable for deterministic tests (mirrors
    `membrane._default_now`)."""
    return datetime.now(timezone.utc).isoformat()


def _proposal_item_ref(finding: DivergenceFinding) -> str:
    """Deterministic, content-derived item ref (no random source — mirrors the oracle's
    `defaultProposalId`, TS index.ts:774, which keys on the finding's stable identity)."""
    return f"proposal:{finding.component}"


# The PENDING decider — the propose lane's resting state. A proposal is NEVER decided by
# a rule or by the loop; it awaits a human. (Oracle `proposalProvenance`, TS index.ts:811:
# `status === 'open' ? { type: 'pending' } : { type: 'human', who }`.)
_PENDING: dict = {"type": "pending"}


@dataclass(frozen=True)
class Proposal:
    """The JOINT ARTIFACT both sides converge on. A designer's deliberate divergence
    captured as a first-class thing — NOT an error and NOT a routed change. It carries
    the DESIGN context (notes, the design owner) AND the CODE side (the code owner, the
    reach a dev reads), so it is the literal handshake: a designer proposes, a dev evaluates.

    ``decided_by`` defaults to ``{"type": "pending"}`` — "the loop never decides". The
    proposal stays PENDING until a human resolves it; only then does ``decided_by`` become
    ``{"type": "human", "who": ...}`` (set by the resolver, out of scope here).

    The embedded :class:`ProvenanceRecord` is the durable audit row: lane =
    ``Lane.PROPOSE``, decided_by pending, passed_floor True by definition (a design-side
    divergence is not a code-floor failure — oracle TS index.ts:808).
    """
    item_ref: str
    component: str
    kind: ProposalKind
    divergence_notes: str
    design_owner: str
    code_owner: str
    proposed_by: Actor
    provenance: ProvenanceRecord
    at: str  # ISO-8601
    figma_url: Optional[str] = None
    reach: Optional[int] = None
    decided_by: dict = field(default_factory=lambda: dict(_PENDING))

    def to_dict(self) -> dict:
        return {
            "itemRef": self.item_ref,
            "component": self.component,
            "kind": self.kind,
            "divergenceNotes": self.divergence_notes,
            "designOwner": self.design_owner,
            "codeOwner": self.code_owner,
            "figmaUrl": self.figma_url,
            "reach": self.reach,
            "proposedBy": self.proposed_by.to_dict(),
            "lane": Lane.PROPOSE.value,
            "decidedBy": dict(self.decided_by),
            "at": self.at,
            "provenance": self.provenance.to_dict(),
        }


# Who proposes a divergence by default: a human (a designer). Mirrors the oracle's
# `DEFAULT_DESIGNER` (TS index.ts:718) — a divergence is a designer's act, not the agent's.
# Carries the design owner only when one is injected per-finding (see `classify_divergences`).
DEFAULT_DESIGNER = Actor(type="human", id="designer")


# =============================================================================
# SECTION 3 — proposalProvenance + classify_divergences  (oracle §8/§9)
# =============================================================================

def proposal_provenance(
    item_ref: str,
    proposed_by: Actor,
    reach: Optional[int],
    at: str,
    decided_by: Optional[dict] = None,
) -> ProvenanceRecord:
    """Build the provenance record for a proposal (the proposal path's audit trail).
    Faithful port of the oracle's `proposalProvenance` (TS index.ts:806):

      - lane is ALWAYS ``Lane.PROPOSE``;
      - decided_by is PENDING while the proposal is open (the default), and only a
        ``{human, who}`` once a human has resolved it;
      - passed_floor is True BY DEFINITION — a design-side divergence is not a code-floor
        failure (TS index.ts:808);
      - reach comes from the code-side slot (may be None / unknown).
    """
    return ProvenanceRecord(
        item_ref=item_ref,
        proposed_by=proposed_by,
        lane=Lane.PROPOSE,
        decided_by=dict(decided_by) if decided_by is not None else dict(_PENDING),
        passed_floor=True,
        at=at,
        reach=reach,
    )


def classify_divergences(
    findings: list[DivergenceFinding],
    policy: Optional[ReviewPolicy] = None,
    *,
    proposed_by: Optional[Actor] = None,
    now: Optional[Callable[[], str]] = None,
) -> list[Proposal]:
    """Turn divergence findings into PROPOSE-lane :class:`Proposal`s (the Python port of
    the oracle's `classifyFindings`, TS index.ts:731 — the DIVERGENCE branch).

    PURE: no policy rule is consulted (the oracle's `_policy` arg is unused there too —
    classification is about the FINDING, not a lane; the lane is fixed: ``propose``). The
    ``policy`` param is kept for signature symmetry with `membrane.route` / the oracle and
    for forward compatibility; it does not influence the result today.

    Each finding becomes an OPEN proposal: decided_by PENDING, owners carried through, the
    designer's notes preserved verbatim, and a provenance record stamped in the propose lane.
    "The loop never decides" — these are PENDING until a human resolves them.

    ``proposed_by`` (when injected) is honored as the proposer; otherwise the proposer is a
    ``human`` actor identified by the finding's ``design_owner`` (the divergence is the
    designer's act — oracle DEFAULT_DESIGNER, TS index.ts:718), falling back to the generic
    designer when no owner is named.
    """
    clock = now if now is not None else _default_now
    at = clock()

    proposals: list[Proposal] = []
    for finding in findings:
        # Proposer: an explicitly injected actor wins; else the divergence is attributed to
        # the design owner (a human), falling back to the generic designer (oracle §8).
        if proposed_by is not None:
            proposer = proposed_by
        elif finding.design_owner:
            proposer = Actor(type="human", id=finding.design_owner)
        else:
            proposer = DEFAULT_DESIGNER

        item_ref = _proposal_item_ref(finding)
        provenance = proposal_provenance(item_ref, proposer, finding.reach, at)
        proposals.append(
            Proposal(
                item_ref=item_ref,
                component=finding.component,
                kind="component",
                divergence_notes=finding.divergence_notes,
                design_owner=finding.design_owner,
                code_owner=finding.code_owner,
                figma_url=finding.figma_url,
                reach=finding.reach,
                proposed_by=proposer,
                provenance=provenance,
                at=at,
                decided_by=dict(_PENDING),
            )
        )

    return proposals


# =============================================================================
# SECTION 4 — Audit sampling  (oracle §10, `sampleAuto`, TS index.ts:841)
#
# Keeps proposal/auto volume sane: a random sample is pulled for a human spot-check so the
# tier is never fully unaudited. Ported faithfully — clamp, at-least-one, partial
# Fisher–Yates WITHOUT replacement.
# =============================================================================

# Default fraction sampled for the recap (oracle DEFAULT_AUTO_SAMPLE_RATE, TS index.ts:826).
DEFAULT_SAMPLE_RATE = 0.1


def sample_audit(
    items: list,
    rate: float = DEFAULT_SAMPLE_RATE,
    *,
    random: Optional[Callable[[], float]] = None,
) -> list:
    """Pull a random sample of ``items`` for a human spot-check. Faithful port of the
    oracle's `sampleAuto` (TS index.ts:841):

      - empty input ⇒ ``[]`` (TS:848);
      - ``rate`` clamped to [0,1] (TS:850); ``rate <= 0`` ⇒ ``[]`` (TS:851);
        ``rate >= 1`` ⇒ ALL items (TS:852);
      - target count = ``max(1, round(len * rate))`` — at least ONE so the tier is never
        fully unaudited (TS:855);
      - sample WITHOUT replacement via a partial Fisher–Yates over a COPY (TS:857–865).

    ``random`` is the injectable RNG in [0,1) (defaults to ``random.random``) — the only
    impure seam, mirroring the oracle's `SampleOptions.random` (TS index.ts:829).

    Generic over the sampled element (proposals OR auto-lane decisions) — the oracle samples
    auto-lane decisions; the propose lane samples proposals; the algorithm is identical.
    """
    rng = random if random is not None else _random_module.random
    if len(items) == 0:  # TS index.ts:848
        return []

    # Clamp rate to [0,1] (TS index.ts:850).
    clamped = 0.0 if rate < 0 else 1.0 if rate > 1 else rate
    if clamped <= 0:  # TS index.ts:851
        return []
    if clamped >= 1:  # TS index.ts:852
        return list(items)

    # Target: round, but guarantee at least one so the tier is never unaudited (TS:855).
    target = max(1, round(len(items) * clamped))

    # Sample WITHOUT replacement via a partial Fisher–Yates over a copy (TS index.ts:857–865).
    pool = list(items)
    out: list = []
    i = 0
    while i < target and len(pool) > 0:
        j = int(rng() * len(pool))
        # Bounds-guard a misbehaving RNG exactly as the oracle does (TS index.ts:862).
        idx = len(pool) - 1 if j >= len(pool) else 0 if j < 0 else j
        out.append(pool[idx])
        pool.pop(idx)
        i += 1
    return out


# =============================================================================
# SECTION 5 — ProposalStore (the injected IO seam)  (MIRRORS provenance.py)
# =============================================================================

# Module constant so tests can redirect it and prod can point it at a durable volume.
# Mirrors `provenance.py`'s PROVENANCE_PATH idiom exactly. On an ephemeral host the
# proposal log is lost on redeploy; set SYNCBOT_PROPOSALS_PATH to a persistent volume.
PROPOSALS_PATH = "data/proposals.jsonl"


class ProposalStore:
    """Append-only, durable proposal log — the joint-artifact ledger. MIRRORS
    :class:`provenance.ProvenanceStore` exactly (same JSONL idiom, same path precedence,
    same corrupt-line tolerance).

    Path precedence: explicit ``path`` arg → ``SYNCBOT_PROPOSALS_PATH`` env → the module
    default. The env read happens here (not at import) so a deploy can set it without
    reordering imports; tests that pass an explicit tmp ``path`` still win.

    Accepts either a :class:`Proposal` (normalized via ``.to_dict()``) or a plain ``dict``
    already in proposal shape.
    """

    def __init__(self, path: str | None = None):
        self.path = path if path is not None else os.getenv("SYNCBOT_PROPOSALS_PATH", PROPOSALS_PATH)

    def append(self, proposal) -> None:
        """Append one proposal as a single JSONL line."""
        row = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(row) + "\n")

    def append_all(self, proposals: list) -> None:
        """Append every proposal in order — the convenience the classifier's caller uses
        after a `classify_divergences` pass."""
        for proposal in proposals:
            self.append(proposal)

    def recent(self, n: int) -> list[dict]:
        """The most recent ``n`` proposals, newest LAST. ``n <= 0`` ⇒ ``[]``. Missing or
        unreadable file ⇒ ``[]`` (a fresh store has no history yet)."""
        if n <= 0:
            return []
        rows = self._read_all()
        return rows[-n:] if n < len(rows) else rows

    def all(self) -> list[dict]:
        """Every proposal, oldest first."""
        return self._read_all()

    def _read_all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        out: list[dict] = []
        try:
            with open(self.path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        # A single corrupt line should not nuke the whole ledger; skip it
                        # and keep the rest (append-only ⇒ corruption is rare and localized).
                        continue
        except OSError:
            return []
        return out
