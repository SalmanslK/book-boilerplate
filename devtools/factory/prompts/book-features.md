## Step: features

Write `docs/project.md`'s Part 1 from the **Inventory** — nothing else. The project:

{brief}

- **Keep the file's shape.** The parts already filled — *What the work needs*, the generated
  blocks between their `<!-- …:begin -->` and `<!-- …:end -->` markers, Parts 2 and 3 — stay as
  they are.
- **One row per `feature` line** under `### Phases`, in tables of exactly these columns:
  `| Feature | FSD | Plan | Owner | Depends on | Status |`. FSD cites `§<id> <title>` with the
  outline's exact title; Plan is empty (plans are written on demand); Owner is `—` until the
  book gate; Status is `Not started`, or `Blocked — Q<n>` for a feature whose sections are empty.
- **Phases** `#### Phase <n> — <name>`, numbered from 0. A feature's phase follows from what the
  FSD says it depends on; end each phase's table with one sentence quoting, in double quotes and
  word for word, the FSD clause that forces the order. Phase 0 holds only the foundation features
  named above.
- **Depends on** — feature names from the rows, `Q<n>` ids, or `nothing`; never a later phase; no
  cycles. Draw the `### Dependency map` from the same edges.
- **`### Sections that are not features`** — every `cross-cutting` and `not-a-feature` line, as
  `| FSD | Why it is not a feature |`.
- **A `part-of` section** is cited in its feature's FSD cell too, joined with `;`
  (`§2.4 Orders; §2.5 Returns`) — every section at the feature depth is cited by a row or listed
  as not a feature, never both.
- **No dates** unless a source gives them, cited. A gap is a question, not a guess.
- **No `<!-- FILL` left anywhere** — the referee fails the file on one. *Where things stand* is a
  short current-state table: nothing built yet, which FSD revision is current, what is blocked on
  which question. Delete the delivery-schedule comment when no source gives dates.
- **Quote from the FSD's text file** the brief names — it is exactly what the referee compares
  your quotes with.
- **Reopened after the book audit?** The reason below lists the auditor's findings, and the run's
  *Audit* section holds its whole report. Answer each one in project.md; where a finding is wrong,
  say why in the run's **Features** section, quoting the FSD.

- **The system overview** — where `docs/system-overview.md` is still the template (it holds
  `<!-- FILL`), write it from the FSD too: what the product is, the vocabulary, the users and roles, the
  main flows — each from the sections that say so, citing them, nothing from general knowledge. A
  project without the file is not asked for one. The referee fails a template left in place.

Write a short **Features** section in the run file: the phases, the rows, the questions the file
names.

Then regenerate the roll-up (`python3 devtools/checks/gen-task-rollup.py`), commit, and check.
Before you check, read your file against the referee's seven rules: shape, citations, coverage,
honesty, order, dates, questions.
