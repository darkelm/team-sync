PY := .venv/bin/python3

.PHONY: check test lint run

# One command to verify the repo before you push or hand it to someone.
check: lint test

test:
	SYNCBOT_TEST=1 $(PY) -m pytest -q

lint:
	$(PY) scripts/lint.py

run:
	$(PY) slack_bot.py
