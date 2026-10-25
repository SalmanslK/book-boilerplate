---
name: review-pr
description: >
  Reviews a Bitbucket Cloud or GitHub pull request in two passes and posts the findings back to
  it. Pass A is conformance and runs on every PR — does the code do what the
  requirements and the project's own docs decided, what was specified and never
  built, and which docs did the change invalidate. Pass B is correctness —
  architecture, security, authorization, data, concurrency, error handling, tests —
  followed by the project's own stack checks from its CLAUDE.md. Findings go inline on
  the offending line; the terminal review carries the verdict and a per-requirement
  conformance table. It never approves, merges, declines or edits a PR, never posts
  without confirmation, and never prints or stores the API token. The host is detected
  from the git remote.
---

Review the current pull request and **post the findings to it**.

> **`/review-branch` and `/review-pr` are not the same thing.** `/review-branch`
> reviews a branch locally before a PR exists and prints its verdict to the
> terminal. This one requires an open PR and writes to it. Use `/review-branch`
> before opening; use this one after.

**Arguments**

| | |
|---|---|
| *(none)* | Find the open PR whose source branch is the current branch |
| `pr=<id>` | Review that PR by number |
| `dry-run` | Do everything except post. Prints exactly what would be posted |

**Host.** Bitbucket Cloud or GitHub, detected from `git remote get-url origin`. On
`github.com` use [`references/github-api.md`](references/github-api.md) (the `gh` CLI holds the credential;
skip the Credentials section below). On `bitbucket.org` follow this file. For any other host, say so and stop. Run
`/review-branch` instead, which needs no host.

---

## HARD LIMITS

**1. Never post without explicit confirmation.** Comments notify the whole
reviewer list, are attributed to the token's owner, and cannot be tidied away
cleanly on a large diff. Present the findings, show the posting plan, and wait
for the user to say yes. `dry-run` is the only mode that skips the question,
because it skips the posting.

**2. Never approve, merge, decline, request changes on, or edit the PR.** This
skill posts comments and nothing else. `POST .../approve`, `.../request-changes`,
`.../merge`, `.../decline` and any `PUT` to the pull request are out of scope —
refuse them even if asked mid-run, and say that merging is the reviewer's
decision, not the tool's. In a factory run the merge gate is a person's approval
on the PR; this skill never stands in for it.

**3. Never touch another person's comment.** No editing, no deleting, no
resolving. Reply if there is something to add.

**Your own are different, and the line runs through *substance*, not ownership.**

- **Deleting** one this skill posted: only when the user asks for it withdrawn.
  Never on your own initiative, never silently, and it leaves a tombstone.
- **Editing** one: **only when the finding itself is unchanged** — stripping a
  rendering artifact, fixing a typo, correcting a line reference. This is the right
  tool for a cosmetic fix: no tombstone, no second notification, and any replies
  stay attached.
- **Never edit to change what a finding says.** Bitbucket shows no edit history, so
  a reviewer who already replied would be answering something that no longer exists.
  Withdraw and repost, and say in the report that you did.

**4. Never print, echo, log, or write the API token.** Not into a file, not into
a commit, not into a comment body, not into the terminal, not even partially
masked. If it is missing, say so and stop — do not ask the user to paste it into
the conversation, because anything pasted into a transcript is compromised and
has to be revoked. See [Credentials](#credentials).

**5. Do not post a finding you have not verified in the code.** A false positive
on a PR costs a reviewer more than a missed finding, because it is public and
someone has to argue it down. If a concern survives only as a suspicion, put it in
the terminal review as a question, never onto the PR as a finding.

**6. Never edit the source under review.** This skill reviews and posts
comments — it never touches the code itself, and never stages, commits or pushes.
A finding is something to report, not something to fix from here.

---

## Credentials

> **GitHub:** none of this section applies. `gh` holds the credential; see
> [`references/github-api.md`](references/github-api.md). The rest is for Bitbucket.

**Take them from git. Do not ask for them, and do not put them in the environment.**

| | Where it comes from |
|---|---|
| Token | The **credential already in the store**, via `git credential fill` — the same value git pushes with |
| Scheme | **Bearer** (`Authorization: Bearer <token>`, no username) — the exception is an Atlassian API token, below |

```bash
REPO=$(git remote get-url origin | sed -E 's#.*bitbucket\.org[:/]##; s#\.git$##')
TOK=$(printf 'protocol=https\nhost=bitbucket.org\npath=%s.git\n\n' "$REPO" \
  | GCM_INTERACTIVE=never git credential fill 2>/dev/null \
  | sed -n 's/^password=//p')
```

- **`GCM_INTERACTIVE=never`** is not optional. Without it Git Credential Manager
  opens a GUI sign-in prompt, and a non-interactive session hangs on it.
- **The `path=` line** is not optional either. A store configured per path answers a
  bare host query with nothing, or with another repository's credential.
- **An Atlassian API token** (it starts with `ATATT`) is the one credential that is
  not Bearer: it needs Basic, with the account **email** as the user
  (`git config user.email`). Test the prefix in the shell without printing the
  token.

**`$TOK` stays inside the one shell invocation that uses it** — never echoed, never
assigned to a variable that outlives the command, never written to a file, and
never passed as a command-line argument (use `curl --config -`, see
[`references/bitbucket-api.md`](references/bitbucket-api.md#authentication-without-leaking-the-token)).

**Override only if git has nothing:** `BITBUCKET_API_TOKEN` (and `BITBUCKET_EMAIL`
for an `ATATT` token) are read as a fallback. If neither source yields a
credential, name both routes and stop.

**Never accept a token pasted into the conversation.** A transcript is a leak, and a
token that reaches one has to be revoked. If someone offers one, decline and point
at `git credential fill`.

**Verify the credential before reviewing**, with a read-only call — a `200` from
`GET /repositories/$REPO` costs one request and saves discovering at Step 5 that
the token cannot write. If it answers `401`, try the other scheme once before
concluding the token is bad.

---

## Process

### Step 1 — Resolve the PR

Read the repository from `git remote get-url origin` rather than assuming it.

With `pr=<id>`, fetch that PR. Without it, find the open PR whose source branch is
the current branch.

**Stop if there is no open PR.** Do not create one — opening a PR is the author's
decision. Say which branch was searched, and suggest `/review-branch` instead, which
needs no PR.

**Stop if more than one matches.** List them and ask which.

Record the PR's **source and destination branches**, and its **head commit**. The
head commit matters: line numbers in inline comments refer to the file as the PR
currently stands, so a PR that moves under you invalidates them.

**Read the PR's existing comments now**, before reviewing — see
[`references/bitbucket-api.md`](references/bitbucket-api.md#read-existing-comments--always-before-posting).
Someone may already have reviewed it, and an earlier run of this skill may already
have posted. What is there shapes the posting plan.

### Step 2 — Establish context

Before reading the diff:

- Read the PR title and description for the intended purpose.
- Identify the affected modules and features.
- Read the plan in `docs/plans/` for each affected feature, and the relevant part
  of the architecture guide the project's `CLAUDE.md` links.
- **Look for a factory run.** A change made through the factory has a run file in
  `runs_dir` (`devtools/factory/factory.toml`, normally
  `docs/factory/runs/R-nnn.md`) — the PR description written by `factory pr-body`
  names it, or search `runs_dir` for the source branch. Read it: Intent, Spec
  (acceptance criteria tied to requirement ids), Design, Plan, the evidence for
  each criterion, and the independent review by the `factory-reviewer` agent. **That
  review already happened** — this one is not a re-run of it. Use the run's
  evidence, do not re-litigate what it proves (see
  [Anti-noise rules](references/review-checklist.md#anti-noise-rules)), and spend
  the review on what it cannot see: requirements the criteria missed or narrowed,
  cross-module effects, and the project's stack checks.
- Find a comparable existing implementation to compare against — the nearest
  sibling feature is usually the standard the PR should be held to.

If the description is thin, infer the purpose from the code rather than asking.

### Step 3 — Review, in two passes

```bash
git fetch origin
git diff origin/<destination>...origin/<source> --stat
```

**Two passes, and they run in this order, because they ask opposite questions.**

| | Direction | Asks |
|---|---|---|
| **A · Conformance** | requirements → code | Does the code do what was decided? **What is missing?** |
| **B · Correctness** | code → itself | Is what is there right? **What is wrong?** |

**Pass B cannot find what Pass A finds.** Reading a diff tells you whether the code
in front of you is correct; it can never tell you about the requirement nobody
implemented, because an omission leaves nothing to read. A review that runs only
Pass B will pass a feature that is half-built and say it looks good — which is the
single most expensive way for this review to fail, since the gap then surfaces in
acceptance testing as a defect against a specification the team agreed to.

**So Pass A runs first, and it is not optional.** Even on a small diff. It is
cheaper than Pass B and it frames everything Pass B then looks at.

#### Pass A — Conformance

Three questions, all of them mandatory. The full method — and the complete
Requirements & Business Logic check set (1–15) — is in
[`references/review-checklist.md`](references/review-checklist.md#pass-a--conformance--requirements--business-logic-checks-115).

1. **Requirements → code.** For every feature the PR touches, read its section of
   the spec source (the project's `CLAUDE.md` names it, and which revision is
   authoritative), list what it specifies, and find each item in the diff. **What
   is absent is a finding.** Check the global requirements too — a PR can satisfy
   its own section perfectly and still miss a rule that binds every module.
   Name requirements by id, `<id_prefix>-<section>-R<n>` (`[requirements]` in
   `factory.toml`).
2. **Code → our docs.** Does each doc that owns an affected topic still describe the
   code truthfully? Where they disagree, decide which is the intended design; where
   the requirements are one side, **the requirements win and the code is the
   finding**.
3. **Change → doc updates.** Did the PR update the docs its own change invalidated,
   *in this PR*? The rule: *change behaviour, fix the doc that owns it in the same
   commit.* An integration guide that now lies is a finding, not a follow-up.

**Record the verdict per requirement or section, including the ones that are
complete.** It goes in the terminal review — see [Comment format](#comment-format).
A review that says nothing about conformance is indistinguishable from one that
never checked, and the next reader cannot tell which they are holding.

#### Pass B — Correctness

Work through the 11 remaining dimensions of the review framework in
[`references/review-checklist.md`](references/review-checklist.md#pass-b--correctness--dimensions-212-checks-16161) —
Architecture & Design, Code Quality & Maintainability, API & Contracts,
Security, Data & Database, Performance & Scalability, Async & Concurrency,
Error Handling & Resilience, Testing, Observability & Operations, and
Configuration & Dependencies. **That file is the coverage framework, not a
checklist to paste anywhere** — evaluate every dimension the diff can
plausibly touch, mark the rest N/A, and report only what has evidence. See
[Anti-noise rules](references/review-checklist.md#anti-noise-rules).

**Then apply the project's stack checks**, per
[Stack checks](references/review-checklist.md#stack-checks--from-the-projects-claudemd-and-adapter):
the rules in the root `CLAUDE.md`, the nested `CLAUDE.md` of each folder the diff
touches, and `[adapter]` in `factory.toml`. The framework names engineering
failures; the project's own rules — its layering, ORM and migration rules, test
conventions — come from the project, never from memory of another stack.

**Review every changed file.** Do not stop at the first finding, and do not review
only the largest diff — the one-line change to a configuration file is exactly
where an authorization default flips.

**Tag every finding New or Pre-existing** — whether the diff created the
defect or merely touches, extends, or newly exposes one that was already
there. See
[How to use this framework, §4](references/review-checklist.md#how-to-use-this-framework).

Severity is assigned per
[`references/review-checklist.md`](references/review-checklist.md#severity) —
CRITICAL / HIGH / MEDIUM / LOW / INFO.

**Verify each finding before it becomes a finding.** Is the path reachable? Is it
already handled by middleware, a global filter, an interceptor, or an architecture
test? Is it intentional per a plan, a *Resolved* entry in `docs/open-questions.md`,
or a recorded trade-off in the architecture guide? Does a factory run's evidence
already settle it? Search for the answer rather than guessing — reporting a recorded
decision as a defect wastes a reviewer's time and costs the review its credibility.

### Step 4 — Show the plan and ask

Print the full review to the terminal first, then the posting plan:

```
Posting plan — PR #NN (feature/x → dev), head a1b2c3d

  Already on the PR: 3 comments (2 by people, 1 from an earlier run of this skill)

  Inline (7):
    🔴 src/…/create_order.py:106   Confirmation never sent outside development
    🔴 src/…/create_order.py:97    Two writes outside a transaction
    …
  Terminal only — no honest anchor (1): a finding spanning files
  Skipped as already posted (2)

Post these to PR #NN? Nothing is written until you say yes.
```

**Wait for a yes.** "Looks good" about the *review* is not consent to *post* — ask
again if it is ambiguous.

**Every defect you know about appears in this plan, whatever its provenance.** A
finding you discussed with the user, raised in an earlier run, or read in a plan
still needs a severity, an anchor and a row here — see
[what already counts as a finding](references/review-checklist.md#before-either-pass--what-already-counts-as-a-finding).
The plan is the only place a reader can see the complete set, so anything absent
from it is a defect nobody has been told about. **Never write "already reported" in
a review; ask "reported to whom?"** — the audience here is the PR's author, who has
read none of your conversation.

### Step 5 — Post

**Re-read the PR's comments immediately before posting** — someone may have
commented since Step 1 — and rebuild the dedupe key from what is there now.

**Inline comments only. Do not post a summary comment.** The findings carry the
review, and a summary alongside them restates on a thread what the reader is
about to read on the lines themselves.

**So every finding needs a line.** A finding with no obvious location is not
exempt — it is a finding whose anchor has not been thought about hard enough, and
there is nearly always an honest one:

| Looks location-less | Actually belongs on |
|---|---|
| "this logic has no test" | the logic, not the missing test file |
| "nothing runs this seed script" | the script |
| "a specified requirement is missing" | the handler, endpoint or request shape that should carry it |

Pin it where the author would make the change. Only when that genuinely does not
exist does the finding stay in the terminal review — and then say so out loud in
the report, rather than letting it disappear.

**The verdict, the counts and the conformance table go to the terminal**, not to
the PR. They are how the reviewer judges the review; the PR gets the work.

**One comment per finding.** Do not batch several findings into one inline comment;
they get resolved as a unit and the unaddressed ones disappear.

**INFO findings are never posted.** They stay in the terminal review.

**Put nothing machine-readable in the body.** No hidden marker, no tracking id, no
tool signature, no AI attribution. **Bitbucket escapes HTML comments rather than
stripping them**, so `<!-- anything -->` renders as visible text at the top of the
comment — it looks like a leaked template, and it makes a careful review read as
generated output.

> **Check `content.html`, not `content.raw`, before trusting how anything renders.**
> The API returns both, and the difference is invisible until a person opens the
> page.

**Dedupe on position instead: `inline.path` + `inline.to`.** A comment already on
that line is this skill's work from an earlier run, or a person's — skip the finding
and say so in the report. That key needs nothing added to the body, cannot leak into
the rendered page, and survives a reworded finding.

**Count only top-level comments — those with no `parent`.** Bitbucket gives a reply
the *same* `inline` block as the thread it answers, so a reply makes its line look
occupied and would suppress a genuinely new finding there. Filter replies out before
building the key, and do not report an inherited position as a duplicate.

**Two findings on one line is the case to handle at plan time, not post time.**
Either merge them into one comment or move one to the line it really belongs on. Do
not defeat the dedupe key to fit both.

Inline comments go on the **line in the new file**. If a finding is about deleted
code, or spans files, or genuinely has no honest anchor, it stays in the terminal
review — an inline comment pinned to an arbitrary line reads as a mistake.

**If a post fails, stop and report.** Do not retry blindly: a 401 or 403 means the
token lacks `pull request: write`, and hammering it can lock the account. A "failed"
post may still have landed — re-list the comments before any retry. Report what was
posted before the failure; the position key makes a re-run resume rather than
duplicate.

**Re-read the comments afterwards and reconcile against the plan**, rather than
trusting what the posting loop said it did. A loop can skip an entry without
erroring — see the `while read` trap in
[`references/bitbucket-api.md`](references/bitbucket-api.md#post-a-comment) — and
the one it drops is the last, which is the easiest to miss. Compare the `path:line`
pairs actually on the PR against the plan, and post whatever is absent.

### Step 6 — Report

State plainly: the PR, how many comments were posted, how many were skipped as
duplicates, and the verdict. Include the
[coverage summary](references/review-checklist.md#coverage-summary) and the
conformance table. Give the PR URL.

The verdict is one of, verbatim:

- **READY** — no open CRITICAL or HIGH finding.
- **NOT READY** — at least one open CRITICAL or HIGH finding (a blocker).

MEDIUM, LOW and INFO findings do not block, and are still listed.

**Never claim to have posted something that failed**, and never report a verdict
that differs from the one the findings support. The verdict is advice to the
reviewer; it is not an approval, and it is never posted as one.

---

## Comment format

Inline comments are read in a narrow column beside the code. Keep them short —
the reasoning belongs in the terminal review, and the comment carries what the
author needs to act.

```markdown
**🔴 CRITICAL — two writes outside a transaction — New**

The first save commits the order on its own. If the payment record or the second
save fails, the order is committed with nothing to pay it — and, given the
duplicate guard above, no way to retry. The comment claiming both are committed
together is the dangerous part.

**Fix:** put both saves in one transaction (CLAUDE.md → *Behaviour*: *"Two saves
that must succeed together…"*). The block must be safe to repeat if the project's
transaction helper retries it.
```

Five elements, always: **severity, title, and New/Pre-existing tag**, **what
is wrong**, **why it matters**, **the evidence** (folded into the same
paragraph — the code beside the comment usually *is* the evidence), **what to
do**. Cite the project's own rule where one applies — the author is far more
likely to act on `CLAUDE.md` than on a general principle. Full field
definitions: [Finding format](references/review-checklist.md#finding-format).

**None of the summary goes to the PR — see [Step 5](#step-5--post): inline
comments only, no summary comment.** The terminal review carries the verdict, the
severity counts, the [coverage summary](references/review-checklist.md#coverage-summary),
the findings that have no single location, and a one-line index of the inline
ones so the reviewer can see the whole shape without hunting through the diff.

**It also carries the conformance table from Pass A — always, including the rows
that pass.** This is the part a reader cannot reconstruct from the inline comments,
because a satisfied requirement leaves no comment behind:

```markdown
### Requirements conformance

| Requirement | What it specifies | Status |
|---|---|---|
| REQ-2.4-R1, R2 | Date and time formats, options and defaults | ✅ complete |
| REQ-2.4-R3 | Preferences screen behaviour | — front end |
| REQ-2.2-R6 | Page size saved per user per screen | ❌ **not built** — see below |
| REQ-2.2-R8 | Created/updated by and date on every record | ✅ complete — run R-012's criteria AC-3, AC-4 |
```

**A green row is worth as much as a red one.** "Section 2.4 checked, complete" tells
the next reader the section was examined; silence tells them nothing, and they
cannot tell a clean section from one nobody opened.

---

## Tone

The author is a colleague, and the comment outlives the conversation.

- Describe the defect, never the person. "This commits before the payment is
  recorded", not "you forgot".
- Say what is right as well as what is wrong. A review of a sound PR that lists
  only faults misrepresents it — the terminal review should name what was done well
  where it is genuinely notable, and skip generic praise entirely.
- Do not inflate severity to be heard. A Medium reported as Critical costs the next
  Critical its urgency.
- Where the requirements or the architecture are ambiguous, say so and ask. Do not
  invent a requirement to justify a finding.

**The point is the maximum number of real defects with the minimum number of false
positives — not the maximum number of comments.**
