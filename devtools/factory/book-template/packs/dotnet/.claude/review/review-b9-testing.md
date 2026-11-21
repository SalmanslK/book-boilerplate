# B9 · Testing — checks 133–147

Part of the review checklist; its method is `review-method.md`, in the book plugin (`book:peer-review/references/`).

| # | Check | Apply it here |
|---|---|---|
| 133 | Missing test for core business behaviour | The rule the FSD names, not incidental code around it |
| 134 | Missing negative-path test | Invalid input, forbidden transition, wrong actor |
| 135 | Missing edge-case test | Boundary values, empty collections, first/last page |
| 136 | Missing validation test | Each validator rule exercised at least once |
| 137 | Missing authorization test | **Always qualifies** for security-critical logic — a new gated endpoint needs a test asserting that a role holding the permission passes and one without it is refused, not just that the catalogue compiles |
| 138 | Missing tenant-isolation test | **Always qualifies** — cross-tenant leakage is Critical, and an aggregate without one is unverified on the one thing that matters most |
| 139 | Missing integration test where integration behaviour matters | EF query shape, filter interaction, transaction behaviour — none of this is provable by a unit test |
| 140 | Missing persistence/database test where database behaviour matters | Constraint, index, migration behaviour against real Postgres |
| 141 | Missing concurrency test where concurrency matters | Optimistic concurrency, idempotent retry — only where the change introduces one of these |
| 142 | Missing regression test for fixed defect | Reference the defect id (`D7`, etc.) in the test name or nearby comment, per `docs/defects.md`'s own convention |
| 143 | Test verifies implementation instead of behaviour | Comparing a computed page size against the constant that produced it proves the wiring, not the FSD's specified value |
| 144 | Test has weak or meaningless assertions | Asserting "no exception thrown" or a non-null result where the actual value matters |
| 145 | Test depends on fragile setup/state | Shared mutable fixtures, ordering-dependent tests |
| 146 | Test unnecessarily coupled to external services | A unit test that shouldn't need Postgres/SMTP but does |
| 147 | Test suite does not adequately prove the requirement | Green tests that, read together, still leave a stated FSD behaviour unverified |

**Check the skip count, not the colour.** Integration tests skip silently
without a database — `BOILERPLATE_TEST_DB` or Docker — and a run that executed
nothing looks identical to one that passed.
