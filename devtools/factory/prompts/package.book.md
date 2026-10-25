## Step: package

A person approved `docs/project.md` at the book gate. Now the run is packaged — nothing more is
written into the book.

- **Do not change `docs/project.md` or the questions.** The referee holds project.md as approved;
  an edit stops the run until a person reopens it.
- **Commit what the run wrote**, if anything is still uncommitted (`git status`): project.md, the
  questions, the regenerated roll-up. Never the requirements.
- **Fill the Package section** with the description the change will carry:
  `python3 devtools/factory/factory.py pr-body` prints it — the FSD it was written from, the
  coverage, the questions, the audit's verdict, the gate and the checks. Paste it, then add one line
  saying how it reaches the base branch: a pull request the person opens (`factory open-pr`, from
  their own terminal), or — where nothing is protected and there is no remote — the person merges
  the branch themselves. You do neither.
- Then run the check.
