#!/usr/bin/env python3
"""Lightweight structural lint for SyncBot.

Guards the bug classes this codebase has actually hit:
  1. Duplicate function/method definitions in the same scope (the answer()
     shadowing bug — commit 41e8971).

Run: python3 scripts/lint.py   (or `make lint`)
Exits non-zero on any finding.
"""
from __future__ import annotations
import ast
import os
import sys

SKIP = {".venv", "__pycache__", ".git", ".pytest_cache", ".ruff_cache", "tests"}


def find_duplicate_defs(path: str) -> list[str]:
    try:
        tree = ast.parse(open(path).read())
    except (OSError, SyntaxError) as e:
        return [f"{path}: could not parse ({e})"]
    findings = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        seen: dict[str, list[int]] = {}
        for child in body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seen.setdefault(child.name, []).append(child.lineno)
        for name, lines in seen.items():
            if len(lines) > 1:
                findings.append(f"{path}: duplicate def {name}() at lines {lines}")
    return findings


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    findings: list[str] = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for fn in files:
            if fn.endswith(".py"):
                findings.extend(find_duplicate_defs(os.path.join(dirpath, fn)))
    if findings:
        print("\n".join(findings))
        print(f"\nlint FAILED: {len(findings)} finding(s)")
        return 1
    print("lint ok: no duplicate definitions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
