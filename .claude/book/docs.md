# The Book's rules for writing documentation

The rules every Book keeps for its documents, the same in every project: `docs/CLAUDE.md` imports this
file and holds only the project's own rules beside it. **This file is the Book's** — `book upgrade`
replaces it, and check-book fails when a project's copy differs; a rule of the project's own goes in
`docs/CLAUDE.md`. Which file owns which topic is the table in [the Book's rules](rules.md#where-knowledge-lives).

## Current state, not history

**A live document says what is true now.** How it got that way is in git, in a
commit message, or in [`archive/`](../../docs/archive/).

- **No history inside a current statement** *[enforced, for headings and struck-through
  text: `check-book.py`]*. No `~~old~~ new`, no "was X until
  2026-09-14", no "Corrected <date>", no changelog section in a plan or guide. Change
  the statement; if the reason stops someone re-introducing the old design, keep one
  short *Why:* sentence without the story.
- **Closed items move to `archive/`, and leave a one-line stub** so every id and
  anchor still resolves:

  | Live file | Stays live | Moves to the archive when closed |
  |---|---|---|
  | [`defects.md`](../../docs/defects.md) | Open rows in full; a one-line index entry per fixed id | The full row → [`archive/defects-closed.md`](../../docs/archive/defects-closed.md) |
  | [`open-questions.md`](../../docs/open-questions.md) | Open questions in full; one line per answered question under *Resolved*, by topic, stating the rule in force | The full section → [`archive/questions-resolved.md`](../../docs/archive/questions-resolved.md) |
  | [`project.md`](../../docs/project.md) Part 1 | Each feature's status, owner and open items, one line | Build history, PR stories, triage → [`archive/project-history.md`](../../docs/archive/project-history.md) |
  | [`project.md`](../../docs/project.md) Part 3 | The latest FSD and wireframe comparison | The one it replaces → [`archive/requirements-history.md`](../../docs/archive/requirements-history.md) |
  | A plan in [`plans/`](../../docs/plans/) | The design as built | Build history → [`archive/plans-history.md`](../../docs/archive/plans-history.md) |
  | [`architecture.md`](../../docs/architecture.md) | Open decisions, open TODOs, not-built items | Done rows → [`archive/architecture-history.md`](../../docs/archive/architecture-history.md) |
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

A doc change the work causes stays on the work's branch — its plan, FE guide, defect row, a doc its
change made wrong. **A general refresh goes to the base branch on a `chore/docs-<slug>` branch of its
own**, cut from `base_ref` (`devtools/factory/factory.toml`) with `--no-track`, holding nothing else and opened as a pull request when
the person asks: a trim, a consistency or book:check-docs fix to docs the work did not touch, a broad
refresh from code, and new requirement snapshots with what they answered. Every branch then takes the
same refresh from the base branch, and two branches never rewrite the same doc differently.

## New files

Do not create a markdown file for a topic an owner already covers. The routine new
files are exactly: **a feature's plan** (copy [`plans/_template.md`](../../docs/plans/_template.md)),
**that feature's FE integration guide**, and **a QA build's release notes**.
Anything else is a decision to raise first. `project.md` and `open-questions.md` grow a
section; they do not spawn a sibling (the `archive/` split above is the one
exception).

## Defects

The ledger is [`defects.md`](../../docs/defects.md).

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
- **Every open row carries a Type and a Source** — the kind of defect, and who or what found it, so
  "what keeps going wrong, found by whom" is a count, not a reading exercise *[enforced: `check-book.py`,
  pre-commit + CI]*. Their columns sit beside the Status: `| Id | Sev | Status | Type | Source | Defect |
  Where | Note |`. A kind that keeps coming back becomes an enforced rule
  ([twice becomes a test](tests.md#rule-tests)).

  | Type | The defect |
  |---|---|
  | `spec-divergence` | Built differently from the FSD or a BA answer, or against an older revision |
  | `bug` | Wrong behaviour the FSD does not speak to: a logic error, a crash, a wrong answer |
  | `regression` | Something that worked and broke again |
  | `test-gap` | A behaviour has no test, or a test passes without proving its claim |
  | `security` | Permissions, tenant scoping, sessions, secrets or data exposed |
  | `data` | Wrong, missing or corrupted data, a migration or a seed |
  | `config-infra` | Settings, environments, deployments, servers |
  | `tooling-ci` | The Book's tools, the checks, the hooks, the pipeline |

  The Source is `<source>: <reference>` — *qa: PROJ-310*, *code-review: PR #60*:

  | Source | Found by |
  |---|---|
  | `qa` | QA's testing; the reference is the issue or the test case |
  | `fe` | The front-end team or another caller; the reference is their message or issue |
  | `ba-product` | The BA, the client or the product owner |
  | `requirement-sync` | A new FSD revision or a BA answer read by `/book:check-sources` or `/book:sync-sources`; the version |
  | `code-review` | A self-review or a peer review; the PR or the branch |
  | `audit` | `/book:fact-check` or `/book:check-docs`; the audit row |
  | `tests-ci` | A failing test, a check or the pipeline; the run |
  | `developer` | Found while building; the plan task or the branch |

  A row from before the columns only warns until `book defects classify` proposes its Type and Source
  and the person approves them; the archive's rows and the one-line *Fixed* entries keep what they say.
- **When it is fixed:** move the full row to the archive with its commit in *Fixed in*,
  and add a one-line entry to the live *Fixed* index. **Never delete a row, never reuse
  an id**, and put the id in the fixing commit's message: on a `fix/*` branch every D id it names is
  in the ledger *[enforced: `check-commit-msg.py`, commit-msg hook + CI]*.
- **Re-verify before marking one fixed.** Open the code; a grep once matched a doc
  comment and reported a missing test as written.
- **A fix must agree with the FSD** — trace it and cite the section in the note. FSD
  silent: use the supporting documents as
  [Which document wins](../../docs/open-questions.md#which-document-wins) allows, and say which.
  FSD disagrees with the fix, or contradicts itself: ask the module's owner first,
  record the reason and who agreed in the row, and raise an FSD amendment in
  `open-questions.md` if one is needed. Infrastructure, CI, tests and code quality: write "No FSD angle".

## Questions

The ledger is [`open-questions.md`](../../docs/open-questions.md).

- **Every question has a State**, on one line under its heading, with its fields *[enforced:
  `check-book.py`, pre-commit + CI]*: `**State:** <state> · **Posted:** <date, thread> · **Answered:** <date,
  who by role> · **In FSD text:** <V<n> §x, or not yet> · **Thread resolved:** yes/no` — `—` where a field
  does not apply yet.

  | State | Means | Where it lives |
  |---|---|---|
  | `OPEN · NOT POSTED` | Raised here, not yet asked on the FSD | *Still open* |
  | `OPEN · POSTED` | Asked as an FSD comment; waiting for the BA. Needs **Posted** | *Still open* |
  | `ANSWERED · THREAD ONLY` | Settled in the thread, not yet in the FSD text: ask the BA to add it, keep the thread open, build to the answer. Needs **Answered** | *Answered, waiting for the FSD text* |
  | `RESOLVED` | Settled and in the FSD text. Needs **In FSD text** | the archive, one line under *Resolved* |

  `/book:raise` starts a question as NOT POSTED; `/book:check-sources` moves each one on and lists what to
  post, what to ask to add to the text and which threads to resolve — it posts nothing without the
  person's word. A superseded question says so in its section, `SUPERSEDED by Q<n>`, and moves to the
  archive like a resolved one. A question with no State line only warns, until `/book:check-sources`
  assigns one.
- **Every open question carries a recommendation**, and the recommendation is what
  gets built unless the Answer line says otherwise.
- **Plain words, one closed question, no section numbers** — questions phrased around
  § numbers came back "not sure I understand".
- **When one is RESOLVED:** move its full section to the archive and add one line
  under *Resolved*, in its topic, stating the rule now in force. Keep the heading text
  exactly, so its anchor still works.

## Requirements register

`docs/requirements/<module>.md`, one per module: the FSD's requirements one row each, kept current while the
work is done *[enforced: `check-book.py`, pre-commit + CI]*.

- **One table**, `| Id | FSD | Requirement | Plan task | Tests | Status | Verified at | Note |`, and an
  *Untraced behaviour* table, `| Id | Code | What it does | Status | Note |`, for code no requirement asks for.
  An id is never reused; *FSD* is the section and the versions (`§2.7.1 · V11–V26`); *Verified at* is
  `<commit> <date>`.
- **Statuses:** `NOT_BUILT` · `BUILT` (no proving test yet) · `VERIFIED` (its *Tests* names the test) ·
  `CONTRADICTED` (its *Note* names the defect) · `NOT_BACKEND` · `DEFERRED` (a release or a reason) ·
  `REVERIFY` (the FSD changed since *Verified at*). Untraced rows are `UNTRACED` or `ACCEPTED` (with the
  decision).
- **An old audit is converted once** with `book requirements import` (report first, `--apply` writes): every
  id is kept (two audit sections for one module become one register, in order, and the written register is
  checked to hold each audit id once), the long evidence goes to `docs/requirements/evidence/<module>.md`, and
  `docs/Audit/spec-traceability.md` moves to the archive, its own relative links rebased there. The audit's
  columns are read by what their headers start with (`Requirement / plan item` is the requirement, `Code` the
  code evidence); a row is untraced by its `UNT` id or its *Untraced* heading, never for a header's wording; a
  column the register has no place for, and a table with no ID column, go whole to the appendix, with links
  rebased to it. A status cell that keeps its history takes its last move (`MISSING → VERIFIED`, `· later: CONTRADICTED`),
  with the whole history in the Note; a condition (`becomes PARTIAL if …`) is no move. One that names several
  statuses without settling which is current is named, in one CONFIRM per file, for a person to check. A row is
  never read shifted: an unescaped pipe in a code span or emphasis stays in its cell, and a row that still has
  more or fewer cells than its header is kept whole in the appendix, flagged, and named for a person to split.
  The import fails unless every non-empty audit cell is in its own id's register row or appendix line, and
  names a dated pass section as a pass for a person to merge. A converted register warns, rather than fails,
  of what the old audit lacked (a VERIFIED row with no test named, a CONTRADICTED one with no defect, an id
  held twice, the ids off the prefix, once per file, the audit's struck-through history), so the backlog never
  blocks a commit.

## Ids — D, Q

The base branch is authoritative; ids are never reused. Two branches taking "the next number"
collide — **before taking one, check the open branches as well as the base branch**, and if a
merge collides, the branch renumbers everywhere it cited the id.

## Plans

One per feature, in [`plans/`](../../docs/plans/).

- A plan is written before the code, from the template, with a row in
  [`project.md` Part 1](../../docs/project.md#part-1--features-and-phasing).
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
never the output. [`Audit/`](../../docs/Audit/) is written only by `/book:fact-check`.
