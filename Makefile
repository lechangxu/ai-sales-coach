PY := .venv/bin/python

.PHONY: db-up db-down migrate seed-demo test live-smoke

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

live-smoke: seed-demo
	LLM_BACKEND=stub $(PY) scripts/live_smoke.py
