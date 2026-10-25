#!/usr/bin/env python3
"""The referee's check of a project.md an AI wrote from an FSD.

    python check_project_md.py <project.md> <fsd file> [--feature-depth N] [--questions <open-questions.md>]

Exits 0 when the file passes, 1 with one line per problem, 2 when a file cannot be read.

What it checks (each problem names the row, the heading or the line it is about):
  (a) shape     - the headings in order (Part 1, Phases, Sections that are not features,
                  Part 2, Part 3); the three generated marker pairs inside Part 1; every phase
                  table's columns exactly FEATURE_COLUMNS; Status from STATUSES; Plan cells
                  empty; no "<!-- FILL" left
  (b) citations - every feature row cites at least one "§id Title" that is in the outline, and
                  the title is that section's title (case and whitespace aside); "—" (no FSD
                  section) only in Phase 0, with a reason
  (c) coverage  - every outline section at the feature depth (and every shallower section with
                  no sub-sections) is cited by a feature row or listed under Sections that are
                  not features - and not both
  (d) honesty   - Phase 0 rows are only the foundation features; every double-quoted phrase in
                  a row, or in a phase's prose, appears verbatim in the text of a section that
                  row or phase cites (a phase may also quote a section above them); a row whose
                  cited sections are all empty is Blocked
  (e) order     - Depends on names feature rows, Q ids, or "nothing"; no cycles; a dependency
                  sits in the same phase or an earlier one
  (f) dates     - no date anywhere (generated tables and Part 3 aside) unless a
                  "### The delivery schedule" section cites its source
  (g) questions - every Q id in Depends on is a question that exists in the open questions
                  (given with --questions; the referee always gives them)

It proves that what project.md cites exists and is quoted exactly. It does not prove the FSD
was read right - which features exist, how they are grouped, what depends on what in reality.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Collection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fsd_outline import outline  # noqa: E402

FOUNDATION = ("Boilerplate and architecture", "Deployment and environments", "Authentication plumbing")
FEATURE_COLUMNS = ["Feature", "FSD", "Plan", "Owner", "Depends on", "Status"]
NOT_FEATURE_COLUMNS = ["FSD", "Why it is not a feature"]
STATUSES = ("Not started", "In progress", "Built", "Blocked — <reason>")
MARKERS = ("task-rollup", "open-tasks", "pace")

PART1 = "# Part 1 — Features and phasing"
PHASES = "### Phases"
NOT_FEATURES = "### Sections that are not features"
PART2_PREFIX = "# Part 2 — Questions"
PART3 = "# Part 3 — Requirements history"
SCHEDULE = "### The delivery schedule"

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
PHASE_HEADING = re.compile(r"^#### Phase (\d+) — (.+?)(?:\s+·\s+.*)?$")
CITATION = re.compile(r"^§(\S+?)\.?\s+(\S.*)$")
STATUS = re.compile(r"^`?(Not started|In progress|Built)`?(\s+—\s+\S.*)?$|^`?Blocked`?\s+—\s+\S")
Q_ID = re.compile(r"^Q\d+$")
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
QUOTE = re.compile(r"\"([^\"]+)\"|“([^”]+)”")
MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
         r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH}\b"
    rf"|\b{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?\b"
    rf"|\b{MONTH}\s+\d{{4}}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
SOURCE = re.compile(r"\[[^\]]+\]\([^)]+\)|`[^`]*/[^`]*`|^\s*Source:\s*\S", re.MULTILINE)


def norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def verbatim(text: str) -> str:
    """Whitespace and typographic quotes aside, exactly the text."""
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.split())


def cells(line: str) -> list[str]:
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", inner)]


def plain(cell: str) -> str:
    return LINK.sub(r"\1", cell).replace("`", "").strip()


def quotes(text: str) -> list[str]:
    return [a or b for a, b in QUOTE.findall(text)]


def is_descendant(unit_id: str, ancestor: str) -> bool:
    return unit_id.startswith(ancestor + ".")


# --- reading project.md -----------------------------------------------------------------------

def headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(line index, level, full line) for each heading outside a code fence."""
    found, fenced = [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        match = HEADING.match(line)
        if match and not fenced:
            found.append((i, len(match.group(1)), line.rstrip()))
    return found


def section_end(heads: list[tuple[int, int, str]], start: int, level: int, total: int) -> int:
    """The line index where the section that starts at `start` ends: the next heading at `level`
    or shallower."""
    return next((i for i, lvl, _ in heads if i > start and lvl <= level), total)


def tables(lines: list[str], start: int, end: int) -> tuple[list[tuple[int, list[str], list[tuple[int, list[str]]]]], list[str]]:
    """The tables in lines[start:end] as (header line index, header cells, [(row index, cells)]),
    and the prose lines around them."""
    found, prose, i = [], [], start
    while i < end:
        if lines[i].lstrip().startswith("|"):
            header_at, header = i, cells(lines[i])
            i += 1
            if i < end and re.match(r"^\s*\|[\s:|-]+\|?\s*$", lines[i]):
                i += 1
            rows = []
            while i < end and lines[i].lstrip().startswith("|"):
                row = cells(lines[i])
                if any(row):
                    rows.append((i, row))
                i += 1
            found.append((header_at, header, rows))
            continue
        if lines[i].strip() and not HEADING.match(lines[i]):
            prose.append(lines[i])
        i += 1
    return found, prose


class Citations:
    """Reads an FSD cell against the outline."""

    def __init__(self, units: list[dict]):
        self.units = units
        self.by_id: dict[str, dict] = {}
        for unit in units:
            self.by_id.setdefault(unit["id"], unit)

    def subtree(self, unit_id: str) -> list[dict]:
        return [u for u in self.units if u["id"] == unit_id or is_descendant(u["id"], unit_id)]

    def text(self, ids: list[str]) -> str:
        return "\n".join(f"{u['title']}\n{u['text']}" for i in ids for u in self.subtree(i))

    def hollow(self, unit_id: str) -> bool:
        return all(u["empty"] for u in self.subtree(unit_id))

    def read(self, cell: str, label: str, problems: list[str]) -> list[str]:
        """The valid ids the cell cites; a problem for each part that is not a real '§id Title'."""
        ids = []
        for part in [p.strip() for p in cell.split(";") if p.strip()]:
            match = CITATION.match(plain(part))
            if not match:
                problems.append(f"{label}: FSD cell part {part!r} is not \"§<id> <title>\"")
                continue
            unit_id, title = match.groups()
            unit = self.by_id.get(unit_id)
            if unit is None:
                problems.append(f"{label}: cites §{unit_id}, which is not a section of the FSD")
            elif norm(title) != norm(unit["title"]):
                problems.append(f"{label}: cites §{unit_id} as {title!r}, but the FSD's title is {unit['title']!r}")
            else:
                ids.append(unit_id)
        return ids


def check_quotes(text: str, cited: list[str], citations: Citations, label: str, problems: list[str],
                 parents: bool = False) -> None:
    """With `parents`, the own text of each section above a cited one may be quoted too: a phase's order is
    often set by a parent's sentence ("a loan needs a book and a member"), which no feature row cites."""
    above = [u for u in citations.units if any(is_descendant(i, u["id"]) for i in cited)] if parents else []
    source = verbatim(citations.text(cited) + "".join(f"\n{u['title']}\n{u['text']}" for u in above))
    for phrase in quotes(text):
        if not cited:
            problems.append(f"{label}: quotes \"{phrase}\" but cites no FSD section to quote from")
        elif verbatim(phrase) not in source:
            where = ", ".join(f"§{i}" for i in cited) + (" or the sections above them" if above else "")
            problems.append(f"{label}: quotes \"{phrase}\", which is not in the text of {where}")


# --- the checks -------------------------------------------------------------------------------

def problems(project_md_text: str, outline_units: list[dict], foundation: tuple[str, ...] = FOUNDATION,
             feature_depth: int = 2, questions: Collection[str] | None = None,
             questions_source: str = "the open questions") -> list[str]:
    """Every problem found, one line each. `questions`: the Q ids that exist (rule g); None leaves it unchecked."""
    found: list[str] = []
    lines = project_md_text.splitlines()
    heads = headings(lines)
    citations = Citations(outline_units)

    seen_ids: set[str] = set()
    for unit in outline_units:
        if unit["id"] in seen_ids:
            found.append(f"the FSD outline has two sections numbered §{unit['id']} - a citation to it is ambiguous")
        seen_ids.add(unit["id"])

    # (a) shape: headings in order
    def find(predicate, name: str) -> int | None:
        matches = [(i, lvl) for i, lvl, text in heads if predicate(text)]
        if not matches:
            found.append(f"shape: the heading {name!r} is missing")
            return None
        if len(matches) > 1:
            found.append(f"shape: the heading {name!r} appears {len(matches)} times")
        return matches[0][0]

    part1 = find(lambda t: t == PART1, PART1)
    phases = find(lambda t: t == PHASES, PHASES)
    not_features = find(lambda t: t == NOT_FEATURES, NOT_FEATURES)
    part2 = find(lambda t: t.startswith(PART2_PREFIX), PART2_PREFIX + " …")
    part3 = find(lambda t: t == PART3, PART3)
    order = [(PART1, part1), (PHASES, phases), (NOT_FEATURES, not_features), (PART2_PREFIX, part2), (PART3, part3)]
    present = [(name, at) for name, at in order if at is not None]
    for (before, at_before), (after, at_after) in zip(present, present[1:]):
        if at_after < at_before:
            found.append(f"shape: {after!r} comes before {before!r}; the order is Part 1, Phases, "
                         f"Sections that are not features, Part 2, Part 3")

    # (a) shape: the generated marker pairs, inside Part 1
    generated: list[tuple[int, int]] = []
    for marker in MARKERS:
        begins = [i for i, l in enumerate(lines) if l.startswith(f"<!-- {marker}:begin")]
        ends = [i for i, l in enumerate(lines) if l.strip() == f"<!-- {marker}:end -->"]
        if len(begins) != 1 or len(ends) != 1:
            found.append(f"shape: the {marker} markers must appear once each "
                         f"(begin {len(begins)}, end {len(ends)})")
            continue
        if ends[0] < begins[0]:
            found.append(f"shape: the {marker} end marker comes before its begin marker")
            continue
        generated.append((begins[0], ends[0]))
        if part1 is not None and part2 is not None and not part1 < begins[0] < part2:
            found.append(f"shape: the {marker} markers are not inside Part 1")

    # the phases and their feature rows
    rows: list[dict] = []
    phase_prose: dict[int, list[str]] = {}
    if phases is not None:
        end = section_end(heads, phases, 3, len(lines))
        phase_heads = [(i, text) for i, lvl, text in heads if phases < i < end and lvl == 4]
        if not phase_heads:
            found.append("shape: Phases holds no \"#### Phase <n> — <name>\" heading")
        numbers = []
        for k, (at, text) in enumerate(phase_heads):
            match = PHASE_HEADING.match(text)
            if not match:
                found.append(f"shape: {text!r} under Phases is not \"#### Phase <n> — <name>\"")
                continue
            number = int(match.group(1))
            numbers.append(number)
            stop = phase_heads[k + 1][0] if k + 1 < len(phase_heads) else end
            found_tables, prose = tables(lines, at + 1, stop)
            phase_prose[number] = prose
            if len(found_tables) != 1:
                found.append(f"shape: Phase {number} has {len(found_tables)} tables; it needs exactly one feature table")
            for _, header, table_rows in found_tables:
                if header != FEATURE_COLUMNS:
                    found.append(f"shape: Phase {number}'s table columns are {header}, "
                                 f"not exactly {FEATURE_COLUMNS}")
                    continue
                for line_at, row in table_rows:
                    rows.append({"phase": number, "line": line_at, "cells": row,
                                 "label": f"Phase {number} row \"{row[0] if row else '?'}\""})
        if numbers and numbers != list(range(len(numbers))):
            found.append(f"shape: the phases are numbered {numbers}; they run 0, 1, 2 … in order")

    names: dict[str, dict] = {}
    cited_by_features: dict[str, str] = {}
    foundation_names = {norm(f) for f in foundation}
    for row in rows:
        label, row_cells = row["label"], row["cells"]
        if len(row_cells) != len(FEATURE_COLUMNS):
            found.append(f"{label}: has {len(row_cells)} cells, not {len(FEATURE_COLUMNS)}")
            row["cited"], row["depends"] = [], []
            continue
        name, fsd, plan, _owner, depends, status = row_cells
        if not name:
            found.append(f"{label}: the Feature cell is empty")
        elif norm(name) in names:
            found.append(f"{label}: a second row named {name!r}")
        else:
            names[norm(name)] = row
        if plan:
            found.append(f"{label}: the Plan cell must be empty until the plan is written (it holds {plan!r})")
        if not STATUS.match(status):
            found.append(f"{label}: Status {status!r} is not one of {', '.join(STATUSES)}")
        # (d) Phase 0 is the foundation only
        if row["phase"] == 0 and norm(name) not in foundation_names:
            found.append(f"{label}: Phase 0 holds only the foundation ({', '.join(foundation)})")
        # (b) citations
        if fsd.startswith("—"):
            row["cited"] = []
            if row["phase"] != 0:
                found.append(f"{label}: \"—\" (no FSD section) is allowed only in Phase 0")
            elif not fsd.lstrip("—").strip(" :"):
                found.append(f"{label}: \"—\" needs a reason - why this has no FSD section")
        else:
            before = len(found)
            row["cited"] = citations.read(fsd, label, found)
            if not row["cited"] and len(found) == before:
                found.append(f"{label}: cites no FSD section - every feature row cites at least one \"§<id> <title>\"")
        for unit_id in row["cited"]:
            cited_by_features.setdefault(unit_id, label)
        if row["cited"] and all(citations.hollow(i) for i in row["cited"]) and not status.lstrip("`").startswith("Blocked"):
            found.append(f"{label}: every section it cites is empty in the FSD, so it is \"Blocked — …\", not {status!r}")
        # (d) quotes - not when a citation already failed: that problem is the one to fix
        if row["cited"] or fsd.startswith("—"):
            check_quotes(lines[row["line"]], row["cited"], citations, label, found)
        row["depends"] = depends

    # (d) quotes in each phase's prose, against what its rows cite
    for number, prose in phase_prose.items():
        cited = sorted({i for r in rows if r["phase"] == number for i in r.get("cited", [])})
        check_quotes("\n".join(prose), cited, citations, f"Phase {number}'s why-sentence", found, parents=True)

    # Sections that are not features
    excluded: dict[str, str] = {}
    if not_features is not None:
        end = section_end(heads, not_features, 3, len(lines))
        nf_tables, _ = tables(lines, not_features + 1, end)
        if len(nf_tables) != 1:
            found.append(f"shape: Sections that are not features has {len(nf_tables)} tables; it needs exactly one")
        for _, header, table_rows in nf_tables:
            if header != NOT_FEATURE_COLUMNS:
                found.append(f"shape: Sections that are not features' columns are {header}, not exactly {NOT_FEATURE_COLUMNS}")
                continue
            for line_at, row in table_rows:
                label = f"Not-a-feature row \"{row[0] if row else '?'}\""
                if len(row) != 2:
                    found.append(f"{label}: has {len(row)} cells, not 2")
                    continue
                ids = citations.read(row[0], label, found)
                if not row[1]:
                    found.append(f"{label}: says nothing about why it is not a feature")
                for unit_id in ids:
                    excluded.setdefault(unit_id, label)
                    if unit_id in cited_by_features:
                        found.append(f"{label}: §{unit_id} is also cited by {cited_by_features[unit_id]} - a feature or not, not both")
                check_quotes(lines[line_at], ids, citations, label, found)

    # (c) coverage
    accounted = set(cited_by_features) | set(excluded)
    for unit in outline_units:
        has_children = any(is_descendant(u["id"], unit["id"]) for u in outline_units)
        if unit["depth"] != feature_depth and not (unit["depth"] < feature_depth and not has_children):
            continue
        if not any(c == unit["id"] or is_descendant(c, unit["id"]) for c in accounted):
            found.append(f"coverage: §{unit['id']} {unit['title']} is neither cited by a feature row "
                         f"nor listed under Sections that are not features")

    # (e) Depends on
    graph: dict[str, list[str]] = {}
    for row in rows:
        if "depends" not in row or not row["cells"] or not row["cells"][0]:
            continue
        label, own = row["label"], norm(row["cells"][0])
        cell = plain(row["depends"])
        items = [plain(i) for i in cell.split(",") if i.strip()]
        if not items:
            found.append(f"{label}: Depends on is empty; write \"nothing\" when it depends on nothing")
            continue
        if [norm(i) for i in items] == ["nothing"]:
            continue
        for item in items:
            if norm(item) == "nothing":
                found.append(f"{label}: \"nothing\" cannot sit beside other dependencies")
            elif Q_ID.match(item):
                # (g) a question the book waits on is one that was raised
                if questions is not None and item not in questions:
                    found.append(f"{label}: depends on {item!r}, which is not a question in {questions_source}")
            elif norm(item) not in names:
                found.append(f"{label}: depends on {item!r}, which is not a feature row, a Q id or \"nothing\"")
            else:
                target = names[norm(item)]
                graph.setdefault(own, []).append(norm(item))
                if target["phase"] > row["phase"]:
                    found.append(f"{label}: depends on {item!r}, which is in a later phase "
                                 f"(Phase {target['phase']}, this is Phase {row['phase']})")
    for cycle in cycles(graph):
        shown = " → ".join(names[n]["cells"][0] for n in cycle)
        found.append(f"{names[cycle[0]]['label']}: a dependency cycle - {shown}")

    # (f) dates
    skip: set[int] = set()
    for begin, end in generated:
        skip.update(range(begin, end + 1))
    if part3 is not None:
        skip.update(range(part3, len(lines)))
    schedule = [i for i, lvl, text in heads if lvl == 3 and text == SCHEDULE]
    schedule_lines: set[int] = set()
    sourced = False
    if schedule:
        end = section_end(heads, schedule[0], 3, len(lines))
        schedule_lines = set(range(schedule[0], end))
        sourced = bool(SOURCE.search("\n".join(lines[schedule[0] + 1:end])))
    row_label = {r["line"]: r["label"] for r in rows}
    for i, line in enumerate(lines):
        if "<!-- FILL" in line:
            found.append(f"{row_label.get(i, f'line {i + 1}')}: still holds a <!-- FILL placeholder")
    for i, line in enumerate(lines):
        if i in skip:
            continue
        for match in DATE.finditer(line):
            if sourced:
                break
            where = row_label.get(i, f"line {i + 1}")
            reason = ("the delivery schedule cites no source" if i in schedule_lines
                      else "dates come only from a \"### The delivery schedule\" section that cites its source")
            found.append(f"{where}: has the date {match.group(0)!r} - {reason}")
    return found


def cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Each cycle once, as the path that closes it, found in a deterministic order."""
    found, colour, stack = [], {}, []
    seen: set[frozenset] = set()

    def visit(node: str) -> None:
        colour[node] = "grey"
        stack.append(node)
        for nxt in graph.get(node, []):
            if colour.get(nxt) == "grey":
                loop = stack[stack.index(nxt):] + [nxt]
                key = frozenset(loop)
                if key not in seen:
                    seen.add(key)
                    found.append(loop)
            elif nxt not in colour:
                visit(nxt)
        stack.pop()
        colour[node] = "black"

    for node in sorted(graph):
        if node not in colour:
            visit(node)
    return found


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    depth = 2
    if "--feature-depth" in args:
        at = args.index("--feature-depth")
        try:
            depth = int(args[at + 1])
        except (IndexError, ValueError):
            print("check_project_md: --feature-depth takes a number", file=sys.stderr)
            return 2
        del args[at:at + 2]
    questions_file = None
    if "--questions" in args:
        at = args.index("--questions")
        if at + 1 >= len(args):
            print("check_project_md: --questions takes a file", file=sys.stderr)
            return 2
        questions_file = Path(args[at + 1])
        del args[at:at + 2]
    if len(args) != 2:
        print("usage: python check_project_md.py <project.md> <fsd file> [--feature-depth N] "
              "[--questions <open-questions.md>]", file=sys.stderr)
        return 2
    questions = None
    try:
        text = Path(args[0]).read_text(encoding="utf-8-sig")
        if questions_file is not None:
            questions = set(re.findall(r"\bQ\d+\b", questions_file.read_text(encoding="utf-8-sig")))
        units = outline(args[1])
    except (OSError, ValueError) as error:
        print(f"check_project_md: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # a .docx that is not a valid zip or XML
        print(f"check_project_md: cannot read {args[1]}: {error}", file=sys.stderr)
        return 2
    found = problems(text, units, feature_depth=depth, questions=questions,
                     questions_source=questions_file.name if questions_file else "the open questions")
    if found:
        print(f"{Path(args[0]).name}: {len(found)} problem(s)")
        for problem in found:
            print(f"  - {problem}")
        return 1
    print(f"{Path(args[0]).name}: passes against {Path(args[1]).name} ({len(units)} FSD sections)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
