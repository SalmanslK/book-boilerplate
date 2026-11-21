# B7 · Async & Concurrency — checks 111–120

Part of the review checklist; its method is `review-method.md`, in the book plugin (`book:peer-review/references/`).

| # | Check | Apply it here |
|---|---|---|
| 111 | Race condition | Two requests that can interleave and leave inconsistent state |
| 112 | Unsafe shared mutable state | **No static mutable state** — this is a flat rule in this codebase, not a judgment call |
| 113 | Thread-unsafe collection/state | A non-concurrent collection touched from more than one request context |
| 114 | Incorrect locking | Lock scope too broad/narrow, or a lock around something that doesn't need one |
| 115 | Potential deadlock | Nested locks in inconsistent order, or blocking-on-async inside a lock |
| 116 | Fire-and-forget operation without controlled lifecycle | **No external side effect in a domain event handler** — no email, no HTTP call, since handlers run inside the transaction and can't be rolled back; use an outbox |
| 117 | Unobserved asynchronous failure | A `Task` started and not awaited, whose exception disappears |
| 118 | Cancellation token ignored | `CancellationToken` must flow all the way through; `CancellationToken.None` does not appear in product code |
| 119 | Cancellation not propagated | A token accepted by a handler but not passed to the repository/HTTP call beneath it |
| 120 | Incorrect concurrent processing/duplicate execution | `ExecuteInTransactionAsync`'s block **may run more than once** under the retry-on-failure execution strategy — anything inside it must tolerate that (cross-reference *check 12*, don't double-report the same root cause) |
