"""Governance routing membrane — the Python port of token-sync's `@token-sync/governance`.

SPEC OF RECORD: `token-sync/docs/integration/governance-router-contract.md` (§ refs below)
ORACLE:         `token-sync/packages/governance/src/index.ts` (+ its tests)

This is a FAITHFUL port of the routing core. The router is pure: routing is a
function of `(items, context, policy)` with NO IO. Provenance persistence lives
behind an injected seam (`provenance.py`) — never in here.

What ports VERBATIM (contract §1–9):
  - the 5-lane vocabulary,
  - the precedence ladder (floor → novel/low-confidence → policy → default),
  - `can_auto_flow` (reach ceiling + confidence VETO-when-present / NEUTRAL-when-absent),
  - `select_rule` (most-specific-wins, earliest-index tie-break, `reachOver` STRICTLY greater),
  - `compile_policy_from_toggles` (toggles → rules; autonomy ONLY via the `*_flow` toggles),
  - the `ProvenanceRecord` shape.

What is RECALIBRATED for the team domain (contract §10 — adaptation layer, expected):
  - `TEAM_REACH_CEILING = 1` instead of token-scale 5 (see the constant's docstring),
  - `tier_of` reads a team-event tier off the path head (the token path convention is
    kept so the conformance oracle ports 1:1; team callers may pass their own paths).

⚠️ CONFIDENCE SEMANTICS (contract §6, settled v1): confidence is a VETO WHEN PRESENT,
   NEUTRAL WHEN ABSENT. An ABSENT confidence does NOT block `auto`. team-sync wires no
   confidence signal today, so requiring *present* confidence would make `auto`
   unreachable and collapse the whole membrane to all-`review`. Do not do that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional


# =============================================================================
# SECTION 1 — Lanes  (contract §1)
# =============================================================================

class Lane(str, Enum):
    """The routing destination for one item.

    - ``blocked`` — failed the HARD validation floor (a P1). Cannot ship. Checked FIRST.
    - ``review``  — needs a human sign-off. The conservative destination.
    - ``digest``  — routine; flows but is gathered into a batched recap read after the fact.
    - ``auto``    — passes the floor, low reach, (confidence not vetoing), AND a policy
                    rule explicitly permits the category. Autonomy is EARNED.
    - ``propose`` — a DIVERGENCE: becomes a joint artifact, not a routed change. (Produced
                    by the divergence classifier, not :func:`route`.)
    """
    BLOCKED = "blocked"
    REVIEW = "review"
    DIGEST = "digest"
    AUTO = "auto"
    PROPOSE = "propose"


# =============================================================================
# SECTION 2 — Tier + change-kind vocabulary  (contract §3, adapted §10)
# =============================================================================

# Designer-meaningful buckets. The token-domain meaning is brand/shared/raw; in the
# team domain this is the *consequence tier* of an event (kept name-compatible with
# the oracle so the conformance tests port unchanged).
Tier = str  # "brand" | "shared" | "raw"

# The change-kind vocabulary. The oracle's TokenChangeKind is added/removed/changed/
# renamed; team-sync may also use its own event kinds (merge/publish/deprecate/…). The
# rule shape (`kind?: list[str]`) is unchanged — kinds are just strings matched by `in`.
TokenChangeKind = str


# =============================================================================
# SECTION 3 — The routed item (analog of TS `TokenChange`)  (oracle: change-set.ts)
# =============================================================================

@dataclass(frozen=True)
class RouteItem:
    """One atomic thing to route: a stable key, a display path, a change kind, and an
    optional mode. Mirrors the oracle's ``TokenChange`` load-bearing fields so the
    router logic ports 1:1. Team callers build these from Events (see events.py)."""
    key: str
    path: str
    kind: TokenChangeKind
    mode: Optional[str] = None


# =============================================================================
# SECTION 4 — Actors + provenance record  (contract §8; oracle §2)
# =============================================================================

@dataclass(frozen=True)
class Actor:
    """A named party. An ``agent`` may carry the model id it ran on; a ``human``
    carries a stable id (handle/email — plain text)."""
    type: str  # "agent" | "human"
    id: str
    model: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"type": self.type, "id": self.id}
        if self.model is not None:
            d["model"] = self.model
        return d


@dataclass(frozen=True)
class ProvenanceRecord:
    """The durable audit record for one routed item (contract §8). One record per
    decision, naming the proposer, the lane, the decider, the reach, whether it
    cleared the floor, and when. The "never just the loop decided" guarantee made
    concrete: ``decided_by`` is ALWAYS a named rule, a named human, or ``pending``."""
    item_ref: str
    proposed_by: Actor
    lane: Lane
    decided_by: dict  # {"type":"rule","ruleId":..} | {"type":"human","who":..} | {"type":"pending"}
    passed_floor: bool
    at: str  # ISO-8601
    reach: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "itemRef": self.item_ref,
            "proposedBy": self.proposed_by.to_dict(),
            "lane": self.lane.value,
            "decidedBy": dict(self.decided_by),
            "reach": self.reach,
            "passedFloor": self.passed_floor,
            "at": self.at,
        }


@dataclass(frozen=True)
class RoutingDecision:
    """One routing decision: the lane, the rule id that decided it (or ``None`` when
    the floor / a non-rule precedence step / the default decided), and the full
    provenance record. ``item_ref`` joins back to the item and to provenance."""
    item_ref: str
    lane: Lane
    rule_id: Optional[str]
    provenance: ProvenanceRecord


# =============================================================================
# SECTION 5 — Review policy (the human-owned rules)  (contract §3)
# =============================================================================

@dataclass(frozen=True)
class ReviewRule:
    """One human-authored rule. ``when`` is a conjunction of OPTIONAL conditions (all
    present conditions must hold); ``lane`` is where a matching item routes. An empty
    ``when`` matches everything (a catch-all).

    Conditions (all optional):
      - ``tier``       — the item's tier must equal this.
      - ``kind``       — the change kind must be one of these.
      - ``reach_over`` — the item's reach must be STRICTLY GREATER than this.
      - ``novel``      — the item must (True) / must not (False) be novel.
    """
    lane: Lane
    tier: Optional[Tier] = None
    kind: Optional[list[str]] = None
    reach_over: Optional[int] = None
    novel: Optional[bool] = None

    def specificity(self) -> int:
        """Count of PRESENT conditions in ``when`` (contract §7)."""
        n = 0
        if self.tier is not None:
            n += 1
        if self.kind is not None:
            n += 1
        if self.reach_over is not None:
            n += 1
        if self.novel is not None:
            n += 1
        return n


@dataclass(frozen=True)
class ReviewPolicy:
    """An ordered list of rules + a default lane for items no rule matches. Matching is
    "most specific wins" (:func:`select_rule`); ORDER is the tie-break among equally
    specific rules (earlier wins) so a human authors deterministically."""
    rules: list[ReviewRule] = field(default_factory=list)
    default_lane: Lane = Lane.REVIEW


def default_policy() -> ReviewPolicy:
    """The conservative default (contract §0 invariant 3, §3): NO rules, default
    ``review``. Everything is fully gated — autonomy is EARNED by an explicit
    human-authored policy, never assumed."""
    return ReviewPolicy(rules=[], default_lane=Lane.REVIEW)


# =============================================================================
# SECTION 6 — Designer toggles → policy  (contract §4)
#
# The plain-language toggles a human flips. These are the ONLY governance surface a
# designer sees; `compile_policy_from_toggles` turns them into a ReviewPolicy. The two
# `*_flow` toggles are the ONLY way anything reaches `auto`.
#
# NOTE (contract §10): token-sync's five toggles are token-shaped. team-sync should
# author its OWN plain-language toggles over time; this port keeps the oracle's set so
# the conformance tests pass 1:1 and the *pattern* (toggles → rules; autonomy only via
# *_flow) is preserved for team-sync to extend.
# =============================================================================

@dataclass
class DesignerToggles:
    brand_changes_always_ask: bool = True
    new_tokens_always_ask: bool = True
    renames_removals_always_ask: bool = True
    small_tweaks_flow: bool = False
    spacing_tweaks_flow: bool = False


def default_toggles() -> DesignerToggles:
    """Conservative default: only the "always ask" guards on, nothing flows."""
    return DesignerToggles()


def compile_policy_from_toggles(toggles: Optional[DesignerToggles] = None) -> ReviewPolicy:
    """Compile plain-language toggles into a :class:`ReviewPolicy` (contract §4).

    Authored MOST-SPECIFIC FIRST so the "most specific wins" matcher resolves them as
    intended; order is only the specificity tie-breaker (§7). With every toggle at its
    conservative default ⇒ behaviorally identical to :func:`default_policy`.
    """
    t = toggles if toggles is not None else default_toggles()
    rules: list[ReviewRule] = []

    # --- "always ask" guards (most specific first) -------------------------------
    if t.brand_changes_always_ask:
        rules.append(ReviewRule(tier="brand", lane=Lane.REVIEW))
    if t.new_tokens_always_ask:
        rules.append(ReviewRule(kind=["added"], lane=Lane.REVIEW))
    if t.renames_removals_always_ask:
        rules.append(ReviewRule(kind=["renamed", "removed"], lane=Lane.REVIEW))

    # --- autonomy grants (the ONLY way anything reaches `auto`) ------------------
    if t.spacing_tweaks_flow:
        rules.append(ReviewRule(tier="raw", kind=["changed"], lane=Lane.AUTO))
    if t.small_tweaks_flow:
        # The low-reach gate is enforced by the router (can_auto_flow); the rule's
        # PRESENCE is what "permits this category to flow".
        rules.append(ReviewRule(kind=["changed"], lane=Lane.AUTO))

    return ReviewPolicy(rules=rules, default_lane=Lane.REVIEW)


# =============================================================================
# SECTION 7 — Route context + constants  (contract §2, §5)
# =============================================================================

@dataclass
class RouteContext:
    """The INJECTED context for a routing pass. Everything expensive/impure is computed
    by the caller and handed in so this module stays pure (contract §2).

      - ``blast_radius`` — downstream reach per item key; MISSING key ⇒ reach 0.
      - ``p1_keys``      — item keys carrying a hard-floor P1 ⇒ ``blocked``.
      - ``confidence``   — optional per-key signal in [0,1]; VETO-when-present,
                           NEUTRAL-when-absent (§5/§6). team-sync wires none today.
      - ``novel_keys``   — optional keys whose value is NOVEL/off-system ⇒ ``review``.
    """
    blast_radius: dict[str, int] = field(default_factory=dict)
    p1_keys: list[str] = field(default_factory=list)
    confidence: Optional[dict[str, float]] = None
    novel_keys: Optional[list[str]] = None


# --- Constants ---------------------------------------------------------------

# AI-confidence floor: a PRESENT confidence STRICTLY BELOW this is "low" and vetoes
# `auto` / routes to `review`. (contract §5; oracle LOW_CONFIDENCE_THRESHOLD)
LOW_CONFIDENCE_THRESHOLD = 0.6

# The reach ceiling under which an item counts as "small" enough to earn `auto`. Reach
# STRICTLY GREATER than this can never auto.
#
# ⚠️ TEAM-SCALE, NOT TOKEN-SCALE (contract §10). The oracle's SMALL_TWEAK_REACH_CEILING
# is 5 — calibrated for token downstream counts in the thousands. Teams live on a
# totally different scale: an org has ~5–20 teams, so a ceiling of 5 would let a change
# touching nearly the whole org still count as "small" and earn `auto`. Here a change
# that touches ≥2 OTHER teams is no longer "small" and cannot auto; only a change
# confined to ≤1 other team is autonomy-eligible. This single number IS the lane
# behavior — it is tunable (recalibrate during pilot), but it is deliberately tight.
TEAM_REACH_CEILING = 1

DEFAULT_AGENT = Actor(type="agent", id="syncbot")


# =============================================================================
# SECTION 8 — route  (the precedence heart)  (contract §6)
# =============================================================================

def _default_now() -> str:
    """The only place a clock is read — injectable for deterministic tests."""
    return datetime.now(timezone.utc).isoformat()


def _reach_of(context: RouteContext, key: str) -> int:
    """Per-key reach; missing ⇒ 0 (contract §2)."""
    return context.blast_radius.get(key, 0)


def _confidence_of(context: RouteContext, key: str) -> Optional[float]:
    """Per-key confidence, or ``None`` when absent (contract §2/§5)."""
    if context.confidence is None:
        return None
    return context.confidence.get(key)


def _item_ref(item: RouteItem) -> str:
    """Stable per-item reference: the key, plus ``#<mode>`` when mode-scoped, so two
    changes on the same key in different modes route + audit independently."""
    return f"{item.key}#{item.mode}" if item.mode else item.key


def can_auto_flow(reach: int, confidence: Optional[float]) -> bool:
    """The low-reach + confidence-VETO gate an ``auto`` rule must also clear (contract §6).

    Autonomy is GRANTED by an explicit policy rule and BOUNDED by the reach ceiling.
    Confidence is a VETO, not a grantor:
      - reach STRICTLY GREATER than the ceiling ⇒ False (high reach can never auto);
      - a PRESENT reading below the low-confidence threshold ⇒ False (present-low vetoes);
      - an ABSENT confidence is NEUTRAL ⇒ True (it does NOT block auto).

    ⚠️ Do NOT require *present* confidence here. team-sync wires no confidence signal;
    requiring it would make `auto` unreachable and collapse the membrane to all-`review`.
    """
    if reach > TEAM_REACH_CEILING:
        return False
    # Veto ONLY on a present, low reading. Absent confidence is neutral.
    if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
        return False
    return True


def route(
    items: list[RouteItem],
    context: RouteContext,
    policy: ReviewPolicy,
    proposed_by: Optional[Actor] = None,
    now: Optional[Callable[[], str]] = None,
) -> list[RoutingDecision]:
    """Route items into lanes. PURE: the only effect is the returned decisions;
    persistence is the caller's job (pass each decision's ``.provenance`` to a store).

    PRECEDENCE — first match wins (contract §6):
      1. FLOOR              key in p1_keys                 → ``blocked`` (ahead of policy)
      2. NOVEL / LOW-CONF   novel OR present-low confidence → ``review``
      3. POLICY             most-specific matching rule decides; an ``auto`` grant only
                            lands in ``auto`` when ``can_auto_flow`` also holds, else ``review``
      4. DEFAULT            ``policy.default_lane``
    """
    proposer = proposed_by if proposed_by is not None else DEFAULT_AGENT
    clock = now if now is not None else _default_now
    at = clock()

    p1 = set(context.p1_keys)
    novel = set(context.novel_keys or [])

    decisions: list[RoutingDecision] = []
    for item in items:
        item_ref = _item_ref(item)
        reach = _reach_of(context, item.key)
        confidence = _confidence_of(context, item.key)
        passed_floor = item.key not in p1

        # 1) FLOOR — absolute, ahead of any policy rule.
        if not passed_floor:
            decisions.append(_decide(item_ref, Lane.BLOCKED, None, proposer, reach, False, at))
            continue

        # 2) NOVEL / LOW-CONFIDENCE — an untrusted change always asks.
        is_novel = item.key in novel
        is_low_conf = confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD
        if is_novel or is_low_conf:
            decisions.append(_decide(item_ref, Lane.REVIEW, None, proposer, reach, True, at))
            continue

        # 3) POLICY — most-specific matching rule decides.
        match = select_rule(policy, item, reach, is_novel)
        if match is not None:
            rule, rule_id = match
            lane = rule.lane
            # An `auto` grant only lands in `auto` when the low-reach + confidence
            # gate also holds; otherwise it falls back to `review`.
            if lane == Lane.AUTO and not can_auto_flow(reach, confidence):
                lane = Lane.REVIEW
            decisions.append(_decide(item_ref, lane, rule_id, proposer, reach, True, at))
            continue

        # 4) DEFAULT — conservative.
        decisions.append(_decide(item_ref, policy.default_lane, None, proposer, reach, True, at))

    return decisions


def _decide(
    item_ref: str,
    lane: Lane,
    rule_id: Optional[str],
    proposed_by: Actor,
    reach: int,
    passed_floor: bool,
    at: str,
) -> RoutingDecision:
    """Assemble a :class:`RoutingDecision` + its provenance in one place (contract §8).

    ``decided_by``: ``blocked`` ⇒ pending (awaits a human to clear the floor); a
    rule-decided lane ⇒ ``{rule, ruleId}``; precedence/default (rule_id None) ⇒ pending.
    """
    if lane == Lane.BLOCKED:
        decided_by: dict = {"type": "pending"}
    elif rule_id is not None:
        decided_by = {"type": "rule", "ruleId": rule_id}
    else:
        decided_by = {"type": "pending"}
    provenance = ProvenanceRecord(
        item_ref=item_ref,
        proposed_by=proposed_by,
        lane=lane,
        decided_by=decided_by,
        passed_floor=passed_floor,
        at=at,
        reach=reach,
    )
    return RoutingDecision(item_ref=item_ref, lane=lane, rule_id=rule_id, provenance=provenance)


# =============================================================================
# SECTION 9 — rule selection (most-specific wins)  (contract §7)
# =============================================================================

def select_rule(
    policy: ReviewPolicy,
    item: RouteItem,
    reach: int,
    is_novel: bool,
) -> Optional[tuple[ReviewRule, str]]:
    """Pick the MOST SPECIFIC matching rule (contract §7). Specificity = number of
    present conditions. Ties break on ORDER (EARLIEST index wins). Returns
    ``(rule, rule_id)`` or ``None`` (caller falls back to the default lane).

    ``rule_id`` is ``rule#<index>`` — stable per policy, good enough for audit.
    """
    best: Optional[tuple[ReviewRule, int, int]] = None  # (rule, index, specificity)
    for index, rule in enumerate(policy.rules):
        if not _rule_matches(rule, item, reach, is_novel):
            continue
        specificity = rule.specificity()
        # Strictly-more-specific wins; equal specificity keeps the EARLIER rule (first
        # seen stays — we only replace on a strictly greater specificity).
        if best is None or specificity > best[2]:
            best = (rule, index, specificity)
    if best is None:
        return None
    return best[0], f"rule#{best[1]}"


def _rule_matches(rule: ReviewRule, item: RouteItem, reach: int, is_novel: bool) -> bool:
    """Does every PRESENT condition of a rule hold for this item? (contract §7)"""
    if rule.tier is not None and tier_of(item) != rule.tier:
        return False
    if rule.kind is not None and item.kind not in rule.kind:
        return False
    if rule.reach_over is not None and not (reach > rule.reach_over):  # STRICTLY greater
        return False
    if rule.novel is not None and rule.novel != is_novel:
        return False
    return True


def tier_of(item: RouteItem) -> Tier:
    """Infer an item's tier from its display path head (contract §7, adapted §10).

    The oracle reads the token path head (``brand/``/``sys/``/``base/``); team-sync has
    no token paths, so this is the team-event *consequence tier* derived the same way —
    a caller building :class:`RouteItem`s for events sets ``path`` to a tier-bearing
    string (e.g. ``"brand/..."`` for brand-defining work, ``"shared/..."`` for
    cross-product, anything else ⇒ ``raw``). The head-segment convention is kept so the
    conformance oracle ports 1:1; the *mapping of events → path* is the team-side knob.
    Unknown head ⇒ ``raw`` (the safest, least-autonomous bucket — reachable only via an
    explicit rule).
    """
    head = (item.path.split("/")[0] if item.path else "").lower()
    if head == "brand":
        return "brand"
    if head in ("sys", "system", "semantic", "shared"):
        return "shared"
    return "raw"
