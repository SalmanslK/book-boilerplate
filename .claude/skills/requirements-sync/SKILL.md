---
name: requirements-sync
description: >
  Use when a new revision of the requirements document arrives: files it as the next snapshot, diffs it
  against the previous one, and updates the Book (project.md rows, plans, open questions) for what
  changed. Never edits an existing snapshot.
---

# Sync the Book to a new requirements revision

1. **File the snapshot.** Copy the new document to `requirements/fsd/<Name> - V<n+1>.<ext>`
   (the next integer). Existing snapshots are read-only; the guard refuses edits to them.
2. **Diff** V<n+1> against V<n> section by section. Classify each: *new*, *changed*, *removed*, *moved*,
   *wording only*. Quote the old and new text for changed sections.
3. **Apply to the Book**, on a branch named by `[backlog.branches].revision`:
   - new section → a feature row in `docs/project.md` (a new phase placement is a question, not a guess);
   - changed section → mark the feature's plan and tasks affected; add a defect-style note if built work
     now disagrees;
   - removed section → mark the feature `superseded` with the revision; never delete the row;
   - contradictions or gaps → `docs/open-questions.md`, one question each.
4. **Verify**: `python3 devtools/checks/check-book.py` and `gen-task-rollup.py --check` pass.
5. **Report**: counts per class, the questions raised, and the features whose built work may now be
   wrong. A person approves the branch.

Never resolve an ambiguity by choosing; ask.
