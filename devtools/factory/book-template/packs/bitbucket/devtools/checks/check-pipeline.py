#!/usr/bin/env python3
"""
Every pipeline step runs on our own runner, within a time limit, and no pipeline runs on every push.

Atlassian's cloud bills each step's minutes, doubled at `size: 2x`; a step on a self-hosted runner bills
nothing. One month of the old file cost 58,334 minutes. So `bitbucket-pipelines.yml` must hold:

- every step definition names `self.hosted` under `runs-on` — a step without it runs in the cloud;
- every step definition has a `max-time` of at most 120 minutes — a stalled step otherwise waits out
  Bitbucket's limit;
- no `default` pipeline — it ran the full build on every push to every branch.

A step written `- step: *anchor` is the definition it points at, which is checked where it is defined.

    python devtools/checks/check-pipeline.py [path]

The file is read as text: the checks use the standard library only, and PyYAML is not in it. A step whose
shape this cannot read fails, rather than passing unread.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = "bitbucket-pipelines.yml"
LONGEST_STEP_MINUTES = 120
STEP = re.compile(r"^(?P<indent>\s*)- step:\s*(?P<rest>.*)$")
KEY = re.compile(r"^(?P<indent>\s*)(?P<key>[\w-]+):\s*(?P<value>.*)$")


def code(line: str) -> str:
    """The line without a trailing comment; a whole-line comment is empty."""
    return "" if line.lstrip().startswith("#") else re.sub(r"\s+#.*$", "", line).rstrip()


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def step_block(lines: list[str], start: int) -> list[str]:
    """The lines of the step that opens at `start`, comments removed: everything indented past its dash."""
    dash = indent_of(lines[start])
    block = []
    for line in lines[start + 1:]:
        live = code(line)
        if not live:
            continue
        if indent_of(live) <= dash:
            break
        block.append(live)
    return block


def runs_on(block: list[str]) -> list[str]:
    """The labels under the step's own `runs-on`, inline (`[a, b]`) or as a list below it."""
    level = min((indent_of(line) for line in block), default=0)
    for at, line in enumerate(block):
        key = KEY.match(line)
        if not key or key.group("key") != "runs-on" or indent_of(line) != level:
            continue
        value = key.group("value").strip()
        if value:
            return [label.strip(" '\"") for label in value.strip("[]").split(",") if label.strip()]
        labels = []
        for item in block[at + 1:]:
            if indent_of(item) <= level:
                break
            labels.append(item.strip().removeprefix("-").strip(" '\""))
        return labels
    return []


def max_time(block: list[str]) -> str:
    level = min((indent_of(line) for line in block), default=0)
    for line in block:
        key = KEY.match(line)
        if key and key.group("key") == "max-time" and indent_of(line) == level:
            return key.group("value").strip()
    return ""


def step_name(block: list[str], rest: str) -> str:
    for line in block:
        key = KEY.match(line)
        if key and key.group("key") == "name":
            return key.group("value").strip(" '\"")
    return rest or "an unnamed step"


def problems(text: str) -> list[str]:
    lines = text.splitlines()
    found: list[str] = []
    steps = 0
    for at, line in enumerate(lines):
        step = STEP.match(code(line))
        if not step:
            continue
        rest = step.group("rest").strip()
        if rest.startswith("*"):
            continue  # an alias: its definition is checked where it is defined
        steps += 1
        block = step_block(lines, at)
        name = step_name(block, rest)
        if not block:
            found.append(f"line {at + 1}: cannot read the step {rest or ''}".rstrip() + " - write it as a mapping")
            continue
        if "self.hosted" not in runs_on(block):
            found.append(f"line {at + 1}: '{name}' has no `runs-on: [self.hosted, ...]`, so it would run, "
                         "and bill, in Atlassian's cloud")
        limit = max_time(block)
        if not limit.isdigit() or not 0 < int(limit) <= LONGEST_STEP_MINUTES:
            found.append(f"line {at + 1}: '{name}' needs a `max-time` of 1-{LONGEST_STEP_MINUTES} minutes"
                         + (f" (has '{limit}')" if limit else ""))
    if not steps:
        found.append("no step definitions found - the file could not be read as a pipeline")
    in_pipelines = False
    child = None  # the indent of the keys directly under `pipelines:`, whatever width the file uses
    for at, line in enumerate(lines):
        live = code(line)
        if not live:
            continue
        if indent_of(live) == 0:
            in_pipelines = live.startswith("pipelines:")
            child = None
            continue
        if in_pipelines and child is None:
            child = indent_of(live)
        key = KEY.match(live)
        if in_pipelines and key and key.group("key") == "default" and indent_of(live) == child:
            found.append(f"line {at + 1}: a `default` pipeline runs on every push to every branch - run on "
                         "pull requests and named branches only")
    return found


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else root / PIPELINE
    if not path.is_file():
        print(f"check-pipeline: {path} does not exist - nothing to check is not a pass.", file=sys.stderr)
        return 1
    found = problems(path.read_text(encoding="utf-8"))
    for problem in found:
        print(f"check-pipeline: {problem}.", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    # UTF-8 out, so an em dash prints on a Windows console's code page too (Book D45).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
