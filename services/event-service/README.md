# event-service

Owns event and venue data (Postgres `event_db`) plus seat maps (MongoDB). Source of
truth for event catalog and organizer-managed inventory; publishes changes for
`search-service` to index via Kafka on publish/update-while-published/delete (§7.1,
§15 delta).

Phase 1 + P1 addendum + Phase 2 integration complete: events/venues/performers in
Postgres via Alembic-managed migrations, seat-map documents in MongoDB, public read
APIs (list/detail/seat-map/venue), organizer write APIs (`POST`/`PATCH`/`DELETE
/events`, `POST /venues`, `PUT /events/{id}/seat-map`, `POST /events/{id}/publish`)
with both role and ownership scoping (§15), and a Kafka producer publishing the
event-carried seat list on every visibility-affecting mutation. Hardened via a
pre-Phase-3 adversarial testing pass (DTO bounds, a concurrent-delete race, a Kafka
producer timeout — see `docs/build-log.md`).

## Run locally

```sh
cd services  # workspace root
uv run --package event-service uvicorn app.main:app --reload --app-dir event-service --port 8001
```

Needs `POSTGRES_HOST`/`POSTGRES_PORT`/`EVENT_DB_*`, `MONGO_HOST`/`MONGO_PORT`/`MONGO_USER`/
`MONGO_PASSWORD`, and `AUTH_KEYCLOAK_ISSUER`/`AUTH_EXPECTED_AUDIENCE` set (see
`.env.example`). Normally run via `docker compose` from `/infra` instead — see
`infra/README.md`.

## Tests

```sh
cd services/event-service
uv run --package event-service pytest tests/unit          # mocked, no live infra
uv run --package event-service pytest tests/integration   # testcontainers: real Postgres + MongoDB
```

## Seed data

`make seed` (from repo root, stack must be up) populates baseline venues, performers,
events, and one seat map — idempotent, skips if data already exists.
