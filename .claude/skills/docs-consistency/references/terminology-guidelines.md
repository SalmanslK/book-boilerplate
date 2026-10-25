# Terminology guidelines

One concept, one word.

Two words for one thing make a reader wonder whether they have missed a
distinction. One word for two things is worse — they will not wonder at all, and
will act on the wrong one.

---

## Before normalising anything: are they the same concept?

**Verify before you unify.** This is the step that gets skipped, and skipping it
does real damage: collapsing two terms deletes a distinction the documentation was
carrying, and the loss is invisible afterwards because the text still reads well.

For each apparent synonym, check:

- Do they have the **same definition** wherever each is defined?
- Are they used in the **same contexts**, interchangeably?
- Does any document use **both**, in a way that distinguishes them?
- Do they map to the **same thing** in the system, or to related-but-different
  things?

A single document using both terms deliberately is strong evidence they are
different. Treat that as the answer, not as an inconsistency.

Legitimate reasons for different words:

- **Different concepts that sound alike.** A "customer" in the billing sense and a
  "customer" in the tenancy sense are two things; one word for both is the bug.
- **Different audiences.** Business documentation and technical documentation may
  each use their own vocabulary correctly. Record the mapping rather than forcing
  one on the other.
- **A quoted external term.** A specification's or a vendor's word, quoted
  deliberately. Do not rewrite quoted source material.
- **A deliberate distinction between the general idea and a specific type.**

---

## Building the model

| Preferred | Avoid | Notes |
|---|---|---|
| Production Order | Production Job, Work Order | One concept, three names |
| Tenant | Customer, Account | "Customer" also means a business entity — keep them apart |
| WIP | Work In Process | Expand once at first use, then abbreviate |

Also record:

- **Ambiguous terms** — one word, several meanings. These are more urgent than
  synonyms and usually need *more* vocabulary, not less.
- **Acronyms** — expand once per document at first use, then abbreviate. An
  acronym never expanded anywhere is a finding.
- **Deprecated terms** — the old name after a rename, and where it survives.

---

## Choosing the preferred term

1. **The project's glossary or index**, if it has one. It decides.
2. **The term the system itself uses** — entity names, screen labels, API fields.
   Documentation that disagrees with the thing being described creates a
   translation step for every reader.
3. **Read-only source material** — specifications and client documents. If the
   requirements say "Purchase Order", the documentation says "Purchase Order".
4. **Prevalence**, but only among documents that did not copy each other.
5. **Precision.** Prefer the term that cannot be confused with something else,
   even where it is longer.

**Do not invent a new term to resolve a conflict between two existing ones.** That
produces a third term and leaves you with three.

---

## Renames

A rename is only finished when the old term is gone from current guidance.

- Check **every** location — prose, headings, tables, code samples, **diagram
  labels**, link text, file names, alt text
- Diagram labels are the ones that get missed, because they are not caught by
  reading the prose
- If the old term must remain findable, note it once in the glossary as a former
  name. Do not scatter parenthetical aliases through the text.

---

## The glossary

Worth creating or updating when several terms are ambiguous, when business and
technical vocabularies both exist, or when there is no single place a reader can
resolve a word.

Each entry: the term, one-sentence definition, and where the concept's canonical
explanation lives. **The glossary defines the word; it does not become a second
home for the concept** — an entry that grows into a full explanation is a
duplication finding against itself.

**Creating a glossary is a structural change and needs approval.**

---

## What not to do

- **Do not normalise terminology inside quoted source material**, historical
  records, or archived documents. Quotes and history are supposed to preserve the
  original words.
- **Do not enforce a preferred term where the other word is being defined**, or
  named as deprecated.
- **Do not treat a plain-English word as a term.** Not every noun is vocabulary,
  and rewriting ordinary prose to hit a word list makes documentation worse.
- **Do not rename in the same pass as consolidating duplication.** Two kinds of
  change at once makes both impossible to review.
