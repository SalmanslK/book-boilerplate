# Plans history

Closed history, written once and not maintained. **Nothing here is a current rule.** Which live
document each archive file belongs to is the table in [`docs/CLAUDE.md`](../CLAUDE.md#current-state-not-history).

## Fixed

## Book v1.5 — what still called `factory` when the alias went (2026-11-22)

v1.4.0 left these on the old name on purpose, because a project on an older Book still ran it; v1.5 switched
each to `book`: the bitbucket pipelines' AI-files step (its `$DEST:devtools/factory/factory.py` test) and
`pr-check` step, the plugin's `setup` skill (`factory.py book init --target`), about 25 of `deploy.py`'s
messages, the upgrade's report header, `fixture_project.py`'s comments, the plugin's fallback note in every skill
with its allowed-tools in both forms and the publish script's fallback, `factory.py`'s rename note, `book.py`'s
fallback to a `main()` that takes no name, and the code comments and docstrings that named a command
`factory <command>`. The guard's deploy check gained the script run directly and `uv run`.
