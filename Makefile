# Trip Package — common tasks.
# `make setup && make seed && make dev` takes a clean checkout to a running app.

PY := backend/.venv/bin

.PHONY: help setup db migrate seed api web dev test lint clean reset

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv, install backend + frontend deps
	python3.12 -m venv backend/.venv
	$(PY)/pip install -q --upgrade pip
	$(PY)/pip install -q -e "backend[dev]"
	cd frontend && npm install
	@test -f .env || cp .env.example .env
	@echo "Setup complete. Next: make seed"

db: ## Start Postgres+PostGIS and wait for it to be healthy
	docker compose up -d db
	@until [ "$$(docker inspect -f '{{.State.Health.Status}}' trip_package_db 2>/dev/null)" = "healthy" ]; do \
		printf '.'; sleep 1; done; echo " database ready"

migrate: db ## Apply database migrations
	cd backend && .venv/bin/alembic upgrade head

seed: migrate ## Full pipeline: ingest all sources, score, tag
	$(PY)/trip ingest
	$(PY)/trip score
	$(PY)/trip tag
	$(PY)/trip status

seed-offline: migrate ## Same, but force fixture mode for every source
	$(PY)/trip ingest --fixtures
	$(PY)/trip score
	$(PY)/trip tag
	$(PY)/trip status

api: ## Run the API on :8000
	cd backend && .venv/bin/uvicorn app.api.main:app --reload --port 8000

web: ## Run the frontend on :5173
	cd frontend && npm run dev

dev: ## Run both (API in the background)
	@$(MAKE) api & sleep 2; $(MAKE) web

test: ## Run the test suite
	cd backend && .venv/bin/pytest -q

lint: ## Lint backend and typecheck frontend
	cd backend && .venv/bin/ruff check app tests
	cd frontend && npx tsc -b --noEmit

reset: ## Drop all data and re-seed from scratch
	docker exec trip_package_db psql -U trip -d trip_package -qc \
		"TRUNCATE places, source_signals, text_evidence, merge_reviews, ingest_runs CASCADE;"
	@$(MAKE) seed-offline

clean: ## Remove venv, node_modules, caches and the database volume
	rm -rf backend/.venv backend/.cache frontend/node_modules frontend/dist
	docker compose down -v
