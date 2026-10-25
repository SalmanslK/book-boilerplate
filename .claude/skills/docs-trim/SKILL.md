---
name: docs-trim
description: >
  Ongoing health maintenance for the documentation we maintain: trims overgrowth,
  removes dead or stale content, consolidates duplication, and tightens structure so
  the knowledge base stays small and dense without losing anything load-bearing. Use
  when docs feel bloated, repetitive or hard to navigate, after a run of features has
  left plans and notes behind, or on a periodic health pass. Reports before changing,
  same as docs-consistency and docs-vs-code.
---

You are gardening this repository's documentation: **minimum sufficient content that
preserves everything needed to understand, build, audit, and correctly extend the
system.** Not maximum documentation, not minimum documentation — maximum **knowledge
density**:

> **useful, correct, actionable knowledge ÷ documentation size**

A shorter document that loses a business rule is a worse document. A long document
with no unnecessary sentence in it is already lean. Judge every change by density,
never by word count alone.

**Your first deliverable is a report, not a commit.**

---

## Which skill is this

| | Compares | |
|---|---|---|
| **`docs-trim`** — this one | Docs against their own purpose and size | Continuous upkeep: trim, dedupe, restructure, remove what's dead |
| `docs-consistency` | Docs against each other | Contradictions, terminology drift, missing cross-references |
| `docs-vs-code` | Docs against code, neither presumed right | Bidirectional correctness audit |
| `docs-update-from-code` | Docs against code you already trust | One-way refresh |
| `requirements-check` | Our snapshots against the live requirement sources | Outside the repo |

**This skill does not settle who is right.** If a finding turns out to be a
contradiction between two docs, or between a doc and the code, hand it to
`docs-consistency` or `docs-vs-code` rather than guessing — gardening removes
overgrowth, it does not adjudicate correctness. If anything under `requirements/`
looks stale, that is `requirements-check`'s job, not this one's.

---

## Scope

Everything under `docs/` (per the ownership table in `CLAUDE.md`) plus `CLAUDE.md`,
the nested `CLAUDE.md` files and `README.md`. **Not** `requirements/` — read-only
source material, never rewritten to save tokens. **Not** `docs/archive/` — historical
by definition; check only that it still reads as superseded, don't compress it.
**Not** `docs/Audit/` (`fsd-audit`'s), `docs/factory/runs/` (the referee's and the
runs') or any generated doc. **Not** application code, and no code changes even if a
doc turns out to describe dead behaviour — that's a `docs-update-from-code` or
`docs-vs-code` finding, not a licence to touch the source.

Sources of truth, highest first: the current requirements → answered questions in
`docs/open-questions.md` → approved plans and `docs/architecture.md` → the code, where
consistent with itself. Use this order only to judge whether a *doc* statement is
still current — never to rewrite the requirements or the code.

---

## Workflow

### Phase 1 — Discover

Read every in-scope document in full — skimming misses both duplication and the
sentence that justifies keeping something. For each, record its owner topic per the
ownership table in `CLAUDE.md`, and check it against a two-line test: *what is this
document's one job, and would that job survive if this document vanished?* A document
with no clear job, or one already covered by its owner elsewhere, is itself a
finding.

### Phase 2 — Audit

Look for, per document and across the set:

- **Stale**: superseded requirements, implementation detail that no longer matches
  code, completed plans still carrying step-by-step build narrative instead of the
  decisions that outlived it.
- **Duplicate**: the same fact stated in two owners ("one topic, one owner" makes this
  always a finding, never intentional).
- **Diary, not knowledge base**: session narrative ("we tried X, then Y"), debugging
  blow-by-blow, or a `TODO` restating a requirement already known — keep the
  decision, drop the journey.
- **Derivable from code**: mechanics a reader gets by opening the file — a signature,
  a folder listing, an obvious default.
- **Verbose**: a paragraph doing a sentence's job, repeated examples where the rule
  alone suffices, a rule restated three ways for emphasis.
- **Missing**: a current business rule, constraint, or requirement no document
  states — flag it, don't fabricate the fix.

### Phase 3 — Classify

Every finding gets exactly one tag:

| Tag | Meaning |
|---|---|
| `KEEP` | Valuable and current as written |
| `COMPRESS` | Valuable, unnecessarily verbose |
| `MERGE` | Duplicates or overlaps another passage; needs one canonical home |
| `MOVE` | Correct content, wrong document per the ownership table |
| `REMOVE` | Confirmed obsolete, derivable, or diary-only |
| `REVIEW` | Can't confirm obsolete/current from the docs alone |

**`REVIEW` is not a fallback for laziness — it's the honest answer when status can't
be established from the documentation itself.** Route it, don't drop it:
possibly-stale-vs-code → `docs-vs-code`; possibly-contradicted-by-another-doc →
`docs-consistency`; possibly-superseded-requirement → `requirements-check`.

Never tag `REMOVE` for any of: business rules, functional requirements, domain
knowledge, constraints, architectural decisions and their rationale, security or
data-integrity rules, integration or compatibility contracts, migration requirements,
non-obvious behaviour, known edge cases, or an interpretation of the requirements
needed to implement correctly — even when long, even when it looks like it could be
inferred later. If in doubt whether a passage is one of these, it's `REVIEW`, not
`REMOVE`. The "what must survive" table in `docs-update-from-code`'s SKILL.md (open
questions, requirements history, proposed/deferred markers, the `docs/archive/`
record, trade-offs, warnings) is binding here.

### Phase 4 — Optimize

Apply `COMPRESS`, `MERGE`, `MOVE` and confirmed `REMOVE` directly, within the table
below. For each:

- **Compress** by cutting words, never by cutting the claim. A compressed rule still
  names its exception; a compressed rationale still says *why*. Prefer a table or
  list over prose when the content is enumerable. Reduce repeated examples to the one
  that earns its place.
- **Merge** into whichever side already owns the topic per the ownership table (or,
  absent a ruling there, whichever is more complete); leave a reference in the other,
  don't leave two full copies. Where the losing side also carried nuance the winner
  lacks, reconciling the *content* is a `docs-consistency` job — flag it there instead
  of quietly dropping the nuance.
- **Move** to the correct owner; leave a link behind, not a stub copy.
- **Remove** only what Phase 3 confirmed obsolete/derivable/diary — delete outright,
  don't comment out, but see below for history that must be demoted instead.
- **Never create a new markdown file** unless no existing owner covers the topic —
  `docs/CLAUDE.md` names the routine exceptions.

**Do not delete history — demote it.** A completed plan's build history, a fixed
defect's full row, an answered question's full section, a superseded requirements
comparison: move it to the matching file in `docs/archive/` — the table in
`docs/CLAUDE.md` says which. Leave the id or heading behind so every link still
resolves. What must not survive is history *presented as current guidance* — the
record surviving labelled as history is the point.

**Ask before, don't ask after:**

| Apply directly | Ask first |
|---|---|
| Compressing verbose prose without cutting a claim | Removing a full section, even if it looks diary-only |
| Fixing a broken or misdirected internal link | Merging or splitting documents |
| An exact duplicate paragraph with an obvious canonical home | Anything touching the interpretation of `requirements/` |
| Moving a passage to its stated owner, leaving a link | A `REVIEW` item — route it, don't resolve it yourself |

### Phase 5 — Validate

After applying changes: re-read each edited document against the requirement
sections and code paths it claims to describe, confirm no contradiction was
introduced between the new text and an untouched sibling document, confirm every
internal link and cross-reference still resolves, and confirm nothing on the "must
survive" list got compressed into silence.

### Phase 6 — Report

1. **Documents reviewed**, and **documents modified**.
2. **What changed**, grouped by tag (`COMPRESS`/`MERGE`/`MOVE`/`REMOVE`), one line
   each with before → after where it's not obvious.
3. **Important knowledge preserved** — the close calls: content that looked removable
   but was kept, and why.
4. **`REVIEW` items**, each routed to the skill that owns resolving it.
5. **Approximate size reduction**, only where genuinely measurable (word or line count
   per file); don't manufacture a number for its own sake.

---

## Rules that hold in every mode

**Density over brevity.** The target is knowledge per token, not a smaller file. Do
not compress a passage below the point where a future reader would have to re-derive
the reason behind a decision.

**Never silently delete uncertain content.** `REVIEW`, then route it — silence here is
indistinguishable from an unreviewed loss six months later.

**Never touch `requirements/`, never rewrite the requirements or another
source-of-truth document to make the docs smaller**, and never change application
code.

**One finding, one tag.** Don't apply `REMOVE` to something you've also flagged
`REVIEW` — resolve the ambiguity by routing it, not by picking the more convenient
tag.

---

## Modes

| Invocation | Does |
|---|---|
| `/docs-trim` | Full pass: Phases 1–6, applies safe optimizations, asks before the rest |
| `/docs-trim audit` | Phases 1–3 only. Reports proposed tags. Changes nothing |
| `/docs-trim optimize` | Phases 1–4, applying everything in the "apply directly" column; asks before the rest |
| `/docs-trim verify` | Phases 1–2 and 5, against the current requirements and code. Correctness over restructuring — reports drift, doesn't reorganize |

---

## The rule that matters most

**A garden that has been weeded down to nothing is not tended, it's gone.** Every
`REMOVE` is a bet that the knowledge is truly derivable or truly dead. Bet wrong and
the next person to need it has no way to know it ever existed — that's why `REVIEW`
exists, and why it's never a shortcut to skip.
