PY := .venv/bin/python3
RUFF := .venv/bin/ruff

.PHONY: check test lint typecheck run

# One command to verify the repo before you push or hand it to someone.
check: lint test

# Advisory static type check. NOT in `check` yet — run it, clear/ignore the
# findings, then add `typecheck` to the `check` target to make it blocking.
# Needs mypy installed (`pip install -e .[dev]`).
typecheck:
	$(PY) -m mypy src/

test:
	SYNCBOT_TEST=1 $(PY) -m pytest -q

lint:
	# Structural guard: duplicate function/method defs (the answer() shadow bug).
	$(PY) scripts/lint.py
	# Style/static lint over the whole repo (intentional patterns ignored in pyproject).
	$(RUFF) check src/ tests/

run:
	$(PY) slack_bot.py
