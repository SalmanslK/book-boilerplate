# Docs — rules for writing documentation

Loaded when you work under `docs/`. The Book's rules for writing documents come first, the same in every
project, imported from `.claude/book/`; the boilerplate's own follow them.

@../.claude/book/docs.md

# The boilerplate's own rules for its documents

- **A general docs refresh goes to `main`**, on a `chore/docs-<slug>` branch cut from `origin/main`.

## Requirements (`requirements/`, read-only)

- **The FSD wins every conflict.** A design showing something the FSD does not specify
  is not a requirement; where the FSD is silent, the supporting documents are the best
  evidence (the specification itself); where it
  contradicts itself, ask. The table is
  [Which document wins](open-questions.md#which-document-wins).
- **The FSD and the QA artifacts are live Google documents; our copies
  are snapshots.** They move without notice, and the BA answers in the FSD Doc's
  comment threads. A question that looks unanswered may have been answered since the
  last snapshot — run `/book:check-sources`.
- **Permissions** come from the specification.
- **QA artifacts are not requirements.** A rule found there is a question until the
  FSD confirms it.
