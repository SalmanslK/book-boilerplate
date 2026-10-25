---
name: fsd-audit
description: >
  Use when someone asks whether the code matches the requirements, whether a plan
  captured everything the requirements ask for, or whether a built feature is
  traceable requirement by requirement to code and tests — before a promotion or
  release, after a feature is marked Built, when QA or another team report "the spec
  says X", or to build or refresh the traceability matrix under docs/Audit/. Scoped
  to one feature or the whole project. Not for reviewing a branch or a pull request,
  and not for docs-versus-code drift (docs-vs-code).
---

You are auditing whether **the requirements, the plan and the code say the same
thing**, and whether that can be *shown* — requirement by requirement, with a file
and a line on every claim. This is a **compliance and traceability audit**, not a
code review: quality, performance, style and security are someone else's skill
unless they change what the requirements asked for.

**The requirements are immutable here. They win every conflict, and this skill never
edits them.** Everything this skill writes goes under **`docs/Audit/`** and nowhere
else. It is the **only** writer of `docs/Audit/`.

**Evidence or UNVERIFIED.** Never "implemented", "covered" or "compliant" from a
related feature existing, a plan saying so, or a comment claiming it.

---

## Configuration

Read `[requirements]` in `devtools/factory/factory.toml`:

- **`id_prefix`** — the prefix of every requirement id (`REQ` by default).
- **`traceability`** — the matrix's path. Empty means the default,
  `docs/Audit/spec-traceability.md`. The other two audit files sit beside it.
- **`defect_logs`** — where defects live (`docs/defects.md` by default). Cite from
  them; never write to them.

---

## Inputs, and where they are

| | Where | Read it how |
|---|---|---|
| **The requirements** | `requirements/`, as snapshots named `<name> - V<n>.<ext>` — **the highest `n` numerically** (`ls \| sort -V`; a lexical sort puts `V9` above `V11`). `requirements/read-me.txt` names each source | Extract to text in the scratchpad (a PDF-to-text tool, or read the file directly). Quote section numbers **and** heading text; where the numbering is garbled in the export, say so and cite page + heading — never adopt the plan's reading of the requirements' structure |
| **Plan(s)** | `docs/plans/<feature>.md`; find them through `docs/project.md` Part 1 (feature → requirement section → plan → status) | What the plan says the requirements ask, its open questions, the *Why* lines in the design sections, and anything it records as deliberately not built are the load-bearing parts; build history is in `docs/archive/plans-history.md` |
| **Answered questions** | `docs/open-questions.md` — a dated answer outranks any plan text written before it | *Resolved* holds one line per answered question, by topic — the rule in force; the full record is in `docs/archive/`. `docs/project.md` Part 3 for what moved between revisions of the requirements |
| **Code** | The source roots (`[impact].source_roots` in `factory.toml`, or the layout table in `CLAUDE.md`) | Trace the real path from the entry point (endpoint, command, UI action, job) through the logic to storage |
| **Tests** | The test projects or folders `CLAUDE.md` names | A test is evidence only if it exercises the behaviour named in the requirement |
| **Factory runs** | `docs/factory/runs/R-nnn.md`, where one exists for the feature | See *Factory runs as evidence* below |
| **Existing audit** | `docs/Audit/` — the three files below, if present | Update, never recreate |
| **Adjacent owners** | `docs/defects.md`, `docs/open-questions.md`, and any QA suite or coverage document the project keeps (see `CLAUDE.md`'s ownership table) | **Reference by id; never restate or re-log** |

**Audit a commit, not a working tree.** Record it as `<branch>@<sha>` — the base
branch (`base_ref` in `factory.toml`) unless told to audit another — and if the
checkout you are reading from is on a different branch, say so and confirm it holds
the same commit. A local checkout on someone else's feature branch is not the base.

---

## Factory runs as evidence

Where a factory run exists for the work (`docs/factory/runs/R-nnn.md`):

- Its **Spec** lists acceptance criteria, each with an id — either a requirement id
  (`<id_prefix>-<section>-R<n>`) or the run's own (`R-nnn.AC-<n>`).
- Tests carrying a criterion's id — in the form the `[adapter]`'s `criterion_marker`
  in `factory.toml` matches — are the tests written to prove it.
- The run's **Checks** and **Record** say whether those tests passed, at which commit;
  the logs are under `evidence_dir/R-nnn/`.

That is strong evidence, and it is still evidence of **the criterion**, not of the
requirement. Check that the criterion says what the requirement says before counting
it; a criterion that narrows or rewords the requirement is a pass-1 finding. A run
whose evidence is for a commit other than the one audited says so in the row
(`VERIFIED (observed, R-nnn @ <sha>)`) — later commits may have changed the code.

---

## Scope

Given a feature: its requirement section(s) from `docs/project.md` Part 1, its plan,
its code, its tests. Given the whole project: every row in Part 1 whose status is
Built, one feature at a time, same method. **State the boundary in the matrix
header** — which sections are in, which adjacent ones were left out and why.

**Global sections** — rules the requirements state once for every feature (paging,
search, auditing, error shape) — **bind every feature.** A feature can satisfy its own
section and miss a rule that applies to all of them. They are audited **once, in
their own block**, against the shared mechanism that implements them (a base class, a
middleware, an interceptor, an architecture test), and a proof of the shared mechanism
counts for every feature that goes through it. A feature's block then cites the global
id, and adds a row of its own only where the feature **bypasses or deviates from** the
shared mechanism.

**Requirements this repository does not implement** (another team's screen behaviour,
another system's job) are `NOT_APPLICABLE` **with the reason stated** — and this
repository's half, if any (the data that screen needs), still gets its own row.

---

## Requirement extraction and ids

One row per **atomic, checkable statement** in the requirements: a field, a
validation, a default, a message, a state transition, an authorization rule, a list
column, a sort, a filter. Not one row per paragraph.

**Id scheme:** `<id_prefix>-<section>-R<n>` — `REQ-4.2.1-R08`. Stable for life:
never renumber, never reuse; a requirement that vanishes from the requirements keeps
its row, with its status and the revision that removed it. Plan items are cited by
section (`plan §4`); code by `path:line`; tests by their full name. Ids live in
`docs/Audit/` (and in factory run criteria and test tags) only — the requirements are
never edited to carry them.

---

## The three passes

Run all three. Each finds what the others cannot.

**1 · Requirements → Plan.** For every requirement: is it in the plan, where, and
does the plan's wording *change* it? Flag missing, partial, contradicted, and
interpretations that alter behaviour ("we read X as Y"). A plan's *deliberately not
built* entry with a real landing place is a decision, not a gap; one whose landing
place does not exist is a finding.

**2 · Plan → Code.** For every plan item: the code that implements it, and the test
that proves it. Flag planned-not-built, partial, differs-from-plan, and "plan says
shipped, no evidence".

**3 · Code → Requirements.** Read the feature's code cold and ask *what does this
actually do?* — validators, error messages, state guards, filters, permissions,
defaults. Every behaviour either traces to a requirement, an answered question, or a
plan decision — or it is **`UNTRACED`**. Useful is not the same as specified.

**Evidence order when sources disagree** is `docs-vs-code`'s Phase 4: the
requirements, then dated answers in `open-questions.md`, then the plan's design, then
integration and architecture tests, then code, then schema, then unit tests, then
comments. Say which two disagreed; never quietly pick.

---

## Status vocabulary

| Status | Means | Needs |
|---|---|---|
| `VERIFIED` | Behaviour is in code **and** a test exercises it | code `path:line` + a named test |
| `PARTIAL` | Part of it, or all of it without test evidence | say which half is missing |
| `MISSING` | No implementation and no documented deferral | — |
| `CONTRADICTED` | Code does something different | requirement quote + code line |
| `UNTRACED` | Behaviour with no requirement, answer or plan decision behind it | code line; needs a human |
| `UNVERIFIED` | Could not be determined from what is available | say what would settle it |
| `NOT_APPLICABLE` | Not this repository's to implement | the reason |

**Code existing is not `VERIFIED`.** An endpoint, a field or a validation rule with
no test naming the behaviour is `PARTIAL` with `Test: none`.

**`VERIFIED` and `CONTRADICTED` say how they were established:** `(observed)` when a
test or a run showed the behaviour, `(traced)` when it was derived by reading the code
path. A traced contradiction is a real finding, and the first action on it is a test
that observes it.

---

## Outputs — `docs/Audit/`, and only there

Three files, templates in [`references/audit-templates.md`](references/audit-templates.md):

- **`spec-traceability.md`** (or the path `[requirements].traceability` names) — the
  matrix, one row per id.
- **`spec-compliance-audit.md`** — the report: date, requirements revision, plan
  dates, commit audited, scope, then findings by pass. **No score, rating or
  percentage** — a number hides the row that failed.
- **`spec-audit-findings.md`** — one entry per actionable finding, `SA-<nnn>`, with
  its own lifecycle (`Open` → `Resolved <date>` / `Superseded`).

**Updating an existing audit:** keep the ids, keep history, flip statuses with a date,
mark resolved findings resolved. Nothing obsolete stays presented as current; nothing
current is deleted to make room.

**Boundaries.** A defect already in `docs/defects.md` is cited as `D<n>`, not
re-described. A compliance gap that is *not yet* a defect is a finding here — do not
add it to `defects.md` from this skill; say in the finding that it is a candidate. A
QA case belongs to the project's QA suite document, if it keeps one; an ambiguity in
the requirements is a **question for `open-questions.md`** — the finding names it and
stops; it does not invent the answer.

---

## Final gate

Close every audit with, explicitly: Requirements → Plan (all represented? any
reinterpreted?), Plan → Code (all built? what is missing?), Code → Requirements
(conforming? what is untraced?), Tests (which requirements have no meaningful test
evidence?), and the counts per status. **Never write "fully compliant" while any
material row is `MISSING`, `CONTRADICTED`, `UNTRACED` or `UNVERIFIED`.**

---

## Red flags — stop and re-read the rule

| The thought | What it actually is |
|---|---|
| "Built, just untested" | `PARTIAL`. Untested is unverified |
| "Harmless in practice" | The requirements said something else. `CONTRADICTED` |
| "That reading is defensible" | A reinterpretation. Flag it; the product owner decides |
| "Benign, documented in the plan" | Documented where, against which requirement? If nowhere: `UNTRACED` |
| "The plan's reading of the section numbering" | The plan is under audit. Cite the page and heading |
| "A related feature covers it" | Which requirement, which line, which test? |
| "The factory run passed, so it is verified" | The run proved its criteria. Does the criterion say what the requirement says, and is the code still the commit the run proved? |
| "Not ours" with no reason | `NOT_APPLICABLE` carries its reason or it is `UNVERIFIED` |
| "Close enough" | `CONTRADICTED` beats close enough. `MISSING` beats implied |
| "I'll audit the checkout I'm on" | Name the commit of the base branch |
