# <!-- FILL: project name --> — Architecture

This document records the architecture of <!-- FILL: project name --> and, more
importantly, **why** each decision was made. It is the answer to "where does this go?"

It describes the code as it stands. Where something is designed but not built, that is
stated in place rather than left to be discovered.

**It owns the reasoning** — why each convention exists, and the trade-offs behind it —
and changes when a decision is made. It states what is true now; superseded history is
in [`archive/architecture-history.md`](archive/architecture-history.md). Which document
owns every other topic is the table in
[`CLAUDE.md`, *Where knowledge lives*](../CLAUDE.md#where-knowledge-lives).

- Features are built from plan documents in [`plans/`](plans/), indexed with their
  status and dependencies in [`project.md`](project.md#part-1--features-and-phasing).
- Questions the requirements have not answered:
  [`open-questions.md`](open-questions.md).

### The *[enforced: X]* convention

A rule a test or check fails the build on is marked *[enforced: X]*, where X names the
test, check or guard rule. The mark is there so you do not trip the rule, not because
anyone has to police it. **A rule without the mark is kept by review**; when a rule is
broken twice, consider enforcing it. The rule is stated in `CLAUDE.md`; the reason for
it is stated here, once.

---

## Contents

| | |
|---|---|
| [1. Layout and layering](#1-layout-and-layering) | the modules, and what may depend on what |
| [2. Where code lives](#2-where-code-lives) | the folder layout, and the rule for placing a new file |
| [3. Conventions](#3-conventions) | each convention, and why it exists |
| [4. Errors](#4-errors) | expected failures versus faults |
| [5. Data](#5-data) | storage, schema changes, and what is never done by hand |
| [6. Cross-cutting decisions](#6-cross-cutting-decisions) | one table, every technology choice |
| [7. Testing](#7-testing) | the suites, and what each guarantees |
| [8. CI](#8-ci) | every step, and why it is there |
| [9. Adding a new feature](#9-adding-a-new-feature) | the checklist |
| [10. Naming](#10-naming) | what a thing is called, and what it is never called |
| [Dependencies](#dependencies) | when to take a library, and the exit for each risky one |
| [Environments and deployment](#environments-and-deployment) | the environments, and what is still missing |
| [Not built yet](#not-built-yet) | designed, documented, absent from the code |
| [Known trade-offs](#known-trade-offs) | deliberate compromises, and what would change them |

---

## 1. Layout and layering

<!-- FILL: the modules or projects, one line each, and the direction dependencies may
point. Say which test enforces it, and mark it *[enforced: X]*. -->

| Module | Contains | May depend on |
|---|---|---|
| <!-- FILL --> | <!-- FILL --> | <!-- FILL --> |

**Why:** <!-- FILL: what the layering protects — e.g. business rules that do not change
when the framework or the database does. -->

## 2. Where code lives

<!-- FILL: the folder layout of one feature, and the rule for deciding where a new file
goes. A worked example or two settles more arguments than the rule alone. -->

## 3. Conventions

One subsection per convention: **the rule, then why.** A rule whose reason is not
written down gets removed by the first person it inconveniences.

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

### <!-- FILL: convention -->

<!-- FILL: the rule in one sentence; *[enforced: X]* if it is. **Why:** the problem it
prevents — a defect id if one prompted it. -->

## 4. Errors

<!-- FILL: how an expected failure (validation, not found, conflict) is returned, how a
fault is raised, and how each reaches the caller. What is never caught. -->

## 5. Data

<!-- FILL: the store, how the schema changes (migrations), what is generated, and what
is never done by hand. Link the generated schema doc if there is one. -->

## 6. Cross-cutting decisions

| Concern | Decision | Why |
|---|---|---|
| <!-- FILL: e.g. logging, time, identity, configuration --> | <!-- FILL --> | <!-- FILL --> |

## 7. Testing

<!-- FILL: each suite — what it covers, what it needs to run (a database, a service),
and what it guarantees. How to run one feature's tests. What makes a skipped test
visible. -->

| Suite | Covers | Needs | Command |
|---|---|---|---|
| <!-- FILL --> | <!-- FILL --> | <!-- FILL --> | <!-- FILL --> |

## 8. CI

<!-- FILL: the CI file, and which branches run which steps. -->

| Step | What it does | Why |
|---|---|---|
| **Build** | <!-- FILL --> | Warnings are errors, so a warning cannot accumulate |
| **Tests** | <!-- FILL --> | <!-- FILL: including how a skipped or empty suite fails the build --> |
| **Generated docs match the source** | <!-- FILL: each generator with `--check` --> | A hand-edited or stale generated doc is caught before merge |
| **Factory self-tests** | `python3 -m unittest` over the factory's tests, `factory.py index --check`, `factory.py verify --all` | The guard is a first layer; CI re-checks what reaches a pull request |
| **No secrets in settings** | <!-- FILL --> | <!-- FILL --> |
| **Dependencies** | <!-- FILL: licence and vulnerability checks --> | <!-- FILL --> |

## 9. Adding a new feature

1. **A row in [`project.md`](project.md#part-1--features-and-phasing)** and a plan from
   [`plans/_template.md`](plans/_template.md), before any code.
2. **Questions the requirements leave open** go to
   [`open-questions.md`](open-questions.md) with a recommendation.
3. <!-- FILL: the build order — which layer first, and what compiles before the next
   step. -->
4. **Tests that prove the behaviour**, including <!-- FILL: the project's mandatory
   cases, e.g. authorization, isolation between tenants -->.
5. **Regenerate the generated docs** the change reaches: <!-- FILL -->.
6. **Update the owning docs in the same commit** — the plan, the feature's status in
   `project.md`, and <!-- FILL: any guide for consumers of the feature -->.

## 10. Naming

<!-- FILL: what each kind of thing is called, and the names never used (e.g. a type
whose last word is Helper, Manager or Utility). *[enforced: X]* where it is. -->

---

## Dependencies

**Prefer a maintained open-source library over writing our own**; write our own only
when the surface is small, the dependency carries risk, and it stays behind one file.
**Every third-party library stays isolated to one place.**

| Library | Licence | Where it is isolated | Exit if it has to go |
|---|---|---|---|
| <!-- FILL: licence-sensitive or risky dependencies only --> | | | |

## Environments and deployment

<!-- FILL: each environment, what deploys to it and from which branch, and how
configuration reaches it. What is still missing. -->

## Not built yet

<!-- FILL: designed and documented here, absent from the code. Delete an entry when it
is built. -->

## Known trade-offs

Compromises **considered and deliberately not fixed**. Each names what would change the
decision, so it is revisited on evidence rather than on someone's memory. A defect
surprised us; a trade-off is a thing we chose.

| Trade-off | Why it stands | Revisit when |
|---|---|---|
| <!-- FILL --> | <!-- FILL --> | <!-- FILL --> |
