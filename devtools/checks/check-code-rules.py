#!/usr/bin/env python3
r"""
The code rules a regular expression can hold, held for every stack: a banned call is refused where it is
written, whatever the language.

Some of CLAUDE.md's coding principles come down to a call that must not appear in product code: the
system clock read directly instead of through the injected clock, a task blocked on, a catch-all that
swallows a fault, a stray print instead of the logger. Each is one line of text, so one regular expression
finds it in any language. The rules are data, not code: each stack's pack, in devtools/factory/factory.toml.

One stack (`[adapter]` at the top level):

    [[code_rules]]
    id = "clock"
    pattern = '\bDateTime(?:Offset)?\.(?:UtcNow|Now|Today)\b'
    files = ["src/**/*.cs"]                 # the files it applies to
    exempt = ["src/**/SystemClock.cs"]      # the one place allowed, if any
    skip_lines = '^\s*(?://|/?\*)'           # optional: lines not judged (comments)
    message = "Read the time from the injected clock (CLAUDE.md, Coding principles)."

Several stacks: the same rules as `[[stacks.code_rules]]` under each `[[stacks]]` entry, their globs relative
to that stack's `root`. Top-level `[[code_rules]]` apply from the repository's root in either layout.

Globs: `**/` is any number of folders (none included), `*` any part of one name, `?` one character;
case-sensitive, as git stores paths. A file matching `files` and no `exempt` glob is judged line by line;
a line matching `skip_lines` is not.

    python devtools/checks/check-code-rules.py            # every tracked file
    python devtools/checks/check-code-rules.py --staged   # pre-commit: the lines this commit adds

With --staged the rules come from the staged factory.toml (the working copy when it is not staged), and
only the lines the commit adds are judged: a file already holding a breach does not block an unrelated
change to it, and the full run still reports it. A factory.toml with no rules passes, saying so.

Prints `path:line: id — matched: message` for each hit and exits 1 on any. "Could not check" — no readable
factory.toml, a rule missing a key, a pattern that is not a valid expression, a git command that failed —
also exits 1: an empty answer is not a clean one.

Python 3.11+, standard library only.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FACTORY_TOML = "devtools/factory/factory.toml"
NAME = "check-code-rules"
REQUIRED = ("id", "pattern", "files", "message")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class CouldNotCheck(Exception):
    """The rules, or what they are judged against, are unknown: nothing may pass as clean."""


@dataclass(frozen=True)
class CodeRule:
    id: str
    pattern: re.Pattern
    files: tuple[re.Pattern, ...]
    exempt: tuple[re.Pattern, ...]
    skip_lines: re.Pattern | None
    message: str

    def applies_to(self, path: str) -> bool:
        return any(g.fullmatch(path) for g in self.files) and not any(g.fullmatch(path) for g in self.exempt)


def glob_regex(glob: str) -> re.Pattern:
    """A glob as a regular expression over a repository path: `**/` any folders, `*` within one name."""
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out))


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=root, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise CouldNotCheck(f"`git {' '.join(args[:2])}` failed ({done.returncode}): {done.stderr.strip()}")
    return done.stdout


def config_text(root: Path, staged: bool) -> str:
    if staged:
        done = subprocess.run(["git", "show", f":{FACTORY_TOML}"], cwd=root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if done.returncode == 0:
            return done.stdout
    path = root / FACTORY_TOML
    if not path.is_file():
        raise CouldNotCheck(f"no {FACTORY_TOML}, so there are no rules to apply")
    return path.read_text(encoding="utf-8")


def compile_rule(entry: object, where: str, prefix: str) -> CodeRule:
    if not isinstance(entry, dict):
        raise CouldNotCheck(f"{where}: a code rule is not a table")
    missing = [key for key in REQUIRED if not entry.get(key)]
    if missing:
        raise CouldNotCheck(f"{where} {entry.get('id', '(no id)')}: lacks {', '.join(missing)}")
    for key in ("files", "exempt"):
        value = entry.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise CouldNotCheck(f"{where} {entry['id']}: {key} is not a list of globs")
    try:
        pattern = re.compile(entry["pattern"])
        skip = re.compile(entry["skip_lines"]) if entry.get("skip_lines") else None
    except re.error as error:
        raise CouldNotCheck(f"{where} {entry['id']}: not a valid expression ({error})") from error
    return CodeRule(str(entry["id"]), pattern, tuple(glob_regex(prefix + g) for g in entry["files"]),
                    tuple(glob_regex(prefix + g) for g in entry.get("exempt", [])), skip, str(entry["message"]))


def load_rules(text: str) -> list[CodeRule]:
    """Every code rule in factory.toml's text, its globs anchored at its stack's root."""
    try:
        config = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CouldNotCheck(f"{FACTORY_TOML} is not valid TOML ({error})") from error
    found = [compile_rule(entry, "[[code_rules]]", "") for entry in config.get("code_rules", [])]
    for stack in config.get("stacks", []) or []:
        if not isinstance(stack, dict):
            continue
        root = str(stack.get("root", "")).strip("/")
        prefix = f"{root}/" if root else ""
        found += [compile_rule(entry, f"[[stacks.code_rules]] in {root or '.'}/", prefix)
                  for entry in stack.get("code_rules", [])]
    return found


def tracked_lines(root: Path, paths: list[str]) -> dict[str, list[tuple[int, str]]]:
    """Every line of each tracked file, numbered; a file that is not text is left out."""
    lines = {}
    for rel in paths:
        path = root / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        lines[rel] = list(enumerate(text.splitlines(), start=1))
    return lines


def added_lines(root: Path) -> dict[str, list[tuple[int, str]]]:
    """The lines the staged commit adds, by file and their line number in it."""
    out = git(root, "diff", "--cached", "--no-color", "--no-ext-diff", "--no-renames", "-U0",
              "--diff-filter=ACMR", "--src-prefix=a/", "--dst-prefix=b/")
    found: dict[str, list[tuple[int, str]]] = {}
    current, number, header = None, 0, False
    for line in out.splitlines():
        if line.startswith("diff --git "):
            current, header = None, True
        elif header and line.startswith("+++ "):
            # git ends a path holding a space with a tab, so a patch tool can find where it ends.
            target = line[4:].rstrip("\t")
            current = target[2:] if target.startswith("b/") else None
            if current is not None:
                found.setdefault(current, [])
        elif header and line.startswith("Binary files"):
            current = None
        elif line.startswith("@@"):
            header = False
            match = HUNK.match(line)
            number = int(match.group(1)) if match else 0
        elif not header and line.startswith("+") and current is not None:
            found[current].append((number, line[1:]))
            number += 1
    return found


def hits(rules: list[CodeRule], lines: dict[str, list[tuple[int, str]]]) -> list[str]:
    found = []
    for rel in sorted(lines):
        applicable = [rule for rule in rules if rule.applies_to(rel)]
        for number, text in lines[rel] if applicable else []:
            for rule in applicable:
                if rule.skip_lines is not None and rule.skip_lines.search(text):
                    continue
                match = rule.pattern.search(text)
                if match:
                    found.append(f"{rel}:{number}: {rule.id} — `{match.group(0).strip()}`: {rule.message}")
    return found


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    args = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    staged = "--staged" in args
    try:
        rules = load_rules(config_text(root, staged))
        if not rules:
            print(f"{NAME}: no code rules in {FACTORY_TOML}; nothing to judge.")
            return 0
        if staged:
            lines, scope = added_lines(root), "the lines this commit adds"
        else:
            listed = git(root, "ls-files", "-z").split("\0")
            lines, scope = tracked_lines(root, [rel for rel in listed if rel]), "every tracked file"
        found = hits(rules, lines)
    except CouldNotCheck as error:
        print(f"{NAME}: could not check — {error}", file=sys.stderr)
        return 1
    if found:
        print(f"{NAME}: {len(found)} breach(es) of the code rules in {scope} (rules: {FACTORY_TOML}). Fix the "
              "code; a file that is the one allowed place belongs in the rule's `exempt`:", file=sys.stderr)
        print("\n".join(f"  {line}" for line in found), file=sys.stderr)
        return 1
    print(f"{NAME}: {len(rules)} code rule(s) hold in {scope}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
