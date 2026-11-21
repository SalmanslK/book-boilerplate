# B1 · Architecture & Design — checks 16–33

Part of the review checklist; its method is `review-method.md`, in the book plugin (`book:peer-review/references/`).

| # | Check | Apply it here |
|---|---|---|
| 16 | Architectural layer boundary violation | Domain → nothing, Application → Domain, Persistence/Infrastructure → Application, API → all three. Architecture tests fail the build on this — check whether the suspected violation is one a test would already have caught, because if so the concern is elsewhere |
| 17 | Incorrect dependency direction | Same as above, checked from the other end |
| 18 | Domain depending on infrastructure concerns | Never EF Core, Npgsql or ASP.NET Core referenced from Domain or Application |
| 19 | Business logic in API/endpoints | Endpoints dispatch through `ISender` and map the result only — no business logic, no repository calls, no `DbContext`. Architecture test enforces the last one |
| 20 | Business logic in the persistence layer | A repository method that does more than fetch/save is doing the handler's job |
| 21 | Business logic duplicated across layers | The same rule re-implemented in a validator and a domain method, silently able to drift |
| 22 | Incorrect responsibility assignment | Is this handler/service/repository doing what its name says, or has scope crept in? |
| 23 | SRP violation | A class with more than one reason to change |
| 24 | OCP violation | Only worth raising with a concrete extension the current design can't accommodate — not "could be more open" in the abstract |
| 25 | LSP violation | A derived type that narrows a contract its base promises |
| 26 | ISP violation | A consumer forced to depend on members it never uses |
| 27 | DIP violation | A high-level module depending on a concrete low-level one instead of an abstraction |
| 28 | Circular dependency | Between projects (architecture test territory) or between classes within one |
| 29 | Excessive coupling | Changing one class forces edits across many unrelated ones |
| 30 | Inappropriate inheritance | Used for code reuse rather than a genuine is-a relationship |
| 31 | Missing composition where appropriate | Only where inheritance is visibly straining |
| 32 | Incorrect aggregate/entity boundaries | Repositories are for **aggregate roots only**, generic ones constrained to `AggregateRoot` — a repository for a child entity lets callers bypass the root's invariants. A child collection loaded by the generic write repository arrives empty; feature-specific write repositories exist for exactly this (`Users`, `Roles`) |
| 33 | Inappropriate abstraction / premature generalization | **No interface or base class without a second implementer in sight.** "Generic by default, explicit by necessity" — generic infrastructure absorbs repetition, explicit code carries the business |
