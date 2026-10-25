## Step: verify

*The referee did this step itself, and its check did not pass — its verdict is at the end. You are here to fix what it found, then check again.*

Run the check. The referee runs the `[commands].verify` list in
`devtools/factory/factory.toml` (secret scan, generated-docs and index checks), the
tier's line budget, file ownership against the Plan, and that every criterion still has
a tagged test.

- A generated doc out of date: regenerate it (the command is in the failure), then
  `reopen build`, commit, and check from build.
- A file no task owns: either it should not have changed — revert it through
  `reopen build` — or the Plan missed it: add it to the task that owns it and say why.
- Over budget: the run is too big. Write that to the Escalation section; a person
  decides how to split it.
