## Step: inventory

**Classify, do not design.** The run's **Outline** section is the FSD's every section, read by the
referee from the document's own headings; each section's own text is in the file the brief names
(read the FSD there — a `.docx` cannot be read directly). The project:

{brief}

Write the **Inventory** section: one line for every section at the feature depth — and every
shallower section with nothing below it — in the outline's order, each with exactly one
disposition after ` — `:

- `- §2.5 <title> — feature` — a unit of work with its own behaviour.
- `- §2.5.3 <title> — part-of <feature>` — only where the FSD itself says it belongs to that
  feature; `<feature>` is the title of a `feature` line.
- `- §2.9 <title> — cross-cutting` — a rule every feature follows (formatting, auditing).
- `- §1.2 <title> — not-a-feature (<reason>)` — the reason from the closed list:
  introduction, glossary, reference, front-end only, out of scope, superseded.
- `- §2.14 <title> — empty → Q<n>` — a heading with no requirement under it. Raise one closed
  question in `docs/open-questions.md`, in plain words, with a recommendation, and name its id.
  The referee fails a question with no recommendation: it is what gets built unless the answer says
  otherwise.

A section that has text but is unclear, or contradicts another, keeps its disposition; raise a
question for it the same way — closed, with a recommendation — and name its id after the line's
disposition (`— feature · Q<n>`).

The title is the outline's, word for word. No feature name that is not a heading; no scope from
general knowledge of such systems; no dates.
