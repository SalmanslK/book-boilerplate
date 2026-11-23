# The Book's rules for the front-end guides

The rules every Book with the fe-guides pack keeps, imported by the root `CLAUDE.md`. **This file is the
Book's** — `book upgrade` replaces it, and check-book fails when a project's copy differs.

- **An API change reaches the guide of the module it changed, in the same branch**: each changed route in
  `docs/api/api-contracts.md` has an owner guide — by its title, its **Base:** path, or `[fe_guides] owners`
  in `devtools/factory/factory.toml` with a reason — and that guide's *Last checked against the code* line
  moves *[enforced: `check-fe-guides.py`, CI]*.

## Where knowledge lives — the front-end documents

| Topic | Owner |
|---|---|
| How the front end calls one **built** module | its guide in `docs/fe-integration-guides/` |
| What every module's API does the same way (auth, errors, paging) | `docs/fe-integration-guides/api-conventions.md` |
| Which endpoints the front end requires, and whether we have them | `docs/api/fe-api-coverage.md` |
| What one QA build delivered (a dated snapshot) | `docs/release-notes/` |
| How a screen works where the requirements and the wireframes are silent | `docs/ui-standard.md` |
