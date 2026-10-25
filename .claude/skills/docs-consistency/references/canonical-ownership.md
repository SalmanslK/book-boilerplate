# Canonical ownership

Every significant concept has **one document responsible for the authoritative
explanation**. Everything else summarises and links.

Without this, a concept is maintained in several places by several people, none of
whom know the others exist. That is how documentation becomes untrustworthy — not
through any single bad edit, but through nobody being responsible.

---

## What a canonical home means

The owner:

- Carries the **complete, detailed, current** explanation
- Is where a correction is made **first**
- Is what other documents point at

Other documents may carry a summary, an example, or the part relevant to their own
subject. What they must not carry is a **second complete explanation** that
someone would have to remember to update.

---

## Choosing the owner

When several documents cover a concept, in this order:

1. **The project's own ruling.** An index, contributor guide, or documentation map
   that assigns topics to documents decides it. Do not overrule it — a document
   contradicting it is the finding.
2. **Subject match.** The concept belongs where its *subject* lives, not where it
   happens to be longest. A database naming convention belongs with the database
   documentation even if the architecture document explains it better today.
3. **Where the detail already is.** Prefer the document that already carries the
   depth. Moving detail risks losing it.
4. **Where a reader would look first**, without being told.
5. **Rate of change.** Where two homes are equally defensible, prefer the one
   whose update rhythm matches the concept's. A weekly-changing fact in a
   rarely-touched document goes stale silently.

**Where the concept is genuinely one thing, resist splitting it across documents
"by aspect".** That reproduces the problem with extra steps.

---

## Naming the owner in the document

Ownership only works if it is visible. The owning document should say what it
owns; a summarising document should say where the detail is.

```
architecture.md
    "This document owns the layering rules and where each class belongs."

development-guide.md
    "Layering rules are in architecture.md §2; this guide covers the
     day-to-day workflow only."
```

Unstated ownership decays. The next person to add a section will put it wherever
seems reasonable to them.

---

## When ownership is contested (C4)

Two documents both look authoritative. Before picking a winner, ask **whether they
are actually describing the same concept.**

They frequently are not. Two documents describing "the order workflow" may be
describing the *business* process and the *system's* state machine — related,
different, and both legitimately owned where they are. The fix then is not to
merge but to **name the two things differently** and cross-link them. Merging them
destroys a real distinction and produces a document that is wrong about both.

Where it is genuinely one concept:

1. Choose the owner by the criteria above.
2. Move anything true that only exists in the loser into the winner **before**
   reducing it.
3. Reduce the loser to a summary and a link.
4. State the ownership in both.
5. Fix inbound links that pointed at the loser's detailed section.

---

## When there is no owner

A concept explained in several places, none of them authoritative — typically
because it grew by accretion.

**Prefer promoting an existing document to creating a new one.** A new file is a
new thing to keep in sync, and many projects have explicit rules against adding
them. Create one only when no existing document could reasonably own the topic,
and say why in the recommendation.

---

## When ownership should move

Ownership is not permanent. Move it when:

- The concept has outgrown a section in a document about something else
- A more specific document now exists
- The current owner has become a grab-bag
- The owner is rarely maintained and the concept changes often

**Moving ownership always requires approval.** It changes where people look, and
it invalidates links and habits.

---

## The concept map

Maintained through Phase 3 and reported:

| Concept | Canonical home | Summarised in | Action |
|---|---|---|---|
| CQRS | `architecture.md` §3 | `README.md` | None — D4 |
| Error format | `api/errors.md` | endpoint docs | Add links from three endpoint docs |
| Order workflow | *contested* | — | **C4 — decide** |

Concepts worth mapping are the ones **more than one document mentions**. A concept
appearing once needs no ownership decision; it has one by default.
