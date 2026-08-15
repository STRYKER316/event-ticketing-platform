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
