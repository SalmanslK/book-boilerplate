# What to compare between code and documentation

**Shared by `docs-vs-code` and `docs-update-from-code`; owned by `docs-vs-code`.** Both
skills walk the same ground; they differ in what they do on arrival.
`docs-vs-code` reports a mismatch without presuming which side is wrong.
`docs-update-from-code` rewrites the documentation to match, and stops where it cannot be
sure the code is right.

Keeping one list means adding a concern here reaches both. Two copies drift, and the
one nobody updated silently stops checking.

**Prefer tracing one real path end to end over skimming ten.** A single flow followed
from entry point to storage finds more than a survey of every file.

---

## Architecture

Module or project references and dependency direction. The folder tree and
class-placement rules in `docs/architecture.md` — where a type *actually* lives
against where the guide says it goes. The request pipeline or middleware, and the
order things run in.

**These drift silently because nothing fails when they do.** No build breaks because
a document describes a folder that was renamed.

## Domain

Entities, value objects, invariants, enums and their values, events.

Check the vocabulary in `docs/system-overview.md` against real type names. **A
renamed concept surviving in the glossary is how two people end up meaning different
things** — and neither notices, because each is internally consistent.

## Database

Tables, columns, nullability, keys, **indexes and their filters**, unique constraints
and what they are scoped by, migrations against the current model, and whether seed
data still matches the schema.

**Index filters and scopes are the ones that matter.** A unique index that forgets a
soft-delete condition or a tenant column is a live bug, not a documentation defect —
escalate it rather than writing it down.

## Public contract

Whatever others call: routes, methods, request and response shapes, status codes,
error formats, the permission each operation requires, paging, filtering, sorting —
or, for a library or CLI, its public API and options. Compare against the contract
tables in the feature plans and any generated contract doc.

## Flows

Trace the real path, from the entry point through validation, the use case, the
domain, persistence, and whatever runs after the save (events, hooks, jobs).

**Follow the path the code takes, not the one the architecture implies.** The gap
between those two is where the interesting findings are.

## Cross-cutting behaviour

Soft delete, tenancy or other data scoping, auditing, authorization, validation,
rate limiting, health checks — whatever `CLAUDE.md` says is enforced centrally.

Each is enforced centrally, so **a gap is invisible until it matters**. Check that
the central mechanism is applied to everything it claims to cover, not that it
exists.

## Status and state

Enums, allowed transitions, terminal states, and the rules guarding them.

## Configuration and deployment

Configuration keys against the code that reads and validates them. The runbook
against the container, compose and pipeline files. Environment names — matched
exactly, and case-sensitive where the platform is.

## Tests

Every suite `CLAUDE.md` names. Where test names state behaviour, read them — they
state intended behaviour more plainly than most comments, which makes them unusually
good evidence of intent. Tests tagged with a factory run's criterion
(`docs/factory/runs/R-nnn.md`) say which behaviour they were written to prove.

## Diagrams

Every diagram in `docs/`. Verify the components exist, the relationships hold, the
order is right, the names match, and every diagram block parses.

**A deliberately simplified conceptual diagram is not a defect.** Say so rather than
replacing it with implementation detail.

---

## Numbers

**Measure them. Do not carry them forward.**

Test counts, feature counts, question counts and branch state are the most-quoted
and least-checked facts in any repository, and arithmetic that looks reasonable is
often wrong. Run the suite and read the total. Count the rows. Check `git log`.

**A count you did not verify this pass is a count you must not write.**

---

## Where drift tends to happen

Not a list to pad a report with. These are the places that hurt most when they drift.
Add this project's own as it is bitten.

- **Data scoping** (tenancy, per-user or per-group visibility). Every entity that
  should be scoped is, any allow-list of exceptions has a reason per entry, unique
  indexes include the scope column, and the filters are applied. **A gap here is a
  cross-customer data leak** — treat any finding as Critical until proven otherwise.
- **Deleted versus inactive.** Two different things that can look identical in the
  database.
- **Authorization.** The permission map in code against the documented one (and the
  requirements' permission source), and default-deny for anything unmapped.
- **Migrations against the current model.**
- **Feature plans versus what shipped**, especially anything still marked proposed or
  deferred that has since been built or dropped.
- **Question and defect numbering and counts** in `docs/open-questions.md` and
  `docs/defects.md` — every item in exactly one section, statuses matching placement.
- **Feature status** in `docs/project.md` Part 1 against what is actually built.
- **The deployment runbook** against the actual container and pipeline files.
