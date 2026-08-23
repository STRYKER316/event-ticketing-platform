# Testing Strategy

*Status: draft, partial — establishes the pattern from Phase 1's first
`testcontainers-python` suite (`shared_auth`'s mocked-JWKS unit tests were
Phase 0's contribution; this chapter grows with every phase's test suite,
per the DOCUMENT step in `CLAUDE.md`). Reorganized 2026-08-23 from a
phase-by-phase chronicle into the theme structure below — same content,
regrouped around the tier model, the idempotency-testing pattern across
all five Kafka integration points, and what each review-gate type caught,
rather than one section per phase. This is also now the canonical home for
several bug narratives previously told in full in more than one chapter
(the Redis hold-sweep gap, the Traefik `/payments/charge` routing bypass,
the `price_cents` bind-param overflow, and the `stripe_charge_id IS NULL`
retry bug) — Class Diagrams, Database Schema Design, and Technologies Used
now point here rather than re-telling each in full. No status label
changed as part of this reorganization.*

## The tier model

Testing in this project runs across four deliberately different tiers,
each proving something the tiers around it structurally cannot: **unit
tests** (business logic isolated from real dependencies), **integration
tests** (`testcontainers`, real datastores/broker), **adversarial testing**
(malicious/edge-case input driven live against the running stack), and
**live traffic through the real stack** (a real HTTP client or browser
driving the entire deployed system, not a single service's own test
client). A fifth, later arc — **post-launch hardening rounds** — repeats
and extends the adversarial/live-traffic tiers deliberately, ahead of
deployment. A sixth concern runs across all of them rather than being its
own tier: proving every Kafka consumer's redelivery is a safe no-op,
required explicitly by `CLAUDE.md`'s architecture invariants (§7) at each
of the five integration points — that gets its own section below rather
than being scattered across each tier's description.

## Tiers 1 and 2 — unit and integration tests

**Unit tests** exercise business logic in isolation, with the Repository
layer mocked out rather than hitting a real database. These target the
one thing a database round-trip can't cheaply prove on every run: does the
*logic* make the right call given a known state? Event Service's unit
suite (`tests/unit/test_event_manager_ownership.py`) constructs an
`EventManager` against fake session/mongo objects, substitutes a mocked
`EventRepository` returning a canned `Event`, and asserts the ownership
branch: an owning organizer's `Principal.subject` matching
`Event.organizer_id` succeeds; a mismatched one raises `HTTPException(403)`
before any repository write is attempted. Fast (no container startup),
deterministic, and — critically — this is the layer that actually encodes
the ownership-scoping invariant (§15), so it's the layer that must be
covered directly, not inferred from an end-to-end HTTP test that also
happens to exercise it.

**Integration tests** run against real dependencies via
`testcontainers-python` — real Postgres, real MongoDB, no mocks, no
in-memory substitutes. Event Service's suite
(`tests/integration/test_event_flow.py`) spins up both containers
session-scoped, runs the actual Alembic migration against the container
(not a hand-written `CREATE TABLE`, so migration drift would be caught
here too), and exercises the full `EventManager` flow: create an event as
one organizer, fetch it, attach and fetch a seat map from the real Mongo
container, then attempt a cross-organizer update/delete and assert both
are rejected — the same ownership guarantee as the unit test, but proven
against a real committed row and a real second query, not a mock's
return value.

This project does not test through the HTTP layer (no `TestClient`
hitting FastAPI routes) for these suites — `EventManager` methods are
called directly. The route layer is intentionally thin (Conventions,
`CLAUDE.md`: "routes — thin, no business logic"), so there is nothing
route-specific left to test once the dependency wiring (`Depends(...)`)
is visually verifiable by reading the route file; testing the Manager
directly is a more direct test of the actual contract.

### Established pattern for Phases 2+

Session-scoped container fixtures (one `PostgresContainer`/
`MongoDbContainer` boot per test session, not per test — container startup
is the expensive part), a single migration run per session, and
table-truncation (not container restart) between individual tests for
isolation.

### A concrete bug this caught (Phase 1)

Alembic's autogenerate scaffold for `migrations/env.py` unconditionally
overwrote whatever `sqlalchemy.url` the caller configured with a URL
derived from the service's own `Settings()` (i.e., whatever `.env` the
shell happened to have sourced). Harmless for the plain CLI case
(`alembic upgrade head` run by a developer), but the integration test
fixture needs Alembic to run against the *testcontainers-generated* URL,
not whatever `.env` state was lying around in the host shell —
first failure looked like a stray-environment authentication error against
a Postgres role that didn't exist in the container at all. Fixed by having
`env.py` prefer an explicitly-passed `config.attributes["sqlalchemy_url"]`
and fall back to `get_settings()` only when the caller didn't set one.
Concrete example of integration tests catching an environment-coupling bug
a mocked unit test structurally cannot.

**Status:** Implemented, Tested, Verified (live, against the running stack —
not just the pytest suite). 54/54 `event-service` tests green (43 unit, 11
integration), 18/18 `search-service` tests green, as of the pre-Phase-3
checkpoint: `cd services/<service> && uv run pytest`.

### Testing an idempotent Kafka consumer, for real (Phase 2)

Search Service's integration suite (`tests/integration/test_event_consumer_flow.py`)
extends the session-scoped-container pattern with `testcontainers`'
`KafkaContainer` (Confluent image, KRaft mode — see the Technologies Used
chapter for why that image differs from the compose stack's) and
`ElasticSearchContainer`, running a real `EventConsumer` as a background
`asyncio.Task` against them. Two things this suite proves that a mocked
unit test structurally cannot:

- **The eventual-consistency window is real and bounded, not assumed.**
  The test asserts a document does *not* exist immediately after
  publishing (`await es_client.exists(...)` is `False` right after
  `send_and_wait`), then polls until it does — capturing the exact
  asynchronous-indexing trade-off called out in decisions-log §7/§26 as an
  honest design consequence rather than a bug, with a passing test as the
  evidence rather than just a written claim.
- **Redelivery is a proven no-op, not an assumed one**, for both directions:
  replaying an identical `upserted` message leaves the document count at
  one (`_version` increments; no duplicate document), and replaying a
  `deleted` message against an already-deleted document doesn't raise —
  the same two cases the architecture invariants require testing, not
  just implementing, for every Kafka consumer (§7).

Event Service's own new unit suite this phase
(`tests/unit/test_event_producer.py`) closes a gap the ownership tests
never covered: it mocks the underlying `AIOKafkaProducer` directly and
asserts the actual wire-level shape of what gets sent — correct topic,
the event ID as raw key bytes (not JSON-wrapped), and the exact JSON
payload structure — rather than only asserting that `EventManager` *calls*
the producer, which the pre-existing tests already did via a mock.

### A real infrastructure bug this suite caught, not a code bug (Phase 2)

Building the Kafka+Elasticsearch integration suite surfaced a genuine
environment problem, not a flaky test: a freshly created Elasticsearch
index sat at cluster status `red` indefinitely, its primary shard never
allocating even after an explicit 30-second `wait_for_status=yellow`.
Root-caused to Docker Desktop's VM disk sitting at 90% usage (a single
orphaned, 101.6GB anonymous volume unrelated to this project) —
Elasticsearch's disk-based shard-allocation watermark was correctly
refusing to allocate onto a node that looked full. This is the kind of
thing a mocked or in-memory test can never surface, and is a concrete,
citable example of why the integration tier exists at all. Fixed (with
user confirmation before touching Docker state) by pruning the orphaned
volume; also motivated a genuine production hardening of
`EventIndexRepository.ensure_index()` (explicit `number_of_replicas: 0`
for this project's single-node topology, plus blocking on cluster health
before returning) rather than only a test workaround — see
`docs/build-log.md`, P2.T3 entry, for the full diagnosis.

### Test-first for the one correctness claim the whole report leans on (Phase 3)

Every prior tier in this chapter was build-then-test. Phase 3 is the
deliberate exception, per `CLAUDE.md`'s explicit process note for this
phase: the `TicketHoldStrategy` contract test and both concrete
implementations' race tests were written *before* their implementations,
not after — "does this behave correctly under a tricky concurrent/edge
case" is exactly the condition `CLAUDE.md` calls out as warranting
test-first, and no other case in this codebase to date has warranted it.

**The shared-contract-first sequence, concretely.** P3.T3 wrote
`tests/unit/test_hold_strategy_contract.py` — acquire-on-available
succeeds, second-acquire-on-held fails, release-then-reacquire succeeds,
release-of-unheld is a no-op, and (running the acquire call 20 times
concurrently via `asyncio.gather`) exactly one of them wins — against a
`FakeHoldStrategy` that did not exist yet, confirmed to fail with
`ModuleNotFoundError` before writing a single line of the interface or
the fake. P3.T4 and P3.T5 then wrote the identical race assertion again,
each against a strategy class that likewise did not exist yet, confirmed
failing first, before implementing `CronHoldStrategy` and
`RedisHoldStrategy` respectively. The same contract test, run against
three independent implementations (one in-memory, two backed by real
infrastructure), is what makes "both strategies satisfy the same
guarantee" a proven claim rather than an assumption from reading two
separate implementations and hoping they agree.

**Why 20-25 concurrent clients via `asyncio.gather`, not a smaller
number.** A race test with too few concurrent callers can pass by luck
even with a genuinely broken lock — two callers might simply not
interleave badly on a given run. Every race test in this phase re-runs
the same assertion against 20 (`test_hold_strategy_contract.py`,
`test_cron_hold_race.py`, `test_redis_hold_race.py`) or 25
(`test_concurrency_suite.py`, going through the full `BookingManager`
flow rather than the strategy alone) simultaneous attempts, and every
race test in this phase was manually re-run several times in a row
during development specifically to rule out a false-positive green —
concurrency bugs are exactly the class that can pass once and fail on
the next run, so "it passed" isn't trusted here without "it passed
repeatedly."

**Two genuinely different atomicity mechanisms, proven against the
identical contract.** `CronHoldStrategy.acquire_hold` is one atomic
conditional Postgres `UPDATE ... WHERE status = 'available'` —
correctness comes from row-level locking under the database's own
`READ COMMITTED` isolation, the same TOCTOU-safe pattern already used to
fix `BaseRepository.delete()`'s race in the pre-Phase-3 hardening pass
(same bug class, much higher stakes here). `RedisHoldStrategy.acquire_hold`
is `SET key value NX EX seconds` — correctness comes from Redis's own
atomic command semantics, an entirely different storage system with a
different atomicity guarantee. Neither implementation shares a code path
with the other; the only thing they share is the interface and the test
suite that holds both to the same bar.

**Idempotent provisioning, proven structurally, not just asserted.** The
provisioning consumer (integration point #2, §7.2) is tested for
redelivery the same way Search Service's consumer was in Phase 2 —
sending the identical Kafka message twice and asserting the ticket count
doesn't grow past what one delivery would produce
(`tests/integration/test_provisioning_consumer.py`,
`test_concurrency_suite.py`) — but the mechanism differs meaningfully
from Phase 2's upsert-by-ID: `bulk_upsert_available` is a single
`INSERT ... ON CONFLICT DO NOTHING` statement per message, so there is no
application-level "have I seen this before" branch to get wrong.
Idempotency here is a property of the unique constraint plus the SQL
statement shape, not of conditional logic the test is checking for a
bug in.

**A live discovery during this phase's own testing, not a code bug.**
Restarting Booking Service's container in quick succession (rebuild →
up → rebuild → up, while developing and manually verifying the
provisioning consumer) once left the `aiokafka` consumer group showing
an actively-heartbeating member that nonetheless stopped advancing past
a one-message backlog for over 40 seconds — the group coordinator's own
heartbeat task stays alive independently of whether the code iterating
`async for record in consumer` is actually still running, so a silently
died background task and a genuinely slow-but-alive one look identical
from `kafka-consumer-groups.sh --describe` alone. A single clean restart
resolved it deterministically and immediately (same consumer group,
generation incremented, processed the pending message within seconds of
rejoining) — consistent with test-methodology interference from rapid
back-to-back container recreation, not a defect in `ProvisioningConsumer`
itself, which passes its full redelivery and idempotency suite
repeatably. Motivated a real, permanent hardening either way: `main.py`'s
background consumer task now has a `done_callback` that logs at
`critical` if the task ever actually dies, since a background
`asyncio.Task`'s exception is otherwise only surfaced when the task
object is garbage-collected — which never happens while `lifespan` holds
a live reference to it for the app's entire run, so a real crash would
otherwise be completely silent. Worth citing as the same category of
finding as Phase 2's Elasticsearch disk-watermark incident: something
live testing surfaces that no mocked or purely logical test structurally
can.

**Status:** Implemented, Tested, Verified (live, both hold strategies).
44/44 `booking-service` tests green (23 unit, 21 integration), plus
`event-service`'s suite at 54/54 (55 prior, net -1 after this phase's
`EventDeletedMessage`/`publish_deleted` dead-code removal — event-service now
refuses to delete a `PUBLISHED` event outright, per the §15 Phase 3
amendment, so the message and its producer method were never reachable).
`cd services/booking-service && uv run pytest`.

## Idempotency testing across all five Kafka integration points

Every phase from Phase 2 onward built its own Kafka consumer's redelivery
test as part of that phase's own work (the general idempotent-consumer
rule, §7, was never a Phase 9 invention — see the tier sections above for
each one as it was built). What Phase 9 (P9.T1) adds is the one point
that had never gotten a literal identical-message-redelivered-twice test,
and a single table that names all five integration points' redelivery
test in one place rather than leaving that claim scattered across five
phases' worth of prose:

| # | Integration point (§7) | Redelivery test | What it asserts |
|---|---|---|---|
| 1 | Event → Search | `search-service/tests/integration/test_event_consumer_flow.py::test_publish_makes_event_searchable_and_redelivery_is_a_noop`, `::test_delete_removes_document_and_redelivered_delete_is_a_noop` | Redelivered upsert creates no duplicate ES document; redelivered delete on an already-deleted document doesn't raise |
| 2 | Event → Booking (provisioning) | `booking-service/tests/integration/test_provisioning_consumer.py::test_redelivered_message_creates_no_duplicate_tickets` | Redelivered "event published" message provisions no duplicate `Ticket` rows |
| 3 | Booking/Payment → Notification | `notification-service/tests/integration/test_notification_flow.py::test_redelivery_of_same_message_is_a_safe_no_op` | Redelivering the identical raw message after a successful delivery produces no second retry-ladder entry |
| 4 | Payment → Booking (outcome) | `booking-service/tests/integration/test_payment_outcome_consumer.py::test_redelivered_succeeded_message_confirms_and_notifies_exactly_once` (added this phase) | Redelivering the identical "succeeded" message confirms the booking and notifies exactly once, not twice — the one point that previously only had a *stale cross-message* test (`test_redelivered_message_on_already_confirmed_booking_does_not_touch_a_new_holder`, a genuinely different scenario: a late "failed" arriving after a different outcome already won), not a literal same-message-twice test |
| 5 | Booking → Payment (refund) | `payment-service/tests/integration/test_refund_flow.py::test_booking_cancelled_consumer_redelivery_is_a_safe_no_op` | Redelivering the identical "booking cancelled" message issues exactly one Stripe refund, not two |

All five confirmed green against real Postgres/MongoDB/Redis/Kafka
testcontainers at the time this test was added (`31 passed`
booking-service — since risen to 33 with P9.T3's two additions below,
`2 passed` search-service, `9 passed` payment-service, `6 passed`
notification-service integration suites). Point 4's addition is the only
new test this task added; points 1, 2, 3, and 5 already had their own
redelivery coverage from the phase that built them and needed no new
test, only citing. Live redelivery against the running stack (not just
testcontainers) for all three DB-writing consumers is covered separately
in the post-launch hardening rounds below (Round 5).

### Edge cases closed in Phase 9: double-cancel and pay-after-hold-lost

Two of the three edge cases the master plan names for this phase already
had both a correctness guard and a test before this phase started: webhook
replay (`payment-service/tests/integration/test_payment_flow.py::
test_webhook_transitions_payment_and_replay_is_a_safe_no_op`) and the
expired-hold race under *concurrent* acquisition
(`booking-service/tests/integration/test_cron_hold_race.py`,
`test_concurrency_suite.py`) — reconfirmed green, no new test needed.

The other two — double-cancel and pay-after-hold-lost — had a subtler gap
than "no test exists." Both guards already had a **unit-level** test
proving the `BookingManager` reacts correctly when its repository layer
*reports* a lost race (`test_booking_manager.py::
test_cancel_booking_race_lost_409s` mocks `transition_if_confirmed` to
return `False`; `test_pay_booking_on_non_pending_booking_409s` mocks
`bookings.get_by_id` to return an already-CONFIRMED booking). Neither
proved the real repository method — a genuine rowcount-gated Postgres
`UPDATE`, or a real booking actually swept to `EXPIRED` — produces that
signal in the first place under a real sequential re-call. Added two
integration tests closing that gap:

- `booking-service/tests/integration/test_cancel_booking_kafka.py::
  test_double_cancel_second_call_409s_and_does_not_re_release_or_republish`
  — cancels a real seeded CONFIRMED booking through `BookingManager`
  (real `BookingRepository`/`TicketRepository`/`CronHoldStrategy`, a fresh
  `AsyncSession` per call, mirroring two separate real HTTP requests),
  then calls `cancel_booking` again on the same booking ID. Asserts the
  second call 409s, its `BookingCancelledProducer` mock is never awaited
  (no second `booking.cancelled` message), and the ticket/booking rows are
  left exactly as the first call set them.
- `booking-service/tests/integration/test_pay_booking_after_hold_expiry.py::
  test_pay_booking_after_hold_expired_via_real_sweep_409s_without_charging`
  — seeds a genuinely stale `PENDING` booking, runs the real sweep
  (`BookingRepository.expire_stale_pending()`, the same call
  `hold_sweep.py`'s scheduled job makes) so it actually transitions to
  `EXPIRED`, then calls `pay_booking`. Asserts a 409 and that the mocked
  `httpx` client's `post()` is never awaited — a charge is never even
  attempted against a booking nobody can legitimately complete.

Both pass against real Postgres testcontainers; full `booking-service`
suite (unit + integration) is 80/80 after these additions (77 prior +
1 P9.T1 redelivery test + these 2).

## Tier 3 — adversarial testing against the live stack

The two tiers above test what the code was *written* to do. Neither
`testcontainers` unit/integration suite is adversarial by construction — the
inputs are the developer's own idea of what a caller sends.

### Eight rounds ahead of Booking Service: malformed input, forged auth, races, outages

Before Phase 3 (Booking Service) started building on this surface, a
separate pass tested what happens when a caller doesn't cooperate:
malformed input, forged auth, races, and infrastructure outages, run live
against the running `docker compose` stack (real Keycloak tokens for three
seeded users, real Postgres/MongoDB/Elasticsearch/Kafka — no mocks) rather
than through pytest.

Eight rounds, roughly 130 individual checks, covering: JWT/auth forgery
(`alg: none`, tampered-payload role escalation with the stale original
signature, unknown `kid`, truncated tokens, real-time token expiry — 19/19
clean, `algorithms=["RS256"]` pinning specifically blocks the RS256→HS256
confusion attack); DTO boundary fuzzing (oversized strings, NUL bytes,
integer overflow, malformed JSON, NaN/Infinity floats); search-query safety
(Lucene/injection-style strings against `multi_match`); Kafka behavior under
redelivery, malformed messages, and a real broker outage; concurrent-request
races (publish, patch, seat-map upsert, delete); and a Postgres-outage
comparison. Five real bugs surfaced, all in `event-service`, none in the
Kafka/search integration point this project treats as its highest-risk
surface (§7):

- Three DTOs (`VenueCreate.name`/`address`, `EventCreate.title`, `.capacity`)
  had no upper bound, so an over-length or over-large value crashed as an
  unhandled `asyncpg` error (a bare 500) instead of the 422 CLAUDE.md's own
  "DTO layer is a strict validation boundary" rule promises.
- `DELETE /events/{id}` wasn't safe under concurrent duplicate requests: ten
  concurrent deletes against one event returned five 204s, not one — the
  repository used `session.delete()+flush()`, which can't distinguish "I
  deleted it" from "it was already gone."
- The Kafka producer had no request timeout, so a broker outage produced a
  ~40-second hang before failing, not a fast, clean error — on top of the
  already-documented commit-then-publish consistency risk, this made the
  *failure mode itself* worse than expected under load.

Postgres-outage behavior was tested as a comparison point and came back
clean by contrast: a stopped Postgres container fails every dependent
request in 10-20ms (TCP refusal, not a slow timeout), and `pool_pre_ping`
recovers transparently on the next request with no restart needed — the
asymmetry between the two outage modes is itself a finding worth having on
record before Booking Service adds a third datastore (Redis) to reason
about.

All five fixes shipped with regression tests in the real suite (not just the
adversarial scripts) and were re-verified live against the rebuilt
containers, not just the automated suite passing.

### The review pass finding a bug in its own fix

The `/pre-pr` gate run on that fix commit (simplify → code-review, scoped to
the commit's own diff rather than re-reviewing already-checkpointed history)
is itself worth citing as evidence for why a dedicated review step earns its
place separately from self-verification: fixing the NaN/Infinity gap above
(`Field(allow_inf_nan=False)`) introduced a *new*, worse bug live — FastAPI's
default validation-error handler echoes the rejected value back in the 422
body, and Starlette's JSON encoder can't serialize `NaN`, so the rejection
itself crashed into a 500. The review agent's own first-pass finding
(misattributing the fix to a nonexistent aiokafka parameter) was also caught
and retracted on its own self-verification pass before reaching this report
— a review process that checks its own output, not just the code's.

## Review gates: self-verification, dedicated adversarial review, and CHECKPOINT `/pre-pr` — what each catches

This project runs three distinct review gates on different schedules:
self-verification (live testing before claiming a task done) runs on
every phase; a dedicated adversarial `/code-review` pass runs
additionally on only P3 and P8, the two phases whose correctness or
measurement claims the whole report leans on; the routine `/pre-pr` gate
(simplify → code-review → verify) runs at every CHECKPOINT regardless.
None of the three substitutes for the others — the sections below are the
direct evidence for that, not just an assertion: Phase 3's own second
adversarial pass caught bugs the first pass's fixes introduced, Phase 5's
second CHECKPOINT round caught a bug the first round's own fix
introduced, and Phase 4's and Phase 6's routine CHECKPOINT gates each
caught a real security or correctness bug self-verification's live
walkthrough had missed.

### Phase 3 — two review passes, the second catching real bugs in the first pass's fixes

Self-verification (live testing before claiming done) is the default review
gate for every phase; P3 is one of two phases (with P8) that additionally
gets a dedicated adversarial `/code-review` pass on top of it, per
`CLAUDE.md`, because the dual hold strategy is the one bug class that
silently corrupts the product's core guarantee. Two passes actually ran here,
not one, and the second earned its place by finding real defects the first
pass's own fixes introduced — direct evidence for why this gate exists as a
separate step rather than folding into self-verification.

**Pass 1** (the routine `/pre-pr` code-review step, run against the full
phase diff) found five high-severity issues, all in code that had already
passed its own test suite: the cron sweep's expiry UPDATE filtered only by
ticket ID, so a hold re-acquired between its SELECT and UPDATE would be
silently reset to AVAILABLE, contradicting the method's own TOCTOU-safety
docstring; `aiokafka`'s `enable_auto_commit` was left at its default `True`,
so Kafka offsets advanced on a timer independent of whether the DB write
underneath them had actually succeeded; the provisioning consumer's DB write
had no error handling at all, so any DB error killed the background consumer
task permanently while `/healthz` stayed green; the multi-row ticket INSERT
bound 5 params/seat with no batching, overflowing Postgres's ~32,767
bind-param cap for any venue past roughly 6,500 seats; and — the most
consequential for this phase's own benchmark premise — the Redis strategy
had no mechanism at all for expiring an abandoned `PENDING` Booking row,
since Redis's own key-expiry frees the *hold* but was never wired to touch
the Booking row it doesn't know about, so one abandoned checkout under
`HOLD_STRATEGY=redis` made that seat permanently unbookable
(`uq_bookings_active_ticket` blocks it forever). That last one is worth
flagging specifically: it meant the two hold strategies weren't actually
behaviorally equivalent, which would have quietly undercut the P8 benchmark
comparison this phase exists to set up — see the Class Diagrams and
Database Schema Design chapters for the resulting fix's design and schema
consequences.

**Pass 2** (a dedicated adversarial `/code-review`, run after Pass 1's fixes
were applied) found four more defects — all introduced or left incomplete by
Pass 1's own fixes, not new discoveries in the original code:

- The DB-write try/except added to satisfy Pass 1's "don't let a DB error
  permanently kill the consumer" finding still let `run()` commit the Kafka
  offset unconditionally after a caught failure — silently losing that
  message's tickets on the very first transient error, the opposite of what
  disabling `enable_auto_commit` was introduced to guarantee. Fixed with a
  bounded in-process retry (3 attempts, 1s backoff) before the consumer
  accepts the loss and moves on, with the final give-up logged at `critical`
  rather than silently.
- `main.py`'s teardown called `scheduler.shutdown(wait=False)`
  unconditionally; if `kafka_consumer.start()` failed before
  `scheduler.start()` ever ran, APScheduler's `shutdown()` raises
  `SchedulerNotRunningError` on a still-stopped scheduler, masking the
  original startup error and aborting the rest of cleanup. Verified directly
  against the installed `apscheduler` source before fixing with a
  `scheduler.running` guard.
- The cron sweep's Booking-row UPDATE (added to fix Pass 1's TOCTOU finding)
  re-checked ticket ID only, asymmetric with the Ticket UPDATE right next to
  it, which *did* get the fresh status/expiry re-check. Not exploitable in
  today's codebase (no cancellation endpoint exists yet to create the
  intervening state change), but fixed for consistency before it becomes
  exploitable later.
- The two batch-size constants Pass 1's fixes introduced
  (`INSERT_BATCH_SIZE`, `SWEEP_BATCH_SIZE`) were independently defined with
  the same magic number in two files — extracted into one shared
  `app/db/chunking.py` helper so they can't silently drift apart.

**Live re-verification, not just the automated suite.** Beyond the two
review passes, the fixed booking flow was walked end-to-end against the real
running stack post-fix: create venue → create event → attach a 12-seat map →
publish → confirm Kafka delivers `tickets_provisioned` with all 12 seats
inserted → book a seat → confirm an immediate duplicate booking attempt on
the same seat gets `409` → run 20 concurrent booking attempts against one
fresh seat and confirm exactly one `201` and nineteen `409`s. The Redis-sweep
fix specifically was re-verified live by temporarily switching the running
container to `HOLD_STRATEGY=redis` with a 5-second TTL/sweep interval:
booked and deliberately abandoned a seat, confirmed a second booking attempt
was correctly rejected while the Redis hold was still live, waited past the
TTL, confirmed the scheduler's `_sweep_stale_redis_bookings_once` job logged
`hold_sweep_expired_stale_redis_bookings` (`count: 1`), then confirmed a
fresh booking attempt on that same seat now succeeded — the exact bug
Pass 1 found, reproduced and confirmed fixed against real Postgres and real
Redis, not just the test suite.

**Status:** Implemented, Tested, Verified (live, both hold strategies).
44/44 `booking-service` tests green (23 unit, 21 integration), plus
`event-service`'s suite at 54/54, as captured in the Tiers 1/2 section
above.

### Phase 4 — a live-testing bug, then a CHECKPOINT gate catching a real security bug

Only P3 and P8 get a dedicated adversarial `/code-review` pass on top of
self-verification (`CLAUDE.md`) — Phase 4 relies on self-verification plus
the routine `/pre-pr` gate at CHECKPOINT. Test-first was used specifically
for the two correctness-critical idempotency contracts this phase adds
(§9): the charge idempotency test
(`test_create_charge_calls_stripe_with_booking_id_as_idempotency_key` and
its replay counterpart) and the webhook replay test, both written before
their respective handlers.

**Self-verification's live walkthrough found a real bug no unit test had
caught**: `PaymentManager.create_charge`'s idempotent short-circuit treated
any existing `Payment` row as "already submitted to Stripe," including one
whose `stripe_charge_id` was still `NULL` because the previous attempt
never actually reached Stripe (a genuine `401 Invalid API Key` against the
placeholder `.env` credential, not a hypothetical). A second `/pay` call
against the same booking replayed the stale row instead of retrying —
reproduced live (`/pay` → 502 → `/pay` again → incorrectly `200` with the
stale `pending` row), fixed to check `stripe_charge_id is not None`
specifically, re-verified live (retry now genuinely re-attempts Stripe),
and a new unit test
(`test_create_charge_retries_stripe_when_previous_attempt_never_reached_it`)
added alongside the existing replay test to lock the distinction in. This
is exactly the kind of defect self-verification's "live testing before
claiming done" step exists to catch — a purely mocked test suite would have
had no reason to construct a `Payment` row with `stripe_charge_id=None`
unless someone already suspected the bug. See the Database Schema Design
chapter's `payment_db` section for the resulting schema-level reasoning
(why a `NULL` charge ID is a meaningful state, not an incidental one).

**A second, unrelated regression surfaced during the same live pass**: the
`price_cents` migration (Class Diagrams/Database Schema Design chapters)
pushed `TicketRepository.bulk_upsert_available`'s per-row bind-param count
from an already-undercounted 5 to a real 7, overflowing Postgres's
~32,767-bind-param cap at the existing `BIND_PARAM_SAFE_BATCH_SIZE` of 5000.
Caught by an *existing* integration test
(`test_seat_map_larger_than_one_insert_batch_provisions_every_seat`)
flipping from green to red the moment the new column landed — the value of
keeping that Phase 3 regression test in the suite rather than treating it as
one-time-proven-fine.

**Kafka integration point #4 (both outcomes) and the webhook's own
idempotency were verified live against the real running stack**, not just
the mocked/testcontainers suite: `succeeded`/`failed` messages produced
directly on `payment.outcomes` (bypassing Stripe, since the mechanism under
test is Booking Service's consumer) correctly confirmed or released a real
booking; redelivering the same `failed` message produced no second
`payment_outcome_applied` log line; `POST /payments/webhook` with an
invalid signature was rejected with 400 before touching any row.

**The routine `/pre-pr` gate at CHECKPOINT (simplify → code-review →
verify) caught what self-verification's live walkthrough didn't**, since
neither pass is a substitute for the other — self-verification proves the
golden path and the paths a human tester thinks to try; an adversarial
code-review pass looks for what a malicious caller could do that a golden-
path walkthrough would never attempt. The code-review step (Opus, reading
`CLAUDE.md`'s conventions first, per this project's own review-prompt
discipline) found six real issues, most severe first:

1. **`/payments/charge` was reachable from outside the Docker network via
   Traefik**, guarded only by "any valid Keycloak token" — meaning any
   authenticated user, not just Booking Service, could submit an arbitrary
   `booking_id` and `amount_cents`, bypassing the ownership check and
   authoritative price lookup that only exist on Booking Service's side of
   the call. This is exactly the failure mode the amendment in decisions-log
   §9 assumed away ("Payment Service only needs to know the caller presented a
   valid Keycloak token... since Booking Service already verified
   ownership") without anything actually enforcing that only Booking
   Service could reach it. Fixed by narrowing the Traefik router rule — see
   the Class Diagrams chapter's Payment Service section for the resulting
   architectural fact.
2. Webhook handling committed the terminal status *before* publishing to
   Kafka — a publish failure could strand a Payment permanently, since
   Stripe's own retry would hit the already-terminal idempotency guard and
   silently no-op. Fixed by publishing first.
3. That same idempotency guard was read-check-then-write, not
   rowcount-gated like every other idempotent-consumer guard in this
   system (§7) — two overlapping webhook deliveries could both pass it.
   Fixed with a conditional `UPDATE`, proven with a new concurrency
   integration test (two real sessions racing the same delivery).
4. Two concurrent first-time charge attempts for one booking crashed with
   an unhandled `IntegrityError` instead of resolving idempotently. Fixed,
   proven with a concurrent-attempt integration test.
5. Both Kafka consumers in Booking Service shared one `group_id`, coupling
   unrelated topics' rebalances. Fixed with a dedicated group id.
6. A real error from Payment Service and a genuine connection failure both
   read as the same misleading "payment service unreachable" message.
   Fixed by forwarding the real status.

All six fixed and live-verified before the checkpoint closed — see
`build-log.md`'s 2026-08-17 CHECKPOINT entry for the full detail and the
Class Diagrams chapter's Payment Service section for the architectural
correction. This is worth stating plainly for the report's Testing
Strategy chapter: the P3/P8-only dedicated-adversarial-pass rule
(`CLAUDE.md`) doesn't mean every other phase's routine gate is a
formality — this session is the evidence that it can still find a real
security bug.

**What wasn't live-verified this phase, tracked honestly rather than
silently marked done**: a real Stripe charge succeeding and its webhook
actually arriving — `.env` only has the placeholder
`STRIPE_SECRET_KEY=sk_test_changeme`, and no real Stripe test-mode
credentials were available this session (the user was asked and chose to
defer rather than provide one). Everything up to Stripe's own API boundary
(auth, ownership, the synchronous call chain, idempotency at the DB level)
is live-verified; the genuine charge → webhook → confirm round trip is
Tested (mocked/integration) but not yet Verified against the real Stripe
API. Recorded on Phase 4's exit checklist as an open item, not glossed over
— closed by Round 9 of the post-launch hardening arc below.

**Status:** Implemented, Tested, Verified (live, except the real-Stripe
leg above, later closed — see Round 9 below). Final counts, after both
self-verification's live-testing fixes and the CHECKPOINT `/pre-pr`
code-review fixes above: `payment-service`: 7/7 unit, 5/5 integration (+2
concurrency tests from the CHECKPOINT review). `booking-service`: 36/36
unit (+12 from Phase 3's 24), 24/24 integration (+3). `event-service`:
44/44 unit, 11/11 integration (unaffected, spot-checked). `search-service`:
16/16 unit (unaffected — silently ignores the new `price_cents` field on
`event.events` it doesn't need, pydantic's default `extra="ignore"`).

### Phase 6 — a Kafka-transport test finds a three-phase-old bug, then CHECKPOINT catches a fail-open bug

Same review posture as Phase 4: no dedicated adversarial `/code-review`
pass (only P3 and P8 get one, `CLAUDE.md`), self-verification plus the
routine `/pre-pr` gate at CHECKPOINT. Build-then-test throughout — nothing
in this phase's scope is on the test-first list (that's specifically the
dual hold strategies and payment/webhook idempotency); the refund
idempotency mechanism this phase adds deliberately reuses Phase 4's
already-reviewed `create_charge` resubmission-gate pattern rather than
designing a new correctness-critical mechanism from scratch.

**Writing the one genuinely new kind of test this phase needed — a real
Kafka-transport round trip, not a mocked producer or a hand-crafted
payload fed straight to `_handle()` — surfaced a bug that had been sitting
untouched since Phase 3.** Every Kafka consumer test in this codebase,
across every phase before this one, tests `_handle()` directly against a
hand-crafted message, deliberately bypassing the real broker; this phase's
`test_cancel_booking_kafka.py` is the first test anywhere in the project
to actually publish through a real `AIOKafkaProducer` and consume with a
real `AIOKafkaConsumer`. `booking-service`'s `kafka_container` fixture
(`tests/integration/conftest.py`) had existed since Phase 3 with zero
callers — confirmed by `grep` across the whole suite — so nothing had ever
actually exercised it. The first real caller hit an immediate container
exit (code 2). `CLAUDE.md`'s Conventions section had claimed
`KafkaContainer("apache/kafka:3.8.0")` "boots and works directly, no
`.with_kraft()` override needed," explicitly contrasted against
`search-service`'s use of `confluentinc/cp-kafka` plus `.with_kraft()`.
Dumping the container's logs before Ryuk's cleanup ran (`TESTCONTAINERS
_RYUK_DISABLED=true`, then reconstructing `KafkaContainer.start()`'s steps
manually to capture output mid-failure) showed the real cause:
`testcontainers.community.kafka.KafkaContainer`'s boot script shells out to
`/etc/confluent/docker/configure` and `/etc/confluent/docker/bash-config`
in **both** its Zookeeper and KRaft code paths — Confluent-specific
tooling the official `apache/kafka` image never ships, regardless of
`.with_kraft()`. `search-service`'s own `conftest.py` already documented
this exact incompatibility correctly, since Phase 2; `CLAUDE.md`'s claim
was simply wrong and went uncaught for three phases because the fixture it
described was never actually run. Fixed by switching `booking-service`'s
fixture to the same proven combination (`confluentinc/cp-kafka:7.6.0` +
`.with_kraft()`), and corrected `CLAUDE.md` to match — see `build-log.md`'s
2026-08-17 P6.T4 entry for the full trace.

**Live-verified end to end, through the real HTTP/Kafka path, under both
hold strategies**: created real events/seat maps, booked and reached
`CONFIRMED` via the self-signed-webhook technique Phase 4 established
(local dev had only a placeholder Stripe key at this point in the
project, so this was the only way to reach a genuinely `CONFIRMED`
booking without a real account — see Round 9 of the post-launch hardening
arc below for the later real-Stripe verification), then cancelled through
the real `POST /bookings/{id}/cancel` route. Under `cron`: seat released
`BOOKED` → `AVAILABLE`, confirmed by direct query, immediately rebookable;
non-owner 404 (existence hidden, not just 403); repeat-cancel 409;
past-cutoff 409 (moved a real `Event.start_time` into the past and
confirmed the rejection). Under `redis`: identical sequence, with
`Ticket.status` confirmed to stay `AVAILABLE` throughout — the documented
hold-strategy asymmetry, not a bug. The refund half: `payment-service`'s
own logs showed `booking.cancelled` consumed and a genuine `POST
https://api.stripe.com/v1/refunds` reaching Stripe's actual API boundary
(401 on the placeholder key, the same expected failure mode Phase 4's
charge flow hits, not a bypass or a mock); the refund-failure branch
correctly triggered, and the `notifications` topic message was verified
directly with a throwaway `kafka-console-consumer` (correct shape, correct
`booking_id`, Stripe's real error text as `reason`). Redelivery verified
live too: a hand-crafted duplicate `booking.cancelled` message (produced
via `kafka-console-producer`) correctly *retried* the refund rather than
silently no-op'ing, since the first attempt's refund had never actually
succeeded (`stripe_refund_id` stayed `NULL`) — exactly the resubmission-gate
semantics the idempotency design specifies; the true
already-refunded-redelivery no-op case is proven in the automated
integration suite with a mocked Stripe success
(`test_refund_payment_replay_against_real_db_does_not_double_refund`),
since observing it live needs a real Stripe account.

**What wasn't live-verified this phase, same tracked gap as Phase 4's
charge flow**: a real Stripe refund actually succeeding, and by extension
the "already-refunded redelivery is a no-op" claim against a genuinely
`SUCCEEDED` Stripe refund rather than a mocked one. Everything up to
Stripe's own API boundary is live-verified; the genuine refund round trip
is Tested (mocked/integration) but not yet Verified against the real
Stripe API — closed by Round 9 below.

**The routine `/pre-pr` gate at CHECKPOINT found a real fail-open bug
self-verification's live walkthrough had missed** — the same lesson Phase
4's CHECKPOINT review already taught (a golden-path walkthrough proves the
paths a human tester thinks to try; an adversarial pass looks for what it
wouldn't). The code-review step (Opus, CLAUDE.md's conventions read first)
found six issues, most severe first:

1. **The cancellation cutoff failed open, silently, for any booking whose
   event predates the `events` table.** `_check_before_event_start`
   treated a missing `Event` row as "before the cutoff, allow it," with no
   log line — undetectable, and reachable for real (two events in the
   running dev stack predate this phase's migration). This is precisely
   the gap amendment #2 to §22 exists to close, so leaving it open would have
   meant the amendment's own fix wasn't actually enforced — exactly the
   kind of claim the Integrity rule doesn't allow standing unverified.
   Fixed to fail closed (409, logged). Live-verified both directions
   against the real running stack: a `CONFIRMED` booking on a
   pre-migration event now correctly 409s ("cannot verify the event's
   start time"); a fresh booking on an event with a real `events` row
   still cancels normally.
2. A refund-notification publish failure could escape `refund_payment`
   entirely and get misattributed by the consumer's retry wrapper as a DB
   failure — logged under a `db_write_failed` event name, retried the
   whole operation (safe but pointless, since it re-submits to Stripe with
   the same idempotency key), then silently lost the notification anyway
   once the retry budget ran out. Fixed by wrapping the publish in its own
   try/except; renamed the retry-wrapper's log events to reflect that the
   operation they wrap was never DB-only.
3. `payment-service`'s copy of the Kafka-consumer retry helper had its
   safety-net `commit()` removed during the routine simplify pass — correct
   for the one current caller (which self-commits via `PaymentManager`),
   but a landmine for any future handler wired through the same helper
   without its own commit. Reverted.
4. `EventUpsertedMessage.start_time`/`end_time` were bare `datetime`, not
   `AwareDatetime` — harmless while merely logged, but `start_time` is now
   load-bearing for the cutoff comparison, and a naive value would crash
   it. Fixed at the DTO boundary, per the DTO-layer convention.
5. Two residual gaps accepted rather than fixed, the same risk tolerance
   already extended to `create_charge`'s own documented residual race:
   `refund_payment`'s idempotency gate has no rowcount-gate/row-lock
   backstop against a rebalance or a second replica racing two calls for
   one booking (rests on Stripe's own idempotency key, same as
   `create_charge`); that key's protection is also time-boxed to Stripe's
   ~24h expiry window, not indefinite. Both now stated explicitly in
   `refund_payment`'s docstring.
6. `RedisHoldStrategy.release_booking`'s no-op is sound only within one
   strategy's lifetime for a given booking — a booking confirmed under
   `cron` then cancelled after a live switch to `redis` would leave its
   ticket stuck `BOOKED`, permanently unbookable. Not fixed (the real fix
   undoes that strategy's whole design); `HOLD_STRATEGY` switching with
   in-flight bookings outstanding was never a supported operation anywhere
   in this system. Recorded as a decisions-log §26 limitation.

All six fixed (or, for #5/#6, explicitly documented as accepted) and
re-verified before this checkpoint closed. Added test coverage for what
was previously untested: `ProvisioningConsumer` writing the `events` row
and its upsert-on-republish path (both integration, real Postgres); a
unit test locking in the new fail-closed cutoff behavior; a unit test
proving `refund_payment` doesn't raise when the notification publish
itself fails.

**Status:** Implemented, Tested, Verified (live, except the real-Stripe
refund leg above, later closed — see Round 9 below — and except the
`kafka_container` infrastructure fix, which is Verified in the sense that
it now demonstrably works, not merely patched). Final counts, after both
self-verification's live-testing and the CHECKPOINT `/pre-pr` review
above: `booking-service` 72/72 (unit + integration, up from 60 before
this phase); `payment-service` 22/22 (up from 12 before this phase).

### Phase 5 — a two-round CHECKPOINT review, the second round catching the first round's own regression

Same review posture as Phases 4 and 6: no dedicated adversarial
`/code-review` pass (only P3 and P8 get one, `CLAUDE.md`), self-
verification plus the routine `/pre-pr` gate at CHECKPOINT. Build-then-test
throughout — nothing in this phase's scope is on the test-first list.

**A phase-5-kickoff.md didn't exist yet when this phase started**, unlike
every prior phase — Phase 5 runs after Phase 6 in the locked report-first
build order (§27: P8 → P4 → P6 → P5 → P7), so its kickoff doc is generated
fresh rather than already sitting on disk. Three real design gaps had to
be resolved before implementation could start, each surfaced by a genuine
question with no existing answer in the codebase: how does a service with
no database hold retry state (answer: on the Kafka message itself, a
`RetryEnvelope`); what shape does a hand-rolled retry/DLQ ladder take with
no `@RetryableTopic` equivalent in `aiokafka` (answer: three topics,
`notifications` → `notification-retry` → `notification-dlq`, an
exponential-with-cap backoff); and how do you prove a retry ladder works
when the service has no real external dependency capable of a genuine
failure (answer: an explicit, honestly-documented demo instrument,
`simulated_failure_attempts`, mirroring the precedent P8.T5 already set for
the benchmark's simulated immediate-release trigger). All three recorded as
a decisions-log §17 amendment before any code was written, not discovered
mid-implementation and back-filled.

**Redelivery-is-a-safe-no-op tested explicitly, per this project's Kafka
rule, not just claimed by the decisions-log amendment's "idempotent by
construction" reasoning.** `test_redelivery_of_same_message_is_a_safe_no_op`
publishes the same key/value twice to `notifications` and asserts two
independent `notification_delivered` log entries for that booking, via
`structlog.testing.capture_logs()`, with no retry-ladder entry resulting —
proving "handled twice, safely," not merely "nothing crashed." The first
version of this test only asserted the *absence* of a retry-topic message,
which the second-round code review pointed out couldn't actually
distinguish that from the consumer having silently stopped after the first
delivery; strengthened to assert on the log entries directly once caught.
**No precedent existed in this repo for asserting on structlog output from
a test** — `configure_logging()` (which wires `structlog` through stdlib
logging) only ever runs from `main.py`'s `create_app()`, never in the test
process, so pytest's stdlib-logging-based `caplog` fixture would see
nothing from a `structlog` call made directly in a test. Used `structlog
.testing.capture_logs()` instead — `structlog`'s own testing context
manager, works regardless of global configuration — documented inline
since it's a new pattern for the codebase.

**A cross-test Kafka topic leakage bug found and fixed while writing the
integration suite**: the first run of the new tests produced three
failures with mismatched `booking_id`s — a fresh consumer group with
`auto_offset_reset="earliest"` reading a session-scoped Kafka topic picked
up an *earlier* test's leftover message instead of its own, since bare
`consumer.getone()` returns whatever record is next regardless of which
test produced it. Fixed with `_find_matching_record`/
`_assert_no_matching_record` helpers filtering on `record.key ==
booking_id.encode()` — every producer in this system already keys by
booking ID, so this reuses an existing property rather than adding new
per-test isolation machinery (a separate topic per test, or a fresh broker
per test, would both have worked too but at real cost — the shared
session-scoped `kafka_container` fixture, established since Phase 1, is
worth keeping).

**Live-verified against the real running stack, both the ladder's outcomes
and the full end-to-end flow**: a genuine `pay` success through the real
HTTP/Kafka path (self-signed webhook, the same technique used since Phase
4) produced real `notification_delivered` log lines for both
`payment_confirmed` and `booking_confirmed`. Retry-then-recovery: an
isolated one-off `docker compose run` container with
`SIMULATED_FAILURE_ATTEMPTS=1` (the always-on baseline container, which
must always run at `0`, was left untouched) showed
`notification_delivery_failed` (attempt 1) → `notification_retry_scheduled`
(next_attempt 2) → `notification_delivered_after_retry` (attempt 2) after a
real ~4s backoff. Exhaustion-to-DLQ: a second one-off container with
`SIMULATED_FAILURE_ATTEMPTS=5` (comfortably past the default
`retry_max_attempts=3`) showed the ladder climb 1→2→3→4,
`notification_routed_to_dlq` at attempt 4, then `DlqConsumer`'s own
`notification_landed_in_dlq`.

**A two-round CHECKPOINT `/pre-pr` review found and fixed real bugs in
both rounds — including one round finding a bug the previous round's own
fix had introduced**, the same lesson Phase 3's own two-pass review already
taught this project once. Round one (Opus, `CLAUDE.md`'s conventions read
first) found three High-severity issues: `compute_backoff_seconds` could
raise `OverflowError` on a sufficiently large `attempt` (`base ** attempt`
was computed before `min()` capped it — reproduced directly with
`attempt=10**9`); the three consumers' republish calls to
`notification-retry`/`notification-dlq` were unguarded, unlike every other
Kafka consumer with a side effect in this system, so a single transient
broker error would have permanently killed a consumer task; and
`PaymentOutcomeConsumer`'s notification publish, placed inside its retried
DB-transaction closure following the publish-before-commit convention,
could roll back an already-successful booking confirmation on a
persistently-failing publish, since `_run_with_retry`'s give-up-and-move-on
design (unlike a request handler's real redelivery) swallows that failure
instead of raising it — see the Class Diagrams chapter's Booking Service
section for the full mechanism. All three fixed, plus a missing redelivery
test added and a genuinely duplicated bounded-retry loop (`_run_with_retry`
and the first version of the new notification-publish retry) deduped into
one shared helper.

**Round two, re-reviewing specifically to verify round one's fixes rather
than trusting the commit message, found a fourth bug the first round's own
fix had introduced**: tightening `RetryEnvelope.last_error` to a non-blank
string (closing the DTO-validation half of the overflow finding) broke the
three call sites that construct a `RetryEnvelope` internally from
`last_error=str(exc)` — an exception whose `str()` is empty
(`str(KeyError())` is `''`) now raised `ValidationError` right there,
escaping every guard the republish fix had just added and killing the
consumer anyway. Fixed with a small `_error_text(exc)` helper (`str(exc)
or repr(exc)`) at all three sites. The same re-review pass also added the
missing `compute_backoff_seconds` overflow unit test, strengthened the
redelivery test (above), and caught an `infra/docker-compose.yml` comment
that had gone stale mid-session (claiming `/healthz` was reachable "through
the gateway," which this same phase's own live-verification had already
established was false).

**Status:** Implemented, Tested, Verified (live). Final counts after both
review rounds: `notification-service` 11/11 (5 unit, 6 `testcontainers`
integration against a real Kafka broker) — this service's first-ever test
suite, all net-new; `booking-service` 72/72 and `payment-service` 22/22,
unchanged in count from Phase 6 (this phase's changes to both were covered
by existing tests plus updated mocks/assertions, not new test cases).
`pyflakes` clean on every file touched across both review rounds.

## Tier 4 — live traffic through the real stack (Phase 7)

Every prior phase's testing tiers — unit tests against fakes, `testcontainers`
integration tests against real datastores, adversarial `/code-review`
passes on P3/P8 — all operate on this system's *backend* in isolation.
Phase 7 added a genuinely new kind of check: driving real, unmocked HTTP
traffic through the *entire* deployed stack the way an actual browser
user would, rather than calling a Manager method directly or hitting one
service's own test client. This tier ran twice — first with `curl` driving
the wire-level protocol directly, then later with a full rendered-browser
pass — and together found five real, previously-undetected bugs that no
earlier tier could have caught, worth documenting as its own tier rather
than folded into "self-verification," because every finding shares a
specific mechanism: each depended on a code path that was structurally
unreachable by every test written before it.

**Finding 1 — an identity-configuration gap invisible to every prior
phase's tests.** A full Authorization Code + PKCE login was driven with
raw HTTP requests (fetch the real Keycloak login page, submit real
credentials, follow the real redirect, exchange the real authorization
code for a real token) against the `ticketing-frontend` client — the same
sequence the frontend's OIDC library performs in a browser. The resulting
token carried no `aud` claim, and every authenticated call with it 401'd
at every backend service. Root cause: only `ticketing-service` (the
direct-grant client every prior phase's tests and manual `curl` checks
actually used) had the `oidc-audience-mapper` protocol mapper that stamps
`aud: ticketing-services` onto issued tokens; `ticketing-frontend` never
did. This gap existed from Phase 0 but was unreachable by any test before
this one, since nothing before Phase 7 ever drove a token through this
specific client. Fixed (decisions-log §5 amendment); re-verified with a
real login producing a token with the correct claim.

**Finding 2 — a crash on the double-booking-critical path itself, present
since Phase 3.** Reproducing P7.T4's own required two-client seat race
with real concurrent `curl` requests (not `asyncio.gather` inside one test
process) returned two `500`s instead of the expected one-`201`-one-`409`.
Traced through the real logs: a pre-existing data inconsistency (a stale
`PENDING` `Booking` row from earlier Phase 6 testing, still referencing a
ticket whose `status` had reset to `AVAILABLE`) meant both concurrent
callers correctly acquired the hold — `_acquire_hold` has no way to see a
stale booking row — and both reached `_create_booking_row`. The second
correctly hit `IntegrityError` on `uq_bookings_active_ticket` and entered
its own documented "defense-in-depth" compensation path, present in
`booking_manager.py` since Phase 3. That path itself crashed:
`session.rollback()` expires every attribute on the in-memory `ticket`
ORM object, and the very next line's `ticket.id` access triggered an
implicit lazy-reload that isn't safely awaitable there —
`sqlalchemy.exc.MissingGreenlet` instead of the intended clean `409`.

**Why this matters more than an ordinary bug find**: this is the exact
mechanism the project's own `CLAUDE.md` singles out P3 for a *dedicated
adversarial `/code-review` pass* to protect — "the double-booking-critical
path" — and the bug survived that pass, the concurrency test suite, and
every phase since. The reason is structural, not a review oversight: the
existing `test_n_clients_race_one_seat_under_{cron,redis}_strategy_
exactly_one_wins` tests (real Postgres via `testcontainers`, 25 real
concurrent clients) already exercise this exact `IntegrityError` branch
under load, but their helper caught a losing client's exception with a
bare `except Exception: return False` — indistinguishable from a clean
`HTTPException(409)` loss. A losing client silently crashing with
`MissingGreenlet` counted as "lost correctly" for three phases running.
Fixed both the bug (capture `ticket_id = ticket.id` before the
commit/rollback, use the captured local afterward — now a `CLAUDE.md`
convention for any future code touching an ORM attribute post-rollback)
and the test gap that hid it (narrowed the race-helper's exception catch
to `HTTPException` with an explicit `status_code == 409` assertion; added
`test_integrity_race_compensation_does_not_crash`, which reproduces the
exact scenario deterministically rather than relying on the probabilistic
25-client race to happen to hit this specific branch).

**The regression test was verified to actually catch the bug, not just
pass by construction**: reverted only the `booking_manager.py` fix (`git
stash`) and re-ran `test_integrity_race_compensation_does_not_crash` —
failed with the exact `MissingGreenlet` traceback the live incident
produced. Restored the fix, re-ran — passed. Then rebuilt and redeployed
`booking-service` against the real running stack and repeated the
original two-client `curl` race with the fix live: one real `201`, one
clean `409`, no `500`.

**Finding 3 — login never actually completed, only reachable by a real
rendered browser pass.** `/` is both the app's index route and the OIDC
`redirect_uri`, so Keycloak lands there with `?code=&state=` after login.
The index route's `<Navigate to="/search" replace />` fired immediately on
mount and won the race against `AuthProvider`'s own callback-processing
effect, stripping those params via client-side routing before OIDC could
ever read them. The result: every login attempt silently failed after
real credentials and a real Keycloak redirect — no console error, no
exception, just an orphaned, never-consumed PKCE `code_verifier` record
left in `localStorage`. No test before this one exercised this specific
failure mode: the wire-level `curl` testing behind Finding 1 exchanged the
authorization code for a token directly at Keycloak's token endpoint,
never routing that exchange through the SPA's own mounted router the way
a real browser redirect does — so it could never have hit a bug that only
exists in how the router handles the callback URL. Root-caused via
`localStorage`/network-request inspection (the leftover unconsumed record
was the tell);
fixed by gating the redirect on `auth.isLoading`, the same guard
`ProtectedRoute.tsx` already used against a different symptom of the same
underlying `auth.isLoading` state. Live re-verified: both a returning and
a brand-new (self-registered) user logged in cleanly after the fix.

**Findings 4 and 5 — display-only bugs only visible in a rendered UI.**
Seat labels concatenated two independent organizer-typed fields
(`rowName`, `seatLabel`) with no separator, producing confusing strings
like `"11-1"` for row `"1"` seat `"1-1"` — invisible to any test asserting
on structured data rather than rendered text. Separately, two header
containers rendered adjacent links/controls with no CSS gap between them,
which caused a real misclick during the walkthrough itself (a click aimed
at the "Organizer" link landed on "Browse events" instead). Both fixed
and live re-verified.

**Frontend testing itself** (Vitest): the two pieces of genuinely
non-trivial logic — `joinSeatMapWithStatus` (the seat-map/ticket-status
composition, including a case a naive join misses: a layout seat with no
matching ticket yet, from asynchronous post-publish provisioning, renders
`unprovisioned` rather than a false `available`) and `checkoutReducer`
(the hold→pay state machine — a failed hold has no booking to retry
payment against; a failed payment keeps the existing `PENDING` booking so
retry is possible) — both unit-tested (8 tests, including a regression
test for a seat-key collision), plus a small
`formatSeatLabel` display-formatting helper (1 test) extracted during a
later `/pre-pr` pass. Presentational components are otherwise not
unit-tested, matching this project's "minimal functional UI" scope (§10)
— the live end-to-end traffic and the browser walkthrough above are what
actually exercise them.

**Status:** Implemented, Tested, Verified (live) for all five findings
and their fixes. `booking-service`: 77/77 (74 + the race-compensation
regression test above, + 2 later DTO-mapping tests for the new
`list_tickets_for_event` endpoint).
Frontend: 9/9 Vitest tests, `tsc -b`/`oxlint`/`vite build` all clean. This
phase's exit checklist is now fully checked off; see `docs/build-log.md`'s
2026-08-21 entries for the walkthrough's full narrative.

## Post-launch hardening: eleven rounds against the live stack ahead of deployment

Phase 9's own CHECKPOINT closed with every exit-checklist item verified,
but the user asked for further rounds beyond it — "run a bunch of
testing rounds... make the system foolproof" — deliberately ahead of
Phase 10 (AWS deployment) and with the Stripe test-mode key still
unset. Not tied to any single numbered phase task, this became an
eleven-round arc against the actual running `docker compose` stack
(never mocks), each round self-verified live before being counted as
done, per the Integrity rule. The user's standing triage instruction
throughout: fix everything real found, rather than partial-defer for
later. Across all eleven rounds, this surfaced and fixed 15 real bugs —
6 in the first pass alone, then one to two per subsequent round — plus
one infrastructure defect (Kafka never actually persisting data), one
deliberately accepted gap (a Postgres-down 500 left unfixed on
purpose, discussed below), Round 9's real Stripe test-mode charge and
refund round trip closing the last deferred gap, Round 10's
genuine-concurrency and webhook-forgery checks (both clean), and Round
11's real Stripe-minimum-charge finding.

**Round 1 — first adversarial/sanity pass, ~36 findings.** Two parallel
live-testing rounds (adversarial + sanity) plus a five-service
adversarial code review, on top of a full docs staleness sweep. Six
significant correctness bugs, one per service: `booking-service`'s
`list_tickets_for_event` sourced BOOKED/HELD status from the raw
`tickets.status` column, which `RedisHoldStrategy` never writes — every
held/booked seat under `HOLD_STRATEGY=redis` reported as AVAILABLE
(fixed to source BOOKED from `Booking.status` and HELD from the
strategy's own `is_held()`); a `SUCCEEDED` payment outcome racing an
already-EXPIRED booking silently dropped a real charge with no refund
path (now republishes to `booking.cancelled`). `event-service` had no
visibility scoping at all on DRAFT events — any caller could `GET`
another organizer's unpublished event or seat map (fixed with a 404,
not 403, existence-hiding check); separately, `seed.py` wrote raw DB
rows directly, so `make reset`'s demo data never reached Kafka, leaving
`booking_db` and the search index empty after every reset (rewritten to
route through the real create/publish path). `payment-service`: a
webhook reporting SUCCEEDED after an earlier FAILED webhook for the
same charge was silently dropped, since the rowcount-gated transition
only matched a PENDING source state (widened to accept PENDING or
FAILED). `notification-service`: a whitespace-only exception message
crashed the fallback-to-`repr()` truthiness check in its error-text
helper. `search-service`: its Kafka consumer was still on `aiokafka`'s
default auto-commit — the one consumer in the codebase not yet on the
manual-commit-plus-bounded-retry convention every other consumer
follows, meaning a crash mid-write could silently lose an index update
(brought in line).

**Round 2 — a second existence-oracle bug, one verb away from the
first.** The DRAFT-event visibility fix above correctly hid a DRAFT
event from `GET` by a non-owner (404), but the separate method backing
every *mutation* route (`PATCH`/`DELETE`/`/publish`/seat-map `PUT`)
still returned 403 for the same non-owner — reopening the exact
enumeration oracle the read-side fix had just closed, via a different
verb. Fixed to match: 404 while DRAFT, 403 only once PUBLISHED (existence
is already public by then). A related log-level inconsistency one hop
upstream in `booking-service` was also corrected to match its sibling.
Regression suite: 74/25/87/24/15 across the five services, all green.

**Round 3 — security/injection fuzzing (clean), and a real
infrastructure bug.** JWT tampering (bad signature, `alg=none` even
with a valid `kid`, expired token, malformed/empty bearer), ES/Lucene
injection via `/search`, SQLi-shaped event titles, a 100KB title, and
malformed/extra-field JSON bodies all handled correctly — no findings.
A real 5,000-seat seat-map upload exercised the shared `chunked()`
bind-param batching helper at genuine scale (not just unit-tested): all
5,000 `Ticket` rows provisioned correctly. Failure-injection testing
then surfaced a real, previously-undocumented infrastructure defect:
Kafka's data volume was mounted at a path `apache/kafka:3.8.0`'s
KRaft-mode default log directory never actually wrote to, so every
container recreate silently wiped every topic and consumer offset —
invisible because `KAFKA_AUTO_CREATE_TOPICS_ENABLE` let topics
reappear empty rather than erroring. Confirmed via a genuine
crash-redelivery test that replayed 5 stale messages for already-purged
`event_id`s, creating 397 orphaned `Ticket` rows. Fixed with an
explicit `KAFKA_LOG_DIRS` override, live-verified against the actual
named Docker volume; `make reset` was also updated to explicitly clear
all six Kafka topics and restart every consuming service afterward,
closing a related zero-partition-assignment gap found while fixing it.

**Round 4 — the two checks Round 3 deferred, both clean.** With
Kafka now genuinely persisting, the DLQ live exercise (a real
`payment_confirmed` message pushed through 3 failed retries, real
2s/4s/8s backoff observed via structured logs, landing in the DLQ
end-to-end in ~30 seconds) and a Kafka-down degradation check (all five
`/healthz` stayed 200, a DB-only route kept serving real data, all four
consumers logged only internal reconnect-retry noise with no busy-loop,
and all four auto-reconnected cleanly once Kafka came back) both came
back clean. This closed out the arc that began right after the Phase 9
CHECKPOINT.

**Round 5 — Kafka redelivery idempotency proven live, a second
ownership-existence oracle, and both hold strategies under real
concurrency.** `CLAUDE.md`'s own architecture invariant requires every
Kafka consumer's idempotency to be explicitly tested, which prior
phases had only done via testcontainers, never a live duplicate
delivery against the running stack. Verified for all three
DB-writing consumers by resetting each consumer group's offset to
earliest and replaying real messages: `booking-service`'s
`ProvisioningConsumer` (392 tickets unchanged, `tickets_inserted: 0` on
replay), its `PaymentOutcomeConsumer` (a synthetic `succeeded` message
replayed produced no second confirmation or notification), and
`payment-service`'s `BookingCancelledConsumer` (a duplicate cancellation
ID produced two clean no-op warnings, no crash). Separately, the same
ownership-scoping sweep that found the DRAFT-event oracle in Round 2 had
never been run against `booking-service`: `_fetch_owned_booking_in_status`
(backing both `/pay` and `/cancel`) returned 403 for a real-but-not-owned
booking and 404 for a nonexistent one, letting any authenticated user
enumerate real booking IDs. Unlike the event case, a booking has no
publicly visible state at all, so every non-owner access now hides
existence (404) uniformly. Finally, 20 simultaneous `POST /bookings`
requests against the same ticket (real HTTP through Traefik, real
Keycloak tokens) produced exactly one 201 and nineteen 409s under both
`cron` and `redis` hold strategies, and hold-expiry was independently
confirmed live under both (a held ticket correctly reverts to
AVAILABLE and its booking to EXPIRED once the TTL lapses, under a
temporarily shortened TTL/sweep interval for a fast check).

**Round 6 — an unblocked concurrency race, and two more real bugs.**
The double-cancel-on-a-CONFIRMED-booking test, tracked since Phase 9 as
blocked on a real Stripe key, turned out not to be blocked at all — a
synthetic `payment.outcomes` message reaches a genuine CONFIRMED
booking without Stripe, and cancellation itself never calls Stripe
directly. 15 simultaneous cancel requests against the same booking
produced exactly one 200 and fourteen 409s, with exactly one
downstream refund-trigger log line confirming no double-refund path was
reached. `search-service`'s own Kafka consumer was checked the same
live-redelivery way as Round 5's — index count unchanged after a
replay. Two real bugs: Redis being down crashed every route touching
the hold strategy — including the public, unauthenticated seat-map
route — to a bare, unstructured 500, fixed with a single
`@app.exception_handler(RedisError)` (mirroring `event-service`'s
existing `RequestValidationError` pattern) rather than duplicating a
try/except across four call sites, yielding a clean `503`. And a seat
map with an internally duplicated `(section, row, label)` seat silently
produced fewer real tickets than the organizer's own map claimed — the
provisioning consumer's `ON CONFLICT DO NOTHING` correctly absorbed it
as a *redelivery* guard, but nothing rejected it on a genuine *first*
upload. Fixed at the DTO boundary with a second validator on
`SeatMapUpsert`, rejecting the exact offending seat with a 422 before
it ever reaches the database.

**Round 7 — completing the external-dependency-down sweep.**
Postgres, Elasticsearch, and MongoDB were each stopped in turn against
the live stack. Postgres down: `/healthz` correctly 503s on the three
Postgres-backed services and stays 200 on the two that aren't, but a
real business route crashes to a bare 500 — deliberately left unfixed,
since the underlying exception here is a bare `socket.gaierror` (an
`OSError` subclass), and an app-level `except OSError` broad enough to
catch it risks silently reclassifying unrelated errors, a worse
trade-off than the 500 it would fix given a full Postgres outage is
already a full system outage either way. Elasticsearch down: a real
bug, `GET /search` crashed to a bare 500; fixed with the same
exception-handler pattern as the Redis fix, this time on the properly
scoped `elastic_transport.TransportError`, yielding a clean 503.
MongoDB down: a real bug, `/healthz` took 30+ seconds to report
unhealthy because the Mongo client used PyMongo's default 30-second
server-selection timeout; fixed by setting an explicit 5-second
timeout, cutting the same correct 503 down to ~5 seconds. A
malformed-input spot-check (non-UUID IDs, truncated JSON, empty
bodies, an unsigned webhook POST) and an inspection of the synchronous
booking→payment HTTP call's existing timeout/error handling both came
back clean, no changes needed.

**Round 8 — JWT edge cases, live cancellation-cutoff enforcement, and
event→search indexing, all clean.** Five malformed/missing-auth
variants (no header, garbage token, tampered signature, wrong auth
scheme, empty bearer value) against a real endpoint all correctly
401'd; a valid token against a public route 200'd; a non-organizer
token against an organizer-only route correctly 403'd. Token-expiry
rejection was not re-exercised live here — it is already covered by a
forged-and-signed unit test — but the cancellation cutoff
(decisions-log §22 amendment #2) had never been live-tested before this
round: an event created with a near-future `start_time`, its one ticket
held and confirmed via the same synthetic-outcome technique used in
Rounds 5–6, cancelled correctly with a 409 once `start_time` passed,
and correctly succeeded (releasing the ticket) against a control event
30 days out. Both events created during this check also appeared
correctly in `GET /search` within seconds of publishing, confirming
the event→search Kafka integration point still holds after this
session's accumulated changes.

**Round 9 — a real Stripe test-mode charge and refund round trip,
closing the last deferred gap.** The user set up a real Stripe
test-mode secret key and logged in the Stripe CLI, unblocking the one
path every round through Round 8 had to work around: everything
downstream of an actual Stripe API call. `stripe listen --forward-to
localhost:80/payments/webhook`, authenticated directly against the
configured test key (the CLI's own browser-OAuth login was never
separately completed, so `--api-key` was used instead — its signing
secret matched `.env`'s already-configured `STRIPE_WEBHOOK_SECRET`
exactly, confirming that value was set up correctly ahead of time), ran
alongside the stack to forward real webhook deliveries. Booked and paid
for a real seat (`pm_card_visa`, Stripe's always-succeeds test payment
method) through the actual `POST /bookings/{id}/pay` route: a genuine
`PaymentIntent` reached Stripe, the real webhook round-tripped back
through Traefik, and the booking reached `CONFIRMED` — the first time
this exact path ran without the synthetic-outcome substitute Phase 4
established for local dev. Cancelled the booking through the real
`POST /bookings/{id}/cancel` route: a genuine Stripe `Refund` was
issued, and `payment_db` confirmed `status = REFUNDED` with both
`stripe_charge_id` and `stripe_refund_id` populated. Finally,
published a synthetic redelivery of the same `booking.cancelled`
message directly to Kafka to exercise the one branch no prior round
could reach: `refund_payment`'s `stripe_refund_id is not None`
replay-no-op guard fired correctly — a clean `refund_replay_no_op` log
line, no second Stripe API call attempted, confirming the idempotency
gate holds against a real, already-populated refund ID and not just a
simulated one. No bugs found; the last deliberately-open item from
Rounds 1-8 is now closed.

**Round 10 — genuine concurrent-request angles the sequential replays in
Rounds 1-9 couldn't reach, all clean.** Every prior round's "concurrent"
tests either raced HTTP requests against a hold/cancel decision (Rounds
5-6) or replayed Kafka messages one at a time; none had fired truly
simultaneous requests at the one synchronous, external-API-calling path
in the system — Payment Service's Stripe charge submission — nor probed
the webhook endpoint as an attacker rather than as Stripe itself. Four
checks, real HTTP through Traefik, real Keycloak tokens, the real
(now-configured) Stripe test key: a forged `Stripe-Signature` header and
a request with the header omitted entirely both 400'd cleanly at
`stripe.Webhook.construct_event`, no crash, no `payment_db` row written
for either — payment-service's webhook endpoint correctly does not trust
its own caller by default, only a signature Stripe itself could have
produced. Five genuinely simultaneous `POST /bookings/{id}/pay` calls
against the same PENDING booking (real concurrent HTTP, not sequential)
exercised `_resolve_payment_row`'s `IntegrityError` race-loser path under
real load: all five reached `_submit_to_stripe` with the same
booking-ID-derived idempotency key (the local unique-index guard only
dedupes the `Payment` row, not the outbound Stripe call itself, since the
losers' re-queried row still had a null `stripe_charge_id` at the moment
they checked it), producing four real `409 Conflict` responses from
Stripe's own idempotency-key locking followed by a retry-and-converge on
all five — confirmed live, for the first time, that Stripe's key-level
locking is what actually closes this race, not application code alone.
`payment_db` ended with exactly one `Payment` row and one real
`stripe_charge_id` shared by all five responses; no double charge.
Separately, a booking whose hold was allowed to expire (real TTL sweep,
temporarily shortened to 15s/5s via a `docker-compose.yml` override
reverted immediately after the check, the same technique Round 5 used)
correctly 409'd on a `/pay` attempt against the now-EXPIRED booking, with
the ticket already back to AVAILABLE and no stray `Payment` row created;
and a `/cancel` attempt against a booking still PENDING (charged but not
yet webhook-confirmed) correctly 409'd rather than being treated as
already-CONFIRMED. No bugs found.

**Round 11 — a real bug: nothing stopped a ticket priced below Stripe's
own minimum charge.** Created a real event as an organizer (`bob`) with a
seat map section priced at `price_cents=1`; Event Service's DTO layer
accepted it (its only constraint at the time was `gt=0`), the ticket
provisioned normally, and a real booking against it reached
`POST /bookings/{id}/pay`. The charge attempt failed at Stripe with
`error_code=amount_too_small`, `"Amount must be at least $0.50 USD"` —
correctly caught as a `StripeError` and turned into a clean `502`, not a
crash, but the message reaching the caller (`"payment service rejected
the charge attempt"`) gives no indication the actual cause is a
mispriced ticket rather than a genuine gateway problem, and the booking
would sit `PENDING`, permanently unpayable, until its hold naturally
expired. This is exactly the class of gap `CLAUDE.md`'s DTO-boundary
convention exists to prevent — a logical constraint enforced several
calls downstream instead of at the DTO that first accepts the value.
Fixed by tightening `SeatMapSection.price_cents` from `Field(gt=0, ...)`
to `Field(ge=STRIPE_MIN_CHARGE_CENTS_USD, ...)` (`STRIPE_MIN_CHARGE_CENTS_USD
= 50`, `event-service/app/api/schemas.py`) — the same boundary a
pricing section already crosses via Kafka into Booking Service's
`Ticket.price_cents`, now rejected at creation with a clean `422` instead
of at charge time with an opaque `502`. Live-reverified after rebuilding
and restarting `event-service`: a 1-cent seat map upload now `422`s with
`"Input should be greater than or equal to 50"`, and a 50-cent upload
still succeeds. Two new unit tests added at the boundary (49 rejected, 50
accepted); `event-service`'s suite: 65 → 67, still green.

The five-service unit suite was re-run after each round that changed
code (Rounds 1, 2, 5, 6, 7, 11 — Rounds 3, 4, 8, 9, 10 either made no
application code changes or, for Round 3, changed infrastructure config
only), green throughout with zero regressions introduced by any fix,
ending at 67/23/52/14/9 after Round 11's fix. `_shared/auth`'s own suite
(31/31) was confirmed separately, alongside adjacent comment-cleanup
work in this same session, not as part of this eleven-round arc itself.
What remained open after Round 8 — the Stripe test-mode key setup, and
the one Payment Service idempotency branch blocked on it — was closed by
Round 9 above; Round 10 closed the remaining untested angle around real
concurrency and webhook trust; Round 11 closed a genuine validation gap
only a real Stripe charge attempt could have surfaced, since no
testcontainer or mock Stripe client enforces its actual minimum-charge
business rule. Nothing further is deliberately deferred.
