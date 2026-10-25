## Step: draft-plan

Write the feature's plan. The feature:

{brief}

1. **Copy the template** (the path above) to the plan's path, and fill it by its own rules: the
   status line — `Not started`, the **Owner** from the feature's row (`—` there: write the person
   or team who will build it, or `—`), the **FSD** sections from the Sources section, **Checked
   against** the newest `V<n>` — then §1–§8, then **§9 Tasks**. Delete the template's guidance
   block and any section that does not apply; never renumber. Leave no `<…>` placeholder.
2. **§1 What the FSD says** cites the sections the Sources section names, by `§` id, in the FSD's
   words — never wider than the feature's own sections.
3. **§6 Public surface** — every endpoint, screen or command the feature adds, from the FSD, with
   who may use it and what it returns; a question for what only a design shows. A screen with
   no wireframe takes how it works from `docs/ui-standard.md` — not a question; what it shows
   still comes from the FSD.
4. **§9 Tasks** — the work in the order that avoids rework, one row each: `#`, Task, FSD, Role,
   Status, Jira, PR, Merged. Every task is `Not started`. A task this repository's team builds is
   `[book] our_role`'s (`devtools/factory/factory.toml`) — back end and front end alike when the
   team builds both; say which part in the Task cell — and cites its FSD section. Other roles'
   tasks only where our work waits on theirs. Size each task so one factory run can build it:
   its tests and its code, in one stack where it can. Jira is `—` unless an issue exists — never
   invent a key.
5. **Anything the FSD leaves open** is a question in `docs/open-questions.md`, closed, with a
   recommendation. A line the plan builds on before it is answered is marked `[PROPOSED]` and
   names that Q id.
6. **Link it from the book.** In `docs/project.md`, the feature's row's Plan cell links the plan,
   as `` [`<slug>.md`](plans/<slug>.md) ``.
7. **Regenerate the roll-up** — `python3 devtools/checks/gen-task-rollup.py` — so the new plan's
   tasks appear in Task progress and Open tasks.
8. In the run file's **Design** section write `**Plan:** [<name>](<the plan, relative to the run file>)`.

Commit the plan, `docs/project.md` and any new question, then check.
