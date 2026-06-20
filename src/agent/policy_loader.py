"""Per-team policy resolution — the bridge from config opt-in to the membrane.

Today the membrane is a notifier: every event is routed under the conservative
``default_policy()`` (all-``review``) unless a caller hands in a policy. This module
makes autonomy something a team OPTS INTO via its manifest. Given a team name, it
looks up the team's `governance:` toggles (schemas.GovernanceToggles, the config
mirror of membrane.DesignerToggles), maps them onto the membrane, and compiles the
matching :class:`ReviewPolicy`. No toggles (field absent / all-off) or any lookup
failure ⇒ the conservative ``default_policy()``.

INVARIANT: this function must NEVER raise. Resolving a policy is on the hot path of
every dispatch; a bad team name, a missing manifest, or a provider blow-up degrades
to the safe, fully-gated default — it never breaks routing. Autonomy is EARNED by an
explicit, valid opt-in; the absence of one is always the conservative posture.
"""
from __future__ import annotations

from . import membrane
from .membrane import DesignerToggles, ReviewPolicy, compile_policy_from_toggles, default_policy


def _toggles_from_governance(gov) -> DesignerToggles:
    """Map a schemas.GovernanceToggles onto the membrane's DesignerToggles.

    Field-for-field (the two models are kept in sync deliberately). Read defensively
    via getattr so a partially-shaped object can never raise — a missing field falls
    back to the conservative DesignerToggles default.
    """
    base = DesignerToggles()
    return DesignerToggles(
        brand_changes_always_ask=getattr(gov, "brand_changes_always_ask", base.brand_changes_always_ask),
        new_tokens_always_ask=getattr(gov, "new_tokens_always_ask", base.new_tokens_always_ask),
        renames_removals_always_ask=getattr(gov, "renames_removals_always_ask", base.renames_removals_always_ask),
        small_tweaks_flow=getattr(gov, "small_tweaks_flow", base.small_tweaks_flow),
        spacing_tweaks_flow=getattr(gov, "spacing_tweaks_flow", base.spacing_tweaks_flow),
    )


def policy_for_team(team_name: str, providers) -> ReviewPolicy:
    """Resolve the ReviewPolicy a team has opted into, or the conservative default.

    Looks up the team's manifest; if it carries `governance:` toggles, maps them to
    DesignerToggles and returns ``compile_policy_from_toggles(...)``. With NO toggles
    (the field is absent), an UNKNOWN team / missing manifest, or ANY failure along
    the way, returns ``default_policy()`` (all-``review``). Never raises.
    """
    try:
        if not team_name or providers is None:
            return default_policy()
        manifest = providers.manifests.get_team(team_name)
        gov = getattr(manifest, "governance", None) if manifest is not None else None
        if gov is None:
            return default_policy()
        return compile_policy_from_toggles(_toggles_from_governance(gov))
    except Exception as e:  # never let policy resolution break routing
        print(f"[policy_loader] policy_for_team({team_name!r}) failed: {e}; using default_policy", flush=True)
        return default_policy()


__all__ = ["policy_for_team", "membrane"]
