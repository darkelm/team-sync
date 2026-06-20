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
from enum import Enum
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
        self._write_row(row)

    def append_all(self, proposals: list) -> None:
        """Append every proposal in order — the convenience the classifier's caller uses
        after a `classify_divergences` pass."""
        for proposal in proposals:
            self.append(proposal)

    def append_transition(self, transition) -> None:
        """Append one :class:`ProposalTransition` as a single JSONL line.

        STORAGE CHOICE — transitions share the SAME append-only file as proposals,
        discriminated by a ``"kind": "transition"`` field (proposal rows carry no such
        marker; see :meth:`_is_transition_row`). One file keeps the ordering of proposals
        and their transitions in a single durable, append-only ledger — there is exactly
        ONE audit trail to read, replay, and reason about, mirroring `provenance.py`'s
        single-log discipline. A sibling file would split the timeline and reintroduce the
        two-file ordering problem this design avoids. Both :meth:`current` and :meth:`all`
        cope with the two row types.
        """
        row = transition.to_dict() if hasattr(transition, "to_dict") else dict(transition)
        self._write_row(row)

    def _write_row(self, row: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(row) + "\n")

    def recent(self, n: int) -> list[dict]:
        """The most recent ``n`` rows, newest LAST. ``n <= 0`` ⇒ ``[]``. Missing or
        unreadable file ⇒ ``[]`` (a fresh store has no history yet).

        NOTE: ``recent`` returns RAW rows in append order — proposals AND transitions
        alike (the full ledger tail). Callers wanting the folded current view use
        :meth:`current`. Kept unchanged from v1: a proposals-only store tails identically.
        """
        if n <= 0:
            return []
        rows = self._read_all()
        return rows[-n:] if n < len(rows) else rows

    def all(self) -> list[dict]:
        """Every row, oldest first — proposals AND transitions interleaved in append
        order (the complete append-only audit trail)."""
        return self._read_all()

    def proposals(self) -> list[dict]:
        """Only the OPENED-proposal rows, oldest first (transition rows AND the
        provenance closer-rows from resolve/accept filtered out — see
        :func:`_is_opened_proposal_row`)."""
        return [r for r in self._read_all() if _is_opened_proposal_row(r)]

    def transitions(self) -> list[dict]:
        """Only the transition rows, oldest first (the audit of every state change)."""
        return [r for r in self._read_all() if _is_transition_row(r)]

    def current(self, item_ref: Optional[str] = None):
        """Fold the transitions over the opened proposals to compute each proposal's
        CURRENT status / assignee / resolution (latest transition wins).

          - ``current()`` ⇒ ``list[CurrentProposal]`` for every opened proposal, oldest
            first. A proposal with NO transitions reads ``OPEN`` (backward compatible).
          - ``current(item_ref)`` ⇒ the single :class:`CurrentProposal`, or ``None`` if no
            opened proposal carries that ref.

        Replays the ledger in append order: each transition's ``toStatus`` overwrites the
        prior status, and the resolver/assignee are carried from the relevant transition.
        """
        views = _fold_current(self._read_all())
        if item_ref is not None:
            return views.get(item_ref)
        return list(views.values())

    def progress(self) -> dict[str, int]:
        """Counts by current status — ``{"open": .., "claimed": .., "resolved": ..,
        "accepted": ..}`` — so a surface can render "3 open, 2 resolved". Convenience
        wrapper over the module-level :func:`progress`."""
        return progress(self)

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


# =============================================================================
# SECTION 6 — Resolution workflow  (the audited state machine over proposals)
#
# The validated team need: design↔code divergences must be RESOLVED (tracked to
# "done"), not just detected. A proposal is OPENED by `classify_divergences` (above);
# this section adds the small, append-only state machine that drives it to closure.
#
# "The loop never decides": EVERY transition requires a human :class:`Actor` and is
# recorded as a :class:`ProposalTransition` row appended to the SAME ledger. No record is
# ever mutated or rewritten — the current view is COMPUTED by replaying transitions
# (`current()` / `_fold_current`). A RESOLVED/ACCEPTED proposal's `decided_by` becomes
# `{"type":"human","who":...}` (via `proposal_provenance`), never pending — closing the
# "never just the loop decided" guarantee for the resolution path too.
# =============================================================================


class ProposalStatus(str, Enum):
    """The CURRENT state of a proposal, computed by folding its transitions.

      - ``OPEN``     — opened by the classifier, no one has claimed it (the resting state).
      - ``CLAIMED``  — a human has taken ownership; an assignee is set.
      - ``RESOLVED`` — closed by a human who updated design OR code (a ResolutionKind).
      - ``ACCEPTED`` — the divergence is ACCEPTED / won't-fix (a deliberate non-change).

    A proposal with NO transitions reads ``OPEN`` (backward compatible)."""
    OPEN = "open"
    CLAIMED = "claimed"
    RESOLVED = "resolved"
    ACCEPTED = "accepted"


class ResolutionKind(str, Enum):
    """HOW a RESOLVED/ACCEPTED proposal reached closure — the audited "what was done".

      - ``DESIGN_UPDATED``       — the design side moved to the system (designer resolved).
      - ``CODE_UPDATED``         — the code side adopted the divergence (dev resolved).
      - ``DIVERGENCE_ACCEPTED``  — neither side changed; the divergence is accepted (won't-fix).
    """
    DESIGN_UPDATED = "design_updated"
    CODE_UPDATED = "code_updated"
    DIVERGENCE_ACCEPTED = "divergence_accepted"


# Discriminator: a transition row carries this exact value in its ``kind`` field. Proposal
# rows carry a ProposalKind (default "component") in ``kind`` — NEVER "transition" — so the
# marker is unambiguous in the shared ledger. (See `ProposalStore.append_transition`.)
_TRANSITION_KIND = "transition"


def _is_transition_row(row: dict) -> bool:
    """Is this ledger row a transition? Discriminates on the ``kind`` marker only."""
    return isinstance(row, dict) and row.get("kind") == _TRANSITION_KIND


def _is_opened_proposal_row(row: dict) -> bool:
    """Is this ledger row an OPENED proposal (vs a transition or a provenance closer-row)?

    Three row types share the one append-only ledger: opened proposals (from
    `classify_divergences`), transition rows (``kind == "transition"``), and the bare
    ProvenanceRecord rows `resolve`/`accept` append to stamp a human decider. Only the
    opened-proposal row carries a ``component`` field (its `Proposal.to_dict` shape), so
    that field is the unambiguous positive discriminator the fold + `proposals()` use."""
    return (
        isinstance(row, dict)
        and not _is_transition_row(row)
        and "component" in row
    )


@dataclass(frozen=True)
class ProposalTransition:
    """One audited state change on a proposal — an append-only ledger row, never mutated.

    Records WHO drove it (a human :class:`Actor`), the FROM/TO status, an optional
    :class:`ResolutionKind` (set on resolve/accept), a free-text note, and WHEN. The
    ``item_ref`` joins back to the opened :class:`Proposal` (and to its provenance).
    """
    item_ref: str
    from_status: ProposalStatus
    to_status: ProposalStatus
    actor: Actor
    note: str = ""
    resolution_kind: Optional[ResolutionKind] = None
    at: str = ""  # ISO-8601

    def to_dict(self) -> dict:
        return {
            "kind": _TRANSITION_KIND,  # the discriminator (shared-ledger row type)
            "itemRef": self.item_ref,
            "fromStatus": self.from_status.value,
            "toStatus": self.to_status.value,
            "actor": self.actor.to_dict(),
            "note": self.note,
            "resolutionKind": self.resolution_kind.value if self.resolution_kind else None,
            "at": self.at,
        }


@dataclass(frozen=True)
class CurrentProposal:
    """The FOLDED current view of one proposal: its opened row plus the replayed state
    (status / assignee / resolution / last-actor). A read model — derived, never stored.

      - ``item_ref``        — joins to the opened proposal + its transitions.
      - ``status``          — the current :class:`ProposalStatus` (latest transition wins).
      - ``proposal``        — the original opened-proposal dict (None only for an orphan
                              transition with no matching opened row — defensive).
      - ``assignee``        — the human id who CLAIMED it (carried while CLAIMED+).
      - ``resolution_kind`` — the :class:`ResolutionKind` once RESOLVED/ACCEPTED, else None.
      - ``resolved_by``     — the human id who RESOLVED/ACCEPTED it, else None.
      - ``last_actor``      — the human id of the most recent transition, else None.
      - ``last_at``         — the ISO-8601 timestamp of the most recent transition, else None.
    """
    item_ref: str
    status: ProposalStatus
    proposal: Optional[dict] = None
    assignee: Optional[str] = None
    resolution_kind: Optional[ResolutionKind] = None
    resolved_by: Optional[str] = None
    last_actor: Optional[str] = None
    last_at: Optional[str] = None


def _fold_current(rows: list[dict]) -> "dict[str, CurrentProposal]":
    """Replay the ledger (proposals THEN their transitions, in append order) into the
    current view per item_ref. Opened proposals seed OPEN; each transition overwrites the
    status and carries assignee / resolution forward. Insertion order is preserved so
    ``current()`` lists proposals oldest-first. A transition with no opened row is still
    folded (defensive — its ``proposal`` stays None) so an audited state is never dropped."""
    views: dict[str, CurrentProposal] = {}

    # Pass 1: seed every opened proposal at OPEN (preserves first-seen order). Provenance
    # closer-rows are skipped here — they carry no `component` and are NOT proposals.
    for row in rows:
        if not _is_opened_proposal_row(row):
            continue
        ref = row.get("itemRef")
        if ref is None or ref in views:
            continue
        views[ref] = CurrentProposal(item_ref=ref, status=ProposalStatus.OPEN, proposal=row)

    # Pass 2: replay transitions in append order (latest wins).
    for row in rows:
        if not _is_transition_row(row):
            continue
        ref = row.get("itemRef")
        if ref is None:
            continue
        prev = views.get(ref) or CurrentProposal(item_ref=ref, status=ProposalStatus.OPEN)
        to_status = ProposalStatus(row["toStatus"])
        rkind = ResolutionKind(row["resolutionKind"]) if row.get("resolutionKind") else None
        actor = row.get("actor") or {}
        who = actor.get("id")
        views[ref] = CurrentProposal(
            item_ref=ref,
            status=to_status,
            proposal=prev.proposal,
            # Assignee is set when CLAIMED and carried forward; resolution carries who/how.
            assignee=who if to_status == ProposalStatus.CLAIMED else prev.assignee,
            resolution_kind=rkind if rkind is not None else prev.resolution_kind,
            resolved_by=who if to_status in (
                ProposalStatus.RESOLVED, ProposalStatus.ACCEPTED) else prev.resolved_by,
            last_actor=who,
            last_at=row.get("at"),
        )

    return views


# Terminal states no further transition may leave (a resolved/accepted proposal is done).
_TERMINAL = (ProposalStatus.RESOLVED, ProposalStatus.ACCEPTED)


def _require_human(actor: Actor) -> None:
    """Every transition is human-driven — "the loop never decides". A non-human actor
    (e.g. the agent) is rejected so autonomy can never close a proposal."""
    if actor is None or actor.type != "human":
        raise ValueError("a proposal transition requires a human actor (the loop never decides)")


def _current_status(store: "ProposalStore", item_ref: str) -> ProposalStatus:
    """The current status of one proposal, or OPEN when it has no transitions. Never
    raises on a missing store file (a fresh store folds to OPEN for any ref)."""
    view = store.current(item_ref)
    return view.status if view is not None else ProposalStatus.OPEN


def _transition(
    store: "ProposalStore",
    item_ref: str,
    actor: Actor,
    to_status: ProposalStatus,
    *,
    allowed_from: tuple[ProposalStatus, ...],
    resolution_kind: Optional[ResolutionKind] = None,
    note: str = "",
    now: Optional[Callable[[], str]] = None,
) -> ProposalTransition:
    """Append one audited transition after validating the FROM state. Shared spine of
    `claim` / `resolve` / `accept`.

    ILLEGAL-TRANSITION BEHAVIOR — RAISE (chosen over silent no-op): an illegal move
    (wrong source state, e.g. resolving an already-resolved item, or claiming a terminal
    one) raises a clear ``ValueError``. Closure is consequential, so a bad transition is
    surfaced loudly rather than swallowed. (Never raises on a missing store file — the
    fold treats an unknown ref as OPEN.)

    On success, the transition row is appended AND a fresh provenance record is written
    for RESOLVED/ACCEPTED with ``decided_by = {"type":"human","who":...}`` (via
    `proposal_provenance`) so the resolution is auditable as human-decided, never pending.
    """
    _require_human(actor)
    clock = now if now is not None else _default_now
    at = clock()

    from_status = _current_status(store, item_ref)
    if from_status not in allowed_from:
        raise ValueError(
            f"illegal transition for {item_ref!r}: {from_status.value} → {to_status.value} "
            f"(allowed from: {', '.join(s.value for s in allowed_from)})"
        )

    transition = ProposalTransition(
        item_ref=item_ref,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        note=note,
        resolution_kind=resolution_kind,
        at=at,
    )
    store.append_transition(transition)

    # Closing a proposal stamps a human decider into the propose lane — never pending.
    if to_status in _TERMINAL:
        view = store.current(item_ref)
        reach = None
        if view is not None and view.proposal is not None:
            reach = view.proposal.get("reach")
        store.append(
            proposal_provenance(
                item_ref,
                actor,
                reach,
                at,
                decided_by={"type": "human", "who": actor.id},
            )
        )

    return transition


def claim(
    store: "ProposalStore",
    item_ref: str,
    actor: Actor,
    *,
    note: str = "",
    now: Optional[Callable[[], str]] = None,
) -> ProposalTransition:
    """OPEN → CLAIMED. A human takes ownership; the actor becomes the assignee. Raises a
    ``ValueError`` if the proposal is not currently OPEN (already claimed / resolved /
    accepted)."""
    return _transition(
        store, item_ref, actor, ProposalStatus.CLAIMED,
        allowed_from=(ProposalStatus.OPEN,), note=note, now=now,
    )


def resolve(
    store: "ProposalStore",
    item_ref: str,
    actor: Actor,
    resolution_kind: ResolutionKind,
    note: str = "",
    *,
    now: Optional[Callable[[], str]] = None,
) -> ProposalTransition:
    """→ RESOLVED. A human closes the divergence by updating design OR code (records
    who/when/how). Allowed from OPEN or CLAIMED (a resolver may resolve directly without a
    prior claim). Raises a ``ValueError`` if already RESOLVED/ACCEPTED. Stamps a human
    decider into provenance (never pending)."""
    return _transition(
        store, item_ref, actor, ProposalStatus.RESOLVED,
        allowed_from=(ProposalStatus.OPEN, ProposalStatus.CLAIMED),
        resolution_kind=resolution_kind, note=note, now=now,
    )


def accept(
    store: "ProposalStore",
    item_ref: str,
    actor: Actor,
    note: str = "",
    *,
    now: Optional[Callable[[], str]] = None,
) -> ProposalTransition:
    """→ ACCEPTED. A human accepts the divergence (won't-fix) — a deliberate non-change,
    recorded with ``ResolutionKind.DIVERGENCE_ACCEPTED``. Allowed from OPEN or CLAIMED.
    Raises a ``ValueError`` if already RESOLVED/ACCEPTED. Stamps a human decider into
    provenance (never pending)."""
    return _transition(
        store, item_ref, actor, ProposalStatus.ACCEPTED,
        allowed_from=(ProposalStatus.OPEN, ProposalStatus.CLAIMED),
        resolution_kind=ResolutionKind.DIVERGENCE_ACCEPTED, note=note, now=now,
    )


def progress(store: "ProposalStore") -> dict[str, int]:
    """Counts by current status across all proposals — ``{"open", "claimed", "resolved",
    "accepted"}`` — so a surface can render "3 open, 2 resolved". Every key is always
    present (zero when none). Reads the folded current view (a fresh/missing store ⇒ all
    zeros)."""
    counts = {s.value: 0 for s in ProposalStatus}
    for view in store.current():
        counts[view.status.value] += 1
    return counts
