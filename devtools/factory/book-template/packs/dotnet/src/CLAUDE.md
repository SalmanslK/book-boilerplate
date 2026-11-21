# Code rules

Loaded when you work under `src/`, and to be read before changing or reviewing product code. The root
[`CLAUDE.md`](../CLAUDE.md) still applies; `src/Boilerplate.Persistence/` and `src/Boilerplate.API/` add their own.

Why each rule below exists is in [`docs/architecture.md`](../docs/architecture.md) — the link after each group goes to its section.

Dependencies ([why](../docs/architecture.md#when-to-take-a-library-and-when-not-to)):

- **Prefer a maintained open-source library over writing our own.** Write our own
  only when the surface is small (~100 lines), the dependency carries governance risk,
  and it stays behind one file.
- **Every third-party library stays isolated to one place.** A licence-sensitive one
  gets a row in the architecture guide (licence and named exit) **before** the package
  is added.
- Package versions live in `Directory.Packages.props` — never `Version=` on a
  `PackageReference`.

Structure ([why](../docs/architecture.md#2-where-classes-live)):

- Feature slices live in `Features/<Feature>/` with `Dtos/`, `Mappings/`, `Commands/`,
  `Queries/`, `EventHandlers/`. Anything else goes in a folder named for what it holds.
  **There is no `Common/`.**
- **`Abstractions/` holds contracts; the folder named for the job holds the
  implementation** (`ISender` in `Abstractions/Messaging/`, `Sender` in `Messaging/`).
- **Every repository port goes in `Abstractions/Repositories/Read/` or `Write/`**,
  feature-specific ones included.
- Shared query machinery by kind: `Queries/Paging/`, `Queries/Lookups/`,
  `Queries/Details/`, each with its shapes and base handler.
- Domain base types in `Domain/Primitives/`; `Domain/Abstractions/` is marker
  interfaces only.
- Namespaces follow folders exactly.
- **Settings are typed options with `ValidateOnStart` and a validator behind it** — a `.Validate(…)`, an
  `IValidateOptions<T>`, or data annotations on the class — bound where their concern lives
  (`API/Configuration/Options/`, `Infrastructure/Mail/`, `Infrastructure/Storage/`,
  `Application/Features/Users/`); one registered without is a `[dotnet_checks] options_unvalidated`
  entry with its reason *[enforced: `check-dotnet.py`, pre-commit + CI]*.
- **No magic-string configuration reads** (`configuration["…"]`, `GetValue<T>("…")`, `GetSection("…")`);
  a read that runs before options exist is a `config-by-literal` allow-list entry with its reason
  *[enforced: `check-code-rules.py`, pre-commit + CI]*.
- Never reference EF Core, Npgsql or ASP.NET Core from Domain or Application
  *(not enforced yet: add a `LayerDependencyTests` architecture test, then mark it `[enforced: …]`)* — fix the design, do not suppress.

Style ([why](../docs/architecture.md#13-naming); [time](../docs/architecture.md#10-cross-cutting-decisions); [LINQ](../docs/architecture.md#linq-method-syntax-and-correlated-subqueries)):

- `sealed` and `readonly` by default; primary constructors where they read better;
  records for immutable request and response shapes; `IReadOnlyCollection<T>` on the
  way out; nullable reference types on; explicit access modifiers. Handlers and entity
  configurations are `sealed` and `internal` *(not enforced yet: add a `ConventionTests` architecture test, then mark it `[enforced: …]`)*.
- **No null-forgiving operator (`!`).** Prove it with a guard or null check.
- **No regions, service locator, static mutable state, magic strings or numbers.** No
  interface or base class without a second implementer in sight; no repository method
  that renames an EF Core method one-for-one. A string that must match another — a
  tag, an endpoint name, a lock key, a validator message — is one constant beside the
  code that owns it, never a global `Constants` class
  ([what counts](../docs/architecture.md#14-what-not-to-build)) *(not enforced yet:
  add a `StringConstantRuleTests` architecture test, then mark it `[enforced: …]`)*.
- **Rename unclear code rather than comment it.** No commented-out code.
- **No `TODO` for a requirement already known** — build it, or track it as a Q or D.
- **LINQ is method syntax, never query syntax** *(not enforced yet: add a `ProductCodeRuleTests` architecture test, then mark it `[enforced: …]`)*.
  Where query syntax would be a `join`, prefer a correlated subquery.
- **`Single`/`SingleOrDefault` over `First`/`FirstOrDefault`** when at most one row
  should match. `First` only where several matches are legitimate — and then order
  the query; an unordered `First` is an allow-list entry with its reason *(not enforced yet:
  add a `ProductCodeRuleTests` architecture test, then mark it `[enforced: …]`)*. **A read that bypasses the soft-delete filter can meet a
  deleted row beside the live one** (a reused email) — order it, never `Single`.
- Names say what the type is (`CreateCompanyCommand`, `ICompanyReadRepository`, `CompanyDto`,
  `CompanyMappings`, `CompanyErrors`); commands and queries are named for what they are.
  **Never `Helper`, `Manager`, `Processor`, `Utility`, `DataService` or
  `CommonService`** as a type name's last word. Both *(not enforced yet: add a `ConventionTests` architecture test, then mark it `[enforced: …]`)*.
- **Generic by default, explicit by necessity.** When two rules collide, say which two
  and which you kept.

Behaviour ([why](../docs/architecture.md#7-cqrs-and-the-dispatcher); [auditing, soft delete, tenancy](../docs/architecture.md#8-auditing); [results](../docs/architecture.md#9-errors-and-results)):

- **Handlers own their save.** A create handler must save before returning a
  database-generated id.
- **Two saves that must succeed together go in `IUnitOfWork.ExecuteInTransactionAsync`**
  — never `BeginTransaction`/`Commit`/`Rollback` *(not enforced yet: add a `TransactionRuleTests` architecture test, then mark it `[enforced: …]`)*. The
  block may run more than once, so it must be safe to repeat.
- **Multi-tenant: a new aggregate implements `ITenantScoped` unless it is shared
  reference data** *(not enforced yet: add a `TenancyTests` architecture test, then mark it `[enforced: …]`)*; opting out is an allow-list entry
  with a reason. Never pass a tenant id into a domain method *(not enforced yet: add a `TenancyTests` architecture test, then mark it `[enforced: …]`)*; never add
  `Where(x => x.TenantId == …)` by hand. **Prove isolation with a two-tenant
  integration test** ([`tests/CLAUDE.md`](../tests/CLAUDE.md)).
- **Anything that belongs to a company implements `ICompanyScoped`** — a session sees only
  its company's rows, and nothing in none. Opt-in; `CompanyId` is stamped on insert, never
  set by a caller; a scoped aggregate is also tenant-scoped *(not enforced yet: add a `TenancyTests` architecture test, then mark it `[enforced: …]`)*.
- **Deletes are soft** (`ISoftDeletable`, `repository.Remove(entity)`) — never a hand
  hard delete. **Deleted is not inactive**: deleted is invisible, inactive is visible
  and labelled.
- **Uploaded files go through `IFileStore`**, never straight to disk. Store the
  returned key, never the bytes. A new upload is a `FileArea` member, a
  `{module}/{kind}` directory under the one `uploads/` root, and a nullable key column.
  Order: write the file, save the row, **then** delete the one it replaced.
- **Trust the bytes, not the file name or content type** — the format comes from the
  leading bytes.
- **Audit actors are user ids, resolved to names on read — every auditable entity, no
  exceptions** *(not enforced yet: add a `AuditingTests` architecture test, then mark it `[enforced: …]`)*. `CreatedBy`/`ModifiedBy`/`DeletedBy` are
  `long?` FKs to `users` with `Restrict` and **no navigation property** *(not enforced yet:
  add a `PersistenceModelTests` architecture test, then mark it `[enforced: …]`)*; null renders
  as "System", `-`, and not-deleted respectively. Resolve names through `AuditActor`,
  never `IgnoreQueryFilters` *(not enforced yet: add a `QueryFilterBypassTests` architecture test, then mark it `[enforced: …]`)*. Detail DTOs only,
  carrying both `createdById` and `createdBy`. **Never store the name.** Authentication metadata
  must not travel through auditing.
- **Never call `DateTime.UtcNow` or `DateTimeOffset.UtcNow`** — inject `TimeProvider`
  or pass the time in. **Timestamps are `DateTimeOffset`.** Both *(not enforced yet:
  add a `DateTimeUsageTests` architecture test, then mark it `[enforced: …]`)*.
- **`Result<T>` for expected failures**; exceptions are for faults. Handlers never
  return `IResult`.
- **Never catch broadly to keep a request alive.** Catch only the specific exception
  you can map to a `Result` or log and rethrow. The exceptions are the background
  workers' loops (outbox, documents, the clean-up workers), which log a failed item and
  carry on. A new
  `catch (Exception)` or bare `catch` is an allow-list entry with its reason *(not enforced yet:
  add a `ProductCodeRuleTests` architecture test, then mark it `[enforced: …]`)*.
- **`CancellationToken` flows all the way through** — no `CancellationToken.None` in
  product code. **Never block on async** (`.Result`, `.Wait()`,
  `.GetAwaiter().GetResult()`). Both *(not enforced yet: add a `ProductCodeRuleTests` architecture test, then mark it `[enforced: …]`)*.
- **No external side effect in a domain event handler** — no email, no HTTP call. A
  handler runs after the save, and an external effect cannot be rolled back
  *[enforced: `check-code-rules.py`, pre-commit + CI]*.
  **Email goes through the outbox:** the command handler calls a thin Application port
  such as `IUserEmailNotifier` **before** its last save — inside the
  `ExecuteInTransactionAsync` block when it has one — and the port adds an outbox row to
  that same save; a worker sends it with retries. The port sends and saves nothing.
  ([architecture guide](../docs/architecture.md#external-side-effects-email-and-http))
- Endpoints dispatch through `ISender` — no logic, repositories or `DbContext`
  *(not enforced yet: add a `EndpointIsolationTests` architecture test, then mark it `[enforced: …]`)*.
- **Authorization is default-deny**: every `.AllowAnonymous()` is an `anonymous-route` allow-list entry,
  counted per file, with its reason *[enforced: `check-code-rules.py`, pre-commit + CI]*.
- Repositories are for **aggregate roots only**.
- A list query's sort field maps through an **explicit allow-list** in the repository,
  never reflection.
- List, detail and dropdown queries derive from the bases in `Queries/Paging/`,
  `Queries/Details/`, `Queries/Lookups/`; a query that does not fit writes its own.
- DTOs are `<Aggregate>Dto` in `Features/<Feature>/Dtos/`. Dropdowns return
  `LookupItem`. Mappings are `Expression` properties on a static
  `<Aggregate>Mappings` class, reused by list, detail and dropdown.
- **A validated request's list of objects or strings validates its items**: `RuleForEach(x => x.Items).NotNull()`
  (or `NotEmpty()`), so `[null]` is a 400, not a 500; a list that may hold a null on purpose is a
  `[dotnet_checks] list_items_nullable` entry with its reason *[enforced: `check-dotnet.py`, pre-commit + CI]*.
- Validators measure trimmed lengths *(not enforced yet: add a `UntrimmedLengthRuleTests` architecture test, then mark it `[enforced: …]`)*.

Security ([logging](../docs/architecture.md#10-cross-cutting-decisions)):

- `ASPNETCORE_ENVIRONMENT` is exactly `Development`, `QA`, `UAT`, `Production` (or
  `Testing`, test host only) — case-sensitive on Linux.
- **Placeholder settings start with `REPLACE`**, or startup validation cannot detect
  them. **Never commit real secrets** *[enforced: `check-appsettings-secrets.sh`,
  pre-commit + CI]*; real values come from user-secrets, app settings or Key Vault.
  `appsettings.Development.json` is tracked and checked like the rest — put nothing in it you would
  not publish.
- **Never log** passwords, hashes, tokens, reset or invitation links, one-time
  codes, connection strings, or customer-identifying data. **Log through a `[LoggerMessage]` method** in
  the area's `*Log` class, with an event id from its range — never a direct
  `logger.Log…` call, never string interpolation
  ([why and the ranges](../docs/architecture.md#10-cross-cutting-decisions))
  *(not enforced yet: add a `ProductCodeRuleTests` architecture test, then mark it `[enforced: …]`)*.
- Never put a secret in a commit message, branch name or test fixture.

Database — full rules in [`src/Boilerplate.Persistence/CLAUDE.md`](Boilerplate.Persistence/CLAUDE.md):

- Every schema change is an EF Core migration; never edit an applied one *[enforced:
  `check-protected-paths.py`, CI]*; never hand-edit a migration or the snapshot except to add
  `migrationBuilder.Sql(...)`.
- Regenerate `docs/database-erd.md` with the migration *[enforced: `gen-database-erd.py`, CI + pre-commit]*.
- Raw SQL must be proven against the **idempotent script**, applied twice.
- **Never pass `--startup-project` to `dotnet ef`**, and resolve the target host before
  any destructive EF command.
