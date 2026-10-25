## Step: package

A person approved the triage at the revision gate. Now the run is packaged — nothing more is written
into the book.

- **Do not change the book, the questions, the plans or the overview.** The referee holds them as they
  were when the gate approved them; an edit stops the run until a person reopens it.
- **Commit what is still uncommitted**, if anything (`git status`). Never the requirements, never code.
- **Fill the Package section** with the description the change will carry:
  `python3 devtools/factory/factory.py pr-body` prints it — the versions compared, the changes, the
  questions answered and raised, the flagged plans, the change items, the audit's verdict, the gate and
  the checks. Paste it, then add one line saying how it reaches the base branch: a pull request the
  person opens (`factory open-pr`, from their own terminal), or — where nothing is protected and there is
  no remote — the person merges the branch themselves. You do neither.
- Then run the check.
