"""
factory book verify|init — whether this repository's Book is set up, and setting one up.

The Book is the documentation, requirements and AI-rules architecture every session works inside:
the instruction files, the owner documents, `requirements/`, the hooks, the guard and the checks.

    factory book verify [--target <repo>] [--no-self-tests]
    factory book init --params <book.toml> [--target <repo>]
    factory book upgrade --target <repo> [--apply]

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
person, and bumps `book_version` in factory.toml.

Python 3.11+, standard library only.
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
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


def load_manifest(template_dir: Path = TEMPLATE_DIR) -> dict:
    if tomllib is None:
        raise BookError("factory book needs Python 3.11 or later (tomllib).")
    path = template_dir / "manifest.toml"
    if not path.is_file():
        return {}
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def load_target_config(root: Path) -> dict:
    """The target's factory.toml, read as data. Its private settings are not merged here."""
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


def cells(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


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
    for line in lines:
        line.code = "N6"
    return lines


def owner_tokens(root_text: str) -> list[str]:
    """The paths CLAUDE.md's owner table names - check-book.py's reading of the same table."""
    if "## Where knowledge lives" not in root_text:
        return []
    table = root_text.split("## Where knowledge lives", 1)[1].split("\n## ", 1)[0]
    tokens = []
    for line in table.splitlines():
        if line.startswith("|") and line.count("|") > 2:
            for token in re.findall(r"`([^`]+)`", line.split("|")[2]):
                if "/" in token or token.endswith(".md"):
                    tokens.append(token)
    return tokens


def n7_owners_exist(ctx: Context) -> list[Line]:
    name = "every owner in Where knowledge lives exists"
    text = ctx.text("CLAUDE.md")
    if text is None:
        return [Line(COULD_NOT, name, "no CLAUDE.md")]
    tokens = owner_tokens(text)
    if not tokens:
        return [Line(WRONG, name, "CLAUDE.md has no owner table under \"Where knowledge lives\"")]
    missing = []
    for token in tokens:
        if "<" in token:  # a naming pattern: its folder is what must exist
            folder = token.split("<", 1)[0].rsplit("/", 1)[0]
            if folder and not (ctx.root / folder).is_dir():
                missing.append(folder + "/")
        elif not (ctx.root / token).exists():
            missing.append(token)
    return [Line(MISSING, name, sample(missing, 6))] if missing else [Line(OK, name, f"{len(tokens)} named")]


def n8_archive(ctx: Context) -> list[Line]:
    name = "every archive row has its file"
    text = ctx.text("docs/CLAUDE.md")
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
    status_at = None
    for line in section(defects, "## Open").splitlines():
        if not line.startswith("|"):
            status_at = None if not line.strip() else status_at
            continue
        row = cells(line)
        if "Status" in row:
            status_at = row.index("Status")
            continue
        if status_at is None or set(line) <= set("|-: ") or len(row) <= status_at:
            continue
        status = row[status_at].strip("` ")
        if not status.startswith(DEFECT_STATUSES) or status.startswith("Blocked") and "—" not in status:
            problems.append(f"{row[0]}: Status {status!r}")
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
        fill.append("[ai_files] owners (each owner runs `factory pr whoami`)")
    trace = (ctx.config.get("requirements") or {}).get("traceability")
    if trace and not (ctx.root / trace).is_file():
        fill.append(f"{trace} (written by /book:fact-check)")
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
    folder = ctx.root / ".claude" / "skills"
    if not folder.is_dir():
        return [Line(MISSING, name, ".claude/skills/")]
    problems = []
    present = sorted(p.name for p in folder.iterdir() if p.is_dir())
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
    lines = [Line(WRONG, name, sample(problems, 2)) if problems else Line(OK, name, f"{len(present)} skills")]
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
    The book plugin is pinned to a release (D39): a marketplace with no `ref`, or a branch, hands each new plugin
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
    One owner: `[fe_guides] drive_folder_id` in factory.toml. The skill and its script read it from there
    (`factory config fe_guides.drive_folder_id`) and repeat no folder id, so the two cannot drift.
    """
    name = "the FE Drive folder id is set, once"
    skill = ctx.text(".claude/skills/publish-guides/SKILL.md")
    script = ctx.text(".claude/skills/publish-guides/Publish-FeGuidesToDrive.ps1")
    if skill is None and script is None:
        return [Line(NA, name, "no publish-guides skill")]
    if skill is None or script is None:
        return [Line(MISSING, name, "publish-guides needs both SKILL.md and Publish-FeGuidesToDrive.ps1")]
    folder_id = str((ctx.config.get("fe_guides") or {}).get("drive_folder_id") or "")
    if FILL.search(folder_id):
        return [Line(TO_FILL, name, "[fe_guides] drive_folder_id in factory.toml: the FE team's existing Drive "
                     "folder (never created by a tool)")]
    if not re.fullmatch(r"[\w-]+", folder_id):
        return [Line(MISSING, name, "no [fe_guides] drive_folder_id in factory.toml")]
    key = "fe_guides.drive_folder_id"
    if re.search(r"drive\.google\.com/drive/folders/[\w-]+", skill) or folder_id in skill:
        return [Line(WRONG, name, f"SKILL.md repeats a folder id; it names `{key}` in factory.toml instead")]
    if re.search(r"\$FolderId\s*=\s*['\"][^'\"]+['\"]", script) or folder_id in script:
        return [Line(WRONG, name, "the script hard-codes a folder id; it reads factory.toml's instead")]
    missing = [rel for rel, text in (("SKILL.md", skill), ("the script", script)) if key not in text]
    if missing:
        return [Line(WRONG, name, f"no `{key}` in {' or '.join(missing)}: it must read factory.toml's id")]
    return [Line(OK, name, "[fe_guides] drive_folder_id in factory.toml")]


def n18_private_settings(ctx: Context) -> list[Line]:
    """Names and set / not set only. Never a value, and never the file's contents."""
    name = "private settings (names only)"
    raw_sources = (load_target_config(ctx.root).get("requirements") or {}).get("sources") or {}
    if raw_sources.get("live_credential"):
        return [Line(WRONG, name, "factory.toml holds live_credential - a person's credential belongs in "
                     "their private settings file, never the repository")]
    if ctx.private is None:
        return [Line(COULD_NOT, name, "only that repository's own `factory book verify` reads its private settings")]
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


N_CHECKS: list[tuple[str, Callable[[Context], list[Line]]]] = [
    ("N1", n1_hooks_path), ("N2", n2_settings), ("N3", n3_guard_mode), ("N4", n4_gitignore),
    ("N5", n5_gitattributes), ("N6", n6_instruction_files), ("N7", n7_owners_exist), ("N8", n8_archive),
    ("N9", n9_project), ("N10", n10_ledgers), ("N11", n11_plan_status), ("N12", n12_fe_guides),
    ("N13", n13_requirements), ("N14", n14_snapshots), ("N15", n15_factory_config), ("N16", n16_skills),
    ("N17", n17_drive_folder), ("N18", n18_private_settings), ("N19", n19_gitkeep), ("N20", n20_names),
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
    head = f"factory book verify - template {target_version or 'not stamped'}"
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
REVIEW_SCOPE = "review-scope.toml"  # a stack pack's own [review_scope]; the skeletons' is the core one


def load_params(path: Path) -> dict:
    if tomllib is None:
        raise BookError("factory book needs Python 3.11 or later (tomllib).")
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
                        '"the Git host account id" }] (`factory pr whoami` prints the id), or [] for none yet.')
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


def review_scope_block(template_root: Path, packs: list[str]) -> str:
    """factory.toml's [review_scope]: the stack pack's own dimensions where it ships them, else the core B0."""
    for pack in packs:
        own = template_root / PACKS_DIR / pack / REVIEW_SCOPE
        if own.is_file():
            return own.read_text(encoding="utf-8").strip()
    core = template_root / SKELETONS_DIR / REVIEW_SCOPE
    return core.read_text(encoding="utf-8").strip() if core.is_file() else ""


# The nested CLAUDE.md files every Book has (docs/, tests/): the root CLAUDE.md's table starts with them.
CORE_INSTRUCTIONS = 2


def pack_rows(manifest: dict, packs: list[str], pairs: list[tuple[str, str]]) -> dict[str, str]:
    """The root CLAUDE.md's rows for what the packs installed: their nested CLAUDE.md files and their owner
    documents (each pack's `instructions` and `owners` in the manifest), with the project's names."""
    settings = [((manifest.get("packs") or {}).get(pack) or {}) for pack in packs]
    instructions = [row for pack in settings for row in pack.get("instructions", [])]
    owners = [row for pack in settings for row in pack.get("owners", [])]
    rules = [rule for pack in settings for rule in pack.get("features", [])]
    count = CORE_INSTRUCTIONS + len(instructions)
    words = {n: w for w, n in NUMBER_WORDS.items()}
    return {
        "pack_instruction_rows": "".join(
            renamed(f"| `{row['file'][:-len('CLAUDE.md')]}` | [`{row['file']}`]({row['file']}) — {row['says']} |\n", pairs)
            for row in instructions),
        "pack_owner_rows": "".join(f"| {row['topic']} | {row['owner']} |\n" for row in owners),
        # A pack's rules for the root CLAUDE.md's Features: they name its own check, so they come with it.
        "pack_feature_rules": "".join(f"{rule.rstrip()}\n" for rule in rules),
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
        "review_scope": params.get("review_scope") or "",
        "pack_instruction_rows": params.get("pack_instruction_rows") or "",
        "pack_owner_rows": params.get("pack_owner_rows") or "",
        "pack_feature_rules": params.get("pack_feature_rules") or "",
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
    for pack in packs:
        folder = template_root / PACKS_DIR / pack
        pack_sources += [(p.relative_to(folder).as_posix(), p) for p in sorted(folder.rglob("*"))
                         if p.is_file() and p.name not in (APPEND, REVIEW_SCOPE) and not CACHES & set(p.parts)]
    replaced = {rel for rel, _ in pack_sources}
    return [(rel, template_root / rel) for rel in copied if rel not in replaced] + pack_sources


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
                  review_scope=review_scope_block(template_root, packs), **pack_rows(manifest, packs, pairs))
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
                    handle.write(append.read_text(encoding="utf-8"))
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
    head = f"factory book init - template {manifest.get('version', 'none')} - {made} created, {kept} kept"
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
UNKNOWN = "UNKNOWN"      # differs from the new template, and no installed version to compare: kept
DECLINED = "DECLINED"    # the project's `[book] declined` names it: never written, nothing for a person
FOR_A_PERSON = (CONFLICT, REMOVED, DROPPED, UNKNOWN)
# A project's own content, never touched by upgrade even where a template pattern would reach it.
PROJECT_CONTENT = ("requirements/*", "docs/*")
TEMPLATE_DOCS = ("docs/plans/_template.md",)


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
    """The template repository at tag v<version>, unpacked into `scratch`; None and why when it cannot be."""
    tag = f"v{version}"
    code, _ = run(["git", "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"], template_root, 60)
    if code != 0:
        return None, f"the template repository has no tag {tag}"
    archive = scratch / "installed.tar"
    code, out = run(["git", "archive", "--format=tar", "-o", str(archive), tag], template_root, 300)
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
                        "(`factory book upgrade`).", line]
        text = newline.join(lines)
    path.write_bytes(text.encode("utf-8"))


def upgrade(target: Path, config: dict, manifest: dict, template_root: Path, apply: bool = False,
            run: Runner = real_runner) -> tuple[str, bool]:
    """
    The drift report (read-only) and, with `apply`, the update: every file the template owns is compared
    with the installed version and the new one; only files the project has not changed are written.
    """
    if target.resolve() == template_root.resolve():
        raise BookError("The template cannot upgrade itself: run `factory book upgrade --target <project>` from the "
                        "newer boilerplate's checkout.")
    if not (target / ".git").exists():
        raise BookError(f"{target} is not a git repository.")
    if not (target / CONFIG_RELATIVE).is_file():
        raise BookError(f"{target} has no {CONFIG_RELATIVE}: it holds no Book to upgrade (`factory book init` "
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
                    old_files = owned_files(tree, old_manifest, params, packs)
                    known = True
        else:
            notes.append(f"{COULD_NOT} the installed version: {where} - every file that differs from the new "
                         "template is kept, for a person")
    # A Book installed before init recorded them (D30) compares no pack files until a person adds them.
    unrecorded = [key for key in ("stack", "packs") if key not in (config.get("book") or {})]
    if unrecorded:
        notes.append(f"{WRONG} [book] has no {' or '.join(unrecorded)}: no pack file is compared or written - add "
                     "what this Book installed (`stack = \"dotnet\"`, `packs = [\"fe-guides\"]`), then run again")
    # A pack file the project chose not to carry (its own script does the job): `[book] declined`.
    declined = set((config.get("book") or {}).get("declined") or [])
    stale = sorted(declined - set(new_files) - set(old_files))
    if stale:
        notes.append(f"{WRONG} [book] declined names {len(stale)} file(s) the template does not own: "
                     f"{', '.join(stale)} - remove them from the list")
    rows: list[tuple[str, str]] = []
    diffs: list[str] = []
    unoffered: list[str] = []
    for rel in sorted(set(new_files) | set(old_files)):
        current = target / rel
        project = current.read_bytes() if current.is_file() else None
        status = DECLINED if rel in declined else classify(project, old_files.get(rel), new_files.get(rel), known)
        if status == NEW and known and rel.startswith(".claude/skills/") and is_rename_stub(new_files[rel]):
            # A stub keeps an old name working; a name the installed version never had needs none.
            unoffered.append(rel)
            continue
        rows.append((status, rel))
        if status in (BEHIND, NEW, UNKNOWN):
            diffs.append(diff(rel, project, new_files[rel], "project", f"template {new_version}"))
        elif status == CONFLICT:
            diffs.append(f"# {rel}: the project changed it, so it is kept. What the template changed, to "
                         "carry over by hand:\n" +
                         diff(rel, old_files[rel], new_files[rel], f"template {installed}", f"template {new_version}"))
    notes += plugin_notes(target, config, manifest, rows)
    notes += fe_guides_notes(target, config)
    if unoffered:
        notes.append(f"{len(unoffered)} rename stub(s) not offered: the installed version had none of these old "
                     f"names ({', '.join(sorted(rel.split('/')[2] for rel in unoffered))})")
    counts = {s: sum(r[0] == s for r in rows)
              for s in (CURRENT, BEHIND, NEW, CHANGED, CONFLICT, REMOVED, DROPPED, UNKNOWN, DECLINED)}
    mode = "--apply" if apply else "report only, nothing written (--apply updates the BEHIND and NEW files)"
    head = (f"factory book upgrade - installed {installed or 'unknown'}, template {new_version} - {mode}\n"
            + " · ".join(f"{n} {s.lower()}" for s, n in counts.items() if n))
    out = [head, *notes, ""] + [f"{status:<9} {rel}" for status, rel in rows]
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
        if created:
            code, text = run(["git", "add", "--", *created], target, 120)
            if code != 0:
                out.append(f"{WRONG} git add the new files: {(text.splitlines() or [''])[-1][:160]}")
            else:
                executable = executable_in_template(template_root, manifest, params, packs, run)
                bits = [rel for rel in created if rel in executable or rel.endswith(".sh")]
                if bits:
                    run(["git", "update-index", "--chmod=+x", "--", *bits], target, 120)
        stamp_version(target / CONFIG_RELATIVE, new_version)
        out += ["", f"Updated {len(written)}, created {len(created)} (staged); {CONFIG_RELATIVE} book_version "
                f"{installed or 'unknown'} -> {new_version}. Review `git diff` and commit."]
    kept = [(s, r) for s, r in rows if s in FOR_A_PERSON]
    if kept:
        out += ["", "For a person (never overwritten or deleted by upgrade):",
                *[f"  {s:<9} {r}" for s, r in kept]]
    if diffs:
        out += ["", "Diffs:", "", *diffs]
    return "\n".join(out).rstrip() + "\n", True


OLD_REVIEW_FOLDER = ".claude/skills/review-pr/references"
FRONTMATTER = re.compile(rb"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)


def is_rename_stub(content: bytes) -> bool:
    """A skill whose only job is to point an old name at its new one: its frontmatter, not its body, says
    `description: Renamed to ...` and `disable-model-invocation: true`."""
    head = FRONTMATTER.match(content)
    lines = head.group(1).splitlines() if head else []
    return (any(line.startswith(b"description: Renamed to ") for line in lines)
            and any(line.strip() == b"disable-model-invocation: true" for line in lines))

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
    A Book whose contracts list unversioned routes that nothing owns (D36): upgrade never writes factory.toml,
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


def plugin_notes(target: Path, config: dict, manifest: dict, rows: list[tuple[str, str]]) -> list[str]:
    """
    What a Book moving onto the book plugin (v1.3.0) must do by hand, since upgrade never writes a file the
    project owns or changed: enable the plugin, point [review_scope] at the plugin's checklist, and delete the
    old skill and agent files the plugin now provides (kept, DROPPED or CONFLICT, so nothing is lost unasked).
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
        # A pin older than this template's plugin_min lacks what the new files call (D39).
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
                     "`always` and `[review_scope.dimensions]` with the template's (the stack pack's "
                     "review-scope.toml, or skeletons/review-scope.toml) - the method, Pass A and B0 are the "
                     "plugin's `book:peer-review/references/` files now")
    retired = sorted(rel for status, rel in rows
                     if rel.startswith(PLUGIN_OWNED) and (status == DROPPED or
                                                          (status == CONFLICT and rel.endswith("/SKILL.md"))))
    if retired:
        notes.append(f"{WRONG} {len(retired)} old skill or agent file(s) the v1.3.0 rename retired are kept "
                     "(DROPPED or CONFLICT below, under .claude/skills/ and .claude/agents/; the plugin or a pack "
                     "skill's new name provides each now): once the plugin is enabled, carry any local edit over "
                     "and delete them; a CONFLICT SKILL.md takes the template's stub")
    return notes


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


def add_parser(sub) -> None:
    book = sub.add_parser("book", allow_abbrev=False,
                          help="the Book: verify it is set up (read-only), init what is absent, or upgrade it")
    book.add_argument("step", choices=["verify", "init", "upgrade"])
    book.add_argument("--target", default="", help="the repository; default: this one")
    book.add_argument("--params", default="", help="init: the parameter file (default <target>/book.toml)")
    book.add_argument("--no-verify", action="store_true", help="init: stop after creating; do not run verify")
    book.add_argument("--no-self-tests", action="store_true", help="verify: skip the self-test suites (reported, never passed)")
    book.add_argument("--apply", action="store_true",
                      help="upgrade: update the files the project has not changed, and bump book_version")


def main(root: Path, config_raw: dict, args, private: dict | None = None, run: Runner = real_runner) -> tuple[str, bool]:
    target = Path(args.target).resolve() if args.target else root
    manifest = load_manifest()
    same = target.resolve() == root.resolve()
    if args.step == "upgrade":
        return upgrade(target, load_target_config(target), manifest, HERE.parents[1], args.apply, run)
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
    print("Run it as `python3 devtools/factory/factory.py book verify`.", file=sys.stderr)
    sys.exit(2)
