# Duplication rules

Repetition is not the defect. **Unmaintainable repetition is.**

The test is not "does this appear twice?" It is: **when this fact changes, how
many places must change, and will someone find them all?** Two copies that must
be edited together are a defect. A one-line summary pointing at the detail is not
— it stays true when the detail changes.

---

## The four kinds

### D1 — Exact duplication

The same or near-identical content in two or more places. Copy-paste, or a
document grown by importing a section from another.

**Almost always harmful.** The copies drift, and the stale one is believed because
nothing marks it as the copy.

**Action:** keep the detail in the canonical home. Replace the others with a
summary and a link, or with a link alone where the surrounding text already
provides the context.

### D2 — Semantic duplication

Different words, substantially the same information. Two independent explanations
of one mechanism.

**Harder to find and more dangerous than D1**, because the two versions have
usually already drifted — each was written from a different understanding, and
the differences look like detail rather than disagreement.

**Check for a hidden contradiction before consolidating.** If the two explanations
do not actually say the same thing, this is not a duplication finding; it is a
contradiction (C1 or C5) wearing a disguise.

**Action:** establish which is canonical, verify the other adds nothing true that
the canonical version lacks, fold in anything it does add, then reduce it.

### D3 — Partial duplication

A shared core, with each copy adding something unique.

**The hardest to handle and the easiest to damage.** The unique parts are usually
the reason each document exists, so consolidating carelessly deletes exactly what
was worth keeping.

**Action:** consolidate the shared core into the canonical home. Leave each
document its unique contribution, plus a link. If the "unique" parts turn out to
be audience differences — the same explanation pitched at two readers — that is a
signal the canonical home may need two sections rather than the duplication being
wrong.

### D4 — Intentional summary

A short contextual explanation that correctly complements a canonical document.

**This is correct. Leave it alone.**

```
README.md          →  "The system uses CQRS: commands and queries are
                       separated. See architecture.md for how."

architecture.md    →  The full explanation of the CQRS implementation.
```

The README is not duplicating; it is orienting. A reader who never opens
`architecture.md` still leaves knowing something true.

**Cutting D4 down to a bare link is a regression**, and it is the most common way
this work does damage. A document that only says "see elsewhere" has stopped
carrying its own weight.

---

## Deciding whether duplication is harmful

Harmful when:

- The copies have already drifted
- The content is detailed enough that it will need updating
- Nothing marks which copy is authoritative
- A reader could act on the stale copy without ever seeing the canonical one
- The same fact is independently *maintained* in both places

Acceptable when:

- It is short, stable, and orienting
- It gives a reader enough to continue without a detour
- It links onward to the canonical home
- The repetition serves a genuinely different audience or purpose

**Stability matters as much as length.** A five-line summary of something that
changes weekly is more dangerous than twenty lines describing something settled
years ago.

---

## Consolidating without doing damage

1. **Establish the canonical home first.** Never consolidate before you know where
   the detail lands — see `canonical-ownership.md`.
2. **Compare the copies line by line.** Anything true that only exists in a
   non-canonical copy must move to the canonical home *before* it is reduced.
   This is where content gets silently lost.
3. **Reduce, do not delete.** Leave a summary that stands on its own, plus a link.
4. **Name what is at the other end.** "See `architecture.md`" is weaker than "see
   `architecture.md` §4 for how access rules are applied" — the second tells
   a reader whether the detour is worth it.
5. **Check the incoming links.** If something linked to the section you reduced,
   confirm it still lands somewhere useful.

---

## What not to do

- **Do not consolidate across audiences without thinking.** An operator's runbook
  and an architecture document may describe the same mechanism for good reason.
- **Do not consolidate a contradiction.** Picking one wording and deleting the
  other hides a disagreement instead of resolving it.
- **Do not chase a duplication count.** The measure is whether the documentation
  is easier to keep true, not how few repeated words survive.
- **Do not create a new document to hold consolidated content** unless no existing
  document could own it. A new file is a new thing to keep in sync.
