---
name: factory
description: >
  Use when someone wants to take a backlog item — a defect or question id, a ticket
  key, a requirement or spec section — through the AI factory: "run D3 through the
  factory", "start a factory run", "drive the run", "where is the run", "continue
  R-002", "what does the factory need from me". Starts or resumes a run, does the
  current step, and lets the referee (devtools/factory/factory.py) decide whether it
  passed; stops at every gate and tells the person exactly what to run. Never approves,
  unlocks or abandons — those are the person's. For what a change reaches without a
  run, use change-impact.
---

# Factory

The referee is `python3 devtools/factory/factory.py`; its steps, checks, tier rules and
this project's paths are [`devtools/factory/factory.toml`](../../../devtools/factory/factory.toml).
This skill is the same work `factory drive` does unattended, done in the person's own
session so they can watch and steer. **The referee decides, not this session.**

## 1. Find or start the run

- `python3 devtools/factory/factory.py status` — the open run on this branch and its
  current step.
- None, and the person named an item: the run needs its own branch. Cut one from the
  base (`base_ref` in `factory.toml`) **with `--no-track`**, so a push cannot land on
  the base: `git checkout --no-track -b <type>/<ID>-<slug> <base_ref>`, then
  `python3 devtools/factory/factory.py start <ID> --title "<what it fixes>"`.
  **One open run per branch** — the referee refuses a second.

## 2. Do the current step, then let the referee check it

Read the run file, then the step's instructions: `devtools/factory/prompts/<step>.md`
(the same text `factory drive` gives the unattended session). Do only that step.
Then `python3 devtools/factory/factory.py check`:

- **passed** — say so in a line, and go on to the next step only if the person wants to.
- **not yet** — fix what it lists and check again. The same failure twice with no
  change is no progress: stop and ask.
- A question the requirements do not answer — stop and ask the person; it goes to
  `docs/open-questions.md`, never into the spec as a guess.

To hand the rest to the unattended session instead: `python3 devtools/factory/factory.py
drive` (it stops at the next gate, and records what each step cost). Run it in the
background **exactly as written — no pipe** (`| tail`, `| head`, `| grep`): its progress lines
go to stderr a line at a time, and a pipe holds them all back until the drive ends, so the
person watches nothing for minutes. Read the background output file to report progress.

## 3. At a gate, stop and hand over

Say which gate, what the person is deciding, and exactly what to do:

- **spec gate / design gate** — *"Open a terminal that is not a Claude Code session
  (not one inside your editor's Claude panel either). `cd` to the repository, then run
  `python devtools/factory/factory.py approve spec` (or `approve design`). Tell me what
  it prints."* It refuses inside a Claude Code session, and the guard refuses it too —
  never try.
- **package → merge gate** — it depends on the merge gate's `check` in `factory.toml`:
  - `bitbucket-approval` (a project with a remote): the pull request is opened in the
    person's name — they push the branch, then run
    `python3 devtools/factory/factory.py open-pr` from their own terminal. Someone other
    than its author approves it in Bitbucket; then `factory check` reads the approval.
  - `local-approval` (no remote, no pull request): the person reads the evidence, then
    runs `python3 devtools/factory/factory.py approve merge` from their own terminal (or
    presses Approve in the Factory app, `Start Factory.cmd`); then `factory check`
    passes the gate. They merge the branch themselves.

## Never

- Never run `factory approve`, `unlock`, `abandon` or `open-pr`, and never edit a run file's header,
  Checks, Gates or Record — the guard refuses all of them.
- Never weaken or rewrite a locked test to make it pass. If it is wrong, say so and let
  the person decide whether to `factory unlock` it.
- Never commit while a `factory check` is running — its evidence records the commit.
