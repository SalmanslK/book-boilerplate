# Docs — rules for writing documentation

Loaded when you work under `docs/`. Which file owns which topic is the table in the
root [`CLAUDE.md`](../CLAUDE.md#where-knowledge-lives); this file is how to write
them.

## Current state, not history

**A live document says what is true now.** How it got that way is in git, in a
commit message, or in [`archive/`](archive/).

- **No history inside a current statement** *[enforced, for headings and struck-through
  text: `check-book.py`]*. No `~~old~~ new`, no "was X until
  2026-09-14", no "Corrected <date>", no changelog section in a plan or guide. Change
  the statement; if the reason stops someone re-introducing the old design, keep one
  short *Why:* sentence without the story.
- **Closed items move to `archive/`, and leave a one-line stub** so every id and
  anchor still resolves:

  | Live file | Stays live | Moves to the archive when closed |
  |---|---|---|
  | [`defects.md`](defects.md) | Open rows in full; a one-line index entry per fixed id | The full row → [`archive/defects-closed.md`](archive/defects-closed.md) |
  | [`open-questions.md`](open-questions.md) | Open questions in full; one line per answered question under *Resolved*, by topic, stating the rule in force | The full section → [`archive/questions-resolved.md`](archive/questions-resolved.md) |
  | [`project.md`](project.md) Part 1 | Each feature's status, owner and open items, one line | Build history, PR stories, triage → [`archive/project-history.md`](archive/project-history.md) |
  | [`project.md`](project.md) Part 3 | The latest FSD and wireframe comparison | The one it replaces → [`archive/requirements-history.md`](archive/requirements-history.md) |
  | A plan in [`plans/`](plans/) | The design as built | Build history → [`archive/plans-history.md`](archive/plans-history.md) |
  | [`architecture.md`](architecture.md) | Open decisions, open TODOs, not-built items | Done rows → [`archive/architecture-history.md`](archive/architecture-history.md) |
  | An FE guide in `fe-integration-guides/` | The contract as built, plus changes the FE may not have consumed yet | Older corrections → `archive/fe-guides-history.md` |
  | `qa-artifact-test-suite.md` | Our position on every case, as of the last trace | What each re-trace changed, and when → `archive/qa-suite-history.md` |
  | A dated audit or report | — | The whole report, once a later one or the owning docs replace it → `archive/<name>-<date>.md` |

  **One archive folder, one history file per live document**, named after it. A live document
  that keeps a dated log needs its row here first — otherwise the log has nowhere to go and
  piles up in the live file. An archive is written once and not maintained. Nothing in it is a
  current rule.
- **State a fact once and link to it.** Two copies drift, and the stale one is
  believed. **Counts live only where the rows are** — never copy "8 open defects" into
  prose — and never write down which FSD version is the current one; the
  highest-numbered file in `requirements/` is.
- **Wrong documentation is worse than none.** Change behaviour, fix the owning doc in
  the same commit.

## Which branch a doc change goes on

A doc change the work causes stays on the work's branch — its plan, a defect row, a doc its change
made wrong. **A general refresh goes to `main` on a `chore/docs-<slug>` branch of its own**, cut from
`origin/main` with `--no-track`, holding nothing else: a trim, a consistency or book:check-docs fix to
docs the work did not touch, a broad refresh from code, and new specification snapshots with what they
answered. Every branch then takes the same refresh from `main`, and two branches never rewrite the
same doc differently.

## New files

Do not create a markdown file for a topic an owner already covers. The routine new
files are exactly: **a feature's plan** (copy [`plans/_template.md`](plans/_template.md)),
**that feature's FE integration guide**, and **a QA build's release notes**.
Anything else is a decision to raise first. `project.md` and `open-questions.md` grow a
section; they do not spawn a sibling (the `archive/` split above is the one
exception).

## Defects

The ledger is [`defects.md`](defects.md).

- **A row the day a defect is confirmed**, from any source — not the day it is fixed.
  A finding raised only in a pull request dies when the PR merges.
- **A missing requirement is a defect once we claimed the feature was built**;
  before that it is scope.
- **A defect surprised us; a trade-off is a thing we chose.** A question for the BA
  goes to `open-questions.md`, a QA case to the QA suite's own tracker, an unbuilt FE
  endpoint to `api/fe-api-coverage.md`, a deliberate compromise to the architecture
  guide's Known trade-offs.
- **Every open row carries a Status:** `Open` · `Mitigated` · `Partial` ·
  `Blocked — <Q id or party>`. Nothing else *[enforced: `check-book.py`, pre-commit + CI]*.
- **Keep a row's text short** — what is wrong, where, and the FSD section it breaks,
  in about 600 characters. The investigation goes in the commit or the PR.
- **Every row ends with its class and the day it was found:** *Class: api-contract ·
  found 2026-10-01* — the kind of mistake, so a kind that keeps coming back can be
  turned into an enforced rule ([twice becomes a test](../tests/CLAUDE.md#what-a-change-must-prove)).
  The archived full row carries it; the one-line *Fixed* entry does not. One class:

  | Class | The mistake |
  |---|---|
  | `spec-divergence` | Built differently from the FSD or a BA answer, or against an older revision |
  | `api-contract` | Response shape, field, status, error code, media type or OpenAPI declaration differs from the documented contract |
  | `input-validation` | Malformed, missing or out-of-range input answers 500 or binds silently; a rule missing on one path |
  | `settings-deploy` | Settings and options validation, environment overlays, deployment, migrations as deployed, scripts, logging that hides failures |
  | `test-gap` | A product behaviour has no test, or a test passes without proving its claim |
  | `ci-test-infra` | CI gates, test infrastructure and fixtures, hooks, generators and dev tooling |
  | `docs-drift` | Docs, FE guides, request collections or comments contradict the code |
  | `concurrency` | Check-then-act without a lock or transaction, unique-index 500s, ordering of commits |
  | `access-control` | Permissions against the FSD, tenant scoping, sessions, rate limits, exposed secrets or data |
  | `soft-delete-integrity` | Delete guards, cascades, and reads meeting deleted or filter-hidden rows |
  | `code-convention` | A house or type-design rule broken (`First` unordered, `!`, static state, public setters) |
  | `audit-trail` | Audit stamps missing, set by the wrong actor, or overwritten |
  | `error-handling` | A failure caught, logged or answered wrongly: a 500 for an expected fault, an exception swallowed, a misleading log or message |
  | `test-flake` | A test that passes or fails by timing, order, machine or shared state, not by the code |
  | `performance` | Too slow or too heavy for its use: a missing index, a query per row, an unbounded read or payload |
  | `observability` | A failure nobody can see: no log, metric, alert or trace where one is needed, or one that lies |
  | `other` | A one-off logic bug that fits none of these |

  Older labels map to these: `validation` → `input-validation`, `contract-gap` → `api-contract`,
  `build-drift` → `ci-test-infra`, and `code-gap`, `logic` or missing behaviour → `spec-divergence`.
  A row found on or after `[book] class_enforced_from` in `devtools/factory/factory.toml` carries a
  listed class *[enforced: `check-book.py`, pre-commit + CI]*; older rows keep their label.
- ***Found by* starts with one of these words**, then the detail — *PR review, PR #60*:
  `Review` (our own, before a PR) · `PR review` · `Spec audit` · `Requirements check` ·
  `Test` · `Guard/CI` · `While building` · `QA` · `Consumer` (the front-end team or
  another caller) · `Client` · `Requirement change`. So "who finds our defects" is a
  count, not a reading exercise. Older rows keep their wording; a row found on or after the adoption date (`[book] found_by_from` in
  `devtools/factory/factory.toml`)
  on is held to the list *[enforced: `check-book.py`, pre-commit + CI]*.
- **When it is fixed:** move the full row to the archive with its commit in *Fixed in*,
  and add a one-line entry to the live *Fixed* index. **Never delete a row, never reuse
  an id**, and put the id in the fixing commit's message: on a `fix/*` branch every D id it names is
  in the ledger *[enforced: `check-commit-msg.py`, commit-msg hook + CI]*.
- **Re-verify before marking one fixed.** Open the code; a grep once matched a doc
  comment and reported a missing test as written.
- **A fix must agree with the FSD** — trace it and cite the section in the note. FSD
  silent: use the supporting documents as
  [Which document wins](open-questions.md#which-document-wins) allows, and say which.
  FSD disagrees with the fix, or contradicts itself: ask the module's owner first,
  record the reason and who agreed in the row, and raise an FSD amendment in
  `open-questions.md` if one is needed. Infrastructure, CI, tests and code quality: write "No FSD angle".

## Questions

The ledger is [`open-questions.md`](open-questions.md).

- **Status is one of `OPEN` · `ANSWERED` · `SUPERSEDED`.**
- **Every open question carries a recommendation**, and the recommendation is what
  gets built unless the Answer line says otherwise.
- **Plain words, one closed question, no section numbers** — questions phrased around
  § numbers came back "not sure I understand".
- **When one is answered:** move its full section to the archive and add one line
  under *Resolved*, in its topic, stating the rule now in force. Keep the heading text
  exactly, so its anchor still works.

## Ids — D, Q

`dev` is authoritative; ids are never reused. Two branches taking "the next number"
collide — **before taking one, check the open branches as well as `dev`**, and if a
merge collides, the branch renumbers everywhere it cited the id.

## Plans

One per feature, in [`plans/`](plans/).

- A plan is written before the code, from the template, with a row in
  [`project.md` Part 1](project.md#part-1--features-and-phasing).
- **Its Tasks section (§9 in a feature plan) is the feature's task list** — every task in build order, with its role (`BE`, `FE`,
  `QA`, `BA`, `DevOps`), status and Jira key, drafted from the FSD before the code. A task's
  status changes in the commit that changes it; `project.md`'s *Task progress* and *Open tasks*
  are generated from these tables *[enforced: `gen-task-rollup.py --check`, pre-commit + CI]*.
- **The first line is the status line** — `Status · FSD § · Checked against FSD V<n> ·
  FE guide · Depends on` — kept current.
- **A built plan does not copy the code.** Routes, permissions and request shapes are
  `api/api-contracts.md` (generated); columns are
  `database-erd.md` (generated); status codes, error codes and
  messages are the module's FE guide. The plan keeps invariants, the FSD rules the code
  cannot show, and why.
- Departing from an approved plan is discussed first, and the plan is updated in the
  same commit either way.

## Front-end documents

Three files, three directions — keep them apart:

| File | Tracks |
|---|---|
| `api/fe-api-coverage.md` | **Their requirements** — every endpoint `requirements/fe-api-contracts/` declares, and whether we have it, including "not built". A new contract revision is a new file in `requirements/`: re-extract its endpoints, diff against this matrix, and give every new one a row. Never tidy their paths. |
| `fe-integration-guides/<module>.md` | **Our implementation** of one built module, written from the shipped code, with a *Deviations* section. Updated in the change that alters the API. |
| `fe-integration-guides/api-conventions.md` | What **every** module does the same way — auth, JSON, errors, paging, tenant scoping. A rule for every endpoint goes here, never in a module guide. |

A guide's header carries one line: *Last checked against the code: <date> (<commit>)*, or
*(<branch> <commit>)* to name the branch checked.
What changed for the front end in a build goes in `release-notes/`.

## Generated — never hand-edit

`api/api-contracts.md` (with the Postman collection) and
`database-erd.md` are generated and CI-checked. Fix the generator,
never the output. [`Audit/`](Audit/) is written only by `/book:fact-check`.

## Requirements (`requirements/`, read-only)

- **The FSD wins every conflict.** A design showing something the FSD does not specify
  is not a requirement; where the FSD is silent, the supporting documents are the best
  evidence (the specification itself); where it
  contradicts itself, ask. The table is
  [Which document wins](open-questions.md#which-document-wins).
- **The FSD and the QA artifacts are live Google documents; our copies
  are snapshots.** They move without notice, and the BA answers in the FSD Doc's
  comment threads. A question that looks unanswered may have been answered since the
  last snapshot — run `/book:check-sources`.
- **Permissions** come from the specification.
- **QA artifacts are not requirements.** A rule found there is a question until the
  FSD confirms it.
