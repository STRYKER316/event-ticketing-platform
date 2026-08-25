# event-ticketing-platform

A backend-heavy event ticketing and reserved-seat booking platform (in the
BookMyShow/Ticketmaster mold), built as an MS CS capstone project
(Scaler-Neovarsity x Woolf, Backend Specialization). Microservices architecture
with Kafka-driven event integration, dual seat-hold concurrency strategies,
Stripe payments, Keycloak authentication, and a React frontend — deployed to
AWS.

## Features

- **Reserved, numbered seating** — every ticket is one specific seat for one
  event, with a live interactive seat map.
- **Dual concurrency-safe hold strategies** for seat reservation (a Postgres
  cron sweep vs. a Redis TTL lock), benchmarked head-to-head under real
  concurrent load.
- **Event-driven microservices** — five independent services communicating
  through five explicitly-scoped Kafka integration points, each with its own
  datastore and no cross-service database access.
- **Real payments** — Stripe (test mode) charges and refunds, confirmed
  asynchronously via webhook, with idempotent handling of retries and
  redelivery throughout.
- **Authentication & authorization** via Keycloak (OIDC + PKCE), with
  ownership-scoped access control, not just role checks.
- **Full-text event search** backed by Elasticsearch, kept in sync via Kafka.
- **Automated delivery/retry pipeline** for notifications, with a hand-rolled
  retry/backoff/dead-letter queue.
- **A minimal React frontend** — browse, view seat map, check out, and an
  organizer event-creation flow.
- Deployed to **AWS Elastic Beanstalk** (Docker platform), with a local
  Docker Compose stack for development.

## Architecture

```
/services
  /event-service        Postgres (event_db) + MongoDB seat maps; organizer writes
  /search-service        Elasticsearch index; Kafka consumer only, not source of truth
  /booking-service       Postgres (booking_db); dual hold strategy; the heart of the system
  /payment-service       Postgres (payment_db); Stripe (test mode)
  /notification-service  no database; hand-rolled retry/DLQ ladder
  /_shared/auth           shared FastAPI JWT-validation dependency, reused across services
/frontend                 React, 5 screens, Nginx-served — behind Traefik at PathPrefix('/app')
/infra                    docker-compose.yml, Traefik config, Keycloak realm export,
                          AWS Elastic Beanstalk deployment bundle/hooks
/benchmark                standalone load-testing harness for the hold-strategy comparison
/docs                     decisions log, development plan, build log, architecture diagrams,
                          and the accompanying capstone report
```

**Tech stack:** FastAPI (async), SQLAlchemy + asyncpg + Alembic, MongoDB (Motor),
Kafka (aiokafka, KRaft mode), Redis, Elasticsearch, Stripe, Keycloak, Traefik,
React, `pytest` + `testcontainers`, `uv` for Python dependency management,
Docker Compose (local) and AWS Elastic Beanstalk (deployed).

For the full architecture rationale, invariants, and conventions, see
`CLAUDE.md`. For the locked design decisions and development plan, see
`/docs/decisions-log.md` and `/docs/master-development-plan.md`. For a
current-state topology and flow diagram, see `/docs/architecture.html`.

## Local Development

```sh
make up             # copies .env.example -> .env if missing, boots the compose stack
make migrate         # applies event-service's, booking-service's, and payment-service's
                      # Alembic migrations (required once against a fresh stack --
                      # nothing runs this automatically)
make seed            # populates baseline demo events/venues/seat maps
make reset            # truncates and re-seeds a running stack back to a known-good demo state
./get-token.sh       # prints an access token for seed user alice (pass a different user/pass as args)
curl "localhost/events"                                              # public read
curl "localhost/search?q=concert"                                    # public search
curl -X POST localhost/events -H "Authorization: Bearer $(./get-token.sh bob changeme)" -d '...'  # organizer write
make logs            # tail all container logs
make down            # stop the stack
make test            # run suites that don't need live infra (currently: shared-auth)
make bench-up         # prometheus + grafana, on demand only (for the hold-strategy benchmark)
make bench-down
```

Frontend: `docker compose up frontend` from `/infra` serves it at `http://localhost/app/`
(or `cd frontend && npm install && npm run dev` for a hot-reloading dev server at
`http://localhost:5173` — see `frontend/README.md`). Either way it talks to the same
Traefik-routed backend, configured via `frontend/.env` (`VITE_EVENT_SERVICE_URL`,
`VITE_SEARCH_SERVICE_URL`, `VITE_BOOKING_SERVICE_URL`, `VITE_KEYCLOAK_PORT`,
`VITE_KEYCLOAK_REALM`).

Seed users (see `infra/keycloak/realm-export.json.template`), all password `changeme`:

| Username | Roles |
|---|---|
| `alice` | `user` |
| `bob` | `user`, `organizer` |
| `carol` | `organizer` |

**Stripe CLI** (`stripe listen --forward-to ...`) is needed for local webhook
forwarding — `.env.example`'s `STRIPE_SECRET_KEY` is only a placeholder, so a
real charge round-trip needs a real Stripe test-mode key supplied first.
Notifications are log-only for now, no email provider needed.

## Testing

Each service has its own unit test suite (mocked dependencies, no live infra
needed) and an integration suite that runs against real Postgres, MongoDB,
Redis, and Kafka via `testcontainers`. Kafka consumers are specifically
tested for redelivery safety (at-least-once delivery must be a safe no-op),
and the dual hold-strategy's concurrency guarantee is verified under real
concurrent load, not just asserted. See `docs/report/testing-strategy.md`
for the full testing approach and results.

## Documentation

- `CLAUDE.md` — architecture invariants, conventions, and workflow
- `docs/decisions-log.md` — the locked design-decision record
- `docs/master-development-plan.md` — the phased development plan
- `docs/build-log.md` — an append-only development diary
- `docs/architecture.html` — current-state topology and flow diagrams
- `docs/report/` — the accompanying capstone report chapters
