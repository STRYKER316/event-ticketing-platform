# event-service

Owns event and venue data (Postgres `event_db`) plus seat maps (MongoDB). Source of
truth for event catalog and organizer-managed inventory; publishes changes for
`search-service` to index via Kafka.

Not yet implemented — placeholder for Phase 0 scaffolding.
