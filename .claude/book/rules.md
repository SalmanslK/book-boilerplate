# The Book's rules

The rules every Book keeps, the same in every project: the project's `CLAUDE.md` imports this file and holds
only its own rules beside it. **This file is the Book's** — `book upgrade` replaces it, and check-book fails
when a project's copy differs. A rule of the project's own goes in its `CLAUDE.md`; a value, in
`devtools/factory/factory.toml`.

*[enforced: X]* marks a rule a test or check fails the build on; it is written here so you do not trip it, not
because anyone has to police it. The table of every one: [devtools](../../devtools/README.md).

**A guard hook reads every file read, file write and shell command** and refuses what these files say never to
do; it asks before touching the files that steer the AI or Claude Code's settings, and before a force-push. A
refusal that is wrong is a guard bug — report it; never route around it. What it covers:
[devtools](../../devtools/README.md).

**Blocked means stop.** When the guard, a permission, or an unreachable source blocks something the task needs,
stop at that step and tell the person what was blocked, why, and what would unblock it. Never carry on with what
you could not read, and never report a check you could not make as passed. **One exception:** where a skill
defines a recorded answer for exactly that case (`could not check — <why>`, `UNVERIFIED`, `passed IN PART`),
writing it where a person must read it before anything proceeds *is* the stop.

## Working with AI here

- **One unit of work per session** — a plan, a task, a review, an audit — then a fresh session for the next.
  **A session that works in a worktree starts in it**: skills load from the folder a session starts in, so
  another checkout's copies may be older.
- **"What is left" comes from the Book, not the conversation:** `project.md` Part 1 and each plan's Tasks,
  which `/book:progress` brings up to date.
- **Write for the reader.** Answer first, then only what they need. For a non-engineer (BA, QA, DevOps,
  management): plain words, at most five points, and no id, file path or term left unexplained.

## Layout

| Path | Contains |
|------|----------|
| `docs/` | The documentation — owners in [Where knowledge lives](#where-knowledge-lives); closed history in `docs/archive/`. |
| `requirements/` | **Read-only**, for every skill too: the client's documents as snapshots; the index is `requirements/read-me.txt`. `fsd/` holds every revision as `- V<n>` — **build from the highest number** (a lexical sort puts V9 above V15). Only `/book:check-sources` adds to it *[enforced: `check-protected-paths.py`, pre-commit]*. |
| `devtools/` | Tooling, never product: the checks, the AI tools that hold a credential, and the session guard. |
| `.claude/book/` | The Book's common rules, this file among them: imported by the `CLAUDE.md` files, never edited here. |
| `database-scripts/` | Hand-written SQL only: seed data and dated one-off data fixes, **never schema** — no `CREATE`, `ALTER` or `DROP` of a table, index, view or type *[enforced: `check-code-rules.py`, pre-commit + CI]*. |

## Branches

- **Never push or commit to a protected branch** (`protected_branches` in `devtools/factory/factory.toml`); it
  is reached by pull request only.
- Cut work branches from the base branch (`base_ref`) **with `--no-track`**, so a push cannot land on it
  *[enforced: guard]*.
- **A doc change the work causes stays on the work's branch; a general docs refresh goes on a
  `chore/docs-<slug>` branch of its own** ([which is which](docs.md#which-branch-a-doc-change-goes-on)).

## Where knowledge lives

**One topic, one owner; everything else links to it.** Before writing anything down, find its owner here or in
the project's `CLAUDE.md`. Every doc in `docs/` and at the root has a row or a folder in one of them
*[enforced: `check-book.py`]*. How to write each one: `docs/CLAUDE.md`.

| Topic | Owner |
|---|---|
| What the product is, its vocabulary, users and flows | `docs/system-overview.md` |
| Architecture, conventions, naming, trade-offs, and **why** each rule exists | `docs/architecture.md` |
| Feature status, phases, dependencies; the latest FSD revision's changes | `docs/project.md` |
| **Rules decided for questions the requirements leave open** — *Resolved*, by topic | `docs/open-questions.md` |
| **A defect**: what is wrong, and what happened to it | `docs/defects.md` |
| One feature's design, and its tasks in build order with their role and status | its plan in `docs/plans/` — its Tasks section |
| Which QA test cases are ours, and whether we satisfy them | `docs/qa-artifact-test-suite.md` |
| **The FSD's requirements**, one row each, and whether each is built and proved | `docs/requirements/` — one register per module |
| FSD ↔ plan ↔ code ↔ test traceability — written only by `/book:fact-check` | `docs/Audit/` |
| Closed history — fixed defects, answered questions, earlier revisions | `docs/archive/` — **never a current rule** |
| Rules to follow while working | the `CLAUDE.md` files and the Book's rules they import (`.claude/book/`) |
| How the team uses AI tools | `AI-DEVELOPMENT-GUIDE.md` (a map, not a second rulebook) |
| How to run and build it | `README.md` |
| The tooling — checks, AI tools, the guard; the enforced-rules table | `devtools/README.md` |

**Is X decided?** Look under *Resolved* in `open-questions.md`, then the feature's plan. A decision found only
in the archive, a chat or a PR thread is not in force until it is in one of those.

## Features

- **The FSD is the source of truth and wins every conflict**; where it is silent, the supporting documents are
  the best evidence, and where it contradicts itself, ask
  ([Which document wins](../../docs/open-questions.md#which-document-wins)).
- **Every feature gets a plan before it gets code**, from `docs/plans/_template.md`, with a row in
  [`project.md` Part 1](../../docs/project.md#part-1--features-and-phasing).
- **The plan and the code stay in sync, in the same commit.** A deviation from an approved plan is discussed
  before it is built.
- **Update a feature's status in `project.md` Part 1, and a task's in its plan's Tasks, when it changes.**
- **Raise requirement, architecture, security, data-model and integration gaps before implementing.** A question
  the requirements do not answer goes to `open-questions.md` with a recommendation — never into a plan as a
  guess.
- **A missed FSD requirement in a completed module is a `defects.md` row**, not a silent rewrite.
- **Stay inside the feature's scope.** A drive-by fix is its own commit, tracked.
- **Change behaviour, fix the doc that owns it in the same commit.** Wrong documentation is worse than none.
- **Every confirmed defect gets a `defects.md` row the day it is found**, and its id goes in the fixing commit
  message *[enforced on `fix/*` branches, that the message names an id: `commit-msg` hook]*.
- **A fix must agree with the FSD — check before writing it**, and cite the section in the defect's note; if
  they disagree, stop and ask ([the full rule](docs.md#defects)).

## Code rules

The rest are in the project's `CLAUDE.md` and the nested ones for the code's folder. These hold wherever you
work:

- **Never commit a real secret** (a placeholder starts with `REPLACE`), nor put one in a commit message, branch
  name or test fixture.
- **Never log** passwords, hashes, tokens, reset or invitation links, one-time codes, connection strings, or
  customer-identifying data.
- **A tool reads the project from its settings — `devtools/factory/factory.toml` over the Book's defaults —
  never from a name in its code**; the Book's own files (`.claude/book/`, the checks, the guard, the defaults)
  are changed in the Book, never in the project *[enforced: `check-book.py`, the Book's files]*.

## Private settings

**Nothing personal or secret goes in the repository** — no credential or token, no path inside someone's home
folder, no person's email or machine name *[enforced: the guard on every write, `check-private-data.py` in
pre-commit and CI]*.

- **A person's own settings live in their private settings file**, `~/.boilerplate/local.toml` (or
  `BOILERPLATE_LOCAL_SETTINGS`), outside every repository; the tooling reads only the keys
  [`devtools/README.md`](../../devtools/README.md) lists. Credentials stay with the tool that
  owns them.
- **A session never reads, prints or copies them** — the private file, a credential store, a command that dumps
  one, or a secret variable *[enforced: guard]*. Check that a secret is set with `test -n`; never print it. The
  person edits their private file themselves.
- **A session reaches a service through a tool that holds the credential, never with it**, and never pastes a
  token into a conversation: the Git host through `book pr`, Jira through `book jira` (read only). A skill that
  needs a new service gets a tool, not a token.

## Building and testing

- **Build before committing**; warnings are errors.
- **Test what the change can break, not everything**: the fast suites and the touched module's tests before a
  commit, the full suite when the change is cross-cutting *[enforced: `book impact`, which picks the local
  run's tier]* (how: `tests/CLAUDE.md`).
- **While CI is suspended, run `sh devtools/checks/ci-local.sh` on the commit before opening a pull request**
  and paste the *Local CI* section it prints into the description *[enforced: `book pr open`, local CI]*. It
  runs as deep as the change reaches ([the local run](../../devtools/README.md)).
- **Report what ran** — the filter, and the executed, passed and skipped counts. A filter that matches nothing
  runs nothing, and a skipped test is not a pass.
- Every feature ships tests that prove its behaviour; never skip, delete or weaken a failing test.

## Git

- **Never delete a branch** — say it looks done, and stop there.
- **Never force-push or rewrite history on a shared branch.**
- **Jira-tracked work carries the key right after the type**: `fix/<KEY>-310-short-slug` (`[jira] project`),
  and in the PR title. Never invent or borrow a key; no issue, plain name. Our own `D`/`Q` ids go in commit
  messages.
- **A change to the files that steer the AI** — the `CLAUDE.md` files and `.claude/`, the AI tools, the guard,
  the checks, the hooks, CI — **is made or approved by an owner** (`[ai_files]` in
  `devtools/factory/factory.toml`) *[enforced: `book ai-files`]*. Its pull request names them on an *AI files*
  line, which the local CI run prints, so the approval is asked for before the merge *[enforced: `book pr open`
  checks the description's AI files line]*.
- **Run `/book:self-review` before opening a PR**, in a fresh session or a subagent — never in the session that
  wrote the change; `book pr open` refuses a commit without a recorded READY verdict — a reminder, not proof
  the review ran.
- **The agent opens a pull request only when the person asks**, from a pushed work branch, with `book pr open`
  — in the person's name, never into a protected branch but the base branch.
- **A pull request merges only with one approval from someone other than its author and a passing build**
  (while CI is suspended, its *Local CI* section): never merge past either warning.
- **A PR description has a summary, a test plan and an Effort line**; a Jira issue's PR also has *Dev testing &
  notes for FE/QA*, and while CI is suspended a *Local CI* section — `/book:ship` drafts them *[enforced: `book
  pr open` checks the description before it opens the PR; `book pr-check` reads it back]*.
- Commit messages are imperative, with no AI or tooling attribution and no trailer lines *[enforced, for
  attribution and trailers — not the mood: `commit-msg` hook]*.

## Jira

Where the project tracks its work in Jira (`[jira]` in `devtools/factory/factory.toml`):

- The issue's owner writes **Dev testing & notes for FE/QA** in the PR description and pastes it onto the issue
  when moving it to QA. **Dev testing:** FSD sections covered; PR, deployed commit and environment; automated
  results **with the skip count**; hand checks; open `D`/`Q` ids. **Notes for FE/QA:** deviations from the FE
  contract, behaviour that differs from a QA case, setup or data needed, known limitations — "none" rather than
  blank. A bug gets the short form: cause, fix, the FSD section it agrees with, how it was verified, notes.
- **Read an issue with `book jira <key>`**, which can only read. **Link, don't copy.** **Never put credentials,
  tokens, test passwords or customer data in Jira.** Claude drafts on request and posts only after approval.

## Skills

Each skill's rules are in its own `SKILL.md`, which loads when the skill runs. Which documentation skill
compares what: [the AI guide](../../AI-DEVELOPMENT-GUIDE.md#which-docs-skill). `/book:setup` verifies the
Book and creates what is absent.
