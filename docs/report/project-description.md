# Project Description

*Status: draft, partial — Phase 0-3 evidence (platform plumbing, the event
catalog, browse/search, and now booking). This section still needs Phases
4-6 (payment, cancellation) before it describes the full product.*

## Overview

This project is a backend-heavy event ticketing and booking platform, built
as a solo capstone for the MS CS Backend Specialization (Scaler-Neovarsity ×
Woolf). It follows the shape of consumer ticketing platforms like
BookMyShow or Ticketmaster: browse events, reserve specific seats, pay, and
receive confirmation — with cancellation and refund as a first-class flow,
not an afterthought.

The system is designed against
[Hello Interview's "Design a Ticket Booking Site Like Ticketmaster"](https://www.hellointerview.com/learn/system-design/problem-breakdowns/ticketmaster)
as an architecture baseline, but deliberately extends past that reference in
several respects: real authentication and authorization via a self-hosted
Keycloak identity provider (not hand-waved), five genuine Kafka integration
points connecting independently-owned services (the reference design does
not integrate Kafka at all), automated testing against real dependencies
rather than mocks, and an actual cloud deployment.

## Architectural shape

The system is a microservices architecture of five backend services, each
owning its own datastore (event, search, booking, payment, notification),
sitting behind a single API gateway, with all cross-service communication
carried over five explicitly-scoped Kafka integration points rather than
shared databases or synchronous service-to-service calls. This
database-per-service, event-driven shape is a deliberate constraint, not an
accident of tooling: it is what makes each service independently testable,
independently deployable, and forces the ownership boundaries (who is
allowed to write what) to be explicit rather than implicit.

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
no synchronous call from either service into the other, and no shared
database. This is the same pattern Booking Service (Phase 3) extends for
integration point #2 (ticket provisioning), not a one-off for this pair.

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
only Measured chapter) will run real concurrent load against to produce
citable throughput/latency numbers, not synthetic ones.

The full current-state topology and a live-traced authentication sequence
diagram are maintained at `docs/architecture.html` (kept current every
phase, not redrawn at report-assembly time) and can be regenerated by
following the reproduction steps in that file.

## What this section still needs

This section now covers Phases 0-3: the platform plumbing, the event
catalog, browse/search, and booking with its dual hold-mechanism
guarantee. It does not yet cover payment, confirmation, cancellation/
refund, or notification delivery — Phases 4-6, not built as of this
draft.
