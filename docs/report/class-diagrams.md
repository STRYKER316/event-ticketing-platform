# Class Diagrams

*Status: draft, Event Service only — Phase 1 evidence. Booking Service's
diagram (Phase 3) will additionally show the `TicketHoldStrategy` interface
— the one place this codebase genuinely branches between alternate
execution paths (see the Internal per-service layering decision in
`CLAUDE.md`); Event Service has no equivalent, every feature here is
single-path.*

## Event Service — Manager + Repository per feature

```mermaid
classDiagram
    class EventManager {
        -session: AsyncSession
        -_events: EventRepository
        -_venues: VenueRepository
        -_performers: PerformerRepository
        -_seat_maps: SeatMapRepository
        +list_events(limit, offset, sort_field, sort_order) EventListResponse
        +get_event(event_id) EventResponse
        +get_seat_map(event_id) SeatMap
        +create_event(user, payload) EventResponse
        +update_event(user, event_id, payload) EventResponse
        +delete_event(user, event_id) void
        -_fetch_owned_event(user, event_id) Event
        -_resolve_venue(venue_id) Venue
        -_resolve_performers(performer_ids) Performer[]
        -_apply_update(event, payload) void
        -_check_no_bookings(event) void
    }

    class VenueManager {
        -_venues: VenueRepository
        +get_venue(venue_id) VenueResponse
    }

    class EventRepository {
        -_session: AsyncSession
        +create(event) Event
        +get_by_id(event_id) Event
        +list(limit, offset, sort_field, sort_desc) Event[]
        +count() int
        +delete(event) void
    }

    class VenueRepository {
        -_session: AsyncSession
        +create(venue) Venue
        +get_by_id(venue_id) Venue
        +list(limit, offset) Venue[]
        +delete(venue) void
    }

    class PerformerRepository {
        -_session: AsyncSession
        +create(performer) Performer
        +get_by_id(performer_id) Performer
        +get_many_by_id(performer_ids) Performer[]
        +delete(performer) void
    }

    class SeatMapRepository {
        -_collection: MotorCollection
        +upsert(seat_map) void
        +get_by_event_id(event_id) SeatMap
        +delete(event_id) void
    }

    EventManager --> EventRepository
    EventManager --> VenueRepository
    EventManager --> PerformerRepository
    EventManager --> SeatMapRepository
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

`EventManager` depends on four repositories, not one — it is the one
Event Service class that talks to both datastores (Postgres via three
repositories, MongoDB via `SeatMapRepository`), because seat-map fetches
are logically part of the event-detail use case even though the data
lives in a different database. Repositories stay single-datastore,
single-model, and business-rule-free by design — `EventRepository` doesn't
know what "ownership" means, `EventManager` does.

**Status:** Implemented, Tested — reflects the actual class structure
under `services/event-service/app/logic/` and `app/db/` as of Phase 1, not
a target design.
