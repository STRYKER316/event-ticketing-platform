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
isolation. Kafka-integrated phases (2 onward) will need a
`KafkaContainer` fixture added to this same pattern, plus the redelivery-
is-a-no-op test the architecture invariants require for every consumer —
not yet exercised since Event Service has no consumer.

**Status:** Implemented, Tested. 7/7 tests green (5 unit, 2 integration) as
of Phase 1: `cd services/event-service && uv run --package event-service
pytest`.
