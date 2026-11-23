# The Book's rules for deploying

The rules every Book with the deploy pack keeps, imported by the root `CLAUDE.md`. **This file is the Book's** —
`book upgrade` replaces it, and check-book fails when a project's copy differs.

- **Deploy to DEV and QA only, through `/book:deploy`** (`book deploy`); never UAT or production. The guard
  refuses any other environment, and no setting changes that *[enforced: guard]*.

## Where knowledge lives — verifying a deployment

| Topic | Owner |
|---|---|
| Verifying a deployed environment, and the last run's result | `docs/e2e-testing.md` |
| Which rows of QA's smoke checklist the smoke tests prove | `docs/dev-smoke-test-suite.md` |
