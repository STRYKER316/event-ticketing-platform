# search-service

Elasticsearch-backed event search. Kafka consumer only (§7) — never a source of truth,
indexes what `event-service` publishes when an event is created, updated, or deleted
while `PUBLISHED` (§15 delta).

Phase 2 in progress: Elasticsearch client + index mapping wired (P2.T2). Kafka
consumer (P2.T3) and the public search API (P2.T4) land next.

## Run locally

```sh
cd services  # workspace root
uv run --package search-service uvicorn app.main:app --reload --app-dir search-service --port 8002
```

Needs `ELASTICSEARCH_HOST`/`ELASTICSEARCH_PORT` set (see `.env.example`). Normally run
via `docker compose` from `/infra` instead — see `infra/README.md`.

## Tests

```sh
cd services/search-service
uv run --package search-service pytest tests/unit          # mocked, no live infra
uv run --package search-service pytest tests/integration   # testcontainers: real Elasticsearch (+ Kafka from P2.T3)
```
