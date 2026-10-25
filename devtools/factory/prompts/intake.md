## Step: intake

Understand the item. For a defect id, read its row in the defect log `[requirements]`
names in `devtools/factory/factory.toml`; for a question id, its section in
`docs/open-questions.md`; for a requirement or a section of the spec source, the newest
revision of it (where the requirements live is in the project's CLAUDE.md). Look at the
code it names.

Write the **Intent** section:
- `**Problem.**` — what is wrong or missing, in a sentence or two, on the label's line.
- `**Outcome.**` — what is true when the run is done.
- `**Touches.**` — every path the change will modify, one per line as `` `path` `` with
  a few words on why. Include the tests and the docs that own the behaviour (the
  module's guide, the defect log) — the risk tier is computed from these paths.
