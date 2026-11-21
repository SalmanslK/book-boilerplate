# Book Boilerplate

The **Book** as a starting point: the documents that say what a project builds, why, how and what is
done, the checks that keep them true, the skills Claude Code works by, and the guard that refuses what
these rules say never to do. A project copies it (`book init`) and fills it in. This
repository is its own first Book: its product is the boilerplate, its only code is the Python tooling
in `devtools/`, and its stack is `none`.

What it is: [docs/system-overview.md](docs/system-overview.md). How it is built and **why each rule
below exists**: [docs/architecture.md](docs/architecture.md). Start here: [docs/README.md](docs/README.md).

## How these instructions are organised

This file holds the rules that apply everywhere. **Two more load when you work in their folder — read
the one for where you are before changing anything there:**

| Folder | Its rules |
|---|---|
| `docs/` | [`docs/CLAUDE.md`](docs/CLAUDE.md) — writing docs: current state vs archive, defects, questions, plans |
| `tests/` | [`tests/CLAUDE.md`](tests/CLAUDE.md) — what the tooling's self-tests must prove, and how to run them |

A stack pack adds its own (the dotnet pack: `src/CLAUDE.md` and one per project folder); they live under
`devtools/factory/book-template/packs/` until `book init` copies them, and are not rules here.

*[enforced: X]* marks a rule a test or check fails the build on; it is written here so you do not trip
it, not because anyone has to police it. The table of every one, and whether it is intact:
[devtools/README.md](devtools/README.md#enforced-rules).

**A guard hook reads every file read, file write and shell command in a Claude Code session** and
refuses what this file says never to do: changing `requirements/` snapshots or generated docs; pushing
or committing to a protected branch; deleting a branch; skipping hooks; secrets or personal paths in
written content; reading or printing [private settings](#private-settings) and credentials. It asks
before touching the files that steer the AI, its own files and Claude Code's settings. A refusal that is
wrong is a guard bug — report it; never route around it. Its rules are `[guard]` in
`devtools/factory/factory.toml`.

**Blocked means stop.** When the guard, a permission, or an unreachable source blocks something the
task needs, stop at that step and tell the person what was blocked, why, and what would unblock it.
Never carry on with what you could not read, and never report a check you could not make as passed.

## Working with AI here

- **One unit of work per session** — a plan, a task, a review, an audit — then a fresh session.
  **`/book:self-review` and `/book:peer-review` run in a fresh session or a subagent**, never in the session that
  wrote the change.
- **"What is left" comes from the Book, not the conversation:** `project.md` Part 1 and each plan's
  Tasks, which `/book:progress` brings up to date.
- **Write for the reader.** Answer first, then only what they need. For a non-engineer: plain words, at
  most five points, and no id, file path or term left unexplained.

## Layout

| Path | Contains |
|------|----------|
| `docs/` | The documentation — owners in [Where knowledge lives](#where-knowledge-lives); closed history in `docs/archive/`. |
| `requirements/` | **Read-only.** The boilerplate's own specification as snapshots: `fsd/` holds every revision as `- V<n>.md` — **build from the highest number** (a lexical sort puts V9 above V15). The index is `requirements/read-me.txt`. Only `/book:check-sources` adds to it *[enforced: `check-protected-paths.py`, pre-commit]*. |
| `devtools/checks/` | The checks the hooks and the local CI run, and their self-tests. |
| `devtools/factory/` | The Book CLI (`factory.py`, run as `python devtools/book.py <command>`), the guard (`hooks/`), and the template `book init` copies (`book-template/`, with the stack and host packs). |
| `.claude/` | The skills, the two Book agents, and the hook settings. |
| `tests/` | Only [`tests/CLAUDE.md`](tests/CLAUDE.md): the self-tests sit beside the code they test. |

## Branches

- `main` is reached by pull request (here: a reviewed local merge) only — **never commit or push to it**.
- Cut work branches from `origin/main` **with `--no-track`**, so a push cannot land on `main`
  *[enforced: guard]*.
- **A doc change the work causes stays on the work's branch.** A general refresh goes on a
  `chore/docs-<slug>` branch of its own ([which is which](docs/CLAUDE.md#which-branch-a-doc-change-goes-on)).

## Where knowledge lives

**One topic, one owner; everything else links to it.** Before writing anything down, find its owner
here. Every doc in `docs/` and at the root has a row or a folder here *[enforced: `check-book.py`]*.
How to write each one: [`docs/CLAUDE.md`](docs/CLAUDE.md).

| Topic | Owner |
|---|---|
| The Book's front page: the reading order of the owner documents | `docs/README.md` |
| What the boilerplate is, its vocabulary, users and flows | `docs/system-overview.md` |
| Architecture, conventions, naming, trade-offs, and **why** each rule exists | `docs/architecture.md` |
| Feature status, phases, dependencies; the latest specification revision's changes | `docs/project.md` |
| **Rules decided for questions the requirements leave open** — *Resolved*, by topic | `docs/open-questions.md` |
| **A defect**: what is wrong, and what happened to it | `docs/defects.md` |
| One feature's design, and its tasks in build order with their role and status | its plan in `docs/plans/` — its Tasks section |
| Which QA test cases are ours, and whether we satisfy them | `docs/qa-artifact-test-suite.md` |
| Specification ↔ plan ↔ code ↔ test traceability — written only by `/book:fact-check` | `docs/Audit/` |
| Closed history — fixed defects, answered questions, earlier revisions | `docs/archive/` — **never a current rule** |
| Rules to follow while working | this file and the nested ones |
| How the team uses AI tools | `AI-DEVELOPMENT-GUIDE.md` (a map, not a second rulebook) |
| The pointer other AI tools read first | `AGENTS.md` |
| How to install and use the boilerplate | `README.md` |
| The tooling — checks, the Book CLI, the guard, the template and its packs; the enforced-rules table | `devtools/README.md` |

**Is X decided?** Look under *Resolved* in `open-questions.md`, then the feature's plan. A decision found
only in the archive, a chat or a PR thread is not in force until it is in one of those.

## Features

- **The specification is the source of truth and wins every conflict**: the highest
  `requirements/fsd/` revision. Where it is silent, ask; a question goes to `open-questions.md` with a
  recommendation — never into a plan as a guess.
- **Every feature gets a plan before it gets code**, from `docs/plans/_template.md`, with a row in
  [`project.md` Part 1](docs/project.md#part-1--features-and-phasing).
- **The plan and the code stay in sync, in the same commit.** A deviation from an approved plan is
  discussed before it is built.
- **Update a feature's status in `project.md` Part 1, and a task's in its plan's Tasks, when it changes.**
- **Stay inside the feature's scope.** A drive-by fix is its own commit, tracked.
- **Change behaviour, fix the doc that owns it in the same commit.** Wrong documentation is worse than none.
- **Every confirmed defect gets a `defects.md` row the day it is found**, and its id goes in the fixing
  commit message *[enforced on `fix/*` branches: `commit-msg` hook]*.

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
  regular expression holds one of these, the stack's pack writes it into `[[code_rules]]` in
  `devtools/factory/factory.toml`, and the lines a commit adds are refused when they break one
  *[enforced: `check-code-rules.py`, pre-commit]*. The boilerplate itself has none (stack `none`).

## Code rules

Four hold wherever you work:

- **Never commit a real secret** (a placeholder starts with `REPLACE`), nor put one in a commit message,
  branch name or test fixture.
- **Never log** passwords, tokens, reset or invitation links, one-time codes, connection strings, or
  customer-identifying data.
- **The tooling is Python 3.11+, standard library only**, so a Book needs nothing installed but Python
  and git.
- **A tool reads the project from `devtools/factory/factory.toml`, never from a name in its code**: a
  project name, path or key written into a script is a leftover `book verify` reports.

## Private settings

**Nothing personal or secret goes in the repository** — no credential or token, no path inside
someone's home folder, no person's email or machine name *[enforced: the guard on every write,
`check-private-data.py` in pre-commit and CI]*.

- **A person's own settings live in their private settings file**, `~/.boilerplate/local.toml` (or
  `BOILERPLATE_LOCAL_SETTINGS`), outside every repository; the tooling reads only the keys
  [`devtools/README.md`](devtools/README.md#private-settings) lists.
- **A session never reads, prints or copies them** — the private file, a credential store, a command
  that dumps one, or a secret variable *[enforced: guard]*. Check that a secret is set with `test -n`;
  never print it.
- **A session reaches a service through a tool that holds the credential, never with it**: Bitbucket
  through `book pr`, Jira through `book jira`, read only.

## Building and testing

- **The self-tests are the build.** Before committing, run the suites the change touches:
  `python devtools/checks/run-self-tests.py <folder>` for `devtools/factory/hooks`, `devtools/factory`
  and `devtools/checks` ([how](tests/CLAUDE.md)). A change to the guard or a shared check runs all three.
- **Before a pull request, run `sh devtools/checks/ci-local.sh`** on the commit and paste the *Local CI*
  section it prints into the description *[enforced: `book pr open`, local CI]*. It runs the
  repository checks, the self-tests, and `[commands]` from `factory.toml` (an empty command is N/A).
- **Report what ran** — the folder, and the executed, passed and skipped counts. A suite that finds no
  tests, or skips one, fails.
- Never skip, delete or weaken a failing test.

## Git

- **Never delete a branch** — say it looks done, and stop there.
- **Never force-push or rewrite history on a shared branch.**
- **Jira-tracked work carries the key right after the type**: `fix/PROJ-310-short-slug`. Our own `D`/`Q`
  ids go in commit messages.
- **A change to the files that steer the AI** — the `CLAUDE.md` files, `.claude/`, the AI tools, the
  guard, the checks, the hooks — **is made or approved by an owner** (`[ai_files]` in
  `devtools/factory/factory.toml`) *[enforced: `book ai-files`]*. Its pull request names them on an
  *AI files* line, which the local CI run prints, so the approval is asked for before the merge
  *[enforced: `book pr open` checks the description's AI files line]*.
- Run `/book:self-review` before opening a PR. `book pr open` refuses a commit without a recorded READY
  verdict.
- **A PR description has a summary, a test plan, a *Local CI* section and one Effort line** *[enforced:
  `book pr open` checks the description before it opens the PR; `book pr-check` reads it back]*.
- Commit messages are imperative, with no AI or tooling attribution and no trailer lines *[enforced:
  `commit-msg` hook]*.

## Skills

Which documentation skill compares what: [the AI guide](AI-DEVELOPMENT-GUIDE.md#which-docs-skill).
All of them treat `requirements/` as read-only; only `/book:check-sources` adds a new snapshot file.
`/book:setup` verifies a Book and creates what is absent. `/book:check-docs` owns
`references/comparison-checklist.md`, which `/book:update-docs` shares — add a new concern there.
`/book:peer-review` posts to an open PR only after asking and never approves, merges or declines.
