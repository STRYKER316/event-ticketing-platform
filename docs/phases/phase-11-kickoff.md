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

## Blocked on P10 — not started this pass

Left untouched, explicitly, not silently skipped:

- **Deployment Flow chapter** — no content exists; needs P10.T1–T4's real
  evidence (EB config, security groups, budget alert, the deployed smoke
  test, cost writeup). Starts once P10 runs.
- **Abstract** (P11.T1) — written last, once every other chapter
  (including Deployment Flow) is in its final state.
- **Conclusion's takeaways/applications** (P11.T3) — needs the finished
  report to take stock of. The Limitations/Future Work half (§26's
  pull-list) could reasonably start now since it doesn't depend on P10,
  but is left for the follow-up pass alongside the takeaways half so the
  chapter is written in one coherent sitting rather than two disjoint
  ones — revisit this call if the user wants Limitations/Future Work
  drafted independently sooner.
- **P11.T1's "stitch into template" step and all of P11.T4** (formatting
  pass — Times New Roman, 14pt/12pt, margins, per-chapter figure/table
  numbering, 40-page check) — not this repo's job at all, per the
  decisions-log §27 amendment's responsibility split. Hand the finished
  `docs/report/*.md` set to the Claude.ai Project once P11a–d and the
  post-P10 items above are done.
- **P11.T5** (demo script + recorded walkthrough) — the graded demo runs
  against the deployed instance per Phase 10's own goal; starts once P10
  is live.

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

**Next:** Phase 10 — AWS Elastic Beanstalk Deployment (still the next
phase in the locked build order); then the blocked P11 items above,
picked up once P10's evidence exists.
