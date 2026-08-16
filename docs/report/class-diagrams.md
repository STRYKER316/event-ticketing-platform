# Class Diagrams

*Status: draft, Event Service (Phase 1), Search Service (Phase 2), and
Booking Service (Phase 3) evidence so far. Payment Service's diagram
(Phase 4) still to come.*

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
        +create_booking(user, ticket_id) BookingResponse
        -_fetch_bookable_ticket(ticket_id) Ticket
        -_acquire_hold(ticket) void
        -_create_booking_row(user, ticket) Booking
        -_build_response(booking) BookingResponse
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
    }

    class ProvisioningConsumer {
        -_consumer: AIOKafkaConsumer
        -_session_factory: async_sessionmaker
        +run() void
        -_handle(raw) void
        -_write_tickets(event_id, seats) int
    }

    class TicketHoldStrategy {
        <<interface>>
        +acquire_hold(ticket_id, ttl_seconds) bool
        +release_hold(ticket_id) void
        +is_held(ticket_id) bool
    }

    class CronHoldStrategy {
        -_session: AsyncSession
        +acquire_hold(ticket_id, ttl_seconds) bool
        +release_hold(ticket_id) void
        +is_held(ticket_id) bool
        +release_expired() int
    }

    class RedisHoldStrategy {
        -_redis: Redis
        +acquire_hold(ticket_id, ttl_seconds) bool
        +release_hold(ticket_id) void
        +is_held(ticket_id) bool
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
    ProvisioningConsumer --> TicketRepository
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
