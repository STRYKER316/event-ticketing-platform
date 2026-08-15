.PHONY: up down logs test seed

# Local dev workflow (§25). Run from repo root.

up:
	@test -f .env || cp .env.example .env
	cd infra && docker compose --env-file ../.env up -d

down:
	cd infra && docker compose --env-file ../.env down

logs:
	cd infra && docker compose --env-file ../.env logs -f

# Runs suites that don't require live infra (fully mocked deps). Suites that
# need a running broker/DB (e.g. infra/kafka-smoke-test) are run separately,
# on purpose — see their own READMEs.
test:
	cd services && uv run --package shared-auth pytest _shared/auth

# Populates baseline demo data (§19). Run against a running local stack.
seed:
	set -a && . .env && set +a && cd services/event-service && uv run --package event-service python -m app.seed
