## Step: context

Run `python3 devtools/factory/factory.py impact --write <the Touches paths>` — it fills
the Context section with the modules, test filters, guides, plans and requirement ids.

Then follow `.claude/skills/change-impact/SKILL.md` step 2 and add, below that output,
under a line `**Read with judgement:**`: whether the item has a requirements angle (and
which requirement or section), callers in other modules, the public contract (API,
CLI or UI), permissions, schema, QA cases, and open defects and questions that touch it.
Say "none" rather than leave a line out.
