---
name: docs-consistency
description: >
  Makes the docs/ directory behave as one coherent knowledge base rather than a pile
  of separate files. Finds duplication, contradictions, terminology drift and missing
  cross-references between documents, establishes a canonical home for each concept,
  and reports before changing anything. Documentation-to-documentation only — it
  never opens the code to decide who is right.
---

You are making a project's documentation **work as one system**.

Every document has a job. Concepts have one home and are referenced from everywhere
else. Nothing contradicts anything. That is the whole skill:

> **ONE CONCEPT → ONE CANONICAL HOME → MANY REFERENCES → MINIMAL DUPLICATION → ZERO CONTRADICTIONS**

**Your first deliverable is a report, not a commit.**

---

## The boundary that defines this skill

You compare **documentation against documentation**. You do not open the code to
decide which document is right.

| | |
|---|---|
| **`docs-consistency`** — this one | Do the documents agree with *each other*? Duplication, contradictions, terminology, structure, navigation. Reports before changing. |
| `docs-vs-code` | Do the documents agree with the *code*? Bidirectional, neither side presumed correct. |
| `docs-update-from-code` | The code is known-good; refresh the docs to match. One-way. |
| `docs-trim` | Are the documents bigger than their job? Trims, dedupes, archives. |
| `requirements-check` | Have the **live requirement sources** moved? The only skill that looks outside the repository. |

This matters because the two questions have different answers. Two documents can
agree perfectly and both be wrong about the system — that is not yours. Two documents
can disagree while one of them matches the code exactly — you can see the
disagreement but **not** which one is correct.

**When a contradiction cannot be settled from the documentation alone, report it as
`Requires Code/Documentation Reconciliation` and stop.** Do not open a source file to
break the tie, and never assume the newer document, the longer one, or the one you
read first is the right one. Guessing here is worse than reporting, because a
confident wrong resolution removes the evidence that anything was ever in doubt.

---

## When to invoke

- A full `docs/` audit — before a release, after a run of merges, when onboarding
  someone reveals the docs disagree
- Ongoing harmonization — after adding or substantially editing documents
- When someone asks "which document is right?", "where does this belong?", or "why is
  this explained in three places?"
- When a concept has been renamed and the old name may survive somewhere

---

## Scope

Recursively, everything under `docs/`: markdown and other documentation text files,
decision records, plans, guides, and the **diagrams embedded in them** — every diagram
makes claims and can contradict prose.

**Read the ownership table first.** `CLAUDE.md`'s *Where knowledge lives* table
assigns each topic to one document (`docs/system-overview.md`,
`docs/architecture.md`, `docs/project.md`, `docs/open-questions.md`,
`docs/defects.md`, `docs/plans/`, and so on), and `docs/CLAUDE.md` says how each is
written. **That ruling wins over your own judgement**, and a document violating it is
a finding. If the project has no such table, discover the set from its index or
`README` rather than assuming one.

Also read, but do not treat as yours to reorganise:

- **Root-level docs** (`README.md`, `CONTRIBUTING.md`, `CLAUDE.md` and nested
  `CLAUDE.md` files). They routinely duplicate `docs/` content, so they are in scope
  for *findings*. Edit them only when the project's own rules place them in your care.
- **`requirements/`** — snapshots of documents we do not own. **Read-only.** Evidence
  for settling a contradiction, often the deciding evidence — but a finding is never
  raised *against* them.
- **`docs/archive/`** — closed history. Not stale by definition. Check only that it is
  still clearly marked as superseded, and that nothing current links to it as though
  it were live.
- **`docs/Audit/`** (written only by `fsd-audit`), **`docs/factory/runs/`** (the
  factory's referee and the runs' own steps) and **generated docs**
  (`[guard.generated]` in `devtools/factory/factory.toml`). Findings about them go to
  their owner; you never edit them.

---

## Workflow

Phases 1–3 build the map. Phases 4–7 use it. Do not start editing during any of them.

### Phase 1 — Inventory

Read every document. **Read them fully** — duplication and contradiction both hide in
the middle of long files, and a skim finds neither.

Record: path, its apparent purpose, main topics, documents it links to, documents it
appears to overlap, and concepts it looks authoritative about.

### Phase 2 — Responsibility mapping

Give every document a one-line statement of its job. Then flag the ones where you
could not: unclear responsibility, several unrelated responsibilities, heavy overlap
with a sibling, or no obvious place in the hierarchy.

**A document whose responsibility you cannot state in one line is itself a finding.**
That is usually where duplication has been accumulating.

### Phase 3 — Canonical knowledge map

For each significant concept, name the document that owns the authoritative
explanation, and list every other place it appears.

A concept may be summarised anywhere. Only one document should carry the detailed
authoritative version. Where two documents both look authoritative, that is a **C4
scope conflict** — see `references/canonical-ownership.md` for how to choose, and for
when the answer is to split the concept rather than pick a winner.

### Phase 4 — Duplication analysis

Classify every repeat as D1–D4 and decide whether it is harmful. **D4, intentional
contextual summary, is correct and stays.** Full rules and worked examples:
`references/duplication-rules.md`.

### Phase 5 — Contradiction analysis

Classify every conflict as C1–C5. C5 — ambiguous, insufficient evidence — is never
resolved by you. Full rules: `references/contradiction-rules.md`.

### Phase 6 — Terminology harmonization

Build the preferred-term list, the deprecated synonyms, and the acronyms. **Different
words for genuinely different concepts are not a conflict** — check before you
normalise. Full rules: `references/terminology-guidelines.md`.

### Phase 7 — Cross-reference analysis

Find concepts mentioned without a link to their canonical home, places a reader would
be stuck, duplicated detail replaceable by a reference, and broken or misdirected
internal links.

**Prefer few meaningful links to many.** A document where every third noun is a link
is harder to read, not easier. Do not add a link merely because you can.

---

## Modes

Default to **Audit** unless the invocation says otherwise.

| Mode | Does | Ends with |
|---|---|---|
| **Audit** | Phases 1–7. Changes nothing. | The report |
| **Review** | Audit, plus sorting every finding into *safe*, *recommended*, or *needs a decision* | The report, and a decision list |
| **Harmonize** | Applies what is approved or unambiguously safe | A list of what changed, and what was left |

Use `templates/harmonization-report.md` for the report's shape.

In **Harmonize**, before any structural change — merging, splitting, moving,
renaming, deleting, or moving canonical ownership — state which files are affected
and how ownership changes, **and get approval**. Safe changes may be applied and
listed as done.

| Apply freely | Ask first |
|---|---|
| Broken or misdirected internal links | Deleting a document |
| Exact duplicate paragraphs, where a canonical home is already clear | Merging or splitting documents |
| A cross-reference typo | Moving canonical ownership |
| An established term used inconsistently | Resolving an ambiguous contradiction |
| A missing link to an explicitly canonical document | Rewriting business or architectural content |

---

## Rules that hold in every mode

**Reducing duplication is not the goal; coherence is.** A reader who has to open three
documents to understand one thing is worse off than one who read a sentence twice.
Cutting a helpful summary down to a bare link is a regression, and it is the most
common way this work goes wrong.

**Never resolve a contradiction by deleting one side.** Determine which is canonical
and correct the other, or flag it. Deleting the losing side destroys the evidence that
the disagreement existed.

**Do not delete history — demote it.** Move it to the matching file in
`docs/archive/` as `docs/CLAUDE.md` says. What must not survive is history presented
as current guidance.

**Respect the project's own conventions.** `docs/CLAUDE.md` limits new markdown files
to named exceptions — do not propose one without saying why the existing owners
cannot hold the content. Follow the ownership table.

---

## Final validation

After harmonizing, confirm and **report the numbers rather than asserting success**:

- Every document has a stated purpose; related documents complement rather than
  compete
- Every major concept has one canonical home, and no concept has two authoritative
  explanations without a stated reason
- Harmful duplication is reduced; intentional summaries survived
- No contradiction is unresolved *and* unflagged
- Terminology is consistent, or the exception is documented
- Internal links resolve — check them, do not assume
- Names describe responsibilities

---

## The rule that matters most

**Map first. Change later.**

Every edit you make is evidence destroyed about how the documentation got into the
state it was in. Knowing *which* document was wrong, and saying so, is worth more than
a directory that quietly agrees with itself.
