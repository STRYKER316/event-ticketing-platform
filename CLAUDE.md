# Scaler_Neovarsity_MS_CS_Capstone

## What this is

Solo backend capstone (Scaler-Neovarsity x Woolf, MS CS, Backend Specialization) — an
event ticketing/booking platform in the BookMyShow/Ticketmaster mold. Microservices,
FastAPI, Kafka-driven integration, Postgres/MongoDB/Elasticsearch/Redis, Keycloak auth,
Stripe (test mode), Docker + AWS Elastic Beanstalk. This is a graded academic
deliverable with an accompanying 40-page report — see "Integrity rule" below before
touching status labels or benchmark numbers.

**Read `/docs/decisions-log.md` and `/docs/master-development-plan.md` before making
any architectural or scope decision.** They are locked (§1–27) and this file does not
override them — it's a quick-reference summary so you don't have to re-read both in
full every session. When in doubt, the decisions log wins.

## Repo layout (monorepo, decisions-log §20)

```
/services
  /event-service       Postgres (event_db) + MongoDB seat maps; organizer writes
  /search-service       Elasticsearch index; Kafka consumer only, not source of truth
  /booking-service      Postgres (booking_db); dual hold strategy; the heart of the system
  /payment-service      Postgres (payment_db); Stripe test mode
  /notification-service no DB (or minimal delivery log); hand-rolled retry/DLQ
  /_shared/auth          shared FastAPI JWT-validation dependency (build once in P0, reuse everywhere)
/frontend                minimal React, 5 screens, Nginx-served (Phase 7, not before)
/infra                   docker-compose.yml, Traefik config, Keycloak realm export
/docs                    decisions-log.md, master-development-plan.md, phase-0-kickoff.md,
                         build-log.md (append-only build diary — decisions, failures, fixes),
                         architecture.html (living topology + flow diagrams, current state —
                         see "End-of-phase walkthrough" below)
```

## Non-negotiable architecture invariants

These hold across every service, every phase — check new code against them:

- **Database-per-service.** No service queries another service's tables directly, ever.
  Cross-service data only arrives via Kafka events.
- **Five Kafka integration points only** (decisions-log §7) — event↔search, event→booking
  provisioning, booking/payment→notification, payment→booking (failed→release), booking→payment
  (cancelled→refund). Don't invent a sixth without discussing it first.
- **Every Kafka consumer is idempotent.** Redelivery must be a safe no-op — this gets
  explicitly tested (unique constraints, "only transition if currently in state X," etc.),
  not assumed.
- **Ownership scoping, not just role checks.** `organizer` role is necessary but not
  sufficient — always compare the resource's owning-organizer ID against the JWT subject.
- **No distributed transactions.** Compensation (refunds, hold release) reuses existing
  release/notify paths rather than inventing saga rollback machinery.
- **Reserved/numbered seating only** — every Ticket row is one specific seat for one event.

## Tech stack

FastAPI (async), SQLAlchemy async + asyncpg + Alembic, Motor (MongoDB), aiokafka
(KRaft-mode Kafka, no Zookeeper), redis.asyncio, APScheduler, elasticsearch-py,
stripe-python, structlog (JSON logs), prometheus-fastapi-instrumentator, Traefik v3
(gateway), Keycloak (dev mode, JWT/OIDC), pytest + testcontainers-python, k6
(benchmark load), React (frontend, Phase 7 only).

## Workflow & cadence (decisions-log §25, §27)

- **Local-first.** Everything is built and tested against `docker compose` locally.
  AWS (Elastic Beanstalk) is a late-phase deployment target, not a dev environment.
- **Per-phase loop:** PLAN → REVIEW PLAN → IMPLEMENT (small, continuous, green commits —
  don't batch a whole phase into one commit) → TEST (pytest unit + testcontainers
  integration; Kafka phases must test redelivery-is-a-no-op) → REVIEW → DOCUMENT
  (draft the report section this phase feeds, log any decisions-log delta; also
  append an entry to `docs/build-log.md` — what was built, what failed and why, what
  fixed it, any ad hoc decision too small for the decisions log) →
  CHECKPOINT (tag/merge at the phase boundary).
- **End-of-phase walkthrough.** At CHECKPOINT — not after every task — do a live,
  narrated walkthrough with the user: bring the stack up, hit real endpoints, show
  real logs/output, don't just describe it. Then update `docs/architecture.html` to
  match current state (topology + key flow diagrams, current-state focus like
  `git status` not `git log` — it shows what's true *now*, `build-log.md` is where
  history lives). Same file every phase, redeployed in place, not a new file per
  phase. Commit it as part of the phase's checkpoint. This is also the report's
  §16 topology/flow evidence, produced once, not redone later for the report.
- **`main` stays bootable at every commit.** If a session ends mid-task, the previous
  commit should still `docker compose up` cleanly. Config-gate anything half-finished
  rather than leaving `main` broken.
- **Build order is locked (report-first):** P0 → P1 → P2 → P3 → **P8 (benchmark)** →
  P4 → P6 → P5 → P7 → P10 → P9/P11. The benchmark runs right after Booking, before
  Payment — it's the report's only Measured chapter. Don't reorder this without
  updating the master plan.
- Current phase's task-by-task prompts live in `/docs/phase-0-kickoff.md` (Phase 0);
  later phases get the same treatment generated from `master-development-plan.md`
  section-by-section as they come up.

## Integrity rule — read before writing status anywhere

Status labels are **Planned → Implemented → Tested → Deployed → Measured → Verified**.
Never describe something as done, tested, or working unless it actually reached that
label through real execution in this repo. This applies to code comments, commit
messages, README claims, and especially anything destined for the report — the
Hold-Mechanism Benchmark (Phase 8) numbers in particular must never be fabricated,
estimated, or placeholder values that could leak into the report undetected.

## Conventions

- Python 3.12, async throughout — no sync `Session` or blocking calls in a request path.
  This is locked (§3): async I/O was a deciding factor over Spring Boot, so don't reach
  for sync patterns even if a past reference codebase does.
- **Commit messages: single imperative subject line, no body paragraph.** Never reference
  a phase/task ID inside the message itself (`P3.T4`, `(Phase 3)`) — that context lives in
  `master-development-plan.md`, not git history. e.g. `booking-service: add Redis TTL hold
  strategy`, not `booking-service: add Redis TTL hold strategy (P3.T5)`.
- **Never let a docs-only change ride along with a real code commit**, and never let a
  commit message *sound* doc-only when it isn't (avoid leading verbs like "Document,"
  "Record," "Confirm" on a commit that actually changes code). A `/docs` update gets its
  own commit.
- **DTO layer is a strict validation boundary, not a formality.** Every enum-backed field
  is typed as its real Python `Enum`, never `str` — let Pydantic's automatic 422 reject
  bad values before business logic ever sees them. Reject blank/whitespace-only required
  strings via a constrained type, not bare `str`. Any field with a logical constraint
  beyond its raw type (a date that can't be in the future, an amount that can't be
  negative) gets a validator at the DTO layer, not a downstream check several calls deep.
- **Every endpoint's auth requirement is explicit, never implicit.** Each route is one of:
  public, authenticated-only, `require_role("organizer")`, or ownership-scoped (role +
  owner-ID comparison, §15). Deciding "no guard needed" is fine; leaving it unstated by
  omission is not.
- **Log level discipline**: `debug` for normal flow/tracing, `info` for notable events,
  `warning` for expected/handled failures (validation rejections, business-rule
  `raise`s), `error`/`critical` reserved for genuine incidents. Don't log routine
  rejected-input paths at `error`.
- **Never query inside a loop.** Any endpoint assembling a response across multiple
  related records bulk-fetches first (`.in_()`-style filters), then assembles in memory.
- New service = copy the Phase-0 service template (once it exists), don't hand-roll a
  second pattern.
- Update this file after each phase checkpoint if conventions, commands, or structure
  shift — treat it as living documentation, not a one-time snapshot.

## Internal per-service layering — Decided

Lightweight version of the day-job pattern: **Manager + Repository per feature**, not
the full Manager/Logic/DBManager split. In the day-job version, Manager and Logic do two
different jobs despite sharing a folder: Manager is a thin dispatcher (which Logic class
handles this call), Logic is where `execute()`/`get()` and the actual business rules
live. Collapsing to one class merges both jobs into Manager:

- **The routing job** is cut because the one place this project genuinely needs to
  branch between alternate execution paths — the dual hold strategy — is already handled
  by the `TicketHoldStrategy` interface (§6). A second dispatch mechanism on top of that
  would be redundant. Every other feature here is single-path, so Manager-as-router has
  nothing to route.
- **The structuring job is not cut** — just no longer factored into its own class.
  Manager methods still read internally as the same sequenced, named-step shape
  `execute()` used to enforce (validate → fetch → mutate → build response); see the
  Manager bullet below.

Kept the Manager/Repository split itself because it's what keeps queries independently
testable and gives each feature a clean 2-class diagram for the report's Class Diagrams
chapter.

```
/services/{service-name}/app/
  main.py                    # app factory, router + consumer registration, startup/shutdown
  api/
    {feature}.py               # router — thin, no business logic
    schemas.py                  # Pydantic request/response DTOs, grouped by concern
  logic/
    {feature}_manager.py         # {Feature}Manager — one public async method per use case;
                                  # owns the transaction boundary (await session.commit());
                                  # constructor takes an existing session — never fetches
                                  # its own, since both API routes and Kafka consumers
                                  # construct it against a session they already hold
    helpers/                       # shared, non-endpoint-backing logic — hold-strategy
                                    # implementations, shared validators/resolvers
  db/
    models.py                       # SQLAlchemy models
    {feature}_repository.py          # query/write methods only, no business rules;
                                      # writes use flush(), never commit — Manager commits
  kafka/
    producers.py
    consumers.py                      # handlers construct the same {Feature}Manager the
                                        # API routes use — one business-logic path, two entry points
  core.py                              # settings, structured logging setup, async session
                                        # factory usable both via FastAPI Depends() and
                                        # directly inside Kafka consumer handlers
tests/
  unit/                                 # Manager methods, given a real or test session
  integration/                          # testcontainers — real Postgres/Mongo/Redis/Kafka
```

- DTOs: request/response Pydantic models live in `api/{feature}/schemas.py`. Enum-backed
  fields are typed as the real `Enum`, never `str` (see Conventions above). No separate
  logic-layer input DTO — Manager methods take typed kwargs or a schema instance directly;
  don't add an extra DTO class solely to pre-convert an enum's `.value`.
- `{Feature}Manager.__init__(self, session: AsyncSession)` — session always injected, never
  self-fetched. This is what makes it callable identically from a FastAPI `Depends()`-built
  session and from a Kafka consumer's own `async with session_factory() as session:` block.
- **A Manager method past the simplest CRUD case is still internally sequenced as named
  private steps** — `_validate()`, `_fetch()`, `_mutate()`, `_build_response()` called in
  order from the public method — even though this isn't factored into a separate Logic
  class. This is what preserves `execute()`'s readability discipline without the extra
  file per feature; don't let a Manager method collapse into one unstructured block just
  because the class boundary is gone.
- Repository methods never commit; Manager methods that mutate always end with
  `await session.commit()`. Same atomicity reasoning as the reference pattern — a Manager
  method may write through more than one Repository call before finishing.

<!-- Fill in as they stabilize post-Phase-0: -->
<!-- Build/run: make up / make down / make logs -->
<!-- Test: make test -->
<!-- Get a dev token: ./infra/get-token.sh -->
