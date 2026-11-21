#!/usr/bin/env python3
"""
How the test suite has grown on dev, merge by merge — the safety net over time.

Counts the tests defined under tests/ (every `[…Fact]` and `[…Theory]` method, the database and
deployed-host variants included) at each commit on dev's first-parent history, then prints one line
per week: the count at the week's end, how much it grew, the merges that week, and every merge that
made the count fall — a fall is not wrong in itself (a test merged into another, a feature removed),
but each one is named so a person can see why.

    python devtools/checks/test-trend.py              # weekly table, from the first test to now
    python devtools/checks/test-trend.py --merges     # one line per merge instead

It reads git only (`git fetch origin` first) and changes nothing. CI's own run counts differ a
little: deployed-host smoke tests are defined here but run only after a deploy.
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
def configured_base(root: Path) -> str:
    """`base_ref` in devtools/factory/factory.toml (origin/dev when it names none): the branch work merges into."""
    try:
        import tomllib
        with open(root / "devtools" / "factory" / "factory.toml", "rb") as handle:
            return str(tomllib.load(handle).get("base_ref") or "origin/dev")
    except (OSError, ImportError, ValueError):
        return "origin/dev"


DEV = configured_base(ROOT)
TEST = r"\[[A-Za-z]*(Fact|Theory)[A-Za-z]*\b"
MERGED = re.compile(r"\(pull request #(\d+)\)")


class GitFailed(Exception):
    """A git command failed: a count read from it would be a guess, and a guess of 0 reads as a fall."""


def git(*args: str, root: Path = ROOT, allowed: tuple[int, ...] = (0,)) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, encoding="utf-8",
                          errors="replace")
    if done.returncode not in allowed:
        raise GitFailed(f"`git {' '.join(args[:2])} …` failed ({done.returncode}): {done.stderr.strip()}")
    return done.stdout


def count(sha: str, root: Path = ROOT) -> int:
    # git grep exits 1 when nothing matches — a state with no tests — and 128 (or another code) on an error.
    out = git("grep", "-c", "-E", TEST, sha, "--", "tests", root=root, allowed=(0, 1))
    return sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines() if line.rsplit(":", 1)[-1].isdigit())


def history(root: Path = ROOT, dev: str = DEV) -> list[tuple[date, str, str, int]]:
    rows = []
    for line in git("log", "--first-parent", "--reverse", "--format=%H|%ad|%s", "--date=short", dev,
                    root=root).splitlines():
        sha, day, subject = line.split("|", 2)
        rows.append((date.fromisoformat(day), sha, subject, count(sha, root)))
    # Only the states before the first test are dropped: a later fall to zero is a fall, and is shown.
    first = next((i for i, row in enumerate(rows) if row[3] > 0), 0)
    return rows[first:]


def falls(rows: list[tuple[date, str, str, int]]) -> list[tuple[date, str, int]]:
    """Each state where the count fell: its day, the PR (or commit) that did it, and by how much."""
    found = []
    previous = None
    for day, sha, subject, n in rows:
        if previous is not None and n < previous:
            m = MERGED.search(subject)
            found.append((day, f"PR #{m.group(1)}" if m else sha[:8], previous - n))
        previous = n
    return found


def main(argv: list[str] | None = None, root: Path = ROOT, dev: str = DEV) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        rows = history(root, dev)
    except GitFailed as failure:
        print(f"test-trend: could not count the tests — {failure}", file=sys.stderr)
        return 1
    if not rows:
        print("No history on", dev)
        return 1
    fell_at = falls(rows)

    if "--merges" in argv:
        print("| Date | Merge | Tests |")
        print("|---|---|---|")
        for day, sha, subject, n in rows:
            m = MERGED.search(subject)
            print(f"| {day} | {'PR #' + m.group(1) if m else sha[:8]} | {n} |")
        return 0

    weeks: dict[date, list[tuple[date, str, str, int]]] = {}
    for row in rows:
        weeks.setdefault(row[0] - timedelta(days=row[0].weekday()), []).append(row)
    print(f"# Tests defined on {dev}, week by week\n")
    print("| Week of | Tests at week's end | Added | Merges into dev | Falls |")
    print("|---|---|---|---|---|")
    start = rows[0][3]
    before = start
    for monday in sorted(weeks):
        week = weeks[monday]
        end = week[-1][3]
        merges = sum(bool(MERGED.search(r[2])) for r in week)
        fell = sum(1 for f in fell_at if f[0] - timedelta(days=f[0].weekday()) == monday)
        print(f"| {monday} | {end:,} | {end - before:+,} | {merges} | {fell or '—'} |")
        before = end
    first, last = rows[0], rows[-1]
    print(f"\nFrom {first[3]:,} tests on {first[0]} to {last[3]:,} on {last[0]}, over {len(rows)} states of {dev}.")
    if fell_at:
        print(f"The count fell {len(fell_at)} time(s): " +
              "; ".join(f"{d} {where} (−{k})" for d, where, k in fell_at) + ".")
    else:
        print("The count never fell.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
