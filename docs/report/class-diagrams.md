# Class Diagrams

*Status: draft, Event Service (Phase 1) and Search Service (Phase 2)
evidence so far. Booking Service's diagram (Phase 3) will additionally show
the `TicketHoldStrategy` interface — the one place this codebase genuinely
branches between alternate execution paths (see the Internal per-service
layering decision in `CLAUDE.md`); neither service below has an equivalent,
every feature in each is single-path.*

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
        -_check_no_bookings(event) void
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
        +publish_deleted(event_id) void
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
`services/event-service/app/logic/` and `app/db/` as of the pre-Phase-3
checkpoint (Phase 1 base + the P1 addendum's `upsert_seat_map`/
`create_venue` + Phase 2's `EventProducer` wiring), not a target design.

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
