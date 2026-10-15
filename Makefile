PY ?= python3.11
VENV ?= .venv
BIN := $(VENV)/bin
export PYTHONPATH := backend

.PHONY: help install dev api web test schema fixtures build docker clean

help:
	@echo "make install   - create venv + install backend and frontend deps"
	@echo "make dev       - run API (:8000) and Vite dev server (:5173) without Docker"
	@echo "make test      - run pytest (MOCK_LLM forced)"
	@echo "make schema    - regenerate schema/incident.schema.json"
	@echo "make fixtures  - regenerate scenario fixtures"
	@echo "make build     - build the dashboard into frontend/dist (served by the API on :8000)"
	@echo "make docker    - docker compose up --build"

$(BIN)/python:
	$(PY) -m venv $(VENV)

install: $(BIN)/python
	$(BIN)/pip install -q -r backend/requirements-dev.txt
	cd frontend && npm install --no-audit --no-fund

api:
	cd backend && ../$(BIN)/uvicorn app.api.main:create_app --factory --reload --port 8000 --reload-dir app

web:
	cd frontend && npm run dev

dev:
	@echo "API on http://localhost:8000  ·  dashboard on http://localhost:5173"
	@trap 'kill 0' INT TERM; $(MAKE) --no-print-directory api & $(MAKE) --no-print-directory web & wait

test:
	cd backend && ../$(BIN)/python -m pytest -q

schema:
	cd backend && ../$(BIN)/python -m app.models.schema_export

fixtures:
	$(BIN)/python scenarios/generate_fixtures.py

build:
	cd frontend && npm run build

docker:
	docker compose up --build

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist data/*.db
