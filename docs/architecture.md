# Book Boilerplate — architecture

How the boilerplate is built, and **why** each rule exists. The rules themselves are in the `CLAUDE.md`
files; this guide is the reasoning behind them. Each section is linked from an instruction file.

## 1. Overview

Three parts, each with one job:

| Part | Where | Job |
|---|---|---|
| **The Book** | `CLAUDE.md`, `docs/`, `requirements/` | What is true: one owner document per topic |
| **The checks** | `devtools/checks/`, `.githooks/` | Fail a commit or a local CI run on a broken rule |
| **The Book CLI and the guard** | `devtools/factory/` | Set a Book up and keep it current (`book init`/`verify`/`upgrade`), work with the Git host and tracker, and refuse in-session what the rules forbid |

**Every project-specific value lives in `devtools/factory/factory.toml`.** A tool reads its names,
branches, paths and keys from there and carries none of its own. Why: the same tooling then serves
every project, and `book verify` can say a leftover name from another project is a defect.

## 2. Where code lives

- `devtools/checks/` — one script per rule family, each runnable alone, each with its self-tests in
  `test_checks.py`. Standard library only, Python 3.11+.
- `devtools/factory/` — `factory.py` (the CLI's commands, run as `devtools/book.py`), one module per outside system (`bitbucket.py`,
  `jira.py`, `drive.py`), `book.py` (init, verify and upgrade), `deploy.py` (the deploy steps, used when
  `[deploy]` is set), `hooks/guard.py`.
- `devtools/factory/book-template/` — `manifest.toml` (what `init` copies and `verify` expects),
  `skeletons/` (the owner documents with `<!-- FILL -->` slots) and `packs/<pack>/` (files at their
  target paths).

**Packs live inside the template, never at their target paths here.** Why: a pack's rules
(`src/CLAUDE.md`'s *[enforced]* markers, its checks) would otherwise be read as this repository's live
rules, and `check-rules.py` would demand their test classes. `check-rules.py` reads every tracked
`CLAUDE.md` outside `book-template/`.

## 3. Conventions

### Configuration over code

A check that needs a project fact reads `factory.toml`: `base_ref` and `protected_branches`, `[book]`
(names and title, stack, packs, roles, the *Found by* adoption date, the phase-table columns, the
recommendation heading, whether a plan's derived status is enforced), `[guard]`, `[requirements.sources]` (the snapshot folders), `[commands]`,
`[checks] fix_id`, `[jira] project`. An empty value turns the feature off and the tool says so (`N/A`), never `OK`.

### One guard, rules as data

The guard holds no project's rules: `[guard]` does. Its self-tests run against a full project's rules
(`GUARD_RULES` in `test_guard.py`), so every Book's guard is held to the same behaviour.

### Upgrades: three copies, and a project's change always wins

`book upgrade`, run from a newer boilerplate, compares each file the template owns in three
copies: the project's, the installed version's and the new template's. The installed version is
`book_version` in the project's `factory.toml` (init writes it; a Book from before 1.2 falls back to
its copied `book-template/manifest.toml`), rebuilt from the boilerplate's git tag `v<version>` with the
project's names. A file equal to the installed copy was never edited, so `--apply` may replace it;
a file that differs is the project's, and is only listed, with the template's change as a diff.
Why: without a record of what was installed, a newer template cannot tell a stale file from a
deliberate edit, and a copy-over either drifts or destroys work. The tags are the record, so a
project carries no lock file. Why the report is the default: every change it would make is shown
before it is made. The skeletons' documents, `docs/` (but the plan template) and `requirements/`
are never compared: they are the project's content from the moment init writes them.

### Self-tests against a fixture project

The factory self-tests use `fixture_project.py` — a full project's `factory.toml` with the dotnet,
deploy, bitbucket and fe-guides packs and a module map — not the boilerplate's own, which has few rules.
A test about this repository's own configuration reads the real file.

### Coding principles (every stack)

The rules are in [`CLAUDE.md`](../CLAUDE.md#coding-principles-every-stack); the reviewer
checks every change against them. Why each one:

- **Less code, inside the boundaries.** Every line is read, tested and kept by someone
  after it is written, so code nobody asked for costs without paying back: an option
  nobody sets is a path nobody tests, and an interface with one implementer is two
  places to change instead of one. A boundary costs a few lines and keeps a change in
  one layer from reaching every other, so when the two collide the boundary wins.
- **Neat and understandable.** Code is read far more often than it is written, by people
  and by the AI that changes it next. A name that says what a thing does cannot drift
  from it the way a comment can; a short function with one job can be tested and
  replaced on its own; an early return keeps the main path at the left margin. A named
  constant says what a number means, and changes in one place.
- **Efficient by design.** The costly mistakes are in the shape of the code, not its
  speed: a query inside a loop runs fast on ten test rows and times out on ten thousand
  real ones, and no later tuning fixes the shape. Below that, a guess about speed is
  usually wrong, so a tuning change waits for a measurement.
- **Reusable, without being clever.** A rule written in two places drifts until the two
  disagree. But an abstraction made from one example guesses at the second and usually
  guesses wrong; by the third, the common part is known. A maintained library carries
  its own tests and fixes, and behind one file it can be replaced.
- **The basics.** Each closes a door a past project left open: input trusted from
  outside, a secret committed with the code, a shared object changed from two places, a
  class with three reasons to change, and a change with nothing to prove it works.

### The rules every stack keeps

The rules are in [`CLAUDE.md`](../CLAUDE.md#coding-principles-every-stack); each is the
concrete form of a principle above, and holds in any language. Why each one:

- **Time comes from an injected clock.** Code that reads the system clock in place cannot
  be tested at midnight, at a month's end or across a time-zone change without waiting
  for one; a clock passed in lets a test set the time. One module may read the real
  clock, and every other file gets it from there.
- **Never block on asynchronous work.** A thread held waiting for a task is a thread the
  server cannot use, and under load the waits pile up until requests time out; where a
  framework schedules work back onto the waiting thread, a block is a deadlock. Await
  all the way up.
- **Long-running work can be cancelled.** A person who closes the page, or a caller that
  times out, should stop the work it started. A signal dropped halfway, or replaced by an
  empty one, keeps a query or a download running for nobody.
- **Catch only what you can handle; let faults surface.** A catch-all that keeps a request
  alive turns a fault into a wrong answer nobody sees, and the first sign is bad data
  weeks later. Catch the failure you expect and can turn into an answer; let the rest
  reach the one top-level handler that logs it and fails the request.
- **Structured logging, never secrets or personal data.** A log with named fields can be
  searched and counted; a sentence pasted together cannot. A print bypasses the logger's
  level, format and destination, so it is lost in production or floods it. Logs are kept
  long, copied widely and read by many: a password, token or personal detail in one has
  leaked.
- **Name things for what they are.** A Helper, Utility or Manager says only that the code
  is somewhere; it grows into the place every unrelated function lands. A name that says
  what the thing does keeps it to that.
- **Say "exactly one" when you mean it.** Taking the first of several matches hides the
  day a second one appears — a duplicate row, a reused key — and returns whichever the
  store happens to give. Asking for the single match fails loudly on two; where several
  are legitimate, an explicit order makes the choice repeatable.
- **Dependencies point inward, and the boundaries are checked.** The business rules should
  not change when the framework, the database or the screen does. A boundary kept only
  by review erodes one convenient import at a time; a test or lint rule that fails the
  build holds it.
- **Configuration is typed and validated at start.** A setting read by name where it is
  used fails on the first request that needs it, perhaps days after a deploy; read once
  into a typed object and checked at start, a missing or malformed value stops the
  deploy instead. A placeholder that starts with `REPLACE` is recognisable to that check
  and to a reader, where a plausible-looking dummy value is not.
- **One constant beside its owner.** Two copies of a string that must match — a route and
  its link, a tag and its filter, a message and its test — drift until they do not. One
  constant, beside the code that owns it, changes in one place; a global constants file
  instead gathers every unrelated string and is owned by nobody.

**How the plain forms are enforced.** `devtools/checks/check-code-rules.py` reads each
stack's `[[code_rules]]` from `devtools/factory/factory.toml` — an id, a regular
expression, the files it applies to, the files exempt (the one clock module, a test, the
top-level fault handler) and a message citing the rule — and refuses any line that
matches. The pre-commit hook runs it on the lines a commit adds; run without `--staged`,
it reads every tracked file. It is a regular expression, so a pack holds only what one can
hold with few false positives: the system clock read in place, a blocking wait, an empty
cancellation token, a catch-all, a stray print, a Helper or Utility type. The rest are the
stack's own tests and lint rules, or review. A false positive is fixed in the rule or by
an `exempt` entry with its reason, never by working around the pattern.

**A site kept on purpose is counted, not exempted.** A rule's `allowed` entries name a file,
the number of matches it keeps and the reason; the full run fails when a file holds more (a
new site) or fewer (a stale entry), so the list is a fact about the code, not a hope. A
`require` rule turns the pattern round: at least so many matches must exist, once
`require_when` says the rule is due (a migration test, once there is a migration). The
template ships every list empty; each project's own entries, with their reasons, live in its
`factory.toml`, which the template never overwrites.

**Python checks, stack-neutral where the idea is.** A rule a regular expression can hold is a
`[[code_rules]]` entry, so any stack can keep it; one that needs a language's shape — a
validator behind a list, an index's columns and filter — is a pack's checker (the dotnet
pack's `check-dotnet.py`), never a test inside the product. The docs rules (the defect
ledger's words and classes, a defect id in a fix commit, an FE guide per changed route, an
*AI files* line in a pull request) read only Markdown and git, so they hold on every stack.

## 4. Testing

The tooling's only build is its self-tests: `python devtools/checks/run-self-tests.py <folder>` over
`devtools/factory/hooks`, `devtools/factory` and `devtools/checks`. It runs each folder's tests across
processes, checks the count against discovery, and fails on zero tests or any skip — a self-test that
skips proves nothing. A pack's own self-tests (`test_checks_dotnet.py`, `test_review_dotnet.py` …) run
in a Book that installed the pack; here, `test_book.py` installs each stack and pack into a scratch
repository and checks what lands, and that no source name survives.

### CI

There is no CI runner for the boilerplate. `sh devtools/checks/ci-local.sh` is the pull request's gate
(`[local_ci] required`): the repository checks, the self-tests, and `[commands]`. The bitbucket pack adds
`bitbucket-pipelines.yml` and `check-pipeline.py` for a project that has a runner.

## 5. Adding a new feature

A plan from `docs/plans/_template.md`, a row in `docs/project.md` Part 1, then the code with its
self-tests, the doc that owns the behaviour changed in the same commit, `/book:self-review`, and the local CI.

## 6. Naming

Scripts are `check-<rule>.py` (fails on a broken rule), `gen-<doc>.py` (writes a generated document;
`--check` fails when it is stale) and `test_<module>.py` (self-tests). Never a Helper or Utility module.

## Known trade-offs

| Trade-off | Why we accept it |
|---|---|
| The Book CLI is `book` (`devtools/book.py`); `devtools/factory/factory.py` is the module it runs and no command: run, it says so and exits 2, and the guard still judges a deploy through it | A hook or pipeline left on the old name fails, rather than passing with nothing run; a project mid-upgrade may keep an older copy that still deploys, so the guard's deploy rule still covers that name. The pipelines' AI-files step refuses a destination branch from before v1.4.0, which has no `devtools/book.py`, rather than letting the pull request judge itself ([the v1.5 plan](plans/book-v1.5.md)). In a Book + Factory install the referee goes to `devtools/ai-factory/` |
| `ai-files`, `pr` and `protection` speak Bitbucket only | The projects using the Book are on Bitbucket; with no Bitbucket remote they report `N/A`, never `OK`. GitHub is a later pack |
| The boilerplate has no CI runner | It is tooling only; `ci-local.sh` runs the same checks, and its PASS is required by `book pr open` |
| A pack's tests run only in a Book that installed it | They test files that are not live here; `test_book.py` installs every stack and pack into a scratch repository and checks the files land; the pack's own tests run in the Book that installed it |
