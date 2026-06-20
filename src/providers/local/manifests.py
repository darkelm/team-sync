import os
import yaml
from typing import Optional
from ...core.schemas import GovernanceToggles, TeamManifest
from ..base import ManifestProvider


# Synthetic governance tiers for the demo org (see membrane.py `Tier`/`tier_of`).
# The component tier lives on the component model (CodeComponent/DesignComponent
# `tier`); for the synthetic data — whose team.yaml files carry no tier yet — we
# stamp a handful here at load time so tier-keyed routing is demonstrable end to
# end without editing runtime data/. Keyed by component name (case-insensitive):
#   brand  — the design-system core + primitive components (highest consequence)
#   shared — components multiple teams consume cross-product
#   raw    — everything else (the default; left unstamped)
# Only applied when the loaded component is still at the default "raw", so any
# real tier authored in a team.yaml always wins.
_SYNTHETIC_TIERS: dict[str, str] = {
    # Brand / foundational — Team Nova's design system and primitives.
    "design-system": "brand",
    "tokens": "brand",
    "button": "brand",
    "forminput": "brand",
    # Shared — used across multiple teams (Nova publishes, others consume).
    "notificationbell": "shared",
    "datatable": "shared",
    "modal": "shared",
    "api-gateway": "shared",
    # Leaf components (badge, login, dashboard, …) stay "raw" by default.
}


# Synthetic per-team governance opt-in for the demo org (see schemas.GovernanceToggles
# / membrane.DesignerToggles). The synthetic team.yaml files carry no `governance:`
# key, so — exactly like the tiers above — we stamp ONE team's opt-in here at load
# time so the per-team GOVERNOR is demonstrable end to end without editing runtime
# data/. Keyed by team name (case-insensitive). Only applied when the loaded manifest
# has NO authored `governance:` block, so any real opt-in in a team.yaml always wins.
#
# Team Forge OPTS INTO small-tweaks autonomy: a low-reach, raw-tier `changed` event
# (e.g. a `code.merged` on its `StatusIndicator`, reach 0) now flows to `auto` instead
# of `review`. Brand/new-token/rename-removal guards stay ON (the conservative
# defaults), so only genuinely-small leaf tweaks earn autonomy.
#
# DELIBERATELY NOT Team Nova or Team Phoenix: tests/test_dispatch_membrane.py dispatches
# Team Nova with no policy and asserts REVIEW (and uses Team Phoenix). Those teams must
# stay toggle-free for that contract to hold.
_SYNTHETIC_GOVERNANCE: dict[str, GovernanceToggles] = {
    "team forge": GovernanceToggles(small_tweaks_flow=True),
}


class LocalManifestProvider(ManifestProvider):
    def __init__(self, teams_dir: str):
        self.teams_dir = teams_dir
        self._cache: dict[str, TeamManifest] = {}

    def _apply_synthetic_tiers(self, manifest: TeamManifest) -> None:
        """Stamp synthetic governance tiers onto components still at the default.

        No-op for any component that already declares a non-default tier in its
        team.yaml, so authored tiers always take precedence over the demo map."""
        for comp in manifest.components.code + manifest.components.design:
            if comp.tier == "raw":
                override = _SYNTHETIC_TIERS.get(comp.name.lower())
                if override:
                    comp.tier = override

    def _apply_synthetic_governance(self, manifest: TeamManifest) -> None:
        """Stamp the demo per-team governance opt-in onto a manifest with none.

        No-op for any team that already authors a `governance:` block in its
        team.yaml, so authored opt-ins always take precedence over the demo map."""
        if manifest.governance is None:
            override = _SYNTHETIC_GOVERNANCE.get(manifest.team.lower())
            if override:
                manifest.governance = override

    def _load_all(self) -> dict[str, TeamManifest]:
        if self._cache:
            return self._cache
        for entry in os.scandir(self.teams_dir):
            if entry.is_dir():
                manifest_path = os.path.join(entry.path, "team.yaml")
                if os.path.exists(manifest_path):
                    with open(manifest_path) as f:
                        data = yaml.safe_load(f)
                    manifest = TeamManifest(**data)
                    self._apply_synthetic_tiers(manifest)
                    self._apply_synthetic_governance(manifest)
                    self._cache[manifest.team.lower()] = manifest
        return self._cache

    def get_all_teams(self) -> list[TeamManifest]:
        return list(self._load_all().values())

    def get_team(self, team_name: str) -> Optional[TeamManifest]:
        teams = self._load_all()
        return teams.get(team_name.lower()) or next(
            (t for t in teams.values() if team_name.lower() in t.team.lower()), None
        )

    def find_component_owner(self, component_name: str) -> Optional[TeamManifest]:
        name_lower = component_name.lower()
        for team in self._load_all().values():
            for c in team.components.code:
                if name_lower in c.name.lower():
                    return team
            for c in team.components.design:
                if name_lower in c.name.lower():
                    return team
        return None

    def get_dependents(self, team_name: str) -> list[TeamManifest]:
        name_lower = team_name.lower()
        return [
            t for t in self._load_all().values()
            if any(d.team.lower() == name_lower for d in t.dependencies)
        ]
