#!/usr/bin/env python3
"""
Runs one folder's Python self-tests in several processes, and says exactly what `unittest discover`
would: the number run, and OK or the failures.

    python devtools/checks/run-self-tests.py <folder> [--workers N]

The factory, checks and guard suites spend their time starting git and Python processes, so they are
waiting, not computing, and the machine has cores to spare: the factory suite alone was ten minutes of
a 26-minute local run, one git process after another. Each worker discovers the same tests and runs
every Nth one (round robin, so a slow class is spread over all of them); a shard that fails prints its
output. The count is checked against discovery, so a test that no shard ran fails the run, and "Ran 0
tests" fails as it does for ci-local.sh's other suites. Nothing is skipped, removed or weakened: the
same tests run, only side by side.

Exit 0 only when every shard passed, the shards together ran every discovered test, at least one was
discovered, and none was skipped (a self-test that skips proves nothing).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import unittest

DEFAULT_WORKERS = 4


def flatten(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def discover(folder: str) -> list[unittest.TestCase]:
    sys.path.insert(0, folder)
    suite = unittest.defaultTestLoader.discover(folder, pattern="test_*.py", top_level_dir=folder)
    return sorted(flatten(suite), key=lambda test: test.id())


def work(folder: str, shard: int, workers: int) -> int:
    """One shard: every `workers`-th test of the sorted list, run in this process."""
    tests = discover(folder)
    suite = unittest.TestSuite(test for index, test in enumerate(tests) if index % workers == shard)
    return 0 if unittest.TextTestRunner(verbosity=0).run(suite).wasSuccessful() else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--workers", type=int, default=min(DEFAULT_WORKERS, os.cpu_count() or 1))
    parser.add_argument("--shard", type=int)
    args = parser.parse_args(argv)
    folder = os.path.abspath(args.folder)
    if args.shard is not None:
        return work(folder, args.shard, args.workers)

    started = time.perf_counter()
    expected = len(discover(folder))
    if expected == 0:
        print(f"Ran 0 tests: discovery found none in {folder}.")
        return 1
    shards = [subprocess.Popen([sys.executable, os.path.abspath(__file__), folder, "--workers", str(args.workers),
                                "--shard", str(shard)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
              for shard in range(args.workers)]
    ran, skipped, failed = 0, 0, False
    for shard in shards:
        output = shard.communicate()[0]
        counted = re.search(r"^Ran (\d+) tests? in", output, re.M)
        skips = re.search(r"skipped=(\d+)", output)
        ran += int(counted.group(1)) if counted else 0
        skipped += int(skips.group(1)) if skips else 0
        if shard.returncode != 0 or not counted:
            failed = True
            print(output)
    elapsed = time.perf_counter() - started
    if ran != expected:
        print(f"Ran {ran} tests, but discovery found {expected}: a shard did not run its tests.")
        failed = True
    if skipped:
        print(f"skipped={skipped}: a self-test must not skip.")
        failed = True
    print(f"Ran {ran} tests in {elapsed:.1f}s ({args.workers} processes) skipped={skipped}\n")
    print("FAILED" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    # UTF-8 out, so an em dash prints on a Windows console's code page too (Book D45).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
