# Book Boilerplate

A starting point for any project that wants **the Book**: one set of documents that says what is being
built, why, how, and what is done, kept true by checks, and worked on by Claude Code under rules a person
approves. Copy it into a new project, or install it into an existing repository.

## The idea

- **The Book** is `docs/` plus `CLAUDE.md`. One owner document per topic (`docs/project.md` is the table of
  contents: phases, features, tasks). Requirements are read-only snapshots in `requirements/`. A plan comes
  before code. Defects and questions are logged the day they appear. Closed items move to `docs/archive/`.
- **The checks** (`devtools/checks/`) keep the Book honest: `check-book.py` (structure, no history in live
  text, links), `gen-task-rollup.py` (task progress is generated, never hand-edited), commit, path and
  secret checks. Git hooks in `.githooks/` run them on commit.
- **The skills** (`.claude/skills/`) are the habits: how to commit, review, audit, sync, report.
- **The factory** (`devtools/factory/`) is optional automation: a referee runs a piece of work step by step,
  a guard stops Claude from breaking the rules, and a person approves each gate.

## Start

```
python3 devtools/factory/factory.py --help          # needs Python 3.11+ and git
git config core.hooksPath .githooks                 # turn the hooks on (already set if you ran setup)
```

1. Replace the `<!-- FILL -->` slots in `CLAUDE.md` and `docs/` with your project.
2. Put your requirements document in `requirements/fsd/<Name> - V1.<ext>` (.md, .docx, .txt or .pdf).
3. Read `devtools/factory/factory.toml`. Every rule is there; agree with each.
4. Draft the Book from the requirements: `python3 devtools/factory/factory.py start book:project --new-branch`,
   then `factory drive`. A person approves it.

## Skills

| Skill | Use it to |
|---|---|
| `factory` | Run the referee: start, drive and approve a piece of work |
| `commit` | Review, then commit with a clean message; never on a protected branch |
| `review-branch` | Review your branch against the Book before a PR |
| `review-pr` | Review a pull request in two passes and post findings (Bitbucket today; adapt for your host) |
| `change-impact` | List what a change touches before making it |
| `requirements-check` | Check a change against the requirements |
| `requirements-sync` | File a new requirements revision and update the Book for what changed |
| `fsd-audit` | Trace every requirement to code and tests |
| `api-contract-draft` | Draft an interface contract from the plan, before code |
| `project-progress` | Report where the project stands against the Book |
| `deploy` | Deploy to a non-production environment and verify it |
| `docs-vs-code` | Find where docs and code disagree |
| `docs-update-from-code` | Update docs from code that changed |
| `docs-consistency` | Find contradictions between documents |
| `docs-trim` | Remove duplication and history from live documents |

## Any technology stack

The Book itself is stack-free. Only `devtools/factory/factory.toml` knows your stack, in three places:

- `[adapter]`: how tests are found, tagged with a requirement id, run, and their results read
  (`pytest`, `dotnet` and `react-vitest` ship in the kit; copy `[adapter]` and `[commands]` from the one
  closest to yours).
- `[commands]`: your build, test and verify commands.
- `[impact]` and `[[code_rules]]`: your source folders and any pattern you want the checks to refuse.

A new stack is one adapter block. Nothing else changes. This copy starts on the `pytest` adapter.

## Host

CI and approval wiring is the one host-specific part. `local-approval` (a person approves on their own
machine) works anywhere. `bitbucket-approval` and the `review-pr` skill assume Bitbucket Cloud; for GitHub
or GitLab, swap the check named in `factory.toml` and the API calls in `review-pr`.

## Licence

Add your own licence file before publishing.
