---
name: requirements-check
description: >
  Checks whether the live requirement sources have moved since our last snapshot —
  every source listed in requirements/read-me.txt, wherever it lives (a URL, a shared
  drive, or documents sent by email) — and if one has, captures it as the next
  V<n> snapshot and records what changed. These are the only sources that change
  without anyone here changing them. Run it before trusting a requirement section,
  before estimating, before chasing an open question, and before a release.
  "Could not check" is a valid result.
  With `all` ("is everything current", the weekly sweep) it checks every
  source with a live location and leads with one verdict: UP TO DATE / STALE /
  COULD NOT VERIFY.
---

You are checking whether **the requirement sources have moved under us**.

Everything else in this repository changes because somebody here changed it. These
are the only sources that change on their own — which makes being out of date the
default rather than an accident.

---

## Why this matters more than it sounds

**A question in `docs/open-questions.md` that looks unanswered may have been answered
weeks ago** — in a new revision, or in a comment on the live document. Chasing someone
for something already written costs their goodwill and ours.

The reverse is worse: **a plan built on a section that has since changed is
confidently wrong**, and nothing in the code, the tests or the other documents will
contradict it. No other skill catches this, because they all treat `requirements/` as
read-only evidence — correct, but it means nobody else checks whether the evidence is
current.

---

## Where this sits

| | |
|---|---|
| **`requirements-check`** — this one | Our snapshots vs **the live sources**. The only skill that looks outside the repository, and the only writer of new files in `requirements/`. |
| `requirements-check all` | This skill's `all` mode — every source, one verdict first (below) |
| `docs-consistency` | Documents against each other |
| `docs-vs-code` | Documents against code, neither presumed right |
| `docs-update-from-code` | Documents rewritten from known-good code |
| `fsd-audit` | Requirements ↔ plan ↔ code ↔ tests |

Run this **first** when requirements are in play. The others all reason from the
snapshot; if the snapshot is stale, they reason from the wrong thing.

---

## When to run it

- Before trusting a requirement section you are about to design or build against
- Before estimating anything, or agreeing a date
- Before chasing anyone about an open question — it may already be answered
- Before a release, alongside the other audits
- On whatever cadence the team agrees. Weekly is not excessive for documents being
  edited continuously.

---

## The sources — `requirements/read-me.txt`

**Start at `requirements/read-me.txt`.** It is the index of every source, and this
skill hard-codes none of them: a moved document needs only its entry updated. Each
entry says:

| Field | Says |
|---|---|
| **Name** | The source's name, which is also its snapshot file stem |
| **Role** | `requirements` (the source of truth — wins every conflict), `supporting` (best evidence where the requirements are silent; say for what, e.g. "the only source for permissions"), or `not a requirement` (QA cases, bug trackers — see below) |
| **Live** | Where the current version lives: a URL, a shared-drive or network path, `sent by email` (from whom), or `none` (delivered once, or derived from code — nothing to check) |
| **Export** | How to get a copy: the format (PDF for a document, CSV for a spreadsheet — one file per tab that is ours), the menu or command, and anything to leave out (tabs belonging to another project) |
| **Comments** | Whether answers arrive as comments or threads on the live document, and how to read them |
| **Snapshot** | `versioned` (default) — each revision a new `<Name> - V<n>.<ext>`; or `current + backup` for a fast-changing tracker that is not a requirement — the superseded copy moves to that folder's `backup/` with a date suffix |
| **Last checked** | One line, **overwritten** each run: date and outcome (captured `V<n>`, unchanged, or could not check) |

An example entry:

```
Name:         Functional Spec
Role:         requirements
Live:         https://docs.example.com/d/abc123
Export:       File > Download > PDF. Save to requirements/functional-spec/
Comments:     yes — the product owner answers in comment threads
Snapshot:     versioned
Last checked: 2026-10-01 — unchanged (V7)
```

A folder with its own `read-me.txt` may hold the entry instead; the root index then
points to it. **A source with a live location and no entry is a finding** — it has
been going unchecked.

**Build from the highest `V<n>`.** Sort numerically (`ls | sort -V`); a lexical sort
puts `V9` above `V11`. Often nothing inside the files distinguishes them; the file
name is the only marker.

### The requirements win, and QA does not count as a requirement

**A rule appearing only in a QA test case, a bug tracker or a design is not a
requirement.** Their authors write from their own reading, and they interpret. Where
such a rule is confirmed by the requirements, it is useful corroboration and worth
citing. Where it is not, **it is a question for `open-questions.md`, never a rule to
adopt.**

Same ordering for a supporting document: where it disagrees with the requirements,
**build the requirements and log the disagreement as a question.** Do not split the
difference.

---

## Phase 1 — Get the current documents

For each source with a live location, in this order:

1. **A connector or CLI that can read it**, if one is available in this session — a
   Drive or Docs connector, a document platform's API or CLI, a mounted shared drive,
   `curl` for a plain URL. Test it with one metadata or read call before committing
   to it for the whole run. **Try it before assuming it will fail**, and do not
   assume yesterday's failure still holds.
2. **Ask the person to export it** in the format the entry names, give them the link,
   and say where to save it (the scratchpad, not `requirements/` — capture comes
   later).
3. **`sent by email`**: ask the person whether a newer version has arrived since the
   last snapshot's date, and to save it if so. Their "no newer version" is the check;
   no answer is "could not check".

**If you cannot reach a document, stop and say so for that source.** Do not report
"no change" when what happened is that you could not look. That is the one failure
here that actively misleads — a clean bill of health nobody earned.

### When a tool cannot open a document

"No access" and "no such file" often look identical. Diagnose before reporting:

1. **Retry by a second route** — metadata *and* content. One failure could be
   transient; two by different routes is not.
2. **Confirm the tool works** by opening something else you can reach. If that
   succeeds, the problem is that specific document.
3. **Check the account** the tool is signed in as.
4. **A search miss proves nothing.** Search indexes are often incomplete; never
   conclude "not shared" from a search returning nothing.

**Being able to open a document in a browser does not mean an API can fetch it.**
Shared or team drives often need an explicit opt-in the tool may not send, and an
owning organisation can restrict third-party app access — both invisible from the
browser, and neither fixable from this end. **Go straight to the manual export**
rather than sending someone through sharing settings that are already correct.

**Never copy the live document somewhere more convenient** (into someone's own drive)
to make it readable — **a copy stops tracking the live document**, which is the one
property this skill depends on.

> **Take the export format the entry names.** A spreadsheet exported to PDF does not
> extract reliably — merged cells misalign values against the wrong rows, and that
> produces false "contradictions". Take CSV for anything tabular, and never
> transcribe from PDF text of a spreadsheet.

**A source that is a folder can gain and lose files, not just change them.** List it
and compare the set of files, not only their contents. A new document appearing is a
finding in its own right.

### Comments and threads — read them every run, where the source has them

Where the entry says answers arrive as comments, **read them on every run.** Owners
often answer in a thread without changing the document's text, and an export to PDF
or CSV drops comments — so a text-only check is **not** a complete check of that
source.

- Read every thread created or replied to since the last run (the *Last checked*
  date). Save what the tool returns to the scratchpad and list threads
  chronologically with their replies.
- **Threads rarely carry a key to their place in the text.** Tie a thread to a section
  by its own wording; a question usually quotes the rule it is about.
- **An owner's reply is an answer.** Cross-check each against the open questions in
  `open-questions.md`. "Need a meeting" or "will clarify later" is recorded on the
  question too — it stops someone chasing it again.
- **A resolved thread means the thread was closed, not that the document was
  corrected.** A reply saying "updated" should show up in the text diff; if it does
  not, check.
- Comment activity alone can move a document's modified time. A metadata move with
  identical text means **read the new comments**, not "nothing happened".

---

## Phase 2 — Fingerprint before diffing

**Cheap check first**, per source.

### Anything whose metadata you can read

**Compare the live modified time against the date our latest snapshot was taken.** If
the live document was last modified *before* our snapshot, **it cannot have changed
since** — conclusive on its own, at the cost of one metadata call. Corroborate with
exact byte size where the tool reports one. This is the only fingerprint that works
for spreadsheets and other binaries, and it is the fastest for everything else.

> **One caveat.** Our snapshot's date is its commit date in git, or its file time on
> disk — which is the download time *only if it was downloaded fresh*. Where the
> margin is narrow or the provenance unclear, extract and diff instead of trusting it.

### A folder

A folder's own modified time usually changes when children are added or removed, so a
folder last modified before our snapshot proves **no files were added or deleted**. It
does **not** track edits inside a child — check the children individually, and if
they cannot be listed, say the folder's contents are unverified rather than implying
they are clean.

### A file you have both copies of

A byte-for-byte hash match settles it. A mismatch does not — many exports embed a
timestamp — so extract text from both and compare paragraph count, table count, page
count and total character count. If every count matches, that source almost certainly
has not moved. Do the full diff only once the fingerprint says something changed.

Extract with a text tool that preserves reading order (for a PDF, a raw rather than
layout mode — layout mode scrambles merged table cells). Work in the scratchpad, never
in the repository.

**A table of contents is not sufficient on its own.** A change inside a section that
shifts nothing across a page boundary leaves the contents list identical. Act on the
modified time and size moving, not on the contents diff alone.

---

## Phase 3 — Diff

```
git diff --no-index --unified=2 <previous>.txt <current>.txt
```

Diff against the previous live pull if one was saved; otherwise against text extracted
from the latest committed `V<n>` — noisier, but it still catches real differences.

**Expect noise.** Page numbers extract inline, so a paragraph moving across a page
boundary shows as a move with no content change. Read past it rather than reporting
it.

**Measure section sizes as well as reading the diff.** Count the lines between each
body heading and the next, in both versions, and compare. The diff alone
under-reports scale and hides removals:

- **A section that grew from one line to a hundred looks like an unremarkable run of
  additions** — and can be mistaken for a rename from the hunk headers.
- **A section that shrank is invisible** among ordinary rewording.
- **"Nothing outside §X moved" is a claim to verify, not to infer.**

**Match headings in the body, not the table of contents.** The contents list repeats
every heading, often with dot leaders, so a naive search matches it first and reports
every section as empty. Skip the front matter.

Sort real changes into four categories:

1. **New requirements** — sections that did not exist
2. **Corrections** — a rule whose meaning changed
3. **Renames and rewording** — same meaning, different words
4. **What did *not* change** — especially sections we are waiting on

That last category is not filler. **"§4.10 is still empty" is a finding**, and it is
the one that affects the schedule.

---

## Phase 4 — Capture

Only for sources that actually moved.

**`versioned`** — save the new copy as the next version, `<Name> - V<n+1>.<ext>`, in
the same folder, matching the existing naming exactly. **Never overwrite or edit an
existing `V<n>`**; they are the record of what we were told and when.

**`current + backup`** — move the superseded copy into the folder's `backup/` with a
date suffix, and put the new one at its plain file name, so links to "the current
document" keep working while history is kept.

**Write the download straight to disk.** Where a tool returns the file encoded (for
example base64 in its output), decode it with a script that reads the tool's saved
output and writes the destination file — never by retyping or re-pasting the encoded
text, which corrupts files that still look the right size. **Verify every capture**:
it must open (a PDF extracts to text, a spreadsheet parses, an archive unzips
cleanly), and the specific text the diff found must be present in the saved file.

---

## Phase 5 — Record

**A revision of a `requirements` or `supporting` source** adds a section to
`docs/project.md` **Part 3**: the source and its new `V<n>`, the short version, the
four categories with section numbers, then how the comparison was produced. **Part 3
keeps only the latest comparison per source**: move the section the new one replaces,
verbatim, to the top of `docs/archive/requirements-history.md` in the same change.

**Answers found in comments** go on the question they answer in
`docs/open-questions.md` — quoted, dated, and marked as having come from a comment
rather than the text, so nobody later hunts for it in a snapshot. They do not get a
Part 3 section: Part 3 is the text's history.

**A `not a requirement` source that changed** does not go in Part 3. Each change
produces one of:

- a **question in `open-questions.md`**, where it states a rule the requirements do
  not
- a **note against the feature's plan**, where the requirements confirm it and it adds
  a test worth having
- a **row in `docs/defects.md`**, for a reported bug a person has confirmed against our
  own code — the tracker is never a substitute for the defect log
- **nothing**, which is a legitimate outcome

Then check what any change lands on:

- **An open question that is now answered** — move it to *Resolved* with the date and
  the quoted answer, as `docs/CLAUDE.md` says. This is the highest-value outcome of the
  whole skill.
- **A feature plan built on a changed section** — flag it. Do not rewrite the plan in
  this pass; naming it is enough.
- **A requirement id in the traceability matrix** (`[requirements].traceability` in
  `devtools/factory/factory.toml`, default `docs/Audit/spec-traceability.md`) whose
  section changed — name it; `fsd-audit` re-audits it. Do not edit `docs/Audit/`.
- **An open factory run** (`docs/factory/runs/`) whose criteria cite a changed
  section — name it.
- **A contradiction with something already built** — the requirements win, so this is
  a `docs/defects.md` row or a new question, **never a change to the snapshot**.
- **The delivery schedule**, if scope moved.

---

## Rules

**This skill is the only writer of new files in `requirements/`, and it never edits
one that exists.** Add a new snapshot; change nothing already there. The `read-me.txt`
files are the only exception, in two places: a link or export instruction, when it
changes, and the one **`Last checked:`** line per source, which each run
**overwrites** with its date and outcome. **Never append a dated note** — that is how a
read-me grows into a diary. What a revision changed goes in `docs/project.md` Part 3;
what a comment answered goes on its question; a check that found nothing leaves only
the overwritten line.

**A design showing something the requirements do not specify is not a requirement.**
A design change is not a specification change.

**Where the requirements contradict themselves**, precedence does not help. That is a
question for `open-questions.md`.

**Report what you could not check.** If only one source was reachable, say which
sources you actually looked at.

---

## Report

1. **Which sources were checked**, and which could not be reached, and why
2. **The fingerprint** per source — ours versus current
3. **Moved or not.** If nothing moved, stop here; that is a complete and useful answer
4. **What changed**, in the four categories, with section numbers
5. **What the comment threads say** — for each source that has them: threads new or
   replied to since the last run, and which questions they answer. "No new threads"
   is a result to state; "did not read them" is not an option
6. **What it lands on** — questions now answered, plans, traceability rows and open
   runs affected, contradictions with built code, schedule impact
7. **What was captured** — new file names, and the Part 3 section added

---

---

## `all` — every source, verdict first

When the question is just "is everything current" — and as a weekly sweep:

1. **Run the phases above for every source in `requirements/read-me.txt` that has a live
   location** — a URL, a shared-drive path, or `sent by email` — not a subset picked in advance.
   Sources marked `none` have nothing to check; list them as skipped.
2. **Lead with one verdict line**, before the report:
   - **`UP TO DATE`** — every source was reached and matched its snapshot; nothing moved.
   - **`STALE — <n> source(s) moved`** — name them.
   - **`COULD NOT VERIFY — <n> source(s) unreachable`** — name them and why. **Never round this
     down to `UP TO DATE`** — an unreachable source is an unanswered question. A `sent by email`
     source nobody confirmed counts here.

   Sources in more than one bucket: lead with both, the worse first.
3. **Then the full report**, as the phases produced it — the verdict is a top line, not a
   replacement.

## The rule that matters most

**Not knowing is not the same as no change.**

If you could not open a document, the honest report is "could not check", and it is
worth more than a reassuring one. The whole point of this skill is that these sources
move silently — a false all-clear reinstates exactly the problem it exists to solve.
