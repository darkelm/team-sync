"""Durable, append-only provenance store for the governance membrane.

The membrane (`membrane.py`) is PURE — it returns decisions and never persists. This
module is the injected IO seam (contract §3/§9): it appends one :class:`ProvenanceRecord`
per routing decision to a durable file so SyncBot can answer "who decided this, and when"
weeks later — the "never just the loop decided" guarantee (contract §0 invariant 4).

Mirrors the `preferences.py` JSON idiom, but as JSON Lines (`.jsonl`): one record per
line. JSONL is the right fit for an append-only "one row per decision" log — it appends
without rewriting the whole file, and tails cheaply for `recent(n)`.

⚠️ DURABILITY (contract §5 open question Q7): Railway's filesystem is ephemeral across
deploys. `data/` is the same dir `notification_prefs.json` already lives in; whether it
survives via a volume is an open question the orchestrator must confirm before relying on
this in production. The store works against any path — the path is a module constant
(`PROVENANCE_PATH`) so tests redirect it and prod can point it at a mounted volume.
"""
from __future__ import annotations

import json
import os
from typing import Any

# Module constant so tests can redirect it (monkeypatch) and prod can point it at a
# durable volume. Mirrors `preferences.py`'s default-path-under-data/ idiom.
# On an ephemeral host (e.g. Railway without a mounted volume) the audit trail is
# lost on redeploy; set SYNCBOT_PROVENANCE_PATH to a path on a persistent volume
# (e.g. /data/provenance.jsonl) to keep it durable.
PROVENANCE_PATH = "data/provenance.jsonl"


class ProvenanceStore:
    """Append-only, durable provenance log (the durable analog of token-sync's
    in-memory-only ``InMemoryProvenanceStore``).

    Interface mirrors the contract's :class:`ProvenanceStore` seam:
      - :meth:`append` — append one record.
      - :meth:`recent` — the most recent ``n`` records, newest LAST (insertion order
        preserved); ``n <= 0`` ⇒ ``[]``.

    Accepts either a :class:`membrane.ProvenanceRecord` (it is normalized via
    ``.to_dict()``) or a plain ``dict`` already in record shape.
    """

    def __init__(self, path: str | None = None):
        # Precedence: explicit arg → SYNCBOT_PROVENANCE_PATH env (durable volume) →
        # module default. The env read happens here (not at import) so a deploy can
        # set it without reordering imports; tests that monkeypatch PROVENANCE_PATH
        # and set no env var still win.
        self.path = path if path is not None else os.getenv("SYNCBOT_PROVENANCE_PATH", PROVENANCE_PATH)

    def append(self, record: Any) -> None:
        """Append one provenance record as a single JSONL line."""
        row = record.to_dict() if hasattr(record, "to_dict") else dict(record)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(row) + "\n")

    def append_decisions(self, decisions: list[Any]) -> None:
        """Append every decision's provenance in order — the convenience the caller
        uses after a routing pass (the Python analog of ``recordDecisions``)."""
        for decision in decisions:
            self.append(decision.provenance)

    def recent(self, n: int) -> list[dict]:
        """The most recent ``n`` records, newest LAST. ``n <= 0`` ⇒ ``[]``. Missing or
        unreadable file ⇒ ``[]`` (a fresh store has no history yet)."""
        if n <= 0:
            return []
        rows = self._read_all()
        return rows[-n:] if n < len(rows) else rows

    def all(self) -> list[dict]:
        """Every record, oldest first."""
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
                        # A single corrupt line should not nuke the whole audit log;
                        # skip it and keep the rest. (Append-only ⇒ corruption is rare
                        # and localized.)
                        continue
        except OSError:
            return []
        return out
