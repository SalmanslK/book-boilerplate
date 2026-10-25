## Step: review

*The referee did this step itself, and its check did not pass — its verdict is at the end. You are here to fix what it found, then check again.*

You do not review your own work. Run `python3 devtools/factory/factory.py check`: the
referee starts the `factory-reviewer` agent in a session of its own — read-only, with no
memory of writing this change — writes its report into the Review section, and reads
its verdict. A change to a screen is held to the checklist in `docs/ui-standard.md`: a
missed accessibility rule or a missing state is a finding like any other.

- **READY** — the step passes. Read the advisory findings; fix a real one only through
  `reopen build` (below), never by arguing with the report.
- **NOT READY** — each blocking or major finding is listed. Fix them in the product:
  `python3 devtools/factory/factory.py reopen build --reason "review: <finding>"`, change
  the code (never a locked test), commit, and check from build again — the run then
  passes integrate and verify again before the reviewer looks a second time.
- A finding you believe is wrong: do not edit the Review section to remove it. Write
  why to the Escalation section; a person decides.
