# Book v1.5 — Retiring the old names

**Status:** `In progress` · **Owner:** Boilerplate owner · **FSD:** — · **Checked against:** — · **FE guide:** — · **Depends on:** Book v1.0.0

v1.4.0 named the Book's command `book` (`python devtools/book.py <command>`) and kept `factory`
(`python devtools/factory/factory.py`) as an alias, and it kept a rename stub under every old skill name. v1.5
removes the alias and the stubs. Its row is in [`../project.md`](../project.md#part-1--features-and-phasing).

## 1. Releasing the plugin first

A template release pins the book plugin it needs: template v1.5.0's `.claude/settings.json` has
`ref = "v0.6.0"`, and its `plugin_min` is 0.6.0. **Push the plugin's tag before the boilerplate's**
(`v0.6.0`, then `v1.5.0`): a Book upgraded before the tag exists pins a release the marketplace cannot fetch.
Plugin 0.6.0 runs only `devtools/book.py`, with no fallback to `factory.py`: every Book that pins it is on
template 1.5.0, which has it.

**Local work branches must not be named like a release tag; use `work/vX.Y.Z`.** Why: `git clone --branch`
and a bare `vX.Y.Z` pick a branch of that name over the tag, and a branch can move. Upgrade reads the
installed version from `refs/tags/v<version>` only, and the plugin's ledger reads use full ref names.

**A long-lived branch older than v1.4.0 needs nothing first.** A project does not promote its base branch
into `qa`, `uat` or `main` to take 1.5: that would release unfinished work. A pull request into a branch whose
Book is older than v1.4.0 (`factory.py`, no `devtools/book.py`) is judged by that branch's own `factory.py`,
which is still a command there (§2), until a promotion brings the branch 1.5. `book upgrade` names each
protected branch still older than v1.4.0, read from the project's own refs, as information.

## 2. The command is `book` only

- **`devtools/book.py` is the command**; `devtools/factory/factory.py` is the module it runs. Run as a script,
  `factory.py` says so and exits 2, so a hook, pipeline or habit left on the old name fails instead of passing
  with nothing run. `book.py` refuses, the same way, a kept `factory.py` from before v1.4.0 whose `main()`
  names no command; the upgrade names that file a must-merge CONFLICT first.
- **The upgrade names a kept CI file, script or hook that still runs `factory.py`** (CHANGED or CONFLICT, so
  the upgrade never rewrites it), however it is started.
- **Every caller says `book`**: the hooks, the local CI, the pipelines (`book.py ai-files`, `book.py
  pr-check`), the plugin's skills, `deploy.py`'s messages, the upgrade's, verify's and init's report headers
  (`book upgrade - installed …`), the help text, comments and docstrings. `check-rules` counts a CI rule as run
  only where a CI file runs `book.py <command>`; a project's own `CLAUDE.md` may still name a marker
  `factory <command>`, and it reads the same.
- **The pipelines' AI-files step** runs the destination branch's own `devtools/book.py` (with
  `devtools/factory/`). A destination with only `factory.py` holds a Book from before v1.4.0: the step says so
  in one line and runs that destination's own `factory.py ai-files`, as the 1.4.x pipeline did, so the
  destination still judges the pull request; the step fails if that command fails. A destination with neither
  has no AI tools yet, and the pull request's own copy judges it, as before.
- **The upgrade's old-command note and `check-rules` leave that fallback alone**: it runs the destination's
  copy, extracted to `/tmp/destination/`, never the project's own `factory.py`.
- **The guard's deploy check** (`factory_deploy_target` in `hooks/guard.py`) judges the command however it
  starts: `python …/book.py`, `python -m book|devtools.book`, `python -c` code that runs it through `runpy`, the
  script run directly by its shebang (`./devtools/book.py deploy …`), and through `uv run` (with `-m` or
  `--module`), `uvx`, `pipx run`, `poetry run`, `pipenv run` and `hatch run`. It judges every `--env` and
  `--target` the command is given, not only the first. It still judges `factory.py` under the same forms: a
  project mid-upgrade may keep an older `factory.py` (a CONFLICT) that still deploys, and judging a name that
  no longer runs costs nothing.

## 3. The old names have no stub

- No rename stub is left under `.claude/skills/` or in a pack, and the template owns no skill: `book init`
  creates no `.claude/skills/`, and verify's N16 passes a Book with none of its own.
- **The upgrade** reports each stub a 1.4.x Book still has as DROPPED, in one note naming each old name and the
  `/book:` name it pointed at, and says plainly that the old names stop working after this upgrade: the
  project deletes those folders in the upgrade's pull request. A stub the project turned into a skill of its
  own is no stub, and keeps the ordinary note for a retired skill.
- **The AI guide's *Old names*** stays, as a plain lookup ("If you type an old name, use this instead"): with
  the stubs gone it is the only place a person finds the new name.

## 9. Tasks

| # | Task | FSD | Role | Status | Jira | PR | Merged |
|---|---|---|---|---|---|---|---|
| 1 | Push plugin tag v0.5.0, then release boilerplate v1.4.0 | — | BE | Built | — | — | 2026-11-22 |
| 2 | Switch every §2 caller to `book` | — | BE | Built | — | — | — |
| 3 | Remove the alias and the rename stubs (§3) | — | BE | Built | — | — | — |
| 4 | Push plugin tag v0.6.0, then release boilerplate v1.5.0 | — | BE | Not started | — | — | — |
