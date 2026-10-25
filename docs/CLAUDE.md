# Docs — rules for writing documentation

Loaded when you work under `docs/`. Which file owns which topic is the table in the
root [`CLAUDE.md`](../CLAUDE.md#where-knowledge-lives); this file is how to write
them.

## Current state, not history

**A live document says what is true now.** How it got that way is in git, in a
commit message, or in [`archive/`](archive/).

- **No history inside a current statement** *[enforced, for headings and struck-through text:
  `devtools/checks/check-book.py`]*. No `~~old~~ new`, no "was X until
  <date>", no "Corrected <date>", no changelog section in a plan or guide. Change the
  statement; if the reason stops someone re-introducing the old design, keep one short
  *Why:* sentence without the story.
- **Closed items move to `archive/`, and leave a one-line stub** so every id and
  anchor still resolves:

  | Live file | Stays live | Moves to the archive when closed |
  |---|---|---|
  | [`defects.md`](defects.md) | Open rows in full; a one-line index entry per fixed id | The full row → `archive/defects-closed.md` |
  | [`open-questions.md`](open-questions.md) | Open questions in full; one line per answered question under *Resolved*, by topic, stating the rule in force | The full section → `archive/questions-resolved.md` |
  | A plan in [`plans/`](plans/) | The design as built | Build history → `archive/plans-history.md` |

  **One archive folder, one history file per live document**, named after it. A live document
  that keeps a dated log needs its row here first — otherwise the log has nowhere to go and
  piles up in the live file. An archive is written once and not maintained. Nothing in it is a
  current rule.
- **State a fact once and link to it.** Two copies drift, and the stale one is
  believed. **Counts live only where the rows are** — never copy "8 open defects" into
  prose.
- **Wrong documentation is worse than none.** Change behaviour, fix the owning doc in
  the same commit.

## New files

Do not create a markdown file for a topic an owner already covers. The routine new
files are exactly: **a feature's plan** (copy [`plans/_template.md`](plans/_template.md))
and **a factory run's file** in `factory/runs/` — created by `factory start`, never by
hand. <!-- FILL: any other routine new file this project has (a module guide, release
notes). --> Anything else is a decision to raise first. A ledger grows a section; it
does not spawn a sibling (the `archive/` split above is the one exception).

## Defects

The ledger is [`defects.md`](defects.md).

- **A row the day a defect is confirmed**, from any source — not the day it is fixed.
  A finding raised only in a pull request dies when the PR merges.
- **A missing requirement is a defect once we claimed the feature was built**;
  before that it is scope.
- **A defect surprised us; a trade-off is a thing we chose.** A question for the
  product owner goes to `open-questions.md`; a deliberate compromise goes to the
  architecture guide.
- **Every open row carries a Status:** `Open` · `Mitigated` · `Partial` ·
  `Blocked — <Q id or party>`. Nothing else.
- **Keep a row's text short** — what is wrong, where, and the requirement it breaks,
  in about 600 characters. The investigation goes in the commit or the PR.
- **Every row ends with its class and the day it was found:** *Class: api-contract ·
  found 2026-10-01* — the kind of mistake, from `[promotion] classes` in
  `devtools/factory/factory.toml`. A kind that keeps coming back is turned into an
  enforced rule: `factory report` proposes the most frequent.
- **A defect a factory run introduced says so** in its Note — *Introduced by run
  [R-nnn](factory/runs/R-nnn.md)* — so `factory report` can count what escaped each run.
- **When it is fixed:** move the full row to the archive with its commit in *Fixed in*,
  and add a one-line entry to the live *Fixed* index. **Never delete a row, never reuse
  an id**, and put the id in the fixing commit's message.
- **Re-verify before marking one fixed.** Open the code; a grep that matches a comment
  is not proof.
- **A fix must agree with the requirements** — trace it and cite the requirement in the
  note. Requirements silent: say which supporting document you followed. Requirements
  disagree with the fix, or contradict themselves: ask the owner first, record the
  reason and who agreed in the row, and raise an amendment in `open-questions.md` if one
  is needed. Infrastructure, CI, tests and code quality: write "No requirements angle".

## Questions

The ledger is [`open-questions.md`](open-questions.md).

- **Status is one of `OPEN` · `ANSWERED` · `SUPERSEDED`.**
- **Every open question carries a recommendation**, and the recommendation is what
  gets built unless the Answer line says otherwise.
- **Plain words, one closed question, no section numbers** — a question phrased around
  section numbers tends to come back "not sure I understand".
- **When one is answered:** move its full section to the archive and add one line
  under *Resolved*, in its topic, stating the rule now in force. Keep the heading text
  exactly, so its anchor still works.

## Ids — D, Q, R

The base branch is authoritative; ids are never reused. Two branches taking "the next
number" collide — **before taking one, check the open branches as well as the base**,
and if a merge collides, the branch renumbers everywhere it cited the id. Run ids
(`R-nnn`) are the referee's; never pick one by hand.

## Plans

One per feature, in [`plans/`](plans/).

- A plan is written before the code, from the template.
- **The first line is the status line** — `Status · Requirements · Checked against ·
  Depends on` — kept current.
- **A built plan does not copy the code.** What a generated doc shows (routes, columns)
  is linked, not restated. The plan keeps invariants, the requirements the code cannot
  show, and why.
- Departing from an approved plan is discussed first, and the plan is updated in the
  same commit either way.

## Generated — never hand-edit

Every file under `[guard.generated]` in `devtools/factory/factory.toml` is written by a
command, and the guard names it when it refuses a hand edit. Fix the generator, never
the output. <!-- FILL: list this project's generated docs, if any, with their commands. -->

## Factory run files

A run's file in `factory/runs/` is created by `factory start`. **Its header, Checks,
Gates and Record are the referee's**, regenerated from the Record on every command; a
hand edit is refused by the guard and reported by `factory verify`. Write only the
other sections — Intent, Context, Spec, Design, Plan, and the later ones — short and
factual, linking rather than copying. `factory/runs/INDEX.md` is generated by
`factory index`.

A session that cannot make progress adds an `## Escalation` section saying what blocks
it and what a person must decide. `factory reopen <step> --reason …` answers it and
moves it into the Record's history; do not delete one by hand.

## Requirements (read-only)

- **The requirements win every conflict.** A design showing something the requirements
  do not specify is not a requirement; where they are silent, the supporting documents
  are the best evidence; where they contradict themselves, ask.
- **Our copies are snapshots.** <!-- FILL: where the live source is, and how a new
  snapshot is taken. --> A question that looks unanswered may have been answered since
  the last snapshot.
- **QA test cases are not requirements.** A rule found only there is a question until
  the requirements confirm it.
