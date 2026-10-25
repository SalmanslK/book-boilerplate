## Step: build

Make the locked tests pass by changing the product — never the tests.

- Change only the files the Plan's tasks own. Follow CLAUDE.md and the nested CLAUDE.md
  of each folder you change.
- A screen follows `docs/ui-standard.md` and the Design's screen description: the design
  system's tokens and shared components first (`src/ui/` in the starter), every state the
  description lists, plain words on screen.
- Update the documents that own the behaviour in the same change: the module's guide,
  the defect row (moved to Fixed, per `docs/CLAUDE.md`), and any generated doc the
  impact list says to regenerate — with its generator, never by hand.
- The build command is `[commands].build` in `devtools/factory/factory.toml`; it must
  pass with no warnings the project treats as errors. Commit, then check: the referee
  confirms the locks hold, builds, and runs every criterion's tests.
- If a locked test is wrong — not merely inconvenient — do not work around it: write
  it to the Escalation section. Only a person can unlock it.
