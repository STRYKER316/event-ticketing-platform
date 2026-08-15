# Project Description

*Status: draft, partial — Phase 0-2 evidence (platform plumbing, the event
catalog, and browse/search). Milestone A (P0-P2) is closed; this section
still needs Phases 3-6 (booking, payment, cancellation) before it describes
the full product.*

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

The full current-state topology and a live-traced authentication sequence
diagram are maintained at `docs/architecture.html` (kept current every
phase, not redrawn at report-assembly time) and can be regenerated by
following the reproduction steps in that file.

## What this section still needs

This section now covers Phases 0-2: the platform plumbing, plus the event
catalog and browse/search verticals it enables. It does not yet cover what
happens once a customer decides to actually book a seat — hold acquisition,
payment, confirmation, cancellation/refund, and notification delivery are
Phases 3-6, not built as of this draft.
