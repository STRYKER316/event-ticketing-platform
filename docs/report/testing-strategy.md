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

**Status:** Implemented, Tested. 17/17 `event-service` tests green (13 unit,
4 integration), 17/17 `search-service` tests green (15 unit, 2 integration)
as of Phase 2: `cd services/<service> && uv run pytest`.
