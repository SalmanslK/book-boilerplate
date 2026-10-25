#!/usr/bin/env python3
"""
The skip gate: a test run that skipped, ran nothing, or left no results is a failure, not a pass.

    python devtools/checks/check-test-results.py <results file or folder> ...

Each argument is a results file — JUnit XML (pytest `--junitxml`, Vitest's junit reporter, most other
runners) or a Visual Studio .trx (`dotnet test --logger trx`) — or a folder, whose *.xml and *.trx
files are each read. Every argument must name at least one results file.

Three ways a run can lie, and all three fail here:
- **no results** — the file is missing, or the folder holds none: the run never finished;
- **nothing executed** — a filter matching no tests exits 0 with an empty report;
- **a skipped test** — no database, no service, a missing tool. A .trx is counted from its results,
  never only from its summary: the summary can say nothing was skipped while a result inside is
  NotExecuted.

A test meant to skip in some runs (it needs a deployed host) is excluded from the gated run by its
tag, so this gate never has to forgive a skip. It prints one line per file: executed and skipped.
Exits 0 only when every file executed tests and skipped none. Python 3, standard library only.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

TRX = "{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}"


def counts(path: Path) -> tuple[int, int]:
    """(executed, skipped) in one results file. Raises ValueError for a file that is not one."""
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError as error:
        raise ValueError(f"not readable results ({error})") from error
    if root.tag.startswith(TRX) or path.suffix.lower() == ".trx":
        results = list(root.iter(f"{TRX}UnitTestResult"))
        skipped = sum(1 for result in results if result.get("outcome") == "NotExecuted")
        summary = next(root.iter(f"{TRX}Counters"), None)
        if summary is None:
            return len(results) - skipped, skipped
        return int(summary.get("executed", "0")), max(skipped, int(summary.get("notExecuted", "0")))
    if root.tag not in ("testsuites", "testsuite"):
        raise ValueError(f"neither JUnit XML nor .trx (its root is <{root.tag}>)")
    cases = list(root.iter("testcase"))
    skipped = sum(1 for case in cases if case.find("skipped") is not None)
    return len(cases) - skipped, skipped


def results_files(argument: str) -> list[Path]:
    path = Path(argument)
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.suffix.lower() in (".xml", ".trx") and p.is_file())
    return [path] if path.is_file() else []


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("usage: check-test-results.py <results file or folder> ...", file=sys.stderr)
        return 2
    failed = False
    for argument in args:
        found = results_files(argument)
        if not found:
            print(f"{argument}: no results file — the run did not finish, or wrote its results elsewhere.")
            failed = True
        for path in found:
            try:
                executed, skipped = counts(path)
            except ValueError as error:
                print(f"{path}: {error}.")
                failed = True
                continue
            if executed == 0:
                print(f"{path}: no tests executed — the filter matched nothing, or the run never started.")
                failed = True
            elif skipped:
                print(f"{path}: {skipped} test(s) skipped — a skip is a failure here (a database or service "
                      "missing?).")
                failed = True
            else:
                print(f"{path}: {executed} executed, 0 skipped.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
