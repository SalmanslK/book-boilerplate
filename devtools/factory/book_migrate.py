"""
The one-time moves a Book makes on its way to 1.6, for `book upgrade` (report-first, written by `--apply`), and
`book defects classify`.

1.6 writes the Book's common parts once, in its own files, and leaves the project's files holding only what is
the project's. A Book from before then has the common parts copied into its own files, so the upgrade moves
them out:

- **The instruction files** (`CLAUDE.md`, `docs/CLAUDE.md`, `tests/CLAUDE.md`, the dotnet pack's nested ones):
  each block of the project's file - a paragraph, a list item, a table row, a heading - that matches the
  text the installed Book wrote there, or the text of the Book's rules files, is the Book's and goes; the
  file imports the Book's rules (`@.claude/book/rules.md`) and keeps every other block, in its order, under
  the headings it had. A table keeps its header with the rows that stay.
- **The settings** (`devtools/factory/factory.toml`): a key whose value is the Book's default for this Book's
  stack and packs goes, with the comment above it; a `[[code_rules]]` entry equal to the default with its id
  goes. The result is written only when the settings it gives, merged over the defaults, are the settings
  the original gives; otherwise nothing is written and the report says why. A list that holds the defaults
  and more is named, for a person to turn into `[extend.<table>]`.
- `[book] type_source_from` (the day the defects' Type and Source columns start) and `[book] drift_accepted`
  (each Book file the project changed, kept as it is for now) are added.

What it cannot decide it reports as CONFIRM, for a person. It never touches a ledger: `book defects classify`
proposes each open defect's Type and Source for the person to approve, and /book:check-sources each
question's State.
"""
from __future__ import annotations

import datetime
import importlib.util
import re
import sys
from pathlib import Path

import book_config

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python before 3.11
    tomllib = None

CONFIG = "devtools/factory/factory.toml"
REGISTER_KEEP = "docs/requirements/.gitkeep"
SPLIT_FROM = (1, 6, 0)


# ---------------------------------------------------------------- blocks

def norm(text: str) -> str:
    """A block's text for comparing: whitespace collapsed, emphasis and link targets kept."""
    return re.sub(r"\s+", " ", text).strip()


def blocks(lines: list[str]) -> list[tuple[str, list[int]]]:
    """The file's blocks, (kind, line indexes): a heading, a table row, a list item with its continuation
    lines, a paragraph, a comment (`<!-- -->`), an import line. Blank lines and fences end a block; a fenced
    block is one block."""
    found: list[tuple[str, list[int]]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith(("```", "~~~")):
            start = i
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(("```", "~~~")):
                i += 1
            found.append(("fence", list(range(start, min(i + 1, len(lines))))))
            i += 1
            continue
        if stripped.startswith("#"):
            found.append(("heading", [i]))
            i += 1
            continue
        if stripped.startswith("|"):
            found.append(("row", [i]))
            i += 1
            continue
        if re.match(r"^(\s*)([-*]|\d+\.)\s", line):
            indent = len(line) - len(line.lstrip())
            span = [i]
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(("|", "#")) \
                    and not re.match(r"^\s{0,%d}([-*]|\d+\.)\s" % indent, lines[i]) \
                    and (len(lines[i]) - len(lines[i].lstrip())) > indent:
                span.append(i)
                i += 1
            found.append(("item", span))
            continue
        span = [i]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(("|", "#", "```", "~~~")) \
                and not re.match(r"^\s*([-*]|\d+\.)\s", lines[i]):
            span.append(i)
            i += 1
        found.append(("para", span))
    return found


def block_texts(text: str) -> set[str]:
    lines = text.replace("\r\n", "\n").split("\n")
    return {norm("\n".join(lines[i] for i in span)) for _, span in blocks(lines)}


def skeleton_sections(skeleton: str) -> list[tuple[str, list[str]]]:
    """The new skeleton's `## ` sections, each with its lines."""
    found: list[tuple[str, list[str]]] = []
    for line in skeleton.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            found.append((line, []))
        elif line.startswith("# ") and found:
            found.append(("", []))  # a top-level heading ends the section before it
        elif found:
            found[-1][1].append(line)
    return [(heading, lines) for heading, lines in found if heading]


def section_key(heading: str) -> str:
    """`## Where knowledge lives — this project's documents` and `## Where knowledge lives` are one section."""
    return re.split(r"\s+[—–]\s+", heading.lstrip("# ").strip(), maxsplit=1)[0].lower()


def split_instructions(project: str, common: set[str], imports: list[str], title: str,
                       skeleton: str = "") -> tuple[str, int, int, list[str]]:
    """The project's instruction file with the Book's blocks taken out and the imports put in: (text, blocks
    moved, blocks kept, the headings that keep something)."""
    lines = project.replace("\r\n", "\n").split("\n")
    found = blocks(lines)
    drop: set[int] = set()
    moved = kept = 0
    for kind, span in found:
        if kind == "heading":
            continue
        if norm("\n".join(lines[i] for i in span)) in common:
            drop.update(span)
            moved += 1
        else:
            kept += 1
    # A table keeps its header and separator while any of its rows stays.
    for index, (kind, span) in enumerate(found):
        if kind != "row" or span[0] in drop:
            continue
        start = index
        while start > 0 and found[start - 1][0] == "row" and found[start - 1][1][0] == found[start][1][0] - 1:
            start -= 1
        for header in found[start:start + 2]:  # the header row and its separator
            if header[0] == "row":
                drop.difference_update(header[1])
    # The text before the first `## ` heading is the preamble: the title stays, the import goes after it.
    first = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
    head = [line for i, line in enumerate(lines[:first]) if i not in drop]
    own: list[str] = []
    if first == len(lines):
        # No section to keep them in: past the file's own `# ` title, the kept lines are its rules and go under
        # the project's title, after the import, rather than above it.
        at = next((n + 1 for n, line in enumerate(head) if line.startswith("# ")), 0)
        head, own = head[:at], trim(head[at:])
    body: list[str] = []
    sections: list[str] = []
    section: list[str] = []
    heading = ""

    def close() -> None:
        content = [line for line in section if line.strip()]
        if content:
            body.extend([heading, ""] + trim(section) + [""])
            sections.append(heading.lstrip("# ").strip())
    for i in range(first, len(lines)):
        line = lines[i]
        if line.startswith("## "):
            close()
            heading, section = line, []
        elif i not in drop:
            section.append(line)
    close()
    # A section the new skeleton keeps in the project's file, and the project's file lost (all of it was the
    # Book's): it comes back as the skeleton has it, its slots for a person to fill.
    have = {section_key(s) for s in sections}
    for heading, lines in skeleton_sections(skeleton):
        if section_key(heading) not in have and any(line.strip() for line in lines):
            body.extend([heading, ""] + trim(lines) + [""])
    out = trim(head) + [""] + imports + ["", title, ""] + (own + [""] if own else []) + body
    text = "\n".join(collapse(out)).rstrip() + "\n"
    return text, moved, kept, sections


def trim(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return lines


def collapse(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line)
    return out


# ---------------------------------------------------------------- the settings

HEADER = re.compile(r"^\s*(\[\[?)\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")
ASSIGN = re.compile(r"^\s*([A-Za-z0-9_\-\"'.]+)\s*=")


STRINGS = re.compile(r'"""[\s\S]*?"""' + r"|'''[\s\S]*?'''" + r'|"(?:\\.|[^"\\])*"' + r"|'[^']*'")


def balanced(text: str) -> bool:
    """Whether a value's brackets, braces and triple-quoted strings are closed."""
    if text.count('"""') % 2 or text.count("'''") % 2:
        return False
    plain = STRINGS.sub('""', text)
    plain = "\n".join(line.split("#", 1)[0] for line in plain.split("\n"))
    return plain.count("[") + plain.count("{") <= plain.count("]") + plain.count("}")


def entries(lines: list[str]) -> list[dict]:
    """Each table header and key assignment: {kind, path, start, end} - `end` past its last line."""
    found: list[dict] = []
    table: list[str] = []
    array = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        header = HEADER.match(line) if stripped.startswith("[") else None
        if header:
            table = [part.strip().strip('"') for part in header.group(2).split(".")]
            array = header.group(1) == "[["
            found.append({"kind": "array" if array else "table", "path": table, "start": i, "end": i + 1})
            i += 1
            continue
        assign = ASSIGN.match(line) if stripped and not stripped.startswith("#") else None
        if assign:
            start = i
            text = line.split("=", 1)[1]
            while not balanced(text) and i + 1 < len(lines):
                i += 1
                text += "\n" + lines[i]
            key = [part.strip().strip('"') for part in assign.group(1).split(".")]
            found.append({"kind": "key", "path": table + key, "start": start, "end": i + 1, "in_array": array})
        i += 1
    return found


def value_at(config: dict, path: list[str]):
    found = config
    for part in path:
        if not isinstance(found, dict) or part not in found:
            raise KeyError(".".join(path))
        found = found[part]
    return found


def comment_text(line: str) -> str:
    return " ".join(line.strip().lstrip("#").split())


def template_comments(texts: list[str]) -> set[str]:
    """The comment lines of the template's settings files (factory.toml.tmpl, a pack's factory-append.toml), as
    `split_settings` compares them: a comment above a key the split drops goes with it only when it is one. A
    `{{placeholder}}` in one stands for whatever init wrote there."""
    return {comment_text(line) for text in texts for line in text.splitlines()
            if line.strip().startswith("#") and comment_text(line)}


PLACEHOLDER = re.compile(r"\{\{\s*\w+\s*\}\}")


def is_template_comment(line: str, comments: set[str] | frozenset) -> bool:
    text = comment_text(line)
    if text in comments:
        return True
    for comment in comments:
        if "{{" in comment:
            parts = PLACEHOLDER.split(comment)
            if len(parts) > 1 and re.fullmatch(".+?".join(re.escape(part) for part in parts), text):
                return True
    return False


def split_settings(text: str, defaults: dict, today: str, accepted: list[str],
                   comments: set[str] | frozenset = frozenset()) -> tuple[str | None, list[str], list[str]]:
    """(the new factory.toml or None when it cannot be written safely, what it dropped, CONFIRM notes). A comment
    above a dropped key goes with it only when `comments` (the template's) holds it; the project's own stays."""
    if tomllib is None:
        return None, [], ["CONFIRM factory.toml was not split: this Python has no tomllib"]
    original = tomllib.loads(text)
    lines = text.replace("\r\n", "\n").split("\n")
    found = entries(lines)
    drop: set[int] = set()
    kept_comments: set[int] = set()
    commented: list[str] = []
    dropped: list[str] = []
    notes: list[str] = []

    def drop_with_comment(start: int, span: range, name: str) -> None:
        above = comment_above(lines, start)
        ours = {i for i in above if not is_template_comment(lines[i], comments)}
        drop.update((above - ours) | set(span))
        if ours:
            kept_comments.update(ours)
            commented.append(name)
    # [[code_rules]] entries equal to the default with their id.
    default_rules = {r.get("id"): r for r in defaults.get("code_rules") or [] if isinstance(r, dict)}
    rules = [e for e in found if e["kind"] == "array" and e["path"] == ["code_rules"]]
    for index, entry in enumerate(rules):
        rule = (original.get("code_rules") or [])[index] if index < len(original.get("code_rules") or []) else None
        if isinstance(rule, dict) and default_rules.get(rule.get("id")) == rule:
            end = next((e["start"] for e in found if e["start"] > entry["start"] and e["kind"] != "key"), len(lines))
            drop_with_comment(entry["start"], range(entry["start"], end), f"[[code_rules]] {rule.get('id')}")
            dropped.append(f"[[code_rules]] {rule.get('id')}")
    for entry in found:
        if entry["kind"] != "key" or entry.get("in_array"):
            continue
        path = entry["path"]
        try:
            mine, theirs = value_at(original, path), value_at(defaults, path)
        except KeyError:
            continue
        if mine == theirs:
            drop_with_comment(entry["start"], range(entry["start"], entry["end"]), ".".join(path))
            dropped.append(".".join(path))
        elif isinstance(mine, list) and isinstance(theirs, list) and theirs and all(x in mine for x in theirs):
            extra = [x for x in mine if x not in theirs]
            notes.append(f"CONFIRM factory.toml {'.'.join(path)} holds the Book's default list and {len(extra)} more: "
                         f"`[extend.{'.'.join(path[:-1])}] {path[-1]} = [...the {len(extra)}...]` would keep the "
                         "Book's later additions coming; kept as it is")
    if commented:
        notes.append(f"CONFIRM factory.toml: a comment above {', '.join(commented)} was kept - the key equals the "
                     "Book's default and went, but the comment is not the template's; move it or delete it")
    drop |= orphaned_comments(lines, drop, dropped, comments)
    survivors = [i for i in range(len(lines)) if i not in drop]
    kept = drop_empty_tables([lines[i] for i in survivors],
                             {n for n, i in enumerate(survivors) if i in kept_comments})
    kept = add_book_keys(kept, today, accepted)
    result = "\n".join(collapse(kept)).rstrip() + "\n"
    try:
        new = tomllib.loads(result)
    except tomllib.TOMLDecodeError as error:
        return None, [], [f"CONFIRM factory.toml was not split: the result would not parse ({error}); split it by hand"]

    def view(config: dict) -> dict:
        merged = book_config.merge(defaults, {k: v for k, v in config.items() if k != book_config.EXTEND})
        if config.get(book_config.EXTEND):
            merged = book_config.extend(merged, config[book_config.EXTEND], [])
        book = dict(merged.get("book") or {})
        book.pop("type_source_from", None)
        book.pop("drift_accepted", None)
        return dict(merged, book=book)
    if view(new) != view(original):
        return None, [], ["CONFIRM factory.toml was not split: without the keys equal to the defaults it would not "
                          "give the same settings; split it by hand"]
    return result, dropped, notes


def orphaned_comments(lines: list[str], drop: set[int], dropped: list[str],
                      comments: set[str] | frozenset) -> set[int]:
    """The template's comment blocks the split leaves meaning nothing: one whose next line (past blanks) went,
    as the dotnet pack's `# [[code_rules]] - …` once its rules did, and one that names a key that went
    (`aliases` in the comment above `title`). A block with a line of the project's own always stays."""
    gone_keys = {name.split(".")[-1] for name in dropped if not name.startswith("[[")}
    kept_keys = {line.split("=", 1)[0].strip() for i, line in enumerate(lines)
                 if i not in drop and "=" in line and not line.strip().startswith("#")}
    found: set[int] = set()
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("#"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("#"):
            j += 1
        block = range(i, j)
        if comments and not any(k in drop for k in block) and all(
                is_template_comment(lines[k], comments) for k in block):
            after = next((k for k in range(j, len(lines)) if lines[k].strip()), None)
            text = " ".join(lines[k] for k in block)
            names_gone = any(f"`{key}`" in text and key not in kept_keys for key in gone_keys)
            if (after is not None and after in drop) or names_gone:
                found.update(block)
        i = j
    return found


def comment_above(lines: list[str], start: int) -> set[int]:
    """The comment lines directly above line `start` (no blank between)."""
    found = set()
    i = start - 1
    while i >= 0 and lines[i].strip().startswith("#"):
        found.add(i)
        i -= 1
    return found


def drop_empty_tables(lines: list[str], keep: set[int] | frozenset = frozenset()) -> list[str]:
    """A `[table]` header with no key and no sub-table left under it goes, with the comments right above it; so
    does one whose sub-tables all went (`[requirements]` once `[requirements.sources]` is empty). A comment in
    `keep` (line indexes) is the project's own: it stays, and so does its table."""
    headers = [i for i, line in enumerate(lines) if line.strip().startswith("[") and HEADER.match(line)]
    gone: set[int] = set()
    changed = True
    while changed:
        changed = False
        alive = [i for i in headers if i not in gone]
        for n, i in enumerate(alive):
            header = HEADER.match(lines[i])
            if header.group(1) != "[":
                continue
            j = alive[n + 1] if n + 1 < len(alive) else len(lines)
            content = [k for k in range(i + 1, j) if k not in gone and lines[k].strip()
                       and (not lines[k].strip().startswith("#") or k in keep)]
            nested = j < len(lines) and HEADER.match(lines[j]).group(2).startswith(header.group(2) + ".")
            if not content and not nested:
                gone.add(i)
                changed = True
                break
    drop: set[int] = set()
    for i in gone:
        end = next((h for h in headers if h > i), len(lines))
        drop.update(range(i, end))
        above = i - 1
        while above >= 0 and lines[above].strip().startswith("#") and above not in keep:
            drop.add(above)
            above -= 1
    return [line for i, line in enumerate(lines) if i not in drop]


def add_book_keys(lines: list[str], today: str, accepted: list[str]) -> list[str]:
    """`type_source_from` and `drift_accepted` at the end of [book] (a [book] table added when there is none)."""
    add = ["# The day this Book took the defects' Type and Source columns (check-book.py): older rows only warn.",
           f'type_source_from = "{today}"']
    if accepted:
        add += ["# Book files this project changed before 1.6, kept as they are for now: move each change to a "
                "setting or the", "# CLAUDE.md, then take its entry out (check-book warns while one is here).",
                "drift_accepted = ["]
        add += [f'    {{ file = "{rel}", reason = "changed before Book 1.6; move the change, then remove this" }},'
                for rel in accepted]
        add += ["]"]
    start = next((i for i, line in enumerate(lines) if re.match(r"^\s*\[book\]\s*(#.*)?$", line)), None)
    if start is None:
        return lines + ["", "[book]"] + add
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    existing = "\n".join(lines[start:end])
    if "type_source_from" in existing:
        add = [line for line in add if "type_source_from" not in line and "Type and Source" not in line]
    # An earlier --apply wrote it (it wrote type_source_from with it), and a person may have trimmed or removed it.
    if "drift_accepted" in existing or "type_source_from" in existing:
        add = [line for line in add if line.startswith("type_source_from") or "Type and Source" in line]
    if add and lines[end - 1].strip().startswith("#"):
        add = [""] + add  # a comment kept there (the project's own) never reads as describing the new keys
    return lines[:end] + add + lines[end:]


# ---------------------------------------------------------------- the plan the upgrade reports

def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version)[:3]) if version else ()


def applies(installed: str, new: str) -> bool:
    return bool(installed) and version_tuple(installed) < SPLIT_FROM <= version_tuple(new)


def instruction_targets(name: str, packs: list[str], stack: str) -> list[tuple[str, list[str]]]:
    """(the instruction file, the imports it takes) for this Book."""
    targets = [("CLAUDE.md", ["@.claude/book/rules.md"]), ("docs/CLAUDE.md", ["@../.claude/book/docs.md"])]
    if stack == "dotnet":
        targets += [("tests/CLAUDE.md", ["@../.claude/book/dotnet/tests.md"]),
                    ("src/CLAUDE.md", ["@../.claude/book/dotnet/src.md"]),
                    (f"src/{name}.API/CLAUDE.md", ["@../../.claude/book/dotnet/api.md"]),
                    (f"src/{name}.Persistence/CLAUDE.md", ["@../../.claude/book/dotnet/persistence.md"])]
    else:
        targets.append(("tests/CLAUDE.md", ["@../.claude/book/tests.md"]))
    for pack in ("deploy", "fe-guides"):  # the packs with a Book rules file of their own, in the manifest's order
        if pack in packs:
            targets[0] = ("CLAUDE.md", targets[0][1] + [f"@.claude/book/{pack}.md"])
    return targets


def leaf_paths(config: dict, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    found: list[tuple[str, ...]] = []
    for key, value in config.items():
        if isinstance(value, dict) and value:
            found += leaf_paths(value, prefix + (key,))
        else:
            found.append(prefix + (key,))
    return found


def held(config: dict, path: tuple[str, ...]) -> bool:
    try:
        value_at(config, list(path))
        return True
    except KeyError:
        return False


def added_defaults(original: dict, defaults: dict, installed: dict | None = None) -> list[str]:
    """CONFIRM lines naming each setting and code rule the project had removed that the Book's defaults bring
    back: one the installed release's init wrote (`installed`, its own settings) that the project's file no
    longer holds. A key new in this release, or one the old template never wrote, was never the project's to
    remove, so it is not named. Without `installed`, every default the file lacks is named."""
    keys = []
    for path in leaf_paths({k: v for k, v in defaults.items() if k not in ("code_rules", book_config.EXTEND)}):
        if path in (("book", "type_source_from"), ("book", "drift_accepted")):
            continue  # the move writes these itself, and its SPLIT line says so
        if not held(original, path) and (installed is None or held(installed, path)):
            keys.append(".".join(path))
    have = {rule.get("id") for rule in original.get("code_rules") or [] if isinstance(rule, dict)}
    wrote = None if installed is None else {rule.get("id") for rule in installed.get("code_rules") or []
                                            if isinstance(rule, dict)}
    rules = [str(rule["id"]) for rule in defaults.get("code_rules") or []
             if isinstance(rule, dict) and "id" in rule and rule["id"] not in have
             and (wrote is None or rule["id"] in wrote)]
    lines = []
    if keys:
        lines.append(f"CONFIRM factory.toml: the project had removed {len(keys)} setting(s) the Book's defaults "
                     f"now give - {', '.join(keys)}; set each in factory.toml if it should differ")
    if rules:
        lines.append(f"CONFIRM factory.toml: the project had removed {len(rules)} code rule(s) the Book's defaults "
                     f"now give - {', '.join(rules)}; to switch one off, add `[[code_rules]]` with its `id` and "
                     "`enabled = false`")
    return lines


def questions_without_state(target: Path) -> bool:
    """Whether a live question in the project's ledger has no State line yet, as check-book reads it."""
    if not (target / "docs/open-questions.md").is_file():
        return False
    checks = Path(__file__).resolve().parents[1] / "checks"
    if str(checks) not in sys.path:
        sys.path.insert(0, str(checks))  # check-book's own imports sit beside it
    spec = importlib.util.spec_from_file_location("check_book_states", checks / "check-book.py")
    check_book = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check_book)
    return any("has no **State:** line" in warning for warning in check_book.question_state_problems(target)[1])


def rules_table_stale(target: Path) -> bool:
    """Whether devtools/README.md's enforced-rules table predates the 1.6 move: once written again by
    `check-rules.py --write`, it names the rules in .claude/book/, where 1.6 keeps them."""
    readme = target / "devtools/README.md"
    if not readme.is_file():
        return False
    block = re.search(r"<!-- rules-table:start -->.*?<!-- rules-table:end -->", readme.read_text(encoding="utf-8"),
                      re.S)
    return bool(block) and ".claude/book/" not in block.group(0)


def plan(target: Path, config: dict, old_common: dict[str, str], new_files: dict[str, bytes], name: str,
         accepted: list[str], today: str | None = None, skeletons: dict[str, str] | None = None,
         comments: set[str] | None = None,
         installed_settings: dict | None = None) -> tuple[dict[str, bytes], list[str]]:
    """(the files to write, the report's lines) for the 1.6 move. `old_common` holds, by path, the text the
    installed Book wrote into each instruction file; `new_files` the new Book's files; `comments` the comment
    lines of the installed template's settings files (`template_comments`)."""
    today = today or datetime.date.today().isoformat()
    book = config.get("book") or {}
    stack = str(book.get("stack") or "none")
    packs = [str(p) for p in book.get("packs") or []]
    rules_text = "\n".join(data.decode("utf-8", "replace") for rel, data in new_files.items()
                           if rel.startswith(".claude/book/"))
    writes: dict[str, bytes] = {}
    notes: list[str] = ["The 1.6 move: the Book's common text and settings out of the project's own files"]
    for rel, imports in instruction_targets(name, packs, stack):
        path = target / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if any(line.strip().startswith("@") and ".claude/book/" in line for line in text.splitlines()):
            notes.append(f"  CURRENT  {rel} already imports the Book's rules")
            continue
        skeleton = (skeletons or {}).get(rel, "")
        # What the installed Book wrote and the new skeleton still holds (the nested-files table, say) stays.
        common = (block_texts(old_common.get(rel, "")) - block_texts(skeleton)) | block_texts(rules_text)
        title = "# This project's own rules" if rel == "CLAUDE.md" else "# This project's own rules here"
        new, moved, kept, sections = split_instructions(text, common, imports, title, skeleton)
        writes[rel] = new.encode("utf-8")
        notes.append(f"  SPLIT    {rel}: {moved} block(s) are the Book's and move into {', '.join(imports)}; "
                     f"{kept} stay the project's own" + (f", under {'; '.join(sections)}" if sections else ""))
        if kept:
            notes.append(f"  CONFIRM  {rel}: read what stays - a block that says a Book rule in other words "
                         "repeats it; delete it, or keep it if it is the project's own")
    config_path = target / CONFIG
    had: dict = {}
    if config_path.is_file():
        text = config_path.read_text(encoding="utf-8")
        reader = lambda rel: new_files[rel].decode("utf-8") if rel in new_files else None  # noqa: E731
        original = tomllib.loads(text)
        defaults = book_config.defaults(original, reader)
        new, dropped, more = split_settings(text, defaults, today, accepted, comments or frozenset())
        had = original.get("book") or {}
        if new is not None and new != text:  # a file already split is not moved again
            writes[CONFIG] = new.encode("utf-8")
            said = ([f"{len(dropped)} key(s) equal to the Book's defaults go ({', '.join(dropped[:12])}"
                     f"{' …' if len(dropped) > 12 else ''})"] if dropped else []) + (
                [] if "type_source_from" in had else [f"[book] type_source_from = {today}"]) + (
                [f"{len(accepted)} file(s) in [book] drift_accepted"] if accepted and "drift_accepted" not in had
                else [])
            notes.append(f"  SPLIT    {CONFIG}: " + ("; ".join(said) or "its layout changes (the diff below)"))
        notes += [f"  {line}" for line in more]
        if "type_source_from" not in had:  # only on the run that splits: a later one reads the split file
            notes += [f"  {line}" for line in added_defaults(original, defaults, installed_settings)]
    # Each "run this" line only while its work is still undone, so a later run says only what is left.
    if questions_without_state(target):
        notes.append("  CONFIRM  run /book:check-sources once: it proposes each open question's State "
                     "(OPEN · NOT POSTED, OPEN · POSTED, ANSWERED · THREAD ONLY) for you to approve; check-book "
                     "warns until then")
    defects = target / "docs" / "defects.md"
    if defects.is_file() and classify(defects.read_text(encoding="utf-8"))[0] is not None:
        notes.append("  CONFIRM  run `book defects classify` (then `--apply` once you have read it): it proposes "
                     "each open defect's Type and Source from its Class and Found by; check-book warns until then")
    if rules_table_stale(target):
        notes.append("  CONFIRM  run `python devtools/checks/check-rules.py --write`: the rules moved into "
                     ".claude/book/, so devtools/README.md's enforced-rules table names them there")
    # The drift list: while [book] drift_accepted names a file, or on the run that splits, which writes it.
    drifted = bool(had.get("drift_accepted") or (accepted and "type_source_from" not in had))
    if drifted:
        notes.append("  CONFIRM  each file in [book] drift_accepted: move its change to a setting or the CLAUDE.md, "
                     "or `[book] declined` if the project owns the file now, then take the entry out")
    if not (target / REGISTER_KEEP).parent.is_dir():
        writes[REGISTER_KEEP] = b""  # the register's folder, which the Book's owner table names
    # Until the audit is converted (no register file yet), whether or not the register's folder exists.
    register = target / REGISTER_KEEP.rsplit("/", 1)[0]
    if (target / "docs/Audit/spec-traceability.md").is_file() and not any(
            p for p in register.glob("*.md") if not p.name.startswith("_")):
        notes.append("  CONFIRM  run `python devtools/book.py requirements import` (then `--apply` once you have "
                     "read it): it converts docs/Audit/spec-traceability.md into the requirements register, "
                     "docs/requirements/, and archives the audit")
    if accepted and drifted:
        notes.append(f"  CONFIRM  {len(accepted)} Book file(s) the project changed are kept and accepted for now: "
                     + ", ".join(accepted[:10]) + (" …" if len(accepted) > 10 else ""))
    return writes, notes


# ---------------------------------------------------------------- book defects classify

FOUND_BY_SOURCE = {"pr review": "code-review", "review": "code-review", "spec audit": "audit",
                   "requirements check": "requirement-sync", "requirement change": "requirement-sync",
                   "test": "tests-ci", "guard/ci": "tests-ci", "while building": "developer", "qa": "qa",
                   "consumer": "fe", "client": "ba-product"}
CLASS_TYPE = {"spec-divergence": "spec-divergence", "api-contract": "spec-divergence", "input-validation": "bug",
              "settings-deploy": "config-infra", "test-gap": "test-gap", "ci-test-infra": "tooling-ci",
              "docs-drift": "bug", "concurrency": "bug", "access-control": "security",
              "soft-delete-integrity": "data", "code-convention": "bug", "audit-trail": "data",
              "error-handling": "bug", "test-flake": "test-gap", "performance": "bug", "observability": "config-infra",
              "other": "bug", "validation": "bug", "contract-gap": "spec-divergence", "build-drift": "tooling-ci",
              "logic": "bug", "code-gap": "spec-divergence", "regression": "regression", "security": "security",
              "data": "data"}
CLASS_MARK = re.compile(r"Class:\s*`?([\w-]+)`?")


# A row's cells as the table renders them: `\|` stays in its cell; a pipe in a code span splits it.
cells = book_config.table_cells


def propose(found_by: str, row_text: str) -> tuple[str, str, bool]:
    """(Type, Source, sure) for one row from its Class mark and its Found by."""
    mark = CLASS_MARK.search(row_text)
    kind = CLASS_TYPE.get(mark.group(1), "") if mark else ""
    plain = found_by.replace("*", "").strip()
    source, detail = "", plain
    for word in sorted(FOUND_BY_SOURCE, key=len, reverse=True):
        if plain.lower().startswith(word):
            source, detail = FOUND_BY_SOURCE[word], plain[len(word):].lstrip(" ,:—-") or plain
            break
    sure = bool(kind and source)
    return kind or "bug", f"{source or 'developer'}: {detail or '—'}", sure


def classify(text: str) -> tuple[str | None, list[str]]:
    """The defects ledger with Type and Source columns in its Open table (None when it has them or no table),
    and the report: one line per open row with what is proposed."""
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    report: list[str] = []
    section = ""
    column = None
    status = width = columns = 0
    changed = False
    for line in lines:
        if line.startswith("## "):
            section, column = line[3:].strip(), None
        if section == "Open" and line.lstrip().startswith("|"):
            row = cells(line)
            if "Status" in row and "Type" not in row:
                column = row.index("Found by") if "Found by" in row else None
                status = row.index("Status")
                columns = len(row)
                new = row[:status + 1] + ["Type", "Source"] + [c for i, c in enumerate(row[status + 1:], status + 1)
                                                               if i != column]
                width = len(new)
                out.append("| " + " | ".join(new) + " |")
                changed = True
                continue
            if changed and set(line.replace("|", "").strip()) <= set("-: "):
                out.append("|" + "---|" * width)
                continue
            if changed and re.match(r"^\|\s*(?:\*\*)?D\d+", line) and len(row) != columns:
                # Its cells cannot be matched to the header's, so no column is moved: left as it is.
                out.append(line)
                report.append(f"CONFIRM  {row[0].strip('* ')}: {len(row)} cells where the table has {columns} - "
                              "left as it is; give it the Type and Source columns by hand (a pipe in a cell, a "
                              "code span's too, is written `\\|`)")
                continue
            if changed and re.match(r"^\|\s*(?:\*\*)?D\d+", line):
                found_by = row[column] if column is not None and column < len(row) else ""
                kind, source, sure = propose(found_by, line)
                new = row[:status + 1] + [kind, source] + [c for i, c in enumerate(row[status + 1:], status + 1)
                                                           if i != column]
                out.append("| " + " | ".join(new) + " |")
                report.append(f"{'PROPOSED' if sure else 'CONFIRM '} {row[0].strip('* ')}: Type {kind}, Source "
                              f"{source}" + ("" if sure else " - guessed: no Class or Found by to go on"))
                continue
        out.append(line)
    return ("\n".join(out) if changed else None), report


def classify_command(target: Path, apply: bool) -> tuple[str, bool]:
    path = target / "docs" / "defects.md"
    if not path.is_file():
        return "book defects classify: no docs/defects.md\n", False
    new, report = classify(path.read_text(encoding="utf-8"))
    if new is None:
        return "book defects classify: the Open table already has Type and Source columns; nothing to do\n", True
    head = ("book defects classify - " + ("--apply: written" if apply else "report only, nothing written "
            "(--apply writes the columns once you have read this)"))
    if apply:
        path.write_text(new, encoding="utf-8", newline="\n")
    return "\n".join([head, *report, "", "Change a proposal in the file after --apply, or before by hand."]) + "\n", True
