# B5 · Data & Database — checks 81–98

Part of the review checklist; its method is `review-method.md`, in the book plugin (`book:peer-review/references/`).

| # | Check | Apply it here |
|---|---|---|
| 81 | N+1 query | Any loop that queries per iteration instead of a single set-based query |
| 82 | Unnecessary database query | A lookup the caller already had, or a `Single` followed by an unrelated `Any` re-hitting the same table |
| 83 | Duplicate database query | Same query run twice for two different checks that could share one result |
| 84 | Database query inside an avoidable loop | Same root cause as 81 — file once |
| 85 | Loading excessive data | Full entity loaded where a projection would do |
| 86 | Filtering performed in application instead of database | `.Where` after `.ToList()`/`.AsEnumerable()`, or a sort/allow-list check done in memory on a large set |
| 87 | Missing database-level pagination | List queries derive from `Queries/Paging/`'s base handler for a reason — hand-rolled paging is the thing to check |
| 88 | Missing required index | Any new frequent filter/sort column with nothing backing it |
| 89 | Incorrect or ineffective index | Unique indexes on a tenant-scoped aggregate must lead with `tenant_id`; filtered on `deleted_at IS NULL` for soft-deletable aggregates, or a deleted record holds its unique values forever |
| 90 | Inefficient join/query | Prefer a correlated subquery over method-syntax outer join for optional relations — it reads the same whether the row is required or optional |
| 91 | Incorrect EF Core tracking behaviour | Read paths should be `AsNoTracking`; a write path that forgot tracking silently no-ops on save |
| 92 | Excessive eager loading | `Include` chains pulling data the query doesn't need |
| 93 | Inefficient projection | Mappings are `Expression` properties on a static `<Aggregate>Mappings` class, reused by list/detail/dropdown — a mapping *method* forces full entity load |
| 94 | Incorrect transaction boundary | Does the unit of work match the business operation, not the code's convenience? |
| 95 | Missing transaction where atomicity is required | `SaveChangesAsync` is atomic **on its own**; two saves that must succeed together need `IUnitOfWork.ExecuteInTransactionAsync` — there is no manual `BeginTransaction`/`Commit` |
| 96 | Missing concurrency protection | Optimistic concurrency token absent on a row multiple actors can race to update |
| 97 | Missing database constraint/invariant | An invariant enforced only in code with nothing backing it at the schema level |
| 98 | Unsafe schema/migration change | Never edit an applied migration — add a new one. Raw SQL in a migration must be proven against the **idempotent script**, not just `MigrateAsync`: dollar-quoting must not nest (`$EF$` vs an inner `$migration$`), every `Sql()` call needs a trailing semicolon, and every cast must guard the value that can't convert (`NULLIF(col, '')`), not just the expected one |
