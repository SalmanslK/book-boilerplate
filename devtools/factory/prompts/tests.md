## Step: tests

Write the tests that prove the Spec's criteria — **before** any product code.

- Every criterion gets at least one test tagged with its id, in the form the
  `[adapter]`'s `criterion_marker` in `devtools/factory/factory.toml` matches — for
  example xUnit `[Trait("AC", "R-001.AC-1")]`, pytest `@pytest.mark.ac("R-001.AC-1")`,
  Vitest a tag in the test's name, `it("shows the total [AC R-001.AC-1]", …)`.
  A test the marker does not match is not counted. With several stacks (`[[stacks]]`,
  each in its folder), each stack's `[stacks.adapter]` says its own form, and its
  tests go under its folder.
- Follow the project's test rules (its CLAUDE.md, and the one in the tests folder if
  there is one): the assertion library, the fixtures, what an integration test needs,
  and any isolation a feature must prove.
- A screen's tests find what they check by role and accessible name (`getByRole`,
  `getByLabelText`), never by test id, class or markup — so a screen a keyboard or screen
  reader cannot use fails its own tests.
- A change criterion's tests must **fail now**, for the reason the criterion names — or,
  on a compiled stack, fail to compile only because a type or member the build step
  will add does not exist yet. A test that already passes does not test the change.
- A **guard** criterion's (`*(guard)*` in the Spec) tests must **pass now** — they pin
  behaviour the change must not break — and the referee checks they do.
- **Reopened after the fix** (a reviewer asked for more tests): add or extend **guard**
  tests only. A change criterion's red proof carries over only while every file carrying
  it is byte-for-byte what was locked — put new tests in files that carry guards, or in
  new files, and leave the change criteria's tests alone.
- If a change criterion cannot honestly fail before the fix, do not force it: write why
  to the Escalation section. The Spec may need that criterion to become a guard, which
  reopens the spec and a person approves it again.
- Change only the test files this step may write (`writes` for `tests` in
  `factory.toml`, and each stack's `[stacks.writes] tests` under its folder). Commit the tests, then check. The referee builds, runs each
  criterion's tests, confirms they fail, and **locks** them: after that no one may
  change them without a person's `factory unlock`.
- Open the failing logs under the evidence directory (`evidence_dir` in `factory.toml`)
  and confirm each failure is the one you meant, not a setup error.
