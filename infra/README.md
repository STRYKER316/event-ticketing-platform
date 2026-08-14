# infra

Shared local infrastructure (§20, §24). App services aren't wired in yet — this is
just the data/broker/gateway layer.

Copy `../.env.example` to `../.env` and fill in values before running.

## Bring up

```sh
docker compose --env-file ../.env up -d
docker compose ps
```

## Ports (host-mapped, from `.env`)

| Service | Container | Host port | Notes |
|---|---|---|---|
| Postgres | `postgres` | `POSTGRES_PORT` (5432) | one container, three logical DBs (`event_db`, `booking_db`, `payment_db`), each with its own user — see `postgres/init.sh` (§8, §20) |
| MongoDB | `mongodb` | `MONGO_PORT` (27017) | event-service seat maps only (§8) |
| Redis | `redis` | `REDIS_PORT` (6379) | |
| Elasticsearch | `elasticsearch` | `ELASTICSEARCH_PORT` (9200) | single-node, security disabled, heap capped at 512m (§24) |
| Kafka | `kafka` | `KAFKA_PORT` (9092) | KRaft mode, single broker, no Zookeeper (§7, §24) |
| Keycloak | `keycloak` | `KEYCLOAK_PORT` (8081) | dev mode, embedded DB, imports `keycloak/realm-export.json` on startup (§5, §12, §15) |
| Traefik | `traefik` | 80 (entrypoint), `TRAEFIK_DASHBOARD_PORT` (8080, dashboard) | Docker-labels provider; no app services registered yet |

All images are arm64-native (§24) — no Rosetta emulation expected on Apple Silicon.
