# Phase 11 Kickoff — Report Assembly & Demo (content-polishing start, ahead of P10)

**Goal of this phase (master plan Phase 11):** stitch the per-milestone
report drafts into the institution's template, write the Abstract and
Conclusion, run the formatting pass, and produce the demo script + recorded
walkthrough. Most chapters are already drafted per-milestone — this phase
is assembly and gap-filling, not first-drafting, per
`master-development-plan.md`'s own framing.

**This kickoff covers a deliberate partial start only** — see
decisions-log §27's 2026-08-23 amendment. P11's locked entry dep is P10
(Deployment Flow chapter needs P10.T1–T4's evidence; the formatting pass
needs every chapter present), which has not run yet. The user asked to
start P11 *work*, not completion, so this file scopes only the tasks that
genuinely don't depend on P10, and explicitly marks the rest as blocked
rather than silently working around the dependency. A second kickoff pass
(or an addendum to this file) picks up the blocked tasks once P10 closes.

**Split of responsibility (decisions-log §27 amendment):** this repo/
Claude Code session owns content correctness — gap-filling and polishing
`docs/report/*.md` against the real evidence trail (decisions-log,
build-log, live-verified test results), since that evidence trail is
what the Integrity rule requires every claim to trace back to, and it
only exists here. The physical template stitch and formatting pass
(P11.T1's "stitch into template" step, all of P11.T4) is **not** this
session's job — this repo has no copy of the institution's actual
template and Claude Code produces Markdown, not a formatted Word/PDF.
That belongs to the separate Claude.ai Project the user has set up for
this MS program; the finished, gap-free `docs/report/*.md` files get
handed to it once ready.

**Entry deps for the tasks below specifically:** P9 complete (confirmed —
all exit-checklist items checked). P10 explicitly NOT required for any
task this file scopes.

**Pre-read audit (done as part of writing this kickoff doc, not deferred
to the tasks themselves):** checked every chapter's actual current
content against its `docs/report/README.md` status label, rather than
trusting the label at face value — two labels turned out stale, and two
concrete content gaps were found:

- **Project Description** — label is accurate, not stale: the chapter's
  own closing section ("What this section still needs") already names
  the exact gap — Phases 4-6 (payment, confirmation, cancellation/refund,
  the notification retry/DLQ ladder) were built and checkpointed but
  never got their narrative framing here. The underlying mechanism is
  already documented elsewhere (Class Diagrams, Database Schema Design,
  Testing Strategy, `architecture.html`) — what's missing is Project
  Description's own prose account of it, in the same voice as its
  existing Phase 0-3/7 sections.
- **Requirement Gathering** — label says "partial," but all six
  roles/permissions tables (Event, Booking, Payment, Cancellation,
  Notification, the Phase 7 ticket-status route — miscounted as "seven"
  in this doc's original pre-read audit and P11b prompt below, corrected
  during P11b's own review pass) are actually present
  and complete. The chapter's own "What this chapter still needs"
  section names the real remaining gap precisely: the Hold-Mechanism
  Benchmark's throughput/latency targets (already measured, Phase 8)
  aren't yet cross-referenced from this chapter's non-functional-
  requirements framing. Small, well-scoped fix — the "partial" label
  should probably read "draft, one cross-reference outstanding" rather
  than implying missing roles tables.
- **Class Diagrams** and **Database Schema Design** — both labels say
  "partial (— Event Service + Search Service + ...)" enumerating
  services as if incomplete, but both chapters actually cover all five
  backend services already (checked every heading in both files). These
  two status labels are stale, not the content — update them to drop the
  service-enumeration framing.
- **Technologies Used** — label says "partial, running list," accurately:
  Stripe (`stripe-python`) has no dedicated technology entry at all
  (checked — only incidental mentions inside the P9.T2 log-level-audit
  section), despite `payment-service` depending on it since Phase 4. This
  session's Round 9 (`docs/build-log.md`, 2026-08-23) just produced fresh,
  citable live-verification evidence for it (a real `PaymentIntent`
  charge, a real `Refund`, both via `stripe-python`'s async client) — good
  timing to write this entry now rather than let it lag further. AWS/
  Elastic Beanstalk is correctly absent, pending P10.

**No open questions requiring a user decision this task set** — every
item below is a bounded content fix with a clear source of truth already
identified above.

---

## P11a — Project Description: backfill the Phase 4-6 narrative gap

**Prompt to Claude Code:**
> Write the missing Phase 4-6 narrative section into
> `docs/report/project-description.md`, matching the voice and level of
> detail the existing Phase 0-3/7 sections already use (see how the
> Phase 3 paragraph frames Booking Service, for the model to follow).
> Cover: Payment Service fronted by Booking Service's one synchronous
> inter-service call (§9 amendment), webhook-driven confirmation over
> Kafka integration point #4, Cancellation & Refunds (§22, integration
> point #5, the cancellation-cutoff rule), and Notification Service's
> hand-rolled retry/DLQ ladder with no datastore of its own (§17
> amendment). Pull the real "why" for each from its own decisions-log
> section rather than re-deriving it. Update the "What this section
> still needs" closing note to reflect what's now actually covered.
> Update `docs/report/README.md`'s Project Description row once done.

**Done when:** the chapter reads as one coherent narrative from Phase 0
through Phase 7 with no gap, every claim traceable to a real decisions-log
section or build-log entry, and the status label in
`docs/report/README.md` updated to match.

---

## P11b — Requirement Gathering: close the benchmark cross-reference gap

**Prompt to Claude Code:**
> Add the missing cross-reference `docs/report/requirement-gathering.md`'s
> own "What this chapter still needs" section names: tie the
> Hold-Mechanism Benchmark's measured throughput/latency numbers (Feature
> Development Process chapter, Phase 8) into this chapter's
> non-functional-requirements framing. Remove the "still needs" section
> once closed, or replace it with an honest "none remaining" note if that
> reads better structurally. Correct the status label in
> `docs/report/README.md` from "partial" to something that doesn't imply
> missing roles tables — all six are already present.

**Done when:** the benchmark numbers are cited from this chapter with a
citation back to the Feature Development Process chapter, and the status
label accurately reflects the chapter's real completeness.

---

## P11c — Class Diagrams / Database Schema Design: fix the stale status labels

**Prompt to Claude Code:**
> Both chapters already cover all five backend services (verified by
> heading — Event, Search, Booking, Payment, Notification for Class
> Diagrams; `event_db`, seat-map docs, ES index, `booking_db`,
> `payment_db`, Notification's deliberate no-schema note for Database
> Schema Design). Update both rows in `docs/report/README.md` to drop
> the service-enumeration "partial" framing that reads as incomplete —
> replace with an accurate label (e.g. "Draft — all five services
> covered") unless a genuine content gap turns up on a closer read while
> making this fix, in which case fix the content instead of just the
> label and note what was found.

**Done when:** both status labels accurately describe the chapters'
actual completeness, with no misleading enumeration implying missing
services.

---

## P11d — Technologies Used: add the missing Stripe entry

**Prompt to Claude Code:**
> Add a `## Stripe (payment processing)` entry to
> `docs/report/technologies-used.md`, matching the existing entries'
> what/why/real-world-use shape (see the Redis or Kafka entries for the
> model). Cover: test-mode `stripe-python` async client, the
> idempotency-key pattern shared by charges (`create_charge`, keyed on
> booking ID) and refunds (`refund_payment`, keyed on `{booking_id}-refund`),
> webhook-driven confirmation as the sole source of truth for a Payment's
> terminal status (not the synchronous charge response), and — now
> genuinely available — cite this session's real charge-and-refund
> round trip (`docs/build-log.md`'s 2026-08-23 "Ninth testing round"
> entry) as live verification rather than describing the mechanism only
> in the abstract. Update `docs/report/README.md`'s Technologies Used row.

**Done when:** Stripe has its own entry at the same depth as every other
technology in this chapter, citing real evidence (the Round 9 live
verification), not just the mechanism description.

---

## Blocked on P10 — resolved 2026-08-25, once P10 closed

Phase 10 finished 2026-08-24; this follow-up pass ran the day after and
closed every item below except the one explicitly out of scope for this
repo:

- **Deployment Flow chapter** — done. Full topology, the three
  localhost-fix gaps, secrets/security groups, the Budgets alert, the
  validation-run screenshots, the real §12/§13 ASG stop/terminate
  correction, and a Measured cost writeup, all built from P10.T1–T4's
  real evidence. `docs/report/deployment-flow.md`.
- **Abstract** (P11.T1) — done, written last once every other chapter
  (including Deployment Flow and Conclusion) was in its final state.
  `docs/report/abstract.md`.
- **Conclusion's takeaways/applications** (P11.T3) — done, alongside the
  Limitations/Future Work half (§26's pull-list) in one coherent sitting,
  as originally planned above. `docs/report/conclusion.md`.
- **P11.T5** (demo script + recorded walkthrough) — done. The EB instance
  was restarted using the corrected pause/resume procedure (§13
  amendment; same instance ID retained, confirmed via
  `describe-auto-scaling-groups` before and after), a full
  browse→login→hold→pay→confirm round trip was run live against the
  deployed CNAME as `alice` on a fresh seat (distinct from P10.T3's),
  Stripe test-mode webhook delivery confirmed the ticket flipped
  `held`→`booked`, five new screenshots were captured, and the instance
  was stopped again afterward per the standing session-end discipline.
  `docs/report/demo-script.md`; the additional EC2 runtime (≈12 minutes,
  ≈$0.035) is folded into Deployment Flow's cost writeup as a second,
  clearly-labeled line item rather than silently merged into P10's
  original figures.
- **P11.T1's "stitch into template" step and all of P11.T4** (formatting
  pass — Times New Roman, 14pt/12pt, margins, per-chapter figure/table
  numbering, 40-page check) — still not this repo's job, per the
  decisions-log §27 amendment's responsibility split. The finished,
  gap-free `docs/report/*.md` set (now including Deployment Flow,
  Conclusion, Abstract, and Demo Script) is ready to hand to the separate
  Claude.ai Project for that pass.

---

## Phase 11 overall status

- [x] **Phase 11 is complete, for everything this repo owns.** P11a-P11d
      (2026-08-23) plus this repo's entire "Blocked on P10" list
      (2026-08-25 — Deployment Flow, Conclusion, Abstract, the demo
      script and its live-recorded walkthrough) are all done, each with
      real evidence, not just described. The one remaining item —
      P11.T1's literal template stitch and all of P11.T4's formatting
      pass — was never this repo's job (decisions-log §27 amendment) and
      belongs to the separate Claude.ai Project once handed the finished
      `docs/report/*.md` set. See "2026-08-25 — P11 follow-up pass exit
      checklist" below for evidence on the second half specifically.

**Note, 2026-08-23:** a separate, user-requested content-quality pass ran
after the P11a-P11d tasks above closed — a review of all seven then-drafted
report chapters for thinness/depth/redundancy, followed by a
de-duplication pass (five bug narratives previously told in full in more
than one chapter, reduced to one canonical telling each in
`testing-strategy.md`) and a reorganization of `testing-strategy.md` from
a phase-by-phase chronicle into a theme structure. Real work, but not on
this kickoff doc's own task list (P11a-P11d) and not itself part of the
"Blocked on P10" items above — see `docs/build-log.md`'s same-dated "Report
content-quality pass" entry for the full account. Does not change the
unchecked box above.

---

## Phase 11 (partial) exit checklist — for this content-polishing pass only

- [x] P11a — Project Description Phase 4-6 narrative gap closed; status
      label updated. Evidence: three new paragraphs (Payment Service,
      Cancellation & Refunds, Notification Service) in
      `docs/report/project-description.md`, commit `2765c51`; status
      line and "What this section still needs" note updated in the same
      commit.
- [x] P11b — Requirement Gathering benchmark cross-reference added;
      "still needs" section closed; status label corrected. Evidence:
      new "Non-functional requirements" section in
      `docs/report/requirement-gathering.md` citing Feature Development
      Process's real measured numbers, commit `9e0b343`; "still needs"
      replaced with "None remaining."
- [x] P11c — Class Diagrams / Database Schema Design status labels fixed
      (or genuine gaps found and fixed instead, if a closer read surfaces
      any). Evidence: re-read both chapters in full — all five backend
      services genuinely present in both (confirmed by heading), no
      content gap found; status lines and `README.md` rows corrected,
      commit `163c112`.
- [x] P11d — Stripe entry added to Technologies Used, citing Round 9's
      live verification; status label updated. Evidence: new `## Stripe
      (payment processing)` entry in `docs/report/technologies-used.md`
      citing the 2026-08-23 Ninth-testing-round charge-and-refund round
      trip, commit `2c8cef1`; status line and `README.md` row updated.
- [x] Cross-doc staleness sweep across everything this pass touched
      (`docs/report/README.md`'s table stays the single source of truth
      for chapter status — no chapter file's own self-description should
      contradict its README row after these fixes). Evidence: grepped
      `docs/` for the old stale phrasing ("P4-P6 not yet backfilled,"
      the enumeration-style "partial" labels, the seven-table miscount)
      after the review pass's fixes — none found outside this pass's own
      already-corrected text; `master-development-plan.md`'s P11.T2 row
      (real-world-framing polish, still correctly pending) needed no
      change.
- [x] `decisions-log.md` delta check — expect none beyond the §27
      amendment already recorded ahead of this file. Evidence: confirmed
      — no commit in this pass touches `decisions-log.md`.
- [x] `CLAUDE.md` self-update check — expect none; no new convention,
      pure content work. Evidence: confirmed — no commit in this pass
      touches `CLAUDE.md`.
- [x] `/pre-pr`-equivalent review of the diff (docs-only, but still worth
      a review pass given four files change) before considering this
      partial pass done. Evidence: adversarial review subagent (read
      `CLAUDE.md` in full, cross-checked every new claim against
      decisions-log/build-log/actual source) found six real accuracy
      issues — a table-count error, a wrong-layer webhook-signature
      claim, an overbroad idempotency claim, a stale future-tense
      reference, a miscited section, and near-verbatim duplication — all
      fixed, commit `004c1ba`.
- [x] Build-log entry appended for this pass. Evidence: `docs/build-log.md`,
      2026-08-23 "Phase 11 kickoff (partial start)" entry, commit
      `b88a284`.

**Not exit criteria for this file** — deliberately excluded, tracked in
the "Blocked on P10" section above instead: Deployment Flow, Abstract,
Conclusion's takeaways, the template/formatting handoff, and the demo
script. Those get their own follow-up kickoff (or an addendum here) once
P10 closes.

---

## 2026-08-25 — P11 follow-up pass exit checklist

Phase 10 closed 2026-08-24; this pass ran the following day and closed
every item the "Blocked on P10" section above had deferred, except the
one item that was never this repo's job.

- [x] Deployment Flow chapter written from P10.T1–T4's real evidence
      (topology, the three localhost-fix gaps, secrets/security groups,
      the Budgets alert, the validation-run screenshots, the real
      §12/§13 ASG stop/terminate correction, a Measured cost writeup).
      Evidence: `docs/report/deployment-flow.md`, commit `cec77ad`.
- [x] Project Description's missing Phase 10 narrative paragraph added
      (mirroring P11a's P4-P6 backfill); status line now reads "covers
      Phases 0-10, no narrative gap." Evidence: `docs/report/project-description.md`,
      commit `cec77ad`.
- [x] Conclusion chapter written (takeaways, real-world applications,
      Limitations/Future Work lifted from decisions-log §26's pull-list),
      in one sitting as originally planned. Evidence:
      `docs/report/conclusion.md`, commit `cec77ad`.
- [x] Abstract written last, once every other chapter was in its final
      state. Caught and fixed one real accuracy bug during drafting
      itself (a benchmark tail-latency claim that overclaimed beyond
      what `feature-development-process.md`'s own "Honest reading"
      section supports) before any external review ran. Evidence:
      `docs/report/abstract.md`, commit `cec77ad`.
- [x] Adversarial accuracy review (a fresh subagent, not self-review) run
      against all four touched files, cross-checking every number,
      resource name, section citation, and "verified/measured" claim
      against decisions-log.md, the Phase 10 build-log entry,
      phase-10-kickoff.md, and feature-development-process.md. Found and
      fixed one real inconsistency (a decisions-log section count, 26 vs.
      the correct 27); everything else confirmed accurate. Evidence:
      fix applied directly to `docs/report/conclusion.md`, folded into
      commit `cec77ad`.
- [x] P11.T5 — demo script written and its live walkthrough actually run
      against the deployed EB environment, not just described: EB
      instance restarted via the corrected pause/resume procedure (same
      instance ID retained, confirmed before and after via
      `describe-auto-scaling-groups`), a full browse→login→hold→pay→confirm
      round trip run live via Playwright as `alice` on a fresh seat
      (General, Row 3, Seat 3-3 — distinct from P10.T3's seat 1-2),
      Stripe test-mode webhook delivery confirmed (`GET
      /bookings/events/{id}/tickets` showing `booked`), five screenshots
      captured, instance stopped again afterward per session-end
      discipline. Evidence: `docs/report/demo-script.md` and
      `docs/report/assets/demo/`, commit `6462d84`.
- [x] Deployment Flow's cost writeup updated with the demo session's real
      EC2 runtime (≈11m41s, CloudTrail-timestamped, ≈$0.035) as a second,
      clearly-labeled line item rather than merged silently into P10's
      original figures. Evidence: `docs/report/deployment-flow.md`,
      commit `cec77ad`.
- [x] `docs/report/README.md`'s chapter-status table updated for all five
      touched/new rows (Project Description, Deployment Flow, Conclusion,
      Abstract, and a new Demo Script row). Evidence: commits `6462d84`
      and `cec77ad`.
- [x] Cross-doc staleness sweep — grepped for any other doc still
      describing these chapters as "blocked on P10" or "not started."
      None found outside historical build-log entries (correctly left
      alone — past-tense record of what was true when written).
      `architecture.html` checked too: it doesn't track report/demo
      status at all (topology/flow-only), and nothing architectural
      changed this pass, so no update needed there.
- [x] `decisions-log.md` delta check — none expected, none made. This
      pass was content-polishing plus a routine, already-decided AWS
      operational action (the corrected stop/start procedure §13 already
      documents), not a new architecture or scope decision.
- [x] `CLAUDE.md` self-update check — none expected, none made. No new
      convention, command, or structural change this pass.
- [x] Build-log entry appended for this pass. Evidence:
      `docs/build-log.md`, 2026-08-25 "Phase 11 follow-up pass" entry.

**Next:** the finished, gap-free `docs/report/*.md` set (Project
Description through Demo Script, all in their final state) is ready to
hand to the separate Claude.ai Project for the template stitch and
formatting pass (P11.T1's literal stitch step, all of P11.T4) — the one
remaining Phase 11 item, and never this repo's job per the decisions-log
§27 amendment.
