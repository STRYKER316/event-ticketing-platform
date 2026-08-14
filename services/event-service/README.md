# event-service

Owns event and venue data (Postgres `event_db`) plus seat maps (MongoDB). Source of
truth for event catalog and organizer-managed inventory; publishes changes for
`search-service` to index via Kafka.

Phase 0 walking skeleton in place (`/healthz`, `/metrics`, `/demo/protected`,
`/demo/organizer-only`) — the event catalog/inventory domain itself isn't built yet;
that's Phase 1.

## Run locally

```sh
cd services  # workspace root
uv run --package event-service uvicorn app.main:app --reload --app-dir event-service --port 8001
```

Needs `POSTGRES_HOST`/`POSTGRES_PORT`/`EVENT_DB_*` and `AUTH_KEYCLOAK_ISSUER`/
`AUTH_EXPECTED_AUDIENCE` set (see `.env.example`). Normally run via
`docker compose` from `/infra` instead — see `infra/README.md`.
