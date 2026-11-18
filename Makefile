PY := .venv/bin/python

.PHONY: db-up db-down migrate seed-demo test eval live-smoke

db-up:
	docker compose up -d --wait db

db-down:
	docker compose down

migrate: db-up
	$(PY) -m alembic upgrade head

# Idempotent: every run clears the demo tables and re-inserts the same 50 transcripts.
seed-demo: migrate
	$(PY) scripts/generate_demo_data.py --count 50

test: db-up
	$(PY) -m pytest -q

# One-command quality eval: migrate + seed + QWK over the 50 synthetic calls.
eval: seed-demo
	$(PY) scripts/eval_qwk.py --expect-stable-rerun --expect-scored-min 50

live-smoke: seed-demo
	LLM_BACKEND=stub $(PY) scripts/live_smoke.py
