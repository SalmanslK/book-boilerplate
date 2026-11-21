#!/usr/bin/env python3
"""
Prints the slowest test classes of a .trx file: the sum of its tests' durations, the test count and
the class. ci-local.sh runs it after the integration tests so a class that grows slow shows in the
run that made it slow, not in the run that finally times out.

    python devtools/checks/trx-slowest.py <file.trx> [count]

The sum is test time, not wall time: classes run in parallel, so the sum can exceed the run. It is
for comparing classes with each other. Exit 0 always - a report, not a gate; a file it cannot read
is said so and skipped.
"""
import sys
import xml.etree.ElementTree as ET

DEFAULT_COUNT = 5
TRX_NAMESPACE = "{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}"


def seconds(duration: str) -> float:
    hours, minutes, rest = duration.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def slowest_classes(path: str, count: int) -> list[tuple[float, int, str]]:
    root = ET.parse(path).getroot()
    class_of = {
        unit.get("id"): unit.find(f"{TRX_NAMESPACE}TestMethod").get("className")
        for unit in root.iter(f"{TRX_NAMESPACE}UnitTest")
        if unit.find(f"{TRX_NAMESPACE}TestMethod") is not None
    }
    totals: dict[str, list[float]] = {}
    for result in root.iter(f"{TRX_NAMESPACE}UnitTestResult"):
        name = class_of.get(result.get("testId"))
        if name and result.get("duration"):
            totals.setdefault(name, []).append(seconds(result.get("duration")))
    ranked = sorted(((sum(times), len(times), name) for name, times in totals.items()), reverse=True)
    return ranked[:count]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python devtools/checks/trx-slowest.py <file.trx> [count]")
        return 2
    try:
        count = int(argv[2]) if len(argv) > 2 else DEFAULT_COUNT
        ranked = slowest_classes(argv[1], count)
    except Exception as error:  # a report must never fail the run it reports on
        print(f"Slowest test classes: could not read {argv[1]} ({error})")
        return 0
    print(f"Slowest {count} test classes (summed test time, tests, class):")
    for total, tests, name in ranked:
        print(f"  {total:8.1f} s  {tests:4d}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
