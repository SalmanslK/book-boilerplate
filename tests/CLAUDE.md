# Tests — rules for the tests

Loaded when you work in the tests folder. The root [`CLAUDE.md`](../CLAUDE.md) still
applies; the reasoning is in [`docs/architecture.md` §7](../docs/architecture.md#7-testing).

## What a feature must prove

- **Every feature ships with tests that prove its behaviour**, not just enough to keep
  the suite green. Each acceptance criterion has a test tagged with it, and the error
  cases the requirement names are criteria too.
- **Isolation is proven, not assumed.** Where data belongs to a tenant, an account or a
  user, a test seeds two of them and asserts one cannot see or change the other's rows.
  A test that only checks the type implements the right interface does not prove the
  filter holds end to end.
- **A list asserts a fixed number of queries**, whatever the number of rows, so a query
  inside a loop fails a test instead of a production page.
  <!-- FILL: how a test counts queries in this stack, and the test to copy. -->
- **A bug fix starts with a test that fails for the bug**, then passes with the fix.

## How tests are written

- <!-- FILL: the test framework and the assertion library, and why (a licence, a team
  habit). -->
- **Unit tests touch no database, network or file system.** What needs one is an
  integration test, and says so in its name or folder.
- **Test what a user or caller sees**, not how the code does it: a test tied to private
  details breaks on every refactor and proves nothing new.
- **Never skip, delete or weaken a failing test** — a loosened assertion, a widened
  tolerance or a retry until green is the same failure. Fix the code, or say plainly
  that the test is broken and log it.

## Choosing what to run

Which tests a change needs is the rule in [`CLAUDE.md`](../CLAUDE.md#building-and-testing);
this is how.

- **The fast suites — always, whole.** They take seconds, and they hold the rules one
  feature's tests never exercise. <!-- FILL: the commands. -->
- **The slow suites — the module's tests, plus any test you changed.** A change another
  module calls into takes that module's tests too. <!-- FILL: how to filter to one
  module. -->
- **Read the counts.** The total must be the number you expected, not `0`: a mistyped
  filter matches nothing and exits green.

## Reporting a run

- **Report what ran:** the command or filter, and the executed, passed, failed and
  **skipped** counts.
- **A skip is a failure until a person says otherwise.** A suite that skips because a
  database or service is missing, or a filter that matches nothing, looks identical to a
  pass. `devtools/checks/check-test-results.py` fails a results file that skipped, ran
  nothing or is missing.
- A test that is meant to skip in some runs (it needs a deployed host or an outside
  service) is excluded from the gated run by its tag, so the gate never has to forgive a
  skip.

## Test data

- **Everything a run creates, it deletes**, or it works in a database or folder made
  for that run alone. Tests never depend on each other's data or on the order they run.
- **A test database's name says it is one** (`test` in its name), and the tests refuse
  to run against one that does not. Never point a test at anything real.
- **No real secrets, customer data or personal details in a fixture.** Made-up values
  only, recognisably made up.

## Rule tests

Where a mechanical rule becomes enforced — layering, naming, banned calls — it is a test
that reads the code. **Never suppress one to get a green build — fix the design.** An
allow-list entry is added visibly, with its reason.
<!-- FILL: where the rule tests live, and how they read the source. -->
