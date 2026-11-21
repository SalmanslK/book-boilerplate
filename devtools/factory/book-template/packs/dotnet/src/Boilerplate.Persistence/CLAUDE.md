# Persistence — database and migration rules

Loaded when you work under `src/Boilerplate.Persistence/`. The root
[`CLAUDE.md`](../../CLAUDE.md) still applies; the reasoning is in
[`docs/architecture.md` §6](../../docs/architecture.md#6-database).

## Schema changes

- **Every schema change ships as an EF Core migration** — never as a script in
  `database-scripts/`, never by hand against a database.
  ```bash
  dotnet ef migrations add <Name> --project src/Boilerplate.Persistence
  ```
- **Never edit a migration that has been applied anywhere shared.** Add a new one.
- **A test fails when the model has changes no migration holds**: one asserts
  `context.Database.HasPendingModelChanges()` is false, once the project has a migration
  — judged by the full run, not a commit's lines *[enforced: `check-code-rules.py`, CI]*.
- **Never hand-edit a migration, its `.Designer.cs`, or the model snapshot.** A wrong
  migration is `dotnet ef migrations remove`, then regenerate. **One exception:** raw
  SQL added through `migrationBuilder.Sql(...)`, governed by the idempotent-script
  rule below.
- **Regenerate `docs/database-erd.md` in the same
  change** — `python3 devtools/checks/gen-database-erd.py`. CI and the pre-commit hook
  run `--check` and fail on a stale diagram. If the output looks wrong, fix the
  generator, never the doc.
- **Migrations are forward-only in production.** A `Down()` that drops a column drops
  its data. Local rollbacks are fine.

## Raw SQL must survive the idempotent script

The deploy's `migrate` container runs `dotnet ef migrations script --idempotent`
through `psql`, which wraps **each migration** in `DO $EF$ … END $EF$`; the test
fixture applies migrations directly and unwrapped. A migration can pass every test
and still break the deploy — three have. Prove it:

```bash
dotnet ef migrations script --idempotent --project src/Boilerplate.Persistence -o migrate.sql
# apply to a scratch database TWICE — the second run is the point
psql -d app_script_check -v ON_ERROR_STOP=1 -f migrate.sql
```

- **Give an inner `DO` block its own dollar tag** (`$migration$`) — `$$` closes the
  `$EF$` wrapper early.
- **End every `migrationBuilder.Sql(...)` with a semicolon** — statements are
  concatenated into the wrapper.
- **When casting a column, guard every value that cannot convert**, not only the one
  you expect — `NULLIF(col, '')` catches the empty string and nothing else.

## Indexes and filters

- **Unique indexes on a tenant-scoped aggregate lead with `tenant_id`, and on a soft-deletable one
  filter on `deleted_at IS NULL`.** An index kept global or unfiltered on purpose is a
  `[dotnet_checks] unique_indexes` entry with its reason *[enforced: `check-dotnet.py`, pre-commit + CI]*.
  Declare indexes in the configuration (`HasIndex`): an `[Index(…)]` attribute is not read.
- Never add `Where(x => x.TenantId == …)` by hand. Never `IgnoreQueryFilters` without
  an allow-list entry and a reason — `QueryFilterBypassTests` fails the build on a new
  bypass *(not enforced yet: add a `QueryFilterBypassTests` architecture test, then mark it `[enforced: …]`)*.

## `dotnet ef` safety

- **Never pass `--startup-project`.** The Design package is `PrivateAssets` here and
  never reaches the API; without a startup project EF uses
  `DesignTimeDbContextFactory`, which holds the non-local host guard.
- **Resolve the target before any destructive command** (`database drop`,
  `database update`). The factory refuses a non-local host unless
  `BOILERPLATE_ALLOW_REMOTE_EF=true`; if you set it, say out loud which host and database
  you are about to hit. Never point a DEV or QA connection string at production.
- Shared databases are migrated by the deploy's `migrate` container, never by
  `dotnet ef` from a laptop.
