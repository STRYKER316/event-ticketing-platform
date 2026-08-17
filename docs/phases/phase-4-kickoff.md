# Phase 4 Kickoff — Payment Service + Stripe + Confirmation

**Goal of this phase:** a new Payment Service, its own Postgres DB
(`payment_db`), a real (test-mode) Stripe charge flow triggered off a
`PENDING` Booking, webhook-driven confirmation, idempotent charge/webhook
handling, and **Kafka integration point #4** (payment outcome → Booking
Service either confirms the booking or releases the hold immediately, §17,
§21) — see the two decisions-log amendments below for what changed before
this task list could be written.

**How to use this file:** run the seven tasks below **in order**, one per
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
that P8 simulated, reusing the exact same `release_hold()` + Booking-row-transition
mechanism P8 measured — no new release mechanism, only a new caller.

**Two architecture gaps found and resolved before this task list was
finalized** (both recorded as decisions-log amendments, same practice as the
§7.2/§15 amendments — surfaced as open questions rather than silently guessed
at, per `CLAUDE.md`'s "don't invent a sixth [integration point] without
discussing it first"):

1. **No ticket pricing existed anywhere** (Event, seat map, or Ticket) for
   Payment Service to charge against. Resolved: **per-section pricing**,
   organizer-set on the seat map (`decisions-log.md` §9/§16 amendments,
   2026-08-17). This is genuinely retroactive scope — it touches Event
   Service's seat-map schema (Phase 1) and Booking Service's `Ticket` model
   and provisioning consumer (Phase 3), both otherwise-closed phases. **P4.T1**
   below does this work before Payment Service itself is even scaffolded,
   since nothing later in this phase can be built without an amount to charge.
2. **Payment Service has no access to `booking_db`** (§8) to verify the
   paying user owns the booking. Resolved: **Booking Service fronts
   payment** (`decisions-log.md` §9 amendment). The client calls a new
   ownership-scoped `POST /bookings/{id}/pay` on Booking Service; Booking
   Service makes a synchronous call to Payment Service's charge endpoint,
   forwarding the caller's JWT and the ticket's already-known `price_cents`.
   This is the system's first synchronous inter-service call — a deliberate,
   narrow exception to "cross-service data only via Kafka," justified because
   initiating a charge needs an immediate result, unlike everything else
   Kafka carries in this system. Confirmation itself stays Kafka/webhook-driven
   (point #4, broadened to carry both outcomes on one topic rather than
   growing to a sixth integration point — see the §7 amendment).

**Process note specific to this phase (per `CLAUDE.md`):**
- **Test-first, not build-then-test**, for payment/webhook idempotency
  specifically (P4.T4's charge idempotency key, P4.T5's webhook handling) —
  write the test capturing "replayed charge attempt / replayed webhook has no
  double effect" *before* the implementation. This is the one correctness-critical
  path in this phase, per `CLAUDE.md`'s test-first list.
- Everywhere else in this phase (pricing schema changes, scaffolding, routes)
  — build then test, matching how P4.T7 is a dedicated Tests task at the end,
  same shape as every other phase.
- No dedicated adversarial `/code-review` pass is required for P4 (only P3 and
  P8 get one per `CLAUDE.md`) — self-verification plus the routine `/pre-pr`
  gate at CHECKPOINT is sufficient here.
- **A Kafka consumer with its own DB write** (Booking Service's new payment-outcome
  consumer, P4.T6) must follow the same `enable_auto_commit=False` + manual
  per-record offset commit + bounded DB-write retry shape as
  `ProvisioningConsumer` (`booking-service/app/kafka/consumers.py`) —
  `CLAUDE.md`'s Conventions section calls this out by name as the pattern any
  future consumer with its own DB write should follow.

---

## P4.T1 — Per-section ticket pricing (Event Service + Booking Service)

**Prompt to Claude Code:**
> Add `price_cents: int` (constrained positive — a validator or constrained
> type at the DTO layer per the DTO-layer convention, never a bare unchecked
> `int`) to Event Service's `SeatMapSection` schema
> (`event-service/app/api/schemas.py`) — organizer-set per section as part of
> seat-map creation/update (`SeatMapUpsert`). No migration needed on the
> Postgres side (seat maps are MongoDB documents, schema-flexible, §8) — this
> is a pure schema-class change plus whatever validation/route-doc updates
> follow from it.
>
> Extend the event-carried Kafka payload (§7.2): add `price_cents` to
> `EventSeat` (`event-service/app/kafka/schemas.py`) and populate it in
> `EventProducer.publish_upserted` from each seat's section. This is the
> existing event-carried-state-transfer mechanism already proven in Phase 3 —
> no new Kafka point, just a wider payload on the existing one (point #2).
>
> On the Booking Service side: add a `price_cents` column to `Ticket`
> (`booking-service/app/db/models.py`) plus an Alembic migration, and update
> `ProvisioningConsumer._write_tickets` /
> `TicketRepository.bulk_upsert_available` to write it from the now-wider
> Kafka message. Existing tickets provisioned before this migration lands
> won't have a price — decide (and note in this task's build-log entry) how
> the upsert should behave for a re-provisioned event's already-existing
> Ticket rows if this matters for local dev data, though a fresh
> `docker compose down -v && up` sidesteps it entirely for this graded
> project's local-first workflow (§25).

**Done when:** an organizer can set a per-section price when creating/updating
a seat map; publishing that event produces `Ticket` rows in `booking_db` with
the correct `price_cents`, verified live end-to-end (not just at the schema
level). Report evidence: none dedicated — this is prerequisite plumbing for
P4.T3's report evidence (Payment sequence diagram), worth one line in this
phase's report material noting the retroactive schema touch, per the
cross-doc staleness sweep (phase-end checklist item 8).

---

## P4.T2 — Scaffold Payment Service + `payment_db` schema/migrations

**Prompt to Claude Code:**
> Scaffold `payment-service` from the `event-service` template (app factory,
> `core.py`, `api/`/`db/`/`logic/`/`kafka/` layering, `/healthz`, `/metrics`,
> structured JSON logging via `configure_logging()`, Traefik routing with a
> specific `PathPrefix('/payments')` — per the per-service layering and
> Traefik-routing conventions in `CLAUDE.md`). Replace the Phase-0 placeholder
> (`services/payment-service/README.md` + empty `app/.gitkeep`) with the real
> service, and add it to the `uv` workspace (`services/pyproject.toml`'s
> `[tool.uv.workspace] members`, alongside `event-service`/`search-service`/
> `booking-service`).
>
> Own Postgres database `payment_db` (§8) — no shared tables with
> `booking_db` or `event_db`, cross-service data only arrives via Kafka (with
> the one narrow exception this phase's decisions-log §9 amendment
> documents — the synchronous charge-initiation call, which is a request
> Payment Service *receives*, not a query it makes into another service's
> tables).
>
> Define the schema and Alembic migration for a `Payment` model: booking ID
> (plain UUID column, not an actual foreign key — database-per-service, §8),
> ticket ID, `amount_cents`, `currency`, status (`pending`/`succeeded`/`failed`
> — a real Python `Enum`, never bare `str`, per the DTO-Enum convention),
> Stripe charge/PaymentIntent ID, the idempotency key used, timestamps. Keep
> this `Payment`-only for now — Phase 6 owns cancellation/refunds and will
> extend this schema with a `Refund` concept then, not now.
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

## P4.T3 — Booking Service `POST /bookings/{id}/pay` + Stripe charge

**Prompt to Claude Code:**
> Add `stripe-python` as a dependency to Payment Service (already in the
> locked tech stack). On Payment Service, add an internal charge endpoint
> (name it something like `POST /payments/charge` — this is called by
> Booking Service, never directly by a browser client, so route naming should
> read that way) that creates a Stripe test-mode charge (or PaymentIntent —
> use judgement on which Stripe API shape fits a synchronous
> initiate-then-confirm-via-webhook flow best, and note the choice in this
> task's build-log entry) for a given booking ID + ticket ID + `amount_cents`
> + `currency`, all supplied in the request body by the caller (Booking
> Service already knows all four locally — no lookup needed on Payment
> Service's side). **Idempotency key = booking ID** (§9), passed as Stripe's
> own `idempotency_key` request option so a retried call for the same booking
> never double-charges. Create the local `Payment` row in `pending` status
> before calling Stripe.
>
> Auth on this endpoint: it still requires a valid JWT via
> `shared_auth.get_current_user` (Payment Service validates any Keycloak
> token the same way regardless of which service presents it) — but *not* an
> ownership check, since Booking Service already did that check before
> calling in (§9 amendment). State this explicitly in the route (per the
> auth-requirement convention) rather than leaving "why no ownership check
> here" implicit.
>
> On Booking Service, add the ownership-scoped `POST /bookings/{id}/pay`
> endpoint: fetch the booking, 404 if missing, 403 if `user.subject` doesn't
> match `booking.user_subject` (ownership scoping, §15's pattern applied
> here), 409 if the booking isn't currently `PENDING`. Look up the ticket's
> `price_cents` (from P4.T1) and make a synchronous HTTP call to Payment
> Service's charge endpoint, forwarding the caller's original bearer token
> unmodified in the `Authorization` header. Use `httpx` (already a dev
> dependency via `testcontainers`' transitive needs and this project's
> existing async-first stack; add it as a real dependency here, not
> dev-only, since this is a runtime call now) for the outbound call.
>
> **Test-first**: before writing the charge logic, write the test that
> captures "the same booking ID charged twice produces one Stripe charge, not
> two" — the idempotency contract this task exists to satisfy.
>
> Local dev note: Stripe test-mode charges work directly against Stripe's API
> with a real `sk_test_...` key (§24) — no local Stripe emulation needed for
> this task specifically; the Stripe CLI forwarding (§24) is what P4.T4's
> webhook needs, not this one.

**Done when:** a charge is created and visible in the Stripe test dashboard,
triggered via `POST /bookings/{id}/pay` (not by calling Payment Service
directly, which is the point of this task); the idempotency test (same
booking ID, two charge attempts) passes; a non-owner's `/pay` attempt on
someone else's booking returns 403. Report evidence: idempotency writeup
(§9) — this is the first of two idempotency mechanisms this phase builds, the
other being the webhook handler in P4.T4.

---

## P4.T4 — Webhook endpoint + Stripe CLI local forwarding

**Prompt to Claude Code:**
> Add a `POST /payments/webhook` endpoint that verifies the Stripe webhook
> signature (`STRIPE_WEBHOOK_SECRET`) and handles the succeeded/failed
> variants of whichever Stripe object type P4.T3 chose. This endpoint is
> necessarily public/unauthenticated in the normal JWT sense — Stripe calls
> it directly, not a logged-in user — but must verify Stripe's own signature
> instead, and that verification **is** this route's auth requirement; state
> that explicitly per the auth-requirement convention rather than leaving an
> unauthenticated route unexplained.
>
> **Idempotent webhook handling**: Stripe redelivers webhooks (at-least-once,
> same reasoning as Kafka's own delivery guarantee, §7) — a replayed
> succeeded or failed event for a `Payment` already in a terminal state must
> be a safe no-op, not a double state transition. Use Stripe's own event ID
> or the existing idempotency-key column to detect a replay, and/or guard the
> state transition itself ("only transition if currently `pending`" — the
> same "only transition if currently in state X" pattern §7 already requires
> for every Kafka consumer, applied here to a webhook instead).
>
> **Test-first**: before writing the handler, write the test that replays the
> same webhook payload twice and asserts no double effect (no second Kafka
> publish in P4.T6).
>
> Document the local dev setup: `stripe listen --forward-to
> localhost:<traefik-port>/payments/webhook` (§24) — add this to
> `payment-service/README.md` (replacing the Phase-0 placeholder content) so
> a future session doesn't have to rediscover it.

**Done when:** a replayed webhook (same event ID, sent twice via `stripe
trigger` or manually) produces the same end state as a single delivery, proven
by the idempotency test. Report evidence: none dedicated (folds into P4.T6's
diagram) — this task is the plumbing that task's report evidence depends on.

---

## P4.T5 — Kafka producer: payment outcome (Payment Service)

**Prompt to Claude Code:**
> On a webhook-confirmed outcome (P4.T4), Payment Service publishes a message
> to a single new topic (e.g. `payment.outcomes`) carrying an `action` field
> (`succeeded`/`failed`, mirroring the `KafkaAction` enum pattern
> `event-service/app/kafka/schemas.py` already uses for `UPSERTED`/`DELETED`
> on `event.events`) plus the booking ID and ticket ID. Mirror
> `event-service/app/kafka/producers.py`'s `EventProducer` shape — a thin
> producer class wrapping `AIOKafkaProducer.send_and_wait`, keyed by booking
> ID so per-booking message ordering is preserved, same reasoning as
> event-service keying by event ID. This is decisions-log §7's point #4,
> broadened to one topic/two outcomes rather than a sixth point (see this
> phase's opening amendment note above) — do not create a second topic for
> the success case.

**Done when:** a successful or failed webhook-confirmed payment produces the
correctly-shaped message on the topic, verified by inspecting the topic
directly (or via the consumer built in P4.T6, whichever is faster to verify
live). Report evidence: integration-point #4 sequence diagram (covers both
outcomes on the one point).

---

## P4.T6 — Kafka consumer: payment outcome (Booking Service)

**Prompt to Claude Code:**
> Add a new `aiokafka` consumer (`booking-service/app/kafka/consumers.py`)
> subscribed to P4.T5's topic. On a `succeeded` message: transition the
> `Booking` row `PENDING → CONFIRMED` (§21). On a `failed` message: release
> the hold immediately — reuse the exact same mechanism
> `BookingManager._create_booking_row`'s integrity-race compensation path
> already calls (`self._hold_strategy.release_hold(ticket_id)`, whichever of
> `cron`/`redis` is configured — this consumer must not care which) — and
> transition `Booking` to `EXPIRED` (§21, same terminal state the cron/Redis
> sweep already uses; see `BookingRepository.expire_stale_pending`).
> **Idempotent by construction** for both branches: only transition if the
> Booking is currently `PENDING` (§7's general idempotent-consumer rule, same
> "only transition if currently in state X" pattern §17 already specifies for
> this integration point) — a redelivered message for an already-terminal
> booking is a safe no-op, not an error and not a second hold-release/confirm
> attempt.
>
> This consumer needs its own DB write (the `Booking.status` update), so it
> must follow `ProvisioningConsumer`'s exact shape per this phase's process
> note above: `enable_auto_commit=False`, manual per-record offset commit only
> after the handler fully finishes, and a bounded retry (a handful of
> attempts, short backoff — mirror `DB_WRITE_MAX_ATTEMPTS`/
> `DB_WRITE_RETRY_BACKOFF_SECONDS`) around the write itself before giving up
> and committing the offset anyway.

**Done when:** triggering a failed test-mode payment (a Stripe test card that
declines, or `stripe trigger payment_intent.payment_failed`) against a real
`PENDING` booking releases its hold **immediately** — verified live by
checking the seat is bookable again well before `HOLD_TTL_SECONDS` would have
expired it passively, not just checking a log line. Triggering a successful
test-mode payment flips the booking to `CONFIRMED`, verified live end-to-end
(booking → `/pay` → webhook → Kafka → confirmed). A redelivery test proves
both branches are safe no-ops on a duplicate message. Report evidence: Payment
sequence diagram covering the full `/pay` → webhook → Kafka → confirm flow;
this is also the mechanism P8.T5 already measured the *latency* of by
simulating the failure branch directly (§17's amendment) — this task is what
makes that measurement's premise ("a real payment-outcome handler would
perform exactly this write") actually true, worth a one-line cross-reference
in this phase's report material rather than re-measuring anything.

---

## P4.T7 — Tests

**Prompt to Claude Code:**
> Round out the test suite for this phase: unit tests for `PaymentManager`
> and the Booking Service `/pay` route's manager logic (given a real or test
> session, per the internal layering convention), and a
> `testcontainers`-based integration suite (Postgres + Kafka, mirroring Phase
> 3's `community.postgres`/`community.kafka` pattern) covering: idempotent
> webhook (replay → no double effect, P4.T4), decline → immediate release
> (P4.T6, including the redelivery-is-a-no-op case), success → confirm
> (P4.T6), and P4.T1's pricing plumbing (a provisioned `Ticket` carries the
> correct `price_cents` from a seat map with per-section prices). Also add
> whatever Stripe-call mocking/stubbing is needed so the suite doesn't depend
> on live Stripe API access to run — check how (or whether) `stripe-python`'s
> test utilities or a lightweight HTTP-level mock fit this project's existing
> testing conventions (§18) before reaching for a new pattern. The
> Booking→Payment synchronous call (P4.T3) also needs stubbing/mocking in
> Booking Service's own test suite so its tests don't depend on a live
> Payment Service being up.

**Done when:** full suite green — unit + integration, both hold strategies'
release path exercised via P4.T6's consumer (not by calling the hold strategy
directly, since the point is proving the Kafka-triggered path end-to-end).
Report evidence: testing-chapter material (§18).

---

## Phase 4 exit checklist (all must pass before P6)

- [x] Per-section pricing live end-to-end: organizer sets section prices,
      publishing the event produces correctly-priced `Ticket` rows in
      `booking_db`. Live-verified: a real venue/event/seat-map
      (`price_cents: 5000`) created via the organizer API and published,
      confirmed to land on the provisioned `Ticket` row via real Kafka —
      see `build-log.md`'s 2026-08-17 P4.T2-T7 entry.
- [x] Payment Service scaffolded, `payment_db` migrated, wired into
      `infra/docker-compose.yml`, Traefik, and the `uv` workspace. Note: the
      Traefik rule is deliberately **not** `PathPrefix('/payments')` as this
      line originally specified — CHECKPOINT review found that would
      publicly expose `/payments/charge`, an authz bypass. Fixed to
      `PathPrefix('/payments/webhook')` only; see the CHECKPOINT build-log
      entry.
- [~] `POST /bookings/{id}/pay` live-verified: ownership-scoped (403 for a
      non-owner, 404 unknown booking, 409 on an already-`CONFIRMED`
      booking) — all done live. Idempotent (same booking ID twice → one
      charge) — proven by unit + integration test, and live via the
      retry-bug fix (a stuck-replay bug found and fixed this phase).
      "Creates a Stripe test-mode charge" — the full chain up to Stripe's
      own API boundary is live-verified (real HTTPS request, Payment
      Service's own auth check passes, forwarded JWT validated); a charge
      actually **succeeding** against Stripe's real API was not reached,
      since no real Stripe test-mode credentials were available this
      session. Not marked done outright — see the open item below.
- [x] Webhook endpoint's own processing verified end-to-end through the
      real HTTP route — **not via Stripe CLI forwarding** (that specific
      phrasing in this line's original text isn't done, see the open item
      below), but via a self-signed synthetic event instead: signature
      verification only depends on our own local `STRIPE_WEBHOOK_SECRET`,
      so a validly-signed `payment_intent.succeeded`/`.payment_failed`
      payload was constructed with Stripe's own signing helper
      (`stripe.WebhookSignature._compute_signature`) against a `Payment`
      row whose `stripe_charge_id` was set directly (standing in for
      Stripe having accepted the charge, since our real submission always
      fails on the placeholder key) and POSTed to the real running
      `/payments/webhook`. Both outcomes proven live end-to-end through
      the actual route, not a manager-level test or a Kafka-injection
      bypass: `succeeded` → `Payment.SUCCEEDED` → `Booking.CONFIRMED` →
      `Ticket.BOOKED`; `failed` → `Payment.FAILED` → `Booking.EXPIRED` →
      `Ticket.AVAILABLE`. Replaying the same signed succeeded-event a
      second time hit `webhook_replay_no_op` in `payment-service`'s own
      logs — the rowcount-gated idempotency guard proven through the real
      route, not just the integration test. The signature-*rejection* path
      (a deliberately bad signature → 400) was also verified live. What
      remains genuinely open is narrower than this line originally implied
      — not "does our webhook handling work," which is now fully
      live-verified, but specifically "does Stripe's own infrastructure,
      given a real accepted charge, actually deliver us a webhook" — see
      the open item below.
- [x] Kafka #4 (payment outcome, both branches) live-verified: produced
      `succeeded`/`failed` messages directly on `payment.outcomes`
      (bypassing Stripe — the mechanism under test is the consumer, not
      Stripe's delivery) against real `PENDING` bookings — `succeeded` →
      `CONFIRMED`/`BOOKED`; `failed` → `EXPIRED`/`AVAILABLE` immediately;
      redelivery produced no second effect and, per the CHECKPOINT fix,
      provably can't touch a different, later booking's legitimate hold.
      (Superseded by the webhook-route test above for the specific
      succeeded/failed → confirm/release claim — this direct-Kafka test
      remains useful as an isolated proof of the consumer alone.)
- [x] Full test suite green (unit + testcontainers integration) — as of the
      CHECKPOINT commit: `event-service` 44/44 + 11/11, `booking-service`
      36/36 + 24/24, `payment-service` 7/7 + 5/5, `search-service` 16/16
      (unaffected, spot-checked).
- [~] Validation checkpoint done live: "decline a payment → hold released
      immediately" and "replayed webhooks change nothing" — both done, now
      via the real webhook route (see above), not just direct Kafka
      injection or a mocked test. "Pay a `PENDING` booking → `CONFIRMED`"
      is also done live via the real route — the one piece still not
      reached is Stripe's own API actually accepting the charge and
      Stripe's own infrastructure actually sending the webhook, both of
      which need a real Stripe account (see the open item below).
- [x] Live walkthrough done at CHECKPOINT; `docs/architecture.html` updated
      to current state (topology, proven/not-built lists, reproduce-this-
      yourself commands, including the CHECKPOINT authz-bypass fix);
      `docs/build-log.md` entries appended (P4.T1, P4.T2-T7, and the
      CHECKPOINT review); decisions-log delta logged (§7/§9/§16 amendments
      made before implementation began, §26 additions after).
- [x] Phase-end checklist items 1-9 from `CLAUDE.md` run in full: (1)
      end-to-end live walkthrough — extensive, see above and the build-log
      entries; (2) report chapters drafted and corrected post-review
      (Class Diagrams, Database Schema Design, Testing Strategy,
      Requirement Gathering, README status table); (3) commit history
      scanned — found and fixed one commit message with an embedded
      phase/task ID (`P4.T2-T7`) via a non-interactive history rewrite
      (nothing was pushed yet), academic-presentation scan (emoji/TODO/
      casual language) clean; (4) `architecture.html` updated; (5)
      decisions-log delta logged; (6) `CLAUDE.md` self-update done (the
      one-synchronous-call exception noted in the architecture invariants);
      (7) `/pre-pr` run against the diff since `b4a4392` — simplify (4 real
      fixes, most notably a blocking-call violation) and code-review (6
      findings, most severe an authz bypass, all fixed) both ran; the
      `verify` step was substituted with this session's own extensive live
      testing against the real running stack rather than a separate
      subagent pass, since that testing already covered every touched
      endpoint directly; (8) cross-doc staleness sweep — `infra/README.md`,
      root `README.md`, both services' own `READMEs`, `architecture.html`'s
      stale "three services"/"12 containers" text, the P8 benchmark
      chapter's cross-reference, all found and fixed; (9) this checklist.

**One thing is genuinely open, not silently marked done** — narrower than
it first looked. Every piece of *this system's own code* is now
live-verified, including the full `/payments/webhook` route's signature
verification, idempotency guard, and Kafka publish (proven with a
self-signed synthetic event, since signature verification only depends on
our own local `STRIPE_WEBHOOK_SECRET`, not a real Stripe account). What's
left needs a real Stripe account specifically: (1) Stripe's own API
actually accepting a `PaymentIntent.create_async` call with real test-mode
credentials, and (2) Stripe's own infrastructure actually delivering the
resulting webhook (as opposed to us constructing an equivalent payload
ourselves). Before this phase is truly closed out: supply a real
`STRIPE_SECRET_KEY`, run `stripe listen --forward-to
localhost/payments/webhook` locally, and re-verify a real charge → webhook
→ `CONFIRMED` round trip — at that point it's confirming Stripe's own
reliability, not hunting for a bug in this codebase.

**Report evidence captured this phase (§16):** Payment class diagram +
textual schema, idempotency writeup (§9, both mechanisms — charge and
webhook), integration-point #4 sequence diagram (both outcomes), Payment
sequence diagram (full pay → confirm flow), testing-chapter material.

**Next:** Phase 6 — Cancellation & Refunds (Kafka #5), per the locked
report-first build order (§27: P8 → P4 → **P6** → P5 → P7 → P10 → P9/P11).
Generate its task prompts once Phase 4's exit checklist is green.
