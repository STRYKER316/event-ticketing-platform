# Class Diagrams

*Status: draft, Event Service (Phase 1), Search Service (Phase 2), Booking
Service (Phase 3), and Payment Service (Phase 4) evidence so far.*

## Event Service — Manager + Repository per feature

```mermaid
classDiagram
    class EventManager {
        -_session: AsyncSession
        -_events: EventRepository
        -_venues: VenueRepository
        -_performers: PerformerRepository
        -_seat_maps: SeatMapRepository
        -_producer: EventProducer
        +list_events(limit, offset, sort_field, sort_order) EventListResponse
        +get_event(event_id) EventResponse
        +get_seat_map(event_id) SeatMap
        +upsert_seat_map(user, event_id, payload) SeatMap
        +create_event(user, payload) EventResponse
        +update_event(user, event_id, payload) EventResponse
        +publish_event(user, event_id) EventResponse
        +delete_event(user, event_id) void
        -_republish(event, seat_map) void
        -_fetch_owned_event(user, event_id) Event
        -_resolve_venue(venue_id) Venue
        -_resolve_performers(performer_ids) Performer[]
        -_apply_update(event, payload) void
        -_check_cannot_delete_published(event) void
    }

    class VenueManager {
        -_venues: VenueRepository
        +get_venue(venue_id) VenueResponse
        +create_venue(payload) VenueResponse
    }

    class BaseRepository~ModelT~ {
        -_session: AsyncSession
        -_model: type[ModelT]
        +create(instance) ModelT
        +get_by_id(instance_id) ModelT
        +get_many_by_id(instance_ids) ModelT[]
        +delete(instance_id) bool
    }

    class EventRepository {
        -_session: AsyncSession
        +create(event) Event
        +get_by_id(event_id) Event
        +list(limit, offset, sort_field, sort_desc) Event[]
        +count() int
        +delete(event_id) bool
    }

    class VenueRepository {
        +list(limit, offset) Venue[]
    }

    class PerformerRepository

    class SeatMapRepository {
        -_collection: MotorCollection
        +upsert(seat_map) void
        +get_by_event_id(event_id) SeatMap
        +delete(event_id) void
    }

    class EventProducer {
        -_producer: AIOKafkaProducer
        -_topic: str
        +publish_upserted(event, seat_map) void
    }

    BaseRepository <|-- VenueRepository
    BaseRepository <|-- PerformerRepository

    EventManager --> EventRepository
    EventManager --> VenueRepository
    EventManager --> PerformerRepository
    EventManager --> SeatMapRepository
    EventManager --> EventProducer
    VenueManager --> VenueRepository
```

## Why this shape

This is the lightweight day-job pattern this project deliberately adopted
(Internal per-service layering, `CLAUDE.md`): **Manager + Repository per
feature**, not the full Manager/Logic/DBManager split. `EventManager` folds
the reference pattern's "which Logic class handles this call" routing job
and the "the actual business rules live here" structuring job into one
class, because Event Service has no dispatch problem — every use case here
is single-path, unlike Booking Service's dual hold strategy. The
structuring discipline the reference `execute()` method enforced is kept
internally: `update_event()` and `delete_event()` both read as named,
sequenced private steps (`_fetch_owned_event` → mutate → commit) rather
than one unstructured block, even without a separate class boundary for
it.

`EventManager` depends on four repositories and one producer, not one
repository — it is the one Event Service class that talks to both
datastores (Postgres via three repositories, MongoDB via
`SeatMapRepository`) and to Kafka (via `EventProducer`), because seat-map
fetches are logically part of the event-detail use case even though the
data lives in a different database, and publishing is a side effect of
the same mutations the repositories already commit. Repositories stay
single-datastore, single-model, and business-rule-free by design —
`EventRepository` doesn't know what "ownership" means, `EventManager` does.
`_producer` is typed `EventProducer | None` — `None` for the read-only and
plain-`create_event` paths that never publish, a real instance injected
into every route that does (§7.1).

`VenueRepository` and `PerformerRepository` inherit shared create/
get_by_id/get_many_by_id/delete boilerplate from a generic
`BaseRepository[ModelT]` — both models needed identical CRUD with nothing
model-specific beyond their SQLAlchemy type, so the duplication was
extracted once it showed up in two places (a Phase 1 review-gate
simplification, not part of the original per-feature design).
`EventRepository` deliberately does **not** inherit it: it always
eager-loads `venue`/`performers` via `selectinload` on every fetch, which
is model-specific enough that forcing it through the generic base would
either weaken the base or special-case it — not worth it for one
repository.

**Status:** Implemented, Tested — reflects the actual class structure under
`services/event-service/app/logic/` and `app/db/` as of Phase 3 (Phase 1
base + the P1 addendum's `upsert_seat_map`/`create_venue` + Phase 2's
`EventProducer` wiring + Phase 3's `_check_cannot_delete_published`, which
replaced the never-implemented `_check_no_bookings` stub once Booking
Service existed and gave the stub something concrete to resolve against),
not a target design.

## Search Service — Manager + Repository, adapted for a non-SQL store

```mermaid
classDiagram
    class SearchManager {
        -_index: EventIndexRepository
        +search(query, limit, offset, sort_field, sort_order) SearchResponse
        -_to_item(hit) SearchResultItem
    }

    class EventIndexRepository {
        -_client: AsyncElasticsearch
        +ensure_index() void
        +upsert(event_id, document) void
        +delete(event_id) void
        +search(query, limit, offset, sort_field, sort_desc) tuple
    }

    class EventConsumer {
        -_consumer: AIOKafkaConsumer
        -_repository: EventIndexRepository
        +run() void
        -_handle(raw) void
    }

    SearchManager --> EventIndexRepository
    EventConsumer --> EventIndexRepository
```

## Why this shape

Search Service has no Postgres or MongoDB of its own — Elasticsearch is
explicitly not a source of truth (§8) — so `EventIndexRepository` plays the
same role `EventRepository` plays in Event Service (query/write methods
only, no business rules) against a different kind of store: an ES index
instead of a SQL table. The Manager + Repository shape doesn't change; only
what's underneath the Repository does. This is the same pattern extension
`CLAUDE.md`'s Conventions section now documents explicitly, so the next
service without its own database (Notification Service, per decisions-log
§4) doesn't have to rediscover it.

`EventConsumer` has no Manager above it — deliberately. Per the per-service
layering decision, a Manager exists to give a multi-step use case a
structured, named-step shape; an ES upsert-or-delete by ID *is* the whole
operation, with nothing to sequence above it. Both `SearchManager` (serving `GET /search`) and `EventConsumer` (serving
the Kafka integration point) depend on the same `EventIndexRepository`, so
there is exactly one place that knows how to talk to Elasticsearch, even
though the two entry points (HTTP route, Kafka message) never share a
Manager — matching the "handlers construct the same `{Feature}Manager` the
API routes use" convention's spirit of one business-logic path per
datastore, just without a Manager in the middle on the consumer side.

**Status:** Implemented, Tested — reflects the actual class structure
under `services/search-service/app/logic/`, `app/db/`, and `app/kafka/` as
of Phase 2.

## Booking Service — Manager + Repository, plus the one Strategy interface in this codebase

```mermaid
classDiagram
    class BookingManager {
        -_session: AsyncSession
        -_tickets: TicketRepository
        -_bookings: BookingRepository
        -_hold_strategy: TicketHoldStrategy
        -_events: EventRepository
        -_cancelled_producer: BookingCancelledProducer
        +create_booking(user, ticket_id) BookingResponse
        +pay_booking(user, booking_id, bearer_token, http_client) BookingPayResponse
        +cancel_booking(user, booking_id) BookingResponse
        -_fetch_bookable_ticket(ticket_id) Ticket
        -_acquire_hold(ticket) void
        -_create_booking_row(user, ticket) Booking
        -_build_response(booking) BookingResponse
        -_fetch_owned_pending_booking(user, booking_id) Booking
        -_charge_via_payment_service(booking, ticket, bearer_token, http_client) BookingPayResponse
        -_fetch_owned_confirmed_booking(user, booking_id) Booking
        -_check_before_event_start(booking) void
        -_transition_and_release(booking) void
    }

    class TicketRepository {
        -_session: AsyncSession
        -_model: type~Ticket~
        +get_by_id(ticket_id) Ticket
        +bulk_upsert_available(event_id, seats) int
    }

    class BookingRepository {
        -_session: AsyncSession
        -_model: type~Booking~
        +expire_stale_pending(older_than_seconds) int
        +transition_if_pending(booking_id, new_status) bool
        +transition_if_confirmed(booking_id, new_status) bool
        +list_confirmed_ticket_ids(event_id) set~UUID~
    }

    class EventRepository {
        -_session: AsyncSession
        +upsert_start_time(event_id, start_time) void
        +get_start_time(event_id) datetime
    }

    class BookingCancelledProducer {
        -_producer: AIOKafkaProducer
        -_topic: str
        +publish_cancelled(booking_id) void
    }

    class ProvisioningConsumer {
        -_consumer: AIOKafkaConsumer
        -_session_factory: async_sessionmaker
        +run() void
        -_handle(raw) void
        -_write_tickets(event_id, seats) int
    }

    class PaymentOutcomeConsumer {
        -_consumer: AIOKafkaConsumer
        -_session_factory: async_sessionmaker
        -_redis: Redis
        -_notification_producer: NotificationProducer
        +run() void
        -_handle(raw) void
        -_transition_with_retry(message, new_status) bool
        -_publish_confirmation_with_retry(booking_id) void
    }

    class NotificationProducer {
        -_producer: AIOKafkaProducer
        -_topic: str
        +publish_booking_confirmed(booking_id) void
    }

    class TicketHoldStrategy {
        <<interface>>
        +acquire_hold(ticket_id, ttl_seconds) bool
        +release_hold(ticket_id) void
        +is_held(ticket_id) bool
        +confirm_hold(ticket_id) void
        +release_booking(ticket_id) void
    }

    class CronHoldStrategy {
        -_session: AsyncSession
        +acquire_hold(ticket_id, ttl_seconds) bool
        +release_hold(ticket_id) void
        +is_held(ticket_id) bool
        +confirm_hold(ticket_id) void
        +release_expired() int
        +release_booking(ticket_id) void
    }

    class RedisHoldStrategy {
        -_redis: Redis
        +acquire_hold(ticket_id, ttl_seconds) bool
        +release_hold(ticket_id) void
        +is_held(ticket_id) bool
        +confirm_hold(ticket_id) void
        +release_booking(ticket_id) void
    }

    class FakeHoldStrategy {
        -_held: set~UUID~
        -_lock: asyncio.Lock
    }

    TicketHoldStrategy <|.. CronHoldStrategy
    TicketHoldStrategy <|.. RedisHoldStrategy
    TicketHoldStrategy <|.. FakeHoldStrategy

    BookingManager --> TicketRepository
    BookingManager --> BookingRepository
    BookingManager --> TicketHoldStrategy
    BookingManager --> EventRepository
    BookingManager --> BookingCancelledProducer
    ProvisioningConsumer --> TicketRepository
    ProvisioningConsumer --> EventRepository
    PaymentOutcomeConsumer --> BookingRepository
    PaymentOutcomeConsumer --> TicketHoldStrategy
    PaymentOutcomeConsumer --> NotificationProducer
```

## Why this shape

`BookingManager` follows the exact same Manager + Repository shape as
`EventManager`/`SearchManager` — with one addition: `TicketHoldStrategy`,
the strategy-pattern interface `CLAUDE.md`'s Internal per-service layering
decision already anticipated as the one place this codebase genuinely
needs to branch between alternate execution paths (§6). Every other
feature in every service is single-path, which is exactly why none of
them needed a second dispatch mechanism; the dual hold mechanism is the
one place a real architectural choice (cron sweep vs. Redis TTL) has to
be swappable at runtime, via one `HOLD_STRATEGY` config value, without
`BookingManager` or the Phase 8 benchmark harness ever branching on which
is active. `FakeHoldStrategy` is the third implementation — not a
production path, but the in-memory stand-in that lets `BookingManager`'s
own unit tests run with neither real strategy's infrastructure present,
per P3.T3's explicit "trivial fake/no-op implementation" requirement.

**Hold-state storage is deliberately asymmetric between the two real
implementations, despite satisfying an identical interface** — this is
the one design decision in this diagram that isn't visible from the
method signatures alone, so it's worth stating here rather than only in
code comments. `CronHoldStrategy`'s hold state *is* `Ticket.status` /
`Ticket.hold_expires_at` in Postgres: `acquire_hold` is a single atomic
conditional `UPDATE ... WHERE status = 'available'`, the same
rowcount-checked-under-concurrency pattern already used to fix
`BaseRepository.delete()`'s TOCTOU race in the pre-Phase-3 hardening pass
— two concurrent transactions serialize on Postgres's row lock, and the
loser's `WHERE` clause re-evaluates false once the winner has committed.
`RedisHoldStrategy`'s hold state lives *only* in Redis (`SET NX EX`) and
deliberately never writes `Ticket.status` at all — the Redis *key* releases
itself via its own TTL expiry, with no sweep needed for that half of the
picture. `Ticket.status` only reflects live hold state under the cron
strategy; anything needing to know "is this seat currently held" must ask
the active strategy's `is_held()`, never read `Ticket.status` directly. This
is a documented trade-off, not an oversight — the same reasoning the project
already applied to the async-provisioning eventual-consistency window (§7,
§26): state the trade-off plainly rather than hide it.

**The Redis strategy still needs a sweep — just for a different row than the
cron strategy's.** `BookingManager` creates a `Booking` row (status
`PENDING`) the moment a hold is acquired, under either strategy (§21) — and
Redis's key expiry knows nothing about that Postgres row. Found during this
phase's dedicated review: an abandoned hold under `HOLD_STRATEGY=redis`
correctly freed the Redis key, but left the `PENDING` Booking row in place
forever, and `uq_bookings_active_ticket` (a partial unique index treating
`PENDING`/`CONFIRMED` as "active") then permanently blocked any future
booking on that seat — the two strategies were not actually behaviorally
equivalent, which would have quietly undercut the Phase 8 benchmark
comparison. Fixed with `BookingRepository.expire_stale_pending()`, an
age-based sweep (`created_at` vs. `hold_ttl_seconds`, since there's no
`Ticket`-side expiry column to key off of under this strategy) run on its
own APScheduler job — `hold_sweep.py` now branches on the active
`HOLD_STRATEGY` and schedules whichever sweep applies, rather than only
existing for the cron strategy as originally built.

`ProvisioningConsumer` has no Manager above it, for the same reason
`EventConsumer` (Search Service) doesn't: nothing else writes `Ticket`
rows from a Kafka payload, so there is no second entry point to unify a
Manager's dispatch job with (per `CLAUDE.md`'s consumer-with-no-API-route
exception). It calls `TicketRepository` directly. Idempotency is
structural, not a checked condition: `bulk_upsert_available` is one
`INSERT ... ON CONFLICT DO NOTHING` statement per message keyed against
the `(event_id, section, row_name, seat_label)` unique constraint, so a
redelivered message matches zero rows on its second attempt rather than
needing an explicit "have I seen this before" branch. `bulk_upsert_available`
and `CronHoldStrategy.release_expired()` both batch their SQL through a
shared `chunked()` helper (`app/db/chunking.py`) rather than each
reimplementing the same bind-param-limit chunking loop — a large seat map or
a large backlog of expired holds would otherwise overflow Postgres's
~32,767-bind-param cap in a single statement. `_write_tickets`, the private
method that owns the actual DB write, retries a transient failure in place
(bounded, with backoff) before `_handle` gives up on a message and the
consumer commits past it — `enable_auto_commit` is deliberately `False` on
the underlying `AIOKafkaConsumer` so a genuine process crash mid-write is
always safely redelivered, independent of whether a caught-and-retried
failure was ultimately given up on or not.

**Status:** Implemented, Tested, Verified (live) — reflects the actual
class structure under `services/booking-service/app/logic/`, `app/db/`,
and `app/kafka/` as of the Phase 3 checkpoint, including the fixes from
both this phase's review passes (see the Testing Strategy chapter's "two
review passes" section for the full list). All three `TicketHoldStrategy`
implementations proven against the identical shared contract test suite
(`tests/unit/test_hold_strategy_contract.py` for the fake,
`tests/integration/test_hold_strategy_contract.py` for cron and Redis
against real Postgres/Redis via `testcontainers`); the concurrency claim
itself (20-25 concurrent clients racing one seat, exactly one wins) proven
under both real strategies in `tests/integration/test_concurrency_suite.py`
*and* live against the running stack via 20 concurrent `curl` requests
through Traefik. Verified live against the running stack, both
`HOLD_STRATEGY` values in turn: publishing a real event through Event
Service provisions real `Ticket` rows (12/12 seats), `POST /bookings`
returns a `PENDING` booking with the seat held, and a second booking
attempt on the same seat cleanly returns 409 under both strategies. The
Redis-sweep fix specifically was verified live end-to-end: booked and
abandoned a seat under `HOLD_STRATEGY=redis` with a short TTL, confirmed
the seat stayed blocked while the Redis hold was live, waited past TTL +
sweep interval, confirmed `hold_sweep_expired_stale_redis_bookings` in the
logs, then confirmed a fresh booking on the same seat succeeded — the
class-diagram claim above about the Redis sweep is not just a code-reading
claim, it's a reproduced-and-confirmed-fixed one.

**Phase 4 additions**: `BookingManager.pay_booking` and `TicketHoldStrategy
.confirm_hold` close two gaps the Phase 3 diagram above left open —
"Booking Service fronts payment" (decisions-log §9 amendment) means the
paying client never talks to Payment Service directly, and
`TicketStatus.BOOKED` had been checked by `_fetch_bookable_ticket` since
Phase 3 but never actually *set* by any code path until this phase gave
`confirm_hold` a real implementation per strategy (`CronHoldStrategy`
transitions `HELD`→`BOOKED`; `RedisHoldStrategy`, which never writes
`Ticket.status` at all, just deletes the now-superseded Redis key).
`PaymentOutcomeConsumer` follows `ProvisioningConsumer`'s exact shape
(direct-repository access, no Manager above it, since nothing else drives
this transition; `enable_auto_commit=False` plus a bounded DB-write retry)
and reuses `BookingRepository.transition_if_pending` — a single
conditional `UPDATE ... WHERE status = 'pending'` — to make redelivery a
structural no-op the same way `bulk_upsert_available`'s `ON CONFLICT DO
NOTHING` does for provisioning, rather than a checked "have I seen this
before" branch. **Status:** Implemented, Tested, Verified (live) — see the
Payment Service section below for the synchronous call this feeds and the
live verification both `confirm_hold`/`transition_if_pending` branches
received.

**Phase 6 additions**: `BookingManager.cancel_booking` and a genuine third
`TicketHoldStrategy` method, `release_booking` — not a `release_hold`
reuse, despite decisions-log §22's original phrasing suggesting one. A
`CONFIRMED` booking's ticket is `BOOKED`, and `release_hold`'s conditional
`UPDATE` only ever matches `HELD`, so the two methods target different
source states even though both end at `AVAILABLE`. `release_booking`
carries the same deliberate asymmetry the acquire/confirm methods already
have: `CronHoldStrategy` does a real `UPDATE ... WHERE status = 'BOOKED'`;
`RedisHoldStrategy` is a documented no-op, since that strategy never
writes `Ticket.status` at all — correctness against re-booking a cancelled
seat comes from `uq_bookings_active_ticket` no longer matching once
`Booking.status` flips to `CANCELLED`, not from any Ticket-table write.
`EventRepository` is new and deliberately not a `BaseRepository`
subclass — `Event`'s primary key is `event_id`, not `id`, and this table
exists solely so `cancel_booking` has a `start_time` to enforce §22's
"before the event starts" cutoff against, populated by
`ProvisioningConsumer` from the same Kafka message that already
provisions tickets (no new integration point). **A missing `Event` row
fails the cutoff check closed (409), not open** — found at CHECKPOINT: the
original version treated "no start time on record" as "before the
cutoff," silently, for any booking whose event predates this table (real
and reachable — two events in the dev stack). See the Testing Strategy
chapter's Phase 6 CHECKPOINT section for the full finding and live
verification both directions. `BookingCancelledProducer`
is Booking Service's first-ever Kafka producer (integration point #5,
§22) — the same thin `send_and_wait` wrapper shape `PaymentOutcomeProducer`
already established on the Payment Service side, described below.
`cancel_booking` publishes *before* committing, the same publish-before-commit
ordering the Phase 4 CHECKPOINT review established for
`handle_webhook_event` — a publish failure must propagate uncommitted so
the whole cancel request fails cleanly and is retryable, rather than
stranding a `CANCELLED` booking whose refund trigger never reached Payment
Service. **Status:** Implemented, Tested, Verified (live) — see the
Payment Service section's own Phase 6 additions for the refund half of
this flow.

**Phase 5 additions**: `PaymentOutcomeConsumer` gained a `NotificationProducer`
dependency and a new private method, `_publish_confirmation_with_retry`,
completing integration point #3's producer side on the Booking Service end
(`booking_confirmed`, alongside Payment Service's own `payment_confirmed`
and `refund_failed` paths). This one is deliberately **not** shaped like
`cancel_booking`'s publish-before-commit call above — a code-review finding
caught that the original version put the notification publish *inside*
`_transition_with_retry`'s retried DB-transaction closure, following that
same convention, and that was wrong here specifically: `_run_with_retry`
(used by both `ProvisioningConsumer` and this consumer) deliberately
*swallows* a permanent failure after its bounded retries and returns
`None` rather than raising, so a persistently-failing notification publish
would have silently rolled back an already-successful booking confirmation
(and hold-strategy `confirm_hold`) while the caller still committed the
Kafka offset — losing the payment-outcome message while leaving the
booking `PENDING` despite a completed charge. The publish-before-commit
convention is safe at `cancel_booking` and Payment Service's webhook route
specifically because a failure there propagates out of a request handler
and triggers real redelivery (Stripe's webhook retry, or the request
simply failing); `_run_with_retry`'s own give-up-and-move-on design means
no equivalent redelivery exists inside a Kafka consumer's retried unit.
Fixed by moving the publish to a separate step *after* `_transition_with_retry`
returns, gated on `transitioned and message.action is SUCCEEDED` — the DB
transition is committed and treated as the source of truth first, and the
notification is a best-effort step afterward (its own small bounded retry)
that cannot undo it. **Status:** Implemented, Tested, Verified (live) — a
real `pay` success through the full HTTP/Kafka path produced a real
`notification_delivered` log line with `action=booking_confirmed`,
`attempt=1`, in Notification Service's own logs; see the Testing Strategy
chapter's Phase 5 section for the two-round CHECKPOINT review that found
this and two other issues.

## Payment Service — Manager + Repository, plus the system's one synchronous inter-service call

```mermaid
classDiagram
    class PaymentManager {
        -_session: AsyncSession
        -_payments: PaymentRepository
        +create_charge(payload) PaymentResponse
        +handle_webhook_event(event, producer, notification_producer) void
        +refund_payment(booking_id, notification_producer) void
        -_create_pending_payment(payload) Payment
        -_submit_to_stripe(payment, payload) void
        -_submit_refund_to_stripe(payment, notification_producer) void
        -_build_response(payment) PaymentResponse
    }

    class PaymentRepository {
        -_session: AsyncSession
        -_model: type~Payment~
        +get_by_booking_id(booking_id) Payment
        +get_by_stripe_charge_id(stripe_charge_id) Payment
        +transition_if_pending(stripe_charge_id, new_status) bool
        +transition_to_succeeded(stripe_charge_id) bool
    }

    class PaymentOutcomeProducer {
        -_producer: AIOKafkaProducer
        -_topic: str
        +publish_outcome(payment) void
    }

    class NotificationProducer {
        -_producer: AIOKafkaProducer
        -_topic: str
        +publish_payment_confirmed(booking_id) void
        +publish_refund_failed(booking_id, reason) void
    }

    class BookingCancelledConsumer {
        -_consumer: AIOKafkaConsumer
        -_session_factory: async_sessionmaker
        +run() void
        -_handle(raw) void
        -_refund_with_retry(booking_id) void
    }

    PaymentManager --> PaymentRepository
    PaymentManager --> PaymentOutcomeProducer
    PaymentManager --> NotificationProducer
    BookingCancelledConsumer --> PaymentRepository
```

## Why this shape

Same Manager + Repository shape as every other service — `PaymentManager`
has exactly one repository and one producer dependency, the smallest of the
four services' Managers, because a `Payment` row's whole lifecycle
(`pending` → `succeeded`/`failed`) is driven by exactly two entry points it
owns directly: the charge-initiation call and the webhook. No consumer
class exists on this side — Payment Service is a Kafka *producer* for
integration point #4, not a consumer of anything.

**`create_charge` is meant to be called by Booking Service, not by a
browser client** (decisions-log §9 amendment) — the one deliberate, narrow
exception to "cross-service data only via Kafka" in this system, made
because initiating a charge needs an immediate request/response result
(did Stripe accept the attempt right now), which is a different shape of
problem than the eventually-consistent facts the five Kafka integration
points (§7) carry everywhere else. `PaymentManager` itself has no idea
it's being called synchronously by another service rather than a route
handler acting on a browser request — the ownership check that makes this
safe lives entirely in `BookingManager.pay_booking`, on the other side of
that call, where `booking_db` actually is (§8). Idempotency here is a
defense-in-depth belt-and-suspenders pair: `create_charge` checks
`existing.stripe_charge_id is not None` before short-circuiting (a Payment
row with no `stripe_charge_id` yet means a previous attempt never actually
reached Stripe and must genuinely retry — a real bug caught by live testing
this phase, not a hypothetical, see `build-log.md`'s 2026-08-17 entry), and
Stripe's own `idempotency_key` (the booking ID) is the backstop if two
requests somehow race past that check simultaneously.

**A genuine authorization bypass in that design was found at CHECKPOINT,
not by self-verification** — the routine `/pre-pr` code-review pass, not
the initial self-verification, is what caught it. "`PaymentManager` trusts
the caller already did the ownership check" is only actually true if
`/payments/charge` is *unreachable* except from Booking Service — and it
wasn't: Traefik's original `PathPrefix('/payments')` rule routed the whole
service publicly, so any authenticated end user could `POST
/payments/charge` directly with an arbitrary `booking_id` and
`amount_cents`, bypassing both checks `BookingManager.pay_booking` exists
to enforce. Fixed by narrowing the Traefik rule to
`PathPrefix('/payments/webhook')` only (`infra/docker-compose.yml`) —
`/payments/charge` is now reachable exclusively over the internal Docker
network, which is how Booking Service already called it. Live-verified
post-fix both directions: `POST localhost/payments/charge` through Traefik
now 404s; the internal call from `booking-service` still succeeds. See
`build-log.md`'s 2026-08-17 CHECKPOINT entry for the full list — this was
the most severe of six findings that session, all fixed before this
checkpoint closed.

**Webhook-driven confirmation is the sole source of truth for a Payment's
terminal status** (§9) — `create_charge`'s synchronous Stripe response is
never trusted for that, even though Stripe test mode often resolves a
`PaymentIntent` synchronously, because a lost synchronous response after
Stripe already processed the charge would otherwise be indistinguishable
from a genuine failure. `handle_webhook_event` is idempotent the same way
Booking Service's Kafka consumers are (§7's general rule, applied to a
webhook instead of a Kafka redelivery): a rowcount-gated conditional
`UPDATE` (`PaymentRepository.transition_if_pending`, mirroring
`BookingRepository`'s method of the same name) only transitions a `Payment`
still `pending`, so a replayed webhook — or two genuinely overlapping
deliveries racing each other — can't both win it, proven by a concurrency
integration test opening two independent sessions and racing the same
delivery. The original version used a read-then-write check instead
(found in the same CHECKPOINT review) and also committed the terminal
status *before* publishing to Kafka, which meant a publish failure could
strand a Payment permanently — Stripe's own retry would hit the
already-terminal guard and silently no-op, losing the outcome for good.
Fixed by reordering to publish before commit, so a publish failure
propagates uncommitted and Stripe's retry genuinely gets another attempt.

**Status:** Implemented, Tested, Verified (live, except the final real-Stripe
leg — see below) — reflects the actual class structure under
`services/payment-service/app/logic/`, `app/db/`, and `app/kafka/` as of
Phase 4. Live-verified against the real running stack: a real
venue/event/seat-map created and published through the organizer API
carried `price_cents` through Kafka into a real `Ticket` row; `/pay`'s
403/404/409 paths and the full synchronous call chain into Payment
Service's own auth check and a genuine HTTPS call to Stripe all verified
live (failing only at Stripe's own `401 Invalid API Key`, since no real
Stripe test-mode credentials were available this session — a real, tracked
gap, not silently marked done); the idempotency-retry bug above was found
and fixed via this same live testing; integration point #4 (`payment
.outcomes`) verified live end-to-end for both outcomes by producing
directly to the topic (bypassing Stripe, since the mechanism under test is
the Kafka consumer, not Stripe's delivery): `succeeded` → `Booking`
`CONFIRMED` + `Ticket` `BOOKED`; `failed` → `Booking` `EXPIRED` + `Ticket`
`AVAILABLE` immediately (not waiting for `HOLD_TTL_SECONDS`); redelivering
the same `failed` message produced no second log line and no second effect.
`POST /payments/webhook` with an invalid signature verified live to reject
with 400 before touching any `Payment` row.

**Phase 6 additions**: `PaymentManager.refund_payment` and Payment
Service's first-ever Kafka consumer, `BookingCancelledConsumer`
(integration point #5, §22). No API route drives a refund — the
`booking.cancelled` Kafka message itself is the authorization (Booking
Service already checked ownership before publishing it), so this consumer
calls `PaymentManager` directly, the same "no equivalent API route" shape
`ProvisioningConsumer` and `PaymentOutcomeConsumer` already use.
`refund_payment`'s idempotency gate deliberately mirrors `create_charge`'s
existing "resubmit only if the provider-side ID column is still `NULL`"
pattern (`stripe_refund_id is None`) rather than a rowcount-gated status
transition like `handle_webhook_event`'s — a genuinely concurrent
redelivery race isn't reachable here the way it was for the webhook route,
since this consumer processes one Kafka partition's records strictly
sequentially, so only crash-then-restart redelivery is possible, and a
retried Stripe call with the same `{booking_id}-refund` idempotency key
(§9's pattern, applied to refunds) is already safe by construction. On a
`stripe.error.StripeError`, `refund_payment` does not roll anything
back — `Payment.status` stays `SUCCEEDED` (§22's explicit no-re-lock
scope boundary) — and publishes to a new `NotificationProducer`, the
producer side only of integration point #3 (Notification Service itself
doesn't exist until Phase 5, which runs after this phase in the locked
build order; Kafka producer and consumer are independently deployable, the
same trade-off already accepted for integration point #2's eventual
consistency). **Status:** Implemented, Tested, Verified (live, except a
real Stripe refund succeeding) — live-verified through the real HTTP/Kafka
path end to end: a real cancel through `POST /bookings/{id}/cancel`
produced a real `booking.cancelled` message, consumed by
`BookingCancelledConsumer`, which reached a genuine
`POST https://api.stripe.com/v1/refunds` call to Stripe's actual API
(failing only at the placeholder-key boundary, 401, same expected failure
mode as Phase 4's charge flow — not a bypass), correctly triggered the
refund-failure branch, and published to `notifications` — verified
directly with a throwaway `kafka-console-consumer`, message shape correct.
Redelivery verified live too: since the first attempt's refund never
actually succeeded (`stripe_refund_id` stayed `NULL`, the only reachable
outcome without real Stripe credentials), a hand-crafted redelivery
correctly *retried* the refund rather than silently no-op'ing — exactly
the resubmission-gate semantics documented above; the "already-refunded
redelivery is a no-op" case is proven in the automated integration suite
(`test_refund_payment_replay_against_real_db_does_not_double_refund`),
which mocks a successful Stripe response to reach that state.

**Phase 5 addition**: `handle_webhook_event` gained a third parameter,
`notification_producer`, and now calls `NotificationProducer
.publish_payment_confirmed(booking_id)` — same publish-before-commit
position as the existing `producer.publish_outcome(payment)` call, only on
the `SUCCEEDED` branch — completing integration point #3's producer side
alongside Phase 6's `refund_failed` path (Notification Service itself,
the consumer, now exists — see this file's Notification Service section
below). **Status:** Implemented, Tested, Verified (live) — a real webhook
delivery (self-signed against the locally-configured secret, the same
technique used since Phase 4 given no real Stripe account) produced a real
`notification_delivered` log line with `action=payment_confirmed` in
Notification Service's own logs.

## Notification Service — Manager only, no Repository, no database (Phase 5)

```mermaid
classDiagram
    class NotificationManager {
        +deliver(message, attempt) void
    }

    class SimulatedDeliveryFailure {
        <<exception>>
    }

    class NotificationConsumer {
        -_consumer: AIOKafkaConsumer
        -_retry_publisher: RetryPublisher
        +run() void
        -_handle(raw) void
    }

    class RetryConsumer {
        -_consumer: AIOKafkaConsumer
        -_retry_publisher: RetryPublisher
        +run() void
        -_handle(raw) void
    }

    class DlqConsumer {
        -_consumer: AIOKafkaConsumer
        +run() void
        -_handle(raw) void
    }

    class RetryPublisher {
        -_producer: AIOKafkaProducer
        -_retry_topic: str
        -_dlq_topic: str
        +publish_retry(envelope) void
        +publish_dlq(envelope) void
    }

    NotificationConsumer --> NotificationManager
    NotificationConsumer --> RetryPublisher
    RetryConsumer --> NotificationManager
    RetryConsumer --> RetryPublisher
    NotificationManager ..> SimulatedDeliveryFailure
```

## Why this shape

The one deliberate departure from the Manager + Repository template every
other service follows (`event-service`'s P0.T5 pattern, reused since):
there is no Repository layer and no `db/` folder at all, because
Notification Service has no database of any kind (decisions-log §17
amendment, 2026-08-18) — `NotificationManager.deliver` *is* the delivery
(a structured log line, §19; no real email/SMS provider exists to write
rows about), so there is nothing for a Repository to query or write. Three
consumer classes exist rather than one because they read from three
different topics with three different failure-handling shapes, but they
share one `_consume_with_manual_commit` helper (same manual-offset-commit-
after-handler-finishes discipline every Kafka consumer in this system
uses) and one `_publish_with_retry` helper wrapping their republish calls.

**The retry/DLQ ladder is this phase's design centerpiece, the same way
the dual hold strategies were Phase 3's.** With no database, retry state
(how many attempts so far, the original message, the last error) has
nowhere to live except the Kafka message itself — `RetryEnvelope { attempt,
original, last_error }`, round-tripped through three topics:
`notifications` → `notification-retry` → `notification-dlq`.
`NotificationConsumer` attempts delivery once (`attempt=1`); on failure it
publishes a `RetryEnvelope(attempt=2, ...)` to `notification-retry`.
`RetryConsumer` sleeps `compute_backoff_seconds(attempt)` — `min(base **
attempt, cap)` — then retries; on another failure it either republishes
with `attempt + 1` or, once `attempt > retry_max_attempts`, routes to
`notification-dlq` instead. `DlqConsumer` is visibility-only (`§17`
amendment: "not silently dropped," not "automatically retried forever") —
nothing reprocesses out of the DLQ.

**Idempotent by construction, not by an explicit guard**: with no database
row to conditionally update, there is no "have I seen this before" check
to write — a duplicate delivery of the same message is just a duplicate
`notification_delivered` log line, safe by the same reasoning
`bulk_upsert_available`'s `ON CONFLICT DO NOTHING` gives structural
idempotency elsewhere in this system, just without a unique constraint
doing the work. This is tested, not just asserted:
`test_redelivery_of_same_message_is_a_safe_no_op` publishes the same
key/value twice and asserts two independent `notification_delivered` log
entries with no corrupted or duplicated retry-ladder entry, per this
project's rule that Kafka-consumer idempotency gets explicitly tested.

**Since this service has no real external delivery dependency capable of
a genuine failure** (§19 — log output only, no SendGrid/etc.), the retry
ladder is proven with a deliberate, honestly-documented demo/test
instrument: `Settings.simulated_failure_attempts`, mirroring the same
precedent already established for the Hold-Mechanism Benchmark's simulated
immediate-release trigger (§17, Phase 8). Set to `0` in the baseline
compose file — with an explicit comment that it must never be left
non-zero there — and overridden only for a live demo container or a test.

**A two-round CHECKPOINT `/pre-pr` code review found and fixed four real
bugs**, none caught by the first pass of self-verification: (1)
`compute_backoff_seconds` computed `base ** attempt` before `min()`
capped it, so a sufficiently large `attempt` raised `OverflowError`
instead of being clamped — fixed with a try/except, and `RetryEnvelope
.attempt` was also given a DTO-level bound (`Field(ge=1, le=1000)`) per
this system's validation-boundary convention; (2) the three consumers'
republish calls to `notification-retry`/`notification-dlq` were
unguarded — unlike every other Kafka consumer with a side effect in this
system, which wraps its write in a bounded retry — so a single transient
broker error would have permanently killed the consumer task; fixed with a
`_publish_with_retry` helper mirroring the shape `_run_with_retry` already
established on the Booking/Payment side; (3) the publish-before-commit
ordering bug in `PaymentOutcomeConsumer` described in the Booking Service
section above; (4) a second-order bug the *first* round of fixes
introduced: `RetryEnvelope.last_error`'s new non-blank-string DTO
constraint could itself raise `ValidationError` when constructed from an
exception whose `str()` is empty (`str(KeyError())` is `''`), escaping
every guard just added and killing the consumer anyway — fixed with a
small `_error_text(exc)` helper (`str(exc) or repr(exc)`) at all three
internal construction sites. All four fixed and live re-verified against
the real running stack; see `docs/build-log.md`'s 2026-08-18 CHECKPOINT
entries for the full list, including two findings deliberately left
unfixed as pre-existing and out of this phase's scope (the Redis
hold-strategy's non-transactional `confirm_hold`/`release_hold`, and the
repo-wide "died consumer task only logs, nothing restarts it" pattern
`notification-service` shares with `booking-service` and `payment-service`).

**Status:** Implemented, Tested, Verified (live) — reflects the actual
class structure under `services/notification-service/app/logic/` and
`app/kafka/` as of Phase 5. Live-verified against the real running stack,
both directions: a genuine booking→payment→confirmation flow producing
real `notification_delivered` log lines for both `payment_confirmed` and
`booking_confirmed`; and, using an isolated one-off container with
`SIMULATED_FAILURE_ATTEMPTS=1` so the always-on baseline container stayed
untouched, a real `notification_delivery_failed` → `notification_delivered
_after_retry` recovery sequence with real backoff timing. 11/11 tests green
(5 unit, 6 `testcontainers` integration against a real Kafka broker).

## Booking Service additions (Phase 7)

`TicketRepository` gains `list_by_event(event_id) -> list[Ticket]` (a
plain filtered `SELECT`, no business rule), and `api/bookings.py` gains
one new route, `GET /bookings/events/{event_id}/tickets`, that calls
`BookingManager.list_tickets_for_event(event_id)`, which runs that
Repository query and maps each row to a new `TicketStatusResponse` DTO
(`ticket_id, section, row_name, seat_label, status, price_cents`) — the
same Manager+Repository shape every other `booking-service` route uses,
not the `search-service` `EventConsumer` Repository-direct exception:
that exception applies to a Kafka consumer with no equivalent API route
to unify with, and this new addition *is* an API route, so it gets its
own `BookingManager` method like the rest (found and corrected in this
phase's `/pre-pr` code-review pass — see decisions-log §23's amendment
correction). This is the read half
of the seat-map composition decisions-log §23 describes, which had a
design but no route until this phase — see the Database Schema Design and
Requirement Gathering chapters' Phase 7 sections for the endpoint's public/
no-auth reasoning and the Testing Strategy chapter for how a real
concurrent-booking bug was found through this exact code path (in
`_create_booking_row`, not the new route itself, but exercised by the same
phase's live testing).

**Status:** Implemented, Tested, Verified (live) — `list_by_event` covered
by two new integration tests (real Postgres) and `BookingManager.list_tickets_for_event`'s
DTO mapping by two new unit tests; the route live-verified against the
real running stack, returning correct per-seat status including a real
hold flipping a seat from `available` to `held` between two polls.
