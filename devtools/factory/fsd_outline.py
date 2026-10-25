#!/usr/bin/env python3
"""The outline of an FSD: every section, with its id, title, depth and own text.

    python fsd_outline.py <file> [--json]

Reads .docx (Heading1..Heading6 paragraphs in word/document.xml; the number comes from a
leading "2.5.1" in the heading's text, else from its list numbering, w:numPr), .md (# headings,
or - when the file has none - lines like "2.5.1 Title") and .txt (lines like "2.5.1 Title").

Each unit is a dict:
    id      "2.5.1", or "H1", "H1.2" for an unnumbered heading (counted in order, by level)
    title   the heading's text without its number
    depth   the number's length ("2.5.1" is 3), or the heading level when unnumbered
    words   the words in the section's own text - up to the next heading of any level
    text    that own text
    empty   True when the own text has fewer than EMPTY_BELOW words

Deterministic and standard library only: the same file always gives the same outline.
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

EMPTY_BELOW = 15
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# "2.5.1 Title", "2.5.1. Title", "2.5.1<tab>Title"
NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(\S.*?)\s*$")
MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
HEADING_NAME = re.compile(r"^heading\s*([1-6])$", re.IGNORECASE)
MAX_HEADING_WORDS = 12


# --- events: a document read as a run of headings and text lines ---------------------------

def _split_number(title: str) -> tuple[str | None, str]:
    match = NUMBERED.match(title)
    if match:
        return match.group(1), match.group(2)
    return None, title.strip()


def _looks_like_numbered_heading(line: str) -> tuple[str, str] | None:
    """A plain-text line that is a section heading: a number, then a short capitalised title
    that does not end like a sentence. "1. Customers can add items." is a list item, not a heading."""
    match = NUMBERED.match(line)
    if not match:
        return None
    number, title = match.groups()
    if not title[0].isupper() or title.endswith((".", ",", ";")):
        return None
    if len(title.split()) > MAX_HEADING_WORDS:
        return None
    return number, title.rstrip(":").strip()


def _events_from_lines(lines: list[str], use_hashes: bool) -> list[tuple]:
    events: list[tuple] = []
    fenced = False
    for line in lines:
        if line.lstrip().startswith("```"):
            fenced = not fenced
            events.append(("text", line))
            continue
        if not fenced and use_hashes:
            match = MD_HEADING.match(line)
            if match:
                number, title = _split_number(match.group(2))
                events.append(("heading", len(match.group(1)), number, title))
                continue
        if not fenced and not use_hashes:
            found = _looks_like_numbered_heading(line)
            if found:
                number, title = found
                events.append(("heading", number.count(".") + 1, number, title))
                continue
        events.append(("text", line))
    return events


def _markdown_events(text: str) -> list[tuple]:
    lines = text.splitlines()
    fenced = False
    has_hashes = False
    for line in lines:
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and MD_HEADING.match(line):
            has_hashes = True
            break
    return _events_from_lines(lines, use_hashes=has_hashes)


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == W + "t" and node.text:
            parts.append(node.text)
        elif node.tag == W + "tab":
            parts.append("\t")
        elif node.tag in (W + "br", W + "cr"):
            parts.append("\n")
    return "".join(parts)


def _attr(node: ElementTree.Element | None, name: str) -> str | None:
    return None if node is None else node.get(W + name)


def _num_pr(ppr: ElementTree.Element | None) -> tuple[str, int] | None:
    if ppr is None:
        return None
    numpr = ppr.find(W + "numPr")
    if numpr is None:
        return None
    num_id = _attr(numpr.find(W + "numId"), "val")
    if not num_id or num_id == "0":
        return None
    ilvl = _attr(numpr.find(W + "ilvl"), "val") or "0"
    return num_id, int(ilvl)


class _Numbering:
    """Word list numbering, counted the way Word counts it: one counter set per abstract list,
    a deeper level restarting whenever a shallower one moves on. Decimal only - an FSD's
    section numbers are."""

    def __init__(self, archive: zipfile.ZipFile):
        self.abstract_of: dict[str, str] = {}
        self.starts: dict[tuple[str, int], int] = {}
        self.counters: dict[str, dict[int, int]] = {}
        if "word/numbering.xml" not in archive.namelist():
            return
        root = ElementTree.fromstring(archive.read("word/numbering.xml"))
        for abstract in root.findall(W + "abstractNum"):
            abstract_id = _attr(abstract, "abstractNumId") or ""
            for lvl in abstract.findall(W + "lvl"):
                start = _attr(lvl.find(W + "start"), "val")
                self.starts[(abstract_id, int(_attr(lvl, "ilvl") or "0"))] = int(start or "1")
        for num in root.findall(W + "num"):
            self.abstract_of[_attr(num, "numId") or ""] = _attr(num.find(W + "abstractNumId"), "val") or ""

    def next(self, num_id: str, ilvl: int) -> str:
        abstract = self.abstract_of.get(num_id, num_id)
        counters = self.counters.setdefault(abstract, {})
        for level in range(ilvl):
            counters.setdefault(level, self.starts.get((abstract, level), 1))
        counters[ilvl] = counters[ilvl] + 1 if ilvl in counters else self.starts.get((abstract, ilvl), 1)
        for level in [l for l in counters if l > ilvl]:
            del counters[level]
        return ".".join(str(counters[level]) for level in range(ilvl + 1))


def _docx_events(path: Path) -> list[tuple]:
    with zipfile.ZipFile(path) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
        heading_level: dict[str, int] = {f"Heading{n}": n for n in range(1, 7)}
        style_numbering: dict[str, tuple[str, int]] = {}
        if "word/styles.xml" in archive.namelist():
            styles = ElementTree.fromstring(archive.read("word/styles.xml"))
            for style in styles.findall(W + "style"):
                style_id = _attr(style, "styleId") or ""
                name = _attr(style.find(W + "name"), "val") or ""
                match = HEADING_NAME.match(name.strip())
                if match:
                    heading_level[style_id] = int(match.group(1))
                numbered = _num_pr(style.find(W + "pPr"))
                if numbered:
                    style_numbering[style_id] = numbered
        numbering = _Numbering(archive)
    body = document.find(W + "body")
    events: list[tuple] = []
    for paragraph in (body.iter(W + "p") if body is not None else []):
        ppr = paragraph.find(W + "pPr")
        style = _attr(ppr.find(W + "pStyle") if ppr is not None else None, "val") or ""
        text = _paragraph_text(paragraph)
        level = heading_level.get(style)
        if level is None:
            events.extend(("text", line) for line in text.split("\n"))
            continue
        number, title = _split_number(text)
        list_number = _num_pr(ppr) or style_numbering.get(style)
        if list_number:
            counted = numbering.next(*list_number)
            number = number or counted
        if not title:
            continue
        events.append(("heading", level, number, title))
    return events


# --- units ------------------------------------------------------------------------------------

def _units(events: list[tuple]) -> list[dict]:
    units: list[dict] = []
    unnumbered = [0] * 6
    lines: list[str] = []

    def close() -> None:
        if units:
            text = "\n".join(lines).strip()
            text = re.sub(r"\n{3,}", "\n\n", text)
            words = len(text.split())
            units[-1].update(text=text, words=words, empty=words < EMPTY_BELOW)
        lines.clear()

    for event in events:
        if event[0] == "text":
            lines.append(event[1])
            continue
        close()
        _, level, number, title = event
        if number:
            unit_id, depth = number, number.count(".") + 1
        else:
            unnumbered[level - 1] += 1
            for deeper in range(level, 6):
                unnumbered[deeper] = 0
            unit_id, depth = "H" + ".".join(str(n) for n in unnumbered[:level]), level
        units.append({"id": unit_id, "title": " ".join(title.split()), "depth": depth,
                      "words": 0, "text": "", "empty": True})
    close()
    return units


def outline(path: str | Path) -> list[dict]:
    """The FSD's sections in document order. Raises ValueError for an unsupported file type."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _units(_docx_events(path))
    text = path.read_text(encoding="utf-8-sig")
    if suffix == ".md":
        return _units(_markdown_events(text))
    if suffix == ".txt":
        return _units(_events_from_lines(text.splitlines(), use_hashes=False))
    raise ValueError(f"{path.name}: an FSD must be .docx, .md or .txt")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    as_json = "--json" in args
    files = [a for a in args if a != "--json"]
    if len(files) != 1:
        print("usage: python fsd_outline.py <file> [--json]", file=sys.stderr)
        return 2
    try:
        units = outline(files[0])
    except (OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        print(f"fsd_outline: {error}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(units, indent=2, ensure_ascii=False))
        return 0
    for unit in units:
        mark = "  [empty]" if unit["empty"] else ""
        print(f"{'  ' * (unit['depth'] - 1)}§{unit['id']} {unit['title']}  ({unit['words']} words){mark}")
    print(f"{len(units)} sections, {sum(u['empty'] for u in units)} empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
