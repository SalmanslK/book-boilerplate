## Step: package

The plan is approved; now the run is written back into the book and packaged.

- **Write the run back.** In `docs/project.md`, the feature's row keeps its Plan link, and its
  Status cell names the run: add "plan by [R-nnn](factory/runs/R-nnn.md)" after its status word.
  The referee checks both.
- **Do not change the plan.** A person approved it at the plan gate; the referee holds the file as
  approved until the merge gate, and an edit stops the run.
- **Fill the Package section** — the plan, the questions it raised, and what the change carries:
  `python3 devtools/factory/factory.py pr-body` prints the description. Add one line saying how it
  reaches the base branch: a pull request the person opens (`factory open-pr`, from their own
  terminal), or — where there is no remote — a person approves the merge on this computer
  (`factory approve merge`, or Approve in the Factory app) and merges the branch. You do neither.
- Commit, then run the check.
