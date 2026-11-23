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

Two more shapes, for a rule that is not a plain ban:

- **An allow-list** (`allowed`): the pattern is allowed only where the project says so, and as often as it
  says, each place with its reason. The places are the project's own, so they live in its factory.toml,
  never in the template. A file not listed may hold none; a listed file must hold exactly its `count` —
  one more is a new site to justify, one fewer leaves the entry stale. Counted by match, not by line:

      allowed = [{ file = "src/Api/Endpoints/AuthEndpoints.cs", count = 12, reason = "sign-in, before a token" }]

- **A requirement** (`require = 1`): the pattern must appear at least that many times across the rule's
  files — a test that has to exist, say. Only the full run judges it; a commit adds lines, it does not
  show what the tree lacks. With `require_when` (globs) it is judged only once a tracked file matches one:
  a migrations test, once there is a migration.

    python devtools/checks/check-code-rules.py            # every tracked file
    python devtools/checks/check-code-rules.py --staged   # pre-commit: the lines this commit adds

The rules are factory.toml's merged over the Book's defaults (book-defaults.toml and the stack's). With
--staged they come from the staged copies (the working copy of a file that is not staged), and
only the lines the commit adds are judged: a file already holding a breach does not block an unrelated
change to it, and the full run still reports it. An allow-listed file the commit adds a match to is
counted whole, as staged, and fails when it holds more than its `count`. A factory.toml with no rules
passes, saying so. The full run is CI's and devtools/checks/ci-local.sh's; it also reports a stale entry.

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
# The settings: factory.toml merged over the Book's defaults (devtools/factory/book_config.py).
_FACTORY = next((p / "devtools" / "factory" for p in Path(__file__).resolve().parents
                 if (p / "devtools" / "factory" / "book_config.py").is_file()), None)
if _FACTORY is not None:
    sys.path.insert(0, str(_FACTORY))
import book_config  # noqa: E402

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
    # {path: (count, reason)}: where the pattern is allowed, and how often (an allow-list rule).
    allowed: tuple[tuple[str, int, str], ...] = ()
    # The least number of matches the rule's files must hold (a requirement rule); 0 for a ban.
    require: int = 0
    # A requirement is judged only once a tracked file matches one of these (none: always).
    require_when: tuple[re.Pattern, ...] = ()

    def applies_to(self, path: str) -> bool:
        return any(g.fullmatch(path) for g in self.files) and not any(g.fullmatch(path) for g in self.exempt)

    def allowance(self, path: str) -> tuple[int, str] | None:
        return next(((count, reason) for listed, count, reason in self.allowed if listed == path), None)

    def count(self, lines: list[tuple[int, str]]) -> int:
        """The matches in these lines, the skipped lines left out."""
        return sum(sum(1 for _ in self.pattern.finditer(text)) for _, text in lines
                   if self.skip_lines is None or not self.skip_lines.search(text))


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
    allowed = []
    for item in entry.get("allowed", []) or []:
        file, count, reason = (item.get(key) for key in ("file", "count", "reason")) if isinstance(item, dict) else (None,) * 3
        if not isinstance(file, str) or not file or isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise CouldNotCheck(f"{where} {entry['id']}: an `allowed` entry needs a file and a count of 1 or more "
                                f"({item!r})")
        if not isinstance(reason, str) or not reason.strip():
            raise CouldNotCheck(f"{where} {entry['id']}: the `allowed` entry for {file} gives no reason — "
                                "every place on an allow-list says why it is there")
        allowed.append((prefix + file, count, reason.strip()))
    require = entry.get("require", 0)
    if isinstance(require, bool) or not isinstance(require, int) or require < 0:
        raise CouldNotCheck(f"{where} {entry['id']}: require is not a whole number")
    if require and allowed:
        raise CouldNotCheck(f"{where} {entry['id']}: a rule either requires the pattern or allow-lists it, not both")
    when = entry.get("require_when", [])
    if not isinstance(when, list) or not all(isinstance(item, str) for item in when):
        raise CouldNotCheck(f"{where} {entry['id']}: require_when is not a list of globs")
    return CodeRule(str(entry["id"]), pattern, tuple(glob_regex(prefix + g) for g in entry["files"]),
                    tuple(glob_regex(prefix + g) for g in entry.get("exempt", [])), skip, str(entry["message"]),
                    tuple(allowed), require, tuple(glob_regex(prefix + g) for g in when))


def defaults_reader(root: Path, staged: bool) -> "book_config.Reader":
    """Where the Book's defaults files are read: as staged (the working copy where one is not), or the checkout."""
    if not staged:
        return book_config.working_reader(root)
    index, working = book_config.git_reader(root, ":"), book_config.working_reader(root)
    return lambda rel: index(rel) if index(rel) is not None else working(rel)


def load_rules(text: str, read: "book_config.Reader | None" = None) -> list[CodeRule]:
    """Every code rule in factory.toml's text merged over the Book's defaults `read` finds (none without one),
    its globs anchored at its stack's root."""
    try:
        config = book_config.load_text(text, read or (lambda rel: None), FACTORY_TOML)
    except book_config.ConfigError as error:
        raise CouldNotCheck(str(error)) from error
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


def staged_file(root: Path, rel: str) -> list[tuple[int, str]]:
    """A file as this commit stages it, numbered."""
    return list(enumerate(git(root, "show", f":{rel}").splitlines(), start=1))


def hits(rules: list[CodeRule], lines: dict[str, list[tuple[int, str]]], whole=None) -> list[str]:
    """
    The breaches in `lines`. `whole` is None for the full run (`lines` is every tracked file); in the
    pre-commit run `lines` holds the added lines and `whole(path)` reads a staged file in full.
    """
    found = []
    for rel in sorted(lines):
        applicable = [rule for rule in rules if rule.applies_to(rel) and not rule.require]
        banned = [rule for rule in applicable if rule.allowance(rel) is None]
        for number, text in lines[rel] if banned else []:
            for rule in banned:
                if rule.skip_lines is not None and rule.skip_lines.search(text):
                    continue
                match = rule.pattern.search(text)
                if match:
                    found.append(f"{rel}:{number}: {rule.id} — `{match.group(0).strip()}`: {rule.message}")
        for rule in applicable:
            allowance = rule.allowance(rel)
            if allowance is None:
                continue
            allowed, reason = allowance
            if whole is not None and not rule.count(lines[rel]):
                continue  # the commit adds no match here; the full run counts the file
            held = rule.count(whole(rel) if whole is not None else lines[rel])
            if held > allowed or (whole is None and held < allowed):
                more = ("a new site: justify it on the allow-list with a reason, or remove it" if held > allowed
                        else "fewer than listed: lower the count, or remove the entry, so the list stays true")
                found.append(f"{rel}: {rule.id} — {held} match(es), the allow-list says {allowed} ({reason}): "
                             f"{more}. {rule.message}")
    if whole is None:
        for rule in rules:
            for listed, _, _ in rule.allowed:
                if listed not in lines or not rule.applies_to(listed):
                    found.append(f"{listed}: {rule.id} — on the allow-list, but not a tracked file the rule "
                                 "applies to: remove the stale entry")
            due = not rule.require_when or any(g.fullmatch(rel) for rel in lines for g in rule.require_when)
            if rule.require and due:
                held = sum(rule.count(lines[rel]) for rel in lines if rule.applies_to(rel))
                if held < rule.require:
                    found.append(f"{rule.id} — {held} match(es) where at least {rule.require} must be: {rule.message}")
    return found


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    args = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    staged = "--staged" in args
    try:
        rules = load_rules(config_text(root, staged), defaults_reader(root, staged))
        if not rules:
            print(f"{NAME}: no code rules in {FACTORY_TOML}; nothing to judge.")
            return 0
        if staged:
            lines, scope = added_lines(root), "the lines this commit adds"
            found = hits(rules, lines, lambda rel: staged_file(root, rel))
        else:
            listed = git(root, "ls-files", "-z").split("\0")
            lines, scope = tracked_lines(root, [rel for rel in listed if rel]), "every tracked file"
            found = hits(rules, lines)
    except CouldNotCheck as error:
        print(f"{NAME}: could not check — {error}", file=sys.stderr)
        return 1
    if found:
        print(f"{NAME}: {len(found)} breach(es) of the code rules in {scope} (rules: {FACTORY_TOML}). Fix the "
              "code; a file that is the one allowed place belongs in the rule's `exempt`, a counted place on "
              "its `allowed` list with a reason:", file=sys.stderr)
        print("\n".join(f"  {line}" for line in found), file=sys.stderr)
        return 1
    print(f"{NAME}: {len(rules)} code rule(s) hold in {scope}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
