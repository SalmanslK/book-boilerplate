# B6 · Performance & Scalability — checks 99–110

Part of the review checklist; its method is `review-method.md`, in the book plugin (`book:peer-review/references/`).

| # | Check | Apply it here |
|---|---|---|
| 99 | Blocking operation on the request path | `.Result`/`.Wait()` on a Task, or a synchronous file/network call in an async handler |
| 100 | Sync-over-async | Same root cause as 99 |
| 101 | Unnecessary `Task.Run` | Wrapping already-async work, or offloading CPU-light work that gains nothing |
| 102 | Excessive memory allocation | Large materialisations, unnecessary `ToList()` on a stream that could stay `IQueryable`/`IAsyncEnumerable` |
| 103 | Potential memory leak | Undisposed resources, event handlers never unsubscribed, growing static collections (also *check 112/113*) |
| 104 | Repeated expensive computation | Recomputing something per-request that doesn't change per-request |
| 105 | Missing appropriate caching | Only worth raising with a concrete, credible hot path — not speculative |
| 106 | Incorrect caching strategy | **Cache invalidation** is the usual failure: does it happen, and does it happen *before or after* the commit it's invalidating? |
| 107 | Excessive network calls | Multiple round trips where one would do |
| 108 | Sequential independent I/O that should be parallelized | Independent awaits chained instead of `Task.WhenAll` |
| 109 | Excessive payload size | A response carrying fields no client needs (ties to *check 55*) |
| 110 | Operation does not scale with expected data volume | A list endpoint's query cost should be **fixed**, not proportional to row count — see the `QueryCounter`/`QueryCountingInterceptor` pattern in `tests/Boilerplate.IntegrationTests/Fixtures/` and `BusinessUnitListQueryTests` for the assertion shape a new list endpoint should copy |

**Raise a performance finding only where the impact is credible at realistic
volume** — this project's own multi-tenant scale, not a hypothetical.
