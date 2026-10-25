## Step: package

The change is done and reviewed; now it goes back into the book and into a pull request.

- **Write the run back.** The referee checks the link exists.
  - A defect: add "fixed by factory run [R-nnn](factory/runs/R-nnn.md)" to its row in
    `docs/defects.md` — or, where its fixed row lives in the archive,
    "[R-nnn](../factory/runs/R-nnn.md)" in `docs/archive/defects-closed.md`.
  - A feature slice or an FSD change: update the feature's status row in `docs/project.md`
    and link the run there.
  - A plan's task (`plan:<stem>#<n>`): end the task's Task cell in its plan with ` (R-nnn)`,
    set its Status to `In progress`, and regenerate the roll-up
    (`python3 devtools/checks/gen-task-rollup.py`), which carries the run into `docs/project.md`.
    Keep the plan's other sections true to what was built.
  - A rule for a recurring defect class (`class:` runs): write the rule down, marked
    *[enforced: …]* and naming the run, in `CLAUDE.md` or `docs/architecture.md`. Its
    `[[promotion.rules]]` entry in `devtools/factory/factory.toml` — class, rule, today's
    date — is the referee's configuration: ask the person to add it.
- **Fill the Package section** with what the pull request carries:
  `python3 devtools/factory/factory.py pr-body` prints its description — summary, test plan,
  the evidence per criterion. Do not open the pull request yourself: it is opened in the
  person's name, so they run `factory open-pr` from their own terminal. In a repository with no
  remote (its merge gate checks `local-approval`), say so in one line instead: a person approves
  the merge on this computer (`factory approve merge`, or Approve in the Factory app), then merges
  the branch. You do neither.
- Commit, then run the check.
