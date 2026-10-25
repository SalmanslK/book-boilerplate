## Step: spec

Write the **Spec** section: the acceptance criteria that, all met, mean the item is done.

- One line each: `- **<id>** — Given …, when …, then ….` — Given, when, then, in order,
  each observable from outside (a response and its body, a stored row, a file, a log
  line, an exit code).
- Use the requirement's own id from the traceability file `[requirements]` names in
  `devtools/factory/factory.toml` where one exists (`<id_prefix>-<section>-R<n>`);
  otherwise `{run}.AC-1`, `{run}.AC-2`, ….
- Quote the requirements' exact messages and limits where they give them. Cite the
  section.
- **A question the requirements do not answer is not yours to settle.** Write it to the
  Escalation section and stop; a person decides, and it goes to `docs/open-questions.md`.
- Cover the error cases the item is about, not only the happy path. For a screen,
  observable is what a person sees and does — a heading, a field by its label, a button by
  its name, an announced message — and its empty and error states (`docs/ui-standard.md`)
  are criteria too.
- **Change or guard?** A criterion the change makes true fails before the build — that
  is the default. A criterion that is already true and must *stay* true — behaviour a
  refactor must not break — is a guard: `- **{run}.AC-4** *(guard)* — Given …`. Its
  tests must pass before the build and after it. At least one criterion must be a
  change, or nothing proves the fix. Do not call something a guard to avoid proving it.

The spec gate follows: a person reads this section and approves it. Write it for them.
