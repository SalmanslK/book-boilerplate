# Contradiction rules

A contradiction is two documents making statements that cannot both be true.

**Your job is to find them and determine which is canonical — not to make them
agree.** Two documents can be made to agree while both are wrong. That looks like
success and is worse than the disagreement, because the disagreement was the
signal that something needed checking.

---

## The five kinds

### C1 — Clear contradiction

Two documents make explicitly incompatible statements about the same thing.

```
architecture.md      →  Repository interfaces belong in Domain.
development-guide.md →  Repository interfaces belong in Application.
```

**Resolvable from the documentation when** one document is the canonical owner of
that topic, or another document, an ADR, or a dated decision settles it.

**Not resolvable when** both are equally authoritative and nothing else refers to
it. Then it becomes C5.

### C2 — Version drift

One document describes an earlier state of the world; the rest have moved on.

The tell is a cluster: one document disagrees with *several* others, all of which
agree with each other. A dated decision, a changelog, or a "changes after
implementation" section usually confirms it.

**One agreeing pair is not a majority.** Two documents can be stale together —
especially if one was written by copying the other.

**Action:** update the drifted document. Preserve the old statement as history if
the project keeps such a record; do not present history as current guidance.

### C3 — Terminology conflict

Different terms for one concept.

```
Document A →  Container
Document B →  Bin
Document C →  Storage Unit
```

**Verify they are actually the same concept first.** Three terms sometimes mean
three things, and collapsing them destroys a real distinction. See
`terminology-guidelines.md`.

### C4 — Scope conflict

Two documents both claim to be the authoritative source for one concept. Often no
statement disagrees — the conflict is about ownership, and contradictions are what
it will produce next.

**Action:** establish one owner. See `canonical-ownership.md`, including the case
where the right answer is to split the concept rather than pick a winner.

### C5 — Ambiguous conflict

The documents differ and nothing in the documentation determines which is right.

**Do not resolve this.** Flag it, name every conflicting source with its location,
state precisely what would settle it, and move on.

If what would settle it is the code, label the finding:

> **Requires Code/Documentation Reconciliation**

and hand it to `docs-vs-code`. If what would settle it is a human decision —
two workflows that are both coherent, two architectural rules that are both
defensible — say who needs to decide and what the options are.

---

## What to compare

Contradictions cluster in specifics rather than prose. Concentrate on:

- **Names** — entities, modules, components, roles, services, files
- **Structure** — layers, boundaries, dependency direction, where things belong
- **Workflows and states** — the set of statuses, allowed transitions, terminal
  states, who can trigger what
- **Contracts** — endpoints, request and response shapes, error formats, status
  codes, permissions
- **Data** — tables, columns, nullability, keys, relationships, cardinality,
  constraints
- **Procedures** — setup, configuration, environment names and values, deployment
  steps
- **Conventions** — naming, formatting, structural rules
- **Stack** — technologies, versions, licences
- **Numbers** — counts, limits, timeouts, sizes, versions. These are quoted freely
  and checked rarely, so they drift almost by default.

**Diagrams contradict prose constantly.** A diagram is a claim. Compare its boxes,
arrows, ordering, cardinality and labels against the text in the same document and
against the equivalent diagram elsewhere. A diagram nobody updated is the most
believed stale artefact in any documentation set — people trust pictures.

**A deliberately simplified diagram is not a contradiction.** Say so rather than
"correcting" a conceptual overview into an implementation drawing.

---

## Weighing the evidence

When two documents disagree, in this order:

1. **The project's own ruling on ownership.** An index or contributor guide that
   says which document owns the topic settles it — the other document is wrong by
   the project's own rules, regardless of which reads better.
2. **A dated decision** — an ADR, a decision log, a resolved question. A decision
   outranks any document written before it.
3. **The canonical home for that concept**, as established in Phase 3.
4. **Read-only source material** — specifications, contracts, client documents.
   Strong evidence, and never itself a finding.
5. **Corroboration.** Several independent documents agreeing, where they were not
   copied from one another.
6. **Specificity.** A precise, detailed statement usually beats a passing mention
   — the passing mention is more often the careless one.

**Recency is not evidence.** A recently edited document can be a recently
introduced error. Use dates to identify *drift* (C2), never to settle a
disagreement on their own.

**Never break a tie by opening the code.** That is the boundary of this skill. A
tie is C5.

---

## Reporting a contradiction

Every finding needs:

- **Every conflicting location**, by file and section — not just two of them
- **The conflict in one sentence**, stated so a reader can see the incompatibility
  without opening either file
- **The classification** C1–C5
- **What settles it**, or what would
- **The recommendation**, or an explicit "needs a decision"
- **A confidence** of High, Medium or Low

Low confidence is a legitimate answer. A finding reported honestly at low
confidence is more useful than one asserted at high confidence and wrong.
