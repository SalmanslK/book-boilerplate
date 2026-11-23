#!/usr/bin/env python3
"""
Generate the task roll-up in docs/project.md from each plan's task table.

A plan's Tasks section holds its work breakdown: one row per task, in build order, with the job role it
belongs to and its status (docs/plans/_template.md). docs/project.md shows three generated tables,
each between its markers: Task progress, one line per plan — how far it has got and what comes
next — Open tasks, every task not yet built across all plans, in progress first — and Delivery
pace, the tasks merged into dev each week, from each Built task's Merged date. The plan is
the only place a task's status is written; the roll-up is generated, so the two never disagree.

    python devtools/checks/gen-task-rollup.py           # rewrite the roll-up and the derived statuses
    python devtools/checks/gen-task-rollup.py --check   # fail if it is stale, a status disagrees, or a table is invalid

A feature's status follows from its back-end tasks too (the rule /book:progress applies): its plan's status
line says `Built` when every `BE` task is `Built`, `Moved` or `Dropped`, `In progress` when any is `Built` or `In
progress` and others are open, and `Not started` when none has started — a plan not yet started may
also say `In design` or `Blocked`. A plan with no `BE` task, or one whose status is `Removed`, keeps the
status it states. Each row of docs/project.md Part 1 that links to the plan shows the plan's status as
its Status cell's first code span. The write mode corrects the status word in both places and nothing
else on the line — the open items and the PR beside it stay a person's; the check fails on either.

`[book] derived_status_enforced` in devtools/factory/factory.toml switches that rule (on when the key is
absent): on, a status that disagrees with its tasks fails the check, and the write mode corrects it.
Off, such a status is only reported as a warning, neither failing the check nor written — the switch a
repository adopting this check flips on once its plans agree with their tasks.

`[book] roles` lists the job roles a task row may name, and `[book] our_role` the one this repository
builds (the "back-end" tasks above, which drive a plan's status). Absent or invalid, they fall back to
FALLBACK_ROLES and FALLBACK_OURS below; an `our_role` that is not in `roles` falls back to both.

A task carried into another plan stays at its number as `Moved`, linking to the plan it went to,
so every reference to its number still lands; it no longer counts in the plan's progress or as
open. A task taken out of scope stays at its number as `Dropped`, naming the decision, and counts
the same way: neither progress nor open. The check also fails a table with an unknown role or status, a missing column, task numbers
that are not 1, 2, 3 … in order, a `Moved` task with no link to another plan — and a plan that
has lost the template's shape: a status line
naming its Owner, and a Tasks section, which a feature plan (one that opens with "1. What the
FSD says") keeps as §9.

A task row keeps its number and is never deleted: `--check` compares each plan with its version on
origin/dev and fails a task number that was there and is gone, or a table with fewer rows. A row
may change its status or its text; a plan origin/dev does not have is new and is not compared.
Without origin/dev (no remote, not fetched, not a git repository) it says the comparison could not
run, and skips it. This rule is enforced whatever derived_status_enforced says.
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
# The settings: factory.toml merged over the Book's defaults (devtools/factory/book_config.py).
_FACTORY = next((p / "devtools" / "factory" for p in Path(__file__).resolve().parents
                 if (p / "devtools" / "factory" / "book_config.py").is_file()), None)
if _FACTORY is not None:
    sys.path.insert(0, str(_FACTORY))
import book_config  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PLANS = ROOT / "docs" / "plans"
PROJECT = ROOT / "docs" / "project.md"

COLUMNS = ["#", "Task", "FSD", "Role", "Status", "Jira", "PR", "Merged"]
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The fallbacks when factory.toml sets no `[book] roles` / `our_role` (the module docstring).
FALLBACK_ROLES = ["BE", "FE", "QA", "BA", "DevOps"]
STATUSES = ["Not started", "In progress", "Built", "Blocked", "Moved", "Dropped"]
MOVED = "Moved"
DROPPED = "Dropped"
SET_ASIDE = (MOVED, DROPPED)  # rows that count neither in progress nor as open
MOVED_TO = re.compile(r"\]\(([^)\s#]+\.md)#[^)\s]*\)")
FALLBACK_OURS = "BE"
# The fallback when factory.toml sets no `[book] derived_status_enforced`: on (the module docstring).
DERIVED_STATUS_ENFORCED = True
TASKS_HEADING = re.compile(r"^## (?:\d+\.\s+)?Tasks\s*$")


def cells(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def is_date(text: str) -> bool:
    """YYYY-MM-DD and a real day: 2026-13-45 has the shape but no calendar holds it."""
    if not DATE.match(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def plain(text: str) -> str:
    """A cell's text without Markdown emphasis or code ticks."""
    return re.sub(r"[`*]", "", text).strip()


def moved_to_another_plan(path: Path, task: str) -> bool:
    """Whether a Moved task's text links to another plan beside this one that holds a task table."""
    for link in MOVED_TO.findall(task):
        target = (path.parent / link).resolve()
        if target != path.resolve() and target.parent == path.parent.resolve() and has_task_table(target):
            return True
    return False


def has_task_table(path: Path) -> bool:
    """A Tasks heading followed by the task table's header — read without validating its rows."""
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, l in enumerate(lines) if TASKS_HEADING.match(l)), None)
    if start is None:
        return False
    for line in lines[start + 1:]:
        if line.startswith("## "):
            return False
        if line.startswith("|") and cells(line)[:2] == ["#", "Task"]:
            return True
    return False


def task_table(path: Path, text: str | None = None) -> tuple[list[dict[str, str]] | None, list[str]]:
    """
    The plan's task rows, or None when it has no task table; plus any problems found. `text` reads
    another version of the plan — origin/dev's — in place of the file.
    """
    lines = (path.read_text(encoding="utf-8") if text is None else text).splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if TASKS_HEADING.match(l))
    except StopIteration:
        return None, []
    header = None
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            break
        if lines[i].startswith("|") and cells(lines[i])[:2] == ["#", "Task"]:
            header = i
            break
    if header is None:
        return None, []
    name = path.name
    heads = cells(lines[header])
    if heads != COLUMNS:
        return [], [f"{name}: the task table's columns must be {' | '.join(COLUMNS)}"]
    rows, problems = [], []
    for line in lines[header + 2:]:
        if not line.startswith("|"):
            break
        row = dict(zip(COLUMNS, cells(line)))
        number, role, status = plain(row.get("#", "")), plain(row.get("Role", "")), plain(row.get("Status", ""))
        if number != str(len(rows) + 1):
            problems.append(f"{name}: task {number or '?'} is out of order - expected {len(rows) + 1}")
        if role not in ROLES:
            problems.append(f"{name} task {number}: role {role!r} is not one of {', '.join(ROLES)}")
        if status not in STATUSES:
            problems.append(f"{name} task {number}: status {status!r} is not one of {', '.join(STATUSES)}")
        elif status == MOVED and not moved_to_another_plan(path, row.get("Task", "")):
            problems.append(f"{name} task {number}: a Moved task links to the plan task it moved to - "
                            "another plan in this folder, with a task table")
        merged = plain(row.get("Merged", ""))
        if merged != "—" and not is_date(merged):
            problems.append(f"{name} task {number}: Merged {merged!r} is not a date (YYYY-MM-DD) or —")
            merged = "—"  # reported above; kept out of the pace table, which reads it as a date
        elif merged != "—" and status != "Built":
            problems.append(f"{name} task {number}: only a Built task has a Merged date")
        elif status == "Built" and re.search(r"#\d+", row.get("PR", "")) and merged == "—":
            problems.append(f"{name} task {number}: a Built task with a PR needs the date it merged into dev")
        rows.append({**row, "Role": role, "Status": status, "Merged": merged})
    if not rows:
        problems.append(f"{name}: the task table has no rows")
    return rows, problems


def anchor(path: Path) -> str:
    """The GitHub-style anchor of the plan's Tasks heading."""
    heading = next(l for l in path.read_text(encoding="utf-8").splitlines() if TASKS_HEADING.match(l))
    return re.sub(r"[^a-z0-9 -]", "", heading[3:].strip().lower()).replace(" ", "-")


def shape_problems(path: Path) -> list[str]:
    """What the plan template requires of every plan (docs/plans/_template.md)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    problems = []
    status = next((l for l in lines[:12] if l.startswith("**Status:**")), None)
    if status is None:
        problems.append(f"{path.name}: no status line near the top")
    elif "**Owner:**" not in status:
        problems.append(f"{path.name}: the status line names no Owner")
    tasks = [l for l in lines if TASKS_HEADING.match(l)]
    if not tasks:
        problems.append(f"{path.name}: no Tasks section")
    elif any(l.startswith("## 1. What the FSD says") for l in lines) and tasks[0] != "## 9. Tasks":
        problems.append(f"{path.name}: a feature plan keeps its Tasks as \"## 9. Tasks\"")
    return problems


def is_removed(path: Path) -> bool:
    """Whether the plan's status line says `Removed`: its feature is no longer in the product."""
    lines = path.read_text(encoding="utf-8").splitlines()
    status = next((l for l in lines[:12] if l.startswith("**Status:**")), "")
    return status.removeprefix("**Status:**").strip().startswith("`Removed`")


def title(path: Path) -> str:
    first = path.read_text(encoding="utf-8").splitlines()[0]
    return re.sub(r"^#\s*|\s*—\s*Design$", "", first).strip()


def summary(rows: list[dict[str, str]]) -> tuple[str, str, str]:
    rows = [r for r in rows if r["Status"] not in SET_ASIDE]
    ours = [r for r in rows if r["Role"] == OURS]
    built = sum(r["Status"] == "Built" for r in ours)
    progress = f"{built} of {len(ours)} built" if ours else "—"
    blocked = sum(r["Status"] == "Blocked" for r in ours)
    if blocked:
        progress += f", {blocked} blocked"
    upcoming = next((r for r in ours if r["Status"] in ("Not started", "In progress")), None)
    nxt = f"{upcoming['#']}. {plain(upcoming['Task']).split(' — ')[0]}" if upcoming else "—"
    others: dict[str, int] = {}
    for r in rows:
        if r["Role"] != OURS and r["Status"] != "Built":
            others[r["Role"]] = others.get(r["Role"], 0) + 1
    waiting = " · ".join(f"{role} {others[role]}" for role in ROLES if role in others) or "—"
    return progress, nxt, waiting


PLAN_STATUSES = ["Not started", "In design", "In progress", "Built", "Blocked", "Removed"]
STATUS_WORD = re.compile(r"^\*\*Status:\*\*\s*(`?)(" + "|".join(PLAN_STATUSES) + r")\1")
NOT_YET_STARTED = {"Not started", "In design", "Blocked"}
PLAN_LINK = re.compile(r"\(plans/([^)#\s/]+\.md)[#)]")
CELL_STATUS = re.compile(r"^\s*`([^`]+)`")


def derived_status(rows: list[dict[str, str]]) -> str | None:
    """The plan's status its BE tasks give (the module docstring); None when it has no BE task."""
    ours = [r["Status"] for r in rows if r["Role"] == OURS]
    if not ours:
        return None
    if all(s in ("Built", *SET_ASIDE) for s in ours):
        return "Built"
    if any(s in ("Built", "In progress") for s in ours):
        return "In progress"
    return "Not started"


def status_line(path: Path) -> tuple[int, re.Match | None]:
    """The status line's index in the plan, and the match of its status word (None when it has none)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    index = next((i for i, l in enumerate(lines[:12]) if l.startswith("**Status:**")), -1)
    return index, (STATUS_WORD.match(lines[index]) if index >= 0 else None)


def plan_statuses(plans: Path) -> tuple[dict[str, str], list[tuple[Path, int, str, str]], list[str]]:
    """
    Each plan's status as its tasks give it, by file name; the status lines to correct (path, line,
    stated, derived); and the problems — a status line whose word is none of PLAN_STATUSES.
    """
    final: dict[str, str] = {}
    fixes: list[tuple[Path, int, str, str]] = []
    problems: list[str] = []
    for path in sorted(plans.glob("*.md")):
        if path.name.startswith("_"):
            continue
        rows, _ = task_table(path)
        index, word = status_line(path)
        if index < 0 or not rows:
            continue  # shape_problems and task_table report these
        if word is None:
            problems.append(f"{path.name}: the status line does not open with one of {', '.join(PLAN_STATUSES)}")
            continue
        stated = word.group(2)
        derived = None if stated == "Removed" else derived_status(rows)
        if derived is None or stated == derived or (derived == "Not started" and stated in NOT_YET_STARTED):
            final[path.name] = stated
        else:
            final[path.name] = derived
            fixes.append((path, index, stated, derived))
    return final, fixes, problems


def part1_bounds(lines: list[str]) -> tuple[int, int]:
    """Where docs/project.md's Part 1 starts and ends (the next top-level heading); (0, 0) without one."""
    start = next((i for i, l in enumerate(lines) if l.startswith("# Part 1")), None)
    if start is None:
        return 0, 0
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("# ")), len(lines))
    return start, end


def part1_rows(lines: list[str]) -> list[tuple[int, int, str, str | None]]:
    """
    Part 1's feature rows that link to a plan: (line, Status column, plan file, the Status cell's
    status — None when the cell opens with no code span). The generated tables are skipped.
    """
    start, end = part1_bounds(lines)
    found = []
    generated = False
    columns: tuple[int, int] | None = None
    for i in range(start, end):
        line = lines[i]
        if line.startswith("<!--") and ":begin" in line:
            generated = True
        if line.startswith("<!--") and ":end" in line:
            generated = False
            continue
        if generated or not line.startswith("|"):
            columns = None
            continue
        row = cells(line)
        if columns is None:
            columns = (row.index("Plan"), row.index("Status")) if "Plan" in row and "Status" in row else (-1, -1)
            continue
        plan_col, status_col = columns
        if plan_col < 0 or set(line) <= set("|-: ") or len(row) <= max(plan_col, status_col):
            continue
        link = PLAN_LINK.search(row[plan_col])
        if link:
            stated = CELL_STATUS.match(row[status_col])
            found.append((i, status_col, link.group(1), stated.group(1) if stated else None))
    return found


def with_status(line: str, column: int, old: str, new: str) -> str:
    """The table row with its Status cell's first code span changed from `old` to `new`."""
    parts = re.split(r"(?<!\\)\|", line)
    parts[column + 1] = parts[column + 1].replace(f"`{old}`", f"`{new}`", 1)
    return "|".join(parts)


def status_corrections(plans: Path, text: str) -> tuple[list[str], dict[Path, list[str]], str, list[str]]:
    """
    What the derived statuses change: one message per change; each plan's corrected lines; the
    project text with Part 1 corrected; and the problems — a Part 1 Status cell with no status.
    """
    final, fixes, problems = plan_statuses(plans)
    messages: list[str] = []
    plan_lines: dict[Path, list[str]] = {}
    for path, index, stated, derived in fixes:
        lines = plan_lines.setdefault(path, path.read_text(encoding="utf-8").splitlines())
        word = STATUS_WORD.match(lines[index])
        lines[index] = lines[index][:word.start(2)] + derived + lines[index][word.end(2):]
        messages.append(f"{path.name}: status line {stated} -> {derived}, from its BE tasks")
    lines = text.splitlines()
    for i, column, plan, stated in part1_rows(lines):
        if plan not in final:
            continue
        if stated is None:
            problems.append(f"docs/project.md Part 1: the row for {plan} has no `status` opening its Status cell")
        elif stated != final[plan]:
            lines[i] = with_status(lines[i], column, stated, final[plan])
            messages.append(f"docs/project.md Part 1: {plan} {stated} -> {final[plan]}, as its plan")
    corrected = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return messages, plan_lines, corrected if messages else text, problems


OPEN_ORDER = {"In progress": 0, "Not started": 1, "Blocked": 2}


def render(plans: Path = PLANS) -> tuple[dict[str, str], list[str]]:
    """The two generated tables, keyed by their marker name; and any problems found."""
    progress_rows = ["| Plan | Back-end tasks | Next back-end task | Open for other roles |", "|---|---|---|---|"]
    open_tasks: list[tuple[int, str, int, str]] = []
    merged_tasks: list[dict[str, str]] = []
    problems: list[str] = []
    for path in sorted(plans.glob("*.md")):
        if path.name.startswith("_"):
            continue
        rows, found = task_table(path)
        problems += shape_problems(path) + found
        if not rows:
            continue
        if is_removed(path):
            # A removed feature's tasks were delivered, then taken out of the product: they still
            # count in the delivery pace, and no longer in the progress or open tables.
            merged_tasks += [r for r in rows if r["Merged"] != "—"]
            continue
        progress, nxt, waiting = summary(rows)
        name = title(path)
        link = f"[{name}](plans/{path.name}#{anchor(path)})"
        progress_rows.append(f"| {link} | {progress} | {nxt.replace('|', '/')} | {waiting} |")
        merged_tasks += [r for r in rows if r["Merged"] != "—"]
        for r in rows:
            if r["Status"] in OPEN_ORDER:
                cells_ = [link, r["#"], r["Task"], r["Role"], r["Status"], r.get("Jira", "—")]
                open_tasks.append((OPEN_ORDER[r["Status"]], name, int(plain(r["#"])), "| " + " | ".join(cells_) + " |"))
    if len(progress_rows) == 2:
        progress_rows.append("| *No plan has a task table yet* | | | |")
    open_rows = ["| Plan | # | Task | Role | Status | Jira |", "|---|---|---|---|---|---|"]
    open_rows += [row for *_, row in sorted(open_tasks)] or ["| *Nothing open* | | | | | |"]
    return {"task-rollup": "\n".join(progress_rows), "open-tasks": "\n".join(open_rows),
            "pace": pace(merged_tasks)}, problems


def pace(merged: list[dict[str, str]]) -> str:
    """Tasks merged into dev per week (weeks start on Monday), and the running total."""
    weeks: dict[date, list[dict[str, str]]] = {}
    for row in merged:
        day = date.fromisoformat(row["Merged"])
        weeks.setdefault(day - timedelta(days=day.weekday()), []).append(row)
    lines = ["| Week of | Tasks merged | Back end | Running total |", "|---|---|---|---|"]
    total = 0
    for monday in sorted(weeks):
        rows = weeks[monday]
        total += len(rows)
        lines.append(f"| {monday.isoformat()} | {len(rows)} | {sum(r['Role'] == OURS for r in rows)} | {total} |")
    if len(lines) == 2:
        lines.append("| *No task has a merge date yet* | | | |")
    return "\n".join(lines)


# The ledgers' counts (check-book.py reads the ledgers; this writes what it counts). A project.md from before
# Book 1.6 has no `ledgers` markers: the counts are left out there rather than failing, until they are added.
OPTIONAL_MARKERS = {"ledgers"}


def ledger_counts(root: Path) -> str:
    """The open questions per State and the open defects by Type and by Source, as one table each."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_book", Path(__file__).with_name("check-book.py"))
    check_book = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check_book)
    states = check_book.question_counts(root)
    types, sources = check_book.defect_counts(root)

    def table(title: str, counts: dict[str, int]) -> list[str]:
        rows = [f"| {name} | {count} |" for name, count in counts.items() if count]
        return [f"| {title} | Open |", "|---|---|"] + (rows or ["| *None* | 0 |"])
    lines = table("Question state", states) + [""] + table("Defect type", dict(sorted(types.items()))) \
        + [""] + table("Defect source", dict(sorted(sources.items())))
    sys.path.insert(0, str(Path(__file__).parent))
    import requirements_register
    register = requirements_register.counts(root)
    if register:
        statuses = [s for s in requirements_register.STATUSES if any(s in c for c in register.values())]
        lines += ["", "| Requirements | " + " | ".join(statuses) + " |", "|---|" + "---|" * len(statuses)]
        lines += [f"| {module} | " + " | ".join(str(counts.get(s, 0)) for s in statuses) + " |"
                  for module, counts in sorted(register.items())]
    return "\n".join(lines)


def splice(text: str, marker: str, table: str, problems: list[str]) -> str:
    begin = next((l for l in text.splitlines() if l.startswith(f"<!-- {marker}:begin")), None)
    end = f"<!-- {marker}:end -->"
    if begin is None or end not in text:
        if marker not in OPTIONAL_MARKERS:
            problems.append(f"docs/project.md: the {marker} markers are missing")
        return text
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{begin}\n{table}\n{end}{tail}"


def configured_base(root: Path) -> str:
    """`base_ref` in devtools/factory/factory.toml (origin/dev when it names none): the branch work merges into."""
    try:
        return str(book_config.load(root).get("base_ref") or "origin/dev")
    except (OSError, ImportError, ValueError):
        return "origin/dev"


BASE = configured_base(ROOT)


def configured_roles(root: Path) -> tuple[list[str], str]:
    """
    `[book] roles` and `[book] our_role` in devtools/factory/factory.toml. FALLBACK_ROLES and FALLBACK_OURS when the file
    or a key is missing, `roles` is not a list of non-empty strings, or `our_role` is not one of them.
    """
    try:
        book = book_config.load(root).get("book") or {}
        roles, ours = book.get("roles", FALLBACK_ROLES), book.get("our_role", FALLBACK_OURS)
    except (OSError, ImportError, ValueError, AttributeError):
        return list(FALLBACK_ROLES), FALLBACK_OURS
    if not (isinstance(roles, list) and roles and all(isinstance(r, str) and r.strip() for r in roles)):
        roles = list(FALLBACK_ROLES)
    if not isinstance(ours, str) or ours not in roles:
        return list(FALLBACK_ROLES), FALLBACK_OURS
    return list(roles), ours


ROLES, OURS = configured_roles(ROOT)


def configured_enforced(root: Path) -> bool:
    """
    `[book] derived_status_enforced` in devtools/factory/factory.toml: whether a plan's status that
    disagrees with its tasks fails. DERIVED_STATUS_ENFORCED when the file, the key or a true/false
    value is missing.
    """
    try:
        value = (book_config.load(root).get("book") or {}).get("derived_status_enforced")
    except (OSError, ImportError, ValueError, AttributeError):
        return DERIVED_STATUS_ENFORCED
    return value if isinstance(value, bool) else DERIVED_STATUS_ENFORCED


def git(plans: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(plans), *args], capture_output=True, text=True, encoding="utf-8")


def lost_tasks(plans: Path = PLANS, base: str = BASE) -> tuple[list[str], str | None]:
    """
    Task rows a plan had on `base` and has lost: a number that is gone, or fewer rows. Returns the
    problems, and a notice when the comparison could not run (no `base` to compare with).
    """
    if git(plans, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode != 0:
        return [], (f"could not compare the task tables with {base} (not fetched, or not a git repository) - "
                    "the rule that a task row is never deleted was not checked")
    problems: list[str] = []
    for path in sorted(plans.glob("*.md")):
        if path.name.startswith("_"):
            continue
        before = git(plans, "show", f"{base}:./{path.name}")
        if before.returncode != 0:
            continue  # a new plan: nothing to compare with
        old, _ = task_table(path, before.stdout)
        if not old:
            continue
        new, _ = task_table(path)
        new = new or []
        numbers = {plain(r.get("#", "")) for r in new}
        for row in old:
            number = plain(row.get("#", ""))
            if number not in numbers:
                problems.append(f"{path.name}: task {number} is on {base} and gone here - a task row keeps its "
                                "number and is never deleted (mark it Dropped or Moved instead)")
        if len(new) < len(old):
            problems.append(f"{path.name}: {len(new)} task rows where {base} has {len(old)} - a task row is never "
                            "deleted")
    return problems, None


def main(argv: list[str] | None = None, plans: Path = PLANS, project: Path = PROJECT,
         enforced: bool | None = None) -> int:
    """`enforced` None reads `[book] derived_status_enforced` from this repository's factory.toml."""
    check = "--check" in (sys.argv[1:] if argv is None else argv)
    if enforced is None:
        enforced = configured_enforced(ROOT)
    tables, problems = render(plans)
    if check:
        lost, notice = lost_tasks(plans)
        problems += lost
        if notice:
            print(f"gen-task-rollup: {notice}", file=sys.stderr)
    text = project.read_text(encoding="utf-8")
    changes, plan_lines, updated, status_problems = status_corrections(plans, text)
    problems += status_problems
    if not enforced:
        for change in changes:
            print("gen-task-rollup: warning, not enforced ([book] derived_status_enforced = false): "
                  f"{change.replace(' -> ', ' should be ')}", file=sys.stderr)
        changes, plan_lines, updated = [], {}, text
    tables["ledgers"] = ledger_counts(project.parent.parent)
    for marker, table in tables.items():
        updated = splice(updated, marker, table, problems)
    for problem in problems:
        print(f"gen-task-rollup: {problem}", file=sys.stderr)
    if problems:
        return 1
    if updated == text and not plan_lines:
        return 0
    if check:
        for change in changes:
            print(f"gen-task-rollup: {change.replace(' -> ', ' should be ')}", file=sys.stderr)
        print("gen-task-rollup: docs/project.md's generated tables, or a status derived from a plan's tasks, are "
              "stale - run `python devtools/checks/gen-task-rollup.py` and commit the result", file=sys.stderr)
        return 1
    for path, lines in plan_lines.items():
        original = path.read_text(encoding="utf-8")
        path.write_text("\n".join(lines) + ("\n" if original.endswith("\n") else ""), encoding="utf-8")
    for change in changes:
        print(f"gen-task-rollup: {change}")
    if updated != text:
        project.write_text(updated, encoding="utf-8")
        print("gen-task-rollup: docs/project.md updated")
    return 0


if __name__ == "__main__":
    # UTF-8 out, so an em dash prints on a Windows console's code page too (Book D45).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
