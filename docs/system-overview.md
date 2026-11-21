# Book Boilerplate — system overview

## What it is

The Book Boilerplate is a starting point for any software project that wants **the Book**: one set of
documents that says what is being built, why, how and what is done, kept true by checks, and worked on
by Claude Code under rules a person approves. A project gets it in one of two ways: a new repository
copies it (greenfield), or an existing repository installs it in stages (brownfield). Either way
`book init` writes the files and `book verify` says whether the Book is set up.

The Book is stack-free. What a technology stack or a Git host adds — code rules, generated API docs, a
deploy, a pipeline — comes from an optional **pack** that `book init` copies in.

## Vocabulary

| Word | Meaning |
|---|---|
| **Book** | `docs/`, the `CLAUDE.md` files and `requirements/`: the project's written truth |
| **Owner document** | The one file that owns a topic ([Where knowledge lives](../CLAUDE.md#where-knowledge-lives)); every other file links to it |
| **Specification (FSD)** | The client's requirements, filed read-only under `requirements/fsd/` as `- V<n>` revisions |
| **Plan** | One feature's design and its tasks, in `docs/plans/`, written before its code |
| **Check** | A script in `devtools/checks/` that a git hook or the local CI runs and that fails on a broken rule |
| **Enforced rule** | A rule marked *[enforced: …]* in a `CLAUDE.md`, naming the check or test that fails on it |
| **Guard** | `devtools/factory/hooks/guard.py`: reads every tool call in a Claude Code session and refuses what the rules say never to do |
| **Skill** | A habit Claude Code follows, in `.claude/skills/`: commit, review, audit, sync, report |
| **Book CLI** | `book`, run as `python devtools/book.py <command>`: the Book's commands (`book init`, `book verify`, `book pr`, `reviewed`, `impact`, `jira` …), in `devtools/factory/factory.py`, which also runs them as `factory` until v1.5 |
| **Stack** | `none`, `dotnet` or `pytest` — `[book] stack` in `devtools/factory/factory.toml` |
| **Pack** | Files `book init` adds for a stack or a host: `dotnet`, `pytest`, `deploy`, `bitbucket`, `fe-guides` |
| **Parameter file** | `book.toml`: a project's answers (name, branches, owners, tracker) that `book init` fills the template with |

## Users and roles

| Role | Uses it to |
|---|---|
| **Project lead** | Run `/book:setup`, answer the setup questions, own the files that steer the AI (`[ai_files] owners`) |
| **Developer** | Work by the rules, plans and skills; commit through the hooks; open a pull request after `/book:self-review` and the local CI |
| **BA, QA, DevOps** | Read the owner documents — the front page, [docs/README.md](README.md), says where to start |
| **Claude Code** | Works inside the guard; reads the nested `CLAUDE.md` for the folder it changes |

## Main flows

1. **Greenfield:** copy the boilerplate (or `book init --target <repo>`), answer the
   parameters in `book.toml`, run `book init`, fill the `<!-- FILL -->` slots, until
   `book verify` says READY.
2. **Brownfield:** on a `chore/book-setup-<slug>` branch, `book init` creates only what is absent and
   reports every existing file as `KEPT`; the hooks and the guard are switched on last, so the guard
   does not refuse the clean-up. `book verify` climbs from NOT READY to READY over staged pull requests.
3. **Daily work:** a plan before code; the hooks run the checks at every commit; `/book:self-review` and
   `sh devtools/checks/ci-local.sh` before a pull request.
4. **Upgrade:** a Book records the `book_version` it came from; `book upgrade --target <repo>`,
   run from a newer boilerplate, reports the drift of every template-owned file with diffs, and with
   `--apply` updates only the files the project never changed, then bumps `book_version`.

## Systems it talks to

| System | Through | What crosses |
|---|---|---|
| git | the hooks and every check | the repository itself |
| Bitbucket (optional) | `book pr`, `book ai-files`, `book protection` | pull requests, comments, branch restrictions; the token stays in the tool |
| Jira (optional) | `book jira`, read only | one issue's text |
| Google Drive (optional) | `book fsd-threads`, rclone | the live specification's comment threads; the FE guides (the `fe-guides` pack) |
