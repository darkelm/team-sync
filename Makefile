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
	# Style/static lint. Mirrors CI's ruff step.
	# NOTE: src/ has pre-existing tracked ruff issues (45 findings) — excluded from
	# the gate here exactly as CI does (see ci.yml comment). Run `$(RUFF) check src/`
	# manually to see them; do not add src/ to the gate until they're cleared.
	# tests/ currently has 6 pre-existing tracked nits in 4 files (unused imports +
	# one unused var); they are excluded below so the gate stays green and meaningful.
	# Clearing them is tracked separately — once fixed, drop the --exclude list.
	$(RUFF) check tests/ \
		--exclude tests/test_figma_live.py \
		--exclude tests/test_snapshot_scan.py \
		--exclude tests/test_strategy_outcomes.py \
		--exclude tests/test_webhook_server.py

run:
	$(PY) slack_bot.py
