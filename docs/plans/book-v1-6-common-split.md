# Book v1.6 — The common parts, written once

**Status:** `In progress` · **Owner:** Boilerplate owner · **FSD:** — · **Checked against:** — · **FE guide:** — · **Depends on:** Book v1.5

A Book's instruction files, its settings file and its tooling each mix two things: the rules, defaults and code
every Book shares, and what is the project's own. Until v1.5 both lived in one file, so a project reworded the
common text, upgrades could only report a project-changed file and never update it, and every Book drifted
from the others. v1.6 splits them: **the common parts live in Book-owned files the project never edits**, the
project's files keep only what is its own, and an upgrade replaces the Book's files as plain copies (§1–§6).
It also gives the ledgers fixed fields a check can count — a question's state (§7), a defect's Type and Source
(§8) — and replaces the hand-written traceability audit with a requirements register kept current at every
commit (§9). Its row is in [`../project.md`](../project.md#part-1--features-and-phasing).

**Nothing of this is in the plugin.** Git hooks and CI do not load plugins, and the guard's deploy rule must
never be something a project can switch off: the common files are copied into the repository, as the checks
and the guard are today. The plugin keeps the skills only.

## 1. The instruction files import the Book's rules

- **The Book's common rules are in `.claude/book/`**, one file per instruction file: `rules.md` (the root
  `CLAUDE.md`'s), `docs.md` (`docs/CLAUDE.md`'s), `tests.md` (`tests/CLAUDE.md`'s), and a pack's own beside
  them — `fe-guides.md` and `deploy.md` (their rows and rules for the root), and the dotnet pack's
  `dotnet/src.md`, `dotnet/api.md`, `dotnet/persistence.md` and `dotnet/tests.md` (its nested files'). They are
  S files: copied with the project's names, never edited in the project.
- **Each `CLAUDE.md` imports them** with Claude Code's `@path` import, on a line of its own under the title,
  with a plain link beside it for a reader or a tool that does not expand imports. It keeps only the
  project's own rules.
- *Why `.claude/book/`:* the guard already asks before any change under `.claude/**` and `[ai_files]` owners
  approve it, so the common text keeps the protection `CLAUDE.md` has; `upgrade` never writes `docs/` (the
  project's content), so a rules file there could never be updated; and `.claude/rules/` loads every file at
  launch, wherever the session works, so the docs rules would load for code work too.
- **Claude Code's imports fit** (the memory documentation): a relative path resolves from the importing
  file, so a nested file imports `@../.claude/book/docs.md`; an import inside the working folder needs no
  approval; the limit is four hops and the Book uses one; a nested `CLAUDE.md` loads its imports when it
  loads; an `@` inside a code span is not an import. Imports do not save context — the rules load as
  before, from one owner.
- **The checks read the instruction files with their imports expanded**: check-book's owner table and its
  defect classes, check-rules' enforced markers and verify's N6 and N7. Verify's N6 also fails a root or
  nested `CLAUDE.md` that has lost its import.

## 2. The settings merge over the Book's defaults

- **The Book's defaults are `devtools/factory/book-defaults.toml`** (every Book), and a pack's
  `devtools/factory/book-defaults/<pack>.toml` (dotnet, pytest, deploy, fe-guides). They hold no project
  name; an S file, never edited in the project.
- **`devtools/factory/factory.toml` keeps the project's own settings and exceptions only**: its names,
  branches, owners, Jira, paths, and every value it sets differently from the defaults.
- **One loader, `devtools/factory/book_config.py`**, gives every reader the merged view, in this order: the
  core defaults, the stack pack's, the other packs' in `[book] packs` order, the project's file. Then the
  private settings, as before, for the commands that read them.
  - **Tables merge per key**, all the way down; a scalar the project sets wins.
  - **A list the project sets replaces the default list.** To add to a default list without restating it —
    and so still get what a later release adds — the project writes the additions under `[extend]`, at the
    same path: `[extend.guard] ask = ["tools/**"]` appends to `[guard] ask`, without duplicates.
  - **A list of tables that carry an `id`** (`[[code_rules]]`) merges by id: a project entry with a default's
    id changes only the keys it sets (its `allowed` list, say); a new id is added after the defaults.
  - A missing defaults file is no error for a Book older than v1.6; a defaults file that is not valid TOML is,
    and every reader says so.
- **Every reader uses it**: `book.py` and `factory.py` (all commands; `book config` prints the merged value,
  and `--json` prints a list or table), every check under `devtools/checks/` and the packs', the guard (which
  fails closed if the loader cannot load), the hooks and the local CI (through `book config`), and the
  pipelines' steps that read the destination branch's settings (`book ai-files`, check-protected-paths at a
  ref, which merge the defaults as that ref holds them). check-code-rules' `--staged` merges the staged
  copies. The plugin's skills and scripts read it through `book config` or the Book's loader (plugin 0.7.0,
  §6).

## 3. A project's code edits become settings

The checks, the guard and the tooling are the Book's code, identical in every project. Where a project edited
its copy to fit, the edit becomes a setting, so the code is the Book's again:

| Edit | Setting |
|---|---|
| A rewritten snapshot accepted on one commit (check-protected-paths' `ACCEPTED`) | `[guard] accepted_rewrites = [{ commit, path, reason }]` |
| An extra placeholder a Compose render needs (check-compose's `QA_PLACEHOLDERS`) | `[deploy.compose_placeholders]`, `NAME = "value"` |
| The test database's name and the extra packages of the daily run (ci-daily.sh) | `[ci_daily] test_database`, `apt_packages` |
| A module's heading in the ERD, and the ERD's tenancy wording and notes (gen-database-erd.py) | `[database_erd] titles`, `scoping`, `notes` |
| An archived question kept under a `##` heading (check-book) | none: check-book accepts both shapes |

- **The deploy guard's DEV/QA-only rule stays in the guard's code and is never a setting**: no key in any
  settings file names the environments a deploy may reach.
- **A test fixture names no project**: the Book's tests use generic fixtures or the settings. A project's
  tests of its own settings go in its own test files, never in the Book's.
- An edit that is a whole script of the project's own — its local CI, its pipeline, its Postman post-processing
  — is no setting: the project keeps its file and names it in `[book] declined`, as it can today.

## 4. Drift fails the build

- **`devtools/factory/book-files.lock`** lists every file the Book owns in the project with the hash of the
  Book's copy (line endings aside). `book init` and `book upgrade --apply` write it, from the template; a
  person never does.
- **check-book fails** (pre-commit and CI) **on a Book-owned file that differs from the lock**, naming it and
  saying how to move the change: into `factory.toml` (the setting), into the project's own file (its
  `CLAUDE.md`, its own script), or into `[book] declined`. Verify reports the same as N21, and a missing
  lock in a v1.6 Book.
- **`[book] drift_accepted = [{ file, reason }]`** holds the differences a project has not moved yet: check-book
  lists them and passes, verify reports each as a gap (READY WITH GAPS), and an entry whose file now matches
  is WRONG. The 1.6.0 upgrade writes one entry per file the project had changed, so a project's build never
  breaks on taking the release, and every difference is in view until it is moved.

## 5. The upgrade from 1.5 migrates the project

Report first, as ever; `--apply` writes.

- **`CLAUDE.md` files**: for the root, `docs/`, `tests/` and the dotnet pack's nested files, upgrade rebuilds
  the installed version's text with the project's names, and moves every paragraph, bullet and table row of
  the project's file that matches it into the import: it deletes them and adds the import line. Headings left
  with nothing under them go too. The rest is kept, in place. A CONFIRM note names each section the project
  words differently from the Book, for a person to compare with `.claude/book/`.
- **`factory.toml`**: upgrade drops every key equal to the merged defaults (with the template's comment above
  it), turns a default list the project only added to into an `[extend]` entry, and keeps the rest. It
  writes the result only when merging it over the defaults gives exactly the settings the project had;
  otherwise it writes nothing and says CONFIRM.
- **The Book-owned files the project changed** go into `[book] drift_accepted`, each named in a CONFIRM note with
  how it differs (comments only, or code).
- **The lock** is written, and the 1.6.0 upgrade notes say what a person does by hand.

## 6. The plugin, 0.7.0

The plugin's skills and scripts read the merged settings: `next_id.py` and `guides_check.py` through the
Book's loader (and the plain file for a Book older than 1.6); the skills that read a value (`[book] packs`,
`[book] recommendation_heading`, `[deploy]`, `[fe_guides]`) through `python devtools/book.py config <key>`.
Template 1.6.0 pins it: `plugin_min` and `ref` are 0.7.0. **Push the plugin's tag before the boilerplate's.**

## 7. A question's state

A question for the BA moves through four states, and the ledger says which one each question is in, so the
team can see what to post, what to chase and what to close.

| State | Means | Where it lives |
|---|---|---|
| `OPEN · NOT POSTED` | Raised here, not yet asked on the FSD | `open-questions.md`, *Still open* |
| `OPEN · POSTED` | Asked as an FSD comment, waiting for the BA (or a reply that does not settle it) | `open-questions.md`, *Still open* |
| `ANSWERED · THREAD ONLY` | Settled in the thread, not yet in the FSD text: we ask the BA to add it and keep the thread open; the code follows the answer | `open-questions.md`, *Answered, waiting for the FSD text* |
| `RESOLVED` | Settled in the thread and in the FSD text; the thread may be resolved | `archive/questions-resolved.md`, with its one line under *Resolved* |

- **Each question's section carries one line of fields**, under its heading: `**State:** <state> · **Posted:**
  <date, thread link or anchor> · **Answered:** <date, who by role> · **In FSD text:** <V<n> §x, or not yet> ·
  **Thread resolved:** yes/no`. A field that does not apply yet is `—`. The `[book] recommendation_heading`
  line stays as it is.
- **check-book** fails a state that is not one of the four, a POSTED or later question with no Posted date, an
  ANSWERED one with no Answered field, a RESOLVED one without an *In FSD text* value or still in the live file,
  and an ANSWERED · THREAD ONLY one outside its section. A question with no State line only warns: the older
  ledgers keep working until `/book:check-sources` assigns states.
- **The roll-up** (`project.md`, by gen-task-rollup) counts the open questions per state.
- **The skills** (plugin 0.7.0): `/book:raise` starts a question as NOT POSTED; `/book:check-sources` moves
  each one on (posted when its thread appears, THREAD ONLY when the BA answers there, RESOLVED when the live
  text says it) and ends with three lists — comments to post, threads to ask to add to the text, threads to
  resolve — posting nothing without the person's word. Planned for plugin 0.8 (task 11): `/book:sync-sources`
  moving the States the same way, and `/book:progress` and `/book:flow` giving the counts and the lists.
- **The upgrade** never rewrites the ledger: a CONFIRM note says to run `/book:check-sources` once, which
  proposes each question's state for the person to approve.

## 8. A defect's Type and Source

The *Found by* column and the *Class* note give way to two fixed fields, so "what kind of defects, found by
whom" is a count.

- **The table is `| Id | Sev | Status | Type | Source | Defect | Where | Note |`**: two short columns beside the
  status, where the eye already looks; *Found by* goes, its detail becomes the Source's reference.
- **Type**, one of: `spec-divergence` (the code does not match the FSD) · `bug` · `regression` · `test-gap` ·
  `security` · `data` (wrong or missing data, or a migration) · `config-infra` (deploys, servers, settings) ·
  `tooling-ci` (the Book's tools and the pipeline).
- **Source**, one of: `qa` · `fe` · `ba-product` · `requirement-sync` · `code-review` · `audit` · `tests-ci` ·
  `developer`, written `<source>: <reference>` — the Jira id, the FSD version or section, the PR, the Q id or
  the audit row.
- **check-book** fails an Open, Mitigated or Partial row with no Type or Source, or with a value off the lists.
  A row from before the columns (the old shape, or `found` before `[book] type_source_from`) only warns, and
  the archive's rows and the one-line *Fixed* entries are exempt. `[book] class_enforced_from` and
  `found_by_from` keep judging the archive's older rows as they do.
- **The roll-up** counts the open defects by Type and by Source.
- **The skills** (plugin 0.7.0): `/book:raise` writes the Type and Source where the table has the columns and
  shows them before writing. Planned for plugin 0.8 (task 11): the sources skills logging `requirement-sync:
  FSD V<n>` (usually `spec-divergence`), the review skills `code-review: PR #<n>`, `/book:fact-check` and
  `/book:check-docs` `audit`, and `/book:progress` giving the counts. Until then check-book still fails a
  row whose Type or Source is off the lists, whoever wrote it.
- **The upgrade** never rewrites the ledger: `book defects classify` proposes a Type and Source for each open
  row from its text and its *Found by*, report-first; `--apply` writes the two columns only after the person
  has read the proposal. A CONFIRM note names it.

## 9. The requirements register

The FSD's requirements, one row each, kept current while the work is done and checked at every commit, in
place of a hand-written audit that goes stale between runs.

- **`docs/requirements/<module>.md`**, one file per module, in the order the projects' audits already use
  (Global first). Each holds one table — `| Id | FSD | Requirement | Plan task | Tests | Status | Verified at |
  Note |` — and a small *Untraced behaviour* table (`| Id | Code | What it does | Status | Note |`) for code no
  requirement asks for.
  - `Id` is stable and never reused (`FSD-2.7.1-R03`, or a project's own scheme such as `FSD-HEADER-R02`: the
    check needs only uniqueness and a prefix of `[requirements] id_prefix`); `FSD` is the section with the
    version first seen and last changed (`§2.7.1 · V11–V26`); `Verified at` is `<commit> <date>`.
- **Statuses**: `NOT_BUILT` · `BUILT` (no proving test yet) · `VERIFIED` · `CONTRADICTED` (names its defect) ·
  `NOT_BACKEND` (the front end or the tablet only) · `DEFERRED` (names a release or a reason) · `REVERIFY` (the
  FSD changed since *Verified at*). Untraced rows are `UNTRACED` or `ACCEPTED` (with the decision).
- **Tests name what they prove**: the dotnet pack's tests may carry `[Trait("Requirement", "<id>")]`, which the
  checks read from the source without running anything. A VERIFIED row needs a test that names it, or a Tests
  cell that names one.
- **At commit** (check-book): the shape and the statuses; ids unique; an id a test trait, a plan or a defect
  names exists; CONTRADICTED names a defect; DEFERRED a reason. **In CI and verify** (check-requirements):
  each VERIFIED row's named tests exist in the source; REVERIFY rows are listed as a warning; the coverage per
  module (verified of the backend's). A register converted from an old audit warns, rather than fails, on
  what its data lacks (a VERIFIED row with no test name), so a backlog never blocks a commit.
- **`project.md`'s roll-up** counts the register per module and status. **`docs/Audit/spec-traceability.md`
  is retired**: the conversion moves it whole into `docs/archive/spec-traceability-<date>.md` and leaves a
  one-line stub. *Why:* a generated copy of the register's counts would be a second owner of one fact; the
  roll-up already shows them, and the register is the record.
- **The conversion, once**: `book requirements import` reads an old audit (report-first, `--apply` writes),
  keeps every id, and maps `VERIFIED (observed)` → VERIFIED (its evidence kept in Note), `VERIFIED (traced)` →
  VERIFIED when its Test evidence names a test and BUILT when not, `NOT_APPLICABLE` → NOT_BACKEND, `PARTIAL`
  → BUILT, `MISSING` → NOT_BUILT, `CONTRADICTED` → CONTRADICTED (its defect from the row, or flagged),
  `UNVERIFIED` and `UNTRACED` → BUILT flagged for review; a status cell that keeps its history
  (`MISSING → VERIFIED (observed)`, or a dated update ending `— CONTRADICTED (traced)`) takes its last move, and
  keeps the history in Note. The long evidence goes to a per-module appendix
  (`docs/requirements/evidence/<module>.md`), so none is lost. The 1.6.0 upgrade names it in a CONFIRM note.
- **The skills** (planned for plugin 0.8, task 11; plugin 0.7.0 has none of them): `/book:sync-sources` adds
  a new version's requirements as NOT_BUILT and marks changed ones REVERIFY; `/book:plan` and
  `/book:outline-api` fill *Plan task*; `/book:self-review` checks the rows a branch touches;
  `/book:fact-check` and `/book:check-docs` verify rows and set *Verified at*; `/book:raise` links a
  CONTRADICTED row's defect; `/book:progress` reports the coverage.

### 9.1 Next, after 1.6.0

Built in 1.6.0: the register files and their statuses, check-book's commit checks (shape, statuses, unique
ids and the id prefix, CONTRADICTED naming a defect, DEFERRED and ACCEPTED a reason, VERIFIED a test; a
converted register only warns of a VERIFIED row with no test), the roll-up's counts per module, and
`book requirements import` with its upgrade CONFIRM note. Left for the next release, because each needs the
register in use first:

- **check-requirements** in CI and verify: each VERIFIED row's named tests exist in the source, and the
  dotnet pack's `[Trait("Requirement", "<id>")]` scanned without running anything; an id a trait, a plan or a
  defect names exists.
- **The skills** (plugin 0.8): `/book:sync-sources` adds and marks REVERIFY, `/book:plan` and
  `/book:outline-api` fill *Plan task*, `/book:self-review` checks the rows a branch touches,
  `/book:fact-check` and `/book:check-docs` set *Verified at*, `/book:raise` links a CONTRADICTED row's
  defect, `/book:progress` reports coverage.
- **A delta-pass section of an old audit** (rows added by a later pass under their own heading) converts to a
  register file of its own, which the import names in a CONFIRM line; merging it into its module is a
  person's step for now.

### 9.2 Planned for the next minor release: project checks in the hooks

A project cannot add its own check to the pre-commit hook, `ci-local.sh` or CI without declining those
files (`[book] declined`), which then stop receiving the Book's updates (defect D97, a known limit of 1.6).
Planned: a settings hook, for example `[checks] project_pre_commit`, `project_ci_local` and `project_ci`,
each a list of commands the Book's own files run after the Book's checks, so a project adds a check
without owning the file.

## 10. Tasks

| # | Task | FSD | Role | Status | Jira | PR | Merged |
|---|---|---|---|---|---|---|---|
| 1 | The settings loader, the defaults files, and every reader on the merged view (§2) | — | BE | Built | — | — | — |
| 2 | The rules files and the imports, the checks reading them expanded (§1) | — | BE | Built | — | — | — |
| 3 | The projects' code edits as settings (§3) | — | BE | Built | — | — | — |
| 4 | The lock and the drift check (§4) | — | BE | Built | — | — | — |
| 5 | The 1.5 to 1.6 migration in `book upgrade`, and its notes (§5) | — | BE | Built | — | — | — |
| 6 | Plugin 0.7.0 on the merged view (§6) | — | BE | Built | — | — | — |
| 7 | Push plugin tag v0.7.0, then release boilerplate v1.6.0 | — | BE | Not started | — | — | — |
| 8 | A question's state: the ledger, check-book, the roll-up, `/book:raise` and `/book:check-sources`, and the upgrade note (§7) | — | BE | Built | — | — | — |
| 9 | A defect's Type and Source: the ledger, check-book, the roll-up, `book defects classify`, `/book:raise` (§8) | — | BE | Built | — | — | — |
| 10 | The requirements register: the files, the checks, the roll-up, the conversion (§9) | — | BE | In progress | — | — | — |
| 11 | Plugin 0.8: the question States, Type and Source and register work in the other skills (§7, §8, §9.1) | — | BE | Not started | — | — | — |
