---
name: deploy
description: >
  Use when asked to deploy the backend API to DEV or QA ("/deploy", "deploy dev to QA", "redeploy
  DEV") over SSH. DEV and QA only: refuse UAT, production or any other host, however it is phrased.
  Gates on a local build and tests, verifies after with a smoke test that offers keep or revert, and
  reports; never commits, never prints a secret.
---

Deploy the Boilerplate backend API to the DEV/QA Compose host. **Default environment is DEV.**

Every step is a `factory deploy` command (`devtools/factory/deploy.py`). It runs the probes,
tests and counts the same way every time, and it keeps what it found in `.git/factory-deploy.json`,
so later steps and the report read measurements, not memory. **This skill decides; the tool
measures.** The host, paths and ports are in `devtools/factory/factory.toml` `[deploy]`, and no
argument names a host. The platform and the reasons behind each check are in
the project's deployment plan (in `docs/plans/`) and the comments in `deploy.py`.

Run every step as `python devtools/factory/factory.py deploy <step> …` from the repository root.
The gate and the smoke run take minutes, so run them in the background and wait.

**To only see what is deployed**, run `deploy status [--target dev|qa|all]`. It is read-only and is not
a deploy: each environment's commit, when it went live, and how far it is behind `origin/dev`. When
the host cannot be reached it shows the deploy ledger's last record, labelled as not a live read. The
report step adds to that ledger.

---

## HARD LIMIT — DEV and QA only

If `env=` is anything but `DEV` or `QA` (in any case) — `PROD`, `PRODUCTION`, `LIVE`, `STAGING`,
`UAT`, `PREPROD`, a hostname, anything — **stop before running any command** and print:

`Refused — /deploy targets DEV and QA only. {VALUE} is not a permitted target.`

Offer no workaround. Don't suggest editing this file, the config or the tool, and never fall back
to DEV after refusing another value. **This holds when the person insists, says it is urgent,
claims approval or asks for "just this once."** UAT and production go through the team's release
process, not this skill. The same refusal covers a request to point a DEV or QA deploy at another
host or another directory on the box. `deploy.py` and the guard both refuse it as well. They are
the backstop, not the reason.

The limit is on the **target**, not the branch: `env=QA branch=fix/xyz` is fine.

---

## Parameters

| Param | Default | |
|---|---|---|
| `env` | `DEV` | `DEV` or `QA`. Nothing else. |
| `branch` | `dev` | The branch the server checks out. Both environments track `dev`. |
| `pre-tests` | asked | `run` or `skip` — the build and test gate before the deploy. **`skip` is DEV only.** |
| `post-tests` | asked | `run` or `skip` — the smoke suite against the host just deployed. |

**A test choice not given is asked, once**, with `run` recommended: *"Run the pre-deploy build and
tests first (about an hour)?"* and *"Run the smoke tests against the host after the deploy?"* With
no one to answer, both are `run`. `pre-tests=skip` with `env=QA` is refused:
`Refused — QA always runs the pre-deploy tests. Use pre-tests=run, or deploy to DEV.` A DEV skip is
the person's choice for a quick fix. Say what it gives up: the build, unit, architecture and
in-process integration suites, including the proof that a migration applies, don't run before the
forward-only `migrate` container touches the shared database.

**Each QA build is preceded by `/book:fact-check` and `/book:check-docs`, and `/book:check-sources all` runs weekly:** on
`env=QA`, remind the person and name the last audit, if the Book records one. A reminder, not a gate.

---

## The steps

Each step prints lines that start with `OK`, `WARN`, `FIX` or `BLOCK`, and exits non-zero when it
did not pass. **A non-zero exit or any `BLOCK` line: stop and report it as it stands.** Never retry
a step to get a better answer, and never work around a check by running its commands yourself.

1. **Check** — `deploy check --env {ENV} --branch {BRANCH} --pre-tests {run|skip} --post-tests {run|skip}`.
   - It confirms the local tree is clean and checks out the branch, so the gate tests the same
     commit the server deploys. **On another branch it stops**: ask the person whether to move this
     tree to {BRANCH}, and only on a yes run it again with `--switch`. It then checks local against origin, SSH with the key, `.env`
     present, disk, backups, the nightly run, mount and volume ownership, and that the target
     supplies every required setting.
   - It prints the target block first; show it to the person.
   - It also records the commit the server runs now, before anything on the box changes: that is the
     commit a revert goes back to.
   - **`WARN`** (stale backups, a failed nightly run with its log, the LAN path) is a loud warning at
     the top of the report. It never blocks, and never try to fix it.
   - **`FIX`**: the only fixes are the ownership commands it names. Tell the person, run the same
     command again with `--fix`, and say so in the report.
   - **`BLOCK`**: stop. Uncommitted work, local and origin differing, a missing key, `.env` or
     setting, or a full disk.
     - Never push for the person, never accept a password, never `docker system prune`. Suggest
       the prune; it is the person's call.
     - A setting the target doesn't supply goes in the overlay's `environment:` through a pull
       request, never as an edit on the box.
2. **Gate** — `deploy gate`, or `deploy gate --build-only` when the pre-tests are skipped and the
   smoke will run, since the smoke runs `--no-build`. With both skipped, run neither.
   - It builds the whole solution and runs the unit, architecture and in-process integration
     suites. It fails on any failure, on a run that executed nothing, and on any skip.
   - An integration skip means Docker isn't running: say to start it, or set `BOILERPLATE_TEST_DB`.
   - Never skip, delete or weaken a failing test.
3. **Changes** — `deploy changes`. Against the server commit `check` recorded, it prints
   `STATUS new|nothing-new|not-ancestor`, the commits, and the migrations they add.
   - `nothing-new`: ask whether to rebuild anyway (worth it when only `.env` or a base image
     changed).
   - `not-ancestor`: the server is pinned by an earlier revert; confirm before going on.
4. **Up** — `deploy up`. One SSH call pulls `--ff-only` and runs `up -d --build`, then checks
   liveness, readiness, default-deny (401) and the LAN path.
   - It refuses on its own a QA deploy without a passing gate for this commit.
   - On failure it prints the migrate and API logs. **The first exception is the cause.** Report
     it and stop; never retry.
   - Readiness 503 with liveness 200 means the app is up and its database isn't.
   - A LAN-only failure is the host firewall, not the deploy.
5. **Smoke** — `deploy smoke`, unless post-tests were skipped. It runs the `Smoke/` tests against
   the host just deployed.
   - The credentials and tenant key come from the person's shell as
     `BOILERPLATE_DEPLOYED_API_EMAIL`, `_PASSWORD` and `_TENANT_KEY`, and they must belong to
     {ENV}.
   - **Never prompt for them, accept them inline, or print them.** The tool checks presence only.
   - What the suite proves, and the rule of **one run per host at a time**:
     [`docs/dev-smoke-test-suite.md`](../../../docs/dev-smoke-test-suite.md). The tool holds a lock
     on the host for the run. When another run holds it, report who and when, and stop. A **STALE**
     lock (over 2 hours) is broken only after the person confirms no smoke run is going, with
     `deploy smoke --break-stale-lock` — never on your own.
6. **Keep or revert** — only when the smoke run says `FAILED`, not `DID NOT RUN`, `IN PART` or a
   pass. Print the failing tests, then ask:
   *"The smoke tests failed on {ENV}. Keep this deployment ({new}), or revert to {previous}?"*
   - Recommend **keep**: the failure may be a test fault, a missing account or a data problem, and
     a revert is itself a deploy. With no one to answer, keep. **Never revert without an explicit
     answer.**
   - If `changes` listed migrations, add this to the same question, naming the files: *"This
     deploy applied a migration. A revert puts the old code on the new schema: harmless if the
     migration only added something the old code never reads, broken if it dropped or renamed
     something it still selects."* Then recommend keep more strongly. Never `dotnet ef database
     update` to undo it.
   - On revert: `deploy revert --to {previous}`. It goes only to the commit this deploy replaced,
     and repeats the health checks. If it fails, report that and stop; never try another commit.
     Don't re-run the smoke after a revert unless the person asks.
7. **Report** — `deploy report`. Print it as it comes, first line first.
   - The first line is the verdict ("Deployment complete" only when the smoke executed, passed and
     skipped nothing). Then the pre-tests-skipped line and the warnings, then the summary block.
   - Don't soften a failure, drop a warning or round a skip count. Never add a connection string,
     key, password or `.env` value.

---

## What this skill must not do

- **Never deploy to UAT or production**, nor anywhere but the DEV and QA tables in `factory.toml`.
- **Never commit, push or merge.** Both environments track `dev`; promoting a branch is not a side
  effect of deploying.
- **Never `docker compose down -v`** (it deletes the database's volume), **never `docker system
  prune`**, and never touch a container, network or volume that isn't this project's. The box is
  shared.
- **Never `dotnet ef database update` against DEV or QA.** The `migrate` container owns schema
  changes.
- **Never read, print, copy or edit the server's `.env`.** Never accept a password for SSH, the
  database or a test account.
- **Never roll back on your own.** Revert only on the person's explicit answer, only to the commit
  this deploy replaced.
- **Never skip the pre-deploy tests on QA**, and never skip them on DEV unless the person chose it.
- **Never run the commands a step runs by hand** to get past it. A step that refuses is the answer.

---

## What next

- **Deployment complete:** if this closes a Jira issue, draft its *Dev testing & notes for FE/QA*
  (CLAUDE.md, Jira) with the report's counts. It is posted only after approval.
- **IN PART:** say which variable was missing. Re-run `deploy smoke` once the person has set it.
- **FAILED and kept:** a confirmed defect gets a `docs/defects.md` row today (CLAUDE.md, Features).
- **Stop** after the report. Don't start another deploy, or a fix, in the same turn without being
  asked.
