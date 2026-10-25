"""Every run of a project, on every branch and worktree, as the Factory Console's pages show them.

Read only. Nothing here runs the referee or writes a file: it reads run files through git
(`git cat-file --batch` on every local and fetched branch) and from each worktree's folder,
keeps each run's most advanced copy, and turns its Record and sections into the fields the Runs
list and a run's own page need — the step spine, the intent, each criterion with its red and
green proof, the checks with their evidence, the gates, the reviewer's and the requirements
auditor's findings, the cost per step and the run's history.

Generic: the runs folder and the tracker come from the project's `devtools/factory/factory.toml`;
there are no project names, paths or run ids in this file. A dry run is a run whose branch starts
with one of `dry_prefixes` (default `runs/`), or whose id the caller lists in `dry_ids`.

Cost is what the CLI reports as `total_cost_usd`: API list price, not what a subscription bills.

Python 3.11+, standard library only.
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import posixpath
import re
import subprocess
import tomllib
from pathlib import Path

FACTORY_TOML = Path("devtools") / "factory" / "factory.toml"
DEFAULT_RUNS_DIR = "docs/factory/runs"
DEFAULT_BASE_REF = "origin/dev"
DEFAULT_DRY_PREFIXES = ("runs/",)
RUN_FILE_NAME = re.compile(r"^R-\d{3}\.md$")
FENCE = "```json factory-record"
TITLE_LINE = re.compile(r"^# (R-\d+) — (.+)$")
STEPS = [
    ("intake", "Intake"), ("context", "Context"), ("spec", "Spec"), ("spec-gate", "Spec gate"),
    ("design", "Design"), ("design-gate", "Design gate"), ("plan", "Plan"), ("tests", "Tests"),
    ("build", "Build"), ("integrate", "Integrate"), ("verify", "Verify"), ("review", "Review"),
    ("accept", "Accept"), ("package", "Package"), ("merge-gate", "Merge gate"), ("release", "Release"),
    ("outcome", "Outcome"),
]
STEP_NAMES = [name for name, _ in STEPS]
# A plan run (`plan:<feature-slug>`, the referee's `plan` kind) has steps of its own; a Record without
# `kind` is a change run, as the referee reads it.
PLAN_STEPS = [
    ("intake", "Intake"), ("context", "Context"), ("sources", "Sources"), ("draft-plan", "Draft plan"),
    ("plan-gate", "Plan gate"), ("package", "Package"), ("merge-gate", "Merge gate"),
]
# A book run (`book:<name>`): project.md written from the newest FSD. The outline gate is skipped when the
# FSD is not a PDF (the referee's `applicable`); a book run ends at package, with no merge gate.
BOOK_STEPS = [
    ("outline", "Outline"), ("outline-gate", "Outline gate"), ("inventory", "Inventory"), ("features", "Features"),
    ("audit", "Audit"), ("book-gate", "Book gate"), ("package", "Package"),
]
# A revision run (`revision:V<n>`): a new FSD version taken into the book; it ends at package, as a book run does.
REVISION_STEPS = [
    ("changes", "Changes"), ("triage", "Triage"), ("audit", "Audit"), ("revision-gate", "Revision gate"), ("package", "Package"),
]
STEPS_BY_KIND = {"change": STEPS, "plan": PLAN_STEPS, "book": BOOK_STEPS, "revision": REVISION_STEPS}
GATES = {"spec-gate", "design-gate", "plan-gate", "outline-gate", "book-gate", "revision-gate", "merge-gate"}
AC_LINE = re.compile(r"^\s*-\s*\*\*(?P<id>[^*]+)\*\*\s*(?P<guard>\*\(guard\)\*)?\s*[—–-]+\s*(?P<text>.+)$")
TOKEN_KINDS = ("input", "output", "cache_read", "cache_write")
SECTIONS_SHOWN = ("Intent", "Context", "Spec", "Design", "Plan", "Requirements audit", "Review", "Acceptance",
                  "Package", "Release", "Outcome", "Escalation", "Outline", "Inventory", "Features", "Audit",
                  "Changes", "Triage", "Answered", "New questions", "Flagged plans", "Change items")
# A book run's Inventory line and audit report, read as the referee reads them (factory.py INVENTORY_LINE,
# DISPOSITIONS, VERDICT): one line per FSD section, and `Verdict:`, `Findings:`, `Coverage:`, `What is sound:`.
INVENTORY_LINE = re.compile(r"^\s*-\s*§(?P<id>[A-Za-z0-9.]+?)\.?\s+(?P<rest>.+)$")
DISPOSITIONS = ("feature", "part-of", "cross-cutting", "not-a-feature", "empty")
BOOK_VERDICT = re.compile(r"^\s*\**Verdict:\**\s*\**(READY|NOT READY)\**\s*\.?\s*$", re.IGNORECASE | re.MULTILINE)
FINDING_LINE = re.compile(r"^\s*-\s*(?:\[(?P<kind>[^\]]{1,40})\]\s*)?(?P<text>.+)$")
# The book's own text, shown at its gate: the tracker (`[requirements] tracker`) as the run left it.
DEFAULT_TRACKER = "docs/project.md"
BOOK_GATE_STEPS = ("outline-gate", "book-gate", "package")
BOOK_TEXT_MAX = 400 * 1024
REL_PATH = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._ -]+)*$")
BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
# What a person reads before a decision (reading_for): the run file's section each gate is about (factory.py
# GATED_SECTIONS), and the documents that section links. Only text documents inside the repository are offered.
GATE_READING = {"spec-gate": ("What “done” means", "Spec"), "design-gate": ("The design", "Design"),
                "outline-gate": ("The FSD's outline", "Outline"),
                "revision-gate": ("What the new FSD changes, and what was done about each change", "Triage")}
DOCUMENT_SUFFIXES = (".md", ".txt")
MD_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
PLAN_LINK = re.compile(r"\*\*Plan:\*\*\s*\[[^\]]*\]\(([^)\s]+)\)")
URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
STEP_WORDS = {"intake": "intake", "context": "context", "spec": "the spec", "spec-gate": "the spec gate",
              "design": "design", "design-gate": "the design gate", "plan": "the plan", "tests": "the tests",
              "build": "the build", "integrate": "integration", "verify": "the spec check", "review": "review",
              "accept": "acceptance", "package": "packaging", "sources": "the sources", "draft-plan": "the plan draft",
              "plan-gate": "the plan gate", "outline": "the outline", "outline-gate": "the outline gate",
              "inventory": "the inventory", "features": "the features", "audit": "the audit", "book-gate": "the book gate",
              "changes": "the changes", "triage": "the triage", "revision-gate": "the revision gate"}
GIT_SECONDS = 60
PACE_BLOCK = "pace"
# [backlog] in factory.toml, with the referee's own defaults (factory.py backlog_settings, new_branch_name).
DEFAULT_PLANS_DIR = "docs/plans"
DEFAULT_OUR_ROLE = "BE"
DEFAULT_BRANCHES = {"plan": "chore/docs-plan-{slug}", "task": "feature/{stem}-t{task}", "book": "setup/book-{name}",
                    "revision": "setup/revision-v{version}", "defect": "fix/{item}-{title}", "other": "feature/{item}-{title}"}
# A plan's Tasks table, read as the referee reads it (factory.py plan_task_rows, PLAN_TASKS_HEADING).
PLAN_TASKS_HEADING = re.compile(r"^## (?:\d+\.\s+)?Tasks\s*$")
UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
RUN_ID = re.compile(r"\bR-\d{3}\b")
# A plan file's stem as the referee's `plan:<stem>#<n>` item takes it.
PLAN_STEM = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
# The roll-up's next task (gen-task-rollup.py summary): the first of ours not started or in progress.
OPEN_TASK_STATUSES = ("Not started", "In progress")
STATUS_PREFIX = "**Status:**"
STATUS_LINE_WITHIN = 12


class RunFileError(ValueError):
    """A run file the pages cannot read: no Record, a Record that is not JSON, no run id."""


# -- configuration -------------------------------------------------------------------------


def project_config(checkout: Path) -> dict:
    """The keys this module reads from the project's factory.toml; defaults where it has none."""
    raw: dict = {}
    with contextlib.suppress(OSError, tomllib.TOMLDecodeError):
        raw = tomllib.loads((checkout / FACTORY_TOML).read_text(encoding="utf-8"))
    requirements = raw.get("requirements") if isinstance(raw.get("requirements"), dict) else {}
    backlog = raw.get("backlog") if isinstance(raw.get("backlog"), dict) else {}
    book = raw.get("book") if isinstance(raw.get("book"), dict) else {}
    branches = backlog.get("branches") if isinstance(backlog.get("branches"), dict) else {}
    # As the referee reads it: [backlog] first, then the kit's [book]; one role or a list, kept as "BE, FE".
    roles = backlog.get("our_role") or book.get("our_role") or DEFAULT_OUR_ROLE
    our = ", ".join(str(r) for r in roles) if isinstance(roles, list) else str(roles)
    steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
    merge_step = next((x for x in steps if isinstance(x, dict) and x.get("name") == "merge-gate"), {})
    return {"runs_dir": str(raw.get("runs_dir") or DEFAULT_RUNS_DIR).strip("/"),
            "merge_check": str(merge_step.get("check") or ""),
            "base_ref": str(raw.get("base_ref") or DEFAULT_BASE_REF),
            # The remote work is shared through (factory.py remote_name): `remote`, else the one base_ref names.
            "remote": str(raw.get("remote") or "").strip() or (str(raw.get("base_ref") or DEFAULT_BASE_REF).split("/", 1)[0]
                                                                  if "/" in str(raw.get("base_ref") or DEFAULT_BASE_REF) else "origin"),
            "tracker": str(requirements.get("tracker") or ""),
            "plans_dir": str(backlog.get("plans_dir") or DEFAULT_PLANS_DIR).strip("/"),
            "our_role": our or DEFAULT_OUR_ROLE,
            "branches": {**DEFAULT_BRANCHES, **{str(k): str(v) for k, v in branches.items() if isinstance(v, str)}},
            "protected_branches": [str(b) for b in raw.get("protected_branches") or [] if isinstance(b, str)]}


# -- one run file ---------------------------------------------------------------------------


def record_of(text: str) -> dict:
    try:
        start = text.index(FENCE)
        body = text[text.index("\n", start) + 1:]
        record = json.loads(body[:body.index("\n```")])
    except ValueError as error:  # no fence, no closing fence, or not JSON (JSONDecodeError is a ValueError)
        raise RunFileError("no readable Record") from error
    if not isinstance(record, dict) or not isinstance(record.get("run"), str):
        raise RunFileError("the Record names no run")
    return record


def sections_of(text: str) -> dict[str, str]:
    found = {}
    for match in re.finditer(r"^## (.+?)\n(.*?)(?=^## |\Z)", text.replace("\r\n", "\n"), re.MULTILINE | re.DOTALL):
        found[match.group(1).strip()] = re.sub(r"<!--.*?-->", "", match.group(2), flags=re.DOTALL).strip()
    return found


def labelled(text: str, label: str) -> str:
    match = re.search(rf"\*\*{label}\.\*\*\s*(.+?)(?=\n\s*\n|\*\*[A-Z][a-z]+\.\*\*|\Z)", text, re.DOTALL)
    return " ".join(match.group(1).split()) if match else ""


def number(value: object) -> float:
    """A cost or a count from a run file; one that is not a number counts as nothing, never an error that blanks the
    project's run list (the 15 Oct review's #13)."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def add_tokens(total: dict, tokens: dict | None) -> None:
    tokens = tokens if isinstance(tokens, dict) else {}
    for kind in TOKEN_KINDS:
        total[kind] = total.get(kind, 0) + int(number(tokens.get(kind)))


def cost_of(record: dict) -> dict:
    cost = record.get("cost") or {}
    steps = {name: entry for name, entry in (cost.get("steps") or {}).items() if isinstance(entry, dict)}
    agents = {name: entry for name, entry in (cost.get("agents") or {}).items() if isinstance(entry, dict)}
    tokens: dict = {}
    measured = False
    for entry in [*steps.values(), *agents.values()]:
        if entry.get("tokens"):
            measured = True
        add_tokens(tokens, entry.get("tokens"))
    review_usd = (record.get("review") or {}).get("usd")
    session = number(cost.get("session_usd"))
    agents_usd = sum(number(a.get("usd")) for a in agents.values())
    return {
        "recorded": bool(cost),
        "session_usd": round(session, 4),
        "agents_usd": round(agents_usd, 4),
        "total_usd": round(session + agents_usd, 4),
        # Before the referee counted its agents, only the last review's cost was kept, in the Record's review.
        "uncounted_review_usd": review_usd if review_usd and not agents else None,
        "tokens": tokens if measured else None,
        "steps": [{"name": name, **entry} for name, entry in sorted(steps.items())],
        "agents": [{"name": name, **entry} for name, entry in sorted(agents.items())],
        "attempts": sum(int(number(e.get("attempts"))) for n, e in steps.items() if n != "compaction"),
    }


def tier_of(record: dict) -> dict:
    tier = record.get("tier")
    return tier if isinstance(tier, dict) else {"value": str(tier or ""), "reasons": []}


def kind_of(record: dict) -> str:
    """The run's kind as the referee records it; a Record without one is a change run."""
    kind = record.get("kind") or "change"
    return kind if isinstance(kind, str) else "change"


def step_state(record: dict, name: str) -> str:
    # The design gate is a High run's, or any run's whose design describes a new screen (factory.py applicable()).
    if (name == "design-gate" and tier_of(record).get("value") != "High"
            and not record.get("screens") and not record.get("packages") and not record.get("writes")):
        return "skipped"
    if (name == "outline-gate" and "outline-gate" not in (record.get("gates") or {})
            and record.get("step") in ("inventory", "features", "audit", "book-gate", "package")):
        return "skipped"  # the run went past it unapproved: the referee skips it for an FSD that is not a PDF
    if name in GATES:
        if name in (record.get("gates") or {}):
            return "done"
    elif ((record.get("steps") or {}).get(name) or {}).get("status") == "passed":
        return "done"
    if record.get("status") == "abandoned" and record.get("step") == name:
        return "stopped"
    return "current" if record.get("step") == name else "todo"


def criteria_of(sections: dict, record: dict) -> list[dict]:
    red = (record.get("red") or {}).get("acs") or {}
    checks = record.get("checks") or []
    out = []
    for line in sections.get("Spec", "").splitlines():
        match = AC_LINE.match(line)
        if not match:
            continue
        ac = match.group("id").strip()
        greens = [c for c in checks if c.get("name") == f"green {ac}"]
        last = greens[-1] if greens else None
        out.append({"id": ac, "guard": bool(match.group("guard")), "text": match.group("text").strip(),
                    "red": red.get(ac), "green": None if last is None else bool(last.get("ok")),
                    "green_head": (last.get("head") or "")[:10] if last else "",
                    "audit": ((record.get("audit") or {}).get("rows") or {}).get(ac)})
    return out


def last_activity(record: dict) -> str:
    """The latest moment a Record names — when it was created, or any `at` under it — as UTC ISO text."""
    moments: list[datetime.datetime] = []

    def walk(value: object, key: str = "") -> None:
        if isinstance(value, dict):
            for name, inner in value.items():
                walk(inner, name)
        elif isinstance(value, list):
            for inner in value:
                walk(inner, key)
        elif key in ("at", "created") and isinstance(value, str):
            with contextlib.suppress(ValueError):
                moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
                moments.append(moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc))

    walk(record)
    return max(moments).astimezone(datetime.timezone.utc).isoformat(timespec="seconds") if moments else ""


def person(by: object) -> str:
    """A gate's or an abandonment's `by`, without the e-mail address the referee records beside the name."""
    return str(by or "").split("<")[0].strip()


def status_of(record: dict) -> tuple[str, str]:
    status = record.get("status", "open")
    if status == "abandoned":
        return "abandoned", "closed by " + (person((record.get("abandoned") or {}).get("by")) or "a person")
    if status == "done":
        return "done", "every step passed"
    step = record.get("step") or ""
    if step in GATES:
        return "waiting", f"waits at the {step.replace('-', ' ')} for a person"
    if record.get("driving"):
        return "running", f"the factory is working on {step}"
    return "open", f"at {step}"


def dry_outcome(run: dict) -> dict:
    """
    What a dry run shows when no register says more: it tests the factory, so it ends as designed when it
    reaches the merge gate (a dry run opens no pull request) or when the factory stops and asks a person.
    """
    reached_merge_gate = run["step"] in STEP_NAMES and STEP_NAMES.index(run["step"]) >= STEP_NAMES.index("merge-gate")
    where = STEP_WORDS.get(run["step"], run["step"])
    if run["status"] == "done":
        kind, label, detail = "complete", "Finished", "every step passed"
    elif reached_merge_gate:
        kind, label, detail = "complete", "Passed every check", "at the merge gate"
    elif run["status"] == "abandoned":
        kind, label, detail = "closed", "Closed", (run.get("abandoned") or {}).get("reason") or "closed by a person"
    elif run["sections"].get("Escalation") or any(r.get("escalation") for r in run["reopened"]):
        kind, label, detail = "caught", "Stopped correctly", f"stopped and asked a person · at {where}"
    else:
        kind, label, detail = "going", "In progress", f"at {where}"
    return {"kind": kind, "label": label, "detail": detail, "ids": [], "tested": "", "expected": "",
            "result": "", "recorded": False, "rerun": ""}


def run_key(project: str, run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", f"{project}-{run_id}").strip("-")


# A drive marker older than this is what a killed drive left behind, not a drive running now.
DRIVE_MARKER_HOURS = 4


def process_alive(pid: int) -> bool:
    """Whether a process of this id is running on this machine; True when it cannot be told."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel32.GetLastError() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def drive_is_live(marker: object) -> bool:
    if not isinstance(marker, dict):
        return bool(marker)
    # A drive that was stopped hard (the app's Stop, a closed window) never cleared its marker: its process is gone.
    pid = marker.get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool) and not process_alive(pid):
        return False
    try:
        at = datetime.datetime.fromisoformat(str(marker.get("at") or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    if at.tzinfo is None:
        at = at.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc) - at < datetime.timedelta(hours=DRIVE_MARKER_HOURS)


def drive_died(marker: object) -> str:
    """
    The step a drive was on when its process ended without clearing its marker — a crash, a closed window, the machine
    short of memory — or "" when it did not. The page then says the AI stopped unexpectedly, instead of nothing
   . Only a process known to have ended counts; an old marker without one says nothing.
    """
    if not isinstance(marker, dict):
        return ""
    pid = marker.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or process_alive(pid):
        return ""
    return str(marker.get("step") or "") or "?"


def load_run(text: str, project: str, where: str, *, dry_prefixes: tuple[str, ...] = DEFAULT_DRY_PREFIXES,
             dry_ids: frozenset[str] | set[str] = frozenset(), trust_driving: bool = True) -> dict:
    """One run file as the pages show it. RunFileError when it has no readable Record. `trust_driving` is False for a
    copy read from a branch: only a working folder can say a drive is running now."""
    record = record_of(text)
    died = drive_died(record.get("driving")) if trust_driving else ""
    if not trust_driving or not drive_is_live(record.get("driving")):
        record = {**record, "driving": None}
    sections = sections_of(text)
    first = text.replace("\r\n", "\n").split("\n", 1)[0]
    title_match = TITLE_LINE.match(first)
    status, status_text = status_of(record)
    tier = tier_of(record)
    gates = record.get("gates") or {}
    run = {
        "key": run_key(project, record["run"]),
        "id": record["run"],
        "title": title_match.group(2) if title_match else "",
        "project": project,
        "path": where,
        "item": record.get("item") or "",
        "branch": record.get("branch") or "",
        "status": status,
        "status_text": status_text,
        "open": record.get("status", "open") == "open",
        "driving": bool(record.get("driving")),
        "drive_died": died,
        "driving_step": str((record.get("driving") or {}).get("step") or "") if isinstance(record.get("driving"), dict) else "",
        "driving_since": str((record.get("driving") or {}).get("at") or "") if isinstance(record.get("driving"), dict) else "",
        "merge_approved": bool(record.get("merge_approval")),
        "step": record.get("step") or "",
        "tier": tier.get("value", ""),
        "tier_reasons": tier.get("reasons") or [],
        "created": record.get("created") or "",
        "last": last_activity(record),
        "kind": kind_of(record),
        "spine": [{"name": n, "label": label, "gate": n in GATES, "state": step_state(record, n),
                   "by": person((gates.get(n) or {}).get("by"))} for n, label in STEPS_BY_KIND.get(kind_of(record), STEPS)],
        "problem": labelled(sections.get("Intent", ""), "Problem"),
        "outcome": labelled(sections.get("Intent", ""), "Outcome"),
        "criteria": criteria_of(sections, record),
        "checks": [{"step": c.get("step"), "name": c.get("name"), "ok": c.get("ok"),
                    "counts": c.get("counts"), "head": (c.get("head") or "")[:10],
                    "sha": (c.get("sha256") or "")[:12]} for c in record.get("checks") or []],
        "gates": [{"name": n, "by": person(g.get("by")), "at": g.get("at") or "", "note": g.get("note") or "",
                   "proof": g.get("proof") or ""} for n, g in sorted(gates.items())],
        # A merge gate approved on this computer (a project with no remote): who, when, and the copy they saw.
        "merge_approval": ({"by": person(record["merge_approval"].get("by")), "at": record["merge_approval"].get("at") or "",
                            "note": record["merge_approval"].get("note") or "", "proof": record["merge_approval"].get("proof") or ""}
                           if isinstance(record.get("merge_approval"), dict) else None),
        "locks": len(record.get("lock") or {}),
        "locked": sorted(record.get("lock") or {}),
        "screens": [str(s) for s in record.get("screens") or []],   # new screens its design describes (the design gate)
        "packages": [f"{p.get('name')}@{p.get('version')}" + (" (dev)" if p.get("dev") else "") for p in record.get("packages") or [] if isinstance(p, dict)],   # the packages its design asks to install (the design gate)
        "writes": [str(p) for p in record.get("writes") or []],   # the new places its design asks the build to write (the design gate)
        # The new places its plan needs and a person may allow (factory.py plan_scope_problems): the Allow button.
        "needs_allow": [str(p) for p in (record.get("needs_allow") or {}).get("paths") or []],
        "unlocks": record.get("unlocks") or [],
        "review": {"verdict": (record.get("review") or {}).get("verdict"),
                   "findings": (record.get("review") or {}).get("findings") or []} if record.get("review") else None,
        "audit": {"verdict": (record.get("audit") or {}).get("verdict"),
                  "rows": (record.get("audit") or {}).get("rows") or {}} if record.get("audit") else None,
        # Each answer: what was asked and when, who answered and the copy they saw (the Decisions log's rows).
        "reopened": [{"step": r.get("step"), "at": r.get("at") or "", "reason": r.get("reason") or "",
                      "escalation": r.get("escalation") or "", "asked_at": r.get("asked_at") or "",
                      "person": person(r.get("person")) if r.get("person") else "", "by": r.get("by") or "",
                      "proof": r.get("proof") or ""} for r in record.get("reopened") or []],
        "asked_at": str(record["asked"].get("at") or "") if isinstance(record.get("asked"), dict) else "",
        "abandoned": ({**record["abandoned"], "by": person(record["abandoned"].get("by"))}
                      if isinstance(record.get("abandoned"), dict) else None),
        "pr": record.get("pr"),
        "cost": cost_of(record),
        "sections": {name: sections[name] for name in SECTIONS_SHOWN if sections.get(name)},
    }
    is_dry = any(run["branch"].startswith(prefix) for prefix in dry_prefixes if prefix) or run["id"] in dry_ids
    run["dry"] = dry_outcome(run) if is_dry else None
    if run["kind"] == "book":
        run["book"] = book_of(sections, record)
    return run


# -- a book run: what a person reads at its gates ----------------------------------------------------


def inventory_of(text: str) -> list[dict]:
    """The Inventory, one row per FSD section: its id, title, disposition and what follows it (a feature
    name, a reason, a question)."""
    rows = []
    for line in text.splitlines():
        match = INVENTORY_LINE.match(line)
        if not match:
            continue
        title, _, said = match.group("rest").rpartition(" — ")
        word = said.split(" ", 1)[0].strip().lower() if title else ""
        rows.append({"section": match.group("id"), "title": title.strip() if title else match.group("rest").strip(),
                     "disposition": word if word in DISPOSITIONS else "",
                     "detail": said[len(word):].strip() if word in DISPOSITIONS else said.strip(),
                     "questions": re.findall(r"\bQ\d+\b", said) if word == "empty" else []})
    return rows


def audit_of(text: str, record: dict) -> dict:
    """The book auditor's report as the referee wrote it into the Audit section, with the Record's verdict."""
    recorded = record.get("book_audit") if isinstance(record.get("book_audit"), dict) else {}
    found = BOOK_VERDICT.findall(text)
    verdict = str(recorded.get("verdict") or (found[0].upper() if len(set(v.upper() for v in found)) == 1 else ""))
    findings, coverage, sound, part = [], "", "", ""
    for line in text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith("findings:"):
            part = "findings"
            continue
        if lower.startswith("coverage:"):
            coverage, part = stripped.split(":", 1)[1].strip(), ""
            continue
        if lower.startswith("what is sound:"):
            sound, part = stripped.split(":", 1)[1].strip(), "sound"
            continue
        if part == "findings":
            match = FINDING_LINE.match(line)
            if match:
                findings.append({"kind": (match.group("kind") or "").strip(), "text": match.group("text").strip()})
            elif stripped and findings:
                findings[-1]["text"] += " " + stripped
        elif part == "sound" and stripped:
            sound += " " + stripped
    return {"verdict": verdict, "at": str(recorded.get("at") or ""), "findings": findings, "coverage": coverage,
            "sound": sound.strip(), "written": bool(text.strip())}


def book_of(sections: dict, record: dict) -> dict:
    """A book run's gate screen: the FSD it read, the outline, the Inventory and the audit. The tracker's own
    text is added by project_runs, which can read the branch."""
    outline = record.get("outline") if isinstance(record.get("outline"), dict) else {}
    return {"fsd": str(outline.get("file") or ""), "fsd_format": str(outline.get("format") or ""),
            "units": outline.get("units") if isinstance(outline.get("units"), int) else None,
            "inventory": inventory_of(sections.get("Inventory", "")),
            "audit": audit_of(sections.get("Audit", ""), record), "tracker": None}


# The FSD a book run reads: the newest `<fsd_dir>/* - V<n>.<ext>` by the book's own pattern (factory.py
# BOOK_FSD_VERSION, `[book] fsd_version`) — .docx, .md, .txt or .pdf.
DEFAULT_FSD_DIR = "requirements/fsd"
DEFAULT_BOOK_FSD_VERSION = r" - V(\d+)\.(docx|md|txt|pdf)$"


def newest_book_fsd(checkout: Path, ref: str = "HEAD") -> str:
    """`V<n>` of the newest FSD a book run would read at `ref` (the folder's own branch by default), or ""."""
    newest = newest_fsd_snapshot(checkout, ref)
    return f"V{newest[0]}" if newest else ""


def newest_fsd_snapshot(checkout: Path, ref: str = "HEAD") -> tuple[int, str] | None:
    """The newest FSD snapshot at `ref` — its version and path in the repository — or None."""
    raw: dict = {}
    with contextlib.suppress(OSError, tomllib.TOMLDecodeError):
        raw = tomllib.loads((checkout / FACTORY_TOML).read_text(encoding="utf-8"))
    sources = ((raw.get("requirements") or {}).get("sources") or {}) if isinstance(raw.get("requirements"), dict) else {}
    book = raw.get("book") if isinstance(raw.get("book"), dict) else {}
    fsd_dir = str(sources.get("fsd_dir") or DEFAULT_FSD_DIR).strip("/")
    try:
        pattern = re.compile(str(book.get("fsd_version") or DEFAULT_BOOK_FSD_VERSION))
    except re.error:
        return None
    if not REL_PATH.fullmatch(fsd_dir) or not BRANCH_NAME.fullmatch(ref):
        return None
    versions = [(int(m.group(1)), name) for name in git_out(checkout, "ls-tree", "-r", "--name-only", ref, "--", f"{fsd_dir}/").splitlines()
                if (m := pattern.search(name))]
    return max(versions) if versions else None


def main_worktree(repo: Path) -> Path | None:
    """The repository's main working folder (git lists it first), or None for a bare repository or no git."""
    first = git_out(repo, "worktree", "list", "--porcelain").replace("\r\n", "\n").split("\n\n")[0]
    fields = dict(line.split(" ", 1) if " " in line else (line, "") for line in first.splitlines())
    if "worktree" not in fields or "bare" in fields:
        return None
    return Path(fields["worktree"])


def display_name(folder_name: str) -> str:
    """A project's name as people read it, from its folder's: `my-shop` → `My Shop`, `acme-shop` → `Acme Shop`."""
    words = [w for w in re.split(r"[-_\s]+", folder_name) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words) or folder_name


def worktree_branches(repo: Path) -> dict[str, Path]:
    """Each branch checked out in a worktree (the main folder included), and that worktree's folder."""
    found: dict[str, Path] = {}
    for block in git_out(repo, "worktree", "list", "--porcelain").replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        branch = fields.get("branch", "").removeprefix("refs/heads/")
        if "worktree" in fields and branch:
            found.setdefault(branch, Path(fields["worktree"]))
    return found


def book_tracker(repo: Path, branch: str, tracker: str, worktrees: dict[str, Path]) -> dict:
    """The tracker (docs/project.md) as a book run left it (run_document)."""
    doc = run_document(repo, branch, tracker, worktrees)
    if doc["error"] and not readable_document(tracker):
        doc["error"] = "the tracker's path in factory.toml is not one the app reads"
    return doc


def readable_document(path: str, suffixes: tuple[str, ...] = DOCUMENT_SUFFIXES) -> bool:
    """A text document inside the repository: relative, no `..` or hidden folder, ending .md or .txt (or `suffixes`)."""
    return (bool(REL_PATH.fullmatch(path)) and path.lower().endswith(suffixes)
            and not any(part in ("", ".", "..") or part.startswith(".") for part in path.split("/")))


def proofs_of(run: dict) -> list[str]:
    """Every saved copy a run's decisions point at — its gates, its answers, its local merge approval — in the record."""
    found = [g.get("proof") for g in run.get("gates") or []] + [r.get("proof") for r in run.get("reopened") or []]
    found.append((run.get("merge_approval") or {}).get("proof"))
    return [p for p in found if p]


def run_document(repo: Path, branch: str, path: str, worktrees: dict[str, Path],
                 suffixes: tuple[str, ...] = DOCUMENT_SUFFIXES) -> dict:
    """
    One document a run's decision is about, as the run left it: from the folder that has the run's branch
    checked out — a person may be editing it at the gate — or else from the branch through git. Read only.
    `suffixes` widens it for a saved copy of a decision (.html), which only a run's own record can name.
    """
    if not readable_document(path, suffixes):
        return {"path": path, "text": "", "from": "", "error": "not a document the app reads"}
    if not BRANCH_NAME.fullmatch(branch) or ".." in branch:
        return {"path": path, "text": "", "from": "", "error": "the run names no branch the app reads"}
    data: bytes | None = None
    where = ""
    folder = worktrees.get(branch)
    if folder is not None:
        with contextlib.suppress(OSError):
            data = (folder / path).read_bytes()
            where = f"the working folder on {branch}"
    if data is None:
        data = git_batch(repo, [f"refs/heads/{branch}:{path}"]).get(f"refs/heads/{branch}:{path}")
        where = f"branch {branch}"
    if data is None:
        return {"path": path, "text": "", "from": "", "error": f"{path} is not on {branch}"}
    text = data[:BOOK_TEXT_MAX].decode("utf-8", "replace").replace("\r\n", "\n")
    return {"path": path, "text": text, "from": where, "truncated": len(data) > BOOK_TEXT_MAX, "error": ""}


def linked_documents(text: str, folder: str) -> list[dict]:
    """Each repository document a run file's section links, resolved from the run file's folder: never a web page or code."""
    found = []
    for label, target in MD_LINK.findall(text):
        if URL_SCHEME.match(target) or target.startswith("#"):
            continue
        path, _, anchor = target.partition("#")
        joined = posixpath.normpath(posixpath.join(folder, path))
        if readable_document(joined):
            found.append({"label": label.strip(), "path": joined, "section": "", "anchor": anchor})
    return found


def reading_for(run: dict, config: dict) -> list[dict]:
    """
    What a person reads before the decision a run waits for, in reading order: the run file's section the gate
    or question is about, the plan a task builds, and every document that section links. Each is
    {label, path, section, anchor}: a whole file, a `## section` of it, or the heading an anchor names.
    Nothing when nothing waits for a person.
    """
    if not run["open"]:
        return []
    runs_dir = config["runs_dir"]
    own = f"{runs_dir}/{run['id']}.md"
    sections, step, item = run["sections"], run["step"], run["item"]
    plan_stem = item.removeprefix("plan:").split("#", 1)[0] if item.startswith("plan:") else ""
    plan = f"{config['plans_dir']}/{plan_stem}.md" if PLAN_STEM.fullmatch(plan_stem) else ""
    docs: list[dict] = []

    def add(label: str, path: str, section: str = "", anchor: str = "") -> None:
        if path and readable_document(path):
            docs.append({"label": label, "path": path, "section": section, "anchor": anchor})

    def section(label: str, name: str) -> None:
        if sections.get(name):
            add(label, own, name)

    if step in GATE_READING:
        label, name = GATE_READING[step]
        section(label, name)
        if step == "revision-gate":
            section("What changed in the FSD", "Changes")
            section("The questions it answers", "Answered")
            section("The book check", "Audit")
            add("The updated book", config["tracker"] or DEFAULT_TRACKER)
        if step == "design-gate" and "#" in item:
            add("The plan it builds", plan)
        docs.extend(linked_documents(sections.get(name, ""), runs_dir))
    elif step == "plan-gate":
        linked = PLAN_LINK.search(sections.get("Design", ""))
        add("The plan", posixpath.normpath(posixpath.join(runs_dir, linked.group(1).split("#", 1)[0])) if linked else plan)
    elif run["kind"] == "book" and step in BOOK_GATE_STEPS:
        add("The drafted book", config["tracker"] or DEFAULT_TRACKER)
        section("The book check", "Audit")
    elif step == "merge-gate":
        section("The proof", "Acceptance")
        section("The review", "Review")
        if "#" in item:
            add("The plan it builds", plan)
    if sections.get("Escalation"):
        section("The AI's question", "Escalation")
        docs.extend(linked_documents(sections["Escalation"], runs_dir))
    unique: dict[tuple[str, str, str], dict] = {}
    for doc in docs:
        unique.setdefault((doc["path"], doc["section"], doc["anchor"]), doc)
    return list(unique.values())


# -- every copy on every branch and worktree ---------------------------------------------------


def git_out(repo: Path, *args: str) -> str:
    """git's stdout, or "" when git fails or is missing — a read that cannot be made is not a crash."""
    try:
        done = subprocess.run(["git", *args], cwd=repo, capture_output=True, timeout=GIT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else ""


def git_batch(repo: Path, names: list[str]) -> dict[str, bytes]:
    """`git cat-file --batch` for many object names in one process: each name's bytes, missing ones left out."""
    if not names:
        return {}
    try:
        done = subprocess.run(["git", "cat-file", "--batch"], cwd=repo, input="\n".join(names).encode("utf-8") + b"\n",
                              capture_output=True, timeout=GIT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if done.returncode != 0:
        return {}
    found: dict[str, bytes] = {}
    out, at = done.stdout, 0
    for name in names:
        end = out.find(b"\n", at)
        if end == -1:
            break
        header = out[at:end].split()
        at = end + 1
        if not header or header[-1] == b"missing" or len(header) != 3:
            continue
        size = int(header[2])
        found[name] = out[at:at + size]
        at += size + 1
    return found


def run_copies(repo: Path, runs_dir: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """
    Each copy of each run file: on every local and fetched branch, read through git without switching to
    it, and in every worktree's folder, where a run's newest state may not be committed yet. Returns
    (text, where, file) triples — `where` is "branch <name>" or "worktree <branch>" — and the files that
    could not be read.
    """
    copies: list[tuple[str, str, str]] = []
    errors: list[str] = []
    refs = [ref for ref in git_out(repo, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes").splitlines()
            if ref and not ref.endswith("/HEAD")]
    trees = git_batch(repo, [f"{ref}:{runs_dir}" for ref in refs])
    blobs: dict[str, list[tuple[str, str]]] = {}  # blob id: (branch, file name) holding it
    for ref in refs:
        tree = trees.get(f"{ref}:{runs_dir}")
        if tree is None:
            continue
        at = 0
        while at < len(tree):  # a tree object: "<mode> <name>\0<20-byte id>" repeated
            nul = tree.find(b"\0", at)
            if nul == -1:
                break
            mode, _, name = tree[at:nul].decode("utf-8", "replace").partition(" ")
            blob = tree[nul + 1:nul + 21].hex()
            at = nul + 21
            if mode.startswith("100") and RUN_FILE_NAME.match(name):
                blobs.setdefault(blob, []).append((ref.removeprefix("refs/heads/").removeprefix("refs/remotes/"), name))
    for blob, data in git_batch(repo, list(blobs)).items():
        branch, name = blobs[blob][0]
        copies.append((data.decode("utf-8", "replace"), f"branch {branch}", f"{runs_dir}/{name}"))
    for block in git_out(repo, "worktree", "list", "--porcelain").replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        if "worktree" not in fields:
            continue
        folder = Path(fields["worktree"]) / runs_dir
        branch = fields.get("branch", "").removeprefix("refs/heads/") or "detached"
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if RUN_FILE_NAME.match(path.name):
                try:
                    copies.append((path.read_text(encoding="utf-8"), f"worktree {branch}", f"{runs_dir}/{path.name}"))
                except (OSError, UnicodeDecodeError) as error:
                    errors.append(f"{path.name} in the worktree of {branch}: {error.__class__.__name__}")
    return copies, errors


def git_succeeds(repo: Path, *args: str) -> bool:
    try:
        done = subprocess.run(["git", *args], cwd=repo, capture_output=True, timeout=GIT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def repo_group(repo: Path) -> str:
    """Folders that are worktrees of one repository share their git directory: the same id for all of them."""
    common = git_out(repo, "rev-parse", "--git-common-dir").strip()
    if not common:
        return ""
    return hashlib.sha256(str((repo / common).resolve()).lower().encode("utf-8")).hexdigest()[:10]


def has_remote(repo: Path) -> bool:
    return bool(git_out(repo, "remote").strip())


def merge_target(repo: Path, base_ref: str = "") -> str:
    """
    The branch a finished run is merged into where there is no pull request: the project's base branch (`base_ref`, as
    `develop` or `origin/trunk` names it), else main, else master; "" if none is here. It was main or master only, so a
    project whose base is `develop` was merged into main (the kit audit, 18 Oct).
    """
    named = [base_ref, base_ref.split("/", 1)[1]] if "/" in base_ref else [base_ref]
    return next((b for b in (*named, "main", "master") if b and BRANCH_NAME.fullmatch(b)
                 and git_succeeds(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{b}")), "")


def is_finished(run: dict) -> bool:
    """Approved at the merge gate and not waiting to be merged: the work is done, whatever the release steps still say."""
    merge = run.get("merge") or {}
    return bool(run.get("open") and merge.get("passed") and merge.get("merged") is not False)


def ready_to_add(run: dict) -> bool:
    """
    A book run, done, its book gate approved: a book run has no merge gate — its book gate is the person's approval —
    so once packaged it waits only to be added to main. Without this the app offered no "Add it to main" and main was
    moved by hand.
    """
    # Any finished run approved at its last gate, not only a book run: a plan run done after its merge gate waited
    # unseen, with no "Add it to main".
    return run.get("status") == "done" and any(g.get("name") in ("book-gate", "revision-gate", "merge-gate") for g in run.get("gates") or [])


def merge_info(repo: Path, config: dict, run: dict, local: bool, target: str) -> dict:
    """Where a run stands at its merge: only a project that merges locally (no remote, a local approval) has a Merge button."""
    passed = any(g["name"] == "merge-gate" for g in run["gates"]) or ready_to_add(run)
    merged = None
    if local and passed and target and BRANCH_NAME.fullmatch(run["branch"]) and run["branch"] != target:
        merged = git_succeeds(repo, "merge-base", "--is-ancestor", f"refs/heads/{run['branch']}", f"refs/heads/{target}")
    return {"local": local, "approved": run["merge_approved"], "passed": passed, "merged": merged, "target": target}


def project_runs(repo: Path, project: str, *, dry_prefixes: tuple[str, ...] = DEFAULT_DRY_PREFIXES,
                 dry_ids: frozenset[str] | set[str] = frozenset()) -> dict:
    """
    Every run of one project, each once: its most advanced copy by the Record's last activity, then the
    number of checks, a worktree's copy winning a tie (it may hold work not yet committed). With the
    files that could not be read, the delivery pace from the tracker, and a version of the answer.
    """
    config = project_config(repo)
    copies, errors = run_copies(repo, config["runs_dir"])
    best: dict[str, tuple[tuple, dict]] = {}
    drives: dict[str, dict] = {}
    for text, where, file in copies:
        try:
            run = load_run(text, project, f"{where}: {file}", dry_prefixes=dry_prefixes, dry_ids=dry_ids,
                           trust_driving=where.startswith("worktree "))
        except (RunFileError, KeyError, TypeError, AttributeError) as error:
            errors.append(f"{file} on {where.split(' ', 1)[1]}: {error}")
            continue
        if run["driving"] and where != f"worktree {run['branch']}":
            # Trial R-009: a folder cut from a run's branch mid-drive carries its marker; only the run's own folder drives it.
            run.update(driving=False, driving_step="", driving_since="")
        if run["driving"] and run["driving_since"] >= drives.get(run["id"], {}).get("driving_since", ""):
            drives[run["id"]] = run   # a drive shows in the working folder that runs it, whichever copy is the newest
        rank = (run["last"], len(run["checks"]), where.startswith("worktree "))
        if run["id"] not in best or rank > best[run["id"]][0]:
            best[run["id"]] = (rank, run)
    runs = [run for _, (_, run) in sorted(best.items())]
    for run in runs:
        found = drives.get(run["id"])
        if found is not None and run["open"]:
            run.update(driving=True, driving_step=found["driving_step"], driving_since=found["driving_since"],
                       status="running" if run["status"] == "open" else run["status"])
            run["status_text"] = f"the factory is working on {run['driving_step'] or run['step']}"
    local = config["merge_check"] == "local-approval" and not has_remote(repo)
    target = merge_target(repo, config["base_ref"]) if local else ""
    for run in runs:
        run["merge"] = merge_info(repo, config, run, local, target) if run["open"] or ready_to_add(run) else None
        run["reading"] = reading_for(run, config)
    # An open book run at (or past) its gate carries the tracker it wrote, for the book-gate screen.
    gated = [run for run in runs if run.get("book") is not None and run["open"] and run["step"] in BOOK_GATE_STEPS]
    if gated:
        worktrees = worktree_branches(repo)
        for run in gated:
            run["book"]["tracker"] = book_tracker(repo, run["branch"], config["tracker"] or DEFAULT_TRACKER, worktrees)
    answer = {"project": project, "runs_dir": config["runs_dir"], "dry_prefixes": list(dry_prefixes),
              "runs": runs, "errors": sorted(set(errors)), "pace": pace(repo, config)}
    answer["version"] = version_of(answer)
    answer["generated"] = now_iso()
    return answer


# -- the tracker's delivery pace (optional) ---------------------------------------------------


def generated_block(text: str, name: str) -> list[list[str]]:
    """The body rows of a table between `<!-- name:begin … -->` and `<!-- name:end -->`."""
    match = re.search(rf"<!-- {name}:begin[^>]*-->(.*?)<!-- {name}:end -->", text, re.DOTALL)
    if not match:
        raise ValueError(f"no {name} block")
    rows = [line.strip().strip("|").split("|") for line in match.group(1).strip().splitlines()
            if line.strip().startswith("|")]
    return [[cell.strip() for cell in row] for row in rows[2:]]


def pace(repo: Path, config: dict) -> dict:
    """
    Tasks merged per week, from the tracker's generated `pace` block at base_ref (else HEAD):
    {"value": [{week, merged, total}], "ref"} or {"error": why} — never a guess.
    """
    tracker = config.get("tracker")
    if not tracker:
        return {"error": "factory.toml names no [requirements] tracker"}
    ref = config["base_ref"] if git_out(repo, "rev-parse", "--verify", "--quiet", f"{config['base_ref']}^{{commit}}") else "HEAD"
    text = git_out(repo, "show", f"{ref}:{tracker}")
    if not text:
        return {"error": f"no {tracker} on {ref}"}
    try:
        rows = generated_block(text, PACE_BLOCK)
        return {"value": [{"week": row[0], "merged": int(row[1]), "total": int(row[3])} for row in rows], "ref": ref}
    except (ValueError, IndexError):
        return {"error": f"{tracker} on {ref} records no delivery pace yet"}


# -- plans: each plan's status line and Tasks table ----------------------------------------------


def table_cells(line: str) -> list[str] | None:
    """A markdown table row's trimmed cells; None when the line is not a row (factory.py table_cells)."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    inner = stripped[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [cell.strip().replace("\\|", "|") for cell in UNESCAPED_PIPE.split(inner)]


def plan_task_rows(text: str) -> list[dict] | None:
    """
    A plan's Tasks table as the referee reads it (factory.py plan_task_rows): the first table under the
    `## <n>. Tasks` heading, each row by its column names. None when the plan has no Tasks heading.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    start = next((i for i, line in enumerate(lines) if PLAN_TASKS_HEADING.match(line.strip())), None)
    if start is None:
        return None
    rows: list[dict] = []
    header: list[str] | None = None
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        cells = table_cells(line)
        if cells is None:
            if header is not None and rows:
                break
            continue
        if header is None:
            header = cells
        elif not all(set(cell) <= set("-: ") for cell in cells):
            rows.append(dict(zip(header, cells)))
    return rows


def plain(text: str) -> str:
    """A cell without Markdown emphasis or code ticks (gen-task-rollup.py plain)."""
    return re.sub(r"[`*]", "", text).strip()


def is_ours(role: str, our_role: str) -> bool:
    """Whether a task's role is one we build: `our_role` is one role, or several as "BE, FE"."""
    return role in [r.strip() for r in our_role.split(",")]


def plan_detail(text: str, path: str, our_role: str = DEFAULT_OUR_ROLE) -> dict:
    """
    One plan as its detail view shows it: the title, the status line, each task (#, task, FSD, role,
    status, Jira, PR, merged, the run ids its Task cell names) and `next` — the number of the first of
    our tasks not started or in progress, as the roll-up names it ("" when there is none).
    """
    lines = text.replace("\r\n", "\n").split("\n")
    stem = Path(path).stem
    first = lines[0] if lines else ""
    title = re.sub(r"^#\s*|\s*—\s*Design$", "", first).strip() if first.startswith("#") else ""
    status = next((line for line in lines[:STATUS_LINE_WITHIN] if line.startswith(STATUS_PREFIX)), "")
    rows = plan_task_rows(text)
    tasks = [{"n": plain(row.get("#", "")), "task": row.get("Task", ""), "fsd": row.get("FSD", ""),
              "role": plain(row.get("Role", "")), "status": plain(row.get("Status", "")),
              "jira": row.get("Jira", ""), "pr": row.get("PR", ""), "merged": plain(row.get("Merged", "")),
              "runs": sorted(set(RUN_ID.findall(row.get("Task", ""))))} for row in rows or []]
    upcoming = next((t["n"] for t in tasks if is_ours(t["role"], our_role) and t["status"] in OPEN_TASK_STATUSES
                     and t["n"].isdigit()), "")
    return {"stem": stem, "path": path, "title": title or stem, "status_line": status[len(STATUS_PREFIX):].strip(),
            "has_tasks": rows is not None, "tasks": tasks, "next": upcoming, "our_role": our_role}


def plan_paths(backlog: dict, plans_dir: str) -> list[str]:
    """The plan files a backlog names — its roll-up's plans and its features' Plan links — under plans_dir."""
    named = [p.get("path", "") for p in backlog.get("plans") or [] if isinstance(p, dict)]
    named += [f.get("plan", "") for f in backlog.get("features") or [] if isinstance(f, dict)]
    found: list[str] = []
    for path in named:
        if not isinstance(path, str) or not path.endswith(".md"):
            continue
        folder, _, name = path.rpartition("/")
        if folder == plans_dir and PLAN_STEM.fullmatch(name[:-3]) and path not in found:
            found.append(path)
    return sorted(found)


def plan_details(repo: Path, ref: str, paths: list[str], our_role: str = DEFAULT_OUR_ROLE) -> dict[str, dict]:
    """
    Each plan's detail at `ref`, by its stem, read through git in one process (the book is read where
    `factory backlog` read it). A plan not on that ref has an `error` instead of tasks.
    """
    held = git_batch(repo, [f"{ref}:{path}" for path in paths]) if ref else {}
    details: dict[str, dict] = {}
    for path in paths:
        stem = Path(path).stem
        data = held.get(f"{ref}:{path}")
        details[stem] = (plan_detail(data.decode("utf-8", "replace"), path, our_role) if data is not None
                         else {"stem": stem, "path": path, "error": f"{path}: not on {ref or 'any ref'}"})
    return details


# -- the truth about progress: what the plans say, corrected by what the runs did ---------------------
# A plan's Tasks table is written by people and by runs; a run that merged its work into main may leave its row at
# "In progress (R-0xx)". The pages show what happened: a task is built when its row says Built, or when a run for it
# finished and its work reached the project's main version. Computed here, never written back to the repository.

MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
DELIVERED = re.compile(r"deliver", re.IGNORECASE)
# The run id a run writes into its task's cell — `(R-010)` — which a person reading the task need not see.
RUN_IN_TITLE = re.compile(r"\s*\(R-\d+\)")


def feature_slug(name: str) -> str:
    """A feature's name as a plan item — the referee's feature_slug: `Book Loans` → `book-loans`."""
    plain_name = " ".join(MD_LINK.sub(r"\1", name).replace("**", "").split())
    return re.sub(r"[^a-z0-9]+", "-", plain_name.replace("`", "").lower()).strip("-")[:60].rstrip("-")


def plain_text(text: str) -> str:
    """A table cell as words: links reduced to their text, emphasis and code ticks gone."""
    return " ".join(plain(MD_LINK.sub(r"\1", str(text or ""))).split())


def run_built(run: dict) -> bool:
    """A run whose work reached the project's main version: every step passed, closed as delivered, or approved at
    the merge gate and merged into main. A practice (dry) run builds nothing."""
    if run.get("dry"):
        return False
    if run.get("status") == "done":
        return True
    if run.get("status") == "abandoned":
        return bool(DELIVERED.search(str((run.get("abandoned") or {}).get("reason") or "")))
    merge = run.get("merge") or {}
    return bool(run.get("open") and merge.get("passed") and merge.get("merged") is True)


def run_going(run: dict) -> bool:
    """A real run still under way: open, and its work not in main yet."""
    return bool(run.get("open") and not run.get("dry") and not run_built(run))


def plan_progress(stem: str, detail: dict, runs: list[dict]) -> dict:
    """
    One plan's tasks as they really stand. Each task is `built` (its row says Built, or a run for it is built),
    `blocked` (its row says so), `building` (a run for it is under way), `next` (the first of ours left), `todo`
    (ours, after the next) or `other` (another role's, not built). A task's runs are the runs whose item is
    `plan:<stem>#<n>` and the run ids its Task cell names.
    """
    our_role = detail.get("our_role") or DEFAULT_OUR_ROLE
    by_id = {r.get("id"): r for r in runs}
    tasks, nxt = [], ""
    for task in detail.get("tasks") or []:
        n = task.get("n", "")
        related = [r for r in runs if r.get("item") == f"plan:{stem}#{n}"]
        related += [by_id[i] for i in task.get("runs") or [] if i in by_id and by_id[i] not in related]
        going = next((r for r in related if run_going(r)), None)
        ours = is_ours(str(task.get("role") or ""), our_role)
        status = str(task.get("status") or "")
        if status.lower() == "built" or any(run_built(r) for r in related):
            state = "built"
        elif status.lower().startswith("blocked"):
            state = "blocked"
        elif going is not None:
            state = "building"
        elif not ours:
            state = "other"
        elif not nxt and n.isdigit():
            state, nxt = "next", n
        else:
            state = "todo"
        tasks.append({"n": n, "title": RUN_IN_TITLE.sub("", plain_text(task.get("task", ""))), "state": state, "ours": ours,
                      "run": (going or {}).get("key", ""), "run_title": (going or {}).get("title", "")})
    mine = [t for t in tasks if t["ours"]]
    return {"stem": stem, "title": detail.get("title") or stem, "our_role": our_role, "tasks": tasks, "next": nxt,
            "ours": len(mine), "built": sum(t["state"] == "built" for t in mine),
            "building": sum(t["state"] == "building" for t in mine)}


FEATURE_WORDS = {"done": "Done", "building": "Being built", "planned": "Planned next", "not-started": "Not started",
                 "blocked": "Blocked"}


def progress(backlog: dict, runs: list[dict]) -> dict:
    """
    What the Progress page shows, one card per feature in the book's order, then any plan no feature row links:
    the feature's state — Done (every task of ours built, or its row says Built), Being built (some built, or a run
    under way), Planned next (no plan yet), Blocked, Not started — with its tasks as they really stand.
    """
    details = backlog.get("plan_details") or {}
    plans = {stem: plan_progress(stem, d, runs) for stem, d in details.items() if isinstance(d, dict) and not d.get("error")}
    cards, used = [], set()
    for feature in backlog.get("features") or []:
        if not isinstance(feature, dict):
            continue
        name = plain_text(feature.get("feature", ""))
        slug = feature_slug(str(feature.get("feature", "")))
        stem = Path(str(feature.get("plan") or "")).stem if feature.get("plan") else ""
        plan = plans.get(stem)
        used.add(stem)
        row = plain_text(feature.get("status", "")).lower()
        writing = next((r for r in runs if r.get("item") == f"plan:{slug}" and run_going(r)), None) if slug else None
        if (plan and plan["ours"] and plan["built"] == plan["ours"]) or row.startswith(("built", "done")):
            state = "done"
        elif plan and (plan["built"] or plan["building"]):
            state = "building"
        elif row.startswith("blocked"):
            state = "blocked"
        elif not stem:
            state = "planned"
        else:
            state = "not-started"
        cards.append(card(name, state, plan, writing, plain_text(feature.get("note", "")) if state == "blocked" else "",
                          slug if not stem else "", feature))
    for stem, plan in plans.items():
        if stem not in used:
            state = ("done" if plan["ours"] and plan["built"] == plan["ours"] else
                     "building" if plan["built"] or plan["building"] else "not-started")
            cards.append(card(plan["title"], state, plan, None, "", "", None))
    return {"cards": cards, "plans": plans}


def card(name: str, state: str, plan: dict | None, writing: dict | None, note: str, slug: str,
         feature: dict | None) -> dict:
    row = feature or {}
    return {"name": name, "state": state, "label": FEATURE_WORDS[state], "plan": plan["stem"] if plan else "",
            "built": plan["built"] if plan else 0, "total": plan["ours"] if plan else 0,
            "tasks": plan["tasks"] if plan else [], "next": plan["next"] if plan else "",
            "writing": (writing or {}).get("key", ""), "note": note, "slug": slug,
            # The feature's own row in the tracker, as the referee read it: the book's Contents shows it as written.
            "row": {"phase": plain_text(row.get("phase", "")), "fsd": plain_text(row.get("fsd", "")),
                    "owner": plain_text(row.get("owner", "")), "depends_on": plain_text(row.get("depends_on", "")),
                    "status": plain_text(row.get("status", "")), "note": plain_text(row.get("note", "")),
                    "plan_path": str(row.get("plan") or "")} if feature is not None else None}


# -- the book: what the console reads beside the referee's backlog --------------------------------------
# The referee's backlog lists the features, the plans, the OPEN defects and the OPEN questions. The book also has
# fixed defects, answered questions, its FSD versions and (where /fsd-audit wrote one) a traceability matrix. These
# are read here, read-only, at the backlog's own ref, from the files the project's factory.toml names — and each one
# is linked to the chapter (feature) it belongs to by the FSD sections it cites. Nothing is written back.

DEFAULT_DEFECT_LOGS = ["docs/defects.md"]
DEFAULT_DECISIONS = ["docs/open-questions.md"]
DEFAULT_TRACE = "docs/Audit/spec-traceability.md"
SEVERITY_BY_ICON = {"\U0001F534": "unsafe", "\U0001F7E0": "wrong", "\U0001F7E1": "contained", "\U0001F535": "minor"}
DEFECT_ROW_ID = re.compile(r"^\*{0,2}(D\d+)\*{0,2}$")
BOLD = re.compile(r"\*\*(.+?)\*\*")
# An FSD section a text cites: `§2.12.3.4.8.a` (the letters are a list item, not a section), `§2.13.1–5` (a range on
# the last part). A feature row's FSD cell names sections without the sign.
CITED_SECTION = re.compile(r"§\s*(\d+(?:\.\d+)*)(?:\s*[–-]\s*(\d+)(?![.\d]))?")
CELL_SECTION = re.compile(r"(\d+(?:\.\d+)+)(?:\s*[–-]\s*(\d+)(?![.\d]))?")
SECTION_RANGE_MAX = 50
PLAN_MENTION = re.compile(r"plans/([a-z0-9][a-z0-9-]{0,59})\.md")
ITEM_ID = re.compile(r"\b([DQ]\d{1,6})\b")
QUESTION_HEADING = re.compile(r"^#{3,4}\s+(Q\d+)\s*[·—–:-]\s*(.+?)\s*$")
DECISION_LINE = re.compile(r"^`(ANSWERED|SUPERSEDED|OPEN)`\s*(?:·\s*([^·]*?)\s*)?(?:·\s*([^—]*?)\s*)?(?:—\s*(.*))?$")
TRACE_ID = re.compile(r"^`?([A-Z]{2,6}-[A-Za-z0-9.\-]+)`?$")
TRACE_STATUSES = ("VERIFIED", "PARTIAL", "MISSING", "CONTRADICTED", "UNVERIFIED", "NOT_APPLICABLE", "UNTRACED")
TRACE_STATUS = re.compile(r"\b(" + "|".join(TRACE_STATUSES) + r")\b")
TRACE_REFRESHED = re.compile(r"(\d{4}-\d{2}-\d{2})\D{1,40}?FSD\s+(V\d+)")
BOOK_FIELD_MAX = {"title": 220, "where": 140, "note": 320, "history": 160, "detail": 600, "rule": 360, "requirement": 130}


def clipped(text: str, field: str) -> str:
    limit = BOOK_FIELD_MAX[field]
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def expand_sections(pattern: re.Pattern[str], text: str) -> list[str]:
    """The FSD sections a text cites, in order, each once; a range `2.13.1–5` is 2.13.1 … 2.13.5."""
    found: list[str] = []
    for match in pattern.finditer(text or ""):
        first, last = match.group(1), match.group(2)
        names = [first]
        if last:
            head, _, start = first.rpartition(".")
            if start.isdigit() and int(last) > int(start) and int(last) - int(start) <= SECTION_RANGE_MAX:
                names = [f"{head}.{n}" if head else str(n) for n in range(int(start), int(last) + 1)]
        for name in names:
            if name not in found:
                found.append(name)
    return found


def cited_sections(text: str) -> list[str]:
    return expand_sections(CITED_SECTION, text)


def heading_sections(text: str) -> list[str]:
    """A feature row's FSD cell or a block heading: sections with or without §."""
    return expand_sections(CELL_SECTION, (text or "").replace("§", ""))


def book_defects(text: str, source: str) -> list[dict]:
    """
    Every defect row of a defect log, whatever section it is in: `## Open` (and anything not under Fixed or Won't
    fix) is open, `## Fixed` fixed, `## Won't fix…` closed without a fix. Columns are found by their header, so the
    live file's one-line Fixed rows and the archive's full rows read alike. Each row keeps the sections it cites.
    """
    rows: list[dict] = []
    part, owner, columns = "open", "", None
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            title = stripped[3:].lower()
            part = "fixed" if title.startswith("fixed") else "wontfix" if title.startswith("won") else "open"
            owner, columns = "", None
            continue
        if stripped.startswith("### "):
            owner, columns = stripped[4:].strip(), None
            continue
        cells = table_cells(line)
        if cells is None:
            columns = None
            continue
        if columns is None:
            columns = {plain(c).lower(): i for i, c in enumerate(cells)}
            continue
        found = DEFECT_ROW_ID.match(cells[0]) if cells else None
        if not found:
            continue

        def cell(*names: str) -> str:
            for name in names:
                if name in columns and columns[name] < len(cells):
                    return cells[columns[name]]
            return ""

        body = cell("defect")
        bold = BOLD.search(body)
        icon = cell("sev").replace("️", "").strip()
        status = plain_text(cell("status")) or {"fixed": "Fixed", "wontfix": "Won't fix"}.get(part, "")
        state = part if part != "open" else ("open" if status.lower().startswith(("open", "partial", "mitigated", "blocked"))
                                            else "fixed" if status.lower().startswith("fixed") else "open")
        rows.append({"id": found.group(1), "state": state, "status": status, "severity": SEVERITY_BY_ICON.get(icon, ""),
                     "severity_icon": icon if icon in SEVERITY_BY_ICON else "",
                     "title": clipped(plain_text(bold.group(1) if bold else body), "title"),
                     # A closed row keeps a short history; its full story is one click away in the file.
                     "where": clipped(plain_text(cell("where")), "where") if state == "open" else "",
                     "note": clipped(plain_text(cell("note", "why")), "note" if state == "open" else "history"),
                     "fixed_in": clipped(plain_text(cell("fixed in")), "where"),
                     "owner": owner if part == "open" else plain_text(cell("addressed by")),
                     "sections": cited_sections(" ".join(cells)), "plans": sorted(set(PLAN_MENTION.findall(line))),
                     "source": source})
    return rows


def book_questions(text: str, source: str) -> list[dict]:
    """
    Every question with a heading of its own — `#### Q<n> · <heading>` — and its status: an `ANSWERED` or
    `SUPERSEDED` line under it (the rule now in force, the date, who decided), or `OPEN` (the open questions in full,
    whose first paragraph is kept as the context). The topic is the `###` heading above a decided one.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    found: list[dict] = []
    topic, part = "", ""
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("## "):
            part, topic = stripped[3:].strip(), ""
        elif stripped.startswith("### ") and not QUESTION_HEADING.match(stripped):
            topic = stripped[4:].strip()
        heading = QUESTION_HEADING.match(stripped)
        if heading:
            body: list[str] = []
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("#"):
                body.append(lines[j])
                j += 1
            text_body = "\n".join(body).strip()
            decided = next((DECISION_LINE.match(b.strip()) for b in body if DECISION_LINE.match(b.strip())), None)
            status_match = re.search(r"\*\*Status:\*\*\s*`?([A-Z]+)", text_body)
            status = decided.group(1) if decided else (status_match.group(1) if status_match else "")
            if not status and part.lower().startswith("resolved"):
                status = "ANSWERED"
            entry = {"id": heading.group(1), "heading": clipped(plain_text(heading.group(2)), "title"),
                     "status": status or "OPEN", "topic": topic if part.lower().startswith(("resolved", "superseded")) else "",
                     "date": "", "by": "", "rule": "", "detail": "", "sections": cited_sections(stripped + " " + text_body),
                     "plans": sorted(set(PLAN_MENTION.findall(text_body))), "source": source}
            if decided:
                entry.update(date=(decided.group(2) or "").strip(), by=(decided.group(3) or "").strip(),
                             rule=clipped(plain_text(re.sub(r"\[Full record\]\([^)]*\)", "", decided.group(4) or "")), "rule"))
            else:
                paragraph = next((p for p in re.split(r"\n\s*\n", text_body) if p.strip() and not p.strip().startswith(("**Status", "|"))), "")
                entry["detail"] = clipped(plain_text(paragraph), "detail")
            found.append(entry)
            i = j
            continue
        i += 1
    return found


def book_trace(text: str | None, path: str) -> dict:
    """
    The traceability matrix /fsd-audit writes (requirement → plan → code → test), one block per `## ` heading: its
    FSD sections and plans, each requirement row's id, FSD reference, words and current status (the last status word
    in its Status cell: `PARTIAL → VERIFIED` is VERIFIED), and counts. `state` is `absent` (no file), `template` (the
    file holds no requirement row yet — "not traced yet") or `traced`.
    """
    if text is None:
        return {"state": "absent", "path": path, "blocks": [], "totals": {}, "refreshed": "", "fsd": ""}
    blocks: list[dict] = []
    block: dict | None = None
    columns: dict | None = None
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            title = stripped[3:].strip()
            name = re.split(r"\s+—\s+(?:FSD\b|plans?\b|§)", title)[0]
            block = {"title": clipped(plain_text(name), "title"), "heading": clipped(plain_text(title), "title"),
                     "sections": heading_sections(title), "plans": sorted(set(PLAN_MENTION.findall(title))), "rows": [],
                     "counts": {}}
            blocks.append(block)
            columns = None
            continue
        cells = table_cells(line)
        if cells is None:
            columns = None
            continue
        if columns is None:
            columns = {plain(c).lower(): i for i, c in enumerate(cells)}
            continue
        if block is None or "status" not in columns or not cells or not TRACE_ID.match(cells[0]):
            continue
        status_cell = cells[columns["status"]] if columns["status"] < len(cells) else ""
        statuses = TRACE_STATUS.findall(status_cell)
        if not statuses:
            continue
        status = statuses[-1]
        words = next((cells[columns[n]] for n in ("requirement", "what it does") if n in columns and columns[n] < len(cells)), "")
        fsd = next((cells[columns[n]] for n in ("fsd ref", "fsd") if n in columns and columns[n] < len(cells)), "")
        block["rows"].append({"id": TRACE_ID.match(cells[0]).group(1), "fsd": clipped(plain_text(fsd), "where"),
                              "requirement": clipped(plain_text(words), "requirement"), "status": status})
        block["counts"][status] = block["counts"].get(status, 0) + 1
    blocks = [b for b in blocks if b["rows"]]
    totals: dict[str, int] = {}
    for b in blocks:
        for status, n in b["counts"].items():
            totals[status] = totals.get(status, 0) + n
    refreshed = TRACE_REFRESHED.search(text)
    return {"state": "traced" if blocks else "template", "path": path, "blocks": blocks, "totals": totals,
            "refreshed": refreshed.group(1) if refreshed else "", "fsd": refreshed.group(2) if refreshed else ""}


def book_config(checkout: Path) -> dict:
    """The book's files, as the project's factory.toml names them (the referee's keys), with defaults."""
    raw: dict = {}
    with contextlib.suppress(OSError, tomllib.TOMLDecodeError):
        raw = tomllib.loads((checkout / FACTORY_TOML).read_text(encoding="utf-8"))
    requirements = raw.get("requirements") if isinstance(raw.get("requirements"), dict) else {}
    sources = requirements.get("sources") if isinstance(requirements.get("sources"), dict) else {}
    book = raw.get("book") if isinstance(raw.get("book"), dict) else {}

    def paths(value: object, default: list[str]) -> list[str]:
        given = value if isinstance(value, list) else default
        return [str(p).strip("/") for p in given if isinstance(p, str) and REL_PATH.fullmatch(str(p).strip("/"))
                and ".." not in str(p).split("/")]

    fsd_dir = str(sources.get("fsd_dir") or DEFAULT_FSD_DIR).strip("/")
    trace = str(requirements.get("traceability") or DEFAULT_TRACE).strip("/")
    return {"defect_logs": paths(requirements.get("defect_logs"), DEFAULT_DEFECT_LOGS),
            "decisions": paths(sources.get("decisions"), DEFAULT_DECISIONS),
            "fsd_dir": fsd_dir if REL_PATH.fullmatch(fsd_dir) else DEFAULT_FSD_DIR,
            "fsd_version": str(book.get("fsd_version") or DEFAULT_BOOK_FSD_VERSION),
            "trace": trace if REL_PATH.fullmatch(trace) and ".." not in trace.split("/") else DEFAULT_TRACE}


def answered_on_branch(repo: Path, ref: str, decisions: list[str], questions: dict[str, dict]) -> None:
    """
    A question open at `ref` that a person has answered on the branch the folder is on (the app's Answer commits it
    there, never on the base): shown answered, with `pending` naming that branch, until the branch reaches `ref`.
    """
    branch = git_out(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if not decisions or not BRANCH_NAME.fullmatch(branch or "") or branch == ref.rsplit("/", 1)[-1]:
        return
    text = git_out(repo, "show", f"HEAD:{decisions[0]}")
    for entry in book_questions(text, decisions[0]) if text else []:
        held = questions.get(entry["id"])
        if held and held["status"] == "OPEN" and entry["status"] in ("ANSWERED", "SUPERSEDED"):
            held.update({key: entry[key] for key in ("status", "rule", "date", "by", "topic")}, pending=branch)


def book_sources(repo: Path, ref: str) -> dict:
    """
    The rest of the book at `ref`, read through git in two processes: every defect (open, fixed, not ours), every
    question with a heading of its own, the FSD versions held, and the traceability matrix. Read only; a file that is
    not on the ref is named in `errors`, never guessed.
    """
    config = book_config(repo)
    errors: list[str] = []
    if not ref or not BRANCH_NAME.fullmatch(ref) or ".." in ref:
        return {"ref": ref, "defects": [], "questions": [], "fsd_versions": [], "trace": book_trace(None, config["trace"]),
                "errors": ["no ref to read the book at"]}
    names = [*config["defect_logs"], *config["decisions"], config["trace"]]
    held = git_batch(repo, [f"{ref}:{name}" for name in names])

    def text_of(name: str) -> str | None:
        data = held.get(f"{ref}:{name}")
        return data.decode("utf-8", "replace") if data is not None else None

    defects: dict[str, dict] = {}
    for name in config["defect_logs"]:
        text = text_of(name)
        if text is None:
            errors.append(f"{name}: not on {ref}")
            continue
        for row in book_defects(text, name):
            held_row = defects.get(row["id"])
            if held_row is None:
                defects[row["id"]] = row   # the live log before its archive
            else:                          # the archive's full row fills what the live one-liner leaves out
                for key in ("severity", "severity_icon", "where", "note", "fixed_in", "owner"):
                    held_row[key] = held_row[key] or row[key]
                held_row["sections"] = held_row["sections"] or row["sections"]
                held_row["plans"] = held_row["plans"] or row["plans"]
    questions: dict[str, dict] = {}
    for name in config["decisions"]:
        text = text_of(name)
        if text is None:
            errors.append(f"{name}: not on {ref}")
            continue
        for entry in book_questions(text, name):
            held_q = questions.get(entry["id"])
            if held_q is None:
                questions[entry["id"]] = entry
            else:
                for key in ("rule", "detail", "date", "by", "topic"):
                    held_q[key] = held_q[key] or entry[key]
                held_q["sections"] = held_q["sections"] or entry["sections"]
    answered_on_branch(repo, ref, config["decisions"][:1], questions)
    try:
        pattern = re.compile(config["fsd_version"])
    except re.error:
        pattern = re.compile(DEFAULT_BOOK_FSD_VERSION)
    versions = []
    for path in git_out(repo, "ls-tree", "-r", "--name-only", ref, "--", f"{config['fsd_dir']}/").splitlines():
        match = pattern.search(path)
        if match:
            versions.append({"version": f"V{int(match.group(1))}", "n": int(match.group(1)), "file": path.rsplit("/", 1)[-1],
                             "path": path})
    versions.sort(key=lambda v: (-v["n"], v["file"]))
    return {"ref": ref, "defects": sorted(defects.values(), key=lambda d: -int(d["id"][1:])),
            "questions": sorted(questions.values(), key=lambda q: -int(q["id"][1:])),
            "fsd_versions": versions, "trace": book_trace(text_of(config["trace"]), config["trace"]), "errors": errors}


def item_chapters(item: dict, owners: list[tuple[str, list[str], str]], names: list[tuple[str, re.Pattern[str]]]) -> tuple[list[str], str]:
    """
    The chapters an item (a defect, a question, a traceability block) belongs to, and how it was matched: by the most
    specific FSD section it cites that a chapter's row cites (or contains), else by the plan it names, else by the
    chapter's name in its words. ([], "") when nothing ties it to one — it is then shown under "Across the project".
    """
    best: dict[str, int] = {}
    for section in item.get("sections") or []:
        for chapter, prefixes, _ in owners:
            for prefix in prefixes:
                if section == prefix or section.startswith(prefix + "."):
                    depth = prefix.count(".") + 1
                    best[chapter] = max(best.get(chapter, 0), depth)
    if best:
        top = max(best.values())
        return [c for c, d in best.items() if d == top], "section"
    plans = set(item.get("plans") or [])
    by_plan = [chapter for chapter, _, stem in owners if stem and stem in plans]
    if by_plan:
        return by_plan, "plan"
    words = " ".join(str(item.get(k) or "") for k in ("title", "heading", "question", "module", "where", "rule", "topic"))
    by_name = [chapter for chapter, pattern in names if pattern.search(words)]
    return by_name, "name" if by_name else ""


def chapters(backlog: dict, progress_view: dict, runs: list[dict]) -> dict:
    """
    The book's chapters — one per feature row of the tracker, in its order, then each plan no row links — each with
    what belongs to it: its row, its plan's tasks as they really stand (progress), the defects and questions that cite
    its FSD sections (or name its plan or the feature), its traceability blocks and its factory runs. Items no chapter
    claims are kept under `across`, never dropped. Computed from what was read; nothing is written.
    """
    sources = backlog.get("book") or {}
    used: set[str] = set()
    out: list[dict] = []
    for c in progress_view.get("cards") or []:
        slug = feature_slug(c["name"]) or (c.get("plan") or "chapter")
        cid, n = slug, 2
        while cid in used:
            cid, n = f"{slug}-{n}", n + 1
        used.add(cid)
        row = c.get("row") or {}
        out.append({**c, "id": cid, "phase": row.get("phase") or ("Plans with no feature row" if c.get("row") is None else ""),
                    "sections": heading_sections(row.get("fsd", "")), "defects": [], "questions": [], "trace": [],
                    "runs": [], "links": {}})
    owners = [(c["id"], c["sections"], c.get("plan") or "") for c in out]
    names: list[tuple[str, re.Pattern[str]]] = []
    for c in out:
        base = re.split(r"\s+[—–(]\s*", c["name"])[0].strip()
        if len(base) >= 4:
            names.append((c["id"], re.compile(r"(?<![\w-])" + re.escape(base) + r"(?![\w-])", re.IGNORECASE)))
    by_id = {c["id"]: c for c in out}
    # A defect or question the chapter's own row or plan tasks name (`(D4)`, `(Q1, Q2)`) is that chapter's too.
    named_in: dict[str, list[str]] = {}
    details = backlog.get("plan_details") or {}
    for c in out:
        row = c.get("row") or {}
        words = " ".join([row.get("status", ""), row.get("note", "")] +
                         [str(t.get("task", "")) for t in (details.get(c.get("plan") or "") or {}).get("tasks") or []])
        for ident in sorted(set(ITEM_ID.findall(words))):
            named_in.setdefault(ident, []).append(c["id"])
    across = {"defects": [], "questions": [], "trace": []}
    open_q = {q.get("id"): q for q in backlog.get("questions") or [] if isinstance(q, dict)}
    questions = {q["id"]: dict(q) for q in sources.get("questions") or []}
    for qid, q in open_q.items():   # the referee's open rows: the module and the question in the table's own words
        entry = questions.setdefault(qid, {"id": qid, "heading": "", "status": "OPEN", "sections": [], "plans": []})
        entry.update(status="OPEN", module=q.get("module", ""), question=q.get("question", ""),
                     suggestion=q.get("suggestion", ""), owner=q.get("owner", ""), release=q.get("release", ""))
        entry["sections"] = sorted(set(entry.get("sections") or []) | set(cited_sections(q.get("module", "") + " " + q.get("question", ""))))
    for kind, items in (("defects", sources.get("defects") or []), ("questions", list(questions.values()))):
        for item in items:
            if kind == "questions" and item["id"] not in open_q and item.get("status") == "OPEN":
                continue   # an `OPEN` heading the referee does not list as open is history, not a live question
            found, how = item_chapters(item, owners, names)
            hows = {cid: how for cid in found}
            for cid in named_in.get(item["id"], []):
                hows[cid] = "named"   # the chapter itself names it: the strongest tie
            if any(h == "named" for h in hows.values()) and how == "name":
                hows = {cid: h for cid, h in hows.items() if h == "named"}   # a guess by name gives way to a citation
            found = list(hows)
            for cid in found:
                by_id[cid][kind].append(item["id"])
                by_id[cid]["links"][item["id"]] = hows[cid]
            if not found:
                across[kind].append(item["id"])
    trace = sources.get("trace") or {}
    for index, block in enumerate(trace.get("blocks") or []):
        found, how = item_chapters({"sections": block.get("sections"), "plans": block.get("plans"), "title": block.get("title")},
                                   [(cid, secs, stem) for cid, secs, stem in owners], names)
        for cid in found:
            by_id[cid]["trace"].append(index)
        if not found:
            across["trace"].append(index)
    defect_of = {}
    for c in out:
        for d in c["defects"]:
            defect_of.setdefault(d, []).append(c["id"])
        for q in c["questions"]:
            defect_of.setdefault(q, []).append(c["id"])
    for run in runs:
        item = str(run.get("item") or "")
        targets: list[str] = []
        if item.startswith("plan:"):
            stem = item[5:].split("#", 1)[0]
            targets = [c["id"] for c in out if c.get("plan") == stem or feature_slug(c["name"]) == stem]
        elif item in defect_of:
            targets = defect_of[item]
        for cid in targets:
            if run.get("key") and run["key"] not in by_id[cid]["runs"]:
                by_id[cid]["runs"].append(run["key"])
    phases: list[dict] = []
    for c in out:
        if not phases or phases[-1]["name"] != c["phase"]:
            phases.append({"name": c["phase"], "chapters": []})
        phases[-1]["chapters"].append(c["id"])
    return {"chapters": out, "phases": phases, "across": across, "questions": sorted(questions.values(), key=lambda q: -int(q["id"][1:]))}


# -- small shared helpers ------------------------------------------------------------------------


def repository_of(repo: Path) -> str:
    """The remote `origin` as a web address with any user name or credential removed; "" when there is none."""
    url = git_out(repo, "remote", "get-url", "origin").strip()
    if not url:
        return ""
    url = re.sub(r"^git@([^:]+):", r"https://\1/", url)        # git@host:team/repo.git
    url = re.sub(r"^(https?://)[^/@]*@", r"\1", url)           # https://user:token@host/…
    return url.removesuffix(".git") if url.startswith(("http://", "https://")) else ""


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


VOLATILE = ("generated", "version", "cached", "age_seconds", "output", "command", "cwd")


def version_of(answer: dict) -> str:
    """A short digest of an answer's data — the same data gives the same version, whenever it was read."""
    data = {key: value for key, value in answer.items() if key not in VOLATILE}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]
