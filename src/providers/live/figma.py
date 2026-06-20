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
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from ...core.schemas import (
    DesignStatus, DriftIssue, DriftSeverity, FigmaComponent,
    DevReadiness, FigmaDevStatus, FigmaComment, FigmaChange, TicketPriority,
)
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


# Figma Dev Mode `devStatus.type` -> our DevReadiness enum.
# The REST file response annotates frames with devStatus = {"type": "READY_FOR_DEV"}
# (or absent). Anything we don't recognise falls back to in_design.
_DEV_STATUS_MAP = {
    "READY_FOR_DEV": DevReadiness.ready_for_dev,
    "COMPLETED": DevReadiness.ready_for_dev,
    "IN_PROGRESS": DevReadiness.in_design,
    "READY_FOR_REVIEW": DevReadiness.needs_review,
    "NEEDS_REVIEW": DevReadiness.needs_review,
    "BLOCKED": DevReadiness.blocked,
}

# Issue-tracker key pattern for pulling linked tickets out of dev_resource names
# / URLs and comment bodies, e.g. "PHX-118", "NOVA-31".
_TICKET_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")

# Keywords that bump an unresolved comment to high priority. Figma comments have
# no native priority field, so we infer it from the message text.
_HIGH_PRIORITY_KEYWORDS = (
    "blocker", "blocked", "urgent", "asap", "critical",
    "regression", "broken", "p0", "p1", "must fix",
)


def _map_dev_readiness(dev_status: Optional[dict]) -> DevReadiness:
    """Map a Figma node `devStatus` object to our DevReadiness enum.

    Figma returns e.g. {"type": "READY_FOR_DEV"}. Unknown/absent -> in_design.
    """
    if not dev_status:
        return DevReadiness.in_design
    raw = (dev_status.get("type") or "").upper()
    return _DEV_STATUS_MAP.get(raw, DevReadiness.in_design)


def _infer_comment_priority(message: str) -> TicketPriority:
    """Infer a TicketPriority from a Figma comment body (no native field exists)."""
    text = (message or "").lower()
    if any(kw in text for kw in _HIGH_PRIORITY_KEYWORDS):
        return TicketPriority.high
    return TicketPriority.medium


def _extract_ticket_keys(*texts: str) -> list[str]:
    """Pull distinct issue-tracker keys (e.g. PHX-118) out of arbitrary text."""
    keys: list[str] = []
    seen: set[str] = set()
    for t in texts:
        for m in _TICKET_KEY_RE.findall(t or ""):
            if m not in seen:
                keys.append(m)
                seen.add(m)
    return keys


def _divergence_flavour(
    comp: FigmaComponent,
    lib_by_name: dict[str, FigmaComponent],
    lib_keys: set[str],
) -> tuple[Optional[str], Optional[FigmaComponent]]:
    """Classify how (if at all) a team component diverges from the library.

    This is the single source of truth for the divergence decision, shared by
    `get_drift_issues` (which turns the flavour into a DriftIssue) and by
    `_judge_divergence` -> `get_components` (which stamps the flag onto returned
    components). Keeping one classifier guarantees the drift report and the
    per-component flag can never disagree.

    Inputs
    ------
    comp        : a team component (is_library_component should be False).
    lib_by_name : {library component name (lower) -> library FigmaComponent}.
    lib_keys    : set of all library component keys (== FigmaComponent.id).

    Returns
    -------
    (flavour, lib_match):
      flavour   : one of "detached" (H1), "stale" (H2), "shadow" (H3), or None
                  when the component does not diverge.
      lib_match : the same-named library component when one exists, else None.

    Branch order mirrors the original `get_drift_issues` if/elif chain exactly
    (H1 then H2 then H3), so behaviour is preserved. A published library
    component never diverges; a component with no same-named library match is
    genuinely novel, not a divergence.
    """
    # A published library component is, by definition, the source of truth.
    if comp.is_library_component:
        return None, None

    lib_match = lib_by_name.get(comp.name.lower())
    if lib_match is None:
        # No same-named library component -> not a divergence (truly novel).
        return None, None

    # H1 — DETACHED / UNLINKED: same name, different key from the library.
    if comp.id not in lib_keys:
        return "detached", lib_match

    # H2 — STALE: team copy last modified before the library version.
    if comp.last_modified < lib_match.last_modified:
        return "stale", lib_match

    # H3 — NAMING SHADOW: name matches a library component yet this is a
    # non-library build that is neither detached nor stale.
    return "shadow", lib_match


def _judge_divergence(
    comp: FigmaComponent,
    lib_by_name: dict[str, FigmaComponent],
    lib_keys: set[str],
) -> tuple[bool, Optional[str]]:
    """Per-component divergence judgement used to stamp FigmaComponent objects.

    Thin wrapper over `_divergence_flavour` that returns the (diverges, notes)
    contract `get_components` needs. The note names the divergence flavour
    (detached / stale / shadow) so the governance membrane's `novel`/`propose`
    routing can read a human-readable reason straight off the component.

    Returns (False, None) for library components, components with no library
    match, and clean live instances.
    """
    flavour, lib_match = _divergence_flavour(comp, lib_by_name, lib_keys)
    if flavour is None or lib_match is None:
        return False, None

    if flavour == "detached":
        notes = (
            f"Detached from library '{lib_match.name}': team key={comp.id} "
            f"differs from library key={lib_match.id} (likely a local copy, "
            "not a live library instance)."
        )
    elif flavour == "stale":
        notes = (
            f"Stale vs library '{lib_match.name}': team copy modified "
            f"{comp.last_modified.date()} predates library update "
            f"{lib_match.last_modified.date()} (may be out of sync)."
        )
    else:  # "shadow"
        notes = (
            f"Shadows library '{lib_match.name}': non-library component shares "
            "the name (possible intentional extension or accidental duplicate)."
        )
    return True, notes


def _iter_frames(node: dict):
    """Yield FRAME / SECTION / COMPONENT nodes from a Figma document tree.

    Walks the canvas → children recursively. These are the node types that can
    carry a Dev Mode status, so they're the unit of dev-handoff readiness.
    """
    node_type = node.get("type")
    if node_type in ("FRAME", "SECTION", "COMPONENT", "COMPONENT_SET"):
        yield node
    for child in node.get("children", []) or []:
        yield from _iter_frames(child)


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
            except Exception as exc:
                log.warning("Figma._library_file_keys: could not read team manifests: %s", exc)
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
        except Exception as exc:
            log.warning("Figma._team_file_map: could not read team manifests: %s", exc)
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
        except Exception as exc:
            log.warning("Figma._used_by_teams_for: could not read team manifests: %s", exc)
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
                except Exception as exc:
                    log.debug("Could not resolve owning team for %s: %s", file_key, exc)

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

        Returns FigmaComponent objects with is_library_component=False. Each is
        post-stamped with the real `diverges_from_library` / `divergence_notes`
        signal by comparing it against the design-system library index (see
        `_stamp_divergence`). Returns [] (with a log) on any API failure for a
        given file; divergence stamping degrades to the safe default (flag
        stays False) and never raises.
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

        # Post-pass: stamp the real per-component divergence signal now that all
        # team components are gathered and the library index can be built.
        self._stamp_divergence(results)
        return results

    def _stamp_divergence(self, team_comps: list[FigmaComponent]) -> None:
        """Stamp `diverges_from_library` + `divergence_notes` on team components.

        Builds the library name/key index (via get_library_components) and runs
        the shared `_judge_divergence` classifier over each component, mutating
        it in place. This is the post-pass that turns _map_component's
        provisional False default into the truth.

        Fully defensive: any API failure (or an empty library index) leaves the
        components at their safe default (diverges_from_library=False,
        divergence_notes=None) and is logged, never raised — matching the
        Jira/Confluence house style.
        """
        if not team_comps:
            return
        try:
            library_comps = self.get_library_components()
        except Exception as exc:
            log.warning(
                "Figma._stamp_divergence: could not fetch library components, "
                "leaving divergence flags at safe default (False): %s", exc,
            )
            return

        # No library to compare against -> nothing diverges; leave defaults.
        if not library_comps:
            return

        lib_by_name: dict[str, FigmaComponent] = {
            c.name.lower(): c for c in library_comps
        }
        lib_keys: set[str] = {c.id for c in library_comps}

        for comp in team_comps:
            try:
                diverges, notes = _judge_divergence(comp, lib_by_name, lib_keys)
            except Exception as exc:
                # Per-component guard: a bad component must not abort the pass.
                log.debug(
                    "Figma._stamp_divergence: skipping %s: %s", comp.name, exc,
                )
                continue
            comp.diverges_from_library = diverges
            comp.divergence_notes = notes

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
            # Single source of truth for the divergence decision (shared with
            # the per-component stamping in get_components). flavour is one of
            # "detached" (H1), "stale" (H2), "shadow" (H3) or None; exactly one
            # fires per component, preserving the original if/elif behaviour.
            flavour, lib_match = _divergence_flavour(comp, lib_by_name, lib_keys)

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
            if flavour == "detached":
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
            elif flavour == "stale":
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
            elif flavour == "shadow":
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

    # ------------------------------------------------------------------
    # Figma-native coordination signals
    # ------------------------------------------------------------------

    def _dev_resources_by_node(self, file_key: str) -> dict[str, list[str]]:
        """Map {node_id: [ticket keys]} from a file's Dev Mode dev_resources.

        Calls GET /v1/files/{key}/dev_resources. Each dev_resource links a node
        to an external URL/name (often a Jira/issue link). We pull tracker keys
        out of the resource name and url. Returns {} (logged) on failure.
        """
        out: dict[str, list[str]] = {}
        try:
            data = self._get(f"/files/{file_key}/dev_resources")
        except Exception as exc:
            log.debug("Could not fetch dev_resources for %s: %s", file_key, exc)
            return out
        for res in data.get("dev_resources", []):
            node_id = res.get("node_id", "")
            if not node_id:
                continue
            keys = _extract_ticket_keys(res.get("name", ""), res.get("url", ""))
            if keys:
                out.setdefault(node_id, [])
                for k in keys:
                    if k not in out[node_id]:
                        out[node_id].append(k)
        return out

    def get_dev_status(self, team: Optional[str] = None) -> list[FigmaDevStatus]:
        """Fetch per-frame dev-handoff readiness for each team's Figma file.

        Calls:
          GET /v1/files/{key}            (document tree -> frames + devStatus)
          GET /v1/files/{key}/dev_resources   (linked tickets per node)

        Frames carry a Dev Mode `devStatus` ({"type": "READY_FOR_DEV"} etc.),
        which we map to DevReadiness. Linked tickets are joined from
        dev_resources by node id. Returns [] (logged) on any API failure.
        """
        results: list[FigmaDevStatus] = []
        team_file_map = self._team_file_map()

        if not team_file_map:
            log.warning(
                "LiveFigmaProvider.get_dev_status: no team file map available. "
                "Pass a ManifestProvider to the constructor."
            )
            return []

        for team_name, file_entries in team_file_map.items():
            if team and team.lower() not in team_name.lower():
                continue
            for file_key, file_name in file_entries:
                try:
                    data = self._get(f"/files/{file_key}")
                except Exception as exc:
                    log.warning(
                        "Figma API error fetching file for dev status team=%s file=%s: %s",
                        team_name, file_key, exc,
                    )
                    continue

                resolved_name = data.get("name", file_name)
                tickets_by_node = self._dev_resources_by_node(file_key)

                document = data.get("document", {})
                for canvas in document.get("children", []) or []:
                    for frame in _iter_frames(canvas):
                        dev_status = frame.get("devStatus")
                        # Only surface frames that actually carry a dev status —
                        # otherwise every frame in the file would be reported.
                        if not dev_status:
                            continue
                        node_id = frame.get("id", "")
                        try:
                            results.append(FigmaDevStatus(
                                node_id=node_id,
                                name=frame.get("name", node_id),
                                file_id=file_key,
                                file_name=resolved_name,
                                team=team_name,
                                readiness=_map_dev_readiness(dev_status),
                                last_modified=_parse_datetime(
                                    frame.get("lastModified") or data.get("lastModified")
                                ),
                                linked_tickets=tickets_by_node.get(node_id, []),
                                assignee=None,
                                notes=(dev_status.get("description") or None),
                            ))
                        except Exception as exc:
                            log.debug(
                                "Skipping dev status for node %s in %s: %s",
                                node_id, file_key, exc,
                            )

        return results

    def get_open_comments(self, team: Optional[str] = None) -> list[FigmaComment]:
        """Fetch unresolved comment threads for each team's Figma file.

        Calls GET /v1/files/{key}/comments. Resolved comments (those with a
        resolved_at timestamp) are filtered out. Priority is inferred from the
        message body (Figma has no native priority). Returned highest-priority
        first. Returns [] (logged) on any API failure.
        """
        results: list[FigmaComment] = []
        team_file_map = self._team_file_map()

        if not team_file_map:
            log.warning(
                "LiveFigmaProvider.get_open_comments: no team file map available. "
                "Pass a ManifestProvider to the constructor."
            )
            return []

        for team_name, file_entries in team_file_map.items():
            if team and team.lower() not in team_name.lower():
                continue
            for file_key, file_name in file_entries:
                try:
                    data = self._get(f"/files/{file_key}/comments")
                except Exception as exc:
                    log.warning(
                        "Figma API error fetching comments team=%s file=%s: %s",
                        team_name, file_key, exc,
                    )
                    continue

                for raw in data.get("comments", []):
                    resolved = bool(raw.get("resolved_at"))
                    if resolved:
                        continue
                    message = raw.get("message", "")
                    user = raw.get("user", {}) or {}
                    client_meta = raw.get("client_meta", {}) or {}
                    node_id = client_meta.get("node_id")
                    try:
                        results.append(FigmaComment(
                            id=str(raw.get("id", "")),
                            file_id=file_key,
                            file_name=file_name,
                            team=team_name,
                            author=user.get("handle", "") or user.get("id", ""),
                            message=message,
                            created_at=_parse_datetime(raw.get("created_at")),
                            resolved=False,
                            priority=_infer_comment_priority(message),
                            node_id=node_id,
                            mentions=[
                                m.get("handle", "")
                                for m in raw.get("mentions", []) or []
                                if m.get("handle")
                            ],
                        ))
                    except Exception as exc:
                        log.debug(
                            "Skipping comment %s in %s: %s",
                            raw.get("id"), file_key, exc,
                        )

        priority_rank = {
            TicketPriority.critical: 0, TicketPriority.high: 1,
            TicketPriority.medium: 2, TicketPriority.low: 3,
        }
        return sorted(
            results,
            key=lambda c: (priority_rank.get(c.priority, 99), c.created_at),
        )

    def get_recent_changes(self, team: Optional[str] = None, days: int = 7) -> list[FigmaChange]:
        """Fetch recent version history for each team's Figma file.

        Calls GET /v1/files/{key}/versions. Returns versions whose created_at
        falls within the last `days`, newest first. Returns [] (logged) on any
        API failure.
        """
        results: list[FigmaChange] = []
        team_file_map = self._team_file_map()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        if not team_file_map:
            log.warning(
                "LiveFigmaProvider.get_recent_changes: no team file map available. "
                "Pass a ManifestProvider to the constructor."
            )
            return []

        for team_name, file_entries in team_file_map.items():
            if team and team.lower() not in team_name.lower():
                continue
            for file_key, file_name in file_entries:
                try:
                    data = self._get(f"/files/{file_key}/versions")
                except Exception as exc:
                    log.warning(
                        "Figma API error fetching versions team=%s file=%s: %s",
                        team_name, file_key, exc,
                    )
                    continue

                for ver in data.get("versions", []):
                    changed_at = _parse_datetime(ver.get("created_at"))
                    if changed_at < cutoff:
                        continue
                    user = ver.get("user", {}) or {}
                    try:
                        results.append(FigmaChange(
                            id=str(ver.get("id", "")),
                            file_id=file_key,
                            file_name=file_name,
                            team=team_name,
                            label=ver.get("label") or "Untitled version",
                            description=ver.get("description", "") or "",
                            changed_at=changed_at,
                            author=user.get("handle", ""),
                            affected_frames=[],
                        ))
                    except Exception as exc:
                        log.debug(
                            "Skipping version %s in %s: %s",
                            ver.get("id"), file_key, exc,
                        )

        return sorted(results, key=lambda c: c.changed_at, reverse=True)


# ---------------------------------------------------------------------------
# Drift heuristic false-positive / false-negative profile (reference)
# ---------------------------------------------------------------------------
#
# These three heuristics are now classified in ONE place — `_divergence_flavour`
# — and consumed by two callers that can never disagree:
#   * get_drift_issues  -> one DriftIssue per diverging component (H1/H2/H3
#     mapping to high/medium/low severity, unchanged).
#   * get_components (via _stamp_divergence -> _judge_divergence) -> stamps
#     FigmaComponent.diverges_from_library (+ divergence_notes) so the
#     governance membrane's novel/propose path reads a real per-component flag.
# The classifier matches a team component to a library component by EXACT
# lower-cased name; a library component never diverges and a no-match component
# is treated as genuinely novel (flag stays False), not a divergence.
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
#   FP: Any non-library team component whose name exactly matches a library
#       component name (and is neither detached nor stale) will be flagged.
#       This has the highest FP rate of the three heuristics — an intentional
#       local extension reusing the base name reads as a shadow.
#   FN: Components with similar but non-identical names escape detection.
#
# Overall: these heuristics are best used as a triage signal, not a
# definitive audit.  Treat high-severity issues (H1) with more confidence;
# treat low-severity (H3) as "worth reviewing."
