#!/usr/bin/env python3
"""
What dev says about task progress: the evidence /book:progress updates the plans' task lists from.

Lists every pull request merged into dev since the last progress update — its branch, the plans
it changed, the D, Q and Jira ids it carries, and the task rows that already cite it — and the
work branches not yet merged, which are the only evidence a task is in progress. It reads git
only, from the remote dev, never the working tree, so `Built` means merged into dev and nothing
else. It changes nothing.

    python devtools/checks/task-evidence.py                 # since the last change to docs/project.md on dev
    python devtools/checks/task-evidence.py --since <ref>   # since any commit
    python devtools/checks/task-evidence.py --all           # every merged pull request

Run `git fetch origin` first; the report says which dev commit it read.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path
# The settings: factory.toml merged over the Book's defaults (devtools/factory/book_config.py).
_FACTORY = next((p / "devtools" / "factory" for p in Path(__file__).resolve().parents
                 if (p / "devtools" / "factory" / "book_config.py").is_file()), None)
if _FACTORY is not None:
    sys.path.insert(0, str(_FACTORY))
import book_config  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
def configured_base(root: Path) -> str:
    """`base_ref` in devtools/factory/factory.toml (origin/dev when it names none): the branch work merges into."""
    try:
        return str(book_config.load(root).get("base_ref") or "origin/dev")
    except (OSError, ImportError, ValueError):
        return "origin/dev"


DEV = configured_base(ROOT)
MERGED = re.compile(r"^Merged in (?P<branch>\S+) \(pull request #(?P<pr>\d+)\)")
def _jira_key() -> str:
    try:
        return str((book_config.load(ROOT).get("jira") or {}).get("project") or "")
    except (OSError, ImportError, ValueError):
        return ""


# Our D and Q ids, and the [jira] project's issue keys when one is set.
IDS = re.compile(r"\b(?:D\d+|Q\d+" + (rf"|{re.escape(_jira_key())}-\d+" if _jira_key() else "") + r")\b")
WORK_BRANCH = re.compile(r"^origin/(feature|fix|chore)/")

_spec = importlib.util.spec_from_file_location("rollup", Path(__file__).with_name("gen-task-rollup.py"))
rollup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rollup)


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def plan_tasks_at(ref: str) -> dict[str, list[dict[str, str]]]:
    """Every plan's task rows as dev has them — read from git, not the working tree."""
    tasks = {}
    for path in git("ls-tree", "--name-only", f"{ref}:docs/plans").split():
        if not path.endswith(".md") or path.startswith("_"):
            continue
        text = git("show", f"{ref}:docs/plans/{path}")
        # The system temp folder, not .git/: in a worktree .git is a file.
        tmp = Path(tempfile.gettempdir()) / "task-evidence.tmp.md"
        tmp.write_text(text, encoding="utf-8")
        try:
            rows, _ = rollup.task_table(tmp)
        finally:
            tmp.unlink()
        tasks[path] = rows or []
    return tasks


def main() -> int:
    args = sys.argv[1:]
    head = git("rev-parse", "--short", DEV).strip()
    if "--all" in args:
        since = None
    elif "--since" in args:
        since = args[args.index("--since") + 1]
    else:
        since = git("log", "-1", "--first-parent", "--format=%h", DEV, "--", "docs/project.md").strip() or None
    span = f"{since}..{DEV}" if since else DEV
    tasks = plan_tasks_at(DEV)
    has_tasks = any(tasks.values())

    print(f"# Task evidence from `{DEV}` at `{head}`" + (f", merged since `{since}`" if since else ""))
    if not has_tasks:
        print("\n> dev's plans have no task tables yet — every merge below is evidence for the first lists.")

    print("\n## Pull requests merged into dev\n")
    print("| PR | Merged | Branch | Plans changed | Ids | Task rows citing it |")
    print("|---|---|---|---|---|---|")
    count = 0
    for line in git("log", "--first-parent", "--merges", "--format=%H\t%ad\t%s", "--date=short", span).splitlines():
        sha, date, subject = line.split("\t", 2)
        m = MERGED.match(subject)
        if not m:
            continue
        count += 1
        pr, branch = m["pr"], m["branch"]
        files = git("diff", "--name-only", f"{sha}^1", sha).split()
        plans = sorted(Path(f).name for f in files if f.startswith("docs/plans/") and not Path(f).name.startswith("_"))
        messages = git("log", "--format=%s%n%b", f"{sha}^1..{sha}^2")
        ids = sorted(set(IDS.findall(branch + "\n" + messages)), key=lambda i: (i[0], int(re.sub(r"\D", "", i))))
        citing = [f"{plan} {r['#']} ({r['Status']})" for plan, rows in tasks.items() for r in rows
                  if re.search(rf"#{pr}\b", r.get("PR", ""))]
        print(f"| #{pr} | {date} | `{branch}` | {', '.join(plans) or '—'} | {' '.join(ids[:12]) or '—'} | "
              f"{'; '.join(citing) or '**none** — find its task, or say it has none'} |")
    if not count:
        print("| — | | *nothing merged in this span* | | | |")

    print("\n## Work branches not merged into dev\n")
    print("A task is `In progress` only with a branch here; a branch quiet for weeks is not progress.\n")
    print("| Branch | Last commit | Ids |")
    print("|---|---|---|")
    for ref in git("branch", "-r", "--no-merged", DEV, "--format=%(refname:short)\t%(committerdate:short)").splitlines():
        name, date = ref.split("\t")
        if WORK_BRANCH.match(name):
            print(f"| `{name}` | {date} | {' '.join(sorted(set(IDS.findall(name)))) or '—'} |")

    stale = [f"{plan} {r['#']}" for plan, rows in tasks.items() for r in rows
             if r["Status"] == "Built" and plain_pr(r) is None]
    if stale:
        print("\n## Built tasks with no PR\n")
        print("Fill the PR column where the merge is known: " + ", ".join(stale))
    return 0


def plain_pr(row: dict[str, str]) -> str | None:
    m = re.search(r"#\d+", row.get("PR", ""))
    return m.group(0) if m else None


if __name__ == "__main__":
    # UTF-8 out, so an em dash prints on a Windows console's code page too (Book D45).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
