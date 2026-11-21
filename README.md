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
- **The skills** (`.claude/skills/`) and **agents** (`.claude/agents/`) are the habits: commit, review,
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
new template (never changed by the project), changed by the project, or changed by both, with a
unified diff for each file it would write. `--apply` updates only the files the project never changed,
lists the rest for a person, and bumps `book_version`. It never touches the project's own content:
the documents init wrote from skeletons, plans, ledgers, `requirements/`.

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

Add your own licence file before publishing.
