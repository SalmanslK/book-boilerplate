# Open questions — what the requirements do not settle

**Status:** `Live` · **Owner of the record:** whoever raises the question

Every question the requirements leave open. **Open ones are in full; each answered
one is a single line under [Resolved](#resolved), grouped by topic, stating the rule
now in force** — the question as asked and the answer in full move to
`archive/questions-resolved.md`. How to raise and close one:
[`docs/CLAUDE.md`](CLAUDE.md#questions).

**Add to it rather than resolving a question in a chat thread**, and record the answer
and date against the question so the reasoning survives. A factory run that meets a
question the requirements do not answer escalates; the question lands here, never in
the run's spec as a guess.

Sources: the FSD, `requirements/fsd/Book Boilerplate - V1.md` — the highest V<n> in `requirements/fsd/` is authoritative.

## How to use it

1. **Take the next `Q<n>`** — check the open branches as well as the base; ids are never
   reused.
2. **Add a summary row** under [Still open](#still-open) and **the question in full**
   under [The open questions in full](#the-open-questions-in-full).
3. **Write it in plain words, one closed question, no section numbers** — and **always
   with a recommendation**. Unless the **Answer** line says otherwise, the
   recommendation is what gets built, so disagreeing with one is as useful as answering
   it, and silence is not neutral.
4. **When it is answered:** move the full section to the archive and add one line under
   [Resolved](#resolved), in its topic, stating the rule now in force. Keep the heading
   text exactly, so its anchor still works.

**Status key:** `OPEN` · `ANSWERED` (a rule is in force — see [Resolved](#resolved)) ·
`SUPERSEDED` (overtaken by another question). Nothing else.

### Which document wins

**The requirements are the source of truth and override any conflict.** Supporting
documents assist; they do not override.

| Situation | What we do |
|---|---|
| A supporting document contradicts the requirements | **Build the requirements.** Log the disagreement so the other document gets corrected |
| A design shows something the requirements do not specify | **Do not build it.** Ask for the requirements to be amended |
| The requirements are *silent* | Use the supporting documents; they are the best evidence there is |
| The requirements contradict **themselves** | Precedence does not help. Ask |

---

## Still open

| | Area | Question | What we suggest | Blocks |
|---|---|---|---|---|

### The open questions in full

Format:

```
#### Q<n> · <the question, in plain words>
**Status:** OPEN · **Area:** <module or requirement> · **Blocks:** <what cannot be built until it is answered>

<the context: what the requirements say, and why it is not enough>

**Recommendation:** <what we would build, and why>

**Answer:** —
```

---

## Superseded

Questions overtaken by another; none carries a rule of its own.

---

## Resolved

**The rule now in force for every answered question, by topic.** One line each —
enough to build from. The question as asked, the answer and the reasoning are in
`archive/questions-resolved.md`.

Format:

```
#### Q<n> · <the question's heading, exactly>

`ANSWERED` · <date> · <who answered> — <the rule now in force>. [Full record](archive/questions-resolved.md#<anchor>)
```
