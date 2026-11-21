---
name: check-guides
description: >
  Use when asked whether the FE integration guides are current — "are the FE guides up to date", "the
  guide is wrong", before a QA build or /publish-guides. Checks every guide against
  the contracts, the source and the ledgers; reports each CURRENT or STALE with evidence, then stops for
  approval. Not for code against the FSD (/book:fact-check) or other docs (/book:check-docs).
allowed-tools: Read, Grep, Glob, Bash(git show*), Bash(git log*), Bash(git grep*), Bash(git ls-tree*), Bash(git rev-list*), Bash(git rev-parse*), Bash(git diff*), Bash(git fetch*), Bash(python .claude/skills/check-guides/scripts/guides_check.py*), Bash(python3 .claude/skills/check-guides/scripts/guides_check.py*), Bash(python devtools/checks/gen-api-contracts.py --check*), Bash(python3 devtools/checks/gen-api-contracts.py --check*)
---

You are checking **the front-end integration guides**: the documents the FE team builds against and
cannot check themselves, because they do not read this repository.

The bar is the one `docs/fe-integration-guides/api-conventions.md` sets under *Keeping these guides
honest*: **if a guide disagrees with the API, the API is right and the guide is stale.** A guide that is
wrong is worse than none, because the FE team trusts it. The job is to find where a guide no longer tells
the truth, prove it, and say so.

**Your first deliverable is a report, not an edit.**

---

## What is compared with what

| The guide says | Checked against | Owner of the truth |
|---|---|---|
| Routes, permissions, request fields | `docs/api/api-contracts.md` (generated from the source) | the code |
| Response fields and example keys | the contracts' *Response schemas*, then the source's identifiers | the code |
| Error codes, limits, defaults | the source | the code |
| Behaviour and rules | *Resolved* in `docs/open-questions.md`, then the feature's plan | the FSD, through those |
| What every module does the same way | `docs/fe-integration-guides/api-conventions.md` | that file |
| A defect or question it cites | `docs/defects.md`, `docs/open-questions.md` and their archives | those files |
| What the front end asked for | `docs/api/fe-api-coverage.md` | the FE contract in `requirements/` |

**Behaviour against the FSD is not this skill's.** It means reading the specification, and `/book:fact-check`
owns that. A guide that agrees with the code but maybe not with the FSD is listed for `/book:fact-check`, never
"fixed" here.

**Read from `origin/dev`** (`git fetch origin dev` first), never the working tree: the guides are
published from what has merged, and a dirty checkout hides drift. State the commit in the report. In Git
Bash on Windows, `git show origin/dev:<path>` needs `MSYS_NO_PATHCONV=1` in front, or the `:` path is
rewritten and git says *unknown revision*; the script calls git directly and is unaffected.

`requirements/` is read-only evidence. A guide is never edited to match the FSD where the code differs:
that is a code finding (a `defects.md` row, or `/book:fact-check`), not a guide fix.

---

## Step 1 — The mechanical lists

```
python .claude/skills/check-guides/scripts/guides_check.py [--ref origin/dev] [--json]
```

Also run `python devtools/checks/gen-api-contracts.py --check` where the stack has a generator: a stale
`api-contracts.md` makes every route comparison wrong.

The script prints, per guide, **candidates, never verdicts**:

| Kind | Meaning | Usually |
|---|---|---|
| `R-STALE` | a route in the guide's endpoint table that the contracts do not hold | removed or renamed: **stale** |
| `R-MENTION` | a route named in prose or an example that the contracts do not hold | stale, or the generator's gap (`C-UNPARSED`) |
| `R-MISSING` | a route this guide owns that no guide documents | **missing** |
| `R-ELSEWHERE` | a route this guide owns, documented only by another guide | usually fine: confirm the link |
| `R-PERM` | the table's permission or *anonymous* differs from the contracts | **incorrect**, and Critical: the app shows or hides a screen on it |
| `R-STATUS` | a status code the table returns that the route does not declare (401 and 403 hold on every protected route) | incorrect |
| `F-MISSING` | a request field of a route the guide documents that it never names (nor api-conventions does) | missing |
| `F-UNKNOWN` | a key in an example that no field, parameter, convention or source identifier spells | a stale example |
| `E-UNKNOWN` | an error code the guide names that the source never returns | stale |
| `ID-OPEN` | **an open question shown as settled**: cited with nothing near it saying it is open | stale: the FE may build on a rule that can still change |
| `ID-STALE` | a cited D or Q id whose own words call it open when it is closed, or the reverse | stale |
| `ID-UNKNOWN` | a cited id no ledger holds (a link to a ledger, or past the last id taken) | incorrect |
| `AGE` | commits on the guide's sources since the guide was last edited | code it has not seen: read them |
| `AGE-LINE` | the guide was edited after its *Last checked* commit, but the line did not move | a stale line |
| `T-TABLE` | a module guide without the template's `\| Method \| Path \| Permission \| Returns \|` table | Low: permissions there cannot be checked |

And once, for all guides:

- **Routes no guide documents.**
- **`C-UNPARSED`**: a route registration in a source file no contracts row names. Every route check is
  blind to it. `not checked` means `[fe_guides] route_calls` is unset for this stack: say so.
- **`E-UNNAMED`**: error codes the source returns that no guide names, less `[fe_guides]
  internal_error_codes` in `devtools/factory/factory.toml` (codes no route can return, each with a reason).
  A stale entry there (it matches no code) is listed too.
- **Response fields:** whether the contracts carry *Response schemas*. Without them, sample the response
  shapes in Step 2 and say they were sampled, not checked.
- **Coverage** against `fe-api-coverage.md`: **not applicable** until the FE sends a contract. Report it
  that way, never as *passed*.

**Exit 2 means it could not check** (a missing file or ledger heading, a malformed setting, `source_files`
that match nothing, a git failure). Settings are read from `factory.toml` at the same ref as the guides. Stop and say
so; never report a guide CURRENT from a run that did not finish.

## Step 2 — Read what it flagged, and the behaviour

Read **only** what the script flagged, plus each guide's behaviour sections, at the cheapest level that
settles them:

1. **Each flag:** open both sides at the cited lines. Drop a false one and say why in one line.
2. **`AGE`:** `git log <last edit>..origin/dev -- <its sources>` (the JSON's `age.sources`), then each
   commit's diff. A comment, a test or an unreachable path is no finding; a changed rule, field, code or
   limit is.
3. **`ID-OPEN`:** read the question's recommendation and what is built. If the guide describes what is
   built and the recommendation would change it, the guide needs *"open with the BA — may change"*.
4. **Behaviour, sampled:** every number the guide states (limits, sizes, lifetimes, defaults, counts) and
   every default it promises (an omitted filter, sort or page size): `git grep` the constant. These go
   stale silently and cost nothing to check.
5. **Rules** a guide states as decided: is there a *Resolved* entry or a plan decision? A rule found only
   in a chat or the archive is not in force.
6. **`E-UNNAMED`:** for each code, can a client receive it? One a route returns belongs in a guide
   (missing); one no route can return goes into `[fe_guides] internal_error_codes` with its reason,
   proposed in the report.

## Step 3 — Report, then stop

**1. Summary:** the commit checked; guides checked; each guide **CURRENT** (no finding beyond a *Last
checked* move) or **STALE**; findings by kind and by risk; what could not be checked (response fields
sampled, coverage not applicable, `C-UNPARSED` not configured).

**2. Per guide**, findings grouped as:

- **Stale:** was true, no longer is (a removed route, an old field, a fixed defect shown open, **an open
  question shown as settled**, an old *Last checked* line).
- **Incorrect:** never true, or contradicts its owner (a wrong permission, a wrong limit).
- **Missing:** true and needed, not there (a route, a field, an error code the FE will receive).

Each with **evidence on both sides (file:line)**, the fix in one sentence, and a risk:

| Risk | What |
|---|---|
| **Critical** | a wrong permission or auth, a route that does not exist, a request the server refuses |
| **High** | a wrong rule, error code, limit or response shape the FE branches on |
| **Medium** | a missing caveat, an undocumented route or field, an example that no longer parses |
| **Low** | a *Last checked* line, the table's shape, wording, a link |

**3. Findings that are not the guides'**: a generator gap (`C-UNPARSED`), code that disagrees with a
decision, a contracts file that claims more than it holds. Each named for its owner (a `defects.md` row,
`/book:check-docs`, `/book:fact-check`), never fixed here.

**Then stop.** A *Last checked* move on a guide that is otherwise CURRENT may be listed as "proposed,
trivial", and is still applied only on approval.

## Step 4 — Apply what is approved

- **Where:** a guide fix the work caused stays on the work's branch; a general refresh goes on a
  `chore/docs-<slug>` branch cut from the base with `--no-track`, holding nothing else (the rule is in
  `docs/CLAUDE.md`, *Which branch a doc change goes on*). Check the branch before the first edit.
- **What:** describe only what the code does; never write intent as behaviour. A new guide starts from
  `docs/fe-integration-guides/_template.md`. Move each fixed guide's `**Last checked against the code:**
  YYYY-MM-DD (<commit>)` to the commit you checked against.
- **A route, request or permission changed in the guide** but not in the code means you are documenting
  something the contracts do not show. Stop: that is a code change, not a guide fix.
- **Verify:** `python devtools/checks/check-book.py`, `python devtools/checks/check-fe-guides.py`, and the
  script again: every approved finding gone, nothing new.
- **A confirmed defect** (the code is wrong, not the guide) gets its `defects.md` row the day it is found.

## What next

- Guides changed and merged: **`/publish-guides`**. The FE team reads Drive, not the repository.
- A guide agrees with the code, but the code may not agree with the FSD: **`/book:fact-check <module>`**.
- A requirement looks moved: **`/book:check-sources`**.
