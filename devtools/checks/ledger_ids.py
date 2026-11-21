"""
The status of every question (Q<n>) and defect (D<n>), read from their ledgers, and the words a line uses
about one. One reader, imported by every tool that judges a citation: `/book:sync-sources`'s
`id_status.py` and `/book:check-guides`'s `guides_check.py`, both in the book plugin and loading this file
from the project they check. A copy in each would drift.

The ledgers, as docs/CLAUDE.md writes them:

  docs/open-questions.md               a `#### Q<n> —` (or `·`) section between "## Still open" and
                                       "## Superseded" is open; one under "## Superseded" is superseded;
                                       every other one, and every one in the archive, is resolved.
  docs/archive/questions-resolved.md   resolved.
  docs/defects.md                      a row in "## Open" carries its Status cell (Open, Mitigated,
                                       Partial, Blocked — …); a one-line entry under "## Fixed"
                                       (`- **D21** — …`) is fixed.
  docs/archive/defects-closed.md       fixed.

A heading a reader relies on that is missing raises LedgerMissing: an empty slice would report every
citation clean while checking nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ID = re.compile(r"\b([QD]\d{1,4})\b")
# A question's heading: `#### Q59 — May …` (the docs write an em dash; a middle dot is the older form).
QUESTION_HEADING = re.compile(r"^#### (Q\d+) [—·]", re.M)
# A defect row: `| D21 | 🟠 | …`, the id plain or in bold.
DEFECT_ROW = re.compile(r"^\|\s*(?:\*\*)?(D\d+)(?:\*\*)?\s*\|", re.M)
# A fixed defect's one-line entry under docs/defects.md "## Fixed": `- **D21** — what it was …`.
FIXED_ENTRY = re.compile(r"^- \*\*(D\d+)\*\* —", re.M)
# What a line says about an id: words that call it open, and words that call it closed.
OPENISH = re.compile(r"still open|stays open|unanswered|not yet answered|pending an answer|remains open|"
                     r"\bis open\b|\(open\)|, open\b", re.I)
CLOSEDISH = re.compile(r"answered|closed|resolved|fixed|superseded|confirmed|reversed", re.I)
# A closing word that is negated ("not fixed", "until … is answered") calls the id open.
NEGATED = re.compile(r"\b(?:not|until|un)\b[^.|;]{0,25}?(?:answered|fixed|resolved|closed)", re.I)

CLOSED = ("resolved", "superseded", "fixed")


class LedgerMissing(Exception):
    """A ledger, or a heading in it, that a reader needs is not there."""


def between(text: str, start: str, end: str, where: str) -> str:
    """The text from heading `start` to heading `end`; a missing heading raises LedgerMissing."""
    first, last = text.find(start), text.find(end)
    if first < 0 or last < first:
        raise LedgerMissing(f"{where} has no {start.strip()!r} … {end.strip()!r} section")
    return text[first:last]


def own_words(line: str, match: re.Match) -> str:
    """What the line says about this id: a little before it, and the clause after it.

    Status words usually follow the id ("[D12] open", "Q9 … still open") or sit just before it
    ("superseded by Q44"), so take 25 characters before and the rest of the clause after, stopping at
    the next id or a clause break. A negated closing word counts as open.
    """
    before = ID.split(line[:match.start()])[-1][-25:]
    after = re.split(r"[.;|]", ID.split(line[match.end():])[0], maxsplit=1)[0][:80]
    return NEGATED.sub(" still open ", before + match.group(1) + after)


def citation_flag(status: str, words: str) -> str | None:
    """"closed, cited as open", "open, cited as closed", or None when the words agree or say nothing."""
    is_open, is_closed = OPENISH.search(words), CLOSEDISH.search(words)
    if status in CLOSED and is_open and not is_closed:
        return "closed, cited as open"
    if status not in CLOSED and is_closed and not is_open:
        return "open, cited as closed"
    return None


@dataclass
class Ledgers:
    """Every id's status: `open`, `resolved` or `superseded` for a question; `fixed`, or an open row's
    Status in lower case (`open`, `mitigated`, `partial`, `blocked — q12`), for a defect."""

    status: dict[str, str] = field(default_factory=dict)
    open_rows: set[str] = field(default_factory=set)   # Still-open summary-table rows: `| [Q1](#…) |`

    @classmethod
    def read(cls, questions: str, resolved: str, defects: str, closed_defects: str) -> "Ledgers":
        ledgers = cls()
        status = ledgers.status
        still_open = between(questions, "\n## Still open", "\n## Superseded", "docs/open-questions.md")
        open_q = set(QUESTION_HEADING.findall(still_open))
        if not open_q and re.search(r"\bQ\d+\b", still_open):
            # Ids under Still open, but no section reads as one: the format moved, so nothing could be read.
            # An empty Still open is a Book with every question answered.
            raise LedgerMissing("docs/open-questions.md has no `#### Q<n> —` section under Still open")
        summary = still_open.split("\n#### ", 1)[0]  # the first table only
        ledgers.open_rows = set(re.findall(r"^\| (?:⛔ )?\[(Q\d+)\]\(", summary, re.M))
        for ident in QUESTION_HEADING.findall(questions + "\n" + resolved):
            status[ident] = "resolved"
        superseded = questions[questions.find("\n## Superseded"):]
        resolved_at = superseded.find("\n## ", 1)
        for ident in QUESTION_HEADING.findall(superseded[:resolved_at] if resolved_at > 0 else superseded):
            status[ident] = "superseded"
        for ident in open_q:
            status[ident] = "open"

        open_block = between(defects, "\n## Open", "\n## Fixed", "docs/defects.md")
        fixed_block = defects[defects.find("\n## Fixed"):]  # between() above proved the heading is there
        for ident in (set(FIXED_ENTRY.findall(fixed_block)) | set(DEFECT_ROW.findall(closed_defects))
                      | set(DEFECT_ROW.findall(defects))):
            status[ident] = "fixed"
        for row in open_block.splitlines():
            match = DEFECT_ROW.match(row)
            if match:
                cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
                status[match.group(1)] = (cells[2] if len(cells) > 2 and cells[2] else "Open").lower()
        return ledgers

    def ids(self, kind: str, open_: bool) -> set[str]:
        """The question (`Q`) or defect (`D`) ids that are open, or closed."""
        return {ident for ident, status in self.status.items()
                if ident[0] == kind and (status not in CLOSED) == open_}

    @staticmethod
    def is_closed(status: str) -> bool:
        return status in CLOSED
