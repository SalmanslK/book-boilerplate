"""
book verify|init|upgrade — whether this repository's Book is set up, setting one up, and upgrading it.

The Book is the documentation, requirements and AI-rules architecture every session works inside:
the instruction files, the owner documents, `requirements/`, the hooks, the guard and the checks.

    book verify [--target <repo>] [--no-self-tests]
    book init --params <book.toml> [--target <repo>]
    book upgrade --target <repo> [--apply]

**verify** is read-only. It runs the checks nothing else makes (N1-N20 of the Book manifest), one line
per element - `OK`, `MISSING`, `WRONG <why>`, `TO FILL <slot>`, `COULD NOT CHECK <why>` - then calls
the existing checks (check-book.py, check-rules.py --check, ...) rather than repeating them, and ends
with a verdict: READY, READY WITH GAPS (only TO FILL items), or NOT READY. A check that could not run
is never OK. It reads no private value: the private settings are reported by key name, set or not set,
from what factory.py already loaded in its own process, and never printed.

**init** creates only what is absent, from the template in book-template/ (named and version-stamped),
with the answers in one parameter file - it never guesses one. An existing path is KEPT, never
overwritten. Then it switches the hooks on, writes the rules table and runs verify.

**upgrade** runs from a newer boilerplate against a Book it installed. For every file the template owns
(the S files and the packs' files; never a skeleton's target, `docs/` but the plan template, or
`requirements/`) it compares three copies: the project's, the installed version's - rebuilt from the
template repository's tag `v<book_version>` with the project's names - and the new template's. It says
which are CURRENT, BEHIND (unchanged by the project, changed by the template), NEW, CHANGED by the
project, or CONFLICT (changed by both), and prints a unified diff for each it would write. It writes
nothing unless `--apply`; then it writes only BEHIND and NEW files, lists every project-changed one for a
person, and bumps `book_version` in factory.toml - or, while a CONFLICT or REMOVED file is left, keeps it and
lists those files in `[book] pending_merge` until a person merges each and takes it off the list.

Python 3.11+, standard library only.
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import book_config
import book_migrate  # noqa: E402 — beside this file: the settings merged over the Book's defaults

TEMPLATE_DIR = HERE / "book-template"
MANIFEST = TEMPLATE_DIR / "manifest.toml"
CONFIG_RELATIVE = "devtools/factory/factory.toml"

OK, MISSING, WRONG, TO_FILL, COULD_NOT, NA, CONFIRM = (
    "OK", "MISSING", "WRONG", "TO FILL", "COULD NOT CHECK", "N/A", "CONFIRM")
CREATED, KEPT = "CREATED", "KEPT"
CHECK_SECONDS = 600
SELF_TEST_SECONDS = 1200
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
DEFECT_STATUSES = ("Open", "Mitigated", "Partial", "Blocked")
# check-book.py TYPES: a 1.6 defect row's Type.
DEFECT_TYPES = ("spec-divergence", "bug", "regression", "test-gap", "security", "data", "config-infra", "tooling-ci")
QUESTION_STATUSES = ("OPEN", "ANSWERED", "SUPERSEDED")
PLAN_FIELDS = ("**Owner:**", "**FSD:**", "**Checked against:**", "**FE guide:**", "**Depends on:**")
# project.md's phase-table columns, in order, and what heads an open question's recommendation:
# the defaults for `[book] phase_columns`, `optional_phase_columns` and `recommendation_heading`.
PHASE_COLUMNS = ["Feature", "Plan", "FE guide", "Status", "Owner", "Depends on"]
OPTIONAL_PHASE_COLUMNS: list[str] = []
RECOMMENDATION = "**Recommendation:**"
# The heading that starts each kind of folder block in requirements/read-me.txt.
LIVE_SECTION, NO_LIVE_SECTION = "HAS A LIVE LINK", "NO LIVE"
PROJECT_HEADINGS = ["## What the work needs", "# Part 1 — Features and phasing", "### Phases",
                    "# Part 2 — Questions", "# Part 3 — Requirements history"]
FILL = re.compile(r"<!--\s*FILL\b[^>]*-->|<FILL-[A-Z0-9-]+>")
SNAPSHOT_NAME = re.compile(r"^.+ - V(\d+)\.[A-Za-z0-9]+$")


class BookError(Exception):
    """A refusal the tool explains: what it will not do, and why."""


@dataclass
class Line:
    status: str
    element: str
    detail: str = ""
    code: str = ""

    def render(self) -> str:
        head = f"{self.code:<4}{self.element:<46}"
        return f"{head} {self.status}" + (f" {self.detail}" if self.detail else "")


# A runner takes a command and a folder and answers (exit code, combined output). Tests replace it.
Runner = Callable[[list[str], Path, int], tuple[int, str]]


def real_runner(command: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    # verify writes nothing, not even __pycache__; and a child's output is read as UTF-8 on every machine.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)
    return done.returncode, (done.stdout + done.stderr).strip()


@dataclass
class Context:
    root: Path
    config: dict
    manifest: dict
    run: Runner = real_runner
    private: dict | None = None        # from factory.py: {"file": ..., "keys": {name: bool}}, never values
    self_tests: bool = True
    tracked_cache: list[str] | None = field(default=None, repr=False)

    def git(self, *args: str) -> tuple[int, str]:
        try:
            return self.run(["git", *args], self.root, 120)
        except (OSError, subprocess.SubprocessError) as error:
            return 128, str(error)

    def tracked(self) -> list[str]:
        """What the next commit holds: tracked files and new ones git does not ignore."""
        if self.tracked_cache is None:
            code, out = self.git("ls-files", "--cached", "--others", "--exclude-standard")
            if code != 0:
                raise BookError("git ls-files failed: this is not a git repository, or git is not answering.")
            self.tracked_cache = [p for p in out.splitlines() if p]
        return self.tracked_cache

    def text(self, rel: str) -> str | None:
        path = self.root / rel
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    def instructions(self, rel: str) -> str | None:
        """An instruction file as Claude Code reads it: its `@path` imports in place."""
        return book_config.expanded(self.root, rel) if (self.root / rel).is_file() else None


def load_manifest(template_dir: Path = TEMPLATE_DIR) -> dict:
    if tomllib is None:
        raise BookError("book needs Python 3.11 or later (tomllib).")
    path = template_dir / "manifest.toml"
    if not path.is_file():
        return {}
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def load_target_config(root: Path) -> dict:
    """The target's settings: its factory.toml over the Book defaults it holds (book_config). Its private
    settings are not merged here."""
    path = root / CONFIG_RELATIVE
    if not path.is_file() or tomllib is None:
        return {}
    try:
        return book_config.load(root)
    except book_config.ConfigError as error:
        raise BookError(f"The target's settings are unreadable: {error}.") from None


def load_target_own(root: Path) -> dict:
    """The target's own factory.toml alone, without the defaults: the names init wrote and what upgrade splits."""
    path = root / CONFIG_RELATIVE
    if not path.is_file() or tomllib is None:
        return {}
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def installed_packs(config: dict) -> list[str]:
    """The packs this Book carries: `[book] stack` (unless none) and `[book] packs`."""
    book = config.get("book") or {}
    stack = book.get("stack") or "none"
    return list(dict.fromkeys(([stack] if stack != "none" else []) + list(book.get("packs") or [])))


def phase_columns(config: dict) -> tuple[list[str], list[str]]:
    """(required, optional) phase-table columns: `[book] phase_columns` less the optional ones, in order."""
    book = config.get("book") or {}
    optional = list(book.get("optional_phase_columns") or OPTIONAL_PHASE_COLUMNS)
    columns = list(book.get("phase_columns") or PHASE_COLUMNS)
    return [c for c in columns if c not in optional], optional


def recommendation_heading(config: dict) -> str:
    """What an open question's recommendation is headed: `[book] recommendation_heading`."""
    return str((config.get("book") or {}).get("recommendation_heading") or RECOMMENDATION)


def index_sections(index: str) -> dict[str, str]:
    """The index's `=== TITLE ===` blocks, by title: LINKS, HAS A LIVE LINK, NO LIVE LINK, ..."""
    parts = re.split(r"(?m)^=== (.+?) ===[ \t]*$", index)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def index_folders(body: str) -> set[str]:
    """The folders an index block has an entry for: a line starting `<folder>/`."""
    return set(re.findall(r"^([\w.-]+)/", body, re.M))


def fsd_delivered_as_file(ctx: "Context") -> bool:
    """The index's FSD line records no live source: the FSD was delivered as a file."""
    index = ctx.text("requirements/read-me.txt") or ""
    if "=== LINKS" not in index:
        return False
    label = (ctx.config.get("requirements", {}).get("sources") or {}).get("fsd_link_label", "FSD")
    links = index.split("=== LINKS", 1)[1].split("\n===", 1)[0]
    line = next((l for l in links.splitlines() if l.startswith(label)), "")
    return bool(re.search(r":\s*none\b", line))


def section(text: str, heading: str) -> str:
    """The body under `heading` up to the next heading of the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == heading:
            body = []
            for rest in lines[index + 1:]:
                match = re.match(r"^(#{1,6}) ", rest)
                if match and len(match.group(1)) <= level:
                    break
                body.append(rest)
            return "\n".join(body)
    return ""


def heading_starting(text: str, start: str) -> str | None:
    return next((l.strip() for l in text.splitlines() if l.strip().startswith(start)), None)


def plain(text: str) -> str:
    return " ".join(re.sub(r"[*`]", "", text).split())


cells = book_config.table_cells  # one splitter for every Book table


def sample(items: list[str], limit: int = 3) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


# ---------------------------------------------------------------- N1-N20


def n1_hooks_path(ctx: Context) -> list[Line]:
    name = "git hooks switched on (core.hooksPath)"
    code, out = ctx.git("config", "--get", "core.hooksPath")
    if code not in (0, 1):
        return [Line(COULD_NOT, name, f"git config answered {code}")]
    if not out.strip():
        return [Line(MISSING, name, "run `git config core.hooksPath .githooks` once in this clone")]
    value = out.strip().rstrip("/\\")
    if value != ".githooks":
        here = (ctx.root / ".githooks").resolve()
        if Path(value).is_absolute() and Path(value).resolve() == here:
            return [Line(OK, name, "an absolute path to this checkout's .githooks")]
        return [Line(WRONG, name, "points at another folder than this checkout's .githooks - set it to the "
                     "relative `.githooks` (a worktree then runs its own hooks)")]
    return [Line(OK, name)]


def n2_settings(ctx: Context) -> list[Line]:
    import json
    name = ".claude/settings.json guard hook"
    text = ctx.text(".claude/settings.json")
    if text is None:
        return [Line(MISSING, name)]
    try:
        data = json.loads(text)
    except ValueError:
        return [Line(WRONG, name, "is not valid JSON")]
    problems = []
    attribution = data.get("attribution") or {}
    if attribution.get("commit") != "" or attribution.get("pr") != "":
        problems.append("attribution.commit and .pr must both be \"\"")
    entries = (data.get("hooks") or {}).get("PreToolUse") or []
    if len(entries) != 1:
        problems.append(f"{len(entries)} PreToolUse entries, want exactly one")
    else:
        entry = entries[0]
        tools = set((entry.get("matcher") or "").split("|"))
        missing = [t for t in ctx.manifest.get("guard_matcher", []) if t not in tools]
        if missing:
            problems.append(f"the matcher leaves out {', '.join(missing)}")
        hooks = entry.get("hooks") or []
        command = hooks[0].get("command", "") if hooks else ""
        if "devtools/factory/hooks/guard.py" not in command:
            problems.append("the hook does not run devtools/factory/hooks/guard.py")
        if '"deny"' not in command.replace('\\"', '"'):
            problems.append("no deny fallback when no Python works")
        if not hooks or not hooks[0].get("timeout"):
            problems.append("no timeout")
    return [Line(WRONG, name, "; ".join(problems))] if problems else [Line(OK, name)]


def n3_guard_mode(ctx: Context) -> list[Line]:
    import json
    name = "guard mode is enforce"
    text = ctx.text("devtools/factory/hooks/guard-config.json")
    if text is None:
        return [Line(MISSING, name, "devtools/factory/hooks/guard-config.json")]
    try:
        mode = json.loads(text).get("mode")
    except (ValueError, AttributeError):
        return [Line(WRONG, name, "guard-config.json is not valid JSON")]
    return [Line(OK, name)] if mode == "enforce" else [Line(WRONG, name, f"mode is {mode!r}")]


GITIGNORE_PROBES = [  # (probe path, must be ignored, why)
    (".claude/settings.local.json", True, "a person's own Claude Code settings"),
    (".claude/worktrees/probe", True, "agent worktrees"),
    (".factory/guard-log.jsonl", True, "the guard's local log"),
    (".env", True, "a local environment file"),
    (".env.example", False, "the committed example"),
    ("requirements/qa-artifacts/backup/probe.csv", False, "requirements backups must be tracked"),
]


def n4_gitignore(ctx: Context) -> list[Line]:
    name = ".gitignore keeps local files out"
    if ctx.text(".gitignore") is None:
        return [Line(MISSING, name)]
    problems = []
    for probe, ignored, why in GITIGNORE_PROBES:
        code, _ = ctx.git("check-ignore", "-q", "--no-index", probe)
        if code not in (0, 1):
            return [Line(COULD_NOT, name, f"git check-ignore answered {code}")]
        if (code == 0) != ignored:
            problems.append(f"{probe} {'is not' if ignored else 'is'} ignored ({why})")
    return [Line(WRONG, name, "; ".join(problems))] if problems else [Line(OK, name)]


def n5_gitattributes(ctx: Context) -> list[Line]:
    name = ".gitattributes keeps hooks and scripts LF"
    if ctx.text(".gitattributes") is None:
        return [Line(MISSING, name)]
    probes = [".githooks/pre-commit", ".githooks/commit-msg", "probe.py", "probe.sh"]
    probes += [p for p in ("docs/api/api-contracts.md",) if (ctx.root / p).is_file()]
    probes += [p for p in (ctx.config.get("guard", {}).get("generated") or {}) if p.endswith(".json")]
    code, out = ctx.git("check-attr", "eol", "--", *probes)
    if code != 0:
        return [Line(COULD_NOT, name, f"git check-attr answered {code}")]
    wrong = [line.split(":")[0] for line in out.splitlines() if not line.endswith(": eol: lf")]
    return [Line(WRONG, name, f"not eol=lf: {sample(wrong)}")] if wrong else [Line(OK, name)]


def nested_instruction_files(ctx: Context) -> list[str]:
    # A pack's CLAUDE.md files under the template are not live until `book init` copies them.
    return sorted(p for p in ctx.tracked() if p.endswith("/CLAUDE.md")
                  and not p.startswith((".claude/", "devtools/factory/book-template/")))


def n6_instruction_files(ctx: Context) -> list[Line]:
    name = "every CLAUDE.md is listed"
    root_text = ctx.text("CLAUDE.md")
    if root_text is None:
        return [Line(MISSING, "CLAUDE.md", "", "N6")]
    nested = nested_instruction_files(ctx)
    lines = []
    table = section(root_text, "## How these instructions are organised")
    if not table:
        lines.append(Line(WRONG, name, "CLAUDE.md has no \"How these instructions are organised\" section"))
    else:
        linked = {os.path.normpath(t).replace("\\", "/") for t in re.findall(r"\]\(([^)#\s]*CLAUDE\.md)\)", table)}
        unlisted = [p for p in nested if p not in linked]
        if unlisted:
            # A table still holding its slot is being filled: a pack's nested files are rows to add.
            lines.append(Line(TO_FILL if FILL.search(table) else WRONG, name,
                              f"no row in the organisation table: {sample(unlisted)}"))
        count = re.search(r"\b(" + "|".join(NUMBER_WORDS) + r") more\b", table + root_text[:4000], re.I)
        if count and NUMBER_WORDS[count.group(1).lower()] != len(nested):
            lines.append(Line(WRONG, name, f"the prose says \"{count.group(0)}\", but {len(nested)} are tracked"))
        if not lines:
            lines.append(Line(OK, name, f"{len(nested)} nested"))
    rules = ctx.text("devtools/checks/check-rules.py")
    listed = re.search(r"^INSTRUCTIONS\s*=\s*\[(.*?)\]", rules or "", re.S | re.M)
    if rules is None:
        lines.append(Line(MISSING, "check-rules.py reads every CLAUDE.md", "devtools/checks/check-rules.py"))
    elif listed:
        named = set(re.findall(r"\"([^\"]+)\"", listed.group(1)))
        want = set(nested) | {"CLAUDE.md"}
        gaps = sorted(want - named) + [f"{p} (not tracked)" for p in sorted(named - want)]
        lines.append(Line(WRONG, "check-rules.py reads every CLAUDE.md", f"INSTRUCTIONS differs: {sample(gaps)}")
                     if gaps else Line(OK, "check-rules.py reads every CLAUDE.md"))
    else:
        lines.append(Line(OK, "check-rules.py reads every CLAUDE.md", "it discovers them"))
    rules_dir = ctx.root / ".cursor" / "rules"
    if not rules_dir.is_dir():
        lines.append(Line(MISSING, "a .cursor/rules pointer per CLAUDE.md", ".cursor/rules/"))
    else:
        pointers = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted(rules_dir.glob("*.mdc")))
        uncovered = [p for p in nested if p not in pointers]
        lines.append(Line(WRONG, "a .cursor/rules pointer per CLAUDE.md", f"none for {sample(uncovered)}")
                     if uncovered else Line(OK, "a .cursor/rules pointer per CLAUDE.md"))
    agents = ctx.text("AGENTS.md")
    if agents is None:
        lines.append(Line(MISSING, "AGENTS.md names every nested folder", "AGENTS.md"))
    else:
        folders = [p[: -len("CLAUDE.md")] for p in nested]
        unnamed = [f for f in folders if not re.search(rf"(?<![\w./-]){re.escape(f)}(?![\w.-])", agents)]
        lines.append(Line(WRONG, "AGENTS.md names every nested folder", f"leaves out {sample(unnamed)}")
                     if unnamed else Line(OK, "AGENTS.md names every nested folder"))
    # Each import names a file that exists: a missing one leaves Claude Code reading the line as text.
    broken = [gap for rel in ["CLAUDE.md"] + nested for gap in book_config.missing_imports(ctx.root, rel)]
    lines.append(Line(MISSING, "every import in a CLAUDE.md resolves", sample(broken)) if broken
                 else Line(OK, "every import in a CLAUDE.md resolves"))
    for line in lines:
        line.code = "N6"
    return lines


def owner_tokens(root_text: str) -> list[str]:
    """The paths CLAUDE.md's owner tables name - check-book.py's reading of the same tables, the imports in
    place (the Book's table is in .claude/book/rules.md)."""
    if "## Where knowledge lives" not in root_text:
        return []
    table = "\n".join(part.split("\n## ", 1)[0] for part in root_text.split("## Where knowledge lives")[1:])
    tokens = []
    for line in table.splitlines():
        if line.startswith("|") and line.count("|") > 2:
            for token in re.findall(r"`([^`]+)`", line.split("|")[2]):
                if "/" in token or token.endswith(".md"):
                    tokens.append(token)
    return tokens


def n7_owners_exist(ctx: Context) -> list[Line]:
    name = "every owner in Where knowledge lives exists"
    text = ctx.instructions("CLAUDE.md")
    if text is None:
        return [Line(COULD_NOT, name, "no CLAUDE.md")]
    tokens = owner_tokens(text)
    if not tokens:
        return [Line(WRONG, name, "CLAUDE.md has no owner table under \"Where knowledge lives\"")]
    # A document the project declined (`[book] skip_skeletons`) stays in the imported owner tables; it is not required.
    skipped = skipped_skeletons(ctx.config) | book_config.declined_files(ctx.config.get("book") or {})
    missing = []
    for token in tokens:
        if token in skipped:
            continue
        if "<" in token:  # a naming pattern: its folder is what must exist
            folder = token.split("<", 1)[0].rsplit("/", 1)[0]
            if folder and not (ctx.root / folder).is_dir():
                missing.append(folder + "/")
        elif not (ctx.root / token).exists():
            missing.append(token)
    return [Line(MISSING, name, sample(missing, 6))] if missing else [Line(OK, name, f"{len(tokens)} named")]


def n8_archive(ctx: Context) -> list[Line]:
    name = "every archive row has its file"
    text = ctx.instructions("docs/CLAUDE.md")
    if text is None:
        return [Line(MISSING, name, "docs/CLAUDE.md")]
    named = sorted(set(re.findall(r"archive/[\w.-]+\.md", section(text, "## Current state, not history") or text)))
    if not named:
        return [Line(WRONG, name, "docs/CLAUDE.md names no archive file")]
    missing = [f"docs/{p}" for p in named if not (ctx.root / "docs" / p).is_file()]
    return [Line(MISSING, name, sample(missing))] if missing else [Line(OK, name, f"{len(named)} files")]


def plan_files(ctx: Context) -> list[Path]:
    folder = ctx.root / "docs" / "plans"
    return sorted(p for p in folder.glob("*.md") if p.name != "_template.md") if folder.is_dir() else []


def status_line(path: Path) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:12]
    return next((l for l in lines if l.startswith("**Status:**")), "")


def n9_project(ctx: Context) -> list[Line]:
    name = "docs/project.md shape"
    text = ctx.text("docs/project.md")
    if text is None:
        return [Line(MISSING, name)]
    if FILL.search(text) and not plan_files(ctx):
        return [Line(TO_FILL, name, "the skeleton's slots")]
    problems = []
    headings = [l.strip() for l in text.splitlines() if re.match(r"^#{1,6} ", l)]
    at = -1
    for want in PROJECT_HEADINGS:
        found = next((i for i, h in enumerate(headings) if h.startswith(want)), None)
        if found is None:
            problems.append(f"no \"{want}\"")
        elif found < at:
            problems.append(f"\"{want}\" is out of order")
        else:
            at = found
    phases = section(text, "### Phases")
    headers = [cells(l) for l in phases.splitlines() if l.startswith("| Feature")]
    if not headers:
        problems.append("no phase table under Phases")
    required, optional = phase_columns(ctx.config)
    bad = [h for h in headers if [c for c in h if c not in optional] != required]
    if bad:
        problems.append(f"{len(bad)} phase table(s) without the columns {' | '.join(required)}"
                        + (f" ({' or '.join(optional)} optional)" if optional else ""))
    unlisted = [p.name for p in plan_files(ctx) if f"plans/{p.name}" not in text]
    if unlisted:
        problems.append(f"no row for plans {sample(unlisted)}")
    return [Line(WRONG, name, "; ".join(problems))] if problems else [Line(OK, name)]


def n10_ledgers(ctx: Context) -> list[Line]:
    lines = []
    questions = ctx.text("docs/open-questions.md")
    name = "docs/open-questions.md shape"
    if questions is None:
        lines.append(Line(MISSING, name, "", "N10"))
    else:
        problems = [f"no \"{h}\"" for h in ("### Which document wins", "## Resolved")
                    if not any(l.strip() == h for l in questions.splitlines())]
        blocks = [b for b in re.split(r"(?m)^#{3,4} ", questions)[1:] if re.match(r"Q\d+\b", b)]
        statuses = [s for b in blocks for s in re.findall(r"^\*\*Status:\*\*\s*`?([A-Za-z]+)", b, re.M)]
        odd = sorted({s for s in statuses if s not in QUESTION_STATUSES})
        if odd:
            problems.append(f"a question's Status outside {' · '.join(QUESTION_STATUSES)}: {sample(odd)}")
        heading = recommendation_heading(ctx.config)
        for block in blocks:
            if re.search(r"^\*\*Status:\*\*\s*`?OPEN", block, re.M) and heading not in block:
                problems.append(f"open question without {heading.strip('*')} {block.splitlines()[0][:40]}")
        lines.append(Line(WRONG, name, "; ".join(problems), "N10") if problems else Line(OK, name, "", "N10"))
    defects = ctx.text("docs/defects.md")
    name = "docs/defects.md shape"
    if defects is None:
        lines.append(Line(MISSING, name, "", "N10"))
        return lines
    problems = [f"no \"{h}\"" for h in ("## Open", "## Fixed") if not any(l.strip() == h for l in defects.splitlines())]
    classes = set(re.findall(r"^\|\s*`([a-z-]+)`\s*\|", ctx.text("docs/CLAUDE.md") or "", re.M))
    status_at, type_at = None, None
    for line in section(defects, "## Open").splitlines():
        if not line.startswith("|"):
            status_at = None if not line.strip() else status_at
            continue
        row = cells(line)
        if "Status" in row:
            status_at = row.index("Status")
            # From 1.6 the Open table has Type and Source columns (check-book checks their values) and a row
            # says only when it was found; the older table carries "Class: <slug> · found <date>" instead.
            type_at = row.index("Type") if "Type" in row and "Source" in row else None
            continue
        if status_at is None or set(line) <= set("|-: ") or len(row) <= status_at:
            continue
        status = row[status_at].strip("` ")
        if not status.startswith(DEFECT_STATUSES) or status.startswith("Blocked") and "—" not in status:
            problems.append(f"{row[0]}: Status {status!r}")
        if type_at is not None:
            kind = row[type_at].strip("`* ") if type_at < len(row) else ""
            if not re.search(r"found \d{4}-\d{2}-\d{2}", line):
                problems.append(f"{row[0]}: no \"found <date>\"")
            if kind not in ("", "—", "-") and kind not in DEFECT_TYPES:
                problems.append(f"{row[0]}: Type {kind!r} is not one of {', '.join(DEFECT_TYPES)}")
            continue
        found = re.search(r"Class:\s*([a-z-]+)\s*·\s*found \d{4}-\d{2}-\d{2}", line)
        if not found:
            problems.append(f"{row[0]}: no \"Class: <slug> · found <date>\"")
        elif classes and found.group(1) not in classes:
            problems.append(f"{row[0]}: class {found.group(1)} is not in docs/CLAUDE.md")
    lines.append(Line(WRONG, name, "; ".join(problems[:4]), "N10") if problems else Line(OK, name, "", "N10"))
    return lines


def n11_plan_status(ctx: Context) -> list[Line]:
    name = "every plan's status line has its six fields"
    plans = plan_files(ctx)
    if not (ctx.root / "docs" / "plans" / "_template.md").is_file():
        return [Line(MISSING, name, "docs/plans/_template.md")]
    short = []
    for path in plans:
        status = status_line(path)
        if "`Removed`" in status[:30]:
            continue
        lacking = [f.strip("*:") for f in PLAN_FIELDS if f not in status]
        if not status or lacking:
            short.append(f"{path.name} ({'no status line' if not status else 'no ' + ', '.join(lacking)})")
    if short:
        return [Line(WRONG, name, f"{len(short)} of {len(plans)}: {sample(short, 2)}")]
    return [Line(OK, name, f"{len(plans)} plans")]


def n12_fe_guides(ctx: Context) -> list[Line]:
    name = "FE guide header and Deviations"
    folder = ctx.root / "docs" / "fe-integration-guides"
    if not folder.is_dir():
        if "fe-guides" not in installed_packs(ctx.config):
            return [Line(NA, name, "no fe-guides pack")]
        return [Line(MISSING, name, "docs/fe-integration-guides/")]
    problems = []
    # "Last checked against the code: 2026-11-02 (dev bac878b1)": the branch is optional, a note may follow.
    header = re.compile(r"Last checked against the code:\s*(\d{4}-\d{2}-\d{2})\s*\((?:[\w./-]+\s+)?([0-9a-f]{7,40})\b")
    # `_template.md` is the template a new guide starts from, not a guide (as in check-fe-guides.py).
    guides = sorted(p for p in folder.glob("*.md") if not p.name.startswith("_"))
    for path in guides:
        text = path.read_text(encoding="utf-8", errors="replace")
        if FILL.search(text):
            continue
        if not header.search(plain("\n".join(text.splitlines()[:20]))):
            problems.append(f"{path.name}: no \"Last checked against the code: <date> ([<branch>] <commit>)\"")
        if path.name != "api-conventions.md" and not re.search(r"^#{2,4} .*Deviations", text, re.M):
            problems.append(f"{path.name}: no Deviations section")
    filling = [p.name for p in guides if FILL.search(p.read_text(encoding="utf-8", errors="replace"))]
    if "fe-guides" in installed_packs(ctx.config):
        # What the pack puts in docs/ (the guide template, its skeletons): a Book installed before upgrade
        # offered the template lacks it (Book D44).
        skipped = skipped_skeletons(ctx.config)
        absent = [rel for rel in pack_docs(ctx.manifest, "fe-guides")
                  if not (ctx.root / rel).is_file() and rel not in skipped]
        if absent:
            return [Line(MISSING, name, ", ".join(absent))]
    if problems:
        return [Line(WRONG, name, sample(problems, 2))]
    if filling:
        return [Line(TO_FILL, name, sample(filling))]
    return [Line(OK, name, f"{len(guides)} guides")]


def n13_requirements(ctx: Context) -> list[Line]:
    lines = []
    folder = ctx.root / "requirements"
    index = ctx.text("requirements/read-me.txt")
    if index is None:
        return [Line(MISSING, "requirements/read-me.txt (the index)", "", "N13")]
    subfolders = sorted(p.name for p in folder.iterdir() if p.is_dir())
    sections = index_sections(index)
    links_title = next((s for s in sections if s.startswith("LINKS")), None)
    linked = set().union(*(index_folders(b) for s, b in sections.items() if s.startswith(LIVE_SECTION)))
    unlinked = set().union(*(index_folders(b) for s, b in sections.items() if s.startswith(NO_LIVE_SECTION)))
    unread = []
    for name in subfolders:
        text = ctx.text(f"requirements/{name}/read-me.txt")
        if text is None:
            # A source with no live link (delivered as files) needs no read-me: its index entry is its record.
            if name not in unlinked or name in linked:
                unread.append(f"{name}/ has no read-me.txt")
            continue
        first = text.lstrip().splitlines()[0] if text.strip() else ""
        checked = len(re.findall(r"^Last checked:", text, re.M))
        if first.startswith("Link:"):
            if checked != 1:
                unread.append(f"{name}/read-me.txt has {checked} \"Last checked:\" lines, want one")
        elif not first.startswith("No live link"):
            unread.append(f"{name}/read-me.txt starts with neither \"Link:\" nor \"No live link\"")
        elif name in linked:
            unread.append(f"{name}/read-me.txt does not start with \"Link:\", though the index lists it under "
                          f"{LIVE_SECTION}")
    lines.append(Line(WRONG, "every requirements folder has its read-me", sample(unread, 2), "N13") if unread
                 else Line(OK, "every requirements folder has its read-me", f"{len(subfolders)} folders", "N13"))
    if links_title is None:
        lines.append(Line(COULD_NOT, "the index's LINKS block", "no `=== LINKS` block (a structured Sources "
                          "block is not read yet)", "N13"))
        return lines
    unindexed = [n for n in subfolders if n not in linked | unlinked]
    lines.append(Line(WRONG, "the index has an entry per folder", f"none for {sample(unindexed)} under "
                      f"{LIVE_SECTION} or {NO_LIVE_SECTION} ...", "N13")
                 if unindexed else Line(OK, "the index has an entry per folder", "", "N13"))
    links = sections[links_title]
    label = (ctx.config.get("requirements", {}).get("sources") or {}).get("fsd_link_label", "FSD")
    fsd = next((l for l in links.splitlines() if l.startswith(label)), None)
    if fsd is None:
        lines.append(Line(MISSING, "the FSD link", f"no LINKS line starting with {label!r}", "N13"))
    elif fsd_delivered_as_file(ctx):
        lines.append(Line(NA, "the FSD link", "delivered as a file: no live source", "N13"))
    elif "<link>" in fsd or FILL.search(fsd):
        lines.append(Line(TO_FILL, "the FSD link", "not recorded yet", "N13"))
    elif not re.search(r"https://docs\.google\.com/document/d/", fsd):
        lines.append(Line(WRONG, "the FSD link", "is not a Google Doc link", "N13"))
    else:
        lines.append(Line(OK, "the FSD link", "", "N13"))
    placeholders = [l.split(":")[0] for l in links.splitlines() if "<link>" in l and not l.startswith(label)]
    if placeholders:
        lines.append(Line(TO_FILL, "live links not recorded", sample(placeholders, 4), "N13"))
    return lines


def n14_snapshots(ctx: Context) -> list[Line]:
    lines = []
    sources = ctx.config.get("requirements", {}).get("sources") or {}
    # The FSD always; each other folder `[requirements.sources] snapshot_dirs` names is required too.
    wanted = [sources.get("fsd_dir", "requirements/fsd"), *(sources.get("snapshot_dirs") or [])]
    for rel in dict.fromkeys(str(r).rstrip("/") for r in wanted):
        name = f"{rel}/ snapshot names"
        folder = ctx.root / rel
        if not folder.is_dir():
            lines.append(Line(MISSING, name, "", "N14"))
            continue
        files = [p.name for p in folder.iterdir() if p.is_file() and p.name not in ("read-me.txt", ".gitkeep")
                 and not p.name.startswith("comment-threads - ")]
        odd = [f for f in files if not SNAPSHOT_NAME.match(f)]
        versions = sorted(int(SNAPSHOT_NAME.match(f).group(1)) for f in files if SNAPSHOT_NAME.match(f))
        if odd:
            lines.append(Line(WRONG, name, f"not \"<Name> - V<n>.<ext>\": {sample(odd)}", "N14"))
        elif not versions:
            lines.append(Line(TO_FILL, name, "no snapshot yet", "N14"))
        else:
            lines.append(Line(OK, name, f"highest V{versions[-1]}", "N14"))
    return lines


def n15_factory_config(ctx: Context) -> list[Line]:
    name = "factory.toml paths and owners"
    if not ctx.config:
        return [Line(MISSING, name, CONFIG_RELATIVE)]
    guard = ctx.config.get("guard") or {}
    problems, fill = [], []
    exact = [p for p in guard.get("read_only_maintained", []) if not re.search(r"[*?\[]", p)]
    gone = [p for p in exact if not (ctx.root / p).is_file()]
    if gone:
        problems.append(f"read_only_maintained names files that do not exist: {sample(gone, 2)}")
    ai = ctx.config.get("ai_files") or {}
    if set(guard.get("ask", [])) != set(ai.get("paths", [])) or set(guard.get("ask_except", [])) != set(ai.get("except", [])):
        problems.append("[guard] ask and ask_except differ from [ai_files] paths and except")
    owners = [o for o in ai.get("owners", []) if isinstance(o, dict) and o.get("account_id")
              and not FILL.search(str(o.get("account_id")))]
    if not owners:
        fill.append("[ai_files] owners (each owner runs `book pr whoami`)")
    notes = (ctx.config.get("deploy") or {}).get("notes") or {}
    if notes:
        import deploy as deploy_tool  # its steps, not a copy of the list
        unknown = sorted(set(notes) - set(deploy_tool.NOTE_STEPS))
        if unknown:
            problems.append(f"[deploy.notes] {', '.join(unknown)}: no /book:deploy step has that name "
                            f"({', '.join(deploy_tool.NOTE_STEPS)}), so the note is never read")
    trace = (ctx.config.get("requirements") or {}).get("traceability")
    # Book D108: the register (one file per module) is the record from 1.6; an older Book's matrix still counts.
    register = ctx.root / "docs" / "requirements"
    recorded = register.is_dir() and any(register.glob("*.md"))
    if trace and not recorded and not (ctx.root / trace).is_file():
        fill.append(f"docs/requirements/<module>.md, or {trace} in a Book before 1.6 (written by /book:fact-check)")
    book_table = ctx.config.get("book") or {}
    unmerged = book_table.get(PENDING_MERGE) or []
    if unmerged:
        fill.append(f"[book] {PENDING_MERGE}: {len(unmerged)} template file(s) the upgrade to "
                    f"{book_table.get(PENDING_FOR) or '?'} left to merge by hand ({sample(list(map(str, unmerged)), 4)}) - "
                    "merge each, take it off the list, and run `book upgrade --apply` again")
    if problems:
        return [Line(WRONG, name, "; ".join(problems))] + ([Line(TO_FILL, name, "; ".join(fill))] if fill else [])
    return [Line(TO_FILL, name, "; ".join(fill))] if fill else [Line(OK, name)]


def frontmatter(text: str) -> dict[str, str] | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    fields: dict[str, str] = {}
    key = None
    for line in text[3:end].splitlines():
        match = re.match(r"^([\w-]+):\s*(.*)$", line)
        if match:
            key = match.group(1)
            fields[key] = match.group(2).strip().strip(">|").strip()
        elif key and line.startswith((" ", "\t")):
            fields[key] = (fields[key] + " " + line.strip()).strip()
    return fields


def n16_skills(ctx: Context) -> list[Line]:
    name = "skills have valid frontmatter"
    # The Book's skills are the plugin's: `.claude/skills/` holds only the project's own, and may be absent.
    folder = ctx.root / ".claude" / "skills"
    problems = []
    present = sorted(p.name for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []
    for skill in present:
        text = ctx.text(f".claude/skills/{skill}/SKILL.md")
        if text is None:
            problems.append(f"{skill}: no SKILL.md")
            continue
        fields = frontmatter(text)
        if fields is None:
            problems.append(f"{skill}: no frontmatter")
        elif fields.get("name") != skill:
            problems.append(f"{skill}: name is {fields.get('name')!r}")
        elif not fields.get("description"):
            problems.append(f"{skill}: no description")
        elif len(fields["description"]) > 1024:
            problems.append(f"{skill}: description over 1,024 characters")
    lines = [Line(WRONG, name, sample(problems, 2)) if problems else
             Line(OK, name, f"{len(present)} skills" if present else "none of the project's own")]
    expected = [s for s in ctx.manifest.get("skills", []) if s not in present]
    if expected:
        lines.append(Line(MISSING, "the template's skills are present", sample(expected, 4)))
    plugin = ctx.manifest.get("plugin")
    if plugin:
        settings = ctx.text(".claude/settings.json") or "{}"
        try:
            enabled = (json.loads(settings).get("enabledPlugins") or {}).get(plugin) is True
        except json.JSONDecodeError:
            enabled = False
        lines.append(Line(OK, "the book plugin is enabled", plugin) if enabled else
                     Line(MISSING, "the book plugin is enabled", f".claude/settings.json enabledPlugins {plugin}"))
        if enabled:
            lines.append(plugin_pin_line(settings, plugin, plugin_floor(ctx.manifest)))
    return lines


TAG = re.compile(r"v(\d+\.\d+\.\d+)")
COMMIT_SHA = re.compile(r"[0-9a-fA-F]{40}")


def plugin_floor(manifest: dict) -> str:
    """The manifest's `plugin_min`, "" when it has none; a malformed one stops the run, not a skipped check."""
    floor = manifest.get("plugin_min")
    if floor in (None, ""):
        return ""
    if not isinstance(floor, str) or not version_key(floor):
        raise BookError(f"The template's manifest plugin_min {floor!r} is not a semver x.y.z.")
    return floor


def plugin_ref(settings: str, plugin: str) -> tuple[bool, str]:
    """(whether .claude/settings.json has the plugin's marketplace entry, the `ref` its source pins, or "")."""
    market = plugin.partition("@")[2]
    try:
        entry = ((json.loads(settings) or {}).get("extraKnownMarketplaces") or {}).get(market)
    except (json.JSONDecodeError, AttributeError):
        entry = None
    if not isinstance(entry, dict):
        return False, ""
    source = entry.get("source")
    return True, str((source.get("ref") if isinstance(source, dict) else "") or "")


def plugin_pin_line(settings: str, plugin: str, plugin_min: str) -> Line:
    """
    The book plugin is pinned to a release (Book D39): a marketplace with no `ref`, or a branch, hands each new plugin
    release to the project with no pull request, and a release older than the template's `plugin_min` lacks what
    the template's files call.
    """
    name = "the book plugin is pinned to a release"
    present, ref = plugin_ref(settings, plugin)
    if not present:
        return Line(CONFIRM, name, f".claude/settings.json has no extraKnownMarketplaces "
                                   f"{plugin.partition('@')[2]} entry: say where the plugin comes from, pinned")
    tag = TAG.fullmatch(ref)
    if tag:
        if version_key(plugin_min) and version_key(tag.group(1)) < version_key(plugin_min):
            return Line(WRONG, name, f"{ref} is older than this template's plugin_min {plugin_min}: move `ref` to "
                                     f"v{plugin_min} or later in a pull request")
        return Line(OK, name, ref)
    if COMMIT_SHA.fullmatch(ref):
        return Line(CONFIRM, name, f"a commit, {ref[:12]}: check it holds plugin {plugin_min or 'a release'} or later")
    if re.search(r"\d|/tags/", ref):
        # A version-like name, a tag path or a short SHA pins something, just not what this check can read.
        return Line(WRONG, name, f"pinned to {ref!r}, which is not a vX.Y.Z release tag or a full commit SHA - "
                                 "pin a release tag (`\"ref\": \"vX.Y.Z\"`)")
    return Line(WRONG, name, f"unpinned ({'ref ' + repr(ref) if ref else 'no ref'}): a plugin release reaches the "
                             "project with no PR - pin it to a release tag (`\"ref\": \"vX.Y.Z\"`)")


def n17_drive_folder(ctx: Context) -> list[Line]:
    """
    One owner: `[fe_guides] drive_folder_id` in factory.toml. /book:publish-guides and its script read it from
    there (`book config fe_guides.drive_folder_id`) and repeat no folder id, so the two cannot drift. From
    v1.4.0 the skill is the book plugin's, so the fe-guides pack is known by `[book] packs`; a project copy
    still there (an older Book's, or one kept on purpose) is held to the same rule.
    """
    name = "the FE Drive folder id is set, once"
    skill = ctx.text(".claude/skills/publish-guides/SKILL.md")
    script = ctx.text(".claude/skills/publish-guides/Publish-FeGuidesToDrive.ps1")
    if skill is not None and is_rename_stub(skill.encode("utf-8")):
        skill = None  # a stub kept from v1.4.x only points at /book:publish-guides
    if "fe-guides" not in installed_packs(ctx.config) and skill is None and script is None:
        return [Line(NA, name, "no fe-guides pack")]
    folder_id = str((ctx.config.get("fe_guides") or {}).get("drive_folder_id") or "")
    if FILL.search(folder_id):
        return [Line(TO_FILL, name, "[fe_guides] drive_folder_id in factory.toml: the FE team's existing Drive "
                     "folder (never created by a tool)")]
    if not re.fullmatch(r"[\w-]+", folder_id):
        return [Line(MISSING, name, "no [fe_guides] drive_folder_id in factory.toml")]
    key = "fe_guides.drive_folder_id"
    if skill is not None and (re.search(r"drive\.google\.com/drive/folders/[\w-]+", skill) or folder_id in skill):
        return [Line(WRONG, name, f"the project's publish-guides SKILL.md repeats a folder id; it names `{key}` "
                                  "in factory.toml instead")]
    if script is not None and (re.search(r"\$FolderId\s*=\s*['\"][^'\"]+['\"]", script) or folder_id in script):
        return [Line(WRONG, name, "the project's Publish-FeGuidesToDrive.ps1 hard-codes a folder id; it reads "
                                  "factory.toml's instead")]
    missing = [rel for rel, text in (("SKILL.md", skill), ("the script", script)) if text is not None and key not in text]
    if missing:
        return [Line(WRONG, name, f"no `{key}` in the project's publish-guides {' or '.join(missing)}: it must "
                                  "read factory.toml's id")]
    return [Line(OK, name, "[fe_guides] drive_folder_id in factory.toml")]


def n18_private_settings(ctx: Context) -> list[Line]:
    """Names and set / not set only. Never a value, and never the file's contents."""
    name = "private settings (names only)"
    raw_sources = (load_target_config(ctx.root).get("requirements") or {}).get("sources") or {}
    if raw_sources.get("live_credential"):
        return [Line(WRONG, name, "factory.toml holds live_credential - a person's credential belongs in "
                     "their private settings file, never the repository")]
    if ctx.private is None:
        return [Line(COULD_NOT, name, "only that repository's own `book verify` reads its private settings")]
    if ctx.private.get("file") == "invalid":
        return [Line(WRONG, name, "the private settings file is not valid TOML")]
    if ctx.private.get("file") != "present" and fsd_delivered_as_file(ctx):
        return [Line(NA, name, "no live source needs a credential (the FSD was delivered as a file)")]
    if ctx.private.get("file") != "present":
        return [Line(TO_FILL, name, "no private settings file yet (a person writes their own; see devtools/README.md)")]
    keys = ctx.private.get("keys", {})
    if fsd_delivered_as_file(ctx):
        # No live source to reach: the file may be there for other settings (foreign_names), not a credential.
        keys = {k: v for k, v in keys.items() if not k.endswith("live_credential")}
    unset = [k for k, is_set in keys.items() if not is_set]
    lines = [Line(TO_FILL, name, f"not set: {', '.join(unset)}")] if unset else [
        Line(OK, name, ", ".join(f"{k}: set" for k in keys) or "present; no live source needs a credential")]
    variables = ctx.private.get("variables", {})
    if variables:
        lines.append(Line(OK, "deploy smoke variables (names only)",
                          ", ".join(f"{k}: {'set' if v else 'not set'}" for k, v in variables.items())))
    return lines


def n19_gitkeep(ctx: Context) -> list[Line]:
    name = "empty Book folders are kept"
    tracked = set(ctx.tracked())
    unkept = []
    folders = list(ctx.manifest.get("folders", []))
    for pack in installed_packs(ctx.config):
        folders += ((ctx.manifest.get("packs") or {}).get(pack) or {}).get("folders", [])
    for folder in folders:
        if not any(p.startswith(folder.rstrip("/") + "/") for p in tracked):
            unkept.append(folder)
    return [Line(WRONG, name, f"nothing tracked in {sample(unkept)} (add a .gitkeep)")] if unkept else [Line(OK, name)]


def name_pattern(name: str) -> str:
    """A whole name; one ending in `_` is a prefix (APP_ in APP_TEST_DB), so a word may follow it; one ending
    in `.` is a namespace (App. in App.API), so a word must follow it - a sentence's last word is not one."""
    if name.endswith("."):
        return rf"(?<![\w.]){re.escape(name)}(?=\w)"
    return rf"(?<![\w.]){re.escape(name)}" + ("" if name.endswith("_") else r"(?![\w])")


def n20_names(ctx: Context) -> list[Line]:
    source = ctx.manifest.get("source") or {}
    own_table = ctx.config.get("book") or {}
    # `aliases`: other names that are this project's own (a client's platform name, say).
    own = ({own_table.get(k) for k in ("name", "title", "slug", "env_prefix")}
           | set(own_table.get("aliases") or [])) - {None, ""}
    # A name's namespace or prefix form (`Acme.`, `ACME_`) is the project's own when the name is.
    foreign = [n for n in source.get("foreign_names", []) if n not in own and n.rstrip("._") not in own]
    # A person's private list (factory.py merges it from their private settings): never exempt anywhere.
    private = [n for n in own_table.get("private_foreign_names") or [] if n not in own]
    exempt = source.get("names_exempt", [])
    book = ("CLAUDE.md", "AGENTS.md", "AI-DEVELOPMENT-GUIDE.md", "README.md", "docs/", ".claude/", ".cursor/",
            "devtools/", ".githooks/", "requirements/read-me.txt")
    paths = [p for p in ctx.tracked() if (p.startswith(book) or p.endswith("/CLAUDE.md"))
             and not p.startswith("docs/archive/")]
    leftover, slots = [], []
    for rel in paths:
        text = ctx.text(rel)
        if text is None or chr(0) in text[:1000]:
            continue
        exempted = any(fnmatch.fnmatch(rel, e) for e in exempt)
        hits = [n for n in ([] if exempted else foreign) + private if re.search(name_pattern(n), text)]
        if hits:
            leftover.append(f"{rel} ({', '.join(hits)})")
        if not exempted and FILL.search(re.sub(r"`[^`\n]*`", "", text)):  # a slot in a code span is prose about slots
            slots.append(rel)
    if not own:
        lines = [Line(COULD_NOT, "no other project's names left", "factory.toml has no [book] table naming this project")]
    else:
        lines = [Line(WRONG, "no other project's names left", f"{len(leftover)} files: {sample(leftover, 3)}")
                 if leftover else Line(OK, "no other project's names left")]
    if slots:
        lines.append(Line(TO_FILL, "template slots to fill", f"{len(slots)} files: {sample(slots, 4)}"))
    for line in lines:
        line.code = "N20"
    return lines


def n22_book_files(ctx: Context) -> list[Line]:
    """The Book's own files as the Book wrote them (book-files.lock): check-book's drift check, as verify sees it."""
    name = "the Book's files are as the Book wrote them"
    if not (ctx.root / book_config.LOCK).is_file():
        return [Line(NA, name, f"no {book_config.LOCK} (a Book before 1.6: `book upgrade --apply` writes it)", "N22")]
    problems, warnings = book_config.drift(ctx.root, ctx.config)
    lines = [Line(WRONG, name, sample([p.split(":", 1)[0] for p in problems]), "N22")] if problems else []
    lines += [Line(CONFIRM, "a Book file kept changed, for now", w, "N22") for w in warnings]
    return lines or [Line(OK, name, "", "N22")]


N_CHECKS: list[tuple[str, Callable[[Context], list[Line]]]] = [
    ("N1", n1_hooks_path), ("N2", n2_settings), ("N3", n3_guard_mode), ("N4", n4_gitignore),
    ("N5", n5_gitattributes), ("N6", n6_instruction_files), ("N7", n7_owners_exist), ("N8", n8_archive),
    ("N9", n9_project), ("N10", n10_ledgers), ("N11", n11_plan_status), ("N12", n12_fe_guides),
    ("N13", n13_requirements), ("N14", n14_snapshots), ("N15", n15_factory_config), ("N16", n16_skills),
    ("N17", n17_drive_folder), ("N18", n18_private_settings), ("N19", n19_gitkeep), ("N20", n20_names),
    ("N22", n22_book_files),
]

# ---------------------------------------------------------------- the existing checks, called


def existing_checks(ctx: Context) -> list[tuple[str, list[str], str | None]]:
    """(label, script and arguments, why it does not apply) - each run as the repository runs it."""
    base = ctx.config.get("base_ref", "origin/dev")
    has = lambda rel: (ctx.root / rel).is_file()  # noqa: E731
    # A generator applies once there is something to generate from; a new Book has no code yet.
    endpoints = any(p.is_dir() for p in ctx.root.glob("src/*.API/Endpoints"))
    snapshot = any(ctx.root.glob("src/*.Persistence/Migrations/*ModelSnapshot.cs"))
    contracts = has("docs/api/api-contracts.md")
    checks = [
        ("check-book.py", ["devtools/checks/check-book.py"], None),
        ("check-rules.py --check", ["devtools/checks/check-rules.py", "--check"], None),
        ("gen-task-rollup.py --check", ["devtools/checks/gen-task-rollup.py", "--check"], None),
        ("check-private-data.py --tracked", ["devtools/checks/check-private-data.py", "--tracked"], None),
        ("check-pipeline.py", ["devtools/checks/check-pipeline.py"],
         None if has("bitbucket-pipelines.yml") else "no bitbucket-pipelines.yml"),
        ("gen-api-contracts.py --check", ["devtools/checks/gen-api-contracts.py", "--check"],
         None if endpoints else "no src/*.API/Endpoints to generate from yet"),
        ("gen-database-erd.py --check", ["devtools/checks/gen-database-erd.py", "--check"],
         None if snapshot else "no EF model snapshot to generate from yet"),
        ("check-fe-guides.py", ["devtools/checks/check-fe-guides.py", "--base", base],
         None if contracts else "no docs/api/api-contracts.md yet"),
        ("check-protected-paths.py", ["devtools/checks/check-protected-paths.py", "--base", base], None),
    ]
    return checks


def run_script(ctx: Context, label: str, command: list[str], timeout: int) -> Line:
    script = ctx.root / command[0]
    if not script.is_file():
        return Line(MISSING, label, command[0], "E")
    try:
        code, out = ctx.run([sys.executable, *command], ctx.root, timeout)
    except subprocess.TimeoutExpired:
        return Line(COULD_NOT, label, f"did not finish in {timeout} s", "E")
    except (OSError, subprocess.SubprocessError) as error:
        return Line(COULD_NOT, label, f"could not start: {error.__class__.__name__}", "E")
    if code == 0:
        return Line(OK, label, "", "E")
    last = [l for l in out.splitlines() if l.strip()]
    return Line(WRONG, label, f"exit {code}: {last[-1][:160] if last else 'no output'}", "E")


def run_existing(ctx: Context) -> list[Line]:
    lines = []
    base = ctx.config.get("base_ref", "origin/dev")
    fetched = ctx.git("rev-parse", "--verify", "-q", base)[0] == 0
    jobs = []
    for label, command, not_applicable in existing_checks(ctx):
        if not_applicable:
            lines.append(Line(NA, label, not_applicable, "E"))
        elif "--base" in command and not fetched:
            lines.append(Line(COULD_NOT, label, f"{base} is not fetched - run `git fetch origin`", "E"))
        else:
            jobs.append((label, command, CHECK_SECONDS))
    if ctx.self_tests:
        folders = (ctx.config.get("commands") or {}).get("self_tests") or [
            "devtools/factory", "devtools/factory/hooks", "devtools/checks"]
        for folder in folders:
            jobs.append((f"run-self-tests.py {folder}", ["devtools/checks/run-self-tests.py", folder], SELF_TEST_SECONDS))
    else:
        lines.append(Line(COULD_NOT, "self-tests", "skipped by --no-self-tests; not passed", "E"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        lines += list(pool.map(lambda job: run_script(ctx, *job), jobs))
    return lines


# ---------------------------------------------------------------- verify


def verdict(lines: list[Line]) -> tuple[str, bool]:
    counts = {s: sum(l.status == s for l in lines) for s in (MISSING, WRONG, COULD_NOT, TO_FILL)}
    parts = [f"{n} {s.lower()}" for s, n in counts.items() if n]
    if counts[MISSING] or counts[WRONG] or counts[COULD_NOT]:
        note = " (a check that could not run does not pass)" if counts[COULD_NOT] else ""
        return f"NOT READY - {', '.join(parts)}{note}", False
    if counts[TO_FILL]:
        return f"READY WITH GAPS - {', '.join(parts)}", True
    return "READY", True


def verify(ctx: Context) -> tuple[str, bool]:
    lines: list[Line] = []
    for code, check in N_CHECKS:
        try:
            found = check(ctx)
        except BookError:
            raise
        except Exception as error:  # a check that broke did not pass
            found = [Line(COULD_NOT, check.__name__, f"the check failed: {error.__class__.__name__}: {error}")]
        for line in found:
            line.code = line.code or code
        lines += found
    lines += run_existing(ctx)
    lines.append(Line(CONFIRM, "AI-file owners are default reviewers on the base", "a person checks the "
                      "repository settings in Bitbucket; no tool reads them", "N21"))
    version = (ctx.manifest.get("version") or "none")
    target_version = (load_manifest(ctx.root / "devtools/factory/book-template").get("version")
                      if (ctx.root / "devtools/factory/book-template/manifest.toml").is_file() else None)
    head = f"book verify - template {target_version or 'not stamped'}"
    if target_version and target_version != version:
        head += f" (this kit is {version})"
    text, ok = verdict(lines)
    return "\n".join([head, *(l.render() for l in lines), "", f"Verdict: {text}"]), ok


# ---------------------------------------------------------------- init

REQUIRED_PARAMS = ["name", "title", "slug", "env_prefix", "stack", "mode", "base_branch", "protected_branches",
                   "git_host", "ci_live", "jira_site", "jira_key", "fsd_label", "fsd_link",
                   "fe_drive_folder_id", "owners"]
STACKS = ("dotnet", "pytest", "none")
PACKS_DIR = "devtools/factory/book-template/packs"
APPEND = "factory-append.toml"
SKELETONS_DIR = "devtools/factory/book-template/skeletons"
# What running a pack's self-tests in place leaves behind: never a template file.
CACHES = {"__pycache__", ".pytest_cache"}
# A pack's own [review_scope] and factory.toml additions before v1.6, which a template from before then still
# holds (an installed version rebuilt from its tag): never a file a Book carries. From v1.6 both are the
# defaults files, book-defaults/<stack>.toml.
REVIEW_SCOPE = "review-scope.toml"


def load_params(path: Path) -> dict:
    if tomllib is None:
        raise BookError("book needs Python 3.11 or later (tomllib).")
    if not path.is_file():
        raise BookError(f"No parameter file at {path}. The book:setup skill writes it from the person's answers; "
                        "init never guesses one.")
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    params = data.get("book", data)
    missing = [k for k in REQUIRED_PARAMS if k not in params]
    if missing:
        raise BookError(f"The parameter file has no answer for: {', '.join(missing)}. Ask the person; "
                        "an empty string means \"not yet\", and leaves a slot to fill.")
    params.setdefault("packs", [])
    owners = params["owners"]
    if not isinstance(owners, list) or not all(
            isinstance(o, dict) and set(o) == {"name", "account_id"} and all(isinstance(v, str) for v in o.values())
            for o in owners):
        raise BookError('owners is a list of tables, one per owner: owners = [{ name = "A Person", account_id = '
                        '"the Git host account id" }] (`book pr whoami` prints the id), or [] for none yet.')
    unknown = [p for p in params["packs"] if p not in ("deploy", "bitbucket", "fe-guides")]
    if unknown:
        raise BookError(f"packs {unknown} are not in this template (deploy, bitbucket, fe-guides).")
    if params["stack"] not in STACKS:
        raise BookError(f"stack is {params['stack']!r}; this template supports {', '.join(STACKS)}.")
    if params["mode"] not in ("greenfield", "brownfield"):
        raise BookError("mode is greenfield or brownfield.")
    if not re.fullmatch(r"[A-Z][A-Za-z0-9]*(?:\.[A-Z][A-Za-z0-9]*)*", params["name"]) \
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .-]*", params["title"]) \
            or not re.fullmatch(r"[a-z0-9-]+", params["slug"]) or not re.fullmatch(r"[A-Z0-9_]+_", params["env_prefix"]):
        raise BookError("name is PascalCase, dotted parts allowed (Acme.Shop); title letters, digits, spaces, "
                        "dots and hyphens (Acme Shop); slug lower-case; env_prefix upper-case ending in _.")
    return params


def substitutions(manifest: dict, params: dict) -> list[tuple[str, str]]:
    """Source name -> target name, longest first, so APP_PRO_ is replaced before AppPro."""
    source = manifest.get("source") or {}
    pairs = [(source.get(key, ""), params[key]) for key in ("env_prefix", "name", "title", "slug")]
    folder_id = source.get("fe_drive_folder_id")
    if folder_id:
        pairs.append((folder_id, params["fe_drive_folder_id"] or "<FILL-FE-DRIVE-FOLDER-ID>"))
    return sorted([(a, b) for a, b in pairs if a and a != b], key=lambda pair: -len(pair[0]))


# The nested CLAUDE.md files every Book has (docs/, tests/): the root CLAUDE.md's table starts with them.
CORE_INSTRUCTIONS = 2


def pack_rows(manifest: dict, packs: list[str], pairs: list[tuple[str, str]]) -> dict[str, str]:
    """The root CLAUDE.md's rows for what the packs installed: their nested CLAUDE.md files and their owner
    documents (each pack's `instructions` and `owners` in the manifest), with the project's names."""
    settings = [((manifest.get("packs") or {}).get(pack) or {}) for pack in packs]
    instructions = [row for pack in settings for row in pack.get("instructions", [])]
    owners = [row for pack in settings for row in pack.get("owners", [])]
    imports = [path for pack in settings for path in pack.get("imports", [])]
    count = CORE_INSTRUCTIONS + len(instructions)
    words = {n: w for w, n in NUMBER_WORDS.items()}
    return {
        "pack_instruction_rows": "".join(
            renamed(f"| `{row['file'][:-len('CLAUDE.md')]}` | [`{row['file']}`]({row['file']}) — {row['says']} |\n", pairs)
            for row in instructions),
        "pack_owner_rows": "".join(f"| {row['topic']} | {row['owner']} |\n" for row in owners),
        # A pack's Book rules files: the root CLAUDE.md imports each after the Book's own rules.
        "pack_imports": "".join(f"@{path}\n" for path in imports),
        "instruction_count": words.get(count, str(count)).capitalize(),
    }


def fill_skeleton(text: str, params: dict) -> str:
    owners = params.get("owners") or []
    owners_toml = ",\n".join(f'    {{ name = "{o.get("name", "")}", account_id = "{o.get("account_id", "")}" }}'
                             for o in owners) or '    # <!-- FILL: { name = "...", account_id = "..." } per owner -->'
    values = {
        "name": params["name"], "title": params["title"], "slug": params["slug"], "env_prefix": params["env_prefix"],
        "base_branch": params["base_branch"], "base_ref": f"origin/{params['base_branch']}",
        "stack": params["stack"], "packs": ", ".join(json.dumps(p) for p in params.get("packs") or []),
        "protected_branches": ", ".join(f'"{b}"' for b in params["protected_branches"]),
        "protected_list": ", ".join(f"`{b}`" for b in params["protected_branches"]),
        "jira_site": params["jira_site"] or "<!-- FILL: Jira site, or remove [jira] -->",
        "jira_key": params["jira_key"] or "<!-- FILL: Jira project key -->",
        "fsd_label": params["fsd_label"] or "FSD",
        "fsd_link": params["fsd_link"] or "<link>",
        "local_ci_required": "false" if params["ci_live"] else "true",
        "fe_drive_folder_id": params["fe_drive_folder_id"] or "<FILL-FE-DRIVE-FOLDER-ID>",
        "owners_toml": owners_toml,
        "book_version": str(params.get("book_version") or "<!-- FILL: the template version -->"),
        "pack_instruction_rows": params.get("pack_instruction_rows") or "",
        "pack_owner_rows": params.get("pack_owner_rows") or "",
        "pack_imports": params.get("pack_imports") or "",
        "instruction_count": params.get("instruction_count") or "Two",
    }
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), text)


def renamed(rel: str, pairs: list[tuple[str, str]]) -> str:
    for old, new in pairs:
        rel = rel.replace(old, new)
    return rel


def expand(template_root: Path, patterns: list[str]) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        if any(c in pattern for c in "*?["):
            found += sorted(p.relative_to(template_root).as_posix() for p in template_root.glob(pattern)
                            if p.is_file() and not CACHES & set(p.parts))
        else:
            found.append(pattern)
    return list(dict.fromkeys(found))


def template_sources(template_root: Path, manifest: dict, packs: list[str]) -> list[tuple[str, Path]]:
    """The S files a Book takes from the template: (path in the template, source file), packs included. A
    pack's file wins over a core file at the same path, so init and upgrade agree on which one a Book has."""
    copied = expand(template_root, list(manifest.get("copy", {}).get("book", [])))
    pack_sources = []
    for folder in [template_root / PACKS_DIR / pack for pack in packs]:
        pack_sources += [(p.relative_to(folder).as_posix(), p) for p in sorted(folder.rglob("*"))
                         if p.is_file() and p.name not in (APPEND, REVIEW_SCOPE) and not CACHES & set(p.parts)]
    # A pack's file wins over a copied one at the same path (a later pack's over an earlier one's).
    layered = dict(pack_sources)
    return [(rel, template_root / rel) for rel in copied if rel not in layered] + list(layered.items())


def verbatim(rel: str, source: Path, template_root: Path, manifest: dict) -> bool:
    """A core file under names_exempt (the template itself, the renamer): copied as it is, path and content."""
    exempt = (manifest.get("source") or {}).get("names_exempt", [])
    return source == template_root / rel and any(fnmatch.fnmatch(rel, pattern) for pattern in exempt)


def target_path(rel: str, source: Path, template_root: Path, manifest: dict, pairs: list[tuple[str, str]]) -> str:
    """Where an S file lands in the Book: renamed like its content, so a verbatim file keeps its path."""
    return rel if verbatim(rel, source, template_root, manifest) else renamed(rel, pairs)


def rendered(rel: str, source: Path, template_root: Path, manifest: dict, pairs: list[tuple[str, str]]) -> bytes:
    """What init writes for one S file: its bytes with the source names replaced (names_exempt as they are)."""
    data = source.read_bytes()
    if verbatim(rel, source, template_root, manifest) or b"\0" in data[:4000]:
        return data
    text = data.decode("utf-8")
    for old, new in pairs:
        text = text.replace(old, new)
    return text.encode("utf-8")


def skeleton_map(manifest: dict, packs: list[str]) -> dict[str, str]:
    """T files: target path -> skeleton, the packs' included. Once written, each is the project's own."""
    skeletons = dict(manifest.get("skeletons", {}).get("book", {}))
    for pack in packs:
        skeletons.update(((manifest.get("packs") or {}).get(pack) or {}).get("skeletons", {}))
    return skeletons


def init(target: Path, params: dict, manifest: dict, template_root: Path, run: Runner = real_runner) -> tuple[str, bool]:
    if not (target / ".git").exists():
        raise BookError(f"{target} is not a git repository: run `git init` (or clone it) first.")
    pairs = substitutions(manifest, params)
    lines: list[Line] = []
    packs = installed_packs({"book": params})
    for rel, source in template_sources(template_root, manifest, packs):
        dest_rel = target_path(rel, source, template_root, manifest, pairs)
        dest = target / dest_rel
        if dest.exists():
            lines.append(Line(KEPT, dest_rel))
            continue
        if not source.is_file():
            lines.append(Line(MISSING, dest_rel, f"the template has no {rel}"))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(rendered(rel, source, template_root, manifest, pairs))
        shutil.copymode(source, dest)
        lines.append(Line(CREATED, dest_rel))
    skeletons = skeleton_map(manifest, packs)
    created = set()
    # factory.toml's book_version: the template version this Book came from (`book upgrade` reads it).
    values = dict(params, book_version=str(manifest.get("version") or ""),
                  **pack_rows(manifest, packs, pairs))
    for dest_rel, skeleton in skeletons.items():
        dest_rel = renamed(dest_rel, pairs)
        dest = target / dest_rel
        if dest.exists():
            lines.append(Line(KEPT, dest_rel))
            continue
        text = (template_root / SKELETONS_DIR / skeleton).read_text(encoding="utf-8")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(fill_skeleton(text, values), encoding="utf-8", newline="\n")
        created.add(dest_rel)
        lines.append(Line(CREATED, dest_rel, "skeleton"))
    if CONFIG_RELATIVE in created:
        for pack in packs:
            append = template_root / PACKS_DIR / pack / APPEND
            if append.is_file():
                with open(target / CONFIG_RELATIVE, "a", encoding="utf-8", newline="\n") as handle:
                    handle.write(fill_skeleton(append.read_text(encoding="utf-8"), values))
                lines.append(Line(OK, f"{CONFIG_RELATIVE}: the {pack} pack's additions"))
    folders = list(manifest.get("folders", []))
    for pack in packs:
        folders += ((manifest.get("packs") or {}).get(pack) or {}).get("folders", [])
    for folder in folders:
        path = target / folder
        if path.is_dir() and any(path.iterdir()):
            continue
        path.mkdir(parents=True, exist_ok=True)
        (path / ".gitkeep").write_text("", encoding="utf-8")
        lines.append(Line(CREATED, f"{folder.rstrip('/')}/.gitkeep"))
    # What the Book wrote, so check-book can tell when a project changes a file the next upgrade replaces.
    if write_lock(target, owned_files(template_root, manifest, params, packs)):
        lines.append(Line(CREATED, book_config.LOCK))
    made_paths = [l.element for l in lines if l.status == CREATED]  # a created line names only its path
    if made_paths:
        code, out = run(["git", "add", "--", *made_paths], target, 120)
        if code == 0:
            # Every created path in the index, or the checks that read tracked files see a partial Book.
            _, listed = run(["git", "ls-files", "--", *made_paths], target, 120)
            unstaged = sorted(set(made_paths) - set(listed.splitlines()))
            if unstaged:
                code, out = 1, f"{len(unstaged)} not staged: {', '.join(unstaged[:3])}"
        lines.append(Line(OK if code == 0 else WRONG, f"git add (the {len(made_paths)} created paths)",
                          "" if code == 0 else (out.splitlines() or [""])[-1][:160]))
        # The template's executable files, the packs' included (a pack's hook or script as much as a core one).
        executable = executable_in_template(template_root, manifest, params, packs, run)
        executable = [p for p in made_paths if p in executable or p.endswith(".sh")]
        if code == 0 and executable:
            done, _ = run(["git", "update-index", "--chmod=+x", "--", *executable], target, 120)
            lines.append(Line(OK if done == 0 else WRONG, f"executable bit on {len(executable)} files"))
    # Switch the hooks on (greenfield). Brownfield turns them on last, after the clean-up (the skill says when).
    if params["mode"] == "greenfield":
        code, _ = run(["git", "config", "core.hooksPath", ".githooks"], target, 60)
        lines.append(Line(OK if code == 0 else WRONG, "git config core.hooksPath .githooks", "" if code == 0 else f"exit {code}"))
    else:
        lines.append(Line(TO_FILL, "git config core.hooksPath .githooks", "brownfield: run it as the last stage"))
    for label, command, when in (("gen-task-rollup.py", ["devtools/checks/gen-task-rollup.py"], "docs/project.md" in created),
                                 ("check-rules.py --write", ["devtools/checks/check-rules.py", "--write"], True)):
        if not when or not (target / command[0]).is_file():
            continue
        code, out = run([sys.executable, *command], target, CHECK_SECONDS)
        lines.append(Line(OK if code == 0 else WRONG, label, "" if code == 0 else (out.splitlines() or [""])[-1][:160]))
    if made_paths:
        # The generators rewrite created files (docs/project.md, devtools/README.md) after they were staged.
        code, out = run(["git", "add", "--", *made_paths], target, 120)
        lines.append(Line(OK if code == 0 else WRONG, "git add (what the generators wrote)",
                          "" if code == 0 else (out.splitlines() or [""])[-1][:160]))
    made = sum(l.status == CREATED for l in lines)
    kept = sum(l.status == KEPT for l in lines)
    head = f"book init - template {manifest.get('version', 'none')} - {made} created, {kept} kept"
    return "\n".join([head, *(f"{l.status:<8} {l.element}" + (f" - {l.detail}" if l.detail else "") for l in lines)]), True


# ---------------------------------------------------------------- upgrade

# What upgrade says of each file the template owns, comparing three copies: the project's, the installed
# template version's (rebuilt from the template repository's tag v<book_version>) and the new template's.
CURRENT = "CURRENT"      # the project's copy is the new template's: nothing to do
BEHIND = "BEHIND"        # unchanged from the installed version; the new template changes it: --apply updates
NEW = "NEW"              # the new template adds it; the project has none: --apply creates
CHANGED = "CHANGED"      # changed by the project; the template did not change it: kept
CONFLICT = "CONFLICT"    # changed by the project and by the new template: kept, for a person
REMOVED = "REMOVED"      # the project deleted a file the installed version had: left absent, for a person
DROPPED = "DROPPED"      # the installed version had it, the new template does not: kept, for a person
RETIRED = "RETIRED"      # a DROPPED file of the Book's own template copy, unchanged and read by nothing: --apply removes
TEMPLATE_COPY = "devtools/factory/book-template/"  # book_config.LOCK_EXEMPT: the Book's source, never run here
SPLIT = "SPLIT"          # a nested CLAUDE.md the Book owned before 1.6: the 1.6 move keeps it, importing the rules
UNKNOWN = "UNKNOWN"      # differs from the new template, and no installed version to compare: kept
DECLINED = "DECLINED"    # the project's `[book] declined` names it: never written, nothing for a person
RESOLVED = "RESOLVED"    # a CONFLICT or REMOVED a person settled, by taking it out of `[book] pending_merge`: kept
FOR_A_PERSON = (CONFLICT, REMOVED, DROPPED, UNKNOWN)
# What --apply leaves to merge by hand (Book D56): while any CONFLICT or REMOVED file remains, book_version stays
# at the installed version, so the next report still shows the template's change; the files are listed here.
PENDING_MERGE, PENDING_FOR = "pending_merge", "pending_merge_for"
# A project's own content, never touched by upgrade even where a template pattern would reach it.
PROJECT_CONTENT = ("requirements/*", "docs/*")
# The template files under docs/ that the template owns, as it owns any S file: the core plan template and the
# fe-guides pack's guide template (Book D44), so upgrade offers each NEW or BEHIND like any other.
TEMPLATE_DOCS = ("docs/plans/_template.md", "docs/fe-integration-guides/_template.md")
# The template version that first owned each: an older installed version shipped it as the project's own (or
# not at all), so its absence is NEW, never REMOVED.
TEMPLATE_DOCS_SINCE = {"docs/fe-integration-guides/_template.md": "1.5.1"}


def owned_then(files: dict[str, bytes], version: str) -> dict[str, bytes]:
    """What the installed version owned: its files, less the template docs it did not own yet."""
    return {rel: data for rel, data in files.items()
            if not (rel in TEMPLATE_DOCS_SINCE and version_key(version) < version_key(TEMPLATE_DOCS_SINCE[rel]))}


def skipped_skeletons(config: dict) -> set[str]:
    """`[book] skip_skeletons`: a pack's optional documents this project has chosen not to keep (a Book with
    the fe-guides pack and no UI standard of its own: `["docs/ui-standard.md"]`). Neither verify nor the upgrade
    asks for one."""
    return {str(rel) for rel in (config.get("book") or {}).get("skip_skeletons") or []}


def pack_docs(manifest: dict, pack: str) -> list[str]:
    """What a pack puts in docs/ that verify expects: its template docs and its skeletons' targets."""
    prefix = f"{PACKS_DIR}/{pack}/"
    skeletons = ((manifest.get("packs") or {}).get(pack) or {}).get("skeletons", {})
    owned = [rel for rel in TEMPLATE_DOCS if (HERE.parents[1] / (prefix + rel)).is_file()]
    return owned + sorted(rel for rel in skeletons if rel.startswith("docs/"))


def version_key(version: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version or "")
    return tuple(int(part) for part in match.groups()) if match else None


def installed_version(target: Path, config: dict) -> tuple[str | None, str]:
    """The template version the target came from, and where it was read: factory.toml's book_version,
    or - for a Book installed before init wrote it - its copy of the template's manifest."""
    stamp = str(config.get("book_version") or "")
    if version_key(stamp):
        return stamp, f"{CONFIG_RELATIVE} book_version"
    copied = target / "devtools/factory/book-template/manifest.toml"
    if copied.is_file():
        version = str(load_manifest(copied.parent).get("version") or "")
        if version_key(version):
            return version, "devtools/factory/book-template/manifest.toml (no book_version in factory.toml)"
    return None, "no book_version in factory.toml and no copied manifest"


def target_params(config: dict) -> dict:
    """The names init replaced in the target, read back from its factory.toml."""
    book = config.get("book") or {}
    missing = [key for key in ("name", "slug", "env_prefix") if not book.get(key)]
    if missing:
        raise BookError(f"The target's {CONFIG_RELATIVE} has no [book] {', '.join(missing)}: upgrade needs the "
                        "names init wrote, to rebuild the template's files with them.")
    return {"name": book["name"], "title": book.get("title") or book["name"], "slug": book["slug"],
            "env_prefix": book["env_prefix"],
            "fe_drive_folder_id": str((config.get("fe_guides") or {}).get("drive_folder_id") or "")}


def owned_files(template_root: Path, manifest: dict, params: dict, packs: list[str]) -> dict[str, bytes]:
    """Every file the template owns in a Book, by its path there, with the content init would write. The
    skeletons (T files), requirements/ and docs/ (but the plan template) are the project's, and left out."""
    pairs = substitutions(manifest, params)
    skeletons = {renamed(rel, pairs) for rel in skeleton_map(manifest, packs)}
    files: dict[str, bytes] = {}
    for rel, source in template_sources(template_root, manifest, packs):
        dest_rel = target_path(rel, source, template_root, manifest, pairs)
        if dest_rel in skeletons or not source.is_file() or (
                any(fnmatch.fnmatch(dest_rel, p) for p in PROJECT_CONTENT) and dest_rel not in TEMPLATE_DOCS):
            continue
        files[dest_rel] = rendered(rel, source, template_root, manifest, pairs)
    return files


def installed_tree(template_root: Path, version: str, scratch: Path, run: Runner) -> tuple[Path | None, str]:
    """
    The template repository at tag v<version>, unpacked into `scratch`; None and why when it cannot be. Read as
    refs/tags/v<version>, never the bare name: a branch of that name would win, and a branch can move (Book D58).
    """
    tag = f"v{version}"
    ref = f"refs/tags/{tag}"
    code, _ = run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], template_root, 60)
    if code != 0:
        return None, f"the template repository has no tag {tag}"
    archive = scratch / "installed.tar"
    code, out = run(["git", "archive", "--format=tar", "-o", str(archive), ref], template_root, 300)
    if code != 0 or not archive.is_file():
        return None, f"git archive {tag} failed: {(out.splitlines() or [''])[-1][:160]}"
    tree = scratch / "tree"
    with tarfile.open(archive) as tar:
        try:
            tar.extractall(tree, filter="data")
        except TypeError:  # Python before the extraction filters
            tar.extractall(tree)
    return tree, tag


def same_text(a: bytes | None, b: bytes | None) -> bool:
    """Equal, line endings aside (a checkout may hold CRLF where the template holds LF)."""
    if a is None or b is None:
        return a is b
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def diff(rel: str, before: bytes | None, after: bytes | None, before_label: str, after_label: str) -> str:
    if b"\0" in (before or b"")[:4000] or b"\0" in (after or b"")[:4000]:
        return f"Binary file {rel} differs ({before_label} -> {after_label})\n"
    old = (before or b"").decode("utf-8", "replace").replace("\r\n", "\n").splitlines(keepends=True)
    new = (after or b"").decode("utf-8", "replace").replace("\r\n", "\n").splitlines(keepends=True)
    lines = difflib.unified_diff(old, new, f"a/{rel} ({before_label})" if before is not None else "/dev/null",
                                 f"b/{rel} ({after_label})" if after is not None else "/dev/null")
    return "".join(l if l.endswith("\n") else l + "\n\\ No newline at end of file\n" for l in lines)


def classify(project: bytes | None, installed: bytes | None, new: bytes | None, known: bool) -> str:
    if new is None:
        return CURRENT if project is None else DROPPED  # absent on both sides: nothing to do
    if same_text(project, new):
        return CURRENT
    if not known:
        return NEW if project is None else UNKNOWN
    if project is None:
        return REMOVED if installed is not None else NEW
    if same_text(project, installed):
        return BEHIND
    return CHANGED if same_text(installed, new) else CONFLICT


def installed_values(manifest: dict, params: dict, packs: list[str], config: dict) -> dict:
    """The values init filled a release's skeletons with, rebuilt from the project's settings."""
    pairs = substitutions(manifest, params)
    book = config.get("book") or {}
    jira = config.get("jira") or {}
    sources = (config.get("requirements") or {}).get("sources") or {}
    values = dict(params, book_version=str(manifest.get("version") or ""), **pack_rows(manifest, packs, pairs),
                  base_branch=str(config.get("base_ref") or "origin/dev").split("/", 1)[-1],
                  protected_branches=list(config.get("protected_branches") or []),
                  stack=str(book.get("stack") or "none"), packs=list(book.get("packs") or []),
                  jira_site=str(jira.get("site") or ""), jira_key=str(jira.get("project") or ""),
                  fsd_label=str(sources.get("fsd_link_label") or "FSD"), fsd_link="",
                  ci_live=not bool((config.get("local_ci") or {}).get("required", True)))
    rules = [rule for pack in packs for rule in ((manifest.get("packs") or {}).get(pack) or {}).get("features", [])]
    values["pack_feature_rules"] = "".join(f"{rule.rstrip()}\n" for rule in rules)
    return values


def installed_settings(tree: Path, manifest: dict, params: dict, packs: list[str], config: dict) -> dict | None:
    """The settings the installed release's init wrote (its factory.toml skeleton and each pack's additions,
    filled as init filled them); None when they cannot be rebuilt."""
    values = installed_values(manifest, params, packs, config)
    # Before 1.6 init filled `{{review_scope}}` with the stack pack's own block, else the core one.
    scopes = [tree / PACKS_DIR / pack / REVIEW_SCOPE for pack in packs] + [tree / SKELETONS_DIR / REVIEW_SCOPE]
    scope = next((p.read_text(encoding="utf-8").strip() for p in scopes if p.is_file()), "")
    skeleton = next((s for rel, s in skeleton_map(manifest, packs).items() if rel == CONFIG_RELATIVE), None)
    if skeleton is None or not (tree / SKELETONS_DIR / skeleton).is_file():
        return None
    text = fill_skeleton((tree / SKELETONS_DIR / skeleton).read_text(encoding="utf-8").replace(
        "{{review_scope}}", scope), values)
    for pack in packs:
        append = tree / PACKS_DIR / pack / APPEND
        if append.is_file():
            text += fill_skeleton(append.read_text(encoding="utf-8"), values)
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def installed_instructions(tree: Path, manifest: dict, params: dict, packs: list[str],
                           old_files: dict[str, bytes], config: dict) -> dict[str, str]:
    """The text the installed Book wrote into each instruction file: its skeletons as init filled them, and a
    pack's nested CLAUDE.md, which the Book owned before 1.6."""
    pairs = substitutions(manifest, params)
    values = installed_values(manifest, params, packs, config)
    found: dict[str, str] = {}
    for rel, skeleton in skeleton_map(manifest, packs).items():
        rel = renamed(rel, pairs)
        if rel.endswith("CLAUDE.md"):
            path = tree / SKELETONS_DIR / skeleton
            if path.is_file():
                found[rel] = fill_skeleton(path.read_text(encoding="utf-8"), values)
    for rel, data in old_files.items():
        if rel.endswith("CLAUDE.md") and not rel.startswith("devtools/"):
            found.setdefault(rel, data.decode("utf-8", "replace"))
    return found


def write_lock(target: Path, files: dict[str, bytes]) -> bool:
    """devtools/factory/book-files.lock: each Book-owned file the project carries, as the Book wrote it. False
    when the lock already said exactly that."""
    declined = book_config.declined_files(book_config.load(target).get("book") or {}) \
        if (target / CONFIG_RELATIVE).is_file() else set()
    path = target / book_config.LOCK
    text = book_config.lock_text({rel: data for rel, data in files.items() if rel not in declined})
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def stamp_version(path: Path, version: str) -> None:
    """Set factory.toml's top-level book_version, keeping every other byte and the file's line endings."""
    text = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    line = f'book_version = "{version}"'
    stamp = re.compile(r"(?m)^book_version[ \t]*=[^\r\n]*")
    if stamp.search(text):
        text = stamp.sub(line, text, count=1)
    else:  # a Book from before init wrote it: the first key, before any table
        lines = text.split(newline)
        at = next((i for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith("#")), len(lines))
        lines[at:at] = ["# The Book template version this repository's template-owned files came from "
                        "(`book upgrade`).", line]
        text = newline.join(lines)
    path.write_bytes(text.encode("utf-8"))


def set_book_key(path: Path, key: str, value: str | list[str] | None) -> None:
    """Set (or, with None, remove) a one-line key of factory.toml's [book] table, keeping every other byte."""
    text = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    start = next((i for i, l in enumerate(lines) if re.fullmatch(r"\[book\][ \t]*(#.*)?", l.strip())), None)
    if start is None:
        if value is None:
            return
        lines += ["[book]"]
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    at = next((i for i in range(start + 1, end) if re.match(rf"^{re.escape(key)}[ \t]*=", lines[i])), None)
    line = None if value is None else f"{key} = {json.dumps(value)}"
    if at is not None:
        lines[at:at + 1] = [] if line is None else [line]
    elif line is not None:
        lines.insert(start + 1, line)
    path.write_bytes(newline.join(lines).encode("utf-8"))


def pending_merge(config: dict, new_version: str) -> list[str] | None:
    """The files an earlier --apply to `new_version` left to merge by hand, or None when none was recorded."""
    book_table = config.get("book") or {}
    if str(book_table.get(PENDING_FOR) or "") != new_version:
        return None
    return [str(rel) for rel in book_table.get(PENDING_MERGE) or []]


def upgrade(target: Path, config: dict, manifest: dict, template_root: Path, apply: bool = False,
            run: Runner = real_runner, diffs_path: Path | None = None) -> tuple[str, bool]:
    """
    The drift report (read-only) and, with `apply`, the update: every file the template owns is compared
    with the installed version and the new one; only files the project has not changed are written. The report
    shows the diffs a person decides on; the BEHIND and NEW ones go to `diffs_path` (else a file in the system's
    temporary folder), which it names.
    """
    if target.resolve() == template_root.resolve():
        raise BookError("The template cannot upgrade itself: run `book upgrade --target <project>` from the "
                        "newer boilerplate's checkout.")
    if not (target / ".git").exists():
        raise BookError(f"{target} is not a git repository.")
    if not (target / CONFIG_RELATIVE).is_file():
        raise BookError(f"{target} has no {CONFIG_RELATIVE}: it holds no Book to upgrade (`book init` "
                        "installs one).")
    new_version = str(manifest.get("version") or "")
    if not version_key(new_version):
        raise BookError(f"The template's manifest version {new_version!r} is not a semver x.y.z.")
    installed, where = installed_version(target, config)
    if installed and version_key(installed) > version_key(new_version):
        raise BookError(f"The target is at template {installed}, newer than this template ({new_version}): "
                        "upgrade never goes back.")
    params = target_params(config)
    packs = installed_packs(config)
    new_files = owned_files(template_root, manifest, params, packs)
    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="book-upgrade-") as scratch:
        old_files: dict[str, bytes] = {}
        old_instructions: dict[str, str] = {}
        old_comments: set[str] = set()
        old_settings: dict | None = None
        known = False
        if installed:
            tree, why = installed_tree(template_root, installed, Path(scratch), run)
            if tree is None:
                notes.append(f"{COULD_NOT} the installed version {installed}: {why} - every file that differs "
                             "from the new template is kept, for a person")
            else:
                old_manifest = load_manifest(tree / "devtools/factory/book-template")
                if not old_manifest:
                    notes.append(f"{COULD_NOT} the installed version {installed}: {why} holds no manifest.toml")
                else:
                    old_files = owned_then(owned_files(tree, old_manifest, params, packs), installed)
                    known = True
                    old_instructions = installed_instructions(tree, old_manifest, params, packs, old_files, config)
                    old_settings = installed_settings(tree, old_manifest, params, packs, config)
                    # The installed template's settings comments: the 1.6 split drops only these with a key.
                    old_comments = book_migrate.template_comments([
                        path.read_text(encoding="utf-8", errors="replace")
                        for path in (tree / "devtools/factory/book-template").rglob("*")
                        if path.name in ("factory.toml.tmpl", "factory-append.toml")])
        else:
            notes.append(f"{COULD_NOT} the installed version: {where} - every file that differs from the new "
                         "template is kept, for a person")
    # A Book installed before init recorded them (Book D30) compares no pack files until a person adds them.
    unrecorded = [key for key in ("stack", "packs") if key not in (config.get("book") or {})]
    if unrecorded:
        notes.append(f"{WRONG} [book] has no {' or '.join(unrecorded)}: no pack file is compared or written - add "
                     "what this Book installed (`stack = \"dotnet\"`, `packs = [\"fe-guides\"]`), then run again")
    # A pack file the project chose not to carry (its own script does the job): `[book] declined`.
    declined = book_config.declined_files(config.get("book") or {})
    stale = sorted(declined - set(new_files) - set(old_files))
    if stale:
        notes.append(f"{WRONG} [book] declined names {len(stale)} file(s) the template does not own: "
                     f"{', '.join(stale)} - remove them from the list")
    notes += [f"{WRONG} {problem}" for problem in book_config.declined_problems(config)]
    rows: list[tuple[str, str]] = []
    diffs: list[str] = []    # what a person decides on: shown in the report
    routine: list[str] = []  # BEHIND and NEW, which --apply takes: written to a file the report names
    unoffered: list[str] = []
    pending = pending_merge(config, new_version)
    for rel in sorted(set(new_files) | set(old_files)):
        current = target / rel
        project = current.read_bytes() if current.is_file() else None
        status = DECLINED if rel in declined else classify(project, old_files.get(rel), new_files.get(rel), known)
        if status in (CONFLICT, REMOVED) and pending is not None and rel not in pending:
            status = RESOLVED  # a person merged it, or chose its absence, and took it off the list
        if status == NEW and known and rel.startswith(".claude/skills/") and is_rename_stub(new_files[rel]):
            # A stub keeps an old name working; a name the installed version never had needs none.
            unoffered.append(rel)
            continue
        if (status == DROPPED and rel.startswith(TEMPLATE_COPY) and known
                and same_text(project, old_files.get(rel))):
            status = RETIRED  # the Book's own template copy, unchanged and read by nothing: --apply removes it
        rows.append((status, rel))
        if status in (BEHIND, NEW):
            routine.append(diff(rel, project, new_files[rel], "project", f"template {new_version}"))
        elif status == UNKNOWN:
            diffs.append(diff(rel, project, new_files[rel], "project", f"template {new_version}"))
        elif status == CHANGED:
            diffs.append(f"# {rel}: the project changed it and the template did not; it is kept. The project's "
                         "change:\n" + diff(rel, old_files[rel], project, f"template {installed}", "project"))
        elif status == CONFLICT:
            diffs.append(f"# {rel}: the project changed it, so it is kept. What the template changed, to "
                         "carry over by hand:\n" +
                         diff(rel, old_files[rel], new_files[rel], f"template {installed}", f"template {new_version}"))
    offers, read_me_notes = requirements_read_me_offers(target, manifest, template_root, params, packs)
    for rel, (project, renamed_text) in offers.items():
        new_files[rel] = renamed_text
        rows.append((BEHIND, rel))
        routine.append(diff(rel, project, renamed_text, "project", f"template {new_version}"))
    notes += read_me_notes
    notes += must_merge_notes(rows)
    notes += old_command_notes(target, rows)
    notes += long_lived_notes(target, config, run)
    # Worked out again after --apply when it writes .claude/settings.json, so the run never names the old pin
    # (Book D57).
    plugin_at, plugin = len(notes), plugin_notes(target, config, manifest, rows)
    notes += plugin
    notes += fe_guides_notes(target, config)
    notes += pack_skeleton_notes(target, manifest, packs, skipped_skeletons(config),
                                 substitutions(manifest, params))
    notes += deploy_notes(config)
    notes += declined_rules_notes(target, config)
    if unoffered:
        notes.append(f"{len(unoffered)} rename stub(s) not offered: the installed version had none of these old "
                     f"names ({', '.join(sorted(rel.split('/')[2] for rel in unoffered))})")
    unmerged = [rel for status, rel in rows if status in (CONFLICT, REMOVED)]
    if pending is not None and unmerged:
        notes.append(f"{WRONG} {len(unmerged)} file(s) an earlier --apply left to merge by hand ([book] "
                     f"{PENDING_MERGE}): {', '.join(unmerged)} - book_version stays {installed} until each is "
                     "merged (CONFLICT: carry over the template's change below) or settled (REMOVED), and taken "
                     f"off the list (keep [book] {PENDING_FOR}: without it every file is listed again); then run "
                     "--apply again")
    moves: dict[str, bytes] = {}
    if book_migrate.applies(installed, new_version):
        # Each Book file the project changed is kept, and accepted for now, so the drift check does not fail the
        # first commit after the upgrade; the project's files lose what is the Book's (book_migrate).
        accepted = [rel for status, rel in rows if status in (CHANGED, CONFLICT)]
        # The new skeletons, as init would fill them: a section they keep in the project's file stays there.
        new_skeletons = {rel: text for rel, text in installed_instructions(
            template_root, manifest, params, packs, {}, config).items()}
        moves, move_notes = book_migrate.plan(target, config, old_instructions, new_files, params["name"], accepted,
                                              skeletons=new_skeletons, comments=old_comments,
                                              installed_settings=old_settings)
        # A file the move would leave as it is (a re-run after the split) is no move: no diff, nothing written.
        moves = {rel: data for rel, data in moves.items()
                 if not ((target / rel).is_file() and (target / rel).read_bytes().replace(b"\r\n", b"\n") == data)}
        # A nested CLAUDE.md the Book owned and 1.6 makes a skeleton is the project's file from now on: the move
        # writes it (or already has), importing the Book's rules, so it is never one to delete (DROPPED).
        rows = [(SPLIT if status == DROPPED and (rel in moves or imports_book_rules(target / rel)) else status, rel)
                for status, rel in rows]
        notes += move_notes
        for rel, data in moves.items():  # what the 1.6 move takes out of each file: a person reads this
            if rel == book_migrate.REGISTER_KEEP:
                continue
            current = target / rel
            diffs.append(f"# {rel}: the 1.6 move (SPLIT) - what goes to the Book's files, and what stays:\n"
                         + diff(rel, current.read_bytes() if current.is_file() else None, data, "project",
                                "after the move"))
        if not old_instructions:
            notes.append(f"  {CONFIRM} the installed Book's instruction files could not be rebuilt: only the text of "
                         "the Book's new rules files counts as the Book's")
    counts = {s: sum(r[0] == s for r in rows)
              for s in (CURRENT, BEHIND, NEW, CHANGED, RESOLVED, CONFLICT, REMOVED, DROPPED, RETIRED, SPLIT, UNKNOWN,
                        DECLINED)}
    retired = [rel for status, rel in rows if status == RETIRED]
    if retired:
        notes.append(f"{len(retired)} file(s) of the Book's own template copy ({TEMPLATE_COPY}) the new template no "
                     "longer has, unchanged and read by nothing here: " + ("removed (staged)" if apply else
                                                                           "--apply removes them (RETIRED below)"))
    diffs_at = ""
    if routine:
        # One file per checkout: two with the same folder name never share it.
        unique = hashlib.sha256(str(target.resolve()).encode("utf-8")).hexdigest()[:8]
        where = diffs_path or Path(tempfile.gettempdir()) / f"book-upgrade-{target.name}-{unique}-{new_version}.diff"
        try:
            where.write_text("".join(routine), encoding="utf-8", newline="\n")
            diffs_at = f"; the BEHIND and NEW diffs are written to {where}"
            notes.append(f"The BEHIND and NEW diffs ({len(routine)} file(s), which --apply takes without a "
                         f"decision): {where}")
        except OSError as error:
            notes.append(f"{WRONG} could not write the BEHIND and NEW diffs to {where}: {error}")
    mode = "--apply" if apply else ("report only, nothing written in the project (--apply updates the BEHIND and "
                                    "NEW files)")
    head = (f"book upgrade - installed {installed or 'unknown'}, template {new_version} - {mode}{diffs_at}\n"
            + " · ".join(f"{n} {s.lower()}" for s, n in counts.items() if n))
    # Each file a person must act on is listed once, under "For a person"; the rest here.
    out = [head, *notes, ""] + [f"{status:<9} {rel}" for status, rel in rows if status not in FOR_A_PERSON]
    written: list[str] = []
    created: list[str] = []
    if apply:
        for status, rel in rows:
            if status not in (BEHIND, NEW):
                continue
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(new_files[rel])
            (created if status == NEW else written).append(rel)
        if ".claude/settings.json" in written + created:
            out[1 + plugin_at:1 + plugin_at + len(plugin)] = plugin_notes(target, config, manifest, rows)
        if created:
            code, text = run(["git", "add", "--", *created], target, 120)
            if code != 0:
                out.append(f"{WRONG} git add the new files: {(text.splitlines() or [''])[-1][:160]}")
            else:
                executable = executable_in_template(template_root, manifest, params, packs, run)
                bits = [rel for rel in created if rel in executable or rel.endswith(".sh")]
                if bits:
                    run(["git", "update-index", "--chmod=+x", "--", *bits], target, 120)
        for rel, data in moves.items():
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            (target / rel).write_bytes(data)
            written.append(rel)
        if book_migrate.REGISTER_KEEP in moves:
            run(["git", "add", "--", book_migrate.REGISTER_KEEP], target, 60)
        if retired:
            code, text = run(["git", "rm", "-q", "--", *retired], target, 120)
            if code != 0:
                out.append(f"{WRONG} git rm the retired template files: {(text.splitlines() or [''])[-1][:160]}")
        # What the Book wrote, for check-book's drift check: the files it owns as this version renders them.
        write_lock(target, new_files)
        run(["git", "add", "--", book_config.LOCK], target, 60)
        config_path = target / CONFIG_RELATIVE
        if unmerged:
            # The installed version stays the record (a Book from before the stamp gets it written now, as its
            # copied manifest was just updated), so the template's change is still there to merge.
            stamp_version(config_path, installed)
            set_book_key(config_path, PENDING_FOR, new_version)
            set_book_key(config_path, PENDING_MERGE, unmerged)
            out += ["", f"Updated {len(written)}, created {len(created)} (staged); {CONFIG_RELATIVE} book_version "
                    f"stays {installed}: {len(unmerged)} file(s) to merge by hand are listed in [book] "
                    f"{PENDING_MERGE}. Merge each, take it off the list (leave [book] {PENDING_FOR} as it is: "
                    "without it the next run lists them all again), then run --apply again. Review `git diff` "
                    "and commit."]
        else:
            stamp_version(config_path, new_version)
            set_book_key(config_path, PENDING_MERGE, None)
            set_book_key(config_path, PENDING_FOR, None)
            out += ["", f"Updated {len(written)}, created {len(created)} (staged); {CONFIG_RELATIVE} book_version "
                    f"{installed or 'unknown'} -> {new_version}. Review `git diff` and commit."]
    kept = [(s, r) for s, r in rows if s in FOR_A_PERSON]
    if kept:
        out += ["", "For a person (never overwritten or deleted by upgrade):",
                *[f"{s:<9} {r}" for s, r in kept]]
    if diffs:
        out += ["", "Diffs to decide on (CONFLICT, CHANGED, UNKNOWN and the 1.6 move):", "", *diffs]
    return "\n".join(out).rstrip() + "\n", True


# The skill names retired in v1.3-v1.5 (the AI guide's *Old names*), as a requirements read-me written before
# then still names them. Only hyphenated names: none is an English phrase or another name's part.
OLD_SKILL_NAMES = {
    "requirements-check": "book:check-sources", "requirements-sync": "book:sync-sources",
    "api-contract-draft": "book:outline-api", "change-impact": "book:impact", "project-progress": "book:progress",
    "review-branch": "book:self-review", "review-pr": "book:peer-review", "fsd-audit": "book:fact-check",
    "docs-vs-code": "book:check-docs", "docs-update-from-code": "book:update-docs",
    "docs-consistency": "book:check-consistency", "docs-trim": "book:trim", "book-setup": "book:setup",
    "plan-feature": "book:plan", "book-upgrade": "book:upgrade", "deploy-dev-qa": "book:deploy",
    "fe-guides-check": "book:check-guides", "fe-guides-publish": "book:publish-guides",
}
# A name on its own, with or without its slash: never inside a path or a longer name (`chore/book-setup-x`).
OLD_SKILL_NAME = re.compile(r"(?<![\w/:.-])(/?)(" + "|".join(sorted(map(re.escape, OLD_SKILL_NAMES), key=len,
                                                                     reverse=True)) + r")(?![\w-])")
REQUIREMENTS_READ_ME = ("requirements/read-me.txt", "requirements/**/read-me.txt")


def new_skill_names(text: str) -> str:
    return OLD_SKILL_NAME.sub(lambda m: m.group(1) + OLD_SKILL_NAMES[m.group(2)], text)


def read_me_shape(text: str) -> str:
    """A requirements read-me with what a project may edit in it masked: the LINKS block and each Last checked
    paragraph (the edits /book:check-sources makes), so what is left is the template's own text."""
    out, masked = [], False
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("=== "):
            masked = line.startswith("=== LINKS")
            out.append(line if not masked else "=== LINKS ===")
            continue
        if line.startswith("Last checked:"):
            masked = True
            out.append("Last checked:")
            continue
        if masked and not line.strip() and out and out[-1] == "Last checked:":
            masked = False
        if not masked:
            out.append(line.rstrip())
    return "\n".join(out).strip()


def requirements_read_me_offers(target: Path, manifest: dict, template_root: Path, params: dict,
                                packs: list[str]) -> tuple[dict[str, tuple[bytes, bytes]], list[str]]:
    """
    The requirements read-me files that still name an old skill (Book D47). They are the project's own (a skeleton's
    target), so upgrade never compares them; but one that is the skeleton with only its LINKS and Last checked lines
    edited is offered BEHIND, its old names renamed and nothing else changed. A copy the project wrote more of is
    named for a person, line by line.
    """
    skeletons = {rel: skeleton for rel, skeleton in skeleton_map(manifest, packs).items()
                 if any(fnmatch.fnmatch(rel, p) for p in REQUIREMENTS_READ_ME)}
    values = {key: str(params.get(key) or "") for key in ("name", "title", "slug", "env_prefix")}
    offers: dict[str, tuple[bytes, bytes]] = {}
    by_hand: list[str] = []
    folder = target / "requirements"
    found = sorted(p.relative_to(target).as_posix() for p in folder.rglob("read-me.txt")) if folder.is_dir() else []
    for rel in found:
        project = (target / rel).read_bytes()
        text = project.decode("utf-8", "replace")
        renamed_text = new_skill_names(text)
        if renamed_text == text:
            continue
        skeleton = template_root / SKELETONS_DIR / skeletons[rel] if rel in skeletons else None
        if skeleton is not None and skeleton.is_file():
            shape = re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)),
                           skeleton.read_text(encoding="utf-8"))
            if read_me_shape(renamed_text) == read_me_shape(shape):
                offers[rel] = (project, renamed_text.encode("utf-8"))
                continue
        lines = [str(n) for n, line in enumerate(text.splitlines(), 1) if OLD_SKILL_NAME.search(line)]
        by_hand.append(f"{rel} (line{'s' if len(lines) > 1 else ''} {', '.join(lines)})")
    notes = []
    if by_hand:
        notes.append(f"{CONFIRM} {len(by_hand)} requirements read-me file(s) the project wrote its own text into still "
                     f"name an old skill: {'; '.join(by_hand)} - rename each by hand in a pull request "
                     "(`/requirements-check` -> `/book:check-sources`, whose capture step is still Phase 4 - Capture; "
                     "the rest in the AI guide's *Old names*); a read-me may be edited in place")
    return offers, notes


OLD_REVIEW_FOLDER = ".claude/skills/review-pr/references"
FRONTMATTER = re.compile(rb"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)


def is_rename_stub(content: bytes) -> bool:
    """A skill whose only job is to point an old name at its new one: its frontmatter, not its body, says
    `description: Renamed to ...` and `disable-model-invocation: true`."""
    head = FRONTMATTER.match(content)
    lines = head.group(1).splitlines() if head else []
    return (any(line.startswith(b"description: Renamed to ") for line in lines)
            and any(line.strip() == b"disable-model-invocation: true" for line in lines))


STUB_TARGET = re.compile(rb"^description: Renamed to (/book:[\w-]+)", re.M)


def stub_target(path: Path) -> str:
    """The new name a rename stub points at (`/book:outline-api`), or "" when the file is no stub."""
    try:
        content = path.read_bytes()
    except OSError:
        return ""
    found = STUB_TARGET.search(FRONTMATTER.match(content).group(1)) if is_rename_stub(content) else None
    return found.group(1).decode() if found else ""


PLUGIN_OWNED = (".claude/skills/", ".claude/agents/")


UNVERSIONED = "Unversioned routes"
CONTRACTS_DOC = "docs/api/api-contracts.md"
FE_GUIDES_DIR = "docs/fe-integration-guides"
CONTRACT_ROW = re.compile(r"^\|\s*(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*\|\s*`([^`]+)`")


def unowned_unversioned(target: Path, config: dict) -> list[str]:
    """
    The paths under the contracts' *Unversioned routes* that nothing owns, as check-fe-guides judges an owner
    (mirrored, since the target's copy of that check may be older): a `[fe_guides] owners` entry for the
    section, a guide titled after it, or a guide whose **Base:** line covers the path.
    """
    try:
        text = (target / CONTRACTS_DOC).read_text(encoding="utf-8")
    except OSError:
        return []
    paths, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == UNVERSIONED
        elif inside and CONTRACT_ROW.match(line):
            paths.append(CONTRACT_ROW.match(line).group(1))
    owners = (config.get("fe_guides") or {}).get("owners") or []
    if not paths or any(isinstance(row, dict) and row.get("section") == UNVERSIONED for row in owners):
        return []
    bases: list[str] = []
    for guide in sorted((target / FE_GUIDES_DIR).glob("*.md")):
        if guide.name.startswith("_") or guide.stem == "api-conventions":
            continue
        body = guide.read_text(encoding="utf-8", errors="replace")
        title = next((line[2:] for line in body.splitlines() if line.startswith("# ")), "")
        # check-fe-guides' title match: the title, or one that starts with the section's name and a word.
        title = re.split(r"\s+[-–—]\s+front-end integration guide", title, flags=re.I)[0]
        title = re.sub(r"\s+", " ", re.sub(r"\s+[-–—]\s+", " — ", title.strip().lower()))
        section = UNVERSIONED.lower()
        if title == section or title.startswith(section + " ") or section.startswith(title + " — "):
            return []
        base = re.search(r"\*\*Base:\*\*(.*?)(?:·\s*\*\*|\n\s*\n|$)", body, re.S)
        bases += re.findall(r"`(/[^`]*)`", base.group(1)) if base else []
    return [path for path in paths if not any(base_covers(base, path) for base in bases)]


def base_covers(base: str, path: str) -> bool:
    """A guide's Base path leads `path`, segment by segment (a placeholder matches any one), as check-fe-guides."""
    want, have = [p for p in base.split("/") if p], [p for p in path.split("/") if p]
    return bool(want) and len(want) <= len(have) and all(
        re.fullmatch(r"<[^>]+>|\{[^}]+\}", part) or part == actual for part, actual in zip(want, have))


def fe_guides_notes(target: Path, config: dict) -> list[str]:
    """
    A Book whose contracts list unversioned routes that nothing owns (Book D36): upgrade never writes factory.toml,
    and check-fe-guides asks for an owner the first time one of those rows changes - as every one did when the
    generator gained the Source column. The skeleton's `[fe_guides] owners` example is commented out, since only
    a dotnet Book's contracts have the section; a person confirms whether the front end calls any of them.
    """
    unowned = unowned_unversioned(target, config)
    if not unowned:
        return []
    return [f"{CONFIRM} {CONTRACTS_DOC} lists {len(unowned)} unversioned route(s) nothing owns "
            f"({', '.join(unowned[:3])}{' ...' if len(unowned) > 3 else ''}): check-fe-guides fails the next branch "
            f"that changes one - if the front end calls none, add the skeleton's commented example to factory.toml "
            f"[fe_guides] owners (`{{ section = \"{UNVERSIONED}\", guides = [], reason = \"...\" }}`) with this "
            "project's reason, otherwise give them a guide"]


def imports_book_rules(path: Path) -> bool:
    """Whether an instruction file imports the Book's rules (`@…/.claude/book/…` on a line of its own)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(".claude/book/" in rel for rel in book_config.imports(text))


def pack_skeleton_notes(target: Path, manifest: dict, packs: list[str], skipped: set[str] = frozenset(),
                        pairs: list[tuple[str, str]] = ()) -> list[str]:
    """
    A pack's skeleton the project lacks (Book D44): a skeleton's target is the project's own content, so upgrade
    never writes one, but `book verify` reports it MISSING; a person fills it from the skeleton. Its path takes
    the project's names (`src/<Name>.API/`), as init wrote it.
    """
    notes = []
    for pack in packs:
        skeletons = ((manifest.get("packs") or {}).get(pack) or {}).get("skeletons", {})
        for rel, skeleton in sorted(skeletons.items()):
            rel = renamed(rel, list(pairs))
            if not (target / rel).is_file() and rel not in skipped:
                notes.append(f"{MISSING} {rel}: the {pack} pack's document, which `book verify` asks for - start "
                             f"it from {SKELETONS_DIR}/{skeleton} and fill its slots in a pull request")
    return notes


def plugin_notes(target: Path, config: dict, manifest: dict, rows: list[tuple[str, str]]) -> list[str]:
    """
    What a Book moving onto the book plugin (v1.3.0) must do by hand, since upgrade never writes a file the
    project owns or changed: enable the plugin, point [review_scope] at the plugin's checklist, and delete the
    old skill and agent files the plugin now provides (kept, DROPPED or CONFLICT, so nothing is lost unasked),
    and - from v1.5 - the old names' rename stubs, which stop working once the upgrade lands.
    """
    plugin = manifest.get("plugin")
    if not plugin:
        return []
    floor = plugin_floor(manifest)
    notes = []
    try:
        settings = json.loads((target / ".claude/settings.json").read_text(encoding="utf-8"))
        enabled = (settings.get("enabledPlugins") or {}).get(plugin) is True
    except (OSError, ValueError, AttributeError):
        enabled = False
    if not enabled:
        notes.append(f"{WRONG} .claude/settings.json does not enable {plugin}: the Book's skills and agents come "
                     "from it now - add its `extraKnownMarketplaces` and `enabledPlugins` entries from the "
                     "template's .claude/settings.json")
    else:
        # A pin older than this template's plugin_min lacks what the new files call (Book D39).
        try:
            _, ref = plugin_ref((target / ".claude/settings.json").read_text(encoding="utf-8"), plugin)
        except OSError:
            ref = ""
        tag = TAG.fullmatch(ref)
        if tag and version_key(floor) and version_key(tag.group(1)) < version_key(floor):
            notes.append(f"{WRONG} .claude/settings.json pins the book plugin at {ref}, older than template "
                         f"{manifest.get('version')}'s plugin_min {floor}: move its `ref` to v{floor} or later "
                         "in the same pull request")
    if (config.get("review_scope") or {}).get("folder") == OLD_REVIEW_FOLDER:
        notes.append(f"{WRONG} factory.toml [review_scope] still reads {OLD_REVIEW_FOLDER}/: replace its `folder`, "
                     "`always` and `[review_scope.dimensions]` (or delete them: the stack's defaults file, "
                     "devtools/factory/book-defaults/<stack>.toml, holds the template's) - the method, Pass A and B0 are the "
                     "plugin's `book:peer-review/references/` files now")
    # A rename stub kept from v1.3.0 or v1.4.0 (DROPPED: v1.5 retires the old names) is its own note: it holds
    # nothing to carry over. So is the copy of a pack's stub in the project's own book-template/.
    stubs = sorted((rel, stub_target(target / rel)) for status, rel in rows
                   if status == DROPPED and "/.claude/skills/" in f"/{rel}" and stub_target(target / rel))
    stubbed = {rel for rel, _ in stubs}
    retired = sorted(rel for status, rel in rows
                     if rel.startswith(PLUGIN_OWNED) and rel not in stubbed and (
                         status == DROPPED or (status == CONFLICT and rel.endswith("/SKILL.md"))))
    # A dropped test that loads one of those scripts fails CI the moment the script goes: it goes with them.
    scripts = {Path(rel).name for rel in retired if rel.endswith(".py")}
    tests = []
    for status, rel in rows:
        if status == DROPPED and Path(rel).name.startswith("test_") and rel.endswith(".py"):
            try:
                text = (target / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(name in text for name in scripts):
                tests.append(rel)
    if tests:
        notes.append(f"{WRONG} {', '.join(sorted(tests))} (DROPPED) load(s) a retired script below: delete them "
                     "with the scripts, or CI fails on the missing file")
    if retired:
        notes.append(f"{WRONG} {len(retired)} old skill or agent file(s) a move into the book plugin retired are "
                     "kept (DROPPED or CONFLICT below, under .claude/skills/ and .claude/agents/; the plugin "
                     "provides each now - v1.3.0 moved the Book's skills and agents, v1.4.0 the packs' skills): "
                     "once the plugin is enabled, carry any local edit over (a pack skill's project facts go in "
                     "factory.toml: [fe_guides], [deploy]) and delete them")
    if stubs:
        names = ", ".join(sorted({f"/{Path(rel).parent.name} -> {new}" for rel, new in stubs}))
        copies = sum(not rel.startswith(".claude/skills/") for rel, _ in stubs)
        where = ".claude/skills/" + (f" (and {copies} in devtools/factory/book-template/packs/)" if copies else "")
        note = (f"{WRONG} {len(stubs)} old-name stub(s) are DROPPED below ({names}): template "
                f"{manifest.get('version')} retires the old names, so they stop working after this upgrade - use "
                f"the /book: name each points at, and delete the stubs' folders under {where} in the same pull "
                "request")
        try:
            agents = (target / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
        except OSError:
            agents = ""
        if "](.claude/skills/)" in agents:
            note += ("; AGENTS.md links to .claude/skills/, which goes once no skill of the project's own is left "
                     "there: take the template's wording, which names the folder without linking it")
        notes.append(note)
    return notes


# What `book` (devtools/book.py, v1.4.0) needs merged first: factory.py's main() names the command, and the guard
# judges a deploy under either name. A kept older copy of either makes the new name fail, or pass unjudged.
BOOK_ENTRY = "devtools/book.py"
BOOK_NEEDS = ("devtools/factory/factory.py", "devtools/factory/hooks/guard.py")
# What each kept file means until it is merged, one clause per file.
MERGE_UNTIL = {"devtools/factory/factory.py": "`book` refuses to run",
               "devtools/factory/hooks/guard.py": "the guard judges a deploy only under the name factory.py"}


def must_merge_notes(rows: list[tuple[str, str]]) -> list[str]:
    """The project gets devtools/book.py, but keeps its own factory.py or guard.py (a CONFLICT): merge them first."""
    status = dict((rel, s) for s, rel in rows)
    kept = [rel for rel in BOOK_NEEDS if status.get(rel) == CONFLICT]
    if status.get(BOOK_ENTRY) != NEW or not kept:
        return []
    until = " and ".join(MERGE_UNTIL[rel] for rel in kept)
    return [f"{WRONG} {BOOK_ENTRY} is NEW, but the project keeps its own {' and '.join(kept)} (CONFLICT below): merge "
            f"{'these' if len(kept) > 1 else 'it'} before using book - until then {until}"]


# From v1.5 factory.py is no command: a CI file, script or hook the project keeps (changed by it, so upgrade never
# rewrites it) that still runs it fails once the upgrade lands, as each `book` command it meant to run.
# A path to factory.py at the start of a word (after a Python of any version, `uv run`, `$PY`, a quote, or run
# directly as `./devtools/factory/factory.py`), then a command - never a redirect (`2>/dev/null`), and never a
# `ref:path` git reads (`git cat-file -e "$DEST:devtools/factory/factory.py"`), which runs nothing, and never the
# destination's copy the pipelines' AI-files step extracts to /tmp/destination/ and runs on a branch older than
# v1.4.0, where factory.py is still a command.
OLD_COMMAND = re.compile(r"""(?:^|[\s;&|(`"'=])(?!/tmp/destination/)[^\s"':;&|(`=]*factory[/\\]factory\.py["']?[ \t]+(?!\d*[<>])[\w$-]""",
                         re.M)
RUNS_COMMANDS = (".yml", ".yaml", ".sh", ".ps1")


def old_command_notes(target: Path, rows: list[tuple[str, str]]) -> list[str]:
    """Kept CI files, scripts and hooks that still run `python .../factory/factory.py <command>`: merge them first."""
    callers = []
    for status, rel in rows:
        if status not in (CHANGED, CONFLICT, UNKNOWN) or rel.startswith("devtools/factory/book-template/") or not (
                rel.endswith(RUNS_COMMANDS) or rel.startswith(".githooks/")):
            continue
        try:
            text = (target / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if OLD_COMMAND.search(text):
            callers.append(f"{rel} ({status})")
    if not callers:
        return []
    return [f"{WRONG} {', '.join(callers)} still run(s) devtools/factory/factory.py as a command, which from v1.5 "
            "refuses to run: carry the template's switch to devtools/book.py over in the same pull request, or "
            "each step that runs it fails"]


# From v1.5 the pipelines' AI-files step runs the destination branch's own devtools/book.py, or, on a branch whose
# Book is older than v1.4.0 (factory.py, no book.py), that branch's own factory.py, still a command there.
PRE_BOOK_ENTRY = "devtools/factory/factory.py"


def long_lived_notes(target: Path, config: dict, run: Runner) -> list[str]:
    """
    The protected branches (`protected_branches` in factory.toml) still holding a Book from before v1.4.0, read
    from the project's own refs (origin/<branch>, else <branch>). Information, not a stop: a pull request into one
    is judged by that branch's own factory.py until a promotion brings it 1.5, and promoting early to avoid that
    would release unfinished work.
    """
    if "bitbucket" not in installed_packs(config):
        return []
    older, unread = [], []
    for branch in [str(b) for b in config.get("protected_branches") or [] if str(b)]:
        ref = next((r for r in (f"origin/{branch}", branch)
                    if run(["git", "rev-parse", "--verify", "--quiet", f"{r}^{{commit}}"], target, 60)[0] == 0), None)
        if ref is None:
            unread.append(branch)
        elif (run(["git", "cat-file", "-e", f"{ref}:{BOOK_ENTRY}"], target, 60)[0] != 0
              and run(["git", "cat-file", "-e", f"{ref}:{PRE_BOOK_ENTRY}"], target, 60)[0] == 0):
            older.append(ref)
    notes = []
    if older:
        notes.append(f"{', '.join(older)} hold(s) a Book from before v1.4.0 (factory.py, no devtools/book.py), for "
                     "information: the pipelines' AI-files step judges a pull request into them with their own old "
                     "check until a promotion brings them 1.5 - nothing to promote before this upgrade")
    if unread:
        books = "their Books were" if len(unread) > 1 else "its Book was"
        notes.append(f"no local or origin ref for {', '.join(unread)}, so {books} not read, for information: a "
                     "long-lived branch older than v1.4.0 keeps its own old check until a promotion brings it 1.5")
    return notes


# What /book:deploy reads from [deploy]: what deploy.py needs before it runs (`target()`), and the keys v1.4.0
# added for the skill's project facts, which a Book from before it has not got.
DEPLOY_ADDED = ("runbook", "audit_record")


def declined_rules_notes(target: Path, config: dict) -> list[str]:
    """
    A project that declines `check-test-setup.py` (Book D103): the rules table in devtools/README.md names its two
    rules by the checks that run them, and says "declined" once `check-rules.py --write` has rewritten it; until
    then `check-rules --check` and the pre-commit hook fail with "table is stale". The one step a person takes.
    """
    declined = book_config.declined_files((config.get("book") or {}))
    if "devtools/checks/check-test-setup.py" not in declined:
        return []
    readme = target / "devtools" / "README.md"
    rows = [l for l in readme.read_text(encoding="utf-8", errors="replace").splitlines()
            if "check-test-setup.py" in l and l.startswith("|")] if readme.is_file() else []
    if not rows or all("declined" in row for row in rows):
        return []
    return [f"{CONFIRM} check-test-setup.py is declined, so devtools/README.md's rules table is stale: run "
            "`python devtools/checks/check-rules.py --write` and commit it, or `check-rules --check` and the "
            "pre-commit hook fail"]


def deploy_notes(config: dict) -> list[str]:
    """
    A Book with the deploy pack: from v1.4.0 the procedure is /book:deploy and the project's facts are
    factory.toml's [deploy] (upgrade never writes factory.toml). Which keys are still to fill, by deploy.py's own
    rule (a missing key, or a `<…>` anywhere in a value), for a person.
    """
    if "deploy" not in installed_packs(config):
        return []
    import deploy as deploy_tool  # the rule a deploy stops on, not a second copy of it

    deploy = config.get("deploy") or {}
    keys = list(dict.fromkeys(key for env in deploy_tool.ENVIRONMENTS for key in deploy_tool.unfilled(deploy, env)))
    added = [f"[deploy] {key}" for key in DEPLOY_ADDED if key not in deploy]
    if not keys and not added:
        return []
    parts = []
    if keys:
        parts.append(f"{', '.join(keys)} to fill - a deploy stops on each")
    if added:
        parts.append(f"{', '.join(added)} to add (the deployment plan, and where the last audit is recorded; "
                     "\"\" when there is none)")
    return [f"{CONFIRM} /book:deploy reads the project's facts from factory.toml [deploy]: {'; '.join(parts)}. "
            "The keys and their meaning are the deploy pack's factory-append.toml; an edited .claude/skills/deploy/"
            "SKILL.md's own detail goes in `runbook` or an optional [deploy.notes] table, by step"]


def executable_in_template(template_root: Path, manifest: dict, params: dict, packs: list[str], run: Runner) -> set[str]:
    """The target paths of the template files git holds as executable (mode 100755)."""
    pairs = substitutions(manifest, params)
    code, modes = run(["git", "ls-files", "-s"], template_root, 120)
    if code != 0:
        return set()
    executable = {entry.split("\t", 1)[1] for entry in modes.splitlines()
                  if entry.startswith("100755 ") and "\t" in entry}
    return {target_path(rel, source, template_root, manifest, pairs)
            for rel, source in template_sources(template_root, manifest, packs)
            if source.relative_to(template_root).as_posix() in executable}


# ---------------------------------------------------------------- the command


def add_parser(sub, prog: str = "book") -> None:
    # Its own prog: devtools/book.py runs `book upgrade` as `book book upgrade`, whose usage line said so (Book D50).
    book = sub.add_parser("book", prog=prog, allow_abbrev=False,
                          help="the Book: verify it is set up (read-only), init what is absent, or upgrade it")
    book.add_argument("step", choices=["verify", "init", "upgrade", "defects"])
    book.add_argument("action", nargs="?", default="", help="defects: `classify` - propose each open defect's "
                      "Type and Source (report-first; --apply writes the columns)")
    book.add_argument("--target", default="", help="the repository; default: this one")
    book.add_argument("--params", default="", help="init: the parameter file (default <target>/book.toml)")
    book.add_argument("--no-verify", action="store_true", help="init: stop after creating; do not run verify")
    book.add_argument("--no-self-tests", action="store_true", help="verify: skip the self-test suites (reported, never passed)")
    book.add_argument("--apply", action="store_true",
                      help="upgrade: update the files the project has not changed, and bump book_version")
    book.add_argument("--diffs", default="", help="upgrade: the file for the BEHIND and NEW diffs, which --apply "
                      "takes without a decision (default: one in the system's temporary folder, named in the report)")


def main(root: Path, config_raw: dict, args, private: dict | None = None, run: Runner = real_runner) -> tuple[str, bool]:
    target = Path(args.target).resolve() if args.target else root
    manifest = load_manifest()
    same = target.resolve() == root.resolve()
    if args.step == "upgrade":
        diffs = Path(args.diffs).resolve() if getattr(args, "diffs", "") else None
        return upgrade(target, load_target_config(target), manifest, HERE.parents[1], args.apply, run, diffs)
    if args.step == "defects":
        if args.action != "classify":
            raise BookError("`book defects classify [--apply]` is the one defects step.")
        return book_migrate.classify_command(target, args.apply)
    if args.step == "init":
        params = load_params(Path(args.params) if args.params else target / "book.toml")
        text, ok = init(target, params, manifest, HERE.parents[1], run)
        if args.no_verify:
            return text, ok
        ctx = Context(target, load_target_config(target), manifest, run, private if same else None,
                      self_tests=not args.no_self_tests)
        report, ready = verify(ctx)
        return f"{text}\n\n{report}", ok and ready
    ctx = Context(target, config_raw if same else load_target_config(target), manifest, run,
                  private if same else None, self_tests=not args.no_self_tests)
    return verify(ctx)


if __name__ == "__main__":  # pragma: no cover - run through factory.py
    print("Run it as `python3 devtools/book.py verify`.", file=sys.stderr)
    sys.exit(2)
