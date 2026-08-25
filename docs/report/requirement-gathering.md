# Requirement Gathering

*Status: draft, all six roles/permissions tables (Event, Booking, Payment,
Cancellation, Notification, the Phase 7 ticket-status route) plus the
Phase 8 benchmark's non-functional-requirements cross-reference in place.
This draft covers what's actually enforced in the codebase today, not
aspirational scope.*

## Actors

Two realm roles exist in Keycloak (`ticketing` realm), and every endpoint
in the system is required to declare which of the following it expects —
"no guard needed" is a decision made explicitly per route, never an
omission (Conventions, `CLAUDE.md`):

- **Anonymous / unauthenticated** — can browse: list events, view event
  detail, view a seat map, view venue detail. No token required.
- **`user`** — an authenticated customer. Everything anonymous can do,
  plus (as of Phase 3) hold a seat and book it; pay and cancel their own
  booking land in Phases 4 and 6.
- **`organizer`** — everything `user` can do, plus create, update, and
  delete events they own, add venues to the shared catalog, and attach or
  update the seat map for events they own. A user can hold both roles
  simultaneously (the seed realm's `bob` does); `organizer` is additive,
  not exclusive.

## Roles/permissions table — Event Service (Phase 1)

| Endpoint | Anonymous | `user` | `organizer` (not owner) | `organizer` (owner) |
|---|---|---|---|---|
| `GET /events`, `GET /events/{id}` | Allow | Allow | Allow | Allow |
| `GET /events/{id}/seat-map` | Allow | Allow | Allow | Allow |
| `GET /venues/{id}` | Allow | Allow | Allow | Allow |
| `POST /venues` | 401 | 403 | Allow (no ownership — shared catalog) | Allow |
| `POST /events` | 401 | 403 | Allow (becomes owner, `DRAFT`) | — |
| `PUT /events/{id}/seat-map` | 401 | 403 | **403** | Allow (upsert) |
| `POST /events/{id}/publish` | 401 | 403 | **403** | Allow (`DRAFT`→`PUBLISHED`) |
| `PATCH /events/{id}` | 401 | 403 | **403** | Allow |
| `DELETE /events/{id}` | 401 | 403 | **403** | Allow (`DRAFT` only — **409** if `PUBLISHED`, see Phase 3 note below) |
| `GET /search` (Search Service) | Allow | Allow | Allow | Allow |

The bolded cells are the requirement that role checking alone cannot
satisfy: `organizer` is necessary but not sufficient (§15). A second,
explicit check — the resource's stored `organizer_id` against the caller's
JWT `subject` — runs inside `EventManager` on every write to an existing
event, independent of the `require_role("organizer")` route guard. This is
the concrete instance of the "ownership scoping, not just role checks"
invariant `CLAUDE.md` holds across every service.

**Status:** Implemented, Tested, Verified. Every cell in this table was
exercised live against a running Keycloak instance with real seed users
(`alice`: `user` only: `bob`, `carol`: both `user` and `organizer`, distinct
subjects) during Phase 1 — not inferred from reading the route
declarations. See `docs/architecture.html` §2 for the traced sequence.

**P1 addendum — closing the venue/seat-map write gap.** Decisions-log §15
originally scoped Event Service's writes as "`POST /events`, venue/seat-map
management," but Phase 1 only built the event endpoints — an audit ahead of
Phase 3 found an organizer had no API path to create a venue or attach a
seat map at all, only the seed script or a direct Mongo write. `POST
/venues` needs the `organizer` role but no ownership check — venues are a
shared catalog (no `organizer_id` on the model). `PUT /events/{id}/seat-map`
is ownership-scoped like every other event mutation, upserts (create or
replace), and re-publishes to Kafka/Search on that path exactly like
`PATCH /events/{id}` already does — since a seat map can change after an
event is `PUBLISHED`, this keeps the search index and Kafka payload's seat
list from going stale relative to the organizer's latest write.

**Phase 2 addition — publish as its own step.** Decisions-log §15's
original "creation = publishing" wording implied `POST /events` should
create in `PUBLISHED` state directly, but Phase 1 built a `DRAFT`/
`PUBLISHED` model with no way to reach `PUBLISHED` — an open scope question
deliberately left at the Phase 1 checkpoint, resolved in Phase 2 once the
Kafka producer needed a concrete answer for "when does an event become
visible to Search." Kept the two-state model and added `POST
/events/{id}/publish` as the same role-plus-ownership-scoped write pattern
as every other mutating endpoint, logged as an explicit §15 amendment.
`GET /search` needs no role distinction — every actor sees the same
published-event index, consistent with "browse/search" being explicitly
anonymous-accessible scope (§2).

## Roles/permissions table — Booking Service (Phase 3)

| Endpoint | Anonymous | `user` (any) |
|---|---|---|
| `POST /bookings` | 401 | Allow (creates a `PENDING` booking, holds the seat) |

Deliberately a one-row table: booking a ticket needs no `organizer` role
at all (§15 delta) — any authenticated user, `user` or `organizer` alike,
can book. There is no ownership-scoping check on this endpoint, because
there is no pre-existing owner to check against — the caller *becomes* the
booking's `user_subject` by making the request, not by matching an existing
resource's stored owner. Worth stating explicitly: "authenticated-only" and
"ownership-scoped" are two different points on the same auth-requirement
spectrum `CLAUDE.md` requires every route to declare, not the same check
applied twice.

**Phase 3 addition — closing the `_check_no_bookings` stub with a
decidable-locally rule.** Decisions-log §15's original delete policy read
"deleting a published event with existing tickets/bookings is not
supported... only events with zero bookings can be removed" — a check
Event Service was never actually able to perform, since database-per-service
(§8) means it cannot see Booking Service's `Ticket`/`Booking` rows, and a
delete-time cross-service query would need a sixth Kafka integration point
past the five-point cap (§7). Resolved once Booking Service existed to give
the stub something concrete to resolve against: `DELETE /events/{id}` now
refuses unconditionally once `status == PUBLISHED` (409), regardless of
whether tickets were ever actually booked — a fully locally-decidable rule,
logged as a §15 amendment. `DRAFT` events, which can never have provisioned
tickets, still delete freely.

## Roles/permissions table — Payment (Phase 4)

| Endpoint | Anonymous | `user` (non-owner) | `user` (owner, booking `PENDING`) |
|---|---|---|---|
| `POST /bookings/{id}/pay` (Booking Service) | 401 | **404** (existence hidden) | Allow (initiates a Stripe charge) |
| `POST /payments/charge` (Payment Service, internal) | 401 | Allow* | Allow* |
| `POST /payments/webhook` (Payment Service) | Allow (Stripe signature is the auth) | — | — |

\* `/payments/charge` is meant to be called only by Booking Service, which
has already done the ownership check. It still requires a valid Keycloak
token (the caller's own, forwarded unmodified), but not an ownership check
of its own, since Payment Service has no access to `booking_db` to perform
one (§8) — the one endpoint whose auth requirement is "authenticated,
checked by a different service" rather than "checked here."
**This "only Booking Service can reach it" premise was not actually true
until a CHECKPOINT code-review pass caught it**: Traefik's original routing
rule exposed the whole `/payments` prefix publicly, so any authenticated
user could call this route directly with a fabricated `amount_cents` — a
real authorization bypass, not a hypothetical one. Fixed by narrowing
Traefik's rule to `/payments/webhook` only; see `build-log.md`'s 2026-08-17
CHECKPOINT entry and the Class Diagrams chapter's Payment Service section
for the full account. The table above reflects the corrected, enforced
state.

**Ownership scoping on `/pay`** follows the same decisions-log §15 pattern:
the booking's stored `user_subject` compared against the caller's JWT
`subject`, not just a role check — `organizer` has no special standing here
(§15 delta, Phase 3). A booking has no publicly visible state (no `GET`
route, no public listing), so a non-owner's request answers **404**, not
403 — existence stays hidden the same way a `DRAFT` event's does, rather
than merely blocking the action while confirming the booking is real. Also
checks the booking is currently `PENDING` (409 otherwise) — a state
precondition on top of, not a substitute for, the authorization check.

**Status:** Implemented, Tested, Verified (live, except a real Stripe
success). Every cell above except the internal `/payments/charge` "Allow*"
rows was exercised live against the running stack with real seed users:
`bob` attempting `alice`'s booking → 404 (indistinguishable from a
nonexistent booking ID, also 404);
`alice` on her own `PENDING` booking → the full chain through to Payment
Service's own auth check and a genuine Stripe API call, failing only at
Stripe's placeholder-credential boundary (401 from Stripe itself, not from
this system). See the Testing Strategy documentation (Appendix A) and the
Class Diagrams chapter's Payment Service section for the full
live-verification detail and the real idempotency bug this testing caught
and fixed.

## Roles/permissions table — Cancellation & Refunds (Phase 6)

| Endpoint | Anonymous | `user` (non-owner) | `user` (owner, `CONFIRMED`, before event start) | `user` (owner, past cutoff / not `CONFIRMED`) |
|---|---|---|---|---|
| `POST /bookings/{id}/cancel` (Booking Service) | 401 | **404** (existence hidden) | Allow (cancels + releases seat + triggers refund) | **409** |

**Ownership scoping works the same way as `/pay`** — the same decisions-log
§15 pattern, applied a third time: the booking's stored `user_subject`
compared against the caller's JWT `subject`, not a role check —
`organizer` has no special standing over a booking it didn't make. Same
existence-hiding 404, not 403, for the same reason as `/pay`. Two state
preconditions stack on top of the authorization check, both independently
checked and independently returning 409: the booking must currently be
`CONFIRMED` (cancelling a still-`PENDING`, already-`CANCELLED`, or
`EXPIRED` booking is rejected), and the event's `start_time` must not have
passed yet (the cancellation policy in §22 — full refund, any time before
the event starts, no partial-refund tiers). Payment Service has no route
of its own for this — the refund is entirely Kafka-triggered (integration
point #5), the message itself standing in as authorization since Booking
Service already checked ownership before publishing it (§8, same reasoning
as `/pay`'s "authenticated, checked by a different service" cell).

**Status:** Implemented, Tested, Verified (live, except a real Stripe
refund succeeding). Every cell above was exercised live against the
running stack with real seed users, under **both** hold strategies: `bob`
attempting `alice`'s booking → 404 (indistinguishable from cancelling an
unknown booking, also 404);
`alice` cancelling her own `CONFIRMED` booking → 200, seat immediately
rebookable; a repeat cancel on the now-`CANCELLED` booking → 409; a cancel
attempt against an event whose `start_time` had passed → 409. The refund
trigger itself reached Stripe's real API boundary (failing only on the
placeholder credential, same tracked gap as `/pay`) — see the Class
Diagrams and Database Schema Design chapters' Phase 6 sections for the
full mechanism and live-verification detail.

## Roles/permissions table — Notification Service (Phase 5)

| Endpoint | Anonymous | Any authenticated caller |
|---|---|---|
| `GET /healthz` (Notification Service) | Allow (200, no DB to check) | Allow |

Notification Service has no organizer- or ownership-scoped route of its
own — `/healthz` is its only endpoint, public, and no other request ever
reaches this service; every notification it delivers arrives over Kafka
(integration point #3), not HTTP. Authorization for the underlying action
was already enforced by whichever service produced the message: Booking
Service's `PaymentOutcomeConsumer` (booking-confirmed) and Payment
Service's webhook handler and refund path (payment-confirmed,
refund-failed) each already checked ownership or used the Kafka message
itself as authorization before publishing (§8/§22). This is the "explicit,
never implicit" auth convention applied to its edge case: a service can
genuinely have nothing to guard, and that absence is stated here rather
than left unaddressed.

**Status:** Implemented, Tested, Verified (live). The retry/backoff/DLQ
ladder itself is not an authorization concern but is this phase's
functional centerpiece — see the Class Diagrams chapter's Phase 5 section
and the Testing Strategy documentation (Appendix A) for the mechanism, the
design reasoning (no database, so retry state rides on the Kafka message itself via a
`RetryEnvelope`), and the live-verification detail (both a forced failure
recovering on retry, and exhausted retries landing in the DLQ).

## Roles/permissions table — new Booking Service route (Phase 7)

| Endpoint | Anonymous | Any authenticated caller |
|---|---|---|
| `GET /bookings/events/{event_id}/tickets` | Allow (200) | Allow |

Public, no role or ownership check — browsing which seats are available
shouldn't require login, matching Event Service's own public `GET
/events*` routes; only the act of booking (already authenticated,
ownership-scoped where relevant) requires one. Added because the
frontend's seat map (§23) needed a read path this system never exposed
before Phase 7 — see the Database Schema Design and Class Diagrams
chapters' Phase 7 sections for the mechanism.

**Frontend auth, stated explicitly so it isn't mistaken for a second
enforcement layer**: the React app checks the logged-in user's `organizer`
realm role client-side, purely to decide whether to show or hide the
organizer screen. That check has zero authority — every organizer route
it calls (`POST /venues`, `POST /events`, `PUT /events/{id}/seat-map`,
`POST /events/{id}/publish`) is still `require_role("organizer")`-gated
server-side exactly as it always was. This phase's live verification
confirmed the split holds in both directions: a request from a
non-organizer token still 403s, independent of whether the frontend would
have shown the button.

**Status:** Implemented, Tested, Verified (live) for the new endpoint and
the auth split. Full organizer flow (venue → event → seat map → publish)
live-verified end to end via the same request shapes the frontend sends;
the published event was immediately searchable and bookable afterward.

## Non-functional requirements — the Hold-Mechanism Benchmark (Phase 8)

Every roles/permissions table above states this system's functional
requirements — who can do what. The one non-functional requirement this
project set out to demonstrate with a real number, not a hand-wave, is
the double-booking-critical path's throughput and latency under
contention (§6): can the dual `TicketHoldStrategy` mechanism resolve a
seat race correctly *and* fast, at a scale beyond the correctness suite's
25 clients. Phase 8's benchmark (this report's only Measured chapter — see
Feature Development Process for the full methodology and honest reading of
the results) answers this directly, against a fixed load profile of 300
concurrent requests (10 clients racing each of 30 contended seats), three
independent runs per strategy:

- **Correctness under load, not just speed**: both `TicketHoldStrategy`
  implementations allocated the contended pool exactly correctly on
  every run — 30/30 successful bookings, 270/270 real `409`s, zero
  double-bookings — confirming the concurrency guarantee P3.T7's smaller
  suite already proved still holds an order of magnitude past that
  suite's own scale.
- **Hold-acquisition latency**: p50 0.431s (cron) / 0.446s (redis), p95
  1.052s / 1.257s, p99 1.144s / 1.417s — statistically indistinguishable
  between strategies at this scale (n=3), the honest reading being that
  neither mechanism's acquisition path is the dominant cost relative to
  request overhead (network, auth, FastAPI dispatch).
- **Release latency**: the passive path (abandoned hold → TTL/sweep) is
  ~12-13s for both strategies, bounded by the configured
  `HOLD_TTL_SECONDS`/`HOLD_SWEEP_INTERVAL_SECONDS` rather than by
  mechanism speed; the immediate-trigger path (§17's compensation flow,
  simulated ahead of Phase 4 actually existing to drive it, then
  confirmed as the real mechanism once Phase 4 shipped) completes in
  single-digit milliseconds for both strategies — roughly three orders of
  magnitude faster than the passive path, the measured justification for
  building the immediate-release compensation flow at all rather than
  relying on the timeout safety net alone.

These numbers are this report's one instance of a non-functional
requirement verified by real, repeated, archived measurement
(`docs/benchmark-results/`) rather than by a roles table or a live-tested
status code — see the Feature Development Process chapter for the full
methodology, per-run data, and the mixed-result reading the data actually
supports (a small, consistent millisecond-scale edge for cron on
immediate release; no meaningful difference elsewhere).

## What this chapter still needs

None remaining.
