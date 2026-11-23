# <Feature> — Design

**Status:** `Not started` · **Owner:** <developer> · **FSD:** §<n> · **Checked against:** FSD V<n> · **FE guide:** — · **Depends on:** <features, or "nothing">

Add a row to the phase table in
[`../project.md`](../project.md#part-1--features-and-phasing) when this file is
created, and keep this status line and that row current — their status word follows from the
`BE` tasks in §9, set by `devtools/checks/gen-task-rollup.py`. Architecture rules —
layering, port placement, keys, results, folder layout — come from
[`../architecture.md`](../architecture.md) and are not restated here.

> Delete this block and any section below that does not apply. An empty heading is
> worse than a missing one: it reads as an omission rather than a decision.
>
> **The shape every plan keeps** *[enforced: `gen-task-rollup.py --check`]*: this status line,
> with its **Owner**, and a **Tasks** section. A feature plan keeps §1–§9 in this order and
> numbering — a section that does not apply is left out, not renumbered — and anything extra
> (testing notes, a reference table) comes after §9 as §10 onwards. A cross-cutting design
> that is no one FSD module (auditing, deployment, tenancy) keeps sections of its own, and ends
> with Tasks.
>
> **This file says what is true now.** When the design changes, change the
> statement — no `~~old~~ new`, no "was X until <date>", no changelog. If the reason
> stops someone re-introducing the old design, add one *Why:* sentence. The story
> goes in the commit message; build history goes to
> [`../archive/plans-history.md`](../archive/plans-history.md). Rules:
> [`../CLAUDE.md`](../../.claude/book/docs.md#current-state-not-history).

---

## 1. What the FSD says

The requirements this implements, cited by section. Quote the parts that constrain
the design — exact validation messages, limits, defaults — rather than
paraphrasing, so nobody has to reopen the PDF to check.

Mark anything the FSD does not settle as **`[PROPOSED]`** and list it in §8.

## 2. The decision this rests on

If the feature has one load-bearing choice — an aggregate boundary, a storage
shape, whether to use a library — make the case here, including what was rejected
and why. If there is no such decision, delete this section.

## 3. Domain model

Aggregates, entities, value objects, enums. For each aggregate: its **invariants**,
and the methods that enforce them. Say which types are **aggregate roots**, since
only those get repositories. Do not list properties the code already shows.

## 4. Flows

The behaviour, step by step, in the order it happens. Name where each step's rule
comes from — the FSD, an answered question (Q id), or a decision made here.

## 5. Where each class lives

Follow the folder layout in the architecture guide. Call out anything **already
built** that this feature should use rather than recreate.

The obligations every feature carries — tenant scoping and the tests that
prove them, the FE integration guide once built, the regenerated API contracts and
ERD — are in [`../architecture.md`](../architecture.md), *Adding a new feature*.
Say which apply here; do not restate them.

## 6. API surface

**Before it is built:** the endpoints, the permission each needs, and the status
codes and error codes it returns — drafted from the FSD and the wireframes with
`/book:outline-api`.

| Endpoint | Permission | Returns |
|---|---|---|

**Once it is built**, replace the table with links — routes, permissions and request
shapes are `../api/api-contracts.md` (generated, the dotnet pack); status
codes, error codes and messages are the module's FE guide. Keep only the reasoning
behind a shape the code cannot explain.

## 7. Data and migrations

Constraints the database enforces that the domain cannot — uniqueness across rows,
partial indexes, a non-obvious column type — and why. Once built, columns are
`../database-erd.md` (generated, the dotnet pack); do not list them here.

## 8. Open questions

Anything the FSD does not answer goes in
[`../open-questions.md`](../open-questions.md), numbered. Link to those numbers here
rather than duplicating them. Technical decisions that are ours to make and still
open stay here. Delete an entry once it is answered — the answer belongs in the
section it shapes.

## 9. Tasks

The work breakdown, **in the order that avoids rework** — not estimates. Draft it from the FSD
sections in §1 before the code starts; keep it when the feature is built, as the record of what
was delivered. [`project.md`](../project.md#task-progress) shows each plan's progress from this
table (`devtools/checks/gen-task-rollup.py`, checked by pre-commit and CI).

- **Role** — who does it: `BE` (us), `FE` (the front-end team), `QA`, `BA`, `DevOps`. Other
  roles' tasks are listed when our work waits on them or theirs on ours.
- **Status** — `Not started` · `In progress` · `Built` · `Blocked` · `Moved` · `Dropped`. `Built` is merged into the base branch.
  Update it in the commit that changes it. **A row keeps its number and is never deleted** —
  its status and text may change *[enforced: `gen-task-rollup.py --check`, against the base branch]*. **`Moved`** is a task carried into another plan — Release 2's
  carry-over, say — so this feature can be `Built` without it: the row keeps its number, so every
  reference still lands, and its text links to the task it became there. It counts neither in the
  plan's progress nor as open. **`Dropped`** is a task taken out of scope: its text names the
  decision (who, when, or the *Resolved* question), and it counts the same way. A plan whose feature was later taken out of the
  product has the plan status `Removed`: its tasks stay as they were delivered, and the plan
  leaves the roll-up's progress and open tables (its merged tasks still count in the delivery pace).
- **Jira** — the Jira issue that tracks the task, or `—`. Never invent a key.
- **PR** — the pull request that delivered it, or `—`.
- **Merged** — the day that pull request merged into the base branch (`YYYY-MM-DD`), for a `Built` task;
  `—` otherwise. It is what [Delivery pace](../project.md#delivery-pace) counts.

| # | Task | FSD | Role | Status | Jira | PR | Merged |
|---|---|---|---|---|---|---|---|
| 1 | <task> | §<n> | BE | Not started | — | — | — |
