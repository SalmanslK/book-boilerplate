# B8 · Error Handling & Resilience — checks 121–132

Part of the review checklist; its method is `review-method.md`, in the book plugin (`book:peer-review/references/`).

| # | Check | Apply it here |
|---|---|---|
| 121 | Exception swallowed | `UserEmailNotifier` logging-and-swallowing is a **recorded, deliberate** exception to this — verify new code follows the same documented shape rather than swallowing by accident |
| 122 | Overly broad exception handling | `catch (Exception)` where a narrower type was available and meaningful |
| 123 | Exception caught without meaningful recovery | A catch block that logs and rethrows the same thing, adding nothing |
| 124 | Original exception context lost | `throw ex;` instead of `throw;`, or a wrapped exception that drops the inner one |
| 125 | Generic/meaningless exception | A raw `Exception`/`InvalidOperationException` where a domain-specific error or `Result<T>` failure was called for |
| 126 | Sensitive information exposed through errors | Stack traces or EF messages reaching the client (ties to *check 56*) |
| 127 | Inconsistent exception-to-response mapping | Expected failures as `Result<T>`, faults as exceptions — handlers never return `IResult` |
| 128 | Missing centralized error handling | A handler doing its own try/catch-to-response translation instead of the pipeline's |
| 129 | Missing timeout for external operation | Any HTTP/SMTP/file-store call with no bound |
| 130 | Incorrect retry strategy | Retrying a non-idempotent operation, or retrying indefinitely |
| 131 | Missing retry/backoff for transient failure | Only worth raising where the failure mode is realistic and the operation is safe to retry |
| 132 | External dependency failure not handled safely | A failed external call must not leave inconsistent local state — check what happens to the already-committed row when the email/file/HTTP call after it fails |
