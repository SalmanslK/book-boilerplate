## Step: intake

This is a **plan run**: it writes one feature's plan, on demand, and changes no code. The item is
the feature's row in `docs/project.md`:

{brief}

Read that row, then the FSD sections it cites in the newest FSD snapshot (the folder
`[requirements.sources] fsd_dir` names in `devtools/factory/factory.toml`, the highest `V<n>`,
numerically), and the rows it depends on. A Phase 0 foundation row cites no section: read the FSD's
sections that constrain it (scope, users, any non-functional rules). Do not design yet.

Write the **Intent** section:
- `**Problem.**` — on the label's line: the feature has no plan, and what the FSD asks of it, in a
  sentence or two.
- `**Outcome.**` — a plan at the path above, from the template, approved by a person, linked from
  the feature's row.
- `**Touches.**` — one per line as `` `path` ``: the plan file, `docs/project.md`, and
  `docs/open-questions.md` if the FSD leaves something open.
