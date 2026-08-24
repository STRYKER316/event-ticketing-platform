#!/bin/bash
# Runs Alembic migrations and the demo seed script against the containers
# EB's Docker Compose deploy just started, mirroring the Makefile's local
# `make migrate && make seed` targets (P10.T1). EB has no host-side Python/
# uv -- alembic and the app venv only exist inside each service's own image
# (/workspace/.venv, set up by services/*/Dockerfile) -- so this execs into
# the running containers instead of shelling out directly. Idempotent:
# alembic upgrade head and the seed script's own
# seed_skipped_data_already_present check both make redeploys a safe no-op.
set -eu

wait_for_container() {
  name="$1"
  for _ in $(seq 1 30); do
    id=$(docker ps --filter "name=$name" --format '{{.ID}}' | head -1)
    if [ -n "$id" ]; then
      echo "$id"
      return 0
    fi
    sleep 2
  done
  echo "ERROR: container matching '$name' did not appear within 60s" >&2
  exit 1
}

for svc in event-service booking-service payment-service; do
  id=$(wait_for_container "$svc")
  echo "Running migrations: $svc ($id)"
  docker exec "$id" /workspace/.venv/bin/alembic upgrade head
done

event_id=$(wait_for_container event-service)
echo "Seeding demo data via event-service ($event_id)"
docker exec "$event_id" /workspace/.venv/bin/python -m app.seed
