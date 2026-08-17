# infra

Shared local infrastructure (§20, §24) — data/broker/gateway layer, plus the
app services wired in behind Traefik as each phase builds them: `event-service`
(Phase 0-1), `search-service` (Phase 2), `booking-service` (Phase 3), and
`payment-service` (Phase 4) so far — Phase 6 extended both `booking-service`
and `payment-service` in place rather than adding a new container.

Copy `../.env.example` to `../.env` and fill in values before running.

## Bring up

```sh
docker compose --env-file ../.env up -d
docker compose ps
```

## Benchmark profile (Prometheus + Grafana, P8)

Not part of the default stack — brought up on demand for benchmark runs
(§6, §11, §24) via the `benchmark` compose profile:

```sh
make bench-up    # from repo root; docker compose --profile benchmark up -d
make bench-down
```

Prometheus (`infra/prometheus/prometheus.yml`) scrapes `event-service`,
`search-service`, `booking-service`, and `payment-service`'s existing
`/metrics` endpoints (`prometheus-fastapi-instrumentator`, wired since
P0.T5 — no new instrumentation) every 5s. Grafana auto-provisions the Prometheus datasource
and a `booking-service` dashboard
(`infra/grafana/provisioning/dashboards/json/booking-service.json`)
from `infra/grafana/provisioning/` on startup — no manual setup needed.

## Ports (host-mapped, from `.env`)

| Service | Container | Host port | Notes |
|---|---|---|---|
| Postgres | `postgres` | `POSTGRES_PORT` (55432 — see note below) | one container, three logical DBs (`event_db`, `booking_db`, `payment_db`), each with its own user — see `postgres/init.sh` (§8, §20) |
| MongoDB | `mongodb` | `MONGO_PORT` (27017) | event-service seat maps only (§8) |
| Redis | `redis` | `REDIS_PORT` (6379) | booking-service's Redis-TTL `TicketHoldStrategy` only (§6, §8) — idle unless `HOLD_STRATEGY=redis` |
| Elasticsearch | `elasticsearch` | `ELASTICSEARCH_PORT` (9200) | single-node, security disabled, heap capped at 512m (§24) |
| Kafka | `kafka` | `KAFKA_PORT` (9092) | KRaft mode, single broker, no Zookeeper (§7, §24) |
| Keycloak | `keycloak` | `KEYCLOAK_PORT` (8081) | dev mode, embedded DB, imports `keycloak/realm-export.json` on startup (§5, §12, §15) |
| event-service | `event-service` | routed via Traefik only (no direct host port) | events/venues/seat-maps API (§8, §15); `/healthz`, `/metrics`, `/events`, `/venues`, `/events/{id}/seat-map`, `/events/{id}/publish` |
| search-service | `search-service` | routed via Traefik only (no direct host port) | public `GET /search` over Elasticsearch, populated via Kafka (§7.1, §8); `/healthz`, `/metrics` |
| booking-service | `booking-service` | routed via Traefik only (no direct host port) | ticket provisioning consumer (Kafka #2, §7.2) + payment-outcome consumer (Kafka #4, §7.4) + `POST /bookings`/`POST /bookings/{id}/pay`/`POST /bookings/{id}/cancel` over `booking_db` and (if `HOLD_STRATEGY=redis`) Redis (§6, §8); publishes Kafka #5 (`booking.cancelled`, its first-ever producer, §22); `/healthz`, `/metrics` |
| payment-service | `payment-service` | routed via Traefik only (no direct host port) | Stripe test-mode charge (`POST /payments/charge`, called by booking-service only — §9 amendment) + webhook (`POST /payments/webhook`) + `booking.cancelled` consumer (Kafka #5, its first-ever consumer — issues Stripe refunds, §22) over `payment_db`; publishes Kafka #4 (`payment.outcomes`) and, on refund failure, `notifications` (producer only — no consumer until Phase 5, §22 amendment #3); `/healthz`, `/metrics` |
| Traefik | `traefik` | 80 (entrypoint), `TRAEFIK_DASHBOARD_PORT` (8080, dashboard) | Docker-labels provider; `event-service` on `PathPrefix('/')`, `search-service` on `PathPrefix('/search')`, `booking-service` on `PathPrefix('/bookings')`, `payment-service` on `PathPrefix('/payments')` |
| docker-socket-proxy | `docker-socket-proxy` | internal only | nginx proxy in front of the Docker socket — see note below |
| Prometheus | `prometheus` | `PROMETHEUS_PORT` (9090) | `benchmark` profile only (`make bench-up`) — scrapes `event-service`/`search-service`/`booking-service`/`payment-service` `/metrics` every 5s (§11, §24) |
| Grafana | `grafana` | `GRAFANA_PORT` (3000) | `benchmark` profile only (`make bench-up`) — Prometheus datasource and a `booking-service` dashboard (request rate, latency p50/p95/p99, error rate) auto-provisioned on startup; login `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD` |

All images are arm64-native (§24) — no Rosetta emulation expected on Apple Silicon.

**Note — `POSTGRES_PORT` default:** left at `55432` (not the usual 5432) because a
locally installed Homebrew Postgres often already occupies 5432/5433 on the host.
If your machine is clear, feel free to set it back to `5432` in `.env`.

**Note — `docker-socket-proxy`:** Traefik's Docker-labels provider needs the daemon
socket to discover containers. On some Docker Desktop builds, Traefik's embedded
client hardcodes an old API-version probe (`/v1.24/...`) that a newer daemon
rejects outright before version negotiation can even happen, breaking service
discovery entirely. `docker-socket-proxy` (nginx, config in
`docker-socket-proxy/nginx.conf`) sits between Traefik and the socket and strips
any version prefix off incoming requests so this negotiation failure can't occur,
regardless of which Docker Desktop build you're on.
