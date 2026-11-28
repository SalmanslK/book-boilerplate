# Book Boilerplate

A starting point for any project that wants **the Book**: one set of documents that says what is being
built, why, how, and what is done, kept true by checks, and worked on by Claude Code under rules a person
approves. Copy it into a new project, or install it into an existing repository. Version: `book_version`
in `devtools/factory/factory.toml`.

## The idea

- **The Book** is `docs/` plus the `CLAUDE.md` files plus `requirements/`. One owner document per topic;
  requirements are read-only snapshots; a plan comes before code; defects and questions are logged the
  day they appear; closed items move to `docs/archive/`. Start at [docs/README.md](docs/README.md).
- **The checks** (`devtools/checks/`) keep the Book honest, run by the git hooks in `.githooks/` and by
  `ci-local.sh` before a pull request.
- **The guard** (`devtools/factory/hooks/guard.py`) refuses, inside a Claude Code session, what the rules
  say never to do.
- **The skills** and **agents**, from the book plugin (`/book:<name>`), are the habits: commit, review,
  audit, sync, report.

## Install

Python 3.11+ and git; nothing else.

**A new repository (greenfield):**

```
git init my-project
# write my-project/book.toml with the answers (the /book:setup skill asks them)
python devtools/book.py init --target my-project
```

**An existing repository (brownfield):** on a `chore/book-setup-<slug>` branch, with `mode = "brownfield"`
in `book.toml`. Init creates only what is absent and reports every existing file `KEPT`; the hooks are
switched on as the last stage.

`book.toml` answers: `name`, `title`, `slug`, `env_prefix`, `stack` (`none`, `dotnet`, `pytest`), `packs`
(`deploy`, `bitbucket`, `fe-guides`), `mode`, `base_branch`, `protected_branches`, `git_host`, `ci_live`,
`jira_site`, `jira_key`, `fsd_label`, `fsd_link`, `fe_drive_folder_id`, `owners`. An empty string means
"not yet" and leaves a slot to fill. `owners` is a list of tables, one per owner:
`owners = [{ name = "A Person", account_id = "<their Git host account id>" }]`, or `[]` for none yet.

Then fill the `<!-- FILL -->` slots until `python devtools/book.py verify` says **READY**.

## Upgrade

Init records the template version in the project's `devtools/factory/factory.toml` (`book_version`).
From a newer checkout of this repository (its `v<version>` tags rebuild the installed version):

```
python devtools/book.py upgrade --target my-project            # drift report, read-only
python devtools/book.py upgrade --target my-project --apply    # update what it may
```

The report says, for every file the template owns, whether the project's copy is current, behind the
new template (never changed by the project), changed by the project, or changed by both. It shows a diff
for each file a person decides on (CONFLICT, CHANGED, UNKNOWN, and from 1.5 the 1.6 move's changes to each
`CLAUDE.md` and `factory.toml`) and lists each such file once, under *For a person*; the diffs of the files
`--apply` simply takes (BEHIND and NEW) go to a file the report names (`--diffs <path>` chooses it). An
unchanged file of the Book's own template copy (`devtools/factory/book-template/`) that the new template
dropped is RETIRED: `--apply` removes it. `--apply` updates only the files the project never changed,
lists the rest for a person, and bumps `book_version` - unless a file changed by both (CONFLICT) or deleted
by the project (REMOVED) is left: then `book_version` stays, the files are listed in `[book] pending_merge`,
and every report and `book verify` names them until each is merged and taken off the list, when the next
`--apply` bumps it. It never touches the project's own content:
the documents init wrote from skeletons, plans, ledgers, `requirements/`.

### Upgrade notes

What a person does by hand when taking a release; the fixes themselves are in
[`docs/defects.md`](docs/defects.md#fixed).

**1.6.5** (book plugin v0.8.0): two fixes and a rule. `book impact --block` reads the requirements register in a 1.6 Book, where it read the retired traceability matrix and called a built module unbuilt (D106), and `book impact` ties requirement ids from the register's Tests cells; `book review-scope` gives a reason when nothing changed (D107). The Book's rules say Claude never creates, edits or moves an issue in a work tracker; it suggests one. The plugin's 0.8.0 skills close a fixed defect with `/book:raise close`, plan a module's tests with `/book:fact-check before-build`, write the register, read the base branch from `base_ref`, and suggest tickets in `/book:progress`. The pin and `plugin_min` move to v0.8.0. A 1.6.4 project upgrades with `book upgrade --apply`; nothing is done by hand.

**1.6.4** (book plugin v0.7.1): the plugin's `/book:publish-guides` skips `_template.md`; without it the dry run failed with "Illegal characters in path" because the template's title has `<` and `>`. The pin and `plugin_min` move to v0.7.1. A 1.6.3 project upgrades with `book upgrade --apply`; nothing is done by hand.

**1.6.3** (book plugin v0.7.0, unchanged): one fix. A second self-test copied into every project built a fresh Book from the project's own plan template, so it failed in a project whose copy differs; it now runs only in the Book's own repository. A 1.6.2 project upgrades with `book upgrade --apply`; nothing is done by hand.

**1.6.2** (book plugin v0.7.0, unchanged): one fix. A self-test copied into every project scanned the project's own
docs and README for project names, so it failed in every real project; it now scans them only in the Book's own repository
and the shipped template everywhere. A 1.6.1 project upgrades with `book upgrade --apply`; nothing is done by hand.

**1.6.1** (book plugin v0.7.0, unchanged): fixes only. N7 no longer asks for a file `[book] skip_skeletons` or
`[book] declined` leaves out. The dotnet rules and review checklists point Compose placeholders to
`[deploy.compose_placeholders]`, name no other project's types, and no longer link to a document only another pack
creates (a dotnet Book without the deploy or fe-guides pack failed check-book on it). A project that declines
`check-test-setup.py` in `[book] declined` can commit again: the pre-commit hook and the local CI run print
"declined" and go on, the Bitbucket pipeline step prints "check-test-setup.py is not in this project (declined),
skipped." and goes on, and `check-rules.py` counts its two rules as declined; the upgrade report then asks for one
step, `python devtools/checks/check-rules.py --write`, committed. A 1.6.0 project upgrades with
`book upgrade --apply`; apart from that step nothing is done by hand.

**1.6.0** (book plugin v0.7.0):

- **Read the report first.** From 1.5 the upgrade adds a section, *The 1.6 move*: each `CLAUDE.md` loses the
  blocks that are the Book's (they now load from `.claude/book/` by an `@` import) and keeps the rest under
  *This project's own rules*; `factory.toml` loses each key equal to the Book's defaults (`book config --json`
  prints the merged view, which the move never changes). `--apply` writes both. Read what each `CLAUDE.md`
  keeps: a block that says a Book rule in other words stays, and is yours to delete.
- **CONFIRM items**, each named in the report: a list that holds the Book's default and more (move the more to
  `[extend.<table>]` to keep the Book's later additions); run `/book:check-sources` once to give each open
  question its **State**; run `book defects classify` (then `--apply`) to give each open defect its **Type**
  and **Source**; run `python devtools/checks/check-rules.py --write`; and, for each file in
  `[book] drift_accepted` (every Book file the project had changed), move the change to a setting or its
  `CLAUDE.md`, or `[book] declined`, then take the entry out.
- **The drift check.** `devtools/factory/book-files.lock` records the Book's files as the upgrade wrote them;
  check-book fails a commit that changes one (`[book] drift_accepted` makes it a warning, with a reason).
- **Move the plugin pin** to `ref = "v0.8.0"` where the upgrade did not (verify N16 says so).
- **A pack's optional document** the project does not keep: `[book] skip_skeletons = ["docs/ui-standard.md"]`,
  and neither verify nor the upgrade asks for it again.
- **While a CONFLICT is left** `book_version` stays 1.5.x (`[book] pending_merge`, as in 1.5.1): the move is
  written on the first `--apply` and not repeated on the next. Keep `[book] pending_merge_for` while you take
  files off the list.
- **`SPLIT` files are yours now, never to delete:** a dotnet Book's `src/CLAUDE.md` and its nested ones import
  the stack's rules from `.claude/book/dotnet/`.
- **Defaults that come back:** on the run that splits `factory.toml`, the report names each setting and code
  rule the installed release wrote that the project had removed, which the defaults now give again. Set a
  setting again in `factory.toml`; switch a code rule off with `[[code_rules]]`, its `id` and `enabled = false`.
  A settings comment that is not the template's is kept, and named.
- **The defaults files are needed:** a 1.6 Book whose `book-defaults*.toml` is missing has unreadable settings
  (the guard refuses every call, naming the file, which a person restores; check-book names it too), never an
  emptied guard. `[book] declined` can never name a defaults file or a `.claude/book/` file.
- **The requirements register:** a Book with an old audit (`docs/Audit/spec-traceability.md`) converts it once
  with `python devtools/book.py requirements import` (report first, then `--apply`); the report keeps naming
  it until then. The import keeps every id and every audit cell (a column the register has no place for, and a
  table with no ID column, go to `docs/requirements/evidence/`), fails if a cell is lost or a link breaks, and
  names in CONFIRM lines what a person decides: an id held twice, and a dated pass section to merge into its
  modules by hand. The register (`docs/requirements/<module>.md`) is then checked at every commit;
  `.claude/book/docs.md`, *Requirements register*, has its rules.
- **`book config --json`** with no key prints the whole merged settings.

**1.5.1** (book plugin v0.6.1):

- **Move the plugin pin**: upgrade updates `.claude/settings.json` to `ref = "v0.6.1"` where the project has not
  changed it. Installing the plugin (`claude plugin install book@book-plugins --scope project`) writes that file
  back with its keys in another order and nothing else changed: harmless, revert it with
  `git checkout .claude/settings.json`.
- **Requirements read-me files**: a `requirements/` read-me that is still its skeleton, but for its LINKS and
  *Last checked* lines, is offered BEHIND with its old skill names renamed (`/requirements-check` becomes
  `/book:check-sources`), and `--apply` writes it. A read-me the project wrote its own text into is named in a
  CONFIRM note, line by line: rename those names by hand in the upgrade's pull request (a read-me may be edited
  in place; check-sources' capture step is still Phase 4, Capture).
- **The fe-guides pack**: `docs/fe-integration-guides/_template.md` arrives NEW. A missing
  `docs/ui-standard.md` is named in a MISSING note with its skeleton: start it from that skeleton and fill it,
  since `book verify` now asks for it.
- **Your own copies of the skeletons**: the `factory.toml` header ("Read by devtools/book.py") and the AI
  guide's note on the settings rewrite changed in the template; a project's copies are its own, so carry them
  over by hand if you want them. The Cursor rule and `AGENTS.md` are the template's, and upgrade updates them.
- **A conflict left by `--apply`** keeps `book_version` at 1.5.0 and is listed in `[book] pending_merge`:
  carry the template's change over (the report's diff), take the file off the list, and run `--apply` again.
- **An open question headed with `**Recommendation:**`** where `[book] recommendation_heading` sets another
  now fails check-book: rename the heading.

## Work in this repository

```
git config core.hooksPath .githooks
python devtools/checks/run-self-tests.py devtools/factory/hooks
python devtools/checks/run-self-tests.py devtools/factory
python devtools/checks/run-self-tests.py devtools/checks
python devtools/book.py verify
```

The rules are [CLAUDE.md](CLAUDE.md); the tooling is [devtools/README.md](devtools/README.md).

## Licence

Licensed for use only: see [LICENSE](LICENSE). You may install and use it in the projects you
work on; you may not redistribute it, or present it as your own, without the author's written
permission. Copyright (c) 2026 Salman Khan.

A project made from the boilerplate chooses its own licence for its own code; the Book's files
inside it stay under the boilerplate's licence.
