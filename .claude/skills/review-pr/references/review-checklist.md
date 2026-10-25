# Review checklist

What to look for, and how hard to press. Owned by
[`../SKILL.md`](../SKILL.md) — **add a new concern here, not to either skill
body.** Shared with [`/review-branch`](../../review-branch/SKILL.md), which runs
this same framework locally against a branch instead of an open PR.

This file is the **internal coverage framework**: 161 named checks across 12
review dimensions, plus a section of [stack checks](#stack-checks--from-the-projects-claudemd-and-adapter)
that come from the project, not from this file. It is not a form to fill in
and not a source of findings by itself — **a check earns a finding only when
the diff gives evidence for it.** Work through every applicable dimension on
every review; report only what the evidence supports.

**This file is stack-neutral on purpose.** It names engineering failures, not
frameworks. The project's own rules — its layering, its ORM and migration
rules, its test conventions, its architecture tests — live in its `CLAUDE.md`
files and its architecture guide, and they are applied through
[Stack checks](#stack-checks--from-the-projects-claudemd-and-adapter). Where a
check below and a project rule collide, **the project rule wins**.

---

## How to use this framework

**1. Scope first.** Read the diff's shape — which modules, which layers,
what kind of change (endpoint, migration, background job, config) — before
opening the checklist. That tells you which of the 12 dimensions can
plausibly apply. A DTO rename touches Code Quality and API & Contracts; it
does not touch Async & Concurrency or database migrations. **Record a
dimension as N/A explicitly rather than silently skipping it** — "N/A: no
async code in this diff" tells the next reader it was considered, not missed.
See [Coverage summary](#coverage-summary).

**2. Evaluate every applicable check mentally.** For each dimension the diff
touches, run down its checks below. Most resolve instantly — "no state
transition here", "no external call here" — and leave nothing behind. That is
the correct, common outcome. **Evaluating 161 checks does not mean producing
161 findings; it means not missing the one that matters because nobody
looked.**

**3. A check becomes a finding only with evidence.** Point at the line, the
test that's missing, the migration that isn't idempotent, the requirement
sentence nothing in the diff satisfies. "This might not scale" with nothing
behind it is not a finding — it is a hunch, and a hunch goes in the terminal
review as a question, never onto a PR or into a verdict as a defect.

**4. Tag every finding New or Pre-existing.** A defect the diff introduces and
a defect the diff merely touches, extends, or newly exposes are both worth
reporting, but they are not the same claim:

- **New** — the diff itself created this defect. Did not exist on the base
  branch.
- **Pre-existing** — the code already had this defect; the diff walks past it,
  extends it, calls it from a new place, or makes it reachable in a way it
  wasn't before. Report it, but don't describe it as something this change did.

Check `git diff <base>...<head>` for whether the offending line is `+` or
context before deciding. Don't blame the change for a line it didn't touch;
don't excuse it from a defect on a line it added because "the pattern already
existed elsewhere."

**5. Report only what survives.** See [Anti-noise rules](#anti-noise-rules)
and [Before a finding is reported](#before-a-finding-is-reported).

---

## Anti-noise rules

These aren't style preferences — violating them is a defect in the review
itself, not a matter of taste:

- **Never manufacture a finding to justify having run the framework.** A
  clean dimension is a legitimate, reportable result: "Security: evaluated,
  no findings" is worth more than an invented Low.
- **Never report a check just because it appears in the tables below.**
  Applicability first, evidence second, report third.
- **Never report the same root cause twice under two dimensions.** A missing
  transaction around two related saves is one finding, not a Data & Database
  finding *and* an Error Handling finding. File it once, under whichever
  dimension the fix actually belongs to, and fold the secondary angle into
  that finding's "why it matters" rather than opening a second entry.
- **Never flag a theoretical vulnerability with no credible execution path.**
  If exploiting it requires a caller that cannot exist given the endpoint's
  auth, the input's shape, or the platform, say why it isn't reachable and
  move on — don't report it "to be safe."
- **Never recommend a rewrite because another architecture is possible.**
  Structural change is a finding only when it fixes evidence of an actual
  defect, or the project's own rules (`CLAUDE.md`, its architecture guide, a
  plan in `docs/plans/`) require it.
- **Never recommend introducing a new abstraction, interface, or layer as
  part of a fix unless the defect itself requires it.** The same restraint
  that applies to reviewing existing code (*check 33*) applies to the fix you
  propose for it.
- **Never treat a stylistic preference as a defect.** If the codebase has no
  established convention on it, it isn't reportable at any severity.
- **Never demand a test that adds no confidence.** A test that re-asserts the
  framework, or duplicates an existing test with different data, is not a
  gap.
- **Never re-litigate what a factory run's evidence already proves.** A
  change made through a factory run carries a run file (`docs/factory/runs/R-nnn.md`)
  with its acceptance criteria, the tests that prove each one (proven red,
  then green), and an independent review by the `factory-reviewer` agent.
  Where that evidence covers a point, cite it and move on; report a finding
  there only if you can show the evidence is wrong — a test that passes for
  the wrong reason, a criterion that misquotes the requirement.
- **Never inflate or deflate severity to make a point.** See
  [Severity](#severity) — a Medium reported as Critical costs the next real
  Critical its urgency; a Critical softened to read comfortably is worse than
  skipping the review.

---

## Finding format

Every reported finding — inline, terminal, or written report — carries:

| Field | |
|---|---|
| **Severity** | CRITICAL / HIGH / MEDIUM / LOW / INFO — see [Severity](#severity) |
| **Title** | One line naming the defect, not the file |
| **Location** | Exact file path and line, or the honest anchor when there is no single line (see [review-pr's Step 5](../SKILL.md#step-5--post)) |
| **New or Pre-existing** | Per [How to use this framework, §4](#how-to-use-this-framework) |
| **What's wrong** | The defect, stated plainly |
| **Why it matters** | The concrete failure it causes — an input, a sequence, a load — never "could be an issue" |
| **Evidence** | The line, the test result, the doc citation, the requirement id, or the trace that supports the claim. This is what separates a finding from a suspicion |
| **Recommendation** | What to change, in one or two sentences — not a rewritten implementation |

Inline PR comments compress *what's wrong / why it matters / evidence* into
one short paragraph (see [Comment format](../SKILL.md#comment-format)) because
the code sits right beside the comment and is itself the evidence. A terminal
or written report keeps the fields separate since there's no code column
beside it.

---

## Severity

| | Means | Examples |
|---|---|---|
| 🔴 **CRITICAL** | Must be fixed before merge | Security vulnerability, data corruption or loss, cross-tenant or cross-customer data access, authorization bypass, production-breaking correctness |
| 🟠 **HIGH** | Should be fixed before merge | Significant business-logic defect, transaction/concurrency bug, major architecture violation, serious performance/scalability issue, critical missing test coverage |
| 🟡 **MEDIUM** | Address, need not block | Real maintainability cost, resilience gap, design smell, thin test coverage, meaningful performance concern below Critical/High scale |
| 🔵 **LOW** | Minor | Small inconsistency, limited-impact quality issue |
| 💡 **INFO** | Not a defect | Useful observation — terminal/report only, **never posted inline on a PR** |

**Do not inflate.** A Medium reported as Critical costs the next real
Critical its urgency. **Do not deflate either** — a security finding softened
to fit a comfortable review is worse than not reviewing.

**A rule the project marks *[enforced]*** — one a test or check fails the build
on — is broken only if the build is green anyway (the check does not cover
this case, or it was suppressed or allow-listed without a reason). If the
check would already fail the build, say so and move on: the build is the
report.

### Conformance findings specifically

Pass A produces a kind of finding the table above doesn't obviously cover:

| Situation | Severity |
|---|---|
| A specified requirement central to the feature is absent | 🟠 **HIGH** — the feature is not finished, whatever the code quality |
| A specified requirement is absent and **nothing owns it** — the deferral names a plan or phase that does not exist | 🟡 **MEDIUM**, anchored on whatever should have carried the requirement |
| A specified requirement is absent with a **real** owner and landing place | **Not a finding.** It is a decision |
| A doc the change invalidated was not updated in the change | 🟡 **MEDIUM** — 🟠 **HIGH** for a guide another team acts on (an integration guide, a published API contract) |
| Code and a doc contradict, and the requirements do not settle it | 🟡 **MEDIUM**, phrased as a question — do not pick a side quietly |

**And do not let a clean Pass A go unsaid.** "Not a finding" is still a
result and belongs in the conformance table.

---

## Coverage summary

Every review — terminal or written — closes with one line per dimension,
covering whichever dimensions actually applied, plus one line for the stack
checks:

```markdown
### Coverage

| Dimension | Result |
|---|---|
| Requirements & Business Logic | Evaluated — 1 finding |
| Architecture & Design | Evaluated — clean |
| Code Quality & Maintainability | Evaluated — 2 findings |
| API & Contracts | N/A — no API surface changed |
| Security | Evaluated — clean |
| Data & Database | Evaluated — 1 finding |
| Performance & Scalability | N/A — no query or loop changes |
| Async & Concurrency | N/A — no async code touched |
| Error Handling & Resilience | Evaluated — clean |
| Testing | Evaluated — 1 finding |
| Observability & Operations | Evaluated — clean |
| Configuration & Dependencies | N/A — no config or package changes |
| Stack checks (project rules) | Evaluated — clean |
```

This is a **summary**, not the 161-item list — never paste the full framework
into a report or a PR comment (only into this file). Its purpose is letting
the next reader tell "checked and clean" apart from "not applicable" apart
from "never looked" — the same reason Pass A's conformance table records
passing requirements rather than only failing ones.

---

## Before either pass — what already counts as a finding

**Knowing about a defect is not reporting it.** Both passes below assume
findings come from reading the diff. A defect you already knew about before
the review started never travels that path, so it is silently exempt from
every rule here — and those are the ones most likely to be dropped, because
familiarity reads as resolution.

So before Pass A, list what you already know is wrong with this code from any
source: earlier conversation with the user, a previous run, a plan in
`docs/plans/`, an open row in the defect log, a factory run file's
*Escalation* or review findings. **Each one is a candidate finding and has to
earn a severity, an anchor, and a New/Pre-existing tag exactly like a fresh
one.**

Three phrases to distrust in your own reasoning:

| You are about to think | What is actually true |
|---|---|
| *"already reported"* | Reported **to whom?** The audience is the PR's author or the branch's owner, not the person you've been talking to |
| *"already known"* | Known to you. The author has not read your conversation |
| *"already on the list"* | A list the author cannot see is not a report |

**And a finding mentioned inside another comment has not been reported.** An
aside at the end of a comment about something else — *"the underlying
inconsistency is already on the list"* — makes the issue feel handled while
leaving it invisible. It gets its own comment, on its own line, or it does
not exist.

---

# Pass A — Conformance — Requirements & Business Logic (checks 1–15)

**Runs first, on every review, however small the diff.** It asks whether the
code does what was decided; [Pass B](#pass-b--correctness--dimensions-212-checks-16161)
asks whether what is there is right. It covers dimension 1 of the framework
in full.

**Only this pass can find an omission.** Reading a diff cannot reveal the
requirement nobody implemented — there is nothing to read. A review that
skips this passes half-built features and calls them sound, and the gap then
arrives in acceptance testing as a defect against a specification the team
agreed to. That is the most expensive way for a review to fail, and the only
one the author cannot catch by re-reading their own diff.

**Where the requirements live.** The project's `CLAUDE.md` names the spec
source — a document set, a folder of snapshots, a ticket system — and which
revision is authoritative. Requirement ids take the form
`<id_prefix>-<section>-R<n>`, with `id_prefix` from `[requirements]` in
`devtools/factory/factory.toml`; the traceability file that section names (if
any) ties ids to code and tests. Rules decided for questions the requirements
leave open are under *Resolved* in `docs/open-questions.md`.

### A1 · Requirements to code — checks 1, 3, 4, 9

**Contradiction and omission are different failures, and only the first is
easy to see.** [A2](#a2--code-to-our-docs--check-4-part-15) catches "the doc
says X, the code does Y." It cannot catch "the requirements specify X and
nothing in the diff does anything at all" — silence produces no mismatch.
**That gap is how a specified requirement ships as missing without a single
reviewer noticing.**

So for every feature the diff touches, go the other way — from the
requirement to the code, not from the code to the requirement:

1. **Read the requirement section the feature claims**, from the
   authoritative revision of the spec source. Read the source itself rather
   than trusting a summary of it.
2. **List what it specifies.** Fields, validation, defaults, messages,
   required behaviours, required state transitions (*check 9*). Name each by
   its requirement id where one exists.
3. **Find each one in the diff.** What is absent is *check 1* (requirement
   not implemented) or *check 3* (required business rule missing).
4. **Check the global sections too** — requirements that bind every module
   (auditing, formats, permissions, paging). A feature can satisfy its own
   section perfectly and still miss a rule that applies to it.
5. **Cross-check any document that already tracks this** — a QA test suite,
   an API coverage table, the traceability file — where the project keeps one.

**A documented deferral is not a defect.** A feature plan's *"Deliberately
not built"* (or equivalent) table, where an item has a stated landing place,
records a decision (*check 4 handled correctly*), not an omission — do not
report it.

**But check that the landing place exists.** A deferral pointing at "its own
plan" or "a later phase" when no such plan and no such phase entry exists
means nobody owns it, and it will be discovered as a defect by QA instead
(*incorrect assumption about the requirement — check 4*). **That is a
finding** — see [Conformance findings specifically](#conformance-findings-specifically).

**In a factory run,** the run file's Spec lists the acceptance criteria the
change was built to, each tied to a requirement id. Compare the criteria
against the requirement text as well as the code against the criteria: a
criterion that misquotes or narrows the requirement is a finding the run's
own evidence cannot catch.

### A2 · Code to our docs — check 4 (part), 15

Find the doc that owns the affected topic — the owners are tabled in the
project's `CLAUDE.md` (*Where knowledge lives*) — and compare.

**Neither side is presumed right.** Where code and documentation disagree,
work out which represents the intended design; where the requirements are one
of the sides, the requirements win and the code is the finding (*business
logic inconsistent with existing system behaviour — check 15*). Flag a
contradiction you cannot settle rather than quietly picking one. Report it
once, not per file.

A guide that another team integrates against must be updated in the **same**
change as the behaviour it describes.

### A3 · The change to the docs it invalidated

The project rule: *change behaviour, fix the doc that owns it in the same
commit.* A change that alters behaviour and leaves its documentation behind
has an unfinished change in it, not a follow-up ticket.

Check the ones that go stale fastest:

- **Integration guides** — written from shipped code and **trusted by another
  team**. A guide that now lies is worse than none, and it is a finding on the
  change that made it lie.
- **The feature's plan in `docs/plans/`** — if the implementation departs
  from the plan, the plan changes in the same commit. Including its
  *Deliberately not built* table, where a scope change should show up.
- **Generated docs** (API contracts, schema) — the project's `CLAUDE.md` names
  them and the command that regenerates each. A changed contract or schema
  with a stale generated doc is a finding (and often an *[enforced]* one).
- **The defect log** — a fix carries its defect id and updates its row.

**Record the conformance verdict per requirement or section, including the
ones that pass.** It goes in the terminal review, not on the PR. A satisfied
requirement leaves no comment behind, so without the table a reader cannot
tell a section that was checked and found clean from one nobody opened.

### A4 · Business rule correctness — checks 2, 5–14

Beyond "is it there", is the rule **right**? This is where most of dimension
1's remaining checks live, and it needs the code read, not just the
requirements.

| # | Check | Apply it here |
|---|---|---|
| 2 | Business rule implemented incorrectly | Compare the code's actual condition against the requirement sentence word for word — an off-by-one on a comparison operator or an inverted boolean reads fine at a glance |
| 5 | Incorrect default behaviour | What happens when the caller supplies nothing? Compare against the requirement's stated default, not what seems reasonable |
| 6 | Incorrect edge-case behaviour | Empty collections, zero, boundary dates, the last item in a page, the first record for a new tenant or account |
| 7 | Invalid state allowed | Can the entity be constructed or saved in a state the domain forbids? Where the project distinguishes *deleted* from *inactive* (or similar), conflating them is this check |
| 8 | Invalid state transition allowed | Can a status move somewhere the domain doesn't permit — an entity re-entering a terminal state, a record moving between owners when the requirements forbid it |
| 9 | Required state transition missing | A workflow step the requirements demand that nothing in the diff triggers |
| 10 | Incorrect operation ordering | Side effects that cannot roll back (a file write, a message sent) ordered against a database commit so that a failure leaves them inconsistent; two saves in an order that can commit half an operation |
| 11 | Duplicate business operation | Can the same request, retried or double-submitted, create two of something meant to be one? |
| 12 | Missing idempotency | Anything run under a retry policy or a retried transaction block — is everything inside it safe to repeat? |
| 13 | Incorrect idempotency | An idempotency guard that only checks part of the key, or that a resubmission with the same *changed* value can defeat |
| 14 | Incorrect handling of partial/failed operations | A failed external call (email, storage, HTTP) after the local write already committed — is the inconsistency acceptable and a recorded decision, or does new code do it without that being decided? |

**A reference check has two questions, and only one applies to resubmitted
data**: "does this row exist" and "may it be chosen *now*." Checking only the
first on a value the record already had is *check 4* (incorrect assumption).

---

# Pass B — Correctness — Dimensions 2–12 (checks 16–161)

Work through the dimensions below in the order that matches what the diff
touches; read the diff, decide which can plausibly break, and press hardest
there. **Record every dimension you didn't press on as N/A in the
[Coverage summary](#coverage-summary) rather than leaving it unsaid.** Then
apply the [stack checks](#stack-checks--from-the-projects-claudemd-and-adapter).

## Principles

**Understand before judging.** Read the diff, then the surrounding code, then
trace the execution path. A finding raised from the diff alone is a guess.

**The project's architecture is the standard, not general best practice.**
Where the project's `CLAUDE.md`, its architecture guide or a plan in
`docs/plans/` has already decided something, that decision wins. A pattern is
not wrong merely because another pattern exists.

**Do not propose rewrites.** Review what is there. Structural change is a
finding only when it fixes a real defect or the architecture requires it.

**Distinguish a defect from a preference.** Correctness, security, data
integrity and architecture violations are findings. Everything else is a
suggestion, and suggestions do not go inline on a PR.

---

## B1 · Architecture & Design — checks 16–33

| # | Check | Apply it here |
|---|---|---|
| 16 | Architectural layer boundary violation | The layering the project's `CLAUDE.md` states. If an architecture test enforces it, check whether the suspected violation is one the test would already have caught — if so the concern is elsewhere |
| 17 | Incorrect dependency direction | Same as above, checked from the other end |
| 18 | Domain depending on infrastructure concerns | Business rules reaching for the database driver, the web framework, the file system or the clock directly |
| 19 | Business logic in API/endpoints | An endpoint, controller or route handler deciding business rules instead of dispatching and mapping the result |
| 20 | Business logic in the persistence layer | A repository or data-access method that does more than fetch/save is doing the handler's job |
| 21 | Business logic duplicated across layers | The same rule re-implemented in a validator and a domain method, silently able to drift |
| 22 | Incorrect responsibility assignment | Is this handler/service/repository doing what its name says, or has scope crept in? |
| 23 | SRP violation | A class or module with more than one reason to change |
| 24 | OCP violation | Only worth raising with a concrete extension the current design can't accommodate — not "could be more open" in the abstract |
| 25 | LSP violation | A derived type that narrows a contract its base promises |
| 26 | ISP violation | A consumer forced to depend on members it never uses |
| 27 | DIP violation | A high-level module depending on a concrete low-level one instead of an abstraction |
| 28 | Circular dependency | Between packages/projects or between classes within one |
| 29 | Excessive coupling | Changing one class forces edits across many unrelated ones |
| 30 | Inappropriate inheritance | Used for code reuse rather than a genuine is-a relationship |
| 31 | Missing composition where appropriate | Only where inheritance is visibly straining |
| 32 | Incorrect aggregate/entity boundaries | A write path that lets callers modify a child without going through the owner that guards its invariants |
| 33 | Inappropriate abstraction / premature generalization | An interface or base class with no second implementer in sight |

---

## B2 · Code Quality & Maintainability — checks 34–48, 161

| # | Check | Apply it here |
|---|---|---|
| 34 | Duplicated code | Especially business rules re-typed rather than shared |
| 35 | Unnecessary complexity | A simpler shape already exists elsewhere in the codebase for the same problem |
| 36 | Excessively complex method | Deep branching, many responsibilities in one function |
| 37 | Excessively complex class | Same, at class or module scope |
| 38 | Excessive nesting | Prefer early returns / guard clauses over pyramids |
| 39 | Poor naming | Names must say what the thing is, per the project's naming rules |
| 40 | Misleading naming | Name promises one thing, code does another |
| 41 | Unclear control flow | Hard to trace what runs when, especially across async/event boundaries |
| 42 | Magic numbers or strings | Unnamed literals carrying meaning; configuration read by raw key string where the project uses typed settings |
| 43 | Dead code | Unreachable branches, unused parameters, leftover scaffolding, commented-out code |
| 44 | Unnecessary variables | Intermediate locals that add no clarity |
| 45 | Unnecessary abstraction | An interface, wrapper, or layer that exists but adds no substitutability or testability |
| 46 | Inconsistent coding patterns | The change solves a problem differently from how the codebase already solves it, with no reason given |
| 47 | Poor separation of concerns | Grab-bag names (`Helper`, `Manager`, `Utils`, `Common`) are a symptom of unassigned responsibility — check the project's own banned-name list |
| 48 | Code difficult to safely modify or extend | Would a reasonable next change require touching many unrelated places? |
| 161 | Code the requirement did not ask for | An option, setting, parameter or extension point no criterion needs; a wrapper that only renames a library call; a "while I was here" change outside the Spec's scope. Name the criterion it serves, or it is a finding — the fix is to delete it, or to take it to its own tracked change |

---

## B3 · API & Contracts — checks 49–60

| # | Check | Apply it here |
|---|---|---|
| 49 | Incorrect HTTP method | GET for reads, POST for creation/actions, PUT/PATCH for updates, DELETE for removal — or the project's own convention |
| 50 | Incorrect HTTP status code | 201 on create, 204 vs 200 on update, 404 vs 403 (a resource outside the caller's scope often correctly answers 404) |
| 51 | Inconsistent API response contract | Enum, date and null serialisation matching what sibling endpoints already do — verify, don't assume |
| 52 | Inconsistent error contract | Failures should map to the same error shape other endpoints already use |
| 53 | Missing request validation | Every input the code trusts without a validator or guard |
| 54 | Incorrect request validation | Validation in the wrong layer is often *also* validation that's wrong — shape checks, checks that need the database, and domain invariants each belong where the project puts them |
| 55 | Returning unnecessary data | List/dropdown responses carrying detail-only fields |
| 56 | Exposing internal implementation details | Stack traces, internal ids not meant for the client, raw database or framework error messages |
| 57 | Exposing sensitive information | Password hashes, tokens, other tenants' or customers' data |
| 58 | Incorrect resource/route design | Nesting, pluralisation, and verbs-as-nouns consistent with sibling endpoints |
| 59 | Unbounded collection endpoint | Every list must page; a caller-chosen sort field goes through an explicit allow-list, never reflection or string-built SQL |
| 60 | Incorrect API compatibility/versioning behaviour | A changed response shape or removed field breaking a contract a consumer or integration guide relies on |

---

## B4 · Security — checks 61–80

| # | Check | Apply it here |
|---|---|---|
| 61 | SQL injection | Any raw SQL built by string concatenation or interpolation of caller input, in queries or migrations |
| 62 | Command injection | Any shell-out built from caller input |
| 63 | Path traversal | Any code building a file path from caller input |
| 64 | SSRF | Any outbound call built from a caller-supplied host or URL |
| 65 | Cross-site scripting | Caller-controlled text rendered unescaped into HTML, email templates included — unless the project records it as a decision |
| 66 | CSRF where applicable | Relevant where cookie-based authentication is in play |
| 67 | Insecure deserialization | Deserialising untrusted data into arbitrary types, or trusting a client-supplied type name |
| 68 | Unsafe file upload | Trust the bytes, not the file name or declared content type |
| 69 | Missing upload size/type restrictions | Every upload path needs bounds |
| 70 | Hardcoded secrets | Anything in a committed file that isn't a recognisable placeholder |
| 71 | Secrets exposed through source/configuration | Real values in tracked settings files, fixtures, scripts or CI config |
| 72 | Secrets exposed through logs/telemetry | Never log passwords, hashes, tokens, reset/invitation links, connection strings, customer-identifying data |
| 73 | Weak password handling | Hashing algorithm, complexity rules, comparison method (constant-time where relevant) |
| 74 | Incorrect credential/token handling | Refresh rotation, invalidation on password change or deactivation |
| 75 | Incorrect token validation | Issuer/audience/lifetime/signature checks all present; signing key not a placeholder |
| 76 | Broken authentication | A new code path that bypasses the project's authentication middleware or current-user resolution |
| 77 | Broken authorization | Is authorization default-deny here? Scrutinise every anonymous or opt-out marker. A new gated operation needs its permission granted where the project defines permissions *and* a test asserting it |
| 78 | IDOR/BOLA/resource ownership | Can the caller supply an id for a resource that isn't theirs and get it back — or change it? |
| 79 | Privilege escalation | Can a lower role reach a privileged action through a side path — a list filter, a bulk endpoint, a nested resource |
| 80 | Cross-tenant data access — **treat as CRITICAL by default** (multi-tenant systems) | Can a query return another tenant's rows? Is a global filter bypassed, and if so is scoping re-applied by hand? Is scoping applied on **writes as well as reads** — a scoped list with an unscoped fetch-for-update is an IDOR with a tidy front end |

---

## B5 · Data & Database — checks 81–98

| # | Check | Apply it here |
|---|---|---|
| 81 | N+1 query | Any loop that queries per iteration instead of a single set-based query |
| 82 | Unnecessary database query | A lookup the caller already had, or a second query re-hitting the same table for something the first returned |
| 83 | Duplicate database query | Same query run twice for two different checks that could share one result |
| 84 | Database query inside an avoidable loop | Same root cause as 81 — file once |
| 85 | Loading excessive data | Full rows or object graphs loaded where a projection would do |
| 86 | Filtering performed in application instead of database | Filtering, sorting or paging after materialising the result set |
| 87 | Missing database-level pagination | Hand-rolled paging, or paging in memory, where the project has a shared paging mechanism |
| 88 | Missing required index | Any new frequent filter/sort column with nothing backing it |
| 89 | Incorrect or ineffective index | A unique index that ignores tenant scoping or soft-deleted rows, so a deleted record holds its unique value forever |
| 90 | Inefficient join/query | A query shape that forces the database to do much more work than the result needs |
| 91 | Incorrect ORM tracking/session behaviour | Read paths paying for change tracking; a write path whose changes are never tracked and silently no-op on save |
| 92 | Excessive eager loading | Related data pulled that the query doesn't need |
| 93 | Inefficient projection | Mapping done in application code after a full load where the query could project |
| 94 | Incorrect transaction boundary | Does the unit of work match the business operation, not the code's convenience? |
| 95 | Missing transaction where atomicity is required | Two writes that must succeed together, committed separately |
| 96 | Missing concurrency protection | No optimistic concurrency token (or lock) on a row multiple actors can race to update |
| 97 | Missing database constraint/invariant | An invariant enforced only in code with nothing backing it at the schema level |
| 98 | Unsafe schema/migration change | Editing an already-applied migration; a destructive change without a data path; raw SQL in a migration that is not idempotent or not proven the way the project requires |

---

## B6 · Performance & Scalability — checks 99–110

| # | Check | Apply it here |
|---|---|---|
| 99 | Blocking operation on the request path | Blocking on async work, or a synchronous file/network call on a request thread |
| 100 | Sync-over-async | Same root cause as 99 |
| 101 | Unnecessary offloading to a thread/worker | Wrapping already-async work, or offloading CPU-light work that gains nothing |
| 102 | Excessive memory allocation | Large materialisations of data that could be streamed |
| 103 | Potential memory leak | Undisposed resources, handlers never unsubscribed, growing static collections (also *check 112/113*) |
| 104 | Repeated expensive computation | Recomputing something per-request that doesn't change per-request |
| 105 | Missing appropriate caching | Only worth raising with a concrete, credible hot path — not speculative |
| 106 | Incorrect caching strategy | **Cache invalidation** is the usual failure: does it happen, and does it happen *before or after* the commit it's invalidating? |
| 107 | Excessive network calls | Multiple round trips where one would do |
| 108 | Sequential independent I/O that should be parallelized | Independent awaits chained where they could run together |
| 109 | Excessive payload size | A response carrying fields no client needs (ties to *check 55*) |
| 110 | Operation does not scale with expected data volume | A list operation's query count should be **fixed**, not proportional to row count — if the project has a query-counting test pattern, a new list endpoint should use it |

**Raise a performance finding only where the impact is credible at realistic
volume** — this project's own scale, not a hypothetical.

---

## B7 · Async & Concurrency — checks 111–120

| # | Check | Apply it here |
|---|---|---|
| 111 | Race condition | Two requests that can interleave and leave inconsistent state |
| 112 | Unsafe shared mutable state | Static or global mutable state reachable from concurrent requests |
| 113 | Thread-unsafe collection/state | A non-concurrent collection touched from more than one request context |
| 114 | Incorrect locking | Lock scope too broad/narrow, or a lock around something that doesn't need one |
| 115 | Potential deadlock | Nested locks in inconsistent order, or blocking-on-async inside a lock |
| 116 | Fire-and-forget operation without controlled lifecycle | Work started with nothing owning its failure or shutdown; an external side effect (email, HTTP) fired from inside a transaction or event handler that can still roll back |
| 117 | Unobserved asynchronous failure | A task/promise started and not awaited, whose exception disappears |
| 118 | Cancellation ignored | A cancellation signal accepted and dropped, or replaced with "never cancel" in product code |
| 119 | Cancellation not propagated | A signal accepted by a handler but not passed to the data-access or HTTP call beneath it |
| 120 | Incorrect concurrent processing/duplicate execution | A block that a retry policy may run more than once, doing something that must happen once (cross-reference *check 12*, don't double-report the same root cause) |

---

## B8 · Error Handling & Resilience — checks 121–132

| # | Check | Apply it here |
|---|---|---|
| 121 | Exception swallowed | A catch that neither recovers nor reports — unless it matches a recorded, deliberate best-effort pattern in the project |
| 122 | Overly broad exception handling | Catching every exception where a narrower type was available and meaningful |
| 123 | Exception caught without meaningful recovery | A catch block that logs and rethrows the same thing, adding nothing |
| 124 | Original exception context lost | Rethrowing in a way that resets the stack, or wrapping without the inner exception |
| 125 | Generic/meaningless exception | A raw generic exception where a domain-specific error or an expected-failure result was called for |
| 126 | Sensitive information exposed through errors | Stack traces or internal messages reaching the client (ties to *check 56*) |
| 127 | Inconsistent exception-to-response mapping | Expected failures and faults handled the way the project separates them (result types vs exceptions, or its equivalent) |
| 128 | Missing centralized error handling | Code doing its own error-to-response translation instead of the project's central handler |
| 129 | Missing timeout for external operation | Any HTTP/SMTP/storage call with no bound |
| 130 | Incorrect retry strategy | Retrying a non-idempotent operation, or retrying indefinitely |
| 131 | Missing retry/backoff for transient failure | Only worth raising where the failure mode is realistic and the operation is safe to retry |
| 132 | External dependency failure not handled safely | A failed external call must not leave inconsistent local state — what happens to the already-committed row when the call after it fails? |

---

## B9 · Testing — checks 133–147

| # | Check | Apply it here |
|---|---|---|
| 133 | Missing test for core business behaviour | The rule the requirements name, not incidental code around it |
| 134 | Missing negative-path test | Invalid input, forbidden transition, wrong actor |
| 135 | Missing edge-case test | Boundary values, empty collections, first/last page |
| 136 | Missing validation test | Each validation rule exercised at least once |
| 137 | Missing authorization test | **Always qualifies** for security-critical logic — a new gated operation needs a test asserting who may and may not reach it |
| 138 | Missing isolation test | **Always qualifies** in a multi-tenant system — cross-tenant leakage is Critical, and an entity without a two-tenant test is unverified on the one thing that matters most |
| 139 | Missing integration test where integration behaviour matters | Query shape, filter interaction, transaction behaviour — none of this is provable by a unit test with the database mocked |
| 140 | Missing persistence/database test where database behaviour matters | Constraint, index, migration behaviour against the real database engine |
| 141 | Missing concurrency test where concurrency matters | Optimistic concurrency, idempotent retry — only where the change introduces one of these |
| 142 | Missing regression test for fixed defect | Reference the defect id in the test name or nearby, per the defect log's convention |
| 143 | Test verifies implementation instead of behaviour | Asserting a computed value against the constant that produced it proves the wiring, not the requirement's specified value |
| 144 | Test has weak or meaningless assertions | Asserting "no exception thrown" or a non-null result where the actual value matters |
| 145 | Test depends on fragile setup/state | Shared mutable fixtures, ordering-dependent tests |
| 146 | Test unnecessarily coupled to external services | A unit test that shouldn't need a database or network but does |
| 147 | Test suite does not adequately prove the requirement | Green tests that, read together, still leave a stated requirement unverified |

**Check the skip count, not the colour.** Tests that need a database or a
service often skip silently when it is missing, and a filter that matches
nothing runs nothing — a run that executed nothing looks identical to one that
passed. In a factory run, the run file records which tests prove each
criterion and that they ran; trust that evidence unless you can show it is
wrong.

---

## B10 · Observability & Operations — checks 148–155

| # | Check | Apply it here |
|---|---|---|
| 148 | Missing meaningful logging | A failure path (especially a swallowed exception, *check 121*) with nothing recorded |
| 149 | Incorrect log level | Expected/handled conditions logged as Error; real faults logged as Information |
| 150 | Sensitive data logged | Same list as *check 72* — passwords, hashes, tokens, reset/invitation links, connection strings, customer data |
| 151 | Insufficient diagnostic context | A log line with no id, no tenant, no correlating value — nothing to search on later |
| 152 | Missing structured logging | Interpolated strings where structured fields were available and would make the log queryable |
| 153 | Missing metrics for important operational behaviour | If the project has no metrics infrastructure decided, treat as **INFO**, not Medium/High, unless a plan committed to metrics for the touched area |
| 154 | Missing tracing/correlation for distributed operations | Same caveat as 153 |
| 155 | Failure cannot be diagnosed effectively in production | The combined effect of 148–152: if this specific failure happened in a shared environment right now, is there enough in the logs to find it? |

---

## B11 · Configuration & Dependencies — checks 156–160

| # | Check | Apply it here |
|---|---|---|
| 156 | Hardcoded environment-specific configuration | A safety-relevant behaviour gated only on an environment name, not also on an explicit setting; environment names spelled differently from what the deployment uses |
| 157 | Unsafe/default configuration | A placeholder that startup validation cannot recognise as a placeholder, so the app boots with a real-looking but wrong value |
| 158 | Configuration not validated at startup/use | A setting read ad hoc, bypassing the project's validated settings mechanism |
| 159 | Unnecessary or inappropriate dependency | Reinventing is a last resort; conversely, a new dependency for something the codebase already does in ten lines is also a finding. A licence-sensitive dependency needs whatever record the project requires before it is added |
| 160 | Dependency/version introduces known compatibility or security risk | Versions pinned where the project pins them. If CI already scans for vulnerable packages, only flag this here if the change is what *introduced* the vulnerable version |

---

# Stack checks — from the project's CLAUDE.md and [adapter]

**This section lists no rules of its own, on purpose.** The 161 checks above
are engineering failures that apply to any stack. Everything that depends on
*this* project's language, framework, ORM, test runner or conventions comes
from the project, and is applied here, after Pass B.

**Where to find them:**

- **The root `CLAUDE.md`** — its *Code rules*, *Behaviour*, *Security* and
  *Git* sections, or whatever it calls them. Every rule there is a stack check.
- **The nested `CLAUDE.md` of each folder the diff touches** — migrations,
  endpoints, tests. Read the one for each folder in the diff.
- **The architecture guide** the root `CLAUDE.md` links — for *why* a rule
  exists, and for its *Known trade-offs*: a recorded trade-off is not a
  finding.
- **`[adapter]` and `[commands]` in `devtools/factory/factory.toml`** — which
  stack the project is, how its tests are tagged to acceptance criteria
  (`criterion_marker`), where test files live, the build command. A test that
  claims to prove a criterion but lacks the adapter's marker proves nothing to
  the referee.

**How to apply them:**

1. **List the rules that the diff can touch** — the ones for the layers,
   folders and kinds of change in scope. Skip the rest, and say so in the
   coverage line.
2. **Rules marked *[enforced: X]*** are backed by a test or check (architecture
   tests, convention tests, a pre-commit script). Do not re-run the check in
   your head: confirm the build is green, and look instead for the ways around
   it — a suppression, a new allow-list entry without a reason, a case the
   check's pattern does not match.
3. **Unenforced rules** — the ones only a reviewer catches — are where this
   section earns its place. Typical kinds: where things live (folder and
   namespace rules), naming, how expected failures are returned, how
   transactions and retries are written, how time is obtained, how files are
   stored, how audit fields are written, how tenancy and ownership scoping are
   declared, what the ORM or migration rules require, how tests assert and
   what an integration test must prove.
4. **Cite the rule, not a principle.** "`CLAUDE.md` → *Behaviour*: two saves
   that must succeed together go in one transaction" gets acted on; "atomicity
   is good practice" gets argued with.
5. **File each finding under the dimension its fix belongs to** (a missing
   tenancy declaration is Security, check 80), and record the stack pass on
   its own coverage line. Never file one root cause twice.

**If the project has no `CLAUDE.md` rules for an area, there is no stack
check there** — do not import another stack's conventions to fill the gap.

---

## Before a finding is reported

Verify each one:

- Is the path actually reachable?
- Is it already handled — middleware, a global filter, an interceptor, an
  architecture test, an existing abstraction?
- Is it intentional per a plan, a *Resolved* entry in
  `docs/open-questions.md`, or a recorded trade-off in the architecture guide?
- Does a factory run's evidence already settle it — a criterion's tests,
  proven red then green?
- Does the concern apply to *this* code path, or to one that only looks like
  it?
- Have you tagged it **New** or **Pre-existing** per
  [How to use this framework, §4](#how-to-use-this-framework)?

If it survives only as a suspicion, it goes in the terminal review as a
question — never as a finding, inline or otherwise.

**If nothing meaningful is found, say so explicitly.** A review that
manufactures findings to look thorough teaches people to skim the next one.
