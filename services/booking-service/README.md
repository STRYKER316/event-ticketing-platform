# Booking Service

Owns `booking_db` (Postgres): tickets and bookings, including hold state
for the dual hold-mechanism strategy (§6). The double-booking-critical
service — no other service queries this database (§8).

**Phase 3 (in progress):** scaffold, `booking_db` schema/migrations.
`/healthz` and `/metrics` only so far; ticket provisioning, the
`TicketHoldStrategy` interface and its two implementations, and the
booking flow API land later in this same phase.
