#!/usr/bin/env python3
"""
Two rules about how an integration test gets its server and its database, run on every commit.

    python devtools/checks/check-test-setup.py            # fail on a violation or a stale allow-list entry
    python devtools/checks/check-test-setup.py --staged   # what pre-commit runs: the staged files only
    python devtools/checks/check-test-setup.py --list     # print every hit, to write an allow-list entry

- test-host: no integration test class builds its own host. xUnit builds the class again for every
  test, so a host built in a constructor, a field initializer or a helper is started once per test;
  hosts started that way can accumulate (a leak risk seen in another project), and the cost here is not yet
  measured. A class
  shares its fixture's host - the fixture's shared stub-user host for a class whose tests
  differ only in who calls. A hit is `WithWebHostBuilder(`, `WithProductCaller(`, `new
  WebApplicationFactory<`, `new IntegrationTestWebAppFactory(` (or the target-typed `= new(`), a class
  that derives from either factory, `new TestServer(` or `WebApplication.CreateBuilder` outside `Fixtures/`.
- own-database: no integration test sets up its own database. The fixture copies a template migrated
  once per run; a test that starts PostgreSQL or migrates a database of its own pays for it again.
  A hit is `PostgreSqlBuilder`, `MigrateAsync` or `EnsureCreated` outside `Fixtures/`.

A hit outside `Fixtures/` needs a line in `tests/test-setup-allow-list.tsv`: rule, file (a glob
under the integration test project), how many hits it may have (`*` for any), and why. A line that
no longer matches a hit is stale and fails too, so the list only shrinks as the code improves. The
architecture tests (`TestSetupRuleTests`) read the same file and apply the same patterns, so the
hook and CI agree; keep the two patterns in step.

The integration test project is `[commands] integration_project` in devtools/factory/factory.toml,
or tests/Boilerplate.IntegrationTests when that is not set.

`--staged` judges only the test files this commit adds or changes, as staged, against the allow-list as
staged: an uncovered hit, or more hits than an entry allows, fails the commit. A stale entry needs every
file to judge, so only the full run (CI, ci-local.sh) reports it.
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from pathlib import Path
# The settings: factory.toml merged over the Book's defaults (devtools/factory/book_config.py).
_FACTORY = next((p / "devtools" / "factory" for p in Path(__file__).resolve().parents
                 if (p / "devtools" / "factory" / "book_config.py").is_file()), None)
if _FACTORY is not None:
    sys.path.insert(0, str(_FACTORY))
import book_config  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

ROOT = Path(__file__).resolve().parents[2]
TESTS = "tests/Boilerplate.IntegrationTests"
ALLOW_LIST = "tests/test-setup-allow-list.tsv"
CONFIG = "devtools/factory/factory.toml"
RULES = {
    "test-host": re.compile(
        r"\b(?:WithWebHostBuilder|WithProductCaller)\s*\("
        r"|\bnew\s+(?:WebApplicationFactory<|IntegrationTestWebAppFactory\s*\(|TestServer\s*\()"
        r"|\b(?:WebApplicationFactory<[^>]*>|IntegrationTestWebAppFactory)\s+\w+\s*=\s*new\s*\("
        r"|\bclass\s+\w+.*:.*(?<!Fixture<)\b(?:WebApplicationFactory<|IntegrationTestWebAppFactory\b)"
        r"|\bWebApplication\.CreateBuilder\b"),
    "own-database": re.compile(r"\b(?:PostgreSqlBuilder|MigrateAsync|EnsureCreated(?:Async)?)\b"),
}


def tests_folder(root: Path) -> str:
    """The integration test project: `[commands] integration_project` in factory.toml, else TESTS."""
    path = root / CONFIG
    if tomllib is None or not path.is_file():
        return TESTS
    folder = str((book_config.load(root).get("commands") or {}).get("integration_project") or "").strip("/")
    return folder or TESTS


def is_comment(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith(("//", "/*", "*"))


def in_scope(relative: str) -> bool:
    """A file under the integration project, relative to it, that the rules judge."""
    return relative.endswith(".cs") and not relative.startswith(("Fixtures/", "bin/", "obj/"))


def count(text: str, found: dict[str, dict[str, int]], relative: str) -> None:
    """Add the file's hits per rule to `found`."""
    lines = text.splitlines()
    for rule, pattern in RULES.items():
        number = sum(1 for line in lines if not is_comment(line) and pattern.search(line))
        if number:
            found[rule][relative] = number


def hits(root: Path) -> dict[str, dict[str, int]]:
    """rule -> file (relative to the integration project, '/'-separated) -> number of lines that match."""
    base = root / tests_folder(root)
    found: dict[str, dict[str, int]] = {rule: {} for rule in RULES}
    for path in sorted(base.rglob("*.cs")):
        relative = path.relative_to(base).as_posix()
        if in_scope(relative):
            count(path.read_text(encoding="utf-8-sig"), found, relative)
    return found


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout


def staged_hits(root: Path) -> dict[str, dict[str, int]]:
    """As `hits`, for the files this commit adds or changes, read as staged."""
    prefix = tests_folder(root) + "/"
    found: dict[str, dict[str, int]] = {rule: {} for rule in RULES}
    for name in git(root, "diff", "--cached", "--name-only", "--diff-filter=AM", "-z").split("\0"):
        if name.startswith(prefix) and in_scope(name[len(prefix):]):
            count(git(root, "show", f":{name}").lstrip("﻿"), found, name[len(prefix):])
    return found


def allow_list(root: Path, text: str | None = None) -> list[tuple[str, str, str, str, int]]:
    """(rule, glob, limit, reason, line number) for each entry; '#' lines and blank lines are skipped."""
    if text is None:
        path = root / ALLOW_LIST
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    entries = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 4 or not all(part.strip() for part in parts):
            entries.append(("", "", "", f"line is not rule<TAB>file<TAB>limit<TAB>reason: {line!r}", number))
            continue
        entries.append((*[part.strip() for part in parts], number))
    return entries


def staged_allow_list(root: Path) -> str:
    """The allow-list as this commit would hold it; empty when it is not in the index."""
    done = subprocess.run(["git", "-C", str(root), "show", f":{ALLOW_LIST}"], capture_output=True, text=True,
                          encoding="utf-8")
    return done.stdout if done.returncode == 0 else ""


def problems(root: Path = ROOT, staged: bool = False) -> list[str]:
    found = staged_hits(root) if staged else hits(root)
    entries = allow_list(root, staged_allow_list(root) if staged else None)
    tests = tests_folder(root)
    result = []
    covered: dict[tuple[str, str], int] = {}
    for rule, pattern, limit, reason, number in entries:
        if rule not in RULES:
            result.append(f"{ALLOW_LIST}:{number}: {reason if not rule else f'unknown rule {rule!r}'}")
            continue
        matching = [file for file in found[rule] if fnmatch.fnmatchcase(file, pattern)]
        if not matching:
            if not staged:
                result.append(f"{ALLOW_LIST}:{number}: stale - no {rule} hit in {pattern}; remove the entry")
            continue
        for file in matching:
            covered[(rule, file)] = number
            if limit != "*" and found[rule][file] > int(limit):
                result.append(f"{file}: {found[rule][file]} {rule} hits, {ALLOW_LIST}:{number} allows {limit}")
            if not staged and limit != "*" and len(matching) == 1 and found[rule][file] < int(limit):
                result.append(f"{ALLOW_LIST}:{number}: stale - {file} has {found[rule][file]} {rule} hits, "
                              f"the entry allows {limit}; lower it")
    for rule, files in found.items():
        for file, number in files.items():
            if (rule, file) not in covered:
                result.append(f"{tests}/{file}: {number} {rule} hit(s) - "
                              + ("a test builds its own host; share the fixture's shared host, "
                                 "or add an allow-list entry with the reason it cannot"
                                 if rule == "test-host" else
                                 "a test sets up its own database; use the fixture's, or add an allow-list "
                                 "entry with the reason it cannot"))
    return result


def main(argv: list[str], root: Path = ROOT) -> int:
    if "--list" in argv:
        for rule, files in hits(root).items():
            for file, number in files.items():
                print(f"{rule}\t{file}\t{number}")
        return 0
    staged = "--staged" in argv
    try:
        found = problems(root, staged)
    except (OSError, subprocess.CalledProcessError) as error:
        # A check that could not run does not pass.
        print(f"check-test-setup: could not read the {'staged ' if staged else ''}test files: {error}",
              file=sys.stderr)
        return 1
    for problem in found:
        print(f"check-test-setup: {problem}", file=sys.stderr)
    if found:
        return 1
    print("Test setup: no test builds its own host or database outside the allow-list"
          + (" (staged files)." if staged else "."))
    return 0


if __name__ == "__main__":
    # UTF-8 out, so an em dash prints on a Windows console's code page too (Book D45).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv))
