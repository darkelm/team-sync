"""Live Figma provider — reads real Figma REST API data.

Auth:    FIGMA_ACCESS_TOKEN env var, sent as X-Figma-Token header.
API:     https://api.figma.com/v1

Rate limits: Figma enforces per-minute limits (~60 req/min for personal
tokens, higher for OAuth). Every call is wrapped in try/except; on any
failure the method returns [] and logs, never raising (matches the
Jira/Confluence house style). A simple in-memory TTL cache is used to
avoid redundant calls within the same session.

Drift heuristics (documented false-positive/negative profile at bottom).
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from ...core.schemas import DesignStatus, DriftIssue, DriftSeverity, FigmaComponent
from ..base import FigmaProvider

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIGMA_FILE_KEY_RE = re.compile(
    r"figma\.com/(?:file|design)/([A-Za-z0-9_-]+)"
)

_CACHE_TTL_SECONDS = 300  # 5-minute in-memory cache


def _parse_file_key(url: str) -> Optional[str]:
    """Extract the Figma file key from a figma.com URL.

    Supports both legacy /file/<key>/... and newer /design/<key>/... URLs.
    Returns None if the URL doesn't match a known pattern.

    Examples
    --------
    https://figma.com/file/abc123/my-file   -> "abc123"
    https://www.figma.com/design/XYZ/name   -> "XYZ"
    https://figma.com/file/nova-design-system/nova-ds -> "nova-design-system"
    """
    if not url:
        return None
    m = _FIGMA_FILE_KEY_RE.search(url)
    return m.group(1) if m else None


def _parse_datetime(ts: Optional[str]) -> datetime:
    """Parse an ISO-8601 timestamp string to an aware UTC datetime.

    Returns the current UTC time if the string is missing or unparseable,
    which is the safest fallback for freshness comparisons.
    """
    if not ts:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


class _Cache:
    """Minimal TTL dict — avoids hammering Figma for repeated calls."""

    def __init__(self, ttl: int = _CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str) -> Optional[object]:
        entry = self._store.get(key)
        if entry and (time.monotonic() - entry[0]) < self._ttl:
            return entry[1]
        return None

    def set(self, key: str, value: object) -> None:
        self._store[key] = (time.monotonic(), value)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class LiveFigmaProvider(FigmaProvider):
    """Reads live component data from the Figma REST API.

    Constructor reads `FIGMA_ACCESS_TOKEN` from the environment (raises
    KeyError if absent, consistent with the other live providers).

    The optional `manifests` parameter (a ManifestProvider instance) enables
    two enrichments:
      - extracting team file keys from team manifests rather than relying on
        the FIGMA_LIBRARY_FILE_KEY env var alone;
      - populating `used_by_teams` on components from manifest data.

    When manifests=None the provider still works — it falls back to only the
    FIGMA_LIBRARY_FILE_KEY env var for library files and to an empty
    used_by_teams list.
    """

    def __init__(self, manifests=None) -> None:
        self.token = os.environ["FIGMA_ACCESS_TOKEN"]
        self.headers = {"X-Figma-Token": self.token}
        self.manifests = manifests
        self._cache = _Cache()

    # ------------------------------------------------------------------
    # Internal HTTP
    # ------------------------------------------------------------------

    def _get(self, path: str) -> dict:
        """GET /v1{path} and return the parsed JSON body.

        Raises httpx.HTTPStatusError on 4xx/5xx so callers can catch it.
        """
        cache_key = path
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        resp = httpx.get(
            f"https://api.figma.com/v1{path}",
            headers=self.headers,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        self._cache.set(cache_key, data)
        return data

    # ------------------------------------------------------------------
    # Key derivation helpers
    # ------------------------------------------------------------------

    def _library_file_keys(self) -> list[str]:
        """Collect distinct design-system library file keys.

        Sources (in priority order, duplicates dropped):
        1. FIGMA_LIBRARY_FILE_KEY env var (comma-separated list supported).
        2. design_system_library URL in each team manifest.
        """
        keys: list[str] = []
        seen: set[str] = set()

        # 1. Explicit env var
        env_val = os.environ.get("FIGMA_LIBRARY_FILE_KEY", "")
        for raw in env_val.split(","):
            k = raw.strip()
            if k and k not in seen:
                keys.append(k)
                seen.add(k)

        # 2. Team manifests
        if self.manifests:
            try:
                teams = self.manifests.get_all_teams()
            except Exception:
                teams = []
            for team in teams:
                url = team.design_system_library or ""
                k = _parse_file_key(url)
                if k and k not in seen:
                    keys.append(k)
                    seen.add(k)

        return keys

    def _team_file_map(self) -> dict[str, list[tuple[str, str]]]:
        """Return {team_name: [(file_key, file_name), ...]} from manifests.

        Falls back to empty dict if manifests are unavailable.
        """
        result: dict[str, list[tuple[str, str]]] = {}
        if not self.manifests:
            return result
        try:
            teams = self.manifests.get_all_teams()
        except Exception:
            return result
        for team in teams:
            entries: list[tuple[str, str]] = []
            for ff in team.figma_files:
                k = _parse_file_key(ff.url)
                if k:
                    entries.append((k, ff.name))
            if entries:
                result[team.team] = entries
        return result

    def _used_by_teams_for(self, component_name: str, file_key: str) -> list[str]:
        """Derive which teams use a component from manifest data.

        A team is considered a user if:
        - it references the component by name in components.design, OR
        - one of its figma_files has a key matching file_key.
        """
        using: list[str] = []
        if not self.manifests:
            return using
        try:
            teams = self.manifests.get_all_teams()
        except Exception:
            return using
        for team in teams:
            name_lower = component_name.lower()
            # Check design component names
            design_names = [dc.name.lower() for dc in team.components.design]
            if name_lower in design_names:
                using.append(team.team)
                continue
            # Check figma_files file keys
            file_keys = [_parse_file_key(ff.url) for ff in team.figma_files]
            if file_key in file_keys:
                using.append(team.team)
        return list(dict.fromkeys(using))  # deduplicate, preserve order

    # ------------------------------------------------------------------
    # Mapping helpers
    # ------------------------------------------------------------------

    def _map_component(
        self,
        item: dict,
        file_id: str,
        file_name: str,
        team: str,
        is_library: bool = False,
        variant_names: Optional[list[str]] = None,
    ) -> FigmaComponent:
        """Map a raw Figma API component dict to a FigmaComponent schema object.

        The Figma /files/{key}/components response returns items with fields:
          key, name, description, updated_at, (containing_frame, component_set_id…)

        We intentionally use `key` (the global component identifier) as our
        schema `id`, because it is stable and globally unique across Figma,
        unlike node_id which is file-scoped.
        """
        raw_updated = item.get("updated_at") or item.get("last_modified") or ""
        last_modified = _parse_datetime(raw_updated)

        used_by = self._used_by_teams_for(item["name"], file_id)

        return FigmaComponent(
            id=item["key"],
            name=item["name"],
            file_id=file_id,
            file_name=file_name,
            team=team,
            description=item.get("description", ""),
            status=DesignStatus.dev_ready,
            last_modified=last_modified,
            variants=variant_names or [],
            used_by_teams=used_by,
            is_library_component=is_library,
            diverges_from_library=False,
        )

    # ------------------------------------------------------------------
    # FigmaProvider ABC methods
    # ------------------------------------------------------------------

    def get_library_components(self) -> list[FigmaComponent]:
        """Fetch all published components from each design-system library file.

        Calls:
          GET /v1/files/{key}/components          (atomic components)
          GET /v1/files/{key}/component_sets      (variant groupings → variant names)

        Returns FigmaComponent objects with is_library_component=True.
        Returns [] and logs on any API failure.
        """
        results: list[FigmaComponent] = []
        keys = self._library_file_keys()

        if not keys:
            log.warning(
                "LiveFigmaProvider.get_library_components: no library file keys found. "
                "Set FIGMA_LIBRARY_FILE_KEY or add design_system_library to a team manifest."
            )
            return []

        for file_key in keys:
            try:
                data = self._get(f"/files/{file_key}/components")
            except Exception as exc:
                log.warning("Figma API error fetching library components for %s: %s", file_key, exc)
                continue

            meta = data.get("meta", {})
            components = meta.get("components", [])
            file_name = data.get("name", file_key)

            # Fetch variant sets to resolve variant names per component_set_id
            variant_map: dict[str, list[str]] = {}
            try:
                set_data = self._get(f"/files/{file_key}/component_sets")
                for cs in set_data.get("meta", {}).get("component_sets", []):
                    # Each component_set has a list of component_ids
                    cs_id = cs.get("key", "")
                    cs_name = cs.get("name", "")
                    if cs_id and cs_name:
                        variant_map[cs_id] = variant_map.get(cs_id, [])
                        variant_map[cs_id].append(cs_name)
            except Exception as exc:
                log.debug("Could not fetch component_sets for %s: %s", file_key, exc)

            # Determine team name — use manifest owner if available
            team_name = "Design System"
            if self.manifests:
                try:
                    teams = self.manifests.get_all_teams()
                    for t in teams:
                        for ff in t.figma_files:
                            if _parse_file_key(ff.url) == file_key:
                                team_name = t.team
                                file_name = ff.name or file_name
                                break
                except Exception:
                    pass

            for item in components:
                cs_id = item.get("component_set_id", "")
                variants = variant_map.get(cs_id, [])
                try:
                    comp = self._map_component(
                        item,
                        file_id=file_key,
                        file_name=file_name,
                        team=team_name,
                        is_library=True,
                        variant_names=variants,
                    )
                    results.append(comp)
                except Exception as exc:
                    log.debug("Skipping library component %s: %s", item.get("name"), exc)

        return results

    def get_components(self, team: Optional[str] = None) -> list[FigmaComponent]:
        """Fetch components from all teams' Figma files.

        For each team (optionally filtered by `team`), iterates the team's
        figma_files and calls GET /v1/files/{key}/components.

        Returns FigmaComponent objects with is_library_component=False.
        Returns [] (with a log) on any API failure for a given file.
        """
        results: list[FigmaComponent] = []
        team_file_map = self._team_file_map()

        if not team_file_map:
            log.warning(
                "LiveFigmaProvider.get_components: no team file map available. "
                "Pass a ManifestProvider to the constructor."
            )
            return []

        for team_name, file_entries in team_file_map.items():
            if team and team.lower() not in team_name.lower():
                continue
            for file_key, file_name in file_entries:
                try:
                    data = self._get(f"/files/{file_key}/components")
                except Exception as exc:
                    log.warning(
                        "Figma API error fetching components for team=%s file=%s: %s",
                        team_name, file_key, exc,
                    )
                    continue

                meta = data.get("meta", {})
                components = meta.get("components", [])
                resolved_name = data.get("name", file_name)

                for item in components:
                    try:
                        comp = self._map_component(
                            item,
                            file_id=file_key,
                            file_name=resolved_name,
                            team=team_name,
                            is_library=False,
                        )
                        results.append(comp)
                    except Exception as exc:
                        log.debug(
                            "Skipping component %s in team=%s file=%s: %s",
                            item.get("name"), team_name, file_key, exc,
                        )

        return results

    def get_components_by_name(self, name: str) -> list[FigmaComponent]:
        """Return all components (library + team) whose name contains `name`.

        Case-insensitive substring match, consistent with the local provider.
        Merges library components and team components and deduplicates by id.
        """
        name_lower = name.lower()
        seen: set[str] = set()
        results: list[FigmaComponent] = []

        all_components = self.get_library_components() + self.get_components()
        for comp in all_components:
            if name_lower in comp.name.lower() and comp.id not in seen:
                results.append(comp)
                seen.add(comp.id)

        return results

    def get_drift_issues(self) -> list[DriftIssue]:
        """Detect design drift using three heuristics.

        Figma's API does not expose a first-class "instance diverged from
        main component" signal. All three heuristics are inferred from
        structural and temporal metadata. See false-positive/negative profile
        at bottom of file.

        Returns DriftIssue objects matching the shape produced by the local
        provider, so live and local are interchangeable for downstream tools.
        """
        issues: list[DriftIssue] = []
        now = datetime.now(timezone.utc)

        # Fetch library and team components (both return [] gracefully on error)
        try:
            library_comps = self.get_library_components()
        except Exception as exc:
            log.warning("get_drift_issues: could not fetch library components: %s", exc)
            library_comps = []

        try:
            team_comps = self.get_components()
        except Exception as exc:
            log.warning("get_drift_issues: could not fetch team components: %s", exc)
            team_comps = []

        # Build lookup index: library component name (lower) -> component
        lib_by_name: dict[str, FigmaComponent] = {
            c.name.lower(): c for c in library_comps
        }
        lib_keys: set[str] = {c.id for c in library_comps}

        for comp in team_comps:
            name_lower = comp.name.lower()
            lib_match = lib_by_name.get(name_lower)

            # ----------------------------------------------------------------
            # Heuristic 1 — DETACHED / UNLINKED
            # A team file contains a component with the same name as a library
            # component, but its key differs from the library key.  This
            # indicates a local copy rather than a live library instance.
            #
            # False positives:  legitimate local extensions that happen to
            # share a name with a library component.
            # False negatives:  detached components with a renamed name won't
            # be caught; library instances still have a different key by design
            # (Figma key = component definition id, not the instance node id).
            # ----------------------------------------------------------------
            if lib_match and comp.id not in lib_keys:
                issues.append(DriftIssue(
                    id=f"design-drift-detached-{comp.id}",
                    type="design_drift",
                    # Inferred: name match + different key ≈ likely detached copy
                    severity=DriftSeverity.high,
                    title=f"Detached component: {comp.name} in {comp.team}",
                    description=(
                        f"{comp.team} has a component named '{comp.name}' "
                        f"(key={comp.id}) that matches a library component "
                        f"(key={lib_match.id}) but is not the library version. "
                        "This is inferred from mismatched keys — it may be a "
                        "detached instance or a renamed copy."
                    ),
                    teams_involved=[comp.team],
                    components_involved=[comp.name],
                    detected_at=now,
                    suggested_action=(
                        "Replace the local copy with an instance of the shared "
                        f"library component '{lib_match.name}' from the design system."
                    ),
                ))

            # ----------------------------------------------------------------
            # Heuristic 2 — STALE (out of sync)
            # The team file's component was last updated before the library
            # component's last_modified date, implying the team copy has not
            # picked up a library update.
            #
            # False positives:  components legitimately frozen at an older
            # state, or where `updated_at` reflects metadata edits (name
            # rename etc.) rather than visual changes.
            # False negatives:  Figma only records the last modification
            # timestamp, not semantic version; minor visual updates may not
            # bump `updated_at` in all contexts.
            # ----------------------------------------------------------------
            elif lib_match and comp.last_modified < lib_match.last_modified:
                issues.append(DriftIssue(
                    id=f"design-drift-stale-{comp.id}",
                    type="design_drift",
                    # Inferred: team component predates library update
                    severity=DriftSeverity.medium,
                    title=f"Stale component: {comp.name} in {comp.team}",
                    description=(
                        f"{comp.team}'s '{comp.name}' was last modified "
                        f"{comp.last_modified.date()} but the library version "
                        f"was updated {lib_match.last_modified.date()}. "
                        "The team copy may be out of sync. "
                        "This is inferred from timestamps — verify visually."
                    ),
                    teams_involved=[comp.team],
                    components_involved=[comp.name],
                    detected_at=now,
                    suggested_action=(
                        "Review the library update for "
                        f"'{lib_match.name}' and re-sync or re-link the team copy."
                    ),
                ))

            # ----------------------------------------------------------------
            # Heuristic 3 — NAMING MATCH WITH NO LIBRARY LINK
            # The team component name matches a library component but it is a
            # new component (not in our team-component list with the lib key),
            # and we couldn't determine staleness (same or newer timestamp),
            # yet the component is not flagged as a library component.  This
            # suggests a custom re-implementation that shadows a library item.
            #
            # False positives:  teams that intentionally maintain a local
            # extended variant with the same base name.
            # False negatives:  components with subtly different names
            # (e.g., "NotifBell" vs "NotificationBell") won't be caught.
            # ----------------------------------------------------------------
            elif lib_match and not comp.is_library_component:
                issues.append(DriftIssue(
                    id=f"design-drift-custom-{comp.id}",
                    type="design_drift",
                    # Inferred: name match but component is a custom build
                    severity=DriftSeverity.low,
                    title=f"Custom implementation shadows library: {comp.name} in {comp.team}",
                    description=(
                        f"{comp.team} has a non-library component '{comp.name}' "
                        "that matches a design-system library component by name. "
                        "This may be an intentional extension or an accidental "
                        "duplicate. Inferred from name match only."
                    ),
                    teams_involved=[comp.team],
                    components_involved=[comp.name],
                    detected_at=now,
                    suggested_action=(
                        "Confirm whether this is an intentional local extension "
                        f"or if the library component '{lib_match.name}' should "
                        "be used instead."
                    ),
                ))

        return issues


# ---------------------------------------------------------------------------
# Drift heuristic false-positive / false-negative profile (reference)
# ---------------------------------------------------------------------------
#
# Heuristic 1 — Detached/unlinked (key mismatch):
#   FP: A team legitimately creates a local component with the same name as a
#       library component (e.g., a scoped variant).  The key will differ even
#       if the component is intentionally standalone.
#   FN: Truly detached Figma instances retain the original component's key —
#       the API does not expose "is_instance_detached" directly.  Only
#       *locally-authored* components with matching names are caught here.
#
# Heuristic 2 — Stale (timestamp):
#   FP: Metadata-only library updates (renaming, reordering) bump updated_at
#       without changing visual content, making a team's copy appear stale.
#   FN: Visual changes that don't update the top-level updated_at (e.g.,
#       changes to nested nodes within a component) won't be reflected.
#       Also, Figma's updated_at is timezone-naive in some responses; we
#       normalise to UTC but edge cases around DST may produce false flags.
#
# Heuristic 3 — Naming shadow:
#   FP: Any team component whose name happens to match a library component
#       name (even partially after the name_lower check) will be flagged.
#       This has the highest FP rate of the three heuristics.
#   FN: Components with similar but non-identical names escape detection.
#
# Overall: these heuristics are best used as a triage signal, not a
# definitive audit.  Treat high-severity issues (H1) with more confidence;
# treat low-severity (H3) as "worth reviewing."
