---
name: release-notes
description: >
  Use when a QA build needs its notes — "write the release notes for build 12", "what goes to QA in
  this build". Drafts docs/release-notes/qa-build-<n>.md from what merged since the last QA build:
  features, fixed defects, known gaps, FE changes and what to test; writes it on approval. Not for one
  person's summary (/book:write-for).
allowed-tools: Read, Grep, Glob, Write(docs/release-notes/**), Edit(docs/release-notes/**), Bash(git log*), Bash(git show*), Bash(git diff*), Bash(git tag*), Bash(git ls-files*), Bash(git fetch*), Bash(git rev-parse*), Bash(git branch*), Bash(python devtools/checks/check-book.py*), Bash(python3 devtools/checks/check-book.py*)
---

You are writing **one QA build's release notes**: a dated snapshot of what that build delivered, for QA
and the front-end team. It is a record, like the archive: **once merged it is never edited.** A later
correction goes in the next build's notes.

**Your first deliverable is a draft, not a file.**

---

## Step 1 — Fix the range

The build is `<n>`, given by the person (`/release-notes 12`). The range is **the last QA build → the
commit this build deploys**:

- **The last QA build:** the commit the previous `docs/release-notes/qa-build-<n-1>.md` names in its
  header; else a tag or deploy record for that build (`git tag --list "qa-*"`, the deploy log the
  deploy skill writes, if this Book has one). **None found: ask.** Never guess a start.
- **This build:** the commit the person names, else `origin/<base>` after `git fetch`. State both
  commits in the header.
- No earlier notes at all: this is the first build. Say so, and ask whether to cover everything merged
  or a start the person names.

## Step 2 — Gather, from the owners only

| Section | Read from |
|---|---|
| **Features** | `docs/project.md` Part 1 rows and each plan's Tasks whose status moved in the range (`git log -p <from>..<to> -- docs/project.md docs/plans/`) |
| **Fixed defects** | ids that moved to *Fixed* in `docs/defects.md` (or into its archive) in the range |
| **Known gaps** | defects still open, and questions still open in `docs/open-questions.md`, that touch a feature in this build |
| **Changes for the front end** | `git diff <from>..<to> -- docs/api/api-contracts.md` (routes, fields, permissions added, changed or removed) and the guides in `docs/fe-integration-guides/` that changed |
| **What QA should test** | each feature's and fix's acceptance criteria or test cases from its plan, and `docs/qa-artifact-test-suite.md` rows if the Book has one |

Merge commits and their pull request titles help you find things, but **a feature or fix is listed only
when its owner document says so.** A commit message is not a status.

## Step 3 — The draft

```
# QA build <n> — <date>

**Build:** <to commit> · **Since build <n-1>:** <from commit> · **Base:** <branch>

## What is new
## What is fixed
## Known gaps
## Changes for the front end
## What to test
```

- Plain words a QA engineer and the FE team read without the repository: say what each id is (`D12 —
  a saved filter was lost on refresh`), never a bare id or file path.
- Each line names its evidence (the plan, the defect row, the contracts diff) in the draft you show;
  keep only the reader-facing text in the file.
- A section with nothing in it says *None in this build*. Never leave one out.
- Nothing secret or personal: no credentials, hosts, customer data or people's emails.

Show the draft and **stop for approval**.

## Step 4 — Write it, on approval

- **Where:** a `chore/docs-release-notes-<n>` branch cut from the base with `--no-track`, holding only
  this file (the rule is in `docs/CLAUDE.md`, *Which branch a doc change goes on*). Check the branch
  before writing.
- **What:** `docs/release-notes/qa-build-<n>.md`, exactly the approved draft. A file for that build
  already merged is never rewritten: say so and stop.
- **Verify:** `python devtools/checks/check-book.py`.

## What next

- The FE guides changed in this build: **`/publish-guides`**, so the Drive copy matches.
- A short summary for a PM or the client: **`/book:write-for <reader>`**.
- A gap found while writing that has no defect or question yet: record it the day it is found.
- **In a flow** (`/book:flow`): `qa-build` → `/deploy`.
