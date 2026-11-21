# Open questions — what the requirements do not settle

**Status:** `Live` · **Owner of the record:** whoever raises the question

## How to use it

Every open question carries a **Recommendation:** line, which is what gets built unless the Answer says
otherwise. Status is one of `OPEN` · `ANSWERED` · `SUPERSEDED`. An answered question moves to
[`archive/questions-resolved.md`](archive/questions-resolved.md), leaving one line under *Resolved*.

### Which document wins

| Situation | What we do |
|---|---|
| A supporting document contradicts the specification | **Build the specification.** Log the disagreement so the other document gets corrected |
| A design shows something the specification does not specify | **Do not build it.** Ask for the specification to be amended |
| The specification is *silent* | Use the supporting documents; they are the best evidence there is |
| The specification contradicts **itself** | Precedence does not help. Ask |

**Status key:** `OPEN` · `ANSWERED` (a rule is in force — see [Resolved](#resolved)) · `SUPERSEDED`
(overtaken by another question). Nothing else.

## Still open

### Anyone

None open.

### The open questions in full

Each one is a `#### Q<n> · <title>` heading, then `**Status:** OPEN · **Raised:** <date> · **Area:** <§>`,
the question in plain words, and a `**Recommendation:**` line.

## Superseded

None.

## Resolved

One line per decision, by topic, stating the rule in force. Decided by the owner with the v1.0.0 build
plan; the reasoning is in [`architecture.md`](architecture.md).

### Layout and naming

- **Where the Factory's referee lives:** in a Book + Factory install the kit puts it at
  `devtools/ai-factory/factory.py`; the Book CLI is `devtools/book.py` (the `book` command), with
  `devtools/factory/factory.py` working as `factory` until v1.5.
- **The boilerplate's own stack is `none`:** its only code is the Python tooling, tested by
  `run-self-tests.py`.
- **One version stamp:** `book_version` (semver) in `factory.toml` and `manifest.toml`.
- **Branch model:** `book:setup` asks; the template's default is `dev → qa → uat → main`; the boilerplate
  itself uses `main` only.

### Requirements

- **The requirements index keeps the `LINKS` blocks** (`requirements/read-me.txt`), which
  `book fsd-threads` and `book verify` parse; structured records may follow in a later minor version.
- **The boilerplate's specification is its own `requirements/fsd/`**: V1 described the referee; V2
  describes the Book. Snapshots are never edited — a revision is a new file.

### Packs and hosts

- **Packs in v1.0:** `dotnet`, `pytest`, `deploy`, `bitbucket`, `fe-guides`. React and GitHub later.
- **Git host:** Bitbucket or none — `pr`, `ai-files` and `protection` report `N/A` without a Bitbucket remote.
- **Pre-commit is one static file**; each pack's line runs only where the pack installed its script (`[ -f ]`).
- **`docs/ui-standard.md` is an optional owner skeleton in the `fe-guides` pack.**
- **The dotnet pack's architecture rules** are kept only where the pack ships the test class that
  enforces them; any other rule is not marked *[enforced]*.
- **Slowest test classes:** the dotnet pack ships `slowest-test-classes.py`.

### Agents and owners

- **The Book's agents ship in the book plugin** (`book:reviewer`, `book:tester`; project-level copies
  in `.claude/agents/` until v1.3.0); a person's own user-level agents stay theirs.
- **`[ai_files] owners`:** the boilerplate's owner fills the account id; `ai-files` reports `N/A` without
  a Bitbucket remote, never `OK`.
