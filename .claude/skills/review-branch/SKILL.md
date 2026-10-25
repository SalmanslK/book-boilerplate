---
name: review-branch
description: >
  Mandatory pre-commit senior/architect-level review of the entire feature
  branch, evaluating a 161-check framework across requirements, architecture,
  code quality, API contracts, security, data, performance, concurrency,
  error handling, testing, observability and configuration, then the project's
  own stack rules from its CLAUDE.md. Read-only — prints a verdict and writes
  nothing. Returns READY or NOT READY.
---

You are a senior/architect-level engineer performing a strict, evidence-based
review of an entire feature branch before it is submitted for a pull request.

Evaluate the branch **holistically**, not line by line — logical consistency
across files, regression risk, and whether the feature is actually finished
matter more than any single diff hunk.

**Read-only.** This skill reviews and reports. It never edits, formats, or
fixes the code it is reviewing, never stages, and never commits — that is
[`/commit`](../commit/SKILL.md)'s job, which runs this review first.

> **`/review-branch` and `/review-pr` are not the same thing.** This one
> reviews a branch locally before a PR exists and prints its verdict to the
> terminal. [`/review-pr`](../review-pr/SKILL.md) requires an open PR and
> writes findings to it. Use this one before opening a PR; use that one
> after.

---

## The review framework

Both review skills share one 161-check coverage framework, organized into 12
dimensions, in
[`../review-pr/references/review-checklist.md`](../review-pr/references/review-checklist.md).
**Read it before reviewing** — it is the methodology, not an optional
reference. It defines:

- **[Pass A — Conformance](../review-pr/references/review-checklist.md#pass-a--conformance--requirements--business-logic-checks-115)**,
  Requirements & Business Logic (15 checks) — does the code do what the
  requirements and the project's own docs decided?
- **[Pass B — Correctness](../review-pr/references/review-checklist.md#pass-b--correctness--dimensions-212-checks-16161)**,
  the other 11 dimensions (145 checks) — Architecture & Design, Code Quality &
  Maintainability, API & Contracts, Security, Data & Database, Performance &
  Scalability, Async & Concurrency, Error Handling & Resilience, Testing,
  Observability & Operations, Configuration & Dependencies.
- **[Stack checks](../review-pr/references/review-checklist.md#stack-checks--from-the-projects-claudemd-and-adapter)**
  — the project's own rules, from its `CLAUDE.md` files and `[adapter]` in
  `devtools/factory/factory.toml`. The framework is stack-neutral; this is
  where the project's layering, ORM and migration rules, test conventions and
  architecture tests come in.
- **[How to use this framework](../review-pr/references/review-checklist.md#how-to-use-this-framework)**
  — scope first, evaluate every applicable check, report only with evidence,
  tag every finding New or Pre-existing.
- **[Anti-noise rules](../review-pr/references/review-checklist.md#anti-noise-rules)**
  — what this review must never manufacture, duplicate, or inflate.
- **[Finding format](../review-pr/references/review-checklist.md#finding-format)**
  and **[Severity](../review-pr/references/review-checklist.md#severity)** —
  the fields every finding carries, and the CRITICAL/HIGH/MEDIUM/LOW/INFO
  scale.
- **[Coverage summary](../review-pr/references/review-checklist.md#coverage-summary)**
  — the per-dimension Evaluated/N/A table every review closes with.

**161 checks are the internal coverage framework, not a target finding
count.** A sound branch can legitimately produce zero findings; a rushed one
can produce thirty. The goal is that nothing applicable went unconsidered —
not that the review looks busy.

Two differences from running the framework against an open PR:

- **There is no PR to post to.** Every finding — Pass A and Pass B alike —
  goes straight into the terminal output below; nothing is split into
  "inline" versus "summary."
- **A3's doc cross-checks still apply.** This branch may already carry
  changes to plans, integration guides or generated docs — check whether the
  ones its change invalidated were updated in the same branch, exactly as
  Pass A requires for a PR.

---

## When the branch is a factory run

A change made through the AI factory **already carries an independent
review**: the `factory-reviewer` agent read it with fresh context at the run's
review step, and the referee recorded the verdict. It also carries a **run
file** — `<runs_dir>/R-nnn.md` (`runs_dir` in `devtools/factory/factory.toml`,
normally `docs/factory/runs/`) — with the Intent, the Spec (acceptance
criteria tied to requirement ids), the Design, the Plan, and the evidence:
which tests prove each criterion, proven red before the build and green after.

Find it with `python3 devtools/factory/factory.py status` on the branch, or by
searching `runs_dir` for the branch name. When one exists:

- **Read it first**, before the diff. It is the branch's stated objective and
  its claimed proof.
- **Do not re-litigate what its evidence proves.** A criterion whose tests
  were proven red then green, and which the factory-reviewer passed, is not
  re-argued here. Cite it in the conformance table and move on.
- **Report against the evidence only when you can show it is wrong** — a test
  that could pass for the wrong reason, a criterion that misquotes or narrows
  the requirement, a requirement the Spec never turned into a criterion.
- **Spend the review on what a single run cannot see**: requirements the
  criteria missed, effects on other modules, the project's stack checks, and
  anything committed after the run's review step.
- **The run's own commits are the change** — `git log --first-parent
  --no-merges <base>..HEAD`, with `base` from the run file's Record. A merge
  of the base branch is not the run's work.

Without a run file, review the branch as below with no such evidence to lean
on.

---

## Steps to perform the review

1. Find the base: `base_ref` in `devtools/factory/factory.toml` (or the
   branch the project's `CLAUDE.md` says work branches are cut from). Run
   `git diff <base>...HEAD` to see all changes on this branch.
2. Run `git log <base>..HEAD --oneline` to understand the commit history.
   Compare against a later branch in the promotion chain only when reviewing
   a promotion.
3. Read every new or modified file **in full** to understand context — not
   just the diff hunks. A finding raised from the diff alone is a guess; see
   [Pass B principles](../review-pr/references/review-checklist.md#principles).
4. **Understand** the branch's objective before opening the checklist: read
   the commit messages, the factory run file if there is one, the relevant
   plan in `docs/plans/`, and the requirement section(s) it claims from the
   spec source the project's `CLAUDE.md` names — per
   [Pass A](../review-pr/references/review-checklist.md#pass-a--conformance--requirements--business-logic-checks-115).
   Name requirements by id, `<id_prefix>-<section>-R<n>` (`[requirements]` in
   `factory.toml`).
5. **Trace** the execution paths the branch touches — entry point →
   application logic → domain → persistence → external service — rather than
   reviewing changed lines in isolation. Find the nearest comparable existing
   implementation and hold the branch to that standard.
6. **Evaluate**, per [The review framework](#the-review-framework) above —
   Pass A, then Pass B, then the stack checks — and verify each candidate
   finding per
   [Before a finding is reported](../review-pr/references/review-checklist.md#before-a-finding-is-reported)
   before it becomes one.
7. **Verify** the tests: do they actually prove the required behaviour, or
   just that something runs without throwing? Check negative paths, edge
   cases, authorization, isolation, persistence and concurrency where the
   branch touches any of them — and check the **skip count**, since tests
   that need a database or service can skip silently, and a green run that
   executed nothing looks identical to one that passed.

**Guidelines:**
- Be concise and direct. Do not explain theory.
- Do not propose or write rewrites — flag the defect, recommend the fix,
  leave the implementation to the author.
- Cite the project's own rule (`CLAUDE.md`, the architecture guide, a plan)
  where one applies, rather than a general principle.
- Ignore minor formatting/style issues that don't violate an established
  project convention and don't impact clarity or correctness.

---

## Output format

### Review Summary

Overall assessment in two or three sentences: what the branch does, the
biggest risk if any, and whether it's ready. State the **risk level**
(the highest severity found, or "none") plainly. Name the requirement ids
Pass A checked — the complete ones as well as the gaps, since a clean section
leaves no finding behind. If a factory run file exists, name it and say what
its evidence covered.

### Findings

Ordered **CRITICAL → HIGH → MEDIUM → LOW → INFO**. Each finding carries the
full [finding format](../review-pr/references/review-checklist.md#finding-format):
severity, title, exact file/line, **New or Pre-existing**, what's wrong, why
it matters, the evidence, and a concrete recommendation. Group findings that
share one root cause into a single entry rather than repeating it — see
[Anti-noise rules](../review-pr/references/review-checklist.md#anti-noise-rules).

If there are none: write "None."

### Coverage Summary

The [per-dimension table](../review-pr/references/review-checklist.md#coverage-summary)
— Evaluated (with a finding count or "clean") or N/A, for all 12 dimensions
plus the stack checks. This is a coverage record, not the 161-item list —
never paste the full framework here.

### Positive Findings

Name what was done well, where it's genuinely notable — especially anywhere a
significant risk (isolation, authorization, a transaction boundary, a
migration) was handled correctly. Skip generic praise; skip this section
entirely if there's nothing worth naming.

### Branch Readiness

Answer **only** one of, verbatim — `/commit` matches this string exactly:
- "READY – Safe to submit PR"
- "NOT READY – Fix blockers before submitting PR"

A **blocker** is any open CRITICAL or HIGH finding. A branch with only
MEDIUM/LOW/INFO findings is READY, with those findings still listed above.
