## Step: context

A plan run changes no code, so `factory impact` has no diff to read. Write the **Context** section
yourself, by `.claude/skills/change-impact/SKILL.md`'s method for an FSD section rather than a diff:

- **Modules** — the module the feature becomes, and the built modules it calls or is called by
  (their plans in `docs/plans/`, their rows in `docs/project.md`), and the code already there
  that it builds on.
- **Requirements** — the FSD sections the feature's row cites, and their sub-sections.
- **Decisions** — the `Q` ids under *Resolved* in `docs/open-questions.md` that touch it, and the
  ones still open.
- **Designs and contracts** — anything under `requirements/` beside the FSD (wireframes, API
  contracts, test cases) for the feature, if any. A screen with none is designed by
  `docs/ui-standard.md`; say so.

Say "none" rather than leave a line out. End with `**Where to read:**` — links to the sections,
with their anchors, that the sources and draft-plan steps need. Link; do not copy.
