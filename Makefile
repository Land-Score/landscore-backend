.PHONY: up down build proto migrate lint format

# ── Docker ──────────────────────────────────────────────
up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

restart:
	docker compose restart

# ── Proto generation ────────────────────────────────────
proto:
	@echo "Generating gRPC code from proto files..."
	@python -m grpc_tools.protoc \
		-I proto \
		--python_out=shared/landscore_shared/proto_generated \
		--grpc_python_out=shared/landscore_shared/proto_generated \
		proto/*.proto
	@echo "Done. Generated files in shared/landscore_shared/proto_generated/"

# ── Database migrations ─────────────────────────────────
migrate-auth:
	docker compose exec auth-service alembic upgrade head

migrate-main:
	docker compose exec check-service alembic upgrade head

migrate-geo:
	docker compose exec geo-service alembic upgrade head

migrate-all: migrate-auth migrate-main migrate-geo

# ── Dev shortcuts ───────────────────────────────────────
shell-gateway:
	docker compose exec api-gateway bash

shell-orchestrator:
	docker compose exec ai-orchestrator bash

# ── Code quality ────────────────────────────────────────
lint:
	ruff check services/ shared/

format:
	ruff format services/ shared/

typecheck:
	mypy services/ shared/ --ignore-missing-imports

# ── Reset ───────────────────────────────────────────────
clean:
	docker compose down -v --remove-orphans
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
