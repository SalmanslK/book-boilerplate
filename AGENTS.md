# Agent instructions (Boilerplate)

This repository's authoritative assistant rules live in [`CLAUDE.md`](CLAUDE.md).

**Follow `CLAUDE.md` for every session** — layout, git and branch rules, docs ownership, security and
testing. Feature state is not in `CLAUDE.md`; it lives in [`docs/project.md`](docs/project.md).
If this file and `CLAUDE.md` disagree, `CLAUDE.md` wins. `docs/` and `tests/` each have their own
`CLAUDE.md` with that folder's rules. A stack pack adds more: the dotnet pack's `src/`,
`src/Boilerplate.API/` and `src/Boilerplate.Persistence/`. `.cursor/rules/` points Cursor at them by glob.

## Skills and agents

The Book's skills and its two agents (`reviewer`, `tester`) come from the **book plugin**, called
`/book:<name>` in Claude Code. The project's own skills, a pack's among them, are under
[`.claude/skills/`](.claude/skills/), which Cursor also loads — do not copy them under `.cursor/`.

Each skill says when to use it in its own description; how the documentation skills differ is
[the AI guide's table](AI-DEVELOPMENT-GUIDE.md#which-docs-skill) — not repeated here.

## Tooling note

- **Claude Code** loads `CLAUDE.md`, `.claude/skills/` and `.claude/agents/` directly.
- **Cursor** loads this file / `CLAUDE.md` (via rules) and `.claude/skills/`.
- Do not invent a second rulebook under `.cursor/` — point at `CLAUDE.md`.
