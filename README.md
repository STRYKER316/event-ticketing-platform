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
/frontend                minimal React, 5 screens, Nginx-served (§10) — behind Traefik
                         at PathPrefix('/app')
/infra                   docker-compose.yml, Traefik config, Keycloak realm export
/benchmark               standalone P8 load harness (own pyproject.toml/uv venv)
/docs                    decisions-log.md, master-development-plan.md, build-log.md,
                         architecture.html, /phases (per-phase task checklists),
                         /report (continuously-drafted report chapters)
```

## Local Development

```sh
make up             # copies .env.example -> .env if missing, boots the compose stack
make migrate         # applies event-service's, booking-service's, and payment-service's
                      # Alembic migrations (required once against a fresh stack --
                      # nothing runs this automatically)
make seed            # populates baseline demo events/venues/seat maps
./get-token.sh       # prints an access token for seed user alice (pass a different user/pass as args)
curl "localhost/events"                                              # public read
curl "localhost/search?q=concert"                                    # public search
curl -X POST localhost/events -H "Authorization: Bearer $(./get-token.sh bob changeme)" -d '...'  # organizer write
make logs            # tail all container logs
make down            # stop the stack
make test            # run suites that don't need live infra (currently: shared-auth)
make bench-up         # prometheus + grafana, on demand only (P8 hold-mechanism benchmark)
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
forwarding (§24, `services/payment-service/README.md`) — `.env` currently
only has a placeholder `STRIPE_SECRET_KEY`, so a real charge round-trip
needs a real Stripe test-mode key supplied first. Notifications are log-only
for now (§19), no SendGrid/etc. needed.

## Status

Phases 0-9 complete: walking skeleton, Event Service, Search Service +
Kafka #1, Booking Service (dual hold strategy: cron sweep + Redis TTL,
proven under real concurrent load), and Payment Service (Stripe test-mode
charge + webhook, Kafka #4 payment-outcome confirm/release, the system's one
synchronous inter-service call) — plus a P1 addendum (venue/seat-map write
API), a pre-Phase-3 hardening pass (adversarial testing, 5 bugs found and
fixed), a Phase 3 checkpoint with two review passes (a dedicated adversarial
one on top of the routine gate, since the dual hold strategy is the one bug
class that silently corrupts the product's core guarantee), Phase 8
(Hold-Mechanism Benchmark, the report's centerpiece) — a measured
cron-vs-Redis comparison plus release-latency (immediate vs. passive), see
`docs/benchmark-results/` and `docs/report/feature-development-process.md`
— and Phase 4, which added organizer-set per-section ticket pricing
(retroactively touching Event Service's seat map and Booking Service's
`Ticket` model) and caught real bugs via live testing and a CHECKPOINT
`/pre-pr` review — a stuck payment-idempotency short-circuit, a Postgres
bind-param overflow, and (most severe) an authorization bypass where
`/payments/charge` was reachable publicly through Traefik, letting any
authenticated user submit an arbitrary charge amount for any booking; all
fixed and live-verified, see `docs/build-log.md` for the full list.
`/payments/webhook`'s own processing (signature verification, idempotency,
Kafka publish) is fully live-verified end-to-end via a self-signed
synthetic event, since signature verification only depends on the locally-
configured webhook secret, not a real Stripe account. What's left needs an
actual Stripe account specifically: a real charge succeeding against
Stripe's API, and Stripe's own infrastructure delivering the resulting
webhook — tracked precisely on Phase 4's exit checklist, not silently
marked done.

**Phase 6** added `POST /bookings/{id}/cancel` (owner-scoped, before the
event starts) and Kafka integration point #5 (`booking.cancelled` →
Payment Service issues a Stripe refund), plus the producer-only half of
integration point #3 (a `notifications` topic, no consumer until Phase 5).
A CHECKPOINT `/pre-pr` review caught and fixed a real fail-open bug — the
cancellation cutoff silently allowed cancelling past an event's start time
for any booking whose event predated the new `events` reference table,
now fails closed instead — plus five other issues (a refund-notification
failure that could be misattributed as a DB error, a DTO gap that could
crash the cutoff comparison on a naive datetime, and others); see
`docs/build-log.md` for the full list. Same Stripe-account gap as Phase 4:
everything up to Stripe's own API boundary is live-verified (a real
refund attempt reaching `https://api.stripe.com/v1/refunds`, failing only
on the placeholder credential), a real refund succeeding isn't yet.

**Phase 5** added the fifth and final backend service, Notification
Service — no database of its own — completing Kafka integration point #3:
it consumes `notifications` (booking-confirmed/payment-confirmed/
refund-failed) and, on a delivery failure, walks a hand-rolled
retry/backoff/DLQ ladder through two more topics (`notification-retry`,
`notification-dlq`), carrying its own retry state on the Kafka message
itself via a `RetryEnvelope` rather than in a database (decisions-log §17
amendment). Booking Service and Payment Service each also gained a new
`notifications` producer path this phase (`booking_confirmed`,
`payment_confirmed`), alongside the existing Phase 6 `refund_failed` path.
A two-round CHECKPOINT `/pre-pr` review caught and fixed a backoff-
computation overflow, an unguarded Kafka republish that could permanently
kill a consumer task, a publish-before-commit ordering bug that could roll
back an already-confirmed booking while losing the payment-outcome
message, and a DTO-validation gap that could crash a consumer on its own
internally-constructed error message; see `docs/build-log.md` for the full
list.

**Phase 7** added the frontend: five React screens (browse/search, event
detail with a polling interactive seat map, checkout, confirmation, and a
minimal organizer create-event flow) served as static assets behind Traefik
at `PathPrefix('/app')`, authenticated via Keycloak Authorization Code +
PKCE. It also added a new booking-service endpoint,
`GET /bookings/events/{event_id}/tickets`, as the seat map's live per-seat
status source — layout comes from Event Service, status and ticket IDs from
here (decisions-log §23 amendment). A CHECKPOINT `/pre-pr` review (three
rounds) caught and fixed a StrictMode double-hold race, a seat-key
collision risk in organizer-typed section/row names, and a documentation
drift where the new endpoint's Manager routing had been described
inconsistently across three docs. A follow-up live Claude-in-Chrome
walkthrough then drove the actual rendered UI end to end and found three
more real bugs no wire-level testing could reach — most notably login
never actually completing, since the index route (also the OIDC
`redirect_uri`) stripped Keycloak's callback query string before login
could process it — plus a seat-label display bug and a nav-spacing
misclick hazard; all fixed and live re-verified. See `docs/build-log.md`
for the full list. This is the locked build order's final backend/frontend
build phase, followed by Phase 9 (hardening) and then Phase 10
(deployment); see `/docs/phases/` for task checklists and
`/docs/architecture.html` for current system state.

**Phase 9** closed integration-test gaps and verified idempotency
invariants system-wide rather than adding new architecture: a literal
identical-message-redelivery test for the one Kafka integration point that
lacked it (Payment→Booking outcome), a log-level-discipline and `/metrics`
parity audit across all five services (one real inconsistency found and
fixed), and tests proving two previously-untested-but-already-implemented
guards actually hold (double-cancelling an already-CANCELLED booking;
paying against a booking whose hold already expired). Also added `make
reset` for a one-command known-good demo state. A CHECKPOINT `/pre-pr`
review caught and fixed a stale audit figure, a self-contradicted test
count in `testing-strategy.md`, a wrong cross-doc reference, and two
commit-message rule violations (rewritten via `git filter-branch` before
anything was pushed); see `docs/build-log.md` for the full list.

