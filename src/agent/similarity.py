"""Lightweight text similarity — no external deps.

Token-overlap (Jaccard) over normalized words, with a small domain stopword
list. Good enough to surface "these look related" candidates for human review.
The Claude agent upgrades this to true semantic similarity when enabled.
"""
import re

_STOP = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with", "is",
    "are", "be", "by", "at", "from", "this", "that", "all", "new", "update",
    "updates", "feat", "fix", "chore", "team", "component", "service", "support",
    "across", "via", "v1", "v2", "v3",
}


def tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def jaccard(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


def overlap_terms(a: str, b: str) -> list[str]:
    return sorted(tokenize(a) & tokenize(b))
