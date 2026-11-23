#!/usr/bin/env python3
"""
Reads `dotnet list package --vulnerable --include-transitive --format json` and passes only on a clean audit.

Searching the text output for the failure phrase recorded "none" for anything else: a reworded or localised
SDK message, or a feed that returned no audit data. So this passes only when the JSON reads, names at least
one project, carries no `problems` (a source that could not be audited is one), and lists no vulnerability.

    dotnet list Boilerplate.slnx package --vulnerable --include-transitive --format json > vulnerable.json
    python devtools/checks/check-vulnerable-packages.py vulnerable.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def vulnerable(node: object) -> list[str]:
    """Every package, anywhere in the report, that carries a vulnerability."""
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("vulnerabilities"):
            found.append(f"{node.get('id', '?')} {node.get('resolvedVersion', '')}".strip())
        for value in node.values():
            found.extend(vulnerable(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(vulnerable(item))
    return found


def problems(text: str) -> list[str]:
    try:
        report = json.loads(text)
    except json.JSONDecodeError as error:
        return [f"the report is not JSON ({error.msg}) - nothing was audited"]
    if not isinstance(report, dict) or not report.get("projects"):
        return ["the report names no project - nothing was audited"]
    found = [f"the audit reported a problem: {problem.get('text', problem)}"
             for problem in report.get("problems") or [] if isinstance(problem, dict)]
    found += [f"vulnerable package: {package}" for package in sorted(set(vulnerable(report["projects"])))]
    return found


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not Path(args[0]).is_file():
        print("check-vulnerable-packages: pass the JSON report's path - nothing to check is not a pass.", file=sys.stderr)
        return 1
    found = problems(Path(args[0]).read_text(encoding="utf-8-sig"))
    for problem in found:
        print(f"check-vulnerable-packages: {problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    # UTF-8 out, so an em dash prints on a Windows console's code page too (Book D45).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    sys.exit(main())
