# Report drafts

Continuously-drafted source material for the 40-page capstone report — per
`master-development-plan.md` §16 ("Report-evidence capture map") and the
per-phase DOCUMENT step: each phase drafts the report section its evidence
feeds, *while the material is fresh*, so P11 is an assembly + formatting pass
into the institution's template, not first-drafting from scratch.

**This is source material, not the final formatted document.** Plain
Markdown here; copy/adapt into the actual submission template (Times New
Roman, 14pt headings/12pt body, per-chapter figure/table numbering) at P11.

**Every status label follows CLAUDE.md's integrity rule** — Planned →
Implemented → Tested → Deployed → Measured → Verified. A claim in these
drafts carries the label it actually earned through real execution in this
repo, never an aspirational one.

## Chapter status

| Chapter | Status | Fed by | File |
|---|---|---|---|
| Project Description | Draft (partial) | P0 topology figure, architecture recap | `project-description.md` |
| Requirement Gathering | Not started — blocked on P1.T5 (roles table) | §15/§16/§22 policies + P1.T5 | — |
| Class Diagrams | Not started — blocked on P1.T4 | P1.T4, P3.T1, P4.T1 | — |
| Database Schema Design | Not started — blocked on P1.T2 | P1.T2 (ER + textual), P3.T1, P4.T1 | — |
| Feature Development Process | Not started — blocked on P8 | P8 benchmark (measured), P3 hold-strategy design | — |
| Testing Strategy | Not started — blocked on first `testcontainers` suite (P1.T6) | every phase's test suite | — |
| Deployment Flow | Not started — blocked on P10 | P10.T1–T4 | — |
| Technologies Used | Draft (partial, running list) | every phase's tech | `technologies-used.md` |
| Conclusion (Limitations/Future Work) | Not started — drafted last, P11.T3 | decisions-log §26 pull-list | — |
| Abstract | Not started — written last, P11.T1 | whole report | — |

Update this table every phase alongside the chapter files — it's the one
place that shows report progress at a glance without opening every file.
