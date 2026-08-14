# Technologies Used

*Status: draft, running list — appended each phase per the DOCUMENT step.
"Real-world framing" polish pass happens at P11.T2; until then this is
accurate but unpolished. Entries below are limited to what Phase 0 actually
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
purely from Docker labels, no manual wiring; dashboard reachable; every
`/demo/*` and `/healthz` route confirmed reachable only through Traefik, not
by hitting the service directly.

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

**Status:** Implemented, Tested. 10 pytest unit tests (valid token, expired
token, tampered token, wrong audience, wrong issuer, unknown signing key,
missing required role, present required role, JWKS-URI override behavior),
all against a mocked JWKS endpoint with a real generated RSA keypair — no
live Keycloak required to run the suite. Also verified live, end-to-end,
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

**Status:** Tested (isolated smoke test only — no service depends on it
yet). A throwaway `aiokafka` producer/consumer round-trip proves the broker
itself works end-to-end: a message produced is consumed back and asserted
equal. Real integration wiring begins in Phase 2 (search indexing).

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
