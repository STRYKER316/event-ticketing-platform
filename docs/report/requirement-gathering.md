# Requirement Gathering

*Status: draft, partial — roles/permissions evidence from Phase 1. The full
functional requirements list (booking, payment, cancellation flows) fills
in as those phases land; this draft covers what's actually enforced in the
codebase today, not aspirational scope.*

## Actors

Two realm roles exist in Keycloak (`ticketing` realm), and every endpoint
in the system is required to declare which of the following it expects —
"no guard needed" is a decision made explicitly per route, never an
omission (Conventions, `CLAUDE.md`):

- **Anonymous / unauthenticated** — can browse: list events, view event
  detail, view a seat map, view venue detail. No token required.
- **`user`** — an authenticated customer. Everything anonymous can do,
  plus (from Phase 3 onward) hold a seat, book, pay, and cancel their own
  booking.
- **`organizer`** — everything `user` can do, plus create, update, and
  delete events they own. A user can hold both roles simultaneously (the
  seed realm's `bob` does); `organizer` is additive, not exclusive.

## Roles/permissions table — Event Service (Phase 1)

| Endpoint | Anonymous | `user` | `organizer` (not owner) | `organizer` (owner) |
|---|---|---|---|---|
| `GET /events`, `GET /events/{id}` | Allow | Allow | Allow | Allow |
| `GET /events/{id}/seat-map` | Allow | Allow | Allow | Allow |
| `GET /venues/{id}` | Allow | Allow | Allow | Allow |
| `POST /events` | 401 | 403 | Allow (becomes owner, `DRAFT`) | — |
| `POST /events/{id}/publish` | 401 | 403 | **403** | Allow (`DRAFT`→`PUBLISHED`) |
| `PATCH /events/{id}` | 401 | 403 | **403** | Allow |
| `DELETE /events/{id}` | 401 | 403 | **403** | Allow |
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

## What this chapter still needs

Functional requirements for booking (seat hold semantics, dual hold
strategy, no-double-booking guarantee), payment (Stripe integration,
idempotent webhook handling), cancellation/refund, and notification
delivery are not yet written — they depend on Phases 3–6, which this draft
does not cover. Non-functional requirements (the Hold-Mechanism Benchmark's
throughput/latency targets) depend on Phase 8.
