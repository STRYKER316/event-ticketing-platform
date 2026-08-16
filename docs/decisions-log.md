# Capstone Decisions Log — Event Ticketing/Booking Platform

**Status: FINAL — locked and ready for implementation.**

*Developed across multiple structured review passes: structural consistency, cross-service data flow, resource/memory budget, runtime and concurrency behavior, and practical execution (local dev environment, dev workflow, realistic cost). All 26 sections below are confirmed and locked — see §3 for the framework confirmation specifically.*

Status key: **Decided** (locked, not yet built) / **Planned** (approach agreed, details open) / **Deferred** (explicitly out of scope, may be discussed in report only)

---

## 1. Project Selection — Decided

Event ticketing/booking platform, in the BookMyShow/Ticketmaster mold. Chosen over marketplace, job-queue, billing, ledger, and ride-hailing alternatives for the most natural (least-forced) coverage of the target stack while staying realistically scoped for a solo 1–2 month build.

Reference: [Hello Interview — Design a Ticket Booking Site Like Ticketmaster](https://www.hellointerview.com/learn/system-design/problem-breakdowns/ticketmaster), used as an architecture baseline. This capstone deliberately goes further than that reference in several ways: real auth (via Keycloak, not hand-waved), real deployment, automated testing, and five genuine Kafka integration points the reference itself doesn't use (see §7).

## 2. Scope Boundary — Decided

**In scope:** view events, search events, book tickets with no double-booking, cancel bookings with refund, real auth, payment processing, deployment to AWS, automated testing.

**Explicitly deferred / not built** — may appear in the report as "Future Work," never described as implemented or tested:
- Virtual waiting queue (SSE/WebSocket) for extreme-demand events
- CDN/edge caching
- Sharding, read replicas, multi-region — no claim of extreme-scale (e.g. 10M-concurrent) numbers
- Kubernetes — Docker + docker-compose locally, Elastic Beanstalk on AWS instead
- Refund-failure rollback (re-locking a seat if a refund fails after release) — see §22
- Claim-check pattern for large-venue seat data (stadium-scale, 20,000+ seats) — see §7.2

## 3. Application Framework — Decided (FastAPI)

**FastAPI**, chosen over Spring Boot after the comparison was reframed twice:
- Originally weighed against curriculum coverage (Modules 15–16 are Spring-centric) and engineering-skill growth via building infrastructure from scratch
- Goal was then explicitly reframed: use existing systems (gateway, identity provider) rather than build them from scratch; skill growth is no longer the deciding criterion
- Once the API Gateway and Auth Server were moved to external, language-agnostic systems (§5), the remaining comparison was FastAPI vs. Spring Boot purely for the 5 application services — and under solo/timeline/existing-fluency constraints, FastAPI won on velocity and avoiding a second framework learning curve stacked on top of the system-design work itself

**What's knowingly given up by not using Spring Boot:** Spring Kafka's more mature retry-topic/dead-letter-topic tooling (est. 2–4 extra days hand-rolling equivalent retry/DLQ logic in aiokafka — this cost is real, see §17), near-zero-config Keycloak resource-server integration (FastAPI needs a hand-written JWKS-validation dependency, ~0.5 day one-time cost), reps in Spring's DI-container/AOP paradigm specifically, and stronger resume signal toward traditionally Java-heavy enterprise backend roles.

**Confirmed:** the program accepts any language/framework for this capstone — a non-Spring implementation is explicitly permitted.

## 4. Microservices (5) — Decided

| Service | Owns | Key libraries |
|---|---|---|
| Event Service | Events/venues/performers (Postgres); venue seat-map layouts (MongoDB); organizer-only write endpoints (§15) | SQLAlchemy (async) + asyncpg, Pydantic, Motor, Alembic |
| Search Service | Elasticsearch index (not a source of truth); Kafka consumer for sync | elasticsearch-py, aiokafka |
| Booking Service | Tickets, bookings, hold state (Postgres) — both hold strategies (§6); cancellation & seat release (§22) | SQLAlchemy (async), redis.asyncio, APScheduler, aiokafka |
| Payment Service | Payment/transaction records (Postgres); refund processing (§22) | stripe-python, APScheduler, aiokafka |
| Notification Service | No DB (or minimal delivery log) | aiokafka consumer, aiosmtplib/SendGrid SDK |

Auth is not a separate microservice — each service independently validates Keycloak-issued JWTs via a FastAPI `Depends()` dependency (python-jose/Authlib + JWKS lookup).

## 5. Gateway & Identity — Decided (existing systems, configured not built)

- **API Gateway: Traefik.** Chosen over Kong, Nginx, and AWS API Gateway — auto-discovers services via Docker labels, no extra datastore (unlike Kong), consistent between local docker-compose and EC2/EB deployment (unlike AWS API Gateway). Kong's main differentiator (deep OIDC plugin integration) is redundant here since each service already validates its own JWTs independently.
- **Identity Provider: Keycloak**, self-hosted via Docker, running in dev/start-dev mode with its embedded database (see §12 for the resource-budget reasoning) — appropriate for a capstone demo; worth a one-line caveat in the report that this isn't production-hardened.

**Trade-off, explicitly acknowledged:** this gives up the "built an authorization server from scratch" story (PKCE, refresh-token rotation, JWK management as your own code) — the most substantive single build item in the original curriculum-driven plan. In exchange: ~8–12 days of build time recovered, and materially better security correctness than a hand-rolled implementation. Judged worth it given the current goal (auth as a platform dependency, not the focus) and the industry-standard norm of not rolling your own auth.

The gateway swap (Spring Cloud Gateway / hand-built → Traefik) was judged close to a free trade — real infra skill either way, minimal cost either way.

## 6. Booking Hold Mechanism — Decided

Both strategies implemented behind a common interface (e.g. `TicketHoldStrategy`) in Booking Service, swappable via config:
- **Cron-based expiry** — status + expiration timestamp, periodic sweep (APScheduler)
- **Redis TTL distributed lock** — `SET key value NX EX seconds`, auto-expiry

**Benchmark plan:** concurrent clients against the same small seat pool (k6 or a multi-threaded harness). Metrics: successful/failed bookings, hold-acquisition latency, time-to-release-after-abandonment. This is the centerpiece of the report's Feature Development Process chapter — real measured numbers only, never fabricated.

See §21 for how a hold relates to the Booking row's lifecycle.

**Amendment (Phase 3, 2026-08-16):** the Redis strategy as originally built released only the Redis lock key on TTL expiry and never touched the Booking row `BookingManager` creates alongside it (§21) — an abandoned checkout left that row `PENDING` forever, and the partial unique index enforcing at-most-one-active-booking-per-ticket then permanently blocked the seat from ever being booked again, since it treats `PENDING` as active regardless of how it got that way. Found during this phase's dedicated adversarial review, specifically because it meant the two strategies were not actually behaviorally equivalent — a real risk to the benchmark comparison this section exists to set up, not just a bug in one implementation. Resolved by giving the Redis strategy its own sweep after all: `BookingRepository.expire_stale_pending()`, an age-based Postgres sweep (`created_at` vs. `hold_ttl_seconds`, since this strategy has no `Ticket`-side expiry column to key off of the way the cron strategy does), run on its own APScheduler job alongside the cron strategy's existing sweep — `hold_sweep.py` now always starts a scheduler and picks the job matching whichever `HOLD_STRATEGY` is active, rather than only running a scheduler under `cron`. This is an extension of this section's original decision, not a reversal: both strategies still keep their core mechanisms (conditional Postgres `UPDATE` vs. Redis `SET NX EX`) exactly as decided: the Redis strategy still never writes `Ticket.status`, and the Redis lock itself still needs no sweep. Only the previously-unaddressed Booking-row cleanup was added.

## 7. Kafka — Decided

Client library: **aiokafka** (native async, integrates directly with FastAPI's event loop; chosen over confluent-kafka's sync-first design for this project's scale). Broker runs in **KRaft mode** — no separate Zookeeper container (see §12).

Five integration points — none of which appear in the Hello Interview reference design:
1. **Event Service → Search Service**: event created, updated, or deleted → Kafka → Elasticsearch index updated or removed accordingly
2. **Event Service → Booking Service**: event published → Kafka → Booking Service provisions Ticket rows in its own DB. **Event-carried state transfer**: the payload includes the full seat list (section/row/number) from the venue's seat map, not just a venue ID — this avoids a synchronous callback from Booking Service to Event Service's API at provisioning time, which would otherwise reintroduce the runtime coupling Kafka is meant to remove. **Scale caveat, decided as-is**: this holds for demo-sized venues — rough estimate, safely under Kafka's default ~1MB message limit up to roughly a couple thousand seats — which comfortably covers a realistic theater or mid-size concert hall. Building for actual stadium scale (20,000-80,000+ seats) would need a claim-check pattern instead (Kafka carries a reference, the payload is fetched from shared storage), which needs infrastructure this project doesn't otherwise have and isn't demonstrating anything the rest of the system doesn't already prove — same reasoning as the other scale items deferred in §2. Listed there as Future Work rather than built.
3. **Booking/Payment Service → Notification Service**: booking confirmed, payment confirmed, or refund failed → Kafka → confirmation/alert sent, with retry/backoff + dead-letter handling on delivery failure (see §17)
4. **Payment Service → Booking Service**: payment declined/failed → Kafka → Booking Service releases the hold immediately rather than waiting for timeout expiry (see §17)
5. **Booking Service → Payment Service**: booking cancelled → Kafka → Payment Service issues the Stripe refund (see §22)

**Idempotent consumers — a general rule, not just the notification path:** Kafka delivers at-least-once by default, so every consumer above must treat redelivery as a safe no-op, not just Notification Service's retry/DLQ handling (§17). Concretely: ticket provisioning (#2) is keyed so a duplicate "event published" message doesn't create duplicate Ticket rows for the same seats (unique constraint on event+seat, upsert semantics); hold release (#4) only transitions a ticket if it's currently held, so a duplicate "payment failed" message is a harmless no-op; refunds (#5) already use the idempotency-key pattern from §9. This is the same reasoning applied consistently across all five points, not a special case for any one of them.

**Documented trade-off, not a bug**: because ticket provisioning (#2) happens asynchronously after event creation, there's a brief window where a newly created event is visible in search before its tickets exist yet. This is normal, expected eventual consistency for an event-driven design and is treated as such — not hidden, and worth a mention in the report rather than papered over (added to §26).

## 8. Database Topology — Decided

**Database-per-service** (at the logical-database level, not necessarily separate server processes — see §12), with cross-service integration handled entirely via the five Kafka events in §7 — no shared databases and no distributed transactions anywhere in the system:

- **Auth**: owned by Keycloak, not a project-owned DB
- **Event DB** (Postgres): events, venues, performers
- **Event Service — MongoDB**: venue seat-map/layout documents only — the one deliberate NoSQL integration, chosen because it's genuinely schema-flexible JSON (sections/rows/seat coordinates), not used elsewhere
- **Booking DB** (Postgres): tickets, bookings, hold state — owned entirely by Booking Service so the double-booking-critical transaction (check availability + reserve + book) stays fully intra-service
- **Search**: Elasticsearch index, populated via Kafka, not a source of truth
- **Payment DB** (Postgres): payment/transaction/refund records
- **Notification Service**: no DB, or a minimal delivery-log table

## 9. Payments — Decided

Stripe, test mode. Webhook-driven confirmation, idempotent handling using the booking ID as the idempotency key for charges, and the same pattern (booking ID + "refund") for refunds issued via §22.

## 10. Frontend — Decided

Minimal functional React UI, not a polished product build. Five screens: event list/search, event detail with interactive seat map, checkout, confirmation, login/register. Built as static assets, served via a lightweight Nginx container behind Traefik — near-zero runtime memory footprint, doesn't meaningfully affect the instance's resource budget (§12). Backend remains the graded emphasis.

## 11. Logging/Monitoring — Decided

**Prometheus + Grafana**, using `prometheus-fastapi-instrumentator` for per-service metrics, plus structured JSON logging (`structlog`). Every service exposes a `/metrics` endpoint regardless of deployment target. Treated primarily as a **local/benchmark-time tool** — spun up when running the k6 hold-mechanism benchmark (§6) and generating report graphs — rather than an always-on component of the AWS deployment, to keep steady-state memory pressure down on a single instance (§12). Replaces the earlier Spring Boot Actuator plan; conceptually the same role.

## 12. Deployment — Decided

**Elastic Beanstalk**, Docker platform branch (AL2023), deploying the existing `docker-compose.yml` directly — EB natively detects and runs Compose files on this branch, no separate multi-container config format needed.

**Single-instance mode** specifically (no load balancer, no auto-scaling group) — avoids a ~$16–20/month fixed ALB cost not needed for a capstone demo, while still getting EB's automated provisioning over raw EC2.

**Resource budget, worked out explicitly:** the full stack is roughly 14–15 containers — 5 app services, Traefik, Keycloak, Kafka, Redis, Elasticsearch, Postgres, MongoDB, frontend, and (only during benchmark runs) Prometheus/Grafana. Elasticsearch and Kafka are both genuinely memory-hungry, so three consolidations are locked in to keep this workable on one instance without discovering the problem via OOM-killed containers mid-demo:
- **One Postgres container, three logical databases** (event_db, booking_db, payment_db) rather than three separate Postgres server processes — same per-service credential isolation, far less overhead
- **Kafka in KRaft mode** (§7) — no separate Zookeeper container
- **Keycloak in dev mode** with its embedded DB (§5) — no dedicated Postgres instance for it

**Instance sizing: t3.xlarge (16GB) as the realistic floor**, not a maybe-upgrade — even after the consolidations above, this is a genuinely heavy stack for 8GB. Given the instance is already stopped when idle (§13), the cost delta over t3.large is small.

## 13. Cost Management — Decided (operational practice, not architecture)

**Revised estimate given the local-first workflow (§25)**: since routine development happens entirely locally, AWS runtime is no longer "60-80 active hours/month" — it's closer to occasional validation checkpoints plus the final demo. Realistically: ~5-10 hrs for initial EB setup/debugging, ~10-15 hrs across a handful of milestone validation runs, ~5-10 hrs for demo/grading prep — roughly **20-35 hours of EC2 runtime across the entire 1-2 month build**, not per month.

- At t3.xlarge rates ($0.1664/hr): **~$3-6 total compute cost for the whole project**, not a monthly figure — a substantial downward revision from the earlier per-month estimate, now that AWS isn't where debugging happens.
- **Stop, not terminate, between sessions — decided.** The dollar difference between the two is small (~$4-6 total across the whole project, mostly EBS storage), and it's outweighed by a real debugging-risk cost on the terminate side: re-provisioning re-pulls every Docker image from scratch (10GB+ across Elasticsearch, Kafka, Postgres, Mongo, Keycloak, and the 5 services), and risks Elastic Beanstalk's underlying Docker platform branch drifting between sessions — a "nothing in my code changed but it broke" bug that's avoidable by just not re-creating the environment repeatedly. Stopping also keeps EB's environment CNAME stable across restarts, since that's tied to the environment persisting, not the instance.
- **Don't provision a separate Elastic IP for a stable address.** As of a February 2024 pricing change, AWS charges for *all* public IPv4 addresses now (~$3.65/month) — attached to a running instance, a stopped one, or unattached entirely — so there's no longer a cost reason to prefer one state over another for the IP itself. EB's own environment CNAME already provides a stable address without needing one.
- **Terminate everything once the project is actually done** (submitted and graded) — the small ongoing EBS storage cost has no reason to continue past that point.
- Avoid provisioning a NAT Gateway (~$33/month fixed) — not needed for a single dev/demo instance in a public subnet with security groups.
- Set an AWS Budgets alert (e.g. at $20) on day one as a safety net.
- Note: AWS's free-tier structure changed July 15, 2025 — accounts created after that date get $100–200 in credits valid 6 months rather than a 12-month EC2 free tier. At this revised usage level (~$7-12 total, compute plus storage), either free-tier structure comfortably covers the whole project.

## 14. Kubernetes — Deferred

Explicitly out of scope for the build (see §2); may be mentioned in the report's Future Work as a natural next step.

## 15. Roles & Event Creation — Decided

Two Keycloak realm roles: **user** (browse/search/book) and **organizer** (create/update events). Event Service gains write endpoints (`POST /events`, venue/seat-map management) protected by the `organizer` role, checked via the JWT role claim in the same per-service `Depends()` validation used for reads.

**Creation = publishing** — collapsed into a single step (`POST /events` immediately triggers ticket provisioning via §7.2), no separate draft/review state. Simpler for this scope.

**Amendment (Phase 2, 2026-08-15):** the above single-step model was never actually built — Phase 1 shipped `Event.status` as `DRAFT`/`PUBLISHED` with `POST /events` defaulting to `DRAFT` and no publish path, flagged at the Phase 1 review as an open scope question rather than silently fixed. Resolved now, at the point Phase 2's Kafka producer needs a clear answer for when an event becomes visible to Search: **keep the two-state model**, and add a dedicated organizer-only, ownership-scoped `POST /events/{id}/publish` endpoint (`DRAFT` → `PUBLISHED`, one-way, 409 if already published) as part of P2.T1. Kafka publishing (§7.1) and Search visibility are both gated on `status == PUBLISHED`: the create/update-while-`DRAFT` path never touches Kafka; `publish` and any subsequent update to an already-`PUBLISHED` event do. Publishing is blocked (422) if the event has no seat map yet, since the event-carried payload (§7.2) requires the full seat list. Ticket provisioning (§7.2) will key off this same `publish` action once Booking Service exists (P3), not off raw creation — this is a genuine extension of this section's original decision, not a bug fix, and is recorded here rather than only in `build-log.md` per the decisions-log-delta check in the phase-end checklist.

**Update/delete policy**: organizers can update event details after creation. Deleting a published event with existing tickets/bookings is not supported, to avoid orphaning booking records — only events with zero bookings can be removed.

**Amendment (Phase 3, 2026-08-16):** the "zero bookings" check above was never actually checkable as written — it implicitly assumed Event Service could see whether Booking Service has live `Ticket`/`Booking` rows for an event, which database-per-service (§8) rules out, and there is no sixth Kafka integration point for a delete-time cross-service query (§7 caps the five). Resolved at the point Booking Service's `_check_no_bookings` stub (left open since Phase 1/2) needed a real implementation: **a `PUBLISHED` event can never be deleted at all**, unconditionally — `DELETE /events/{id}` returns 409 once `status == PUBLISHED`, regardless of whether tickets were ever actually booked against it. `DRAFT` events, which are never provisioned into Booking Service, still delete freely with no check needed. This trades a small amount of precision (an unbooked-but-published event also can't be deleted) for a rule that's fully decidable within Event Service alone — the same reasoning as every other database-per-service boundary in this system.

**Ownership scoping**: an organizer can only update or delete their own events, not other organizers' — enforced by comparing the event's owning-organizer ID against the JWT subject, not just checking for the `organizer` role generically. The `user` and `organizer` roles aren't mutually exclusive — a single account can hold both and both browse/book and create events.

## 16. Seating Model — Decided

**Reserved/numbered seating only** — no general-admission/quantity-based ticket type. Every Ticket row corresponds to one specific seat (section/row/number) for one event, matching the Hello Interview reference model directly. This is what makes the seat-map MongoDB documents (§8) and per-seat hold-locking (§6) meaningful rather than decorative.

## 17. Failure Handling — Decided

**Full compensation flow**, not just timeout-based — two explicit mechanisms beyond the natural hold-expiry safety net (§6):

- **Payment failure → immediate hold release**: when a Stripe webhook reports a declined/failed payment, Payment Service publishes a `payment.failed` Kafka event (§7.4). Booking Service consumes it and releases the hold immediately — deletes the Redis lock / clears the ticket's hold status — rather than waiting for TTL or cron-sweep expiry. Gives the report a second, directly measurable metric (release latency: immediate-on-failure vs. timeout-based) alongside the cron-vs-Redis benchmark in §6.
- **Notification delivery retry/backoff**: Notification Service's Kafka consumer implements a manual retry-with-backoff pattern — aiokafka has no built-in equivalent to Spring Kafka's `@RetryableTopic`, so this is hand-rolled: on processing failure, republish to a `notification-retry` topic with increasing backoff, and after a fixed number of attempts, route to a `notification-dlq` topic rather than silently dropping the message.

**Cost, honestly stated**: Spring Kafka's more mature retry/DLT tooling (§3) is a real, relevant gap here, since this design requires hand-rolled retry/DLQ logic in aiokafka. Estimated added implementation time: roughly 2–4 days for the retry/DLQ pattern, plus ~0.5–1 day for the payment-failure hold-release handler. Chosen anyway for a stronger, more citable engineering story in the Feature Development Process chapter.

**Amendment (Phase 8, 2026-08-16, P8.T5):** the immediate-release path's real trigger — Payment Service's `payment.failed` Kafka event — cannot exist yet at this point in the locked report-first build order (P8 runs before P4, §27); `docs/phases/phase-8-kickoff.md` flagged this as an open question with two options (simulate the trigger directly, or defer the whole metric until P4). Resolved as **simulate directly**: the P8.T2 harness (`benchmark/run_benchmark.py`) gained a `--measure-immediate-release` mode that performs the exact same write `TicketHoldStrategy.release_hold()` would — a Postgres `UPDATE tickets SET status = 'AVAILABLE' ... WHERE status = 'HELD'` for the cron strategy, a Redis `DEL ticket:hold:{id}` for the Redis strategy, both mirrored directly from `app/logic/helpers/{cron,redis}_hold_strategy.py` rather than importing booking-service's app code into the harness's separate standalone venv — plus the `Booking.status` update a real `payment.failed` consumer would make alongside it, at a known instant, then polls for the same `Booking.status` `PENDING` → `EXPIRED` signal the passive-path measurement already uses. Chosen over deferral because "needs only Booking's two hold strategies" (§27) was the reason P8 runs before P4 in the first place — the release *mechanism* is identical either way, P4 only changes what calls it, so simulating the call is a faithful measurement of the mechanism, not a placeholder.

One correction made to the kickoff doc's own framing while implementing this: it describes the signal to observe as "`Ticket` back to `AVAILABLE`" — accurate for the cron strategy, but the Redis strategy never writes `Ticket.status` at all (this section's own documented trade-off), so that column is already `AVAILABLE` throughout and observing it would be vacuous under Redis. The harness instead polls `Booking.status`, the one signal both strategies actually produce on release, for both the passive and immediate measurements — an observation-methodology fix, not a change to what's being measured or to this section's mechanism decision.

**Measured (P8.T5, fixed load profile — 30-seat pool, 10 clients/seat, `HOLD_TTL_SECONDS=10`/`HOLD_SWEEP_INTERVAL_SECONDS=5` for reproducibility, see `docs/benchmark-results/`):** immediate-trigger release was observable in under 10ms for both strategies (cron: 5.1ms write / <1ms to observed; Redis: 5.9ms write / <1ms to observed), against a passive-path release of ~11–12s (bounded by the TTL/sweep-interval configuration, not the mechanism itself) — roughly three orders of magnitude faster, confirming immediate release is worth the added complexity §17 already committed to, independent of which hold strategy is active.

## 18. Testing Strategy — Decided

pytest + FastAPI's `TestClient` for unit tests. `testcontainers-python` for integration tests that need a real Postgres/Kafka/Redis instance rather than mocks — this is the practical equivalent of the curriculum's WebMvcMock-based testing approach, and supports honest "Tested" claims in the report rather than mocked-only coverage.

## 19. Seed Data, Secrets, and CI/CD

- **Seed data**: a seed script populates baseline sample events, venues, and seat-map documents for report screenshots and demos. The organizer creation API (§15) can add more live during a demo, but isn't relied on for initial state.
- **Notification delivery**: log-only/console output in dev and demo — no real email sending, no dependency on a SendGrid API key or deliverability during grading. A real provider is a later swap if wanted.
- **Secrets/config**: `.env` files locally, Elastic Beanstalk environment properties in AWS — no separate secrets manager at this scale.
- **CI/CD**: explicitly deferred, same as Kubernetes (§14) — not built for a solo capstone in this timeline, may be named in Future Work.

## 20. Repo Structure — Decided

**Monorepo** — one repository, one folder per service, plus frontend and infra as top-level folders:

```
/services
  /event-service
  /search-service
  /booking-service
  /payment-service
  /notification-service
/frontend
/infra          → docker-compose.yml, Traefik config, Keycloak realm config
/docs           → decisions-log.md
```

Chosen over five (or six, including an orchestration repo) separate repos: the 5 Kafka integration points (§7) span service pairs, so cross-service contract changes stay in a single commit rather than manually coordinated across repos. Also keeps a single `docker-compose.yml` at the root, gives Claude Code full cross-service context, and produces one clean link/commit for grading. Repo layout is a project-management choice here, not an architecture one.

## 21. Booking Lifecycle — Decided

A Booking row is created in **PENDING** state the moment a hold is acquired (§6) — before payment happens, not after. This single ID threads through the entire flow: passed to Payment Service as the object being paid for, used as the Stripe idempotency key (§9), and referenced by the `payment.failed` event (§7.4) so Booking Service knows exactly which hold to release.

State transitions:
- `PENDING` → `CONFIRMED` on successful payment
- `PENDING` → released/expired on hold timeout or payment failure (§17) — no booking record is retained as a meaningful entity beyond this
- `CONFIRMED` → `CANCELLED` on user-initiated cancellation (§22), with the associated Ticket row(s) returned to `AVAILABLE`

## 22. Cancellation & Refunds — Decided

**Full flow**: cancel + Stripe refund + seat released — chosen over a minimal (no-refund-automation) or deferred alternative.

- Booking Service exposes a cancel endpoint, restricted to the booking's owner, valid only on a `CONFIRMED` booking.
- **Seat release is optimistic and immediate**: the Ticket row returns to `AVAILABLE` (§21) as soon as cancellation is requested, reusing the exact same release mechanism already built for hold-release-on-payment-failure (§17) — no new mechanism needed.
- Booking Service doesn't own payment data (§8), so it can't call Stripe directly. It publishes a `booking.cancelled` Kafka event (§7.5); Payment Service consumes it and issues the refund using the same idempotency-key pattern as regular charges (§9).
- **Explicit scope boundary**: if a refund fails after the seat has already been released, this design does not attempt to roll back and re-lock the seat — that's saga-style rollback complexity disproportionate to a capstone. A failed refund is logged and surfaced via the existing Notification Service path (reuses §7.3, not a new integration point) for manual reconciliation. Documented in §2 as a deliberate boundary, not an oversight.
- Cancellation policy: full refund, allowed any time before the event's start time — no partial-refund tiers.

## 23. Seat Map Data & Refresh Strategy — Decided

The frontend's interactive seat map (§10) needs two different things from two different services, composed client-side rather than through a single backend call:
- **Layout** (sections/rows/seat positions) — from Event Service's MongoDB documents (§8), rarely changes
- **Live status** (available/held/booked, per seat) — from Booking Service's Ticket rows (§8), changes constantly

**Refresh: polling, not push.** Real-time push (SSE/WebSocket) is already off the table for this scope (§2, virtual waiting queue) — the same reasoning applies here. The frontend re-fetches seat status on an interval (or a manual refresh action) rather than subscribing to live updates. Simpler, consistent with everything else already decided, and honest about not building real-time infrastructure this project doesn't need.

## 24. Local Development Setup — Decided

Confirmed feasible on the actual dev machine: M3 Pro MacBook Pro, 36GB RAM, 500GB storage — well above what this stack needs, unlike the AWS side (§12) where memory was the binding constraint.

- **Docker Desktop**, already installed — no need to switch tools. VM resource allocation set generously (~8–12GB) via Docker Desktop → Settings → Resources.
- **No Apple Silicon friction expected**: every component publishes native arm64 images — Postgres, Redis, Elasticsearch (7.x+), MongoDB (4.4+), Keycloak, Traefik, and Apache's official Kafka image (which also natively supports KRaft mode, §7). No Rosetta emulation needed.
- **Elasticsearch, local config**: `discovery.type=single-node`, JVM heap capped explicitly (`ES_JAVA_OPTS=-Xms512m -Xmx512m`) rather than left to auto-detect against 36GB of host memory.
- **Stripe webhooks locally**: Stripe's servers can't reach a local machine directly. Use the **Stripe CLI** (`stripe listen --forward-to localhost:PORT/webhook`) to forward events — needed for the payment-confirmation and refund flows (§9, §22) to work in dev at all.
- **Prometheus/Grafana**: run via a docker-compose profile, brought up on demand for benchmark runs (§6) rather than every `docker compose up` — consistent with §11's "local/benchmark-time tool" framing, and keeps the default local stack lean even though memory isn't the constraint it is on AWS.

## 25. Development Workflow — Decided

**Local-first, AWS as deployment target, not a dev environment.** The full stack is built and iterated on locally via docker-compose (§20, §24) — this is where day-to-day coding, debugging, testing (§18), and the hold-mechanism benchmark itself (§6) happen. AWS (§12) is where the system gets deployed to prove it runs on real cloud infrastructure and to host the graded demo — not where development happens.

Practical reasons this matters, tying together decisions already made:
- §13's cost strategy (stop the instance when idle) only works if AWS isn't absorbing routine dev/debug time — you don't want to be paying per-hour to fix a typo in a route handler
- Local iteration is faster (no deploy step, no network latency) and easier to debug (logs directly in your terminal)
- §24's Docker Desktop / Stripe CLI / capped-ES-heap setup exists specifically to make the full stack runnable locally without AWS at all

**Realistic sequence**: build and stabilize all 5 services + infrastructure locally → get the booking flow, cancellation, and hold-mechanism benchmark working and tested there → only then deploy the same docker-compose setup to Elastic Beanstalk, spun up for validation checkpoints and the final demo, stopped (or terminated — see §13) the rest of the time.

## 26. Report Material — Limitations & Future Work (pull-list)

Everything below is scattered through the sections above but consolidated here for direct use in the report's Conclusion chapter, which explicitly requires "Limitations... and suggestions for improvement." Two categories, since they serve slightly different framing:

**Deferred entirely (Future Work — not built, not claimed as tested):**
- Virtual waiting queue (SSE/WebSocket) for extreme-demand events — §2
- CDN/edge caching — §2
- Sharding, read replicas, multi-region — §2
- Kubernetes (Docker + docker-compose/EB used instead) — §2, §14
- CI/CD pipeline (manual deploy used instead) — §19
- Refund-failure rollback / re-locking a released seat — §2, §22
- Claim-check pattern for stadium-scale venue seat data — §2, §7

**Deliberate simplifications for demo scope (Limitations — built, but in a lighter form than production would need):**
- Keycloak running in dev mode with its embedded database rather than a dedicated production-grade Postgres backing store — §5, §12
- Single Postgres container hosting three logical databases rather than fully isolated database instances per service — §12
- Notification Service sending to logs/console rather than a real email provider — §19
- Single-instance EC2 deployment with no load balancer or auto-scaling, so no high availability — §12
- Seat map refreshes via polling rather than real-time push — §23

**Worth discussing on its own merits (not a limitation, an honest eventual-consistency trade-off):**
- Brief window between event creation and ticket availability, since provisioning is asynchronous via Kafka (§7) — a legitimate talking point for the report's discussion of the event-driven design, not something to hide

Each of these has a real one-line "why" already written in its source section, ready to lift directly into the report without re-deriving the reasoning.

---

*Open items: none. All 26 sections above — architecture, scope, data flow, failure handling, local dev, workflow, and cost — are confirmed and locked. Remaining questions (API route naming, exact request/response payloads, health-check paths, CORS config, benchmark concurrency levels, etc.) are implementation-level and belong in detailed API/schema design or the build itself, where they're better discovered empirically than pre-decided here. This document is ready to hand to Claude Code for scaffolding, or to work from directly for service-by-service schema and API design.*


## 27. Development Phasing & Workflow — Decided

Build sequenced into 12 dependency-ordered phases (P0–P11); full detail in
`master-development-plan.md` (added to project knowledge). Scope/architecture
(§1–26) unchanged — this section records workflow only.

- North star: always-demoable `main`; a presentable report + working core by the
  end of September's 3rd week, at whatever completeness (~50–60% expected). Full
  completion ~early-to-mid November — September is a presentable-partial checkpoint.
- Locked build order (report-first): P0 → P1 → P2 → P3 → P8 → P4 → P6 → P5 → P7 →
  P10 → P9/P11. Benchmark (P8) runs immediately after Booking (P3), ahead of
  Payment, because it is the report's only Measured chapter and needs only Booking's
  two hold strategies — securing it early puts the report centerpiece in the
  showcase window.
- Report is a parallel workstream, not a final phase: each section drafted at the
  milestone that produces its evidence (§16 map); P11 is assembly + formatting only.
  Showcase report = Milestones A+B.
- Per-phase cadence: PLAN → REVIEW-PLAN → IMPLEMENT (continuous green commits) →
  TEST (unit + testcontainers; redelivery-is-no-op for Kafka) → REVIEW → DOCUMENT
  (draft the report section + log deltas) → CHECKPOINT (tag/merge).
- Capacity basis: 2 hrs/evening from 2026-08-13; ~163 hr reference (true ~170–185);
  the "testing + report" line is under-budgeted, mitigated by continuous drafting.