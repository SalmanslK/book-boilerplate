#!/usr/bin/env python3
"""
Check the book — the documents we maintain — against its own rules.

The rules are in CLAUDE.md ("Where knowledge lives") and docs/CLAUDE.md. Three of them a
program can hold, so this one does, and fails the build when one breaks:

1. Every link resolves. A relative link in a live document points at a file that exists, and
   an anchor at a heading that exists. A moved or renamed document otherwise breaks its links
   silently.
2. Every document has an owner. Each Markdown file in docs/, and at the repository root, is
   named in CLAUDE.md's owner table or sits in a folder the table names. A document the map
   does not mention is one nobody knows to keep current. A CLAUDE.md without the table is a
   finding; a project that keeps no such table sets `[book] owner_table = false` in factory.toml.
3. History stays in the archive. No live document carries a re-trace log, a changelog or a
   struck-through statement — docs/CLAUDE.md's "current state, not history". Each live document
   that keeps a dated log has an archive file for it instead.

It also reports — without failing — the documents over the size the audit flagged, so growth is
seen before it hurts. A passing check is not proof the book is well organised; it proves only
these three things.

Not checked, on purpose: docs/archive/ (written once, never maintained), the factory's run
files (records, under factory.toml's `runs_dir`), requirements/ (the client's documents) and
graphify-out/ (generated).

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
CONFIG = Path("devtools") / "factory" / "factory.toml"
DEFAULT_RUNS = "docs/factory/runs"
# The factory's run files are added from `runs_dir` in factory.toml (not_live), wherever it puts them.
NOT_LIVE = ("docs/archive/", "requirements/", "graphify-out/")
OWNER_HEADING = re.compile(r"^## Where knowledge lives[ \t]*$", re.MULTILINE)
# Written only by /fsd-audit, which marks a closed finding by striking it through: its own convention.
HISTORY_EXEMPT = ("docs/Audit/",)
# Instruction files carry rules, not topics; CLAUDE.md's "Rules to follow" row names them as a set.
INSTRUCTION_FILES = {"CLAUDE.md", "AGENTS.md"}
# A link written as a pattern, not a path: `R-nnn` in a template, `<n>` in a naming rule.
PLACEHOLDER = re.compile(r"R-nnn|<[^>]+>|\{[^}]+\}")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
# A heading that files history in a live document. A module heading saying when it was traced
# ("Labs — traced 2026-09-03") states the current trace and is fine.
HISTORY_HEADING = re.compile(r"^#{2,6} (Re-traced|Re-checked|Changelog|Change log)\b", re.IGNORECASE)
STRUCK = re.compile(r"~~[^~\n]+~~")
SIZE_WARNING = 1500


def factory_config(root: Path = ROOT) -> dict:
    """factory.toml, or {} where the project has none. One that does not parse stops the check (main)."""
    path = root / CONFIG
    if not path.is_file():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:  # Python before 3.11 (a pre-commit hook's python3): today's defaults
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def not_live(config: dict) -> tuple[str, ...]:
    return (*NOT_LIVE, str(config.get("runs_dir", DEFAULT_RUNS)).strip("/") + "/")


def tracked_markdown(root: Path = ROOT, config: dict | None = None) -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=root, capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    skipped = not_live(factory_config(root) if config is None else config)
    return [path for path in out.splitlines() if path and not path.startswith(skipped)]


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
        # A heading's code spans are part of its anchor: "`factory/` — the referee" → factory--the-referee.
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


class NoOwnerTable(Exception):
    """CLAUDE.md is missing, or has no owner table: the owner rule has nothing to read."""


def owned_patterns(root: Path = ROOT) -> list[re.Pattern]:
    """The paths CLAUDE.md's owner table names: a file, a folder (`…/`), or a pattern (`<n>`)."""
    path = root / "CLAUDE.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    heading = OWNER_HEADING.search(text)
    if heading is None:
        raise NoOwnerTable("CLAUDE.md does not exist" if not path.is_file() else
                           'CLAUDE.md has no "## Where knowledge lives" owner table')
    table = text[heading.end():].split("\n## ", 1)[0]
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
    """
    A CLAUDE.md with no owner table is a finding, not a skip: a check that turns itself off when its input
    goes missing lets a deleted heading pass, which is how the rule rots. A project that keeps its own
    CLAUDE.md and has not taken up the owner table says so once, with `[book] owner_table = false` (main).
    """
    try:
        patterns = owned_patterns(root)
    except NoOwnerTable as missing:
        return [f"{missing}, so which document has an owner cannot be checked. Add the table (a "
                "`| Topic | Owner |` row per document), or set `[book] owner_table = false` in "
                "devtools/factory/factory.toml where the project does not keep one"]
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


def size_warnings(files: list[str], root: Path = ROOT) -> list[str]:
    sizes = [(sum(1 for _ in (root / rel).open(encoding="utf-8", errors="replace")), rel) for rel in files]
    return [f"{rel}: {lines:,} lines" for lines, rel in sorted(sizes, reverse=True) if lines > SIZE_WARNING]


def main(root: Path = ROOT) -> int:
    try:
        config = factory_config(root)
    except (OSError, ValueError) as error:  # tomllib.TOMLDecodeError is a ValueError
        print(f"{CONFIG.as_posix()} could not be read ({error}), so the book cannot be checked.")
        return 1
    files = tracked_markdown(root, config)
    failed = False
    owners_on = (config.get("book") or {}).get("owner_table", True) is not False
    if not owners_on:
        print("Owners: not checked — [book] owner_table = false in factory.toml.")
    rules = (("Links", link_problems(files, root)),
             ("Owners", owner_problems(files, root) if owners_on else []),
             ("History", history_problems(files, root)))
    for title, problems in rules:
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
