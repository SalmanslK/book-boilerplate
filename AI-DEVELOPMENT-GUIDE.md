# AI-assisted development on the Book Boilerplate

A map of how the team works with Claude Code here — not a second rulebook. The rules are
[`CLAUDE.md`](CLAUDE.md) and the nested files; where this guide and they differ, they win.

## Summary

- **The repository teaches the assistant.** Every session reads `CLAUDE.md`; a nested `CLAUDE.md` loads in
  its folder. A rule that matters is written there, and where a check can hold it, marked *[enforced]*.
- **Plan before code.** A feature's plan in `docs/plans/` and its row in `docs/project.md` come first.
- **The guard** refuses in-session what the rules forbid; a wrong refusal is a guard bug to report.
- **Skills** are the habits; **agents** are second pairs of eyes. Neither replaces a person at a gate.

## Skills

Each skill says when to use it in its own description. The flow of a change:
`/book:impact` → plan → code and self-tests → `/book:self-review` → `/book:commit` → `/book:ship` (the local CI, the
description, the pull request when the person asks) → `/book:peer-review` by a teammate.

### Which docs skill

Documentation skills differ by **what they compare**:

| Skill | Compares | Posture |
|---|---|---|
| `/book:check-sources` (`/book:check-sources all` for every source) | our snapshots ↔ the client's live documents | Run first when requirements are in play; "could not check" is a valid result |
| `/book:check-consistency` | docs ↔ each other | Reports before changing |
| `/book:check-docs` | docs ↔ code, neither presumed right | Reports before changing; owns `references/comparison-checklist.md` |
| `/book:update-docs` | docs ← code you already trust | Applies changes |
| `/book:fact-check` | specification ↔ plan ↔ code ↔ tests | Evidence or `UNVERIFIED`; writes only `docs/Audit/` |
| `/book:trim` | docs ↔ their own size | Trims and archives; reports before changing |
| `/book:plan` | a new plan ← the FSD | Writes the draft; the person approves it |
| `/book:raise` (`defect` or `question`) | one ledger entry ← a finding | Writes the row; never posts an FSD comment unasked |
| `/book:write-for` | a short message ← the Book, for one non-engineer | Drafts only; never sends |
| `/book:upgrade` | the Book ↔ a newer template | Reports, applies on approval |

### The names before v1.3.0

v1.3.0 moved the Book's own skills and agents into the **book plugin** (each called `/book:<name>`) and
renamed some. An old name still in a doc, a chat or a habit means the new one here. For v1.3.x a stub
under each old name says the same and does nothing else.

| Before v1.3.0 | Now |
|---|---|
| `/requirements-check` | `/book:check-sources` |
| `/requirements-sync` | `/book:sync-sources` |
| `/api-contract-draft` | `/book:outline-api` |
| `/change-impact` | `/book:impact` |
| `/project-progress` | `/book:progress` |
| `/review-branch` | `/book:self-review` |
| `/review-pr` | `/book:peer-review` |
| `/commit`, `/ship`, `/raise`, `/write-for` | `/book:commit`, `/book:ship`, `/book:raise`, `/book:write-for` |
| `/fsd-audit` | `/book:fact-check` |
| `/docs-vs-code` | `/book:check-docs` |
| `/docs-update-from-code` | `/book:update-docs` |
| `/docs-consistency` | `/book:check-consistency` |
| `/docs-trim` | `/book:trim` |
| `/book-setup`, `/plan-feature`, `/book-upgrade` | `/book:setup`, `/book:plan`, `/book:upgrade` |
| agents `book-reviewer`, `book-tester` | agents `book:reviewer`, `book:tester` |
| `/deploy-dev-qa` (deploy pack) | `/deploy` |
| `/fe-guides-publish`, `/fe-guides-check` (fe-guides pack) | `/publish-guides`, `/check-guides` |

## Agents

| Agent | Use it to |
|---|---|
| `book:reviewer` | Read a branch that changes the Book against its rules; read-only, no verdict |
| `book:tester` | Run and read the self-tests a change reaches; write a failing test first |

## Practical guidance

- One unit of work per session; "what is left" comes from `docs/project.md` and the plans' Tasks.
- Blocked means stop: say what was blocked, why, and what would unblock it.
- Never paste a token into a conversation; a tool that needs a credential holds it itself.

## Keeping this guide current

A new skill or agent gets a row here in the change that adds it.
