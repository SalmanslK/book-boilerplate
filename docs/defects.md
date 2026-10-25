# Defects — what was wrong, and what happened to it

**Status:** `Live` · **Owner of the record:** whoever finds the defect

What is wrong with what we built, from any source — a user, a code review, QA, a
factory run, or ourselves. **Open rows are in full; fixed ones are one line each**, with
the full row in `archive/defects-closed.md`. How to write a row, and when a finding is
not a defect: [`docs/CLAUDE.md`](CLAUDE.md#defects).

**Status** (open rows): `Open` · `Mitigated` (harm contained, cause remains) ·
`Partial` (part fixed) · `Blocked — <Q id or party>`. Nothing else.
**Severity** is consequence, not effort: 🔴 unsafe or unusable in production ·
🟠 wrong behaviour · 🟡 real cost, contained · 🔵 minor.

## How to use it

1. **Add a row the day a defect is confirmed**, in [Open](#open), with the next `D<n>`
   (see [Numbering](#numbering)).
2. **Keep it short** — what is wrong, where, and the requirement it breaks, in about
   600 characters. The investigation goes in the commit or the PR.
3. **A defect a factory run introduced says so** in its Note — *Introduced by run
   [R-nnn](factory/runs/R-nnn.md)* — so `factory report` can count what escaped each run.
4. **Put the id in the fixing commit's message.**
5. **When it is fixed:** re-verify in the code, then move the full row to
   `archive/defects-closed.md` with its commit in *Fixed in*, and add a one-line entry
   to [Fixed](#fixed). Never delete a row; never reuse an id.

Row format:

```
| **D<n>** | <sev> | <status> | **<what is wrong, in one bold sentence.>** <the detail> | `<file:line>` | <who, how, date> | <what to do; the requirement it traces to, or "No requirements angle"> |
```

---

## Open

| Id | Sev | Status | Defect | Where | Found by | Note |
|---|---|---|---|---|---|---|

### Numbering

**The base branch is authoritative and ids are never reused.** A gap in this file is
an id taken on a branch that has not merged yet. Before taking the next number, check
the highest id on the open branches as well as on the base.

---

## Fixed

One line per fixed defect, newest first. The full row is in
`archive/defects-closed.md`. `Fixed in` is the commit or branch.

| Id | Defect | Fixed in |
|---|---|---|

## Won't fix, or not ours

| Id | Addressed by | Defect | Why |
|---|---|---|---|

---

## Keeping this honest

**Re-verify before reporting a row as fixed.** Grep is not proof — open the code.

**A row moved to Fixed carries its commit**, so the reasoning survives even when this
table is skimmed.

**Counts belong nowhere but here.** Do not quote "8 open defects" into another
document — link to this one.
