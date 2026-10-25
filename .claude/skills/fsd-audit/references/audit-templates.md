# Audit file templates

Three files under `docs/Audit/`. Each has one job; nothing is restated between
them — the matrix carries evidence, the report carries the narrative, the findings
file carries the actions. Cross-reference by id.

The examples use `REQ` as the id prefix; use `[requirements].id_prefix` from
`devtools/factory/factory.toml`.

---

## `docs/Audit/spec-traceability.md`

(Or the path `[requirements].traceability` names.)

```markdown
# Spec traceability — Requirements → Plan → Code → Test

**Requirements:** <name> V<n> (`requirements/<name> - V<n>.<ext>`) · **Audited commit:** `<branch>@<sha>` ·
**Last full pass:** <date> · **Scope so far:** <features audited; "whole project" when complete>

Statuses: VERIFIED · PARTIAL · MISSING · CONTRADICTED · UNTRACED · UNVERIFIED · NOT_APPLICABLE
(definitions in `.claude/skills/fsd-audit/SKILL.md`). Ids are stable — never renumbered, never reused.

## <Feature> — §<section> — plan `docs/plans/<feature>.md`

| ID | Req ref | Requirement | Plan ref | Code evidence | Test evidence | Status |
|---|---|---|---|---|---|---|
| REQ-4.2.1-R01 | §4.2.1.1 | Total record count shown on the list | §1, §6 | `src/orders/list.py:98` | `test_orders_list.test_listing_returns_total_count` | VERIFIED (observed) |
| REQ-4.2.1-R08 | §4.2.1.8 | Pending list is not paged (answered 2026-03-14, Q3) | §2 Q3 | `src/orders/pending.py:12` — paged | — | CONTRADICTED (observed) → D7 |
| REQ-4.2.1-R09 | §4.2.1.9 | Cancelled orders cannot be edited | §4 | `src/orders/edit.py:40` | `test_edit.test_cancelled_order_is_refused` (R-004.AC-2, R-004 @ `a1b2c3d`) | VERIFIED (observed, R-004 @ a1b2c3d) |
| REQ-4.2.1-R07 | §4.2.1.7 | Frozen action column on horizontal scroll | — | — | — | NOT_APPLICABLE — front-end rendering |

### Untraced behaviour in this feature

| ID | Code evidence | What it does | Nearest justification found | Status |
|---|---|---|---|---|
| UNT-ORD-001 | `src/orders/reorder.py:41` | Refuses a partial id set on reorder | plan §10 | UNTRACED — plan decision, no requirement |
```

Rules for the matrix:

- **One row per atomic requirement.** Split a paragraph into its checkable
  statements; a row that says "list works as specified" cannot be verified.
- **`Code evidence` is `path:line`**, never a folder. **`Test evidence` is the test's
  full name.** `—` means none was found, which is itself the finding.
- **A test proven by a factory run** names the criterion and the run
  (`R-nnn.AC-<n>` or the requirement id, and `R-nnn @ <sha>`).
- **Global sections** get one block at the top of the file, one row per rule, whose
  evidence is the **shared mechanism** and its test. Feature blocks cite the global
  id in prose and add a row **only** where the feature bypasses the mechanism — that
  row's id is the feature's own, and it cites the global one.
- **`VERIFIED` / `CONTRADICTED` carry `(observed)` or `(traced)`** — see the skill's
  status vocabulary.
- **A status change keeps the history inline**: `CONTRADICTED (traced) → VERIFIED
  (observed, <date>, <PR or commit>)`.
- **Untraced behaviour** is a separate table per feature with `UNT-<FEATURE>-<nnn>`
  ids — it has no requirement to key on.

---

## `docs/Audit/spec-compliance-audit.md`

```markdown
# Spec compliance audit

**Audit date:** <date> · **Requirements:** <name> V<n> (captured <date>, `docs/project.md` Part 3) ·
**Plan(s):** `<file>` (status, last changed <date>) · **Audited commit:** `<branch>@<sha>` ·
**Scope:** <feature(s) | whole project> · **Previous audit:** <date or "none">

## Coverage

| Feature | Rows | VERIFIED | PARTIAL | MISSING | CONTRADICTED | UNTRACED | UNVERIFIED | N/A |
|---|---|---|---|---|---|---|---|---|

Counts only — no percentage, no score.

## Requirements → Plan
What the plan omits, waters down, or reinterprets. One line per item, id-linked.

## Plan → Code
Planned and not built, partial, differs from plan, claimed without evidence.

## Code → Requirements
Contradictions, and every UNTRACED behaviour with what would justify it.

## Tests
Requirements with no meaningful test evidence, by id. Factory runs used as evidence,
with the commit each proved.

## Existing defects touched
`D<n>` rows this audit relies on or corroborates — reference only.

## Required follow-up
Ordered list, each pointing at an `SA-<nnn>` finding, a `D<n>`, or an
`open-questions.md` question to raise.

## Final gate
- Requirements → Plan: …
- Plan → Code: …
- Code → Requirements: …
- Tests: …
- Not declared compliant because: <the rows that prevent it>

## History
| Date | Scope | Commit | Outcome |
|---|---|---|---|
```

---

## `docs/Audit/spec-audit-findings.md`

```markdown
# Spec audit findings

Actionable items only. Compliance findings, not defects — a finding that turns out
to be a defect is logged in `docs/defects.md` by whoever owns the fix and referenced
here by `D<n>`; this file never adds a `D` row itself.

Severity: HIGH — required behaviour missing or contradicted on a shipped path ·
MEDIUM — partial, or reinterpreted without a recorded decision ·
LOW — untested, or traceability gap only.

## Open

### SA-001 · HIGH · CONTRADICTED (observed) · Pending list is paged
- **Requirement:** §4.2.1.8 + answer <date> (Q3)
- **Plan:** `orders.md` §2 (stated default while Q3 was open)
- **Code:** `src/orders/pending.py:12`
- **Test:** `test_pending.test_pending_list_returns_first_page` asserts the paged shape
- **Expected:** the whole list in one response
- **Actual:** one page, at most 100 rows
- **Action:** already D7 — verify on merge of the fix, then flip R08
- **Status:** Open · found <date>

## Resolved

### SA-000 · … · Resolved <date> — <how, with commit or PR>
```

Rules for findings:

- **One finding per root cause.** Five requirements failing for the same reason are
  one `SA` with five ids listed, not five findings.
- **A finding cites the matrix row(s)**; it does not repeat the evidence columns.
- **Resolved is a state, not a deletion.** Move the entry, add the date and the
  commit or PR that resolved it.
- **Questions are not findings.** An ambiguity in the requirements becomes an
  `open-questions.md` question; the row that exposed it is `UNVERIFIED` until the
  answer lands, and says which question it waits on.
