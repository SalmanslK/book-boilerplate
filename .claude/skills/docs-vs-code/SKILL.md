---
name: docs-vs-code
description: >
  Bidirectional audit between the codebase and the documentation we maintain.
  Neither side is assumed correct: every mismatch is classified, weighed against
  evidence, and reported before anything is changed. Produces an audit report and
  stops for approval. Use before a release, after a run of merges, or when the docs
  are suspected of drifting. To refresh docs from code you already trust, use
  docs-update-from-code instead.
---

You are auditing whether this repository's **code and its documentation agree**.

This is not a documentation refresh. **Neither side is presumed correct.** Code
carries bugs, half-finished work and things nobody meant to keep; documentation
carries yesterday's design, tomorrow's intention, and decisions that were reversed in
a chat thread. The job is to find where they disagree, work out which one is wrong,
and say so with evidence.

**Your first deliverable is a report, not a commit.**

---

## What counts as documentation here

**Only what we write and maintain:**

| | |
|---|---|
| `docs/system-overview.md` | what the product is, its vocabulary |
| `docs/architecture.md` | how we build, and why |
| `docs/project.md` | where we are — features, status, requirements history |
| `docs/open-questions.md` | every question the requirements left open, open and answered |
| `docs/defects.md` | what is wrong, and what happened to it |
| `docs/plans/*.md` | one design per feature |
| `CLAUDE.md`, `docs/CLAUDE.md` and any nested `CLAUDE.md` | the rules |
| `README.md` and any other how-to-run file the project keeps | how to run it |
| Doc comments and inline comments | they make claims too, and they go stale |

**`requirements/` is never edited.** The requirements and every supporting document
there are **source material** — snapshots of documents we do not own. They are
evidence in this audit — often the deciding evidence — but they are read-only. If the
code contradicts the requirements, the finding is against the code or against a
question in `docs/open-questions.md`, never against the snapshot.

**Generated docs** (listed under `[guard.generated]` in `devtools/factory/factory.toml`,
and in `CLAUDE.md`) are fixed by regenerating, never by hand. A generated doc that
disagrees with the code means the generator was not run — say so.

`docs/archive/` is historical by definition. Do not report it as stale; check only
that it is still marked as superseded. `docs/Audit/` belongs to `fsd-audit` and
`docs/factory/runs/` to the factory's referee — read them as evidence, do not edit
them.

---

## Phase 1 — Discovery

Read, do not assume. The point of the audit is that memory and documentation are
exactly the things under suspicion.

**Code:** the source roots (`[impact].source_roots` in `factory.toml`, or the layout
table in `CLAUDE.md`) — the domain model, the use cases or handlers, the entry points
(endpoints, commands, jobs, screens), persistence and migrations, integrations,
configuration and its validation, seed data, the dependency manifest, the CI
pipeline, container and deployment files.

**Tests:** every suite `CLAUDE.md` names — unit, architecture or convention, and
integration. Where test names describe behaviour, read them; they state intended
behaviour more plainly than most comments.

**Documentation:** the files listed above, in full.

---

## Phase 2 — Compare

**Read `references/comparison-checklist.md`** and work through it. It lists what to
compare, where drift tends to happen, and the rule about measuring numbers rather
than carrying them forward.

That file is shared with `docs-update-from-code`, which walks the same ground and differs
only in what it does on arrival. **This skill owns it. Add a new concern there, not
here**, so both skills get it.

---

## Phase 3 — Classify every mismatch

| Type | Meaning | Usual action |
|---|---|---|
| **A** | Documentation right, code wrong | Change the code |
| **B** | Code right, documentation stale | Change the documentation |
| **C** | Both unclear or self-contradictory | Needs a decision |
| **D** | Code, documentation and tests all disagree | **Escalate — never resolve silently** |
| **E** | Implemented, documented nowhere | Add documentation |
| **F** | Code does something no requirement or decision explains | Flag; do not rationalise it |

---

## Phase 4 — Weigh the evidence

In this order. When a higher source contradicts a lower one, **say so** rather than
quietly picking a winner.

1. **The requirements** — the highest `V<n>` snapshot of the source `CLAUDE.md` names
   as the source of truth. They win every conflict, including against supporting
   documents (designs, plans from the client, QA cases). Where they are *silent*, the
   supporting documents are the best evidence available. Where they contradict
   *themselves*, precedence does not help: that is a question.
2. **Answered questions in `docs/open-questions.md`.** An answer with a date settles
   the matter and outranks any design written before it.
3. **A feature plan's decisions** — the design as built, each with its *Why*. How the
   code came to depart from an earlier design is in `docs/archive/`.
4. **Integration and architecture tests.** They run against real infrastructure and
   a real host, so they are strong evidence of intended behaviour. Tests a factory run
   tagged with a criterion and proved are evidence of that criterion.
5. **The domain and application code**, where it is consistent with itself.
6. **Database constraints and migrations.**
7. **Unit tests.**
8. **Code comments**, which are assertions and can be false — a comment claiming a
   constraint that does not exist is a common find.
9. **Historical notes.**

**Never invent a requirement.** If a behaviour exists and nothing explains why, that
is Type F, not an opportunity to write a rule that matches the code.

**Do not delete history — archive it.** Correct the current statement in place and
move the old one, verbatim, to the matching file in `docs/archive/` (see
`docs/CLAUDE.md`). What must not survive is history *presented as current*.

---

## Phase 5 — Risk

**Critical** — anything that leaks or corrupts data across tenants or users, a wrong
authorization rule, a public contract mismatch, incorrect database documentation
someone would act on, a deployment instruction that would fail or destroy data.

**High** — a business rule implemented differently from the requirements, wrong
validation, wrong status transitions, a feature plan that misdescribes what shipped.

**Medium** — architecture documentation that no longer matches the layout,
terminology drift, an incomplete flow.

**Low** — formatting, duplication, wording.

---

## Phase 6 — Report, then stop

Deliver:

**1. Executive summary** — totals by risk and by type, and how many need a human
decision rather than an edit.

**2. The findings table.**

| ID | Area | Documentation says | Code does | Tests say | Type | Source of truth | Action | Confidence |
|---|---|---|---|---|---|---|---|---|

**3. Per finding**, with file paths and line numbers on both sides, the conflict
stated in one sentence, the recommendation, the reason, and a confidence of High,
Medium or Low.

**4. The synchronisation plan**, grouped into *code changes*, *documentation
changes*, and *needs a decision*.

**5. A decision list** — `AUD-001 → Update Code`, one line each.

**Then stop.** Do not begin changing things. A trivially obvious Type B — a stale
count, a renamed file — may be fixed in passing and listed as done; anything touching
behaviour, contracts, schema or a business rule waits for approval.

---

## Phase 7 — Apply what is approved

**Code changes** keep the existing architecture, boundaries and data integrity.
Update or add tests in the same commit. A confirmed Type A that is a defect gets a
`docs/defects.md` row the day it is confirmed.

**Documentation changes** describe only what the code, schema, contracts, tests and
configuration actually do. Do not write intent as though it were behaviour.

Respect the repository's own rules while doing it: **one topic, one owner** — put the
fact in the document that owns it (the table in `CLAUDE.md`) and link from anywhere
else; **no new markdown file** unless no owner covers the topic; a question goes to
`docs/open-questions.md` rather than being answered by guesswork in a plan.

Afterwards, run the build, the tests the change can break, and a link check, and
report the numbers rather than asserting success.

---

## The rule that matters most

**Analyse first. Change later.**

An audit that quietly edits until everything matches has destroyed the evidence that
anything was ever wrong. Being right about *which* side was wrong is the entire value
of the exercise.
