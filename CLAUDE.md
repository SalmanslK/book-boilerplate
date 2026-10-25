# Book Boilerplate

<!-- FILL: one paragraph — what this repository is, its stack, and how to build it.
Link to the product overview and the architecture guide once they exist. -->

## How these instructions are organised

This file holds the rules that apply everywhere. **Nested `CLAUDE.md` files load when
you work in their folder — read the one for where you are before changing anything
there:**

| Folder | Its rules |
|---|---|
| `docs/` | [`docs/CLAUDE.md`](docs/CLAUDE.md) — writing docs: current state vs history, defects, questions, plans, generated files |
| <!-- FILL: folder --> | <!-- FILL: its CLAUDE.md, and what it covers (e.g. migrations, endpoints). The install writes the testing rules, tests/CLAUDE.md, in each stack's test folder: give each a row. --> |

*[enforced: X]* marks a rule a test or check fails the build on; it is written here
so you do not trip it, not because anyone has to police it.

## Layout

| Path | Contains |
|------|----------|
| <!-- FILL: source folder --> | All application code. |
| <!-- FILL: tests folder --> | All tests. |
| `docs/` | The documentation — owners in [Where knowledge lives](#where-knowledge-lives). |
| `requirements/` | **Read-only.** The client's or product's requirement documents, as snapshots. The guard refuses edits (`[guard].read_only` in `devtools/factory/factory.toml`), and a commit that changes one is refused *[enforced: `check-protected-paths.py`, pre-commit]*. |
| `devtools/factory/` | The AI factory: the referee, the guard, its config and step prompts. |

## Where knowledge lives

**One topic, one owner; everything else links to it.** Before writing anything down,
find its owner here. Every doc in `docs/` and at the root has a row or a folder here
*[enforced: `devtools/checks/check-book.py`]*. How to write each one: [`docs/CLAUDE.md`](docs/CLAUDE.md).

| Topic | Owner |
|---|---|
| **The requirements** — read-only snapshots, one file per revision; build from the highest | `requirements/` ([`read-me.txt`](requirements/read-me.txt)) |
| What the product is, its vocabulary, users and flows | `docs/system-overview.md` |
| Architecture, conventions, and **why** each rule exists | `docs/architecture.md` |
| How a screen looks and works where the requirements and wireframes do not say | `docs/ui-standard.md` |
| Feature status and phases; the latest requirements revision's changes; the task roll-up (**generated** from the plans by `devtools/checks/gen-task-rollup.py`) | `docs/project.md` |
| Requirement ↔ plan ↔ code ↔ test traceability — written only by `/fsd-audit` | `docs/Audit/spec-traceability.md` |
| **Rules decided for questions the requirements leave open** — *Resolved*, by topic | `docs/open-questions.md` |
| **A defect**: what is wrong, and what happened to it | `docs/defects.md` |
| One feature's design, and its tasks in build order with their role and status | its plan in `docs/plans/`, from `docs/plans/_template.md` — its Tasks section |
| One factory run: intent, spec, design, plan, evidence, review | its file in `docs/factory/runs/` — created by `factory start`, never by hand |
| Every factory run in one table — **generated** | `docs/factory/runs/INDEX.md` (`factory index`) |
| <!-- FILL: generated docs (API contracts, schema) --> | <!-- FILL: path — **generated**, and the command that writes it --> |
| Closed history — fixed defects, answered questions, build logs | `docs/archive/` — **never a current rule** |
| Rules to follow while working | this file and the nested ones |
| How to run and build it | `README.md` |

**Is X decided?** Look under *Resolved* in `open-questions.md`, then the feature's
plan. A decision found only in the archive, a chat or a PR thread is not in force until
it is in one of those.

## Features

- **The requirements are the source of truth and win every conflict.**
  <!-- FILL: name the spec source (a document, a folder, a ticket system) and which
  revision is authoritative. --> Where they are silent, the supporting documents are
  the best evidence. A design showing what the requirements do not specify is not a
  requirement. Where the requirements contradict themselves, ask.
- **Every feature gets a plan before it gets code**, from `docs/plans/_template.md`.
- **The plan and the code stay in sync, in the same commit.** A deviation from an
  approved plan is **discussed before it is built**, not found in the diff.
- **A question the requirements do not answer goes to `open-questions.md` with a
  recommendation** — never into a plan or a spec as a guess.
- **Every confirmed defect gets a `defects.md` row the day it is found**, and its id
  goes in the fixing commit's message. A missed requirement in a completed feature is
  a defect row, not a silent rewrite.
- **Stay inside the feature's scope.** A drive-by fix is its own commit, tracked.
- **Change behaviour, fix the doc that owns it in the same commit.** Wrong
  documentation is worse than none.

## Coding principles (every stack)

These hold in every language and framework, and the reviewer checks each change against
them: a breach is a finding, not a matter of taste. Why each exists:
[`docs/architecture.md`](docs/architecture.md#coding-principles-every-stack).

- **Less code is better code, inside the architecture's boundaries.** Build what the
  requirement asks and nothing speculative: no interface or base class without a second
  implementer in sight, no option or setting nobody asked for, no wrapper that only
  renames a library call. Delete dead code and commented-out code. Shorter code that
  crosses a layer boundary is not better: the boundary wins.
- **Neat and understandable.** Names say what a thing is or does, so the code needs few
  comments; rename unclear code rather than explain it. Short functions with one job
  each, no deep nesting (return early). No magic numbers: a named constant says what one
  means.
- **Efficient by design.** No query or call inside a loop; read only the columns and
  rows needed; page long lists; do once what can be done once. Beyond that, measure
  before tuning: clarity wins over a micro-optimisation nobody measured.
- **Reusable, without being clever.** Shared logic lives in one place, named for what it
  does. Write it twice before you abstract it; abstract it the third time. Prefer a
  maintained library over writing your own, kept behind one file.
- **The basics, always:** validate input at the boundary and trust nothing from outside;
  no secrets in code; immutable by default; one responsibility per class or module;
  every change ships with tests that prove it.

**The rules that make those concrete**, in every stack. Where a regular expression holds
a rule's plain form, the stack's pack (`[[code_rules]]` in `devtools/factory/factory.toml`)
refuses it at every commit; the rest are the stack's own tests and lint, or review.
Why each: [`docs/architecture.md`](docs/architecture.md#the-rules-every-stack-keeps).

- **Time comes from an injected clock**, never the system clock read in place *[enforced: `check-code-rules.py`, pre-commit]*.
- **Never block on asynchronous work** — await it all the way up; the stack's pack refuses the blocking calls where its language has them.
- **Long-running work can be cancelled**: the caller's cancellation signal is passed on to the last call, never replaced by an empty one (the .NET pack refuses `CancellationToken.None`).
- **Catch only the failures you can handle and let faults surface** — no catch-all, no empty catch *[enforced: `check-code-rules.py`, pre-commit]*.
- **Log through the logger, with named fields, never a stray print** *[enforced: `check-code-rules.py`, pre-commit]*; never a secret, token or personal data in a log.
- **Name things for what they are** — never a Helper or Utility type *[enforced: `check-code-rules.py`, pre-commit]*, nor a Manager or Processor.
- **Say "exactly one" when you mean it**: take the single match and fail on two; never an arbitrary first — order the query when several are legitimate.
- **Dependencies point inward, and the boundaries are checked** by the stack's own layer test or import rule, never by review alone.
- **Configuration is typed and validated at start**: a missing setting stops startup, and a placeholder value is recognisable (it starts with `REPLACE`).
- **A string that must match another is one constant beside the code that owns it** — a route, a tag, a key, a message — never repeated, never a global constants file.

## Code rules

<!-- FILL: the project's code rules — layering, naming, error handling, dependency
policy, style. Say which ones a test or check enforces, and mark them *[enforced: X]*.
Keep the "why" for each in the architecture guide, not here. -->

- **Never commit real secrets**, a path inside someone's home folder, or a person's
  email or machine name *[enforced: `check-private-data.py`, pre-commit]*.

## The AI factory

A backlog item goes through the factory as a **run**: one branch, one run file in
`docs/factory/runs/`, and the steps `devtools/factory/factory.toml` lists. `/factory`
drives a run in your own session; `factory drive` drives it unattended.

- **The guard** (`devtools/factory/hooks/guard.py`, wired in `.claude/settings.json`)
  reads every file write and shell command before it runs and refuses what this file
  says never to do — the rules are `[guard]` in `factory.toml`. Some writes it **asks**
  about instead: the factory itself, settings, CI and git hooks.
- **Never route around a guard refusal** — not with another tool, a shell redirect, a
  script or a renamed path. A refusal means stop: fix the approach, or ask the person.
  The guard is a first layer; CI re-checks what reaches a pull request.
- **The referee** (`devtools/factory/factory.py`) checks each step **itself** and
  advances only on a pass. A session's word that a step is done is never taken.
- **Gates are a person's.** The spec gate, the design gate (High tier only) and the
  merge gate are decided by people: `factory approve` from a terminal that is not a
  Claude Code session (or the Factory app), and at the merge gate the pull request
  approved by someone other than its author — or, in a project with no remote
  (`check = "local-approval"`), a person's `factory approve merge`.
  An agent never runs `factory approve`, `unlock`, `abandon` or `open-pr` *[enforced: guard]*.
- **Tests are proven red, then locked.** A change criterion's tests must fail before the
  build; then they are locked by hash, and only a person's `factory unlock` releases
  one *[enforced: guard]*. A criterion marked *(guard)* pins behaviour that must not
  break: its tests pass before and after.
- **A run is measured by its branch's own commits** since its base
  (`git log --first-parent --no-merges <base>..HEAD`) — merges of the base branch are
  not the run's change.
- **Escalate instead of guessing.** A session that cannot make progress writes an
  `## Escalation` section in the run file saying what a person must decide, and stops.
- **A defect a run introduced** says *Introduced by run [R-nnn](factory/runs/R-nnn.md)*
  in its defect row, so `factory report` can count what escaped each run.

## Git

- **Protected branches are reached by pull request only — never push or commit to
  them** *[enforced: guard]*: <!-- FILL: e.g. `main`, `dev` — the same list as
  `protected_branches` in `factory.toml` -->.
- **Cut work branches from the base with `--no-track`**
  (`git checkout --no-track -b <type>/<slug> <base_ref>`), so a bare `git push` cannot
  land on the base.
- **Never delete a branch, never force-push or rewrite history on a shared branch**
  *[enforced: guard]*.
- Commit messages are imperative, with **no AI or tooling attribution and no trailer
  lines** *[enforced: `commit-msg` hook]*. On a `fix/*` branch the message names the
  defect or question id, or the tracker key, it fixes.
- **The git hooks in `.githooks/` run on every commit**: the install set
  `core.hooksPath`, and a new clone runs `git config core.hooksPath .githooks` once.
  **Never skip them** (`--no-verify`) *[enforced: guard]*: a hook that refuses names
  what to fix.
- <!-- FILL: ticket keys in branch names and PR titles, if the team uses a tracker. -->
- **A pull request description has a summary and a test plan.** `factory pr-body`
  writes one from a run.

## Building and testing

- **Build before committing**: <!-- FILL: the build command — the same as
  `[commands].build` in `factory.toml` -->.
- **Test what the change can break, not everything**: the fast suites always, and the
  integration tests of the modules you touched. Run the full suite when the change is
  cross-cutting. <!-- FILL: the test commands, and what counts as cross-cutting here. -->
- **Report what ran** — the command or filter, and the executed, passed, failed and
  **skipped** counts. A suite that skips because a database or service is missing, or a
  filter that matches nothing, looks like a pass; neither is one.
  `devtools/checks/check-test-results.py` fails a results file that skipped, ran nothing
  or is missing.
- Every feature ships tests that prove its behaviour. **Never skip, delete or weaken a
  failing test.**
