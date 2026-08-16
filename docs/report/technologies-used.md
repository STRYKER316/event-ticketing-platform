# Technologies Used

*Status: draft, running list — appended each phase per the DOCUMENT step.
"Real-world framing" polish pass happens at P11.T2; until then this is
accurate but unpolished. Entries below cover what Phases 0-3 actually
introduced and verified running.*

Each entry: what it is, why it was chosen over the alternatives considered,
and its status in this build.

## Traefik (gateway)

**What:** a reverse proxy / API gateway that discovers backend services
automatically by reading Docker labels on running containers, rather than
requiring a static routing config file to be hand-maintained as services are
added or removed.

**Why:** chosen over Kong, Nginx, and AWS API Gateway specifically for that
auto-discovery property — adding a new backend service means adding labels
to its container definition, not editing a separate gateway config in
lockstep. It behaves identically in local Docker Compose and in the
eventual AWS deployment, which Kong (needing an extra datastore) and AWS API
Gateway (no meaningful local equivalent) do not.

**Status:** Implemented, Tested. Verified live: routes to `event-service`
and, since Phase 2, `search-service` purely from Docker labels, no manual
wiring; dashboard reachable; every route confirmed reachable only through
Traefik, not by hitting a service directly on its container port.

**Real-world caveat surfaced during the build:** on this development
machine's Docker Desktop build, Traefik's embedded Docker client hardcodes
an outdated initial API-version probe that the daemon rejected outright,
breaking service discovery entirely until a small proxy was added in front
of the Docker socket to normalize the request. Worth a mention as a concrete
example of environment-specific integration friction that doesn't show up
until you actually run the thing — see `docs/build-log.md`, P0.T5 entry, for
the full diagnosis.

## Keycloak (identity provider)

**What:** a self-hosted, open-source identity and access management server
implementing OpenID Connect. Runs here in development mode with its
embedded database — appropriate for a capstone demo, explicitly not
production-hardened (worth stating plainly in the report rather than
implying otherwise).

**Why:** rather than hand-rolling authentication or offloading it to a
managed third-party service, Keycloak lets the project demonstrate a real,
standards-based auth flow: password-grant token issuance, RS256-signed
JWTs, realm-scoped roles, and a JWKS endpoint services use to verify
signatures without ever seeing user passwords.

**Status:** Implemented, Tested. A realm (`ticketing`) with `user` and
`organizer` roles, a public PKCE-only frontend client, and a confidential
password-grant client is imported automatically on container start. Verified
live: password grant returns a token whose decoded claims carry the
expected realm roles.

## Shared JWT-validation dependency (`shared_auth`)

**What:** a small internal Python package, built once and imported by every
backend service, that fetches and caches Keycloak's public signing keys,
validates a bearer token's signature/issuer/audience/expiry, and exposes two
FastAPI dependencies — `get_current_user()` and a `require_role(...)`
factory — as the single way any service authenticates or authorizes a
request.

**Why:** with five independently-deployed backend services all needing to
validate the same tokens against the same identity provider, implementing
that logic five times would mean five chances for the validation logic to
drift out of sync — a real, tested security-boundary bug class. Building it
once as a shared dependency makes correctness (or a fix to it) apply
uniformly across every service that imports it.

**Status:** Implemented, Tested. 28 pytest unit tests: the original 10
(valid token, expired token, tampered token, wrong audience, wrong issuer,
unknown signing key, missing required role, present required role,
JWKS-URI override behavior) against a mocked JWKS endpoint with a real
generated RSA keypair, plus 18 added during a retroactive review-gate pass
(2026-08-15, see `docs/build-log.md`) that hardened the package past its
Phase 0 walking-skeleton state: a genuine algorithm-confusion attack (a
hand-constructed forged HS256-signed token using the RSA public key as the
HMAC secret — PyJWT's own `encode()` refuses to build this via its normal
API, so the forged JWS is built by hand, the way a real attacker would),
missing-required-claim rejection, malformed-claims fail-closed behavior,
and a full suite against the real `JWKSCache` (TTL expiry, unknown-kid
refresh, key rotation, fetch-failure handling) via `httpx.MockTransport` —
the original suite fully stubbed out `get_key()`, so this logic had zero
real coverage until then. The same pass added: an `asyncio.Lock` around
JWKS refresh (was vulnerable to a thundering-herd re-fetch and a racy
double-client-creation window under concurrent requests), a minimum-
refetch-interval throttle (an unauthenticated client sending garbage `kid`s
could previously drive unbounded 1:1 request-to-Keycloak-fetch traffic),
and a proper error boundary around JWKS fetch/parse failures (an IdP
outage or malformed JWKS response previously crashed with an uncaught
500 instead of a clean 503). No live Keycloak required to run the suite —
also verified live, end-to-end,
against a running Keycloak instance.

## FastAPI + `prometheus-fastapi-instrumentator` + `structlog`

**What:** the async Python web framework instantiated services are built
on, paired with automatic Prometheus metrics instrumentation and structured
(JSON) application logging.

**Why:** async I/O throughout was a deciding architectural factor for this
project (over a synchronous framework), so the web framework, database
driver, and logging all had to support it natively rather than blocking the
event loop. JSON logging over plain text specifically so log lines are
machine-parseable — a real requirement once there is more than one service
producing logs to correlate.

**Status:** Implemented, Tested. Verified that both application-level log
calls and the framework's own request-access logging render as JSON —
initially only the former did, until the logging configuration was extended
to route stdlib/uvicorn logging through the same formatter (see build-log,
P0.T5). `/metrics` confirmed scraping through the gateway.

## PostgreSQL, database-per-service credential isolation

**What:** one Postgres container hosting three logically separate
databases (`event_db`, `booking_db`, `payment_db`), each with its own
dedicated user and password, initialized from environment-supplied
credentials rather than hardcoded values.

**Why:** database-per-service isolation is a locked architectural
invariant of this project — no service is permitted to query another
service's tables directly. Per-database credentials (rather than one shared
superuser) makes that boundary enforceable at the database layer, not just
by convention.

**Status:** Implemented, Tested. Verified via direct `psql` inspection that
all three databases exist with correct per-database ownership.

## Apache Kafka (KRaft mode)

**What:** the event-streaming broker used for the project's five
cross-service integration points, running in KRaft mode (no separate
Zookeeper process) as a single broker for local development.

**Why:** the architecture's cross-service communication is Kafka-only by
design — no service calls another service synchronously, and no service
shares another's database. KRaft mode over Zookeeper-based Kafka
specifically to keep the local resource footprint down (one fewer
long-running process) without giving up anything this project actually
needs from Kafka.

**Status:** Implemented, Tested (Phase 2 — first real integration point,
event↔search, §7.1; Phase 3 adds integration point #2, event→booking
provisioning — same producer, a second independent consumer group
(`booking-service`) reading the same topic, proving the one-producer/
many-independent-consumer-groups shape scales past the first pair without
any change to the producer side). Event Service's `aiokafka` producer publishes a keyed
message (event ID as the partition/idempotency key) on the event's `publish`
action and on any update while `PUBLISHED`; Search Service's consumer
processes it into Elasticsearch. (A `PUBLISHED` event can no longer be
deleted at all as of Phase 3 — `EventProducer.publish_deleted` was removed
as dead code once that path became unreachable — so this producer only
ever emits `upserted`, never `deleted`, going forward; see the Booking
Service class-diagram note.) Idempotency verified two ways: unit tests
against a mocked repository, and live against the real stack by hand-
replaying an identical Kafka message via `kafka-console-producer` and
confirming the document count never grows past one (an upsert-by-ID
overwrites; `_version` increments, no duplicate). A redelivered delete
against an already-deleted document is caught and logged as a no-op rather
than raising. `testcontainers`' `KafkaContainer` (Confluent image, KRaft
mode) backs the integration suite — a different image than the compose
stack's `apache/kafka`, since that container helper's bootstrap scripts are
Confluent-specific; noted as a test-infrastructure detail, not a production
concern.

**Phase 3 addendum:** Booking Service's own integration suite passes
`KafkaContainer("apache/kafka:3.8.0")` directly — the same image the
compose stack actually runs, not the Confluent substitute above — and it
boots and works without needing `.with_kraft()` or any other override,
since the `apache/kafka` image already runs KRaft mode by default. Worth
noting as a small, real discrepancy between the two services' test
infrastructure rather than glossing over it: Search Service's Confluent
workaround may no longer be strictly necessary, but re-verifying that and
switching it over is out of scope for this phase and not revisited here.

**Phase 3 review finding: offset-commit semantics matter for idempotency,
not just the write itself.** `ProvisioningConsumer` originally left
`aiokafka`'s `enable_auto_commit` at its default `True`, which commits
offsets on a background timer independent of whether the DB write under
it actually finished — a crash between that timer firing and the write
committing would silently drop tickets rather than trigger the
redelivery the idempotent `ON CONFLICT DO NOTHING` design depends on.
Fixed with `enable_auto_commit=False` and an explicit `commit()` after
each record is fully handled, plus a bounded in-process retry (3
attempts, 1s backoff) so a merely transient DB error doesn't cost that
message's tickets on the very first hiccup. The "idempotent consumer"
claim (decisions-log §7) is about more than the write being safe to
redeliver — it also requires the redelivery to actually happen when it's
needed, which is an offset-commit-timing property, not a write-shape one.

## Elasticsearch (search index)

**What:** a document search engine, populated exclusively via the Kafka
consumer above — never queried back into Event Service, and never treated
as a source of truth (§8).

**Why:** free-text search across title/description/venue/performers with
relevance ranking is the kind of query a relational database handles
poorly compared to a purpose-built search index; Elasticsearch was already
the architecturally locked choice (decisions-log §8) specifically for this
role. Populated asynchronously via Kafka rather than synchronously from
Event Service writes, so a slow or unavailable search index can never block
an organizer's write path — the same reasoning behind every other
Kafka-mediated integration point in this system.

**Status:** Implemented, Tested. Single-node local topology
(`discovery.type=single-node`, per §12/§24); the index is created with
`number_of_replicas: 0` since a replica could never be assigned to a
second node that doesn't exist in this topology, and service startup
blocks on `cluster.health(wait_for_status="yellow")` so nothing reports
healthy before its shards are actually assigned — a real fix, not
precautionary, after a Docker Desktop disk-space incident during test
development surfaced that a fresh index can otherwise sit unready far
longer than a caller might assume (see `docs/build-log.md`, P2.T3 entry).
`GET /search` (free-text, paginated, sortable by relevance or `start_time`,
every sort carrying an explicit tiebreaker to keep pagination stable)
verified live end-to-end through Traefik against real published events.

## `uv` (Python tooling) and workspace structure

**What:** a Python package/dependency manager and project runner, used here
to set up a **workspace**: one shared virtual environment and lockfile
across every backend Python package in `/services`, rather than each
service (and the shared auth package) maintaining its own isolated
environment.

**Why:** with multiple services sharing a dependency (`shared_auth`) and
needing consistent dependency versions, per-package environments would mean
duplicated installs and a real risk of the same dependency resolving to
different versions in different services. A workspace gives one dependency
resolution across everything while each service still declares its own
`pyproject.toml`.

**Status:** Implemented. Confirmed both `uv run pytest` from within a
member package and `uv run --package <name> pytest` from the workspace root
resolve against the same shared environment.

## MongoDB (Motor, async driver)

**What:** a document database, used by Event Service exclusively for
seat-map storage — sections/rows/seats per event — accessed via Motor,
the async MongoDB driver.

**Why:** seat maps are read and written as a single nested document per
event, never queried at sub-document granularity by this service, which
is a better fit for a document store than forcing a normalized
rows-and-seats relational schema onto data that's always accessed whole.
Motor specifically (over the sync PyMongo driver) to stay consistent with
the project's async-throughout requirement (§3) — no blocking I/O in a
request path, including database calls to the document store.

**Status:** Implemented, Tested. Verified live: store/fetch/delete
round-trip against a real `mongo:7` container; `/healthz` confirms
connectivity via `db.command("ping")` alongside the existing Postgres
check.

## Alembic (schema migrations)

**What:** SQLAlchemy's migration tool, generating versioned, revertible
schema changes from the ORM models rather than hand-written DDL.

**Why:** with schema changes expected across every remaining phase
(Booking, Payment each add their own tables) and grading requiring a
demonstrable, reproducible schema history rather than a snapshot,
autogenerated + reviewed migrations give both a paper trail and a repeatable
`alembic upgrade head` that works identically in local dev, CI-equivalent
test runs, and eventual AWS deployment.

**Status:** Implemented, Tested. First migration applies clean against a
real Postgres instance; downgrade-then-upgrade cycle verified — surfaced
and fixed a real gap in the autogenerated `downgrade()` (a Postgres
`ENUM` type isn't dropped automatically when its owning table is), see
`docs/build-log.md`, P1.T2 entry.

## `testcontainers-python`

**What:** a library that boots real, disposable Docker containers (here:
Postgres, MongoDB) for the duration of a test session, rather than mocking
the database layer or relying on a shared long-lived test database.

**Why:** for a project whose core correctness claims are about real
database behavior — unique constraints, transaction boundaries, and later
the dual-hold concurrency race (Phase 3) — a mocked database can't
actually prove those claims. `testcontainers-python` gives integration
tests the real thing, disposably, without a hand-maintained shared test
environment to keep in sync.

**Why not go through the running `docker compose` stack instead?** Test
isolation — containers boot fresh per test *session*, migrate cleanly, and
tear down automatically, so the suite is runnable from a clean checkout
(including CI, later) without a developer having remembered to
`make up` first, and without test data leaking between runs.

**Status:** Implemented, Tested. First use in this repo, Phase 1: session-
scoped `PostgresContainer`/`MongoDbContainer` fixtures, one Alembic
migration run per session, table truncation between individual tests.
Established as the pattern every later phase's integration suite reuses.
Phase 3 extends the pattern to a third and fourth container type
(`community.redis.RedisContainer`, `community.kafka.KafkaContainer`) in
the same suite — Booking Service's correctness claims span three real
datastores at once (Postgres, Redis, and the Kafka broker the
provisioning consumer reads from), so the integration tier needed all
three running simultaneously, not sequentially.

## Redis (`redis.asyncio`)

**What:** an in-memory key-value store, used here exclusively as the
backing store for one of Booking Service's two `TicketHoldStrategy`
implementations — a distributed lock via `SET key value NX EX seconds`,
Redis's atomic acquire-or-fail-with-auto-expiry primitive.

**Why:** chosen for this specific role because `SET ... NX EX` gives
exactly-one-winner concurrency semantics and self-expiry in a single
atomic operation, with no sweep needed to reclaim the *lock* itself —
a genuinely different mechanism from the cron strategy's
periodic-sweep approach (§6), which is the entire point of building both:
the Phase 8 benchmark measures which trade-off performs better under
real concurrent load, and that comparison is only meaningful if the two
mechanisms are actually different, not two names for the same idea.
`redis.asyncio` specifically (over the sync `redis-py` client) for the
same async-throughout reason as every other I/O dependency in this
project (§3).

**A gap the "no sweep needed" framing hid, found during this phase's
review:** the *lock* self-expires, but the `Booking` row `BookingManager`
creates alongside it lives in Postgres, which Redis knows nothing about.
An abandoned checkout used to leave that row `pending` forever, and a
partial unique index (see the Database Schema Design chapter) then
permanently blocked the seat — so this strategy does need a sweep after
all, just for a Postgres row instead of the Redis key, run on its own
APScheduler job (see the APScheduler entry below).

**Why not use Redis for the cron strategy's hold state too, for
consistency?** Considered and deliberately rejected — the cron
strategy's whole reason for existing in this comparison is that it
stores hold state in the same Postgres database the rest of Booking
Service already writes to, using an atomic conditional `UPDATE` rather
than a second datastore's primitive. Making both strategies use Redis
would collapse the comparison into "the same lock, implemented twice,"
not two architecturally different approaches worth benchmarking against
each other.

**Status:** Implemented, Tested, Verified (live). `RedisHoldStrategy`'s
`acquire_hold`/`release_hold`/`is_held` proven against the shared
`TicketHoldStrategy` contract test and a dedicated race test (25
concurrent clients, exactly one winner) via a real `testcontainers`
Redis instance; the auto-release-on-TTL claim specifically verified by
asserting the Redis key is simply gone after its TTL elapses, not via
any scheduler run — there is no scheduler on this path, which is the
mechanism being proven. Verified live against the running compose
stack's `redis` container too: booked a real seat with `HOLD_STRATEGY=redis`
active, confirmed a second booking attempt on the same seat cleanly
returned 409, and inspected the live Redis key and its TTL directly
(`redis-cli GET`/`TTL`) alongside confirming `tickets.status` correctly
stays `AVAILABLE` in Postgres throughout — the documented trade-off (see
Class Diagrams chapter) observed directly, not just asserted in a
docstring.

## APScheduler

**What:** an in-process Python job scheduler, used here to run Booking
Service's periodic sweep — an `AsyncIOScheduler` interval job that runs
whichever cleanup the active `HOLD_STRATEGY` needs.

**Why:** chosen over a system-level cron job or a separate scheduling
service specifically because the sweep needs to run inside the same
async application (same event loop, same database session factory) as
the rest of Booking Service, with no separate process, deployment
artifact, or inter-process coordination to stand up for what is, in this
project's scope, a single periodic in-process task. Wired into the same
FastAPI `lifespan` context manager that starts/stops the Kafka consumer.

**Both hold strategies need this scheduler, not just cron — a correction
made during this phase's review.** Originally only started when
`HOLD_STRATEGY=cron`, on the reasoning that the Redis strategy's lock
expires on its own. True for the lock, not for the `Booking` row
alongside it (see the Redis entry above) — `hold_sweep.py` now always
starts the scheduler and picks which job to register based on the active
strategy: `_sweep_cron_holds_once` (releases expired `Ticket` holds and
their `Booking` rows together) under `cron`, or
`_sweep_stale_redis_bookings_once` (age-based `Booking`-row expiry only)
under `redis`. An unrecognized `HOLD_STRATEGY` value fails scheduler
construction the same way `get_hold_strategy()` already failed on the
first request that needed it, rather than only failing one of the two
places.

**Status:** Implemented, Tested, Verified (live, both strategies). The
cron sweep's release logic (`CronHoldStrategy.release_expired()`) is
tested directly against a real Postgres instance — an already-expired
hold is released and its associated `PENDING` booking transitioned to
`EXPIRED`, while an unexpired hold is correctly left untouched — and the
Redis sweep's logic (`BookingRepository.expire_stale_pending()`) is
tested the same way, independent of whether APScheduler's own timer
fires during the test, since the scheduler is only the trigger, not the
logic being proven. Verified live under both values: booted with
`HOLD_STRATEGY=cron`, confirmed `"Scheduler started"` and `"Added job
\"_sweep_cron_holds_once\""` in the structured logs alongside the Kafka
consumer's own startup sequence; switched to `HOLD_STRATEGY=redis` with a
short TTL/sweep interval, confirmed `"Added job
\"_sweep_stale_redis_bookings_once\""` on boot, then confirmed the job
actually fires and clears an abandoned booking (`hold_sweep_expired_
stale_redis_bookings`, `count: 1`) — see the Testing Strategy chapter for
the full live sequence.
