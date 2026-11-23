#!/usr/bin/env python3
"""
Every skill under `.claude/skills/` loads, and stays findable.

A malformed frontmatter loads a skill with no description, so it silently never triggers; a
description over the portable limit is cut where people and the model read it; and the listing of
every skill's description has a budget in the session's context, past which the least-used skills
lose their descriptions without a word. Nothing else checks any of that. Links inside skills are
check-book.py's (it reads every tracked Markdown file); which skills a Book must have is
`book verify`'s N16.

    python devtools/checks/check-skills.py [--root <path>]

For each `.claude/skills/<folder>/SKILL.md`:

1. It opens with a `---` frontmatter block that closes, and every key in it is one Claude Code reads.
2. `name` is present and equals the folder's name.
3. `description` is present, not empty, and at most 1,024 characters (the portable limit; the
   listing cuts at 1,536).
4. It ends with a `## What next` section: every skill says what comes after it.

For each `.claude/agents/<name>.md` (the Book's agents): a frontmatter block whose keys are ones Claude
Code reads for an agent, `name` equal to the file's name, a description within the same limit, and a
`tools` list - an agent with no tools list inherits every tool, which a reviewer must not.

Then the sum of every description is held to LISTING_BUDGET. Over it is a warning, never a failure:
which description to shorten is a person's call.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTION_LIMIT = 1024
# About 2.5k tokens. The listing's whole budget is about 1% of the context window and is shared
# with the person's own skills, plugins and built-in commands, so this repository keeps to part of it.
LISTING_BUDGET = 10_000
KEYS = {"name", "description", "when_to_use", "allowed-tools", "argument-hint", "disable-model-invocation",
        "user-invocable", "model", "license", "version", "metadata"}
AGENT_KEYS = {"name", "description", "tools", "model", "color"}
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
TOP_KEY = re.compile(r"^([A-Za-z0-9_-]+):(.*)$")


def frontmatter(text: str) -> dict[str, str] | None:
    """The top-level keys and their values, a folded or literal block joined into one line; None if absent."""
    match = FRONTMATTER.match(text.replace("\r\n", "\n"))
    if not match:
        return None
    fields: dict[str, str] = {}
    key = None
    for line in match.group(1).split("\n"):
        top = TOP_KEY.match(line)
        if top:
            key = top.group(1)
            value = top.group(2).strip()
            fields[key] = "" if value in (">", ">-", "|", "|-") else value.strip("\"'")
        elif key and line.strip():
            fields[key] = (fields[key] + " " + line.strip()).strip()
    return fields


def problems(root: Path = ROOT) -> tuple[list[str], list[str], int]:
    """(failures, warnings, total description length) for every skill under root."""
    failures: list[str] = []
    total = 0
    skills = sorted((root / ".claude" / "skills").glob("*/SKILL.md"))
    for path in skills:
        rel = path.relative_to(root).as_posix()
        folder = path.parent.name
        text = path.read_text(encoding="utf-8")
        fields = frontmatter(text)
        if fields is None:
            failures.append(f"{rel}: no frontmatter — it must open with a `---` block that closes")
            continue
        unknown = sorted(set(fields) - KEYS)
        if unknown:
            failures.append(f"{rel}: frontmatter key(s) Claude Code does not read: {', '.join(unknown)}")
        if fields.get("name") != folder:
            failures.append(f"{rel}: name is {fields.get('name')!r}; it must be the folder's name, {folder!r}")
        description = fields.get("description", "")
        if not description:
            failures.append(f"{rel}: no description — the skill would never trigger")
        elif len(description) > DESCRIPTION_LIMIT:
            failures.append(f"{rel}: description is {len(description):,} characters; the limit is {DESCRIPTION_LIMIT:,}")
        total += len(description)
        if not re.search(r"^## What next\s*$", text.replace("\r\n", "\n"), re.M):
            failures.append(f"{rel}: no `## What next` section — every skill says what comes after it")
    for path in sorted((root / ".claude" / "agents").glob("*.md")):
        rel = path.relative_to(root).as_posix()
        fields = frontmatter(path.read_text(encoding="utf-8"))
        if fields is None:
            failures.append(f"{rel}: no frontmatter — it must open with a `---` block that closes")
            continue
        unknown = sorted(set(fields) - AGENT_KEYS)
        if unknown:
            failures.append(f"{rel}: frontmatter key(s) Claude Code does not read for an agent: {', '.join(unknown)}")
        if fields.get("name") != path.stem:
            failures.append(f"{rel}: name is {fields.get('name')!r}; it must be the file's name, {path.stem!r}")
        description = fields.get("description", "")
        if not description or len(description) > DESCRIPTION_LIMIT:
            failures.append(f"{rel}: description is {'missing' if not description else f'{len(description):,} characters'}"
                            f"; it must be present and at most {DESCRIPTION_LIMIT:,}")
        if not fields.get("tools"):
            failures.append(f"{rel}: no `tools` list — an agent without one inherits every tool")
    warnings = []
    if total > LISTING_BUDGET:
        warnings.append(f"the {len(skills)} descriptions total {total:,} characters, over the {LISTING_BUDGET:,} "
                        "budget: past the session's listing budget the least-used skills lose their descriptions "
                        "silently. Shorten the longest.")
    return failures, warnings, total


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[args.index("--root") + 1]) if "--root" in args else ROOT
    failures, warnings, total = problems(root)
    for line in warnings:
        print(f"Warning (not a failure): {line}")
    if failures:
        print(f"Skills: {len(failures)} problem(s)")
        for line in failures:
            print(f"  {line}")
        return 1
    print(f"Every skill loads ({total:,} characters of description).")
    return 0


if __name__ == "__main__":
    # UTF-8 out, so an em dash prints on a Windows console's code page too (Book D45).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    sys.exit(main())
