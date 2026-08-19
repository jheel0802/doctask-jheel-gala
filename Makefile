.PHONY: install db test test-live-llm api frontend mcp mcp-http app demo down

ifneq (,$(wildcard ./.env))
include .env
export
endif

install:
	cd backend && UV_CACHE_DIR=../work/uv-cache uv sync --all-groups
	cd frontend && npm ci

db:
	docker compose up -d --wait postgres

test: export LLM_PROVIDER=fake
test: db
	cd backend && .venv/bin/pytest -q

test-live-llm:
	cd backend && RUN_LIVE_LLM_SMOKE=1 .venv/bin/pytest -q -m live_llm -s

api: db
	cd backend && .venv/bin/uvicorn project_evidence.main:app --reload

frontend:
	cd frontend && npm run dev

mcp:
	cd backend && .venv/bin/python -m project_evidence.mcp_server

mcp-http: db
	cd backend && .venv/bin/python -m project_evidence.mcp_server --transport streamable-http --port 8001

app:
	docker compose up -d --build --wait
	@echo "Project Evidence Agent: http://localhost:3000"

demo: export LLM_PROVIDER=fake
demo:
	docker compose up -d --build --wait
	@echo "Deterministic Atlas demo: http://localhost:3000/?demo=1"

down:
	docker compose down
