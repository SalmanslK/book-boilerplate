---
name: project-progress
description: >
  Use to report where the project stands against the Book: features by phase and status, tasks done and
  open, open questions and defects, and what needs a person. Read-only; writes a report, changes no file
  under docs/ except the report.
---

# Project progress report

1. **Read** `docs/project.md` (phases, feature rows, generated task tables), `docs/plans/`,
   `docs/defects.md`, `docs/open-questions.md`, and `docs/factory/runs/INDEX.md` if present.
2. **Refresh the roll-up first**: `python3 devtools/checks/gen-task-rollup.py --check`. If it fails, say
   the tables are stale; do not hand-edit generated tables.
3. **Report**, in this order, in plain words:
   - **Headline**: features done / in progress / not started, as counts and a percentage of tasks.
   - **By phase**: one line per phase with its status.
   - **Needs a person**: approvals waiting, open questions blocking work, defects by severity.
   - **At risk**: features with no plan, tasks with no owner, requirements with no feature.
   - **Next**: the three most valuable next tasks, with why.
4. **Cite** the file and row for every number so the reader can check it.

Output goes in the reply, or `docs/reports/progress-<date>.md` when asked. Never invent a status the
Book does not hold; "not recorded" is an answer.
