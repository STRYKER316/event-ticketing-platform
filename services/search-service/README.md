# search-service

Elasticsearch-backed event search. Kafka consumer only (§7) — never a source of truth,
indexes what `event-service` publishes when an event is published, updated while
published, or deleted (§15 delta).

Phase 2 complete: Elasticsearch client + index mapping (P2.T2), idempotent Kafka
consumer (P2.T3), and the public `GET /search` API (P2.T4) are all wired and tested.

## Run locally

```sh
cd services  # workspace root
uv run --package search-service uvicorn app.main:app --reload --app-dir search-service --port 8002
```

Needs `ELASTICSEARCH_HOST`/`ELASTICSEARCH_PORT`/`KAFKA_BOOTSTRAP_SERVERS`/`EVENTS_TOPIC`
set (see `.env.example`). Normally run via `docker compose` from `/infra` instead — see
`infra/README.md`.

## Tests

```sh
cd services/search-service
uv run --package search-service pytest tests/unit          # mocked, no live infra
uv run --package search-service pytest tests/integration   # testcontainers: real Elasticsearch + Kafka
```
