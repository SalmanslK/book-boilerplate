# Book Boilerplate — Functional Specification, V2

## 1. Purpose

The Book Boilerplate gives any software project the Book: the documents that say what is built, why, how
and what is done; the checks that keep them true; the skills and agents Claude Code works by; and a guard
that refuses, inside a session, what the rules forbid. It is stack-free and names no project.

## 2. Configuration

Every project-specific value — names, branches, protected paths, commands, owners, tracker — lives in
`devtools/factory/factory.toml`. A tool reads it from there and carries no project's name, path or key.
An empty value turns a feature off, and the tool reports it `N/A`, never `OK`.

## 3. Skills

The Book carries the skills for committing, reviewing a branch and a pull request, change impact,
requirements check and sync, traceability audit, project progress, and the four documentation skills.
Each loads (valid frontmatter, a description within the limit) and ends by saying what comes next.

## 4. Setting a Book up

- `factory book init` creates only what is absent from one parameter file, never guessing an answer and
  never overwriting a file. It works for a new repository and an existing one (the hooks last).
- A stack (`none`, `dotnet`, `pytest`) and optional packs (`deploy`, `bitbucket`, `fe-guides`) add the
  files only they need.
- `factory book verify` reports each element `OK`, `MISSING`, `WRONG`, `TO FILL`, `N/A` or
  `COULD NOT CHECK`, runs the existing checks and the self-tests, and ends READY, READY WITH GAPS or
  NOT READY. A check that could not run never passes.

## 5. The boilerplate's own Book

The boilerplate is itself a Book that `book verify` reports READY, with no other project's names left,
every enforced rule intact, and its own self-tests passing with nothing skipped.

## 6. Agents

Two project-level agents: a read-only Book reviewer, and a tester that runs and writes the self-tests.
