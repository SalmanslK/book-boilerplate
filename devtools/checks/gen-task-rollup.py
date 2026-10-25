#!/usr/bin/env python3
"""
Generate the task roll-up in docs/project.md from each plan's task table.

A plan's Tasks section holds its work breakdown: one row per task, in build order, with the job
role it belongs to and its status (docs/plans/_template.md). docs/project.md shows three generated
tables, each between its markers: Task progress, one line per plan — how far it has got and what
comes next — Open tasks, every task not yet built across all plans, in progress first — and
Delivery pace, the tasks merged into the base branch each week, from each Built task's Merged date.
The plan is the only place a task's status is written; the roll-up is generated, so the two never
disagree.

    python3 devtools/checks/gen-task-rollup.py           # rewrite the roll-up
    python3 devtools/checks/gen-task-rollup.py --check   # fail if it is stale or a table is invalid

The check also fails a table with an unknown role or status, a missing column, or task numbers
that are not 1, 2, 3 … in order — and a plan that has lost the template's shape: a status line
naming its Owner, and a Tasks section, which a feature plan (one that opens with "1. What the
requirements say" or "1. What the FSD says") keeps as §9.

The roles come from devtools/factory/factory.toml:

    [book]
    our_role = "BE"                             # the role this team's progress is counted for, or a list
                                                # (["BE", "FE"] for a team that builds both halves)
    roles = ["BE", "FE", "QA", "BA", "DevOps"]  # every role a task may name

Both are optional; those are the defaults. Python 3.11+, standard library only.
"""
from __future__ import annotations

import os
import re
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLANS = ROOT / "docs" / "plans"
PROJECT = ROOT / "docs" / "project.md"
CONFIG = ROOT / "devtools" / "factory" / "factory.toml"

COLUMNS = ["#", "Task", "FSD", "Role", "Status", "Jira", "PR", "Merged"]
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_ROLES = ["BE", "FE", "QA", "BA", "DevOps"]
DEFAULT_OUR_ROLE = "BE"
STATUSES = ["Not started", "In progress", "Built", "Blocked"]
TASKS_HEADING = re.compile(r"^## (?:\d+\.\s+)?Tasks\s*$")
FEATURE_PLAN = re.compile(r"^## 1\. What the (?:FSD|requirements) says?\b")
OPEN_ORDER = {"In progress": 0, "Not started": 1, "Blocked": 2}


class Roles:
    """The [book] roles: which one is ours, and every one a task may name."""

    def __init__(self, ours: str | list[str] = DEFAULT_OUR_ROLE, every: list[str] | None = None,
                 base: str = "the base branch"):
        # One role, or several for a team that builds several halves.
        self.ours = [str(r) for r in ours] if isinstance(ours, list) else [str(ours)]
        self.label = ", ".join(self.ours)
        self.every = list(every or DEFAULT_ROLES)
        self.base = base
        for role in reversed(self.ours):
            if role not in self.every:
                self.every.insert(0, role)

    @classmethod
    def from_config(cls, path: Path = CONFIG) -> "Roles":
        if not path.is_file():
            return cls()
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        book = config.get("book", {})
        base = str(config.get("base_ref", "")).removeprefix("origin/") or "the base branch"
        return cls(book.get("our_role", DEFAULT_OUR_ROLE), [str(r) for r in book.get("roles", DEFAULT_ROLES)], base)


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


def task_table(path: Path, roles: Roles) -> tuple[list[dict[str, str]] | None, list[str]]:
    """The plan's task rows, or None when it has no task table; plus any problems found."""
    lines = path.read_text(encoding="utf-8").splitlines()
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
    if cells(lines[header]) != COLUMNS:
        return [], [f"{name}: the task table's columns must be {' | '.join(COLUMNS)}"]
    rows, problems = [], []
    for line in lines[header + 2:]:
        if not line.startswith("|"):
            break
        row = dict(zip(COLUMNS, cells(line)))
        number, role, status = plain(row.get("#", "")), plain(row.get("Role", "")), plain(row.get("Status", ""))
        if number != str(len(rows) + 1):
            problems.append(f"{name}: task {number or '?'} is out of order - expected {len(rows) + 1}")
        if role not in roles.every:
            problems.append(f"{name} task {number}: role {role!r} is not one of {', '.join(roles.every)}")
        if status not in STATUSES:
            problems.append(f"{name} task {number}: status {status!r} is not one of {', '.join(STATUSES)}")
        merged = plain(row.get("Merged", ""))
        if merged != "—" and not is_date(merged):
            problems.append(f"{name} task {number}: Merged {merged!r} is not a date (YYYY-MM-DD) or —")
            merged = "—"  # reported above; kept out of the pace table, which reads it as a date
        elif merged != "—" and status != "Built":
            problems.append(f"{name} task {number}: only a Built task has a Merged date")
        elif status == "Built" and re.search(r"#\d+", row.get("PR", "")) and merged == "—":
            problems.append(f"{name} task {number}: a Built task with a PR needs the date it merged into {roles.base}")
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
    elif any(FEATURE_PLAN.match(l) for l in lines) and tasks[0] != "## 9. Tasks":
        problems.append(f"{path.name}: a feature plan keeps its Tasks as \"## 9. Tasks\"")
    return problems


def title(path: Path) -> str:
    first = path.read_text(encoding="utf-8").splitlines()[0]
    return re.sub(r"^#\s*|\s*—\s*Design$", "", first).strip()


def summary(rows: list[dict[str, str]], roles: Roles) -> tuple[str, str, str]:
    ours = [r for r in rows if r["Role"] in roles.ours]
    built = sum(r["Status"] == "Built" for r in ours)
    progress = f"{built} of {len(ours)} built" if ours else "—"
    blocked = sum(r["Status"] == "Blocked" for r in ours)
    if blocked:
        progress += f", {blocked} blocked"
    upcoming = next((r for r in ours if r["Status"] in ("Not started", "In progress")), None)
    nxt = f"{upcoming['#']}. {plain(upcoming['Task']).split(' — ')[0]}" if upcoming else "—"
    others: dict[str, int] = {}
    for r in rows:
        if r["Role"] not in roles.ours and r["Status"] != "Built":
            others[r["Role"]] = others.get(r["Role"], 0) + 1
    waiting = " · ".join(f"{role} {others[role]}" for role in roles.every if role in others) or "—"
    return progress, nxt, waiting


def configured_paths(config: Path = CONFIG, root: Path = ROOT) -> tuple[Path, Path]:
    """
    The plans folder and the tracker as factory.toml names them (`[backlog] plans_dir`, `[requirements] tracker`),
    as the referee reads them; docs/plans and docs/project.md without either. A project that keeps them elsewhere
    got a roll-up of nothing.
    """
    raw = tomllib.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    plans = str((raw.get("backlog") or {}).get("plans_dir") or "docs/plans").rstrip("/")
    tracker = str((raw.get("requirements") or {}).get("tracker") or "docs/project.md")
    return root / plans, root / tracker


def render(plans: Path = PLANS, roles: Roles | None = None, tracker: Path = PROJECT) -> tuple[dict[str, str], list[str]]:
    """The three generated tables, keyed by their marker name; and any problems found. Each plan's link is written
    from the tracker's own folder."""
    roles = roles or Roles()
    progress_rows = [f"| Plan | {roles.label} tasks | Next {roles.label} task | Open for other roles |", "|---|---|---|---|"]
    open_tasks: list[tuple[int, str, int, str]] = []
    merged_tasks: list[dict[str, str]] = []
    problems: list[str] = []
    for path in sorted(plans.glob("*.md")) if plans.is_dir() else []:
        if path.name.startswith("_"):
            continue
        rows, found = task_table(path, roles)
        problems += shape_problems(path) + found
        if not rows:
            continue
        progress, nxt, waiting = summary(rows, roles)
        name = title(path)
        link = f"[{name}]({Path(os.path.relpath(path, tracker.parent)).as_posix()}#{anchor(path)})"
        progress_rows.append(f"| {link} | {progress} | {nxt.replace('|', '/')} | {waiting} |")
        merged_tasks += [r for r in rows if r["Merged"] != "—"]
        for r in rows:
            if r["Status"] in OPEN_ORDER:
                cells_ = [link, r["#"], r["Task"], r["Role"], r["Status"], r.get("Jira", "—")]
                number = plain(r["#"])
                open_tasks.append((OPEN_ORDER[r["Status"]], name, int(number) if number.isdigit() else 0,
                                   "| " + " | ".join(cells_) + " |"))
    if len(progress_rows) == 2:
        progress_rows.append("| *No plan has a task table yet* | | | |")
    open_rows = ["| Plan | # | Task | Role | Status | Jira |", "|---|---|---|---|---|---|"]
    open_rows += [row for *_, row in sorted(open_tasks)] or ["| *Nothing open* | | | | | |"]
    return {"task-rollup": "\n".join(progress_rows), "open-tasks": "\n".join(open_rows),
            "pace": pace(merged_tasks, roles)}, problems


def pace(merged: list[dict[str, str]], roles: Roles) -> str:
    """Tasks merged into the base branch per week (weeks start on Monday), and the running total."""
    weeks: dict[date, list[dict[str, str]]] = {}
    for row in merged:
        day = date.fromisoformat(row["Merged"])
        weeks.setdefault(day - timedelta(days=day.weekday()), []).append(row)
    lines = [f"| Week of | Tasks merged | {roles.label} | Running total |", "|---|---|---|---|"]
    total = 0
    for monday in sorted(weeks):
        rows = weeks[monday]
        total += len(rows)
        lines.append(f"| {monday.isoformat()} | {len(rows)} | {sum(r['Role'] in roles.ours for r in rows)} | {total} |")
    if len(lines) == 2:
        lines.append("| *No task has a merge date yet* | | | |")
    return "\n".join(lines)


def splice(text: str, marker: str, table: str, problems: list[str]) -> str:
    begin = next((l for l in text.splitlines() if l.startswith(f"<!-- {marker}:begin")), None)
    end = f"<!-- {marker}:end -->"
    if begin is None or end not in text:
        problems.append(f"docs/project.md: the {marker} markers are missing")
        return text
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{begin}\n{table}\n{end}{tail}"


def main(argv: list[str] | None = None, plans: Path | None = None, project: Path | None = None,
         config: Path = CONFIG) -> int:
    check = "--check" in (sys.argv[1:] if argv is None else argv)
    try:
        roles = Roles.from_config(config)
        where = configured_paths(config, config.parents[2])
    except (tomllib.TOMLDecodeError, OSError) as error:
        print(f"gen-task-rollup: cannot read {config}: {error}", file=sys.stderr)
        return 1
    plans, project = plans or where[0], project or where[1]
    if not project.is_file():
        print(f"gen-task-rollup: {project} does not exist", file=sys.stderr)
        return 1
    tables, problems = render(plans, roles, project)
    text = project.read_text(encoding="utf-8")
    updated = text
    for marker, table in tables.items():
        updated = splice(updated, marker, table, problems)
    for problem in problems:
        print(f"gen-task-rollup: {problem}", file=sys.stderr)
    if problems:
        return 1
    if updated == text:
        return 0
    if check:
        print(f"gen-task-rollup: {project.name}'s task progress, open tasks or delivery pace are stale - run "
              "`python3 devtools/checks/gen-task-rollup.py` and commit the result", file=sys.stderr)
        return 1
    project.write_text(updated, encoding="utf-8", newline="\n")
    print(f"gen-task-rollup: {project.name} updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
