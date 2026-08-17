# Phase 6 Kickoff — Cancellation & Refunds (Kafka #5)

**Goal of this phase:** a booking-owner-only cancel endpoint on Booking
Service with optimistic immediate seat release, **Kafka integration point
#5** (`booking.cancelled` → Payment Service issues a Stripe test-mode
refund), and a refund-failure path that logs and surfaces via the
notification producer side of integration point #3 — see the three
decisions-log §22 amendments below for what changed before this task list
could be written.

**How to use this file:** run the four tasks below **in order**, one per
Claude Code session. Commit after each (small, green commits, per the
per-service layering conventions in `CLAUDE.md`). `main` stays bootable at
every step. Give Claude Code the repo plus `decisions-log.md` and
`master-development-plan.md` as context so it stays anchored to the locked
decisions (referenced by § below).

**Entry deps:** Phase 3 (Booking Service, `TicketHoldStrategy`, both hold
strategies) and Phase 4 (Payment Service, `Payment` model, Stripe
integration, `PaymentOutcomeProducer`) both complete and checkpointed. Per
the locked report-first build order (§27: P8 → P4 → **P6** → P5 → P7 →
P10 → P9/P11), this is the next phase — not Phase 5, which the master plan
lists earlier in the document but later in the actual build sequence.
Notification Service (Phase 5) does **not** exist yet; see amendment #3
below for how this phase's refund-failure notification is scoped around
that.

**Three architecture/implementation gaps found and resolved before this
task list was finalized** (all recorded as a decisions-log §22 amendment,
same practice as the §7.2/§9/§16 amendments — surfaced as open questions
rather than silently guessed at, per `CLAUDE.md`'s "don't invent a sixth
[integration point] without discussing it first"):

1. **§22's "reusing the exact same release mechanism" claim doesn't hold
   at the code level.** `TicketHoldStrategy.release_hold()` only matches a
   ticket currently `HELD`; a `CONFIRMED` booking's ticket is `BOOKED`.
   Calling `release_hold()` on it would be a silent no-op under the cron
   strategy, permanently stranding a cancelled seat. **Resolved:** a new
   `TicketHoldStrategy.release_booking(ticket_id)` method, implemented
   asymmetrically across the two concrete strategies exactly the way
   `acquire_hold`/`confirm_hold` already are (§6) — see P6.T1 below for
   the full shape.
2. **The "before the event's start time" cancellation cutoff has nowhere
   to read a start time from.** `booking_db` never stores it — the only
   place it exists is a field on the same Kafka message that already
   provisions tickets (`EventUpsertedMessage.start_time`), received and
   discarded by `ProvisioningConsumer`. **Resolved:** a small `Event`
   reference table, written by `ProvisioningConsumer` alongside ticket
   provisioning (same message, no new Kafka point) — same shape as
   P4.T1's retroactive pricing touch. See P6.T1 below.
3. **Kafka #3's consumer (Notification Service) doesn't exist yet** —
   Phase 5 runs after this phase in the actual build order. **Resolved:**
   Kafka producer and consumer are independently deployable, so this
   phase builds the **producer** side only (Payment Service publishes to
   a new `notifications` topic on refund failure); Phase 5 builds the
   consumer later, which is the same eventual-consistency trade-off
   already accepted for integration point #2 (§7's "documented trade-off,
   not a bug" note). This phase's own validation checkpoint verifies
   delivery with a throwaway/console consumer, not Notification Service's
   real retry/DLQ pipeline. Booking-confirmed and payment-confirmed —
   the other two triggers this same topic will eventually carry — are
   **not** retrofitted here; they're out of this phase's scope
   (cancellation/refund only) and stay deferred to Phase 5 itself,
   alongside the consumer that will finally read them.

**Process note specific to this phase (per `CLAUDE.md`):**
- **Build-then-test for everything in this phase** — nothing here is on
  `CLAUDE.md`'s test-first list (that's specifically the dual hold
  strategies, P3, and payment/webhook idempotency, P4). The refund
  idempotency mechanism this phase adds (P6.T2) reuses the exact
  "resubmit only if the provider-side ID column is still `NULL`" pattern
  `PaymentManager.create_charge` already established and had adversarially
  reviewed in Phase 4 — not a new correctness-critical mechanism being
  designed from scratch, so it doesn't independently qualify.
- No dedicated adversarial `/code-review` pass is required for P6 (only P3
  and P8 get one per `CLAUDE.md`) — self-verification plus the routine
  `/pre-pr` gate at CHECKPOINT is sufficient here.
- **A Kafka consumer with its own DB write** (Payment Service's new
  `BookingCancelledConsumer`, P6.T2) must follow the same
  `enable_auto_commit=False` + manual per-record offset commit + bounded
  DB-write retry shape as `ProvisioningConsumer`/`PaymentOutcomeConsumer` —
  `CLAUDE.md`'s Conventions section calls this out by name as the pattern
  any future consumer with its own DB write should follow. This is also
  the first Kafka **producer** Booking Service has ever needed (it has
  only ever consumed) — `core.py` needs a `get_kafka_producer()` added,
  mirroring Payment Service's own (`payment-service/app/core.py`).
- **Publish-before-commit**, not commit-then-publish, for
  `BookingManager.cancel_booking`'s Kafka publish — same reasoning as the
  Phase 4 CHECKPOINT fix to `handle_webhook_event`: publishing after
  commit risks stranding a `CANCELLED` booking whose refund trigger never
  actually reached Payment Service if the publish itself fails, with no
  path back to PENDING-like retry. Publishing first means a publish
  failure propagates uncommitted and the whole cancel request 5xx-and-
  retries cleanly, still `CONFIRMED`.

---

## P6.T1 — Booking cancel endpoint + seat release (Booking Service)

**Prompt to Claude Code:**
> Add `TicketHoldStrategy.release_booking(ticket_id) -> None` as a new
> abstract method on `booking-service/app/logic/helpers/hold_strategy.py`,
> alongside `acquire_hold`/`release_hold`/`is_held`/`confirm_hold`.
> Docstring it as: releases a **booked** (not held) ticket back to
> available on cancellation — a distinct operation from `release_hold()`,
> which only ever matches a `HELD` ticket. Idempotent, same contract as
> `release_hold`.
>
> Implement it in all three concrete strategies:
> - `CronHoldStrategy.release_booking`: `UPDATE tickets SET status =
>   'AVAILABLE', hold_expires_at = NULL WHERE id = ? AND status =
>   'BOOKED'` — same rowcount-gated conditional-UPDATE shape as its
>   siblings in this class, `.flush()` after.
> - `RedisHoldStrategy.release_booking`: a no-op. This strategy never
>   writes `tickets.status` at all (see its own class docstring, §6) — a
>   `CONFIRMED` booking's ticket is already sitting at `AVAILABLE` under
>   Redis. Document inline *why* it's a no-op (point to
>   `uq_bookings_active_ticket` as what actually re-permits booking that
>   ticket again, not this method) rather than leaving an empty method
>   body unexplained.
> - `FakeHoldStrategy.release_booking`: mirror `confirm_hold`'s shape
>   (`self._held.discard(ticket_id)`), for the unit-test suite.
>
> Add a small `Event` reference table to `booking_db` (decisions-log §22
> amendment #2 — the retroactive scope this phase needs, same shape as
> P4.T1's pricing touch): `event_id` (UUID, primary key), `start_time`
> (timestamptz, not null). Add the Alembic migration. Update
> `ProvisioningConsumer._handle` (`app/kafka/consumers.py`) to upsert this
> row (`event_id`, `start_time` from the already-parsed
> `EventUpsertedMessage`) alongside its existing ticket-writing work —
> same session, same idempotent-upsert shape (`ON CONFLICT (event_id) DO
> UPDATE SET start_time = excluded.start_time`, so a republished event
> with a corrected start time stays current rather than sticking to
> whatever value arrived first).
>
> Add `BookingRepository.transition_if_confirmed(booking_id, new_status) ->
> bool`, mirroring `transition_if_pending`'s exact shape (rowcount-gated
> conditional `UPDATE ... WHERE status = 'CONFIRMED'`).
>
> Add `BookingManager.cancel_booking(user, booking_id) -> BookingResponse`:
> fetch the booking (404 if missing), 403 if `user.subject` doesn't match
> `booking.user_subject` (ownership scoping, §15's pattern, same as
> `pay_booking`), 409 if not currently `CONFIRMED`. Look up the `Event` row
> for `booking.event_id`; 409 if `start_time` is at or before now (the
> cancellation-cutoff check amendment #2 above added). Attempt
> `bookings.transition_if_confirmed(booking_id, BookingStatus.CANCELLED)`
> as the race backstop (mirrors `_create_booking_row`'s own
> integrity-race reasoning) — 409 if it matches zero rows (lost a race to
> a concurrent cancel/expiry). On success, call
> `hold_strategy.release_booking(ticket_id)`, then commit. **Kafka publish
> is not part of this task** — P6.T2 adds it; for now `cancel_booking`
> only cancels the booking and releases the seat.
>
> Add the ownership-scoped route: `POST /bookings/{id}/cancel`, mirroring
> `pay_booking`'s route shape (`Depends(get_current_user)`,
> `Depends(get_session)`, `Depends(get_redis)` for
> `get_hold_strategy(session, redis)`). State the auth requirement
> explicitly in the route docstring per the auth-requirement convention.

**Done when:** an owner can cancel their own `CONFIRMED` booking and the
seat becomes bookable again immediately, verified live under **both** hold
strategies (`HOLD_STRATEGY=cron` and `HOLD_STRATEGY=redis`) — a fresh
`create_booking` → `pay` → cancel → `create_booking` round trip on the
same seat succeeds under each. A non-owner's cancel attempt on someone
else's booking returns 403; cancelling an already-`CANCELLED`,
still-`PENDING`, or `EXPIRED` booking returns 409; cancelling past the
event's `start_time` returns 409. Report evidence: Cancellation sequence
diagram (the seat-release half — the refund half is P6.T2's evidence).

---

## P6.T2 — Kafka #5: `booking.cancelled` producer (Booking Service) + refund consumer (Payment Service)

**Prompt to Claude Code:**
> **Booking Service side.** Add `get_kafka_producer()`/
> `close_kafka_producer()` to `booking-service/app/core.py`, mirroring
> `payment-service/app/core.py`'s existing implementation exactly (lock-
> guarded lazy singleton, 10s `request_timeout_ms`, started/stopped in
> `main.py`'s `lifespan`). Add `cancelled_bookings_topic: str =
> "booking.cancelled"` to `Settings`, and the matching
> `CANCELLED_BOOKINGS_TOPIC` env var to `infra/docker-compose.yml`'s
> `booking-service` block (alongside its existing `EVENTS_TOPIC`/
> `PAYMENT_OUTCOMES_TOPIC` entries).
>
> Add `booking-service/app/kafka/producers.py` (new file — this service
> has only ever consumed until now) with a `BookingCancelledProducer`,
> mirroring `payment-service/app/kafka/producers.py`'s
> `PaymentOutcomeProducer` shape: a thin wrapper around
> `AIOKafkaProducer.send_and_wait`, keyed by booking ID. Message schema
> (new `BookingCancelledMessage` in `booking-service/app/kafka/schemas.py`,
> producer side): just `booking_id: uuid.UUID` — Payment Service already
> holds everything else it needs (amount, Stripe charge ID) keyed off that
> ID in its own `payment_db`.
>
> Wire it into `BookingManager.cancel_booking` (P6.T1): after
> `release_booking()`, publish the `booking.cancelled` message **before**
> `session.commit()` — see this phase's "publish-before-commit" process
> note above for why. `BookingManager.__init__` needs the producer passed
> in (same constructor-injection shape as `session`/`tickets`/`bookings`/
> `hold_strategy` — never self-fetched), and the route needs a
> `Depends(get_booking_cancelled_producer)` provider function mirroring
> `payment-service/app/kafka/producers.py`'s `get_payment_outcome_producer`.
>
> **Payment Service side.** Add `PaymentStatus.REFUNDED` to the enum and a
> `stripe_refund_id: str | None` column to `Payment`, plus an Alembic
> migration. Note: adding a value to an existing Postgres `ENUM` type via
> `ALTER TYPE ... ADD VALUE` cannot run inside the same transaction as a
> statement that uses the new value — check how Alembic's `op.execute`
> needs to be structured here (autocommit block, or split into two
> migrations) rather than assuming the usual single-transaction migration
> shape just works.
>
> Add `PaymentManager.refund_payment(booking_id: uuid.UUID, producer:
> NotificationProducer) -> None` (the `NotificationProducer` type comes
> from P6.T3, sequenced after this task — stub/forward-declare or do P6.T2
> and P6.T3 in the same session if that's cleaner than a genuinely broken
> intermediate commit). Fetch the `Payment` by `booking_id`; if missing,
> log a warning and return (defensive — shouldn't happen, a `CONFIRMED`
> booking implies a `SUCCEEDED` payment exists). If `stripe_refund_id` is
> already set, log `refund_replay_no_op` and return — this is the
> idempotency gate, deliberately mirroring `create_charge`'s existing
> "resubmit only if the provider-side ID column is still `NULL`" pattern
> rather than inventing a second idempotency mechanism (a genuinely
> concurrent redelivery race isn't reachable here the same way it was for
> the webhook route: this consumer processes one Kafka partition's
> records strictly sequentially, so only crash-then-restart redelivery is
> possible, not two overlapping deliveries). If `payment.status` isn't
> `SUCCEEDED`, log a warning and return (defensive — an unexpected state
> this design doesn't otherwise reach). Otherwise call
> `stripe.Refund.create_async(payment_intent=payment.stripe_charge_id,
> idempotency_key=f"{booking_id}-refund")` (§9's idempotency-key pattern,
> applied to refunds per §22). On success: set `stripe_refund_id` and
> `status = REFUNDED`, commit. On `stripe.error.StripeError`: this is
> P6.T3's refund-failure path — see that task.
>
> Add `BookingCancelledConsumer` to `payment-service/app/kafka/`
> (new — this service has only ever produced until now; add a
> `consumers.py`), subscribed to the new `booking.cancelled` topic,
> following `enable_auto_commit=False` + manual per-record offset commit +
> bounded DB-write retry, same shape as `ProvisioningConsumer`/
> `PaymentOutcomeConsumer` (mirror `_run_with_retry`/
> `_consume_with_manual_commit` if it's cheap to factor out into a shared
> location, otherwise duplicate the pattern locally — use judgement, note
> the choice in this task's build-log entry).

**Done when:** cancelling a `CONFIRMED` booking whose payment succeeded
produces a real refund visible in the Stripe test dashboard, triggered via
the full chain (`POST /bookings/{id}/cancel` → Kafka → Payment Service →
Stripe), not by calling Payment Service directly. A redelivered
`booking.cancelled` message (hand-crafted, sent twice) produces exactly
one Stripe refund call with the correct idempotency key, verified via
Stripe's own idempotency behavior (same request twice, same response) —
this may only be reachable as far as Stripe's auth boundary in local dev
without real Stripe credentials, same caveat Phase 4's `pay` flow already
carries; state precisely how far it was actually verified, per the
Integrity rule. Report evidence: integration-point #5 sequence diagram.

---

## P6.T3 — Refund-failure path: notification producer (Payment Service)

**Prompt to Claude Code:**
> Add a `notifications` Kafka topic (`notifications_topic` setting on
> Payment Service, env var `NOTIFICATIONS_TOPIC`) and a
> `NotificationMessage` schema (`payment-service/app/kafka/schemas.py`):
> `action` (a real `Enum`, not `str` — start with just
> `NotificationAction.REFUND_FAILED = "refund_failed"`; Phase 5 will add
> `BOOKING_CONFIRMED`/`PAYMENT_CONFIRMED` when it wires up those two
> triggers, not this phase — don't pre-add unused enum members), plus
> `booking_id: uuid.UUID` and `reason: str` (the Stripe error message, for
> whatever eventually reads this in Phase 5 or a manual reconciliation
> query against the topic in the meantime).
>
> Add a `NotificationProducer` (`payment-service/app/kafka/producers.py`,
> alongside the existing `PaymentOutcomeProducer`), same thin
> `send_and_wait` wrapper shape, keyed by booking ID.
>
> Wire it into `PaymentManager.refund_payment`'s (P6.T2)
> `stripe.error.StripeError` branch: log at `warning` (a failed refund is
> an expected, handled failure per the log-level-discipline convention,
> not a system incident — it's Stripe/the card network declining, not a
> bug), then publish a `refund_failed` notification with the booking ID
> and the Stripe error's message. Do **not** change `Payment.status` or
> `stripe_refund_id` on this path (§22's explicit scope boundary — no
> re-lock, no rollback; the row stays `SUCCEEDED` so a future redelivery
> or manual retry can still attempt the refund again) and do **not**
> re-raise past this point — a Kafka consumer's per-message exception
> handling shouldn't kill the background consumer task over a Stripe-side
> failure that's already been logged and surfaced.
>
> Wire `PaymentManager.__init__` (or `refund_payment`'s signature,
> whichever fits the existing constructor-injection convention better —
> `PaymentManager` currently takes `session`/`payments` only) to receive
> the `NotificationProducer`, and add a
> `get_notification_producer()` provider mirroring
> `get_payment_outcome_producer()`.

**Done when:** a simulated refund failure (a `stripe.error.StripeError`
raised via monkeypatching/mocking `stripe.Refund.create_async` in a test,
since local dev's placeholder Stripe key can't reach a genuine
success/decline boundary any more precisely than Phase 4's charge flow
already established) produces a correctly-shaped message on the
`notifications` topic — verified with a throwaway/hand-rolled console
consumer (`kafka-console-consumer` or a short-lived test `AIOKafkaConsumer`
in the integration suite), **not** Notification Service's own retry/DLQ
pipeline, which doesn't exist until Phase 5 (see this phase's opening
amendment #3). `Payment.status` stays `SUCCEEDED`, not silently flipped to
any terminal-looking state, after a failed refund attempt. Report
evidence: Limitation writeup (no saga rollback, §26's existing pull-list
entry — this phase is what makes that entry concrete rather than
aspirational).

---

## P6.T4 — Tests

**Prompt to Claude Code:**
> Round out the test suite for this phase: unit tests for
> `TicketHoldStrategy.release_booking` across all three implementations
> (`CronHoldStrategy`, `RedisHoldStrategy`, `FakeHoldStrategy`), for
> `BookingManager.cancel_booking` (ownership 403, non-`CONFIRMED` 409,
> past-cutoff 409, happy path under `FakeHoldStrategy`), and for
> `PaymentManager.refund_payment` (missing payment, already-refunded
> no-op, non-`SUCCEEDED` status, happy path, `StripeError` path —
> reuse whatever Stripe-call mocking pattern P4.T7 already established
> rather than inventing a second one). A `testcontainers`-based
> integration suite (Postgres + Kafka, mirroring Phase 3/4's
> `community.postgres`/`community.kafka` pattern) covering: cancel →
> `booking.cancelled` → real refund flow end-to-end across both services'
> real consumers (not by calling the manager methods directly — the point
> is proving the Kafka-triggered path), redelivery-is-a-no-op for both
> `BookingCancelledConsumer` (Payment Service side — a duplicate cancel
> message doesn't double-refund) and, if not already covered by P6.T1's
> unit tests, the cron-strategy race backstop
> (`transition_if_confirmed` matching zero rows on a second concurrent
> cancel attempt). Also cover the `Event` reference table
> (`ProvisioningConsumer` upsert, P6.T1) and the refund-failure →
> `notifications` topic path (P6.T3) with an integration test reading the
> topic directly.

**Done when:** full suite green — unit + integration, both hold
strategies' `release_booking` path exercised via the real cancel endpoint
under a running stack, not just at the strategy level in isolation.
Report evidence: testing-chapter material (§18).

---

## Phase 6 exit checklist (all must pass before P5)

- [x] Cancel endpoint live-verified under both `cron` and `redis` hold
      strategies: owner-only (403 non-owner), `CONFIRMED`-only (409
      otherwise), past-event-cutoff (409), seat immediately rebookable
      after cancel. Done during P6.T1 (both strategies) and re-verified
      after the CHECKPOINT fail-open fix, including the specific
      pre-migration-event case that fix was for — see `build-log.md`'s
      2026-08-17 P6.T1 and CHECKPOINT entries.
- [x] Kafka #5 (`booking.cancelled`) live-verified end-to-end: a real
      cancel through `POST /bookings/{id}/cancel` produced a real
      `booking.cancelled` message, consumed by `BookingCancelledConsumer`,
      reaching a genuine `POST https://api.stripe.com/v1/refunds` call —
      failing only at Stripe's placeholder-key boundary (401), same
      tracked gap Phase 4 carries for a real Stripe account, not reached
      further this session. **Redelivery**: precisely scoped, not
      overclaimed — a hand-crafted duplicate correctly *re-attempted* the
      refund (since the first attempt never actually succeeded,
      `stripe_refund_id` stayed `NULL`, the only reachable outcome without
      real Stripe credentials), matching the resubmission-gate design, not
      a strict no-op. The true already-refunded-redelivery no-op case is
      proven in the automated integration suite with a mocked Stripe
      success (`test_refund_payment_replay_against_real_db_does_not_double_refund`),
      not reproduced live.
- [x] Refund-failure path live-verified: the real failed-refund attempt
      above produced a correctly-shaped `refund_failed` message on
      `notifications`, verified directly with a throwaway
      `kafka-console-consumer` (correct `booking_id`, Stripe's real error
      text as `reason`). `Payment.status` confirmed `SUCCEEDED`,
      `stripe_refund_id` confirmed `NULL`, via direct query after the
      failed attempt.
- [x] Full test suite green (unit + testcontainers integration) — final
      counts after CHECKPOINT fixes: `booking-service` 72/72,
      `payment-service` 22/22. See `build-log.md`'s 2026-08-17 CHECKPOINT
      entry.
- [x] Live walkthrough done at CHECKPOINT (see the two items above);
      `docs/architecture.html` updated to current state (topology,
      integration-point #5 row, the new `Event` reference table note,
      proven/not-built lists, the CHECKPOINT fail-open finding);
      `docs/build-log.md` entries appended for P6.T1, P6.T2+T3, P6.T4, and
      the CHECKPOINT review; decisions-log delta logged (the §22 amendment
      made before implementation began, plus the §26 `HOLD_STRATEGY`
      limitation added during CHECKPOINT review).
- [x] Phase-end checklist items 1-9 from `CLAUDE.md` run in full: (1)
      end-to-end live walkthrough — see above; (2) report chapters drafted
      then corrected post-CHECKPOINT (Requirement Gathering, Class
      Diagrams, Database Schema Design, Testing Strategy, `README.md`
      chapter-status table); (3) commit history scanned — ten commits
      this phase, each a real unit of work, no bare mechanical tweaks, no
      phase/task IDs in messages; academic-presentation scan (emoji/TODO/
      casual language) clean; (4) `architecture.html` updated (above); (5)
      decisions-log delta logged (above); (6) `CLAUDE.md` self-update
      done — the Kafka-testcontainer note corrected during P6.T4; (7)
      `/pre-pr` run against the diff since `e299021` — simplify (4 real
      dedups) and code-review (6 findings, most severe a fail-open
      cancellation-cutoff bug, all fixed) both ran; `verify` skipped as a
      separate subagent pass since every fix was already live-tested
      directly in this session against the real running stack; (8)
      cross-doc staleness sweep — root `README.md`, `infra/README.md`,
      both services' own READMEs, `docs/report/README.md`'s chapter table,
      and the affected report chapters all found stale and fixed.
- [x] This checklist itself — every box above flipped to `[x]` with a
      one-line note pointing at actual evidence, not left unchecked
      despite genuinely-done work (the failure mode Phase 3's own kickoff
      doc had, per `CLAUDE.md`'s phase-end checklist item 9).

**Next:** Phase 5 — Notification Service + Retry/DLQ (Kafka #3), per the
locked report-first build order (§27: P8 → P4 → P6 → **P5** → P7 → P10 →
P9/P11). Phase 5 builds the consumer for the `notifications` topic this
phase's P6.T3 started producing to, plus the two other trigger call sites
(booking-confirmed in `PaymentOutcomeConsumer`, payment-confirmed in
`PaymentManager.handle_webhook_event`) this phase deliberately left alone.
Generate its task prompts once Phase 6's exit checklist is green.
