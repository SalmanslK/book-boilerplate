# Tests — rules for the tests

Loaded when you work in the tests folder. The root [`CLAUDE.md`](../CLAUDE.md) still applies; the
reasoning is in [`docs/architecture.md` §4](../docs/architecture.md#4-testing).

The boilerplate's only code is its tooling, and its tests are the **self-tests** beside the code they
test: `devtools/factory/hooks/test_guard.py`, `devtools/factory/test_*.py`, `devtools/checks/test_checks.py`.
A stack pack brings its own `tests/CLAUDE.md` for the project's product tests.

## What a change must prove

- **Every behaviour ships with a self-test that proves it**, and a fix starts with a test that fails for
  the bug, then passes with the fix.
- **A self-test runs against a throwaway repository or `fixture_project.py`**, never against this
  repository's own state — unless the test is about this repository's own configuration, and says so.
- **Nothing reaches the network.** A command that would call a service, SSH or git remote goes to a fake
  runner that answers from a script.
- **Never skip, delete or weaken a failing test** — fix the code, or say plainly the test is broken and log it.
- **Twice becomes a test.** A review finding, or a defect class, found a second time gets a self-test or a
  check in `devtools/checks/` with its fix, not a third review comment.

## Choosing what to run

- `python devtools/checks/run-self-tests.py <folder>` for each folder the change reaches:
  `devtools/factory/hooks`, `devtools/factory`, `devtools/checks`. A change to the guard, a shared check,
  `factory.toml` or the template runs all three.
- One class while working: `python -m unittest <module>.<Class>` from the folder.

## Reporting a run

- **Report what ran:** the folder, and the executed, passed, failed and **skipped** counts.
- **A skip is a failure**, and so is a folder that ran no tests: `run-self-tests.py` fails both.
