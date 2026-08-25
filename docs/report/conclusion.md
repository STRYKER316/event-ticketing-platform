# Conclusion

*Status: Draft — takeaways and real-world applications written now that
every other chapter (through Deployment Flow) is in its final state;
Limitations and Future Work lifted directly from decisions-log §26's
pull-list, each carrying the one-line "why" already written at its
source section rather than re-derived here.*

## Takeaways

Building this system end to end, rather than designing it on paper,
surfaced a few things that are easy to state as principles but only
really land once a real bug forces the point:

- **Database-per-service is only as real as its enforcement.** Every
  service in this system owns its datastore outright, and cross-service
  facts move exclusively through five explicitly-scoped Kafka integration
  points (§7) — with exactly one narrow, deliberate exception (§9
  amendment). Holding that line took active discipline, not just an
  initial design decision: Payment Service genuinely cannot check booking
  ownership itself, which is what forced the one synchronous call to
  exist in the first place, fronted by Booking Service rather than
  reached directly. A boundary that can't be crossed even when it would
  be momentarily convenient is what makes the boundary mean something.
- **"Idempotent consumer" is a testable claim, not a design aspiration.**
  Kafka's at-least-once delivery means every one of this system's
  consumers must treat redelivery as a safe no-op — and the project's
  testing strategy backs that claim with an explicit coverage matrix
  across all five integration points (P9.T1), not an assumption. The
  places this discipline slipped even briefly — a consumer's own DB write
  gated on the wrong commit semantics, a DTO tightening that broke an
  internal call site the same guard was meant to protect — are recorded
  honestly in Testing Strategy and CLAUDE.md's conventions rather than
  quietly fixed and forgotten, because the failure mode (a redelivered
  message doing real damage) is exactly the kind of bug that only shows
  up under production load, not a local demo.
- **Concurrency correctness has to be proven under real contention, not
  argued from the code.** The dual seat-hold mechanism (§6) is this
  project's centerpiece guarantee — selling the same seat twice would
  silently break the entire product. Both hold strategies were held to
  an identical concurrency contract, verified by the same test suite
  racing 25 simulated clients against one seat under each real
  implementation, and the Feature Development Process chapter's Phase 8
  benchmark then measured both under genuine concurrent load rather than
  asserting either was "fine." A correctness claim about concurrent
  systems that hasn't been run under actual concurrent load is not yet a
  verified claim, only a plausible one.
- **A local-first workflow makes cloud deployment a late, thin, and
  therefore honest checkpoint.** Every phase before Phase 10 ran entirely
  against local Docker — real Postgres/MongoDB/Elasticsearch/Redis/Kafka
  via `testcontainers`, not mocks — so Phase 10's job was narrowly to
  prove the already-correct system runs on real infrastructure, not to
  debug application logic against AWS. It still found three real gaps
  (hardcoded `localhost` URLs, a secure-context restriction on
  `crypto.subtle`, EB's flat-bundle requirement) and one real
  operational surprise (an Auto Scaling Group replacing, not pausing, a
  stopped instance) — proof that "it works locally" and "it works
  deployed" are genuinely different claims, each needing its own
  live verification, not one standing in for the other.
- **A locked decisions log is a discipline, not a straitjacket** — it
  held for 27 sections across the whole build, and every genuine
  correction to it (the §7 Kafka-topic broadening, the §9 synchronous-call
  exception, the §12/§13 Auto Scaling Group finding) is recorded as an
  explicit, dated amendment rather than a silent edit, which is what
  keeps the log trustworthy as a record of what was actually decided and
  why, not just what's currently true.

## Real-world applications

The core problem this system solves — reserving a scarce, uniquely
identified resource (a specific seat) under genuine concurrent demand,
without overselling it — is not specific to event ticketing. The same
shape shows up in hotel and short-term rental booking (a specific room,
specific nights), restaurant reservation systems (a specific table, a
specific time slot), airline seat selection, and any flash-sale
e-commerce scenario where a fixed, small inventory faces a demand spike
far larger than the inventory itself. The dual hold-strategy design in
particular generalizes directly: a TTL-based hold (simple, no extra
infrastructure, coarser expiry granularity) versus a distributed-lock
hold (tighter expiry, needs Redis or an equivalent) is a real trade-off
any of those systems would face, and this project's benchmark — not just
its design — is the kind of evidence that trade-off decision should
actually be made against.

More broadly, the event-driven integration pattern here (services owning
their own data, propagating facts via Kafka rather than reaching into
each other's databases) is the same shape used at production scale by
real ticketing and e-commerce platforms specifically because it lets each
service scale, fail, and deploy independently — a booking surge doesn't
require search infrastructure to scale in lockstep, and a notification
outage doesn't block a payment from completing. The one deliberate
exception to that pattern (Booking Service's synchronous call into
Payment Service, §9 amendment) is itself a realistic lesson: not every
cross-service interaction fits the eventually-consistent shape Kafka is
good at, and knowing when a synchronous call is the more honest design —
rather than forcing everything through one integration style for
consistency's sake — is itself a transferable judgment call.

## Limitations

Built, but in a lighter form than a production system would need — each
a deliberate simplification for demo/capstone scope, not an oversight:

- Keycloak runs in dev mode with its embedded database rather than a
  dedicated production-grade Postgres backing store (§5, §12).
- A single Postgres container hosts three logical databases
  (`event_db`, `booking_db`, `payment_db`) rather than fully isolated
  database instances per service (§12).
- Notification Service sends to logs/console rather than a real email
  provider (§19).
- Single-instance EC2 deployment with no load balancer or auto-scaling,
  so no high availability (§12).
- The seat map refreshes via polling rather than a real-time push
  mechanism (§23).
- No card-collection UI — Payment Service always charges against a fixed
  Stripe test payment method rather than accepting real card details,
  since a checkout form was never in this project's scope (§9, §10).
- No confirmation-page refresh persistence — there is no
  `GET /bookings/{id}` route, so the confirmation page renders only from
  the in-memory state a successful checkout hands off via React Router; a
  hard refresh loses the displayed detail even though the underlying
  booking is unaffected and already `CONFIRMED` server-side (§10, Phase 7).
- The synchronous Booking→Payment call for charge initiation is a
  deliberate, narrow exception to "cross-service data only via Kafka"
  (§9 amendment), justified for a request/response action needing an
  immediate result, but it remains the one place this system's
  event-driven purity is intentionally broken.
- `HOLD_STRATEGY` is assumed fixed for a deployment's whole lifetime, not
  safely switchable with in-flight bookings outstanding — a booking
  confirmed under `cron` and later cancelled after a live switch to
  `redis` would leave its ticket stuck `BOOKED`, permanently unbookable
  (found in Phase 6 code review; not fixed, since a real fix would mean
  `RedisHoldStrategy` writing `tickets.status` after all, undoing the
  point of that strategy's design, §6).
- `RedisHoldStrategy.confirm_hold`/`release_hold` are not transactional
  with the corresponding Postgres write — a DB-side rollback after a
  retried-then-abandoned write can leave the Redis key deleted while the
  Postgres row reverts, a narrow inconsistency window specific to the
  `redis` strategy (found in Phase 5 code review, not fixed — a real fix
  needs either a two-phase-commit-style protocol the two stores don't
  share, or restructuring the retry unit in a way not exercised or proven
  this phase).
- A background Kafka consumer task that dies is only logged
  (`critical`), not restarted, and no `/healthz` check reflects it —
  across all three services with consumer tasks. A deliberate scope
  boundary (bounded per-message retries are what's meant to keep a task
  alive under transient failures) rather than an oversight — genuine
  process-level supervision was never built.
- Booking Service's `ProvisioningConsumer`/`PaymentOutcomeConsumer` drop
  a message permanently (no secondary topic or replay) if bounded DB-write
  retries exhaust, unlike Notification Service's dedicated retry/DLQ
  ladder (§17) — only reachable under a Postgres outage lasting longer
  than the bounded retry window at the exact moment a message is
  processed. Found in a pre-Phase-10 hardening pass; not fixed, since
  building a DLQ for these consumers is new architecture, not a bug fix.
- A narrower race in the same two consumers: if a DB commit succeeds
  server-side but the client-side `await` raises, the retry re-runs
  against a fresh session, finds the row already in its target state, and
  treats that as a no-op — the booking ends up correctly confirmed, but
  the one-time booking-confirmed notification tied to that code path is
  silently skipped. Not fixed, since a real fix needs idempotent
  notification-delivery tracking this system doesn't otherwise have.
- `notification-service`'s `RetryConsumer` processes its retry topic
  strictly serially — one booking's backoff delay blocks every other
  booking's retry queued behind it on the same partition, degrading
  retry throughput under exactly the load the retry ladder exists to
  handle. Not fixed, since per-key concurrent processing is new
  concurrency architecture, not a bug fix.

**Worth naming as an honest trade-off, not a limitation:** a brief window
exists between a newly created event becoming visible in search and its
tickets actually existing, since ticket provisioning is asynchronous via
Kafka (§7). This is normal, expected eventual consistency for an
event-driven design, documented rather than hidden.

## Future Work

Deferred entirely — not built, not claimed as tested:

- A virtual waiting queue (SSE/WebSocket-based) for extreme-demand events
  (§2).
- CDN/edge caching (§2).
- Sharding, read replicas, and multi-region deployment (§2).
- Kubernetes, in place of the Docker/`docker-compose`/Elastic Beanstalk
  approach actually used (§2, §14).
- A CI/CD pipeline, in place of the manual deploy actually used (§19).
- Refund-failure rollback / re-locking a seat that was already released
  before a refund is known to have failed (§2, §22).
- The claim-check pattern, for stadium-scale venue seat data volumes
  beyond what this project's seed data exercises (§2, §7).
- Genuine process-level supervision for a crashed Kafka consumer task,
  and a dead-letter path for Booking Service's own consumers, matching
  Notification Service's existing retry/DLQ ladder — both named as
  limitations above, both real architectural additions rather than bug
  fixes, and both natural next steps if this system moved past capstone
  scope toward production.
