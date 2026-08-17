# Booking Service

Owns `booking_db` (Postgres): tickets and bookings, including hold state
for the dual hold-mechanism strategy (§6). The double-booking-critical
service — no other service queries this database (§8).

**Phase 3 complete:** scaffold, `booking_db` schema/migrations, the
idempotent ticket-provisioning Kafka consumer, the `TicketHoldStrategy`
interface with its cron and Redis implementations, and the booking flow
API are all in place, proven against a shared concurrency test suite
(20-25 clients racing one seat under each strategy, exactly one winner).
Hardened via two checkpoint review passes (routine + a dedicated
adversarial `/code-review`, the second catching real bugs in the first
pass's own fixes) — a cron-sweep TOCTOU guard, Kafka consumer offset-commit
semantics plus a bounded DB-write retry, bind-param-safe batching for both
ticket provisioning and the sweep, and a Redis-strategy sweep for
abandoned bookings that the Redis lock's own TTL alone can't reach — see
`docs/build-log.md` and `docs/report/testing-strategy.md` for the full
list.

**Phase 4 additions:** `POST /bookings/{id}/pay` — ownership-scoped,
initiates a Stripe charge via the system's one synchronous inter-service
call to Payment Service (decisions-log §9 amendment), forwarding the
caller's JWT. A second Kafka consumer, `PaymentOutcomeConsumer`, confirms
(`PENDING`→`CONFIRMED`) or releases (`PENDING`→`EXPIRED` + immediate hold
release) a booking based on Payment Service's webhook-driven outcome
(integration point #4). `TicketHoldStrategy` gained `confirm_hold`,
closing a gap where `TicketStatus.BOOKED` was checked but never actually
set by any code path. `Ticket` gained `price_cents`, organizer-set per
section and carried through the Kafka provisioning payload (§9/§16
amendments).
