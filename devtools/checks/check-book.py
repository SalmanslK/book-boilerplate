#!/usr/bin/env python3
"""
Check the book — the documents we maintain — against its own rules.

The rules are in CLAUDE.md ("Where knowledge lives") and docs/CLAUDE.md. Six of them a
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
4. A defect says who found it in the shared words. A row in defects.md or its archive found on or
   after FOUND_BY_FROM starts its *Found by* with one of FOUND_BY_WORDS (docs/CLAUDE.md, Defects).
5. An id is defined once. No D id heads two rows of defects.md (Open, or a Won't fix section) or of
   its archive, or two Fixed entries, and no open or won't-fix defect is also fixed, nor an open one
   archived; no Q id heads two sections or Still open summary rows of open-questions.md, or two
   sections of its archive (`##` or `####`), and no Still open question is also archived
   (docs/CLAUDE.md, Ids). A closed id's one-line entry beside its archived full row is not a repeat.
6. The defect ledger keeps its shape. An open row's Status is Open, Mitigated, Partial or Blocked — <who>,
   and it says when it was found; every row found on or after `[book] class_enforced_from` carries
   *Class: <class> · found <date>* with a class docs/CLAUDE.md's Defects table lists. Rows found before
   that date keep the label they were written with: a closed row is never rewritten.

It also reports — without failing — the documents over the size the audit flagged, so growth is
seen before it hurts. A passing check is not proof the book is well organised; it proves only
these six things.

Not checked, on purpose: docs/archive/ (written once, never maintained), requirements/ (the
client's documents) and devtools/factory/book-template/ (the template's files, live only once
`factory book init` copies them into a Book) — except that rules 4 and 5 read the two archived ledgers.

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
        import tomllib
        with open(ROOT / "devtools" / "factory" / "factory.toml", "rb") as handle:
            value = (tomllib.load(handle).get("book") or {}).get(key)
    except (OSError, ImportError, ValueError):
        value = None
    return default if value in (None, "") else value


# A live document over this many lines is named, as a warning (`[book] size_warning`).
SIZE_WARNING = int(book_setting("size_warning", 1500))


def tracked_markdown(root: Path = ROOT) -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=root, capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    return [path for path in out.splitlines() if path and not path.startswith(NOT_LIVE)]


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
        for _, line in unfenced_lines(path.read_text(encoding="utf-8", errors="replace")):
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
    """The paths CLAUDE.md's owner table names: a file, a folder (`…/`), or a pattern (`<n>`)."""
    path = root / "CLAUDE.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if "## Where knowledge lives" not in text:
        return []  # owner_problems reports the missing table; a brownfield CLAUDE.md has none yet
    table = text.split("## Where knowledge lives", 1)[1].split("\n## ", 1)[0]
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


def history_problems(files: list[str], root: Path = ROOT) -> list[str]:
    problems = []
    for rel in files:
        if rel.startswith(HISTORY_EXEMPT):
            continue
        for number, line in prose_lines((root / rel).read_text(encoding="utf-8", errors="replace")):
            if HISTORY_HEADING.match(line):
                problems.append(f"{rel}:{number}: a history heading in a live document — fold what it found into "
                                "the current text; the log goes to its archive file (docs/CLAUDE.md)")
            elif STRUCK.search(line):
                problems.append(f"{rel}:{number}: struck-through text — state what is true now; "
                                "history goes to the archive (docs/CLAUDE.md)")
    return problems


# docs/CLAUDE.md, Defects: *Found by* starts with one of these words, so "who finds our defects" is a
# count. Rows found before the rule keep their wording, so only a row found on or after it is held to it.
FOUND_BY_WORDS = ("Review", "PR review", "Spec audit", "Requirements check", "Test", "Guard/CI", "While building",
                  "QA", "Consumer", "Client", "Requirement change")
# The adoption date (`[book] found_by_from`): a row found on or after it carries the words.
FOUND_BY_FROM = str(book_setting("found_by_from", "1970-01-01"))
FOUND_BY_LEDGERS = ("docs/defects.md", "docs/archive/defects-closed.md")
FOUND_BY_HEADER = "Found by"
FOUND_ON = re.compile(r"\bfound (\d{4}-\d{2}-\d{2})\b")
FOUND_BY_START = re.compile(r"^(?:" + "|".join(sorted(map(re.escape, FOUND_BY_WORDS), key=len, reverse=True))
                            + r")(?![\w/])")


def table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def found_by_problems(root: Path = ROOT) -> list[str]:
    """Each defect row found on or after FOUND_BY_FROM whose Found by does not start with a listed word."""
    problems = []
    for rel in FOUND_BY_LEDGERS:
        path = root / rel
        if not path.is_file():
            continue
        column = None
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.lstrip().startswith("|"):
                column = None
                continue
            cells = table_cells(line)
            if FOUND_BY_HEADER in cells:
                column = cells.index(FOUND_BY_HEADER)
                continue
            found = FOUND_ON.search(line)
            if column is None or column >= len(cells) or not found or found.group(1) < FOUND_BY_FROM:
                continue
            found_by = cells[column].replace("*", "").strip()
            if not FOUND_BY_START.match(found_by):
                problems.append(f"{rel}:{number}: Found by {found_by!r} does not start with one of "
                                f"{', '.join(FOUND_BY_WORDS)} (docs/CLAUDE.md, Defects)")
    return problems


# docs/CLAUDE.md, Ids: an id is never reused, so each D and Q id is defined once. An id is defined where it
# heads a row or an entry; anywhere else it is a reference. A closed id is written twice by design: a fixed
# defect's full row moves to the archive and leaves a one-line entry in defects.md's Fixed index, and an
# answered question's full section moves to the archive and leaves a one-line entry under Resolved (a
# Superseded question may already have been answered and archived). An id still open must not also be in
# the archive. id_status.py, run by /book:sync-sources, reports the statuses; this holds the duplicates.
DEFECTS, DEFECTS_ARCHIVE = "docs/defects.md", "docs/archive/defects-closed.md"
QUESTIONS, QUESTIONS_ARCHIVE = "docs/open-questions.md", "docs/archive/questions-resolved.md"
DEFECT_ROW = re.compile(r"^\|\s*(?:\*\*)?(D\d+)(?:\*\*)?\s*\|")  # | D21 | 🟠 | …, the id plain or in bold
DEFECT_FIXED_ENTRY = re.compile(r"^- \*\*(D\d+)\*\* [—·-]")      # - **D21** — what it was …
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


# docs/CLAUDE.md, Defects: an open row's Status is one of these, and every row found on or after the adoption
# date (`[book] class_enforced_from`) carries *Class: <a listed class> · found <date>*, the class one of the
# table's in docs/CLAUDE.md. Rows found before it keep their label: a closed row is never rewritten.
STATUSES = re.compile(r"^(?:Open|Mitigated|Partial|Blocked — \S.*)$")
CLASS_FROM = str(book_setting("class_enforced_from", "1970-01-01"))
CLASS_MARK = re.compile(r"Class:\s*`?([\w-]+)`?\s*·\s*found (\d{4}-\d{2}-\d{2})")
CLASS_ROW = re.compile(r"^\|\s*`([a-z][\w-]*)`\s*\|")
RULES_FILE = "docs/CLAUDE.md"


def defect_classes(root: Path = ROOT) -> list[str]:
    """The classes docs/CLAUDE.md's Defects section lists, in its table's order."""
    path = root / RULES_FILE
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    section = text.split("\n## Defects", 1)[1].split("\n## ", 1)[0] if "\n## Defects" in text else ""
    return [match.group(1) for line in section.splitlines() if (match := CLASS_ROW.match(line.strip()))]


def ledger_problems(root: Path = ROOT) -> list[str]:
    """Open rows' Status and found date; the class of every row found on or after CLASS_FROM."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", CLASS_FROM):
        # Compared as text, "2026/11/20" sorts after every real date and would exempt every row.
        return [f"devtools/factory/factory.toml: [book] class_enforced_from is {CLASS_FROM!r}, not a YYYY-MM-DD date "
                "- the defect classes could not be checked"]
    classes = defect_classes(root)
    problems = []
    for rel in (DEFECTS, DEFECTS_ARCHIVE):
        path = root / rel
        if not path.is_file():
            continue
        section, status_column = "", None
        for number, line in unfenced_lines(path.read_text(encoding="utf-8", errors="replace")):
            if line.startswith("## "):
                section, status_column = line[3:].strip(), None
                continue
            if not line.lstrip().startswith("|"):
                status_column = None
                continue
            cells = table_cells(line)
            if "Status" in cells:
                status_column = cells.index("Status")
                continue
            row = DEFECT_ROW.match(line)
            if not row or (rel == DEFECTS and section == DEFECTS_FIXED):
                continue
            ident, mark, found = row.group(1), CLASS_MARK.search(line), FOUND_ON.search(line)
            if rel == DEFECTS and section == DEFECTS_OPEN:
                status = cells[status_column].replace("*", "").strip() if status_column is not None \
                    and status_column < len(cells) else ""
                if not STATUSES.match(status):
                    problems.append(f"{rel}:{number}: {ident}'s Status {status!r} is not Open, Mitigated, Partial or "
                                    "Blocked — <Q id or party> (docs/CLAUDE.md, Defects)")
                if not found:
                    problems.append(f"{rel}:{number}: {ident} is open but does not say when it was found — end it "
                                    "with *Class: <class> · found YYYY-MM-DD* (docs/CLAUDE.md, Defects)")
            date = (mark.group(2) if mark else found.group(1) if found else "")
            if not date or date < CLASS_FROM:
                continue
            if not mark:
                problems.append(f"{rel}:{number}: {ident}, found {date}, has no *Class: <class> · found <date>* "
                                "(docs/CLAUDE.md, Defects)")
            elif not classes:
                problems.append(f"{rel}:{number}: {ident}'s class could not be checked — {RULES_FILE} has no class "
                                "table under ## Defects")
            elif mark.group(1) not in classes:
                problems.append(f"{rel}:{number}: {ident}'s class {mark.group(1)!r} is not in {RULES_FILE}'s list "
                                f"({', '.join(classes)}) — use the nearest listed one, or add the class there first")
    return problems


def size_warnings(files: list[str], root: Path = ROOT) -> list[str]:
    sizes = [(sum(1 for _ in (root / rel).open(encoding="utf-8", errors="replace")), rel) for rel in files]
    return [f"{rel}: {lines:,} lines" for lines, rel in sorted(sizes, reverse=True) if lines > SIZE_WARNING]


def main(root: Path = ROOT) -> int:
    files = tracked_markdown(root)
    failed = False
    for title, problems in (("Links", link_problems(files, root)), ("Owners", owner_problems(files, root)),
                            ("History", history_problems(files, root)), ("Found by", found_by_problems(root)),
                            ("Ids", id_problems(root)), ("Ledger", ledger_problems(root))):
        if problems:
            failed = True
            print(f"{title}: {len(problems)} problem(s)")
            for problem in problems:
                print(f"  {problem}")
    large = size_warnings(files, root)
    if large:
        print(f"Size (a warning, not a failure): {len(large)} document(s) over {SIZE_WARNING:,} lines")
        for entry in large:
            print(f"  {entry}")
    print("The book breaks one of its rules." if failed else f"The book keeps its rules ({len(files)} documents).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
