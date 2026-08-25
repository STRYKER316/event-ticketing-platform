# Demo Script

*Status: Verified — this exact script was run live against the deployed
AWS Elastic Beanstalk environment on 2026-08-25, not drafted from the
mechanism alone. Screenshots captured during that run are at
`docs/report/assets/demo/`.*

## Purpose

A reproducible, step-by-step walkthrough of the platform's core customer
flow — browse, log in, hold a seat, pay, confirm — run against the real
deployed instance (`event-ticketing-env.eba-uvwm2tcf.ap-south-1.elasticbeanstalk.com`),
not local Docker. This is the same flow P10.T3's AWS validation run
exercised; this script re-runs it as the graded demo, with a fresh seat so
the two runs don't collide, and documents the exact reproduction steps
rather than only the outcome.

## Prerequisites

- The EB environment must be running. Per §13's corrected stop/resume
  procedure (decisions-log amendment, P10.T4): the instance is normally
  left stopped between sessions with the Auto Scaling Group's
  `HealthCheck`/`ReplaceUnhealthy`/`AZRebalance` processes suspended.
  Starting it for a demo is a plain `aws ec2 start-instances` against that
  same instance — do **not** use the EB console's "Restart app server(s)"
  or recreate the environment, and do not resume the suspended ASG
  processes first (resuming them, then stopping normally, is what caused
  the original P10.T4 incident).
- Allow a few minutes after `start-instances` for the full container stack
  to come back up (Elasticsearch and Kafka are the slowest). Poll
  `GET /events` and `GET /app/` on the CNAME until both return `200`
  before starting the walkthrough — the frontend is served at the `/app/`
  path specifically, not `/`.
- For the payment step to reach a `booked` (not just `pending`) terminal
  state live, forward Stripe test-mode webhooks to the deployed
  environment for the duration of the demo:
  `stripe listen --forward-to http://<eb-cname>/payments/webhook`. If the
  printed signing secret doesn't match the `STRIPE_WEBHOOK_SECRET` already
  set as an EB environment property, update it via `eb setenv` before
  paying (it matched with no change needed in both this run and P10.T3's).

## Walkthrough

1. **Browse events.** Navigate to `http://<eb-cname>/app/`. The seeded
   event catalog loads from the deployed `search-service`, served through
   the deployed frontend and Traefik — no login required yet.
   (`01-browse-events.png`)
2. **Log in.** Click "Log in / Register." This redirects to the deployed
   Keycloak instance on its own port (`:8081`, not proxied through
   Traefik, per §12's network-exposure note) with a real Authorization
   Code + PKCE `S256` challenge in the URL — the exact flow the
   `crypto.subtle` polyfill (§12 amendment) exists to keep working on a
   plain-HTTP public hostname. Sign in as one of the seeded demo users
   (`alice` / `changeme`). (`02-keycloak-login.png`)
3. **Open an event's seat map.** After redirect back to the app, pick any
   event from the catalog. The interactive seat map renders live from
   `booking-service`'s seat-status data. (`03-seat-map.png`)
4. **Hold a seat.** Click any available seat button. This fires a real
   `POST /bookings` against the deployed `booking-service`, and the app
   navigates to Checkout showing the specific seat and its price.
   (`04-checkout.png`)
5. **Pay.** Click "Pay." This calls Booking Service's ownership-scoped
   `POST /bookings/{id}/pay`, which makes the system's one synchronous
   inter-service call into Payment Service (§9 amendment) to charge a
   real Stripe test-mode `PaymentIntent`. The confirmation page renders
   immediately afterward showing `status: pending` — this is expected,
   not a bug: confirmation is webhook-driven, not synchronous (§17).
   (`05-booking-confirmed.png`)
6. **Confirm the webhook landed.** Once Stripe's forwarded events
   (`payment_intent.created`, `payment_intent.succeeded`,
   `charge.succeeded`, `charge.updated`) each return `200` from the
   deployed `payment-service`, the ticket's status flips from `held` to
   `booked`. Verify via
   `GET /bookings/events/{event_id}/tickets` on the deployed environment
   and checking the held seat's `status` field.

## Live run record (2026-08-25)

This script was executed exactly as written above, against
`event-ticketing-env.eba-uvwm2tcf.ap-south-1.elasticbeanstalk.com`, logged
in as `alice`:

- Held seat **General, Row 3, Seat 3-3** on "Wandering Notes: Reunion
  Tour" (`ticket_id 9711c2cd-7d49-4fbc-934b-a96ba6624e45`) — a fresh seat,
  distinct from P10.T3's seat 1-2, so the two validation runs don't
  collide in the same seed data.
- Paid **25.00 USD**; confirmation page showed `status: pending` and
  `Payment ID: 5145a3d2-906b-4182-a8c9-13daaea94120`, immediately after
  the synchronous charge call returned.
- All four forwarded Stripe webhook events returned `200` from the
  deployed `payment-service` within 3 seconds of the charge.
- `GET /bookings/events/54d1c670-9018-4261-ac94-2d1839aeb8f2/tickets`
  confirmed seat 3-3's status as `booked` after the webhook landed —
  the full asynchronous confirmation path, not just a page render.

Five screenshots captured across this run, at
`docs/report/assets/demo/`: `01-browse-events.png` through
`05-booking-confirmed.png`.

## What this script does not cover

Organizer-side flows (create venue, create event, attach seat map,
publish) and cancellation/refund are not part of this customer-facing
demo script — they were live-verified during their own phases (Phase 1,
Phase 6) and are documented in the Testing Strategy chapter, not
re-demonstrated here. This script is scoped to the single flow a grader
watching a live demo is most likely to want to see reproduced end to end:
the core booking transaction this whole project is built around.
