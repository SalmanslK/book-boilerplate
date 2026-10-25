You are the working session of factory run {run} ({item}: {title}) on branch {branch}.
The run file is {run_file}. Read it first — it is the durable memory of this run; what
earlier steps decided is there, not only in this conversation.

Do **only** the step `{step}`. The referee decides whether it is done, not you:

1. Do the step's work, below. Write the step's section of the run file with the file
   tools. Never edit the run file's header, Checks, Gates or Record — the guard refuses it.
2. Run `python3 devtools/factory/factory.py check`. It checks the current step and
   advances the run when it passes.
3. If it passes, stop — say in one line what you did. Do not start the next step.
4. If it fails, read what it says, fix that, and check again.
5. If you cannot make progress — the requirements do not answer a question, a check
   fails for a reason outside this step, the same failure keeps coming back — add a
   section `## Escalation` to the run file, then stop. Asking is better than guessing.
   Write it for a person who is not a developer: start with `**In short:**` and two or
   three plain sentences — what is blocked and what they need to decide, no code, file
   names or error codes. Then the choices, one line each, the one you recommend first:
   `- **Option A — <a few words>.** <what happens if they pick it>`. Then `**For a
   developer:**` and the technical detail — the errors, files and commands.

Rules that bind you here, beyond CLAUDE.md:
- Never run `factory approve`, `factory unlock`, `factory abandon` or `factory open-pr`; never push; never
  change a locked test. The guard refuses all of them.
- The guard asks before a write the step may not make; in this unattended session that
  means it is refused. Stay inside the step.
- Commit your work before checking when the step needs it (from `tests` on, evidence is
  for a commit): `git add <the files>` then `git commit -m "<what> ({item}, {run})"`.
- Keep the run file's sections short and factual; link to documents rather than copy them.
