#!/bin/bash
# Returns a running, already-migrated stack to a known-good demo state
# (P9.T4) — truncates every table/collection/document a demo run
# accumulates (events, venues, performers, tickets, bookings, payments,
# the Mongo seat-map docs, the Elasticsearch search index, every Redis
# hold key), then re-seeds. Truncates in place against the running
# containers rather than dropping/recreating volumes — faster, and does
# not require re-running migrations or waiting on healthchecks again.
#
# Deliberately does NOT touch Keycloak — its realm/user data is imported
# configuration (infra/keycloak/), not state a demo run accumulates, so
# resetting it here would be destructive in a way this script isn't meant
# to be (see docs/phases/phase-9-kickoff.md).
#
# Run via `make reset` from the repo root, against a stack already
# brought up and migrated (`make up && make migrate`).
set -euo pipefail

cd "$(dirname "$0")"
set -a && . ../.env && set +a

echo "Truncating event_db..."
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$EVENT_DB_USER" -d "$EVENT_DB_NAME" \
  -c "TRUNCATE TABLE event_performers, events, performers, venues CASCADE;"

echo "Truncating booking_db..."
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$BOOKING_DB_USER" -d "$BOOKING_DB_NAME" \
  -c "TRUNCATE TABLE bookings, tickets, events CASCADE;"

echo "Truncating payment_db..."
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$PAYMENT_DB_USER" -d "$PAYMENT_DB_NAME" \
  -c "TRUNCATE TABLE payments CASCADE;"

echo "Clearing seat-map documents (MongoDB)..."
docker compose exec -T mongodb mongosh --quiet \
  "mongodb://${MONGO_USER}:${MONGO_PASSWORD}@localhost:27017/event_service?authSource=admin" \
  --eval "db.seat_maps.deleteMany({})"

echo "Clearing the search index (Elasticsearch)..."
curl -s -o /dev/null -X POST "http://localhost:${ELASTICSEARCH_PORT}/events/_delete_by_query?conflicts=proceed" \
  -H "Content-Type: application/json" -d '{"query": {"match_all": {}}}' \
  || echo "  (events index not present yet — nothing to clear)"

echo "Clearing hold state (Redis)..."
docker compose exec -T redis redis-cli FLUSHALL

echo "Re-seeding baseline demo data..."
cd ../services/event-service && uv run --package event-service python -m app.seed

echo "Demo state reset complete."
