# B10 · Observability & Operations — checks 148–155

Part of the review checklist; its method is `review-method.md`, in the book plugin (`book:peer-review/references/`).

| # | Check | Apply it here |
|---|---|---|
| 148 | Missing meaningful logging | A failure path (especially a swallowed exception, *check 121*) with nothing recorded |
| 149 | Incorrect log level | Expected/handled conditions logged as Error; real faults logged as Information |
| 150 | Sensitive data logged | Same list as *check 72* — passwords, hashes, tokens, reset/invitation links, connection strings, customer data |
| 151 | Insufficient diagnostic context | A log line with no id, no tenant, no correlating value — nothing to search on later |
| 152 | Missing structured logging | Interpolated strings where structured fields were available and would make the log queryable |
| 153 | Missing metrics for important operational behaviour | This codebase has no metrics infrastructure decided yet — treat as **INFO**, not a Medium/High, unless a plan has committed to metrics for the touched area |
| 154 | Missing tracing/correlation for distributed operations | Same caveat as 153 — no tracing infra decided; INFO unless a plan says otherwise |
| 155 | Failure cannot be diagnosed effectively in production | The combined effect of 148–152: if this specific failure happened on DEV/QA right now, is there enough in the logs to find it? |
