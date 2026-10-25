# Spec traceability — Requirements → Plan → Code → Test

**Written only by `/fsd-audit`.** Never edit this file by hand: a row changes when an
audit re-reads the requirement, the code and the tests and records what it found. A
row you believe is wrong is a reason to run `/fsd-audit` on that block, not to change
the row.

**Requirements:** <!-- FILL: the revision audited — the file in `requirements/` and the
date it was captured --> · **Audited commit:** <!-- FILL: branch@commit --> ·
**Last full pass:** <!-- FILL: date --> · **Scope:** <!-- FILL: the whole project, or
the blocks audited so far -->

**Out of scope, and why:** <!-- FILL: requirement sections not audited — not built,
empty in the requirements, or owned by another system — one reason each. -->

## How to read this file

**One block per requirement section**, in the requirements' own order. Each block
names the section and the feature's plan, then one row per requirement.

**Ids.** A requirement is `<prefix>-<section>-R<n>` — e.g. `REQ-2.4-R03` — where
`<prefix>` is `[requirements].id_prefix` in `devtools/factory/factory.toml` (default
`REQ`) and `<section>` is the requirements' own section number. Untraced behaviour is
`UNT-<block>-<nnn>`. **Ids are stable — never renumbered, never reused.** A status
change keeps its history inline: `PARTIAL → VERIFIED (<date>, <PR or commit>)`.

**Evidence.** *Code* is `path:line` at the audited commit; *Test* is the test that
names the behaviour; `—` means none was found, which is itself the finding.

**Statuses:**

| Status | Means |
|---|---|
| `VERIFIED` | Built as the requirement says, with code evidence **and** a test that names the behaviour. |
| `PARTIAL` | Built without a test naming the behaviour, or only part of the requirement is built. The row says which part is missing. |
| `MISSING` | The requirement is in scope and nothing in the code implements it. A `MISSING` row in a feature marked Built is a defect — it gets a row in [`defects.md`](../defects.md). |
| `CONTRADICTED` | The code does something the requirement says it must not, or something different. A defect row, or an open question if the requirements are unclear. |
| `UNVERIFIED` | The audit could not establish either way — the code path was not read, the requirement is ambiguous, or the evidence needs a running system. The row says why. Not a pass. |
| `NOT_APPLICABLE` | Not this repository's to implement — another system or the front end owns it. The row says whose. |
| `UNTRACED` | Untraced-behaviour rows only: the code does it and no requirement asks for it. The row says whether it is harmless convention or a question. |

---

## <!-- FILL: requirement section title --> — §<!-- FILL: section --> — plan [`plans/<!-- FILL -->.md`](../plans/)

<!-- FILL: optional — the commit this block was last refreshed at, if it differs from
the header, and any caveat on how the requirement text was read. -->

| Req id | Requirement | Plan | Code (path:line) | Test | Status |
|---|---|---|---|---|---|
| REQ-<!-- FILL -->-R01 | <!-- FILL: the requirement, in the requirements' words, shortened --> | <!-- FILL: plan section --> | <!-- FILL: `path:line` --> | <!-- FILL: test name, or — --> | <!-- FILL --> |

### Untraced behaviour — <!-- FILL: block -->

Behaviour the code has that no requirement in this section asks for. Each one is
either a harmless convention, a decision recorded somewhere (say where), or a question
for [`open-questions.md`](../open-questions.md).

| Id | Code (path:line) | What it does | Nearest justification found | Status |
|---|---|---|---|---|
| UNT-<!-- FILL -->-001 | <!-- FILL --> | <!-- FILL --> | <!-- FILL --> | `UNTRACED` — <!-- FILL: convention, harmless / question Q<n> --> |
