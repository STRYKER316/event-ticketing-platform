# Payment Service

Owns `payment_db` (Postgres): payment records for the Stripe test-mode
charge flow. No shared tables with `booking_db` or `event_db` — cross-service
data only arrives via Kafka, with one narrow documented exception (see
below).

**Booking Service fronts payment** (decisions-log §9 amendment,
2026-08-17): the client never calls this service directly. It calls Booking
Service's ownership-scoped `POST /bookings/{id}/pay`, which makes a
synchronous call to this service's charge endpoint, forwarding the caller's
JWT and the ticket's already-known `price_cents`. This is the system's only
synchronous inter-service call — everywhere else, cross-service data moves
over Kafka.

Idempotency key for a charge is the booking ID (§9) — a retried charge
attempt for the same booking never double-charges. Confirmation is
webhook-driven: a successful or failed Stripe webhook publishes a message to
the `payment.outcomes` Kafka topic (integration point #4), which Booking
Service consumes to confirm the booking or release the hold immediately.

**Phase 6 additions:** this service's first-ever Kafka consumer,
`BookingCancelledConsumer`, subscribes to `booking.cancelled` (integration
point #5, §22) — no equivalent API route, the Kafka message itself is the
authorization, since Booking Service already checked ownership before
publishing it. `PaymentManager.refund_payment` issues a Stripe refund
(idempotency key `{booking_id}-refund`, the same pattern §9 uses for charges),
gated by `stripe_refund_id is None`, the same resubmission-gate shape
`create_charge` already uses. On a Stripe failure, `Payment.status` stays
`SUCCEEDED` (no re-lock, no rollback — this is the explicit scope boundary §22 sets) and a
message publishes to a new `notifications` topic — producer only this
phase, since Notification Service (its consumer) doesn't exist until
Phase 5.

## Local Stripe webhook forwarding

Stripe's servers can't reach a local machine directly (decisions-log §24).
Use the Stripe CLI to forward webhook events to this service through Traefik:

```
stripe listen --forward-to localhost:<traefik-port>/payments/webhook
```

Set `STRIPE_WEBHOOK_SECRET` to the signing secret the CLI prints on start.
