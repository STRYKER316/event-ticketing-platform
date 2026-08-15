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
/docs                    decisions-log.md, master-development-plan.md, build-log.md,
                         architecture.html, /phases (per-phase task checklists),
                         /report (continuously-drafted report chapters)
```

## Local Development

```sh
make up             # copies .env.example -> .env if missing, boots the compose stack
make seed            # populates baseline demo events/venues/seat maps
./get-token.sh       # prints an access token for seed user alice (pass a different user/pass as args)
curl "localhost/events"                                              # public read
curl "localhost/search?q=concert"                                    # public search
curl -X POST localhost/events -H "Authorization: Bearer $(./get-token.sh bob changeme)" -d '...'  # organizer write
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

Phases 0-2 complete (walking skeleton, Event Service, Search Service +
Kafka #1), plus a P1 addendum (venue/seat-map write API) and a pre-Phase-3
hardening pass (adversarial testing, 5 bugs found and fixed — see
`docs/build-log.md`). Phase 3 (Booking Service + dual hold strategy) is
next; see `/docs/phases/` for task checklists and `/docs/architecture.html`
for current system state.

