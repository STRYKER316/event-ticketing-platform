# Abstract

*Status: Draft — written last, now that every other chapter (Project
Description through Conclusion, including Deployment Flow) is in its
final state, per this chapter's own dependency in `docs/report/README.md`.*

This project is a backend-heavy event ticketing and booking platform,
built as a solo capstone for the MS CS Backend Specialization
(Scaler-Neovarsity x Woolf), following the shape of consumer platforms
like BookMyShow or Ticketmaster: browse events, reserve a specific
numbered seat, pay, and receive confirmation, with cancellation and
refund treated as a first-class flow rather than an afterthought. The
system is a microservices architecture of five independently deployable
backend services — Event, Search, Booking, Payment, and Notification —
each owning its own datastore, communicating almost exclusively through
five explicitly-scoped Kafka integration points rather than shared
databases or synchronous calls, with exactly one narrow, deliberate
exception where Booking Service fronts a synchronous charge-initiation
call into Payment Service because that specific interaction needs an
immediate request/response result, not an eventually-consistent fact.
Authentication and authorization are handled by a self-hosted Keycloak
identity provider issuing real signed JWTs, validated independently by
every service through a shared, once-built dependency, with
ownership-scoped authorization enforced on every mutating endpoint rather
than role checks alone.

The system's centerpiece correctness guarantee is its seat-hold
mechanism: two independently swappable strategies — a cron-swept
Postgres-column hold and a Redis `SET NX EX` distributed lock — sit
behind one common interface, selected by configuration rather than baked
into the code path, and both are held to an identical concurrency
contract proven under real contention (25 simulated clients racing one
seat, exactly one winner, every run) rather than assumed correct from the
code. A standalone Python `asyncio` load harness then measured both
strategies under a genuine 300-request contention burst across three
independent runs each: every run allocated the contended seat pool
exactly correctly (30/30 successful holds, 270/270 correctly rejected as
real `409`s, no double-booking in any run). Reported as a genuinely mixed
result rather than a manufactured clean win, per the project's integrity
rule: hold-acquisition latency (p50 cron 0.431s vs. redis 0.446s) and
passive release latency do not meaningfully distinguish the two
strategies at this benchmark's scale — an apparent tail-latency edge in
a first single-run draft did not reproduce once each strategy was run
three times — while immediate-release trigger time shows a small,
consistent, millisecond-scale edge for cron. A measured trade-off, not a
guess, that a real system choosing between these two mechanisms could act
on.

The full lifecycle is proven with real, non-mocked traffic rather than
assumed from unit tests alone: Stripe test-mode payments with
webhook-driven confirmation as the sole source of a payment's terminal
status, Kafka-driven cancellation and refund, a hand-rolled
retry/dead-letter ladder for notification delivery with no database of
its own, and a minimal React frontend exercised through real browser
sessions, real Keycloak logins, and real concurrent booking races that
found and fixed genuine bugs no synthetic test had caught. Every Kafka
consumer in the system is explicitly required and tested to treat
redelivery as a safe no-op, and the project's decisions log records 27
locked architectural sections plus every deliberate amendment made to
them along the way, including two corrections found only once real AWS
infrastructure was involved: an Elastic Beanstalk environment's Auto
Scaling Group replaces, rather than pauses, a directly-stopped instance
unless its health-check processes are explicitly suspended first, and
three localhost-only assumptions (build-time service URLs, a browser
secure-context restriction on PKCE's cryptography, and a compose-file
build-context layout) had to be fixed before the already-correct system
would actually run on a public address. The finished system is deployed
and live-verified end to end — browse, log in, hold a seat, pay, and
confirm — against a real AWS Elastic Beanstalk environment, not just
local Docker, at a measured total cloud cost of roughly $0.15 for the
deployment and validation session itself.
