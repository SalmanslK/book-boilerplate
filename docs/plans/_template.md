# <Feature> — Design

**Status:** `Not started` · **Owner:** <developer> · **FSD:** §<n> · **Checked against:** FSD V<n> · **Depends on:** <features, or "nothing">

Add a row to the phase table in
[`../project.md`](../project.md#part-1--features-and-phasing) when this file is
created — or link this file from the feature's row already there — and keep this status
line and that row current. Architecture rules — layering, where code lives, error
handling, folder layout — come from [`../architecture.md`](../architecture.md) and are
not restated here.

> Delete this block and any section below that does not apply. An empty heading is
> worse than a missing one: it reads as an omission rather than a decision.
>
> **The shape every plan keeps** *[enforced: `devtools/checks/gen-task-rollup.py --check`]*:
> this status line, with its **Owner**, and a **Tasks** section. A feature plan keeps §1–§9
> in this order and numbering — a section that does not apply is left out, not renumbered —
> and anything extra (testing notes, a reference table) comes after §9 as §10 onwards. A
> cross-cutting design that is no one requirements section (auditing, deployment) keeps
> sections of its own, and ends with Tasks.
>
> **This file says what is true now.** When the design changes, change the
> statement — no `~~old~~ new`, no "was X until <date>", no changelog. If the reason
> stops someone re-introducing the old design, add one *Why:* sentence. The story
> goes in the commit message; build history goes to `../archive/plans-history.md`.
> Rules: [`../CLAUDE.md`](../CLAUDE.md#current-state-not-history).

---

## 1. What the FSD says

The requirements this implements, cited by section or id. Quote the parts that
constrain the design — exact messages, limits, defaults — rather than paraphrasing, so
nobody has to reopen the source to check.

Mark anything the requirements do not settle as **`[PROPOSED]`** and list it in §8.

## 2. The decision this rests on

If the feature has one load-bearing choice — a boundary, a storage shape, whether to
use a library — make the case here, including what was rejected and why. If there is
no such decision, delete this section.

## 3. Model

The core types and their **invariants**, and where each invariant is enforced. Do not
list fields the code already shows.

## 4. Flows

The behaviour, step by step, in the order it happens. Name where each step's rule
comes from — the requirements, an answered question (Q id), or a decision made here.

## 5. Where each piece lives

Follow the layout in the architecture guide. Call out anything **already built** that
this feature should use rather than recreate. Name the obligations every feature
carries in this project that apply here — the tests that prove them, guides to write,
generated docs to regenerate — without restating them.

## 6. Public surface

**Before it is built:** what callers see — endpoints, commands, screens or functions —
with who may use each and what each returns on success and on failure.

| Surface | Who may use it | Returns |
|---|---|---|

**Once it is built**, replace the table with links to the generated contract or the
module's guide. Keep only the reasoning behind a shape the code cannot explain.

## 7. Data and migrations

Constraints the storage enforces that the code cannot — uniqueness across rows, a
non-obvious type — and why. Once built, the schema is its generated doc; do not list
columns here.

## 8. Open questions

Anything the requirements do not answer goes in
[`../open-questions.md`](../open-questions.md), numbered. Link to those numbers here
rather than duplicating them. Technical decisions that are ours to make and still
open stay here. Delete an entry once it is answered — the answer belongs in the
section it shapes.

## 9. Tasks

The work breakdown, **in the order that avoids rework** — not estimates. Draft it from the
sections in §1 before the code starts; keep it when the feature is built, as the record of
what was delivered. [`project.md`](../project.md#task-progress) shows each plan's progress
from this table (`devtools/checks/gen-task-rollup.py`).

- **FSD** — the section the task delivers (`§<n>`), or `—` for work no section names.
- **Role** — who does it: one of `[book] roles` in `devtools/factory/factory.toml`
  (default `BE`, `FE`, `QA`, `BA`, `DevOps`); `[book] our_role` is the one this team's
  progress counts, and the only one a factory run builds (`factory start plan:<stem>#<number>`).
  Where this team builds every stack, back end and front end alike, each of its tasks is
  `our_role`'s; the Task cell says which part it is. Other roles' tasks are listed when our
  work waits on them or theirs on ours.
- **Status** — `Not started` · `In progress` · `Built` · `Blocked`. `Built` is merged into
  the base branch. Update it in the commit that changes it.
- **Jira** — the issue that tracks the task, or `—`. Never invent a key.
- **PR** — the pull request that delivered it, or `—`.
- **Merged** — the day that pull request merged into the base branch (`YYYY-MM-DD`), for a
  `Built` task; `—` otherwise. It is what [Delivery pace](../project.md#delivery-pace) counts.

| # | Task | FSD | Role | Status | Jira | PR | Merged |
|---|---|---|---|---|---|---|---|
| 1 | <task> | §<n> | BE | Not started | — | — | — |
