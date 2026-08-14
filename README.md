# event-ticketing-platform

Backend-heavy event ticketing and booking platform built as an MS CS capstone
(Scaler-Neovarsity x Woolf), featuring microservices, reserved-seat booking,
Kafka-based event processing, payments, authentication, search, caching, automated
testing, and AWS deployment.

See `CLAUDE.md` for architecture invariants and conventions, and `/docs` for the
locked decisions log and master development plan — read those before making any
architectural or scope decision.

## Layout

```
/services
  /event-service       Postgres (event_db) + MongoDB seat maps; organizer writes
  /search-service       Elasticsearch index; Kafka consumer only, not source of truth
  /booking-service      Postgres (booking_db); dual hold strategy; the heart of the system
  /payment-service      Postgres (payment_db); Stripe (test mode)
  /notification-service no DB (or minimal delivery log); hand-rolled retry/DLQ
  /_shared/auth          shared FastAPI JWT-validation dependency, built once, reused everywhere
/frontend                minimal React, 5 screens, Nginx-served (Phase 7, not before)
/infra                   docker-compose.yml, Traefik config, Keycloak realm export
/docs                    decisions-log.md, master-development-plan.md, phase-0-kickoff.md
```

## Local Development

```sh
make up             # copies .env.example -> .env if missing, boots the compose stack
./get-token.sh       # prints an access token for seed user alice (pass a different user/pass as args)
curl -H "Authorization: Bearer $(./get-token.sh)" http://localhost/demo/protected
make logs            # tail all container logs
make down            # stop the stack
make test            # run suites that don't need live infra (currently: shared-auth)
```

Seed users (see `infra/keycloak/realm-export.json`), all password `changeme`:

| Username | Roles |
|---|---|
| `alice` | `user` |
| `bob` | `user`, `organizer` |
| `carol` | `organizer` |

**Stripe CLI** (`stripe listen --forward-to ...`) isn't needed yet — that's P4
(§24). Notifications are log-only for now (§19), no SendGrid/etc. needed.

## Status

Phase 0 (Foundation & Walking Skeleton) — in progress. Nothing is deployed or
running yet; see `/docs/phase-0-kickoff.md` for the current task list.

