---
name: docs-update-from-code
description: >
  One-way refresh of the documentation we maintain so it matches what the code
  actually does. Code wins on questions of behaviour; it does not win on questions of
  intent. Use it only when you already know the code is right and the docs have
  lagged — after a merge or a rename you performed yourself. If you are unsure which
  side is wrong, use docs-vs-code instead. Reports what it changed.
---

You are bringing this repository's documentation back in line with its code.

**Code is the source of truth for what the system *does*. It is never the source of
truth for what the system *should* do.** That distinction is the whole skill. Get it
wrong and you will faithfully document a bug as a design.

---

## Which skill is this

| | |
|---|---|
| **`docs-update-from-code`** — this one | You know the code is right and the docs have lagged. After a merge, a rename, a refactor **you performed**. One-way; applies changes. |
| `docs-vs-code` | You do not know which side is wrong. Before a release, or when something smells. Bidirectional, reports before changing. |
| `docs-consistency` | The documents disagree with *each other*. No code involved. |
| `requirements-check` | The **requirements** may have moved under you. Run it first if the docs cite a requirement section you are about to rewrite. |

**If you find yourself unsure whether the code is correct, stop and run
`docs-vs-code`.** This skill has no mechanism for being wrong safely.

**The window this skill is for is narrower than it looks.** It is not "faster" in the
sense of skipping analysis — the stop conditions below and the "what must survive"
table demand most of the same reading. What it saves is the report-and-approve round
trip, and it earns that only when you already know what changed because you changed
it.

---

## What you may edit

Only documentation we write and maintain:

- `docs/system-overview.md`, `docs/architecture.md`, `docs/project.md`,
  `docs/open-questions.md`, `docs/defects.md`
- `docs/plans/*.md`
- `CLAUDE.md`, `docs/CLAUDE.md` and any nested `CLAUDE.md`, `README.md` and other
  how-to-run files
- Doc comments and inline comments, where they make a claim that is now false

## What you must not touch

**`requirements/` is read-only.** The requirements and every supporting document
there are snapshots of documents we do not own. They are evidence, never a target. If
the code disagrees with the requirements, **that is not a documentation problem** —
see the stop conditions below.

**`docs/archive/`** is historical by definition. Leave it; check only that it is still
labelled as superseded.

**`docs/Audit/`** is `fsd-audit`'s alone. **`docs/factory/runs/`** belongs to the
factory's referee and the run's own steps. **Generated docs** (`[guard.generated]` in
`devtools/factory/factory.toml`, and any `CLAUDE.md` lists) are regenerated with their
command, never edited by hand.

**Implementation code**, beyond a comment that states something untrue.

---

## What must survive, no matter how little code supports it

This is where a literal reading of "remove speculative content" does real damage.
None of the following has a code counterpart, and **all of it is load-bearing**:

| Keep | Why |
|---|---|
| **`docs/open-questions.md`** — every open and answered question | Their recommendations and answers. No code will ever evidence these |
| **`docs/project.md` Part 3**, and the requirements history in `docs/archive/` | What changed between revisions of the requirements |
| **Delivery schedule and feature status tables** | Facts about the project, not about the code |
| **"Not built yet", "Known trade-offs", "Open decisions"** | Deliberate statements that something is absent. Deleting them makes a gap look like an oversight |
| **Proposed and deferred markers** | They mark an unanswered question, not stale text |
| ***Why* a decision was made, and what would change it** | The most valuable content in `architecture.md`, and the least code-evidenced |
| **`docs/archive/`** — closed defects, answered questions, plan and architecture history | The record of where code departed from design — including mistakes. Archived, never deleted |
| **Bugs and traps recorded as warnings** | They describe things that are *not* in the code precisely because they were fixed |

**The test:** would deleting this sentence lose knowledge that the code cannot
re-derive? If yes, it stays regardless of whether the code mentions it.

---

## Stop conditions

Stop and report rather than editing when:

- **The code contradicts the requirements.** The requirements win every conflict.
  This is a defect or a question for `docs/open-questions.md` — never a doc rewrite
  that enshrines the code's version.
- **The code contradicts a dated answer** recorded against a question. Someone decided
  that; the code drifting from it is a finding.
- **The code and the tests disagree.** You cannot tell which is intended. Report it.
- **The behaviour looks wrong.** If documenting it accurately would mean writing down
  something nobody would have asked for, say so instead.
- **You cannot establish intent** — the behaviour exists and nothing explains why.
  Describe what it does, flag that the reason is unknown, and do not invent one.

In every case: **finish the rest of the pass, and list what you stopped on.**

---

## The pass

**Read `../docs-vs-code/references/comparison-checklist.md`** and work through it.
It lists what to compare, where drift tends to happen, and the rule about measuring
numbers rather than carrying them forward.

That file is owned by `docs-vs-code` and shared with this skill. **Add a new
concern there, not here**, so both skills get it.

Work outward, so later sections rest on facts established earlier: architecture, then
domain, then database, then the public contract, then flows, then configuration, then
tests and diagrams. Where the checklist says *compare*, you **update the
documentation** — unless a stop condition above applies.

---

## House rules that still apply

`CLAUDE.md` and `docs/CLAUDE.md` own these; they are named here because this skill
edits documentation and would otherwise break them. Read them if any is unclear.

- **One topic, one owner** — put the fact where it belongs and link from elsewhere
- **No new markdown file** unless no existing owner covers the topic
- **A question goes to `docs/open-questions.md`**, never answered by guesswork
- **Do not delete history — archive it**
- **A generated doc is regenerated**, not edited

Run a link and anchor check across every markdown file when you are done.

---

## Report

Finish with:

1. **Files changed**, and what changed in each.
2. **Corrections that mattered** — where the documentation had been actively wrong,
   as opposed to merely thin. These are the ones worth a reader's attention.
3. **Content removed**, and why it was safe to remove.
4. **Stop conditions hit** — every place the code and the requirements, the tests, or
   plain sense disagreed. **This is the most important section**; a pass that found
   nothing to escalate either got lucky or was not looking.
5. **Gaps** — implemented behaviour whose intent could not be established.
6. **Verification** — build, the tests the change could affect, link check. Report the
   numbers.

---

## The rule that matters most

**Accuracy over tidiness.**

A document that says "we do not know why this works this way" is more useful than a
confident sentence someone invented to make the page look finished. If the code
cannot tell you the answer, say that it cannot.
