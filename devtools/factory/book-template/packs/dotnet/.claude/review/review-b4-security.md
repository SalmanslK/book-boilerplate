# B4 · Security — checks 61–80

Part of the review checklist; its method is `review-method.md`, in the book plugin (`book:peer-review/references/`).

| # | Check | Apply it here |
|---|---|---|
| 61 | SQL injection | EF parameterises by default — flag any raw SQL (migrations, `FromSqlRaw`) built by string concatenation |
| 62 | Command injection | Any shell-out; none expected in this API |
| 63 | Path traversal | Any code building a file path from caller input — uploads go through `IFileStore` only, never direct disk writes |
| 64 | SSRF | Any outbound call built from caller-supplied host/URL |
| 65 | Cross-site scripting | The invitation template's unescaped name rendering is a **recorded decision** (pinned by a test) — don't re-flag it; do flag a *new* unescaped render elsewhere |
| 66 | CSRF where applicable | Relevant only if a cookie-based flow is introduced; token-bearer API is not exposed to it today |
| 67 | Insecure deserialization | Any `BinaryFormatter`-class deserialization, or trusting a client-supplied type name |
| 68 | Unsafe file upload | Trust the bytes, not the filename or content type — format is decided by reading leading bytes |
| 69 | Missing upload size/type restrictions | Every `IFileStore` use needs bounds; check against the module's `FileArea` |
| 70 | Hardcoded secrets | Anything that isn't a `REPLACE`-prefixed placeholder in a committed settings file |
| 71 | Secrets exposed through source/configuration | `appsettings.Development.json` is the one tracked exception (SMTP password) — anything else committed there is a finding |
| 72 | Secrets exposed through logs/telemetry | Never log passwords, hashes, tokens, reset/invitation links, connection strings, customer-identifying data |
| 73 | Weak password handling | Hashing algorithm, complexity rules, comparison method (constant-time where relevant) |
| 74 | Incorrect credential/token handling | Refresh rotation, security-stamp invalidation on password change/deactivation |
| 75 | Incorrect JWT validation | Issuer/audience/lifetime/signature checks all present; signing key not a placeholder-length value |
| 76 | Broken authentication | Every authenticated request re-checks the security stamp — a new code path that bypasses `ICurrentUser`/middleware breaks this |
| 77 | Broken authorization | Authorization is **default-deny** — `.AllowAnonymous()` is the thing to scrutinise. A permission missing from the `Permissions` catalogue fails closed (correct), but a **new gated endpoint needs a catalogue entry *and* a test asserting which role holds it** |
| 78 | IDOR/BOLA/resource ownership | Can the caller supply an id for a resource that isn't theirs and get it back? |
| 79 | Privilege escalation | Can a lower role reach an Admin-only action through a side path — a list filter, a bulk endpoint, a nested resource |
| 80 | Cross-tenant data access — **treat as CRITICAL by default** | Can a query return another tenant's rows? Is a global filter bypassed? `IgnoreQueryFilters()` drops tenancy **and** soft-delete together — there is no way to waive one, so every use needs an explicit re-check afterwards and an entry in the project's filter-bypass test (for example `QueryFilterBypassTests`). Is the project's scope interface applied on **writes as well as reads** — a scoped list with an unscoped `GetForUpdate` is an IDOR with a tidy front end |
