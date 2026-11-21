# Tests — rules for the test projects

Loaded when you work under `tests/`. The root [`CLAUDE.md`](../CLAUDE.md) still
applies, and so do the style rules in [`src/CLAUDE.md`](../src/CLAUDE.md) — test code is code; the
reasoning is in [`docs/architecture.md` §11](../docs/architecture.md#11-testing).

## What a feature must prove

- **Every feature ships with tests that prove its behaviour**, not just enough to keep
  the suite green.
- **A tenant-scoped feature needs an integration test that seeds two tenants** and
  asserts one cannot see the other's rows. `TenancyTests` only proves the interface is
  implemented, not that the filter holds end to end. Company-scoped features prove the
  company filter the same way.
  Copy `CompanyScopedAggregateTests` — it proves the generic filter on a test-only aggregate
  and fails if the filter is loosened.
- **A request body that carries a list is tried with a null element** (`{"ids":[null]}`):
  the answer is a refusal that names the problem, never a 500. No endpoint takes a list today;
  the first one writes the pattern.
- **A list endpoint asserts a fixed query count**, independent of row count — the counter's use is
  `SessionEndpointTests.The_call_costs_a_fixed_number_of_queries`; a list seeds rows between two
  calls and asserts the same count.
  `IntegrationTestWebAppFactory.Queries` exposes the counter (`QueryCounter`,
  `QueryCountingInterceptor` in `IntegrationTests/Fixtures/`): reset it, make the call,
  assert the count. The expected count is endpoint-specific.

## How tests are written

- **Assertions use Shouldly.** FluentAssertions v8 is commercially licensed.
- **Unit tests touch no database.**
- **Never skip, delete or weaken a failing test** — a loosened assertion or tolerance
  is the same failure. Fix the code, or say plainly that the test is broken.

## Choosing what to run

Which tests a change needs is the rule in [`CLAUDE.md`](../CLAUDE.md#building-and-testing);
this is how.

- **Unit and architecture suites — always, whole:** `dotnet test tests/Boilerplate.UnitTests`
  and `dotnet test tests/Boilerplate.ArchitectureTests`. They take seconds, and they hold
  the layering, tenancy, auditing and registration rules one feature's tests never
  exercise.
- **Integration — the module's folder, plus any class you changed:**
  `dotnet test tests/Boilerplate.IntegrationTests --filter "FullyQualifiedName~Features.Companies|FullyQualifiedName~SessionEndpointTests"`.
  A module's tests sit under `Features/<Module>/`. A change another module calls into
  — a delete guard, a lookup, a shared handler — takes that module's tests too.
- **Read the counts.** `Total` must be the number you expected, not `0`: a mistyped
  filter matches nothing and exits green.

## The integration database

- Integration tests need PostgreSQL: Testcontainers starts one if Docker is running,
  or set `BOILERPLATE_TEST_DB` to a server you already have. **Without either they skip
  silently — always report the skip count**; a green run that executed nothing looks
  identical to a pass. CI fails if they skip.
- **The tests create and drop databases named after the `BOILERPLATE_TEST_DB` database**, on
  its server, so its name must say it is a test database: `test` or `tests` as a whole
  `_`-separated lowercase word (`app_tests`), at most 29 characters. Never point it
  at anything real. On a shared server, a run also drops the tests' own databases
  older than six hours.
- CI uses one PostgreSQL service through `BOILERPLATE_TEST_DB`, not Testcontainers: its
  per-class containers cross-connected on Pipelines' Docker service. Locally
  Testcontainers starts one container per run.

## Hosts and databases

- **A test class never starts its own server** *[enforced: `check-test-setup.py`, pre-commit + CI]* *(add a `TestSetupRuleTests` architecture test to enforce it in code too)*.
  xUnit builds the class again for every test, so a host started in a constructor, a field
  initializer or a helper (`WithWebHostBuilder`, `WithProductCaller`, a new `WebApplicationFactory`)
  starts once per test, and hosts started that way can pile up (the leak a sister project hit; the
  cost here is not yet measured). Share the fixture's host. A class
  whose tests differ only in who calls uses `IntegrationTestWebAppFactory.SharedStubUserHost`:
  `StartTest()` as the test starts resets the stub caller, so nothing of the last test is left, and
  rate limiting is off on it. Services the same for every test of the class (a fixed clock, a
  context): `SharedStubUserHostWith(name, services)`, same stub. Tests that sign in for real:
  `SharedProductCallerHost`. A class that needs services that change per test, a recording logger or its own
  configuration may build a host in the one test that needs it, as an entry in
  `tests/test-setup-allow-list.tsv` with the reason. The rate-limit classes keep their own hosts,
  since limiter state is per host.
- **A test never sets up its own database** *[enforced: `check-test-setup.py`, pre-commit + CI]* *(add a `TestSetupRuleTests` architecture test to enforce it in code too)*.
  The fixture copies a template migrated once per run. `PostgreSqlBuilder`, `MigrateAsync` or
  `EnsureCreated` outside `Fixtures/` is an entry in the same allow-list, as the migration upgrade
  tests are, with the reason. An entry whose code is gone fails too, so the list only shrinks.
- **Throwaway PostgreSQL settings never reach a compose file** *(enforced by the deploy pack's `check-compose.py` when that pack is installed)*.
  `fsync`, `synchronous_commit`, `full_page_writes` and `tmpfs` make the tests' database fast by
  giving up durability; they belong only in the throwaway container the tests start, never in a
  `compose*.yaml` an environment runs.
- **A run over 25 minutes warns**, and `ci-local.sh` lists the five slowest integration classes
  after it: look there first.

## Smoke tests

- **The integration project has two halves, told apart by a trait.** Classes under
  `Smoke/` carry `[SmokeTest]` — `Category=Smoke` — and talk to a **deployed** host
  over HTTP; everything else is in-process. CI runs `--filter "Category!=Smoke&Category!=S3"`;
  `/deploy` runs `--filter "Category=Smoke"` after a deploy.
- **The smoke half skips unless `BOILERPLATE_DEPLOYED_API_URL` is set, and that skip is
  intended** — so CI excludes it, because its gate fails the build on any skip.
- **No `Category=S3` test exists** — S3 is proved by `S3FileStoreTests` and a check by hand on
  UAT. The filter still
  leaves the category out, so one written later must skip without its simulator, as smoke does.
- Credentials come from the `BOILERPLATE_DEPLOYED_API_*` variables in the user's shell —
  never prompt for them, never print them. **Everything a run creates it deletes.**
  The variables and the rules that keep a shared host clean:
  [`docs/dev-smoke-test-suite.md`](../docs/dev-smoke-test-suite.md) §1 and §4.
- The smoke tests use none of the product's DTOs, on purpose: they parse the wire
  format, so a serialization change cannot hide behind a shared type.

## Rule tests

**Twice becomes a test.** A review finding, or a defect class, found a second time gets a rule test or a
check in `devtools/checks/` with its fix, not a third review comment.

### Architecture tests

`tests/Boilerplate.ArchitectureTests/` is where a mechanical rule becomes enforced.
**Never suppress one to get a green build — fix the design.** An allow-list entry
(`TenancyTests`, `AuditingTests`, `QueryFilterBypassTests`,
`UntrimmedLengthRuleTests`, `ProductCodeRuleTests`) is added visibly, with a reason.
A rule read from source goes through `ProductSources`, which skips comments,
generated migrations and `bin`/`obj`.
