## Step: triage

**A new version of the FSD has arrived. Work out what each change means for the book — and change only
the book.** The run's **Changes** section is every section the new version added, removed or changed,
read by the referee itself, a changed one with its diff. Each version's text, section by section, is in
the files the brief names: quote from there. The project:

{brief}

Nothing here is decided until a person approves it at the revision gate. Never touch code, tests or
anything outside the files the brief lists — a revision run changes the book, and the referee refuses
any other change.

For each change, in the **Changes** section's order:

- **A new section** — a new feature becomes a row in `docs/project.md` (Part 1, the phase its
  dependencies put it in, citing `§<id> <title>` exactly as the new version has it, `Not started`); a
  section that belongs to an existing feature is added to that row's FSD cell.
- **A removed section** — the feature row that cites it **stays**: never delete a row. Add to its Status
  note `— §<id> removed in V<n>`, so a person decides what happens to it.
- **A changed section** — link it to the feature or features whose rows cite it.
  - A feature **not started**, with a plan whose tasks are all `Not started`: update the plan to match the
    new version (its §1 and the tasks the change reaches).
  - A plan with a task `In progress` or `Built`: **flag it, do not rewrite it.** List it under
    **Flagged plans**; it goes back through the plan gate with a person. The only change allowed to such a
    plan is a new task row, `Not started`.
  - A feature already **Built** whose requirement changed: a **change item**, never a code change — a
    defect row in the defect log, or a new task row in its plan, as this project records a missed or
    changed requirement in work already done. List it under **Change items**.
- **The system overview** — where a change touches what the product is, its words, its users or its main
  flows, bring `docs/system-overview.md` in line with the new version.
- **Part 3 of `docs/project.md`** — add the comparison of the two versions in the shape Part 3 asks for.

Then the questions in the decisions file:

- **Every open question**: does the new version answer it? Only where its words settle it — closed, in
  so many words. Then move it to Resolved, as the file's own instructions say, with the rule now in force,
  and list it under **Answered** with the exact words, their section and the version.
- **New questions**: where the new version is unclear, contradicts itself or leaves a gap a feature
  needs, raise one closed question in plain words, **always with a recommendation**, and list it under
  **New questions**. Take the next free id.

Write the run file's sections:

- **Triage** — one line per change, in this shape, its disposition after the second ` — `:
  `- §2.6 <title> — added — feature-row <feature>`. The dispositions: `feature-row`, `flagged`,
  `linked`, `answers Q<n>`, `question Q<n>`, `plan-updated`, `plan-flagged`, `change-item`, `overview`,
  `no-impact (<why>)` — as many as apply, joined with `;`.
- **Answered** — `- Q<n> — "<the new version's words, exactly>" — §<id>, V<n>`.
- **New questions** — `- Q<n> — <the question>`; its recommendation is in the file.
- **Flagged plans** — `- <plan path> — <what changed, and which task has work started>`.
- **Change items** — `- <feature> — D<n>` or `- <feature> — <plan-stem>#<task>`.

Write `none` in a section that has nothing. Then regenerate the roll-up if a plan's tasks changed
(`python3 devtools/checks/gen-task-rollup.py`), commit, and check. The referee checks every line against
the Changes, the questions file at the run's start and now, the new version's text and the diff.

**Reopened after the audit?** The reason below lists the auditor's findings, and the run's *Audit*
section holds its whole report. Answer each one; where a finding is wrong, say why in the **Triage**
section, quoting the FSD.
