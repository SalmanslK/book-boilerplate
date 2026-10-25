# Harmonization report — <date>

Mode: **Audit | Review | Harmonize** · Scope: `<paths analysed>`

> Delete any section with nothing in it. An empty table is noise; a sentence
> saying "no contradictions found" is a result.

---

## Executive summary

| | |
|---|---|
| Documents analysed | |
| Duplication findings | D1: · D2: · D3: |
| Contradictions | C1: · C2: · C3: · C4: · C5: |
| Terminology findings | |
| Missing or broken cross-references | |
| Documents with unclear responsibility | |
| Structural changes proposed | |
| **Needing a human decision** | |
| **Needing code/doc reconciliation** | |

Two or three sentences on the **state of the documentation as a system** — not a
restatement of the table. Is it coherent? Where is it decaying, and why?

---

## Inventory

| File | Responsibility | Canonical topics | Notes |
|---|---|---|---|

One line per document. If the responsibility could not be stated in one line, say
so — that is a finding in itself.

---

## Duplication findings

| ID | Files | Topic | Type | Recommendation | Confidence |
|---|---|---|---|---|---|

Then, per finding:

```
DOC-DUP-001
Files:            README.md §2
                  architecture/cqrs.md §1
Topic:            CQRS implementation
Type:             D1 — exact duplication
Canonical home:   architecture/cqrs.md
Recommendation:   Keep the detailed explanation in architecture/cqrs.md.
                  Reduce the README to a three-line summary plus a link.
                  Nothing in the README copy is absent from the canonical one.
Confidence:       High
```

State explicitly whether the non-canonical copy contains anything the canonical
one lacks. That is what makes the recommendation safe to act on.

---

## Contradiction findings

| ID | Files | Topic | Conflict | Type | Recommendation | Confidence |
|---|---|---|---|---|---|---|

Per finding: every conflicting location, the incompatibility in one sentence, what
settles it — or, for C5, what *would*.

Findings that cannot be settled from documentation alone go under their own
heading:

### Requires code/documentation reconciliation

| ID | Files | The question | What would answer it |
|---|---|---|---|

Hand these to `docs-vs-code`. Do not resolve them here.

---

## Terminology findings

| ID | Terms in use | Preferred | Files affected | Recommendation |
|---|---|---|---|---|

For any pair proposed for unification, say how you confirmed they are the same
concept.

---

## Canonical knowledge map

| Concept | Canonical home | Summarised in | Action |
|---|---|---|---|

Contested ownership is marked and listed under decisions, not silently assigned.

---

## Cross-reference findings

| ID | File | Issue | Recommendation |
|---|---|---|---|

Broken links, links to the wrong section, and concepts mentioned without a route
to their canonical explanation. **Not** every place a link could be added.

---

## Structural recommendations

Merges, splits, renames, moves, a glossary, an index, a new canonical document.
Each with: what changes, which files, how ownership moves, and what it costs.

**None of these are applied without approval.**

---

## Decision list

| ID | Decision needed | Options | Who decides |
|---|---|---|---|

One line each, e.g. `DOC-CON-003 → keep architecture.md's rule, or the guide's`.

---

## Applied changes

*Harmonize mode only.*

| ID | File | Change | Approved by |
|---|---|---|---|

And a short list of what was **deliberately not** applied, with the reason —
awaiting approval, insufficient confidence, or needing reconciliation.

---

## Validation

Report the numbers; do not assert success.

| Check | Result |
|---|---|
| Documents with a stated responsibility | n of m |
| Concepts with exactly one canonical home | n of m |
| Internal links resolving | n of m checked |
| Contradictions resolved / flagged / open | |
| Harmful duplication reduced | |
| Intentional summaries preserved | |
