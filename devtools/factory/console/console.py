"""Factory Console: watch and drive the AI factory's referee from a browser on this machine.

    python console.py [--project <path-to-checkout> [--project <another>]] [--kit <factory kit>] [--port 8770]
    python console.py --export <page.html> [--project <path-to-checkout> ...]
    python console.py --add-project <checkout> [--name <shown name>]  |  --remove-project <checkout or name>

One app for all of a person's projects; each keeps its own factory (referee, factory.toml, guard and
checks, committed in that project), and the console only runs that project's own referee. Without
--project it opens the person's project list — ~/.factory-console/projects.toml, or the file
FACTORY_CONSOLE_PROJECTS names, outside every repository; --project wins and the list is not read.
A list entry that is not a checkout is skipped and named by its folder, never its path. Each
project's factory version comes from its referee (`factory.py version --json`); the newest is the
referee beside this console.

A project is one repository. Given any folder of it (or several), the console finds the repository's worktrees
itself — afresh on each action, so a worktree added later needs no restart — and shows one project, named by
--name or the list's `name`, else after the main worktree's folder made readable (`my-shop` → My Shop).
An action names its run (or the run's branch) and runs in the folder that has that branch open; each folder has its
own jobs and lock. No folder name or path reaches the page. The Progress page's truth is computed here
(app_data.progress): a plan task whose run merged into main counts as built, whatever its row says; the repository's
documents are never rewritten. A decision may be held for a demo (POST /api/hold): the holds are kept in
~/.factory-console/holds.json (or FACTORY_CONSOLE_HOLDS), outside every repository. A drive or check in a folder whose
front end has no packages runs `npm ci` there first, in the same job.

One page, two modes. Started as a server it is the live console below. With --export it writes the
same page as a read-only snapshot — the data embedded, no session key, no action code and no buttons —
plus `<page>.data.json` beside it (written atomically), which an open snapshot page polls and applies
in place; it never reloads itself.

The console is a thin, local front for `python devtools/factory/factory.py <command>`.
It never decides anything itself and never works around the referee:

- It binds 127.0.0.1 only, and every API call needs the session token printed at start
  (header X-Console-Token), a Host of 127.0.0.1:<port> or localhost:<port>, and, on a
  POST, the matching Origin — so another web page cannot drive it.
- Every command is an argv built from a fixed allow-list; nothing a person types is ever
  handed to a shell. Each answer carries the exact argv, the folder it ran in, the exit
  code and the output, so nothing is hidden.
- The person's commands (approve, unlock, abandon) are the referee's to allow. A person
  starts the console from their own terminal, so a click is their decision; started from
  a Claude session, the referee refuses them, and the console shows that refusal as it is.
  The console never changes the environment to avoid it, never detaches, never edits run files.
- The Plans, Issues and Runs screens read `factory backlog --json` only; its answer is cached for
  30 seconds per project and ref.
- The Requirements screen stages an uploaded FSD PDF as `<log dir>/uploads/<sha256>.pdf` — outside
  every checkout; the console never writes `requirements/` — and previews it with the read-only
  `factory fsd-diff --staged <that path> --json`. The path is always built here from a validated
  sha256, never taken from a request. Taking the snapshot is a person's decision, made with
  /requirements-check; the console has no button for it.
- The Runs screens and a run's own page read `GET /api/runs`: every run on every branch and worktree,
  read through git by app_data.py beside this file — read-only, in-process, cached like the backlog.
- Work on demand — **Create plan** on a feature with none, **Implement next task** on a plan, **Fix this
  defect** — is `factory start <item> --title=<the book's words> --new-branch=<branch>`, one argv. The item
  is built from fields that each match a strict pattern, then checked against the backlog (a feature with
  no plan, the plan's next task for us, an open defect; no open run for it); the console refuses while a
  drive or check runs (409), while the folder has any uncommitted change, and while the branch it has
  checked out has an open run — `--new-branch` switches that folder's branch. `GET /api/new-run` answers
  what a start would run, and whether it would be refused now, without changing anything.
- Each plan's detail (status line and Tasks table) is read in-process at the backlog's ref and added to
  the backlog answer as `plan_details`; the read-only snapshot carries it, without any button.
- The page polls `/api/backlog` and `/api/runs` with `since=<version>` (or If-None-Match): an unchanged
  answer is `{"unchanged": true}` (or 304), so polling is cheap. The same token, Host and Origin rules apply.
- **New project** (Phase D, D5) runs the factory kit's own setup, `python <kit>/setup.py --greenfield|--brownfield
  … --json`, from the kit named with `--kit <folder>` — without it the page says how to start the console with
  one. The FSD is uploaded to `/api/setup/upload` and staged like the Requirements upload, as
  `<log dir>/uploads/<sha256>.<docx|pdf|md|txt>`, after its bytes are checked against the kind it was sent as.
  Every field is checked by wizard.py — absolute folder paths without `..`, no link on the way, a greenfield
  folder new or empty under a parent that exists and is in no repository, a brownfield folder the top of a git
  repository — before any process runs; `POST /api/setup/preview` answers the exact argv without running it.
  `POST /api/setup/run` starts it in the background, one setup at a time (409 while one runs); setup.py's own
  refusals come back as it wrote them. On success the project is added to the person's project list (the
  same code as --add-project) and opened in this console. Its next step, **Draft the book**, is
  `factory start book:<name> --title=… --new-branch=<branch>`, one argv, checked like the other work on demand
  (`/api/book/start`); the run's page then drives it to its book gate, whose screen shows the drafted
  project.md, the auditor's verdict and findings, and the Inventory, beside the usual Approve.

Python 3.11+, standard library only.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import traceback
import time
import tomllib
import webbrowser
from html import escape as html_escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import app_data
import wizard

HOST = "127.0.0.1"
# Names a browser may use for the bound address; the Host and Origin checks accept these only.
HOST_NAMES = ("127.0.0.1", "localhost")
DEFAULT_PORT = 8770
# The referee, relative to a project's checkout.
FACTORY_SCRIPT = Path("devtools") / "factory" / "factory.py"
INDEX_FILE = Path(__file__).resolve().parent / "index.html"

TOKEN_HEADER = "X-Console-Token"
ITEM_PATTERN = re.compile(r"^(D\d+|Q\d+|[A-Z][A-Z0-9]+-\d+|\d+(\.\d+)*|plan:[a-z0-9][a-z0-9-]{0,59}(#\d{1,3})?"
                          r"|book:[a-z0-9][a-z0-9-]{0,59})$")
# Work on demand: a new run on a branch of its own (`factory start <item> --new-branch`). Each part of the
# item is checked against these, then against the backlog, before anything runs.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")         # the referee's PLAN_ITEM slug and plan stem
TASK_PATTERN = re.compile(r"^[1-9]\d{0,2}$")                    # the referee's PLAN_TASK_ITEM task, no leading 0
DEFECT_PATTERN = re.compile(r"^D\d{1,6}$")
# The branch the console names for `--new-branch=`: what git and the referee accept, and no more.
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
# A [backlog.branches] template: plain text and the referee's named fields, no format specs or attributes.
BRANCH_TEMPLATE = re.compile(r"(?:[^{}]|\{(?:item|title|slug|stem|task|name|version)\})+")
NEW_RUN_ACTIONS = ("create-plan", "implement", "fix", "book", "revision")
NEW_RUN_PATHS = {"/api/plan/create": "create-plan", "/api/plan/implement": "implement", "/api/fix": "fix",
                 "/api/book/start": "book", "/api/revision/start": "revision"}
# A book run's title: the FSD it is written from (`Book from FSD V1`), as setup.py's next step names it.
BOOK_TITLE = "Book from FSD {version}"
# A revision run's: the new version it takes in, filed from the Requirements page's preview.
REVISION_TITLE = "Take in FSD V{version}"
MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
# A title the console writes from the book: one argv element, on one line, at most TITLE_MAX characters.
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")
# A plan run's title: the feature's name and this (Phase C design §5).
PLAN_TITLE_SUFFIX = " — plan"
RUN_KEY = re.compile(r"^[A-Za-z0-9-]{1,120}$")
RUN_ID = re.compile(r"^R-\d{3,6}$")
GIT_STEP_SECONDS = 2 * 60
# The book's generated files, rewritten from the plans and run files: a merge that clashes only there is finished by
# writing them again. project.md counts only when every clash lies inside one of its generated blocks.
ROLLUP_SCRIPT = Path("devtools") / "checks" / "gen-task-rollup.py"
PROJECT_MD = "docs/project.md"
GENERATED_BLOCK = re.compile(r"<!-- ([a-z-]+):begin\b.*?<!-- \1:end -->", re.S)
CLASH = re.compile(r"^<<<<<<< .*?^>>>>>>> [^\n]*\n?", re.S | re.M)
DRIVE_LETTER = re.compile(r"^[A-Za-z]:")
TITLE_MAX = 120
REASON_MAX = 600
TEST_PATH_MAX = 260
# A file a step may be allowed to write (factory.py allow_refusal): never an option, never outside the project.
ALLOW_PATTERN = re.compile(r"(?!-)(?!.*(?:^|/)\.\.(?:/|$))[\w.*/-]{1,260}")
ALLOW_MAX = 20
STEPS_MAX = 50
BODY_MAX_BYTES = 16 * 1024
# The copy of the card a person saw when they approved or answered, saved beside the run's record (the Decisions log).
SNAPSHOT_MAX = 256 * 1024
PROOF_FOLDER = "proof"
SCRIPT_TAG = re.compile(r"<script\b.*?</script\s*>", re.IGNORECASE | re.DOTALL)
EVENT_ATTRIBUTE = re.compile(r"""\s+on[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)
PAGE_STYLE = re.compile(r"<style>(.*?)</style>", re.DOTALL)
TAIL_LINES = 200
# A short command run while the request waits (status, start, approve …) gets this long.
COMMAND_TIMEOUT_SECONDS = 10 * 60
# After an interrupt (POSIX), how long the referee gets to end its own children.
STOP_GRACE_SECONDS = 15
# `factory backlog` reads only and takes a couple of seconds; a page switching tabs reuses its
# answer for this long (per project and ref) unless the person asks for a fresh read.
BACKLOG_CACHE_SECONDS = 30
BACKLOG_TIMEOUT_SECONDS = 2 * 60
# A git ref or commit to read the book from. `..` is refused (a range, not a ref), and so is a
# leading `-`, which the referee's argument parser would take for an option.
REF_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
# An uploaded FSD: a PDF of at most 50 MB, kept under the log folder by its sha256 and nowhere else.
UPLOAD_MAX_BYTES = 50 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
UPLOAD_CONTENT_TYPE = "application/pdf"
PDF_MAGIC = b"%PDF-"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
UPLOADS_FOLDER = "uploads"
# `factory fsd-diff` runs pdftotext on two PDFs of a few MB each and reads git: seconds, not minutes.
FSD_PREVIEW_TIMEOUT_SECONDS = 5 * 60

# Long commands run in the background, one at a time per project.
JOB_KINDS = ("drive", "check", "prepare")
# `factory approve <gate>`: the person's decision at each gate — plan for a plan run, outline and book
# for a book run, revision for a new FSD version (the referee's own choices).
GATES = ("spec", "design", "plan", "outline", "book", "revision", "merge")
# The steps in factory.toml that `factory reopen` takes; reopening a step clears its gate. `sources` and
# `draft-plan` are a plan run's; `outline`, `inventory`, `features` and `audit` a book run's; `changes` and
# `triage` a revision run's.
STEPS = ("intake", "context", "spec", "design", "plan", "tests", "build", "integrate",
         "verify", "review", "accept", "package", "release", "outcome", "sources", "draft-plan",
         "outline", "inventory", "features", "audit", "changes", "triage")
# Set in a process a Claude Code session started; the referee refuses a person's commands there.
CLAUDE_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
# Reading every run on every branch is a few git processes; a page polling every 15 s reuses it this long.
RUNS_CACHE_SECONDS = 15
# A version a poll may send back: the 16 hex characters app_data.version_of makes.
VERSION_PATTERN = re.compile(r"^[0-9a-f]{16}$")
# The read-only snapshot: the page with its live parts cut out and the data embedded.
DATA_SLOT = '<script id="factory-data" type="application/json"></script>'
LIVE_HTML = re.compile(r"<!--live:begin-->.*?<!--live:end-->", re.DOTALL)
LIVE_JS = re.compile(r"/\*live:begin\*/.*?/\*live:end\*/", re.DOTALL)
SIDECAR_SUFFIX = ".data.json"
SIDECAR_SCRIPT_SUFFIX = ".data.js"
START_HINT = "python devtools/factory/console/console.py --project <checkout>"
TASK_ROW = re.compile(r"^\| *(\d+) *\|")
# The actions the referee takes on the folder's current run: each may name the run's branch, and is refused elsewhere.
RUN_BOUND_PATHS = ("/api/approve", "/api/drive", "/api/check", "/api/reopen", "/api/unlock", "/api/abandon", "/api/open-pr")
# The branches work is promoted along: the app never pushes one (CLAUDE.md: never push to them).
SHARED_BRANCHES = ("dev", "qa", "uat", "main", "master")
CLASH_PARTS = re.compile(r"^<<<<<<< [^\n]*\n(.*?)^(?:\|{7}[^\n]*\n.*?)?^=======\n(.*?)^>>>>>>> [^\n]*\n?", re.S | re.M)


def clash_sides(clash: re.Match) -> tuple[str, str]:
    """The two sides of one git clash block, ours (HEAD) then theirs; a diff3 base section is left out."""
    parts = CLASH_PARTS.match(clash.group(0))
    return (parts.group(1), parts.group(2)) if parts else ("", "")


def merge_task_rows(text: str, base: str) -> str | None:
    """
    A plan whose every clash is between task rows (`| <n> | …`): each row comes from the side that changed it since
    the common base, as a run writes only its own task's row. None when a clash holds anything else, or both sides
    changed one row differently.
    """
    base_rows: dict[str, str] = {}
    for line in base.splitlines():
        match = TASK_ROW.match(line)
        if match:
            if match.group(1) in base_rows:
                return None  # more than one table numbers its rows: a row cannot be matched to its base
            base_rows[match.group(1)] = line
    failed = False

    def merge(clash: re.Match) -> str:
        nonlocal failed
        rows: list[dict[str, str]] = [{}, {}]
        for side, lines in enumerate(clash_sides(clash)):
            for line in lines.splitlines():
                match = TASK_ROW.match(line)
                if not line.strip():
                    continue
                if not match or match.group(1) in rows[side]:
                    failed = True
                    return clash.group(0)
                rows[side][match.group(1)] = line
        merged = []
        for number in dict.fromkeys([*rows[0], *rows[1]]):
            ours, theirs, was = rows[0].get(number), rows[1].get(number), base_rows.get(number)
            if ours == theirs or theirs == was:
                pick = ours
            elif ours == was:
                pick = theirs
            else:
                failed = True
                return clash.group(0)
            if pick is not None:
                merged.append(pick + "\n")
        return "".join(merged)

    clashes = CLASH.findall(text)
    result = CLASH.sub(merge, text)
    return None if failed or not clashes else result


class BadRequest(Exception):
    """A request the console refuses before running anything."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


def started_by_claude() -> bool:
    return any(marker in os.environ for marker in CLAUDE_MARKERS)


class Job:
    """One background referee command the console started: a drive or a check."""

    preparing = False

    def __init__(self, kind: str, command: list[str], process: subprocess.Popen[bytes], log: Path) -> None:
        self.kind = kind
        self.command = command
        self.process = process
        self.log = log
        self.started = time.time()  # the page shows the latest of a project's jobs (the 15 Oct review's #12)
        self.stopped = False  # a person pressed Stop
        self.lock = threading.Lock()

    def running(self) -> bool:
        return self.process.poll() is None

    def exit_code(self) -> int | None:
        return self.process.poll()


class PreparedJob(Job):
    """
    A drive or check that first prepares the folder's front ends — `npm ci` in each that has no packages yet — then
    runs, one command after another in one log. A step that fails, or a Stop, ends it there. Each command is a fixed
    argv; nothing goes through a shell.
    """

    def __init__(self, kind: str, steps: list[tuple[list[str], Path]], log: Path, env: dict) -> None:
        self.steps, self.env, self.at = steps, env, 0
        self.done = threading.Event()
        super().__init__(kind, steps[-1][0], self.launch(steps[0], log), log)
        threading.Thread(target=self.follow, daemon=True).start()

    def launch(self, step: tuple[list[str], Path], log: Path) -> subprocess.Popen[bytes]:
        command, cwd = step
        with open(log, "ab") as sink:
            sink.write(f"[console] {' '.join(command)}\n".encode("utf-8"))
            sink.flush()
            return subprocess.Popen(command, cwd=cwd, stdout=sink, stderr=subprocess.STDOUT, env=self.env)

    def follow(self) -> None:
        try:
            while True:
                code = self.process.wait()
                with self.lock:
                    if self.stopped or code != 0 or self.at == len(self.steps) - 1:
                        return
                    self.at += 1
                    try:
                        self.process = self.launch(self.steps[self.at], self.log)
                    except OSError as error:
                        with open(self.log, "ab") as sink:
                            sink.write(f"[console] could not run {self.steps[self.at][0][0]}: {error}\n".encode("utf-8"))
                        self.failed = True
                        return
        finally:
            self.done.set()

    failed = False

    def running(self) -> bool:
        return not self.done.is_set()

    def exit_code(self) -> int | None:
        if self.running():
            return None
        return -1 if self.failed else self.process.poll()

    @property
    def preparing(self) -> bool:  # type: ignore[override]
        return self.running() and self.at < len(self.steps) - 1


class Folder:
    """
    One working folder of a project: its main folder, or another git worktree of the same repository. Each keeps the
    jobs the console started in it and a lock, so one change runs at a time per folder — and work in two folders of
    one project can run side by side.
    """

    def __init__(self, path: Path, home: "Project | None" = None) -> None:
        self.path = path
        self.home: Project = home if home is not None else self  # type: ignore[assignment]
        self.name = home.name if home is not None else path.name
        self.jobs: dict[str, Job | None] = {kind: None for kind in JOB_KINDS}
        self.busy = threading.Lock()

    def branch(self) -> str:
        # symbolic-ref names the branch even before its first commit; a detached HEAD has none.
        for args, shown in ((["symbolic-ref", "--short", "HEAD"], "{}"),
                            (["rev-parse", "--short", "HEAD"], "(detached at {})")):
            try:
                done = subprocess.run(["git", *args], cwd=self.path, capture_output=True,
                                      text=True, timeout=30, check=False)
            except (OSError, subprocess.TimeoutExpired):
                return "(unknown)"
            if done.returncode == 0 and done.stdout.strip():
                return shown.format(done.stdout.strip())
        return "(unknown)"

    def running_job(self) -> Job | None:
        for job in self.jobs.values():
            if job is not None and job.running():
                return job
        return None


def same_folder(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


NOT_LISTED_PRIMARY = ("this work is open in the repository's own main folder, which you did not give the app, so the app "
                      "never acts there: act on it from that folder yourself, or start the app with that folder")
NO_OPEN_RUN_FOLDER = ("that working copy of the project has no open factory work, so the app does not act in it: "
                      "the app acts only in the folders you gave it and in copies that hold an open run")


class Project(Folder):
    """
    One project: one repository. Its main folder is the first folder the person gave the app (`--project` or the
    project list) — never the repository's primary checkout unless that is the folder given, since a person may be
    working there. Every other worktree of it is found afresh from git when an action needs it, so a worktree added
    later is used without a restart. A run is acted on in the folder that has its branch open, and only where the app
    may act: a folder the person gave it, or a worktree whose branch holds an open run — never the primary checkout
    the person did not give.
    """

    def __init__(self, path: Path, name: str | None = None, group: str = "", listed: list[Path] | None = None,
                 primary: Path | None = None) -> None:
        super().__init__(path)
        self.name = name or app_data.display_name(path.name)
        self.group = group or "folder-" + hashlib.sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest()[:10]
        self.others: dict[str, Folder] = {}
        self.folders_lock = threading.Lock()
        self.listed: list[Path] = [path, *(listed or [])]
        self.primary = primary
        # The branches that hold an open run (set by the Console from its run reader); `fresh` reads them again.
        self.open_branches = lambda fresh=False: set()

    def worktrees(self) -> dict[str, Path]:
        """Each branch open in a folder of this repository, and that folder."""
        return app_data.worktree_branches(self.path)

    def refusal_for(self, path: Path, branch: str | None, *, recheck: bool = True) -> str | None:
        """Why the app may not act in this folder, or None. A folder the person gave it: always. The repository's
        primary checkout they did not give: never. Any other worktree: only while its branch holds an open run (read
        again once when the cached runs do not show one, unless `recheck` is False)."""
        if any(same_folder(path, given) for given in self.listed):
            return None
        if self.primary is not None and same_folder(path, self.primary):
            return NOT_LISTED_PRIMARY
        if branch and (branch in self.open_branches() or (recheck and branch in self.open_branches(True))):
            return None
        return NO_OPEN_RUN_FOLDER

    def acting_branches(self) -> tuple[list[str], list[str]]:
        """The branches open in folders the app acts in, and those open where it does not (from the cached runs)."""
        mine, elsewhere = [], []
        for branch, path in sorted(self.worktrees().items()):
            (elsewhere if self.refusal_for(path, branch, recheck=False) else mine).append(branch)
        return mine, elsewhere

    def folder_at(self, path: Path, branch: str | None = None) -> Folder:
        if same_folder(path, self.path):
            return self
        refusal = self.refusal_for(path, branch)
        if refusal:
            raise BadRequest(refusal, HTTPStatus.CONFLICT)
        key = os.path.normcase(str(path.resolve()))
        with self.folders_lock:
            if key not in self.others:
                self.others = {**self.others, key: Folder(path.resolve(), self)}
            return self.others[key]

    def folder_for(self, branch: str | None) -> Folder:
        """The folder that has this branch open; the main folder when none has. A BadRequest (409) when the folder
        that has it open is one the app does not act in."""
        where = self.worktrees().get(branch) if branch else None
        return self.folder_at(where, branch) if where is not None else self

    def folders(self) -> list[Folder]:
        return [self, *self.others.values()]

    def running_anywhere(self) -> list[Folder]:
        return [f for f in self.folders() if f.running_job() is not None]


def group_projects(given: list[Path] | list[Path | tuple[Path, str]]) -> list[Project]:
    """
    One project per repository, in the order given: folders that are worktrees of one repository (`--project` given
    each, or listed twice) become one project, whose main folder is the repository's main worktree when that holds the
    factory. The first name given for any of them is its name; otherwise its main folder's, made readable.
    """
    found: dict[str, list] = {}
    for item in given:
        path, name = item if isinstance(item, tuple) else (item, None)
        group = app_data.repo_group(path)
        key = group or "folder-" + os.path.normcase(str(Path(path).resolve()))
        if key in found:
            found[key][1] = found[key][1] or name
            found[key][3].append(Path(path))
            continue
        primary = app_data.main_worktree(path) if group else None
        found[key] = [Path(path), name, group, [], primary]
    # Named after the repository (its primary checkout's folder), but acting in the folder the person gave.
    return [Project(home, name or app_data.display_name((primary or home).name), group, listed, primary)
            for home, name, group, listed, primary in found.values()]


class SetupJob:
    """The kit's setup.py, run in the background for the New project wizard: one at a time per console."""

    def __init__(self, plan: wizard.SetupPlan, process: subprocess.Popen[bytes], out: Path, err: Path) -> None:
        self.plan = plan
        self.process = process
        self.out = out
        self.err = err
        self.result: dict | None = None   # setup.py's JSON answer, once it has ended
        self.project: str | None = None   # the name it was opened under in this console
        self.note = ""                    # what happened to the person's project list
        self.done = threading.Event()     # set once the answer is read and the project added

    def running(self) -> bool:
        return not self.done.is_set()


class Console:
    """The state behind the HTTP handler: projects, the token, background jobs."""

    def __init__(self, projects: list[Path] | list[Path | tuple[Path, str]], python: str = sys.executable,
                 dry_prefixes: tuple[str, ...] = app_data.DEFAULT_DRY_PREFIXES, kit: wizard.Kit | None = None,
                 holds: "Holds | None" = None) -> None:
        self.token = secrets.token_urlsafe(32)
        self.python = python
        self.kit = kit
        self.setup_job: SetupJob | None = None
        self.setup_lock = threading.Lock()
        self.dry_prefixes = tuple(dry_prefixes)
        self.projects: dict[str, Project] = {}
        for project in group_projects(projects):
            if project.name in self.projects:
                raise ValueError(f"two projects share the name {project.name!r}: give one a name of its own")
            self.bind(project)
            self.projects[project.name] = project
        self.holds = holds or Holds()
        self.log_dir = Path(tempfile.mkdtemp(prefix="factory-console-"))
        self.uploads_dir = self.log_dir / UPLOADS_FOLDER
        self.port = 0
        # (project name, ref or "") -> (monotonic time read, answer); successful reads only.
        self.backlog_cache: dict[tuple[str, str], tuple[float, dict]] = {}
        self.backlog_cache_seconds: float = BACKLOG_CACHE_SECONDS
        self.cache_lock = threading.Lock()
        # project name -> (monotonic time read, answer) for GET /api/runs.
        self.runs_cache: dict[str, tuple[float, dict]] = {}
        self.runs_cache_seconds: float = RUNS_CACHE_SECONDS
        # project name -> (monotonic time read, its factory line); see project_factory.
        self.version_cache: dict[str, tuple[float, dict]] = {}
        # project name -> (monotonic time read, why new work cannot start in its folder now, or None); see start_refusal.
        self.start_cache: dict[str, tuple[float, str | None]] = {}
        # Entries of the person's project list that were skipped, by path-free name (resolve_projects).
        self.skipped: list[str] = []

    def bind(self, project: Project) -> None:
        """Tell a project which branches hold an open run, from this console's run reader (cached unless fresh)."""
        project.open_branches = lambda fresh=False: {
            str(r.get("branch")) for r in self.runs(project, fresh=fresh).get("runs") or [] if r.get("open")}

    # -- commands -------------------------------------------------------------------

    def argv(self, *args: str) -> list[str]:
        return [self.python, str(FACTORY_SCRIPT), *args]

    def run(self, project: Project, args: list[str]) -> dict:
        command = self.argv(*args)
        try:
            done = subprocess.run(command, cwd=project.path, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=COMMAND_TIMEOUT_SECONDS,
                                  check=False)
            exit_code, output = done.returncode, decode(done.stdout)
        except subprocess.TimeoutExpired as expired:
            exit_code = -1
            output = decode(expired.output or b"") + "\n[console] stopped after the time limit"
        except OSError as error:
            exit_code, output = -1, f"[console] could not run the referee: {error}"
        return {"command": command, "cwd": str(project.path), "exit": exit_code, "output": output}

    def backlog(self, project: Project, ref: str | None, fresh: bool) -> dict:
        """
        `factory backlog --json [--ref <ref>]`: the referee's JSON, passed through, with the usual
        {command, cwd, exit, output}. Read-only, so it answers beside a running drive, like status.
        Only stdout is parsed; stderr (the referee's progress or refusal) goes to `output`. Read in the project's
        main folder, whichever of its folders asks.
        """
        project = project.home
        key = (project.name, ref or "")
        now = time.monotonic()
        if not fresh:
            with self.cache_lock:
                held = self.backlog_cache.get(key)
            if held is not None and now - held[0] < self.backlog_cache_seconds:
                return {**held[1], "cached": True, "age_seconds": round(now - held[0], 1)}
        args = ["backlog", "--json"] + (["--ref", ref] if ref else [])
        result = self.run_json(project, args, BACKLOG_TIMEOUT_SECONDS)
        if result["exit"] == 0:
            add_plan_details(project.path, result)
            add_book(project.path, result)
        # A version of the data alone (never of when it was read), so a poll can ask "has it changed?".
        result["version"] = app_data.version_of(result)
        if result["exit"] != 0:
            return {**result, "cached": False}
        with self.cache_lock:
            self.backlog_cache[key] = (time.monotonic(), result)
        return {**result, "cached": False, "age_seconds": 0}

    def runs(self, project: Project, fresh: bool) -> dict:
        """
        Every run of the project on every branch and worktree (app_data.project_runs): read-only and
        in-process, so it answers beside a running drive. Kept for RUNS_CACHE_SECONDS unless fresh. Every folder of
        a repository sees the same runs: read from the project's main folder.
        """
        project = project.home
        now = time.monotonic()
        if not fresh:
            with self.cache_lock:
                held = self.runs_cache.get(project.name)
            if held is not None and now - held[0] < self.runs_cache_seconds:
                return {**held[1], "cached": True, "age_seconds": round(now - held[0], 1)}
        answer = app_data.project_runs(project.path, project.name, dry_prefixes=self.dry_prefixes)
        with self.cache_lock:
            self.runs_cache[project.name] = (time.monotonic(), answer)
        return {**answer, "cached": False, "age_seconds": 0}

    def project_line(self, project: Project) -> dict:
        """
        What the pages know of a project: its name, the branches its folders have open (an action for a run on one of
        them runs there), what this console is running in them, its online copy, its factory and the decisions held
        for a demo. No folder name or path: a project is one repository, whatever folders it has.
        """
        jobs = [{"branch": f.branch(), "kind": job.kind, "preparing": job.preparing}
                for f in project.folders() for job in [f.running_job()] if job is not None]
        branches, elsewhere = project.acting_branches()
        return {"name": project.name, "branch": project.branch(), "branches": branches, "elsewhere": elsewhere,
                "start_refusal": self.start_refusal(project),
                "running": jobs[0]["kind"] if jobs else None, "jobs": jobs,
                "repository": app_data.repository_of(project.path), "factory": project_factory(self, project),
                "holds": self.holds.of(project.group)}

    def start_refusal(self, project: Project) -> str | None:
        """
        Why Write the plan, Build the next task, Fix this defect and Draft the book would be refused in the project's
        folder now — a drive or check running, open work on its branch, unsaved changes — or None. A hint for the
        page, so an unavailable start says why before it is pressed; the start itself checks again, afresh. Kept for
        RUNS_CACHE_SECONDS.
        """
        job = next((f.running_job() for f in project.folders() if f.running_job() is not None), None)
        if job is not None:
            return f"a {job.kind} is running on this project; wait for it or stop it"
        now = time.monotonic()
        with self.cache_lock:
            held = self.start_cache.get(project.name)
        if held is not None and now - held[0] < self.runs_cache_seconds:
            return held[1]
        refusal = self.folder_refusal(project, fresh=False)
        with self.cache_lock:
            self.start_cache[project.name] = (time.monotonic(), refusal)
        return refusal

    def hold(self, project: Project, key: object, on: object) -> dict:
        """
        Hold one decision for a demo — or stop holding it. A hold names the run and the step it waits at, so the run's
        next decision is not held too. Holds of runs that have closed or moved on are dropped as the list changes.
        Only what the Needs you page shows changes; the run itself does not.
        """
        if not isinstance(on, bool):
            raise BadRequest("hold must be true or false")
        if not isinstance(key, str) or not RUN_KEY.fullmatch(key):
            raise BadRequest("run must be a run's key")
        runs = self.runs(project, fresh=False).get("runs") or []
        run = next((r for r in runs if r.get("key") == key), None)
        if run is None or not HOLD_KEY.fullmatch(f"{run.get('id')}@{run.get('step')}"):
            raise BadRequest("no such run in this project", HTTPStatus.NOT_FOUND)
        if on and not run.get("open"):
            raise BadRequest("only work that is still open can be held", HTTPStatus.CONFLICT)
        waiting = {r.get("id"): (r.get("step") if r.get("open") else None) for r in runs}

        def still_waits(hold: str) -> bool:
            run_id, _, step = hold.partition("@")
            return run_id not in waiting or waiting[run_id] == step

        held = self.holds.change(project.group, f"{run['id']}@{run['step']}" if on else None,
                                 None if on else f"{run['id']}@", still_waits)
        return {"run": run["id"], "holds": held}

    def document(self, project: Project, key: object, path: object) -> dict:
        """
        One document a run's decision offers (app_data.reading_for), for the page to show or save. Read only, and only
        a document on the run's own reading list: this route reads nothing else in the repository.
        """
        if not isinstance(key, str) or not RUN_KEY.fullmatch(key):
            raise BadRequest("run must be a run's key")
        if not isinstance(path, str) or not path:
            raise BadRequest("path must be a document's path")

        def offered(fresh: bool) -> dict | None:
            run = next((r for r in self.runs(project, fresh=fresh).get("runs") or [] if r.get("key") == key), None)
            return run if run is not None and path in {d.get("path") for d in run.get("reading") or []} else None

        run = offered(False) or offered(True)
        if run is None:
            raise BadRequest("that is not a document this run's decision offers", HTTPStatus.NOT_FOUND)
        home = project.home.path
        return app_data.run_document(home, str(run.get("branch") or ""), path, app_data.worktree_branches(home))

    def save_proof(self, folder: Folder, project: Project, body: dict, kind: str) -> str | None:
        """
        The copy of the card a person saw when they decided (`snapshot`, the page's own HTML of it), saved as a page that
        opens on its own — the app's styles, and nothing that can run — in the run's proof folder, beside its record, so
        it is committed with the run. Its path, for `--proof`; None when the page sent none. The referee keeps the path and
        a digest of the file, so a copy changed later no longer matches.
        """
        snapshot, key = body.get("snapshot"), body.get("run")
        if snapshot is None or not key:
            return None
        if not isinstance(snapshot, str) or len(snapshot.encode()) > SNAPSHOT_MAX:
            raise BadRequest(f"snapshot must be the card's HTML, at most {SNAPSHOT_MAX // 1024} KB")
        run = next((r for r in self.runs(project, fresh=False).get("runs") or [] if r.get("key") == key), None) \
            or next((r for r in self.runs(project, fresh=True).get("runs") or [] if r.get("key") == key), None)
        if run is None or not RUN_ID.fullmatch(str(run.get("id"))):
            raise BadRequest("no such run in this project", HTTPStatus.NOT_FOUND)
        runs_dir = app_data.project_config(folder.path)["runs_dir"]
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        rel = f"{runs_dir}/{PROOF_FOLDER}/{run['id']}/{stamp}-{kind}.html"
        style = PAGE_STYLE.search(INDEX_FILE.read_text(encoding="utf-8"))
        card = EVENT_ATTRIBUTE.sub("", SCRIPT_TAG.sub("", snapshot))
        who = subprocess.run(["git", "config", "user.name"], cwd=folder.path, capture_output=True, text=True,
                             timeout=30, check=False).stdout.strip() or "the person at this computer"
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=folder.path, capture_output=True, text=True,
                                timeout=30, check=False).stdout.strip()[:10]
        saved = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        page = (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
                f"<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\">"
                f'<meta name="viewport" content="width=device-width, initial-scale=1">'
                f"<title>{html_escape(run['id'])} — what {html_escape(who)} saw ({html_escape(kind)})</title>"
                f"<style>{style.group(1) if style else ''}\nbody{{padding:24px}} .proofhead{{max-width:960px;margin:0 auto 14px}}"
                f" .proofcard{{max-width:960px;margin:0 auto}}</style></head><body>"
                f'<div class="proofhead small"><b>Saved by By the Book</b> when {html_escape(who)} decided ({html_escape(kind)}), '
                f"{html_escape(saved)}. Work {html_escape(run['id'])}, “{html_escape(str(run.get('title') or ''))}”, on "
                f"<code>{html_escape(str(run.get('branch') or ''))}</code> at <code>{html_escape(commit)}</code>. This is the card as it "
                f"was on the screen; the decision itself is in the run's record.</div>"
                f'<div class="proofcard">{card}</div></body></html>\n')
        path = folder.path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page, encoding="utf-8", newline="\n")
        return rel

    def keep_proof_if_recorded(self, folder: Folder, proof: str | None, result: dict) -> None:
        """A decision the referee refused records nothing, so its copy goes too."""
        if proof and result.get("exit") != 0:
            with contextlib.suppress(OSError):
                (folder.path / proof).unlink()

    def proof(self, project: Project, key: object, path: object) -> dict:
        """One saved copy of a decision, for the page to show or save: only a copy the run's own record names."""
        if not isinstance(key, str) or not RUN_KEY.fullmatch(key):
            raise BadRequest("run must be a run's key")
        run = next((r for r in self.runs(project, fresh=False).get("runs") or [] if r.get("key") == key), None) \
            or next((r for r in self.runs(project, fresh=True).get("runs") or [] if r.get("key") == key), None)
        if run is None or not isinstance(path, str) or path not in app_data.proofs_of(run):
            raise BadRequest("that is not a saved copy this run's record names", HTTPStatus.NOT_FOUND)
        home = project.home.path
        return app_data.run_document(home, str(run.get("branch") or ""), path, app_data.worktree_branches(home), (".html",))

    def locked_tests(self, project: Project, key: str) -> list[str]:
        """The test files a run has locked, as its record lists them (fresh when the cached answer does not have the run)."""
        run = next((r for r in self.runs(project, fresh=False).get("runs") or [] if r.get("key") == key), None)
        if run is None:
            run = next((r for r in self.runs(project, fresh=True).get("runs") or [] if r.get("key") == key), None)
        return [str(x) for x in (run or {}).get("locked") or []]

    def allow_files(self, project: Project, folder: Project, body: dict) -> dict:
        """`factory allow <paths> --step build`: only the files the run's plan is held for, as its record lists them
        (fresh when the cached answer does not have them). The referee refuses anything a person guards, and checks
        the plan again."""
        key, wanted = body.get("run"), body.get("paths")
        if not isinstance(key, str) or not isinstance(wanted, list) or not wanted or len(wanted) > ALLOW_MAX:
            raise BadRequest(f"run must be a run and paths a list of 1 to {ALLOW_MAX} files")
        if not all(isinstance(p, str) and ALLOW_PATTERN.fullmatch(p) for p in wanted):
            raise BadRequest("each path must be a relative path (letters, digits, . _ - / and *)")

        def needed(fresh: bool) -> list[str]:
            run = next((r for r in self.runs(project, fresh=fresh).get("runs") or [] if r.get("key") == key), None)
            return [str(x) for x in (run or {}).get("needs_allow") or []]
        unknown = [p for p in wanted if p not in needed(False)]
        if unknown and [p for p in wanted if p not in needed(True)]:
            raise BadRequest(f"{', '.join(unknown)}: not a file this run's plan is waiting for", HTTPStatus.NOT_FOUND)
        result = self.run_change(folder, ["allow", "--step", "build", "--", *dict.fromkeys(wanted)])
        self.runs_cache.pop(project.name, None)
        return result

    def target(self, project: Project, given: dict) -> tuple[Folder, str | None]:
        """
        The folder an action acts in, and the branch it is bound to: a request names the run (`run`, its key) or the
        run's branch (`branch`), and the action runs in the folder of the project that has that branch open — the main
        folder when none has. Nothing named: the main folder, unbound. A run and a branch that disagree are refused.
        """
        branch = given.get("branch")
        if branch is not None and (not isinstance(branch, str) or not BRANCH_PATTERN.fullmatch(branch)):
            raise BadRequest("branch must be a branch name")
        key = given.get("run")
        if key is not None and key != "":
            if not isinstance(key, str) or not RUN_KEY.fullmatch(key):
                raise BadRequest("run must be a run's key")
            run = next((r for r in self.runs(project, fresh=False).get("runs") or [] if r.get("key") == key), None)
            if run is None:
                run = next((r for r in self.runs(project, fresh=True).get("runs") or [] if r.get("key") == key), None)
            if run is None or not BRANCH_PATTERN.fullmatch(str(run.get("branch") or "")):
                raise BadRequest("no such run in this project", HTTPStatus.NOT_FOUND)
            if branch is not None and branch != run["branch"]:
                raise BadRequest("the page is out of date: that run is on other work now; look again before you act",
                                 HTTPStatus.CONFLICT)
            branch = run["branch"]
        return (project.folder_for(branch) if branch else project), branch

    def backlog_view(self, project: Project, ref: str | None, fresh: bool) -> dict:
        """
        The backlog as the pages show it: the referee's answer with `progress` — each feature and plan task as it
        really stands, its plan rows corrected by the runs (app_data.progress). Computed on each answer from the
        cached backlog and runs, so a merge shows within one poll; nothing is written to the repository.
        """
        answer = self.backlog(project, ref, fresh)
        if answer.get("exit") != 0:
            return answer
        runs = self.runs(project, fresh=False).get("runs") or []
        view = {**answer, "progress": app_data.progress(answer, runs)}
        view["chapters"] = app_data.chapters(view, view["progress"], runs)
        view["version"] = app_data.version_of(view)
        return view

    def run_json(self, project: Project, args: list[str], timeout: float) -> dict:
        """
        A read-only referee command that answers JSON on stdout: its fields passed through, with the
        usual {command, cwd, exit, output}. Only stdout is parsed; stderr (the referee's progress or
        refusal) goes to `output`.
        """
        command = self.argv(*args)
        answer: dict = {"command": command, "cwd": str(project.path)}
        try:
            done = subprocess.run(command, cwd=project.path, capture_output=True,
                                  timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return {**answer, "exit": -1, "output": "[console] the referee did not answer in time"}
        except OSError as error:
            return {**answer, "exit": -1, "output": f"[console] could not run the referee: {error}"}
        output = decode(done.stderr)
        if done.returncode != 0:
            return {**answer, "exit": done.returncode, "output": output or decode(done.stdout)}
        try:
            parsed = json.loads(decode(done.stdout))
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict):
            # The referee said it succeeded, but the console cannot read its answer: -1, as for
            # every other failure on the console's side.
            return {**answer, "exit": -1,
                    "output": (output + "\n[console] the referee's answer was not a JSON object:\n"
                               + decode(done.stdout)[:2000]).strip()}
        # The referee's fields first, so they can never overwrite what the console ran.
        return {**parsed, **answer, "exit": 0, "output": output}

    # -- the staged FSD --------------------------------------------------------------

    def staged_path(self, sha256: object, kind: str = "pdf") -> Path:
        """Where an upload with this sha256 is kept — built here, from a checked name and kind, and nowhere else."""
        if not isinstance(sha256, str) or not SHA256_PATTERN.fullmatch(sha256):
            raise BadRequest("staged must be the 64 lowercase hex characters of an upload's sha256")
        if not isinstance(kind, str) or not wizard.KIND_PATTERN.fullmatch(kind):
            raise BadRequest("the kind of an upload is docx, pdf, md or txt")
        return self.uploads_dir / f"{sha256}.{kind}"

    def stage_upload(self, stream, length: int, kind: str = "pdf") -> dict:
        """
        Copy `length` bytes from the request into the uploads folder, hashing as it goes: a temporary
        name first, then `<sha256>.<kind>`. Refused, and nothing kept, unless the bytes are that kind of
        file — a PDF starts %PDF- (and the Requirements preview takes nothing else); a .docx, .md or .txt
        for the New project wizard is checked by wizard.check_fsd_bytes.
        """
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        handle = tempfile.NamedTemporaryFile(dir=self.uploads_dir, prefix="upload-", suffix=".part", delete=False)
        partial = Path(handle.name)
        try:
            with handle:
                left, first = length, b""
                while left > 0:
                    chunk = stream.read(min(UPLOAD_CHUNK_BYTES, left))
                    if not chunk:
                        raise BadRequest("the upload ended before its Content-Length")
                    if len(first) < len(PDF_MAGIC):
                        first += chunk[:len(PDF_MAGIC) - len(first)]
                    digest.update(chunk)
                    handle.write(chunk)
                    left -= len(chunk)
            if kind == "pdf" and first != PDF_MAGIC:
                raise BadRequest("that file is not a PDF: it does not start with %PDF-",
                                 HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            try:
                wizard.check_fsd_bytes(partial, kind)
            except wizard.NotThatKind as error:
                raise BadRequest(str(error), HTTPStatus.UNSUPPORTED_MEDIA_TYPE) from None
            sha256 = digest.hexdigest()
            staged = self.staged_path(sha256, kind)
            if not staged.is_file():
                # The same bytes already staged keep their file: a preview may be reading it right now,
                # and on Windows replacing an open file fails.
                os.replace(partial, staged)
            return {"staged": sha256, "bytes": length} if kind == "pdf" else {"staged": sha256, "bytes": length, "kind": kind}
        finally:
            partial.unlink(missing_ok=True)

    def fsd_preview(self, project: Project, sha256: object) -> dict:
        """`factory fsd-diff --staged <console-built path> --json` — read-only, so beside a running job too."""
        path = self.staged_path(sha256)
        if not path.is_file():
            raise BadRequest("no upload with that sha256 is staged in this console session; upload it again",
                             HTTPStatus.NOT_FOUND)
        return self.run_json(project, ["fsd-diff", "--staged", str(path), "--json"], FSD_PREVIEW_TIMEOUT_SECONDS)

    # -- New project: the kit's setup.py ---------------------------------------------------------------

    def need_kit(self) -> wizard.Kit:
        if self.kit is None:
            raise BadRequest("this console was started without a factory kit, so it cannot set a project up. Stop it "
                             f"and start it again with the kit: {wizard.KIT_HINT}", HTTPStatus.CONFLICT)
        return self.kit

    def setup_plan(self, body: dict) -> wizard.SetupPlan:
        """The setup a request asks for, every field checked (wizard.plan_setup) — or a BadRequest. Runs nothing
        but read-only `git rev-parse` in folders that passed the checks."""
        kit = self.need_kit()

        def staged(sha: object, kind: str) -> Path:
            path = self.staged_path(sha, kind)
            if not path.is_file():
                raise wizard.Refusal("no FSD with that sha256 is staged in this console session; upload it again")
            return path

        try:
            listed, _ = load_project_list()
        except ProjectListError as error:
            raise BadRequest(f"{error}: a new project could not be added to it. Fix it first.", HTTPStatus.CONFLICT) from None
        try:
            return wizard.plan_setup(body, kit=kit, python=self.python, staged=staged,
                                     projects=[(p.path, p.name) for p in self.projects.values()], listed=listed)
        except wizard.Refusal as refusal:
            raise BadRequest(str(refusal)) from None

    def start_setup(self, body: dict) -> dict:
        """setup.py in the background: one at a time (409 while one runs); its answer is read when it ends."""
        if not self.setup_lock.acquire(blocking=False):
            raise BadRequest("a project is being set up now; wait for it", HTTPStatus.CONFLICT)
        try:
            if self.setup_job is not None and self.setup_job.running():
                raise BadRequest("a project is being set up now; wait for it", HTTPStatus.CONFLICT)
            plan = self.setup_plan(body)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            out, err = self.log_dir / f"setup-{stamp}.out.json", self.log_dir / f"setup-{stamp}.err.log"
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            try:
                with open(out, "wb") as sink, open(err, "wb") as errors:
                    process = subprocess.Popen(plan.command, cwd=plan.cwd, stdout=sink, stderr=errors, env=env)
            except OSError as error:
                return {"command": plan.command, "cwd": str(plan.cwd), "exit": -1,
                        "output": f"[console] could not run the kit's setup.py: {error}"}
            job = SetupJob(plan, process, out, err)
            self.setup_job = job
            threading.Thread(target=self.finish_setup, args=(job,), daemon=True).start()
        finally:
            self.setup_lock.release()
        return {"command": plan.command, "cwd": str(plan.cwd), "exit": None,
                "output": f"setup started; its answer is kept in {out}"}

    def finish_setup(self, job: SetupJob) -> None:
        """When setup.py ends: read its JSON; on success add the project to the person's list and open it here."""
        try:
            job.process.wait()
            try:
                answer = json.loads(decode(job.out.read_bytes()))
            except (OSError, json.JSONDecodeError):
                answer = None
            job.result = answer if isinstance(answer, dict) else None
            ok = (job.process.returncode == 0 and job.result is not None and job.result.get("ok") is True
                  and isinstance(job.result.get("dir"), str) and wizard.same_path(Path(job.result["dir"]), job.plan.folder))
            if not ok:
                return
            folder = job.plan.folder
            if not (folder / FACTORY_SCRIPT).is_file():
                job.note = f"setup.py reported success, but {folder.name} has no {FACTORY_SCRIPT.as_posix()}; not opened"
                return
            try:
                job.note = add_project(str(folder), job.plan.shown if job.plan.shown != folder.name else None)
            except (ProjectListError, OSError) as error:
                job.note = f"Not added to your project list: {error}. It is open in this console until the console stops."
            try:
                self.add_session_project(folder, job.plan.shown)
                job.project = job.plan.shown
            except ValueError as error:
                job.note += f" Not opened here: {error}."
        finally:
            job.done.set()

    def add_session_project(self, folder: Path, name: str) -> Project:
        """Open one more project in this console. The dict is replaced, never changed, so a reader holding the
        old one is never surprised."""
        [project] = group_projects([(folder, name)])
        self.bind(project)
        with self.cache_lock:
            if name in self.projects:
                raise ValueError(f"a project called {name} is already open")
            if any(p.group == project.group for p in self.projects.values()):
                raise ValueError("that repository is already open here")
            self.projects = {**self.projects, name: project}
        return project

    def setup_state(self) -> dict:
        job = self.setup_job
        if job is None:
            return {"running": False, "exit": None, "command": [], "cwd": "", "output": "", "result": None,
                    "project": None, "note": ""}
        running = job.running()
        output = ""
        with contextlib.suppress(OSError):
            output = decode(job.out.read_bytes())
        with contextlib.suppress(OSError):
            errors = decode(job.err.read_bytes())
            output = (output + ("\n" if output and errors else "") + errors).strip()
        return {"running": running, "exit": None if running else job.process.returncode, "command": job.plan.command,
                "cwd": str(job.plan.cwd), "mode": job.plan.mode, "dir": str(job.plan.folder), "shown": job.plan.shown,
                "output": output[-20000:], "result": None if running else job.result,
                "project": None if running else job.project, "note": "" if running else job.note}

    def setup_info(self) -> dict:
        return {"kit": self.kit.describe() if self.kit else None, "hint": wizard.KIT_HINT,
                "kinds": list(wizard.FSD_SUFFIXES), "max_bytes": wizard.SETUP_UPLOAD_MAX_BYTES,
                "started_by_claude": started_by_claude(), "job": self.setup_state()}

    def run_change(self, project: Project, args: list[str]) -> dict:
        """A short command that changes the run: never beside a job, never two at once."""
        if not project.busy.acquire(blocking=False):
            raise BadRequest("another command is running on this project", HTTPStatus.CONFLICT)
        try:
            job = project.running_job()
            if job is not None:
                raise BadRequest(f"a {job.kind} is running on this project; wait for it or stop it",
                                 HTTPStatus.CONFLICT)
            return self.run(project, args)
        finally:
            project.busy.release()

    def answer_question(self, project: Project, folder: Project, body: dict) -> dict:
        """`factory answer Q<n>` with the person's words, or `--accept` for the question's own recommendation.
        The referee commits it on the folder's branch; the book is read afresh so the page shows it answered."""
        question = text_field(body, "question", 12)
        if not re.fullmatch(r"Q\d+", question):
            raise BadRequest("question must be a question id, e.g. Q2")
        accept = body.get("accept", False)
        if not isinstance(accept, bool):
            raise BadRequest("accept must be true or false")
        args = ["answer", question, "--accept"] if accept else ["answer", question, f"--answer={text_field(body, 'answer', REASON_MAX)}"]
        result = self.run_change(folder, args)
        self.backlog_cache.clear()
        self.runs_cache.pop(project.name, None)
        return result

    # -- work on demand: a new run on a branch of its own ------------------------------------------

    def new_run(self, project: Project, action: str, body: dict, *, fresh: bool) -> dict:
        """
        What `factory start <item> --title=<title> --new-branch=<branch>` would run for one of the
        console's three on-demand actions — {action, item, title, branch, from_branch, command, cwd} —
        or a BadRequest. Every value the request gives is checked against a strict pattern first (no
        process runs for a malformed request), then against the backlog (`factory backlog`, read-only,
        cached unless `fresh`): an item the backlog does not offer is refused before the referee is asked.
        The title is the book's own words; the branch is named as the referee would name it, and given
        to it, so the confirmation shows exactly what will happen.
        """
        given = new_run_fields(action, body)
        slug, stem, task, defect, book = (given.get(key, "") for key in ("feature_slug", "stem", "task", "defect", "book"))
        backlog = self.backlog(project, None, fresh=fresh)
        if backlog.get("exit") != 0:
            raise BadRequest("the backlog could not be read, so the item cannot be checked against it: "
                             + (backlog.get("output") or "no reason given").strip()[:500], HTTPStatus.CONFLICT)
        config = app_data.project_config(project.path)
        # A run approved at the merge gate is finished work, whatever its release steps still say.
        done = {r.get("id") for r in self.runs(project, fresh=False).get("runs") or [] if app_data.is_finished(r)}
        runs = [r for r in backlog.get("runs") or [] if isinstance(r, dict) and r.get("id") not in done]

        def open_run_for(item: str) -> None:
            running = next((r for r in runs if r.get("item") == item), None)
            if running:
                raise BadRequest(f"{running.get('id')} is already open for {item}, on {running.get('branch')} at "
                                 f"{running.get('step')}. Continue that run instead.", HTTPStatus.CONFLICT)

        if action == "create-plan":
            feature = next((f for f in backlog.get("features") or [] if isinstance(f, dict)
                            and feature_slug(str(f.get("feature", ""))) == slug), None)
            if feature is None:
                raise BadRequest(f"no feature in the backlog is called {slug}", HTTPStatus.NOT_FOUND)
            if feature.get("plan"):
                raise BadRequest(f"{feature['feature']} already has a plan, {feature['plan']}", HTTPStatus.CONFLICT)
            if str(feature.get("status", "")).lower().startswith("blocked"):
                raise BadRequest(f"{feature['feature']} is Blocked — {feature.get('note') or 'its row says why'}",
                                 HTTPStatus.CONFLICT)
            item = f"plan:{slug}"
            open_run_for(item)
            title = one_line(str(feature["feature"]) + PLAN_TITLE_SUFFIX, f"{slug}{PLAN_TITLE_SUFFIX}")
            branch = branch_name(config["branches"], "plan", {"slug": slug}, item, title)
        elif action == "implement":
            detail = (backlog.get("plan_details") or {}).get(stem)
            if not isinstance(detail, dict) or detail.get("error"):
                raise BadRequest(f"no plan {stem} in the backlog" + (f" ({detail['error']})" if isinstance(detail, dict) else ""),
                                 HTTPStatus.NOT_FOUND)
            row = next((t for t in detail.get("tasks") or [] if t.get("n") == task), None)
            if row is None:
                raise BadRequest(f"{detail['path']} has no task {task}", HTTPStatus.NOT_FOUND)
            item = f"plan:{stem}#{task}"
            open_run_for(item)
            # The next task as it really stands: a task whose run merged its work is built, whatever its row still says.
            truth = app_data.plan_progress(stem, detail, self.runs(project, fresh=False).get("runs") or [])
            if truth["next"] != task:
                raise BadRequest(f"task {task} is not the plan's next task for us ({truth['next'] or 'none is left'}): "
                                 "tasks are built one at a time, in the plan's order", HTTPStatus.CONFLICT)
            title = one_line(app_data.plain(MD_LINK.sub(r"\1", str(row.get("task", "")))), f"{stem} task {task}")
            branch = branch_name(config["branches"], "task", {"stem": stem, "task": task}, item, title)
        elif action == "book":
            # The book is written from the newest FSD the project holds; without one there is nothing to read.
            # Found as the book run finds it — .docx, .md, .txt or .pdf, on the branch the run is cut from —
            # since `factory backlog` counts PDF snapshots only.
            version = app_data.newest_book_fsd(project.path)
            if not re.fullmatch(r"V\d{1,4}", version):
                raise BadRequest("the project holds no FSD under its requirements folder yet, so there is no book to "
                                 "draft: file one first (New project, or setup.py --brownfield --fsd)", HTTPStatus.CONFLICT)
            item = f"book:{book}"
            open_run_for(item)
            title = one_line(BOOK_TITLE.format(version=version), "Book from the FSD")
            branch = branch_name(config["branches"], "book", {"name": book}, item, title)
        elif action == "revision":
            # The previewed PDF becomes the next version, named as the newest is: `<Name> - V<n+1>.pdf`.
            self.staged_path(given["staged"])
            newest = app_data.newest_fsd_snapshot(project.path)
            if newest is None:
                raise BadRequest("the project holds no FSD yet: its first version is taken in by a book run",
                                 HTTPStatus.CONFLICT)
            version = newest[0] + 1
            snapshot = re.sub(r" - V\d+\.\w+$", f" - V{version}.pdf", newest[1])
            item = f"revision:V{version}"
            open_run_for(item)
            title = REVISION_TITLE.format(version=version)
            branch = branch_name(config["branches"], "revision", {"version": str(version)}, item, title)
            if branch in config["protected_branches"]:
                raise BadRequest(f"{branch} is a protected branch; a run works on its own", HTTPStatus.CONFLICT)
            return {"action": action, "item": item, "title": title, "branch": branch, "from_branch": project.branch(),
                    "snapshot": snapshot, "command": self.argv("start", item, f"--title={title}", f"--new-branch={branch}"),
                    "cwd": str(project.path), "ref": backlog.get("ref", "")}
        else:
            found = next((d for d in backlog.get("defects") or [] if isinstance(d, dict) and d.get("id") == defect), None)
            if found is None:
                raise BadRequest(f"{defect} is not an open defect in the backlog", HTTPStatus.NOT_FOUND)
            item = defect
            open_run_for(item)
            title = one_line(app_data.plain(MD_LINK.sub(r"\1", str(found.get("title", "")))), f"Fix {defect}")
            branch = branch_name(config["branches"], "defect", {}, item, title)
        if branch in config["protected_branches"]:
            raise BadRequest(f"{branch} is a protected branch; a run works on its own", HTTPStatus.CONFLICT)
        return {"action": action, "item": item, "title": title, "branch": branch, "from_branch": project.branch(),
                "command": self.argv("start", item, f"--title={title}", f"--new-branch={branch}"),
                "cwd": str(project.path), "ref": backlog.get("ref", "")}

    def folder_refusal(self, project: Project, fresh: bool = True) -> str | None:
        """
        Why the project folder cannot switch to a new branch now, or None: `--new-branch` changes the branch of the
        folder itself. Unfinished work on the branch it has checked out, or any unsaved change other than the
        run records (start_new_run saves those first), stops it. `fresh` False reads the cached runs (the page's hint).
        """
        branch = project.branch()
        if branch.startswith("("):
            return f"the project folder is not on a branch ({branch}), so the console does not switch it"
        open_here = [r for r in self.runs(project, fresh=fresh).get("runs") or []
                     if r.get("open") and r.get("branch") == branch and not app_data.is_finished(r)]
        if open_here:
            run = open_here[0]
            return (f"“{run.get('title') or run['id']}” is not finished yet, and the project folder is on its work. Finish or "
                    "stop it first, or open other work in the folder.")
        state = self.folder_state(project)
        if not state["readable"]:
            return "could not read whether the project folder has unsaved changes, so it is not switched"
        if state["other"]:
            return (f"the project folder has {len(state['other'])} unsaved change{'' if len(state['other']) == 1 else 's'} ("
                    + ", ".join(state["other"][:5]) + (" …" if len(state["other"]) > 5 else "")
                    + "). Starting here would carry them to the new work: save them or set them aside first.")
        return None

    # -- finishing a run in a project with no remote: commit its record, merge it into main --------------

    def folder_state(self, project: Project) -> dict:
        """The project folder as git sees it: its branch, and what is uncommitted — the run files apart from the rest."""
        runs_dir = app_data.project_config(project.path)["runs_dir"]
        try:
            done = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=project.path, capture_output=True,
                                  timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired):
            done = None
        readable = done is not None and done.returncode == 0
        paths = [line[3:].strip().strip('"') for line in decode(done.stdout).splitlines() if line.strip()] if readable else []
        return {"branch": project.branch(), "readable": readable, "runs_dir": runs_dir,
                "run_files": [x for x in paths if x.startswith(runs_dir + "/")],
                "other": [x for x in paths if not x.startswith(runs_dir + "/")]}

    def save_record_steps(self, state: dict, message: str) -> list[list[str]]:
        """The two commands that keep a run's record files on the branch they belong to (nothing when there are none)."""
        if not state["run_files"]:
            return []
        return [["git", "add", "--", state["runs_dir"]], ["git", "commit", "-m", message]]

    def merge_plan(self, project: Project, key: object) -> dict:
        """
        What finishing a run here would do, as fixed argv: save its record on its branch, switch to main, merge the
        branch with a merge commit. Nothing runs. `refusal` says why not, in plain words; the console never
        merges where a pull request is the way (a project with a remote), never past uncommitted work of
        any other kind, and only a branch the run file names that exists here.
        """
        if not isinstance(key, str) or not RUN_KEY.fullmatch(key):
            raise BadRequest("run must be a run's key")
        run = next((r for r in self.runs(project, fresh=True).get("runs") or [] if r.get("key") == key), None)
        if run is None:
            raise BadRequest("no such run in this project", HTTPStatus.NOT_FOUND)
        merge = run.get("merge") or {}
        branch, target = str(run.get("branch") or ""), str(merge.get("target") or "")
        # The run's record is saved in the folder that has its branch open: the main folder, or another worktree.
        try:
            folder = project.folder_for(branch) if BRANCH_PATTERN.fullmatch(branch) else project
            elsewhere = None
        except BadRequest as error:
            folder, elsewhere = project, str(error)
        plan = {"run": run["id"], "title": run.get("title", ""), "branch": branch, "target": target,
                "commands": [], "refusal": None, "saves_record": False, "folder_branch": folder.branch(), "_folder": folder}
        state = self.folder_state(folder)

        def refuse(why: str) -> dict:
            return {**plan, "refusal": why}

        if elsewhere:
            return refuse(elsewhere)

        if not merge.get("local"):
            return refuse("this project finishes a change through a pull request, not here")
        if not merge.get("passed"):
            return refuse("the approval has not been checked yet")
        if merge.get("merged"):
            return refuse(f"it is already merged into {target}")
        if not target:
            return refuse("this project has no main branch to merge into")
        if not BRANCH_PATTERN.fullmatch(branch) or branch == target or not app_data.git_succeeds(
                project.path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
            return refuse("the work's own branch is not in this project folder")
        if branch in app_data.project_config(project.path)["protected_branches"]:
            return refuse("the work is on a protected branch")
        if not state["readable"]:
            return refuse("could not read whether the project folder has unsaved changes")
        if state["other"]:
            shown = ", ".join(state["other"][:4]) + (" …" if len(state["other"]) > 4 else "")
            return refuse(f"the project folder has unsaved changes ({shown}). Save or set them aside first")
        if state["run_files"] and state["branch"] != branch:
            return refuse(f"unsaved run records belong to the branch the folder is on ({state['branch']}); switch to the work's own branch first")
        # git opens a branch in one folder only: main open in another worktree of the project is merged there.
        open_in = project.worktrees().get(target)
        merge_in = None
        if open_in is not None and not same_folder(open_in, folder.path):
            try:
                merge_in = project.folder_at(open_in, target)
            except BadRequest as error:
                return refuse(f"{target} is open in another folder: {error}")
            try:
                there = subprocess.run(["git", "status", "--porcelain"], cwd=merge_in.path, capture_output=True, timeout=60, check=False)
            except (OSError, subprocess.TimeoutExpired):
                there = None
            if there is None or there.returncode != 0 or decode(there.stdout).strip():
                return refuse(f"{target} is open in another working copy of this project, which has unsaved changes. "
                              "Save or set them aside first")
        title = one_line(str(run.get("title") or ""), run["id"])
        commands = self.save_record_steps(state, f"Record {run['id']} as finished")
        if state["branch"] != target and merge_in is None:
            commands.append(["git", "checkout", target])
        commands.append(["git", "merge", "--no-ff", branch, "-m", one_line(f"Merge {branch}: {title}", f"Merge {branch}")])
        return {**plan, "commands": commands, "saves_record": bool(state["run_files"]),
                "merge_in": merge_in is not None, "_merge_in": merge_in}

    def run_git_steps(self, project: Folder, commands: list[list[str]], merge_in: Path | None = None) -> dict:
        """
        Run fixed git commands in the project folder, one after another, stopping at the first that fails; a merge
        runs in `merge_in` when the branch it merges into is open there.
        """
        shown, exit_code, last = [], 0, commands[0] if commands else []
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        for command in commands:
            last = command
            folder = merge_in if merge_in is not None and command[:2] == ["git", "merge"] else project.path
            try:
                done = subprocess.run(command, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      timeout=GIT_STEP_SECONDS, check=False, env=env)
                exit_code, output = done.returncode, decode(done.stdout)
            except (OSError, subprocess.TimeoutExpired) as error:
                exit_code, output = -1, f"[console] could not run git: {error}"
            shown.append(f"$ {' '.join(command)}\n{output.strip()}")
            if exit_code != 0 and command[:2] == ["git", "merge"]:
                finished = self.finish_generated_clash(folder, env)
                if finished is not None:
                    shown.append(finished)
                    exit_code = 0
                    continue
            if exit_code != 0:
                if command[:2] == ["git", "merge"]:
                    subprocess.run(["git", "merge", "--abort"], cwd=folder, capture_output=True, timeout=60, check=False)
                    shown.append("$ git merge --abort\n(the merge was undone; nothing changed on the main branch)")
                break
        return {"command": last, "cwd": str(project.path), "exit": exit_code, "output": "\n\n".join(shown)}

    def open_pr(self, folder: Folder, branch: str | None) -> dict:
        """
        Open a run's pull request from the app, so no one needs a terminal for it: push the run's own branch to origin
        (that branch only, never a protected one), then `factory open-pr` — which opens it in the person's name with their
        own stored Bitbucket credential, and refuses whatever the referee would refuse. Nothing is opened when the push fails.
        """
        if not branch:
            raise BadRequest("name the run whose pull request to open")
        if branch in {*SHARED_BRANCHES, *app_data.project_config(folder.path)["protected_branches"]}:
            raise BadRequest(f"{branch} is a shared branch: a pull request is opened from a run's own branch",
                             HTTPStatus.CONFLICT)
        remote = app_data.project_config(folder.path)["remote"]
        pushed = self.run_git_steps(folder, [["git", "push", remote, f"refs/heads/{branch}:refs/heads/{branch}"]])
        if pushed["exit"] != 0:
            return {**pushed, "output": "The branch could not be sent to the shared repository, so no pull request was opened.\n\n"
                                        + pushed["output"]}
        opened = self.run_change(folder, ["open-pr"])
        return {**opened, "output": pushed["output"] + "\n\n" + (opened.get("output") or "")}

    def finish_generated_clash(self, folder: Path, env: dict) -> str | None:
        """
        A merge stopped on a clash: when it is only where the factory keeps its book — the runs' INDEX.md, project.md's
        generated blocks, and the plans' task rows, each run writing its own — finish it. A plan's rows are merged row
        by row against their common base; main's project.md blocks and INDEX.md are kept and written again from the
        merged plans and run files; the merge is committed. Anything else, a row both sides changed differently, or
        any step failing, answers None and the merge is undone as before (trial R-009, R-013: tasks built side by side
        each wrote their row and the roll-up, and a person had to finish the merge in a terminal).
        """
        def git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(["git", *args], cwd=folder, capture_output=True, timeout=GIT_STEP_SECONDS,
                                  check=False, env=env)

        try:
            listed = git("diff", "--name-only", "--diff-filter=U")
            clashed = [line.strip() for line in decode(listed.stdout).splitlines() if line.strip()]
            config = app_data.project_config(folder)
            index = config["runs_dir"] + "/INDEX.md"
            plans = [p for p in clashed if p.startswith(config["plans_dir"] + "/") and p.endswith(".md")]
            if listed.returncode != 0 or not clashed or not set(clashed) <= {index, PROJECT_MD, *plans}:
                return None
            for plan in plans:
                base = git("show", f":1:{plan}")
                merged = merge_task_rows((folder / plan).read_text(encoding="utf-8"), decode(base.stdout)) \
                    if base.returncode == 0 else None
                if merged is None:
                    return None
                (folder / plan).write_text(merged, encoding="utf-8", newline="\n")
            steps = []
            if PROJECT_MD in clashed:
                path = folder / PROJECT_MD
                text = path.read_text(encoding="utf-8")
                blocks = [m.span() for m in GENERATED_BLOCK.finditer(text)]
                clashes = list(CLASH.finditer(text))
                if not clashes or not all(any(s <= c.start() and c.end() <= e for s, e in blocks) for c in clashes):
                    return None
                path.write_text(CLASH.sub(lambda c: clash_sides(c)[0], text), encoding="utf-8", newline="\n")
            if PROJECT_MD in clashed or plans:
                if not (folder / ROLLUP_SCRIPT).is_file():
                    return None
                steps.append([self.python, str(ROLLUP_SCRIPT)])  # the roll-up reads the plans' rows
            if index in clashed:
                if git("checkout", "--ours", "--", index).returncode != 0:
                    return None
                steps.append(self.argv("index"))
            for step in steps:
                if subprocess.run(step, cwd=folder, capture_output=True, timeout=GIT_STEP_SECONDS, check=False,
                                  env=env).returncode != 0:
                    return None
            touched = dict.fromkeys([*clashed, *([PROJECT_MD] if plans and (folder / PROJECT_MD).is_file() else [])])
            if git("add", "--", *touched).returncode != 0 or git("commit", "--no-edit").returncode != 0:
                return None
        except (OSError, UnicodeDecodeError, subprocess.TimeoutExpired):
            return None
        return (f"Both sides had changed {' and '.join(clashed)}, where each task keeps its own row and the factory writes "
                "the rest; they were combined and the merge finished.")

    def finish_run(self, project: Project, key: object) -> dict:
        plan = self.merge_plan(project, key)
        folder, merge_in = plan.pop("_folder"), plan.pop("_merge_in", None)
        held = [f for f in (folder, merge_in) if f is not None]
        taken = []
        try:
            for f in held:
                if not f.busy.acquire(blocking=False):
                    raise BadRequest("another command is running on this project", HTTPStatus.CONFLICT)
                taken.append(f)
                job = f.running_job()
                if job is not None:
                    raise BadRequest(f"a {job.kind} is running on this project; wait for it or stop it", HTTPStatus.CONFLICT)
            plan = self.merge_plan(project, key)   # again, under the locks: the folders may have changed meanwhile
            plan.pop("_folder"), plan.pop("_merge_in", None)
            if plan["refusal"]:
                raise BadRequest(f"Not merged: {plan['refusal']}.", HTTPStatus.CONFLICT)
            result = self.run_git_steps(folder, plan["commands"], merge_in.path if merge_in is not None else None)
        finally:
            for f in taken:
                f.busy.release()
        with self.cache_lock:
            self.backlog_cache.clear()
            self.runs_cache.pop(project.name, None)
            self.start_cache.pop(project.name, None)
        return {**result, "branch": plan["branch"], "target": plan["target"]}

    def switch_branch(self, project: Project, branch: object) -> dict:
        """
        Put the project folder on the branch of one of its open runs, so the console can act on that run. Refused
        while a drive or check runs, and for any unsaved change except run records, which are saved on the
        branch they belong to first — so git never refuses to switch.
        """
        if not isinstance(branch, str) or not BRANCH_PATTERN.fullmatch(branch):
            raise BadRequest("branch must be a branch name")
        if not project.busy.acquire(blocking=False):
            raise BadRequest("another command is running on this project", HTTPStatus.CONFLICT)
        try:
            job = project.running_job()
            if job is not None:
                raise BadRequest(f"a {job.kind} is running on this project; wait for it or stop it", HTTPStatus.CONFLICT)
            if not any(r.get("open") and r.get("branch") == branch for r in self.runs(project, fresh=True).get("runs") or []):
                raise BadRequest("no open run of this project is on that branch", HTTPStatus.NOT_FOUND)
            if not app_data.git_succeeds(project.path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
                raise BadRequest("that branch is not in this project folder", HTTPStatus.CONFLICT)
            elsewhere = project.worktrees().get(branch)
            if elsewhere is not None and not same_folder(elsewhere, project.path):
                # git refuses to open one branch in two folders; the app acts on the work where it is open.
                raise BadRequest("this work is already open in another working copy of this project, and the app acts on "
                                 "it there: there is nothing to open", HTTPStatus.CONFLICT)
            state = self.folder_state(project)
            if not state["readable"]:
                raise BadRequest("could not read whether the project folder has unsaved changes", HTTPStatus.CONFLICT)
            if state["other"]:
                raise BadRequest("the project folder has unsaved changes (" + ", ".join(state["other"][:4])
                                 + (" …" if len(state["other"]) > 4 else "") + "). Save or set them aside first.", HTTPStatus.CONFLICT)
            if state["run_files"] and state["branch"].startswith("("):
                # Saved on no branch, the notes would be left behind on a commit nothing names (the demo review).
                raise BadRequest("the project folder is not on a branch, and it holds the AI's unsaved notes; save them on "
                                 "their run's branch first", HTTPStatus.CONFLICT)
            commands: list[list[str]] = []
            if state["branch"] != branch:
                commands = self.save_record_steps(state, f"Save the run records on {state['branch']}")
                commands.append(["git", "checkout", branch])
            result = (self.run_git_steps(project, commands) if commands else
                      {"command": [], "cwd": str(project.path), "exit": 0, "output": "the folder is already on that branch"})
        finally:
            project.busy.release()
        with self.cache_lock:
            self.backlog_cache.clear()
            self.runs_cache.pop(project.name, None)
            self.start_cache.pop(project.name, None)
        return result

    # -- the front end of a new copy of a project: its packages are not there until `npm ci` has run ----------

    def frontend_folders(self, project: Project) -> list[dict]:
        """Folders of the project (its top and up to two levels down) that hold a package.json and a package-lock.json,
        and whether their packages are installed. Found here, never named by a request."""
        return frontend_folders(project.path)

    def start_prepare(self, project: Project, folder: object) -> dict:
        """`npm ci` in one of those folders, in the background: fixed argv, nothing a person types reaches a shell."""
        found = {f["folder"]: f for f in self.frontend_folders(project)}
        if not isinstance(folder, str) or folder not in found:
            raise BadRequest("that is not a front-end folder of this project", HTTPStatus.NOT_FOUND)
        if found[folder]["ready"]:
            raise BadRequest("that front end is already prepared", HTTPStatus.CONFLICT)
        npm = shutil.which("npm")
        if npm is None:
            raise BadRequest("Node.js is not installed on this computer, so the front end cannot be prepared. Install it from "
                             "nodejs.org, then press the button again.", HTTPStatus.CONFLICT)
        return self.start_job(project, "prepare", [], command=[npm, "ci"], cwd=(project.path / folder).resolve())

    def start_new_run(self, project: Project, action: str, body: dict) -> dict:
        """
        One on-demand start: under the project's lock (409 while another command or a drive runs), the
        item checked against a fresh backlog, the folder checked, then the one argv — no shell.
        """
        new_run_fields(action, body)  # a malformed request is refused before anything else
        if not project.busy.acquire(blocking=False):
            raise BadRequest("another command is running on this project", HTTPStatus.CONFLICT)
        try:
            job = project.running_job()
            if job is not None:
                raise BadRequest(f"a {job.kind} is running on this project; wait for it or stop it", HTTPStatus.CONFLICT)
            plan = self.new_run(project, action, body, fresh=True)
            refusal = self.folder_refusal(project)
            if refusal:
                raise BadRequest(refusal, HTTPStatus.CONFLICT)
            state = self.folder_state(project)
            if state["run_files"]:   # the AI's records of earlier work stay on the branch they belong to
                saved = self.run_git_steps(project, self.save_record_steps(state, f"Save the run records on {state['branch']}"))
                if saved["exit"] != 0:
                    raise BadRequest("The earlier work's records could not be saved first: " + saved["output"][-300:], HTTPStatus.CONFLICT)
            result = self.start_revision(project, plan, body) if action == "revision" else self.run(project, plan["command"][2:])
        finally:
            project.busy.release()
        with self.cache_lock:  # the folder is on another branch now, with a new run: read both afresh
            self.start_cache.pop(project.name, None)
            self.backlog_cache.clear()
            self.runs_cache.pop(project.name, None)
        return {**result, **{key: plan[key] for key in ("action", "item", "title", "branch", "from_branch")}}

    def start_revision(self, project: Project, plan: dict, body: dict) -> dict:
        """
        The previewed PDF filed as the next version, the revision run started on its own branch (the referee
        reads the snapshot from the folder), then the snapshot committed there. A start that fails takes the file back.
        """
        staged = self.staged_path(body.get("staged"))
        if not staged.is_file():
            raise BadRequest("the previewed PDF is no longer kept by the app; upload it again", HTTPStatus.GONE)
        target = project.path / plan["snapshot"]
        if target.exists():
            raise BadRequest(f"{plan['snapshot']} exists already", HTTPStatus.CONFLICT)
        shutil.copyfile(staged, target)
        result = self.run(project, plan["command"][2:])
        if result.get("exit") != 0:
            target.unlink(missing_ok=True)
            return result
        filed = self.run_git_steps(project, [["git", "add", "--", plan["snapshot"]],
                                             ["git", "commit", "-m", f"File the FSD as V{plan['item'].split(':V')[1]}, for {plan['item']}"]])
        if filed["exit"] != 0:
            return {**result, "exit": filed["exit"], "output": result.get("output", "") + "\nThe run started, but the new FSD "
                    "could not be committed on its branch: " + filed["output"][-300:]}
        return result

    def npm_ci(self, npm: str) -> list[str]:
        """The argv that installs a front end's packages exactly as its lock file says."""
        return [npm, "ci"]

    def start_job(self, project: Folder, kind: str, args: list[str], *, command: list[str] | None = None,
                  cwd: Path | None = None) -> dict:
        """
        A long command (drive, check, prepare) in the background, in one folder; its output goes to a log for the tail.
        A drive or check in a folder whose front end has no packages yet prepares it first (`npm ci`), in the same job,
        so a person is never asked to: the run's page says "Preparing the front end…" while it does.
        """
        command = command or self.argv(*args)
        cwd = cwd or project.path
        if not project.busy.acquire(blocking=False):
            raise BadRequest("another command is running on this project", HTTPStatus.CONFLICT)
        try:
            job = project.running_job()
            if job is not None:
                raise BadRequest(f"a {job.kind} is already running on this project", HTTPStatus.CONFLICT)
            stamp = time.strftime('%Y%m%d-%H%M%S')
            log = self.log_dir / f"{re.sub(r'[^A-Za-z0-9._-]+', '-', project.name)}-{kind}-{stamp}-{secrets.token_hex(3)}.log"
            # Additions only: plain progress lines and unbuffered output, so the tail is live.
            # Same process group and terminal as the console — never detached — so Ctrl+C in
            # the console's terminal reaches the referee as it would at a prompt.
            env = {**os.environ, "FACTORY_PROGRESS": "on", "PYTHONUNBUFFERED": "1"}
            unready = [f["folder"] for f in frontend_folders(project.path) if not f["ready"]] if kind in ("drive", "check") else []
            npm = shutil.which("npm") if unready else None
            try:
                if unready and npm:
                    steps = [(self.npm_ci(npm), (project.path / folder).resolve()) for folder in unready]
                    job = PreparedJob(kind, [*steps, (command, cwd)], log, env)
                else:
                    with open(log, "wb") as sink:
                        if unready:
                            sink.write(b"[console] Node.js is not installed on this computer, so the front end was not "
                                       b"prepared first; its tests cannot run until it is (nodejs.org).\n")
                            sink.flush()
                        process = subprocess.Popen(command, cwd=cwd, stdout=sink,
                                                   stderr=subprocess.STDOUT, env=env)
                    job = Job(kind, command, process, log)
            except OSError as error:
                # `error` too: the page reads it, and without it said the AI was on it (the 15 Oct review's #11).
                return {"command": command, "cwd": str(cwd), "exit": -1,
                        "error": f"the {kind} could not start: {error}",
                        "output": f"[console] could not run {command[0]}: {error}"}
            project.jobs[kind] = job
        finally:
            project.busy.release()
        return {"command": command, "cwd": str(cwd), "exit": None, "preparing": job.preparing,
                "output": f"{kind} started; its output is kept in {log}"}

    def job_state(self, project: Folder, kind: str) -> dict:
        job = project.jobs[kind]
        if job is None:
            return {"kind": kind, "running": False, "exit": None, "stopped": False, "tail": "", "preparing": False,
                    "command": [], "cwd": str(project.path), "started": None}
        return {"kind": kind, "running": job.running(), "exit": job.exit_code(), "preparing": job.preparing,
                "started": job.started,
                "stopped": job.stopped, "tail": tail(job.log, TAIL_LINES), "command": job.command,
                "cwd": str(project.path), "log": str(job.log)}

    def stop_job(self, project: Folder) -> dict:
        """Stop the drive or check this console started in this folder — its process tree, nothing else."""
        job = project.running_job()
        if job is None:
            raise BadRequest("nothing the console started is running on this project", HTTPStatus.CONFLICT)
        with job.lock:   # a job of several commands starts no further one once stopped
            job.stopped = True
            process = job.process
        stop_command, said = stop_tree(process)
        return {"command": stop_command, "cwd": str(project.path), "exit": process.poll(),
                "output": f"stopped the {job.kind} (process {process.pid}): {said}",
                "stopped": job.kind}

    def stop_all(self) -> None:
        for project in self.projects.values():
            for folder in project.folders():
                if folder.running_job() is not None:
                    self.stop_job(folder)


FRONTEND_SKIP = {"node_modules", ".git", ".factory", "dist", "build", "bin", "obj", ".venv", "venv"}
FRONTEND_DEPTH = 2


def frontend_folders(root: Path) -> list[dict]:
    """See Console.frontend_folders. Folder names are relative, with forward slashes; the top is ".". """
    found: list[dict] = []
    try:
        for current, dirs, files in os.walk(root):
            depth = len(Path(current).relative_to(root).parts)
            dirs[:] = [d for d in dirs if d not in FRONTEND_SKIP and not d.startswith(".")] if depth < FRONTEND_DEPTH else []
            if "package.json" in files and "package-lock.json" in files:
                relative = Path(current).relative_to(root).as_posix()
                found.append({"folder": relative, "ready": (Path(current) / "node_modules").is_dir()})
    except OSError:
        return []
    return sorted(found, key=lambda f: f["folder"])


def open_in_browser(url: str, opener=webbrowser.open) -> bool:
    """Open the tokened address in the person's browser; False when the machine has none (the address is printed too)."""
    try:
        return bool(opener(url))
    except webbrowser.Error:
        return False


def stop_tree(process: subprocess.Popen[bytes]) -> tuple[list[str], str]:
    """
    End a process the console started, and everything under it. The Popen object is not
    reaped while it runs, so its pid cannot have been handed to some other program.

    Windows: `taskkill /T /F` on that pid ends the tree by parentage. POSIX: an interrupt
    first, so the referee's own handler ends its children (they run in sessions of their
    own, out of reach of a group signal), then a kill if it has not stopped in time.
    """
    if process.poll() is not None:
        return [], "it had already finished"
    if os.name == "nt":
        command = ["taskkill", "/T", "/F", "/PID", str(process.pid)]
        done = subprocess.run(command, capture_output=True, text=True, check=False)
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return command, (done.stdout + done.stderr).strip() or f"taskkill exit {done.returncode}"
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=STOP_GRACE_SECONDS)
        return ["SIGINT", str(process.pid)], "interrupted; the referee ends its own children"
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return (["SIGINT", str(process.pid), "SIGKILL", str(process.pid)],
                f"it did not stop within {STOP_GRACE_SECONDS} s of the interrupt, so it was killed")


def decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def tail(path: Path, count: int) -> str:
    try:
        text = decode(path.read_bytes())
    except OSError:
        return ""
    lines = collections.deque(re.split(r"\r\n|\r|\n", text), maxlen=count)
    return "\n".join(lines)


# -- request validation -------------------------------------------------------------


def text_field(body: dict, name: str, limit: int) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise BadRequest(f"{name} is required")
    value = value.strip()
    if len(value) > limit:
        raise BadRequest(f"{name} is longer than {limit} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise BadRequest(f"{name} may not contain control characters")
    return value


def choice_field(body: dict, name: str, allowed: tuple[str, ...]) -> str:
    value = body.get(name)
    if value not in allowed:
        raise BadRequest(f"{name} must be one of: {', '.join(allowed)}")
    return value


def ref_field(query: dict[str, str]) -> str | None:
    """The optional `ref` of GET /api/backlog: a branch, tag or commit, never a range or an option."""
    value = query.get("ref")
    if value is None or value == "":
        return None
    if not REF_PATTERN.fullmatch(value) or ".." in value or value.startswith("-"):
        raise BadRequest("ref must be a branch, tag or commit: letters, digits and . _ / - only, "
                         "at most 100 characters, without '..' and not starting with '-'")
    return value


def since_field(query: dict[str, str]) -> str:
    """The optional `since` of a polled read: a version this console sent, or ""."""
    value = query.get("since") or ""
    if value and not VERSION_PATTERN.fullmatch(value):
        raise BadRequest("since must be a version this console sent: 16 lowercase hex characters")
    return value


def pattern_field(body: dict, name: str, pattern: re.Pattern[str], shape: str, numbers: bool = False) -> str:
    """A required field matching `pattern` in full; a whole number is accepted for it when `numbers`."""
    value = body.get(name)
    if numbers and isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise BadRequest(f"{name} must be {shape}")
    return value


def new_run_fields(action: str, body: dict) -> dict[str, str]:
    """The fields one on-demand action takes, each matching its pattern in full — or a BadRequest."""
    if action == "create-plan":
        return {"feature_slug": pattern_field(body, "feature_slug", SLUG_PATTERN, "lower-case letters, digits and "
                                              "dashes, as the feature's name becomes (Book Loans → book-loans)")}
    if action == "implement":
        return {"stem": pattern_field(body, "stem", SLUG_PATTERN, "a plan file's name without .md: lower-case "
                                      "letters, digits and dashes"),
                "task": pattern_field(body, "task", TASK_PATTERN, "a task's number in the plan, 1 to 999", numbers=True)}
    if action == "fix":
        return {"defect": pattern_field(body, "defect", DEFECT_PATTERN, "a defect id such as D4")}
    if action == "book":
        return {"book": pattern_field(body, "book", SLUG_PATTERN, "the book's name: lower-case letters, digits and "
                                      "dashes — `project` for the project's own book")}
    if action == "revision":
        staged = body.get("staged")
        if not isinstance(staged, str) or not SHA256_PATTERN.fullmatch(staged):
            raise BadRequest("staged must be the 64 lowercase hex characters of a previewed upload's sha256")
        return {"staged": staged}
    raise BadRequest("unknown action", HTTPStatus.NOT_FOUND)


feature_slug = app_data.feature_slug


def one_line(text: str, fallback: str) -> str:
    """A title from the book: control characters out, whitespace collapsed, at most TITLE_MAX characters."""
    value = " ".join(CONTROL_CHARS.sub(" ", text).split())
    if len(value) > TITLE_MAX:
        value = value[:TITLE_MAX - 1].rstrip() + "…"
    return value or fallback


def branch_name(templates: dict[str, str], key: str, groups: dict[str, str], item: str, title: str) -> str:
    """
    The branch `--new-branch` cuts, named as the referee's new_branch_name names it from
    `[backlog.branches]` — then checked here: a name the pattern does not allow is refused, never cleaned.
    """
    fields = {"item": item, "title": feature_slug(title)[:40].rstrip("-") or "run", **groups}
    template = templates.get(key, "")
    if not BRANCH_TEMPLATE.fullmatch(template):
        raise BadRequest(f"[backlog.branches] {key} = {template!r}: a template may hold only plain text and "
                         "{item}, {title}, {slug}, {stem}, {task} or {name}", HTTPStatus.CONFLICT)
    try:
        name = template.format_map(fields)
    except (KeyError, IndexError, ValueError, AttributeError) as error:
        raise BadRequest(f"[backlog.branches] {key} = {template!r} cannot be filled: {error}", HTTPStatus.CONFLICT) from error
    if (not BRANCH_PATTERN.fullmatch(name) or ".." in name or "//" in name or "@{" in name
            or name.endswith(("/", ".", ".lock")) or "/." in name):
        raise BadRequest(f"the branch {name!r} from [backlog.branches] {key} is not a name the console passes on",
                         HTTPStatus.CONFLICT)
    return name


def add_plan_details(path: Path, backlog: dict) -> dict:
    """
    `plan_details` for a backlog answer: each plan it names, by stem, read in-process at the backlog's own
    ref (app_data.plan_details) — so a plan's detail view and its "Implement next task" read the same book
    as the lists. Read-only.
    """
    config = app_data.project_config(path)
    ref = backlog.get("ref") if isinstance(backlog.get("ref"), str) else ""
    if not REF_PATTERN.fullmatch(ref or "") or ".." in ref or ref.startswith("-"):
        ref = ""
    backlog["plan_details"] = app_data.plan_details(path, ref, app_data.plan_paths(backlog, config["plans_dir"]),
                                                    config["our_role"])
    return backlog


def add_book(path: Path, backlog: dict) -> dict:
    """
    `book` for a backlog answer: the rest of the book at the backlog's own ref (app_data.book_sources) — every defect
    and answered question, the FSD versions and the traceability matrix — so the chapters and the back matter read the
    same book as the lists. Read-only.
    """
    ref = backlog.get("ref") if isinstance(backlog.get("ref"), str) else ""
    backlog["book"] = app_data.book_sources(path, ref)
    # A question answered on the folder's branch is no longer waiting on anyone: the open list and its counts agree.
    pending = {q["id"] for q in backlog["book"]["questions"] if q.get("pending")}
    if pending and isinstance(backlog.get("questions"), list):
        backlog["questions"] = [q for q in backlog["questions"] if not (isinstance(q, dict) and q.get("id") in pending)]
    return backlog


def test_path_field(body: dict) -> str:
    """
    A locked test file: a relative path inside the project, given with / or \\ separators. Not only under tests/: a
    stack's tests live in its own folder (backend/tests/…), and a React test beside its code (src/App.test.tsx). Which
    files are locked is the run's to say (Console.locked_tests); the referee refuses any other.
    """
    value = text_field(body, "test", TEST_PATH_MAX)
    path = value.replace("\\", "/")
    parts = path.split("/")
    if (path.startswith("/") or DRIVE_LETTER.match(path) or ":" in path
            or any(part in ("", ".", "..") for part in parts)):
        raise BadRequest("test must be a relative path inside the project, without '..', "
                         "a leading slash or a drive letter")
    return path


# -- HTTP ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "FactoryConsole"
    console: Console  # set on the subclass make_server builds
    log_requests = True

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # One line per request, no query strings (they could carry a token by mistake).
        if self.log_requests:
            sys.stderr.write(f"[console] {self.command} {urlsplit(self.path).path} -> {args[1] if len(args) > 1 else ''}\n")

    # -- checks --

    def allowed_hosts(self) -> set[str]:
        return {f"{name}:{self.console.port}" for name in HOST_NAMES}

    def host_ok(self) -> bool:
        return self.headers.get("Host", "").lower() in self.allowed_hosts()

    def origin_ok(self, required: bool) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return not required
        return origin.lower() in {f"http://{host}" for host in self.allowed_hosts()}

    def token_ok(self) -> bool:
        given = self.headers.get(TOKEN_HEADER, "")
        return secrets.compare_digest(given.encode(), self.console.token.encode())

    def guard(self, post: bool) -> bool:
        if not self.host_ok():
            self.reply(HTTPStatus.FORBIDDEN, {"error": "wrong Host"})
            return False
        if not self.origin_ok(required=post):
            self.reply(HTTPStatus.FORBIDDEN, {"error": "wrong or missing Origin"})
            return False
        if not self.token_ok():
            self.reply(HTTPStatus.UNAUTHORIZED, {"error": "missing or wrong console token"})
            return False
        return True

    # -- replies --

    def reply(self, status: HTTPStatus, payload: dict | list) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def reply_versioned(self, answer: dict, query: dict[str, str]) -> None:
        """
        A read a page polls. When the caller already holds this version — `since=<version>` or
        If-None-Match — the answer is {"unchanged": true, "version"} (or 304 with no body), not the data.
        """
        version = answer.get("version", "")
        since = since_field(query)
        etag = f'"{version}"'
        if version and self.headers.get("If-None-Match", "") == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        payload = {"unchanged": True, "version": version, "cached": answer.get("cached", False)} \
            if version and since == version else answer
        data = json.dumps(payload).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if version:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(data)

    def page(self) -> None:
        if not self.host_ok():
            self.reply(HTTPStatus.FORBIDDEN, {"error": "wrong Host"})
            return
        data = INDEX_FILE.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                         "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    # -- plumbing --

    def project(self, name: object) -> Project:
        if not isinstance(name, str) or name not in self.console.projects:
            raise BadRequest("unknown project", HTTPStatus.NOT_FOUND)
        return self.console.projects[name]

    def query(self) -> dict[str, str]:
        # keep_blank_values: an empty `ref=` reads as "no ref", not as absent-by-accident.
        return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query, keep_blank_values=True).items()}

    def read_raw(self, limit: int = BODY_MAX_BYTES) -> bytes | None:
        """The request body, read before any answer — a refusal sent over an unread body is
        a reset connection on Windows. None when it is too large to read."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length < 0 or length > limit:
            self.close_connection = True
            return None
        return self.rfile.read(length)

    def body(self, raw: bytes | None) -> dict:
        if raw is None:
            raise BadRequest("request too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise BadRequest("send JSON (Content-Type: application/json)",
                             HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError as error:
            raise BadRequest("the body is not valid JSON") from error
        if not isinstance(data, dict):
            raise BadRequest("the body must be a JSON object")
        return data

    # -- routes --

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self.page()
            return
        if not path.startswith("/api/"):
            self.reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self.guard(post=False):
            return
        console = self.console
        try:
            if path == "/api/projects":
                self.reply(HTTPStatus.OK, {"started_by_claude": started_by_claude(),
                                           "projects": [console.project_line(p) for p in console.projects.values()],
                                           "skipped": console.skipped, "kit": console.kit is not None})
            elif path == "/api/setup":
                self.reply(HTTPStatus.OK, console.setup_info())
            elif path in ("/api/status", "/api/report"):
                query = self.query()
                folder, _ = console.target(self.project(query.get("project")), query)
                self.reply(HTTPStatus.OK, console.run(folder, [path.rsplit("/", 1)[1]]))
            elif path == "/api/backlog":
                query = self.query()
                project = self.project(query.get("project"))
                ref = ref_field(query)
                since_field(query)
                self.reply_versioned(console.backlog_view(project, ref, fresh=query.get("fresh") == "1"), query)
            elif path == "/api/runs":
                query = self.query()
                project = self.project(query.get("project"))
                since_field(query)
                self.reply_versioned(console.runs(project, fresh=query.get("fresh") == "1"), query)
            elif path == "/api/fsd/preview":
                query = self.query()
                project = self.project(query.get("project"))
                self.reply(HTTPStatus.OK, console.fsd_preview(project, query.get("staged")))
            elif path == "/api/plan":
                query = self.query()
                project = self.project(query.get("project"))
                stem = pattern_field(query, "stem", SLUG_PATTERN, "a plan file's name without .md")
                backlog = console.backlog_view(project, ref_field(query), fresh=query.get("fresh") == "1")
                detail = (backlog.get("plan_details") or {}).get(stem)
                if not isinstance(detail, dict):
                    raise BadRequest(f"no plan {stem} in the backlog", HTTPStatus.NOT_FOUND)
                self.reply(HTTPStatus.OK, {**detail, "ref": backlog.get("ref", ""),
                                           "progress": ((backlog.get("progress") or {}).get("plans") or {}).get(stem)})
            elif path == "/api/proof":
                query = self.query()
                self.reply(HTTPStatus.OK, console.proof(self.project(query.get("project")), query.get("run"), query.get("path")))
            elif path == "/api/doc":
                query = self.query()
                self.reply(HTTPStatus.OK, console.document(self.project(query.get("project")), query.get("run"), query.get("path")))
            elif path == "/api/new-run":
                # What a start would run, and whether the folder would refuse it now: nothing changes.
                query = self.query()
                project = self.project(query.get("project"))
                action = choice_field(query, "action", NEW_RUN_ACTIONS)
                plan = console.new_run(project, action, query, fresh=False)
                job = project.running_job()
                refusal = (f"a {job.kind} is running on this project; wait for it or stop it" if job is not None
                           else console.folder_refusal(project))
                self.reply(HTTPStatus.OK, {**plan, "refusal": refusal})
            elif path == "/api/merge":
                query = self.query()
                project = self.project(query.get("project"))
                self.reply(HTTPStatus.OK, {k: v for k, v in console.merge_plan(project, query.get("run")).items() if not k.startswith("_")})
            elif path in ("/api/drive", "/api/check", "/api/prepare"):
                query = self.query()
                folder, _ = console.target(self.project(query.get("project")), query)
                self.reply(HTTPStatus.OK, console.job_state(folder, path.rsplit("/", 1)[1]))
            else:
                self.reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except BadRequest as error:
            self.reply(error.status, {"error": str(error)})
        except Exception as error:  # noqa: BLE001 - one odd run file must not drop the page's connection
            self.unexpected(error)

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/fsd/upload":
            self.upload()
            return
        if path == "/api/setup/upload":
            self.upload(setup=True)
            return
        # A decision may carry the copy of the card the person saw (the Decisions log's proof): a larger body.
        raw = self.read_raw(BODY_MAX_BYTES + SNAPSHOT_MAX if path in ("/api/approve", "/api/reopen") else BODY_MAX_BYTES)
        if not path.startswith("/api/"):
            self.reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self.guard(post=True):
            return
        console = self.console
        try:
            body = self.body(raw)
            if path == "/api/setup/preview":
                # What setup would run, every field checked; nothing runs but read-only git in checked folders.
                self.reply(HTTPStatus.OK, {**console.setup_plan(body).describe(), "kit": console.need_kit().describe()})
                return
            if path == "/api/setup/run":
                self.reply(HTTPStatus.OK, console.start_setup(body))
                return
            project = self.project(body.get("project"))
            if path == "/api/hold":
                self.reply(HTTPStatus.OK, console.hold(project, body.get("run"), body.get("hold")))
                return
            # The folder of the project that has the named run's branch open; the main folder when nothing is named.
            folder, branch = console.target(project, body) if path not in ("/api/switch", "/api/merge") else (project, None)
            if path in RUN_BOUND_PATHS and branch is not None and branch != folder.branch():
                # The referee acts on the run of the folder's branch: a card for a run elsewhere must not approve this one.
                raise BadRequest(f"the project folder is on other work now ({folder.branch()}), not this one; the page "
                                 "has been brought up to date — look again before you act", HTTPStatus.CONFLICT)
            if path == "/api/start":
                item = text_field(body, "item", 40)
                if not ITEM_PATTERN.fullmatch(item):
                    raise BadRequest("item must be a D or Q id, a Jira key, an FSD section or a plan item "
                                     "(e.g. D4, PROJ-12, 2.7.1, plan:book-loans, plan:labs#3)")
                title = text_field(body, "title", TITLE_MAX)
                base_head = body.get("base_head", False)
                if not isinstance(base_head, bool):
                    raise BadRequest("base_head must be true or false")
                args = ["start", item, f"--title={title}"] + (["--base", "HEAD"] if base_head else [])
                result = console.run_change(folder, args)
            elif path in NEW_RUN_PATHS:
                result = console.start_new_run(project, NEW_RUN_PATHS[path], body)
            elif path == "/api/drive":
                steps = body.get("steps")
                if steps is not None and (isinstance(steps, bool) or not isinstance(steps, int)
                                          or not 0 <= steps <= STEPS_MAX):
                    raise BadRequest(f"steps must be a whole number from 0 to {STEPS_MAX}")
                result = console.start_job(folder, "drive",
                                           ["drive"] if steps is None else ["drive", "--steps", str(steps)])
            elif path == "/api/drive/stop":
                if branch is None and folder.running_job() is None:
                    folder = next(iter(project.running_anywhere()), folder)   # the one thing running, wherever it is
                result = console.stop_job(folder)
            elif path == "/api/check":
                result = console.start_job(folder, "check", ["check"])
            elif path == "/api/prepare":
                result = console.start_prepare(folder, body.get("folder"))
            elif path == "/api/merge":
                result = console.finish_run(project, body.get("run"))
            elif path == "/api/switch":
                result = console.switch_branch(project, body.get("branch"))
            elif path == "/api/reopen":
                step = choice_field(body, "step", STEPS)
                reason = text_field(body, "reason", REASON_MAX)
                proof = console.save_proof(folder, project, body, f"answer-{step}")
                result = console.run_change(folder, ["reopen", step, f"--reason={reason}", *(["--proof", proof] if proof else [])])
                console.keep_proof_if_recorded(folder, proof, result)
            elif path == "/api/approve":
                gate = choice_field(body, "gate", GATES)
                proof = console.save_proof(folder, project, body, f"approve-{gate}")
                result = console.run_change(folder, ["approve", gate, *(["--proof", proof] if proof else [])])
                console.keep_proof_if_recorded(folder, proof, result)
            elif path == "/api/unlock":
                test = test_path_field(body)
                if body.get("run") and test not in console.locked_tests(project, body["run"]):
                    raise BadRequest(f"{test} is not a test this run has locked", HTTPStatus.NOT_FOUND)
                reason = text_field(body, "reason", REASON_MAX)
                result = console.run_change(folder, ["unlock", test, f"--reason={reason}"])
            elif path == "/api/answer":
                result = console.answer_question(project, folder, body)
            elif path == "/api/allow":
                result = console.allow_files(project, folder, body)
            elif path == "/api/open-pr":
                result = console.open_pr(folder, branch)
            elif path == "/api/abandon":
                reason = text_field(body, "reason", REASON_MAX)
                result = console.run_change(folder, ["abandon", f"--reason={reason}"])
            else:
                raise BadRequest("not found", HTTPStatus.NOT_FOUND)
        except BadRequest as error:
            self.reply(error.status, {"error": str(error)})
            return
        except Exception as error:  # noqa: BLE001 - the page gets a plain answer instead of a dropped connection
            self.unexpected(error)
            return
        self.reply(HTTPStatus.OK, result)

    def unexpected(self, error: Exception) -> None:
        """An error no check expected: logged here with its trace, answered as a plain 500 the page can show."""
        traceback.print_exception(error)
        self.reply(HTTPStatus.INTERNAL_SERVER_ERROR,
                   {"error": f"the app hit a problem it did not expect ({error.__class__.__name__}); the details are in its window"})


    def drain(self, length: int) -> None:
        """Read and drop a body the console refuses, so the refusal reaches the client (Windows resets otherwise)."""
        while length > 0:
            chunk = self.rfile.read(min(UPLOAD_CHUNK_BYTES, length))
            if not chunk:
                return
            length -= len(chunk)

    def upload(self, setup: bool = False) -> None:
        """
        POST /api/fsd/upload: a raw PDF body for the Requirements preview; POST /api/setup/upload: the FSD
        for the New project wizard — a .docx, .pdf, .md or .txt, its kind named by its media type, at most
        25 MB (setup.py's limit). Either is staged outside every checkout; answers {staged, bytes[, kind]}.
        """
        what = "FSD" if setup else "PDF"
        limit = wizard.SETUP_UPLOAD_MAX_BYTES if setup else UPLOAD_MAX_BYTES
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            self.close_connection = True
            self.reply(HTTPStatus.LENGTH_REQUIRED, {"error": f"send the {what} with a Content-Length"})
            return
        if length < 0 or length > limit:
            # Never read: the body is too large to take. The connection closes after the answer.
            self.close_connection = True
            self.reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                       {"error": f"the {what} may be at most {limit // (1024 * 1024)} MB"})
            return
        if not self.guard(post=True):  # guard() has replied; drop the body it did not read
            self.drain(length)
            return
        if setup and self.console.kit is None:
            self.drain(length)
            self.reply(HTTPStatus.CONFLICT, {"error": "this console was started without a factory kit; start it again "
                                                      f"with the kit: {wizard.KIT_HINT}"})
            return
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        kind = wizard.FSD_KINDS.get(media_type) if setup else ("pdf" if media_type == UPLOAD_CONTENT_TYPE else None)
        if kind is None:
            self.drain(length)
            self.reply(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                       {"error": "send the FSD as one of: " + ", ".join(sorted(wizard.FSD_KINDS)) if setup
                        else f"send the PDF as {UPLOAD_CONTENT_TYPE}"})
            return
        if length == 0:
            self.reply(HTTPStatus.BAD_REQUEST, {"error": "the upload is empty"})
            return
        try:
            result = self.console.stage_upload(self.rfile, length, kind)
            if setup:
                result["kind"] = kind
        except BadRequest as error:
            self.close_connection = True
            self.reply(error.status, {"error": str(error)})
            return
        self.reply(HTTPStatus.OK, result)


class ConsoleServer(ThreadingHTTPServer):
    """
    One console per port. HTTPServer turns SO_REUSEADDR on, which on Windows lets a second process bind a port another
    still serves, and a click could reach either (the 15 Oct review's #10). There the port is taken exclusively; on
    other systems SO_REUSEADDR only lets a restart reuse a port in TIME_WAIT, and is kept.
    """
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if os.name == "nt" and exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


def make_server(projects: list[Path], port: int = DEFAULT_PORT,
                python: str = sys.executable,
                log_requests: bool = True,
                dry_prefixes: tuple[str, ...] = app_data.DEFAULT_DRY_PREFIXES,
                kit: wizard.Kit | None = None, holds: Holds | None = None) -> tuple[ConsoleServer, Console]:
    console = Console(projects, python, dry_prefixes, kit, holds)
    handler = type("ConsoleHandler", (Handler,), {"console": console, "log_requests": log_requests})
    server = ConsoleServer((HOST, port), handler)
    server.daemon_threads = True
    console.port = server.server_address[1]
    return server, console


def checked_projects(raw: list[str]) -> list[Path]:
    paths = []
    for given in raw:
        path = Path(given).resolve()
        if not (path / FACTORY_SCRIPT).is_file():
            raise SystemExit(f"{path} has no {FACTORY_SCRIPT.as_posix()} — give the root of a checkout")
        paths.append(path)
    return paths


# -- the person's project list ----------------------------------------------------------------------
# One console for all of a person's projects. The list is theirs, kept outside every repository (a
# path inside someone's home folder never goes into one): ~/.factory-console/projects.toml, or the file
# FACTORY_CONSOLE_PROJECTS names. Only the console reads it, and only `--add-project` and
# `--remove-project`, run by the person, change it — one [[projects]] table at a time, nothing else.
#
#     [[projects]]
#     path = "D:/work/acme-shop"
#     name = "Acme Shop"      # optional: the name shown; the folder's name otherwise
#
# A project keeps its own factory: the console holds no rules and runs each project's own referee.

PROJECTS_ENV = "FACTORY_CONSOLE_PROJECTS"
DEFAULT_PROJECTS_FILE = "~/.factory-console/projects.toml"
PROJECTS_KEY = "projects"
PROJECT_NAME_MAX = 60
PROJECT_HEADER = re.compile(r"^\s*\[\[\s*projects\s*\]\]\s*(#.*)?$")
ANY_HEADER = re.compile(r"^\s*\[")
NEW_LIST_HEADER = ("# The Factory app's project list: yours, outside every repository.\n"
                   "# Change it with console.py --add-project <checkout> / --remove-project <checkout or name>.\n")


class ProjectListError(Exception):
    """The list cannot be read or changed as asked. Its message never quotes the file."""


def projects_file() -> Path:
    override = os.environ.get(PROJECTS_ENV)
    return Path(os.path.expanduser(override if override else DEFAULT_PROJECTS_FILE))


def _read_list(path: Path) -> dict | None:
    """The parsed file, or None when there is none. Invalid TOML is an error that names the file, not its text."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ProjectListError(f"your project list ({path}) could not be read: {error.strerror}") from None
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise ProjectListError(f"your project list ({path}) is not valid TOML") from None


def _entry_path(value: object, base: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip() or any(ord(ch) < 32 for ch in value):
        return None
    path = Path(os.path.expanduser(value.strip()))
    return (path if path.is_absolute() else base / path).resolve()


def _display_name(value: object) -> str | None:
    if value is None:
        return None
    if (not isinstance(value, str) or not value.strip() or len(value.strip()) > PROJECT_NAME_MAX
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        return ""
    return value.strip()


def load_project_list(path: Path | None = None) -> tuple[list[tuple[Path, str]], list[str]]:
    """
    The projects on the person's list, and a line for each entry skipped. A skipped entry is named
    by its place and its name — the folder's last part, never its path — so the note can be shown.
    No list: ([], []). An unreadable list raises ProjectListError.
    """
    path = path or projects_file()
    data = _read_list(path)
    if data is None:
        return [], []
    entries = data.get(PROJECTS_KEY, [])
    if not isinstance(entries, list):
        return [], [f"`{PROJECTS_KEY}` is not a list of [[{PROJECTS_KEY}]] tables"]
    projects: list[tuple[Path, str]] = []
    skipped: list[str] = []
    seen_names: set[str] = set()
    seen_paths: set[str] = set()
    for number, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            skipped.append(f"entry {number}: not a table with a path")
            continue
        checkout = _entry_path(entry.get("path"), path.parent)
        name = _display_name(entry.get("name"))
        if checkout is None:
            skipped.append(f"entry {number}{f' ({name})' if name else ''}: no usable path")
            continue
        shown = name or checkout.name
        if name == "":
            skipped.append(f"entry {number} ({checkout.name}): its name must be 1 to {PROJECT_NAME_MAX} "
                           "characters without control characters")
            continue
        if not (checkout / FACTORY_SCRIPT).is_file():
            skipped.append(f"entry {number} ({shown}): no {FACTORY_SCRIPT.as_posix()} there — "
                           "not the root of a checkout, or the folder is gone")
            continue
        if os.path.normcase(str(checkout)) in seen_paths:
            skipped.append(f"entry {number} ({shown}): listed twice")
            continue
        if shown in seen_names:
            skipped.append(f"entry {number} ({shown}): another project already has that name — give one a `name`")
            continue
        seen_paths.add(os.path.normcase(str(checkout)))
        seen_names.add(shown)
        projects.append((checkout, shown))
    return projects, skipped


def _toml_string(value: str) -> str:
    # A TOML basic string: JSON's escapes are TOML's (\\, \", \uXXXX).
    return json.dumps(value, ensure_ascii=False)


def add_project(checkout: str, name: str | None = None, path: Path | None = None) -> str:
    """Append one [[projects]] table; every other byte of the file stays as it was."""
    path = path or projects_file()
    folder = Path(os.path.expanduser(checkout)).resolve()
    if not (folder / FACTORY_SCRIPT).is_file():
        raise ProjectListError(f"{folder.name} has no {FACTORY_SCRIPT.as_posix()} — give the root of a checkout")
    shown = _display_name(name)
    if shown == "":
        raise ProjectListError(f"a name is 1 to {PROJECT_NAME_MAX} characters without control characters")
    data = _read_list(path)
    text = path.read_text(encoding="utf-8") if data is not None else NEW_LIST_HEADER
    listed_entries = (data or {}).get(PROJECTS_KEY, [])
    for entry in listed_entries if isinstance(listed_entries, list) else []:
        if not isinstance(entry, dict):
            continue
        listed = _entry_path(entry.get("path"), path.parent)
        if listed is not None and os.path.normcase(str(listed)) == os.path.normcase(str(folder)):
            raise ProjectListError(f"{shown or folder.name} is already on your project list")
        if (shown or folder.name) == (_display_name(entry.get("name")) or (listed.name if listed else None)):
            raise ProjectListError(f"another project on your list is already called {shown or folder.name} — "
                                   "give this one a different --name")
    block = f"[[{PROJECTS_KEY}]]\npath = {_toml_string(folder.as_posix())}\n"
    if shown:
        block += f"name = {_toml_string(shown)}\n"
    if text and not text.endswith("\n"):
        text += "\n"
    new_text = text + ("\n" if text.strip() else "") + block
    _check_and_write(path, new_text)
    return f"Added {shown or folder.name} to your project list ({path})."


def remove_project(which: str, path: Path | None = None) -> str:
    """Remove the [[projects]] table(s) naming this checkout or this name; nothing else in the file changes."""
    path = path or projects_file()
    if _read_list(path) is None:
        raise ProjectListError(f"there is no project list yet ({path})")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    target = Path(os.path.expanduser(which)).resolve() if which.strip() else None
    keep = [True] * len(lines)
    removed: list[str] = []
    starts = [i for i, line in enumerate(lines) if PROJECT_HEADER.match(line)]
    for start in starts:
        end = next((j for j in range(start + 1, len(lines)) if ANY_HEADER.match(lines[j])), len(lines))
        # Comments and blank lines just above the next table belong to it, not to this one.
        while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")) \
                and end < len(lines):
            end -= 1
        try:
            entry = (tomllib.loads("".join(lines[start:end])).get(PROJECTS_KEY) or [{}])[0]
        except tomllib.TOMLDecodeError:
            continue
        listed = _entry_path(entry.get("path"), path.parent)
        shown = _display_name(entry.get("name")) or (listed.name if listed else "")
        same_path = listed is not None and target is not None and \
            os.path.normcase(str(listed)) == os.path.normcase(str(target))
        if same_path or (shown and shown == which.strip()):
            # The comment lines directly above the table (no blank between) are this entry's own.
            first = start
            while first > 0 and lines[first - 1].lstrip().startswith("#") and keep[first - 1]:
                first -= 1
            # One blank line before it goes too, so removing leaves no growing gap.
            if first > 0 and not lines[first - 1].strip():
                first -= 1
            for i in range(first, end):
                keep[i] = False
            removed.append(shown or "a project")
    if not removed:
        raise ProjectListError(f"nothing on your project list matches {Path(which).name or which}")
    _check_and_write(path, "".join(line for line, kept in zip(lines, keep) if kept))
    return f"Removed {', '.join(removed)} from your project list ({path})."


def _check_and_write(path: Path, text: str) -> None:
    """Refuse a change that would leave the file invalid; otherwise write it whole, atomically."""
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise ProjectListError(f"your project list ({path}) is not in a shape this command can change "
                               f"safely — edit it by hand, as [[{PROJECTS_KEY}]] tables") from None
    write_atomic(path, text)


# -- decisions held for a demo ------------------------------------------------------------------------------
# A person may keep a decision waiting on purpose — a gate kept for a live demo — and hide it from Needs you meanwhile.
# The holds are the person's, kept beside their project list and outside every repository: ~/.factory-console/holds.json,
# or the file FACTORY_CONSOLE_HOLDS names. One JSON object, {"projects": {<repository id>: ["R-012@plan-gate", …]}};
# the repository id is app_data.repo_group's, the same for every folder of a repository. Only this console writes it.

HOLDS_ENV = "FACTORY_CONSOLE_HOLDS"
DEFAULT_HOLDS_FILE = "~/.factory-console/holds.json"
HOLD_KEY = re.compile(r"^R-\d{1,6}@[a-z][a-z-]{0,29}$")
HOLDS_MAX = 100


def inside_repository(path: Path) -> bool:
    """Whether a file at `path` would be inside a git repository (its working tree or its .git folder)."""
    folder = path.parent
    while not folder.exists() and folder.parent != folder:
        folder = folder.parent
    return "true" in app_data.git_out(folder, "rev-parse", "--is-inside-work-tree", "--is-inside-git-dir").split()


class Holds:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.lock = threading.Lock()

    def where(self) -> Path:
        return self.path or Path(os.path.expanduser(os.environ.get(HOLDS_ENV) or DEFAULT_HOLDS_FILE))

    def read(self) -> dict[str, list[str]]:
        """Every project's holds; a missing or unreadable file holds nothing."""
        try:
            data = json.loads(self.where().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        projects = data.get("projects") if isinstance(data, dict) else None
        if not isinstance(projects, dict):
            return {}
        return {str(group): [k for k in keys if isinstance(k, str) and HOLD_KEY.fullmatch(k)]
                for group, keys in projects.items() if isinstance(keys, list)}

    def of(self, group: str) -> list[str]:
        return self.read().get(group, [])

    def change(self, group: str, add: str | None, drop_prefix: str | None, keep) -> list[str]:
        where = self.where()
        if inside_repository(where):
            raise BadRequest("the list of held decisions would be inside a repository, so it is not written: point "
                             f"{HOLDS_ENV} at a file outside every repository", HTTPStatus.CONFLICT)
        with self.lock:
            data = self.read()
            held = [k for k in data.get(group, []) if keep(k) and not (drop_prefix and k.startswith(drop_prefix))]
            if add and add not in held:
                held.append(add)
            data[group] = held[-HOLDS_MAX:]
            data = {g: keys for g, keys in data.items() if keys}
            try:
                write_atomic(where, json.dumps({"projects": data}, indent=2) + "\n")
            except OSError as error:
                raise BadRequest(f"the list of held decisions could not be saved: {error.strerror or error}",
                                 HTTPStatus.CONFLICT) from None
        return data.get(group, [])


# -- each project's factory version -----------------------------------------------------------------
# A project's referee says its version (`factory.py version --json`, which an older referee does not
# have). The newest is the referee beside this console. The console only compares; upgrading is the
# kit's init.py --upgrade, run in that project, in its own pull request.

OWN_FACTORY = Path(__file__).resolve().parent.parent / "factory.py"
FACTORY_VERSION_LINE = re.compile(r'^FACTORY_VERSION\s*=\s*"(\d+(?:\.\d+){0,3})"\s*$', re.MULTILINE)
FACTORY_VERSION_SHAPE = re.compile(r"^\d+(?:\.\d+){0,3}$")
KIT_STAMP_MAX = 60
VERSION_CACHE_SECONDS = 5 * 60
VERSION_TIMEOUT_SECONDS = 60
UNKNOWN_FACTORY = "unknown (older factory)"
UPGRADE_HINT = "run the kit's init.py --upgrade in that project, in its own PR"


def newest_factory_version(own: Path = OWN_FACTORY) -> str | None:
    """The version of the referee beside this console, read from its text — never imported, never run."""
    try:
        found = FACTORY_VERSION_LINE.search(own.read_text(encoding="utf-8"))
    except OSError:
        return None
    return found.group(1) if found else None


def _version_key(version: str) -> tuple[int, ...]:
    parts = [int(part) for part in version.split(".")]
    return tuple(parts + [0] * (4 - len(parts)))


def factory_line(version: str | None, kit: str | None, newest: str | None) -> dict:
    """What the picker and Home show for one project, in words and as fields."""
    known = version is not None
    upgrade = newest is not None and (not known or _version_key(version) < _version_key(newest))
    ahead = known and newest is not None and _version_key(version) > _version_key(newest)
    text = f"Factory {version if known else UNKNOWN_FACTORY}" + (f" (kit {kit})" if kit else "")
    if newest is not None:
        text += f" · newest {newest}"
    if upgrade:
        text += f" — upgrade available: {UPGRADE_HINT}"
    elif ahead:
        text += " — newer than the copy beside this app"
    return {"version": version, "kit": kit, "newest": newest, "known": known, "upgrade": upgrade, "text": text}


def read_factory_version(python: str, checkout: Path, timeout: float = VERSION_TIMEOUT_SECONDS) -> tuple[str | None, str | None]:
    """
    `<checkout>/devtools/factory/factory.py version --json`, run in the checkout — a fixed argv, read-only.
    (None, None) when that referee has no such command, fails, or answers something else.
    """
    try:
        done = subprocess.run([python, str(FACTORY_SCRIPT), "version", "--json"], cwd=checkout,
                              capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if done.returncode != 0:
        return None, None
    try:
        answer = json.loads(decode(done.stdout))
    except json.JSONDecodeError:
        return None, None
    if not isinstance(answer, dict):
        return None, None
    version, kit = answer.get("version"), answer.get("kit")
    if not isinstance(version, str) or not FACTORY_VERSION_SHAPE.fullmatch(version):
        return None, None
    if not isinstance(kit, str) or not kit.strip() or len(kit) > KIT_STAMP_MAX or any(ord(c) < 32 for c in kit):
        kit = None
    return version, kit


def project_factory(console: "Console", project: "Project", fresh: bool = False) -> dict:
    """The project's factory line, kept VERSION_CACHE_SECONDS: an upgrade is rare and a poll is every 15 s."""
    now = time.monotonic()
    with console.cache_lock:
        held = console.version_cache.get(project.name)
    if held is not None and not fresh and now - held[0] < VERSION_CACHE_SECONDS:
        return held[1]
    version, kit = read_factory_version(console.python, project.path)
    line = factory_line(version, kit, newest_factory_version())
    with console.cache_lock:
        console.version_cache[project.name] = (time.monotonic(), line)
    return line


def resolve_projects(given: list[str] | None, list_file: Path | None = None,
                     allow_none: bool = False) -> tuple[list[Path | tuple[Path, str]], list[str]]:
    """
    `--project` wins, and then the list is not read at all. Otherwise the person's list; its skipped
    entries come back as notes. Exits when no project remains — unless `allow_none` (a console started
    with --kit opens on New project, to set up the first one).
    """
    if given:
        return list(checked_projects(given)), []
    where = list_file or projects_file()
    try:
        projects, skipped = load_project_list(where)
    except ProjectListError as error:
        raise SystemExit(f"{error}. Fix it, or give --project <checkout>.") from None
    if not projects and allow_none:
        return [], skipped
    if not projects:
        lines = [f"No project to open: give --project <checkout>, or add one to your project list "
                 f"({where}) with  python {Path(__file__).name} --add-project <checkout>"]
        lines += [f"  skipped {note}" for note in skipped]
        raise SystemExit("\n".join(lines))
    return list(projects), skipped


# -- the read-only snapshot (--export) ------------------------------------------------------------


def collect(projects: list[Path | tuple[Path, str]], *, python: str = sys.executable,
            refs: dict[str, str] | None = None,
            dry_prefixes: tuple[str, ...] = app_data.DEFAULT_DRY_PREFIXES,
            dry_ids: dict[str, set[str]] | None = None,
            backlog_for=None, version_for=None) -> dict:
    """
    What a snapshot shows, for each project: its branch and repository, the referee's
    `backlog --json` (or `backlog_for(path, name, ref)`'s answer, for a caller whose checkouts hold an
    older referee), every run (app_data), and its factory line (project_factory, or
    `version_for(path, name)`; with backlog_for and no version_for, no referee runs and the version shows
    as unknown). Paths of the checkouts and the commands' argv stay out: a snapshot is a page to share.
    Read-only throughout.
    """
    console = Console(projects, python, dry_prefixes)
    try:
        shown = []
        for project in console.projects.values():
            ref = (refs or {}).get(project.name)
            backlog = backlog_for(project.path, project.name, ref) if backlog_for else console.backlog(project, ref, fresh=True)
            backlog = {key: value for key, value in backlog.items() if key not in ("command", "cwd", "cached", "age_seconds")}
            if "plan_details" not in backlog and backlog.get("exit", 0) == 0 and not backlog.get("error"):
                add_plan_details(project.path, backlog)  # a caller's own backlog: its plans read the same way
            if "book" not in backlog and backlog.get("exit", 0) == 0 and not backlog.get("error"):
                add_book(project.path, backlog)
            runs = app_data.project_runs(project.path, project.name, dry_prefixes=console.dry_prefixes,
                                         dry_ids=(dry_ids or {}).get(project.name, set()))
            if version_for:
                factory = version_for(project.path, project.name)
            elif backlog_for:
                factory = factory_line(None, None, newest_factory_version())
            else:
                factory = project_factory(console, project, fresh=True)
            if backlog.get("exit", 0) == 0 and not backlog.get("error"):
                backlog["progress"] = app_data.progress(backlog, runs.get("runs") or [])
                backlog["chapters"] = app_data.chapters(backlog, backlog["progress"], runs.get("runs") or [])
            backlog["version"] = app_data.version_of(backlog)
            branches, elsewhere = project.acting_branches()
            shown.append({"name": project.name, "branch": project.branch(), "branches": branches, "elsewhere": elsewhere,
                          "repository": app_data.repository_of(project.path),
                          "backlog": backlog, "runs": runs, "numbers_extra": [], "factory": factory,
                          "holds": console.holds.of(project.group)})
    finally:
        shutil.rmtree(console.log_dir, ignore_errors=True)
    return {"mode": "export", "projects": shown, "validation": None, "stale_after_seconds": None,
            "regenerate": "", "start": START_HINT}


def finish(payload: dict) -> dict:
    """Stamp a payload with when it was written and a version of its data (which ignores the stamp)."""
    for project in payload.get("projects", []):
        for part in ("backlog", "runs"):
            if isinstance(project.get(part), dict):
                project[part]["version"] = app_data.version_of(project[part])
    payload["generated"] = app_data.now_iso()
    # Each part's own version stands for its data, so a nested `generated` stamp never changes this one.
    payload["version"] = app_data.version_of({
        "projects": [{key: (value.get("version") if key in ("backlog", "runs") and isinstance(value, dict) else value)
                      for key, value in project.items()} for project in payload.get("projects", [])],
        "validation": payload.get("validation")})
    return payload


def embedded(payload: dict) -> str:
    """JSON for a <script type="application/json"> element: no '<', '>' or '&' can end it or open a tag."""
    return (json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render_static(payload: dict) -> str:
    """The page as a read-only snapshot: every live part cut out, the payload embedded."""
    page = INDEX_FILE.read_text(encoding="utf-8")
    page = LIVE_JS.sub("", LIVE_HTML.sub("", page))
    if DATA_SLOT not in page:
        raise ValueError(f"{INDEX_FILE.name} has no data slot")
    return page.replace(DATA_SLOT, DATA_SLOT.replace("></", ">" + embedded(payload) + "</"), 1)


def write_atomic(path: Path, text: str) -> None:
    """A whole file or the old one — a page polling it never reads half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False)
    try:
        with handle:
            handle.write(text)
        os.replace(handle.name, path)
    finally:
        Path(handle.name).unlink(missing_ok=True)


def sidecar_of(page: Path) -> Path:
    return page.with_name(page.stem + SIDECAR_SUFFIX)


def script_sidecar_of(page: Path) -> Path:
    return page.with_name(page.stem + SIDECAR_SCRIPT_SUFFIX)


def write_export(payload: dict, out: Path) -> tuple[Path, Path]:
    """
    The sidecars first, then the page, each atomically. The page embeds the data and the sidecars' names;
    an open page polls them and applies a newer version in place — it never reloads. The .data.json is
    for a page served over http; a page opened from disk cannot fetch a file beside it in Chromium, so it
    loads the same data as the .data.js script, which calls the page's apply function and nothing else.
    """
    sidecar, script = sidecar_of(out), script_sidecar_of(out)
    payload = finish({**payload, "sidecar": sidecar.name, "sidecar_script": script.name})
    write_atomic(sidecar, json.dumps(payload, ensure_ascii=False))
    write_atomic(script, f"window.__factoryApply && window.__factoryApply({embedded(payload)});\n")
    write_atomic(out, render_static(payload))
    return out, sidecar


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A local browser console for the AI factory's referee.")
    parser.add_argument("--project", action="append",
                        help="a project's checkout (repeat for more than one); without it, your project list "
                             f"({DEFAULT_PROJECTS_FILE}, or {PROJECTS_ENV})")
    parser.add_argument("--add-project", metavar="CHECKOUT",
                        help="add a checkout to your project list, and do nothing else")
    parser.add_argument("--remove-project", metavar="CHECKOUT_OR_NAME",
                        help="remove a project from your project list, and do nothing else")
    parser.add_argument("--name", help="the name the app shows for the project: with --add-project, kept in your list; "
                                       "with --project, for this start (the folders given must be of one repository)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--export", type=Path, metavar="PAGE",
                        help="write a read-only snapshot page (and PAGE's .data.json) instead of serving")
    parser.add_argument("--dry-prefix", action="append", metavar="PREFIX",
                        help="a branch prefix that marks dry runs (repeatable; default runs/)")
    parser.add_argument("--open", action="store_true",
                        help="open the app in your browser at its own address (what Start Factory.cmd does)")
    parser.add_argument("--kit", metavar="FOLDER",
                        help="the factory kit (the folder with setup.py and init.py): turns on New project, which "
                             "runs the kit's setup.py; with it the console starts even with no project yet")
    args = parser.parse_args(argv)
    if args.kit is None and not args.export and not args.add_project and not args.remove_project:
        args.kit = os.environ.get("FACTORY_CONSOLE_KIT") or None   # a launcher can name the kit once, for every start
    dry_prefixes = tuple(args.dry_prefix) if args.dry_prefix else app_data.DEFAULT_DRY_PREFIXES
    if args.add_project or args.remove_project:
        if (args.add_project and args.remove_project) or args.project or args.export:
            parser.error("--add-project and --remove-project each run alone")
        if args.name and not args.add_project:
            parser.error("--name goes with --add-project")
        try:
            print(add_project(args.add_project, args.name) if args.add_project else remove_project(args.remove_project))
        except ProjectListError as error:
            raise SystemExit(f"Not changed: {error}.") from None
        return 0
    if args.name and not args.project:
        parser.error("--name goes with --add-project or --project")
    if args.kit and args.export:
        parser.error("--kit is for the live console; a read-only snapshot has no New project")
    kit = None
    if args.kit:
        try:
            kit = wizard.load_kit(args.kit)
        except wizard.KitError as error:
            raise SystemExit(str(error)) from None
    chosen, skipped = resolve_projects(args.project, allow_none=kit is not None)
    if args.name:
        name = _display_name(args.name)
        if not name:
            parser.error(f"--name is 1 to {PROJECT_NAME_MAX} characters without control characters")
        if len(group_projects(chosen)) != 1:
            parser.error("--name names one project, but the folders given are of more than one repository")
        chosen = [(path if isinstance(path, Path) else path[0], name) for path in chosen]
    for note in skipped:
        print(f"Skipped from your project list: {note}", file=sys.stderr)
    if args.export:
        try:
            payload = collect(chosen, dry_prefixes=dry_prefixes)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        payload["regenerate"] = (f"python devtools/factory/console/console.py --export {args.export.name}"
                                 + (" --project <checkout>" if args.project else ""))
        page, sidecar = write_export(payload, args.export)
        print(f"Wrote {page} and {sidecar.name}: a read-only snapshot of {', '.join(p['name'] for p in payload['projects'])}")
        return 0
    try:
        server, console = make_server(chosen, args.port, dry_prefixes=dry_prefixes, kit=kit)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    console.skipped = skipped
    print(f"Factory Console for: {', '.join(console.projects) or 'no project yet — open New project'}")
    if kit is not None:
        print(f"New project uses the factory kit in {kit.path} (kit {kit.version or 'version unknown'}; "
              f"stacks {', '.join(kit.stacks)}).")
    print("Open this address in your browser (keep it to yourself — it carries this session's key):")
    print(f"  http://{HOST}:{console.port}/#token={console.token}")
    if started_by_claude():
        print("Note: this console was started from a Claude Code session. The referee will refuse "
              "the person's commands (approve, unlock, abandon); start it from your own terminal to use them.")
    if args.open and not open_in_browser(f"http://{HOST}:{console.port}/#token={console.token}"):
        print("No browser could be opened here; open the address above yourself.")
    print("Press Ctrl+C to stop. A drive or check the console started stops with it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        # Ctrl+C in this terminal already reached them; this covers any other way out.
        console.stop_all()
        # Staged FSDs last as long as the console (up to 50 MB each); its job logs stay for reading.
        shutil.rmtree(console.uploads_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
