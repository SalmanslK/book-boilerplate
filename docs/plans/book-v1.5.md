# Book v1.5 — Retiring the old names

**Status:** `Not started` · **Owner:** Boilerplate owner · **FSD:** — · **Checked against:** — · **FE guide:** — · **Depends on:** Book v1.0.0

v1.4.0 named the Book's command `book` (`python devtools/book.py <command>`) and kept `factory`
(`python devtools/factory/factory.py`) as an alias, and it kept a rename stub under every old skill name. v1.5
removes the alias and the stubs. Its row is in [`../project.md`](../project.md#part-1--features-and-phasing).

## 1. Releasing v1.4.0 first

Template v1.4.0's `.claude/settings.json` pins the book plugin at `ref = "v0.5.0"` and its `plugin_min` is
0.5.0. **Push the plugin's `v0.5.0` tag before the boilerplate's `v1.4.0`**: a Book upgraded to 1.4.0
before the tag exists pins a release the marketplace cannot fetch.

## 2. What still calls `factory`, and must switch when the alias goes

Each was left on the old name on purpose in v1.4.0, because a project on an older Book still runs it:

- **The pipelines**, `devtools/factory/book-template/packs/bitbucket/bitbucket-pipelines.yml`: the AI-files
  step (lines 512-517), which tests `$DEST:devtools/factory/factory.py` to run the destination branch's own
  copy, and the `pr-check` step (line 537). They switch to `devtools/book.py` once no destination branch
  predates v1.4.0.
- **The plugin's `setup/SKILL.md`** (line 104): `factory.py book init --target <this repo>`.
- **`deploy.py`'s user messages**, about 25 of them ("`factory deploy check` did not pass", "/deploy targets
  DEV and QA only", its docstring and `--help`).
- **The upgrade's report header**, `factory book upgrade - installed …`, in `book.py`.
- **`fixture_project.py`** (line 137): its `[fe_guides]` comment names
  `python devtools/factory/factory.py config fe_guides.drive_folder_id`.
- **The plugin's fallback**: each skill's note that runs `devtools/factory/factory.py` on a Book before
  v1.4.0, its allowed-tools in both forms, and the publish script's fallback to `factory.py`.
- **`factory.py`'s rename note** and `book.py`'s fallback to a `main()` that takes no name.
- **Code comments and docstrings** that still name a command as `factory <command>` (for example
  `fixture_project.py`'s "What `factory protection --check` holds…"): renamed with the messages, in one pass.
- **The guard's deploy check** (`factory_deploy_target` in `hooks/guard.py`) judges `python …/book.py`,
  `python …/factory.py` and `python -m book|factory|devtools.book|devtools.factory.factory`. It does not yet
  judge the script run directly (`./devtools/book.py deploy …`, by its shebang) or through `uv run`: add both
  when the alias goes, with tests both ways.

## 3. What goes

- The rename stubs: the v1.3.0 ones under `.claude/skills/` and the packs' (v1.3.0 and v1.4.0), all of which
  say "removed in v1.5". The upgrade reports each REMOVED or DROPPED; the AI guide's *Old names* table stays
  as the record of what each old name means.
- `factory` as a command name, once §2 is done. `devtools/factory/factory.py` stays the module the command
  runs; only the name a person types changes.

## 9. Tasks

| # | Task | FSD | Role | Status | Jira | PR | Merged |
|---|---|---|---|---|---|---|---|
| 1 | Push plugin tag v0.5.0, then release boilerplate v1.4.0 | — | BE | Not started | — | — | — |
| 2 | Switch every §2 caller to `book` | — | BE | Not started | — | — | — |
| 3 | Remove the alias and the rename stubs (§3) | — | BE | Not started | — | — | — |
