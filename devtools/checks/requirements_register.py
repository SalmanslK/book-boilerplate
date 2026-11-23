"""
The requirements register: docs/requirements/<module>.md, the FSD's requirements one row each.

Read by check-book (the shape and the statuses, at every commit) and by `book requirements import`, which
converts an old audit (docs/Audit/spec-traceability.md) into the register once. The rules are the Book's:
.claude/book/docs.md, *Requirements register*.

Python 3.11+, standard library only.
"""

from __future__ import annotations

import posixpath
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The Book's shared helpers (book_config.py) sit beside the checks, in devtools/factory.
_FACTORY = str(Path(__file__).resolve().parents[1] / "factory")
if _FACTORY not in sys.path:
    sys.path.insert(0, _FACTORY)
import book_config  # noqa: E402

FOLDER = "docs/requirements"
EVIDENCE = "evidence"
AUDIT = "docs/Audit/spec-traceability.md"
COLUMNS = ["Id", "FSD", "Requirement", "Plan task", "Tests", "Status", "Verified at", "Note"]
UNTRACED_COLUMNS = ["Id", "Code", "What it does", "Status", "Note"]
STATUSES = ("NOT_BUILT", "BUILT", "VERIFIED", "CONTRADICTED", "NOT_BACKEND", "DEFERRED", "REVERIFY")
UNTRACED_STATUSES = ("UNTRACED", "ACCEPTED")
DEFECT = re.compile(r"\bD\d+\b")
CONVERTED = "<!-- converted from an audit -->"  # a register with this mark warns where its data falls short


@dataclass
class Row:
    file: str
    line: int
    cells: dict[str, str]
    untraced: bool = False


@dataclass
class Register:
    rows: list[Row] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    converted: set[str] = field(default_factory=set)


cells = book_config.table_cells  # one splitter for every Book table: `\|` (what `table` writes) stays put


def is_separator(line: str) -> bool:
    return bool(line.strip().startswith("|")) and set(line.replace("|", "").strip()) <= set("-: ")


def files(root: Path) -> list[Path]:
    folder = root / FOLDER
    return sorted(p for p in folder.glob("*.md") if not p.name.startswith("_")) if folder.is_dir() else []


def read(root: Path) -> Register:
    """Every row of every register file, and the shape problems found while reading."""
    register = Register()
    for path in files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if CONVERTED in text:
            register.converted.add(rel)
        header: list[str] | None = None
        for number, line in enumerate(text.splitlines(), 1):
            if not line.lstrip().startswith("|"):
                header = None
                continue
            if header is None:
                header = cells(line)
                if header not in (COLUMNS, UNTRACED_COLUMNS):
                    register.problems.append(f"{rel}:{number}: a table's columns are not `| "
                                             + " | ".join(COLUMNS) + " |` nor the Untraced behaviour table's")
                    header = []
                continue
            if is_separator(line) or not header:
                continue
            values = cells(line)
            if len(values) != len(header):
                register.problems.append(f"{rel}:{number}: {len(values)} cells where the table has {len(header)}")
                continue
            register.rows.append(Row(rel, number, dict(zip(header, values)), header == UNTRACED_COLUMNS))
    return register


def status_word(cell: str) -> str:
    return re.split(r"[\s—–-]", cell.strip().strip("*`"), maxsplit=1)[0]


def problems(root: Path, prefix: str = "") -> tuple[list[str], list[str]]:
    """(failures, warnings) for check-book: the shape, the statuses, unique ids, what a status must name."""
    register = read(root)
    failures, warnings = list(register.problems), []
    seen: dict[str, str] = {}
    off_prefix: dict[str, list[str]] = {}  # a converted file's ids off the prefix: one warning per file
    for row in register.rows:
        where = f"{row.file}:{row.line}"
        ident = row.cells["Id"].strip("*` ")
        status = status_word(row.cells["Status"])
        allowed = UNTRACED_STATUSES if row.untraced else STATUSES
        # A register converted from an old audit warns of what the audit itself lacked, so its backlog never
        # blocks a commit; a register written in the Book fails.
        lacking = warnings if row.file in register.converted else failures
        if not ident:
            failures.append(f"{where}: a row with no Id")
            continue
        if ident in seen:
            lacking.append(f"{where}: {ident} is also at {seen[ident]} - an id is never reused")
        seen[ident] = where
        if prefix and not row.untraced and not ident.startswith(prefix):
            if row.file in register.converted:
                off_prefix.setdefault(row.file, []).append(ident)
            else:
                failures.append(f"{where}: {ident} does not start with [requirements] id_prefix {prefix!r}")
        if status not in allowed:
            failures.append(f"{where}: {ident}'s status {status or '(none)'} is not one of {', '.join(allowed)}")
            continue
        note = row.cells.get("Note", "") + " " + row.cells["Status"]
        # What the row says beyond its status word: a reason, a release, a decision.
        said = re.sub(r"[\s—–\-*`]+", " ", note.replace(status, "", 1)).strip()
        if status == "CONTRADICTED" and not DEFECT.search(note):
            lacking.append(f"{where}: {ident} is CONTRADICTED but names no defect (a D id in its Note)")
        if status == "DEFERRED" and not said:
            failures.append(f"{where}: {ident} is DEFERRED with no release or reason in its Note")
        if status == "ACCEPTED" and not said:
            failures.append(f"{where}: {ident} is ACCEPTED with no decision in its Note")
        if status == "VERIFIED" and row.cells.get("Tests", "").strip() in ("", "—", "-"):
            message = f"{where}: {ident} is VERIFIED but its Tests cell names no test"
            (warnings if row.file in register.converted else failures).append(message)
        if status == "REVERIFY":
            warnings.append(f"{where}: {ident} is REVERIFY - the FSD changed since it was verified")
    for rel, idents in off_prefix.items():
        warnings.append(f"{rel}: {len(idents)} id(s) the audit gave do not start with [requirements] id_prefix "
                        f"{prefix!r} ({prefixes(idents)})")
    return failures, warnings


def prefixes(idents: list[str]) -> str:
    """`PLN 366, REQ 2`: how many ids start with each prefix (the letters before the first hyphen)."""
    found: dict[str, int] = {}
    for ident in idents:
        word = (re.match(r"[A-Za-z]+(?=-)", ident) or re.match(r"\S*", ident)).group(0) or ident
        found[word] = found.get(word, 0) + 1
    return ", ".join(f"{word} {n}" for word, n in sorted(found.items(), key=lambda item: (-item[1], item[0])))


def counts(root: Path) -> dict[str, dict[str, int]]:
    """{module file stem: {status: rows}} for the roll-up."""
    found: dict[str, dict[str, int]] = {}
    for row in read(root).rows:
        if row.untraced:
            continue
        module = Path(row.file).stem
        status = status_word(row.cells["Status"])
        found.setdefault(module, {})
        found[module][status] = found[module].get(status, 0) + 1
    return found


# ------------------------------------------------------------------------------------- the conversion

OLD_STATUS = [  # (how the old audit's cell starts, the register's status, flagged for review)
    ("VERIFIED (observed)", "VERIFIED", False), ("VERIFIED (traced)", "VERIFIED", False),
    ("VERIFIED", "VERIFIED", False), ("NOT_APPLICABLE", "NOT_BACKEND", False), ("PARTIAL", "BUILT", False),
    ("MISSING", "NOT_BUILT", False), ("CONTRADICTED", "CONTRADICTED", False), ("UNVERIFIED", "BUILT", True),
    ("UNTRACED", "BUILT", True), ("DEFERRED", "DEFERRED", False), ("NOT_BUILT", "NOT_BUILT", False),
    ("BUILT", "BUILT", False),
]
OLD_COLUMNS = ["ID", "FSD ref", "Requirement", "Plan ref", "Code evidence", "Test evidence", "Status"]
OLD_WORDS = "|".join(sorted({old.split(" (")[0] for old, _, _ in OLD_STATUS}, key=len, reverse=True))
# A status cell that keeps its history says where the status moved: `MISSING → **VERIFIED (observed)**`, a
# dated update ending `— CONTRADICTED (traced), [D616](…)`, or in words: `· later: CONTRADICTED (traced)`,
# `; now VERIFIED`, `stays PARTIAL`. The last move is the row's status now.
STATUS_MOVE = re.compile(rf"(?:→|->)\s*({OLD_WORDS})\b|[—–]\s*({OLD_WORDS})\s*\((?:traced|observed)"
                         rf"|\b(?i:later|now|since then|became|becomes|changed to|moved to|stays|remains)\b\s*:?\s*"
                         rf"({OLD_WORDS})\b")
# A status word heading a clause of its own (`… (Lab.cs:22) — UNVERIFIED, the FSD …`): the cell may have moved there.
STATUS_HEAD = re.compile(rf"(?:^|[·;—–:]|\.\s)\s*({OLD_WORDS})\b(?=\s*(?:\(|,|$|[—–·;.:]))")
STATUS_WORD = re.compile(rf"\b({OLD_WORDS})\b")
# What the cell says its status was, or is not: `was: MISSING`, `never recorded CONTRADICTED`, `the old "MISSING`.
STATUS_BEFORE = re.compile(r"\b(?:was|were|previously|formerly|previous|old|not|no longer|never(?: \w+)?)\s*:?\s*"
                           r"[\"'*~]*$", re.IGNORECASE)
# A condition is no move: `becomes PARTIAL if Q9 is refused`, `would become VERIFIED`, `once D5 lands: VERIFIED`.
CONDITION = re.compile(r"\b(?:if|when|once|unless|until|would|could|may)\b", re.IGNORECASE)
CLAUSE_END = re.compile(r"[·;—–:→]|->|[.!?](?=\s|$)")
ASIDE = re.compile(r"`[^`]*`|\"[^\"]*\"|\]\([^)]*\)|\([^()]*\)")  # code, a quotation, a link's target, a parenthesis
# A status plainly of a part or of another row: `server half VERIFIED`, `PARTIAL half`, `R20 (PARTIAL)`, and in a cell
# that is PARTIAL as a whole, the one of a part it names (`PARTIAL — farms VERIFIED`).
PART_BEFORE = re.compile(r"(?:\b(?:half|part|side)|R\d+)(?:\s+(?:is|are))?[\s(]*$")
OWN_SUBJECT = re.compile(r"(?:^|\b(?:it|row|status|this|requirement)(?: is| has)?)[\s,(]*$", re.IGNORECASE)
PART_AFTER = re.compile(r"\s+(?:half|part|side|there)\b")
PART_NAMED = re.compile(r"^[\s(]*(?:(?:the|its|and|or)\s+)*[^\W\d][^\W\d'’-]*(?:[ ,]+[^\W\d][\w'’-]*)*\s+$")
NOT_A_PART = re.compile(r"\b(?:after|again|as|at|back|be|becomes?|been|but|by|finally|found|from|in|it|later|made|marked|now"
                        r"|on|re-?\w*|row|set|shows?|says?|since|so|status|still|then|this|to|today|yet)\b", re.IGNORECASE)
TEST_NAME = re.compile(r"`?[A-Z]\w+(?:Tests?|Spec)\b(?:\.\w+)?`?")
# An audit's column, by what its header starts with (any case): `Requirement / plan item` is the requirement,
# `FSD section / source` the FSD reference, `Code evidence (API)` the code. The first match wins; a
# header no entry names (or a second header for a field already taken) goes to the evidence appendix.
FIELDS = (("requirement", "requirement"), ("what it does", "what"), ("behaviour", "what"), ("fsd", "fsd"),
          ("plan", "plan"), ("code", "code"), ("where", "code"), ("test", "test"), ("status", "status"),
          ("nearest justification", "justification"), ("note", "note"))
REQUIREMENT_ID = re.compile(r"(?:FSD|PLN|REQ)-", re.IGNORECASE)  # never an untraced row, whatever its heading
UNTRACED_ID = re.compile(r"UNT\b|UNT-", re.IGNORECASE)
SUPPLEMENT = "↳"  # an audit row that adds a module's evidence to a requirement held elsewhere: no row of its own
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NOTHING = ("", "—", "–", "-")
MARKDOWN_LINK = re.compile(r"(\]\()([^)\s]+)(\))")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "module"


def module_name(heading: str) -> str:
    """`## Labs — FSD §2.7 — plan ...` -> Labs."""
    return re.split(r"\s+[—–]\s+", heading[3:].strip(), maxsplit=1)[0].strip()


def is_pass(heading: str) -> bool:
    """A section that holds one audit pass's rows (`Rows added by the delta pass (2026-11-07, …)`), not a
    module: its name has no ` — ` split, or it is dated, or it names a pass or a delta."""
    name = module_name(f"## {heading}")
    return (not re.search(r"\s[—–]\s", heading) or bool(DATE.search(heading))
            or bool(re.search(r"\b(?:pass|delta)\b", name, re.IGNORECASE)))


def field_of(header: str) -> str | None:
    plain = header.strip().strip("*`").strip().lower()
    if plain == "id":
        return "id"
    return next((name for start, name in FIELDS if plain.startswith(start)), None)


def old_status(text: str) -> tuple[str, str, bool] | None:
    """The OLD_STATUS entry `text` starts with (any case), or None."""
    return next((entry for entry in OLD_STATUS if text.upper().startswith(entry[0].upper())), None)


def conditional(plain: str, at: int) -> bool:
    """Whether the status word at `at` is only a condition's: its own clause holds one, or the clause that leads
    into it, or the one that follows it, opens with one. Code, a quotation and an aside are not read."""
    def mask(aside: re.Match) -> str:
        own = aside.start() <= at < aside.end()
        return aside.group() if own or CONDITION.match(aside.group(), 1) else " " * len(aside.group())
    text = ASIDE.sub(mask, plain)
    ends = [m.span() for m in CLAUSE_END.finditer(text)]
    before = [span for span in ends if span[1] <= at]
    after = next((span for span in ends if span[0] >= at), (len(text), len(text)))
    if CONDITION.search(text[before[-1][1] if before else 0:after[0]]):
        return True
    if before and text[slice(*before[-1])] in ("—", "–", ":", "→", "->"):
        led = text[before[-2][1] if len(before) > 1 else 0:before[-1][0]]
        if CONDITION.match(led.lstrip()):
            return True
    return text[slice(*after)] in ("—", "–") and bool(CONDITION.match(text[after[1]:].lstrip()))


def of_a_part(plain: str, found: re.Match, name: str) -> bool:
    """Whether a status named in a cell whose own is `name` is plainly that of a part, or of another row."""
    before = plain[:found.start()]
    if PART_AFTER.match(plain, found.end()) or PART_BEFORE.search(before):
        return True
    clause = CLAUSE_END.split(re.sub(r"^.* - ", "", before))[-1]
    return (name == "PARTIAL" and bool(PART_NAMED.match(clause)) and not NOT_A_PART.search(clause)
            and not STATUS_WORD.search(clause))


def current_status(plain: str) -> tuple[int | None, str, bool, bool]:
    """(where in the cell the row's status now is, None when the cell gives none; the cell from its first status;
    whether it moved; whether the cell leaves its status unsettled, for a person to confirm)."""
    words = list(STATUS_WORD.finditer(plain))
    opens = old_status(plain)
    began = plain if opens else plain[words[0].start():] if words else ""
    others = [m for m in words if not STATUS_BEFORE.search(plain[:m.start()])]
    moves = [m for m in STATUS_MOVE.finditer(plain)
             if not STATUS_BEFORE.search(plain[:m.start()]) and not conditional(plain, m.start(m.lastindex))
             and not re.search(r"\b(?:a|an|the)\s+$", plain[:m.start()], re.IGNORECASE)]
    at = moves[-1].start(moves[-1].lastindex) if moves else 0 if opens else None
    name = next((m.group(1) for m in words if m.start() == at), opens[0].split(" (")[0] if opens else "")
    settled = at or 0
    heads = [m for m in STATUS_HEAD.finditer(plain, at + 1 if moves else len(opens[0]) if opens else 0)
             if m.group(1) != name and not STATUS_BEFORE.search(plain[:m.start(1)])
             and not conditional(plain, m.start(1))]
    if heads:
        at, name = heads[-1].start(1), heads[-1].group(1)
    several = len({m.group(1) for m in others}) > 1
    if at is None:
        return None, began, False, several
    # A move in words may be a part's: `the server half now VERIFIED`.
    partly = bool(moves) and moves[-1].lastindex == 3 and not began.startswith(name) and not OWN_SUBJECT.search(
        CLAUSE_END.split(plain[:moves[-1].start()])[-1])
    unsettled = bool(heads) and several or partly or any(
        m.group(1) != name and m.start() > settled
        and (conditional(plain, m.start()) or not of_a_part(plain, m, name)) for m in others)
    return at, began, bool(moves or heads), unsettled


def convert_status(cell: str, tests: str) -> tuple[str, bool, str, bool]:
    """(status, flagged, the rest of the old cell, unclear) for one old audit status cell. A cell that keeps its
    history has the status of its last move, and its rest is the whole cell, the status it began with included; any
    other cell's rest is everything after its status. A condition is no move. A cell that names another status no
    move settles takes it only when it heads a clause of its own, and is `unclear` either way."""
    plain = cell.replace("**", "").strip()
    at, began, moved, unclear = current_status(plain)
    if at is None:
        return "BUILT", True, plain, unclear
    first, latest = old_status(began), plain[at:]
    rest = re.sub(r"^(?:[\s—–:·]|-(?!>))+", "", plain[len(first[0]):]).rstrip(" —–-:·")  # an arrow stays
    if moved or unclear:
        rest = plain.rstrip(" —–-:·")
    _, new, flagged = old_status(latest) or first
    flagged = flagged or first[2]  # a row that began unverified stays one to review
    qualifier = re.match(r"VERIFIED\s*\((traced|observed)", latest, re.IGNORECASE)
    if qualifier and not TEST_NAME.search(tests):
        if qualifier.group(1).lower() == "traced":
            return "BUILT", flagged, rest, unclear
        # Seen working but proved by no test: it stays VERIFIED, and is counted to review.
        return new, True, rest, unclear
    if new == "CONTRADICTED" and not DEFECT.search(plain):
        return new, True, rest, unclear
    return new, flagged, rest, unclear


@dataclass
class Cell:
    """One non-empty audit cell the register must carry: where it ended up is checked after writing."""
    ident: str
    header: str
    text: str


@dataclass
class Module:
    name: str
    heading: str
    rows: list[list[str]] = field(default_factory=list)
    untraced: list[list[str]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)  # the audit's tables with no ID column, kept whole
    flagged: int = 0
    statuses: dict[str, int] = field(default_factory=dict)
    sections: list[str] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)       # ids whose Requirement or What it does is empty
    unmapped: dict[str, set[str]] = field(default_factory=dict)  # section -> the headers sent to the appendix
    supplements: int = 0
    rejoined: list[str] = field(default_factory=list)  # ids whose cell held a pipe in a code span or emphasis
    unclear: list[str] = field(default_factory=list)  # `id STATUS` of a cell that does not settle its status
    misaligned: list[tuple[str, int, int, str]] = field(default_factory=list)  # (id, cells, header's, the line)

    def merge(self, other: "Module") -> None:
        """Another audit section with the same file name: its rows follow this one's, in order."""
        self.rows += other.rows
        self.untraced += other.untraced
        self.evidence += other.evidence
        self.tables += other.tables
        self.flagged += other.flagged
        for status, n in other.statuses.items():
            self.statuses[status] = self.statuses.get(status, 0) + n
        self.sections += other.sections or [other.heading]
        self.cells += other.cells
        self.empty += other.empty
        for section, headers in other.unmapped.items():
            self.unmapped.setdefault(section, set()).update(headers)
        self.supplements += other.supplements
        self.rejoined += other.rejoined
        self.unclear += other.unclear
        self.misaligned += other.misaligned


def joined(*parts: str) -> str:
    return " · ".join(part for part in parts if part and part not in NOTHING)


def labelled(header: str, text: str) -> str:
    return f"{header}: {text}" if text not in NOTHING else ""


def add_row(module: Module, header: list[str], values: list[str], in_untraced: bool) -> None:
    """One audit row with an id into the module: a requirement, an untraced behaviour or a supplement."""
    taken: dict[str, int] = {}
    for at, name in enumerate(header):
        kind = field_of(name)
        if kind and kind not in taken:
            taken[kind] = at
    cell = {kind: values[at] if at < len(values) else "" for kind, at in taken.items()}
    ident = cell.get("id", "")
    spare = [(header[at], values[at]) for at in range(min(len(header), len(values)))
             if at not in taken.values() and values[at] not in NOTHING]
    if spare:
        module.unmapped.setdefault(module.heading, set()).update(name for name, _ in spare)
    module.cells += [Cell(ident, name, values[at]) for at, name in enumerate(header)
                     if at < len(values) and at != taken.get("id") and values[at] not in NOTHING]
    if ident.startswith(SUPPLEMENT):
        # The audit's own words: "same IDs, not counted again" - its cells go to the appendix, whole.
        module.supplements += 1
        module.evidence.append(f"- **{ident}** (a supplement to that requirement) — " + joined(
            *(labelled(header[at], values[at]) for at in range(min(len(header), len(values))) if at != taken["id"])))
        return
    plain_id = ident.strip("*` ")
    untraced = bool(UNTRACED_ID.match(plain_id)) or (in_untraced and not REQUIREMENT_ID.match(plain_id)) or (
        "requirement" not in cell and "what" in cell and not REQUIREMENT_ID.match(plain_id))
    old = cell.get("status", "")
    extra = [labelled(name, text) for name, text in spare]
    justification = labelled(header[taken["justification"]], cell["justification"]) if "justification" in cell \
        else ""
    if untraced:
        what = cell.get("what") or cell.get("requirement") or ""
        status = "ACCEPTED" if old.upper().replace("*", "").startswith("ACCEPTED") else "UNTRACED"
        said = old if status == "UNTRACED" else old.replace("*", "")[8:].strip(" —-:")
        module.untraced.append([ident, cell.get("code", "") or "—", what or "—", status,
                                joined(said, cell.get("note", ""), justification) or "—"])
        extra += [labelled(header[taken[kind]], cell[kind]) for kind in ("fsd", "plan", "test") if kind in cell]
        if not what.strip() or what in NOTHING:
            module.empty.append(plain_id)
    else:
        tests = cell.get("test", "")
        status, flagged, rest, unclear = convert_status(old, tests)
        if unclear:
            module.unclear.append(f"{plain_id} {status}")
        if "status" not in cell:
            flagged = True
        module.flagged += flagged
        module.statuses[status] = module.statuses.get(status, 0) + 1
        requirement = cell.get("requirement") or cell.get("what") or ""
        note = joined(rest, cell.get("note", ""), justification,
                      "review: converted from an unverified audit row" if flagged else "")
        test_names = ", ".join(dict.fromkeys(m.strip("`") for m in TEST_NAME.findall(tests))) or "—"
        module.rows.append([ident, cell.get("fsd", "") or "—", requirement or "—", cell.get("plan", "") or "—",
                            test_names, status, "—", note or "—"])
        extra = [labelled(header[taken[kind]], cell[kind]) for kind in ("code", "test") if kind in cell] + extra
        if not requirement.strip() or requirement in NOTHING:
            module.empty.append(plain_id)
    evidence = joined(*extra)
    if evidence:
        module.evidence.append(f"- **{plain_id}** — {evidence}")


def is_open(text: str, emphasis: bool) -> bool:
    """Whether `text` ends inside a code span (or, with `emphasis`, inside `*emphasis*`) it opened."""
    text = re.sub(r"(`+).*?\1", "", text.replace("\\`", ""))
    if "`" in text:
        return True
    return emphasis and text.replace("\\*", "").replace("**", "").count("*") % 2 == 1


def rejoined(line: str, width: int) -> list[str] | None:
    """A row that splits into more cells than its header's `width` because a cell holds an unescaped pipe in a code
    span (`` `a || b` ``) or in emphasis (`*A | B*`), with each such pipe kept in its cell; None when that does not
    give `width` cells. The pipes split first exactly as `table_cells` splits them."""
    text = line.strip()
    text = text[1:] if text.startswith("|") else text
    text = text[:-1] if text.endswith("|") and not text.endswith("\\|") else text
    parts = re.split(r"(?<!\\)\|", text)
    for emphasis in (False, True):
        joined_parts: list[str] = []
        for part in parts:
            if joined_parts and is_open(joined_parts[-1], emphasis):
                joined_parts[-1] += "|" + part
            else:
                joined_parts.append(part)
        if len(joined_parts) == width:
            return [part.strip() for part in joined_parts]
    return None


def add_whole(module: Module, header: list[str], values: list[str], line: str, in_untraced: bool) -> None:
    """An audit row whose cells cannot be matched to its header: never read by position. It is kept whole in the
    evidence appendix, and its register row (BUILT, or UNTRACED) is flagged to review."""
    ident = next((values[at] for at, name in enumerate(header) if field_of(name) == "id" and at < len(values)), "")
    plain_id = ident.strip("*` ")
    module.misaligned.append((ident, len(values), len(header), line.strip()))
    kept = (f"the audit's row, kept whole ({len(values)} cells under a {len(header)}-column header): "
            + line.strip())
    if ident.startswith(SUPPLEMENT):
        module.supplements += 1
        module.evidence.append(f"- **{ident}** (a supplement to that requirement) — {kept}")
        return
    note = (f"review: the audit's row has {len(values)} cells where its header has {len(header)} - read it whole "
            "in the evidence appendix")
    if UNTRACED_ID.match(plain_id) or (in_untraced and not REQUIREMENT_ID.match(plain_id)):
        module.untraced.append([ident, "—", "—", "UNTRACED", note])
    else:
        module.rows.append([ident, "—", "—", "—", "—", "BUILT", "—", note])
        module.flagged += 1
        module.statuses["BUILT"] = module.statuses.get("BUILT", 0) + 1
    module.evidence.append(f"- **{plain_id}** — {kept}")


def parse_audit(text: str) -> list[Module]:
    modules: list[Module] = []
    module: Module | None = None
    header: list[str] | None = None
    other: list[str] | None = None  # the lines of a table with no ID column, kept whole
    in_untraced = False
    for line in text.splitlines():
        if line.startswith("## "):
            module = Module(module_name(line), line[3:].strip(), sections=[line[3:].strip()])
            modules.append(module)
            header, other, in_untraced = None, None, False
            continue
        if re.match(r"#{3,6} ", line) and module is not None:
            in_untraced = "untraced" in line.lower()
            header, other = None, None
            continue
        if module is None or not line.lstrip().startswith("|"):
            header, other = None, None
            continue
        if other is not None:
            other.append(line)
            continue
        if header is None:
            header = cells(line)
            if "id" not in [field_of(name) for name in header]:
                other = [line]
                module.tables.append(other)  # type: ignore[arg-type] - joined into its text below
            continue
        if is_separator(line):
            continue
        values = cells(line)
        ident = next((values[at] for at, name in enumerate(header) if field_of(name) == "id" and at < len(values)),
                     "")
        if not ident:
            continue
        if len(values) > len(header) and (whole := rejoined(line, len(header))):
            module.rejoined.append(ident.strip("*` "))
            values = whole
        if len(values) != len(header):
            add_whole(module, header, values, line, in_untraced)
            continue
        add_row(module, header, values, in_untraced)
    for module in modules:
        module.tables = ["\n".join(table) if isinstance(table, list) else table for table in module.tables]
    return [m for m in modules if m.rows or m.untraced or m.evidence or m.tables]


def by_file(modules: list[Module]) -> list[Module]:
    """One module per register file: sections whose names slug the same (`Labs — §2.7`, `Labs — §2.8`) are
    merged in the audit's order, so the second never overwrites the first."""
    merged: dict[str, Module] = {}
    for module in modules:
        if slug(module.name) in merged:
            merged[slug(module.name)].merge(module)
        else:
            merged[slug(module.name)] = module
    return list(merged.values())


def row_ids(module: Module) -> list[str]:
    return [row[0].strip("*` ") for row in module.rows + module.untraced]


def escape(cell: str) -> str:
    """A cell's pipes escaped once: one the audit already wrote `\\|` stays as it is."""
    return re.sub(r"(?<!\\)\|", r"\\|", cell)


def table(columns: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)] + [
        "| " + " | ".join(escape(cell) for cell in row) + " |" for row in rows]


AUDIT_FOLDER = posixpath.dirname(AUDIT)


def rebase(text: str, to_folder: str, archived: str) -> str:
    """`text`'s relative links, written from the audit's folder, written again from `to_folder` (both
    repository paths); a link into the audit itself points at its archived copy."""
    def swap(match: re.Match) -> str:
        target = match.group(2)
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|#|/)", target, re.IGNORECASE):
            return match.group(0)
        path, mark, anchor = target.partition("#")
        where = posixpath.normpath(posixpath.join(AUDIT_FOLDER, path)) if path else AUDIT
        if where == AUDIT:
            where = archived
        moved = posixpath.relpath(where, to_folder)
        return f"{match.group(1)}{moved}{mark}{anchor}{match.group(3)}"
    return MARKDOWN_LINK.sub(swap, text)


def render(module: Module, today: str, archived: str = "") -> tuple[str, str]:
    """(the register file, its evidence appendix); the audit's own links in them rebased to where each is."""
    archived = archived or f"docs/archive/spec-traceability-{today}.md"
    deeper = f"{FOLDER}/{EVIDENCE}"
    rows = [[rebase(cell, FOLDER, archived) for cell in row] for row in module.rows]
    untraced = [[rebase(cell, FOLDER, archived) for cell in row] for row in module.untraced]
    lines = [f"# {module.name} — requirements", "", CONVERTED,
             f"Converted on {today} from the audit's {sections(module)}; each row's long evidence is in "
             f"[the evidence appendix]({EVIDENCE}/{slug(module.name)}.md). The rules: `.claude/book/docs.md`, "
             "*Requirements register*.", "", *table(COLUMNS, rows), "", "## Untraced behaviour", ""]
    lines += table(UNTRACED_COLUMNS, untraced) if untraced else ["None found."]
    evidence = [f"# {module.name} — the converted audit's evidence", "", CONVERTED,
                "The Code and Test evidence each audit row held when it was converted, and every other column "
                "the register has no place for, kept so none is lost; the "
                f"register is [{slug(module.name)}.md](../{slug(module.name)}.md).", "",
                *(rebase(line, deeper, archived) for line in module.evidence)]
    if module.tables:
        evidence += ["", "## The audit's other tables", ""]
        for other in module.tables:
            evidence += [rebase(other, deeper, archived), ""]
    return "\n".join(lines) + "\n", "\n".join(evidence).rstrip("\n") + "\n"


def sections(module: Module) -> str:
    names = module.sections or [module.heading]
    if len(names) == 1:
        return f"section *{names[0]}*"
    return "sections " + ", ".join(f"*{name}*" for name in names[:-1]) + f" and *{names[-1]}*"


LINK = re.compile(r"\]\(([^)\s#]*spec-traceability\.md)(#[^)\s]*)?\)")


def relink(root: Path, audit: Path, archived: Path) -> int:
    """Point each live document's link into the audit (`...spec-traceability.md#a-row`) at its archived copy, so
    its anchors keep resolving once the audit is a stub. Returns how many links moved."""
    import os
    moved = 0
    for path in (root / "docs").rglob("*.md"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("docs/archive/") or path == audit:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")

        def swap(match: re.Match) -> str:
            nonlocal moved
            if (path.parent / match.group(1)).resolve() != audit.resolve():
                return match.group(0)
            moved += 1
            return f"]({os.path.relpath(archived, path.parent).replace(os.sep, '/')}{match.group(2) or ''})"
        new = LINK.sub(swap, text)
        if new != text:
            path.write_text(new, encoding="utf-8", newline="\n")
    return moved


def id_problems(root: Path, expected: list[str]) -> list[str]:
    """Each id the audit held that the written register does not hold exactly once."""
    held: dict[str, int] = {}
    for row in read(root).rows:
        ident = row.cells["Id"].strip("*` ")
        held[ident] = held.get(ident, 0) + 1
    return [f"{ident} is in the register {held.get(ident, 0)} time(s), not once"
            for ident in dict.fromkeys(expected) if held.get(ident, 0) != 1]


APPENDIX_ID = re.compile(r"- \*\*(.+?)\*\*[ (]")


def lines_by_id(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """({id: its register row(s)}, {id: its appendix line(s)}) as written, each id stripped of its markup."""
    folder = root / FOLDER
    rows: dict[str, str] = {}
    for path in files(root):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("|") and not is_separator(line):
                ident = cells(line)[0].strip("*` ")
                rows[ident] = rows.get(ident, "") + line + "\n"
    found: dict[str, str] = {}
    for path in sorted((folder / EVIDENCE).glob("*.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if match := APPENDIX_ID.match(line):
                ident = match.group(1).strip("*` ")
                found[ident] = found.get(ident, "") + line + "\n"
    return rows, found


def cell_problems(root: Path, modules: list[Module], archived: str) -> list[str]:
    """Each non-empty audit cell that its own id's register row and appendix line do not hold, and each row not
    matched to its header that the appendix does not keep whole. A Status cell is held when what follows its status
    word is; every other cell, whole (its links rebased)."""
    folder = root / FOLDER
    appendix = "\n".join(p.read_text(encoding="utf-8") for p in (folder / EVIDENCE).glob("*.md"))
    rows, found = lines_by_id(root)
    deeper = f"{FOLDER}/{EVIDENCE}"
    lost = []
    for module in modules:
        for cell in module.cells:
            key = cell.ident.strip("*` ")
            row, evidence = rows.get(key, ""), found.get(key, "")
            text = cell.text
            if field_of(cell.header) == "status":
                text = re.sub(r"^\W*[A-Z_]+(?:\s*\([^)]*\))?", "", text.replace("**", "")).strip(" —–-:·")
                # A status cell's rest is written without its bold.
                if text and escape(rebase(text, FOLDER, archived)) not in row.replace("**", ""):
                    lost.append(f"{cell.ident}: its {cell.header} cell is in neither the register nor the appendix")
                continue
            if (escape(rebase(text, FOLDER, archived)) not in row
                    and rebase(text, deeper, archived) not in evidence):
                lost.append(f"{cell.ident}: its {cell.header} cell is in neither the register nor the appendix")
        for ident, count, width, line in module.misaligned:
            if rebase(line, deeper, archived) not in found.get(ident.strip("*` "), ""):
                lost.append(f"{ident}: {count} cells where the header has {width}, and the appendix does not keep "
                            "the row whole")
        for other in module.tables:
            if rebase(other, f"{FOLDER}/{EVIDENCE}", archived) not in appendix:
                lost.append(f"{slug(module.name)}: a table with no ID column was not kept in the appendix")
    return lost


def link_problems(root: Path, written: list[str]) -> list[str]:
    """check-book's link check on the files the import wrote (a link that does not resolve)."""
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location("check_book_links", Path(__file__).with_name("check-book.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.link_problems(written, root)
    except Exception:  # noqa: BLE001 - check-book unloadable here: the links still get a plain existence check
        problems = []
        for rel in written:
            for number, line in enumerate((root / rel).read_text(encoding="utf-8").splitlines(), 1):
                for target in re.findall(r"\]\(([^)\s#]+)", line):
                    if not re.match(r"^[a-z][a-z0-9+.-]*:", target) and not (root / rel).parent.joinpath(
                            target).exists():
                        problems.append(f"{rel}:{number}: links to {target}, which does not exist")
        return problems


def import_command(root: Path, apply: bool, today: str) -> tuple[str, bool]:
    """`book requirements import`: the old audit into the register, report first; `--apply` writes it."""
    audit = root / AUDIT
    if not audit.is_file():
        return f"book requirements import: no {AUDIT} to convert\n", True
    if files(root):
        return f"book requirements import: {FOLDER}/ already holds a register; nothing converted\n", True
    parsed = parse_audit(audit.read_text(encoding="utf-8", errors="replace"))
    modules = by_file(parsed)
    expected = [ident for module in parsed for ident in row_ids(module)]
    out = [f"book requirements import - {AUDIT} - " + ("--apply: written" if apply else
                                                        "report only, nothing written (--apply writes it)")]
    confirm: list[str] = []
    misaligned: list[str] = []  # rows not matched to their header: the import is not OK until a person splits them
    total: dict[str, int] = {}
    for module in modules:
        for status, n in module.statuses.items():
            total[status] = total.get(status, 0) + n
        statuses = ", ".join(f"{n} {s}" for s, n in sorted(module.statuses.items()))
        name = f"{slug(module.name)}.md"
        out.append(f"  {name}  {len(module.rows)} requirement(s) ({statuses or 'none'}), "
                   f"{len(module.untraced)} untraced" + (f", {module.flagged} to review" if module.flagged else "")
                   + (f", {module.supplements} supplement row(s) in the appendix" if module.supplements else ""))
        if len(module.sections) > 1:
            out.append(f"    from {len(module.sections)} audit sections, merged in order: "
                       + "; ".join(module.sections))
        passes = [section for section in module.sections if is_pass(section)]
        if passes:
            confirm.append(f"CONFIRM {name}: a pass, not a module ({passes[0]}) - merge its rows into their "
                           "modules by hand")
        for section, headers in module.unmapped.items():
            confirm.append(f"CONFIRM {name}: the audit's {', '.join(sorted(headers))} column(s) in *{section}* "
                           "have no register column - each cell is in the evidence appendix")
        if module.tables:
            confirm.append(f"CONFIRM {name}: {len(module.tables)} audit table(s) with no ID column are kept whole "
                           "in the evidence appendix")
        if module.empty:
            confirm.append(f"CONFIRM {name}: {len(module.empty)} row(s) with no requirement text in the audit - "
                           + ", ".join(module.empty[:5]) + (" …" if len(module.empty) > 5 else ""))
        for ident in module.rejoined:
            confirm.append(f"CONFIRM {ident}: a cell holds an unescaped pipe in a code span or emphasis - read as "
                           "part of that cell, and written as \\|")
        if module.unclear:
            confirm.append(f"CONFIRM {name}: {len(module.unclear)} row(s) whose audit Status cell names several "
                           "statuses and does not settle which is current - check each, filed as shown, against its "
                           "Note: " + ", ".join(module.unclear))
        for ident, found, width, _ in module.misaligned:
            misaligned.append(f"CONFIRM {ident.strip('*` ')}: {found} cells where the header has {width} - kept "
                              "whole in the appendix; split it by hand (write a pipe as \\|)")
    out.append("  total: " + ", ".join(f"{n} {s}" for s, n in sorted(total.items())))
    requirement_ids = [row[0].strip("*` ") for module in modules for row in module.rows]
    if requirement_ids:
        out.append(f"  id prefixes: {prefixes(requirement_ids)}")
    try:
        prefix = str((book_config.load(root).get("requirements") or {}).get("id_prefix") or "")
    except (OSError, ValueError):
        prefix = ""
    off = [ident for ident in requirement_ids if prefix and not ident.startswith(prefix)]
    if off:
        confirm.append(f"CONFIRM {len(off)} requirement id(s) do not start with [requirements] id_prefix {prefix!r} "
                       f"({prefixes(off)}): they stay as the audit gave them, and check-book warns of them once per "
                       "register file")
    archived = f"docs/archive/spec-traceability-{today}.md"
    out.append(f"  {AUDIT} moves whole to {archived}, leaving a one-line stub")
    out += [f"  {line}" for line in confirm + misaligned]
    if apply:
        folder = root / FOLDER
        (folder / EVIDENCE).mkdir(parents=True, exist_ok=True)
        written = []
        for module in modules:
            register, evidence = render(module, today, archived)
            (folder / f"{slug(module.name)}.md").write_text(register, encoding="utf-8", newline="\n")
            (folder / EVIDENCE / f"{slug(module.name)}.md").write_text(evidence, encoding="utf-8", newline="\n")
            written += [f"{FOLDER}/{slug(module.name)}.md", f"{FOLDER}/{EVIDENCE}/{slug(module.name)}.md"]
        text = audit.read_text(encoding="utf-8", errors="replace")
        target = root / archived
        target.parent.mkdir(parents=True, exist_ok=True)
        # The archive is a sibling folder: the audit's own relative links are written again from there.
        target.write_text(rebase(text, posixpath.dirname(archived), archived), encoding="utf-8", newline="\n")
        moved = relink(root, audit, target)
        if moved:
            out.append(f"  {moved} link(s) into the audit now point at the archived copy")
        audit.write_text(f"# Spec traceability\n\nRetired on {today}: the requirements register in "
                         f"[`{FOLDER}/`](../requirements/) is the record; the last audit is "
                         f"[archived](../archive/spec-traceability-{today}.md).\n", encoding="utf-8", newline="\n")
        lost = id_problems(root, expected) + cell_problems(root, modules, archived)
        broken = link_problems(root, written)
        out += [f"  CONFIRM {problem}" for problem in lost + broken]
        if lost:
            out.append("Written, but the register does not hold every audit id once and every audit cell: compare "
                       "it with the archived audit before you commit.")
            return "\n".join(out) + "\n", False
        if broken:
            out.append("Written, but check-book will fail on the links above: fix each before you commit.")
            return "\n".join(out) + "\n", False
        if misaligned:
            out.append(f"Written, but {len(misaligned)} audit row(s) could not be matched to their header: each is "
                       "kept whole in the evidence appendix - split it into its register row by hand before you "
                       "commit.")
            return "\n".join(out) + "\n", False
        out.append("Written. Review `git diff`, read each row flagged to review and each CONFIRM, and commit.")
    elif misaligned:
        out.append(f"{len(misaligned)} audit row(s) cannot be matched to their header: escape each stray pipe (\\|) "
                   "in the audit before --apply, or split each row by hand after it.")
        return "\n".join(out) + "\n", False
    return "\n".join(out) + "\n", True
