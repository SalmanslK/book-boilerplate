---
name: deploy
description: >
  Use to deploy a branch or commit to a non-production environment (dev, QA) and confirm it is healthy.
  Reads the project's deployment document for the actual steps; never deploys to production.
---

# Deploy to a non-production environment

The steps and environment names live in the project's deployment document (named in `CLAUDE.md`).
This skill is the safe procedure around them.

1. **Preconditions.** Working tree clean; the commit is pushed; its checks passed (CI or
   `python3 devtools/checks/run-checks.py` if the project has it). If any fails, stop and report.
2. **Environment.** Take the target from the user. `prod`, `production`, `preprod` and anything listed in
   `non_dev_targets` of `devtools/factory/factory.toml`: **refuse** and say a person does that.
3. **Plan the deploy.** Read the deployment document; print the exact commands you will run and the
   commit they deploy. Wait for a yes if the environment is shared.
4. **Run it.** Never print, echo or write a secret; secrets come from the environment or the vault the
   document names. Stop at the first error.
5. **Verify.** Run the document's health check. Report: commit, environment, health result, and how to
   roll back (the previous version's id).
6. **Record.** Add a line to the deployment log the document names, if it has one.

If anything is ambiguous, stop and ask; a half-finished deploy is worse than none.
