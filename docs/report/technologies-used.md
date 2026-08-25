# Technologies Used

*Status: draft, running list — appended each phase per the DOCUMENT step.
Condensed 2026-08-25 (report trim, phase 2): each entry's narration was
cut to its essential what/why/verified-status claim, dropping blow-by-blow
bug narratives already covered in Class Diagrams, Database Schema Design,
or `docs/build-log.md` — no fact, number, or verification claim was
removed, only the duplicate retelling of it. AWS/Elastic Beanstalk is
covered by the Deployment Flow chapter rather than repeated here.*

Each entry: what it is, why it was chosen over the alternatives considered,
and its status in this build.

## Traefik (gateway)

**What:** a reverse proxy / API gateway that discovers backend services
automatically by reading Docker labels on running containers, rather than
requiring a static routing config file to be hand-maintained.

**Why:** chosen over Kong, Nginx, and AWS API Gateway specifically for
that auto-discovery property, and because it behaves identically in local
Docker Compose and in the AWS deployment.

**Status:** Implemented, Tested, Verified (live) — routes to all five
backend services and the frontend purely from Docker labels, dashboard
reachable, every route confirmed reachable only through Traefik, not by
hitting a service directly on its container port. (A one-time Docker
Desktop API-version incompatibility during setup was fixed with a small
proxy in front of the Docker socket — see `docs/build-log.md`, P0.T5.)

## Keycloak (identity provider)

**What:** a self-hosted, open-source identity and access management server
implementing OpenID Connect. Runs here in development mode with its
embedded database — not production-hardened, stated plainly rather than
implied otherwise.

**Why:** rather than hand-rolling authentication or offloading it to a
managed third-party service, Keycloak demonstrates a real, standards-based
auth flow: password-grant token issuance, RS256-signed JWTs, realm-scoped
roles, and a JWKS endpoint services use to verify signatures without ever
seeing user passwords.

**Status:** Implemented, Tested, Verified (live) — a realm (`ticketing`)
with `user`/`organizer` roles and both a PKCE-only public client and a
confidential password-grant client import automatically on container
start; password grant returns a token with the expected realm roles.

## Shared JWT-validation dependency (`shared_auth`)

**What:** a small internal Python package, built once and imported by
every backend service, that fetches and caches Keycloak's public signing
keys, validates a bearer token's signature/issuer/audience/expiry, and
exposes `get_current_user()` and `require_role(...)` as the single way any
service authenticates or authorizes a request.

**Why:** with five independently-deployed services all needing to
validate the same tokens, implementing that logic five times would mean
five chances for it to drift — a real security-boundary bug class.
Building it once makes correctness apply uniformly.

**Status:** Implemented, Tested. 28 pytest unit tests against a mocked
JWKS endpoint with a real generated RSA keypair, including a hand-built
algorithm-confusion attack (RS256→HS256 key-confusion), missing-claim and
malformed-claim rejection, and JWKS cache behavior (TTL expiry, key
rotation, fetch-failure handling) via `httpx.MockTransport`. Hardened past
its Phase 0 walking-skeleton state with an `asyncio.Lock` around JWKS
refresh, a minimum-refetch throttle, and a proper error boundary around
JWKS fetch failures (previously an uncaught 500). Also verified live,
end-to-end, against a running Keycloak instance.

## FastAPI + `prometheus-fastapi-instrumentator` + `structlog`

**What:** the async Python web framework every service is built on,
paired with automatic Prometheus metrics and structured (JSON)
application logging.

**Why:** async I/O throughout was a deciding architectural factor (§3),
so the framework, DB driver, and logging all had to support it natively.
JSON logging specifically so log lines are machine-parseable across five
services.

**Status:** Implemented, Tested, Verified. Both application-level log
calls and the framework's own request-access logging render as JSON.
`/metrics` is reachable on all five services (directly on each service's
container port, and through the gateway only for `event-service`, which
holds Traefik's catch-all router — Prometheus's own scrape config already
targets each service directly on the Docker network, so this was never a
real gap, only a stale claim in an earlier draft of this section,
corrected P9.T2). A log-level discipline audit (P9.T2, 67 call sites
across all five services) found and fixed one inconsistency
(`payment-service`'s charge-path Stripe-error log level, aligned to match
its already-correct refund-path sibling).

## PostgreSQL, database-per-service credential isolation

**What:** one Postgres container hosting three logically separate
databases (`event_db`, `booking_db`, `payment_db`), each with its own
dedicated user and password.

**Why:** database-per-service isolation is a locked architectural
invariant (§8) — per-database credentials make that boundary enforceable
at the database layer, not just by convention.

**Status:** Implemented, Tested, Verified — all three databases exist with
correct per-database ownership, confirmed via direct `psql` inspection.

## Apache Kafka (KRaft mode)

**What:** the event-streaming broker behind the project's five
cross-service integration points, running in KRaft mode (no Zookeeper) as
a single local broker.

**Why:** cross-service communication is Kafka-only by design (with one
narrow exception, §9). KRaft mode keeps the local resource footprint down
by one fewer long-running process.

**Status:** Implemented, Tested, Verified (live). Idempotency verified two
ways for every integration point: unit tests against mocked repositories,
and live redelivery against the real stack showing no duplicate effect.
Offset-commit semantics matter as much as the write itself — every
DB-writing consumer disables `aiokafka`'s default auto-commit and commits
only after its write fully succeeds, with a bounded retry so a merely
transient DB error doesn't cost a message on the first hiccup (found and
fixed in Phase 3 review; see Class Diagrams). Integration tests use
`confluentinc/cp-kafka:7.6.0` with `.with_kraft()` — the plain
`apache/kafka` image's bootstrap scripts are Confluent-specific and won't
boot under `testcontainers`, a fact this project got wrong in an earlier
draft of this note and corrected once a real test actually exercised it
(Phase 6; see `docs/build-log.md`).

## Elasticsearch (search index)

**What:** a document search engine, populated exclusively via Kafka —
never queried back into Event Service, never a source of truth (§8).

**Why:** free-text search with relevance ranking is a poor fit for a
relational database. Populated asynchronously so a slow or unavailable
index can never block an organizer's write path.

**Status:** Implemented, Tested. Single-node topology
(`number_of_replicas: 0`); service startup blocks on cluster health so
nothing reports healthy before its shards are assigned (a real fix after
a Docker-disk-space incident during test development, P2.T3). `GET
/search` verified live end-to-end through Traefik against real published
events.

## `uv` (Python tooling) and workspace structure

**What:** a Python package/dependency manager used here to set up a
**workspace** — one shared virtual environment and lockfile across every
backend Python package in `/services`.

**Why:** with a shared dependency (`shared_auth`) across services,
per-package environments risk the same dependency resolving to different
versions in different services.

**Status:** Implemented — `uv run pytest` from within a member package and
`uv run --package <name> pytest` from the workspace root both confirmed to
resolve against the same shared environment.

## MongoDB (Motor, async driver)

**What:** a document database, used by Event Service exclusively for
seat-map storage, accessed via Motor, the async MongoDB driver.

**Why:** a seat map is always read and written as one whole nested
document per event — a better fit for a document store than a normalized
relational schema. Motor specifically for the project's async-throughout
requirement (§3).

**Status:** Implemented, Tested, Verified (live) — store/fetch/delete
round-trip against a real `mongo:7` container; `/healthz` confirms
connectivity alongside the existing Postgres check.

## Alembic (schema migrations)

**What:** SQLAlchemy's migration tool, generating versioned, revertible
schema changes from the ORM models.

**Why:** with schema changes across every phase and grading requiring a
reproducible schema history, autogenerated + reviewed migrations give both
a paper trail and a repeatable `alembic upgrade head`.

**Status:** Implemented, Tested — first migration applies clean;
downgrade-then-upgrade cycle verified (surfaced and fixed a gap in
autogenerate's default `downgrade()`, which doesn't drop a Postgres `ENUM`
type automatically — see `docs/build-log.md`, P1.T2).

## `testcontainers-python`

**What:** a library that boots real, disposable Docker containers for the
duration of a test session, rather than mocking the database layer.

**Why:** for a project whose core correctness claims are about real
database behavior — unique constraints, transaction boundaries, the
dual-hold concurrency race — a mocked database can't prove those claims.
Session-scoped containers over the running `docker compose` stack
specifically for test isolation: fresh boot, clean migration, automatic
teardown, runnable from a clean checkout without ambient state.

**Status:** Implemented, Tested. First used Phase 1 (Postgres, MongoDB);
extended Phase 3 to Redis and Kafka simultaneously, since Booking
Service's correctness claims span all three real datastores at once.

## Stripe (payment processing)

**What:** a third-party payment processor, integrated via `stripe-python`'s
async client, in test mode throughout — real API calls against Stripe's
sandbox, never a mocked HTTP layer, never real money.

**Why:** rather than hand-rolling a payment gateway (a security-sensitive,
PCI-scope-heavy problem outside this project's scope), Stripe demonstrates
a real, production-shaped integration: a genuine charge attempt, a genuine
webhook carrying the authoritative outcome, and a genuine refund.

**Idempotency-key pattern** (§9): both `create_charge` and `refund_payment`
pass Stripe's own `idempotency_key` as a backstop behind this system's
primary application-level guard (a resubmission gate keyed on whether
`stripe_charge_id`/`stripe_refund_id` is still `NULL` — see Database
Schema Design). **Webhook-driven confirmation is the sole source of truth
for a Payment's terminal status** (§9), never the synchronous response,
via a rowcount-gated conditional `UPDATE` the same way every Kafka
consumer in this system is required to be idempotent (§7).

**Status:** Implemented, Tested, Verified (live) — a real Stripe test-mode
charge-and-refund round trip: a real booking's `/pay` drove a genuine
`PaymentIntent`, the webhook round-tripped within about a second and
confirmed the booking, and cancelling drove a genuine refund
(`payment_db` confirmed `REFUNDED` with both provider IDs populated). A
synthetic redelivery against the now-populated refund ID exercised the
replay-no-op guard cleanly. See `docs/build-log.md`'s 2026-08-23 "Ninth
testing round" entry for the full session.

## Redis (`redis.asyncio`)

**What:** an in-memory key-value store, used exclusively as the backing
store for one of Booking Service's two `TicketHoldStrategy`
implementations — a distributed lock via `SET key value NX EX seconds`.

**Why:** `SET ... NX EX` gives exactly-one-winner concurrency semantics
and self-expiry in one atomic operation — a genuinely different mechanism
from the cron strategy's periodic-sweep approach (§6), which is the point
of building both: the Phase 8 benchmark only means something if the two
mechanisms are actually different. `redis.asyncio` for the same
async-throughout reason as every I/O dependency in this project.

**A gap found during Phase 3 review:** the lock itself self-expires, but
the `Booking` row created alongside it lives in Postgres, which Redis
knows nothing about — an abandoned checkout used to block the seat
permanently. Fixed with a separate age-based sweep for that row (see the
Class Diagrams chapter).

**Status:** Implemented, Tested, Verified (live) — proven against the
shared `TicketHoldStrategy` contract test and a dedicated 25-client race
test via real Redis; verified live against the running stack, including
direct inspection of the Redis key/TTL alongside `tickets.status`
correctly staying `AVAILABLE` throughout (the documented strategy
trade-off, observed directly).

## APScheduler

**What:** an in-process Python job scheduler running Booking Service's
periodic sweep — whichever cleanup the active `HOLD_STRATEGY` needs.

**Why:** the sweep needs to run inside the same async application (same
event loop, same session factory) as the rest of Booking Service, with no
separate process to stand up.

**Status:** Implemented, Tested, Verified (live, both strategies). Both
hold strategies need the scheduler, not just cron (a correction made
during Phase 3 review, once it became clear the Redis strategy's
`Booking`-row sweep needs it too). Verified live under both
`HOLD_STRATEGY` values, including a real abandoned booking cleared by the
Redis-side sweep job.

## Prometheus + Grafana (benchmark observability)

**What:** Prometheus scrapes each service's `/metrics` endpoint; Grafana
renders it. Run as a `benchmark`-profiled `docker-compose` pair, not part
of the default local stack.

**Why:** chosen over a hosted APM specifically for the local/on-demand fit
(§11) — no external account, no steady-state memory cost, the same config
works locally or against AWS. Dashboard-as-JSON provisioning keeps the
dashboard checked into the repo and reproducible.

**Status:** Implemented, Tested, Verified (P8.T1) — all three scrape
targets confirmed `up`; real traffic driven through Traefik; the resulting
counters/histograms confirmed scraped; all four dashboard panels queried
directly and confirmed to return real values. See `docs/build-log.md`'s
P8.T1 entry for two provisioning bugs found and fixed during this
verification.

## Python asyncio load harness (`/benchmark`)

**What:** a standalone script (`benchmark/run_benchmark.py`, its own `uv`
environment) that provisions a fresh seat pool via the real APIs, fires a
fixed burst of concurrent clients at it via `asyncio.gather`, and measures
successful/failed booking counts, hold-acquisition latency, and
time-to-release — the three metrics §6 calls for.

**Why (over k6):** resolved 2026-08-16 in favor of Python — it reuses the
exact `asyncio.gather` pattern already proven correct in P3.T7's
concurrency suite, at the cost of hand-rolling percentile reporting k6
would have provided out of the box.

**Status:** Implemented, Tested (P8.T2) — live-verified against the real
stack under both `HOLD_STRATEGY` values: a burst of 15 clients against a
5-seat pool produced exactly 5 successes/10 failures both times; release
latency confirmed within the expected TTL-plus-sweep window for both
strategies. Full contention-burst and release-latency runs at the fixed
load profile, archived under `/docs`, are covered in the Feature
Development Process chapter.

## React + Vite + TypeScript (frontend)

**What:** the minimal five-screen frontend (§10) — Vite, React 19,
TypeScript throughout, built to static assets and served by
`nginx:1.27-alpine` behind Traefik at `PathPrefix('/app')`.

**Why:** §10 already settled on a minimal React UI over a polished product
build. Vite over Create React App (unmaintained) or a Next.js-style
framework (server rendering this project has no use for). TypeScript to
catch API-shape mismatches against the backend's Pydantic schemas at
compile time.

**Status:** Implemented, Tested (Vitest — the seat-map layout/status join
and the checkout hold→pay state machine). `tsc -b`, `oxlint`, and a
production `vite build` all clean. Verified live both at the API-contract
level and via a rendered, clicked-through browser pass (2026-08-21) — see
`docs/build-log.md` for the bugs that pass found and fixed, including a
login-completion race in the OIDC callback route.

## `@tanstack/react-query`

**What:** the frontend's data-fetching/caching layer — every API call goes
through it, including the seat map's live status poll.

**Why:** the "polling, not push" seat-map design (§23) needs an
interval-based refetch with de-duplication and cache invalidation;
`refetchInterval` gives that in one config line.

**Status:** Implemented, Tested (indirectly — the seat-map join function
it feeds is unit-tested; the poll itself is live-verified: a real hold in
one request flipped a real seat's status in the polled response within
one interval).

## `react-oidc-context` / `oidc-client-ts` (frontend OIDC client)

**What:** drives the browser-side half of Authorization Code + PKCE
against Keycloak's public frontend client — login/register redirect,
callback handling, token storage, and exposing the current user/token via
a React context.

**Why:** the realm's frontend client was already configured for a real
PKCE flow before Phase 7 began — hand-building that exchange is exactly
the "don't roll your own auth" reasoning already applied to the backend
(§5), now applied to the browser side.

**Status:** Implemented, Tested (live) — but not by trusting the library.
A full Authorization Code + PKCE exchange was independently driven with
raw HTTP requests performing the library's exact steps, which is what
caught a real bug: the issued token carried no `aud` claim at all, since
the frontend client was missing the audience-mapper every backend service
requires (§5 amendment) — fixed, then re-verified the same way.
