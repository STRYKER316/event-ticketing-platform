# Project Description

*Status: draft, covers Phases 0-10 — platform plumbing, the event catalog,
browse/search, booking, payment, cancellation/refunds, notification, the
frontend, and the AWS deployment. No narrative gap remains; the underlying
mechanisms for every phase below are also documented in the Class
Diagrams, Database Schema Design, Testing Strategy, and Deployment Flow
chapters.*

## Overview

This project is a backend-heavy event ticketing and booking platform, built
as a solo capstone for the MS CS Backend Specialization (Scaler-Neovarsity ×
Woolf). It follows the shape of consumer ticketing platforms like
BookMyShow or Ticketmaster: browse events, reserve specific seats, pay, and
receive confirmation — with cancellation and refund as a first-class flow,
not an afterthought.

The system is designed against
[Hello Interview's "Design a Ticket Booking Site Like Ticketmaster"](https://www.hellointerview.com/learn/system-design/problem-breakdowns/ticketmaster)
as an architecture baseline, extended past that reference with: real
authentication/authorization via a self-hosted Keycloak identity provider;
five genuine Kafka integration points connecting independently-owned
services (the reference design does not integrate Kafka at all); automated
testing against real dependencies rather than mocks; and an actual cloud
deployment.

## Architectural shape

The system is a microservices architecture of five backend services, each
owning its own datastore (event, search, booking, payment, notification),
sitting behind a single API gateway, with all cross-service communication
carried over five explicitly-scoped Kafka integration points rather than
shared databases or synchronous calls. This database-per-service,
event-driven shape is a deliberate constraint: it makes each service
independently testable and deployable, and forces ownership boundaries (who
is allowed to write what) to be explicit rather than implicit.

Phase 0's walking skeleton proved the plumbing this architecture depends on
actually works end-to-end, before any business logic existed:

- **Traefik** as the single gateway, discovering backend services
  automatically via Docker labels rather than manual routing configuration.
- **Keycloak**, self-hosted in dev mode, issuing and signing the JSON Web
  Tokens every service validates independently — no service trusts a
  request without verifying the token's signature, issuer, audience, and
  expiry itself.
- A shared, once-built authentication dependency (`shared_auth`) that every
  backend service imports rather than re-implementing token validation five
  times — the first concrete instance of the "build once, reuse everywhere"
  principle this architecture depends on for consistency.
- A proven, isolated Kafka round-trip, ahead of any service actually
  depending on the broker for real integration traffic.

Phases 1-2 turned that plumbing into the first two real product verticals —
**event management** and **browse/search** — the first two of the five
backend services actually built out, not just scaffolded:

- **Event Service** (Postgres for events/venues/performers, MongoDB for
  seat-map layouts, §8) is the system's source of truth for what exists to
  be booked. An organizer creates a venue (a shared catalog entry, not
  owned by whoever adds it), creates an event against that venue as a
  `DRAFT`, attaches a reserved-seating seat map (sections/rows/seats, §16),
  and publishes it — the one-way `DRAFT` → `PUBLISHED` transition that
  gates both Kafka visibility and, later, ticket provisioning (§7.2, §15).
  Every write is ownership-scoped: an `organizer` role is necessary but not
  sufficient — the resource's owning-organizer ID is checked against the
  caller's JWT subject on every mutation (§15), not just the role claim.
  Anyone, authenticated or not, can browse the catalog: list events
  (paginated, sortable), fetch one, fetch its seat map, fetch a venue.
- **Search Service** (Elasticsearch, populated exclusively via Kafka, never
  a source of truth, §8) is what a customer actually searches against.
  Publishing an event in Event Service produces a Kafka message carrying
  the full event-catalog state — including the seat list, so Search Service
  never needs a synchronous callback back into Event Service (§7.2) — and
  Search Service's consumer turns that into a queryable document within a
  bounded, tested eventual-consistency window (§7, §26). `GET /search` is
  public, free-text across title/description/venue/performers, paginated
  and sortable by relevance or start time.

The two services communicate only through Kafka integration point #1 (§7) —
no synchronous call, no shared database. Booking Service (Phase 3) extends
the same pattern for integration point #2 (ticket provisioning).

Phase 3 adds the third backend service and the system's centerpiece
correctness guarantee: **Booking Service** (Postgres `booking_db`, plus
Redis for one of its two hold mechanisms), which owns tickets, bookings,
and hold state entirely on its own (§8) — no other service can query it,
and it cannot query them back. Publishing an event (integration point #1)
also triggers integration point #2: an idempotent Kafka consumer
provisions one `Ticket` row per seat from the event-carried seat list, so
Booking Service never needs a synchronous callback into Event Service to
find out what seats exist. A customer books a specific seat by acquiring
a hold on it — the one place in this system's product logic where two
concurrent requests genuinely race for the same resource, and getting
that race wrong would silently sell the same seat twice. Booking Service
runs **two independently swappable hold mechanisms** behind one
`TicketHoldStrategy` interface, selected by a single config value rather
than baked into the code path: a cron-swept Postgres-column hold
(status + expiry timestamp, a periodic sweep releasing anything expired)
and a Redis `SET NX EX` distributed lock (the lock itself self-expires;
the Booking row created alongside it still needs its own age-based sweep,
since Redis has no way to reach into Postgres — found and fixed during
this phase's review, see `docs/report/technologies-used.md`). Both
satisfy an identical concurrency contract — proven, not
assumed, by a shared test suite exercised against both real
implementations, including 25 simulated clients racing one seat under
each mechanism with the assertion that exactly one ever wins — and this
is exactly the pair of mechanisms the Phase 8 benchmark (this report's
only Measured chapter) ran real concurrent load against to produce
citable throughput/latency numbers, not synthetic ones — see the Feature
Development Process and Requirement Gathering chapters for the results.

Phase 4 adds the fourth backend service, **Payment Service** (Postgres
`payment_db`), and the system's one deliberate exception to Kafka-only
cross-service communication. A customer initiates payment through Booking
Service's ownership-scoped `POST /bookings/{id}/pay`, not by talking to
Payment Service directly — Payment Service has no access to `booking_db`
(§8) to verify the caller actually owns the booking being charged, so
Booking Service makes the system's first and only synchronous
inter-service call, forwarding the caller's JWT unmodified to Payment
Service's charge endpoint along with the ticket's `price_cents` it
already holds locally (§9 amendment). This is a narrow, deliberate
exception to "cross-service data only via Kafka," justified because
initiating a charge needs an immediate request/response result — did
Stripe accept the attempt right now — a different shape of problem than
the eventually-consistent facts the five Kafka integration points
otherwise carry. The charge attempt's actual outcome is never trusted
from that synchronous response, though — **webhook-driven confirmation
is the sole source of truth for a Payment's terminal status instead**
(§9); the Technologies Used chapter's Stripe entry covers the full
mechanism, including a real Stripe test-mode charge-and-refund round
trip verified live in this project. Payment Service's webhook handler is
idempotent the same way every Kafka consumer in this system is required
to be (§7), and publishes the
confirmed or declined outcome onto Kafka integration point #4
(`payment.outcomes`), broadened
at implementation time to carry both `succeeded` and `failed` actions on
one topic rather than adding a sixth integration point (§7 amendment).
Booking Service's consumer for that topic either confirms the booking
(`PENDING` → `CONFIRMED`, ticket `BOOKED`) or releases the hold
immediately on failure, rather than waiting for the natural
TTL/sweep-interval safety net — the release mechanism the Feature
Development Process chapter's benchmark measured directly, ahead of this
phase actually existing to drive it (§17 amendment).

Phase 6 completes the booking lifecycle with **cancellation and
refunds**, a first-class flow rather than an afterthought (§22). A
customer cancels their own `CONFIRMED` booking, before the event's start
time, through the same ownership-scoped pattern every other mutating
endpoint uses; the seat releases immediately via a genuine third
`TicketHoldStrategy` method, `release_booking` — not a `release_hold`
reuse, since a confirmed booking's ticket is `BOOKED`, not `HELD`, and the
two methods target different source states. Booking Service doesn't own
payment data (§8), so it publishes a `booking.cancelled` Kafka event on
integration point #5, and Payment Service's first-ever Kafka consumer
issues the refund using the same idempotency-key pattern already
established for charges (booking ID plus "refund", §9). The cancellation
cutoff needed a start time Booking Service's own tables never stored —
resolved the same way Phase 4's pricing gap was, by adding a small
reference table (`booking_db.events`) populated from the same Kafka
message that already provisions tickets, no new integration point needed.
If a refund fails after the seat has already been released, this design
deliberately does not attempt saga-style rollback to re-lock the seat — a
failed refund is logged and surfaced via Notification Service for manual
reconciliation, an explicit scope boundary rather than an oversight (§22).

Phase 5 closes the loop with **Notification Service**, the one backend
service with no database of any kind (§17 amendment) — a documented
decision, not a smaller schema. `NotificationManager.deliver` writes
nothing anywhere; the delivery *is* the structured log line it emits,
since no real email/SMS provider exists in this system's scope. With no
database, the retry ladder's state (attempt count, original message, last
error) has nowhere to live except the Kafka message itself: a
`RetryEnvelope` round-trips through three topics — `notifications` (one
attempt, no backoff) → `notification-retry` (increasing backoff, `min(2
** attempt, cap)`) → `notification-dlq` (terminal, visibility-only —
nothing reprocesses out of it automatically, consistent with §17's "not
silently dropped" rather than "automatically retried forever"). Since
this service has no real external delivery dependency capable of a
genuine transient failure the way Stripe's API calls are for Payment
Service, the retry ladder is proven with a deliberate,
honestly-documented `simulated_failure_attempts` toggle rather than a
fabricated claim (§17 amendment) — off by default, overridden only for a
live demo or test. This completes integration point #3 (booking/payment
→ notification), fed by three producers across two services:
`PaymentOutcomeConsumer`'s booking-confirmed path and Payment Service's
own payment-confirmed and refund-failed paths, each already having
checked ownership or used the Kafka message itself as authorization
before publishing — the "message is the authorization" reasoning already
established for every consumer with no independent way to re-check it
(§8/§22, per the Requirement Gathering chapter's Notification Service
section).

The full current-state topology and a live-traced authentication sequence
diagram are maintained at `docs/architecture.html` (kept current every
phase, not redrawn at report-assembly time) and can be regenerated by
following the reproduction steps in that file.

**Phase 7** added the platform's one user-facing surface: a minimal React
single-page app (§10) — login/register, browse/search, an event detail
screen with an interactive seat map that polls Booking Service every five
seconds for live per-seat status, checkout (hold, then pay), confirmation,
and a minimal organizer flow (venue → event → seat map → publish) — served
as static assets behind Traefik. It needed one new backend route nothing
before it had exposed: a public `GET /bookings/events/{event_id}/tickets`
on Booking Service, since the seat map's live half (§23) had a design but
no read endpoint until this phase. Live-verifying the frontend's actual
login (a real Keycloak Authorization Code + PKCE exchange, not a mock)
surfaced findings worth naming here, not just in Testing Strategy: an
identity-configuration gap (the frontend's OIDC client had never been
issued a token carrying the audience claim every backend service
requires, so every authenticated call would have failed silently), a
genuine concurrency bug on the double-booking guarantee itself, and —
only surfaced once a later pass drove login through the app's own
rendered router rather than curl — login never actually completing at
all, since the index route stripped Keycloak's callback query string
before it could be processed. All found only because this phase was the
first to drive real, non-mocked traffic through the entire stack the way
an actual user would — see the Testing Strategy chapter's Phase 7 section
for the full account.

**Phase 10** took the already-working stack off a developer's own machine
and onto real cloud infrastructure — AWS Elastic Beanstalk, Docker
platform branch, single-instance mode (§12) — since local-first (§25)
makes this a late, thin deployment checkpoint rather than where
day-to-day development happens. Three real gaps surfaced only by
reaching the app through a public address instead of `localhost`: the
frontend's build-time service URLs and Keycloak's realm config were
hardcoded to `localhost` and had to become relative/runtime-derived;
PKCE's `code_challenge` needs `crypto.subtle`, which browsers restrict to
secure contexts, so the deployed plain-HTTP CNAME silently broke login
until a pure-JS SHA-256 fallback was added; and EB's Docker-Compose
deploy needs the compose file and every build context flattened at the
bundle root, unlike the nested `../services`/`../frontend` layout local
dev uses — solved with a dedicated bundle-assembly script rather than
restructuring the canonical compose file. The full customer flow —
browse, log in, hold a seat, pay via a real Stripe test-mode charge, and
confirm via the same webhook-driven path Phase 4 built — was then
live-verified against the deployed environment, not just local Docker.
The phase's other real find was operational: every EB environment,
single-instance tier included, is backed by an Auto Scaling Group that
treats a directly-stopped instance as a health-check failure and
replaces it, wiping container-local data — a correction to §12/§13's
original stop/terminate reasoning, fixed by suspending the ASG's
replacement processes before stopping. See the Deployment Flow chapter
for the full topology, configuration, validation evidence, and cost
writeup.

## What this section still needs

None remaining. This section now reads as one coherent narrative from
Phase 0 through Phase 10 with no gap — every phase's mechanism is
described here at the same narrative depth, with the full technical
detail cross-referenced to Class Diagrams, Database Schema Design,
Testing Strategy, Deployment Flow, and `docs/architecture.html` rather
than duplicated.
