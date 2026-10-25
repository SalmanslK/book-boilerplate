## Step: sources

Before the plan is written, say what it is written from — the referee checks each line as it
checks a spec's sources, with no criteria. The feature:

{brief}

Write the **Sources** section, one line each:

- `**FSD:** V<n> §…` — the newest snapshot (the highest `V<n>` in the FSD folder, numerically)
  and every section you read, as `§2.1.1`. Only the feature's own sections and their sub-sections;
  another feature's section is a dependency, named in the plan, not a source. A feature whose row
  cites no section (a Phase 0 foundation) names the sections that constrain it.
- `**Decisions:** Q…` — the answered questions the plan relies on, from *Resolved* in
  `docs/open-questions.md`, or `none — <why>`. An open question decides nothing yet.
- `**Live check:** <today> — up to date | stale — <what moved> | could not check — <why>` — what
  the live copy of the FSD said for those sections, where the project keeps one. An unattended
  session cannot read it: write `could not check — no live copy read in an unattended session`.

Where the FSD does not answer something the plan needs, raise a question in
`docs/open-questions.md` — closed, in plain words, with a recommendation — and name it here.
