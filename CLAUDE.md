# Book Boilerplate

The **Book** as a starting point: the documents that say what a project builds, why, how and what is
done, the checks that keep them true, the skills Claude Code works by, and the guard that refuses what
these rules say never to do. A project copies it (`book init`) and fills it in. This
repository is its own first Book: its product is the boilerplate, its only code is the Python tooling
in `devtools/`, and its stack is `none`.

What it is: [docs/system-overview.md](docs/system-overview.md). How it is built and **why each rule
exists**: [docs/architecture.md](docs/architecture.md). Start here: [docs/README.md](docs/README.md).

## How these instructions are organised

**The Book's rules come first, the same in every Book**, from `.claude/book/`; they are imported below,
and this repository is where they are written. This file holds the boilerplate's own rules after them.
**Two more load when you work in their folder — read the one for where you are before changing
anything there:**

| Folder | Its rules |
|---|---|
| `docs/` | [`docs/CLAUDE.md`](docs/CLAUDE.md) — writing docs: current state vs archive, defects, questions, plans |
| `tests/` | [`tests/CLAUDE.md`](tests/CLAUDE.md) — what the tooling's self-tests must prove, and how to run them |

A stack pack adds its own (the dotnet pack: `src/CLAUDE.md` and one per project folder, importing
`.claude/book/dotnet/`); they live under `devtools/factory/book-template/packs/` until `book init` copies
them, and are not rules here.

@.claude/book/rules.md

# The boilerplate's own rules

## Layout

| Path | Contains |
|------|----------|
| `requirements/` | The boilerplate's own specification: `fsd/` holds every revision as `- V<n>.md`. |
| `devtools/checks/` | The checks the hooks and the local CI run, and their self-tests. |
| `devtools/factory/` | The Book CLI (`factory.py`, run as `python devtools/book.py <command>`), the guard (`hooks/`), the settings loader and the Book's defaults (`book_config.py`, `book-defaults*`), and the template `book init` copies (`book-template/`, with the stack and host packs). |
| `.claude/` | The hook settings, the book plugin's pin, and the Book's rules files (`book/`): the Book's skills and agents are the plugin's. |
| `tests/` | Only [`tests/CLAUDE.md`](tests/CLAUDE.md): the self-tests sit beside the code they test. |

## Branches

- `main` is reached by pull request (here: a reviewed local merge) only — **never commit or push to it**.
- Cut work branches from `origin/main` **with `--no-track`**. A work branch is never named like a release
  tag (`v1.6.0`): the tag and the branch would share a name.

## Where knowledge lives — the boilerplate's documents

| Topic | Owner |
|---|---|
| The Book's front page: the reading order of the owner documents | `docs/README.md` |
| The pointer other AI tools read first | `AGENTS.md` |
| How to install and use the boilerplate | `README.md` |
| The tooling — checks, the Book CLI, the guard, the template and its packs; the enforced-rules table | `devtools/README.md` |

## Coding principles (every stack)

These hold in every language, and the reviewer checks each change against them: a breach is a finding,
not a matter of taste. Why each exists: [`docs/architecture.md`](docs/architecture.md#coding-principles-every-stack).

- **Less code is better code, inside the architecture's boundaries.** Build what the requirement asks
  and nothing speculative. Delete dead and commented-out code. Shorter code that crosses a layer
  boundary is not better: the boundary wins.
- **Neat and understandable.** Names say what a thing is or does; short functions with one job each; no
  deep nesting; no magic numbers.
- **Efficient by design.** No query or call inside a loop; page long lists; measure before tuning.
- **Reusable, without being clever.** Shared logic lives in one place. Write it twice before you
  abstract it; prefer a maintained library over writing your own.
- **The basics, always:** validate input at the boundary; no secrets in code; immutable by default; one
  responsibility per module; every change ships with tests that prove it.
- **Time comes from an injected clock; never block on asynchronous work; catch only the failures you
  can handle; log through the logger, never a stray print; never a Helper or Utility type.** Where a
  regular expression holds one of these, the stack's defaults file (`devtools/factory/book-defaults/`)
  holds it in `[[code_rules]]`, and the lines a commit adds are refused when they break one
  *[enforced: `check-code-rules.py`, pre-commit]*. The boilerplate's stack, `none`, has one: no schema
  in a data script.

## Code rules

- **The tooling is Python 3.11+, standard library only**, so a Book needs nothing installed but Python
  and git.
- **A common rule, default or check is written here once**, in the Book's own files (`.claude/book/`,
  `devtools/factory/book-defaults*`, the checks): never as a copy a project would have to edit.

## Building and testing

- **The self-tests are the build.** Before committing, run the suites the change touches:
  `python devtools/checks/run-self-tests.py <folder>` for `devtools/factory/hooks`, `devtools/factory`
  and `devtools/checks` ([how](tests/CLAUDE.md)). A change to the guard or a shared check runs all three.
- The local CI run (`sh devtools/checks/ci-local.sh`) runs the repository checks, the self-tests, and
  `[commands]` from the settings (an empty command is N/A).

## Skills

`/book:check-docs` owns `references/comparison-checklist.md`, which `/book:update-docs` shares — add a
new concern there. `/book:peer-review` posts to an open PR only after asking and never approves, merges
or declines.
