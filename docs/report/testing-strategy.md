# Testing Strategy

*Status: draft, partial — establishes the pattern from Phase 1's first
`testcontainers-python` suite (`shared_auth`'s mocked-JWKS unit tests were
Phase 0's contribution; this chapter grows with every phase's test suite,
per the DOCUMENT step in `CLAUDE.md`).*

## Two tiers, deliberately different scopes

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

## A concrete bug this caught

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

## Established pattern for Phases 2+

Session-scoped container fixtures (one `PostgresContainer`/
`MongoDbContainer` boot per test session, not per test — container startup
is the expensive part), a single migration run per session, and
table-truncation (not container restart) between individual tests for
isolation.

## Phase 2: testing an idempotent Kafka consumer, for real

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
  asynchronous-indexing trade-off decisions-log §7/§26 calls out as an
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

## A real infrastructure bug this suite caught (not a code bug)

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

## A third tier: adversarial testing against the live stack

The two tiers above test what the code was *written* to do. Neither
`testcontainers` unit/integration suite is adversarial by construction — the
inputs are the developer's own idea of what a caller sends. Before Phase 3
(Booking Service) started building on this surface, a separate pass tested
what happens when a caller doesn't cooperate: malformed input, forged auth,
races, and infrastructure outages, run live against the running
`docker compose` stack (real Keycloak tokens for three seeded users, real
Postgres/MongoDB/Elasticsearch/Kafka — no mocks) rather than through pytest.

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

## The review pass finding a bug in its own fix

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

**Status:** Implemented, Tested, Verified (live, against the running stack —
not just the pytest suite). 54/54 `event-service` tests green (43 unit, 11
integration), 18/18 `search-service` tests green, as of the pre-Phase-3
checkpoint: `cd services/<service> && uv run pytest`.

## Phase 3: test-first for the one correctness claim the whole report leans on

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

## Phase 3: two review passes, the second catching real bugs in the first pass's fixes

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
comparison this phase exists to set up.

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
`event-service`'s suite at 54/54 (55 prior, net -1 after this phase's
`EventDeletedMessage`/`publish_deleted` dead-code removal — event-service now
refuses to delete a `PUBLISHED` event outright, per the §15 Phase 3
amendment, so the message and its producer method were never reachable).
`cd services/booking-service && uv run pytest`.
