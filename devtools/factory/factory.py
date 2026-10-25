#!/usr/bin/env python3
"""
factory — the referee of the AI factory.

The model does the work; this decides whether a step is done. It owns each
run's state, runs every check itself, and advances a run only when a check
passes. A gate is a person's decision, given from their own terminal.

    factory start <item> --title "..." [--base <ref>] [--new-branch [<name>]]
                                          open a run on the current branch, or on a new one cut from it
    factory status                        where the run is and what it needs
    factory check                         run the current step's check; advance on a pass
    factory drive [--steps N]             resume the run's one working session step by step, to the next gate
    factory approve <gate> [--note ...]   a person approves a gate (never the agent)
    factory unlock <path> --reason ...    a person releases one locked test
    factory reopen <step> --reason ...    re-prove a step and everything after it
    factory abandon --reason ...          a person closes a run that will not continue
    factory impact [--write]              what the change reaches: modules, tests, guides, requirement ids
    factory index [--check]               regenerate docs/factory/runs/INDEX.md
    factory verify [--all]                re-check what can be checked without building

Outside a run's steps (`factory <command> --help` for each): `backlog`, `fsd-diff`, `report`, `render`, `affected`,
`options`, `pr-body`, `link-pr`, `open-pr` (a person), `pr` (show, comments, open, comment,
whoami), `reviewed`, `pr-check`, `protection`, `ai-files` (CI), `fsd-threads`, `jira`, `version`.

A run has a kind, from its item: `plan:<slug>` writes a feature's plan, `book:<name>` writes the
project's backlog (project.md) from its FSD, `revision:V<n>` brings that book up to a new FSD
version, and anything else — a D or Q id, a Jira key, an FSD
section, a plan's task `plan:<stem>#<n>` — is a change. Each kind passes through its own steps
(`kinds` on each [[steps]] entry in factory.toml).

One file per run, docs/factory/runs/R-<nnn>.md. People and the working session
write its sections; the referee owns the header, Checks, Gates and the JSON
Record at the end, and regenerates them from the Record on every command.

In a terminal every command shows its progress as it goes — a ticking timer while something long
runs, and in a drive each thing the AI does — on stderr; the answer on stdout is unchanged. A
drive a Claude Code session started says it in plain lines. FACTORY_PROGRESS=off silences it,
=on forces it.

Configuration: factory.toml beside this file. Design:
docs/plans/ai-factory-harness.md. Python 3.11+, standard library only.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import difflib
import fnmatch
import hashlib
import importlib
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ElementTree
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bitbucket  # noqa: E402 — beside this file; standard library only
import github  # noqa: E402 — beside this file; standard library only
import drive  # noqa: E402 — beside this file; standard library only
import jira  # noqa: E402 — beside this file; standard library only

CONFIG_RELATIVE = "devtools/factory/factory.toml"
# This referee's version: MAJOR.MINOR.PATCH, raised with every change to how it behaves or what it
# answers, so a project can tell which referee it holds (`factory version`). The kit's init.py
# --upgrade replaces this file, and with it this number; a console reads it per project.
FACTORY_VERSION = "1.0.0"
# Optional, top level in factory.toml: the kit release that installed or last upgraded the project.
KIT_STAMP_KEY = "kit_version"
FINGERPRINTED = (CONFIG_RELATIVE, "devtools/factory/factory.py", "devtools/factory/hooks/guard.py",
                 ".claude/settings.json", "CLAUDE.md")

RECORD_FENCE = "```json factory-record"
GIT_SECONDS = 120
NOT_PROVEN = "NOT PROVEN red — could not run: the build fails on another criterion's missing symbol"
COMMAND_SECONDS = 3 * 60 * 60  # the full integration suite takes about half an hour; three hours is a hang
TIMED_OUT = 124  # the exit code `timeout` uses
TURN_SECONDS = 60 * 60  # one turn of the working session or the reviewer; a step's budget ends sooner
TITLE_LINE = re.compile(r"^# (R-\d{3}) — (.*)$")
RUN_FILE_NAME = re.compile(r"^R-(\d{3})\.md$")

AGENT_SECTIONS = ("Intent", "Context", "Spec", "Design", "Plan", "Review", "Acceptance", "Package",
                  "Release", "Outcome")
REFEREE_SECTIONS = ("Checks", "Gates", "Record")
TIERS = ("Low", "Medium", "High")
STATUSES = ("open", "done", "abandoned")
# Steps the referee's own code acts on by name: renaming one in factory.toml would silently switch
# that code off (the harness review), so load_config refuses a configuration without them.
SPEC_GATE, DESIGN_GATE, TESTS_STEP, MERGE_GATE = "spec-gate", "design-gate", "tests", "merge-gate"
NAMED_STEPS = (SPEC_GATE, DESIGN_GATE, TESTS_STEP, MERGE_GATE)
KNOWN_CHECKS = ("intent", "context", "spec", "design", "plan", "tests-red", "build", "integrate", "verify",
                "bitbucket-approval", "github-approval", "local-approval", "review", "package", "acceptance",
                "sources", "plan-doc", "fsd-outline", "fsd-coverage", "project-md", "book-audit",
                "fsd-changes", "revision-triage")
RECORD_KEYS = ("run", "branch", "base", "status", "tier", "steps", "gates")

# What a run is for, from its item: a change to the product — every
# run before kinds existed —, a feature's plan written on demand, the project's book (project.md)
# written from its FSD, or the book brought up to a new FSD version. One list of steps serves them all:
# each step names the kinds it belongs to, so a kind is its steps, not a second pipeline to keep in step
# with the first. A Record without `kind` was written before kinds and is a change.
CHANGE, PLAN, BOOK, REVISION = "change", "plan", "book", "revision"
KINDS = (CHANGE, PLAN, BOOK, REVISION)
PLAN_ITEM = re.compile(r"plan:(?P<slug>[a-z0-9][a-z0-9-]{0,59})")
# A task of a plan already written — a change run, packaged into the plan's task row.
PLAN_TASK_ITEM = re.compile(r"plan:(?P<stem>[a-z0-9][a-z0-9-]{0,59})#(?P<task>\d{1,3})")
BOOK_ITEM = re.compile(r"book:(?P<name>[a-z0-9][a-z0-9-]{0,59})")
# A new FSD version taken into the book: `revision:V2`, the version as the snapshots name it.
REVISION_ITEM = re.compile(r"revision:V(?P<version>[1-9]\d{0,3})")
# Gates of the other kinds the referee acts on by name: the plan gate and the book gate approve a
# file, not a section; the outline gate applies only when the FSD's outline was read by the AI.
PLAN_GATE, OUTLINE_GATE, BOOK_GATE = "plan-gate", "outline-gate", "book-gate"
# The revision gate approves the triage: nothing is closed or changed in the book without a person.
REVISION_GATE = "revision-gate"
NAMED_BY_KIND = {CHANGE: NAMED_STEPS, PLAN: (PLAN_GATE, MERGE_GATE), BOOK: (OUTLINE_GATE, BOOK_GATE),
                 REVISION: (REVISION_GATE,)}
# The sections a run file carries, by kind. A change run's are AGENT_SECTIONS, as before kinds.
KIND_SECTIONS = {CHANGE: AGENT_SECTIONS,
                 PLAN: ("Intent", "Context", "Sources", "Design", "Package"),
                 BOOK: ("Outline", "Inventory", "Features", "Audit", "Package"),
                 REVISION: ("Changes", "Triage", "Answered", "New questions", "Flagged plans", "Change items",
                            "Audit", "Package")}

# A criterion is a change (it fails before the build) unless marked *(guard)*: behaviour the
# change must not break, which passes before and after. One early run needed the difference.
AC_LINE = re.compile(r"^\s*-\s*\*\*(?P<id>[^*]+)\*\*\s*(?P<guard>\*\(guard\)\*)?\s*[—–-]+\s*(?P<text>.+)$")
SECTION_AND_RULE = r"\d+(?:\.\d+)*-R\d+"
TRX_NAMESPACE = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}

TEMPLATES = {
    "Intent": (
        "**Problem.** <!-- what is wrong or missing, in a sentence or two -->\n\n"
        "**Outcome.** <!-- what is true when this run is done -->\n\n"
        "**Touches.** <!-- the paths or modules it will change, each as `path` -->"
    ),
    "Context": "<!-- `factory impact --write` fills this from the diff; add what it cannot see -->",
    "Spec": (
        "<!-- One line per acceptance criterion, in this shape:\n"
        "- **{prefix}-2.13.9-R04** — Given …, when …, then ….\n"
        "Use the requirement's id from {traceability} where one exists, otherwise\n"
        "{run}.AC-1, {run}.AC-2 … A question the requirements do not answer goes to\n"
        "docs/open-questions.md, not here. -->"
    ),
    "Design": (
        "<!-- Either link the feature's plan:  **Plan:** [name](../../plans/<name>.md)\n"
        "or give at least two options, one of them the simplest thing that could work:\n"
        "### Option A — simplest: …\n### Option B — …\n**Chosen:** A, because … -->"
    ),
    "Plan": (
        "<!-- One row per task. Files are `paths` or `globs/**`; no two tasks share one.\n"
        "Verify is the command that proves the task. -->\n\n"
        "| Task | Files | Verify |\n|---|---|---|"
    ),
    "Review": "<!-- The reviewer's verdict and findings, with severity and confidence -->",
    "Acceptance": "<!-- One piece of evidence per criterion, from the running software -->",
    "Package": "<!-- The pull request: link, and what its description carries -->",
    "Release": "<!-- Where it was deployed, and the smoke result -->",
    "Outcome": "<!-- Defects later traced to this run -->",
    "Sources": (
        "<!-- What the plan is written from, one line each:\n"
        "**FSD:** V<n> §… — the newest snapshot, and the sections read (inside the feature's)\n"
        "**Decisions:** the Q ids whose answers it relies on, or `none — <why>`\n"
        "**Live check:** <YYYY-MM-DD> — up to date — modifiedTime <t> | stale — … | could not check — <why> -->"
    ),
    "Outline": "<!-- Written by the referee from the newest FSD: every section, its id and title -->",
    "Inventory": (
        "<!-- One line per FSD section at the feature depth, in the outline's order:\n"
        "- §2.5 Title — feature\n- §2.5.3 Title — part-of <feature>\n- §2.9 Title — cross-cutting\n"
        "- §1.2 Title — not-a-feature (<reason>)\n- §2.14 Title — empty → Q<n> -->"
    ),
    "Features": "<!-- What docs/project.md now holds: phases, rows, questions raised -->",
    "Audit": "<!-- The book auditor's verdict and findings, written by the referee -->",
    "Changes": "<!-- Written by the referee: every section the new FSD version added, removed or changed -->",
    "Triage": (
        "<!-- One line per change in the Changes section, its disposition after the second ` — `:\n"
        "- §2.6 Title — added — feature-row <feature>\n- §2.2 Title — removed — flagged <feature>\n"
        "- §2.3 Title — changed — linked <feature>; change-item D<n> | plan-updated <plan> | plan-flagged <plan>\n"
        "- §2.5 Title — changed — answers Q<n> | question Q<n> | overview | no-impact (<why>) -->"
    ),
    "Answered": "<!-- Each question the new version answers:  - Q<n> — \"<the FSD's words>\" — §<id>, V<n> -->",
    "New questions": "<!-- Each question raised from the new version:  - Q<n> — <the question>; its recommendation is in the file -->",
    "Flagged plans": "<!-- Each plan with work started that a change lands on:  - docs/plans/<name>.md — <what changed> -->",
    "Change items": "<!-- Each built feature a change lands on:  - <feature> — D<n> | <plan-stem>#<task> -->",
}
# A kind's own wording for a section the change kind also has.
KIND_TEMPLATES = {
    PLAN: {
        "Design": "<!-- Link the plan this run writes:  **Plan:** [name](../../plans/{slug}.md) -->",
        "Package": "<!-- The pull request: link, and the feature row in {tracker} naming this run -->",
    },
    BOOK: {"Package": "<!-- The pull request or merge: link, and the checks it passed -->"},
    REVISION: {"Package": "<!-- The pull request or merge: link, and the checks it passed -->"},
}


class FactoryError(Exception):
    """A refusal the referee explains to the person or session that asked."""


class UsageLimit(FactoryError):
    """The account's AI usage limit answered instead of the model: nothing was attempted, so stop."""


# What the CLI answers in place of the model when the account's limit is reached — R-003 met
# "You've hit your session limit · resets 12:20am" three times in a row and counted each as a failure.
# Only the CLI's own wording: this project builds a rate limiter, so a session's answer may well
# say "rate limit" or "limit reached" about the code (the fourth review).
USAGE_LIMIT = re.compile(r"^\s*(you've |you have )?hit your [a-z0-9 -]*limit\b|^\s*usage limit reached\b",
                         re.IGNORECASE)


def refuse_on_usage_limit(data: dict, who: str) -> None:
    """The limit answers in place of the model, at once: no tool was used, and the text is the CLI's."""
    result = str(data.get("result") or "")
    if int(data.get("num_turns") or 0) <= 1 and USAGE_LIMIT.search(result):
        raise UsageLimit(f"The AI usage limit was reached ({who}): {result.strip()[:160]} — nothing was attempted. "
                         "Run `factory drive` (or `factory check`) again once it resets.")


# ---------------------------------------------------------------- config

@dataclass(frozen=True)
class Step:
    name: str
    check: str | None
    gate: bool
    when: str | None
    writes: tuple[str, ...]
    # The referee does the step by itself; a working session is started only if its check fails.
    auto: bool = False
    # Steps naming the same session share one (with `[drive] session = "step"`): intake and context,
    # design and plan.
    session: str | None = None
    # The working session's model and effort for this step; empty takes `[drive]`'s. An easy step
    # need not think as hard as the spec, the design or the build.
    model: str = ""
    effort: str = ""
    # The kinds of run that pass through this step; left out, a change run's only — every step
    # written before kinds was one.
    kinds: tuple[str, ...] = (CHANGE,)


EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class Config:
    raw: dict

    @property
    def steps(self) -> list[Step]:
        # A project with [[stacks]]: each stack's `writes.<step>`, prefixed with its root, joins the step's own.
        return [Step(s["name"], s.get("check"), bool(s.get("gate")), s.get("when"),
                     tuple(s.get("writes", [])) + stack_writes(self.raw, s["name"]),
                     bool(s.get("auto")), s.get("session"), s.get("model", ""), s.get("effort", ""),
                     tuple(s.get("kinds", [CHANGE])))
                for s in self.raw["steps"]]

    def steps_of(self, kind: str) -> list[Step]:
        """The steps a run of this kind passes through, in order."""
        return [step for step in self.steps if kind in step.kinds]

    def session_settings(self, step: Step | None) -> tuple[str, str]:
        """The model and effort a working session runs this step with: the step's own, else `[drive]`'s."""
        drive = self.raw["drive"]
        return ((step.model if step else "") or drive.get("model", ""),
                (step.effort if step else "") or drive.get("effort", ""))

    def step(self, name: str) -> Step:
        for step in self.steps:
            if step.name == name:
                return step
        raise FactoryError(f"factory.toml has no step named {name}")

    @property
    def runs_dir(self) -> str:
        return self.raw["runs_dir"]

    @property
    def modules(self) -> list[dict]:
        return self.raw.get("modules", [])


DEFAULT_PRIVATE_SETTINGS = "~/.ai-factory/local.toml"


def private_settings_path(raw: dict | None = None) -> Path:
    """
    The one place a person's own settings live: outside every repository, in their home folder —
    `private_settings` in factory.toml, or FACTORY_LOCAL_SETTINGS.
    Never committed, never printed, and the guard refuses a session reading it (CLAUDE.md, Private
    settings).
    """
    override = os.environ.get("FACTORY_LOCAL_SETTINGS")
    if override:
        return Path(override)
    if raw is None:
        try:
            with open(repo_root() / CONFIG_RELATIVE, "rb") as handle:
                raw = tomllib.load(handle) if tomllib else {}
        except OSError:
            raw = {}
    return Path(os.path.expanduser(str(raw.get("private_settings") or DEFAULT_PRIVATE_SETTINGS)))


# The settings a person's private file may supply, by section: what is theirs, not the project's.
PRIVATE_KEYS = {("requirements", "sources"): ("live_credential",)}


def load_config(root: Path) -> Config:
    if tomllib is None:
        raise FactoryError("factory needs Python 3.11 or later (tomllib).")
    with open(root / CONFIG_RELATIVE, "rb") as handle:
        raw = tomllib.load(handle)
    private = private_settings_path(raw)
    if private.is_file():
        try:
            with open(private, "rb") as handle:
                mine = tomllib.load(handle)
        except (OSError, ValueError):
            # Never quote the file: it is private. Its name is enough to find it.
            raise FactoryError(f"Your private settings file ({private}) is not valid TOML.") from None
        for (section, table), keys in PRIVATE_KEYS.items():
            supplied = (mine.get(section) or {}).get(table) or {}
            target = raw.setdefault(section, {}).setdefault(table, {})
            target.update({key: supplied[key] for key in keys if key in supplied})
    config = Config(raw)
    check_stacks(config)
    check_steps(config)
    check_session_settings(config)
    return config


def check_steps(config: Config) -> None:
    """
    A typo in factory.toml fails here, not halfway through a run: a `when` no tier matches skipped
    its step for good (a High-tier design gate never applied), a check name was found only when a run
    reached it, and a renamed gate switched off the code that acts on it (the harness review).
    """
    steps = config.steps
    names = [step.name for step in steps]
    problems = [f"step {name!r} is missing — the referee acts on it by name" for name in NAMED_STEPS if name not in names]
    problems += [f"step {name!r} is named twice" for name in sorted({n for n in names if names.count(n) > 1})]
    # Names are unique across kinds — `reopen`, the guard and the Record find a step by its name alone.
    problems += [f"step {step.name}: kinds names {kind!r}, which is not one of {', '.join(KINDS)}"
                 for step in steps for kind in step.kinds if kind not in KINDS]
    # A kind no step serves is simply not offered (`factory start` refuses its items); a kind that is
    # served needs the steps its code acts on, as the change kind always has.
    for kind in KINDS[1:]:
        served = [step.name for step in config.steps_of(kind)]
        if served:
            problems += [f"step {name!r} is missing from the {kind} runs' steps — the referee acts on it by name"
                         for name in NAMED_BY_KIND[kind] if name not in served]
    problems += [f"step {name!r} does not list {CHANGE!r} in its kinds — the referee acts on it by name in a "
                 f"{CHANGE} run" for name in NAMED_STEPS if name in names and CHANGE not in config.step(name).kinds]
    problems += [f"step {step.name}: when = {step.when!r} is not one of {', '.join(TIERS)}"
                 for step in steps if step.when is not None and step.when not in TIERS]
    problems += [f"step {step.name}: check = {step.check!r} is not a check the referee knows"
                 for step in steps if step.check and step.check not in KNOWN_CHECKS and not step.check.startswith("section:")]
    for host in ("bitbucket", "github"):
        approvals = (config.raw.get(host) or {}).get("approvals") or {}
        problems += [f"[{host}.approvals] {tier!r} is not one of {', '.join(TIERS)}" for tier in approvals if tier not in TIERS]
    if problems:
        raise FactoryError("factory.toml: " + "; ".join(problems) + ".")


def check_session_settings(config: Config) -> None:
    """
    An effort the CLI does not know fails here, not halfway through a run. Steps sharing a session
    share its model: a session resumed on another model re-reads its whole history uncached.
    """
    for name, effort in [("[drive]", config.raw.get("drive", {}).get("effort", "")),
                         ("[review]", config.raw.get("review", {}).get("effort", "")),
                         ("[audit]", config.raw.get("audit", {}).get("effort", "")),
                         *((f"step {s.name}", s.effort) for s in config.steps)]:
        if effort and effort not in EFFORTS:
            raise FactoryError(f"factory.toml: {name} effort {effort!r} is not one of {', '.join(EFFORTS)}.")
    models: dict[str, str] = {}
    for step in config.steps:
        if step.session:
            model = config.session_settings(step)[0]
            if models.setdefault(step.session, model) != model:
                raise FactoryError(f"factory.toml: the steps sharing session {step.session!r} name different "
                                   "models; a shared session keeps one model.")


# ---------------------------------------------------------------- git and processes

def git(root: Path, *args: str) -> tuple[int, str]:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=GIT_SECONDS)
    # rstrip, never strip: `status --porcelain` starts a line with a space, and it is part of the status.
    return done.returncode, done.stdout.rstrip()


def git_out(root: Path, *args: str) -> str:
    code, out = git(root, *args)
    return out if code == 0 else ""


def git_strict(root: Path, *args: str) -> str:
    """
    For reads where an empty answer passes a check — "nothing changed", "no merge", "clean": a git
    that failed must stop the check, not read as nothing (the harness review). git_out stays for
    reads where empty means "not there" and the caller says so.
    """
    code, out = git(root, *args)
    if code != 0:
        raise FactoryError(f"git {' '.join(args[:2])} failed (exit {code}); the check cannot trust an empty answer.")
    return out


def current_branch(root: Path) -> str:
    return git_out(root, "rev-parse", "--abbrev-ref", "HEAD")


def head(root: Path) -> str:
    return git_out(root, "rev-parse", "HEAD")


def merge_base(root: Path, base_ref: str) -> str:
    return git_out(root, "merge-base", "HEAD", base_ref) or head(root)


def run_base(root: Path, config: Config) -> str:
    """
    Where a new run's own change starts: the merge base with the base branch — unless every commit HEAD
    has beyond it is already on another branch. A run started on a branch cut from unmerged shared work
    (R-022: 171 harness commits not yet in dev) counted that work as its own, read the change as
    cross-cutting, and ran the whole integration suite for a one-file fix — 24 minutes. Then the run
    starts at HEAD. A person's own commits made before `factory start`, on this branch only (or on its
    own upstream), are not on another branch, so they still count.
    """
    base = merge_base(root, config.raw["base_ref"])
    current = head(root)
    if not base or base == current:
        return base
    branch = current_branch(root)
    private = git_strict(root, "rev-list", "HEAD", "--not", base, f"--exclude={branch}", "--branches",
                         f"--exclude=*/{branch}", "--remotes")
    return base if private.strip() else current


def own_commits(root: Path, base: str) -> tuple[str, ...]:
    """
    The `git log` range of the commits this branch would bring to the base branch: reachable
    from HEAD, not from the run's base and not from the base branch. `dev` merged in is
    excluded, because it is already there (R-002 counted it once); any other branch merged
    in is counted, because the pull request carries it (the review found first-parent
    measurement let a merged branch skip the tier, the budget, the tests and the reviewer).
    """
    base_ref = load_config(root).raw["base_ref"]
    known = git(root, "rev-parse", "--verify", "--quiet", base_ref)[0] == 0
    return ("--no-merges", "HEAD", "--not", base, *([base_ref] if known else []))


def changed_paths(root: Path, base: str) -> list[str]:
    """
    What this branch changed since the base: its own commits, plus staged, unstaged and
    untracked work. See own_commits for which commits are the branch's.
    """
    names: set[str] = set()
    for args in (("log", *own_commits(root, base), "--format=", "--name-only"),
                 ("diff", "--name-only", "HEAD"), ("ls-files", "--others", "--exclude-standard")):
        names.update(line for line in git_strict(root, *args).splitlines() if line)
    names.update(line.split("\t")[-1] for line in merge_resolutions(root, base))
    return sorted(names)


def merge_resolutions(root: Path, base: str) -> list[str]:
    """
    `git diff --numstat` lines for what the branch's merges added beyond an automatic merge.
    `--no-merges` leaves a merge commit out, so a change written into one — a conflict
    resolved by hand, or a file slipped into a `--no-commit` merge — was invisible to the
    tier, the budget, ownership and the reviewer (the re-review's #6). Each merge is redone
    by git and only the difference is the branch's own.
    """
    excluded = own_commits(root, base)[3:]  # the run's base, and the base branch when it is known
    merges = git_strict(root, "rev-list", "--merges", "--parents", "HEAD", "--not", *excluded).splitlines()
    lines: list[str] = []
    for merge in merges:
        commit, *parents = merge.split()
        if len(parents) > 2:
            # git cannot redo an octopus merge as one tree; count all it brought over the first
            # parent — more than its own work, never less (the third review found it skipped).
            lines += [line for line in git_strict(root, "diff", "--numstat", parents[0], commit).splitlines() if line]
            continue
        _, auto = git(root, "merge-tree", "--write-tree", *parents)
        auto_tree = auto.splitlines()[0] if auto else ""
        # No tree back — a git before 2.38, or any merge-tree error: count the whole merge over its
        # first parent rather than nothing. Over-counting fails safe; skipping it hid the merge.
        base = auto_tree if re.fullmatch(r"[0-9a-f]{40,64}", auto_tree) else parents[0]
        lines += [line for line in git_strict(root, "diff", "--numstat", base, commit).splitlines() if line]
    return lines


def dirty_paths(root: Path, runs_dir: str) -> list[str]:
    """Uncommitted changes, apart from run files — the referee writes those constantly. Untracked files are listed one
    by one: a runs folder never committed was shown whole (`docs/factory/`) and passed for uncommitted work."""
    lines = git_strict(root, "status", "--porcelain", "--untracked-files=all").splitlines()
    paths = [line[3:].strip().strip('"') for line in lines if line]
    return [path for path in paths if not path.startswith(runs_dir + "/")]


def find_sh() -> str:
    found = shutil.which("sh")
    if found:
        return found
    git_exe = shutil.which("git")
    if git_exe:
        candidate = Path(git_exe).resolve().parent.parent / "bin" / "sh.exe"
        if candidate.exists():
            return str(candidate)
    raise FactoryError("No POSIX sh found; install Git for Windows or run from Git Bash.")


def remote_name(config: Config) -> str:
    """
    The remote the team shares work through: `remote` in factory.toml, else the one `base_ref` names (`origin/dev` →
    origin, `upstream/main` → upstream), else origin. A fork, or a remote not called origin, was told to push forever.
    """
    given = config.raw.get("remote")
    if isinstance(given, str) and given.strip():
        return given.strip()
    base = str(config.raw.get("base_ref") or "")
    return base.split("/", 1)[0] if "/" in base else "origin"


def base_branch(config: Config) -> str:
    """The base branch's own name, as a pull request names its destination: `base_ref` without its remote."""
    return str(config.raw["base_ref"]).removeprefix(remote_name(config) + "/")


def to_argv(command: str, windows: bool | None = None) -> list[str]:
    # On Windows a backslash is a path separator, not an escape: POSIX splitting turned `src\App.sln` into
    # `srcApp.sln` (the kit audit, 18 Oct). drive.command_words reads a command line as Windows does.
    argv = drive.command_words(command, os.name == "nt" if windows is None else windows)
    if argv and argv[0] in ("python3", "python"):
        argv[0] = sys.executable
    elif argv and argv[0] == "sh":
        argv[0] = find_sh()
    elif argv and os.name == "nt" and not Path(argv[0]).suffix:
        # Popen without a shell finds only an .exe on Windows; npm and npx are .cmd shims. An .exe
        # (dotnet, git) is left exactly as it was.
        found = shutil.which(argv[0])
        if found and found.lower().endswith((".cmd", ".bat")):
            argv[0] = found
    return argv


@dataclass
class Execution:
    command: str
    exit: int
    output: str
    seconds: float


def run_bounded(argv: list[str], cwd: Path, seconds: int, stdin: str | None = None,
                env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """
    subprocess.run with a limit that holds: at the limit the whole process tree is killed. A
    plain timeout kills only the child, then waits for a grandchild still holding the pipe —
    a test host — so a 2 s limit returned after 25 s (the re-review). Raises TimeoutExpired.
    """
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE if stdin is not None else None,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               encoding="utf-8", errors="replace", env=env, **group)
    try:
        out, err = process.communicate(stdin, timeout=seconds)
    except subprocess.TimeoutExpired:
        kill_tree(process.pid)
        try:
            out, err = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            # The child is gone but an orphaned grandchild still holds the pipe, out of the tree's
            # reach (the third review): stop waiting, and give up its output rather than the limit.
            process.kill()
            out, err = "", ""
        raise subprocess.TimeoutExpired(argv, seconds, output=out, stderr=err) from None
    except BaseException:
        # Ctrl+C, or anything else, ends the whole tree too: the child is in its own process group,
        # so the console's interrupt never reached it, and a stopped drive left its Claude session
        # editing the checkout unattended (the branch review).
        kill_tree(process.pid)
        raise
    return subprocess.CompletedProcess(argv, process.returncode, out, err)


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.killpg(pid, 9)
        except ProcessLookupError:
            pass  # already gone


def run_streaming(argv: list[str], cwd: Path, seconds: int, stdin: str | None,
                  on_line: Callable[[str], None], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """
    run_bounded, but stdout is read line by line as it comes and each line is handed to `on_line` —
    so a drive can show what its Claude session is doing while it works. The same limits hold: the
    whole tree is killed at the limit, or on Ctrl+C.
    """
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace", env=env, **group)
    lines: list[str] = []
    errors: list[str] = []

    def read_out() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)
            on_line(line)

    def read_err() -> None:
        assert process.stderr is not None
        errors.append(process.stderr.read())

    readers = [threading.Thread(target=read_out, daemon=True), threading.Thread(target=read_err, daemon=True)]
    for reader in readers:
        reader.start()
    try:
        assert process.stdin is not None
        try:
            process.stdin.write(stdin or "")
            process.stdin.close()
        except OSError:
            pass  # the child ended before reading its input; its exit says why
        process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        kill_tree(process.pid)
        finish_streaming(process, readers)
        raise subprocess.TimeoutExpired(argv, seconds, output="".join(lines), stderr="".join(errors)) from None
    except BaseException:
        kill_tree(process.pid)
        raise
    finish_streaming(process, readers)
    return subprocess.CompletedProcess(argv, process.returncode, "".join(lines), "".join(errors))


def finish_streaming(process: subprocess.Popen, readers: list[threading.Thread]) -> None:
    """Let the readers drain, then close the pipes. An orphaned grandchild may still hold one: stop waiting."""
    for reader in readers:
        reader.join(5)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            with contextlib.suppress(OSError, ValueError):
                pipe.close()


# ---------------------------------------------------------------- live progress

def clock(seconds: float) -> str:
    """How long something has taken, for a person watching: 12 s, 1m 05s, 1h 02m."""
    whole = int(seconds)
    if whole < 60:
        return f"{whole} s"
    if whole < 3600:
        return f"{whole // 60}m {whole % 60:02d}s"
    return f"{whole // 3600}h {whole % 3600 // 60:02d}m"


def terminal_redraws(stream: object) -> bool:
    """Whether `stream` is a terminal that can redraw a line in place; a Windows console must be told to."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-12 if stream is sys.__stderr__ else -11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except (AttributeError, OSError, ValueError):
        return False


class Progress:
    """
    What the referee is doing, said as it happens, so a person never wonders what is going on behind:
    one line per stage, a ticking timer while something long runs, and in a drive each thing the AI
    does. It writes to stderr and only to a terminal — or, as plain lines, to a `factory drive` a
    Claude Code session started, whose output is a file the session reads, not a terminal — so the
    answer on stdout, CI, the self-tests and a session's own `factory check` are unchanged.
    FACTORY_PROGRESS=off silences it; =on forces it.
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    PLAIN_EVERY = 30  # seconds between "still …" lines where a line cannot be redrawn in place

    def __init__(self) -> None:
        self.enabled = False
        self.live = False
        self.stream: object = sys.stderr
        self._lock = threading.RLock()
        self._waits: list[list] = []  # [text, since] — the innermost is shown
        self._drawn = False
        self._ticker: threading.Thread | None = None

    def configure(self, stream: object | None = None, drive_in_session: bool = False) -> None:
        self.stream = stream if stream is not None else sys.stderr
        setting = os.environ.get("FACTORY_PROGRESS", "auto").strip().lower()
        terminal = bool(getattr(self.stream, "isatty", lambda: False)())
        self.enabled = (setting in ("on", "1", "yes", "true")
                        or (setting in ("", "auto") and (terminal or drive_in_session)))
        self.live = self.enabled and terminal and terminal_redraws(self.stream)

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)  # type: ignore[attr-defined]
            self.stream.flush()  # type: ignore[attr-defined]
        except (OSError, ValueError):
            self.enabled = False  # a closed console: progress is never worth failing the command for

    def _erase(self) -> None:
        if self._drawn:
            self._write("\r\x1b[2K")
            self._drawn = False

    def _draw(self) -> None:
        if not (self.enabled and self.live and self._waits):
            return
        text, since = self._waits[-1]
        elapsed = time.monotonic() - since
        frame = self.FRAMES[int(elapsed * 8) % len(self.FRAMES)]
        width = max(20, shutil.get_terminal_size((100, 20)).columns - 14)
        shown = text if len(text) <= width else text[:width - 1] + "…"
        self._write(f"\r\x1b[2K{frame} {shown} · {clock(elapsed)}")
        self._drawn = True

    def say(self, text: str) -> None:
        """A line that stays on screen, above the ticking one."""
        if not self.enabled:
            return
        with self._lock:
            self._erase()
            self._write(text + "\n")
            self._draw()

    @contextlib.contextmanager
    def waiting(self, text: str):
        """While the block runs, a ticking line says what it waits for, and for how long."""
        if not self.enabled:
            yield
            return
        entry = [text, time.monotonic()]
        with self._lock:
            self._waits.append(entry)
            if not self.live:
                self._write(f"… {text}\n")
            self._draw()
            if self._ticker is None:
                self._ticker = threading.Thread(target=self._tick, daemon=True)
                self._ticker.start()
        try:
            yield
        finally:
            with self._lock:
                self._waits.remove(entry)
                self._erase()
                self._draw()

    def _tick(self) -> None:
        last_line = time.monotonic()
        while True:
            time.sleep(0.125 if self.live else 1)
            with self._lock:
                if not self._waits or not self.enabled:
                    self._ticker = None
                    return
                if self.live:
                    self._erase()
                    self._draw()
                elif time.monotonic() - last_line >= self.PLAIN_EVERY:
                    text, since = self._waits[-1]
                    self._write(f"… still {text} · {clock(time.monotonic() - since)}\n")
                    last_line = time.monotonic()


PROGRESS = Progress()

# What each step is, in a few words, for the banner a drive shows when it starts one.
STEP_STORY = {
    "intake": "the AI reads the item and says what is wrong",
    "context": "the AI finds what the change reaches",
    "spec": "the AI writes what \"done\" means, and where each rule comes from",
    "spec-gate": "a person approves what \"done\" means",
    "design": "the AI chooses how to build it",
    "design-gate": "a second person approves the design (High tier)",
    "plan": "the AI splits the work into tasks",
    "tests": "the AI writes the tests first — they must fail before the fix",
    "build": "the AI writes the fix",
    "integrate": "the referee runs the whole test suite",
    "verify": "a separate AI checks the change against the FSD",
    "review": "a separate AI reviews the change",
    "accept": "the referee writes the evidence from its own test runs",
    "package": "the defect row and the pull request text",
    "merge-gate": "a teammate approves the pull request (Bitbucket or GitHub)",
    "release": "how it reached an environment",
    "outcome": "what happened after",
    "sources": "the AI names what the plan is written from — the FSD, the decisions, the live Doc",
    "draft-plan": "the AI writes the feature's plan, its tasks in build order",
    "plan-gate": "a person approves the plan",
    "outline": "the referee reads the FSD's outline",
    "outline-gate": "a person confirms the outline the AI read from a PDF",
    "inventory": "the AI sorts every FSD section: feature, part of one, or not a feature",
    "features": "the AI writes project.md's features and phases",
    "audit": "a separate AI audits project.md against the FSD",
    "book-gate": "a person approves project.md",
    "changes": "the referee compares the new FSD version with the one before it",
    "triage": "the AI works out what each change means for the book",
    "audit.revision": "a separate AI checks the triage against the FSD",
    "revision-gate": "a person approves the book's changes",
    "package.revision": "the book's changes are committed, its checks green",
    "intake.plan": "the AI reads the feature's row and its FSD sections",
    "context.plan": "the AI finds what the feature reaches",
    "package.plan": "the feature's row links the plan and names the run",
    "package.book": "project.md is committed, its checks green",
}
JUDGES = {
    "factory-reviewer": "🧐 The reviewer (a separate AI) reads the change",
    "factory-spec-auditor": "🔬 The spec checker (a separate AI) reads the FSD and the change",
}


def step_banner(run: Run, step: Step, config: Config) -> str:
    names = [s.name for s in config.steps if applicable(s, run.record)]
    number = names.index(step.name) + 1 if step.name in names else 0
    # A shared step says what it does in this kind: `package` writes a plan run back differently.
    story = STEP_STORY.get(f"{step.name}.{record_kind(run.record)}") or STEP_STORY.get(step.name, "")
    if step.check == "local-approval":
        story = "a person approves the finished work"
    return f"━━ {run.id} · step {number}/{len(names)} · {step.name} — {story} ━━"


def short_path(path: str, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path.replace("\\", "/")


def describe_tool(name: str, data: dict, root: Path) -> str:
    """One thing a Claude session did, in a few words; "" for bookkeeping not worth showing."""
    target = str(data.get("file_path") or data.get("notebook_path") or data.get("path") or "")
    if name == "Read":
        return f"📖 reads {short_path(target, root)}"
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"✏️  edits {short_path(target, root)}"
    if name == "Write":
        return f"📝 writes {short_path(target, root)}"
    if name in ("Grep", "Glob"):
        return f"🔍 searches for {str(data.get('pattern') or '')[:60]}"
    if name == "Bash":
        command = " ".join(str(data.get("command") or "").split())
        lowered = command.lower()
        if "factory.py check" in lowered or lowered.startswith("factory check"):
            return "⚖️  asks the referee to check its work"
        if "dotnet test" in lowered or "pytest" in lowered:
            return "🧪 runs the tests"
        if "dotnet build" in lowered:
            return "🏗️  builds the solution"
        if lowered.startswith("git commit"):
            return "📦 commits its work"
        if "pdftotext" in lowered:
            return "📄 reads the FSD"
        return f"⚙️  runs `{command if len(command) <= 70 else command[:69] + '…'}`"
    if name in ("Task", "Agent"):
        return "🧑‍🤝‍🧑 asks a helper"
    if name == "TodoWrite":
        return ""
    return f"🔧 {name}"


def session_actions(event: dict, root: Path) -> list[str]:
    """What one event of a streamed Claude session did: its tool calls, and the first line of what it said."""
    if not isinstance(event, dict) or event.get("type") != "assistant":
        return []
    actions = []
    for block in (event.get("message") or {}).get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            actions.append(describe_tool(str(block.get("name") or ""), block.get("input") or {}, root))
        elif block.get("type") == "text" and str(block.get("text") or "").strip():
            first = str(block["text"]).strip().splitlines()[0]
            actions.append("💭 " + (first if len(first) <= 110 else first[:109] + "…"))
    return [action for action in actions if action]


def watch_session(root: Path) -> Callable[[str], None]:
    """Shows each thing a streamed session does, as it does it."""
    def on_line(line: str) -> None:
        try:
            event = json.loads(line)
        except ValueError:
            return
        for action in session_actions(event, root):
            PROGRESS.say(f"     {action}")
    return on_line


def session_result(stdout: str) -> dict:
    """
    A session's answer: the `result` event that ends a stream (`--output-format stream-json`), or the
    one object `--output-format json` prints. Raises ValueError when there is neither.
    """
    for line in reversed(stdout.strip().splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            return event
    data = json.loads(stdout)
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def describe_command(argv: list[str]) -> str:
    """A command the referee runs, as a person would say it."""
    words = [Path(word).name if index == 0 else word for index, word in enumerate(argv)]
    joined = " ".join(words).lower()
    if "dotnet build" in joined:
        return "🏗️  Building the solution"
    if "dotnet test" in joined:
        after = words[words.index("test") + 1:] if "test" in words else []
        target = next((word for word in after if not word.startswith("-")), "")
        return f"🧪 Running the tests{f' in {Path(target).name}' if target else ''}"
    if "pytest" in joined or "stub_stack.py test" in joined:
        return "🧪 Running the tests"
    text = portable_command(" ".join(argv))
    return f"⚙️  Running {text if len(text) <= 70 else text[:69] + '…'}"


def execute(root: Path, argv: list[str], log: Path | None = None, cwd: Path | None = None) -> Execution:
    """Runs a command at the repository's root, or in `cwd` — a stack's own root in a project with [[stacks]]."""
    started = time.monotonic()
    label = describe_command(argv)
    with PROGRESS.waiting(label):
        try:
            done = run_bounded(argv, cwd or root, COMMAND_SECONDS)
            exit_code, output = done.returncode, done.stdout + done.stderr
        except subprocess.TimeoutExpired as expired:
            # A hung test run must not stall an unattended drive: it fails, with what it printed.
            exit_code, output = TIMED_OUT, (expired.output or "") + f"\n[factory] stopped after {COMMAND_SECONDS} s.\n"
    took = clock(time.monotonic() - started)
    PROGRESS.say(f"   {'✓' if exit_code == 0 else '✗'} {label.split(' ', 1)[-1].strip()} — "
                 f"{'done' if exit_code == 0 else f'exit {exit_code}'} in {took}")
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(output, encoding="utf-8")
    return Execution(portable_command(" ".join(argv)), exit_code, output, round(time.monotonic() - started, 1))


def content_hash(path: Path) -> str:
    """
    A locked test's hash: its content with line endings made LF, so the same text hashes the
    same on a Windows checkout (CRLF) and on CI (LF). R-002's lock, taken on Windows, read as
    a changed test in a checkout with the other line endings.
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def lock_holds(path: Path, digest: str) -> bool:
    """The file is what was locked — by content, or byte for byte under a lock taken before content hashing."""
    return path.exists() and digest in (content_hash(path), sha256_file(path))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for rel in FINGERPRINTED:
        path = root / rel
        digest.update(rel.encode("utf-8"))
        digest.update(path.read_bytes() if path.exists() else b"")
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def local_zone() -> datetime.tzinfo | None:
    """This machine's time zone (None: Python's own idea of local time)."""
    return datetime.datetime.now().astimezone().tzinfo


def calendar_days(instant: str) -> tuple[str, str]:
    """The date of an ISO instant in UTC and on this machine's clock, earliest first: a person in
    Pakistan dates a read the 4th while it is still the 3rd in UTC (the dry run's F-08)."""
    try:
        moment = datetime.datetime.fromisoformat(instant.replace("Z", "+00:00"))
    except ValueError:
        return instant[:10], instant[:10]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    days = (moment.astimezone(datetime.timezone.utc).date().isoformat(), moment.astimezone(local_zone()).date().isoformat())
    return min(days), max(days)


# ---------------------------------------------------------------- the run file

@dataclass
class Run:
    id: str
    title: str
    sections: dict[str, str]
    record: dict
    path: Path | None = None

    def section_text(self, name: str) -> str:
        """The section with its guidance comments removed."""
        return re.sub(r"<!--.*?-->", "", self.sections.get(name, ""), flags=re.DOTALL).strip()


def parse_record(text: str) -> dict | None:
    """
    The one Record: the fence under the `## Record` heading, and only when the file holds
    exactly one. The first fence anywhere used to win, so a fence planted in a session's
    section replaced the referee's state (the re-review's HIGH-1).
    """
    if text.count(RECORD_FENCE) != 1:
        return None
    heading = re.search(r"^## Record[ \t]*$", text, re.MULTILINE)
    start = text.find(RECORD_FENCE)
    if heading is None or start < heading.end():
        return None
    body_start = text.find("\n", start) + 1
    end = text.find("\n```", body_start)
    if end < 0:
        return None
    try:
        return json.loads(text[body_start:end])
    except ValueError:
        return None


def parse_run(text: str, path: Path | None = None) -> Run:
    lines = text.replace("\r\n", "\n").split("\n")
    title = TITLE_LINE.match(lines[0]) if lines else None
    if not title:
        raise FactoryError(f"{path or 'run file'}: the first line must be '# R-<nnn> — <title>'.")
    sections: dict[str, str] = {}
    current: str | None = None
    for line in lines[1:]:
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = ""
        elif current is not None:
            sections[current] += line + "\n"
    record = parse_record(text)
    if record is None:
        raise FactoryError(f"{path or 'run file'}: the Record is missing, not valid JSON, not under its heading, or not the only one.")
    problems = record_problems(record)
    if problems:
        raise FactoryError(f"{path or 'run file'}: the Record " + "; ".join(problems) + ".")
    return Run(title.group(1), title.group(2), sections, record, path)


def record_problems(record: dict) -> list[str]:
    """
    A Record whose status or tier is not one the referee writes hid its run: `"Open"` is not open,
    so the run and its locked tests went unseen (the harness review). Said when the file is read.
    """
    problems = [f"has no {key!r}" for key in RECORD_KEYS if key not in record]
    if "status" in record and record["status"] not in STATUSES:
        problems.append(f"status {record['status']!r} is not one of {', '.join(STATUSES)}")
    tier = record.get("tier")
    if "tier" in record and not (isinstance(tier, dict) and tier.get("value") in TIERS):
        problems.append(f"tier {tier!r} is not one of {', '.join(TIERS)}")
    for key in ("steps", "gates"):
        if key in record and not isinstance(record[key], dict):
            problems.append(f"{key} is not a table")
    if "kind" in record and record["kind"] not in KINDS:
        problems.append(f"kind {record['kind']!r} is not one of {', '.join(KINDS)}")
    return problems


def gate_instruction(step: Step) -> str:
    if step.check == "local-approval":
        return "`factory approve merge` from their own terminal, after reading the evidence; `factory check` then passes it"
    if step.check:
        host = "GitHub" if step.check == "github-approval" else "Bitbucket"
        return f"someone other than the author approves the pull request in {host}; `factory check` then reads it"
    return f"`factory approve {step.name.removesuffix('-gate')}` from their own terminal"


def render_header(run: Run, config: Config) -> str:
    record = run.record
    tier = record["tier"]
    step = current_step(record, config)
    rows = [
        ("Item", record.get("item") or "—"),
        # Only for the kinds that are not a change, so a change run's header reads as it always has.
        *([("Kind", f"{record_kind(record)} — {KIND_STORY[record_kind(record)]}")]
          if record_kind(record) != CHANGE else []),
        *([("Jira", "{type} · {status} when the run started ({updated})".format(**record["jira"]))]
          if record.get("jira") else []),
        ("Branch", f"`{record['branch']}`"),
        ("Tier", f"**{tier['value']}** — {'; '.join(tier.get('reasons') or ['no path reached a higher tier'])}"),
        ("Step", (f"`{step.name}`" + (f" — **a person's decision**: {gate_instruction(step)}" if step.gate else ""))
         if step else "all steps passed"),
        ("Status", record["status"]),
        ("Base", f"`{record['base'][:10]}`"),
        ("PR", f"[#{record['pr']['id']}]({record['pr']['url']})" if record.get("pr") else "—"),
        ("Session", record.get("session") or "—"),
    ]
    table = "\n".join(f"| {name} | {value} |" for name, value in rows)
    return (f"# {run.id} — {run.title}\n\n| | |\n|---|---|\n{table}\n\n"
            "> Written through `devtools/factory/factory.py`. The header, **Checks**, **Gates** and "
            "**Record** are the referee's and are regenerated from the Record on every command; "
            "the other sections are yours.\n")


def render_checks(record: dict) -> str:
    rows = []
    for check in record.get("checks", []):
        counts = check.get("counts")
        count_text = (f"{counts['passed']} passed, {counts['failed']} failed, {counts['notExecuted']} skipped"
                      if counts else "—")
        rows.append(f"| {check['step']} | {check['name']} | {'pass' if check['ok'] else '**fail**'} | "
                    # `or ''`, not a .get default: a check with no log stores sha256 as None (R-002's review).
                    f"{count_text} | `{(check.get('head') or '')[:10]}` | `{(check.get('sha256') or '—')[:12]}` |")
    red = record.get("red")
    lock = record.get("lock", {})
    extra = ""
    if red:
        extra += f"\n\n**Red before build** at `{red['head'][:10]}` — {red['by']}: " + ", ".join(
            f"{ac} ({result})" for ac, result in sorted(red["acs"].items())) + "."
    if lock:
        extra += "\n\n**Locked tests** — a change to any of these fails the run:\n\n" + "\n".join(
            f"- `{path}` `{digest[:12]}`" for path, digest in sorted(lock.items()))
    cost = record.get("cost")
    if cost:
        def tokens(entry: dict) -> str:
            counted = entry.get("tokens")
            if not counted:
                return "—"
            return " / ".join(f"{counted.get(kind, 0) / 1000:,.0f}k" for kind in ("cache_read", "cache_write", "output"))

        def ran_at(entry: dict) -> str:
            return f" ({entry['model']} model, {entry['effort']} effort)" if entry.get("model") else ""

        agents = cost.get("agents", {})
        extra += ("\n\n**Cost** — ${:.2f} at API list price (the tokens priced as the API would; a subscription "
                  "is not charged per token). Tokens: context re-read from cache / written to cache / "
                  "written by the model.\n\n| Step | USD | Tokens | Turns | Seconds | Attempts |\n"
                  "|---|---|---|---|---|---|\n").format(total_usd(cost)) + "\n".join(
            # Sorted: the Record is saved with sorted keys, so insertion order does not survive a re-read,
            # and `verify` read the reordered rows as a hand edit (the kit's demo run).
            # A step names the model and effort it ran at; a record from before they were kept shows none.
            [f"| {name}{ran_at(entry)} | {entry['usd']:.2f} | {tokens(entry)} | {entry['turns']} | {entry['seconds']} | "
             f"{entry['attempts']} |" for name, entry in sorted(cost["steps"].items())]
            + [f"| *{name}* (agent) | {entry['usd']:.2f} | {tokens(entry)} | — | — | {entry['calls']} |"
               for name, entry in sorted(agents.items())])
    live = record.get("live")
    if live:
        extra += (f"\n\n**Live FSD check**, by the referee at {live['at']}: **{live['result']}**"
                  + (f" — {live['why']}" if live.get("why") else
                     f" — Doc modifiedTime {live['modified']} (last check: {live['recorded']}); {live['threads']} "
                     f"comment threads, {live['open']} open, {len(live['changed'])} changed after "
                     f"{live['read_through']}") + "."
                  + (f" Verified live for this run: {live['verified']}." if live.get("verified") else ""))
    if not rows:
        return "No checks have run yet." + extra
    return ("| Step | Check | Result | Tests | Commit | Evidence sha256 |\n|---|---|---|---|---|---|\n"
            + "\n".join(rows) + extra)


def render_gates(record: dict) -> str:
    gates = record.get("gates", {})
    if not gates:
        return "No gate has been approved yet."
    rows = [f"| {name} | {entry['by']} | {entry['at']} | {entry.get('note') or '—'} |"
            for name, entry in sorted(gates.items())]
    unlocks = record.get("unlocks", [])
    extra = "".join(f"\n\n**Unlocked** `{u['path']}` by {u['by']} at {u['at']}: {u['reason']}" for u in unlocks)
    return "| Gate | Approved by | At | Note |\n|---|---|---|---|\n" + "\n".join(rows) + extra


def render_run(run: Run, config: Config) -> str:
    parts = [render_header(run, config)]
    own = KIND_SECTIONS[record_kind(run.record)]
    extra = [name for name in run.sections if name not in own and name not in REFEREE_SECTIONS]
    for name in (*own, *extra):
        parts.append(f"## {name}\n\n{run.sections.get(name, '').strip()}\n")
    parts.append(f"## Checks\n\n{render_checks(run.record)}\n")
    parts.append(f"## Gates\n\n{render_gates(run.record)}\n")
    parts.append(f"## Record\n\n{RECORD_FENCE}\n{json.dumps(run.record, indent=2, sort_keys=True)}\n```\n")
    return "\n".join(parts)


def referee_parts(text: str) -> str:
    """The header and the referee's sections of a run file, for tamper detection."""
    normalized = text.replace("\r\n", "\n")
    header = normalized.split("\n## ", 1)[0]
    kept = [header.strip()]
    for block in normalized.split("\n## ")[1:]:
        name = block.split("\n", 1)[0].strip()
        if name in REFEREE_SECTIONS:
            kept.append(block.strip())
    return "\n".join(kept)


def write_run(root: Path, config: Config, run: Run) -> None:
    path = run.path or root / config.runs_dir / f"{run.id}.md"
    run.path = path
    step = current_step(run.record, config)
    run.record["step"] = (step or Step("done", None, False, None, ())).name
    if step is None and run.record["status"] == "open":
        run.record["status"] = "done"
    run.record = scrub_personal(run.record)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written beside it, then swapped in: a drive stopped mid-write leaves the last whole run file, never half of one.
    partial = path.with_name(f".{path.name}.writing")
    partial.write_text(render_run(run, config), encoding="utf-8", newline="\n")
    replace_when_free(partial, path)
    write_index(root, config)


# How long a run file may be held by another reader before the swap gives up: the app and the guard read it
# in a few milliseconds, so a second covers them many times over.
REPLACE_ATTEMPTS, REPLACE_WAIT_SECONDS = 20, 0.05


def replace_when_free(partial: Path, path: Path) -> None:
    """
    os.replace, again while another program holds the file: on Windows the swap fails while the Factory app
    or the guard is reading it (the 15 Oct review's #2). Still held after a second: a plain refusal that
    names the file, never a traceback, and the last whole file stays.
    """
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(partial, path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                partial.unlink(missing_ok=True)
                raise FactoryError(f"{path.name} is held open by another program, so it could not be written. "
                                   "Close what has it open and try again.") from None
            time.sleep(REPLACE_WAIT_SECONDS)


def load_runs(root: Path, config: Config) -> list[Run]:
    folder = root / config.runs_dir
    if not folder.exists():
        return []
    return [parse_run(path.read_text(encoding="utf-8"), path)
            for path in sorted(folder.iterdir()) if RUN_FILE_NAME.match(path.name)]


def active_run(root: Path, config: Config, run_id: str | None = None) -> Run:
    runs = load_runs(root, config)
    if run_id:
        for run in runs:
            if run.id == run_id:
                return run
        raise FactoryError(f"No run {run_id} in {config.runs_dir}.")
    branch = current_branch(root)
    open_here = [run for run in runs if run.record["branch"] == branch and run.record["status"] == "open"]
    if not open_here:
        raise FactoryError(f"No open run on branch {branch}. Start one with `factory start <item> --title ...`.")
    if len(open_here) > 1:
        raise FactoryError(f"{len(open_here)} open runs on {branch}; name one with --run.")
    return open_here[0]


def next_run_id(root: Path, config: Config) -> str:
    numbers = [int(m.group(1)) for run in load_runs(root, config) if (m := re.match(r"R-(\d{3})", run.id))]
    # Every branch, local and fetched — not only the base: two branches cut from the same dev both
    # took the next number, and their run files collided at merge (the review's LOW-6). One `git log`
    # over every ref names each run file any branch ever held: a `git ls-tree` per ref took 30 s with
    # 553 refs on Windows, and `factory start` sat silent all that time.
    listed = git_out(root, "log", "--all", "--no-renames", "--format=", "--name-only", "--", f"{config.runs_dir}/")
    numbers += [int(m.group(1)) for name in listed.splitlines()
                if (m := RUN_FILE_NAME.match(name.rsplit("/", 1)[-1]))]
    # And every other worktree of this repository, committed or not: two runs started at once in two
    # worktrees both took R-011 (the dry run's F-02). Another machine's uncommitted run stays unseen.
    for line in git_out(root, "worktree", "list", "--porcelain").splitlines():
        folder = Path(line.removeprefix("worktree ")) / config.runs_dir if line.startswith("worktree ") else None
        if folder and folder.is_dir():
            numbers += [int(m.group(1)) for path in folder.iterdir() if (m := RUN_FILE_NAME.match(path.name))]
    return f"R-{max(numbers, default=0) + 1:03d}"


# ---------------------------------------------------------------- steps and tier

KIND_STORY = {CHANGE: "a change to the product", PLAN: "a feature's plan, written on demand",
              BOOK: "the project's backlog, written from its FSD",
              REVISION: "the book brought up to a new FSD version"}


def run_kind(item: str) -> str:
    """The kind of run an item starts. `plan:<stem>#<n>` is a task of a written plan: a change."""
    if PLAN_ITEM.fullmatch(item):
        return PLAN
    if BOOK_ITEM.fullmatch(item):
        return BOOK
    if REVISION_ITEM.fullmatch(item):
        return REVISION
    return CHANGE


def record_kind(record: dict) -> str:
    """The run's kind; a Record from before kinds is a change run, and reads exactly as it did."""
    return record.get("kind") or CHANGE


def applicable(step: Step, record: dict) -> bool:
    if record_kind(record) not in step.kinds:
        return False
    if step.name == OUTLINE_GATE and (record.get("outline") or {}).get("format") not in (None, "pdf"):
        # The outline was read from the FSD's own headings, not by the AI: there is nothing for a
        # person to confirm against the document (the outline gate is for a PDF only).
        return False
    if step.name == DESIGN_GATE and (record.get("screens") or record.get("packages") or record.get("writes")):
        return True   # a new screen's description is approved by a person, whatever the tier (check_design)
    return step.when is None or step.when == record["tier"]["value"]


def current_step(record: dict, config: Config) -> Step | None:
    """The first step that applies and has not passed. A tier that rises reopens a skipped gate."""
    for step in config.steps:
        if not applicable(step, record):
            continue
        done = step.name in record.get("gates", {}) if step.gate else \
            record.get("steps", {}).get(step.name, {}).get("status") == "passed"
        if not done:
            return step
    return None


def tier_for(paths: list[str], config: Config) -> tuple[str, list[str]]:
    rules = config.raw.get("tier", {})
    for tier in ("High", "Medium"):
        reasons = [f"`{path}` matches `{pattern}`" for path in paths for pattern in rules.get(tier.lower(), [])
                   if re.search(pattern, path, re.IGNORECASE)]
        if reasons:
            return tier, reasons[:3]
    return "Low", []


def touches(run: Run) -> list[str]:
    intent = run.section_text("Intent")
    match = re.search(r"\*\*Touches\.\*\*(.*)", intent, re.DOTALL)
    return re.findall(r"`([^`]+)`", match.group(1)) if match else []


def refresh_tier(root: Path, config: Config, run: Run) -> None:
    # The Plan's files too: a High file the Plan names is known at plan, not first when the diff touches it
    # at build — R-019 met its design gate after the build had started (the dry run's F-18).
    paths = touches(run) + [f for task in plan_tasks(run) for f in task["files"]] + changed_paths(root, run.record["base"])
    tier, reasons = tier_for(paths, config)
    words_tier, words_reasons = task_tier(plan_task_text(root, config, run), config)
    if TIERS.index(words_tier) > TIERS.index(tier):
        tier, reasons = words_tier, words_reasons
    if TIERS.index(tier) > TIERS.index(run.record["tier"]["value"]):
        run.record["tier"] = {"value": tier, "reasons": reasons}


def task_tier(text: str, config: Config) -> tuple[str, list[str]]:
    """
    High when a plan task's own words match a High pattern ("migrations"): known when the run starts, not first when
    the build writes the file — an earlier run met its design gate after the build had begun (the dry runs' F-18 again).
    Only High: the Medium rules name paths (`^src/`), which a sentence never matches.
    """
    reasons = [f"the task names `{pattern}`" for pattern in config.raw.get("tier", {}).get("high", [])
               if text and re.search(pattern, text, re.IGNORECASE)]
    return ("High", reasons[:3]) if reasons else ("Low", [])


def plan_task_text(root: Path, config: Config, run: Run) -> str:
    """A plan task run's Task cell, read from its plan in this checkout; "" for any other run or a row not found."""
    task_item = PLAN_TASK_ITEM.fullmatch(run.record.get("item", ""))
    if not task_item:
        return ""
    path = root / plan_path(config, task_item.group("stem"))
    rows = plan_task_rows(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else None
    row = next((r for r in rows or [] if r.get("#", "").strip() == task_item.group("task")), None)
    return (row or {}).get("Task", "")


# ---------------------------------------------------------------- section checks

@dataclass
class Outcome:
    ok: bool
    messages: list[str] = field(default_factory=list)
    gate_by: str | None = None


def check_intent(run: Run) -> Outcome:
    intent = run.section_text("Intent")
    # The text goes on the label's own line; the next label's ** is not an answer.
    missing = [label for label in ("Problem", "Outcome")
               if not re.search(rf"\*\*{label}\.\*\*[ \t]*(?!\*\*)\S", intent)]
    if not touches(run):
        missing.append("Touches")
    if missing:
        return Outcome(False, [f"Intent: write the {', '.join(missing)} part — Touches lists each path as `path`."])
    return Outcome(True)


def acceptance_criteria(run: Run) -> list[tuple[str, str]]:
    return [(m.group("id").strip(), m.group("text").strip())
            for line in run.section_text("Spec").splitlines() if (m := AC_LINE.match(line))]


def guard_criteria(run: Run) -> set[str]:
    """The criteria marked *(guard)* — they pass before the build and after it."""
    return {m.group("id").strip() for line in run.section_text("Spec").splitlines()
            if (m := AC_LINE.match(line)) and m.group("guard")}


def requirement_prefix(config: Config) -> str:
    return config.raw.get("requirements", {}).get("id_prefix", "REQ")


def requirement_ids_in(text: str, config: Config) -> list[str]:
    """Every requirement id in the text: `<id_prefix>-<section>-R<n>`."""
    return re.findall(rf"\b{re.escape(requirement_prefix(config))}-{SECTION_AND_RULE}\b", text)


def is_requirement_id(value: str, config: Config) -> bool:
    return re.fullmatch(rf"{re.escape(requirement_prefix(config))}-{SECTION_AND_RULE}", value) is not None


def section_of(requirement: str, config: Config) -> str:
    """`FSD-2.7.1-R09` or `2.7.1` or `§2.7.1` → `2.7.1`."""
    return requirement.removeprefix(requirement_prefix(config) + "-").split("-R")[0].lstrip("§")


def newest_version(root: Path, folder: str, pattern: str) -> int | None:
    """The highest `V<n>` among a requirements folder's snapshots — numerically, not lexically."""
    found = [int(m.group(1)) for path in (root / folder).glob("*") if (m := re.search(pattern, path.name))] \
        if folder and (root / folder).is_dir() else []
    return max(found) if found else None


def spec_line(spec: str, label: str) -> str | None:
    """The text after `**<label>:**`, or None when the line is absent."""
    match = re.search(rf"^\s*\*\*{re.escape(label)}:\*\*[ \t]*(.*)$", spec, re.MULTILINE)
    return match.group(1).strip() if match else None


def resolved_part(text: str) -> str:
    """A decisions document's answered part: its `## Resolved` section when it has one (open-questions.md
    keeps open ones above it), otherwise all of it (the archive holds only answered questions)."""
    heading = re.search(r"^(#{1,3}) Resolved\b[^\n]*$", text, re.MULTILINE)
    if not heading:
        return text
    rest = text[heading.end():]
    following = re.search(rf"^#{{1,{len(heading.group(1))}}} ", rest, re.MULTILINE)
    return rest[:following.start()] if following else rest


def answered_questions(text: str) -> set[str]:
    """
    The Q ids a decisions document answers. Under a `## Resolved` heading, only a question declared there
    counts — its own heading (`#### Q6 · …`) or a bold id opening a line (`- **Q4**`, `| **Q3** |`):
    a resolved entry's note "Numbered Q9 until the merge" made the open Q9 pass (the dry run's
    F-10). A document without that heading is all answered (the archive), so any mention counts.
    """
    if not re.search(r"^#{1,3} Resolved\b", text, re.MULTILINE):
        return set(re.findall(r"\bQ\d+\b", text))
    return set(re.findall(r"^(?:#{2,6}\s+|[-*|]\s*)?\*{0,2}\[?(Q\d+)\b", resolved_part(text), re.MULTILINE))


LIVE_RESULTS = ("up to date", "verified live", "stale", "could not check")
VERIFIED_LIVE = re.compile(r"verified live\W+modifiedTime\s+(\d{4}-\d{2}-\d{2}T[\d:.]+Z)\s*[—–-]+\s*(\S.*)",
                           re.IGNORECASE)


MODIFIED_TIME = re.compile(r"modifiedTime\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")


def live_check_problems(spec: str, run: Run, root: Path, record: str, section: str = "Spec") -> list[str]:
    """
    A snapshot can be behind the client's live FSD, and the BA answers in its comment threads
    without touching the text. The Spec says what a read of the live Doc found for the sections it
    cites, dated from the day the run started to today: up to date; stale, which a person settles
    with a new snapshot (the session cannot write requirements/); or could not check, with why —
    recorded, never passed off as up to date.

    "Up to date" is the model's claim, so it carries evidence the referee can hold it to: the
    Doc's `modifiedTime`, which must be the one `record` (the FSD folder's read-me) holds for the
    last check. A later one means the Doc changed since: not up to date. It proves the text has not
    moved; the comment threads are still the session's read.
    """
    usage = (f"{section}: add **Live check:** <YYYY-MM-DD> — up to date — modifiedTime <the Doc's> | stale — <what "
             "moved> | could not check — <why>: the live FSD Doc's text and comment threads for the cited "
             "sections (/requirements-check).")
    line = spec_line(spec, "Live check")
    match = re.match(rf"(\d{{4}}-\d{{2}}-\d{{2}})\s*[—–-]+\s*({'|'.join(LIVE_RESULTS)})\b\W*(.*)", line or "",
                     re.IGNORECASE)
    if not match:
        return [usage]
    day, result, detail = match.group(1), match.group(2).lower(), match.group(3).strip()
    # A day counts in UTC or on this machine's clock, whichever the person wrote; nothing wider.
    created = str(run.record.get("created", ""))
    started = calendar_days(created)[0] if created else ""
    if started and day < started:
        return [f"{section}: the live check is dated {day}, before the run started ({started}). Check again."]
    if day > calendar_days(utc_now())[1]:
        return [f"{section}: the live check is dated {day}, after today. Date it the day the Doc was read."]
    if result == "up to date":
        cited = MODIFIED_TIME.search(detail)
        if not cited:
            return [f"{section}: `up to date` names the evidence — `modifiedTime <the Doc's, as the read returned it>`."]
        path = root / record
        recorded = MODIFIED_TIME.findall(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else []
        if not recorded:
            return [f"{section}: {record} records no `modifiedTime` from a previous check to compare with — a person runs "
                    "/requirements-check, which records one."]
        if cited.group(1) != recorded[0]:
            return [f"{section}: the live Doc's modifiedTime is {cited.group(1)}; the last check ({record}) recorded "
                    f"{recorded[0]}. It changed since: that is stale, not up to date."]
    if result == "verified live" and not VERIFIED_LIVE.match(f"verified live — {detail}"):
        return [f"{section}: `verified live` names the evidence and the finding — `modifiedTime <the Doc's> — <cited "
                "sections unchanged | what changed for this run>`."]
    if result == "stale":
        return [f"{section}: the live FSD has moved since the snapshot ({detail or 'no detail given'}). Read the live Doc "
                f"for the sections this {section} cites and write `verified live — modifiedTime <t> — <cited sections "
                f"unchanged | what changed for this run>`, writing the {section} from the live text where it changed; or, "
                "with no way to read it, write that to the Escalation section and stop — a person verifies it, or "
                "refreshes the whole FSD with /requirements-check."]
    if result == "could not check" and not detail:
        return [f"{section}: the live check could not check — say why."]
    return []


DOC_ID = re.compile(r"/document/d/([A-Za-z0-9_-]{20,})")
THREADS_READ_THROUGH = re.compile(r"modified after\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")


def fsd_doc_id(root: Path, sources: dict, record_text: str) -> str:
    """
    The FSD Doc's id: from the check's record when it carries the link, else from the one file that
    lists every requirements link (`links_file`), on the line `fsd_link_label` starts — that file names
    several Docs, so the first one is not necessarily the FSD.
    """
    found = DOC_ID.search(record_text)
    if found:
        return found.group(1)
    links = root / sources.get("links_file", "")
    label = sources.get("fsd_link_label", "")
    if not sources.get("links_file") or not label or not links.is_file():
        return ""
    for line in links.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().lower().startswith(label.lower()) and (found := DOC_ID.search(line)):
            return found.group(1)
    return ""


LIVE_REUSE_SECONDS = 10 * 60


def recent_live_check(run: Run) -> dict | None:
    """
    The run's own live check from the last ten minutes, when it found the Doc up to date — so the
    session's `factory check` and the referee's, a minute apart, ask Drive once, not twice. Asking
    pages through every comment thread (188 on 5 Oct, 34 s), and R-022's spec step paid it four
    times. A check that found anything but "up to date" is always asked again: a person may have
    taken a new snapshot since. The Record it is read from is the referee's; no session writes it.
    """
    last = run.record.get("live") or {}
    if last.get("result") != "up to date" or not last.get("at"):
        return None
    try:
        age = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(last["at"])).total_seconds()
    except (TypeError, ValueError):
        return None
    if not 0 <= age <= LIVE_REUSE_SECONDS:
        return None
    PROGRESS.say(f"🌐 The live FSD check from {clock(age)} ago still holds — Drive is asked again after "
                 f"{LIVE_REUSE_SECONDS // 60} minutes")
    return last


def live_text_path(root: Path, run: Run) -> Path | None:
    """Where the referee leaves the live FSD's text for a run whose snapshot is behind: inside .git, never
    in requirements/ — it is the run's evidence, not a snapshot. None outside a repository."""
    found = git_out(root, "rev-parse", "--git-path", f"factory-live-fsd/{run.id}.txt").strip()
    if not found:
        return None
    path = Path(found)
    return path if path.is_absolute() else root / path


def save_live_text(path: Path, state: dict, text: str, threads: list[dict]) -> None:
    changed = {c["id"] for c in state["changed"]}
    lines = [f"# The live FSD for {path.stem} — modifiedTime {state['modified']} (our snapshot: {state['recorded']})",
             "", "Read by the referee. The sections the Spec cites are compared with the snapshot here; never "
             "copied into requirements/.", "", f"## Comment threads changed after {state['read_through']}", ""]
    lines.append(render_threads([t for t in threads if t.get("id") in changed], state["modified"], state["at"][:10])
                 if changed else "None.")
    lines += ["", "## The Doc's text", "", text]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def referee_live_check(root: Path, sources: dict, record: str, save_to: Path | None = None) -> dict | None:
    """
    The live check done by the referee itself, when `live_credential` gives it a way into Google
    Drive: the Doc's modifiedTime against the one `record` holds from the last check, and every
    comment thread against the time the last check read them through ("… modified after <t>" in
    `record`). Either moving means our snapshot is behind — the text changed, or the BA answered.
    None when no credential is configured (or FACTORY_DRIVE=off); "could not check" when one is but
    Drive could not be asked — then the Spec's own Live check line is what the step reads.
    """
    credential = sources.get("live_credential", "")
    if not credential or os.environ.get("FACTORY_DRIVE") == "off":
        return None
    path = root / record
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    doc = fsd_doc_id(root, sources, text)
    recorded = MODIFIED_TIME.findall(text)
    if not doc or not recorded:
        return {"at": utc_now(), "result": "could not check",
                "why": f"no FSD Google Doc link (in {record} or the links file), or no recorded modifiedTime"}
    read_through = THREADS_READ_THROUGH.findall(text)
    cutoff = read_through[-1] if read_through else recorded[0]
    try:
        with PROGRESS.waiting("🌐 The referee asks Google Drive whether the FSD or its comments moved"):
            token = drive.access_token(credential)
            modified = drive.modified_time(doc, token)
            threads = drive.comment_threads(doc, token)
    except drive.DriveError as error:
        return {"at": utc_now(), "result": "could not check", "why": str(error)}
    changed = sorted((t for t in threads if t.get("modifiedTime", "") > cutoff), key=lambda t: t["modifiedTime"])
    state = {
        "at": utc_now(), "modified": modified, "recorded": recorded[0], "threads": len(threads),
        "open": sum(not t.get("resolved") for t in threads), "read_through": cutoff,
        "changed": [{"id": t.get("id", ""), "modifiedTime": t["modifiedTime"],
                     "quoted": ((t.get("quotedFileContent") or {}).get("value") or "")[:80]} for t in changed],
        "result": "stale" if modified != recorded[0] or changed else "up to date",
    }
    if state["result"] == "stale" and save_to is not None:
        # The default when the snapshot is behind: the run reads the live text for its own sections, so an
        # edit elsewhere in the FSD does not send a person to refresh all of it (R-022, 5 Oct).
        try:
            with PROGRESS.waiting("🌐 The referee fetches the live FSD's text for this run"):
                save_live_text(save_to, state, drive.export_text(doc, token), threads)
            state["live_text"] = str(save_to)
        except drive.DriveError as error:
            state["live_text_error"] = str(error)
    return state


def live_state_problems(state: dict, record: str, spec: str = "", section: str = "Spec") -> list[str]:
    if state["result"] != "stale":
        return []
    line = spec_line(spec, "Live check") or ""
    verified = VERIFIED_LIVE.search(line)
    if verified and state.get("live_text") and verified.group(1) == state["modified"]:
        state["verified"] = verified.group(2).strip()
        return []
    moved = []
    if state["modified"] != state["recorded"]:
        moved.append(f"the Doc changed at {state['modified']} (the last check, {record}, recorded {state['recorded']})")
    if state["changed"]:
        quoted = "; ".join(f"\"{c['quoted']}\"" for c in state["changed"][:3] if c["quoted"])
        moved.append(f"{len(state['changed'])} comment thread(s) changed after {state['read_through']}"
                     + (f" — on {quoted}" if quoted else ""))
    if not state.get("live_text"):
        return [f"{section}: the referee asked the live FSD — {' and '.join(moved)} — but could not fetch its text "
                f"({state.get('live_text_error') or 'no run to save it for'}). A person runs /requirements-check, "
                f"which takes the new snapshot, and the {section} is written from it. Write that to the Escalation "
                "section and stop."]
    return [f"{section}: the referee asked the live FSD — {' and '.join(moved)}. By default the run checks only its own "
            f"sections against the live Doc: read {state['live_text']} (the changed comment threads, then the live "
            f"text) for every section this {section} cites, compare it with the snapshot, and write **Live check:** "
            f"<today> — verified live — modifiedTime {state['modified']} — `cited sections unchanged`, or what "
            f"changed for this run (section, old → new). Where a cited section changed, write the {section} from the live "
            f"text. The person is told at the {GATE_OF_SECTION.get(section, SPEC_GATE).removesuffix('-gate')} gate and decides there: approve, refresh the whole FSD with "
            "/requirements-check, or abandon the run."]


def fsd_changed_notice(record: dict) -> str:
    """What a person reads before approving a Spec built while our FSD snapshot was behind the live Doc."""
    live = record.get("live") or {}
    if live.get("result") != "stale" or "verified" not in live:
        return ""
    return (f"⚠ The FSD changed since our snapshot (live Doc {live['modified']}, snapshot {live['recorded']}, "
            f"{len(live.get('changed', []))} comment thread(s) changed). For this run: {live['verified']}. "
            "Approve to build from the live text; or refresh the whole FSD with /requirements-check first; or "
            "abandon the run.")


def render_threads(threads: list[dict], modified: str, today: str) -> str:
    """The comment threads as a snapshot: what was asked, on which text, and what the BA answered."""
    ordered = sorted(threads, key=lambda t: t.get("createdTime", ""))
    lines = [f"# FSD comment threads — {today}", "",
             f"> Saved by `factory fsd-threads --save` from the live FSD Doc (modifiedTime {modified}): "
             f"{len(threads)} threads, {sum(not t.get('resolved') for t in threads)} open. The client's material, "
             "read-only like the PDFs beside it; the next save is a new file. Where an answer here and the "
             "FSD text disagree, the FSD wins and the difference is a question.", ""]
    for thread in ordered:
        state = "Resolved" if thread.get("resolved") else "Open"
        lines.append(f"## {state} · {thread.get('modifiedTime', '')} · `{thread.get('id', '')}`")
        quoted = ((thread.get("quotedFileContent") or {}).get("value") or "").strip()
        if quoted:
            lines.append("> " + quoted.replace("\n", "\n> "))
        lines.append(f"\n**{(thread.get('author') or {}).get('displayName', '?')}:** {thread.get('content', '').strip()}")
        for reply in thread.get("replies") or []:
            body = reply.get("content", "").strip() or f"*({reply.get('action', 'no text')})*"
            lines.append(f"- **{(reply.get('author') or {}).get('displayName', '?')}** "
                         f"({reply.get('modifiedTime', '')}): {body}")
        lines.append("")
    return "\n".join(lines)


def command_fsd_threads(root: Path, config: Config, args: argparse.Namespace) -> str:
    """The live FSD's state against the last check; with --save, its comment threads as a snapshot."""
    sources = config.raw.get("requirements", {}).get("sources") or {}
    record = sources.get("live_check_record", f"{sources.get('fsd_dir', '')}/read-me.txt")
    if not sources.get("live_credential"):
        raise FactoryError(f"Set [requirements.sources] live_credential in your private settings file "
                           f"({private_settings_path(config.raw)}) first — devtools/README.md, Private settings.")
    text = (root / record).read_text(encoding="utf-8", errors="replace") if (root / record).is_file() else ""
    doc = fsd_doc_id(root, sources, text)
    if not doc:
        raise FactoryError(f"No FSD Google Doc link in {record} or {sources.get('links_file') or 'a links file'}.")
    try:
        with PROGRESS.waiting("🌐 Asking Google Drive for the FSD's last change and its comments"):
            token = drive.access_token(sources["live_credential"])
            modified = drive.modified_time(doc, token)
            threads = drive.comment_threads(doc, token)
    except drive.DriveError as error:
        raise FactoryError(f"Could not ask Google Drive: {error}") from None
    recorded = MODIFIED_TIME.findall(text)
    read_through = (THREADS_READ_THROUGH.findall(text) or recorded or [""])[-1]
    latest = max((t.get("modifiedTime", "") for t in threads), default="")
    changed = sum(t.get("modifiedTime", "") > read_through for t in threads) if read_through else len(threads)
    lines = [f"FSD Doc modifiedTime {modified} — the last check recorded {recorded[0] if recorded else 'none'}.",
             f"{len(threads)} comment threads, {sum(not t.get('resolved') for t in threads)} open; "
             f"{changed} changed after {read_through or 'the last check'}; the latest at {latest or '—'}."]
    if args.save:
        today = utc_now()[:10]
        target = root / sources.get("fsd_dir", "requirements/fsd") / f"comment-threads - {today}.md"
        if target.exists():
            raise FactoryError(f"{target.relative_to(root).as_posix()} already exists — one save a day; "
                               "snapshots are never overwritten.")
        target.write_text(render_threads(threads, modified, today), encoding="utf-8", newline="\n")
        lines += [f"Saved {target.relative_to(root).as_posix()}.",
                  f"Now record the check in {record}: threads read through \"modified after {latest or modified}\"; "
                  f"and modifiedTime {modified} only if the newest snapshot still matches the Doc's text — if the "
                  f"text moved and was not captured, keep the recorded modifiedTime, so the live check stays stale."]
    return "\n".join(lines)


def known_qa_cases(text: str, case_id: str) -> set[str]:
    """
    Every QA case id the suite holds — written out whole (`LAB-002`), or in the short form a row
    listing several uses, where the ids after the first carry only their number: "USR-068, 069" is
    USR-068 and USR-069 (the dry run's F-07). A short number counts only when it has as many digits
    as the whole id before it, so a row's "USR-068, 2 more" adds nothing. Whole ids only: `FAKE-GLB-003`
    is not GLB-003.
    """
    known = set()
    for match in re.finditer(rf"(?<![\w-]){case_id}(?![\w-])", text):
        case = match.group(0)
        known.add(case)
        prefix, _, number = case.rpartition("-")
        tail = re.match(r"(?:\s*(?:,|/|&|\band\b)\s*\d+(?![\w-]))+", text[match.end():])
        known.update(f"{prefix}-{short}" for short in re.findall(r"\d+", tail.group(0) if tail else "")
                     if prefix and len(short) == len(number))
    return known


def spec_source_problems(root: Path, config: Config, run: Run, section: str = "Spec") -> list[str]:
    """
    The Spec names the sources it was written from, so the gate's reader can see them and the
    referee can check them: the FSD revision (the newest — and never `none` when the criteria are
    FSD requirements), the QA cases it answers to (each one real), the decisions it relies on
    (each one answered — a question still open is not a decision), and — when a criterion is about
    who may do something — the newest permissions matrix. A configured path that does not exist
    is an error, not a check switched off. A plan run's Sources section is held to the same rules,
    with no criteria: no QA cases or matrix to cite.
    """
    settings = config.raw.get("requirements", {})
    sources = settings.get("sources") or {}
    if not sources:
        return []
    spec = run.section_text(section)
    problems = [f"factory.toml [requirements.sources] {key} = {sources[key]!r} does not exist."
                for key in ("fsd_dir", "matrix_dir", "qa_suite") if sources.get(key) and not (root / sources[key]).exists()]
    problems += [f"factory.toml [requirements.sources] decisions names {doc!r}, which does not exist."
                 for doc in sources.get("decisions", []) if not (root / doc).is_file()]
    version = sources.get("version", r" - V(\d+)\.pdf$")
    fsd = newest_version(root, sources.get("fsd_dir", ""), version)
    if fsd is not None:
        line = spec_line(spec, "FSD")
        cited = re.search(r"\bV(\d+)\b", line or "")
        if line is None or not (cited or line.lower().startswith("none")):
            problems.append(f"{section}: add **FSD:** — the revision and sections read (`V{fsd} §…`), or `none — <why>`.")
        elif cited and int(cited.group(1)) != fsd:
            problems.append(f"{section}: it cites FSD V{cited.group(1)}; the newest snapshot is V{fsd}. Build from the newest.")
        elif not cited and spec_requirement_ids(run, config):
            problems.append(f"Spec: it names FSD requirements, so **FSD:** names the revision read (`V{fsd} §…`), "
                            "not none.")
        if cited and sources.get("live_check"):
            record = sources.get("live_check_record", f"{sources['fsd_dir']}/read-me.txt")
            state = recent_live_check(run) or referee_live_check(root, sources, record, live_text_path(root, run))
            if state:
                run.record["live"] = state
            if state and state["result"] != "could not check":
                problems += live_state_problems(state, record, spec, section)
            else:
                problems += live_check_problems(spec, run, root, record, section)
    suite = sources.get("qa_suite", "")
    if suite and (root / suite).is_file() and section == "Spec":
        line = spec_line(spec, "QA cases")
        # A whole id: `FAKE-GLB-003` is not GLB-003 (the fourth review).
        case_id = sources.get("qa_case", r"\b[A-Z]{2,5}-\d{3}\b")
        cases = re.findall(rf"(?<![\w-]){case_id}(?![\w-])", line or "")
        if line is None or not (cases or line.lower().startswith("none")):
            problems.append(f"Spec: add **QA cases:** — the cases in {suite} this item answers to, or `none — <why>`.")
        else:
            known = known_qa_cases((root / suite).read_text(encoding="utf-8", errors="replace"), case_id)
            problems += [f"Spec: QA case {case} is not in {suite}." for case in cases if case not in known]
    decided = [doc for doc in sources.get("decisions", []) if (root / doc).is_file()]
    if decided:
        line = spec_line(spec, "Decisions")
        questions = re.findall(r"\bQ\d+\b", line or "")
        if line is None or not (questions or line.lower().startswith("none")):
            problems.append(f"{section}: add **Decisions:** — the Q ids whose answers it relies on, or `none — <why>`.")
        else:
            answered = set().union(*(answered_questions((root / doc).read_text(encoding="utf-8", errors="replace"))
                                     for doc in decided))
            problems += [f"{section}: {q} is not an answered question in {', '.join(decided)} — an open one decides "
                         "nothing yet." for q in questions if q not in answered]
    matrix = newest_version(root, sources.get("matrix_dir", ""), version)
    criteria_text = " ".join(text for _, text in acceptance_criteria(run))
    if matrix is not None and re.search(sources.get("permission_words", r"\b(403|forbidden|permission|role)s?\b"),
                                        criteria_text, re.IGNORECASE):
        cited = re.search(r"Permissions Matrix V(\d+)", spec, re.IGNORECASE)
        if not cited:
            problems.append(f"Spec: a criterion is about who may do something — cite the matrix it follows "
                            f"(`Permissions Matrix V{matrix}`).")
        elif int(cited.group(1)) != matrix:
            problems.append(f"Spec: it cites Permissions Matrix V{cited.group(1)}; the newest is V{matrix}.")
    return problems


def check_spec(run: Run, config: Config, root: Path | None = None) -> Outcome:
    criteria = acceptance_criteria(run)
    if not criteria:
        return Outcome(False, ["Spec: no acceptance criteria. One line each: - **<id>** — Given …, when …, then …."])
    problems = spec_source_problems(root, config, run) if root else []
    own_id = re.compile(rf"^{re.escape(run.id)}\.AC-\d+$")
    seen: set[str] = set()
    for ac, text in criteria:
        if not (is_requirement_id(ac, config) or own_id.match(ac)):
            problems.append(f"{ac}: an id is {requirement_prefix(config)}-<section>-R<n> or {run.id}.AC-<n>.")
        if ac in seen:
            problems.append(f"{ac}: used twice.")
        seen.add(ac)
        # Whole words: "authenticated" holds "then" (found by R-002's working session).
        found = [re.search(rf"\b{word}\b", text, re.IGNORECASE) for word in ("given", "when", "then")]
        positions = [match.start() if match else -1 for match in found]
        if -1 in positions or positions != sorted(positions):
            problems.append(f"{ac}: say Given …, when …, then … — in that order — so it can be tested.")
    if len(guard_criteria(run)) == len(criteria):
        problems.append("Every criterion is a guard, so nothing would prove the change. At least one criterion "
                        "must fail before the build.")
    return Outcome(not problems, problems)


PACKAGE_LINE = re.compile(r"^\s*[-*]\s*(?P<root>[\w./-]+)(?P<dev>\s+dev)?\s*:\s*(?P<list>.+)$")
EXACT_VERSION = re.compile(r"^\d[\w.+-]*$")


def design_packages(design: str) -> tuple[list[dict], list[str]]:
    """
    A design's `### Packages` list — one line per stack, `- <stack folder, or .>[ dev]: name@version, …` — as
    {root, dev, name, version} entries, and its problems: every package needs an exact version, so what a person
    approves is exactly what is installed.
    """
    match = re.search(r"^###\s+Packages\b.*?$(?P<body>.*?)(?=^#{1,3}\s|\Z)", design, re.MULTILINE | re.DOTALL)
    if not match:
        return [], []
    packages, problems = [], []
    for line in match.group("body").splitlines():
        found = PACKAGE_LINE.match(line)
        if not found:
            continue
        root = "" if found.group("root") == "." else found.group("root").strip("/")
        for item in (part.strip().strip("`") for part in found.group("list").split(",")):
            name, at, version = item.rpartition("@")
            if not at or not name or not EXACT_VERSION.match(version):
                problems.append(f"Design: `{item}` under Packages needs an exact version (name@1.2.3), so what a "
                                "person approves is exactly what is installed.")
                continue
            packages.append({"root": root, "dev": bool(found.group("dev")), "name": name, "version": version})
    return packages, problems


WRITES_LINE = re.compile(r"^\s*[-*]\s*`(?P<path>[^`]+)`")
ALLOW_PATH = re.compile(r"[\w.*/-]+")


def design_writes(design: str, config: Config | None) -> tuple[list[str], list[str]]:
    """
    A design's `### Writes` list — `- \\`compose.yaml\\` — why` — the files the build will write where its step may
    not yet: the paths a person allows by approving the design (command_approve), and the design's problems. One the
    build step already writes is left out; one no step may ever be given (allow_refusal) is a problem. Without a
    config the list is taken as written.
    """
    match = re.search(r"^###\s+Writes\b.*?$(?P<body>.*?)(?=^#{1,3}\s|\Z)", design, re.MULTILINE | re.DOTALL)
    if not match:
        return [], []
    paths = list(dict.fromkeys(found.group("path").strip() for line in match.group("body").splitlines()
                               if (found := WRITES_LINE.match(line))))
    if config is None:
        return paths, []
    build = next((step for step in config.steps if step.check == "build"), None)
    wanted, problems = [], []
    for path in paths:
        refused = allow_refusal(config, "build", path)
        if refused:
            problems.append(f"Design: `{path}` under Writes {refused}")
        elif build is not None and not any(path == w or glob_match(path, w) for w in build.writes):
            wanted.append(path)
    return wanted, problems


def allow_refusal(config: Config, step: str, path: str) -> str | None:
    """
    Why `path` may never join a step's writes, or None. A step's list widens only by a person's yes, and never to
    what a person guards (the guard's lists, the files that steer the AI, the factory's own folder), never to the
    tests step's files for the build (a build that rewrote its tests would pass anything), and never to everything.
    """
    if not ALLOW_PATH.fullmatch(path) or path.startswith(("/", "-")) or ".." in path.split("/"):
        return "is not a path inside the repository (letters, digits, . _ - / and * only)."
    if path.startswith("*"):
        return "would let the step write anywhere; name a file or a folder (`devtools/scripts/**`)."
    guard = config.raw.get("guard") or {}
    guarded = [*(guard.get(key) or [] for key in ("read_only", "ask", "applied", "never_by_hand")),
               (config.raw.get("ai_files") or {}).get("paths") or [], [CONFIG_RELATIVE, "devtools/factory/**"]]
    hit = next((g for group in guarded for g in group if patterns_overlap(path, g) or glob_match(path, g)), None)
    if hit:
        return f"is one a person guards ({hit}); no step may write it."
    tests = next((s for s in config.steps if s.check == "tests-red"), None)
    if step != (tests.name if tests else "") and tests is not None and any(
            patterns_overlap(path, w) or glob_match(path, w) for w in tests.writes):
        return f"is the {tests.name} step's; only it writes tests."
    return None


def writes_added(text: str, step: str, paths: list[str], note: str) -> tuple[str, list[str]]:
    """
    factory.toml's text with `paths` joined to the named step's `writes`, under a comment saying who allowed them,
    and the paths that were new. Edited as text, so every comment in the file stays; a path the list already covers
    is not added twice.
    """
    headers = [m.start() for m in re.finditer(r"^\[\[?[^\]\n]+\]\]?[ \t]*(#[^\n]*)?$", text, re.MULTILINE)]
    block = next(((start, end) for start, end in zip(headers, headers[1:] + [len(text)])
                  if text.startswith("[[steps]]", start)
                  and re.search(rf'^name\s*=\s*"{re.escape(step)}"\s*$', text[start:end], re.MULTILINE)), None)
    if block is None:
        raise FactoryError(f"factory.toml has no step named {step}")
    start, end = block
    found = re.search(r"^writes\s*=\s*\[.*?\][ \t]*$", text[start:end], re.MULTILINE | re.DOTALL)
    current = tomllib.loads(found.group(0))["writes"] if found else []
    added = [p for p in dict.fromkeys(paths) if not any(p == w or glob_match(p, w) for w in current)]
    if not added:
        return text, []
    items = [json.dumps(p) for p in [*current, *added]]
    lines, line = [], "writes = ["
    for index, item in enumerate(items):
        piece = item + (", " if index < len(items) - 1 else "]")
        if len(line) + len(piece.rstrip()) > 110 and line.strip() not in ("writes = [", ""):
            lines.append(line.rstrip())
            line = " " * len("writes = [")
        line += piece
    lines.append(line if items else "writes = []")
    new = f"# {note}\n" + "\n".join(lines)
    if found:
        at, stop = start + found.start(), start + found.end()
    else:
        body = text[start:end].rstrip("\n")
        while body.splitlines()[-1].lstrip().startswith("#"):
            body = body[:body.rindex("\n")]
        at = stop = start + len(body)
        new = "\n" + new
    result = text[:at] + new + text[stop:]
    tomllib.loads(result)
    return result, added


def merges_locally(config: Config) -> bool:
    """A project with no remote: its merge gate is a person's local approval, and the app adds work to main itself."""
    return any(step.check == "local-approval" for step in config.steps)


def refuse_shared_branch(root: Path, config: Config, doing: str, base_when_local: bool = False) -> str:
    """
    The folder's branch, when it is a work branch; a person's decision is never committed on a shared one. With
    `base_when_local`, the base branch too in a project that merges locally: nobody else shares it, and an answer
    refused there was lost from the app without the person seeing why.
    """
    branch = current_branch(root)
    if base_when_local and branch == base_branch(config) and merges_locally(config):
        return branch
    if branch in ("", "HEAD") or branch == base_branch(config) or branch in config.raw.get("protected_branches", []):
        raise FactoryError(f"The folder is on {branch or 'no branch'}. {doing} on a work branch — the one whose run "
                           "asked, or a branch of its own — never on a shared one.")
    return branch


def allow_writes(root: Path, config: Config, step: str, paths: list[str], person: str, why: str) -> list[str]:
    """
    A person's yes to a step writing `paths`: joined to its `writes` in factory.toml and committed on the work branch,
    so the run that needed them reads them. Refuses what no step may write (allow_refusal). The paths that were new.
    """
    config.step(step)
    refused = [f"{path} {reason}" for path in paths if (reason := allow_refusal(config, step, path))]
    if refused:
        raise FactoryError("Not allowed:\n" + "\n".join(f"  - {r}" for r in refused))
    refuse_shared_branch(root, config, "Allow files")
    if git_strict(root, "status", "--porcelain", "--", CONFIG_RELATIVE):
        raise FactoryError(f"{CONFIG_RELATIVE} has changes not yet committed; commit or undo them first.")
    path = root / CONFIG_RELATIVE
    text, added = writes_added(path.read_text(encoding="utf-8"), step, paths,
                               f"{', '.join(paths)}: allowed by {person}, {utc_now()[:10]} ({why}).")
    if not added:
        return []
    path.write_text(text, encoding="utf-8")
    git_strict(root, "add", "--", CONFIG_RELATIVE)
    code, out = git(root, "commit", "-m", f"Let the {step} step write {', '.join(added)} ({why}; {person} agreed)",
                    "--", CONFIG_RELATIVE)
    if code != 0:
        raise FactoryError(f"The change to {CONFIG_RELATIVE} is written but the commit failed:\n{out}")
    return added


def install_approved_packages(root: Path, config: Config, run: Run) -> str:
    """
    Install the packages the run's approved design names, before its build: each with its stack's own command
    (`[adapter] install_package` / `install_dev_package`, `{name}` and `{version}` filled in), in the stack's folder,
    then one commit of what they changed. The AI never installs anything itself (an unattended session that could would
    pick its own); a person approved this exact list at the design gate. What it said, or "" when there is nothing to do.
    """
    wanted = run.record.get("packages") or []
    if not wanted or run.record.get("packages_installed") == wanted:
        return ""
    if DESIGN_GATE not in (run.record.get("gates") or {}):
        raise FactoryError(f"{run.id} names packages its design gate has not approved; a person approves the design first.")
    by_root = {stack.root: stack for stack in stacks(config)}
    done = []
    for package in wanted:
        stack = by_root.get(package["root"]) or (by_root.get("") if not is_multi_stack(config) else None)
        if stack is None:
            raise FactoryError(f"{run.id}'s design names packages for `{package['root']}/`, which is no stack's folder.")
        template = stack.config.raw.get("adapter", {}).get("install_dev_package" if package["dev"] else "install_package")
        if not template:
            raise FactoryError(f"{stack.name or 'this project'}'s adapter says no install command ([adapter] install_package): "
                               f"a person installs {package['name']}@{package['version']}, commits it, then `factory check`.")
        command = to_argv(template.format(name=package["name"], version=package["version"]))
        folder = root / stack.root if stack.root else root
        installed = subprocess.run(command, cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=COMMAND_SECONDS, check=False)
        if installed.returncode != 0:
            raise FactoryError(f"Installing {package['name']}@{package['version']} failed: "
                               + (installed.stderr or installed.stdout).strip()[-600:])
        done.append(f"{package['name']}@{package['version']}" + (" (dev)" if package["dev"] else ""))
    git_strict(root, "add", "-u")
    committed = subprocess.run(["git", "commit", "-q", "-m", f"Install the packages {run.id}'s approved design names: {', '.join(done)}"],
                               cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=GIT_SECONDS)
    if committed.returncode != 0:
        raise FactoryError("The installed packages could not be committed: " + (committed.stderr or committed.stdout).strip()[-600:])
    run.record["packages_installed"] = wanted
    write_run(root, config, run)
    return f"{run.id}: installed {', '.join(done)}, as the approved design names, and committed them."


def check_design(root: Path, run: Run, config: Config | None = None) -> Outcome:
    design = run.section_text("Design")
    # A screen described with no wireframe (`### Screen — <name>`, docs/ui-standard.md) is approved by a person before it
    # is built, at any tier: the design gate applies to the run from here (applicable()).
    screens = [name.strip() for name in re.findall(r"^###\s+Screen\b\s*[—:-]?\s*(.*)$", design, re.MULTILINE)]
    if screens:
        run.record["screens"] = screens
    else:
        run.record.pop("screens", None)
    # The packages the build needs (`### Packages`): approved by a person at the design gate, which they bring at any
    # tier, and installed by the referee before the build (install_approved_packages) — never by the AI itself.
    packages, problems = design_packages(design)
    if packages:
        run.record["packages"] = packages
    else:
        run.record.pop("packages", None)
    # The files the build writes where no step may yet (`### Writes`): allowed by the person who approves the design.
    writes, refused = design_writes(design, config)
    problems += refused
    if writes:
        run.record["writes"] = writes
    else:
        run.record.pop("writes", None)
    linked =re.search(r"\*\*Plan:\*\*\s*\[[^\]]*\]\(([^)]+)\)", design)
    if linked:
        target = (run.path.parent / linked.group(1)).resolve() if run.path else None
        if target is not None and not target.exists():
            return Outcome(False, [f"Design: the linked plan {linked.group(1)} does not exist."])
        return Outcome(not problems, problems)
    options = re.findall(r"^###\s+Option\b(.*)$", design, re.MULTILINE)
    if len(options) < 2:
        problems.append("Design: give at least two options (### Option A — …), or link the feature's plan.")
    if not any("simplest" in option.lower() for option in options):
        problems.append("Design: one option must be the simplest thing that could work — say so in its heading.")
    if not re.search(r"\*\*Chosen:\*\*\s*\S", design):
        problems.append("Design: say which option is chosen and why (**Chosen:** …).")
    return Outcome(not problems, problems)


def plan_tasks(run: Run) -> list[dict]:
    rows = [line for line in run.section_text("Plan").splitlines() if line.strip().startswith("|")]
    if len(rows) < 2:
        return []
    def cells_of(row: str) -> list[str]:
        # A cell may hold an escaped pipe — a test filter's "AC=a\|AC=b" — which is not a column.
        return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", row.strip().strip("|"))]

    headers = [cell.lower() for cell in cells_of(rows[0])]
    tasks = []
    for row in rows[2:]:
        cells = cells_of(row)
        entry = dict(zip(headers, cells))
        tasks.append({"task": entry.get("task", ""), "files": re.findall(r"`([^`]+)`", entry.get("files", "")),
                      "verify": entry.get("verify", "")})
    return tasks


def glob_match(path: str, pattern: str) -> bool:
    """
    A repository glob: `*` spans folders, and `**/` also matches no folder at all — so
    `tests/**/*.py` holds `tests/test_pricing.py`. fnmatch alone needs a folder there, which
    the kit's demo run found: a flat test folder counted as zero test lines.
    Case-insensitive on every OS, as the guard reads them:
    fnmatch.fnmatch folds case only on Windows, so on Linux CI `.Claude/settings.local.json`
    passed the AI-files gate while NTFS reads it as `.claude/`.
    """
    path, pattern = path.lower(), pattern.lower()
    return fnmatch.fnmatchcase(path, pattern) or ("**/" in pattern
                                                  and fnmatch.fnmatchcase(path, pattern.replace("**/", "")))


def patterns_overlap(first: str, second: str) -> bool:
    return first == second or fnmatch.fnmatch(first, second) or fnmatch.fnmatch(second, first)


def check_plan(run: Run, config: Config | None = None) -> Outcome:
    tasks = plan_tasks(run)
    if not tasks:
        return Outcome(False, ["Plan: add a table with Task, Files and Verify columns, one row per task."])
    problems = []
    for task in tasks:
        if not task["files"]:
            problems.append(f"Plan: '{task['task']}' names no files.")
        if not task["verify"]:
            problems.append(f"Plan: '{task['task']}' has no command that verifies it.")
    for index, task in enumerate(tasks):
        for other in tasks[index + 1:]:
            shared = [f for f in task["files"] for o in other["files"] if patterns_overlap(f, o)]
            if shared:
                problems.append(f"Plan: '{task['task']}' and '{other['task']}' both own {', '.join(sorted(set(shared)))}.")
    if config is not None:
        problems += plan_scope_problems(run, config, tasks)
    return Outcome(not problems, problems)


def plan_scope_problems(run: Run, config: Config, tasks: list[dict]) -> list[str]:
    """
    Each task's files lie inside what the step that will do it may write: the Plan's tests task is the
    tests step's, every other task the build step's. A Plan whose build task named an architecture test
    passed here, and the unattended build session was refused by the guard and escalated (the dry run's
    F-18). The build step never writes tests/ — a test the change makes stale belongs to the tests task.
    """
    steps = {step.check: step for step in config.steps if step.check}
    tests_step, build_step = steps.get("tests-red"), steps.get("build")
    if tests_step is None or build_step is None:
        return []
    test_tasks = [t["task"] for t in plan_test_tasks(run, config)]
    problems, allowable = [], []
    for task in tasks:
        step = tests_step if task["task"] in test_tasks else build_step
        outside = [f for f in task["files"] if not any(f == w or glob_match(f, w) for w in step.writes)]
        if outside:
            # A file in a new place is a person's yes, not the AI's to route around.
            fresh = [f for f in outside if step is build_step and not allow_refusal(config, step.name, f)]
            allowable += fresh
            hint = (" A test file goes in the tests task (the tests step writes tests/), which then holds only "
                    "test files." if step is build_step and any(f.startswith("tests/") for f in outside) else
                    f" A person may allow {'them' if len(fresh) > 1 else 'it'}: `factory allow {' '.join(fresh)}`; "
                    "or name them under the Design's `### Writes` and reopen the design." if fresh else
                    " Split the task so each part is one step's.")
            problems.append(f"Plan: '{task['task'][:60]}' is the {step.name} step's, which may write only "
                            f"{', '.join(step.writes) or 'nothing'}; it names {', '.join(outside)}.{hint}")
    if allowable:
        run.record["needs_allow"] = {"step": build_step.name, "paths": list(dict.fromkeys(allowable))}
    else:
        run.record.pop("needs_allow", None)
    return problems


def check_package(root: Path, config: Config, run: Run) -> Outcome:
    """
    The Package section is filled, and the run has written itself back into the book: a
    defect's row in the defect log names the run; anything else — a plan slice, an FSD
    change — names it in its feature's row in the tracker (project.md), so "what is left"
    and "how was it delivered" link both ways. A plan run names itself in its feature's row
    beside the plan it links; a plan's task in its task row; a book run commits what it wrote.
    """
    problems = [] if run.section_text("Package") else ["Package: the section is empty."]
    # The locks bind until the merge gate: a locked test edited at package passed this check (F-13).
    problems += lock_problems(root, run)
    settings = config.raw.get("requirements", {})
    item = run.record.get("item", "")
    kind = record_kind(run.record)
    if kind == PLAN:
        problems += plan_package_problems(root, config, run)
    elif kind == BOOK:
        problems += book_package_problems(root, config, run)
    elif kind == REVISION:
        problems += revision_package_problems(root, config, run)
    elif re.fullmatch(r"D\d+", item):
        rows = [line for log in settings.get("defect_logs", []) if (root / log).is_file()
                for line in (root / log).read_text(encoding="utf-8", errors="replace").splitlines()
                if re.search(rf"\*\*{item}\*\*", line)]
        if not any(run.id in row for row in rows):
            problems.append(f"{item}'s row in the defect log does not name {run.id}. Add "
                            f"\"fixed by factory run [{run.id}](factory/runs/{run.id}.md)\" to it.")
    elif item.startswith("class:"):
        # A rule promoted from a recurring defect class is written where rules live, naming its run.
        docs = config.raw.get("promotion", {}).get("rule_docs", ["CLAUDE.md"])
        cls = item.removeprefix("class:")
        written = [doc for doc in docs if (root / doc).is_file()
                   and run.id in (text := (root / doc).read_text(encoding="utf-8", errors="replace"))
                   and "[enforced" in text]
        if not written:
            problems.append(f"The new rule is not written down: name it, marked *[enforced: …]*, with {run.id} "
                            f"in one of {', '.join(docs)}.")
        # Without its [[promotion.rules]] entry the report keeps proposing the class and counting its
        # defects from before the rule (the branch review).
        if not any(rule.get("class") == cls and rule.get("since") for rule in config.raw.get("promotion", {}).get("rules", [])):
            problems.append(f"`[[promotion.rules]]` in factory.toml has no entry for {cls}: add the rule, its "
                            "class and today's date, so the report counts only what comes after it.")
    elif not re.fullmatch(r"Q\d+", item):
        if PLAN_TASK_ITEM.fullmatch(item):
            # A plan's task names the run in its row; the roll-up carries it into the tracker, below.
            problems += task_package_problems(root, config, run)
        tracker = settings.get("tracker", "")
        text = (root / tracker).read_text(encoding="utf-8", errors="replace") if tracker and (root / tracker).is_file() else ""
        if tracker and run.id not in text:
            problems.append(f"{tracker} does not name {run.id}. Update the feature's status row, and link the run.")
    problems += traceability_problems(root, config, run)
    problems += uncommitted_problems(root, config)
    return Outcome(not problems, problems or ["Packaged, and written back to the book."])


def uncommitted_problems(root: Path, config: Config) -> list[str]:
    """
    require_clean as a problem: the package step's write-back must be committed, or the merge gate's
    evidence holds a commit without it (the 15 Oct review's #1). A folder that is not a repository has
    no commit to hold — the section checks' own tests — so there is nothing to ask of it.
    """
    if not (root / ".git").exists():
        return []
    try:
        require_clean(root, config)
    except FactoryError as error:
        return [str(error)]
    return []


def traceability_problems(root: Path, config: Config, run: Run) -> list[str]:
    """Each requirement the audit VERIFIED has its row in the traceability matrix name this run."""
    matrix = config.raw.get("requirements", {}).get("traceability", "")
    verified = [i for i, status in ((run.record.get("audit") or {}).get("rows") or {}).items() if status == "VERIFIED"]
    if not matrix or not verified or not (root / matrix).is_file():
        return []
    rows = {line.split("|")[1].strip(): line for line in (root / matrix).read_text(encoding="utf-8", errors="replace")
            .splitlines() if line.startswith("| ") and line.count("|") > 2}
    # The run's id as a whole word: QA case `USR-052` holds `R-052` (the fourth review).
    named = re.compile(rf"(?<![\w-]){re.escape(run.id)}(?![\w-])")
    return [f"{i}: its row in {matrix} does not name {run.id} — record the code and test evidence the audit "
            f"found, and the run." for i in verified if not named.search(rows.get(i, ""))]


def check_section(run: Run, name: str) -> Outcome:
    if run.section_text(name):
        return Outcome(True)
    return Outcome(False, [f"{name}: the section is empty."])


# ---------------------------------------------------------------- run kinds: plans and the book
#
# A plan run (`plan:<slug>`) writes one feature's plan on demand: it names its sources, drafts the
# plan from the template, and a person approves the plan file itself. A plan's task (`plan:<stem>#<n>`)
# is then an ordinary change run whose package step writes back into that task's row. A book run
# (`book:<name>`) writes project.md from the FSD; its checks are the Phase D modules', and until those
# are beside this file each one says it is not built yet — never a pass.

PLAN_TASKS_HEADING = re.compile(r"^## (?:\d+\.\s+)?Tasks\s*$")
FEATURE_PLAN_TASKS = re.compile(r"^## 9\. Tasks\s*$", re.MULTILINE)
WHAT_THE_FSD_SAYS = re.compile(r"^## 1\. What the FSD says\s*$", re.MULTILINE)
SECTION_CITED = re.compile(r"§\s*(\d+(?:\.\d+)*)")
# What the template leaves for the writer to fill: a plan that still holds one is not written yet.
TEMPLATE_PLACEHOLDER = re.compile(r"<(?:Feature|developer|task|n|features, or \"nothing\")>")
PROPOSED = "[PROPOSED]"
# The status a task of a written plan takes when a run starts it, and the one a new plan's tasks hold.
TASK_IN_PROGRESS, TASK_NOT_STARTED = "In progress", "Not started"
BOOK_FSD_VERSION = r" - V(\d+)\.(docx|md|txt|pdf)$"
# The Inventory's dispositions; `not-a-feature` gives its reason from a closed list.
INVENTORY_LINE = re.compile(r"^\s*-\s*§(?P<id>[A-Za-z0-9.]+?)\.?\s+(?P<rest>.+)$")
DISPOSITIONS = ("feature", "part-of", "cross-cutting", "not-a-feature", "empty")
NOT_A_FEATURE_REASONS = ("introduction", "glossary", "reference", "front-end only", "out of scope", "superseded")


def backlog_settings(config: Config) -> dict:
    return config.raw.get("backlog") or {}


def book_settings(config: Config) -> dict:
    return config.raw.get("book") or {}


def feature_depth(config: Config) -> int:
    """The outline depth a feature row stands for: `[book] feature_depth`, else `[requirements.sources]`'s (the kit's)."""
    sources = config.raw.get("requirements", {}).get("sources") or {}
    return int(book_settings(config).get("feature_depth") or sources.get("feature_depth") or 2)


def our_roles(config: Config) -> list[str]:
    """
    The roles whose tasks this team builds: `[backlog] our_role`, else the roll-up's `[book] our_role` (the kit's) — one
    role, or a list for a team that builds several halves, such as ["BE", "FE"] for a .NET + React project (with
    "BE" alone, its front-end task was labelled BE so the factory would build it).
    """
    value = backlog_settings(config).get("our_role") or book_settings(config).get("our_role") or "BE"
    roles = [str(role) for role in value] if isinstance(value, list) else [str(value)]
    return roles or ["BE"]


def our_role(config: Config) -> str:
    """Our roles as said in a sentence: "BE", or "BE, FE"."""
    return ", ".join(our_roles(config))


def feature_slug(name: str) -> str:
    """A feature's name as a plan item: `Book Loans` → `book-loans`, `Session endpoint — GET /auth/me` →
    `session-endpoint-get-auth-me`. The same words the console offers **Create plan** with."""
    return re.sub(r"[^a-z0-9]+", "-", plain_cell(name).replace("`", "").lower()).strip("-")[:60].rstrip("-")


def plan_path(config: Config, stem: str) -> str:
    return f"{backlog_settings(config).get('plans_dir', 'docs/plans').rstrip('/')}/{stem}.md"


def plan_slug(run: Run) -> str:
    match = PLAN_ITEM.fullmatch(run.record.get("item", ""))
    return match.group("slug") if match else ""


def read_text(root: Path, rel: str) -> str:
    """A book file from the working tree, or "" when it is not there."""
    path = root / rel
    return path.read_text(encoding="utf-8", errors="replace") if rel and path.is_file() else ""


def feature_row(root: Path, config: Config, slug: str) -> dict | None:
    """The tracker's feature row whose name makes this slug — read from the working tree, as a run sees it."""
    tracker = config.raw.get("requirements", {}).get("tracker", "")
    return next((row for row in book_features(read_text(root, tracker), tracker)
                 if feature_slug(row["feature"]) == slug), None)


def sections_cited(text: str) -> list[str]:
    """The FSD section ids a text cites: `§2.7`, `§ 2.5.1` → `2.7`, `2.5.1`."""
    return SECTION_CITED.findall(text)


def inside(section: str, parents: list[str]) -> bool:
    """Whether a section is one of these or below one: 2.7.1 is inside 2.7; 2.70 is not."""
    return any(section == parent or section.startswith(parent + ".") for parent in parents)


def plan_task_rows(text: str) -> list[dict] | None:
    """
    A plan's Tasks table, read as `gen-task-rollup.py` reads it — each row by its column names, plus
    its `line` — or None when the plan has no Tasks heading.
    """
    lines = text.splitlines()
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
            rows.append({**dict(zip(header, cells)), "line": line})
    return rows


def task_status(row: dict) -> str:
    return re.sub(r"[`*]", "", row.get("Status", "")).strip()


def open_run_on(root: Path, config: Config, item: str) -> dict | None:
    """An open run for this item on any branch or worktree — where `factory backlog` looks."""
    runs, _ = open_runs(root, config)
    return next((run for run in runs if run["item"] == item), None)


def plan_item_refusal(root: Path, config: Config, item: str) -> str | None:
    """
    Why a plan, plan-task or book item cannot start a run, or None. Each refusal says what to do
    instead: the console offers these items from the book, and a refusal here is what it shows.
    """
    tracker = config.raw.get("requirements", {}).get("tracker", "")
    plan_item, task_item, book_item = PLAN_ITEM.fullmatch(item), PLAN_TASK_ITEM.fullmatch(item), BOOK_ITEM.fullmatch(item)
    if plan_item:
        if not tracker:
            return "factory.toml names no [requirements] tracker, so a plan run has no feature row to start from."
        slug = plan_item.group("slug")
        row = feature_row(root, config, slug)
        if row is None:
            return (f"{item} names no feature in {tracker}: the slug is a feature row's name in lower case, words "
                    "joined by dashes (`Book Loans` → `plan:book-loans`). Add the feature's row first.")
        existing = row["plan"] or (plan_path(config, slug) if (root / plan_path(config, slug)).is_file() else "")
        if existing:
            return (f"{row['feature']} already has a plan, {existing}. Implement its tasks instead: "
                    f"`factory start plan:{Path(existing).stem}#<n>`.")
        if row["status"].lower().startswith("blocked"):
            return f"{row['feature']} is Blocked — {row['note'] or 'its row says why'}. Settle what blocks it first."
    elif task_item:
        stem, number = task_item.group("stem"), task_item.group("task")
        rel = plan_path(config, stem)
        text = read_text(root, rel)
        if not text:
            return f"{item}: there is no plan {rel}. Write it first with `factory start plan:<feature-slug>`."
        rows = plan_task_rows(text)
        row = next((r for r in rows or [] if r.get("#", "").strip() == number), None)
        if row is None:
            return f"{item}: {rel} has no task {number} in its Tasks table."
        status = task_status(row)
        if status in ("Built", "Blocked"):
            return (f"{item}: task {number} is {status}" + (" — merged into dev already." if status == "Built" else
                    " — settle what blocks it first, and set its Status in the plan."))
        role = re.sub(r"[`*]", "", row.get("Role", "")).strip()
        if role not in our_roles(config):
            return (f"{item}: task {number} is {role or 'no one'}'s, not ours ({our_role(config)}). "
                    "A run builds only our tasks.")
    elif REVISION_ITEM.fullmatch(item):
        refused = revision_refusal(root, config, item)
        if refused:
            return refused
    elif not book_item:
        return None
    running = open_run_on(root, config, item)
    if running:
        return f"{running['id']} is already open for {item}, on {running['branch']} at {running['step']}. Continue that run."
    return None


def item_notes(root: Path, config: Config, item: str) -> list[str]:
    """What a person should know when the run starts, without refusing it."""
    task_item = PLAN_TASK_ITEM.fullmatch(item)
    if not task_item:
        return []
    rel = plan_path(config, task_item.group("stem"))
    base = config.raw["base_ref"]
    code, _ = git(root, "cat-file", "-e", f"{base}:{rel}")
    if code != 0:
        return [f"⚠ {rel} is not on {base} yet: the task is built from a plan nobody has merged."]
    return []


def item_brief(root: Path, config: Config, run: Run) -> str:
    """
    The referee's own words for the item, given to the session as `{brief}`: the feature's row for a
    plan run, the task's row and the plan's §1 lines for its sections for a plan task, the FSD for a
    book. "" for any other item — its prompts read the item themselves, as before kinds.
    """
    item = run.record.get("item", "")
    tracker = config.raw.get("requirements", {}).get("tracker", "")
    kind = record_kind(run.record)
    task_item = PLAN_TASK_ITEM.fullmatch(item)
    if kind == PLAN:
        slug = plan_slug(run)
        row = feature_row(root, config, slug)
        if row is None:
            return f"`{item}` — its feature row is no longer in {tracker}; say so in the Escalation section."
        template = backlog_settings(config).get("template", "docs/plans/_template.md")
        return "\n".join([
            f"`{item}` — the feature **{row['feature']}** in {tracker}" + (f" ({row['phase']})." if row["phase"] else "."),
            f"- FSD: {row['fsd'] or '—'}", f"- Depends on: {row['depends_on'] or 'nothing'}",
            f"- Owner: {row['owner'] or '—'}", f"- Status: {row['status'] or '—'}",
            f"Write the plan at `{plan_path(config, slug)}`, from `{template}`."])
    if task_item:
        rel = plan_path(config, task_item.group("stem"))
        text = read_text(root, rel)
        row = next((r for r in plan_task_rows(text) or [] if r.get("#", "").strip() == task_item.group("task")), None)
        if row is None:
            return f"`{item}` — the task is no longer in {rel}; say so in the Escalation section."
        cited = sections_cited(row.get("FSD", ""))
        what = WHAT_THE_FSD_SAYS.search(text)
        first = text[what.end():].split("\n## ", 1)[0] if what else ""
        lines = [line for line in first.splitlines() if any(inside(s, cited) for s in sections_cited(line))]
        return "\n".join([f"`{item}` — task {task_item.group('task')} of `{rel}`:", "", row["line"].strip(), "",
                          f"What the plan's §1 says for {', '.join('§' + s for s in cited) or 'it'}:",
                          *(lines or ["(no line of §1 cites it — read §1 whole)"]), "",
                          f"At package: end the task's Task cell with ` ({run.id})`, set its Status to "
                          f"`{TASK_IN_PROGRESS}`, and regenerate the roll-up "
                          f"(`{backlog_settings(config).get('plan_check', '').replace(' --check', '')}`)."])
    if kind == BOOK:
        fsd = book_fsd(root, config)
        settings = book_settings(config)
        text = (run.record.get("outline") or {}).get("text")
        return "\n".join([f"`{item}` — the project's backlog, `{tracker}`, from its FSD.",
                          f"- FSD: `{fsd.relative_to(root).as_posix() if fsd else 'none saved yet'}`",
                          f"- Each section's text, as the referee read it: `{text}` — read the FSD there"
                          if text else "- Each section's text: not read yet (the outline step writes it)",
                          f"- Feature depth: {feature_depth(config)}",
                          f"- Phase 0 only from: {', '.join(settings.get('foundation', [])) or '—'}"])
    if kind == REVISION:
        changes = run.record.get("changes") or {}
        logs = config.raw.get("requirements", {}).get("defect_logs", [])
        return "\n".join([
            f"`{item}` — FSD V{changes.get('version', revision_version(run))} taken into the book, `{tracker}`.",
            f"- New version: `{changes.get('new', '—')}`; its text, section by section, as the referee read it: "
            f"`{changes.get('text', '—')}` — quote from there",
            f"- The version before it: `{changes.get('old', '—')}` (`{changes.get('previous_text', '—')}`)",
            f"- Questions: {', '.join(decisions_docs(config))}",
            f"- System overview: `{overview_path(config)}`",
            f"- Plans: `{backlog_settings(config).get('plans_dir', 'docs/plans')}/`",
            f"- Defect log: {', '.join(logs) or '—'}",
            f"- What the steps may write: {', '.join(revision_writes(config)) or 'nothing'} — never code"])
    return ""


def check_sources(root: Path, config: Config, run: Run) -> Outcome:
    """
    A plan run names what its plan is written from before it writes it — the Spec's source rules,
    without criteria: the newest FSD and the sections read (inside the feature's own), the decisions
    it relies on, what the live Doc said. A stale Doc stops it as it stops a spec.
    """
    sources = run.section_text("Sources")
    if not sources:
        return Outcome(False, ["Sources: the section is empty. Name the FSD revision and the sections read "
                               "(**FSD:** V<n> §…), the decisions (**Decisions:**) and the live check (**Live check:**)."])
    problems = spec_source_problems(root, config, run, "Sources")
    line = spec_line(sources, "FSD")
    row = feature_row(root, config, plan_slug(run))
    feature = sections_cited(row["fsd"]) if row else []
    cited = sections_cited(line or "")
    if line is None:
        if not any("**FSD:**" in problem for problem in problems):
            problems.append("Sources: add **FSD:** — the revision and the sections read (`V<n> §…`).")
    elif line.lower().startswith("none"):
        if feature:
            problems.append(f"Sources: the feature's row cites {', '.join('§' + s for s in feature)} — name the "
                            "revision and the sections of it read, not none.")
    elif not cited:
        problems.append("Sources: **FSD:** names the sections read, as `§2.7.1` — the plan's §1 cites them.")
    problems += [f"Sources: §{section} is outside the feature's sections ({', '.join('§' + s for s in feature)}) — "
                 "a plan covers its own feature; another's section is a dependency, named in the plan."
                 for section in cited if feature and not inside(section, feature)]
    return Outcome(not problems, problems)


def plan_check_command(root: Path, config: Config, run: Run, step: str) -> list[str]:
    """`[backlog] plan_check` — the roll-up's own check of every plan's shape — run as evidence."""
    command = backlog_settings(config).get("plan_check", "")
    if not command:
        return []
    log = evidence_path(root, config, run, f"{step}-plan-check.log")
    execution = execute(root, to_argv(command), log)
    record_check(root, run, step, "plan-check", execution.exit == 0, execution, log)
    if execution.exit == 0:
        return []
    said = [line.strip() for line in execution.output.splitlines() if line.strip()][:5]
    return [f"`{command}` failed (exit {execution.exit}): " + (" · ".join(said) or "no output") +
            f" — fix the plan, or regenerate the roll-up with `{command.replace(' --check', '')}`."]


def check_plan_doc(root: Path, config: Config, run: Run) -> Outcome:
    """
    The plan the run wrote is there, in the template's shape, and tied to the book: the Design links
    it; its status line names the Owner, the feature's FSD section and the newest revision; §1 cites
    a section of the feature's; §9 Tasks holds the work, all Not started, ours each citing the FSD;
    the roll-up's own check passes; the feature row links it; and nothing is [PROPOSED] without an
    open question deciding it.
    """
    slug = plan_slug(run)
    rel = plan_path(config, slug)
    tracker = config.raw.get("requirements", {}).get("tracker", "")
    template = backlog_settings(config).get("template", "docs/plans/_template.md")
    problems = []
    linked = re.search(r"\*\*Plan:\*\*\s*\[[^\]]*\]\(([^)]+)\)", run.section_text("Design"))
    link = posixpath.relpath(rel, config.runs_dir)
    if not linked:
        problems.append(f"Design: link the plan this run writes — **Plan:** [{slug}]({link}).")
    elif run.path and (run.path.parent / linked.group(1)).resolve() != (root / rel).resolve():
        problems.append(f"Design: it links {linked.group(1)}; this run writes {rel} — **Plan:** [{slug}]({link}).")
    text = read_text(root, rel)
    if not text:
        return Outcome(False, problems + [f"{rel} does not exist. Copy {template} to it and fill §1–§9."])
    row = feature_row(root, config, slug)
    feature = sections_cited(row["fsd"]) if row else []
    status = next((line for line in text.splitlines() if line.startswith("**Status:**")), "")
    if not status:
        problems.append(f"{rel}: the status line is missing — **Status:** `Not started` · **Owner:** … · "
                        "**FSD:** §… · **Checked against:** FSD V<n>, as the template has it.")
    else:
        owner = re.search(r"\*\*Owner:\*\*\s*([^·]*)", status)
        if not owner or not owner.group(1).strip() or owner.group(1).strip().startswith("<"):
            problems.append(f"{rel}: the status line names no **Owner:** — the feature row's owner, or the person "
                            "who will build it.")
        fsd_field = re.search(r"\*\*FSD:\*\*\s*([^·]*)", status)
        own = sections_cited(fsd_field.group(1)) if fsd_field else []
        if not own:
            problems.append(f"{rel}: the status line's **FSD:** cites no section (`§2.7`).")
        problems += [f"{rel}: the status line cites §{s}, outside the feature's sections "
                     f"({', '.join('§' + f for f in feature)})." for s in own if feature and not inside(s, feature)]
        sources = config.raw.get("requirements", {}).get("sources") or {}
        newest = newest_version(root, sources.get("fsd_dir", ""), sources.get("version", r" - V(\d+)\.pdf$"))
        checked = re.search(r"\*\*Checked against:\*\*\s*FSD V(\d+)", status)
        if newest is not None and (not checked or int(checked.group(1)) != newest):
            problems.append(f"{rel}: the status line says **Checked against:** "
                            f"{'FSD V' + checked.group(1) if checked else 'nothing'}; the newest snapshot is V{newest}.")
    if TEMPLATE_PLACEHOLDER.search(text):
        problems.append(f"{rel}: it still holds the template's `{TEMPLATE_PLACEHOLDER.search(text).group(0)}` — fill it in.")
    what = WHAT_THE_FSD_SAYS.search(text)
    first = text[what.end():].split("\n## ", 1)[0] if what else ""
    if not what:
        problems.append(f"{rel}: `## 1. What the FSD says` is missing.")
    elif not any(inside(s, feature) if feature else True for s in sections_cited(first)):
        problems.append(f"{rel}: §1 cites no section of the feature's ({', '.join('§' + f for f in feature) or 'none'}).")
    rows = plan_task_rows(text)
    if not FEATURE_PLAN_TASKS.search(text):
        problems.append(f"{rel}: `## 9. Tasks` is missing — a feature plan keeps its tasks as §9 (the template).")
    elif not rows:
        problems.append(f"{rel}: §9 Tasks has no task rows.")
    ours = our_roles(config)
    for task in rows or []:
        number, state = task.get("#", "?").strip(), task_status(task)
        if state != TASK_NOT_STARTED:
            problems.append(f"{rel}: task {number} is {state or 'blank'}; a new plan's tasks are all "
                            f"`{TASK_NOT_STARTED}` — Built means merged into dev.")
        role = re.sub(r"[`*]", "", task.get("Role", "")).strip()
        if role in ours and not sections_cited(task.get("FSD", "")):
            problems.append(f"{rel}: task {number} is ours ({role}) and cites no FSD section — every task we build "
                            "traces to the FSD.")
    if row is None:
        problems.append(f"{tracker}: no feature row makes the slug {slug} any more.")
    elif row["plan"] != rel:
        problems.append(f"{tracker}: the {row['feature']} row's Plan cell does not link {rel} — "
                        f"[`{slug}.md`]({posixpath.relpath(rel, posixpath.dirname(tracker))}).")
    problems += proposed_problems(root, config, rel, text)
    step = current_step(run.record, config)
    problems += plan_check_command(root, config, run, step.name if step else "draft-plan")
    return Outcome(not problems, problems or [f"{rel} is written, in the template's shape, and linked from {tracker}."])


def proposed_problems(root: Path, config: Config, rel: str, text: str) -> list[str]:
    """A [PROPOSED] line is a guess until a question decides it: each names a Q id that is really open."""
    decisions = (config.raw.get("requirements", {}).get("sources") or {}).get("decisions", ["docs/open-questions.md"])
    texts = [read_text(root, doc) for doc in decisions]
    answered = set().union(set(), *(answered_questions(t) for t in texts if t))
    problems = []
    for number, line in enumerate(text.splitlines(), 1):
        if PROPOSED not in line:
            continue
        named = re.findall(r"\bQ\d+\b", line)
        open_ones = [q for q in named if q not in answered and any(re.search(rf"\b{q}\b", t) for t in texts)]
        if not open_ones:
            problems.append(f"{rel}:{number}: {PROPOSED} names no open question — raise it in "
                            f"{decisions[0]} with a recommendation, and name its Q id on the line.")
    return problems


def plan_package_problems(root: Path, config: Config, run: Run) -> list[str]:
    """A plan run is written back as its feature's row — the Plan cell links the plan, the Status names
    the run — and committed."""
    tracker = config.raw.get("requirements", {}).get("tracker", "")
    slug = plan_slug(run)
    rel = plan_path(config, slug)
    row = feature_row(root, config, slug)
    if row is None:
        return [f"{tracker}: no feature row makes the slug {slug} any more."]
    # The commit package checks is the merge gate's evidence: what the run wrote must be in it.
    dirty = dirty_paths(root, config.runs_dir)
    problems = [f"Commit first — these are not committed: {', '.join(dirty[:8])}"] if dirty else []
    if row["plan"] != rel:
        problems.append(f"{tracker}: the {row['feature']} row's Plan cell does not link {rel}.")
    if run.id not in row["runs"]:
        problems.append(f"{tracker}: the {row['feature']} row's Status does not name {run.id} — add "
                        f"\"plan by [{run.id}]({posixpath.relpath(f'{config.runs_dir}/{run.id}.md', posixpath.dirname(tracker))})\".")
    return problems


def task_package_problems(root: Path, config: Config, run: Run) -> list[str]:
    """A plan task's run is written back as its task row: the Task cell names the run, the Status is
    In progress, and the roll-up in the tracker is current (it copies the cell there)."""
    found = PLAN_TASK_ITEM.fullmatch(run.record.get("item", ""))
    assert found is not None
    rel, number = plan_path(config, found.group("stem")), found.group("task")
    row = next((r for r in plan_task_rows(read_text(root, rel)) or [] if r.get("#", "").strip() == number), None)
    if row is None:
        return [f"{rel} has no task {number} any more."]
    problems = []
    if run.id not in RUN_ID.findall(row.get("Task", "")):
        problems.append(f"{rel}: task {number}'s Task cell does not name {run.id} — end it with ` ({run.id})`, as a "
                        "defect's id is.")
    if task_status(row) != TASK_IN_PROGRESS:
        problems.append(f"{rel}: task {number} is {task_status(row) or 'blank'}; at package it is "
                        f"`{TASK_IN_PROGRESS}` — Built is set when it merges into dev.")
    return problems + plan_check_command(root, config, run, "package")


# The Phase D modules each book check needs. They live beside this file once built (`fsd_outline.py`,
# `check_project_md.py`); until then the check says so and never passes.
BOOK_CHECK_NEEDS = {"fsd-outline": ("fsd_outline",), "fsd-coverage": ("fsd_outline",),
                    "project-md": ("fsd_outline", "check_project_md"), "book-audit": ("fsd_outline",),
                    "fsd-changes": ("fsd_outline",), "revision-triage": ("fsd_outline",)}


def phase_d_module(name: str):
    """A Phase D module, or None while it is not built."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def not_built(root: Path, config: Config, step: Step) -> str | None:
    """What a book step's check still lacks — said in place of a verdict, so it can never pass on nothing."""
    needs = BOOK_CHECK_NEEDS.get(step.check or "")
    if needs is None:
        return None
    missing = [f"devtools/factory/{name}.py" for name in needs if phase_d_module(name) is None]
    if step.check == "book-audit":
        agent = (book_settings(config).get("audit") or {}).get("agent", "book-auditor")
        if not (root / ".claude" / "agents" / f"{agent}.md").is_file():
            missing.append(f".claude/agents/{agent}.md")
    if not missing:
        return None
    return (f"{step.name}: not built yet — the {step.check} check needs {', '.join(missing)} (console Phase D). "
            "The step cannot pass until they are there; a person decides whether the run waits or is abandoned.")


def book_fsd(root: Path, config: Config) -> Path | None:
    """The newest FSD snapshot a book is written from; a .docx, .md or .txt beside a PDF of the same V<n> wins."""
    folder = (config.raw.get("requirements", {}).get("sources") or {}).get("fsd_dir", "requirements/fsd")
    pattern = book_settings(config).get("fsd_version", BOOK_FSD_VERSION)
    found = [((int(m.group(1)), m.group(2).lower() != "pdf"), path) for path in (root / folder).glob("*")
             if (m := re.search(pattern, path.name))] if (root / folder).is_dir() else []
    return max(found)[1] if found else None


def book_units(root: Path, config: Config, run: Run) -> list[dict]:
    """The outline the outline step read, read again from the same file."""
    module = phase_d_module("fsd_outline")
    rel = (run.record.get("outline") or {}).get("file", "")
    if module is None or not rel or not (root / rel).is_file():
        raise FactoryError("The outline step has not read an FSD yet: `factory reopen outline --reason \"…\"`.")
    return module.outline(root / rel)


def check_fsd_outline(root: Path, config: Config, run: Run) -> Outcome:
    """
    The referee reads the FSD's outline itself, from the document's own headings, and writes it as
    the Outline section: every section once, numbered in order, each with a title. A PDF it cannot
    read — that outline is the AI's, confirmed by a person at the outline gate (not built yet).
    """
    fsd = book_fsd(root, config)
    folder = (config.raw.get("requirements", {}).get("sources") or {}).get("fsd_dir", "requirements/fsd")
    if fsd is None:
        return Outcome(False, [f"Outline: no FSD snapshot in {folder} — save it as `<Name> - V1.docx` (or .md, .txt)."])
    rel = fsd.relative_to(root).as_posix()
    form = fsd.suffix.lstrip(".").lower()
    run.record["outline"] = {"file": rel, "format": form, "sha256": sha256_file(fsd), "at": utc_now()}
    if form == "pdf":
        return Outcome(False, [f"Outline: {rel} is a PDF, which the referee cannot outline itself; the AI-read outline "
                               "and its gate are not built yet. Save a .docx export beside it as the "
                               "same V<n>."])
    try:
        units = phase_d_module("fsd_outline").outline(fsd)
    except (zipfile.BadZipFile, ElementTree.ParseError, UnicodeDecodeError, KeyError) as error:
        return Outcome(False, [f"Outline: {rel} cannot be read as a {form} file ({error}) — export it again."])
    problems = [] if units else [f"Outline: {rel} has no headings the referee can read."]
    ids = [unit["id"] for unit in units]
    problems += [f"Outline: §{i} is numbered twice — a citation to it would be ambiguous." for i in sorted({i for i in ids if ids.count(i) > 1})]
    problems += [f"Outline: §{unit['id']} has no title." for unit in units if not unit["title"].strip()]
    numbered = [(tuple(int(p) for p in unit["id"].split(".")), unit["id"]) for unit in units
                if re.fullmatch(r"\d+(?:\.\d+)*", unit["id"])]
    problems += [f"Outline: §{after[1]} comes after §{before[1]} — the numbering is out of order."
                 for before, after in zip(numbered, numbered[1:]) if after[0] <= before[0]]
    run.record["outline"]["units"] = len(units)
    # Each section's own text, as the referee read it: a .docx is a zip the sessions and the book
    # auditor cannot read, and the quotes project.md makes are checked against exactly this text.
    text_file = fsd_text_path(root, config, run)
    text_file.parent.mkdir(parents=True, exist_ok=True)
    text_file.write_text(render_fsd_text(rel, units), encoding="utf-8", newline="\n")
    run.record["outline"]["text"] = text_file.relative_to(root).as_posix()
    run.sections["Outline"] = (f"*Written by the referee from `{rel}`: {len(units)} sections. Each section's own "
                               f"text, as the referee read it: `{run.record['outline']['text']}`.*\n\n" + "\n".join(
        "  " * (unit["depth"] - 1) + f"- §{unit['id']} {unit['title']}" + (" *(empty)*" if unit["empty"] else "")
        for unit in units))
    stamp_section(run, "Outline", "outline")
    return Outcome(not problems, problems or [f"Outline: {len(units)} sections from {rel}."])


def fsd_text_path(root: Path, config: Config, run: Run) -> Path:
    """Where the outline step leaves the FSD's text, section by section: the run's evidence, never committed."""
    return evidence_path(root, config, run, "fsd-text.md")


def render_fsd_text(rel: str, units: list[dict]) -> str:
    """The FSD as the referee read it: every section's id, title and own text, in the outline's order."""
    parts = [f"# The FSD's text, section by section\n\n*Read by the referee from `{rel}`. A quote in project.md "
             "is checked against this text, word for word.*"]
    for unit in units:
        parts.append(f"{'#' * min(unit['depth'] + 1, 6)} §{unit['id']} {unit['title']}\n\n"
                     + (unit["text"].strip() or "*(no text)*") + (" *(empty)*" if unit["empty"] and unit["text"].strip() else ""))
    return "\n\n".join(parts) + "\n"


def feature_units(units: list[dict], depth: int) -> list[dict]:
    """The sections a backlog accounts for: those at the feature depth, and shallower ones with none below."""
    return [unit for unit in units if unit["depth"] == depth or (unit["depth"] < depth and not any(
        other["id"].startswith(unit["id"] + ".") for other in units))]


def check_fsd_coverage(root: Path, config: Config, run: Run) -> Outcome:
    """
    Every FSD section at the feature depth is in the Inventory once, with one disposition: a feature,
    part of a feature the Inventory names, cross-cutting, not a feature for a reason from the closed
    list, or empty and turned into a question that exists. Nothing outside the outline.
    """
    settings = book_settings(config)
    units = book_units(root, config, run)
    known = {unit["id"]: unit for unit in units}
    reasons = tuple(settings.get("not_a_feature_reasons", NOT_A_FEATURE_REASONS))
    decisions = (config.raw.get("requirements", {}).get("sources") or {}).get("decisions", ["docs/open-questions.md"])
    questions = set(re.findall(r"\bQ\d+\b", "\n".join(read_text(root, doc) for doc in decisions)))
    entries: list[tuple[str, str, str]] = []  # id, disposition, what follows it
    problems = []
    for line in run.section_text("Inventory").splitlines():
        match = INVENTORY_LINE.match(line)
        if not match:
            continue
        title, _, said = match.group("rest").rpartition(" — ")
        word = said.split(" ", 1)[0].strip().lower() if title else ""
        if word not in DISPOSITIONS:
            problems.append(f"Inventory: §{match.group('id')} — say `feature`, `part-of <feature>`, `cross-cutting`, "
                            "`not-a-feature (<reason>)` or `empty → Q<n>` after ` — `.")
            continue
        entries.append((match.group("id"), word, said[len(word):].strip()))
    features = {known[i]["title"].lower() for i, word, _ in entries if word == "feature" and i in known}
    listed = [i for i, _, _ in entries]
    problems += [f"Inventory: §{i} is not in the FSD's outline." for i in dict.fromkeys(listed) if i not in known]
    for unit in feature_units(units, feature_depth(config)):
        count = listed.count(unit["id"])
        if count != 1:
            problems.append(f"Inventory: §{unit['id']} {unit['title']} is " + ("missing." if not count else f"listed {count} times."))
    for i, word, rest in entries:
        if word == "part-of" and rest.lower() not in features:
            problems.append(f"Inventory: §{i} is part of {rest or 'nothing named'}, which no `feature` line names.")
        if word == "not-a-feature" and rest.strip("() ").lower() not in reasons:
            problems.append(f"Inventory: §{i} is not a feature for {rest or 'no reason'} — the reason is one of "
                            f"{', '.join(reasons)}.")
        named = [q for q in re.findall(r"\bQ\d+\b", rest) if q in questions]
        if word == "empty" and not named:
            problems.append(f"Inventory: §{i} is empty and names no question in {', '.join(decisions)} — raise one, "
                            "closed, with a recommendation, and name its Q id.")
        elif word == "empty":
            # The recommendation is what gets built unless the answer says otherwise: a question without one asks
            # the client to design the feature (the kit's open-questions rule).
            texts = [read_text(root, doc) for doc in decisions]
            problems += [f"Inventory: §{i} is empty and its question {q} has no recommendation in {', '.join(decisions)} — "
                         "add what we would build, and why." for q in named if not question_has_recommendation(texts, q)]
    return Outcome(not problems, problems or [f"Inventory: {len(entries)} sections, each accounted for once."])


def project_md_problems(root: Path, config: Config, run: Run, step: str) -> list[str]:
    """project.md against the outline — Phase D's seven rules (shape, citations, coverage, honesty, order,
    dates, questions), the referee's alone — then the book's own checks."""
    settings = book_settings(config)
    module = phase_d_module("check_project_md")
    tracker = config.raw.get("requirements", {}).get("tracker", "docs/project.md")
    text = read_text(root, tracker)
    if not text:
        return [f"{tracker} does not exist yet — write it from the Inventory."]
    decisions = (config.raw.get("requirements", {}).get("sources") or {}).get("decisions", ["docs/open-questions.md"])
    found = module.problems(text, book_units(root, config, run),
                            foundation=tuple(settings.get("foundation", module.FOUNDATION)),
                            feature_depth=feature_depth(config),
                            questions=set(re.findall(r"\bQ\d+\b", "\n".join(read_text(root, doc) for doc in decisions))),
                            questions_source=", ".join(decisions))
    return ([f"{tracker}: {problem}" for problem in found] + overview_problems(root, config)
            + book_command_problems(root, config, run, step))


def check_book_project_md(root: Path, config: Config, run: Run) -> Outcome:
    step = current_step(run.record, config)
    problems = project_md_problems(root, config, run, step.name if step else "features")
    return Outcome(not problems, problems or ["project.md cites, covers and orders the FSD's sections."])


def book_command_problems(root: Path, config: Config, run: Run, step: str) -> list[str]:
    """`[book] checks`: the roll-up's and the book's own checks, run as evidence."""
    problems = []
    for index, command in enumerate(book_settings(config).get("checks", []), 1):
        log = evidence_path(root, config, run, f"{step}-book-check-{index}.log")
        execution = execute(root, to_argv(command), log)
        record_check(root, run, step, f"book-check-{index}", execution.exit == 0, execution, log)
        if execution.exit != 0:
            said = [line.strip() for line in execution.output.splitlines() if line.strip()][:3]
            problems.append(f"`{command}` failed (exit {execution.exit}): " + (" · ".join(said) or "no output"))
    return problems


def audited_step(run: Run) -> str:
    """The step whose work the audit reads, and a NOT READY reopens: the book's features, a revision's triage."""
    return "triage" if record_kind(run.record) == REVISION else "features"


# The run file's sections a revision's triage writes, read by the audit beside the book.
TRIAGE_SECTIONS = ("Changes", "Triage", "Answered", "New questions", "Flagged plans", "Change items")


def revision_audit_prompt(root: Path, config: Config, run: Run) -> str:
    """What the book auditor is asked about a revision: the triage against both versions of the FSD."""
    changes = run.record.get("changes") or {}
    tracker = config.raw.get("requirements", {}).get("tracker", "docs/project.md")
    return (f"Audit the triage of revision run {run.id}: FSD V{changes.get('previous', '?')} → V{changes.get('version', '?')}, "
            f"taken into a book already written. The run file is {run.path.relative_to(root).as_posix() if run.path else ''}: "
            "its Changes section is every section the new version added, removed or changed, read by the referee itself; "
            "its Triage, Answered, New questions, Flagged plans and Change items sections are the triage you check. "
            f"The new version's text, section by section, is `{changes.get('text', '—')}`; the one before it, "
            f"`{changes.get('previous_text', '—')}` — read the FSD there. The book: {tracker}, the questions in "
            f"{', '.join(decisions_docs(config))}, the system overview `{overview_path(config)}`, the plans in "
            f"`{backlog_settings(config).get('plans_dir', 'docs/plans')}/`. This is a revision, not a new backlog. Look for: "
            "a change whose disposition misreads it (a new feature with no row, a changed rule not linked to the feature it "
            "changes, a removed section whose row is not flagged); a question moved to Resolved that the quoted words do not "
            "really answer; a gap, a contradiction or an unclear rule in the new version with no question raised; a question "
            "that is not closed or has no recommendation; a plan with work started that was rewritten rather than flagged; a "
            "built feature whose requirement changed with no change item; a system overview the new version now contradicts. "
            "Tag each finding [disposition], [answered], [question], [plan], [built] or [overview]. Report in your fixed "
            "shape, ending with `Verdict: READY` or `Verdict: NOT READY`.")


def check_book_audit(root: Path, config: Config, run: Run) -> Outcome:
    """
    The book auditor, a read-only agent in a session of its own, reads project.md against the FSD — or, in a
    revision run, the triage against both versions of it.
    """
    settings = {key: value for key, value in (book_settings(config).get("audit") or {}).items() if key != "max_retries"}
    tracker = config.raw.get("requirements", {}).get("tracker", "docs/project.md")
    decisions = (config.raw.get("requirements", {}).get("sources") or {}).get("decisions", ["docs/open-questions.md"])
    outline = run.record.get("outline") or {}
    revision = record_kind(run.record) == REVISION
    written = TRIAGE_SECTIONS if revision else ("Outline", "Inventory", "Features")
    # A revision's triage reaches the overview as well (its plans and defect rows are in the commit, which the
    # triage check asks for).
    read_also = [overview_path(config)] if revision else []
    prompt = revision_audit_prompt(root, config, run) if revision else (f"Audit {tracker} against the FSD for book run {run.id}. The run file is "
              f"{run.path.relative_to(root).as_posix() if run.path else ''}: its Outline section is every FSD section, "
              "read by the referee from the document's headings, and its Inventory section is each section's "
              f"disposition. The FSD is `{outline.get('file', '—')}`; each section's own text, as the referee read it, "
              f"is `{outline.get('text', '—')}` — read the FSD there. Questions are in {', '.join(decisions)}. "
              f"Feature depth {feature_depth(config)}; Phase 0 only from: "
              f"{', '.join(book_settings(config).get('foundation', [])) or '—'}. Look for invented rows, missed or "
              "misread sections, empty sections that did not become questions, dependencies the FSD does not "
              "support, and phase orders that force rework. Report in your fixed shape, ending with "
              "`Verdict: READY` or `Verdict: NOT READY`.")
    agent = settings.get("agent", "book-auditor")
    # Judges are asked once per commit: the same book, read again, is the same verdict — asking again
    # would only roll the dice. The book is more than the commit: project.md and the questions may be
    # uncommitted, the run's sections are what the auditor reads beside them, and a rewrite of the
    # features step (a revision's triage) is a new answer to audit even when it wrote the same file.
    basis = sha256_text("\n".join(
        [head(root), read_text(root, tracker), *(read_text(root, doc) for doc in [*decisions, *read_also]),
         *(run.section_text(name) for name in written),
         str(((run.record.get("steps") or {}).get(audited_step(run)) or {}).get("at") or "")]))
    previous = run.record.get("book_audit") or {}
    if previous.get("verdict") and previous.get("basis") == basis:
        said = f" Nothing has changed since that audit at `{head(root)[:10]}`; change the book to be audited again."
        if previous["verdict"] != "READY":
            return Outcome(False, [book_audit_failed(config, previous["verdict"], audited_step(run)) + said])
        return Outcome(True, ["Book auditor: READY." + said])
    text, usd, tokens = agent_turn(root, config, {"agent": "book-auditor", "max_budget_usd": 3,
                                                  "allowed_tools": ["Read", "Grep", "Glob"], **settings}, prompt)
    add_agent_cost(run, agent, usd, tokens)
    verdict = one_verdict(VERDICT, text)
    keep_judgement(run, agent, verdict, head(root), text)
    run.sections["Audit"] = f"*Written by the referee from the book auditor's report.*\n\n{text.strip()}"
    run.record["book_audit"] = {"verdict": verdict, "at": utc_now(), "head": head(root), "basis": basis,
                                "sha256": sha256_text(text), "findings": len(audit_findings(text))}
    stamp_section(run, "Audit", "book_audit")
    if verdict != "READY":
        return Outcome(False, [book_audit_failed(config, verdict, audited_step(run))])
    return Outcome(True, ["Book auditor: READY."])


def book_audit_failed(config: Config, verdict: str | None, step: str = "features") -> str:
    return (f"Book auditor: {verdict or 'no single verdict'} — read the Audit section. `factory drive` "
            f"reopens the {step} step to answer it, at most {book_audit_retries(config)} times, then "
            f"escalates to a person; by hand: `factory reopen {step} --reason \"…\"`.")


# The audit's findings, one per line, as its agent's fixed shape writes them (.claude/agents/book-auditor.md);
# a revision's audit tags its own (disposition, answered, plan, built, overview).
AUDIT_FINDING = re.compile(r"^\s*-\s*\[(invented|missed|misread|citation|dependency|phase|question|shape|disposition|"
                           r"answered|plan|built|overview)\]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
BOOK_AUDIT_RETRIES = 2


def audit_findings(text: str) -> list[str]:
    return [f"[{m.group(1).lower()}] {m.group(2).strip()}" for m in AUDIT_FINDING.finditer(text)]


def book_audit_retries(config: Config) -> int:
    """How many times a NOT READY audit reopens features before a person decides (design §3.2: two)."""
    return int((book_settings(config).get("audit") or {}).get("max_retries", BOOK_AUDIT_RETRIES))


def answer_book_audit(root: Path, config: Config, run: Run, step: Step) -> tuple[str, bool] | None:
    """
    After a failed audit in a drive: what happens next, as (what to say, whether the drive stops).
    NOT READY reopens the features step with the findings as the reason — the fresh session reads them —
    at most `[book.audit] max_retries` times; after that the referee writes an Escalation and a person
    decides. No single verdict is not something a rewrite answers: the drive stops. None for another step.
    """
    if step.check != "book-audit":
        return None
    verdict = (run.record.get("book_audit") or {}).get("verdict")
    if verdict != "NOT READY":
        return (f"{run.id} stops at {step.name}: the book auditor gave no single verdict. A person reads the Audit "
                "section and decides — `factory check` asks the auditor again.", True)
    findings = audit_findings(run.section_text("Audit")) or ["(the report names none in its fixed shape — read the Audit section)"]
    used = sum(1 for entry in run.record.get("reopened") or [] if entry.get("by") == "book-audit")
    allowed = book_audit_retries(config)
    target = audited_step(run)
    # What the rewrite writes: a book's project.md; a revision's triage — the book's files and the run's sections.
    what, where = (("the triage", "the book's files and the run's triage sections") if target == "triage"
                   else ("project.md", "docs/project.md"))
    if used >= allowed:
        run.sections["Escalation"] = (
            f"*Written by the referee.* The book auditor said NOT READY {used + 1} times; the {target} step was "
            f"rewritten {used} time(s) to answer it, the most `[book.audit] max_retries` allows. A person decides: "
            f"correct {where} by hand and `factory reopen {target} --reason \"…\"`, or reopen with what the "
            "rewrite must do differently, or abandon the run.\n\nThe last audit's findings:\n"
            + "\n".join(f"- {finding}" for finding in findings))
        write_run(root, config, run)
        return (f"{run.id} escalated at {step.name}: the book auditor said NOT READY after {used} rewrite(s) of "
                f"{what} — a person reads the Escalation section and decides.", True)
    reason = (f"The book auditor said NOT READY (rewrite {used + 1} of {allowed}). Answer every finding in "
              f"{where} — or, where a finding is wrong, say why in the {target.capitalize()} section, quoting the FSD: "
              + " · ".join(findings))
    reopen_steps(run, config, target, reason, by="book-audit")
    write_run(root, config, run)
    return (f"{run.id} · {step.name} · NOT READY — {target} reopened to answer {len(findings)} finding(s) "
            f"(rewrite {used + 1} of {allowed})", False)


def book_package_problems(root: Path, config: Config, run: Run) -> list[str]:
    """A book run is packaged as a commit of what it wrote, with its checks still green."""
    dirty = dirty_paths(root, config.runs_dir)
    problems = [f"Commit first — these are not committed: {', '.join(dirty[:8])}"] if dirty else []
    missing = not_built(root, config, Step("package", "project-md", False, None, (), kinds=(BOOK,)))
    return problems + ([missing] if missing else project_md_problems(root, config, run, "package"))


# ---------------------------------------------------------------- run kinds: a new FSD version
#
# A revision run (`revision:V<n>`) takes a new FSD version into a book already written. The referee
# compares it with the version before it (changes); a session works out what each change means for the
# book (triage); the book auditor reads the triage against the FSD (audit); a person approves it
# (revision-gate) before it is packaged. What the triage claims is held to the referee's own reading,
# never the session's word: every change disposed of, every answered question quoted word for word from
# the new version, every new question recommended, no feature row deleted, no plan with work started
# rewritten — and no code: a built feature whose requirement changed becomes a change item, built by a
# change run of its own.

# What a change can mean for the book, as a Triage line says it after its second ` — `.
REVISION_DISPOSITIONS = ("feature-row", "flagged", "linked", "answers", "question", "plan-updated",
                         "plan-flagged", "change-item", "overview", "no-impact")
DISPOSITION_WORD = re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, REVISION_DISPOSITIONS)) + r")(?![\w-])")
DISPOSITION_QUESTION = re.compile(r"(?<![\w-])(answers|question)\s+(Q\d+)\b")
QUOTED = re.compile(r"[\"“]([^\"“”]+)[\"”]")
CITED_SECTION = re.compile(r"§\s*([A-Za-z]?\d+(?:\.\d+)*|front)")
VERSION_NAMED = re.compile(r"\bV(\d+)\b")
# A quote shorter than this proves nothing: "yes" is in every document.
QUOTE_MIN_CHARS = 12
# A quote may spell the FSD's typography plainly: curly quotes, dashes and a non-breaking space read alike.
QUOTE_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
# A plan with a task in one of these has work started: a revision flags it for a person, never rewrites it.
STARTED_TASKS = ("In progress", "Built")
# Where a question's recommendation is written: its own line in the question in full, or a column of the
# summary table — the kit's "What we suggest", or "Recommendation".
RECOMMENDATION_LABEL = re.compile(r"\*\*(?:Recommendation|What we suggest|Suggestion):?\*\*:?\s*(.*)", re.IGNORECASE)
RECOMMENDATION_COLUMNS = ("recommendation", "what we suggest", "suggestion", "recommended")
DEFAULT_OVERVIEW = "docs/system-overview.md"
TEMPLATE_FILL = "<!-- FILL"


def revision_version(run: Run) -> int:
    found = REVISION_ITEM.fullmatch(run.record.get("item", ""))
    return int(found.group("version")) if found else 0


def fsd_folder(config: Config) -> str:
    return (config.raw.get("requirements", {}).get("sources") or {}).get("fsd_dir", "requirements/fsd")


def fsd_versions(root: Path, config: Config) -> dict[int, Path]:
    """Every FSD snapshot by its version, as book_fsd reads them: a .docx, .md or .txt beside a PDF of the same V<n> wins."""
    folder = root / fsd_folder(config)
    pattern = book_settings(config).get("fsd_version", BOOK_FSD_VERSION)
    found: dict[int, list[tuple[bool, str, Path]]] = {}
    for path in sorted(folder.glob("*")) if folder.is_dir() else []:
        match = re.search(pattern, path.name)
        if match:
            found.setdefault(int(match.group(1)), []).append((match.group(2).lower() != "pdf", path.name, path))
    return {version: max(candidates)[2] for version, candidates in found.items()}


def text_at(root: Path, ref: str, rel: str) -> str:
    """A file as it was at a commit — the run's base — or "" when it was not there."""
    if not ref or not rel:
        return ""
    code, text = git(root, "show", f"{ref}:{rel}")
    return text if code == 0 else ""


def decisions_docs(config: Config) -> list[str]:
    return (config.raw.get("requirements", {}).get("sources") or {}).get("decisions", ["docs/open-questions.md"])


def overview_path(config: Config) -> str:
    """The product's system overview: `[book] overview`, else the kit's place for it."""
    return str(book_settings(config).get("overview") or DEFAULT_OVERVIEW)


def revision_refusal(root: Path, config: Config, item: str) -> str | None:
    """Why `revision:V<n>` cannot start: no book to bring up to date, no such snapshot, none before it, or not the newest."""
    found = REVISION_ITEM.fullmatch(item)
    assert found is not None
    version = int(found.group("version"))
    tracker = config.raw.get("requirements", {}).get("tracker", "")
    if not tracker or not read_text(root, tracker):
        return (f"{item}: there is no book to bring up to date — {tracker or 'the [requirements] tracker'} does not exist. "
                "Write it from the first version with a book run: `factory start book:project --new-branch`.")
    versions = fsd_versions(root, config)
    if version not in versions:
        return (f"{item}: {fsd_folder(config)} holds no V{version} snapshot. A person saves the new version there first, "
                f"as `<Name> - V{version}.<ext>` (.docx, .md, .txt or .pdf).")
    if not [v for v in versions if v < version]:
        return (f"{item}: there is no version before V{version} to compare it with — the first version is taken in by "
                "a book run (`book:project`).")
    newest = max(versions)
    if newest != version:
        return f"{item}: V{newest} is the newest snapshot; a revision run takes in the newest — `factory start revision:V{newest}`."
    return None


def section_order(number: str) -> tuple:
    """A section's place: text before the first heading, then numbered sections in order, then unnumbered (`H1.2`)."""
    if number == FRONT_MATTER:
        return (0, ())
    parts = number.removeprefix("H").split(".")
    if all(part.isdigit() for part in parts):
        return (2 if number.startswith("H") else 1, tuple(int(part) for part in parts))
    return (3, (number,))


def revision_sections(path: Path) -> dict[str, dict]:
    """
    A snapshot's sections as {id: {title, lines}}, in document order: from the document's own headings
    (fsd_outline) for a .docx, .md or .txt, from pdftotext's text for a PDF — the reading `factory
    fsd-diff` makes. Lines are whitespace-collapsed, so a layout shift is not a change.
    """
    if path.suffix.lower() == ".pdf":
        return fsd_sections(pdf_text(path))
    module = phase_d_module("fsd_outline")
    if module is None:
        raise FactoryError("devtools/factory/fsd_outline.py is not here to read the FSD's headings.")
    sections: dict[str, dict] = {}
    for unit in module.outline(path):
        lines = [" ".join(line.split()) for line in unit["text"].translate(ZERO_WIDTH).splitlines() if line.strip()]
        sections.setdefault(unit["id"], {"title": unit["title"], "lines": lines})
    return sections


def section_changes(old: dict[str, dict], new: dict[str, dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """
    The sections added, removed and changed between two readings, in document order. A section is
    changed when its title or its whitespace-collapsed lines differ; each changed one carries a short
    unified diff (`fsd-diff`'s comparison, shared by the revision run's changes step).
    """
    added, removed, changed = [], [], []
    for number in sorted(set(old) | set(new), key=section_order):
        before, after = old.get(number), new.get(number)
        if before is None:
            added.append({"section": number, "title": after["title"], "lines": len(after["lines"])})
        elif after is None:
            removed.append({"section": number, "title": before["title"], "lines": len(before["lines"])})
        elif before["lines"] != after["lines"] or before["title"] != after["title"]:
            diff, more, plus, minus = short_diff(before["lines"], after["lines"])
            changed.append({"section": number, "title": after["title"], "old_title": before["title"],
                            "added": plus, "removed": minus, "diff": diff, "diff_more": more})
    return added, removed, changed


def render_sections_text(rel: str, version: int, sections: dict[str, dict]) -> str:
    """One version's text, section by section — what the triage quotes and the referee checks the quotes against."""
    parts = [f"# The FSD's text, section by section — V{version}\n\n*Read by the referee from `{rel}`. A quote under "
             "the run's Answered section is checked against this text, word for word.*"]
    for number, section in sections.items():
        parts.append(f"### §{number} {section['title']}\n\n" + ("\n".join(section["lines"]) or "*(no text)*"))
    return "\n\n".join(parts) + "\n"


def check_fsd_changes(root: Path, config: Config, run: Run) -> Outcome:
    """
    The referee compares the run's FSD version with the one before it, itself, and writes the Changes
    section: every section added, removed or changed, in document order, a changed one with its diff.
    Both versions' text, section by section, is left as the run's evidence for the triage and the audit.
    """
    version = revision_version(run)
    versions = fsd_versions(root, config)
    earlier = sorted(v for v in versions if v < version)
    if version not in versions or not earlier:
        return Outcome(False, [f"Changes: {fsd_folder(config)} needs V{version} and a version before it — "
                               + (revision_refusal(root, config, run.record.get("item", "")) or "it has neither.")])
    previous = earlier[-1]
    old_path, new_path = versions[previous], versions[version]
    old_rel, new_rel = old_path.relative_to(root).as_posix(), new_path.relative_to(root).as_posix()
    try:
        old, new = revision_sections(old_path), revision_sections(new_path)
    except FactoryError as error:
        return Outcome(False, [f"Changes: {error}"])
    except (zipfile.BadZipFile, ElementTree.ParseError, UnicodeDecodeError, KeyError, ValueError) as error:
        return Outcome(False, [f"Changes: a snapshot cannot be read ({error}) — export it again."])
    problems = [f"Changes: {rel} has no headings the referee can read." for rel, read in ((old_rel, old), (new_rel, new))
                if not [number for number in read if number != FRONT_MATTER]]
    added, removed, changed = section_changes(old, new)
    texts = {}
    for name, rel, number, read in (("text", new_rel, version, new), ("previous_text", old_rel, previous, old)):
        path = evidence_path(root, config, run, "fsd-text.md" if name == "text" else "fsd-text-previous.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_sections_text(rel, number, read), encoding="utf-8", newline="\n")
        texts[name] = path.relative_to(root).as_posix()
    run.record["changes"] = {
        "previous": previous, "version": version, "old": old_rel, "new": new_rel, "at": utc_now(),
        "old_sha256": sha256_file(old_path), "new_sha256": sha256_file(new_path), **texts,
        "added": [{"section": c["section"], "title": c["title"]} for c in added],
        "removed": [{"section": c["section"], "title": c["title"]} for c in removed],
        "changed": [{"section": c["section"], "title": c["title"], "old_title": c["old_title"],
                     "added": c["added"], "removed": c["removed"]} for c in changed]}
    entries = sorted([(c, "added") for c in added] + [(c, "removed") for c in removed] + [(c, "changed") for c in changed],
                     key=lambda pair: section_order(pair[0]["section"]))
    lines = []
    for entry, how in entries:
        if how == "changed":
            renamed = f", was \"{entry['old_title']}\"" if entry["old_title"] != entry["title"] else ""
            lines.append(f"- §{entry['section']} {entry['title']} — changed (+{entry['added']} −{entry['removed']}{renamed})")
            lines += ["  ```diff", *(f"  {line}" for line in entry["diff"]),
                      *([f"  … {entry['diff_more']} more line(s)"] if entry["diff_more"] else []), "  ```"]
        else:
            lines.append(f"- §{entry['section']} {entry['title']} — {how}")
    summary = f"{len(added)} added · {len(removed)} removed · {len(changed)} changed"
    run.sections["Changes"] = (f"*Written by the referee: `{new_rel}` (V{version}) against `{old_rel}` (V{previous}) — "
                               f"{summary}. Each version's text, section by section: `{texts['text']}` (V{version}), "
                               f"`{texts['previous_text']}` (V{previous}).*\n\n" + ("\n".join(lines) or "No section changed."))
    stamp_section(run, "Changes", "changes")
    return Outcome(not problems, problems or [f"Changes: V{previous} → V{version} — {summary}."])


def revision_entries(changes: dict) -> list[tuple[dict, str]]:
    """Every change the changes step listed, with how it changed, in document order."""
    return sorted([(entry, how) for how in ("added", "removed", "changed") for entry in changes.get(how) or []],
                  key=lambda pair: section_order(pair[0]["section"]))


def listed_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip().startswith(("-", "*"))]


def question_has_recommendation(texts: list[str], question: str) -> bool:
    """
    Whether a question carries a recommendation, wherever the project writes one: a `**Recommendation:**`
    (or "What we suggest") line in the question in full, or that column of a summary table — the last
    cell of a table with no header that names one.
    """
    named = re.compile(rf"(?<![\w-]){re.escape(question)}(?!\d)")

    def filled(value: str) -> bool:
        value = plain_cell(re.sub(r"<!--.*?-->", "", value)).strip(" *_`")
        return bool(value) and value not in NO_VALUE and not value.startswith("<")

    for text in texts:
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if re.match(rf"^#{{2,6}}\s+\[?{re.escape(question)}\b", line):
                block = []
                for following in lines[index + 1:]:
                    if following.startswith("#"):
                        break
                    block.append(following)
                if any((m := RECOMMENDATION_LABEL.search(part)) and filled(m.group(1)) for part in block):
                    return True
        header: list[str] | None = None
        for line in lines:
            cells = table_cells(line)
            if cells is None:
                header = None
                continue
            if all(set(cell) <= set("-: ") for cell in cells):
                continue
            if header is None and not named.search(cells[0]):
                header = [cell.lower().strip(" *") for cell in cells]
                continue
            if not named.search(cells[0]):
                continue
            if header is None:
                value = cells[-1]  # a table with no header row: the recommendation is its last cell
            else:
                column = next((i for i, name in enumerate(header) if name in RECOMMENDATION_COLUMNS), None)
                value = cells[column] if column is not None and column < len(cells) else ""
            if filled(value):
                return True
    return False


def fold_quote(text: str) -> str:
    return " ".join(text.translate(QUOTE_FOLD).translate(ZERO_WIDTH).split())


def revision_question_problems(root: Path, config: Config, run: Run, changes: dict) -> tuple[list[str], set[str], set[str]]:
    """
    The questions the triage answered and raised, against the files themselves at the run's base and now:
    each question moved to Resolved is under ## Answered and each listed there was moved — quoted from the
    new version, word for word, with its section and the version; each new question is under ## New questions
    and carries a recommendation. Returns the problems, and the ids listed in each section.
    """
    version, base = changes["version"], run.record.get("base") or ""
    docs = decisions_docs(config)
    before = [text_at(root, base, doc) for doc in docs]
    now = [read_text(root, doc) for doc in docs]
    answered_before = set().union(set(), *(answered_questions(t) for t in before if t))
    answered_now = set().union(set(), *(answered_questions(t) for t in now if t))
    moved = answered_now - answered_before
    raised = set(re.findall(r"\bQ\d+\b", "\n".join(now))) - set(re.findall(r"\bQ\d+\b", "\n".join(before)))
    where = ", ".join(docs)
    problems: list[str] = []
    answered: dict[str, str] = {}
    for line in listed_lines(run.section_text("Answered")):
        found = re.search(r"\bQ\d+\b", QUOTED.sub("", line))
        if found:
            answered.setdefault(found.group(0), line)
    new_text: dict[str, dict] | None = None
    for question, line in sorted(answered.items(), key=lambda pair: int(pair[0][1:])):
        if question not in moved:
            problems.append(f"Answered: {question} is listed, but it was answered before this run." if question in answered_before
                            else f"Answered: {question} is listed, but {where} does not have it under Resolved — move it "
                                 "there with the rule now in force, or take it off the list.")
        quotes = [quote for quote in QUOTED.findall(line) if len(quote.strip()) >= QUOTE_MIN_CHARS]
        rest = QUOTED.sub("", line)
        cited = CITED_SECTION.findall(rest)
        named = VERSION_NAMED.findall(rest)
        if not quotes:
            problems.append(f"Answered: {question} quotes nothing — give the V{version} words that answer it, in double "
                            f"quotes, as `{changes.get('text', 'the FSD text')}` has them.")
        if not cited:
            problems.append(f"Answered: {question} names no section (§<id>) of V{version} that answers it.")
        if not named:
            problems.append(f"Answered: {question} names no version — the answer is V{version}'s.")
        elif str(version) not in named:
            problems.append(f"Answered: {question} names V{', V'.join(named)}; this run takes in V{version}.")
        if quotes and cited:
            if new_text is None:
                try:
                    new_text = revision_sections(root / changes["new"])
                except (FactoryError, OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as error:
                    problems.append(f"Answered: V{version} could not be read again to check the quotes ({error}).")
                    new_text = {}
            texts = [fold_quote(" ".join([new_text[s]["title"], *new_text[s]["lines"]])) for s in cited if s in new_text]
            for quote in quotes:
                if not any(fold_quote(quote) in text for text in texts):
                    shown = quote if len(quote) <= 60 else quote[:59] + "…"
                    problems.append(f"Answered: {question}'s quote \"{shown}\" is not in {', '.join('§' + s for s in cited)} "
                                    f"of V{version}, word for word — quote it from `{changes.get('text', 'the FSD text')}`.")
    problems += [f"{question} moved to Resolved in {where} but is not under ## Answered — list it with the V{version} "
                 "words that answer it, their section and the version." for question in sorted(moved - set(answered))]
    listed_new: set[str] = set()
    for line in listed_lines(run.section_text("New questions")):
        listed_new.update(re.findall(r"\bQ\d+\b", line)[:1])
    problems += [f"{question} is new in {where} but is not under ## New questions — list it."
                 for question in sorted(raised - listed_new, key=lambda q: int(q[1:]))]
    problems += [f"New questions: {question} is listed, but it is not a new question in {where}."
                 for question in sorted(listed_new - raised, key=lambda q: int(q[1:]))]
    problems += [f"New questions: {question} has no recommendation in {where} — every question carries one, and it is "
                 "what gets built unless the answer says otherwise." for question in sorted(raised & listed_new, key=lambda q: int(q[1:]))
                 if not question_has_recommendation(now, question)]
    return problems, set(answered), listed_new


def revision_triage_line_problems(root: Path, config: Config, run: Run, changes: dict, answered: set[str],
                                  raised: set[str]) -> list[str]:
    """Every change has a Triage line with a disposition from the closed list, and what a line claims holds."""
    tracker = config.raw.get("requirements", {}).get("tracker", "docs/project.md")
    cited_now = set(sections_cited(read_text(root, tracker)))
    lines: dict[str, list[str]] = {}
    for line in run.section_text("Triage").splitlines():
        match = INVENTORY_LINE.match(line)
        if match:
            lines.setdefault(match.group("id"), []).append(match.group("rest"))
    known = {entry["section"] for entry, _ in revision_entries(changes)}
    problems = [f"Triage: §{section} is not a change the Changes section lists." for section in lines if section not in known]
    for entry, how in revision_entries(changes):
        section = entry["section"]
        said = [rest.split(" — ", 1)[1] if " — " in rest else "" for rest in lines.get(section, [])]
        if not said:
            problems.append(f"Triage: §{section} {entry['title']} ({how}) has no line in the Triage section — say what it "
                            "means for the book.")
            continue
        words = {word for text in said for word in DISPOSITION_WORD.findall(text)}
        if not words:
            problems.append(f"Triage: §{section} — say what it means for the book with one of "
                            f"{', '.join(REVISION_DISPOSITIONS)}, after ` — `.")
        text = " ".join(said)
        if "no-impact" in words and not re.search(r"no-impact\s*\(\s*\S", text):
            problems.append(f"Triage: §{section} — no-impact says why: `no-impact (<why>)`.")
        if "feature-row" in words and section not in cited_now:
            problems.append(f"Triage: §{section} says feature-row, but no row of {tracker} cites §{section}.")
        for verb, question in DISPOSITION_QUESTION.findall(text):
            if verb == "answers" and question not in answered:
                problems.append(f"Triage: §{section} says it answers {question}, which is not under ## Answered.")
            if verb == "question" and question not in raised:
                problems.append(f"Triage: §{section} raises {question}, which is not under ## New questions.")
    return problems


def landing_sections(row: dict, sections: list[str]) -> list[str]:
    """The changed sections a feature row's FSD cell names, holds or sits inside."""
    cited = fsd_cell_sections(row["fsd"])
    return [section for section in sections if any(section_relation(section, c) for c in cited)]


def tracker_row_line(text: str, feature: str) -> str:
    return next((line for line in text.splitlines() if (cells := table_cells(line)) and plain_cell(cells[0]) == feature), "")


def started_plan_problems(rel: str, before: str, now: str) -> list[str]:
    """A plan with work started keeps every line it had; it may only gain a new task row, not started."""
    kept = {line.rstrip() for line in now.splitlines()}
    gone = [line.rstrip() for line in before.splitlines() if line.strip() and line.rstrip() not in kept]
    had = {line.rstrip() for line in before.splitlines()}
    new_rows = {row["line"].rstrip() for row in plan_task_rows(now) or [] if row["line"].rstrip() not in had
                and task_status(row) == TASK_NOT_STARTED}
    added = [line.rstrip() for line in now.splitlines() if line.strip() and line.rstrip() not in had
             and line.rstrip() not in new_rows]
    if not gone and not added:
        return []
    first = (gone or added)[0]
    return [f"{rel} has work started: it is flagged, not rewritten — only a new `{TASK_NOT_STARTED}` task row may be "
            f"added. A person takes it back through the plan gate. Put back: {first[:90]}"]


def revision_book_problems(root: Path, config: Config, run: Run, changes: dict) -> list[str]:
    """
    The book against itself at the run's base: no feature row deleted; a row whose section was removed
    still there and saying so; a built feature a change lands on listed as a change item that exists;
    a plan with work started that a change lands on flagged for a person, and left as it was.
    """
    version, base = changes["version"], run.record.get("base") or ""
    tracker = config.raw.get("requirements", {}).get("tracker", "docs/project.md")
    before_rows = book_features(text_at(root, base, tracker), tracker)
    now_text = read_text(root, tracker)
    now_names = {row["feature"] for row in book_features(now_text, tracker)}
    removed = [entry["section"] for entry in changes.get("removed") or []]
    landed = [entry["section"] for entry, _ in revision_entries(changes)]
    logs = config.raw.get("requirements", {}).get("defect_logs", [])
    defects = "\n".join(read_text(root, log) for log in logs)
    items = listed_lines(run.section_text("Change items"))
    flagged = run.section_text("Flagged plans")
    problems = []
    for row in before_rows:
        feature = row["feature"]
        if feature not in now_names:
            problems.append(f"{tracker}: the {feature} row is gone from {tracker} — a revision never deletes a feature; a "
                            f"removed section's row stays, flagged (`… — §<id> removed in V{version}`).")
            continue
        gone = landing_sections(row, removed)
        if gone and f"V{version}" not in tracker_row_line(now_text, feature):
            problems.append(f"{tracker}: the {feature} row cites §{gone[0]}, removed in V{version}: keep the row and say "
                            f"so in it (`… — §{gone[0]} removed in V{version}`).")
        hits = landing_sections(row, landed)
        if not hits:
            continue
        if row["status"].lower().startswith("built"):
            line = next((item for item in items if feature.lower() in item.lower()), None)
            if line is None:
                problems.append(f"Change items: {feature} is Built and §{hits[0]} changed in V{version} — list it with the "
                                "defect row or the plan task that tracks the change; a revision never changes code.")
            else:
                tasks = [(stem, number) for stem, number in re.findall(r"([a-z0-9][a-z0-9-]*)#(\d+)", line)
                         if any(r.get("#", "").strip() == number for r in plan_task_rows(read_text(root, plan_path(config, stem))) or [])]
                ids = [d for d in re.findall(r"\bD\d+\b", line) if re.search(rf"\*\*{d}\*\*", defects)]
                if not tasks and not ids:
                    problems.append(f"Change items: {feature} names no defect row or plan task that exists — a `D<n>` with "
                                    f"its row in {', '.join(logs) or 'the defect log'}, or `<plan-stem>#<task>`.")
        plan = row["plan"]
        plan_before = text_at(root, base, plan) if plan else ""
        if plan_before and any(task_status(task) in STARTED_TASKS for task in plan_task_rows(plan_before) or []):
            if plan not in flagged:
                problems.append(f"Flagged plans: {plan} has work started and §{hits[0]} changed in V{version} — list it "
                                "for a person; it goes back through the plan gate, never rewritten here.")
            problems += started_plan_problems(plan, plan_before, read_text(root, plan))
    return problems


def revision_writes(config: Config) -> list[str]:
    return list(dict.fromkeys(write for step in config.steps_of(REVISION) for write in step.writes))


def revision_scope_problems(root: Path, config: Config, run: Run) -> list[str]:
    """A revision run changes the book alone: only what its steps may write, its run file and the new snapshot."""
    allowed = revision_writes(config)
    own = (config.runs_dir.rstrip("/") + "/", fsd_folder(config).rstrip("/") + "/",
           str(config.raw.get("evidence_dir", ".factory/evidence")).rstrip("/") + "/")
    paths = changed_paths(root, run.record.get("base") or merge_base(root, config.raw["base_ref"]))
    return [f"{path}: a revision run changes the book, never code — only {', '.join(allowed) or 'its run file'}. Undo it; "
            "a built feature's change is a change item, built by a run of its own."
            for path in paths if not path.startswith(own) and not any(glob_match(path, write) for write in allowed)]


def check_revision_triage(root: Path, config: Config, run: Run) -> Outcome:
    """The triage, held to the referee's own reading of the changes, the questions, the book and the diff."""
    changes = run.record.get("changes") or {}
    if not changes:
        return Outcome(False, ["Triage: the changes step has not compared the versions yet — "
                               "`factory reopen changes --reason \"…\"`."])
    problems, answered, raised = revision_question_problems(root, config, run, changes)
    problems = revision_triage_line_problems(root, config, run, changes, answered, raised) + problems
    problems += revision_book_problems(root, config, run, changes)
    problems += revision_scope_problems(root, config, run)
    dirty = dirty_paths(root, config.runs_dir)
    if dirty:
        problems.append(f"Commit first — the revision gate approves a commit, and these are not committed: {', '.join(dirty[:8])}")
    problems += book_command_problems(root, config, run, "triage")
    return Outcome(not problems, problems or [f"Triage: {len(revision_entries(changes))} change(s), each disposed of; "
                                              f"{len(answered)} question(s) answered, {len(raised)} raised."])


def revision_package_problems(root: Path, config: Config, run: Run) -> list[str]:
    """A revision run is packaged as the commit its gate approved: nothing in the book changed after it, and no code."""
    dirty = dirty_paths(root, config.runs_dir)
    problems = [f"Commit first — these are not committed: {', '.join(dirty[:8])}"] if dirty else []
    approved = ((run.record.get("gates") or {}).get(REVISION_GATE) or {}).get("head") or ""
    if not approved:
        problems.append(f"The {REVISION_GATE} has no approval on record: a person approves it, `factory approve revision`.")
    else:
        runs = config.runs_dir.rstrip("/") + "/"
        after = [path for path in git_strict(root, "diff", "--name-only", "--no-renames", approved, "HEAD").splitlines()
                 if path and not path.startswith(runs)]
        if after:
            problems.append(f"The book changed after the {REVISION_GATE} approved the triage: {', '.join(after[:8])} — a "
                            "person reopens it, `factory reopen triage --reason \"…\"`, and approves it again.")
    return problems + revision_scope_problems(root, config, run) + book_command_problems(root, config, run, "package")


def overview_problems(root: Path, config: Config) -> list[str]:
    """
    The system overview, where the project keeps one, is written from the FSD — what the product is, its
    vocabulary, its users and its main flows — not left as the kit's template. A project without the page
    is not asked for one.
    """
    rel = overview_path(config)
    text = read_text(root, rel)
    if not text or TEMPLATE_FILL not in text:
        return []
    return [f"{rel} is still the template: write it from the FSD — what the product is, the vocabulary, the users and "
            f"roles, the main flows — each from the sections that say so, and leave no `{TEMPLATE_FILL}`."]


BOOK_CHECKS = {"fsd-outline": check_fsd_outline, "fsd-coverage": check_fsd_coverage,
               "project-md": check_book_project_md, "book-audit": check_book_audit,
               "fsd-changes": check_fsd_changes, "revision-triage": check_revision_triage}


# ---------------------------------------------------------------- tests and evidence

# ---- stacks: a project with `[[stacks]]` holds several, each with its own root, adapter, commands and
# writes (a `backend/` and a `frontend/`). Without it the top-level [adapter] and [commands] are the one
# stack, at the repository's root, exactly as before stacks existed.

STACK_ADAPTER_KEYS = ("test_files", "criterion_marker", "run_tests", "run_tests_filtered", "criterion_filter",
                      "results", "results_file")


@dataclass(frozen=True)
class Stack:
    name: str  # "" for the one stack of a project without [[stacks]]
    root: str  # repository-relative, no trailing slash; "" for the repository's root
    config: Config  # the stack's own view: its adapter (globs prefixed with root) and commands


def is_multi_stack(config: Config) -> bool:
    return bool(config.raw.get("stacks"))


def prefixed(root: str, patterns: list[str]) -> list[str]:
    return [f"{root}/{pattern}" if root else pattern for pattern in patterns]


def stack_writes(raw: dict, step: str) -> tuple[str, ...]:
    """What a step may write in each stack, prefixed with the stack's root: `[stacks.writes] <step> = [...]`."""
    return tuple(path for stack in raw.get("stacks") or [] if isinstance(stack, dict)
                 for path in prefixed(str(stack.get("root", "")).strip("/"),
                                      list((stack.get("writes") or {}).get(step, []))))


def stacks(config: Config) -> list[Stack]:
    """Every stack of the project, each with its own view of the configuration."""
    if not is_multi_stack(config):
        return [Stack("", "", config)]
    found = []
    for entry in config.raw["stacks"]:
        root = str(entry["root"]).strip("/")
        settings = dict(entry["adapter"])
        settings["test_files"] = prefixed(root, list(settings.get("test_files", [])))
        settings["lock_files"] = prefixed(root, list(settings.get("lock_files", [])))
        view = {key: value for key, value in config.raw.items() if key != "stacks"}
        view.update({"adapter": settings, "commands": dict(entry.get("commands") or {}),
                     "stack": {"name": entry["name"], "root": root}})
        found.append(Stack(entry["name"], root, Config(view)))
    return found


def stack_root(config: Config) -> str:
    """The root of the stack this view is for: "" for a whole project or a project without [[stacks]]."""
    return (config.raw.get("stack") or {}).get("root", "")


def stack_cwd(root: Path, config: Config) -> Path | None:
    """Where a stack's commands run: its root; None (the repository's root) without [[stacks]]."""
    return root / stack_root(config) if stack_root(config) else None


def under(path: str, folder: str) -> bool:
    return not folder or path == folder or path.startswith(folder + "/")


def stack_of(rel: str, config: Config) -> Stack | None:
    """The stack a repository-relative path lies in: the deepest root holding it, or None."""
    holding = [stack for stack in stacks(config) if under(rel, stack.root)]
    return max(holding, key=lambda stack: len(stack.root)) if holding else None


def within_stack(rel: str, stack: Stack) -> str:
    """A repository-relative path as its stack sees it: `frontend/src/a.ts` → `src/a.ts`."""
    if not stack.root:
        return rel
    return "." if rel == stack.root else rel.removeprefix(stack.root + "/")


def run_stacks(root: Path, config: Config, run: Run, also: list[str] | tuple[str, ...] = ()) -> list[Stack]:
    """
    The stacks a run reaches: those whose root holds a path the run changed, a test it locked, or one of
    `also` (its criteria's tests). A run that reaches none — documentation only — reaches every stack,
    so nothing goes unbuilt. Without [[stacks]], the one stack.
    """
    every = stacks(config)
    if not is_multi_stack(config):
        return every
    paths = [*changed_paths(root, run.record["base"]), *(run.record.get("lock") or {}), *also]
    reached = [stack for stack in every if any(under(path, stack.root) for path in paths)]
    return reached or every


def check_stacks(config: Config) -> None:
    """A `[[stacks]]` typo fails at load, not halfway through a run's tests."""
    entries = config.raw.get("stacks")
    if entries is None:
        return
    if not isinstance(entries, list) or not entries or not all(isinstance(entry, dict) for entry in entries):
        raise FactoryError("factory.toml: [[stacks]] must be one or more tables.")
    problems = []
    if "adapter" in config.raw:
        problems.append("it has both [adapter] and [[stacks]] — with stacks, each stack's adapter is [stacks.adapter]")
    names, roots = [], []
    for index, entry in enumerate(entries, 1):
        name = entry.get("name")
        label = f"stack {name or index}"
        if not name or not isinstance(name, str):
            problems.append(f"{label} has no name")
        root = str(entry.get("root", "")).strip("/")
        if not root or ".." in root.split("/"):
            problems.append(f"{label}: root must be a folder inside the repository (backend, frontend)")
        missing = [key for key in STACK_ADAPTER_KEYS if key not in (entry.get("adapter") or {})]
        if missing:
            problems.append(f"{label}: [stacks.adapter] lacks {', '.join(missing)}")
        if "build" not in (entry.get("commands") or {}):
            problems.append(f"{label}: [stacks.commands] has no build")
        names.append(name)
        roots.append(root)
    problems += [f"stack name {name!r} is used twice" for name in sorted({n for n in names if n and names.count(n) > 1})]
    problems += [f"stack roots {a!r} and {b!r} overlap; each stack needs a folder of its own"
                 for i, a in enumerate(roots) for b in roots[i + 1:] if a and b and (under(a, b) or under(b, a))]
    if problems:
        raise FactoryError("factory.toml: " + "; ".join(problems) + ".")


def adapter(config: Config) -> dict:
    """
    The stack adapter. With [[stacks]], a merged view for what only matches paths — every stack's test
    and lock globs (prefixed with its root), its skipped folders, its names; what runs or parses a stack's
    output (markers, commands, error patterns) is read from that stack's own view, never from this.
    """
    if not is_multi_stack(config):
        return config.raw["adapter"]
    every = stacks(config)
    views = [stack.config.raw["adapter"] for stack in every]
    return {
        "name": " + ".join(f"{stack.name}: {view.get('name', '?')}" for stack, view in zip(every, views)),
        "test_files": [p for view in views for p in view.get("test_files", [])],
        "lock_files": [p for view in views for p in view.get("lock_files", [])],
        "skip_parts": sorted({p for view in views for p in view.get("skip_parts", [])}),
        "acceptance_note": " ".join(dict.fromkeys(view["acceptance_note"] for view in views
                                                  if view.get("acceptance_note")))
                           or "Evidence from a deployed environment comes after the merge.",
    }


def ac_traits(root: Path, config: Config) -> dict[str, list[str]]:
    """Every test tagged with a criterion (the adapter's `criterion_marker`), id → the files carrying it."""
    found: dict[str, list[str]] = {}
    if is_multi_stack(config):
        for stack in stacks(config):
            for ac, files in ac_traits(root, stack.config).items():
                found.setdefault(ac, []).extend(files)
        return found
    settings = adapter(config)
    marker = re.compile(settings["criterion_marker"])
    skip = set(settings.get("skip_parts", []))
    seen: set[Path] = set()
    for pattern in settings["test_files"]:
        for path in root.glob(pattern):
            if path in seen or not path.is_file() or skip & set(path.relative_to(root).parts):
                continue
            seen.add(path)
            # Once per file: a file with nine tests for one criterion was listed nine times in the PR body.
            for ac in sorted(set(marker.findall(path.read_text(encoding="utf-8", errors="replace")))):
                found.setdefault(ac, []).append(path.relative_to(root).as_posix())
    return found


def project_test_files(root: Path, config: Config) -> list[str]:
    """
    Every file the adapter calls a test file, and its `lock_files` — the build files that decide
    what a test project compiles. A `<Compile Remove>` in a test project swapped a locked
    helper for a weaker copy (the third review). Repository-relative, build output left out.
    """
    if is_multi_stack(config):
        return sorted({rel for stack in stacks(config) for rel in project_test_files(root, stack.config)})
    settings = adapter(config)
    skip = set(settings.get("skip_parts", []))
    found: set[str] = set()
    for pattern in [*settings["test_files"], *settings.get("lock_files", [])]:
        for path in root.glob(pattern):
            if path.is_file() and not skip & set(path.relative_to(root).parts):
                found.add(path.relative_to(root).as_posix())
    return sorted(found)


def test_project(rel: str, config: Config) -> str:
    """
    The test project a file belongs to: its first `project_depth` path segments, or `.`. With
    [[stacks]], counted inside the file's stack and named from the repository's root (`frontend`,
    `backend/tests/Shop.Tests`), so two stacks' projects never share a name.
    """
    if is_multi_stack(config):
        stack = stack_of(rel, config)
        if stack is None:
            return "."
        inner = test_project(within_stack(rel, stack), stack.config)
        return stack.root if inner == "." else f"{stack.root}/{inner}"
    depth = int(adapter(config).get("project_depth", 0))
    return "/".join(rel.split("/")[:depth]) if depth else "."


def project_stack(project: str, config: Config) -> tuple[Config, str]:
    """A test project (as test_project names it) → its stack's view, and the project as that stack names it."""
    if not is_multi_stack(config):
        return config, project
    stack = stack_of(project, config)
    if stack is None:
        raise FactoryError(f"{project} lies in none of the [[stacks]] roots, so no stack can run its tests.")
    return stack.config, within_stack(project, stack)


def parse_trx(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError:
        return None  # cut short by a crashed test host: no result, never a traceback (the 15 Oct review's #3)
    counters = root.find(".//t:ResultSummary/t:Counters", TRX_NAMESPACE)
    if counters is None:
        return None
    keys = ("total", "executed", "passed", "failed", "notExecuted")
    counts = {key: int(counters.get(key, "0")) for key in keys}
    # The summary can say notExecuted="0" while results inside are NotExecuted — a skipped
    # xUnit test is counted that way. Count the results themselves, as CI's gate does.
    skipped = sum(1 for result in root.iterfind(".//t:UnitTestResult", TRX_NAMESPACE)
                  if result.get("outcome") == "NotExecuted")
    counts["notExecuted"] = max(counts["notExecuted"], skipped, counts["total"] - counts["executed"])
    return counts


def parse_junit(path: Path, counted: Callable[[str], bool] | None = None) -> dict | None:
    """
    JUnit XML — pytest, Jest, Vitest, Go and most runners write it. Counted per test case; with `counted`,
    only the cases whose name it accepts (a filtered Vitest run lists every test it left out as skipped).
    """
    if not path.exists():
        return None
    try:
        cases = list(ElementTree.parse(path).getroot().iter("testcase"))
    except ElementTree.ParseError:
        return None  # cut short by a crashed runner: no result
    total = failed = skipped = 0
    for case in cases:
        if counted is not None and not counted(case.get("name") or ""):
            continue
        total += 1
        if case.find("failure") is not None or case.find("error") is not None:
            failed += 1
        elif case.find("skipped") is not None:
            skipped += 1
    executed = total - skipped
    return {"total": total, "executed": executed, "passed": executed - failed, "failed": failed,
            "notExecuted": skipped}


def parse_results(path: Path, config: Config, criteria: list[str] | None = None) -> dict | None:
    """
    The run's counts. An adapter with `filtered_results = "tagged"` (Vitest: `-t` reports what it left out
    as skipped) counts, in a run filtered to `criteria`, only the test cases whose name carries one of them.
    """
    settings = adapter(config)
    if settings["results"] != "junit":
        return parse_trx(path)
    if criteria and settings.get("filtered_results") == "tagged":
        marker, wanted = re.compile(settings["criterion_marker"]), set(criteria)
        return parse_junit(path, lambda name: bool(wanted & set(marker.findall(name))))
    return parse_junit(path)


def compile_errors(output: str, config: Config) -> list[str]:
    return sorted(set(re.findall(adapter(config).get("compile_error", r"(?!)"), output)))


def missing_symbol_only(output: str, config: Config) -> bool:
    """Every error is a compiler error, and every compiler error is a missing symbol."""
    settings = adapter(config)
    codes = set(compile_errors(output, config))
    compiler_lines = {line for line in output.splitlines() if re.search(settings.get("compile_error", r"(?!)"), line)}
    other_errors = [line for line in output.splitlines()
                    if re.search(settings.get("any_error", r"(?!)"), line) and line not in compiler_lines]
    return bool(codes) and not other_errors and codes <= set(settings.get("missing_symbol_errors", []))


def files_missing_a_symbol(output: str, config: Config) -> str:
    """The error lines, slashes normalised, for matching against a criterion's test files."""
    pattern = adapter(config).get("compile_error", r"(?!)")
    return "\n".join(line.replace("\\", "/") for line in output.splitlines() if re.search(pattern, line))


def evidence_path(root: Path, config: Config, run: Run, name: str) -> Path:
    return root / config.raw["evidence_dir"] / run.id / name


def record_check(root: Path, run: Run, step: str, name: str, ok: bool, execution: Execution | None,
                 log: Path | None, counts: dict | None = None) -> None:
    entry = {
        "step": step, "name": name, "ok": ok, "at": utc_now(), "head": head(root),
        "config": fingerprint(root)[:16],
        "command": execution.command if execution else None,
        "exit": execution.exit if execution else None,
        "seconds": execution.seconds if execution else None,
        "sha256": sha256_file(log) if log and log.exists() else None,
        "counts": counts,
    }
    checks = [c for c in run.record.get("checks", []) if not (c["step"] == step and c["name"] == name)]
    run.record["checks"] = checks + [entry]


def run_tests(root: Path, config: Config, run: Run, step: str, name: str, project: str,
              test_filter: str | None, criteria: list[str] | None = None) -> tuple[Execution, dict | None, Path]:
    """
    Runs a suite, or the part of it `test_filter` selects (`criteria`'s tests), through the stack adapter's
    command. With [[stacks]], the project names its stack, and the command runs in that stack's root.
    """
    stack_config, project = project_stack(project, config)
    settings = adapter(stack_config)
    # Windows drops a folder name's trailing dots: the suite of project `.` wrote its results where none were read.
    results = evidence_path(root, config, run, f"{step}-{name}".rstrip(".") if name.strip(".") else f"{step}-all")
    shutil.rmtree(results, ignore_errors=True)
    results.mkdir(parents=True, exist_ok=True)
    template = settings["run_tests_filtered"] if test_filter else settings["run_tests"]
    command = template.format(project=project, results=results.as_posix(), filter=test_filter or "")
    log = results / "output.log"
    execution = execute(root, to_argv(command), log, cwd=stack_cwd(root, stack_config))
    return execution, parse_results(results / settings["results_file"], stack_config,
                                    criteria if test_filter else None), log


def build(root: Path, config: Config, run: Run, step: str) -> tuple[Execution, Path]:
    """The build, in the stack's root; `config` is a stack's view (or the project, without [[stacks]])."""
    name = (config.raw.get("stack") or {}).get("name")
    log = evidence_path(root, config, run, f"{step}-{name}-build.log" if name else f"{step}-build.log")
    return execute(root, to_argv(config.raw["commands"]["build"]), log, cwd=stack_cwd(root, config)), log


def build_check_name(stack: Stack) -> str:
    """The Record's name for a stack's build: `build`, as ever, or `build <stack>` with [[stacks]]."""
    return f"build {stack.name}" if stack.name else "build"


def require_clean(root: Path, config: Config) -> None:
    dirty = dirty_paths(root, config.runs_dir)
    if dirty:
        raise FactoryError("Commit first — evidence is for a commit, and these are not committed: "
                           + ", ".join(dirty[:8]) + (" …" if len(dirty) > 8 else ""))


def lock_problems(root: Path, run: Run) -> list[str]:
    """
    A locked test the run itself changed after its red proof. A change that arrived with `dev`
    merged in is not the run's: the lock spans whole test projects, which `dev` changes daily,
    and failing every run on each catch-up made the lock unusable (the third review).
    """
    problems = []
    changed: set[str] | None = None
    for rel, digest in run.record.get("lock", {}).items():
        path = root / rel
        if path.exists() and lock_holds(path, digest):
            continue
        if changed is None:
            changed = changed_since_red(root, run)
        if changed is not None and rel not in changed:
            continue  # changed only by work merged in from the base branch
        if not path.exists():
            problems.append(f"Locked test {rel} was deleted.")
        else:
            problems.append(f"Locked test {rel} changed after it was proven red. "
                            "A person can release it with `factory unlock`.")
    return problems


def changed_since_red(root: Path, run: Run) -> set[str] | None:
    """Paths the run's own work changed after its red proof; None when there is no red proof to measure from."""
    red_head = (run.record.get("red") or (run.record.get("previous") or {}).get("red") or {}).get("head")
    if not red_head or git(root, "cat-file", "-e", f"{red_head}^{{commit}}")[0] != 0:
        return None
    return set(changed_paths(root, red_head))


def plan_test_tasks(run: Run, config: Config) -> list[dict]:
    """
    The Plan's tests tasks: those whose every file is a test file or a test project's build file
    (`test_files`, `lock_files`). A Plan that has none falls back to the tasks naming any test file.
    """
    settings = adapter(config)
    patterns = [*settings["test_files"], *settings.get("lock_files", [])]

    def test_path(path: str) -> bool:
        # A glob is a test path when a file it names would be one: `tests/…/Breeds/**` under `tests/**`, and
        # `src/cart/*.test.tsx` under `src/**/*.test.tsx` — but not `src/cart/**`, which a project with its tests
        # beside the code (React) gives a build task. A folder prefix alone said yes to that one too.
        if not any(ch in path for ch in "*?["):
            return any(glob_match(path, pattern) for pattern in patterns)
        sample = re.sub(r"\[[^\]]*\]|\?", "x", path.replace("**", "x/x")).replace("*", "x")
        return any(glob_match(sample, pattern) or tests_folder_holds(pattern, path) for pattern in patterns)

    def tests_folder_holds(pattern: str, path: str) -> bool:
        # A folder that only ever holds tests — a pattern whose file part is `*` or `*.<ext>` (`tests/**/*.cs`) —
        # holds every glob under it. One that picks tests out of a code folder (`src/**/*.test.tsx`) does not.
        fixed = re.split(r"[*?\[]", pattern, maxsplit=1)[0]
        return bool(fixed) and path.startswith(fixed) and re.fullmatch(r"\*(\.[A-Za-z0-9]+)?", pattern.rsplit("/", 1)[-1]) is not None

    tasks = [task for task in plan_tasks(run) if task["files"]]
    return [t for t in tasks if all(test_path(f) for f in t["files"])] or \
           [t for t in tasks if any(test_path(f) for f in t["files"])]


def misplaced_test_problems(run: Run, config: Config, traits: dict[str, list[str]], criteria: list[str],
                            changed: set[str]) -> list[str]:
    """
    A criterion's test file this run wrote must be one the Plan's tests task names (globs allowed): a
    unit test put where the Plan promised an integration test passed unflagged (the dry run's F-17). A
    file the run did not write — a criterion id an earlier change already tagged — is not judged.
    """
    tasks = plan_test_tasks(run, config)
    if not plan_tasks(run):
        return []  # no Plan to judge against; the plan check requires one before this step
    named = [f for task in tasks for f in task["files"]]
    where = "; ".join(f"'{t['task'][:60]}': {', '.join(t['files'])}" for t in tasks) or "no task names a test file"
    return [f"{ac}: its test {rel} is not in a file the Plan's tests task names ({where}). Write it where the Plan "
            "says, or escalate to change the Plan."
            for ac in criteria for rel in traits.get(ac, [])
            if rel in changed and not any(glob_match(rel, pattern) or rel == pattern for pattern in named)]


def test_regions(text: str, marker: re.Pattern, declaration: re.Pattern | None) -> list[tuple[str, str]]:
    """
    Each criterion marker with the code it tags: from the marker to the next one. With a `declaration`
    pattern (a language whose tags sit above a method), a marker stacked on another — two criteria on
    one test — takes the next region holding a declaration; without one, every region is a test.
    """
    found = list(marker.finditer(text))
    chunks = [text[m.end():found[i + 1].start() if i + 1 < len(found) else len(text)] for i, m in enumerate(found)]
    regions = []
    for i, m in enumerate(found):
        body = next((chunk for chunk in chunks[i:] if declaration.search(chunk)), None) if declaration else chunks[i]
        if body is not None:
            regions.append((m.group(1), declaration.sub(" ", body, count=1) if declaration else body))
    return regions


def vacuous_test_problems(root: Path, config: Config, traits: dict[str, list[str]], criteria: list[str],
                          changed: set[str]) -> list[str]:
    """
    A criterion's test that asserts nothing about the product: once the declaration, comments and
    string literals are gone, its code names only words the adapter's `vacuous_words` calls trivial —
    keywords and the assertion library's own. `true.ShouldBeTrue()` passed as a guard's test (the dry
    run's F-17). Cheap and deliberately narrow: a test that names anything else is not judged here.
    With [[stacks]], each stack's tests are read with that stack's own words and markers.
    """
    if is_multi_stack(config):
        return [problem for stack in stacks(config)
                for problem in vacuous_test_problems(root, stack.config, {
                    ac: [rel for rel in files if under(rel, stack.root)] for ac, files in traits.items()}, criteria, changed)]
    settings = adapter(config)
    trivial = settings.get("vacuous_words")
    if not trivial:
        return []
    trivial_word = re.compile(trivial)
    marker = re.compile(settings["criterion_marker"])
    declaration = re.compile(settings["test_declaration"]) if settings.get("test_declaration") else None
    comments = re.compile(settings.get("comment", r"//[^\n]*|/\*.*?\*/|#[^\n]*"), re.DOTALL)
    strings = re.compile(r"@?\$?\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'")
    wanted = set(criteria)
    problems = []
    for rel in sorted({rel for ac in criteria for rel in traits.get(ac, []) if rel in changed}):
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        for ac, body in test_regions(text, marker, declaration):
            code = strings.sub(" ", comments.sub(" ", body))
            words = set(re.findall(r"\b[A-Za-z_]\w*", code))
            if ac in wanted and not any(not trivial_word.fullmatch(word) for word in words):
                problems.append(f"{ac}: its test in {rel} asserts nothing about the product — it names only "
                                f"literals and assertions ({' '.join(sorted(words)) or 'nothing'}). Test what the "
                                "criterion says, through the code it is about.")
    return problems


def check_tests_red(root: Path, config: Config, run: Run) -> Outcome:
    criteria = [ac for ac, _ in acceptance_criteria(run)]
    traits = ac_traits(root, config)
    untested = [ac for ac in criteria if ac not in traits]
    if untested:
        return Outcome(False, [f"No test carries [Trait(\"AC\", \"{ac}\")]." for ac in untested])
    require_clean(root, config)
    # Before anything runs: where the criteria's tests are, and whether they test anything (F-17).
    written = set(changed_paths(root, run.record["base"]))
    placed = misplaced_test_problems(run, config, traits, criteria, written) + \
        vacuous_test_problems(root, config, traits, criteria, written)
    if placed:
        return Outcome(False, placed)
    # A test locked earlier in this run stays locked across a reopen; one that changed without a
    # person's `factory unlock` fails here, before anything is re-proven.
    held = lock_problems(root, run)
    if held:
        return Outcome(False, held)
    guards = guard_criteria(run)
    carried = carried_red(root, run, criteria, guards, traits)

    # Each stack the run reaches builds in its own root; without [[stacks]], the one build, as ever.
    built: dict[str, tuple[Stack, Execution, Path]] = {}
    for stack in run_stacks(root, config, run, [rel for ac in criteria for rel in traits[ac]]):
        execution, log = build(root, stack.config, run, "tests")
        built[stack.name] = (stack, execution, log)
    for stack, execution, log in built.values():
        if execution.exit != 0 and not missing_symbol_only(execution.output, stack.config):
            record_check(root, run, "tests", build_check_name(stack), False, execution, log)
            codes = compile_errors(execution.output, stack.config)
            return Outcome(False, [f"{stack.name + ': ' if stack.name else ''}The build fails for reasons other than "
                                   f"a missing symbol ({', '.join(codes) or 'see the log'}). Log: {log}"])
    # Recorded like every other check, so its time shows in the run file (it went unseen before).
    for stack, execution, log in built.values():
        if execution.exit == 0:
            record_check(root, run, "tests", build_check_name(stack), True, execution, log)
    # A failed build is red proof only for a criterion whose OWN test files lack the symbol. One bad
    # reference elsewhere used to label every criterion red (the review's MEDIUM-1). The others could
    # not run — in .NET one missing symbol stops the whole test project — so they are recorded as
    # NOT PROVEN, where the Record, the PR body and the reviewer all show it, never as red. With
    # [[stacks]], that holds per stack: a criterion whose stack built runs its tests.
    missing = {name: entry for name, entry in built.items() if entry[1].exit != 0}
    errors = {name: "\n" + files_missing_a_symbol(execution.output, stack.config)
              for name, (stack, execution, _) in missing.items()}

    def own_error(files: list[str], name: str) -> bool:
        stack, lines = missing[name][0], errors[name]
        return any(f"/{rel}(" in lines or f"\n{rel}(" in lines or f"\n{within_stack(rel, stack)}(" in lines
                   for rel in files)

    def stack_name(rel: str) -> str:
        stack = stack_of(rel, config)
        return stack.name if stack else ""

    red: dict[str, str] = dict(carried)
    problems = []
    for ac in criteria:
        if ac in carried:
            if not any(stack_name(rel) in missing for rel in traits[ac]):
                record_check(root, run, "tests", f"red {ac} (carried over)", True, None, None)
            continue
        guard = ac in guards
        parts = []
        for name in sorted({stack_name(rel) for rel in traits[ac]}):
            files = [rel for rel in traits[ac] if stack_name(rel) == name]
            if name in missing:
                parts.append("guard — cannot run before the build (missing symbol)" if guard else
                             "does not compile yet — missing symbol" if own_error(files, name) else NOT_PROVEN)
                continue
            stack_config = built[name][0].config if name in built else config
            for project in sorted({test_project(rel, config) for rel in files}):
                check = f"{'guard' if guard else 'red'} {ac}"
                evidence = check.replace(" ", "-") + (f"-{project.replace('/', '-')}" if name else "")
                test_execution, counts, test_log = run_tests(root, config, run, "tests", evidence, project,
                                                             adapter(stack_config)["criterion_filter"].format(ac=ac),
                                                             **tagged(stack_config, [ac]))
                ran = bool(counts) and counts["executed"] > 0
                if guard:
                    # A guard must hold before the change: it pins behaviour the change must not break.
                    ok = ran and counts["failed"] == 0 and counts["notExecuted"] == 0
                else:
                    ok = ran and counts["failed"] > 0
                record_check(root, run, "tests", check, ok, test_execution, test_log, counts)
                if not ran:
                    problems.append(f"{ac}: no test executed in {project} — is Docker running for integration tests?")
                elif guard and not ok:
                    problems.append(f"{ac} is a guard, but its tests do not pass before the build ({counts}) — "
                                    "a guard pins behaviour that already holds.")
                elif not guard and not ok and run.record.get("previous"):
                    problems.append(f"{ac}: its tests changed after they were proven red, so the proof cannot carry "
                                    "over — and after the fix they cannot be red again. Restore them, or escalate.")
                elif not guard and not ok:
                    problems.append(f"{ac}: its tests pass before the build — they do not test the change. If it "
                                    "pins behaviour that must not break, it should be a guard: that changes the "
                                    "approved Spec, so escalate — a person reopens the spec and approves it again.")
                else:
                    parts.append(f"guard — {counts['passed']} passing" if guard else f"{counts['failed']} failing")
        proven = [part for part in parts if part != NOT_PROVEN]
        if parts:
            red[ac] = proven[-1] if proven else NOT_PROVEN
    if problems:
        return Outcome(False, problems)
    if missing:
        changes = [ac for ac in criteria if ac not in guards]
        if all(red.get(ac) == NOT_PROVEN for ac in changes):
            # A missing symbol in a helper no criterion owns proves none of them (the re-review's #7).
            for stack, execution, log in missing.values():
                record_check(root, run, "tests", build_check_name(stack), False, execution, log)
            logs = ", ".join(str(log) for _, _, log in missing.values())
            return Outcome(False, ["No change criterion is proven red: the build fails only on a missing symbol "
                                   "none of their own tests references. Make them compile against today's code "
                                   f"and fail, or escalate. Log: {logs}"])
        for stack, execution, log in missing.values():
            record_check(root, run, "tests", build_check_name(stack), True, execution, log)
    if missing:
        red = {ac: red[ac] for ac in criteria if ac in red}  # criterion order, as a failed build always wrote it
    if missing and len(missing) == len(built):
        by = "missing symbol"
    elif missing:
        by = (f"missing symbol in {', '.join(sorted(missing))}; failing tests in "
              f"{', '.join(sorted(set(built) - set(missing)))}")
    else:
        by = "failing tests" + (f"; {len(carried)} carried over" if carried else "")

    # Every test file of the criteria's test projects, not only those carrying a criterion:
    # a shared helper left unlocked let a reopened run make a locked test pass without the
    # fix (the re-review's HIGH-3). New files stay addable; an existing one needs an unlock.
    locked = {rel for ac in criteria for rel in traits[ac]}
    test_patterns = adapter(config).get("test_files", [])
    projects = {test_project(rel, config) for rel in locked}
    locked.update(rel for rel in project_test_files(root, config) if test_project(rel, config) in projects)
    locked.update(p for p in changed_paths(root, run.record["base"])
                  if any(glob_match(p, pattern) for pattern in test_patterns))
    # Existing locks are kept as they were: a lock is only ever added to here, never re-taken.
    existing = run.record.get("lock", {})
    run.record["lock"] = {**{rel: content_hash(root / rel) for rel in sorted(locked) if (root / rel).exists()},
                          **existing}
    run.record["red"] = {"head": head(root), "by": by, "acs": red}
    return Outcome(True, [f"Red proven ({by}); {len(run.record['lock'])} test file(s) locked."])


def carried_red(root: Path, run: Run, criteria: list[str], guards: set[str],
                traits: dict[str, list[str]]) -> dict[str, str]:
    """
    Change criteria whose red-before-build proof still stands after the run was reopened at
    tests: proven red before, and every file carrying them byte-for-byte what was locked
    then. After the fix a change criterion cannot be red again, so this is the only way a
    reviewer's request for more guard tests can be met without weakening the proof.
    """
    previous = run.record.get("previous") or {}
    old_lock, old_red = previous.get("lock", {}), (previous.get("red") or {}).get("acs", {})
    carried = {}
    for ac in criteria:
        if ac in guards or ac not in old_red or old_red[ac].startswith(("guard", NOT_PROVEN)):
            continue
        files = traits.get(ac, [])
        if files and all(rel in old_lock and lock_holds(root / rel, old_lock[rel])
                         for rel in files):
            carried[ac] = f"{old_red[ac]} — carried over, its tests unchanged"
    return carried


def tagged(config: Config, criteria: list[str]) -> dict:
    """run_tests' `criteria`, for an adapter that counts a filtered run's own tests only (`filtered_results`)."""
    return {"criteria": criteria} if adapter(config).get("filtered_results") else {}


def criteria_filter(config: Config, criteria: list[str]) -> str:
    """One filter selecting every criterion's tests — `AC=a|AC=b` for dotnet, `a,b` for the pytest plugin."""
    settings = adapter(config)
    return settings.get("criteria_join", "|").join(settings["criterion_filter"].format(ac=ac) for ac in criteria)


def all_green(counts: dict | None) -> bool:
    return bool(counts) and counts["executed"] > 0 and counts["failed"] == 0 and counts["notExecuted"] == 0


def green_run(execution: Execution, counts: dict | None) -> bool:
    """
    Green counts from a runner that exited cleanly. A test host that crashed after writing a passing results
    file exits non-zero, and the counts alone called it green (the 15 Oct review's #3).
    """
    return execution.exit == 0 and all_green(counts)


def green_criteria(root: Path, config: Config, run: Run, step: str) -> list[str]:
    """
    Every criterion's tests pass. One run per test project for all its criteria — R-002 spent
    a test run, and a database start, on each of eight. Only when that run fails does the
    referee go criterion by criterion, to say which one broke.
    """
    problems = []
    traits = ac_traits(root, config)
    by_project: dict[str, list[str]] = {}
    for ac, _ in acceptance_criteria(run):
        for project in sorted({test_project(rel, config) for rel in traits.get(ac, [])}):
            by_project.setdefault(project, []).append(ac)
    for project, criteria in by_project.items():
        label = project.rsplit("/", 1)[-1]
        stack_config = project_stack(project, config)[0]  # the project's stack: its filter syntax
        execution, counts, log = run_tests(root, config, run, step, f"green-{label}", project,
                                           criteria_filter(stack_config, criteria), **tagged(stack_config, criteria))
        if green_run(execution, counts):
            for ac in criteria:
                record_check(root, run, step, f"green {ac}", True, execution, log, counts)
            continue
        for ac in criteria:
            execution, counts, log = run_tests(root, config, run, step, f"green-{ac}", project,
                                               criteria_filter(stack_config, [ac]), **tagged(stack_config, [ac]))
            record_check(root, run, step, f"green {ac}", green_run(execution, counts), execution, log, counts)
            if not green_run(execution, counts):
                exited = f"; the runner exited {execution.exit}" if execution.exit != 0 and all_green(counts) else ""
                problems.append(f"{ac}: its tests do not all pass in {project} ({counts or 'no results'}{exited}).")
    return problems


def stale_mentions(root: Path, config: Config, run: Run) -> list[str]:
    """
    A file under `[requirements] keep_in_step` that names the run's item, and that the run did not
    change: a run fixed a defect and changed its error code, and an API request collection still
    said the defect was open — no check and neither judge noticed (the dry run's F-15). The build step may
    write these files, so the build session can bring them in line before it hands over.
    """
    item = run.record.get("item", "")
    globs = config.raw.get("requirements", {}).get("keep_in_step", [])
    if not re.fullmatch(r"[DQ]\d+", item) or not globs:
        return []
    mention = re.compile(rf"\b{item}\b")
    named = sorted({path.relative_to(root).as_posix() for pattern in globs for path in root.glob(pattern)
                    if path.is_file() and mention.search(path.read_text(encoding="utf-8", errors="replace"))})
    if not named:
        return []
    changed = set(changed_paths(root, run.record["base"]))
    return [f"{path} names {item}, and this run has not changed it: bring what it says about {item} in line "
            "with the fix (an error code, a message, \"open\"), then commit." for path in named if path not in changed]


def check_build(root: Path, config: Config, run: Run) -> Outcome:
    problems = lock_problems(root, run) or stale_mentions(root, config, run)
    if problems:
        return Outcome(False, problems)
    require_clean(root, config)
    # Every stack the run reaches builds: its changes, its locked tests and its criteria's tests.
    traits = ac_traits(root, config)
    for stack in run_stacks(root, config, run, [rel for ac, _ in acceptance_criteria(run) for rel in traits.get(ac, [])]):
        execution, log = build(root, stack.config, run, "build")
        record_check(root, run, "build", build_check_name(stack), execution.exit == 0, execution, log)
        if execution.exit != 0:
            return Outcome(False, [f"{stack.name + ': ' if stack.name else ''}The build fails. Log: {log}"])
    problems = green_criteria(root, config, run, "build")
    return Outcome(not problems, problems or ["Build passes and every criterion's tests pass."])


def check_integrate(root: Path, config: Config, run: Run) -> Outcome:
    problems = lock_problems(root, run)
    if problems:
        return Outcome(False, problems)
    require_clean(root, config)
    report = impact(root, config, run.record["base"])
    suites = []
    # Each stack the run reaches: its build, its always-run suites and its integration suite, in its root.
    for stack in run_stacks(root, config, run):
        execution, log = build(root, stack.config, run, "integrate")
        record_check(root, run, "integrate", build_check_name(stack), execution.exit == 0, execution, log)
        if execution.exit != 0:
            return Outcome(False, [f"{stack.name + ': ' if stack.name else ''}The build fails. Log: {log}"])
        commands = stack.config.raw["commands"]
        # A suite's project is named from the repository's root, as test_project names it.
        here = [(prefixed(stack.root, [project])[0] if project not in (".", "") else (stack.root or project),
                 None, project.rsplit("/", 1)[-1] if not stack.name else f"{stack.name} {project.rsplit('/', 1)[-1]}")
                for project in commands.get("test_projects_always", [])]
        integration = commands.get("integration_project") or ""
        in_process = commands.get("integration_filter") or ""
        if stack.root and integration:
            integration = prefixed(stack.root, [integration])[0]
        label = f"{stack.name} IntegrationTests" if stack.name else "IntegrationTests"
        if not integration:
            pass  # a stack with no separate integration suite: the always-run suites are the whole story
        elif report["cross_cutting"]:
            here.append((integration, in_process, f"{label} (all in-process — cross-cutting)"))
        elif report["modules"]:
            # In the stack's own filter grammar (both_filters): it was VSTest's for every stack (the kit audit).
            settings = stack.config.raw.get("adapter", {})
            here.append((integration, both_filters(settings, in_process, modules_filter(settings, report["modules"])),
                         f"{label} (touched modules)"))
        suites += here
    for project, test_filter, name in suites:
        test_execution, counts, test_log = run_tests(root, config, run, "integrate", project.rsplit("/", 1)[-1],
                                                     project, test_filter)
        ok = green_run(test_execution, counts)
        record_check(root, run, "integrate", name, ok, test_execution, test_log, counts)
        if not ok:
            exited = f", exit {test_execution.exit}" if test_execution.exit != 0 and all_green(counts) else ""
            problems.append(f"{name}: {counts or 'no results'}{exited} — failed, skipped, nothing ran or the runner "
                            f"did not exit cleanly. Log: {test_log}")
    return Outcome(not problems, problems or ["Unit, architecture and the touched modules' integration tests pass."])


def diff_sizes(root: Path, config: Config, base: str) -> tuple[int, int]:
    """
    Lines changed on the branch — (product, tests). Tests are the adapter's `test_files`: a
    reviewer asking for more tests should not push a run over the budget that exists to keep
    product changes reviewable (R-002: 942 lines of change, then 300 of requested tests).
    """
    excluded = config.raw["budget"]["excluded"]
    test_patterns = adapter(config).get("test_files", [])
    product = tests = 0
    # The branch's own commits, as changed_paths counts them; a line changed twice counts twice.
    for args in (("log", *own_commits(root, base), "--format=", "--numstat"),
                 ("diff", "--numstat", "HEAD")):
        for line in git_strict(root, *args).splitlines() + (merge_resolutions(root, base) if args[0] == "log" else []):
            added, deleted, path = (line.split("\t") + ["", "", ""])[:3]
            if not added.isdigit() or any(glob_match(path, pattern) for pattern in excluded):
                continue
            if any(glob_match(path, pattern) for pattern in test_patterns):
                tests += int(added) + int(deleted)
            else:
                product += int(added) + int(deleted)
    return product, tests


def diff_size(root: Path, config: Config, base: str) -> int:
    return sum(diff_sizes(root, config, base))


def ownership_problems(root: Path, config: Config, run: Run) -> list[str]:
    owned = [pattern for task in plan_tasks(run) for pattern in task["files"]]
    always = config.raw["ownership"]["always"] + [f"{config.runs_dir}/**"]

    def covered(path: str) -> bool:
        return any(glob_match(path, p) or path == p or (p.endswith("/") and path.startswith(p))
                   for p in owned + always)

    stray = [path for path in changed_paths(root, run.record["base"]) if not covered(path)]
    return [f"{path} is changed but no Plan task owns it." for path in stray]


def check_verify(root: Path, config: Config, run: Run) -> Outcome:
    problems = lock_problems(root, run)
    require_clean(root, config)
    # The project's verify commands at its root; with [[stacks]], then each reached stack's own (lint,
    # type-check) in that stack's root.
    commands = [("", None, command) for command in config.raw.get("commands", {}).get("verify", [])]
    if is_multi_stack(config):
        commands += [(stack.name, stack, command) for stack in run_stacks(root, config, run)
                     for command in stack.config.raw["commands"].get("verify", [])]
    for prefix, stack, command in commands:
        words = command.split()
        # `npm run lint` is named for its script, not for `run`.
        word = words[2] if len(words) > 2 and words[1] in ("run", "exec") else words[1] if len(words) > 1 else command
        name = word.rsplit("/", 1)[-1]
        name = f"{prefix} {name}" if prefix else name
        log = evidence_path(root, config, run, f"verify-{name.replace(' ', '-')}.log")
        execution = execute(root, to_argv(command), log, cwd=stack_cwd(root, stack.config) if stack else None)
        record_check(root, run, "verify", name, execution.exit == 0, execution, log)
        if execution.exit != 0:
            problems.append(f"{prefix + ': ' if prefix else ''}{command} failed. Log: {log}")
    tier = run.record["tier"]["value"]
    budget = config.raw["budget"][tier]
    test_budget = config.raw["budget"].get("tests", {}).get(tier, budget)
    size, test_size = diff_sizes(root, config, run.record["base"])
    if size > budget:
        problems.append(f"The product change is {size} lines against a {tier} budget of {budget}; split it.")
    if test_size > test_budget:
        problems.append(f"The tests are {test_size} lines against a {tier} test budget of {test_budget}; split the run.")
    problems += ownership_problems(root, config, run)
    traits = ac_traits(root, config)
    problems += [f"{ac}: no test carries its trait." for ac, _ in acceptance_criteria(run) if ac not in traits]
    if not problems:
        # Last, and only on an otherwise clean change: the audit is an agent's time and money. The
        # reviewer reads the same commit at the same time, so the two judges cost one wait, not two.
        problems += audit_beside_review(root, config, run)
    audited = (run.record.get("audit") or {}).get("verdict") if spec_requirement_ids(run, config) else None
    return Outcome(not problems, problems or [f"Verify passes; the product change is {size} of {budget} lines, "
                                              f"the tests {test_size} of {test_budget}"
                                              + (f"; requirements audit {audited}." if audited else ".")])


def bitbucket_client(root: Path, personal: bool = False) -> bitbucket.Bitbucket:
    """The seam tests replace. `personal`: the person's own stored credential, never BITBUCKET_TOKEN."""
    PROGRESS.say("🌐 Talking to Bitbucket" + (" in your name" if personal else ""))
    try:
        return bitbucket.Bitbucket.for_repository(root, personal)
    except bitbucket.BitbucketError as error:
        raise FactoryError(str(error)) from error


def check_bitbucket_approval(root: Path, config: Config, run: Run) -> Outcome:
    """The Merge gate: approved in Bitbucket, by someone who did not write it, for the commit checked here."""
    pr = run.record.get("pr")
    if not pr:
        return Outcome(False, ["No pull request is linked. Open it, then `factory link-pr <id>`."])
    settings = config.raw.get("bitbucket", {})
    try:
        pull_request = bitbucket_client(root).pull_request(pr["id"])
    except bitbucket.BitbucketError as error:
        return Outcome(False, [f"Could not read PR #{pr['id']}: {error}"])
    problems = []
    source = pull_request.get("source") or {}
    if (source.get("branch") or {}).get("name") != run.record["branch"]:
        problems.append(f"PR #{pr['id']} is from {(source.get('branch') or {}).get('name')}, not {run.record['branch']}.")
    pr_commit = (source.get("commit") or {}).get("hash", "")
    local = head(root)
    if not pr_commit or not local.startswith(pr_commit):
        problems.append(f"PR #{pr['id']} is at {pr_commit[:10] or '?'}, this checkout at {local[:10]} — "
                        "an approval counts only for the commit that was checked. Push, then check again.")
    # The evidence must describe the PR as it stands: a commit after review would pass on older proof.
    if pr_commit:
        problems += merge_evidence_problems(root, config, run, pr_commit, local)
    # An approval given before the PR's commit arrived approved something else: Bitbucket keeps an
    # approval across a push, so only one made after the commit reached the PR counts.
    try:
        arrived = bitbucket.commit_arrived(bitbucket_client(root).activity(pr["id"]), pr_commit)
    except bitbucket.BitbucketError as error:
        return Outcome(False, problems + [f"Could not read PR #{pr['id']}'s activity: {error}"])
    if not arrived:
        # Fail closed: without Bitbucket's time for the commit, no approval can be shown to follow it.
        return Outcome(False, problems + [f"PR #{pr['id']}'s activity does not show commit {pr_commit[:10]} "
                                          "arriving, so no approval can be shown to be of it. Push, then check again."])
    excluded = set(settings.get("excluded_identities", [])) | set(pr.get("not_approvers", []))
    names = bitbucket.approvers(pull_request, excluded, since=arrived)
    needed = settings.get("approvals", {}).get(run.record["tier"]["value"], 1)
    if len(names) < needed:
        author = (pull_request.get("author") or {}).get("display_name", "the author")
        problems.append(f"PR #{pr['id']} needs {needed} approval(s) from someone other than {author} and the "
                        f"factory; it has {len(names)}{': ' + ', '.join(names) if names else ''}.")
    if problems:
        return Outcome(False, problems)
    return Outcome(True, [f"PR #{pr['id']} approved at {pr_commit[:10]} by {', '.join(names)}."], ", ".join(names))


def github_client(root: Path) -> github.GitHub:
    """The seam tests replace."""
    PROGRESS.say("🌐 Talking to GitHub")
    try:
        return github.GitHub.for_repository(root)
    except github.GitHubError as error:
        raise FactoryError(str(error)) from error


def check_github_approval(root: Path, config: Config, run: Run) -> Outcome:
    """The Merge gate on GitHub: approved by someone who did not write it, on the commit checked here."""
    pr = run.record.get("pr")
    if not pr:
        return Outcome(False, ["No pull request is linked. Open it, then `factory link-pr <id>`."])
    settings = config.raw.get("github", {})
    client = github_client(root)
    try:
        pull_request = client.pull_request(pr["id"])
        reviews = client.reviews(pr["id"])
    except github.GitHubError as error:
        return Outcome(False, [f"Could not read PR #{pr['id']}: {error}"])
    problems = []
    if pull_request["branch"] != run.record["branch"]:
        problems.append(f"PR #{pr['id']} is from {pull_request['branch']}, not {run.record['branch']}.")
    pr_commit = pull_request["commit"]
    local = head(root)
    if not pr_commit or not local.startswith(pr_commit[:10]):
        problems.append(f"PR #{pr['id']} is at {pr_commit[:10] or '?'}, this checkout at {local[:10]} — "
                        "an approval counts only for the commit that was checked. Push, then check again.")
    if pr_commit:
        problems += merge_evidence_problems(root, config, run, pr_commit, local)
    excluded = set(settings.get("excluded_identities", [])) | set(pr.get("not_approvers", []))
    names = github.approvers(pull_request, reviews, excluded)
    needed = settings.get("approvals", {}).get(run.record["tier"]["value"], 1)
    if len(names) < needed:
        problems.append(f"PR #{pr['id']} needs {needed} approval(s) of commit {pr_commit[:10]} from someone other than "
                        f"{pull_request['author'] or 'the author'} and the factory; it has {len(names)}"
                        f"{': ' + ', '.join(names) if names else ''}.")
    if problems:
        return Outcome(False, problems)
    return Outcome(True, [f"PR #{pr['id']} approved at {pr_commit[:10]} by {', '.join(names)}."], ", ".join(names))


def check_local_approval(root: Path, config: Config, run: Run) -> Outcome:
    """
    The Merge gate of a project with no pull requests (no remote, or none the referee reads): a person's
    `factory approve merge`, recorded for the commit they read, held to the same evidence rules as a PR.
    """
    approval = run.record.get("merge_approval")
    if not approval:
        return Outcome(False, ["Waiting for a person: `factory approve merge` from their own terminal, after "
                               "reading the run's evidence."])
    local = head(root)
    if not evidence_still_holds(root, config, approval["head"], local):
        return Outcome(False, [f"{approval['by']} approved the merge at {approval['head'][:10]}; the branch is now at "
                               f"{local[:10]} with more than run files changed. `factory approve merge` again."])
    problems = merge_evidence_problems(root, config, run, local, local)
    if problems:
        return Outcome(False, problems)
    return Outcome(True, [f"Merge approved by {approval['by']} at {approval['head'][:10]}."], approval["by"])


def evidence_still_holds(root: Path, config: Config, checked: str, pr_head: str, packaged: str = "") -> bool:
    """
    Evidence proven at `checked` still describes `pr_head` when nothing but run files changed
    between them: the review, accept and package steps rewrite the run file after the review,
    and the PR must carry it (AF-4) — so its commit always follows the evidence (the re-review's #9).
    With `packaged` (the commit the package step passed at), the package step's own book-keeping counts
    too: between the evidence and that commit, files its `writes` name may be added or changed — never
    removed — as it writes the run back into the defect log or the plan's Tasks (trial R-003: every change
    run's own package commit put its evidence out of date). After it, only run files.
    """
    if not checked:
        return False
    if pr_head.startswith(checked) or checked.startswith(pr_head):
        return True
    if git(root, "merge-base", "--is-ancestor", checked, pr_head)[0] != 0:
        return False
    runs = config.runs_dir + "/"
    if packaged and git(root, "merge-base", "--is-ancestor", checked, packaged)[0] == 0 \
            and git(root, "merge-base", "--is-ancestor", packaged, pr_head)[0] == 0:
        writes = next((s.writes for s in config.steps if s.name == "package"), ())
        status = git_strict(root, "diff", "--name-status", "--no-renames", checked, packaged).splitlines()
        for line in filter(None, status):
            kind, path = line.split("\t", 1)
            if not path.startswith(runs) and (kind not in ("A", "M") or not any(glob_match(path, w) for w in writes)):
                return False
        checked = packaged
    changed = git_strict(root, "diff", "--name-only", "--no-renames", checked, pr_head).splitlines()
    return all(path.startswith(runs) for path in changed if path)


def packaged_head(run: Run) -> str:
    """
    The commit a change run's package step passed at, kept by reopen when it clears that step: its write-back into
    the book (the defect log, the plan's Tasks) is book-keeping, not code (evidence_still_holds' `packaged`).
    """
    if record_kind(run.record) != CHANGE:
        return ""
    return ((run.record.get("steps") or {}).get("package") or {}).get("head") or run.record.get("packaged") or ""


def evidence_heads(run: Run) -> dict[str, str]:
    """
    The commit each evidence step last proved. A change run: integrate and verify from their checks,
    review from its verdict. A plan or book run has none of those steps: its commit evidence is the one
    its package step checked (its approved file is merge_evidence_problems' other half).
    """
    if record_kind(run.record) != CHANGE:
        return {"package": ((run.record.get("steps") or {}).get("package") or {}).get("head") or ""}
    heads = {}
    for step in ("integrate", "verify"):
        entries = [c for c in run.record.get("checks", []) if c["step"] == step]
        heads[step] = entries[-1]["head"] if entries else ""
    heads["review"] = (run.record.get("review") or {}).get("head") or ""
    return heads


# The gate whose approved file is a plan or book run's evidence at the merge gate.
EVIDENCE_GATE = {PLAN: PLAN_GATE, BOOK: BOOK_GATE, REVISION: REVISION_GATE}


def merge_evidence_problems(root: Path, config: Config, run: Run, pr_commit: str, local: str) -> list[str]:
    """
    Whether the run's evidence describes the PR's commit, by kind. A change run: integrate, verify and
    review proved it. A plan run: the plan its plan gate approved is the plan at the PR's commit, and
    its package step checked that commit. A book run: the same, with project.md and the book gate.
    Only run files may change after the evidence (evidence_still_holds).
    """
    problems = []
    gate = EVIDENCE_GATE.get(record_kind(run.record))
    if gate:
        entry = (run.record.get("gates") or {}).get(gate) or {}
        rel = entry.get("file") or gated_file(config, run, gate) or "its file"
        reopen = entry.get("checked_by") or gate
        if not entry.get("file_sha256"):
            problems.append(f"The {gate} has no approval of {rel} on record. A person approves it: "
                            f"`factory approve {gate.removesuffix('-gate')}`.")
        else:
            code, text = git(root, "show", f"{local}:{rel}")
            if code != 0 or text_digest(text) != entry["file_sha256"]:
                problems.append(f"{rel} at the PR's commit {pr_commit[:10]} is not what the {gate} approved. "
                                f"Reopen it: `factory reopen {reopen} --reason \"changed after its gate\"`, then "
                                "approve it again.")
    # A change run's package step writes the run back into the book after its evidence: that book-keeping counts.
    packaged = packaged_head(run)
    for evidence, checked in evidence_heads(run).items():
        if not evidence_still_holds(root, config, checked, local, packaged):
            problems.append(f"The {evidence} step's evidence is for {checked[:10] or '?'}, not the PR's "
                            f"{pr_commit[:10]}. Reopen it: `factory reopen {evidence} --reason \"commits after it\"`.")
    return problems


# A verdict line holds one verdict and nothing else: the template's `Verdict: READY | NOT READY`, echoed
# back, is not a verdict (the harness review: it read as READY ahead of a later NOT READY).
VERDICT = re.compile(r"^\s*\**Verdict:\**\s*\**(READY|NOT READY)\**\s*\.?\s*$", re.IGNORECASE | re.MULTILINE)
# The checklist's verdict block (review-checklist.md) uses its severities; a run file from before it
# used the referee's own words. Both read the same: CRITICAL and HIGH block.
SEVERITY = {"critical": "blocking", "high": "blocking", "medium": "major", "low": "minor", "info": "nit"}
FINDING = re.compile(r"^\s*-\s*\[(critical|high|medium|low|info|blocking|major|minor|nit)\]\s*\[(high|medium|low)\]\s*(.+)$",
                     re.IGNORECASE | re.MULTILINE)


def one_verdict(pattern: re.Pattern, text: str) -> str | None:
    """
    The report's verdict, or None when it has none — or more than one that disagree: a report that
    says both is not a verdict, and the step fails rather than pick one.
    """
    found = {match.group(1).upper() for match in pattern.finditer(text)}
    return found.pop() if len(found) == 1 else None


def parse_review(text: str) -> tuple[str | None, list[dict]]:
    verdict = one_verdict(VERDICT, text)
    findings = [{"severity": SEVERITY.get(m.group(1).lower(), m.group(1).lower()), "confidence": m.group(2).lower(),
                 "what": m.group(3).strip()}
                for m in FINDING.finditer(text)]
    return verdict, findings


def agent_turn(root: Path, config: Config, settings: dict, prompt: str) -> tuple[str, float, dict]:
    """A judging agent in a session of its own — never the working session. Its report, cost and tokens."""
    drive = config.raw["drive"]
    program = [sys.executable, drive["claude"]] if drive["claude"].endswith(".py") else \
        [shutil.which(drive["claude"]) or drive["claude"]]
    streaming = PROGRESS.enabled
    argv = [*program, "-p", "--output-format", "stream-json" if streaming else "json",
            *(["--verbose"] if streaming else []), "--agent", settings["agent"],
            "--permission-mode", "dontAsk", "--setting-sources", drive["setting_sources"], "--strict-mcp-config",
            "--max-budget-usd", str(settings["max_budget_usd"]),
            "--allowedTools", *settings["allowed_tools"]]
    if settings.get("model"):
        argv += ["--model", settings["model"]]
    if settings.get("effort"):
        argv += ["--effort", settings["effort"]]
    who = JUDGES.get(settings["agent"], f"🧐 The {settings['agent']} agent (a separate AI)")
    PROGRESS.say(f"   {who} — it never saw the change being written")
    with PROGRESS.waiting(f"{who} at work"):
        done = launch_claude(argv, root, prompt, watch_session(root) if streaming else None)
    try:
        data = session_result(done.stdout)
    except ValueError as error:
        raise FactoryError(f"The {settings['agent']} agent did not answer in JSON (exit {done.returncode}).") from error
    refuse_on_usage_limit(data, f"the {settings['agent']} agent")
    return str(data.get("result") or ""), float(data.get("total_cost_usd") or 0), session_tokens(data)


def change_commits(root: Path, run: Run) -> str:
    return " ".join(own_commits(root, run.record["base"]))


def review_turn(root: Path, config: Config, run: Run) -> tuple[str, float, dict]:
    prompt = (f"Review factory run {run.id}. Its file is {run.path.relative_to(root).as_posix() if run.path else ''}. "
              f"The change is every commit the pull request would bring: `git log -p {change_commits(root, run)}`, "
              "plus any uncommitted work. Work merged in from the base branch is already there — do not review it; "
              "anything else merged in ships with this change, so review it. Report in your fixed shape.")
    return agent_turn(root, config, config.raw["review"], prompt)


def keep_judgement(run: Run, agent: str, verdict: str | None, at: str, text: str) -> None:
    """Every judge's verdict stays in the Record: a later one never hides an earlier failure."""
    run.record.setdefault("judgements", []).append(
        {"agent": agent, "verdict": verdict, "head": at, "at": utc_now(), "sha256": sha256_text(text)})


def review_outcome(verdict: str | None, findings: list[dict], note: str = "") -> Outcome:
    blocking = [f for f in findings if f["severity"] == "blocking" and f["confidence"] != "low"]
    if verdict is None:
        return Outcome(False, ["The reviewer's report has no single 'Verdict:' line — none, or two that disagree; "
                               "see the Review section."])
    if verdict != "READY" or blocking:
        return Outcome(False, [f"Reviewer: {verdict}.{note}"] + [f"[{f['severity']}/{f['confidence']}] {f['what']}"
                                                                for f in findings if f["severity"] in ("blocking", "major")])
    advisory = [f for f in findings if f["severity"] != "blocking"]
    return Outcome(True, [f"Reviewer: READY, {len(advisory)} advisory finding(s) — read the Review section.{note}"])


def check_review(root: Path, config: Config, run: Run) -> Outcome:
    """READY from the independent reviewer, and no blocking finding it is confident in."""
    require_clean(root, config)
    previous = run.record.get("review") or {}
    if previous.get("verdict") and previous.get("beside_audit") \
            and only_run_files_changed(root, config, previous.get("head", ""), head(root)):
        # Asked at verify, beside the spec checker, on this code — at most the run file has been committed
        # since, which is not code: the same review, not a second roll.
        return review_outcome(previous["verdict"], previous.get("findings", []),
                              f" The reviewer read `{previous['head'][:10]}` at verify, beside the spec checker.")
    if previous.get("verdict") and previous.get("head") == head(root):
        # Nothing changed since its verdict: asking again would only roll the dice (the fourth review).
        return review_outcome(previous["verdict"], previous.get("findings", []),
                              f" Nothing has changed since that verdict at `{head(root)[:10]}`; change the code to be "
                              "reviewed again.")
    text, usd, tokens = review_turn(root, config, run)
    return record_review(root, config, run, text, usd, tokens)


def record_review(root: Path, config: Config, run: Run, text: str, usd: float, tokens: dict) -> Outcome:
    """The reviewer's report, kept: its verdict and findings in the Record, its text as evidence."""
    add_agent_cost(run, config.raw["review"]["agent"], usd, tokens)
    verdict, findings = parse_review(text)
    blocking = [f for f in findings if f["severity"] == "blocking" and f["confidence"] != "low"]
    keep_judgement(run, config.raw["review"]["agent"], verdict, head(root), text)
    run.sections["Review"] = (f"*Written by the referee from the `{config.raw['review']['agent']}` agent's report — "
                              f"a session of its own, at `{head(root)[:10]}`.*\n\n{text.strip()}")
    run.record["review"] = {"verdict": verdict, "findings": findings, "at": utc_now(), "head": head(root),
                            "usd": round(usd, 4), "sha256": sha256_text(text)}
    # The report is the review's evidence: kept like any check's log, and hashed.
    report = evidence_path(root, config, run, "review-report.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text, encoding="utf-8")
    record_check(root, run, "review", f"reviewer: {verdict or 'no verdict'}", verdict == "READY" and not blocking,
                 None, report)
    stamp_section(run, "Review", "review")
    return review_outcome(verdict, findings)


AUDIT_VERDICT = re.compile(r"^\s*\**Verdict:\**\s*\**(CONSISTENT|DIVERGES)\**\s*\.?\s*$", re.IGNORECASE | re.MULTILINE)
AUDIT_STATUSES = ("VERIFIED", "PARTIAL", "MISSING", "CONTRADICTED", "UNTRACED", "NOT_APPLICABLE", "UNVERIFIED")


def spec_requirement_ids(run: Run, config: Config) -> list[str]:
    """Every requirement id the Spec names, criteria first, each once."""
    ids = [ac for ac, _ in acceptance_criteria(run) if is_requirement_id(ac, config)]
    return list(dict.fromkeys(ids + requirement_ids_in(run.section_text("Spec"), config)))


def parse_audit(text: str, config: Config) -> tuple[str | None, dict[str, str]]:
    verdict = one_verdict(AUDIT_VERDICT, text)
    row = re.compile(rf"^\s*-\s*\**(?P<id>{re.escape(requirement_prefix(config))}-{SECTION_AND_RULE})\**\s*[:—–-]+\s*"
                     rf"\**(?P<status>{'|'.join(AUDIT_STATUSES)})\b", re.MULTILINE)
    return verdict, {m.group("id"): m.group("status") for m in row.finditer(text)}


def audit_problems(run: Run, config: Config, verdict: str | None, rows: dict[str, str]) -> list[str]:
    problems = []
    if verdict is None:
        problems.append("The requirements audit has no single 'Verdict:' line — none, or two that disagree; "
                        "see its section.")
    for ac in (ac for ac, _ in acceptance_criteria(run) if is_requirement_id(ac, config)):
        if rows.get(ac) != "VERIFIED":
            problems.append(f"Requirements audit: {ac} is {rows.get(ac, 'not reported')} — a criterion must be VERIFIED.")
    if verdict == "DIVERGES" and not problems:
        problems.append("Requirements audit: DIVERGES — "
                        + ", ".join(f"{i} {s}" for i, s in rows.items() if s in ("PARTIAL", "MISSING", "CONTRADICTED")) + ".")
    return problems


def only_run_files_changed(root: Path, config: Config, earlier: str, now: str) -> bool:
    """Whether `now` differs from `earlier` only in the runs folder — the run file, its index — or not at all."""
    if not earlier:
        return False
    if earlier == now:
        return True
    code, changed = git(root, "diff", "--name-only", earlier, now)
    if code != 0:
        return False
    runs = config.runs_dir.rstrip("/") + "/"
    return all(path.startswith(runs) for path in changed.splitlines() if path)


def review_beside_audit(root: Path, config: Config, run: Run) -> tuple[threading.Thread, dict] | None:
    """
    Start the reviewer on this commit while the spec checker audits it: the two judges each have a session
    of their own and neither reads the other, so running them together keeps both verdicts and halves the
    wait (R-018: 2.6 minutes, one after the other). Only when the audit is really about to ask, and the
    reviewer has no verdict on this commit yet. The review step then reads that verdict, not a second one.
    """
    settings, review = config.raw.get("audit"), config.raw.get("review")
    ids = spec_requirement_ids(run, config)
    previous_audit, previous_review = run.record.get("audit") or {}, run.record.get("review") or {}
    if not settings or not review or not ids:
        return None
    if previous_audit.get("verdict") and previous_audit.get("head") == head(root) and previous_audit.get("ids") == ids:
        return None  # the audit is not asked again, so there is nothing to run beside
    if previous_review.get("verdict") and previous_review.get("head") == head(root):
        return None
    holder: dict = {"head": head(root)}

    def ask() -> None:
        try:
            holder["result"] = review_turn(root, config, run)
        except (FactoryError, OSError) as error:  # the review step asks again, and says why
            holder["error"] = error

    thread = threading.Thread(target=ask, daemon=True)
    thread.start()
    return thread, holder


def audit_beside_review(root: Path, config: Config, run: Run) -> list[str]:
    """
    The requirements audit, with the reviewer started beside it. The reviewer is joined and its verdict and cost kept
    whatever the audit does — an audit that raised left it running unjoined, and both were lost (the 15 Oct review's #9).
    """
    reviewer = review_beside_audit(root, config, run)
    try:
        return audit_requirements(root, config, run)
    finally:
        keep_review_beside_audit(root, config, run, reviewer)


def keep_review_beside_audit(root: Path, config: Config, run: Run,
                             started: tuple[threading.Thread, dict] | None) -> None:
    if started is None:
        return
    thread, holder = started
    with PROGRESS.waiting("🧐 Waiting for the reviewer, who started beside the spec checker"):
        thread.join()
    if "result" not in holder or holder["head"] != head(root):
        return
    record_review(root, config, run, *holder["result"])
    run.record["review"]["beside_audit"] = True


def audit_requirements(root: Path, config: Config, run: Run) -> list[str]:
    """
    The project's spec audit, scoped to the run: an agent in a session of its own checks the
    change against each requirement the Spec names. A criterion that is a requirement must come
    back VERIFIED, and a DIVERGES verdict fails whichever id it names. Nothing to audit without
    an id — and an audit of ids the Spec no longer names is dropped, so package does not ask for
    their traceability rows.
    """
    settings = config.raw.get("audit")
    ids = spec_requirement_ids(run, config)
    if not settings or not ids:
        run.record.pop("audit", None)
        run.sections.pop("Requirements audit", None)
        return []
    previous = run.record.get("audit") or {}
    if previous.get("verdict") and previous.get("head") == head(root) and previous.get("ids") == ids:
        # Nothing changed since its verdict: asking again would only roll the dice (the fourth review).
        problems = audit_problems(run, config, previous["verdict"], previous.get("rows", {}))
        return [f"{p} Nothing has changed since that audit at `{head(root)[:10]}`; change the code to be audited "
                "again." for p in problems]
    prompt = (f"Audit factory run {run.id} against its requirements. Its file is "
              f"{run.path.relative_to(root).as_posix() if run.path else ''}. The requirement ids: {', '.join(ids)}. "
              f"The change is `git log -p {change_commits(root, run)}`, plus any uncommitted work. "
              "Report in your fixed shape.")
    text, usd, tokens = agent_turn(root, config, settings, prompt)
    add_agent_cost(run, settings["agent"], usd, tokens)
    verdict, rows = parse_audit(text, config)
    problems = audit_problems(run, config, verdict, rows)
    keep_judgement(run, settings["agent"], verdict, head(root), text)
    run.sections["Requirements audit"] = (f"*Written by the referee from the `{settings['agent']}` agent's report — "
                                          f"a session of its own, at `{head(root)[:10]}`.*\n\n{text.strip()}")
    run.record["audit"] = {"verdict": verdict, "rows": rows, "ids": ids, "at": utc_now(), "head": head(root),
                           "usd": round(usd, 4), "sha256": sha256_text(text)}
    report = evidence_path(root, config, run, "requirements-audit.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text, encoding="utf-8")
    record_check(root, run, "verify", f"requirements audit: {verdict or 'no verdict'}", not problems, None, report)
    stamp_section(run, "Requirements audit", "audit")
    return problems


def check_acceptance(root: Path, config: Config, run: Run) -> Outcome:
    """
    One line of evidence per criterion, written by the referee from its own records: what the
    tests step proved before the build — red, a guard green, or NOT PROVEN where another
    criterion's missing symbol kept it from running (said as such, never as red) — and green at
    the reviewed commit; the integration suites that passed at that commit; the reviewer's verdict.
    Notes a person adds under **Checked by hand:** are kept.
    """
    record = run.record
    reviewed = (record.get("review") or {}).get("head") or ""
    at = reviewed or head(root)
    checks = record.get("checks", [])
    red = (record.get("red") or {}).get("acs", {})
    guards = guard_criteria(run)
    traits = ac_traits(root, config)
    packaged = packaged_head(run)
    integrate = [c for c in checks if c["step"] == "integrate" and c.get("counts") and c["ok"]
                 and evidence_still_holds(root, config, c.get("head", ""), at, packaged)]
    problems, rows, proven, changes = [], [], 0, 0
    for ac, _ in acceptance_criteria(run):
        greens = [c for c in checks if c["name"] == f"green {ac}" and c["ok"]]
        green = greens[-1] if greens else None
        if ac in guards:
            before = f"guard — {red[ac].removeprefix('guard — ')}" if ac in red else "guard — green before the build"
        elif ac in red:
            changes += 1
            proven += not red[ac].startswith("NOT PROVEN")
            before = f"**not proven red** — {red[ac]}" if red[ac].startswith("NOT PROVEN") else f"red: {red[ac]}"
        else:
            before = ""
            problems.append(f"{ac}: no red proof in the Record.")
        if not green or not evidence_still_holds(root, config, green.get("head", ""), at, packaged):
            problems.append(f"{ac}: no green run of its tests at the reviewed commit.")
        files = ", ".join(f"`{f}`" for f in traits.get(ac, [])) or "—"
        counts = (green or {}).get("counts") or {}
        rows.append(f"| {ac} | {files} | {before or '—'} | "
                    f"{counts.get('passed', '?')} passed at `{(green or {}).get('head', '')[:10]}` | "
                    f"`{((green or {}).get('sha256') or '—')[:12]}` |")
    if not reviewed:
        problems.append("No reviewer verdict yet — acceptance follows the review.")
    if not integrate:
        problems.append("No integration suite passed at the reviewed commit.")
    suites = "; ".join(f"{c['name']}: {c['counts']['passed']} passed, {c['counts']['notExecuted']} skipped"
                       for c in integrate) or "none at the reviewed commit"
    by_hand = run.section_text("Acceptance")
    kept = by_hand[by_hand.index(CHECKED_BY_HAND):] if CHECKED_BY_HAND in by_hand else ""
    # What the evidence is, in the project's own words (`[adapter] acceptance_note`).
    how = adapter(config).get("acceptance_note", "Evidence from a deployed environment comes after the merge.")
    run.sections["Acceptance"] = (
        f"*Written by the referee from its own evidence at `{reviewed[:10] or '—'}`. {how}*\n\n"
        "| Criterion | Tests | Before the build | At the reviewed commit | Evidence sha256 |\n|---|---|---|---|---|\n"
        + "\n".join(rows) + f"\n\n**Suites at integrate:** {suites}.\n\n"
        f"**Review:** {(record.get('review') or {}).get('verdict') or '—'}."
        # Anything below is the run file's, not the referee's: the session may have written it (the fourth review).
        + (f"\n\n{CHECKED_BY_HAND} {NOT_CHECKED} "
           f"{kept.removeprefix(CHECKED_BY_HAND).strip().removeprefix(NOT_CHECKED).strip()}" if kept else ""))
    stamp_section(run, "Acceptance", "acceptance")
    return Outcome(not problems, problems or [
        f"Every criterion is green at {reviewed[:10]}; {proven} of {changes} change criteria were proven red before "
        "the fix" + ("" if proven == changes else ", the rest NOT PROVEN (see the table)") + "."])


def run_check(root: Path, config: Config, run: Run, step: Step) -> Outcome:
    """The referee's own verdict on the step — shown to a person watching as it is reached."""
    with PROGRESS.waiting(f"⚖️  The referee checks {step.name}"):
        try:
            outcome = step_check(root, config, run, step)
        except (bitbucket.BitbucketError, subprocess.TimeoutExpired, ElementTree.ParseError) as error:
            # Failures outside the referee: as a FactoryError, `factory check` writes the run file — the checks
            # recorded before it are kept — and says why, instead of a traceback (the 15 Oct review's #8).
            raise FactoryError(f"The {step.name} check could not finish: {type(error).__name__}: {error}") from error
    first = next((line for line in outcome.messages if line.strip()), "")
    PROGRESS.say(f"   {'✅' if outcome.ok else '❌'} {step.name}: {'passed' if outcome.ok else 'not yet'}"
                 + (f" — {first if len(first) <= 120 else first[:119] + '…'}" if first else ""))
    return outcome


def step_check(root: Path, config: Config, run: Run, step: Step) -> Outcome:
    name = step.check or ""
    if name == "intent":
        return check_intent(run)
    if name == "context":
        return check_section(run, "Context")
    if name == "spec":
        return check_spec(run, config, root)
    if name == "design":
        return check_design(root, run, config)
    if name == "plan":
        return check_plan(run, config)
    if name == "tests-red":
        return check_tests_red(root, config, run)
    if name == "build":
        return check_build(root, config, run)
    if name == "integrate":
        return check_integrate(root, config, run)
    if name == "verify":
        return check_verify(root, config, run)
    if name == "bitbucket-approval":
        return check_bitbucket_approval(root, config, run)
    if name == "github-approval":
        return check_github_approval(root, config, run)
    if name == "local-approval":
        return check_local_approval(root, config, run)
    if name == "review":
        return check_review(root, config, run)
    if name == "package":
        return check_package(root, config, run)
    if name == "acceptance":
        return check_acceptance(root, config, run)
    if name == "sources":
        return check_sources(root, config, run)
    if name == "plan-doc":
        return check_plan_doc(root, config, run)
    if name in BOOK_CHECKS:
        missing = not_built(root, config, step)
        return Outcome(False, [missing]) if missing else BOOK_CHECKS[name](root, config, run)
    if name.startswith("section:"):
        return check_section(run, name.split(":", 1)[1])
    raise FactoryError(f"factory.toml names an unknown check '{name}' for step {step.name}.")


# ---------------------------------------------------------------- impact

def impact(root: Path, config: Config, base: str, paths: list[str] | None = None) -> dict:
    paths = paths if paths is not None else changed_paths(root, base)
    settings = config.raw.get("impact", {})
    source_roots = tuple(settings.get("source_roots", ["src/"]))
    modules: list[dict] = []
    cross_cutting: list[str] = []
    for path in paths:
        module = next((m for m in config.modules if re.search(m["match"], path, re.IGNORECASE)), None)
        if module is not None:
            if module not in modules:
                modules.append(module)
        elif path.startswith(source_roots) or any(glob_match(path, g) for g in settings.get("cross_cutting", [])):
            cross_cutting.append(path)
    requirement_ids: set[str] = set()
    traceability = root / config.raw.get("requirements", {}).get("traceability", "")
    if traceability.is_file():
        names = {path.rsplit("/", 1)[-1] for path in paths if path.startswith(source_roots)}
        for line in traceability.read_text(encoding="utf-8", errors="replace").splitlines():
            if any(name in line for name in names):
                requirement_ids.update(requirement_ids_in(line, config))
    regenerate = [rule["note"] for rule in settings.get("regenerate", [])
                  if any(re.search(rule["match"], path) for path in paths)]
    return {"paths": paths, "modules": modules, "cross_cutting": cross_cutting,
            "requirement_ids": sorted(requirement_ids), "regenerate": regenerate}


def render_impact(report: dict) -> str:
    lines = []
    if report["modules"]:
        lines.append("**Modules**")
        lines += [f"- {m['name']} — tests `{m['tests']}`"
                  + (f"; guide [{m['guide'].rsplit('/', 1)[-1]}](../../../{m['guide']})" if m.get("guide") else "")
                  + (f"; plan [{m['plan'].rsplit('/', 1)[-1]}](../../../{m['plan']})" if m.get("plan") else "")
                  for m in report["modules"]]
    if report["cross_cutting"]:
        lines.append("\n**Cross-cutting** — no module owns these, so the whole integration suite runs:")
        lines += [f"- `{p}`" for p in report["cross_cutting"][:15]]
    if report["requirement_ids"]:
        lines.append("\n**Requirement ids** the traceability matrix ties to these files: "
                     + ", ".join(report["requirement_ids"]))
    if report["regenerate"]:
        lines.append("\n**Regenerate**")
        lines += [f"- {item}" for item in report["regenerate"]]
    if not lines:
        lines.append("Nothing changed against the base yet — name the paths you expect to touch: "
                     "`factory impact <paths…>`; in a run, the Touches list in Intent is the prediction.")
    return "\n".join(lines)


# ---------------------------------------------------------------- index

def render_index(runs: list[Run], config: Config) -> str:
    rows = []
    for run in sorted(runs, key=lambda r: r.id):
        record = run.record
        pr = f"[#{record['pr']['id']}]({record['pr']['url']})" if record.get("pr") else "—"
        rows.append(f"| [{run.id}]({run.id}.md) | {run.title} | {record.get('item') or '—'} | "
                    f"{record['tier']['value']} | `{record.get('step', '')}` | {record['status']} | "
                    f"`{record['branch']}` | {pr} | {record['created'][:10]} |")
    table = "\n".join(rows) if rows else "| — | No runs yet | | | | | | | |"
    return ("# Factory runs\n\n"
            "Generated by `python3 devtools/factory/factory.py index` from the run files beside it — "
            "never hand-edit; CI checks it. One file per run; what each holds and why: "
            "[the harness plan](../../plans/ai-factory-harness.md#3-the-run-record).\n\n"
            "| Run | Title | Item | Tier | Step | Status | Branch | PR | Started |\n"
            "|---|---|---|---|---|---|---|---|---|\n" + table + "\n")


def write_index(root: Path, config: Config) -> None:
    path = root / config.runs_dir / "INDEX.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_index(load_runs(root, config), config), encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- verify

def verify_run(root: Path, config: Config, run: Run) -> list[str]:
    assert run.path is not None
    problems = []
    on_disk = run.path.read_text(encoding="utf-8")
    if referee_parts(on_disk) != referee_parts(render_run(run, config)):
        problems.append(f"{run.id}: the header, Checks or Gates do not match the Record — edited by hand.")
    # A lock binds until the merge gate: after it, the tests are the team's again, and a
    # teammate editing one on another branch must not fail CI (the re-review's M1).
    if run.record["status"] == "open" and MERGE_GATE not in run.record.get("gates", {}):
        problems += [f"{run.id}: {p}" for p in lock_problems(root, run)]
    problems += [f"{run.id}: {p}" for p in gate_drift(run, root)]
    problems += [f"{run.id}: {p}" for p in record_claims_problems(root, run)]
    problems += [f"{run.id}: {p}" for p in written_section_problems(run)]
    return problems


CHECKED_BY_HAND = "**Checked by hand:**"
NOT_CHECKED = "*(added to the run file; the referee did not check it)*"
# The sections the referee writes, and where their fingerprint is kept.
REFEREE_WRITTEN = (("Review", "review"), ("Requirements audit", "audit"), ("Acceptance", "acceptance"),
                   ("Outline", "outline"), ("Audit", "book_audit"), ("Changes", "changes"))


def referee_written_text(run: Run, name: str) -> str:
    """The part of a section the referee wrote — Acceptance keeps a person's notes after it."""
    text = run.section_text(name).strip()
    return text.split(CHECKED_BY_HAND)[0].strip() if name == "Acceptance" else text


def stamp_section(run: Run, name: str, key: str) -> None:
    run.record.setdefault(key, {})["section_sha256"] = sha256_text(referee_written_text(run, name))


def written_section_problems(run: Run) -> list[str]:
    """A section the referee wrote must still read as it wrote it: the person reading it is told so."""
    return [f"the {name} section is not what the referee wrote — edited by hand."
            for name, key in REFEREE_WRITTEN
            if (run.record.get(key) or {}).get("section_sha256")
            and sha256_text(referee_written_text(run, name)) != run.record[key]["section_sha256"]]


def record_claims_problems(root: Path, run: Run) -> list[str]:
    """
    What CI can check of a Record from git alone: the red proof happened on this branch's history,
    and each locked test was locked as it stood there. Not proof that a person approved a gate —
    that is the merge gate's job, in Bitbucket.
    """
    red = run.record.get("red") or {}
    red_head = red.get("head")
    passed_tests = run.record.get("steps", {}).get("tests", {}).get("status") == "passed"
    # An empty lock is honest when a person released the tests — their unlocks are in the Record.
    released = bool(run.record.get("unlocks"))
    if passed_tests and (not red_head or (not run.record.get("lock") and not released)):
        # A Record rolled back or rewritten to drop its proof (the re-review's HIGH-2).
        return ["its tests step has passed, but the Record holds no red proof or no locked tests."]
    if not red_head:
        return []
    code, _ = git(root, "merge-base", "--is-ancestor", red_head, "HEAD")
    if code != 0:
        # A shallow clone cannot tell a forged commit from one it never fetched: say which, never skip.
        if git_out(root, "rev-parse", "--is-shallow-repository") == "true":
            return [f"its red proof, commit {red_head[:10]}, is beyond this shallow clone — "
                    "run `git fetch --unshallow` (CI: `clone: depth: full`) and verify again."]
        return [f"its red proof names commit {red_head[:10]}, which is not in this branch's history."]
    problems = []
    for rel, digest in run.record.get("lock", {}).items():
        blob = blob_bytes(root, red_head, rel)
        if blob is None:
            continue  # locked later in the run, after the red proof — held by lock_problems instead
        committed = {hashlib.sha256(blob).hexdigest(), hashlib.sha256(blob.replace(b"\r\n", b"\n")).hexdigest()}
        if digest not in committed:
            problems.append(f"locked test {rel} does not match its content at the red-proof commit {red_head[:10]}.")
    return problems


def blob_bytes(root: Path, rev: str, rel: str) -> bytes | None:
    """A file's exact bytes at a commit, or None when it did not exist there."""
    done = subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=root, capture_output=True, timeout=60)
    return done.stdout if done.returncode == 0 else None


# ---------------------------------------------------------------- commands

def person_only(action: str) -> None:
    # Present at all, even blank: `CLAUDECODE= factory approve` passed a truthiness test (the review found it).
    # The environment can be cleared in more ways than a guard can list (the re-review), so the process
    # tree is asked too: nothing a Claude Code session launched may make a person's decision.
    if "CLAUDECODE" in os.environ or "CLAUDE_CODE_ENTRYPOINT" in os.environ or launched_by_claude():
        raise FactoryError(f"`factory {action}` is a person's decision. Run it in your own terminal, "
                           "not in a Claude Code session.")


CLAUDE_EXECUTABLES = frozenset({"claude", "claude.exe"})
CLAUDE_SCRIPTS = ("claude-code", "@anthropic-ai")


def launched_by_claude() -> bool:
    """
    Whether a Claude Code process is an ancestor of this one: the CLI itself, or node running
    it. Names only, never paths — a repository under D:\\Claude-Local is not Claude. When the
    process table cannot be read, nobody can be shown not to be a session, so the command is
    refused (the harness review: "no" let a session that cleared its environment through).
    """
    try:
        table = process_table()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise FactoryError(f"Could not read the process table ({type(error).__name__}), so the referee cannot "
                           "confirm a person — not a Claude Code session — is running this. Run it from an "
                           "ordinary terminal; if this repeats there, report it.") from None
    pid, seen = os.getpid(), set()
    if pid in table and table[pid][0] not in table:
        # Our own parent has exited: `start /b` and similar detach a command from the session that
        # launched it, ending the walk before it reaches Claude (the third review). A person runs
        # this from a terminal that is still open, so a detached caller is refused.
        return True
    while pid in table and pid not in seen:
        seen.add(pid)
        parent, name, command = table[pid]
        name = name.lower()
        if name in CLAUDE_EXECUTABLES or (name.startswith("node") and any(s in command for s in CLAUDE_SCRIPTS)):
            return True
        pid = parent
    return False


def process_table() -> dict[int, tuple[int, str, str]]:
    """pid → (parent pid, executable name, command line)."""
    if os.name == "nt":
        # A loaded machine can take longer than one try: a second, longer one before a person's approval is refused.
        for attempt, seconds in enumerate((30, 90)):
            try:
                done = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=seconds)
                rows = json.loads(done.stdout or "[]")
                break
            except (subprocess.TimeoutExpired, json.JSONDecodeError):
                if attempt:
                    raise
        return {int(r["ProcessId"]): (int(r["ParentProcessId"] or 0), r.get("Name") or "", r.get("CommandLine") or "")
                for r in (rows if isinstance(rows, list) else [rows])}
    done = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,comm=,args="], capture_output=True, text=True, timeout=30)
    table = {}
    for line in done.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), os.path.basename(parts[2]), parts[3] if len(parts) > 3 else "")
    return table


def git_identity(root: Path) -> str:
    """Who approved, by name only: the run file is in the repository, and nothing about a person beyond
    their name goes there — no email, no machine (CLAUDE.md, Private settings)."""
    name = git_out(root, "config", "user.name")
    if not name:
        raise FactoryError("Set git user.name first — a gate records who approved it.")
    return name


PERSON_WITH_EMAIL = re.compile(r"^(.*?)\s*<[^<>@\s]+@[^<>\s]+>$")
INTERPRETER = re.compile(r'^"?(?:[A-Za-z]:)?[^"\s]*[\\/]python(?:3(?:\.\d+)?)?(?:\.exe)?"?(?=\s|$)', re.IGNORECASE)
HOME_PATH = re.compile(r"(?:[A-Za-z]:[\\/]+Users[\\/]+|/(?:c/)?Users/|/home/)[^\\/\s\"']+", re.IGNORECASE)


def portable_command(command: str) -> str:
    """A recorded command as anyone would type it: `python3`, not this machine's interpreter path, and no
    home folder."""
    return HOME_PATH.sub("~", INTERPRETER.sub("python3", command))


def scrub_personal(value):
    """
    The Record without what is personal to whoever ran the referee: an approver's email, the machine's
    name, a home-folder path. Applied on every write, so a run file never carries them — and `factory
    render --all` cleans a file recorded before this rule.
    """
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key == "host":
                continue
            if key == "by" and isinstance(item, str):
                match = PERSON_WITH_EMAIL.match(item)
                cleaned[key] = match.group(1) if match else item
            elif key == "command" and isinstance(item, str):
                cleaned[key] = portable_command(item)
            else:
                cleaned[key] = scrub_personal(item)
        return cleaned
    if isinstance(value, list):
        return [scrub_personal(item) for item in value]
    return value


def unknown_item(root: Path, config: Config, item: str) -> str | None:
    """
    Why a `D` or `Q` id cannot start a run, or None: a defect has its row in the defect log the day
    it is found, and a question is in the decisions, before anyone works on it. A run on an id
    nobody logged passed every step to package — the dry run's F-01. A plan, plan-task or book
    item is held to its own rules (plan_item_refusal).
    """
    if (PLAN_ITEM.fullmatch(item) or PLAN_TASK_ITEM.fullmatch(item) or BOOK_ITEM.fullmatch(item)
            or REVISION_ITEM.fullmatch(item)):
        return plan_item_refusal(root, config, item)
    if item.startswith(("plan:", "book:", "revision:")):
        return (f"{item} is not an item the referee knows: `plan:<feature-slug>`, `plan:<plan-stem>#<task>` or "
                "`book:<name>` — lower case, digits and dashes — or `revision:V<n>`, the FSD version as its snapshot "
                "names it.")
    settings = config.raw.get("requirements", {})
    if re.fullmatch(r"D\d+", item):
        where, pattern = settings.get("defect_logs", []), rf"\*\*{item}\*\*"
        advice = "Log the defect first — its row, the day it is found (CLAUDE.md, Features) — then start its run."
    elif re.fullmatch(r"Q\d+", item):
        where, pattern = settings.get("sources", {}).get("decisions", []), rf"\b{item}\b"
        advice = "Raise the question in docs/open-questions.md first, then start its run."
    else:
        return None
    files = [root / name for name in where if (root / name).is_file()]
    if not files or any(re.search(pattern, path.read_text(encoding="utf-8", errors="replace")) for path in files):
        return None
    return f"{item} is in none of {', '.join(where)}. {advice}"


def branch_templates(config: Config) -> dict[str, str]:
    """How each kind of run's branch is named: the defaults, overridden by `[backlog.branches]`."""
    return {"plan": "chore/docs-plan-{slug}", "task": "feature/{stem}-t{task}", "book": "setup/book-{name}",
            "revision": "setup/revision-v{version}", "defect": "fix/{item}-{title}", "bug": "fix/{item}-{title}", "other": "feature/{item}-{title}",
            **(backlog_settings(config).get("branches") or {})}


def new_branch_name(config: Config, item: str, title: str, issue_type: str = "") -> str:
    """
    The branch `--new-branch` cuts for an item, from `[backlog.branches]`: a plan run's docs branch, a
    plan task's feature branch, the book's setup branch, a defect's fix branch — and a Jira Bug's, since
    the Jira check asks a Bug for a fix/ branch (`issue_type`: the issue's type, as Jira gave it).
    """
    templates = branch_templates(config)
    fields = {"item": item, "title": feature_slug(title)[:40].rstrip("-") or "run"}
    for key, pattern in (("task", PLAN_TASK_ITEM), ("plan", PLAN_ITEM), ("book", BOOK_ITEM), ("revision", REVISION_ITEM)):
        if (found := pattern.fullmatch(item)):
            return templates[key].format(**fields, **found.groupdict())
    if re.fullmatch(r"D\d+", item):
        return templates["defect"].format(**fields)
    return templates["bug" if issue_type.lower() == "bug" else "other"].format(**fields)


def cut_branch(root: Path, config: Config, name: str) -> None:
    """
    Cut a run's branch from HEAD, tracking nothing (CLAUDE.md, Branches). From HEAD, not the base
    branch: the console's checkout may hold the factory itself, unmerged (Phase C, R2). It switches the
    checkout's branch under whoever is using it — a console runs in a checkout of its own (R1).
    """
    if name in config.raw["protected_branches"]:
        raise FactoryError(f"--new-branch {name} is a protected branch; a run works on its own.")
    if git(root, "check-ref-format", "--branch", name)[0] != 0:
        raise FactoryError(f"--new-branch {name} is not a branch name git accepts.")
    if git_out(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"):
        raise FactoryError(f"Branch {name} already exists. Check it out and start there, or name another with "
                           "--new-branch <name>.")
    require_clean(root, config)
    git_strict(root, "checkout", "-q", "--no-track", "-b", name)


def command_start(root: Path, config: Config, args: argparse.Namespace) -> str:
    kind = run_kind(args.item)
    if not config.steps_of(kind):
        raise FactoryError(f"factory.toml has no steps for {kind} runs, so {args.item} cannot start one.")
    branch = current_branch(root)
    cut_from = None
    issue = None
    if args.new_branch is not None:
        if not args.new_branch:
            # A Jira issue's type decides the branch's (a Bug's is fix/): read it before the name is chosen.
            issue = jira_issue_to_start(root, config, args.item)
        cut_from, branch = branch, args.new_branch or new_branch_name(config, args.item, args.title,
                                                                      (issue or {}).get("type", ""))
    elif branch in config.raw["protected_branches"] or branch == "HEAD":
        raise FactoryError(f"On {branch}. A run works on its own branch: "
                           f"git checkout --no-track -b <type>/<slug> {config.raw['base_ref']}")
    already = [run.id for run in load_runs(root, config)
               if run.record["branch"] == branch and run.record["status"] == "open"]
    if already:
        raise FactoryError(f"{', '.join(already)} is open on {branch}. One open run per branch — the guard "
                           "protects the open run's locks and scope, and two would leave it unsure which.")
    PROGRESS.say(f"🔎 Checking {args.item} is in the book…")
    unknown = unknown_item(root, config, args.item)
    if unknown:
        raise FactoryError(unknown)
    given_base = None
    if args.base:
        # Before any branch is cut: a refusal must not leave a new branch behind.
        given_base = git_out(root, "rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}")
        if not given_base:
            raise FactoryError(f"--base {args.base} is not a commit.")
    if cut_from is not None:
        # Before the branch is cut: a refusal from Jira must not leave a new branch behind.
        issue = jira_issue_for_start(root, config, args.item, branch, issue)
        PROGRESS.say(f"🌿 Cutting {branch} from {cut_from}…")
        cut_branch(root, config, branch)
    PROGRESS.say("🔢 Numbering the run — every branch and worktree, so two runs never share a number…")
    run_id = next_run_id(root, config)
    PROGRESS.say(f"📝 {run_id}: writing its run file")
    if given_base:
        base = given_base
    else:
        base = run_base(root, config)
        if base == head(root) and base != merge_base(root, config.raw["base_ref"]):
            PROGRESS.say("🌿 This branch carries work already on another branch: the run counts only its own commits")
    if cut_from is None:
        issue = jira_issue_for_start(root, config, args.item, branch)
    record = {
        "run": run_id, "item": args.item, "kind": kind, "branch": branch, "base": base,
        "created": utc_now(), "status": "open", "tier": {"value": "Low", "reasons": []},
        "steps": {}, "gates": {}, "checks": [], "lock": {}, "unlocks": [], "pr": None, "session": None,
    }
    plan_item = PLAN_ITEM.fullmatch(args.item)
    fields = {"{run}": run_id, "{prefix}": requirement_prefix(config),
              "{traceability}": config.raw.get("requirements", {}).get("traceability", "the traceability matrix"),
              "{slug}": plan_item.group("slug") if plan_item else "",
              "{tracker}": config.raw.get("requirements", {}).get("tracker", "the tracker")}
    sections = {}
    for name in KIND_SECTIONS[kind]:
        text = KIND_TEMPLATES.get(kind, {}).get(name) or TEMPLATES[name]
        for key, value in fields.items():
            text = text.replace(key, value)
        sections[name] = text
    if issue:
        record["jira"] = issue
    run = Run(run_id, args.title, sections, record)
    write_run(root, config, run)
    assert run.path is not None
    first = config.steps_of(kind)[0]
    return "\n".join([f"Started {run_id} on {branch}: {run.path.relative_to(root).as_posix()}"
                      + (f" — a {kind} run: {KIND_STORY[kind]}" if kind != CHANGE else ""),
                      *item_notes(root, config, args.item),
                      "Next: write Intent, then `factory check`." if kind == CHANGE else
                      f"Next: {first.name}, then `factory check` — or `factory drive`.",
                      *options_block(root, config, None, "before")])
def both_filters(settings: dict, first: str, second: str) -> str:
    """
    Two test filters that must both hold, in the stack's own grammar: `[adapter] filter_and`, `{a}` and `{b}` for the
    two — VSTest's `{a}&({b})` when the adapter says nothing, the grammar the factory began with. Either one empty:
    the other alone. It was the VSTest form for every stack, so a pytest or Vitest suite got `&` and `|` it cannot read.
    """
    if not first or not second:
        return first or second
    return str(settings.get("filter_and", "{a}&({b})")).format(a=first, b=second)


def modules_filter(settings: dict, modules: list[dict]) -> str:
    """The touched modules' test filters as one: each in `[adapter] module_filter` (VSTest's `({tests})` by default),
    joined with the adapter's `criteria_join`."""
    shape = str(settings.get("module_filter", "({tests})"))
    return str(settings.get("criteria_join", "|")).join(shape.format(tests=m["tests"]) for m in modules)


def ci_tier(root: Path, config: Config, base: str, paths: list[str] | None = None) -> dict:
    """
    How much of devtools/checks/ci-local.sh a change needs (docs/plans/local-ci-tiers.md §3): the
    highest tier any changed path reaches. Full for a cross-cutting or high-tier path, for an
    integration test no module owns — the module map cannot say what such a file reaches — and for
    more than one module, which CLAUDE.md's testing rule runs in full. What it reads is the project's own:
    the in-process filter (`[local_ci] filter`, else `[commands] integration_filter`), the integration
    project, the source roots and the test files — not one project's names, as it did before the kit audit.
    """
    report = impact(root, config, base, paths)
    paths = report["paths"]
    commands, local, settings = config.raw.get("commands", {}), config.raw.get("local_ci", {}), adapter(config)
    in_process = str(local.get("filter", commands.get("integration_filter", "")))
    integration = str(commands.get("integration_project") or "").strip("/")
    high = [path for path in paths
            if any(re.search(pattern, path, re.IGNORECASE) for pattern in config.raw.get("tier", {}).get("high", []))]
    stray = [path for path in paths if integration and path.startswith(integration + "/")
             and not any(re.search(m["match"], path, re.IGNORECASE) for m in config.modules)]
    if report["cross_cutting"] or high or stray:
        reached = list(dict.fromkeys(report["cross_cutting"] + high + stray))
        return {"tier": "full", "filter": in_process, "modules": [],
                "reason": "reaches every module: " + ", ".join(reached[:3]) + (", …" if len(reached) > 3 else "")}
    names = [m["name"] for m in report["modules"]]
    if len(names) > 1:
        return {"tier": "full", "filter": in_process, "modules": names,
                "reason": "more than one module: " + ", ".join(names)}
    if names:
        return {"tier": "modules", "filter": both_filters(settings, in_process, modules_filter(settings, report["modules"][:1])),
                "modules": names, "reason": names[0]}
    roots = tuple(config.raw.get("impact", {}).get("source_roots", ["src/"]))
    if any(path.startswith(roots) or any(glob_match(path, p) for p in settings.get("test_files", [])) for path in paths):
        return {"tier": "build", "filter": "", "modules": [], "reason": "code that no module's integration tests cover"}
    return {"tier": "checks", "filter": "", "modules": [], "reason": "no code changed"}


def render_ci_tier(result: dict) -> str:
    """One key per line, for ci-local.sh to read with sed."""
    return f"tier={result['tier']}\nfilter={result['filter']}\nreason={result['reason']}"


def jira_settings(config: Config) -> tuple[str, str]:
    settings = config.raw.get("jira") or {}
    if not settings.get("site") or not settings.get("project"):
        raise FactoryError("factory.toml has no [jira] site and project.")
    return settings["site"], settings["project"]


def jira_issue_for_start(root: Path, config: Config, item: str, branch: str, issue: dict | None = None) -> dict | None:
    """
    A run for a Jira issue starts on a branch carrying its key right after the type (CLAUDE.md, Git),
    a Bug on a `fix/` one; the referee reads the issue itself, so the run records what it really is.
    Jira unreachable stops the start: a run is never opened on an issue nobody could read. `issue`: the
    summary already read (jira_issue_to_start), so it is not read twice.
    """
    if not jira.KEY.match(item) or not config.raw.get("jira"):
        return None
    _, project = jira_settings(config)
    if not item.startswith(f"{project}-"):
        raise FactoryError(f"{item} is not a {project} issue — [jira] project in factory.toml.")
    if not re.match(rf"[a-z]+/{re.escape(item)}(-|$)", branch):
        raise FactoryError(f"A run for {item} works on a branch carrying the key right after the type "
                           f"(CLAUDE.md, Git): git checkout --no-track -b <type>/{item}-<slug> {config.raw['base_ref']}")
    found = issue or jira_issue_to_start(root, config, item)
    if found is None:
        return None
    # A Bug's branch starts as the project names bug branches (`[backlog.branches] bug`, fix/ by default): a fixed
    # `fix/` refused the `bugfix/` branch --new-branch had just cut for it (the kit audit, 18 Oct).
    prefix = branch_templates(config)["bug"].split("{", 1)[0]
    if found["type"].lower() == "bug" and not branch.startswith(prefix):
        raise FactoryError(f"{item} is a Bug: its run works on {prefix}{item}-<slug> ([backlog.branches] bug).")
    return found


def jira_issue_to_start(root: Path, config: Config, item: str) -> dict | None:
    """The summary of the Jira issue a run starts from, read by the referee; None for an item that is not
    one of the project's keys, or with Jira off. Jira unreachable stops the start."""
    if not jira.KEY.match(item) or not config.raw.get("jira") or os.environ.get("FACTORY_JIRA") == "off":
        return None
    site, project = jira_settings(config)
    if not item.startswith(f"{project}-"):
        return None  # jira_issue_for_start refuses it, naming the project
    try:
        return jira.summary(jira.read_issue(site, item, jira.credential(root, site)))
    except jira.JiraError as error:
        raise FactoryError(f"Could not read {item} from Jira: {error}. A run starts from an issue someone has "
                           "read — fix the credential or the network and start again.") from None


def command_jira(root: Path, config: Config, args: argparse.Namespace) -> str:
    """One Jira issue, read only, for a session to read — the credential stays in this process."""
    site, project = jira_settings(config)
    key = args.key.upper()
    if not key.startswith(f"{project}-"):
        raise FactoryError(f"{key} is not a {project} issue — [jira] project in factory.toml.")
    try:
        with PROGRESS.waiting(f"🌐 Asking Jira for {key}"):
            return jira.render(jira.read_issue(site, key, jira.credential(root, site)), site)
    except jira.JiraError as error:
        raise FactoryError(f"Could not read {key} from Jira: {error}.") from None


OPTION_HEADINGS = {"before": "Before the run, if it fits:", "after": "At the merge gate and after, if it fits:",
                   "periodic": "Now and then, not per run:"}


def options_for(root: Path, config: Config, run: Run | None, when: str) -> list[str]:
    """
    The skills `[[options]]` offers a person before a run, or from its merge gate on — never steps, so
    nothing waits on them. For a run, one with `touches` fits only when the run changed such a
    path, and one with `needs = "pr"` only once it has a pull request; with no run, all are listed.
    """
    paths = touches(run) if run else []
    if run and current_branch(root) == run.record["branch"]:
        paths += changed_paths(root, run.record["base"])
    found = []
    for option in config.raw.get("options", []):
        if option.get("when") != when:
            continue
        if run and option.get("touches") and not any(re.search(option["touches"], path) for path in paths):
            continue
        if run and option.get("needs") == "pr" and not run.record.get("pr"):
            continue
        found.append(f"  {option['skill']} — {option['why']}")
    return found


def options_block(root: Path, config: Config, run: Run | None, when: str) -> list[str]:
    found = options_for(root, config, run, when)
    return [OPTION_HEADINGS[when], *found] if found else []


def describe(run: Run, config: Config, root: Path | None = None) -> str:
    step = current_step(run.record, config)
    lines = [f"{run.id} — {run.title}", f"  branch {run.record['branch']} · tier {run.record['tier']['value']}"]
    for each in config.steps:
        if not applicable(each, run.record):
            continue
        passed = each.name in run.record.get("gates", {}) if each.gate else \
            run.record.get("steps", {}).get(each.name, {}).get("status") == "passed"
        marker = "[x]" if passed else ("-->" if step and each.name == step.name else "[ ]")
        lines.append(f"  {marker} {each.name}{' (gate)' if each.gate else ''}")
    notice = fsd_changed_notice(run.record)
    if notice:
        lines.append(notice)
    if step and step.gate:
        lines.append(f"Waiting for a person: {gate_instruction(step)}.")
    elif step:
        lines.append(f"Next: complete {step.name}, then `factory check`.")
    if root and (step is None or step.name == MERGE_GATE):
        lines += options_block(root, config, run, "after")
    return "\n".join(lines)


def command_status(root: Path, config: Config, args: argparse.Namespace) -> str:
    return describe(active_run(root, config, args.run), config, root)


def command_options(root: Path, config: Config, args: argparse.Namespace) -> str:
    """Every option that fits: for the open run on this branch (or --run), or — with none — the general list."""
    try:
        run = active_run(root, config, args.run)
    except FactoryError:
        if args.run:
            raise
        run = None
    lines = [line for when in OPTION_HEADINGS for line in options_block(root, config, run, when)]
    return "\n".join(lines) if lines else "factory.toml offers no [[options]]."


def command_check(root: Path, config: Config, args: argparse.Namespace) -> tuple[str, bool]:
    run = active_run(root, config, args.run)
    refresh_tier(root, config, run)
    step = current_step(run.record, config)
    if step is None:
        write_run(root, config, run)
        return f"{run.id}: every step has passed.", True
    drift = gate_drift(run, root)
    if drift:
        write_run(root, config, run)
        return f"{run.id} · {step.name}: not yet\n" + "\n".join(f"  - {m}" for m in drift), False
    if step.gate and not step.check:
        write_run(root, config, run)
        return (f"{run.id} is at {step.name}: a person approves it with "
                f"`factory approve {step.name.removesuffix('-gate')}` from their own terminal."), False
    try:
        outcome = run_check(root, config, run, step)
    except FactoryError:
        write_run(root, config, run)
        raise
    if outcome.ok and step.gate:
        # A gate the referee reads rather than records — the Merge gate's approval lives in Bitbucket.
        run.record.setdefault("gates", {})[step.name] = {
            "by": outcome.gate_by or "", "at": utc_now(), "head": head(root),
            "source": {"local-approval": "local", "github-approval": "github"}.get(step.check, "bitbucket"),
            "note": outcome.messages[0] if outcome.messages else None}
    elif outcome.ok:
        run.record.setdefault("steps", {})[step.name] = {"status": "passed", "at": utc_now(), "head": head(root)}
    write_run(root, config, run)
    verdict = "passed" if outcome.ok else "not yet"
    following = current_step(run.record, config)
    tail = f"\nNow: {following.name}" if outcome.ok and following else ""
    return f"{run.id} · {step.name}: {verdict}\n" + "\n".join(f"  - {m}" for m in outcome.messages) + tail, outcome.ok


OPEN_QUESTION = re.compile(r"^#{3,4}\s+(Q\d+)\s*·\s*(.+?)\s*$")
RESOLVED_ARCHIVE_TOP = ("# Resolved questions — the full record\n\nEach answered question as it was asked, "
                        "with its answer: who gave it and when. The rule now in force is one line under "
                        "Resolved in `open-questions.md`.\n")


def heading_anchor(heading: str) -> str:
    """A Markdown heading's link anchor, as GitHub and VS Code make it: `Q1 · May…?` → `q1--may…`."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def question_answered(text: str, archive: str, qid: str, answer: str | None, person: str, day: str) -> tuple[str, str, str]:
    """
    open-questions.md and its archive once a person has answered Q<n>: the question in full, with its answer, who
    and when, moves to the archive; a one-line rule takes its place under Resolved, in the topic its Area names.
    `answer` None takes the question's own Recommendation. Refuses a question that is not open, or a file without
    the kit's sections — never a guess at where the answer goes.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    resolved_at = next((i for i, line in enumerate(lines) if re.match(r"^## Resolved\b", line)), None)
    if resolved_at is None:
        raise FactoryError("open-questions.md has no `## Resolved` section, so there is nowhere to record the answer.")
    start = next((i for i, line in enumerate(lines[:resolved_at])
                  if (m := OPEN_QUESTION.match(line.strip())) and m.group(1) == qid), None)
    if start is None:
        raise FactoryError(f"{qid} is not an open question in open-questions.md — answered already, or never asked.")
    end = start + 1
    while end < resolved_at and not re.match(r"^(#{1,4} |---\s*$)", lines[end]):
        end += 1
    block = "\n".join(lines[start:end]).strip()
    heading = f"{qid} · {OPEN_QUESTION.match(lines[start].strip()).group(2)}"
    area = re.search(r"\*\*Area:\*\*\s*([^·\n]+)", block)
    topic = area.group(1).strip() if area else "Other"
    suggested = re.search(r"\*\*Recommendation:\*\*\s*(.+?)(?:\n\s*\n|$)", block, re.DOTALL)
    if answer is None and not suggested:
        raise FactoryError(f"{qid} has no Recommendation to accept; write the answer instead.")
    rule = " ".join((answer if answer is not None else suggested.group(1)).split())
    if not rule:
        raise FactoryError("The answer is empty.")
    said = "As recommended." if answer is None else rule
    full = re.sub(r"\*\*Status:\*\*\s*`?OPEN`?", "**Status:** ANSWERED", block, count=1)
    full, filled = re.subn(r"^\*\*Answer:\*\*.*$", lambda _: f"**Answer:** {said} — {person}, {day}", full, flags=re.MULTILINE)
    if not filled:
        full += f"\n\n**Answer:** {said} — {person}, {day}"
    entry = [f"#### {heading}", "",
             f"`ANSWERED` · {day} · {person} — {rule.rstrip('.')}. [Full record](archive/questions-resolved.md#{heading_anchor(heading)})"]
    rest = lines[:start] + lines[end:]
    while start < len(rest) and start > 0 and not rest[start].strip() and not rest[start - 1].strip():
        del rest[start]   # the blank line the question left behind
    resolved_at = next(i for i, line in enumerate(rest) if re.match(r"^## Resolved\b", line))
    section_end = next((i for i in range(resolved_at + 1, len(rest)) if re.match(r"^## ", rest[i])), len(rest))
    at = next((i for i in range(resolved_at + 1, section_end) if rest[i].strip() == f"### {topic}"), None)
    after = section_end if at is None else next((i for i in range(at + 1, section_end) if re.match(r"^#{2,3} ", rest[i])), section_end)
    while after > resolved_at + 1 and not rest[after - 1].strip():
        after -= 1
    rest[after:after] = ["", *([f"### {topic}", ""] if at is None else []), *entry, *([""] if after < len(rest) else [])]
    archived = (archive.rstrip("\n") if archive.strip() else RESOLVED_ARCHIVE_TOP.rstrip("\n")) + f"\n\n{full}\n"
    return "\n".join(rest).rstrip("\n") + "\n", archived, rule


def command_answer(root: Path, config: Config, args: argparse.Namespace) -> str:
    """A person answers an open question — their own words, or the question's Recommendation — committed on the
    branch the folder is on, so the run that asked reads it. Never on a protected branch, nor on the base branch unless
    the project merges locally."""
    person_only("answer")
    if not re.fullmatch(r"Q\d+", args.question):
        raise FactoryError(f"{args.question} is not a question id (Q<n>).")
    if (args.answer is None) == (not args.accept):
        raise FactoryError("Give the answer (--answer \"…\") or accept the recommendation (--accept), not both.")
    refuse_shared_branch(root, config, "Answer", base_when_local=True)
    decisions = config.raw.get("requirements", {}).get("sources", {}).get("decisions") or []
    questions_file = next((d for d in decisions if not d.startswith("docs/archive/")), "docs/open-questions.md")
    archive_file = next((d for d in decisions if d.startswith("docs/archive/")), "docs/archive/questions-resolved.md")
    if git_strict(root, "status", "--porcelain", "--", questions_file, archive_file):
        raise FactoryError(f"{questions_file} or its archive has changes not yet committed; commit or undo them first.")
    person = git_identity(root)
    archive_path = root / archive_file
    text, archive, rule = question_answered((root / questions_file).read_text(encoding="utf-8"),
                                            archive_path.read_text(encoding="utf-8") if archive_path.is_file() else "",
                                            args.question, args.answer, person, utc_now()[:10])
    (root / questions_file).write_text(text, encoding="utf-8")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(archive, encoding="utf-8")
    git_strict(root, "add", "--", questions_file, archive_file)
    code, out = git(root, "commit", "-m", f"Record {person}'s answer to {args.question}", "--", questions_file, archive_file)
    if code != 0:
        raise FactoryError(f"The answer is written but the commit failed:\n{out}")
    return f"{args.question} answered by {person}: {rule}"


def command_allow(root: Path, config: Config, args: argparse.Namespace) -> str:
    """A person lets a step write files in a new place — committed on the work branch — and a run held at its plan
    for want of them is checked again."""
    person_only("allow")
    try:
        run = active_run(root, config, args.run)
    except FactoryError:
        if args.run:
            raise
        run = None
    added = allow_writes(root, config, args.step, args.paths, git_identity(root), run.id if run else "by hand")
    said = (f"The {args.step} step may now write {', '.join(added)}." if added
            else f"The {args.step} step may already write {', '.join(args.paths)}.")
    if run is None:
        return said
    config = load_config(root)
    run = active_run(root, config, run.id)
    step = current_step(run.record, config)
    if step is None or step.check != "plan":
        return said
    if run.section_text("Escalation"):
        # The AI asked for exactly this: the yes is the person's answer, kept in the run's history like any other.
        reopen_steps(run, config, step.name, f"Allowed the {args.step} step to write {', '.join(args.paths)}.")
        run.record["reopened"][-1]["person"] = git_identity(root)
        write_run(root, config, run)
    checked, _ = command_check(root, config, argparse.Namespace(run=run.id))
    return f"{said}\n{checked}"


def command_approve(root: Path, config: Config, args: argparse.Namespace) -> str:
    person_only("approve")
    run = active_run(root, config, args.run)
    refresh_tier(root, config, run)
    gate = f"{args.gate}-gate"
    step = current_step(run.record, config)
    if step is None or step.name != gate:
        raise FactoryError(f"{run.id} is not at {gate}; it is at {step.name if step else 'the end'}.")
    approver = git_identity(root)
    proof = proof_entry(root, config, run, getattr(args, "proof", None))
    if gate == MERGE_GATE:
        return approve_merge_locally(root, config, run, step, approver, args.note, proof)
    # By name, as gates are recorded now — a gate recorded before carries "Name <email>", the same person.
    spec_by = scrub_personal({"by": (run.record.get("gates", {}).get(SPEC_GATE) or {}).get("by")})["by"]
    # A second person for a risky change's design; a screen's description on a lower tier is anyone's to approve.
    if (gate == DESIGN_GATE and config.raw.get("gates", {}).get("design_second_person") and approver == spec_by
            and run.record["tier"]["value"] == "High"):
        raise FactoryError(f"The design gate needs a second person: {approver} approved the spec. "
                           "Ask the module's owner to run `factory approve design` in their own checkout.")
    allowed = []
    if gate == DESIGN_GATE and run.record.get("writes"):
        # The design's `### Writes`: approving the design is the person's yes to them (check_design).
        allowed = allow_writes(root, config, next(s.name for s in config.steps if s.check == "build"),
                               run.record["writes"], approver, f"{run.id}'s design")
    entry = {"by": approver, "at": utc_now(), "note": args.note, "head": head(root), **proof}
    if gate in GATED_SECTIONS:
        # What was approved, not only who: a Spec edited after its gate is caught (the review's HIGH-3).
        entry["section_sha256"] = section_digest(run, GATED_SECTIONS[gate])
    gated = gated_file(config, run, gate)
    if gated:
        # A plan gate approves the plan file, a book gate project.md: the same hold, on a file.
        if not (root / gated).is_file():
            raise FactoryError(f"{gated} does not exist, so there is nothing to approve at {gate}.")
        # A change to it is re-checked from the step a session wrote it in — not from an auto step
        # between (the book's audit reads project.md; the features step writes it).
        before = [s for s in config.steps if applicable(s, run.record)]
        before = before[:[s.name for s in before].index(gate)]
        entry.update({"file": gated, "file_sha256": file_digest(root / gated),
                      "checked_by": next((s.name for s in reversed(before) if not s.gate and not s.auto), gate)})
    run.record.setdefault("gates", {})[gate] = entry
    write_run(root, config, run)
    return (f"{run.id}: {gate} approved by {run.record['gates'][gate]['by']}."
            + (f" The build may now write {', '.join(allowed)}." if allowed else ""))


def approve_merge_locally(root: Path, config: Config, run: Run, step: Step, approver: str, note: str | None,
                          proof: dict | None = None) -> str:
    """
    `factory approve merge`: only where the Merge gate reads a local approval. It records who approved which
    commit; the gate's check then passes it, as it would a Bitbucket approval, only while the evidence holds.
    """
    if step.check != "local-approval":
        raise FactoryError("This project's merge gate is approved on the pull request (Bitbucket or GitHub) — not here.")
    local = head(root)
    problems = merge_evidence_problems(root, config, run, local, local)
    if problems:
        raise FactoryError("Not approved — the evidence does not hold for this commit:\n"
                           + "\n".join(f"  - {p}" for p in problems))
    run.record["merge_approval"] = {"by": approver, "at": utc_now(), "note": note, "head": local, **(proof or {})}
    write_run(root, config, run)
    return (f"{run.id}: merge approved by {approver} at {local[:10]}. `factory check` (or Drive) passes the merge "
            "gate; then merge the branch.")


# The revision gate approves the Triage section as well as project.md: a disposition changed after it is caught.
GATED_SECTIONS = {SPEC_GATE: "Spec", DESIGN_GATE: "Design", OUTLINE_GATE: "Outline", REVISION_GATE: "Triage"}
# The gate a section's source rules lead to, named in what a stale live check asks of a person.
GATE_OF_SECTION = {"Spec": SPEC_GATE, "Sources": PLAN_GATE}


def gated_file(config: Config, run: Run, gate: str) -> str | None:
    """The file a gate approves, when it approves one: the plan a plan run wrote, the book's project.md (a book
    run's, or a revision run's — the rest of what a revision wrote is held by its package check, from the gate's commit)."""
    if gate == PLAN_GATE and plan_slug(run):
        return plan_path(config, plan_slug(run))
    if gate in (BOOK_GATE, REVISION_GATE):
        return config.raw.get("requirements", {}).get("tracker") or None
    return None


PROOF_FOLDER = "proof"


def proof_entry(root: Path, config: Config, run: Run, given: str | None) -> dict:
    """
    The saved copy of what a person saw when they decided (`--proof`, which the Factory app writes): where it is and
    its digest, kept with the approval or the answer — the Decisions log's evidence that a person was asked. Only a
    file in the run's own proof folder, beside its record, so it is committed with the run.
    """
    if not given:
        return {}
    folder = f"{config.runs_dir}/{PROOF_FOLDER}/{run.id}/"
    rel = given.replace("\\", "/")
    if not rel.startswith(folder) or any(part in ("", ".", "..") for part in rel.split("/")):
        raise FactoryError(f"--proof must be a file under {folder}, where the Factory app saves what a person saw.")
    if not (root / rel).is_file():
        raise FactoryError(f"--proof {rel}: no such file.")
    return {"proof": rel, "proof_sha256": file_digest(root / rel)}


def file_digest(path: Path) -> str:
    """A file as approved, read the way section_digest reads a section: comments, line endings and trailing
    space aside — a re-saved file on another OS is the same file; a changed sentence is not."""
    return text_digest(path.read_text(encoding="utf-8", errors="replace"))


def text_digest(text: str) -> str:
    """file_digest of a file's text, however it was read — from the working tree or from a commit."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return sha256_text("\n".join(line.rstrip() for line in text.replace("\r\n", "\n").splitlines()).strip())


def section_digest(run: Run, name: str) -> str:
    """The section as approved: guidance comments stripped, line endings and trailing space normalised."""
    text = "\n".join(line.rstrip() for line in run.section_text(name).replace("\r\n", "\n").splitlines())
    return sha256_text(text.strip())


def gate_drift(run: Run, root: Path | None = None) -> list[str]:
    """
    Approved sections that have changed since their gate — the run cannot go on until a person looks again.
    With `root`, approved files too, while the run is open and short of its merge gate: after that the plan
    is the team's, and its task rows move as the work is built.
    """
    problems = []
    for gate, section in GATED_SECTIONS.items():
        approved = (run.record.get("gates", {}).get(gate) or {}).get("section_sha256")
        if approved and approved != section_digest(run, section):
            problems.append(f"The {section} section changed after the {gate} approved it. A person reopens it: "
                            f"`factory reopen {section.lower()} --reason \"…\"`, then approves it again.")
    if root is None or run.record["status"] != "open" or MERGE_GATE in run.record.get("gates", {}):
        return problems
    for gate, entry in sorted(run.record.get("gates", {}).items()):
        if not (entry or {}).get("file_sha256"):
            continue
        path = root / entry["file"]
        if not path.is_file() or file_digest(path) != entry["file_sha256"]:
            problems.append(f"{entry['file']} changed after the {gate} approved it. A person reopens it: "
                            f"`factory reopen {entry.get('checked_by') or gate} --reason \"…\"`, then approves it again.")
    return problems


def command_unlock(root: Path, config: Config, args: argparse.Namespace) -> str:
    person_only("unlock")
    run = active_run(root, config, args.run)
    rel = Path(args.path).as_posix()
    if rel not in run.record.get("lock", {}):
        raise FactoryError(f"{rel} is not locked in {run.id}.")
    del run.record["lock"][rel]
    run.record.setdefault("unlocks", []).append(
        {"path": rel, "by": git_identity(root), "at": utc_now(), "reason": args.reason})
    write_run(root, config, run)
    return f"{run.id}: {rel} unlocked."


# ---------------------------------------------------------------- the working session

PROMPTS_RELATIVE = "devtools/factory/prompts"


@dataclass
class SessionTurn:
    ok: bool
    result: str
    cumulative_usd: float
    context_tokens: int
    turns: int
    seconds: float
    tokens: dict = field(default_factory=dict)  # the session's running totals, by TOKEN_KINDS
    model: str = "default"
    effort: str = "default"


# What the CLI reports, cumulative over a resumed session like its `total_cost_usd` (both measured
# 2026-10-02 with two calls of one session). Its USD is list price (`costBasis: "list"`): what the
# tokens would cost on the API, not what a subscription is charged.
TOKEN_KINDS = (("input", "inputTokens", "input_tokens"), ("output", "outputTokens", "output_tokens"),
               ("cache_read", "cacheReadInputTokens", "cache_read_input_tokens"),
               ("cache_write", "cacheCreationInputTokens", "cache_creation_input_tokens"))


def session_tokens(data: dict) -> dict:
    """Tokens by kind from a `claude -p` answer: `modelUsage` sums the session; `usage` is the fallback."""
    by_model = data.get("modelUsage")
    if isinstance(by_model, dict) and by_model:
        return {kind: sum(int((usage or {}).get(camel) or 0) for usage in by_model.values())
                for kind, camel, _ in TOKEN_KINDS}
    usage = data.get("usage") or {}
    return {kind: int(usage.get(snake) or 0) for kind, _, snake in TOKEN_KINDS}


def step_prompt(root: Path, run: Run, step: Step, failure: list[str], config: Config | None = None) -> str | None:
    """
    The preamble and the step's own instructions — None when the step has none yet. A kind's own
    version of a shared step comes first (`intake.plan.md`); a step only one kind has may carry the
    kind in front (`book-inventory.md`), so the folder keeps a kind's prompts together; then the step's.
    """
    folder = root / PROMPTS_RELATIVE
    kind = record_kind(run.record)
    own = next((path for path in (folder / f"{step.name}.{kind}.md", folder / f"{kind}-{step.name}.md",
                                  folder / f"{step.name}.md") if path.exists()), None)
    if own is None:
        return None
    assert run.path is not None
    brief = item_brief(root, config, run) if config is not None else ""
    fields = {"run": run.id, "item": run.record.get("item") or "—", "title": run.title,
              "branch": run.record["branch"], "step": step.name,
              "run_file": run.path.relative_to(root).as_posix(), "brief": brief or "—"}
    text = (folder / "_preamble.md").read_text(encoding="utf-8") + "\n" + own.read_text(encoding="utf-8")
    if brief and "{brief}" not in text:
        # A change step's prompt reads its item itself; a plan task's item is a row the referee has read.
        text += "\n\n## The item, as the book has it\n\n{brief}"
    for key, value in fields.items():
        text = text.replace("{" + key + "}", value)
    reopened = run.record.get("reopened") or []
    if reopened:
        # What a person asked for when they reopened the run; without it, a later step never learns it.
        last = reopened[-1]
        text += (f"\n\n## Why the run was last reopened\n\nAt `{last['step']}`, {last['at'][:10]}: {last['reason']}\n\n"
                 "Do the part of that which belongs to this step. If it changes a file no Plan task owns, "
                 "add the file to the task that owns the work, in the Plan section.")
    if failure:
        text += ("\n\n## The referee's last verdict on this step\n\nThe step was checked and did not pass:\n"
                 + "\n".join(f"- {line}" for line in failure) + "\n\nFix what it says, then check again.")
    return text


LAUNCH_ATTEMPTS = 4


def session_environment(root: Path) -> dict[str, str]:
    """
    The environment a Claude session runs in: the person's, with git pointed at the run's own hooks,
    `.githooks` in its checkout. A worktree inherits the main checkout's absolute core.hooksPath, and a
    hook installed there locally — graphify's post-commit, which rebuilds a 1,000-file graph — cost
    R-022 four to five minutes on every commit. The repository's own hooks, the checks every commit
    must pass, still run: they are the ones in `.githooks`.
    """
    env = dict(os.environ)
    hooks = root / ".githooks"
    if (hooks / "pre-commit").is_file():
        index = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
        env["GIT_CONFIG_COUNT"] = str(index + 1)
        env[f"GIT_CONFIG_KEY_{index}"] = "core.hooksPath"
        env[f"GIT_CONFIG_VALUE_{index}"] = hooks.resolve().as_posix()
    return env


def launch_claude(argv: list[str], root: Path, prompt: str,
                  on_line: Callable[[str], None] | None = None) -> subprocess.CompletedProcess:
    """
    Start the CLI, resolving it afresh each time. The demo run met Claude Code updating itself
    mid-drive: for a moment its executable did not exist, and the launch failed. With `on_line`,
    each line it prints is handed over as it comes (a streamed session, shown to a person).
    """
    env = session_environment(root)
    for attempt in range(LAUNCH_ATTEMPTS):
        if not argv[0].endswith(".py") and not Path(argv[0]).is_file():
            argv = [shutil.which(Path(argv[0]).stem) or argv[0], *argv[1:]]
        try:
            if on_line is not None:
                return run_streaming(argv, root, TURN_SECONDS, prompt, on_line, env)
            return run_bounded(argv, root, TURN_SECONDS, stdin=prompt, env=env)
        except FileNotFoundError:
            time.sleep(10 * (attempt + 1))
        except subprocess.TimeoutExpired as expired:
            raise FactoryError(f"The session did not answer within {TURN_SECONDS // 60} minutes; "
                               "the drive stops for a person.") from expired
    raise FactoryError(f"Could not start {argv[0]} after {LAUNCH_ATTEMPTS} attempts — is the claude CLI on PATH, "
                       "or still updating?")


# What the claude CLI answers to `--resume` of a session it never created.
NO_SESSION = "No conversation found"


def claude_turn(root: Path, config: Config, session: str, first: bool, prompt: str,
                step: Step | None = None) -> SessionTurn:
    """One headless turn of the run's working session, at the step's model and effort. The prompt goes on stdin."""
    drive = config.raw["drive"]
    # A .py stands in for the CLI in the self-tests; anything else is the claude executable.
    program = [sys.executable, drive["claude"]] if drive["claude"].endswith(".py") else \
        [shutil.which(drive["claude"]) or drive["claude"]]
    # A person watching sees the session work: it streams, and each thing it does is shown as it does it.
    streaming = PROGRESS.enabled
    argv = [*program, "-p", "--output-format", "stream-json" if streaming else "json",
            *(["--verbose"] if streaming else []), "--permission-mode", drive["permission_mode"],
            # No MCP server either: the person's own account connectors (Gmail, Calendar) loaded into
            # every dry-run session and asked to be authorised (the dry run's F-04).
            "--setting-sources", drive["setting_sources"], "--strict-mcp-config",
            "--max-budget-usd", str(drive["max_budget_usd_per_step"]),
            "--allowedTools", *drive["allowed_tools"],
            # Its commands are judged against Bash patterns; a PowerShell call never matches one and is
            # refused, a turn lost each time (the dry runs).
            "--disallowedTools", "PowerShell",
            *(["--session-id", session] if first else ["--resume", session])]
    model, effort = config.session_settings(step)
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    started = time.monotonic()
    with PROGRESS.waiting(f"🤖 Claude is working on {step.name if step else 'the run'}"):
        done = launch_claude(argv, root, prompt, watch_session(root) if streaming else None)
        if not first and NO_SESSION in f"{done.stdout}\n{done.stderr}":
            # A drive stopped in a turn's first seconds named a session the CLI never created: start it under that
            # name instead of failing every resume until a person reopens the step (the demo review).
            fresh = ["--session-id" if word == "--resume" else word for word in argv]
            done = launch_claude(fresh, root, prompt, watch_session(root) if streaming else None)
    try:
        data = session_result(done.stdout)
    except ValueError as error:
        raise FactoryError(f"The working session did not answer in JSON (exit {done.returncode}): "
                           f"{(done.stderr or done.stdout)[-400:]}") from error
    refuse_on_usage_limit(data, "the working session")
    turns = int(data.get("num_turns") or 0)
    return SessionTurn(
        ok=not data.get("is_error"),
        result=str(data.get("result") or "")[-2000:],
        cumulative_usd=float(data.get("total_cost_usd") or 0),
        context_tokens=session_context_tokens(session, data.get("usage") or {}, turns),
        turns=turns,
        seconds=round(time.monotonic() - started, 1),
        tokens=session_tokens(data),
        model=model or "default",
        effort=effort or "default",
    )


def context_of(usage: dict) -> int:
    return sum(int(usage.get(key) or 0) for key in
               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def session_context_tokens(session: str, total_usage: dict, turns: int) -> int:
    """
    How full the session's window is now: the last assistant turn's usage in its
    transcript. The CLI's own `usage` sums every turn of the call — R-002 read 867k
    from it while the window held 70k — so it is only the fallback, averaged.
    """
    for transcript in (Path.home() / ".claude" / "projects").glob(f"*/{session}.jsonl"):
        last = 0
        for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            message = entry.get("message") if entry.get("type") == "assistant" else None
            if isinstance(message, dict) and message.get("usage"):
                last = context_of(message["usage"])
        if last:
            return last
    return context_of(total_usage) // max(turns, 1)


def progress_signature(root: Path, config: Config, messages: list[str]) -> str:
    """
    The same failure on the same code twice is no progress. The run files are not the code: the
    referee rewrites them after every attempt (its cost, its log), which hid every repeat (the fourth review).
    """
    return sha256_text("\n".join(messages) + head(root)
                       + git_out(root, "diff", "HEAD", "--", ".", f":(exclude){config.runs_dir}"))


def add_tokens(entry: dict, tokens: dict) -> None:
    totals = entry.setdefault("tokens", {})
    for kind, _, _ in TOKEN_KINDS:
        totals[kind] = totals.get(kind, 0) + max(int(tokens.get(kind) or 0), 0)


def carry_legacy_session(run: Run) -> None:
    """
    A record from before sessions were kept has one session and its total. Name that session as
    the total's owner, before any other session is started — a new session must count from zero,
    not from the old one's total (the fourth review: $15 of history hid a new session's first $15).
    """
    cost = run.record.get("cost")
    if cost and "sessions" not in cost and run.record.get("session"):
        cost["sessions"] = {run.record["session"]: {"usd": cost.get("session_usd", 0.0), "tokens": None}}


def add_cost(run: Run, step: str, turn: SessionTurn, session: str | None = None) -> None:
    """
    A turn's share of its session: the CLI reports running totals for the session, so the
    turn's cost is the difference from what that session had reported before. With a session
    per step (`[drive] session = "step"`) each session keeps its own baseline.
    """
    cost = run.record.setdefault("cost", {"session_usd": 0.0, "steps": {}})
    session = session or run.record.get("session") or ""
    sessions = cost.setdefault("sessions", {})
    before = sessions.get(session) or {"usd": 0.0, "tokens": {}}
    if before.get("tokens") is None:
        # The one session of a record from before sessions were kept (see carry_legacy_session):
        # its tokens were never counted, so its first turn here counts none rather than all of them.
        before = {"usd": before["usd"], "tokens": dict(turn.tokens)}
    delta = max(turn.cumulative_usd - before["usd"], 0.0)
    sessions[session] = {"usd": round(max(turn.cumulative_usd, before["usd"]), 4), "tokens": dict(turn.tokens)}
    cost["session_usd"] = round(cost["session_usd"] + delta, 4)
    entry = cost["steps"].setdefault(step, {"usd": 0.0, "turns": 0, "seconds": 0.0, "attempts": 0})
    entry["usd"] = round(entry["usd"] + delta, 4)
    entry["turns"] += turn.turns
    entry["seconds"] = round(entry["seconds"] + turn.seconds, 1)
    entry["attempts"] += 1
    # What the step ran at, so a run's cost can be read against its settings.
    entry["model"], entry["effort"] = turn.model, turn.effort
    add_tokens(entry, {kind: turn.tokens.get(kind, 0) - before["tokens"].get(kind, 0) for kind, _, _ in TOKEN_KINDS})


def add_agent_cost(run: Run, agent: str, usd: float, tokens: dict) -> None:
    """A referee-launched agent (the reviewer, the requirements auditor): one fresh session per call."""
    cost = run.record.setdefault("cost", {"session_usd": 0.0, "steps": {}})
    entry = cost.setdefault("agents", {}).setdefault(agent, {"usd": 0.0, "calls": 0})
    entry["usd"] = round(entry["usd"] + usd, 4)
    entry["calls"] += 1
    add_tokens(entry, tokens)


def total_usd(cost: dict | None) -> float:
    """The working session and every agent the referee launched, at list price."""
    if not cost:
        return 0.0
    return round(cost.get("session_usd", 0.0) + sum(a["usd"] for a in cost.get("agents", {}).values()), 4)


def total_tokens(cost: dict | None) -> dict:
    totals: dict = {}
    for entry in [*(cost or {}).get("steps", {}).values(), *(cost or {}).get("agents", {}).values()]:
        add_tokens(totals, entry.get("tokens") or {})
    return totals.get("tokens", {})


def prepare_test_database(config: Config) -> None:
    """
    Before a drive: the integration tests' database. With `[test_database]` in factory.toml, and its
    variable not already set, Docker must be running — R-003 learned it was not five minutes in, from a
    test run that executed nothing — and one long-lived container serves every test run in the drive,
    the session's and the referee's (each run still creates and drops its own databases on it): a new
    container per run cost 15–40 s, eight to twelve times a run. If the container cannot start, the tests
    start their own as before.
    """
    settings = config.raw.get("test_database")
    if not settings or os.environ.get("FACTORY_TEST_DATABASE", "").lower() == "off":
        return
    variable = settings["env"]
    if os.environ.get(variable):
        PROGRESS.say(f"🐘 The tests use the database {variable} names")
        return
    docker = shutil.which("docker")
    if docker is None:
        raise FactoryError(f"The integration tests need Docker, or {variable} set to a PostgreSQL test server. "
                           "Install or start Docker, then drive again.")
    here = Path.cwd()
    with PROGRESS.waiting("🐳 Checking Docker is running"):
        alive = run_bounded([docker, "info", "--format", "{{.ServerVersion}}"], here, 120)
    if alive.returncode != 0:
        raise FactoryError(f"Docker is not running, and the integration tests need it (or {variable}). "
                           "Start Docker Desktop, then drive again.")
    name, port = settings["container"], int(settings["port"])
    state = run_bounded([docker, "ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.State}}"], here, 60)
    with PROGRESS.waiting(f"🐘 Starting the test database {name}"):
        if not state.stdout.strip():
            started = run_bounded([docker, "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:5432",
                                   "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-e", f"POSTGRES_DB={settings['database']}",
                                   settings["image"]], here, 300)
        elif state.stdout.strip() != "running":
            started = run_bounded([docker, "start", name], here, 120)
        else:
            started = subprocess.CompletedProcess([], 0, "", "")
        ready = started.returncode == 0
        for _ in range(30 if ready else 0):
            probe = run_bounded([docker, "exec", name, "pg_isready", "-U", "postgres", "-d", settings["database"]],
                                here, 30)
            if probe.returncode == 0:
                break
            time.sleep(1)
        else:
            ready = False
    if not ready:
        PROGRESS.say(f"   ⚠️  {name} did not start ({(started.stderr or '').strip()[:120]}); "
                     "the tests start their own database each run, as before")
        return
    os.environ[variable] = (f"Host=127.0.0.1;Port={port};Database={settings['database']};Username=postgres")
    PROGRESS.say(f"🐘 Test database {name} ready — every test run in this drive reuses it")


def closed(run: Run) -> bool:
    """Closed by a person (abandoned): unlike `done`, which every step passing makes, nothing more is driven."""
    return run.record["status"] not in ("open", "done")


def no_longer_open(run: Run) -> str:
    return f"{run.id} is {run.record['status']}, so the drive stops: nothing more is checked or asked."


def closed_meanwhile(root: Path, config: Config, run: Run) -> str:
    """
    The run as the disk holds it now, after a check that took minutes: a person may have abandoned it from another
    terminal or the app meanwhile, and writing the run read before the check would put it back (the 15 Oct
    review's #4). The message to stop with, or "" while it is still open.
    """
    now = active_run(root, config, run.id)
    return no_longer_open(now) if closed(now) else ""


def command_drive(root: Path, config: Config, args: argparse.Namespace) -> tuple[str, bool]:
    """Drive the run step by step: the AI works where a step needs it; the referee decides after each."""
    drive = config.raw["drive"]
    run = active_run(root, config, args.run)
    prepare_test_database(config)
    carry_legacy_session(run)
    log: list[str] = []
    steps_done = 0
    announced = ""
    PROGRESS.say(f"🏭 {run.id} · {run.record.get('item', '')} · {run.title} — tier {run.record['tier']['value']}; "
                 "driving to the next gate")
    while True:
        run = active_run(root, config, run.id)
        if closed(run):
            return "\n".join(log + [no_longer_open(run)]), False
        refresh_tier(root, config, run)
        step = current_step(run.record, config)
        if step is not None and step.name != announced:
            announced = step.name
            PROGRESS.say("")
            PROGRESS.say(step_banner(run, step, config))
        if step is None:
            write_run(root, config, run)
            return "\n".join(log + [f"{run.id}: every step has passed."]), True
        if step.gate:
            write_run(root, config, run)
            PROGRESS.say(f"✋ Your turn — {gate_instruction(step)}.")
            PROGRESS.say("")
            return "\n".join(log + [f"{run.id} waits at {step.name}: {gate_instruction(step)}."]), True
        if args.steps and steps_done >= args.steps:
            return "\n".join(log + [f"{run.id}: stopped after {steps_done} step(s), as asked; next is {step.name}."]), True
        drift = gate_drift(run, root)
        if drift:
            write_run(root, config, run)
            return "\n".join(log + [f"{run.id} stops at {step.name}:"] + [f"  - {m}" for m in drift]), False
        missing = not_built(root, config, step)
        if missing:
            # Neither the referee nor a working session can do a step whose check is not built: a session
            # would be paid to fail it max_attempts times.
            write_run(root, config, run)
            return "\n".join(log + [f"{run.id} stops at {step.name}:", f"  - {missing}"]), False
        failure: list[str] = []
        if step.check == "book-audit" and run.section_text("Escalation"):
            # The audit escalated: asking it again answers nothing until a person reopens the run.
            write_run(root, config, run)
            return "\n".join(log + [f"{run.id} waits on a person at {step.name}: read the Escalation section, "
                                    f"then `factory reopen {audited_step(run)} --reason \"…\"`."]), False
        if step.auto:
            # The referee's own step — build, run, read, or launch a judge. A working session is
            # paid for only when the check fails and something must be fixed (R-002 spent $1.66 of
            # session time at integrate and verify just to ask for the check).
            try:
                outcome = run_check(root, config, run, step)
            except UsageLimit:
                raise
            except FactoryError as error:
                # The check could not run — a judge that did not answer, a refusal — which no change
                # to the code fixes: a working session would only be paid to ask again.
                write_run(root, config, run)
                return "\n".join(log + [f"{run.id} stops at {step.name}: the referee's own check could not run. "
                                        f"A person decides:", f"  - {error}"]), False
            stopped = closed_meanwhile(root, config, run)
            if stopped:
                return "\n".join(log + [stopped]), False
            if outcome.ok:
                run.record.setdefault("steps", {})[step.name] = {"status": "passed", "at": utc_now(), "head": head(root)}
            write_run(root, config, run)
            log.append(f"{run.id} · {step.name} · checked by the referee: {'passed' if outcome.ok else 'not yet'}")
            if outcome.ok:
                steps_done += 1
                continue
            answered = answer_book_audit(root, config, run, step)
            if answered is not None:
                said, stop = answered
                log.append(said)
                if stop:
                    return "\n".join(log), False
                PROGRESS.say(f"   🔁 {said}")
                continue
            failure = outcome.messages
        if step.name == "build":
            installed = install_approved_packages(root, config, run)  # the approved list, before any code
            if installed:
                log.append(installed)
                PROGRESS.say(f"   📦 {installed}")
        previous: str | None = None
        for attempt in range(1, drive["max_attempts"] + 1):
            prompt = step_prompt(root, run, step, failure, config)
            if prompt is None:
                return "\n".join(log + [f"{run.id} is at {step.name}, which has no instructions in "
                                        f"{PROMPTS_RELATIVE} yet — a person does it, then `factory check`."]), True
            if drive.get("session") == "step":
                # A fresh session per step: the run file is the run's memory, and a session carried
                # across steps re-reads every earlier step on every turn — R-002 re-read 22.3M tokens
                # of cached context to write 0.1M. A reopened step starts afresh too.
                key = f"{step.session or step.name}#{len(run.record.get('reopened') or [])}"
                by_step = run.record.setdefault("step_sessions", {})
                first = key not in by_step
                session = by_step.setdefault(key, str(uuid.uuid4()))
            else:
                first = not run.record.get("session")
                session = run.record.get("session") or str(uuid.uuid4())
            earlier_session = run.record.get("session")
            run.record["session"] = session
            run.record["driving"] = {"session": session, "step": step.name, "attempt": attempt, "at": utc_now(), "pid": os.getpid()}
            write_run(root, config, run)
            try:
                turn = claude_turn(root, config, session, first, prompt, step)
            except FactoryError as error:
                # The session never answered, or not in JSON: clear the marker the Stop hook reads,
                # so a later session is not held to a drive that ended (the re-review).
                run = active_run(root, config, run.id)
                run.record["driving"] = None
                if isinstance(error, UsageLimit) and first:
                    # The limit answered before the session existed: the retry starts it afresh,
                    # rather than resuming an id the CLI never created.
                    if drive.get("session") == "step":
                        run.record.get("step_sessions", {}).pop(key, None)
                    run.record["session"] = earlier_session
                write_run(root, config, run)
                raise
            run = active_run(root, config, run.id)
            add_cost(run, step.name, turn, session)
            run.record["driving"] = None
            write_run(root, config, run)
            if closed(run):
                # A person closed it while the AI worked: its turn's cost is kept, and nothing is checked or asked again.
                return "\n".join(log + [no_longer_open(run)]), False
            said = turn.result.strip().splitlines()[-1][:160] if turn.result.strip() else "(no reply)"
            log.append(f"{run.id} · {step.name} · attempt {attempt}: {said}")
            PROGRESS.say(f"   💬 Claude: {said}")
            PROGRESS.say(f"   ⏱️  {clock(turn.seconds)} · {turn.turns} turns · ${turn.cumulative_usd:.2f} so far in this session"
                         + (f" · attempt {attempt}" if attempt > 1 else ""))
            if run.section_text("Escalation"):
                # When it asked: the Decisions log counts the wait for a person from here (the answer moves it into
                # `reopened`, as asked_at).
                run.record.setdefault("asked", {"step": step.name, "at": utc_now()})
                write_run(root, config, run)
                PROGRESS.say("   🙋 The AI stopped to ask a person — see the run's Escalation section")
                return "\n".join(log + [f"{run.id} escalated at {step.name} — a person reads the Escalation "
                                        "section and decides."]), False
            if current_step(run.record, config) != step:
                break  # the session's own `factory check` passed and advanced the run
            try:
                outcome = run_check(root, config, run, step)  # the referee's verdict, not the session's word
            except UsageLimit:
                raise
            except FactoryError as error:
                # A fixable refusal ("commit first") goes back to the session, like any failed check.
                outcome = Outcome(False, [str(error)])
            stopped = closed_meanwhile(root, config, run)
            if stopped:
                return "\n".join(log + [stopped]), False
            if outcome.ok:
                run.record.setdefault("steps", {})[step.name] = {"status": "passed", "at": utc_now(), "head": head(root)}
            write_run(root, config, run)
            if outcome.ok:
                break
            signature = progress_signature(root, config, outcome.messages)
            if signature == previous:
                return "\n".join(log + [f"{run.id} made no progress at {step.name} — the same failure on the same "
                                        "code twice. A person decides:"] + [f"  - {m}" for m in outcome.messages]), False
            previous, failure = signature, outcome.messages
        else:
            return "\n".join(log + [f"{run.id} did not pass {step.name} in {drive['max_attempts']} attempts:"]
                             + [f"  - {m}" for m in failure]), False
        steps_done += 1
        # Compact only a session that a later step resumes: with a session per step, one used by a
        # single step is never resumed, and compacting it is paid for nothing.
        # This kind's steps only: another kind's step naming the same session never resumes this run's.
        mine = config.steps_of(record_kind(run.record))
        names = [s.name for s in mine]
        resumed_later = drive.get("session") != "step" or (step.session is not None and any(
            s.session == step.session for s in mine[names.index(step.name) + 1:]))
        if resumed_later and turn.context_tokens > drive["compact_above_tokens"]:
            compacted = claude_turn(root, config, run.record["session"], False, "/compact", step)
            run = active_run(root, config, run.id)
            add_cost(run, "compaction", compacted, run.record["session"])
            write_run(root, config, run)
            log.append(f"{run.id}: compacted the session at {turn.context_tokens} tokens.")


def evidence_manifest(run: Run, config: Config) -> str:
    """One hash over every check's evidence and the configuration it ran under."""
    parts = sorted(f"{c['step']}|{c['name']}|{c.get('head', '')}|{c.get('sha256') or ''}|{c.get('config', '')}"
                   for c in run.record.get("checks", []))
    return sha256_text("\n".join(parts))


def render_pr_body(root: Path, config: Config, run: Run) -> str:
    """The pull request a person reads: every claim points at the run's evidence."""
    record = run.record
    assert run.path is not None
    link = run.path.relative_to(root).as_posix()
    kind = record_kind(record)
    if kind in (PLAN, BOOK, REVISION):
        # None of these kinds has acceptance criteria, red/green evidence or a code review: their bodies say
        # what was written, from what, who approved it, and the checks it passed.
        return {PLAN: render_plan_pr_body, BOOK: render_book_pr_body,
                REVISION: render_revision_pr_body}[kind](root, config, run, link)
    intent = run.section_text("Intent")
    problem = re.search(r"\*\*Problem\.\*\*\s*(.+)", intent)
    outcome = re.search(r"\*\*Outcome\.\*\*\s*(.+)", intent)
    chosen = re.search(r"\*\*Chosen:\*\*\s*(.+)", run.section_text("Design"))
    plan_link = re.search(r"\*\*Plan:\*\*\s*(\[[^\]]+\]\([^)]+\))", run.section_text("Design"))
    traits = ac_traits(root, config)
    green = {c["name"].removeprefix("green ") for c in record.get("checks", [])
             if c["step"] == "build" and c["name"].startswith("green ") and c["ok"]}
    red = (record.get("red") or {}).get("acs", {})
    criteria_rows = "\n".join(
        f"| **{ac}** — {text} | {', '.join(f'`{p}`' for p in traits.get(ac, [])) or '**none**'} | "
        f"{red.get(ac, '—')} | {'yes' if ac in green else '**no**'} |"
        for ac, text in acceptance_criteria(run))
    suites = [c for c in record.get("checks", []) if c["step"] == "integrate" and c.get("counts")]
    suite_lines = "\n".join(
        f"- {c['name']}: {c['counts']['passed']} passed, {c['counts']['failed']} failed, "
        f"**{c['counts']['notExecuted']} skipped** — `{(c.get('head') or '')[:10]}`" for c in suites) or "- not run yet"
    review = record.get("review") or {}
    review_line = (f"**{review.get('verdict')}** from the independent `factory-reviewer`, "
                   f"{len(review.get('findings', []))} finding(s) — see the run's Review section."
                   if review else "See the run's Review section.")
    changed = changed_paths(root, record["base"])
    impact_settings = config.raw.get("impact", {})
    one_way = any(re.search(pattern, path) for pattern in impact_settings.get("one_way", []) for path in changed)
    rollback = (impact_settings.get("one_way_note", "Contains a change reverting cannot undo.") if one_way
                else "Nothing one-way: reverting the merge commit is a complete rollback.")
    return f"""## Summary

{problem.group(1).strip() if problem else run.title}

{outcome.group(1).strip() if outcome else ''}

Built through factory run [{run.id}]({link}) for {record.get('item') or '—'} · tier **{record['tier']['value']}**
· evidence manifest `{evidence_manifest(run, config)[:16]}`.

## What changed, and why this way

{('Design: ' + plan_link.group(1)) if plan_link else ('Chosen: ' + chosen.group(1).strip()) if chosen else 'See the run.'}
The rejected alternatives and the task plan are in the run file.

## Acceptance criteria

| Criterion | Proven by | Red before the build | Green |
|---|---|---|---|
{criteria_rows}

## Review

{review_line}

## Test plan

Run by the referee at the commits shown, never on a session's word:

{suite_lines}

Re-run: the suites above through the `{adapter(config)['name']}` adapter in `devtools/factory/factory.toml`,
the integration suite filtered by `{config.raw.get('commands', {}).get('integration_filter') or 'nothing'}` and the modules
in the run's Context; `python3 devtools/factory/factory.py verify`.

## Dev testing & notes for FE/QA

**Dev testing:** the Test plan above — counts with the skip count, per commit. Checked by
hand on DEV/QA: *the author fills this in after deploying.*

**Notes for FE/QA:** the documents this change updated are in the run's Context and
Plan; deviations from the FE contract: *the author confirms, or writes "none".*

## Rollback

{rollback}
"""


def gate_line(record: dict, gate: str) -> str:
    entry = (record.get("gates") or {}).get(gate)
    if not entry:
        return f"`{gate}` — **not approved yet**."
    return f"`{gate}` — approved by {entry.get('by') or '—'}, {str(entry.get('at') or '')[:10]}" + (
        f" (`{entry['file']}` as approved)" if entry.get("file") else "") + "."


def check_lines(record: dict, steps: tuple[str, ...]) -> str:
    """The referee's recorded checks for these steps — the command, its result and the commit it ran at."""
    lines = [f"- {c['step']} · `{c.get('command') or c['name']}` — {'passed' if c.get('ok') else '**failed**'}"
             f" at `{(c.get('head') or '')[:10]}`" for c in record.get("checks", []) if c["step"] in steps]
    return "\n".join(lines) or "- none recorded yet"


def cost_line(record: dict) -> str:
    cost = record.get("cost")
    return f"${total_usd(cost):.2f} of AI time at list price (the run's Cost table)." if cost else "No AI time recorded."


def render_plan_pr_body(root: Path, config: Config, run: Run, link: str) -> str:
    """A plan run's pull request: the plan, what it was written from, its gate, and the checks it passed."""
    record = run.record
    design = run.section_text("Design")
    plan_link = re.search(r"\*\*Plan:\*\*\s*(\[[^\]]+\]\([^)]+\))", design)
    rel = plan_path(config, plan_slug(run)) if plan_slug(run) else ""
    plan_text = read_text(root, rel) if rel else ""
    tasks = plan_task_rows(plan_text) or []
    questions = sorted(set(re.findall(r"\bQ\d+\b", plan_text + "\n" + run.section_text("Sources"))),
                       key=lambda q: int(q[1:]))
    sources = run.section_text("Sources").strip() or "*The run's Sources section is empty.*"
    check = backlog_settings(config).get("plan_check", "")
    return f"""## Summary

The plan for **{run.title}** — `{record.get('item') or '—'}`, written on demand through factory run
[{run.id}]({link}). No code changes: the plan is what the feature's tasks will be built from.

- Plan: {plan_link.group(1) if plan_link else (f'`{rel}`' if rel else 'see the run')} — {len(tasks)} task(s) in its §9 Tasks.
- Questions it names: {', '.join(questions) or 'none'}.
- {cost_line(record)}

## What it was written from

{sources}

## Approval

- {gate_line(record, PLAN_GATE)}
- The merge gate is this pull request: approved by someone other than its author.

## Test plan

Run by the referee, never on a session's word:

{check_lines(record, ('sources', 'draft-plan', 'package'))}

Re-run: `{check or 'the roll-up check'}`, `python3 devtools/checks/check-book.py`, and
`python3 devtools/factory/factory.py verify`.

## Rollback

Documentation only: reverting the merge commit removes the plan and its row's link.
"""


def render_book_pr_body(root: Path, config: Config, run: Run, link: str) -> str:
    """A book run's pull request: project.md from the FSD — coverage, questions, the audit, the gate, the checks."""
    record = run.record
    tracker = config.raw.get("requirements", {}).get("tracker", "docs/project.md")
    outline = record.get("outline") or {}
    dispositions: dict[str, int] = {}
    questions: list[str] = []
    for line in run.section_text("Inventory").splitlines():
        match = INVENTORY_LINE.match(line)
        if match and " — " in match.group("rest"):
            said = match.group("rest").rpartition(" — ")[2].strip()
            word = said.split(" ", 1)[0].lower()
            dispositions[word] = dispositions.get(word, 0) + 1
            questions += re.findall(r"\bQ\d+\b", said) if word == "empty" else []
    text = read_text(root, tracker)
    phases = re.findall(r"^#### (Phase \d+ — .+)$", text, re.MULTILINE)
    rows, in_phase = 0, False
    for line in text.splitlines():
        if line.startswith("#"):
            in_phase = line.startswith("#### Phase ")
        elif in_phase and line.startswith("|") and not line.startswith(("| Feature ", "|---")):
            rows += 1
    counted = " · ".join(f"{dispositions[word]} {word}" for word in DISPOSITIONS if word in dispositions) or "none recorded"
    audit = record.get("book_audit") or {}
    rewrites = len([r for r in record.get("reopened") or [] if r.get("by") == "book-audit"])
    return f"""## Summary

The project's backlog, `{tracker}`, written from its FSD through factory run [{run.id}]({link})
(`{record.get('item') or '—'}`). Every feature row cites the FSD section it comes from; the referee
checked the citations, the coverage and the order itself, and an independent auditor read it against the FSD.

- FSD: `{outline.get('file', '—')}` — {outline.get('units', '—')} sections, read by the referee from its headings ({outline.get('format', '—')}).
- Inventory: {counted}.
- {rows} feature row(s) in {len(phases)} phase(s): {'; '.join(phases) or '—'}.
- Questions for the client from empty sections: {', '.join(dict.fromkeys(questions)) or 'none'}.
- {cost_line(record)}

## Review

**{audit.get('verdict') or 'not run yet'}** from the independent `book-auditor`, {audit.get('findings', 0)} finding(s) in its
last report — see the run's Audit section. Rewrites of project.md that answered a NOT READY: {rewrites}.

## Approval

- {gate_line(record, OUTLINE_GATE) if outline.get('format') == 'pdf' else '`outline-gate` — not needed: the outline was read from the document itself.'}
- {gate_line(record, BOOK_GATE)}

## Test plan

Run by the referee, never on a session's word:

{check_lines(record, ('outline', 'inventory', 'features', 'audit', 'package'))}

Re-run: {', '.join(f'`{c}`' for c in book_settings(config).get('checks', [])) or 'the book checks'},
`python3 devtools/factory/check_project_md.py {tracker} "{outline.get('file', '<the FSD>')}"` and
`python3 devtools/factory/factory.py verify`.

## Rollback

Documentation only: reverting the merge commit restores the previous `{tracker}` and open questions.
"""


def render_revision_pr_body(root: Path, config: Config, run: Run, link: str) -> str:
    """A revision run's pull request: what the new FSD version changed, and what the book now says about it."""
    record = run.record
    changes = record.get("changes") or {}
    tracker = config.raw.get("requirements", {}).get("tracker", "docs/project.md")

    def ids(section: str) -> str:
        found = [re.findall(r"\bQ\d+\b", line)[:1] for line in listed_lines(run.section_text(section))]
        return ", ".join(dict.fromkeys(q for qs in found for q in qs)) or "none"

    flagged = re.findall(r"`?([\w./-]+\.md)`?", run.section_text("Flagged plans"))
    items = [line.lstrip("-* ").strip() for line in listed_lines(run.section_text("Change items"))
             if line.lstrip("-* ").strip().lower() != "none"]
    audit = record.get("book_audit") or {}
    rewrites = len([r for r in record.get("reopened") or [] if r.get("by") == "book-audit"])
    version = changes.get("version", revision_version(run))
    return f"""## Summary

FSD V{changes.get('previous', '?')} → V{version}, taken into the book through factory run [{run.id}]({link})
(`{record.get('item') or '—'}`). The referee compared the two versions itself; a session worked out what each
change means for `{tracker}`, the questions, the plans and the system overview; an independent auditor read it
against both versions; a person approved it. No code changes: a built feature whose requirement changed is a
change item, built by a run of its own.

- FSD: `{changes.get('old', '—')}` → `{changes.get('new', '—')}` — {len(changes.get('added') or [])} added · \
{len(changes.get('removed') or [])} removed · {len(changes.get('changed') or [])} changed.
- Questions answered by V{version}: {ids('Answered')}.
- New questions: {ids('New questions')}.
- Flagged plans: {', '.join(dict.fromkeys(flagged)) or 'none'} — each goes back through the plan gate.
- Change items: {'; '.join(items) or 'none'}.
- {cost_line(record)}

## Review

**{audit.get('verdict') or 'not run yet'}** from the independent `book-auditor`, {audit.get('findings', 0)} finding(s) in its
last report — see the run's Audit section. Rewrites of the triage that answered a NOT READY: {rewrites}.

## Approval

- {gate_line(record, REVISION_GATE)}

## Test plan

Run by the referee, never on a session's word:

{check_lines(record, ('changes', 'triage', 'audit', 'package'))}

Re-run: {', '.join(f'`{c}`' for c in book_settings(config).get('checks', [])) or 'the book checks'} and
`python3 devtools/factory/factory.py verify`.

## Rollback

Documentation only: reverting the merge commit restores the book, the questions and the plans as they were at V{changes.get('previous', '?')}.
"""


def command_pr_body(root: Path, config: Config, args: argparse.Namespace) -> str:
    return render_pr_body(root, config, active_run(root, config, args.run))


# ---------------------------------------------------------------- outcome and report

INTRODUCED_BY = re.compile(r"introduced by (?:factory )?run \[?(R-\d{3})", re.IGNORECASE)
DEFECT_ID = re.compile(r"^\|\s*\*\*(D\d+)\*\*")
MIN_RUNS_FOR_A_RATE = 5


def traced_defects(root: Path, config: Config) -> dict[str, list[str]]:
    """Defect rows that name the run that introduced them — in every defect log `[requirements]` lists."""
    traced: dict[str, list[str]] = {}
    for rel in config.raw.get("requirements", {}).get("defect_logs", []):
        path = root / rel
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            defect = DEFECT_ID.match(line)
            if defect:
                for run_id in INTRODUCED_BY.findall(line):
                    traced.setdefault(run_id, [])
                    if defect.group(1) not in traced[run_id]:
                        traced[run_id].append(defect.group(1))
    return traced


CLASS_TAG = re.compile(r"\*Class:\s*([a-z0-9-]+)\s*(?:·\s*found\s*(\d{4}-\d{2}-\d{2}))?\s*\*", re.IGNORECASE)


@dataclass
class ClassedDefect:
    id: str
    cls: str
    found: datetime.date | None  # None for a defect logged before rows carried a date


def classed_defects(root: Path, config: Config) -> tuple[list[ClassedDefect], list[str]]:
    """
    Every defect's class and the day it was found, from the *Class: <slug> · found <date>*
    tag in its row (the date is left out when nobody recorded it), once per id across the
    defect logs — a fixed defect has a full row in the archive and a one-line entry in the
    live log. Also the ids no row tags.
    """
    seen: dict[str, ClassedDefect] = {}
    untagged: set[str] = set()
    for rel in config.raw.get("requirements", {}).get("defect_logs", []):
        path = root / rel
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            defect = DEFECT_ID.match(line)
            if not defect:
                continue
            tag = CLASS_TAG.search(line)
            if tag:
                found = datetime.date.fromisoformat(tag.group(2)) if tag.group(2) else None
                seen[defect.group(1)] = ClassedDefect(defect.group(1), tag.group(1).lower(), found)
            else:
                untagged.add(defect.group(1))
    return (sorted(seen.values(), key=lambda d: int(d.id[1:])),
            sorted(untagged - set(seen), key=lambda d: int(d[1:])))


def class_rules(config: Config) -> dict[str, list[dict]]:
    """`[[promotion.rules]]` — each enforced rule, the class it guards, and the day it began."""
    rules: dict[str, list[dict]] = {}
    for rule in config.raw.get("promotion", {}).get("rules", []):
        rules.setdefault(rule["class"], []).append(rule)
    return rules


def recurring_classes(defects: list[ClassedDefect], config: Config, today: datetime.date) -> list[tuple[str, list[str]]]:
    """
    The classes the project is making most right now: the `propose` largest by defects found in
    the last `window_days` days, at least `threshold` each. A class with an enforced rule counts
    only defects found after its newest rule — so a class that keeps coming back past its rule
    shows the rule is not working, and one that stops shows it is. Raw counts over the life of
    a young project made every class "recurring" (all 373 defects fell inside 29 days).
    """
    settings = config.raw.get("promotion", {})
    window_start = today - datetime.timedelta(days=int(settings.get("window_days", 30)))
    threshold = int(settings.get("threshold", 3))
    since = {cls: max(datetime.date.fromisoformat(rule["since"]) for rule in rules)
             for cls, rules in class_rules(config).items()}
    by_class: dict[str, list[str]] = {}
    for defect in defects:
        if defect.found is None or defect.cls == "other" or not window_start <= defect.found <= today:
            continue
        if defect.cls in since and defect.found <= since[defect.cls]:
            continue
        by_class.setdefault(defect.cls, []).append(defect.id)
    ranked = sorted(((cls, ids) for cls, ids in by_class.items() if len(ids) >= threshold),
                    key=lambda item: (-len(item[1]), item[0]))
    return ranked[:int(settings.get("propose", 3))]


def render_promotion(root: Path, config: Config, today: datetime.date) -> list[str]:
    settings = config.raw.get("promotion", {})
    defects, untagged = classed_defects(root, config)
    known = set(settings.get("classes", []))
    unknown = sorted({d.cls for d in defects} - known - {"other"}) if known else []
    recurring = recurring_classes(defects, config, today)
    rules = class_rules(config)
    lines = ["", "## Recurring defect classes", "",
             f"{len(defects)} defect(s) carry a class. Proposed: the {settings.get('propose', 3)} classes found most "
             f"in the last {settings.get('window_days', 30)} days (at least {settings.get('threshold', 3)} each), "
             "counting a class with a rule only after its newest rule."]
    if untagged:
        lines.append(f"**{len(untagged)} defect(s) carry no class:** {', '.join(untagged[:20])}"
                     + (" …" if len(untagged) > 20 else "") + ".")
    if unknown:
        lines.append(f"**Classes not in `[promotion] classes`:** {', '.join(unknown)} — a typo, or a class to add.")
    if not recurring:
        lines.append("None recurring.")
        return lines
    lines += ["", "| Class | Found in the window | Defects | Rules already enforcing it |", "|---|---|---|---|"]
    for cls, ids in recurring:
        enforced = "; ".join(f"{rule['rule']} (since {rule['since']})" for rule in rules.get(cls, [])) or "none yet"
        lines.append(f"| {cls} | {len(ids)} | {', '.join(ids)} | {enforced} |")
    lines += ["", "To build a rule that would have caught them, as an ordinary run a person approves:", ""]
    lines += [f"    python3 devtools/factory/factory.py start class:{cls} --title \"A rule that catches {cls}\""
              for cls, _ in recurring]
    return lines


def render_report(root: Path, config: Config, runs: list[Run], today: datetime.date | None = None) -> str:
    traced = traced_defects(root, config)
    finished = [run for run in runs if run.record["status"] == "done"]
    all_usd = sum(total_usd(run.record.get("cost")) for run in runs)
    lines = ["# Factory report", "", f"{len(runs)} run(s): "
             + ", ".join(f"{sum(1 for r in runs if r.record['status'] == s)} {s}"
                         for s in ("open", "done", "abandoned") if any(r.record["status"] == s for r in runs)),
             f"Cost, all runs: ${all_usd:.2f} at API list price — the working sessions and the agents the referee "
             "launched; a subscription is not charged per token.", "",
             "| Run | Item | Tier | Step | Cost | Attempts | Reopened | Gates | Checks failed | Defects traced |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for run in sorted(runs, key=lambda r: r.id):
        record = run.record
        cost = record.get("cost") or {}
        attempts = sum(entry["attempts"] for name, entry in cost.get("steps", {}).items() if name != "compaction")
        failed = sum(1 for check in record.get("checks", []) if not check["ok"])
        lines.append(f"| {run.id} | {record.get('item') or '—'} | {record['tier']['value']} | {record.get('step')} | "
                     f"{'$%.2f' % total_usd(cost) if cost else 'by hand'} | {attempts or '—'} | "
                     f"{len(record.get('reopened', []))} | {', '.join(record.get('gates', {})) or '—'} | {failed} | "
                     f"{', '.join(traced.get(run.id, [])) or 'none'} |")
    escaped = sum(len(traced.get(run.id, [])) for run in finished)
    rate = (f"{escaped / len(finished):.2f} per finished run" if len(finished) >= MIN_RUNS_FOR_A_RATE
            else f"unknown — {len(finished)} finished run(s), fewer than {MIN_RUNS_FOR_A_RATE}")
    lines += ["", f"**Escaped defects:** {escaped} traced to finished runs; rate {rate}. A defect is traced when "
                  "its row says *Introduced by run R-nnn*.",
              f"**Escalations answered by a person:** "
              f"{sum(1 for run in runs for entry in run.record.get('reopened', []) if entry.get('escalation'))}."]
    if config.raw.get("promotion"):
        lines += render_promotion(root, config, today or datetime.datetime.now(datetime.timezone.utc).date())
    return "\n".join(lines)


def in_section(requirement_id: str, section: str, config: Config) -> bool:
    own = section_of(requirement_id, config)
    return own == section or own.startswith(section + ".")


def requirement_files(root: Path, config: Config, requirement: str) -> set[str]:
    """The source files the traceability matrix cites for a requirement id or a section."""
    traceability = root / config.raw.get("requirements", {}).get("traceability", "")
    if not traceability.is_file():
        return set()
    section = section_of(requirement, config)
    exact = requirement if is_requirement_id(requirement, config) else None
    files: set[str] = set()
    for line in traceability.read_text(encoding="utf-8", errors="replace").splitlines():
        ids = requirement_ids_in(line, config)
        hit = (exact in ids) if exact else any(in_section(i, section, config) for i in ids)
        if hit:
            # Any file name with an extension the line cites — `LabEndpoints.cs:40`, `labs.py:12`.
            files.update(re.findall(r"([A-Za-z0-9_\-]+\.[A-Za-z0-9]{1,5})(?=[:`\s)]|$)", line))
    return files


def affected_runs(root: Path, config: Config, requirement: str) -> list[tuple[Run, list[str]]]:
    """Open runs a requirement change lands on, each with why."""
    section = section_of(requirement, config)
    cited_files = requirement_files(root, config, requirement)
    found = []
    for run in load_runs(root, config):
        if run.record["status"] != "open":
            continue
        reasons = []
        for ac, _ in acceptance_criteria(run):
            if ac == requirement or (is_requirement_id(ac, config) and in_section(ac, section, config)):
                reasons.append(f"criterion {ac} belongs to §{section}")
        if re.search(rf"§\s*{re.escape(section)}\b", run.section_text("Spec") + run.section_text("Intent")):
            reasons.append(f"its Intent or Spec cites §{section}")
        touched = {path.rsplit("/", 1)[-1] for path in touches(run)}
        overlap = sorted(touched & cited_files)
        if overlap:
            reasons.append(f"it touches {', '.join(overlap)}, which the traceability matrix ties to §{section}")
        if reasons:
            found.append((run, reasons))
    return found


def command_affected(root: Path, config: Config, args: argparse.Namespace) -> tuple[str, bool]:
    hits = affected_runs(root, config, args.requirement)
    if not hits:
        return f"No open run is affected by {args.requirement}.", True
    lines = [f"{len(hits)} open run(s) affected by {args.requirement}:"]
    for run, reasons in hits:
        lines.append(f"- {run.id} ({run.record.get('item')}, branch {run.record['branch']}, at {run.record.get('step')}):")
        lines += [f"    {reason}" for reason in reasons]
        lines.append(f"    on its branch: python3 devtools/factory/factory.py reopen spec --reason "
                     f"\"{args.requirement} changed: <what changed>\"")
    return "\n".join(lines), True


def command_report(root: Path, config: Config, args: argparse.Namespace) -> str:
    return render_report(root, config, load_runs(root, config))


def command_link_pr(root: Path, config: Config, args: argparse.Namespace) -> str:
    run = active_run(root, config, args.run)
    try:
        pull_request = bitbucket_client(root).pull_request(args.number)
    except bitbucket.BitbucketError as error:
        raise FactoryError(str(error)) from error
    branch = ((pull_request.get("source") or {}).get("branch") or {}).get("name")
    if branch != run.record["branch"]:
        raise FactoryError(f"PR #{args.number} is from {branch}, not this run's branch {run.record['branch']}.")
    url = ((pull_request.get("links") or {}).get("html") or {}).get("href", "")
    run.record["pr"] = {"id": args.number, "url": url}
    write_run(root, config, run)
    return f"{run.id}: linked to PR #{args.number}."


def command_open_pr(root: Path, config: Config, args: argparse.Namespace) -> str:
    """
    Opens the run's pull request as the person running this — their own Bitbucket credentials —
    so the person who drove the run is its author and answers for it, and a teammate approves it.
    A person's command: it acts in their name. The description is the run's own (`pr-body`).
    """
    person_only("open-pr")
    run = active_run(root, config, args.run)
    if run.record.get("pr"):
        raise FactoryError(f"{run.id} is already linked to PR #{run.record['pr']['id']}: {run.record['pr'].get('url', '')}")
    branch = run.record["branch"]
    if current_branch(root) != branch:
        raise FactoryError(f"{run.id} is on {branch}; this checkout is on {current_branch(root)}.")
    pushed = git_out(root, "ls-remote", remote_name(config), f"refs/heads/{branch}").split("\t")[0]
    if pushed != head(root):
        raise FactoryError(f"origin does not have this commit on {branch} yet. Push first: "
                           f"`git push -u {remote_name(config)} {branch}` — then open the pull request.")
    client = bitbucket_client(root, personal=True)
    destination = args.destination or base_branch(config)
    try:
        # Whose credential this is. A repository or workspace access token acts as a bot user: a
        # PR it opened would have the bot as author, and the driver's own approval would then
        # pass as someone else's (the branch review). Only a person's account opens the PR.
        me = client.current_user()
        if me.get("type") != "user" or not me.get("account_id"):
            raise FactoryError("This Bitbucket credential is not a person's account (an access token?). "
                               "`open-pr` opens the PR in your name — store your own credential for bitbucket.org in git.")
        existing = client.open_pull_requests_from(branch)
        if existing:
            pull_request = existing[0]
            opened = False
        else:
            pull_request = client.create_pull_request(f"{run.record['item']}: {run.title}",
                                                      render_pr_body(root, config, run), branch, destination)
            opened = True
    except bitbucket.BitbucketError as error:
        raise FactoryError(str(error)) from error
    author = ((pull_request.get("author") or {}).get("account_id")) or ""
    if opened and author != me["account_id"]:
        raise FactoryError(f"PR #{pull_request['id']} was opened, but Bitbucket names another author. "
                           "Look at it before going on; it is not linked to the run.")
    url = ((pull_request.get("links") or {}).get("html") or {}).get("href", "")
    # The driver's account and the PR's author never count as the approval at the merge gate.
    run.record["pr"] = {"id": pull_request["id"], "url": url,
                        "opened_by": {"account_id": me["account_id"], "name": me.get("display_name", "")},
                        "not_approvers": sorted({me["account_id"], author} - {""})}
    write_run(root, config, run)
    verb = "opened" if opened else "found already open, and linked"
    return (f"{run.id}: PR #{pull_request['id']} {verb} — {url}\n"
            f"Commit and push the run file, so the pull request carries it.")


def review_marker(root: Path) -> Path:
    """Where /review-branch's last verdict is kept: inside .git, never in the repository."""
    path = Path(git_out(root, "rev-parse", "--git-path", "factory-review-verdict").strip())
    return path if path.is_absolute() else root / path


def recorded_review(root: Path) -> tuple[str, str]:
    """The last recorded verdict and the commit it was given for, or ("", "")."""
    marker = review_marker(root)
    if not marker.exists():
        return "", ""
    parts = marker.read_text(encoding="utf-8").split()
    return (parts[0], parts[1]) if len(parts) >= 2 else ("", "")


def local_ci_required(config: Config) -> bool:
    """While the hosted CI is switched off, a local CI run (`[local_ci]`) gates a pull request."""
    return bool((config.raw.get("local_ci") or {}).get("required"))


def recorded_local_ci(root: Path) -> tuple[str, str]:
    """The last result devtools/checks/ci-local.sh recorded inside .git and its commit, or ("", "")."""
    path = Path(git_out(root, "rev-parse", "--git-path", "factory-local-ci").strip())
    marker = path if path.is_absolute() else root / path
    if not marker.exists():
        return "", ""
    parts = marker.read_text(encoding="utf-8").split()
    return (parts[0], parts[1]) if len(parts) >= 2 else ("", "")


def command_reviewed(root: Path, config: Config, args: argparse.Namespace) -> str:
    """
    Records /review-branch's verdict for the current commit, so `factory pr open` can require a READY
    one (CLAUDE.md, Git: run /review-branch before opening a PR). A review covers uncommitted work too,
    so a verdict is recorded only for a clean tree — otherwise it would vouch for a commit it never saw.
    """
    if git_strict(root, "status", "--porcelain").strip():
        raise FactoryError("The tree has uncommitted changes: commit them, then review, then record.")
    marker = review_marker(root)
    marker.write_text(f"{args.verdict} {head(root)} {utc_now()}\n", encoding="utf-8")
    return f"Recorded {args.verdict} for {head(root)[:8]}."


def command_pr(root: Path, config: Config, args: argparse.Namespace) -> str:
    """
    Pull request work outside a factory run, for a session as well as a person: show a PR, list its
    comments, open one from the current branch, post one comment. The token is read inside this
    process (bitbucket.py) and goes only to Bitbucket — never printed, never in the transcript, so the
    session needs no credential of its own (CLAUDE.md, Private settings). Writes act in the person's
    name, with their own stored credential. A run's own PR stays the person's `open-pr`; nothing
    here approves, merges or declines.
    """
    action = args.action
    branch = current_branch(root)
    destination = args.destination or base_branch(config)
    if action == "open":
        if branch in config.raw["protected_branches"] or branch in ("", "HEAD"):
            raise FactoryError(f"{branch or 'a detached HEAD'} is not a work branch; open a PR from your own branch.")
        # A run's branch, or any copy of it: the branch name, or an open run file this branch changed
        # (a renamed copy carries the run's commits and its file — the review's MEDIUM).
        changed = set(git_strict(root, "diff", "--name-only", merge_base(root, config.raw["base_ref"]), "HEAD",
                              "--", config.runs_dir).splitlines())
        for run in load_runs(root, config):
            rel = f"{config.runs_dir}/{run.path.name}" if run.path else ""
            if run.record["status"] == "open" and (run.record["branch"] == branch or rel in changed):
                raise FactoryError(f"{branch} carries open factory run {run.id}: its PR is the person's "
                                   "`factory open-pr`, at the merge gate.")
        if destination in config.raw["protected_branches"] and destination != base_branch(config):
            raise FactoryError(f"A PR into {destination} is a promotion — a person's step, not this command's.")
        if not args.title.strip():
            raise FactoryError("Give the PR a --title.")
        # A git that could not reach origin is said as that, not as "push first".
        pushed = git_strict(root, "ls-remote", remote_name(config), f"refs/heads/{branch}").split("\t")[0]
        if pushed != head(root):
            raise FactoryError(f"origin does not have this commit on {branch} yet. Push first: "
                               f"`git push -u {remote_name(config)} {branch}`.")
        verdict, reviewed_at = recorded_review(root)
        if verdict != "READY" or reviewed_at != head(root):
            last = ("No verdict is recorded." if not verdict
                    else "Its last verdict was for another commit." if reviewed_at != head(root)
                    else f"It answered {verdict}.")
            raise FactoryError(f"Run /review-branch on this commit first; it must answer READY (CLAUDE.md, Git). {last}")
        if local_ci_required(config):
            result, passed_at = recorded_local_ci(root)
            if result != "PASS" or passed_at != head(root):
                last = ("No local CI result is recorded." if not result
                        else "Its last pass was for another commit." if passed_at != head(root)
                        else f"It recorded {result}.")
                raise FactoryError(f"CI is switched off: run `{(config.raw.get('local_ci') or {}).get('command') or 'the local CI'}` on this commit "
                                   f"first and paste its Local CI section into the description. {last}")
    body = ""
    if action in ("open", "comment"):
        if not args.body_file:
            raise FactoryError("Give the text with --body-file <path>.")
        body = pr_body_text(root, args.body_file)
    if action in ("comments", "comment") and not args.number:
        raise FactoryError("Name the PR with --number.")
    if action == "comment" and (not args.path or args.line < 1):
        raise FactoryError("An inline comment needs --path and --line (the line in the new file).")

    client = bitbucket_client(root, personal=action in ("open", "comment", "whoami"))
    try:
        if action == "whoami":
            # The account id `[ai_files] owners` names: a display name anyone can copy, an id no one can.
            me = client.current_user()
            return f"{me.get('display_name', '?')} — account id {me.get('account_id', '?')} ({me.get('type', '?')})"
        if action in ("open", "comment"):
            me = client.current_user()
            if me.get("type") != "user" or not me.get("account_id"):
                raise FactoryError("This Bitbucket credential is not a person's account (an access token?). "
                                   "PRs and comments go in your name — store your own credential for bitbucket.org in git.")
        if action == "show":
            if args.number:
                pull_request = client.pull_request(args.number)
            else:
                found = client.open_pull_requests_from(branch)
                if not found:
                    return f"No open PR from {branch}."
                if len(found) > 1:
                    return "Several open PRs from " + branch + ": " + ", ".join(f"#{p['id']}" for p in found)
                pull_request = found[0]
            return render_pull_request(pull_request)
        if action == "comments":
            lines, general = [], 0
            for comment in client.comments(args.number):
                if comment.get("parent") or comment.get("deleted"):
                    continue
                inline = comment.get("inline") or {}
                author = (comment.get("user") or {}).get("display_name", "")
                if inline.get("path"):
                    lines.append(f"{inline['path']}:{inline.get('to') or inline.get('from')}  #{comment['id']}  {author}")
                else:
                    general += 1
            return "\n".join([f"PR #{args.number}: {len(lines)} inline, {general} general (top-level only)", *lines])
        if action == "open":
            existing = client.open_pull_requests_from(branch)
            if existing:
                return f"Already open: {render_pull_request(existing[0])}"
            pull_request = client.create_pull_request(args.title.strip(), body, branch, destination)
            author = ((pull_request.get("author") or {}).get("account_id")) or ""
            if author != me["account_id"]:
                raise FactoryError(f"PR #{pull_request['id']} was opened, but Bitbucket names another author. Look at it.")
            return f"Opened {render_pull_request(pull_request)}"
        posted = client.post_comment(args.number, body, args.path, args.line)
        return f"Comment #{posted.get('id', '?')} posted on PR #{args.number} at {args.path}:{args.line}."
    except bitbucket.BitbucketError as error:
        raise FactoryError(str(error)) from error


def pr_body_text(root: Path, body_file: str) -> str:
    """
    The text `pr open` and `pr comment` publish, read only from a file a session may write — the
    repository or the temp folder (the scratchpad is under it) — and never one the guard calls private,
    nor holding a secret or a home path. What this posts, every reviewer on the PR reads.
    """
    hooks = str(Path(__file__).resolve().parent / "hooks")
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    import guard  # the guard's own definitions of private and secret: one place, not two
    path = Path(body_file).expanduser().resolve()
    in_repo = path.is_relative_to(root.resolve())
    in_temp = path.is_relative_to(Path(tempfile.gettempdir()).resolve())
    # Inside the repository, a git-ignored file is local-only — a `.env`, an overlay — never PR text.
    ignored = in_repo and git(root, "check-ignore", "-q", str(path))[0] == 0
    if guard.is_private(str(path)) or not (in_repo or in_temp) or ignored:
        raise FactoryError(f"{body_file}: a PR's text comes from a tracked file in the repository or the "
                           "session's scratchpad — never from a private, personal or git-ignored location.")
    if not path.is_file():
        raise FactoryError(f"{body_file} does not exist.")
    body = path.read_text(encoding="utf-8").strip()
    if not body:
        raise FactoryError(f"{body_file} is empty.")
    if guard.secret_findings("pull request text", body):
        raise FactoryError(f"{body_file} holds what looks like a secret or a home-folder path. "
                           "Nothing like that goes on a pull request.")
    return body


def render_pull_request(pull_request: dict) -> str:
    """One PR in a few lines — what a session needs, and nothing that could carry a credential."""
    def name(side: str) -> str:
        return ((pull_request.get(side) or {}).get("branch") or {}).get("name", "?")
    commit = (((pull_request.get("source") or {}).get("commit") or {}).get("hash") or "")[:12]
    url = ((pull_request.get("links") or {}).get("html") or {}).get("href", "")
    author = (pull_request.get("author") or {}).get("display_name", "")
    return (f"PR #{pull_request.get('id')} [{pull_request.get('state', 'OPEN')}] {pull_request.get('title', '')}\n"
            f"  {name('source')} → {name('destination')} · head {commit or '?'} · by {author or '?'}\n  {url}")


def command_protection(root: Path, config: Config, args: argparse.Namespace) -> tuple[str, bool]:
    wanted = config.raw["protection"]
    try:
        rules = bitbucket_client(root).branch_restrictions()
    except bitbucket.BitbucketError as error:
        raise FactoryError(str(error)) from error
    missing, exemptions = bitbucket.protection_gaps(rules, wanted)
    lines = [f"{len(rules)} branch restrictions read back from Bitbucket."]
    lines += [f"  {branch}: missing {', '.join(kinds)}" for branch, kinds in missing.items()] or \
             ["  Every decided rule is live."]
    if exemptions:
        lines.append("Exemptions — a person should know these are intended:")
        lines += [f"  {entry}" for entry in exemptions]
    return "\n".join(lines), not (args.check and missing)


def command_pr_check(root: Path, config: Config, args: argparse.Namespace) -> tuple[str, bool]:
    number = args.number or int(os.environ.get("BITBUCKET_PR_ID", "0") or 0)
    if not number:
        raise FactoryError("Name the PR, or run inside a pull-request pipeline (BITBUCKET_PR_ID).")
    try:
        pull_request = bitbucket_client(root).pull_request(number)
    except bitbucket.BitbucketError as error:
        raise FactoryError(str(error)) from error
    problems = bitbucket.description_problems(pull_request, local_ci_required(config))
    if problems:
        return f"PR #{number}'s description: " + "; ".join(problems) + " (CLAUDE.md, Git).", False
    return f"PR #{number}'s description has what CLAUDE.md asks for.", True


def ai_files_settings(root: Path, config: Config, destination: str) -> tuple[dict, str]:
    """
    `[ai_files]` as the destination branch has it, never as the pull request does: a PR that added
    its author to `owners`, or narrowed `paths`, would otherwise approve itself (the harness review).
    Only before the destination has the section at all — the PR that brings it — is the branch's own
    used, and the step says so.
    """
    code, text = git(root, "show", f"{remote_name(config)}/{destination}:{CONFIG_RELATIVE}")
    if code == 0:
        try:
            settings = tomllib.loads(text).get("ai_files") if tomllib else None
        except ValueError:
            raise FactoryError(f"{remote_name(config)}/{destination}'s factory.toml is not valid TOML.") from None
        if settings:
            return settings, ""
    return (config.raw.get("ai_files") or {},
            f" (origin/{destination} has no [ai_files] yet, so this branch's own list was used)")


def ai_files_changed(root: Path, settings: dict, base: str) -> list[str]:
    """
    The files this branch changed that steer the AI (`[ai_files] paths`, less `except`). A rename
    names both its paths: with git's rename detection only the new one showed, so moving
    `.claude/settings.json` out of the paths passed as nothing changed.
    """
    return [path for path in git_strict(root, "diff", "--no-renames", "--name-only", base, "HEAD").splitlines()
            if any(glob_match(path, glob) for glob in settings.get("paths", []))
            and not any(glob_match(path, glob) for glob in settings.get("except", []))]


def command_ai_files(root: Path, config: Config, args: argparse.Namespace) -> tuple[str, bool]:
    """
    CI, on every pull request: a change to the files that steer the AI — its rules, skills, the
    referee, the guard, the checks, the hooks, CI — is made or approved by an owner: an owner's own pull
    request passes, and anyone else's passes once an owner approved it after its latest commit (decided by
    the owner, 2026-10-04; Bitbucket never lets an author approve their own PR, so making it is approving
    it). The guard asks before a session edits them; this holds for a person editing by hand too. An
    approval arrives after the build, so the owner re-runs this step.
    """
    destination = os.environ.get("BITBUCKET_PR_DESTINATION_BRANCH") or base_branch(config)
    settings, source = ai_files_settings(root, config, destination)
    owners = {owner["account_id"]: owner["name"] for owner in settings.get("owners", [])}
    who = ", ".join(owners.values()) or "an owner ([ai_files] owners is empty)"
    base = git_out(root, "merge-base", f"{remote_name(config)}/{destination}", "HEAD")
    if not base:
        return (f"Could not check — no merge base with origin/{destination}, so the changed files are unknown. "
                "Fetch it and run again."), False
    touched = ai_files_changed(root, settings, base)
    if not touched:
        return f"No file that steers the AI changed{source}.", True
    listing = ", ".join(touched[:8]) + (f" and {len(touched) - 8} more" if len(touched) > 8 else "")
    number = args.number or int(os.environ.get("BITBUCKET_PR_ID", "0") or 0)
    if not number:
        return (f"{len(touched)} file(s) that steer the AI changed ({listing}): {who} must approve the pull "
                f"request{source}."), False
    try:
        client = bitbucket_client(root)
        pull_request, activity = client.pull_request(number), client.activity(number)
    except (bitbucket.BitbucketError, FactoryError) as error:
        return (f"Could not check PR #{number}: {error}. The step needs the secured variable BITBUCKET_TOKEN "
                "(Pull requests: Read)."), False
    # Made or approved by an owner (CLAUDE.md, Git). The owners come from the destination branch, so a PR
    # that names its own author an owner is still judged by the list it would change.
    # Made by an owner means every push to it was an owner's, not only that one opened it: anyone else's
    # commits on an owner's PR need an owner's approval after the latest commit, like any other PR's.
    author = (pull_request.get("author") or {}).get("account_id", "")
    pushers = {((entry["update"].get("author") or {}).get("account_id") or "") for entry in activity
               if "update" in entry}
    if author in owners and pushers and pushers <= owners.keys():
        return (f"PR #{number} changes {len(touched)} file(s) that steer the AI; its author, {owners[author]}, "
                f"is an owner and every push to it was an owner's{source}."), True
    reviewers = owners
    if not reviewers:
        return (f"PR #{number} changes {len(touched)} file(s) that steer the AI, and [ai_files] owners is empty"
                f"{source}: nobody can approve it."), False
    commit = os.environ.get("BITBUCKET_COMMIT") or head(root)
    cutoff = bitbucket.parse_time(bitbucket.commit_arrived(activity, commit))
    if cutoff is None:
        # Without the time the commit arrived, no approval can be shown to come after it — so none
        # counts, as at the merge gate (the harness review: here an old approval used to pass).
        return (f"Could not check — PR #{number}'s activity does not show commit {commit[:12]} arriving yet, so no "
                "approval can be dated after it. Re-run this step in a minute."), False
    for participant in pull_request.get("participants", []):
        user = participant.get("user") or {}
        when = bitbucket.parse_time(participant.get("participated_on") or "")
        if participant.get("approved") and user.get("account_id") in reviewers and when and when >= cutoff:
            return (f"PR #{number} changes {len(touched)} file(s) that steer the AI; "
                    f"{reviewers[user['account_id']]} approved it{source}."), True
    return (f"PR #{number} changes {len(touched)} file(s) that steer the AI ({listing}). {who} must approve it "
            "after its latest commit; then re-run this step."), False


def command_reopen(root: Path, config: Config, args: argparse.Namespace) -> str:
    """Clears a step and everything after it — steps, gates, checks — so they are re-proven.
    It only ever makes a run stricter, so the session may do it; the reason is recorded."""
    run = active_run(root, config, args.run)
    step, moved = args.step, ""
    names = [s.name for s in config.steps_of(record_kind(run.record))]
    now = current_step(run.record, config)
    done = step in (run.record.get("steps") or {}) or step in (run.record.get("gates") or {})
    if step in names and not done and now is not None and now.name in names and names.index(step) > names.index(now.name):
        # a reopen meant for a finished run landed on the next one, at a step it had not reached —
        # it cleared nothing, but recorded another run's answer as this one's.
        raise FactoryError(f"{run.id} has not reached {step}; it is at {now.name}. Reopen a step it has done — or name "
                           "the run you mean with --run.")
    proof = proof_entry(root, config, run, getattr(args, "proof", None))
    if "build" in names and step in names and names.index(step) > names.index("build"):
        # Each criterion is proven green only at the build step: when the code has changed since that proof, a
        # reopen after it would leave the acceptance check nothing it may count (trial R-014, after a merge).
        packaged = packaged_head(run)
        stale = [c["name"] for c in run.record.get("checks", []) if c["step"] == "build" and c["name"].startswith("green ")
                 and c.get("ok") and not evidence_still_holds(root, config, c.get("head", ""), head(root), packaged)]
        if stale:
            step = "build"
            moved = (f" The code has changed since each criterion was proven green, so it starts at build instead of "
                     f"{args.step}: the proof is made again on the code as it is now.")
    cleared = reopen_steps(run, config, step, args.reason)
    # Who answered, and the copy of the question they saw: an AI session may reopen too, and says so.
    run.record["reopened"][-1].update({"person": "an AI session" if launched_by_claude() else git_identity(root), **proof})
    write_run(root, config, run)
    return f"{run.id}: reopened at {step}; {', '.join(cleared)} must be proven again.{moved}"


def reopen_steps(run: Run, config: Config, step: str, reason: str, by: str | None = None) -> list[str]:
    """
    Clear a step and everything after it in the run's kind, recording why; the names cleared. `by` marks
    a reopen the referee made itself (`book-audit`: a NOT READY audit), which it counts against its limit.
    """
    names = [s.name for s in config.steps_of(record_kind(run.record))]
    if step not in names:
        raise FactoryError(f"No step {step}; the steps are {', '.join(names)}.")
    cleared = names[names.index(step):]
    if "package" in cleared and ((run.record.get("steps") or {}).get("package") or {}).get("head"):
        # Its commit stays in the branch: accept and a later reopen still count its write-back (the 15 Oct review's #6).
        run.record["packaged"] = run.record["steps"]["package"]["head"]
    for name in cleared:
        run.record.get("steps", {}).pop(name, None)
        run.record.get("gates", {}).pop(name, None)
    run.record["checks"] = [c for c in run.record.get("checks", []) if c["step"] not in cleared]
    if TESTS_STEP in cleared:
        # The red proof moves to `previous`, so an untouched change criterion keeps it. The LOCK STAYS:
        # releasing it here let a session reopen, weaken a test and re-lock it (the review's HIGH-2).
        # Only a person's `factory unlock` releases a locked test.
        if run.record.get("red"):
            run.record["previous"] = {"lock": dict(run.record.get("lock", {})), "red": run.record["red"]}
        run.record["red"] = None
    # Reopening is how an escalation is answered: its question moves into the history with the answer.
    escalation = run.section_text("Escalation")
    run.sections.pop("Escalation", None)
    entry = {"step": step, "at": utc_now(), "reason": reason, "escalation": escalation or None}
    if escalation:
        # When it was asked (the drive notes it as it stops), so the wait for a person can be counted.
        entry["asked_at"] = (run.record.pop("asked", None) or {}).get("at")
    if by:
        entry["by"] = by
    run.record.setdefault("reopened", []).append(entry)
    return cleared


def command_abandon(root: Path, config: Config, args: argparse.Namespace) -> str:
    person_only("abandon")
    run = active_run(root, config, args.run)
    if run.record["status"] != "open":
        raise FactoryError(f"{run.id} is already {run.record['status']}.")
    run.record["status"] = "abandoned"
    run.record["abandoned"] = {"by": git_identity(root), "at": utc_now(), "reason": args.reason}
    write_run(root, config, run)
    return f"{run.id} abandoned. Its file stays as the record of why."


def command_impact(root: Path, config: Config, args: argparse.Namespace) -> str:
    base = merge_base(root, config.raw["base_ref"])
    if args.ci:
        return render_ci_tier(ci_tier(root, config, base, args.paths or None))
    report = impact(root, config, base, args.paths or None)
    text = render_impact(report)
    if args.write:
        run = active_run(root, config, args.run)
        run.sections["Context"] = text
        write_run(root, config, run)
    return text


def command_index(root: Path, config: Config, args: argparse.Namespace) -> tuple[str, bool]:
    path = root / config.runs_dir / "INDEX.md"
    expected = render_index(load_runs(root, config), config)
    if args.check:
        actual = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else ""
        if not load_runs(root, config) and not path.exists():
            return "No runs; no index needed.", True
        ok = actual == expected
        return ("INDEX.md is up to date" if ok else "INDEX.md is stale — run `factory index`"), ok
    write_index(root, config)
    return "INDEX.md written.", True


def command_render(root: Path, config: Config, args: argparse.Namespace) -> str:
    """Redraw run files' referee parts from their Records — after a referee upgrade changes how they read."""
    runs = load_runs(root, config) if args.all else [active_run(root, config, args.run)]
    upgraded = scrubbed = 0
    for run in runs:
        # A lock taken before content hashing moves to it — only after proving the file is byte for byte
        # what was locked, so the upgrade can never hide a change.
        for rel, digest in run.record.get("lock", {}).items():
            path = root / rel
            if path.exists() and sha256_file(path) == digest and content_hash(path) != digest:
                run.record["lock"][rel] = content_hash(path)
                upgraded += 1
        scrubbed += scrub_personal(run.record) != run.record
        write_run(root, config, run)
    notes = ([f"{upgraded} lock(s) moved to line-ending-neutral hashes after proving the files unchanged"] if upgraded
             else []) + ([f"{scrubbed} Record(s) cleared of personal details (an email, a machine, a home folder)"]
                         if scrubbed else [])
    return f"{len(runs)} run file(s) redrawn from their Records; " + ("; ".join(notes) or "the Records are unchanged") + "."


def command_verify(root: Path, config: Config, args: argparse.Namespace) -> tuple[str, bool]:
    runs = load_runs(root, config) if args.all else [active_run(root, config, args.run)]
    problems: list[str] = []
    for run in runs:
        with PROGRESS.waiting(f"🔁 Verifying {run.id} — its Record, its locks, its red proof"):
            found = verify_run(root, config, run)
        PROGRESS.say(f"   {'✅' if not found else '❌'} {run.id}" + (f" — {len(found)} problem(s)" if found else ""))
        problems += found
    return ("\n".join(problems) if problems else f"{len(runs)} run(s) verified."), not problems


# ---------------------------------------------------------------- the backlog
#
# What the book says is left — features, plans, open defects, open questions, the FSD's freshness —
# and every open run, as one read-only answer for a console. Text only, read through `git show` from
# one ref, so a person's uncommitted edits never colour it and nothing is written. The parsers are
# the dashboard's (book_data.py), moved here with the same behaviour; a file the book lacks is an
# `errors` entry and an empty list, never a crash.

# Severity per docs/defects.md: "🔴 unsafe ... 🟠 wrong ... 🟡 contained ... 🔵 minor".
SEVERITY_BY_ICON = {"\U0001F534": "unsafe", "\U0001F7E0": "wrong", "\U0001F7E1": "contained", "\U0001F535": "minor"}
OPEN_DEFECT_STATUSES = ("Open", "Partial", "Mitigated", "Blocked")
DEFECT_TITLE_MAX = 160
QUESTION_CELL_MAX = 240
FSD_SUMMARY_MAX = 300
FEATURE_NOTE_MAX = 240
UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
BOLD_RUN = re.compile(r"\*\*(.+?)\*\*")
MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
BOOK_DEFECT_ID = re.compile(r"^\*{0,2}(D\d+)\*{0,2}$")
BOOK_QUESTION_ID = re.compile(r"\[(Q\d+)\]")
ROLLUP_BEGIN = "<!-- task-rollup:begin"
ROLLUP_END = "<!-- task-rollup:end -->"
PLAN_LINK = re.compile(r"^\[(?P<name>.*)\]\((?P<href>[^)]*)\)$")
COUNT_PHRASE = re.compile(r"(\d+)\s+(blocked|in progress|not started)", re.IGNORECASE)
BUILT_PHRASE = re.compile(r"(\d+)\s+of\s+(\d+)\s+built", re.IGNORECASE)
FSD_VERSION = re.compile(BOOK_FSD_VERSION)
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
FSD_MODIFIED_TIME = re.compile(r"\(modifiedTime\s+([0-9][0-9T:.+\-Z]*)")
STATUS_WORD = re.compile(r"^`([^`]+)`")
RUN_ID = re.compile(r"\bR-\d{3}\b")
NO_VALUE = {"", "—", "-", "–"}  # em dash, hyphen, en dash
FEATURES_HEADING = "### Phases"
FSD_README = "read-me.txt"


def book_section(text: str, start_heading: str, end_heading: str) -> str | None:
    """Text after the line `start_heading` up to the line `end_heading` (or the end); None without the start."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == start_heading), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() == end_heading), len(lines))
    return "\n".join(lines[start + 1:end])


def table_cells(line: str) -> list[str] | None:
    """A markdown table row's trimmed cells; None when the line is not a row."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    inner = stripped[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [cell.strip().replace("\\|", "|") for cell in UNESCAPED_PIPE.split(inner)]


def heading3(line: str) -> str | None:
    """The text of a `### heading` line (not `####`), else None."""
    return line[4:].strip() if line.startswith("### ") else None


def truncated(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def plain_cell(cell: str) -> str:
    """Markdown links reduced to their text, `**` removed, whitespace collapsed."""
    return " ".join(MD_LINK.sub(r"\1", cell).replace("**", "").split())


def first_sentence(text: str) -> str:
    plain = plain_cell(text)
    match = re.search(r"(.+?[.!?])(\s|$)", plain)
    return match.group(1) if match else plain


def book_open_defects(text: str) -> list[dict]:
    """
    Open defect rows: the `## Open` section up to `## Fixed`, or the whole text (still stopping at
    `## Fixed`) when there is no `## Open`. A row is open when its status starts Open, Partial,
    Mitigated or Blocked; its owner is the `###` heading above it.
    """
    body = book_section(text, "## Open", "## Fixed")
    if body is None:
        fixed = re.search(r"^## Fixed\s*$", text, re.MULTILINE)
        body = text[: fixed.start()] if fixed else text
    defects: list[dict] = []
    owner = ""
    for line in body.splitlines():
        heading = heading3(line)
        if heading is not None:
            owner = heading
            continue
        cells = table_cells(line)
        if not cells or len(cells) < 4:
            continue
        found = BOOK_DEFECT_ID.match(cells[0])
        if not found or not cells[2].startswith(OPEN_DEFECT_STATUSES):
            continue
        icon = cells[1].replace("️", "").strip()
        bold = BOLD_RUN.search(cells[3])
        title = (" ".join(MD_LINK.sub(r"\1", bold.group(1)).split()) if bold
                 else truncated(first_sentence(cells[3]), DEFECT_TITLE_MAX))
        defects.append({"id": found.group(1), "severity": SEVERITY_BY_ICON.get(icon, ""), "severity_icon": icon,
                        "status": cells[2], "title": title, "owner": owner})
    return defects


def book_open_questions(text: str) -> list[dict]:
    """
    The rows of the `## Still open` tables, up to `### The open questions in full` (or the next `## `
    heading); [] without that section. A row's owner is the `###` heading above it.
    """
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "## Still open"), None)
    if start is None:
        return []
    questions: list[dict] = []
    owner = ""
    columns = QUESTION_COLUMNS_SIX
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped == "### The open questions in full" or stripped.startswith("## "):
            break
        heading = heading3(stripped)
        if heading is not None:
            owner = heading
            continue
        cells = table_cells(line)
        if not cells:
            continue
        named = question_columns(cells)
        if named is not None:
            columns = named  # a table's header says where each column is: the six-column layout, or the kit's five
            continue
        found = BOOK_QUESTION_ID.search(cells[0])
        if not found or len(cells) <= max(columns.values()):
            continue

        def cell(key: str) -> str:
            return plain_cell(cells[columns[key]]) if key in columns else ""

        questions.append({"id": found.group(1), "release": cell("release"), "module": cell("module"),
                          "phase": cell("phase"), "question": truncated(cell("question"), QUESTION_CELL_MAX),
                          "suggestion": truncated(cell("suggestion"), QUESTION_CELL_MAX), "owner": owner})
    return questions


# Where each field sits in a `## Still open` table, by its header. The six-column layout: | | Rel | Module | Phase | Question | What we
# suggest |; the kit's: | | Area | Question | What we suggest | Blocks | — read by name, so both count.
QUESTION_COLUMNS_SIX = {"release": 1, "module": 2, "phase": 3, "question": 4, "suggestion": 5}
QUESTION_HEADERS = {"rel": "release", "release": "release", "module": "module", "area": "module", "phase": "phase",
                    "question": "question", "what we suggest": "suggestion", "suggestion": "suggestion"}


def question_columns(cells: list[str]) -> dict[str, int] | None:
    """A `## Still open` table's header row as {field: column}, or None for any other row."""
    named = {QUESTION_HEADERS[cell.strip().lower()]: index for index, cell in enumerate(cells)
             if cell.strip().lower() in QUESTION_HEADERS}
    return named if "question" in named and not BOOK_QUESTION_ID.search(cells[0]) else None


def book_plans(text: str, tracker: str = "docs/project.md") -> list[dict]:
    """
    The task roll-up between the `task-rollup` markers in the tracker; ValueError without one. Each plan's link is
    resolved from the tracker's own folder, as book_features does — it was "docs/" + link, so a tracker kept anywhere
    else got every plan path wrong (the kit audit, 18 Oct).
    """
    begin = text.find(ROLLUP_BEGIN)
    end = text.find(ROLLUP_END, begin + 1) if begin != -1 else -1
    if begin == -1 or end == -1:
        raise ValueError("no task roll-up")
    rows: list[dict] = []
    for line in text[begin:end].splitlines()[1:]:  # past the begin-marker line
        cells = table_cells(line)
        if not cells or len(cells) < 4:
            continue
        link = PLAN_LINK.match(cells[0])
        if not link:
            continue  # the header or separator row
        href = link.group("href").split("#", 1)[0]
        built = BUILT_PHRASE.search(cells[1])
        counts = {phrase: 0 for phrase in ("blocked", "in progress", "not started")}
        for number, phrase in COUNT_PHRASE.findall(cells[1]):
            counts[phrase.lower()] += int(number)
        rows.append({"name": link.group("name").strip(),
                     "path": posixpath.normpath(posixpath.join(posixpath.dirname(tracker), href)),
                     "built": int(built.group(1)) if built else 0, "total": int(built.group(2)) if built else 0,
                     "blocked": counts["blocked"], "in_progress": counts["in progress"],
                     "not_started": counts["not started"],
                     "next": "" if cells[2] in NO_VALUE else cells[2], "others": "" if cells[3] in NO_VALUE else cells[3]})
    return rows


def book_fsd_freshness(readme_text: str, fsd_paths: list[str]) -> dict:
    """The newest FSD snapshot, and the `Last checked:` paragraph of the FSD folder's read-me."""
    versions = [int(m.group(1)) for m in (FSD_VERSION.search(path) for path in fsd_paths) if m]
    paragraph: list[str] = []
    for line in readme_text.splitlines():
        if paragraph:
            if not line.strip():
                break
            paragraph.append(line.strip())
        elif line.startswith("Last checked:") and "<YYYY-MM-DD>" not in line:
            # Only the real line: the kit's read-me shows the line's shape first, indented and with placeholders,
            # and that example was reported as the last check.
            paragraph.append(line.strip())
    summary = " ".join(paragraph)
    date = ISO_DATE.search(paragraph[0]) if paragraph else None
    modified = FSD_MODIFIED_TIME.search(summary)
    return {"version": f"V{max(versions)}" if versions else "", "last_checked": date.group(0) if date else "",
            "modified": modified.group(1) if modified else "", "summary": truncated(summary, FSD_SUMMARY_MAX)}


def book_features(text: str, tracker: str) -> list[dict]:
    """
    Every feature row of the tracker's Part 1: each table under `### Phases` (its first column is the
    feature — Phase 5 calls it Stage), and any other table whose header starts `| Feature |`. Columns
    are found by their header, so a phase without `Depends on` reads it as empty. A plan link resolves
    from the tracker's folder; `status` is the cell's leading `code` word, `note` the rest, and `runs`
    the run ids the status cell names.
    """
    features: list[dict] = []
    in_phases, phase, columns = False, "", None
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^#{1,3} ", stripped):
            in_phases = stripped == FEATURES_HEADING
            phase, columns = "", None
            continue
        if stripped.startswith("#### "):
            phase, columns = plain_cell(stripped[5:]).replace("`", ""), None
            continue
        cells = table_cells(line)
        if cells is None:
            columns = None
            continue
        if columns is None:  # a table's header row: a feature table's columns by name, any other's none
            names = [cell.lower() for cell in cells]
            columns = {name: i for i, name in enumerate(names)} if in_phases or names[0] == "feature" else {}
            continue
        if not columns or all(set(cell) <= set("-: ") for cell in cells):
            continue  # a table that is not a feature table, or the separator row

        def cell(name: str) -> str:
            index = columns.get(name)
            return cells[index] if index is not None and index < len(cells) else ""

        status_cell = cell("status")
        word = STATUS_WORD.match(status_cell)
        note = plain_cell(status_cell[word.end():] if word else status_cell).lstrip(" —–-.").strip()
        link = MD_LINK.search(cell("plan"))
        href = link.group(2).split("#", 1)[0] if link else ""
        plan = posixpath.normpath(posixpath.join(posixpath.dirname(tracker), href)) if href else ""

        def value(name: str) -> str:
            text_value = plain_cell(cell(name)).strip("`")
            return "" if text_value in NO_VALUE else text_value

        features.append({"feature": plain_cell(cells[0]), "phase": phase, "fsd": value("fsd"), "plan": plan,
                         "owner": value("owner"), "depends_on": value("depends on"),
                         "status": word.group(1) if word else plain_cell(status_cell),
                         "note": truncated(note, FEATURE_NOTE_MAX) if word else "",
                         "runs": sorted(set(RUN_ID.findall(status_cell)))})
    return features


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


def git_batch(root: Path, names: list[str]) -> dict[str, bytes]:
    """`git cat-file --batch` for many object names in one process: each name's bytes, missing ones left out."""
    if not names:
        return {}
    done = subprocess.run(["git", "cat-file", "--batch"], cwd=root, input="\n".join(names).encode("utf-8") + b"\n",
                          capture_output=True, timeout=GIT_SECONDS)
    if done.returncode != 0:
        raise FactoryError(f"git cat-file --batch failed (exit {done.returncode}).")
    found: dict[str, bytes] = {}
    out, at = done.stdout, 0
    for name in names:
        end = out.index(b"\n", at)
        header = out[at:end].split()
        at = end + 1
        if header[-1] == b"missing" or len(header) != 3:
            continue
        size = int(header[2])
        found[name] = out[at:at + size]
        at += size + 1
    return found


def every_run_copy(root: Path, config: Config) -> tuple[list[tuple[Run, str]], list[str]]:
    """
    Each copy of each run file on every branch, local and fetched, and in every worktree, committed
    or not — where next_run_id looks, for the same reason: a run lives on its own branch until it
    merges. Three git processes for every ref, not one per ref: most refs share the runs folder's tree.
    """
    copies: list[tuple[Run, str]] = []
    errors: list[str] = []
    runs_dir = config.runs_dir.rstrip("/")
    refs = [ref for ref in git_out(root, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes").splitlines()
            if not ref.endswith("/HEAD")]
    trees = git_batch(root, [f"{ref}:{runs_dir}" for ref in refs])
    blobs: dict[str, list[str]] = {}  # blob id: the refs holding it
    for ref in refs:
        tree = trees.get(f"{ref}:{runs_dir}")
        if tree is None:
            continue
        at = 0
        while at < len(tree):  # a tree object: "<mode> <name>\0<20-byte id>" repeated
            nul = tree.index(b"\0", at)
            mode, name = tree[at:nul].decode("utf-8", "replace").split(" ", 1)
            blob = tree[nul + 1:nul + 21].hex()
            at = nul + 21
            if mode.startswith("100") and RUN_FILE_NAME.match(name):
                blobs.setdefault(blob, []).append(ref.removeprefix("refs/heads/").removeprefix("refs/remotes/"))
    for blob, text in git_batch(root, list(blobs)).items():
        try:
            copies.append((parse_run(text.decode("utf-8", "replace")), blobs[blob][0]))
        except FactoryError as error:
            errors.append(f"a run file on {blobs[blob][0]}: {error}")
    # Porcelain blocks, one per worktree: "worktree <folder>", then "branch refs/heads/<name>" unless detached.
    for block in git_out(root, "worktree", "list", "--porcelain").split("\n\n"):
        fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        folder = Path(fields["worktree"]) / config.runs_dir if "worktree" in fields else None
        if folder and folder.is_dir():
            copies += worktree_runs(folder, fields.get("branch", "").removeprefix("refs/heads/"), errors)
    return copies, errors


def worktree_runs(folder: Path, branch: str, errors: list[str]) -> list[tuple[Run, str]]:
    found: list[tuple[Run, str]] = []
    for path in sorted(folder.iterdir()):
        if RUN_FILE_NAME.match(path.name):
            try:
                found.append((parse_run(path.read_text(encoding="utf-8")), f"worktree {branch or 'detached'}"))
            except (FactoryError, OSError, UnicodeDecodeError) as error:
                errors.append(f"{path.name} in the worktree of {branch or 'a detached HEAD'}: {error}")
    return found


def open_runs(root: Path, config: Config) -> tuple[list[dict], list[str]]:
    """Every run whose newest copy, by its Record's last activity, is open; a worktree's copy wins a tie."""
    copies, errors = every_run_copy(root, config)
    newest: dict[str, tuple[str, Run, str]] = {}
    for run, source in copies:
        moment = last_activity(run.record)
        held = newest.get(run.id)
        if held is None or moment > held[0] or (moment == held[0] and source.startswith("worktree ")
                                                 and not held[2].startswith("worktree ")):
            newest[run.id] = (moment, run, source)
    runs = [{"id": run.id, "title": run.title, "item": run.record.get("item", ""), "kind": record_kind(run.record),
             "step": run.record.get("step", ""), "status": run.record["status"], "branch": run.record["branch"], "updated": moment, "source": source}
            for moment, run, source in (newest[key] for key in sorted(newest)) if run.record["status"] == "open"]
    return runs, errors


def backlog(root: Path, config: Config, ref: str | None = None) -> dict:
    """The book at `ref` (default: base_ref, else HEAD) and every open run, as one JSON-ready table."""
    errors: list[str] = []
    wanted = ref or config.raw["base_ref"]
    if not git_out(root, "rev-parse", "--verify", "--quiet", f"{wanted}^{{commit}}"):
        if ref:
            raise FactoryError(f"--ref {ref} is not a commit.")
        errors.append(f"base_ref {wanted} is not here; read HEAD instead")
        wanted = "HEAD"
    requirements = config.raw.get("requirements", {})
    sources = requirements.get("sources", {})

    def book_file(rel: str) -> str | None:
        code, out = git(root, "show", f"{wanted}:{rel}")
        if code != 0:
            errors.append(f"{rel}: not on {wanted}")
            return None
        return out

    def merged(paths: list[str], parse: Callable[[str], list[dict]]) -> list[dict]:
        rows: dict[str, dict] = {}
        for rel in paths:
            text = book_file(rel)
            for row in parse(text) if text is not None else []:
                rows.setdefault(row["id"], row)  # the live log before its archive
        return list(rows.values())

    features: list[dict] = []
    plans: list[dict] = []
    tracker = requirements.get("tracker", "")
    text = book_file(tracker) if tracker else None
    if not tracker:
        errors.append("factory.toml names no [requirements] tracker")
    if text is not None:
        features = book_features(text, tracker)
        if not features:
            errors.append(f"{tracker}: no feature tables")
        try:
            plans = book_plans(text, tracker)
        except ValueError as error:
            errors.append(f"{tracker}: {error}")
    fsd = {"version": "", "last_checked": "", "modified": "", "summary": ""}
    fsd_dir = sources.get("fsd_dir", "")
    if fsd_dir:
        listed = git_out(root, "ls-tree", "-r", "--name-only", wanted, "--", f"{fsd_dir}/").splitlines()
        if not listed:
            errors.append(f"{fsd_dir}: not on {wanted}")
        else:
            readme = book_file(f"{fsd_dir}/{FSD_README}")
            fsd = book_fsd_freshness(readme or "", listed)
    else:
        errors.append("factory.toml names no [requirements.sources] fsd_dir")
    runs, run_errors = open_runs(root, config)
    return {"ref": wanted, "generated": utc_now(), "features": features, "plans": plans,
            "defects": merged(requirements.get("defect_logs", []), book_open_defects),
            "questions": merged(sources.get("decisions", []), book_open_questions),
            "fsd": fsd, "runs": runs, "errors": errors + run_errors}


def command_backlog(root: Path, config: Config, args: argparse.Namespace) -> str:
    report = backlog(root, config, args.ref)
    if args.json:
        return json.dumps(report, indent=2, ensure_ascii=False)
    fsd = report["fsd"]
    lines = [f"The backlog at {report['ref']}:",
             f"  features   {len(report['features'])} ({sum(f['status'] != 'Built' for f in report['features'])} not Built)",
             f"  plans      {len(report['plans'])}",
             f"  defects    {len(report['defects'])} open",
             f"  questions  {len(report['questions'])} open",
             f"  FSD        {fsd['version'] or '—'}, last checked {fsd['last_checked'] or '—'}",
             f"  runs       {len(report['runs'])} open" + "".join(f"\n    {run['id']} {run['item']} — {run['step']} on {run['branch']}"
                                                            for run in report["runs"])]
    lines += [f"  ! {error}" for error in report["errors"]]
    return "\n".join(lines)


# ---- fsd-diff: a staged FSD against the newest snapshot (read only) ----------------------------

PDF_MAGIC = b"%PDF-"
FSD_STAGED_MAX_BYTES = 50 * 1024 * 1024
PDFTOTEXT_SECONDS = 3 * 60
# A numbered heading: `2.13.6. Loans`, `2.1 Users`. pdftotext -layout indents some and runs a second
# column on beside others, so the title is the text up to the first wide gap.
FSD_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)+)\.?\s+(\S.*)$")
# A section a feature row cites, with an optional range on its last part: `2.13.1`, `2.13.1–5`.
FSD_SECTION_RANGE = re.compile(r"(\d+(?:\.\d+)+)(?:\s*[–-]\s*(\d+)(?![.\d]))?")
FSD_RANGE_MAX = 50
TOC_LEADER = re.compile(r"\.{4,}")  # a table-of-contents line: its dot leaders and page number are not content
PAGE_NUMBER_LINE = re.compile(r"^\s*(?:page\s+)?\d+(?:\s*(?:of|/)\s*\d+)?\s*$", re.IGNORECASE)
FRONT_MATTER = "front"
# Google Docs puts a zero-width space after each heading number; it is not whitespace to a regex.
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))
FSD_DIFF_LINES = 40
FSD_DIFF_CONTEXT = 1


def pdf_text(path: Path) -> str:
    """A PDF's text, through `pdftotext -layout … -` (it prints; it writes no file)."""
    try:
        done = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"], capture_output=True,
                              timeout=PDFTOTEXT_SECONDS, check=False)
    except FileNotFoundError:
        raise FactoryError("fsd-diff needs pdftotext on PATH (poppler-utils; Git for Windows ships it).") from None
    except subprocess.TimeoutExpired:
        raise FactoryError(f"pdftotext took longer than {PDFTOTEXT_SECONDS} s on {path.name}.") from None
    if done.returncode != 0:
        said = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise FactoryError(f"pdftotext could not read {path.name} (exit {done.returncode})"
                           + (f": {said[-1]}" if said else "."))
    return done.stdout.decode("utf-8", "replace")


def fsd_sections(text: str) -> dict[str, dict]:
    """
    The text split at each numbered heading, in document order: {number: {title, lines}}. Lines are
    whitespace-collapsed (a layout shift is not a change); blank lines, page numbers and
    table-of-contents lines are dropped. Text before the first heading is `front`. A number met twice
    (the layout repeats one beside a second column) carries on the section it already has.
    """
    sections: dict[str, dict] = {FRONT_MATTER: {"title": "Before the first numbered section", "lines": []}}
    current = sections[FRONT_MATTER]
    for raw in text.replace("\f", "\n").translate(ZERO_WIDTH).splitlines():
        if TOC_LEADER.search(raw) or PAGE_NUMBER_LINE.match(raw):
            continue
        line = " ".join(raw.split())
        if not line:
            continue
        heading = FSD_HEADING.match(raw)
        if heading:
            number = heading.group(1)
            title = re.split(r"\s{2,}", heading.group(2).strip())[0].strip()
            if number not in sections:
                sections[number] = {"title": title, "lines": []}
            current = sections[number]
        current["lines"].append(line)
    if not sections[FRONT_MATTER]["lines"]:
        del sections[FRONT_MATTER]
    return sections


def short_diff(old: list[str], new: list[str]) -> tuple[list[str], int, int, int]:
    """A unified diff without its file header, capped at FSD_DIFF_LINES: (lines, cut, added, removed)."""
    lines = list(difflib.unified_diff(old, new, lineterm="", n=FSD_DIFF_CONTEXT))[2:]
    added = sum(1 for line in lines if line.startswith("+"))
    removed = sum(1 for line in lines if line.startswith("-"))
    return lines[:FSD_DIFF_LINES], max(0, len(lines) - FSD_DIFF_LINES), added, removed


def fsd_cell_sections(cell: str) -> list[str]:
    """
    The FSD section numbers a feature row's FSD cell names: `§2.13.6, 2.13.7` → ['2.13.6', '2.13.7'];
    a range on the last part is spelt out: `§2.13.1–3` → ['2.13.1', '2.13.2', '2.13.3'].
    """
    found: list[str] = []
    for match in FSD_SECTION_RANGE.finditer(cell):
        number, last = match.group(1), match.group(2)
        found.append(number)
        if last:
            prefix, _, first = number.rpartition(".")
            if first.isdigit() and int(first) < int(last) <= int(first) + FSD_RANGE_MAX:
                found += [f"{prefix}.{n}" for n in range(int(first) + 1, int(last) + 1)]
    return found


def section_relation(changed: str, cited: str) -> str | None:
    """How a changed section and a feature's cited one relate, or None when they do not."""
    if changed == cited:
        return "same section"
    if changed.startswith(cited + "."):
        return f"inside the feature's §{cited}"
    if cited.startswith(changed + "."):
        return f"the feature's §{cited} is inside it"
    return None


def capture_cadence(root: Path, config: Config, fsd_dir: str, today: datetime.date | None = None) -> dict:
    """
    The week's FSD captures, as /requirements-check's Phase 4 counts them: a `*V*.pdf` added or
    modified on base_ref since this week's Monday (local time), and one on an unmerged
    `<remote>/chore/docs-*` branch. Read only: no fetch, so it is as fresh as the last one.
    """
    base_ref = config.raw["base_ref"]
    today = today or datetime.datetime.now().astimezone().date()
    monday = today - datetime.timedelta(days=today.weekday())
    since = f"{monday.isoformat()} 00:00:00"
    pathspec = f"{fsd_dir}/*V*.pdf"
    captures: list[dict] = []
    errors: list[str] = []

    def logged(range_spec: str, ref: str) -> None:
        code, out = git(root, "log", range_spec, "--diff-filter=AM", f"--since={since}",
                        "--format=%x1e%H%x1f%cs%x1f%s", "--name-only", "--", pathspec)
        if code != 0:
            errors.append(f"git log {range_spec} failed: {out.strip().splitlines()[-1] if out.strip() else code}")
            return
        for block in out.split("\x1e")[1:]:
            head_line, *names = block.strip("\n").split("\n")
            commit, date, subject = (head_line.split("\x1f") + ["", "", ""])[:3]
            captures.append({"ref": ref, "commit": commit[:12], "date": date, "subject": subject,
                             "files": [name for name in names if name.strip()]})

    if git_out(root, "rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"):
        logged(base_ref, base_ref)
        remote = base_ref.split("/", 1)[0] if "/" in base_ref else "origin"
        code, out = git(root, "branch", "-r", "--no-merged", base_ref, "--list", f"{remote}/chore/docs-*")
        if code == 0:
            for branch in (line.strip() for line in out.splitlines()):
                if branch and " -> " not in branch:
                    logged(f"{base_ref}..{branch}", branch)
        else:
            errors.append("could not list the open chore/docs-* branches")
    else:
        errors.append(f"base_ref {base_ref} is not here; the week's captures could not be counted")
    return {"monday": monday.isoformat(), "captures": captures, "taken_this_week": bool(captures),
            "rule": "At most one capture a week; a second is the owner's decision (requirements/read-me.txt, Cadence).",
            "errors": errors}


def fsd_features(root: Path, config: Config) -> tuple[list[dict], str, list[str]]:
    """The tracker's feature rows at base_ref (else HEAD), as `factory backlog` reads them."""
    tracker = config.raw.get("requirements", {}).get("tracker", "")
    if not tracker:
        return [], "", ["factory.toml names no [requirements] tracker"]
    ref = config.raw["base_ref"]
    errors: list[str] = []
    if not git_out(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
        errors.append(f"base_ref {ref} is not here; features read from HEAD")
        ref = "HEAD"
    code, text = git(root, "show", f"{ref}:{tracker}")
    if code != 0:
        return [], ref, errors + [f"{tracker}: not on {ref}"]
    return book_features(text, tracker), ref, errors


def fsd_diff(root: Path, config: Config, staged: Path) -> dict:
    """
    What a staged FSD PDF changes against the newest snapshot. Read only: it runs pdftotext and git
    and writes nothing. The JSON shape (`factory fsd-diff --staged <pdf> --json`):

        {
          "staged":   {"path", "sha256", "bytes"},
          "current":  {"path", "version": "V20", "sha256", "bytes"},   # the newest `<fsd_dir>/* - V<n>.pdf`
          "next_version": "V21",
          "identical": bool,                  # the staged file is byte for byte the newest snapshot
          "sections": {"current": int, "staged": int},
          "added":   [{"section", "title", "lines": int, "features": [...]}],
          "removed": [{"section", "title", "lines": int, "features": [...], "runs": [...]}],
          "changed": [{"section", "title", "old_title", "added": int, "removed": int,
                       "diff": [str],           # a unified diff without its header, at most 40 lines
                       "diff_more": int,        # lines cut from it
                       "runs": [{"id", "item", "branch", "step", "reasons": [str]}],   # `factory affected`
                       "features": [{"feature", "phase", "fsd", "status", "plan", "relation"}]}],
          "features_ref": "origin/dev",        # where the feature rows were read
          "cadence": {"monday", "captures": [{"ref", "commit", "date", "subject", "files"}],
                      "taken_this_week": bool, "rule", "errors": [str]},
          "last_checked": {"version", "last_checked", "modified", "summary"},   # the FSD read-me's line
          "errors": [str]                      # what could not be read; never a crash
        }

    `section` is the heading's number (`2.13.6`), or `front` for text before the first heading. A
    section is changed when its title or its whitespace-collapsed lines differ. A run is listed when
    `factory affected <section>` would list it (its own checkout's runs); a feature when a section its
    FSD cell names is the changed one, holds it, or sits inside it.
    """
    sources = config.raw.get("requirements", {}).get("sources", {})
    fsd_dir, pattern = sources.get("fsd_dir", ""), sources.get("version", r" - V(\d+)\.pdf$")
    if not fsd_dir:
        raise FactoryError("factory.toml names no [requirements.sources] fsd_dir.")
    if not staged.is_file():
        raise FactoryError(f"The staged file {staged} does not exist.")
    size = staged.stat().st_size
    if size > FSD_STAGED_MAX_BYTES:
        raise FactoryError(f"The staged file is {size} bytes; the limit is {FSD_STAGED_MAX_BYTES}.")
    with open(staged, "rb") as handle:
        if handle.read(len(PDF_MAGIC)) != PDF_MAGIC:
            raise FactoryError("The staged file is not a PDF: it does not start with %PDF-.")
    version = newest_version(root, fsd_dir, pattern)
    if version is None:
        raise FactoryError(f"{fsd_dir} holds no snapshot matching {pattern!r} to compare with.")
    current = next(path for path in sorted((root / fsd_dir).glob("*"))
                   if (m := re.search(pattern, path.name)) and int(m.group(1)) == version)
    errors: list[str] = []
    old_sections = fsd_sections(pdf_text(current))
    new_sections = fsd_sections(pdf_text(staged))
    if len(new_sections) <= 1:
        errors.append("the staged PDF has no numbered headings pdftotext could read; is it the FSD?")
    features, features_ref, feature_errors = fsd_features(root, config)
    errors += feature_errors

    def lands_on(number: str) -> list[dict]:
        if number == FRONT_MATTER:
            return []
        found = []
        for feature in features:
            relation = next((r for cited in fsd_cell_sections(feature["fsd"]) if (r := section_relation(number, cited))),
                            None)
            if relation:
                found.append({"feature": feature["feature"], "phase": feature["phase"], "fsd": feature["fsd"],
                              "status": feature["status"], "plan": feature["plan"], "relation": relation})
        return found

    def runs_on(number: str) -> list[dict]:
        if number == FRONT_MATTER:
            return []
        return [{"id": run.id, "item": run.record.get("item", ""), "branch": run.record["branch"],
                 "step": run.record.get("step", ""), "reasons": reasons}
                for run, reasons in affected_runs(root, config, number)]

    # The comparison the revision run's changes step makes too (section_changes); here each change is
    # also told what it lands on.
    added, removed, changed = section_changes(old_sections, new_sections)
    for entry in added:
        entry["features"] = lands_on(entry["section"])
    for entry in removed + changed:
        entry.update(features=lands_on(entry["section"]), runs=runs_on(entry["section"]))
    readme = root / fsd_dir / FSD_README
    try:
        readme_text = readme.read_text(encoding="utf-8")
    except OSError:
        readme_text = ""
        errors.append(f"{fsd_dir}/{FSD_README}: not found")
    listed = [f"{fsd_dir}/{path.name}" for path in (root / fsd_dir).glob("*")]
    cadence = capture_cadence(root, config, fsd_dir)
    staged_sha, current_sha = sha256_file(staged), sha256_file(current)
    return {"staged": {"path": str(staged), "sha256": staged_sha, "bytes": size},
            "current": {"path": f"{fsd_dir}/{current.name}", "version": f"V{version}", "sha256": current_sha,
                        "bytes": current.stat().st_size},
            "next_version": f"V{version + 1}", "identical": staged_sha == current_sha,
            "sections": {"current": len(old_sections), "staged": len(new_sections)},
            "added": added, "removed": removed, "changed": changed, "features_ref": features_ref,
            "cadence": cadence, "last_checked": book_fsd_freshness(readme_text, listed),
            "errors": errors + cadence["errors"]}


def command_fsd_diff(root: Path, config: Config, args: argparse.Namespace) -> str:
    report = fsd_diff(root, config, Path(args.staged).resolve())
    if args.json:
        return json.dumps(report, indent=2, ensure_ascii=False)
    current = report["current"]
    lines = [f"The staged FSD against {current['path']} ({current['version']}):"]
    if report["identical"]:
        lines.append("  identical to the newest snapshot — nothing to capture")
    lines += [f"  sections   {report['sections']['current']} now, {report['sections']['staged']} staged",
              f"  added      {len(report['added'])}" + "".join(f"\n    §{s['section']} {s['title']}" for s in report["added"]),
              f"  removed    {len(report['removed'])}" + "".join(f"\n    §{s['section']} {s['title']}" for s in report["removed"]),
              f"  changed    {len(report['changed'])}"]
    for section in report["changed"]:
        lines.append(f"    §{section['section']} {section['title']} (+{section['added']} −{section['removed']})")
        lines += [f"      lands on {run['id']} ({run['item']}, {run['branch']})" for run in section["runs"]]
        lines += [f"      feature {feature['feature']} ({feature['fsd']}, {feature['status']})"
                  for feature in section["features"]]
    cadence = report["cadence"]
    lines.append(f"  this week  {len(cadence['captures'])} capture(s) since {cadence['monday']}"
                 + ("; another is the owner's decision" if cadence["taken_this_week"] else ""))
    lines.append(f"  last check {report['last_checked']['summary'] or '—'}")
    lines.append(f"  capture    a person decides; /requirements-check takes it as {report['next_version']}")
    lines += [f"  ! {error}" for error in report["errors"]]
    return "\n".join(lines)


def repo_root() -> Path:
    override = os.environ.get("FACTORY_ROOT")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[2]


def parser() -> argparse.ArgumentParser:
    # No abbreviations: `--ru R-002 approve` hid the subcommand from the guard (the re-review).
    top = argparse.ArgumentParser(prog="factory", description="The AI factory's referee.", allow_abbrev=False)
    top.add_argument("--run", help="a run id, when the branch has more than one open run")
    sub = top.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="open a run on the current branch")
    start.add_argument("item", help="a D or Q id, a Jira key, an FSD section, plan:<feature-slug>, "
                                    "plan:<plan-stem>#<task>, book:<name> or revision:V<n>")
    start.add_argument("--title", required=True)
    start.add_argument("--base", default=None,
                       help="the commit the run's changes are measured from — default: where the branch "
                            "left the base branch; give HEAD to start on top of work already on the branch")
    start.add_argument("--new-branch", nargs="?", const="", default=None, metavar="NAME",
                       help="cut the run's own branch from HEAD first — named from [backlog.branches] unless given")
    drive_parser = sub.add_parser("drive", help="resume the run's working session step by step until a gate")
    drive_parser.add_argument("--steps", type=int, default=0, help="stop after this many steps (0: until a gate)")
    sub.add_parser("pr-body", help="write the pull request description from the run")
    sub.add_parser("report", help="every run: state, cost, attempts, escalations, defects traced back")
    backlog_parser = sub.add_parser("backlog", help="what is left: features, plans, open defects and questions, the FSD, open runs; read only")
    backlog_parser.add_argument("--json", action="store_true", help="the whole answer as JSON, for a console")
    backlog_parser.add_argument("--ref", default=None,
                                help="the commit whose book is read — default: the base branch, else HEAD")
    fsd_diff_parser = sub.add_parser("fsd-diff", help="a staged FSD PDF against the newest snapshot: sections added, removed, changed; read only")
    fsd_diff_parser.add_argument("--staged", required=True, help="the PDF to compare; the console stages it outside every checkout")
    fsd_diff_parser.add_argument("--json", action="store_true", help="the whole answer as JSON, for a console")
    affected = sub.add_parser("affected", help="which open runs a changed requirement lands on")
    affected.add_argument("requirement", help="an FSD section (2.7.1) or requirement id (FSD-2.7.1-R09)")
    open_pr = sub.add_parser("open-pr", help="a person: open the run's pull request in their own name")
    open_pr.add_argument("--destination", default="", help="the branch to merge into; default: the base branch")
    pr = sub.add_parser("pr", help="a PR outside a run — show, comments, open, comment; the token stays in this process")
    pr.add_argument("action", choices=["show", "comments", "open", "comment", "whoami"])
    pr.add_argument("--number", type=int, default=0, help="the PR; show defaults to the current branch's open PR")
    pr.add_argument("--title", default="", help="open: the PR's title")
    pr.add_argument("--body-file", default="", help="open, comment: a file holding the description or comment")
    pr.add_argument("--destination", default="", help="open: the branch to merge into; default: the base branch")
    pr.add_argument("--path", default="", help="comment: the file, as the diff names it")
    pr.add_argument("--line", type=int, default=0, help="comment: the line in the new file")
    reviewed = sub.add_parser("reviewed", help="record /review-branch's verdict for this commit (inside .git)")
    reviewed.add_argument("verdict", choices=["READY", "NOT-READY"])
    link = sub.add_parser("link-pr", help="link the run to its Bitbucket pull request")
    link.add_argument("number", type=int)
    protection = sub.add_parser("protection", help="read the branch restrictions back from Bitbucket")
    protection.add_argument("--check", action="store_true", help="exit 1 when a decided rule is missing")
    pr_check = sub.add_parser("pr-check", help="check a PR description has what CLAUDE.md asks for")
    pr_check.add_argument("number", type=int, nargs="?", default=0)
    ai_files = sub.add_parser("ai-files", help="CI: a change to the files that steer the AI is made or approved by an owner")
    ai_files.add_argument("number", type=int, nargs="?", default=0)
    reopen = sub.add_parser("reopen", help="re-prove a step and everything after it")
    reopen.add_argument("step")
    reopen.add_argument("--reason", required=True)
    reopen.add_argument("--proof", default=None, help="the saved copy of the question a person answered (the Factory app writes it)")
    abandon = sub.add_parser("abandon", help="a person closes a run that will not continue")
    abandon.add_argument("--reason", required=True)
    sub.add_parser("status", help="where the run is")
    sub.add_parser("options", help="skills a person may use before a run, or from its merge gate on, when they fit")
    jira_parser = sub.add_parser("jira", help="read one Jira issue — a Task or a Bug; read only, the token stays in this process")
    jira_parser.add_argument("key", help="the issue key, e.g. PROJ-12")
    threads = sub.add_parser("fsd-threads", help="the live FSD against the last check; --save keeps its comment threads")
    threads.add_argument("--save", action="store_true", help="write requirements/fsd/comment-threads - <date>.md")
    sub.add_parser("check", help="run the current step's check")
    approve = sub.add_parser("approve", help="a person approves a gate")
    # The Merge gate is a Bitbucket pull request approval, read by the referee — never a local command.
    approve.add_argument("gate", choices=["spec", "design", "plan", "outline", "book", "revision", "merge"])
    approve.add_argument("--note", default=None)
    approve.add_argument("--proof", default=None, help="the saved copy of what the person approved (the Factory app writes it)")
    answer = sub.add_parser("answer", help="a person answers an open question in open-questions.md, and commits it")
    answer.add_argument("question", help="the question's id, e.g. Q2")
    answer.add_argument("--answer", default=None, help="the answer, in the person's words")
    answer.add_argument("--accept", action="store_true", help="accept the question's Recommendation as the answer")
    allow = sub.add_parser("allow", help="a person lets a step write files in a new place, and commits it")
    allow.add_argument("paths", nargs="+", help="files or folders (`devtools/scripts/**`), relative to the repository")
    allow.add_argument("--step", default="build", help="the step that may write them (default: build)")
    unlock = sub.add_parser("unlock", help="a person releases a locked test")
    unlock.add_argument("path")
    unlock.add_argument("--reason", required=True)
    impact_parser = sub.add_parser("impact", help="what the change reaches")
    impact_parser.add_argument("--write", action="store_true", help="fill the run's Context section")
    impact_parser.add_argument("paths", nargs="*")
    impact_parser.add_argument("--ci", action="store_true", help="the local CI tier and integration filter (ci-local.sh)")
    index = sub.add_parser("index", help="regenerate the runs index")
    index.add_argument("--check", action="store_true")
    render_parser = sub.add_parser("render", help="redraw run files' referee parts from their Records")
    render_parser.add_argument("--all", action="store_true")
    verify = sub.add_parser("verify", help="re-check run files without building")
    verify.add_argument("--all", action="store_true")
    version_parser = sub.add_parser("version", help="this referee's version, and the kit stamp in factory.toml if any; read only")
    version_parser.add_argument("--json", action="store_true", help="the answer as JSON, for a console")
    return top


def command_version(root: Path, args: argparse.Namespace) -> str:
    """
    This referee's FACTORY_VERSION and the kit stamp (`kit_version` in factory.toml), if any. Reads
    factory.toml alone — never the private settings — and answers even when the configuration would
    not load, so a console can always tell which referee a project holds.
    """
    stamp = None
    try:
        with open(root / CONFIG_RELATIVE, "rb") as handle:
            raw = tomllib.load(handle) if tomllib else {}
        value = raw.get(KIT_STAMP_KEY)
        stamp = value if isinstance(value, str) and value.strip() else None
    except (OSError, ValueError):
        stamp = None
    if args.json:
        return json.dumps({"version": FACTORY_VERSION, "kit": stamp})
    return f"factory {FACTORY_VERSION}" + (f" (kit {stamp})" if stamp else "")


COMMANDS: dict[str, Callable] = {
    "start": command_start, "status": command_status, "check": command_check, "approve": command_approve,
    "answer": command_answer, "allow": command_allow, "unlock": command_unlock, "drive": command_drive, "pr-body": command_pr_body, "report": command_report, "backlog": command_backlog, "fsd-diff": command_fsd_diff, "render": command_render, "affected": command_affected,
    "link-pr": command_link_pr, "open-pr": command_open_pr, "pr": command_pr, "protection": command_protection,
    "reviewed": command_reviewed,
    "pr-check": command_pr_check, "ai-files": command_ai_files, "reopen": command_reopen, "abandon": command_abandon, "impact": command_impact, "index": command_index,
    "verify": command_verify, "options": command_options, "fsd-threads": command_fsd_threads,
    "jira": command_jira,
}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    top = parser()
    args = top.parse_args(argv)
    # A drive a Claude Code session started runs for many minutes with no terminal to tick in; the
    # session streams its lines to the person. Every other command stays silent there.
    PROGRESS.configure(drive_in_session=args.command == "drive" and "CLAUDECODE" in os.environ)
    root = repo_root()
    if args.command == "version":  # before the configuration: it must answer even where that would not load
        print(command_version(root, args))
        return 0
    try:
        config = load_config(root)
        with PROGRESS.waiting(f"🏭 factory {args.command} — {command_help(top, args.command)}"):
            result = COMMANDS[args.command](root, config, args)
    except FactoryError as error:
        print(f"factory: {error}", file=sys.stderr)
        return 1
    text, ok = result if isinstance(result, tuple) else (result, True)
    print(text)
    return 0 if ok else 1


def command_help(top: argparse.ArgumentParser, command: str) -> str:
    """The command's own one-line help, for the line that ticks while it runs."""
    for action in top._actions:  # argparse keeps its subcommands' help here and nowhere public
        if isinstance(action, argparse._SubParsersAction):
            for choice in action._choices_actions:
                if choice.dest == command:
                    return str(choice.help or "")
    return ""


if __name__ == "__main__":
    sys.exit(main())
