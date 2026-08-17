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

## Local Stripe webhook forwarding

Stripe's servers can't reach a local machine directly (decisions-log §24).
Use the Stripe CLI to forward webhook events to this service through Traefik:

```
stripe listen --forward-to localhost:<traefik-port>/payments/webhook
```

Set `STRIPE_WEBHOOK_SECRET` to the signing secret the CLI prints on start.
