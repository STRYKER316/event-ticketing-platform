# Requirement Gathering

*Status: draft, partial — roles/permissions evidence from Phases 1, 3, 4, 5,
6, and now 7. This draft covers what's actually enforced in the codebase
today, not aspirational scope.*

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
event, independent of and in addition to the `require_role("organizer")`
route guard. This is the concrete instance of the architecture invariant
"ownership scoping, not just role checks" that `CLAUDE.md` calls out as
holding across every service, not just this one.

**Status:** Implemented, Tested, Verified. Every cell in this table was
exercised live against a running Keycloak instance with real seed users
(`alice`: `user` only: `bob`, `carol`: both `user` and `organizer`, distinct
subjects) during Phase 1 — not inferred from reading the route
declarations. See `docs/architecture.html` §2 for the traced sequence.

**P1 addendum — closing the venue/seat-map write gap.** Decisions-log §15
originally described Event Service's write scope as "`POST /events`,
venue/seat-map management," but only the event endpoints were ever built in
Phase 1 — `master-development-plan.md` never scheduled a task for the other
two, so it went unnoticed until an audit ahead of Phase 3 surfaced it: an
organizer had no way to create a venue or attach a seat map through the API
at all, only via the seed script or a direct Mongo write. `POST /venues`
needs the `organizer` role but no ownership check — venues are a shared
catalog (no `organizer_id` on the model), unlike events. `PUT /events/{id}/seat-map`
is ownership-scoped like every other event mutation, upserts (create or
replace), and — since a seat map can change after an event is already
`PUBLISHED` — re-publishes to Kafka/Search on that path exactly like
`PATCH /events/{id}` already does for title/venue/performer changes, so the
search index and the Kafka payload's seat list never go stale relative to
what an organizer most recently set.

**Phase 2 addition — publish as its own step.** Event Service originally
shipped `POST /events` creating an event directly in a `PUBLISHED` state
per decisions-log §15's original "creation = publishing" wording, but the
actual Phase 1 build used a `DRAFT`/`PUBLISHED` model with no way to reach
`PUBLISHED` at all — an open scope question deliberately left at the Phase
1 checkpoint. Resolved in Phase 2 once the Kafka producer needed a concrete
answer for "when does an event become visible to Search": kept the two-
state model and added `POST /events/{id}/publish` as the same
role-plus-ownership-scoped write pattern as every other mutating endpoint,
logged as an explicit amendment to §15 rather than a silent implementation
detail (decisions-log is the normative record; see its §15 for the full
amendment text). `GET /search` needs no role distinction at all — every
actor sees the same published-event index, consistent with "browse/search"
being explicitly anonymous-accessible scope (§2).

## Roles/permissions table — Booking Service (Phase 3)

| Endpoint | Anonymous | `user` (any) |
|---|---|---|
| `POST /bookings` | 401 | Allow (creates a `PENDING` booking, holds the seat) |

Deliberately a one-row table: booking a ticket needs no `organizer` role
at all (§15 delta) — any authenticated user, `user` or `organizer` alike,
can book. There is no ownership-scoping check on this endpoint the way
Event Service's writes have one, because there is no pre-existing owner
to check against — the caller *becomes* the booking's `user_subject` by
making the request, not by matching an existing resource's stored owner.
This is a structurally different kind of authorization requirement than
every Event Service write, worth stating explicitly rather than leaving
implicit: "authenticated-only" and "ownership-scoped" are two different
points on the same auth-requirement spectrum `CLAUDE.md`'s conventions
require every route to declare, not the same check applied twice.

**Phase 3 addition — closing the `_check_no_bookings` stub with a
decidable-locally rule.** §15's original delete policy read "deleting a
published event with existing tickets/bookings is not supported... only
events with zero bookings can be removed" — a check Event Service was
never actually able to perform, since database-per-service (§8) means it
cannot see whether Booking Service holds any `Ticket`/`Booking` rows for
a given event, and there is no sixth Kafka integration point for a
delete-time cross-service query (§7 caps the five). Resolved at the point
Booking Service actually existed and gave the stub something concrete to
resolve against: `DELETE /events/{id}` now refuses unconditionally once
`status == PUBLISHED` (409), regardless of whether tickets were ever
actually booked — a fully locally-decidable rule, logged as a decisions-
log amendment to §15 rather than a silent behavior change. `DRAFT`
events, which can never have provisioned tickets, still delete freely.

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
one (§8). This is the one endpoint in the system whose auth requirement is
"authenticated, checked by a different service" rather than "authenticated
and/or ownership-scoped, checked here" — worth stating explicitly per the
auth-requirement convention rather than leaving it looking like an
omission. **This "only Booking Service can reach it" premise was not
actually true until a CHECKPOINT code-review pass caught it**: Traefik's
original routing rule exposed the whole `/payments` prefix publicly, so
any authenticated user could call this route directly with a fabricated
`amount_cents` — a real authorization bypass, not a hypothetical one.
Fixed by narrowing Traefik's rule to `/payments/webhook` only; see
`build-log.md`'s 2026-08-17 CHECKPOINT entry and the Class Diagrams
chapter's Payment Service section for the full account. The table above
reflects the corrected, enforced state.

**Ownership scoping on `/pay` works the same way as every other
ownership-scoped route** (§15's pattern): the booking's stored
`user_subject` compared against the caller's JWT `subject`, not just a role
check — `organizer` has no special standing here, same as booking itself
(§15 delta, Phase 3). A booking has no publicly visible state at all (no
`GET` route, no public listing), so a non-owner's request answers **404**,
not 403 — its existence stays hidden the same way a `DRAFT` event's does,
rather than merely blocking the action while confirming the booking is
real. Additionally checks the booking is currently `PENDING` (409
otherwise) — a state precondition on top of the authorization check, not a
substitute for it.

**Status:** Implemented, Tested, Verified (live, except a real Stripe
success). Every cell above except the internal `/payments/charge` "Allow*"
rows was exercised live against the running stack with real seed users:
`bob` attempting `alice`'s booking → 404 (indistinguishable from a
nonexistent booking ID, also 404);
`alice` on her own `PENDING` booking → the full chain through to Payment
Service's own auth check and a genuine Stripe API call, failing only at
Stripe's placeholder-credential boundary (401 from Stripe itself, not from
this system). See the Testing Strategy and Class Diagrams chapters'
Payment Service sections for the full live-verification detail and the
real idempotency bug this testing caught and fixed.

## Roles/permissions table — Cancellation & Refunds (Phase 6)

| Endpoint | Anonymous | `user` (non-owner) | `user` (owner, `CONFIRMED`, before event start) | `user` (owner, past cutoff / not `CONFIRMED`) |
|---|---|---|---|---|
| `POST /bookings/{id}/cancel` (Booking Service) | 401 | **404** (existence hidden) | Allow (cancels + releases seat + triggers refund) | **409** |

**Ownership scoping works the same way as `/pay`** (§15's pattern, applied
a third time in this system): the booking's stored `user_subject` compared
against the caller's JWT `subject`, not a role check — `organizer` has no
special standing over a booking it didn't make, same as booking and
payment before it. Same existence-hiding 404, not 403, for the same reason
as `/pay`. Two state preconditions stack on top of the
authorization check, both independently checked and independently
returning 409: the booking must currently be `CONFIRMED` (cancelling a
still-`PENDING`, already-`CANCELLED`, or `EXPIRED` booking is rejected),
and the event's `start_time` must not have passed yet (§22's cancellation
policy — full refund, any time before the event starts, no partial-refund
tiers). Payment Service has no route of its own for this at all — the
refund is entirely Kafka-triggered (integration point #5), the message
itself standing in as authorization since Booking Service already checked
ownership before publishing it (§8: Payment Service has no access to
`booking_db` to check ownership a second time, same reasoning as `/pay`'s
"authenticated, checked by a different service" cell).

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
itself as authorization before publishing, the same "message is the
authorization" reasoning §8/§22 already establish for Payment Service's
own consumers. This is the "explicit, never implicit" auth convention
applied to its edge case: a service can genuinely have nothing to guard,
and that absence is stated here rather than left unaddressed.

**Status:** Implemented, Tested, Verified (live). The retry/backoff/DLQ
ladder itself is not an authorization concern but is this phase's
functional centerpiece — see the Class Diagrams and Testing Strategy
chapters' Phase 5 sections for the mechanism, the design reasoning (no
database, so retry state rides on the Kafka message itself via a
`RetryEnvelope`), and the live-verification detail (both a forced failure
recovering on retry, and exhausted retries landing in the DLQ).

## Roles/permissions table — new Booking Service route (Phase 7)

| Endpoint | Anonymous | Any authenticated caller |
|---|---|---|
| `GET /bookings/events/{event_id}/tickets` | Allow (200) | Allow |

Public, no role or ownership check — browsing which seats are available
shouldn't require login, matching Event Service's own public `GET
/events*` routes; only the act of booking (already authenticated,
ownership-scoped where relevant) requires one. Added specifically because
the frontend's seat map (§23) needed a read path this system never
exposed before Phase 7 — see the Database Schema Design and Class
Diagrams chapters' Phase 7 sections for the mechanism.

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

## What this chapter still needs

Non-functional requirements (the Hold-Mechanism Benchmark's
throughput/latency targets) depend on Phase 8 (already measured — see the
Feature Development Process chapter — but not yet cross-referenced from
this chapter's non-functional-requirements framing).
