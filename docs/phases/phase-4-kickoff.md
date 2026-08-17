# Phase 4 Kickoff — Payment Service + Stripe + Confirmation

**Goal of this phase:** a new Payment Service, its own Postgres DB
(`payment_db`), a real (test-mode) Stripe charge flow triggered off a
`PENDING` Booking, webhook-driven confirmation, idempotent charge/webhook
handling, and **Kafka integration point #4** (`payment.failed` →
Booking Service releases the hold immediately, §17) plus the successful-payment
confirmation path (`PENDING` → `CONFIRMED`, §21).

**How to use this file:** run the six tasks below **in order**, one per
Claude Code session. Commit after each (small, green commits, per the
per-service layering conventions in `CLAUDE.md`). `main` stays bootable at
every step. Give Claude Code the repo plus `decisions-log.md` and
`master-development-plan.md` as context so it stays anchored to the locked
decisions (referenced by § below).

**Entry deps:** Phase 3 complete (Booking Service exists, `TicketHoldStrategy`
interface with both `cron`/`redis` implementations, `BookingManager.create_booking`
producing `PENDING` bookings). Phase 8 (benchmark) already ran directly after
Phase 3 per the locked report-first build order (§27) and already exercises
both hold strategies' release mechanisms directly — see §17's amendment in
`decisions-log.md` for how P8.T5 simulated this phase's release trigger ahead
of this phase actually existing. This phase now builds the **real** trigger
(`payment.failed`) that P8 simulated, reusing the exact same `release_hold()` +
Booking-row-transition mechanism P8 measured — no new release mechanism, only
a new caller.

**Payment Service does not exist yet** — `services/payment-service/` currently
holds only a placeholder `README.md` and an empty `app/.gitkeep` (Phase 0
scaffolding stub). P4.T1 replaces that with the real `event-service`-template
service.

**Process note specific to this phase (per `CLAUDE.md`):**
- **Test-first, not build-then-test**, for payment/webhook idempotency
  specifically (P4.T2's charge idempotency key, P4.T3's webhook handling) —
  write the test capturing "replayed charge attempt / replayed webhook has no
  double effect" *before* the implementation. This is the one correctness-critical
  path in this phase, per `CLAUDE.md`'s test-first list.
- Everywhere else in this phase (scaffolding, schema, routes) — build then test,
  matching how P4.T6 is a dedicated Tests task at the end, same shape as every
  other phase.
- No dedicated adversarial `/code-review` pass is required for P4 (only P3 and
  P8 get one per `CLAUDE.md`) — self-verification plus the routine `/pre-pr`
  gate at CHECKPOINT is sufficient here.
- **A Kafka consumer with its own DB write** (Booking Service's new
  `payment.failed` consumer, P4.T4) must follow the same
  `enable_auto_commit=False` + manual per-record offset commit + bounded
  DB-write retry shape as `ProvisioningConsumer`
  (`booking-service/app/kafka/consumers.py`) — `CLAUDE.md`'s Conventions
  section calls this out by name as the pattern Payment Service's webhook
  handler (and, by the same reasoning, this new Booking Service consumer)
  should follow.

---

## P4.T1 — Scaffold Payment Service + `payment_db` schema/migrations

**Prompt to Claude Code:**
> Scaffold `payment-service` from the `event-service` template (app factory,
> `core.py`, `api/`/`db/`/`logic/`/`kafka/` layering, `/healthz`, `/metrics`,
> structured JSON logging via `configure_logging()`, Traefik routing with a
> specific `PathPrefix('/payments')` — per the per-service layering and
> Traefik-routing conventions in `CLAUDE.md`). Replace the Phase-0 placeholder
> (`services/payment-service/README.md` + empty `app/.gitkeep`) with the real
> service. Own Postgres database `payment_db` (§8) — no shared tables with
> `booking_db` or `event_db`, cross-service data only arrives via Kafka.
>
> Define the schema and Alembic migration for a `Payment` model: booking ID
> (the FK-shaped reference into Booking Service's `booking_db`, but *not* an
> actual foreign key — database-per-service, §8 — just a plain UUID column),
> amount, currency, status (`pending`/`succeeded`/`failed`/`refunded` — a real
> Python `Enum`, never bare `str`, per the DTO-Enum convention), Stripe charge
> ID, Stripe idempotency key used, timestamps. Consider whether a separate
> `Refund` row (§22, used later in Phase 6) belongs in this same migration or
> a later one — Phase 6 owns cancellation/refunds, so a minimal `Payment`-only
> schema now, extended in Phase 6, is probably right; use judgement and note
> the choice in this phase's `build-log.md` entry either way.
>
> Wire `payment-service` into `infra/docker-compose.yml`: own service block
> (`payment_db` env vars — `PAYMENT_DB_NAME`/`PAYMENT_DB_USER`/
> `PAYMENT_DB_PASSWORD`/`PAYMENT_SERVICE_PORT` already exist in `.env.example`),
> `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` env vars (also already in
> `.env.example`, currently unused placeholders), Traefik labels
> (`PathPrefix('/payments')`, port `8004`), `depends_on` postgres/keycloak/kafka
> healthy. Single Postgres container, three logical databases (§12) — same
> pattern `booking_db`/`event_db` already use, not a new Postgres container.

**Done when:** migrations run clean against a fresh `payment_db`; `docker
compose up` brings up `payment-service` cleanly alongside the existing stack;
`/healthz` and `/metrics` respond. Report evidence: Payment Service class
diagram + textual schema.

---

## P4.T2 — Stripe test-mode charge for a booking

**Prompt to Claude Code:**
> Add `stripe-python` as a dependency (already in the locked tech stack) and a
> `POST /payments` (or similar — pick a route name and state it explicitly)
> endpoint: given a booking ID, create a Stripe test-mode charge (or
> PaymentIntent — use judgement on which Stripe API shape fits a synchronous
> test-mode confirmation flow best, and note the choice) for that booking's
> amount. **Idempotency key = booking ID** (§9, §21 — "this single ID threads
> through the entire flow... used as the Stripe idempotency key"), passed as
> Stripe's own `idempotency_key` request option so a client retry against the
> same booking never double-charges. Explicit auth: authenticated user only,
> and ownership-scoped — the paying user must be the booking's owner (§21
> doesn't specify this explicitly, but every other ownership case in this
> project scopes by resource-owner-vs-JWT-subject, so apply the same rule
> here and state it in the route per the auth-requirement convention).
> Create the local `Payment` row in `pending` status before calling Stripe,
> matching Booking's own PENDING-before-payment shape (§21).
>
> **Test-first**: before writing the charge logic, write the test that
> captures "the same booking ID charged twice produces one Stripe charge, not
> two" — the idempotency contract this task exists to satisfy.
>
> Local dev note: Stripe test-mode charges work directly against Stripe's API
> with a real `sk_test_...` key (§24) — no local Stripe emulation needed for
> this task specifically; the Stripe CLI forwarding (§24) is what P4.T3's
> webhook needs, not this one.

**Done when:** a charge is created and visible in the Stripe test dashboard;
the idempotency test (same booking ID, two charge attempts) passes. Report
evidence: idempotency writeup (§9) — this is the first of two idempotency
mechanisms this phase builds, the other being the webhook handler in P4.T3.

---

## P4.T3 — Webhook endpoint + Stripe CLI local forwarding

**Prompt to Claude Code:**
> Add a `POST /payments/webhook` endpoint that verifies the Stripe webhook
> signature (`STRIPE_WEBHOOK_SECRET`) and handles `payment_intent.succeeded`/
> `charge.succeeded` and `payment_intent.payment_failed`/`charge.failed` events
> (pick whichever Stripe object type matches P4.T2's choice). This endpoint is
> necessarily public/unauthenticated in the normal JWT sense — Stripe calls it
> directly, not a logged-in user — but must verify Stripe's own signature
> instead, and that verification **is** this route's auth requirement; state
> that explicitly per the auth-requirement convention rather than leaving an
> unauthenticated route unexplained.
>
> **Idempotent webhook handling**: Stripe redelivers webhooks (at-least-once,
> same reasoning as Kafka's own delivery guarantee, §7) — a replayed
> `succeeded` or `failed` event for a `Payment` already in a terminal state
> must be a safe no-op, not a double state transition. Use Stripe's own event
> ID or the existing idempotency-key column to detect a replay, and/or guard
> the state transition itself ("only transition if currently `pending`" — the
> same "only transition if currently in state X" pattern §7 already requires
> for every Kafka consumer, applied here to a webhook instead).
>
> **Test-first**: before writing the handler, write the test that replays the
> same webhook payload twice and asserts no double effect (no second Kafka
> publish in P4.T4, no double Booking-state transition in P4.T5).
>
> Document the local dev setup: `stripe listen --forward-to
> localhost:<traefik-port>/payments/webhook` (§24) — add this to
> `payment-service/README.md` (replacing the Phase-0 placeholder content) so
> a future session doesn't have to rediscover it.

**Done when:** a replayed webhook (same event ID, sent twice via `stripe
trigger` or manually) produces the same end state as a single delivery, proven
by the idempotency test. Report evidence: none dedicated (folds into P4.T4/T5's
diagrams) — this task is the plumbing those two report-evidence tasks depend on.

---

## P4.T4 — Kafka integration point #4: payment.failed → immediate hold release

**Prompt to Claude Code:**
> **Producer side (Payment Service):** on a webhook-confirmed failed payment
> (P4.T3), publish a `payment.failed` message to a new Kafka topic (mirror
> `event-service/app/kafka/producers.py`'s `EventProducer` shape — a thin
> producer class wrapping `AIOKafkaProducer.send_and_wait`, keyed by booking ID
> so redelivery-ordering per booking is preserved same as event-service keys
> by event ID). Payload needs at minimum the booking ID and ticket ID — check
> what `BookingRepository`/`Booking` actually expose before assuming
> `ticket_id` is on the message vs. inferable Booking-service-side from the
> booking ID alone; prefer sending what Booking Service needs directly over
> making its consumer look anything up first, same reasoning as §7.2's
> event-carried seat list avoiding a synchronous callback.
>
> **Consumer side (Booking Service):** add a new `aiokafka` consumer
> (`booking-service/app/kafka/consumers.py`) subscribed to this topic.
> On a `payment.failed` message, release the hold immediately — reuse the
> exact same mechanism `BookingManager._create_booking_row`'s integrity-race
> compensation path already calls: `self._hold_strategy.release_hold(ticket_id)`
> (configured strategy, whichever of `cron`/`redis` is active — this consumer
> must not care which), plus transition the `Booking` row to `EXPIRED` (§21:
> "PENDING → released/expired on hold timeout or payment failure" — same
> terminal state the cron/Redis sweep already uses, see
> `booking-service/app/db/models.py`'s `BookingStatus` and
> `BookingRepository.expire_stale_pending`). **Idempotent by construction**:
> only transition if the Booking is currently `PENDING` (§7's general
> idempotent-consumer rule, same "only transition if currently in state X"
> pattern §17 already specifies for this exact integration point) — a
> redelivered `payment.failed` message for an already-`EXPIRED` (or, if a race
> against confirmation is somehow possible, already-`CONFIRMED`) booking is a
> safe no-op, not an error and not a second hold-release attempt against an
> already-released ticket.
>
> This consumer needs its own DB write (the `Booking.status` update), so it
> must follow `ProvisioningConsumer`'s exact shape per this phase's process
> note above: `enable_auto_commit=False`, manual per-record offset commit only
> after the handler fully finishes, and a bounded retry (a handful of
> attempts, short backoff — mirror `DB_WRITE_MAX_ATTEMPTS`/
> `DB_WRITE_RETRY_BACKOFF_SECONDS`) around the write itself before giving up
> and committing the offset anyway.
>
> Update `docs/decisions-log.md` §7's five-integration-points list if the
> actual topic name or payload shape decided here is worth recording (it
> likely isn't a *decision* delta, just an implementation detail — use
> judgement per the decisions-log-delta check in the phase-end checklist).

**Done when:** triggering a failed test-mode payment (a Stripe test card that
declines, or `stripe trigger payment_intent.payment_failed`) against a real
`PENDING` booking releases its hold **immediately** — verified live by
checking the seat is bookable again well before `HOLD_TTL_SECONDS` would have
expired it passively, not just checking a log line. A redelivery test proves
the consumer is a safe no-op on a duplicate message. Report evidence:
integration-point #4 sequence diagram; this is also the mechanism P8.T5
already measured the *latency* of by simulating it directly (§17's amendment)
— this task is what makes that measurement's premise ("a real
`payment.failed` handler would perform exactly this write") actually true,
worth a one-line cross-reference in this phase's report material rather than
re-measuring anything.

---

## P4.T5 — Confirmation path: successful payment → Booking CONFIRMED

**Prompt to Claude Code:**
> Mirror P4.T4's shape for the success side, but simpler: on a webhook-confirmed
> successful payment (P4.T3), Payment Service transitions its own `Payment` row
> to `succeeded` directly (no Kafka needed for this — Payment Service already
> owns that row, this isn't cross-service data). Whether Booking Service's
> `PENDING → CONFIRMED` transition (§21) is driven by a **new** Kafka message
> (a `payment.succeeded` topic, symmetric with P4.T4) or by some other means
> needs an explicit decision — check `decisions-log.md` §7's five-integration-points
> list first: it enumerates exactly five points and confirmation isn't one of
> the five named there, which suggests either it was meant to be folded into
> an existing point, or the five-point list needs a decisions-log amendment
> (same kind of amendment §7 already got once) to add a sixth. **Do not
> silently invent a sixth integration point** — CLAUDE.md's architecture
> invariants are explicit that this needs discussing first, not assuming.
> Bring this to the user's attention as an open question before implementing,
> the same way the P1 `_check_no_bookings` stub and the P15 amendment were
> both flagged as open questions rather than guessed at silently.
>
> Once resolved: idempotent by construction (only transition if currently
> `PENDING`, same reasoning as P4.T4), and reuses whatever mechanism gets
> decided rather than inventing a second one.

**Done when:** a successful test-mode payment against a `PENDING` booking
flips it to `CONFIRMED`, verified live end-to-end (booking → charge → webhook
→ confirmed), with the integration-point question above explicitly resolved
and recorded (decisions-log amendment if a sixth point is genuinely needed,
or a note explaining why it isn't). Report evidence: Payment sequence diagram
covering the full pay → webhook → confirm flow.

---

## P4.T6 — Tests

**Prompt to Claude Code:**
> Round out the test suite for this phase: unit tests for `PaymentManager`
> methods (given a real or test session, per the internal layering
> convention), and a `testcontainers`-based integration suite (Postgres +
> Kafka, mirroring Phase 3's `community.postgres`/`community.kafka` pattern)
> covering: idempotent webhook (replay → no double effect, P4.T3), decline →
> immediate release (P4.T4, including the redelivery-is-a-no-op case), success
> → confirm (P4.T5). Also add whatever Stripe-call mocking/stubbing is needed
> so the suite doesn't depend on live Stripe API access to run — check how (or
> whether) `stripe-python`'s test utilities or a lightweight HTTP-level mock
> fit this project's existing testing conventions (§18) before reaching for a
> new pattern.

**Done when:** full suite green — unit + integration, both hold strategies'
release path exercised via P4.T4's consumer (not by calling the hold strategy
directly, since the point is proving the Kafka-triggered path end-to-end).
Report evidence: testing-chapter material (§18).

---

## Phase 4 exit checklist (all must pass before P6)

- [ ] Payment Service scaffolded, `payment_db` migrated, wired into
      `infra/docker-compose.yml` and Traefik (`PathPrefix('/payments')`).
- [ ] Stripe test-mode charge created and idempotent (same booking ID twice →
      one charge, proven by test).
- [ ] Webhook endpoint verified against Stripe CLI local forwarding;
      idempotent handling proven by a replay test (no double effect).
- [ ] Kafka #4 (`payment.failed` → immediate hold release) live-verified: a
      declined payment against a real `PENDING` booking releases the seat
      well before TTL/cron-sweep would; redelivery proven a safe no-op.
- [ ] Confirmation path live-verified: a successful payment flips a `PENDING`
      booking to `CONFIRMED`; the integration-point question in P4.T5 explicitly
      resolved and recorded (decisions-log amendment or documented rationale).
- [ ] Full test suite green (unit + testcontainers integration).
- [ ] Validation checkpoint done live: pay a `PENDING` booking → `CONFIRMED`;
      decline a payment → hold released immediately (not on timeout); replayed
      webhooks change nothing.
- [ ] Live walkthrough done at CHECKPOINT; `docs/architecture.html` updated to
      current state; `docs/build-log.md` entry appended; decisions-log delta
      logged if any (expected: at minimum the P4.T5 integration-point
      resolution, possibly the P4.T4 topic/payload detail).
- [ ] Phase-end checklist items 1-9 from `CLAUDE.md` run in full, including
      `/pre-pr` against the diff since this phase's starting commit and this
      file's own exit-checklist boxes checked with evidence (per item 9 —
      not left unchecked the way Phase 3's kickoff doc was before that item
      existed).

**Report evidence captured this phase (§16):** Payment class diagram +
textual schema, idempotency writeup (§9, both mechanisms — charge and
webhook), integration-point #4 sequence diagram, Payment sequence diagram
(full pay → confirm flow), testing-chapter material.

**Next:** Phase 6 — Cancellation & Refunds (Kafka #5), per the locked
report-first build order (§27: P8 → P4 → **P6** → P5 → P7 → P10 → P9/P11).
Generate its task prompts once Phase 4's exit checklist is green.
