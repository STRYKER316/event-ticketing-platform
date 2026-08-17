.PHONY: up down logs test seed migrate bench-up bench-down

# Local dev workflow (§25). Run from repo root.

up:
	@test -f .env || cp .env.example .env
	cd infra && docker compose --env-file ../.env up -d

down:
	# --profile benchmark: a plain `down` only tears down the *default*
	# profile's services -- prometheus/grafana (profile-gated, not
	# "orphans" in compose's sense -- --remove-orphans does NOT touch
	# them, verified live) would stay running silently if `make bench-up`
	# was used and `bench-down` forgotten. Passing the profile here is a
	# no-op if those containers were never started.
	cd infra && docker compose --env-file ../.env --profile benchmark down

logs:
	cd infra && docker compose --env-file ../.env logs -f

# Brings up Prometheus + Grafana alongside the default stack (§11, §24) — on
# demand for benchmark runs (P8) rather than part of every `make up`.
# Grafana: http://localhost:$$GRAFANA_PORT (admin / $$GRAFANA_ADMIN_PASSWORD).
bench-up:
	@test -f .env || cp .env.example .env
	cd infra && docker compose --env-file ../.env --profile benchmark up -d

# `docker compose down` always tears down every enabled service project-wide
# (it takes no service-name arguments) -- `--profile benchmark down` would
# stop the whole stack, not just prometheus/grafana. `stop`+`rm` do accept
# service names, so only the benchmark-profile containers are touched.
bench-down:
	cd infra && docker compose --env-file ../.env stop prometheus grafana
	cd infra && docker compose --env-file ../.env rm -f prometheus grafana

# Runs suites that don't require live infra (fully mocked deps). Suites that
# need a running broker/DB (e.g. infra/kafka-smoke-test) are run separately,
# on purpose — see their own READMEs.
test:
	cd services && uv run --package shared-auth pytest _shared/auth

# Applies every Postgres-backed service's Alembic migrations against the
# running stack. Required once after a fresh `make up` (against empty
# databases) — no container runs this automatically, so `make seed` and
# every API route fail with "relation does not exist" until this has run.
migrate:
	set -a && . .env && set +a && cd services/event-service && uv run --package event-service alembic upgrade head
	set -a && . .env && set +a && cd services/booking-service && uv run --package booking-service alembic upgrade head
	set -a && . .env && set +a && cd services/payment-service && uv run --package payment-service alembic upgrade head

# Populates baseline demo data (§19). Run against a running, migrated stack.
seed:
	set -a && . .env && set +a && cd services/event-service && uv run --package event-service python -m app.seed
