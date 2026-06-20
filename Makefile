PY := .venv/bin/python3
RUFF := .venv/bin/ruff

.PHONY: check test lint run

# One command to verify the repo before you push or hand it to someone.
check: lint test

test:
	SYNCBOT_TEST=1 $(PY) -m pytest -q

lint:
	# Structural guard: duplicate function/method defs (the answer() shadow bug).
	$(PY) scripts/lint.py
	# Style/static lint over the whole repo (intentional patterns ignored in pyproject).
	$(RUFF) check src/ tests/

run:
	$(PY) slack_bot.py
