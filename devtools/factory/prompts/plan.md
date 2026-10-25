## Step: plan

Write the **Plan** section as a table: `| Task | Files | Verify |`.

- One row per task, in the order they will be done. Tests first.
- **Files**: every path the task changes, each as `` `path` `` (a glob like
  `` `src/billing/**` `` is allowed). **No two tasks may own the same file** — the check
  refuses an overlap.
- **Verify**: the command that proves the task — the project's test command with a
  filter for the criterion, as the `[adapter]` in `devtools/factory/factory.toml` runs
  it (with several stacks, the stack's `[stacks.adapter]`, run in its folder). Escape a `|` inside a command as `\|`.
- Every file the change touches must be owned by a task, or the verify step refuses it;
  what `[ownership].always` lists (usually `docs/**`) is always allowed.
