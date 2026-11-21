# B3 · API & Contracts — checks 49–60

Part of the review checklist; its method is `review-method.md`, in the book plugin (`book:peer-review/references/`).

| # | Check | Apply it here |
|---|---|---|
| 49 | Incorrect HTTP method | GET for reads, POST for creation/actions, PUT/PATCH for updates, DELETE for removal (soft, per this codebase) |
| 50 | Incorrect HTTP status code | 201 on create, 204 vs 200 on update, 404 vs 403 for authorization vs existence (careful: default-deny can make a wrong-tenant resource look 404, which is usually correct here) |
| 51 | Inconsistent API response contract | Enums serialise as names; null fields are omitted from responses — API-wide, verify new endpoints match rather than assume |
| 52 | Inconsistent error contract | `Result<T>` failures should map to the same shape other endpoints already use |
| 53 | Missing request validation | Every input the handler trusts without a validator or factory check |
| 54 | Incorrect request validation | Shape validated in the validator, anything needing the database in the factory, invariants on the aggregate — validation in the wrong layer is often *also* validation that's wrong |
| 55 | Returning unnecessary data | List/dropdown DTOs shouldn't carry detail-only fields; dropdowns return `LookupItem`, never whole records |
| 56 | Exposing internal implementation details | Stack traces, internal ids not meant for the client, EF exception messages |
| 57 | Exposing sensitive information | Password hashes, tokens, other tenants' data, audit actor names on entities the caller shouldn't resolve |
| 58 | Incorrect resource/route design | Nesting, pluralisation, and verbs-as-nouns consistent with sibling endpoints |
| 59 | Unbounded collection endpoint | Every list must page; a list query's sort field goes through an **explicit allow-list**, never reflection |
| 60 | Incorrect API compatibility/versioning behaviour | A changed response shape or removed field breaking an existing FE integration guide's contract |
