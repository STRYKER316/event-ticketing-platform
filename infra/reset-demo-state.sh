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

# Table lists are hand-kept in sync with each service's SQLAlchemy models —
# a migration adding a table needs a matching addition here.
truncate_db() {
  echo "Truncating $1..."
  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$2" -d "$3" -c "TRUNCATE TABLE $4 CASCADE;"
}
truncate_db "event_db" "$EVENT_DB_USER" "$EVENT_DB_NAME" "event_performers, events, performers, venues"
truncate_db "booking_db" "$BOOKING_DB_USER" "$BOOKING_DB_NAME" "bookings, tickets, events"
truncate_db "payment_db" "$PAYMENT_DB_USER" "$PAYMENT_DB_NAME" "payments"

echo "Clearing seat-map documents (MongoDB)..."
docker compose exec -T mongodb mongosh --quiet \
  "mongodb://${MONGO_USER}:${MONGO_PASSWORD}@localhost:27017/event_service?authSource=admin" \
  --eval "db.seat_maps.deleteMany({})"

echo "Clearing the search index (Elasticsearch)..."
# Checked by explicit status code, not curl's own exit code: without -f,
# curl exits 0 on a 404 (so the intended "index missing" case would never
# actually be distinguishable from success), and -f alone would collapse
# a genuine ES failure into the same swallowed-and-continue path under
# set -e, silently leaving a stale index behind.
es_status=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  "http://localhost:${ELASTICSEARCH_PORT}/events/_delete_by_query?conflicts=proceed&refresh=true" \
  -H "Content-Type: application/json" -d '{"query": {"match_all": {}}}')
if [ "$es_status" = "404" ]; then
  echo "  (events index not present yet — nothing to clear)"
elif [ "$es_status" != "200" ]; then
  echo "  ERROR: Elasticsearch delete_by_query failed (HTTP $es_status)" >&2
  exit 1
fi

echo "Clearing hold state (Redis)..."
docker compose exec -T redis redis-cli FLUSHALL

echo "Re-seeding baseline demo data..."
cd ../services/event-service && uv run --package event-service python -m app.seed

echo "Demo state reset complete."
