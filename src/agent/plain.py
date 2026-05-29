"""Plain-language layer — de-jargon outputs for non-technical audiences.

Leadership and designers shouldn't need to know what "drift" or "a PR" is to
get value. `plainify` swaps dev-isms for plain words; `labels` loads the
configurable unit/portfolio terminology (team/workstream/squad, etc.).
"""
from __future__ import annotations
import re
import yaml

# Dev term → plain term. Applied case-insensitively, whole-word.
PLAIN_TERMS = {
    "drift": "inconsistency",
    "drifted": "diverged",
    "PR": "code change",
    "PRs": "code changes",
    "pull request": "code change",
    "merge": "code change",
    "manifest": "team profile",
    "repo": "codebase",
    "ticket": "work item",
    "component": "feature area",
    "decision log": "decision record",
}


def labels(config: str = "config.yaml") -> dict:
    try:
        with open(config) as f:
            lead = (yaml.safe_load(f) or {}).get("leadership", {})
    except OSError:
        lead = {}
    return {
        "unit": lead.get("unit_label", "team"),
        "portfolio": lead.get("portfolio_label", "portfolio"),
        "exec_channel": lead.get("exec_channel", ""),
    }


def plainify(text: str) -> str:
    """Replace dev jargon with plain language (whole-word, case-insensitive)."""
    out = text
    for term, plain in sorted(PLAIN_TERMS.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(rf"\b{re.escape(term)}\b", plain, out, flags=re.IGNORECASE)
    return out
