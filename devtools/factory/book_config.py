"""
The Book's settings, merged: the Book's defaults with the project's own `devtools/factory/factory.toml` over them.

Every reader of the settings - book.py and factory.py, the checks, the guard, the hooks, the local CI and the
plugin's scripts - reads them through this module, so a project's file holds only what is its own.

The layers, in order (each later one wins):

1. `devtools/factory/book-defaults.toml` - the Book's defaults for every Book;
2. `devtools/factory/book-defaults/<pack>.toml` - the stack's (`[book] stack`, `none.toml` for a stack-free
   Book), then each of `[book] packs` in their order; a pack with no file adds nothing;
3. `devtools/factory/factory.toml` - the project's own settings and exceptions.

How they merge:

- **tables merge per key**, all the way down; a scalar the later layer sets wins;
- **a list the later layer sets replaces the earlier one**, except
- **a list of tables that each carry an `id`** (`[[code_rules]]`): merged by id - an entry with an earlier id
  changes only the keys it sets; a new id is added after the earlier ones, in its order; an entry the merge
  leaves with `enabled = false` is switched off and left out of the merged settings, so a project turns a
  default code rule off with `[[code_rules]]` `id = "<its id>"`, `enabled = false`;
- **`[extend]`** in the project's file appends to the merged list at the same path, skipping what it already
  holds: `[extend.guard] ask = ["tools/**"]` adds to `[guard] ask` without restating the Book's list, so a
  later release's additions still arrive. An `[extend]` entry for a list nothing sets is that list.

The defaults files are the Book's, copied into the project and never edited there (check-book fails on a
changed one). A Book older than v1.6 has none: the project's file is then the whole of the settings. A
defaults file that is not valid TOML is an error, never an empty layer; so is one a 1.6 Book should hold and
does not (the core file once `[book] book_version` is 1.6 or later, and any file `book-files.lock` records),
so that a deleted file stops the guard rather than emptying its lists. The private settings (factory.py's
load_config) are not read here.

Python 3.11+, standard library only.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
from pathlib import Path
from typing import Callable

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

PROJECT = "devtools/factory/factory.toml"
DEFAULTS = "devtools/factory/book-defaults.toml"
PACK_DEFAULTS = "devtools/factory/book-defaults/{pack}.toml"
EXTEND = "extend"

# Reads one repository-relative file's text, or None when it has none.
Reader = Callable[[str], "str | None"]


class ConfigError(ValueError):
    """A settings file that is not valid TOML, or an `[extend]` entry that is not a list: named, never guessed."""


def parse(text: str, where: str) -> dict:
    if tomllib is None:
        raise ConfigError("the Book's settings need Python 3.11 or later (tomllib)")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{where} is not valid TOML ({error})") from None


def packs(project: dict) -> list[str]:
    """The defaults layers past the core one: `[book] stack` (`none` included: a stack-free Book's review
    dimensions are its own), then `[book] packs`."""
    book = project.get("book") or {}
    stack = str(book.get("stack") or "none")
    return list(dict.fromkeys([stack] + [str(p) for p in book.get("packs") or []]))


def layers(project: dict) -> list[str]:
    """The defaults files that apply to a project, in merge order (the project's own file comes after them)."""
    return [DEFAULTS, *(PACK_DEFAULTS.format(pack=pack) for pack in packs(project))]


def _by_id(items: list) -> bool:
    return bool(items) and all(isinstance(item, dict) and "id" in item for item in items)


def merge(base: object, over: object) -> object:
    """`over` merged onto `base` by the rules in the module docstring (neither is changed)."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for key, value in over.items():
            out[key] = merge(base[key], value) if key in base else value
        return out
    if isinstance(base, list) and isinstance(over, list) and _by_id(base) and _by_id(over):
        out = list(base)
        at = {item["id"]: i for i, item in enumerate(out)}
        for item in over:
            if item["id"] in at:
                out[at[item["id"]]] = merge(out[at[item["id"]]], item)
            else:
                at[item["id"]] = len(out)
                out.append(item)
        return out
    return over


def extend(config: dict, additions: dict, path: str = EXTEND) -> dict:
    """`[extend]` applied: each list in `additions` appended to the list at the same path in `config`."""
    out = dict(config)
    for key, value in additions.items():
        where = f"{path}.{key}"
        if isinstance(value, dict):
            current = out.get(key)
            if current is not None and not isinstance(current, dict):
                raise ConfigError(f"[{where}] extends {key}, which is not a table")
            out[key] = extend(current or {}, value, where)
        elif isinstance(value, list):
            current = out.get(key)
            if current is not None and not isinstance(current, list):
                raise ConfigError(f"{where} extends {key}, which is not a list")
            out[key] = list(current or []) + [item for item in value if item not in (current or [])]
        else:
            raise ConfigError(f"{where} is not a list: [extend] only adds to a list")
    return out


def at_least(version: object, wanted: tuple[int, ...]) -> bool:
    """`[book] book_version` (`1.6.0`) is `wanted` or later; False for a missing or unreadable one."""
    parts = re.findall(r"\d+", str(version or ""))[:len(wanted)]
    return bool(parts) and tuple(int(part) for part in parts) >= wanted


def locked_files(read: Reader) -> dict:
    """The files `book-files.lock` records (path -> hash); {} where there is none or it is not TOML (the drift
    check names a broken lock)."""
    text = read(LOCK)
    if text is None or tomllib is None:
        return {}
    try:
        return tomllib.loads(text).get("files") or {}
    except tomllib.TOMLDecodeError:
        return {}


def undeclinable(rel: str) -> bool:
    """A Book file `[book] declined` cannot name: a defaults file (the guard's lists are in them) or a common
    rules file the CLAUDE.md files import. Declining one would switch the guard or the rules off unseen."""
    rel = posixpath.normpath(str(rel).replace("\\", "/")).lower()  # as the guard reads a path: any case
    return (rel == DEFAULTS or (rel.startswith("devtools/factory/book-defaults/") and rel.endswith(".toml"))
            or (rel.startswith(".claude/book/") and rel.endswith(".md")))


def declined_files(book: dict) -> set[str]:
    """`[book] declined`, less the entries it may not name (`undeclinable`): those are never honoured."""
    return {str(d) for d in book.get("declined") or [] if not undeclinable(str(d))}


def declined_problems(config: dict) -> list[str]:
    """Each `[book] declined` entry naming a file the Book never lets a project decline."""
    return [f"devtools/factory/factory.toml: [book] declined names {d}, which a project cannot decline - the "
            "guard's lists and the Book's rules come from it. Take the entry out (and restore the file if it is "
            "gone); a key of its own in factory.toml wins over a default"
            for d in (config.get("book") or {}).get("declined") or [] if undeclinable(str(d))]


def defaults(project: dict, read: Reader) -> dict:
    """The defaults alone, as they apply to this project's packs: what the project's file need not repeat."""
    book = project.get("book") or {}
    declined = declined_files(book)
    locked = None
    config: dict = {}
    for rel in layers(project):
        text = read(rel)
        if text is None:
            if locked is None:
                locked = locked_files(read)
            wanted = rel in locked or rel == DEFAULTS and at_least(book.get("book_version"), (1, 6))
            if wanted and rel not in declined:
                raise ConfigError(f"{rel} is missing: a Book from 1.6 on reads its defaults from it, and without "
                                  "it the settings would lose the Book's lists. Restore it (`book upgrade` writes "
                                  "it) rather than run without it")
            continue
        config = merge(config, parse(text, rel))
    return config


def merged(project: dict, read: Reader) -> dict:
    """The project's settings over the defaults `read` finds, `[extend]` applied last."""
    project = dict(project)
    additions = project.pop(EXTEND, None) or {}
    if not isinstance(additions, dict):
        raise ConfigError("[extend] is not a table")
    return switched_on(extend(merge(defaults(project, read), project), additions))


def switched_on(config: dict) -> dict:
    """The settings without the code rules a layer switched off (`enabled = false`), top-level and in each of
    `[[stacks]]`, so every reader of the merged settings sees the same rules."""
    def rules(table: dict) -> dict:
        found = table.get("code_rules")
        if not isinstance(found, list):
            return table
        return dict(table, code_rules=[rule for rule in found
                                       if not (isinstance(rule, dict) and rule.get("enabled") is False)])
    out = rules(config)
    if isinstance(out.get("stacks"), list):
        out = dict(out, stacks=[rules(stack) if isinstance(stack, dict) else stack for stack in out["stacks"]])
    return out


def working_reader(root: Path) -> Reader:
    def read(rel: str) -> str | None:
        path = Path(root) / rel
        return path.read_text(encoding="utf-8") if path.is_file() else None
    return read


def git_reader(root: Path, spec: str) -> Reader:
    """A reader of the files `git show <spec><path>` holds: `spec` is `<ref>:` for a commit, `:` for the index."""
    def read(rel: str) -> str | None:
        done = subprocess.run(["git", "show", f"{spec}{rel}"], cwd=root, capture_output=True)
        return done.stdout.decode("utf-8") if done.returncode == 0 else None
    return read


def load(root: Path) -> dict:
    """The merged settings of the checkout at `root`; {} when it has no factory.toml."""
    read = working_reader(root)
    text = read(PROJECT)
    return merged(parse(text, PROJECT), read) if text is not None else {}


def load_text(text: str, read: Reader, where: str = PROJECT) -> dict:
    """The merged settings for a project file's text, its defaults read with `read`."""
    return merged(parse(text, where), read)


def load_at(root: Path, ref: str) -> dict | None:
    """The merged settings as commit `ref` holds them; None when it holds no factory.toml."""
    read = git_reader(root, f"{ref}:")
    text = read(PROJECT)
    return merged(parse(text, f"{ref}:{PROJECT}"), read) if text is not None else None


def load_staged(root: Path) -> dict:
    """The merged settings as the index holds them, each file the working copy's where it is not staged."""
    staged, working = git_reader(root, ":"), working_reader(root)

    def read(rel: str) -> str | None:
        text = staged(rel)
        return text if text is not None else working(rel)
    text = read(PROJECT)
    return merged(parse(text, PROJECT), read) if text is not None else {}


def table_cells(line: str) -> list[str]:
    """A Markdown table row's cells, as GitHub and Bitbucket read them: split on each pipe that no backslash
    escapes (`\\|` stays in its cell, as written); a pipe inside a code span splits too, as it does when the
    table renders. Every tool that reads a Book table splits its rows here, so they never disagree."""
    text = line.strip()
    text = text[1:] if text.startswith("|") else text
    text = text[:-1] if text.endswith("|") and not text.endswith("\\|") else text
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", text)]


def value(config: dict, key: str) -> object:
    """One value by dotted key (`fe_guides.drive_folder_id`); KeyError naming the key when it is not set."""
    found: object = config
    for part in key.split("."):
        if not isinstance(found, dict) or part not in found:
            raise KeyError(key)
        found = found[part]
    return found


# ---- The instruction files' imports --------------------------------------------------------------------------
# A CLAUDE.md holds the Book's common rules by importing them (`@.claude/book/rules.md` on a line of its own),
# as Claude Code reads it: a path relative to the importing file, at most four hops deep, never inside a code
# span or a fenced block. A check that reads the rules reads the text Claude Code sees.

IMPORT = re.compile(r"^@(\S+)\s*$")
IMPORT_DEPTH = 4
FENCE = re.compile(r"^\s*(```|~~~)")


def imports(text: str) -> list[str]:
    """The paths a file imports, in order: `@path` lines outside fenced blocks."""
    found, fenced = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced and (match := IMPORT.match(line)):
            found.append(match.group(1))
    return found


def import_target(rel: str, path: str) -> str:
    """The repository path an `@path` in file `rel` names."""
    return posixpath.normpath(posixpath.join(posixpath.dirname(rel), path))


def expanded(root: Path, rel: str, depth: int = 0) -> str | None:
    """File `rel`'s text with each import replaced by the imported text (an import that is missing stays as
    it is: `missing_imports` names it); None when `rel` itself is absent."""
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except OSError:
        return None
    if depth >= IMPORT_DEPTH:
        return text
    out, fenced = [], False
    for line in text.splitlines(keepends=True):
        if FENCE.match(line):
            fenced = not fenced
        match = None if fenced else IMPORT.match(line.rstrip("\r\n"))
        inner = expanded(root, import_target(rel, match.group(1)), depth + 1) if match else None
        out.append(inner + ("" if inner.endswith("\n") else "\n") if inner is not None else line)
    return "".join(out)


def missing_imports(root: Path, rel: str) -> list[str]:
    """The imports in file `rel` whose target does not exist, as `rel: @path`."""
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except OSError:
        return []
    return [f"{rel}: @{path}" for path in imports(text) if not (root / import_target(rel, path)).is_file()]


# ---- The Book's own files, as the Book wrote them ------------------------------------------------------------
# `book init` and `book upgrade --apply` record each Book-owned file's hash in LOCK. A project's copy that no
# longer matches is drift: the project changed a file the next upgrade replaces. check-book fails on it, naming
# where the change belongs; `[book] drift_accepted` (`{ file, reason }`) holds one, as a warning, until it moves.

LOCK = "devtools/factory/book-files.lock"
LOCK_EXEMPT = ("devtools/factory/book-template/",)  # the template copy is the Book's source, not run here


def digest(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def lock_text(files: dict[str, bytes]) -> str:
    """The lock for these files (path -> the bytes the Book wrote)."""
    lines = ["# The Book's own files as `book init` or `book upgrade --apply` wrote them: path = sha256 (line",
             "# endings aside). Written by the Book, never by hand; check-book compares each file with it.",
             "[files]"]
    lines += [f'"{rel}" = "{digest(data)}"' for rel, data in sorted(files.items())
              if not rel.startswith(LOCK_EXEMPT)]
    return "\n".join(lines) + "\n"


def where_it_belongs(rel: str) -> str:
    if rel.startswith(".claude/book/"):
        return "put a rule of the project's own in the CLAUDE.md that imports this file, after the import"
    if rel.startswith("devtools/factory/book-defaults"):
        return ("set the key in devtools/factory/factory.toml (a project key wins; `[extend.<table>]` adds to a "
                "list)")
    return ("make it a setting in devtools/factory/factory.toml if the tool reads one, or list the file under "
            "`[book] declined` if the project now owns it")


def drift(root: Path, config: dict) -> tuple[list[str], list[str]]:
    """(problems, warnings): each Book-owned file whose content differs from what the Book wrote."""
    try:
        text = (root / LOCK).read_text(encoding="utf-8")
    except OSError:
        return [], []  # a Book older than 1.6, or the template repository itself: nothing recorded
    try:
        locked = tomllib.loads(text).get("files") or {}
    except tomllib.TOMLDecodeError as error:
        return [f"{LOCK}: not valid TOML ({error}) - the Book's files could not be compared"], []
    book = config.get("book") or {}
    accepted = {str(e.get("file")): str(e.get("reason") or "") for e in book.get("drift_accepted") or []
                if isinstance(e, dict)}
    declined = declined_files(book)
    problems, warnings = declined_problems(config), []
    for rel, want in sorted(locked.items()):
        path = root / rel
        if rel in declined:
            continue
        if not path.is_file():
            # A deleted Book file is drift too: a defaults file gone would empty the guard's lists unseen.
            if accepted.get(rel):
                warnings.append(f"{rel}: missing, accepted for now ({accepted[rel]})")
            else:
                problems.append(f"{rel}: missing - this file is the Book's and `book upgrade` writes it. Restore "
                                "it (`git checkout -- <file>`, or `book upgrade --apply`)"
                                + ("" if undeclinable(rel) else ", or list it under `[book] declined` if the "
                                   "project no longer uses it"))
            continue
        if digest(path.read_bytes()) == want:
            continue
        if accepted.get(rel):
            warnings.append(f"{rel}: differs from the Book's copy, accepted for now ({accepted[rel]})")
        else:
            problems.append(f"{rel}: differs from the Book's copy - this file is the Book's and `book upgrade` "
                            f"replaces it. To keep your change, {where_it_belongs(rel)}; or propose it to the Book. "
                            "Until then `[book] drift_accepted = [{ file, reason }]` holds it as a warning")
    for rel, reason in accepted.items():
        if not reason:
            problems.append(f"devtools/factory/factory.toml: [book] drift_accepted {rel} has no reason")
    return problems, warnings
