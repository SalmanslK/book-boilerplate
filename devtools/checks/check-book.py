#!/usr/bin/env python3
"""
Check the book — the documents we maintain — against its own rules.

The rules are in CLAUDE.md ("Where knowledge lives") and docs/CLAUDE.md, and the Book's rules files they import. Ten of them a
program can hold, so this one does, and fails the build when one breaks:

1. Every link resolves. A relative link in a live document points at a file that exists, and
   an anchor at a heading that exists. A moved or renamed document otherwise breaks its links
   silently.
2. Every document has an owner. Each Markdown file in docs/, and at the repository root, is
   named in CLAUDE.md's owner table or sits in a folder the table names. A document the map
   does not mention is one nobody knows to keep current.
3. History stays in the archive. No live document carries a re-trace log, a changelog or a
   struck-through statement — docs/CLAUDE.md's "current state, not history". Each live document
   that keeps a dated log has an archive file for it instead.
4. The rules are read as Claude Code reads them: CLAUDE.md with its `@path` imports in place, so the
   Book's owner table (.claude/book/rules.md) and the project's own count as one.
5. An id is defined once. No D id heads two rows of defects.md (Open, or a Won't fix section) or of
   its archive, or two Fixed entries, and no open or won't-fix defect is also fixed, nor an open one
   archived; no Q id heads two sections or Still open summary rows of open-questions.md, or two
   sections of its archive (`##` or `####`), and no Still open question is also archived
   (docs/CLAUDE.md, Ids). A closed id's one-line entry beside its archived full row is not a repeat.
6. The defect ledger keeps its shape. An open row's Status is Open, Mitigated, Partial or Blocked — <who>,
   and it says when it was found; an Open, Mitigated or Partial row carries a Type and a Source
   (`<source>: <reference>`) from .claude/book/docs.md's lists. A value off a list fails; a missing one fails
   for a row found on or after `[book] type_source_from` and is warned of before it, as is a ledger without
   the columns. The archive's rows and the Fixed entries are never judged: a closed row is never rewritten.
7. An open question is headed the project's way. Its recommendation starts with `[book]
   recommendation_heading` (default **Recommendation:**); one headed with the default where the project set
   another fails, and one with no heading is warned of, so a question from before the rule never blocks.
8. A question's State is one of the four, with the fields it needs (POSTED a Posted date, THREAD ONLY an
   Answered line, RESOLVED the In FSD text), in the section it belongs to; a RESOLVED one is in the archive.
   A live question with no State line is warned of.
9. The Book's files are the Book's. Each file devtools/factory/book-files.lock records (written by `book
   init` and `book upgrade --apply`) still has the content the Book wrote; one that differs fails, naming
   where the change belongs, unless `[book] drift_accepted` holds it with a reason (a warning).
10. The requirements register (docs/requirements/<module>.md) keeps its shape: its tables' columns, a status
   from the list, every id once (starting with `[requirements] id_prefix` where one is set), a CONTRADICTED row
   naming its defect, a DEFERRED one its reason, a VERIFIED one a test. A register converted from an old audit
   only warns of what the audit lacked (a test, a defect, a unique id); a REVERIFY row is warned of.

It also reports — without failing — the documents over the size the audit flagged, so growth is
seen before it hurts. A passing check is not proof the book is well organised; it proves only
these ten things.

Not checked, on purpose: docs/archive/ (written once, never maintained), requirements/ (the
client's documents) and devtools/factory/book-template/ (the template's files, live only once
`book init` copies them into a Book) — except that rule 5 reads the two archived ledgers.

Usage (from the repository root):
    python3 devtools/checks/check-book.py      # exit 1 when a rule is broken

Runs in CI and in .githooks/pre-commit, beside the generated-docs checks. Python 3, standard
library only.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
# The settings: factory.toml merged over the Book's defaults (devtools/factory/book_config.py).
_FACTORY = next((p / "devtools" / "factory" for p in Path(__file__).resolve().parents
                 if (p / "devtools" / "factory" / "book_config.py").is_file()), None)
if _FACTORY is not None:
    sys.path.insert(0, str(_FACTORY))
import book_config  # noqa: E402
import requirements_register  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
# The template and its packs are not live here: their links resolve once `book init` has copied them.
NOT_LIVE = ("docs/archive/", "requirements/", "devtools/factory/book-template/")
# Written only by /book:fact-check, which marks a closed finding by striking it through: its own convention.
HISTORY_EXEMPT = ("docs/Audit/",)
# Instruction files carry rules, not topics; CLAUDE.md's "Rules to follow" row names them as a set.
INSTRUCTION_FILES = {"CLAUDE.md", "AGENTS.md"}
# A link written as a pattern, not a path: `R-nnn` in a template, `<n>` in a naming rule.
PLACEHOLDER = re.compile(r"R-nnn|<[^>]+>|\{[^}]+\}")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
# A heading that files history in a live document. A module heading saying when it was traced
# ("Widgets — traced 2026-09-03") states the current trace and is fine.
HISTORY_HEADING = re.compile(r"^#{2,6} (Re-traced|Re-checked|Changelog|Change log)\b", re.IGNORECASE)
STRUCK = re.compile(r"~~[^~\n]+~~")


def book_setting(key: str, default):
    """One `[book]` value from devtools/factory/factory.toml, or `default` when it is not set."""
    try:
        value = (book_config.load(ROOT).get("book") or {}).get(key)
    except (OSError, ImportError, ValueError):
        value = None
    return default if value in (None, "") else value


# A live document over this many lines is named, as a warning (`[book] size_warning`).
SIZE_WARNING = int(book_setting("size_warning", 1500))


def tracked_markdown(root: Path = ROOT) -> list[str]:
    """The tracked live documents still in the working tree: one deleted there is not read (a Book file's absence
    is the drift check's to name, a link to it the link check's)."""
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=root, capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    return [path for path in out.splitlines() if path and not path.startswith(NOT_LIVE) and (root / path).is_file()]


def unfenced_lines(text: str):
    """(line number, line) outside fenced code blocks."""
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield number, line


def prose_lines(text: str):
    """Unfenced lines with inline code removed — a link or a `~~` inside code is not prose."""
    for number, line in unfenced_lines(text):
        yield number, re.sub(r"`[^`]*`", "", line)


def slug(heading: str) -> str:
    """GitHub's and Bitbucket's anchor for a heading."""
    text = re.sub(r"<[^>]+>", "", heading.strip()).lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


_anchors: dict[Path, set[str]] = {}


def anchors(path: Path) -> set[str]:
    if path not in _anchors:
        found: set[str] = set()
        seen: dict[str, int] = {}
        # A heading's code spans are part of its anchor: "`factory/` — the AI tools" → factory--the-ai-tools.
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name == "CLAUDE.md":
            # Read as Claude Code reads it: a heading the file imports from the Book's rules is one of its own.
            text = book_config.expanded(path.parent, path.name) or text
        for _, line in unfenced_lines(text):
            match = HEADING.match(line)
            if match:
                base = slug(match.group(2))
                found.add(base if base not in seen else f"{base}-{seen[base]}")
                seen[base] = seen.get(base, 0) + 1
        raw = path.read_text(encoding="utf-8", errors="replace")
        found.update(re.findall(r'<a\s+(?:name|id)="([^"]+)"', raw))
        _anchors[path] = found
    return _anchors[path]


def link_problems(files: list[str], root: Path = ROOT) -> list[str]:
    problems = []
    for rel in files:
        path = root / rel
        for number, line in prose_lines(path.read_text(encoding="utf-8", errors="replace")):
            for target in LINK.findall(line):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target) or PLACEHOLDER.search(target):
                    continue
                file_part, _, anchor = target.partition("#")
                dest = (path.parent / file_part).resolve() if file_part else path
                if file_part and not dest.exists():
                    problems.append(f"{rel}:{number}: links to {target}, which does not exist")
                elif anchor and dest.is_file() and dest.suffix == ".md" and anchor.lower() not in anchors(dest):
                    problems.append(f"{rel}:{number}: links to {target}, but that heading does not exist")
    return problems


def owned_patterns(root: Path = ROOT) -> list[re.Pattern]:
    """The paths CLAUDE.md's owner tables name: a file, a folder (`…/`), or a pattern (`<n>`). CLAUDE.md is
    read as Claude Code reads it, its imports in place: the Book's table is in `.claude/book/rules.md`, and
    each "Where knowledge lives" section after it (the project's own documents) adds its rows."""
    text = book_config.expanded(root, "CLAUDE.md") or ""
    if "## Where knowledge lives" not in text:
        return []  # owner_problems reports the missing table; a brownfield CLAUDE.md has none yet
    table = "\n".join(part.split("\n## ", 1)[0] for part in text.split("## Where knowledge lives")[1:])
    patterns = []
    for line in table.splitlines():
        if not line.startswith("|"):
            continue
        for token in re.findall(r"`([^`]+)`", line.split("|")[2] if line.count("|") > 2 else ""):
            if "/" not in token and not token.endswith(".md"):
                continue
            pattern = re.escape(token).replace(re.escape("<n>"), r"[^/]+")
            patterns.append(re.compile(f"^{pattern}" + ("" if token.endswith("/") else "$")))
    return patterns


def owner_problems(files: list[str], root: Path = ROOT) -> list[str]:
    patterns = owned_patterns(root)
    if not patterns:
        return ["CLAUDE.md: the owner table under \"Where knowledge lives\" names no paths"]
    problems = []
    for rel in files:
        in_scope = rel.startswith("docs/") or "/" not in rel
        if not in_scope or Path(rel).name in INSTRUCTION_FILES:
            continue
        if not any(pattern.match(rel) for pattern in patterns):
            problems.append(f"{rel}: no owner — add its row to CLAUDE.md \"Where knowledge lives\", "
                            "or move it under a folder the table names")
    return problems


def history_problems(files: list[str], root: Path = ROOT, converted: bool = False) -> list[str]:
    """History filed in a live document. A register or appendix converted from an old audit carries the audit's
    own struck-through history until a person tidies it: `converted=True` reads only those (for a warning),
    `False` every other document."""
    problems = []
    for rel in files:
        if rel.startswith(HISTORY_EXEMPT):
            continue
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        if (requirements_register.CONVERTED in text and rel.startswith(requirements_register.FOLDER)) != converted:
            continue
        for number, line in prose_lines(text):
            if HISTORY_HEADING.match(line):
                problems.append(f"{rel}:{number}: a history heading in a live document — fold what it found into "
                                "the current text; the log goes to its archive file (docs/CLAUDE.md)")
            elif STRUCK.search(line):
                problems.append(f"{rel}:{number}: struck-through text — state what is true now; "
                                "history goes to the archive (docs/CLAUDE.md)")
    return problems


FOUND_ON = re.compile(r"\bfound (\d{4}-\d{2}-\d{2})\b")


table_cells = book_config.table_cells  # one splitter for every Book table: `\|` stays in its cell


# docs/CLAUDE.md, Ids: an id is never reused, so each D and Q id is defined once. An id is defined where it
# heads a row or an entry; anywhere else it is a reference. A closed id is written twice by design: a fixed
# defect's full row moves to the archive and leaves a one-line entry in defects.md's Fixed index, and an
# answered question's full section moves to the archive and leaves a one-line entry under Resolved (a
# Superseded question may already have been answered and archived). An id still open must not also be in
# the archive. id_status.py, run by /book:sync-sources, reports the statuses; this holds the duplicates.
DEFECTS, DEFECTS_ARCHIVE = "docs/defects.md", "docs/archive/defects-closed.md"
QUESTIONS, QUESTIONS_ARCHIVE = "docs/open-questions.md", "docs/archive/questions-resolved.md"
DEFECT_ROW = re.compile(r"^\|\s*(?:\*\*)?(D\d+)(?:\*\*)?\s*\|")  # `| D21 | 🟠 | …`, the id plain or in bold
DEFECT_FIXED_ENTRY = re.compile(r"^- \*\*(D\d+)\*\* [—·-]")      # `- **D21** — what it was …`
QUESTION_SECTION = re.compile(r"^#### (Q\d+) [—·]")               # #### Q59 — May …
QUESTION_ARCHIVED = re.compile(r"^#{2}(?:##)? (Q\d+) [—·]")       # ## or #### Q59 — May … (the archive)
QUESTION_SUMMARY_ROW = re.compile(r"^\| (?:⛔ )?\[(Q\d+)\]\(")    # | [Q122](#q122--…) | … (Still open's tables)
# defects.md's sections: open rows under Open, one-line entries (bullets or table rows) under Fixed, and any
# other section's rows (Won't fix) closed by decision. open-questions.md: the summary tables are Still open's,
# above its questions in full (a table inside a question's own section lists other questions: a reference).
DEFECTS_OPEN, DEFECTS_FIXED = "Open", "Fixed"
QUESTIONS_OPEN, QUESTIONS_IN_FULL = "Still open", "The open questions in full"


def id_definitions(root: Path, rel: str, places: dict[str, tuple]) -> dict[str, list]:
    """
    {place: [(id, line number, its ## section)]} for each line of `rel` that one of `places[place]`'s
    patterns matches, under a ## and ### heading its test accepts.
    """
    found: dict[str, list] = {place: [] for place in places}
    path = root / rel
    if not path.is_file():
        return found
    section = subsection = ""
    for number, line in unfenced_lines(path.read_text(encoding="utf-8", errors="replace")):
        if line.startswith("## "):
            section, subsection = line[3:].strip(), ""
        elif line.startswith("### "):
            subsection = line[4:].strip()
        for place, (patterns, where) in places.items():
            match = next((m for m in (pattern.match(line) for pattern in patterns) if m), None)
            if match and where(section, subsection):
                found[place].append((match.group(1), number, section))
    return found


def repeated(rel: str, what: str, definitions: list) -> list[str]:
    lines: dict[str, list[int]] = {}
    for ident, number, _ in definitions:
        lines.setdefault(ident, []).append(number)
    return [f"{rel}: {ident} {what} {len(numbers)} times (lines {', '.join(map(str, numbers))}) — "
            "an id is never reused (docs/CLAUDE.md, Ids)"
            for ident, numbers in sorted(lines.items(), key=lambda item: int(item[0][1:])) if len(numbers) > 1]


def also_in(rel: str, what: str, definitions: list, other_rel: str, other: list, other_what: str) -> list[str]:
    elsewhere = {ident: number for ident, number, _ in other}
    return [f"{rel}:{number}: {ident} {what}, but {other_rel}:{elsewhere[ident]} {other_what} — "
            "one id, one defect or question (docs/CLAUDE.md, Ids)"
            for ident, number, _ in definitions if ident in elsewhere]


def id_problems(root: Path = ROOT) -> list[str]:
    """Each D and Q id defined twice in one place, or both open and closed."""
    anywhere = lambda section, subsection: True  # noqa: E731
    defects = id_definitions(root, DEFECTS, {
        "row": ((DEFECT_ROW,), lambda section, subsection: section != DEFECTS_FIXED),
        "fixed": ((DEFECT_ROW, DEFECT_FIXED_ENTRY), lambda section, subsection: section == DEFECTS_FIXED)})
    open_d = [entry for entry in defects["row"] if entry[2] == DEFECTS_OPEN]
    archived_d = id_definitions(root, DEFECTS_ARCHIVE, {"row": ((DEFECT_ROW,), anywhere)})["row"]
    questions = id_definitions(root, QUESTIONS, {
        "section": ((QUESTION_SECTION,), anywhere),
        "summary": ((QUESTION_SUMMARY_ROW,),
                    lambda section, subsection: section == QUESTIONS_OPEN and subsection != QUESTIONS_IN_FULL)})
    archived_q = id_definitions(root, QUESTIONS_ARCHIVE, {"section": ((QUESTION_ARCHIVED,), anywhere)})["section"]
    still_open = [entry for entry in questions["section"] if entry[2] == QUESTIONS_OPEN]
    return (repeated(DEFECTS, "heads a row", defects["row"])
            + repeated(DEFECTS, "has a Fixed entry", defects["fixed"])
            + repeated(DEFECTS_ARCHIVE, "heads a row", archived_d)
            + also_in(DEFECTS, "heads a row", defects["row"], DEFECTS, defects["fixed"], "lists it as fixed")
            + also_in(DEFECTS, "is open", open_d, DEFECTS_ARCHIVE, archived_d, "holds its closed row")
            + repeated(QUESTIONS, "heads a section", questions["section"])
            + repeated(QUESTIONS, "heads a summary row", questions["summary"])
            + repeated(QUESTIONS_ARCHIVE, "heads a section", archived_q)
            + also_in(QUESTIONS, "is still open", still_open, QUESTIONS_ARCHIVE, archived_q, "holds it as resolved"))


# The Book's rules for documents (.claude/book/docs.md, Defects): an open row's Status is one of these, it says
# when it was found, and an Open, Mitigated or Partial row carries a Type and a Source from the lists below. The
# archive's rows and the one-line Fixed entries keep what they say: a closed row is never rewritten.
STATUSES = re.compile(r"^(?:Open|Mitigated|Partial|Blocked — \S.*)$")
CLASSIFIED = ("Open", "Mitigated", "Partial")
TYPES = ("spec-divergence", "bug", "regression", "test-gap", "security", "data", "config-infra", "tooling-ci")
SOURCES = ("qa", "fe", "ba-product", "requirement-sync", "code-review", "audit", "tests-ci", "developer")
SOURCE_VALUE = re.compile(r"^`?([a-z][\w-]*)`?\s*(?::\s*(.*))?$")
# A row found before `[book] type_source_from` (the day a Book took the columns) with no Type or Source is only
# warned of, until `book defects classify` proposes them; one found on or after it fails.
TYPE_SOURCE_FROM = str(book_setting("type_source_from", "1970-01-01"))
RULES_FILE = ".claude/book/docs.md"
EMPTY = {"", "—", "-"}


def cell(cells: list[str], column: int | None) -> str:
    return cells[column].replace("*", "").strip() if column is not None and column < len(cells) else ""


def type_source_problems(ident: str, where: str, kind: str, source: str, found: str) -> tuple[list[str], list[str]]:
    """One classified row's Type and Source: (problems, warnings)."""
    problems, warnings = [], []
    kind = kind.strip("`")
    if kind in EMPTY or source in EMPTY:
        missing = " and ".join(name for name, value in (("Type", kind), ("Source", source)) if value in EMPTY)
        text = f"{where}: {ident} has no {missing}"
        if found and found >= TYPE_SOURCE_FROM:
            problems.append(f"{text} ({RULES_FILE}, Defects)")
        else:
            warnings.append(f"{text} — `book defects classify` proposes one")
    if kind not in EMPTY and kind not in TYPES:
        problems.append(f"{where}: {ident}'s Type {kind!r} is not one of {', '.join(TYPES)} ({RULES_FILE}, Defects)")
    if source not in EMPTY:
        match = SOURCE_VALUE.match(source)
        if not match or match.group(1) not in SOURCES:
            problems.append(f"{where}: {ident}'s Source {source!r} does not start with one of {', '.join(SOURCES)} "
                            f"({RULES_FILE}, Defects)")
        elif not (match.group(2) or "").strip():
            problems.append(f"{where}: {ident}'s Source {source!r} has no reference — `{match.group(1)}: <the PR, "
                            f"issue, FSD version or row>` ({RULES_FILE}, Defects)")
    return problems, warnings


def ledger_problems(root: Path = ROOT) -> tuple[list[str], list[str]]:
    """(problems, warnings): each open row's Status, found date, Type and Source."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", TYPE_SOURCE_FROM):
        # Compared as text, "2026/11/20" sorts after every real date and would exempt every row.
        return [f"devtools/factory/factory.toml: [book] type_source_from is {TYPE_SOURCE_FROM!r}, not a YYYY-MM-DD "
                "date - the defects' Type and Source could not be checked"], []
    path = root / DEFECTS
    if not path.is_file():
        return [], []
    problems, warnings = [], []
    section, columns, old_shape = "", {}, False
    for number, line in unfenced_lines(path.read_text(encoding="utf-8", errors="replace")):
        if line.startswith("## "):
            section, columns = line[3:].strip(), {}
            continue
        if not line.lstrip().startswith("|"):
            columns = {}
            continue
        cells = table_cells(line)
        if "Status" in cells:
            columns = {name: cells.index(name) for name in ("Status", "Type", "Source") if name in cells}
            continue
        row = DEFECT_ROW.match(line)
        if not row or section != DEFECTS_OPEN:
            continue
        ident, found, where = row.group(1), FOUND_ON.search(line), f"{DEFECTS}:{number}"
        status = cell(cells, columns.get("Status"))
        if not STATUSES.match(status):
            problems.append(f"{where}: {ident}'s Status {status!r} is not Open, Mitigated, Partial or "
                            f"Blocked — <Q id or party> ({RULES_FILE}, Defects)")
        if not found:
            problems.append(f"{where}: {ident} is open but does not say when it was found — end its Note with "
                            f"*found YYYY-MM-DD* ({RULES_FILE}, Defects)")
        if "Type" not in columns or "Source" not in columns:
            old_shape = True
            continue
        if status in CLASSIFIED or cell(cells, columns["Type"]) not in EMPTY:
            more = type_source_problems(ident, where, cell(cells, columns["Type"]), cell(cells, columns["Source"]),
                                        found.group(1) if found else "")
            problems += more[0] if status in CLASSIFIED else [p for p in more[0] if " has no " not in p]
            warnings += more[1] if status in CLASSIFIED else []
    if old_shape:
        warnings.append(f"{DEFECTS}: the Open table has no Type and Source columns — `book defects classify` "
                        "proposes each open row's, for you to approve")
    return problems, warnings


def defect_counts(root: Path = ROOT) -> tuple[dict[str, int], dict[str, int]]:
    """The open defects by Type and by Source (its name, without the reference); `—` where a row has none."""
    path = root / DEFECTS
    types: dict[str, int] = {}
    sources: dict[str, int] = {}
    if not path.is_file():
        return types, sources
    section, columns = "", {}
    for _, line in unfenced_lines(path.read_text(encoding="utf-8", errors="replace")):
        if line.startswith("## "):
            section, columns = line[3:].strip(), {}
            continue
        if not line.lstrip().startswith("|"):
            columns = {}
            continue
        cells = table_cells(line)
        if "Status" in cells:
            columns = {name: cells.index(name) for name in ("Type", "Source") if name in cells}
            continue
        if section != DEFECTS_OPEN or not DEFECT_ROW.match(line):
            continue
        kind = cell(cells, columns.get("Type")).strip("`") or "—"
        source = cell(cells, columns.get("Source"))
        match = SOURCE_VALUE.match(source)
        name = match.group(1) if match and source not in EMPTY else "—"
        types[kind] = types.get(kind, 0) + 1
        sources[name] = sources.get(name, 0) + 1
    return types, sources


# An open question's recommendation starts with the project's own heading, `[book] recommendation_heading`
# (book verify N10 reads the same key). The template's default written into a project that set another is
# what a skill following the template literally writes: that fails. No heading at all is only warned of,
# so a question written before the heading was a rule never blocks a commit (Book D51).
DEFAULT_RECOMMENDATION = "**Recommendation:**"
RECOMMENDATION = str(book_setting("recommendation_heading", DEFAULT_RECOMMENDATION) or DEFAULT_RECOMMENDATION)
QUESTION_BLOCK = re.compile(r"^#{3,4} (Q\d+)\b")
OPEN_STATUS = re.compile(r"^\*\*(?:Status|State):\*\*\s*`?OPEN\b")


def recommendation_problems(root: Path = ROOT, heading: str = "") -> tuple[list[str], list[str]]:
    """(problems, warnings): each OPEN question in open-questions.md, and the heading its recommendation uses."""
    heading = heading or RECOMMENDATION
    path = root / QUESTIONS
    if not path.is_file():
        return [], []
    blocks: list[tuple[str, int, list[str]]] = []
    for number, line in unfenced_lines(path.read_text(encoding="utf-8", errors="replace")):
        match = QUESTION_BLOCK.match(line)
        if match:
            blocks.append((match.group(1), number, []))
        elif line.startswith("#"):
            blocks.append(("", number, []))
        elif blocks:
            blocks[-1][2].append(line)
    problems, warnings = [], []
    for ident, number, body in blocks:
        if not ident or not any(OPEN_STATUS.match(line) for line in body):
            continue
        if any(line.lstrip().startswith(heading) for line in body):
            continue
        if heading != DEFAULT_RECOMMENDATION and any(l.lstrip().startswith(DEFAULT_RECOMMENDATION) for l in body):
            problems.append(f"{QUESTIONS}:{number}: {ident} is open and its recommendation is headed "
                            f"{DEFAULT_RECOMMENDATION} - this project's heading is {heading} "
                            "([book] recommendation_heading in devtools/factory/factory.toml)")
        else:
            warnings.append(f"{QUESTIONS}:{number}: {ident} is open with no {heading} line")
    return problems, warnings


# The Book's rules for documents (.claude/book/docs.md, Questions): a question's State, and the fields that
# State needs, on one line under its heading. A question with no State line (one written before the rule) is
# only warned of, until /book:check-sources assigns one; the archive's older sections are never judged.
NOT_POSTED, POSTED, THREAD_ONLY, RESOLVED = "OPEN · NOT POSTED", "OPEN · POSTED", "ANSWERED · THREAD ONLY", "RESOLVED"
STATES = (NOT_POSTED, POSTED, THREAD_ONLY, RESOLVED)
STATE_LINE = re.compile(r"^\*\*State:\*\*\s*(.*)$")
STATE_FIELD = re.compile(r"\*\*([A-Z][\w ]*?):\*\*\s*(.*?)(?=\s*·\s*\*\*[A-Z][\w ]*?:\*\*|$)")
QUESTIONS_ANSWERED = "Answered, waiting for the FSD text"
NOT_SET = {"", "—", "-", "not yet"}


def state_fields(line: str) -> dict[str, str]:
    """`**State:** OPEN · POSTED · **Posted:** 2026-11-02, thread · …` → {"State": "OPEN · POSTED", "Posted": …}."""
    return {name: value.strip().strip("`").strip() for name, value in STATE_FIELD.findall(line.strip())}


def question_blocks(path: Path, heading: re.Pattern) -> list[tuple[str, int, str, list[str]]]:
    """(id, line number, the `## ` section it is in, its body lines) for each question section in a ledger."""
    blocks, section = [], ""
    for number, line in unfenced_lines(path.read_text(encoding="utf-8", errors="replace")):
        match = heading.match(line)
        if line.startswith("## ") and not match:
            section = line[3:].strip()
        if match:
            blocks.append((match.group(1), number, section, []))
        elif line.startswith("#"):
            blocks.append(("", number, section, []))
        elif blocks:
            blocks[-1][3].append(line)
    return [block for block in blocks if block[0]]


def question_state(body: list[str]) -> dict[str, str] | None:
    line = next((line for line in body if STATE_LINE.match(line.strip())), None)
    return state_fields(line) if line is not None else None


def state_problems(ident: str, where: str, fields: dict[str, str], section: str, live: bool) -> list[str]:
    """One question's State against the rules: its value, the fields it needs, where it lives."""
    state, rule = fields.get("State", ""), "(.claude/book/docs.md, Questions)"
    if state not in STATES:
        return [f"{where}: {ident}'s State {state!r} is not one of {', '.join(STATES)} {rule}"]
    problems = []
    needs = {POSTED: ["Posted"], THREAD_ONLY: ["Posted", "Answered"], RESOLVED: ["Answered", "In FSD text"]}
    for field in needs.get(state, []):
        if fields.get(field, "") in NOT_SET:
            problems.append(f"{where}: {ident} is {state} but has no {field} {rule}")
    if live and state == RESOLVED:
        problems.append(f"{where}: {ident} is RESOLVED but still in {QUESTIONS} — move its section to "
                        f"{QUESTIONS_ARCHIVE} and leave one line under *Resolved* {rule}")
    elif live and state == THREAD_ONLY and section != QUESTIONS_ANSWERED:
        problems.append(f"{where}: {ident} is {state}, so it belongs under ## {QUESTIONS_ANSWERED} {rule}")
    elif live and state in (NOT_POSTED, POSTED) and section != QUESTIONS_OPEN:
        problems.append(f"{where}: {ident} is {state}, so it belongs under ## {QUESTIONS_OPEN} {rule}")
    elif not live and state != RESOLVED:
        problems.append(f"{where}: {ident} is archived but its State is {state}, not RESOLVED {rule}")
    return problems


def question_state_problems(root: Path = ROOT) -> tuple[list[str], list[str]]:
    """(problems, warnings): each question's State in the live ledger, and each archived one that has a State."""
    problems, warnings = [], []
    live = root / QUESTIONS
    if live.is_file():
        for ident, number, section, body in question_blocks(live, QUESTION_BLOCK):
            if section not in (QUESTIONS_OPEN, QUESTIONS_ANSWERED):
                continue  # Superseded and Resolved hold no live question
            fields = question_state(body)
            if fields is None:
                warnings.append(f"{QUESTIONS}:{number}: {ident} has no **State:** line — /book:check-sources "
                                "proposes one")
            else:
                problems += state_problems(ident, f"{QUESTIONS}:{number}", fields, section, True)
    archive = root / QUESTIONS_ARCHIVE
    if archive.is_file():
        for ident, number, section, body in question_blocks(archive, QUESTION_ARCHIVED):
            fields = question_state(body)
            if fields is not None and "SUPERSEDED" not in fields.get("State", ""):
                problems += state_problems(ident, f"{QUESTIONS_ARCHIVE}:{number}", fields, section, False)
    return problems, warnings


def question_counts(root: Path = ROOT) -> dict[str, int]:
    """How many live questions are in each State; those with none are counted as `no State yet`."""
    counts = {state: 0 for state in (NOT_POSTED, POSTED, THREAD_ONLY)}
    live = root / QUESTIONS
    if live.is_file():
        for _, _, section, body in question_blocks(live, QUESTION_BLOCK):
            if section in (QUESTIONS_OPEN, QUESTIONS_ANSWERED):
                state = (question_state(body) or {}).get("State", "no State yet")
                counts[state] = counts.get(state, 0) + 1
    return counts


def size_warnings(files: list[str], root: Path = ROOT) -> list[str]:
    sizes = [(sum(1 for _ in (root / rel).open(encoding="utf-8", errors="replace")), rel) for rel in files]
    return [f"{rel}: {lines:,} lines" for lines, rel in sorted(sizes, reverse=True) if lines > SIZE_WARNING]


def main(root: Path = ROOT) -> int:
    files = tracked_markdown(root)
    failed = False
    questions = recommendation_problems(root)
    ledger = ledger_problems(root)
    states = question_state_problems(root)
    try:
        drift = book_config.drift(root, book_config.load(root))
    except (OSError, ValueError) as error:
        drift = ([f"devtools/factory/factory.toml: {error} - the Book's files could not be compared"], [])
    try:
        prefix = str((book_config.load(root).get("requirements") or {}).get("id_prefix") or "")
    except (OSError, ValueError):
        prefix = ""
    register = requirements_register.problems(root, prefix)
    for title, problems in (("Links", link_problems(files, root)), ("Owners", owner_problems(files, root)),
                            ("History", history_problems(files, root)),
                            ("Ids", id_problems(root)), ("Ledger", ledger[0]),
                            ("Questions", questions[0] + states[0]), ("Book files", drift[0]),
                            ("Requirements", register[0])):
        if problems:
            failed = True
            print(f"{title}: {len(problems)} problem(s)")
            for problem in problems:
                print(f"  {problem}")
    for warning in questions[1] + states[1]:
        print(f"Questions (a warning, not a failure): {warning}")
    for warning in ledger[1]:
        print(f"Ledger (a warning, not a failure): {warning}")
    for warning in drift[1]:
        print(f"Book files (a warning, not a failure): {warning}")
    for warning in register[1]:
        print(f"Requirements (a warning, not a failure): {warning}")
    for warning in history_problems(files, root, converted=True):
        print(f"History in a converted register (a warning, not a failure): {warning}")
    large = size_warnings(files, root)
    if large:
        print(f"Size (a warning, not a failure): {len(large)} document(s) over {SIZE_WARNING:,} lines")
        for entry in large:
            print(f"  {entry}")
    print("The book breaks one of its rules." if failed else f"The book keeps its rules ({len(files)} documents).")
    return 1 if failed else 0


if __name__ == "__main__":
    # UTF-8 out, so an em dash prints on a Windows console's code page too (Book D45).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    sys.exit(main())
