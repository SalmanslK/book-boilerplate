---
name: change-impact
description: >
  Use when someone asks what a change reaches — "what does this branch touch", "what
  do I need to test or update for this", "what does this requirement change land on",
  "which modules, tests, guides and docs does D<n> affect" — before a plan, before a
  PR, or when a new revision of the requirements moves a section. Lists the modules,
  the requirement ids, the tests to run, the guides, plans and generated docs to
  update, and the risk tier, from the diff or from a requirement. Read-only; in a
  factory run it fills the run's Context section. Not a review and not a traceability
  audit.
allowed-tools: Read, Grep, Glob, Bash(python3 devtools/factory/factory.py impact*), Bash(python devtools/factory/factory.py impact*), Bash(git diff*), Bash(git log*), Bash(git status*)
---

# Impact analyzer

Answers one question: **what does this change reach?** The deterministic part is
`factory impact`; this skill adds what a path map cannot see. It writes nothing
except, inside a factory run, the run's Context section — and that only through
`factory impact --write`.

## Input — one of

- **The current branch** (default): everything that differs from the base
  (`base_ref` in `devtools/factory/factory.toml`), committed or not.
- **Paths** the change will touch, before any code exists — the Intent's *Touches*.
- **A requirement or section**: find it in the traceability file `[requirements]`
  names, take the code paths its rows cite, and analyse those paths. A requirement
  with no rows is unbuilt — say so, and name the plan in `docs/plans/` that would own it.

## Steps

1. **Run the map.** `python3 devtools/factory/factory.py impact [paths…]`. It gives
   the modules (`[[modules]]` in `factory.toml`), each module's test filter, guide and
   plan, the cross-cutting paths no module owns, the requirement ids the traceability
   file ties to the changed files, and the generated docs to regenerate.
2. **Add what the map cannot see** — each only if the change reaches it:
   - **Callers in other modules.** A shared type, function or handler another module
     calls into: grep for its name across the source tree. Each caller's module joins
     the list, and its tests run too.
   - **The public contract.** An endpoint, request or response shape, command-line
     option, permission or error message changed: the module's guide, any
     project-wide conventions guide, and generated contract docs.
   - **Permissions.** Where the project defines who may do what (its CLAUDE.md says).
   - **Schema.** A migration or data change: the generated schema doc, and seed or
     fix scripts that assume the old shape.
   - **QA.** The requirement ids' cases in the project's QA suite, if it keeps one.
   - **Open items.** Open rows in the defect log and `docs/open-questions.md` naming
     the same module or ids.
3. **Name the tier** the way the referee computes it (`[tier]` in `factory.toml`): the
   highest any touched path reaches. Say which path set it.

## Output — this shape, always

```
Impact — <branch or paths or requirement>
Tier: <Low|Medium|High> — <the path that set it>

Modules: <name> (tests: <filter>) …
Cross-cutting: <paths>, so the whole integration suite runs | none
Requirement ids: <id> …
Callers outside these modules: <module — type — why> | none found

Run:    <the test commands, with filters>
Update: <each doc or guide, and why>
Regenerate: <generated docs>
Open items touching it: <defect/question ids> | none
Unknown: <what could not be determined, and how to find out>
```

Say **"none"** rather than leaving a line out — an absent line reads as not checked.

## Inside a factory run

With an open run on the branch, run `python3 devtools/factory/factory.py impact --write`
first — it fills the run's Context section — then add step 2's findings to that
section below its output. Never edit the run's Record; the guard refuses it.
