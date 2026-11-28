#!/usr/bin/env python3
"""
factory.py — the module behind `book` (python devtools/book.py <command>): the AI tools a session and a person
use, each holding its own credential. It is not a command of its own from Book v1.5.

    book pr show|comments|open|describe|comment|post|resolve|whoami   a Bitbucket pull request; the token stays in this process
    book fingerprint                            the hash of what a review covers (one owner: book:self-review, commit)
    book reviewed READY|NOT-READY [--fingerprint <hash>]   record /book:self-review's verdict for this commit
    book review-scope [paths...] [--range A...B] which review checklist files a change needs read
    book jira <key>                             read one Jira Task or Bug, read only
    book ai-files [<pr>]                        CI: a change to the files that steer the AI is made or approved by an owner
    book protection [--check]                   read the branch restrictions back from Bitbucket
    book pr-check [<pr>]                        a PR description has what CLAUDE.md asks for
    book fsd-threads [--save]                   the live FSD against the last check
    book fsd-page <heading> [--print]           which pages of the highest FSD revision hold a section
    book requirements status                    offline: every requirements source, its snapshot, the weekly rule
    book sync-plan [--mode delta|full|verify-only]   read-only: /book:sync-sources's baseline, mode, delta set
    book impact [paths...]                      what the change reaches: modules, tests, guides, requirement ids
    book deploy check|gate|changes|up|smoke|revert|report   /book:deploy's steps, DEV and QA only
    book deploy status [--target dev|qa|all]   read-only: the commit each environment runs
    book verify [--target <repo>]               whether the Book is set up (read-only): READY or not
    book upgrade --target <repo> [--apply]      drift against a newer template; --apply updates unchanged files
    book settings --check                       each private setting the tooling reads: set or not set, names only
    book config <table.key> [--json]            one value of the settings, factory.toml over the Book's defaults

A session reaches a service through a tool that holds the credential, never with it (CLAUDE.md,
Private settings).

Configuration: factory.toml beside this file. Python 3.11+, standard library only.
"""

from __future__ import annotations

import argparse
import datetime
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bitbucket  # noqa: E402 — beside this file; standard library only
import book  # noqa: E402 — beside this file; standard library only
import book_config  # noqa: E402 — beside this file; the settings merged over the Book's defaults
try:
    import deploy  # noqa: E402 — the deploy pack's, beside this file when the pack is installed
except ImportError:
    deploy = None
import drive  # noqa: E402 — beside this file; standard library only
import jira  # noqa: E402 — beside this file; standard library only
import snapshots  # noqa: E402 — beside this file; standard library only

CONFIG_RELATIVE = "devtools/factory/factory.toml"
GIT_SECONDS = 120
SECTION_AND_RULE = r"\d+(?:\.\d+)*-R\d+"


class FactoryError(Exception):
    """A refusal the tool explains to the person or session that asked."""


@dataclass(frozen=True)
class Config:
    raw: dict

    @property
    def modules(self) -> list[dict]:
        return self.raw.get("modules", [])


DEFAULT_PRIVATE_SETTINGS = "~/.ai-factory/local.toml"


def settings_variable(raw: dict | None) -> str:
    """The environment variable that moves the private settings file: `[book] env_prefix` + LOCAL_SETTINGS
    (e.g. APP_LOCAL_SETTINGS); BOOK_LOCAL_SETTINGS when no prefix is set."""
    prefix = str(((raw or {}).get("book") or {}).get("env_prefix") or "BOOK_")
    return prefix + "LOCAL_SETTINGS"


def private_settings_path(raw: dict | None = None) -> Path:
    """
    The one place a person's own settings live: outside every repository, in their home folder —
    `private_settings` in factory.toml (`~/.<slug>/local.toml`), or the variable settings_variable()
    names (`[book] env_prefix` + LOCAL_SETTINGS). Never committed, never printed, and the guard refuses a
    session reading it (CLAUDE.md, Private settings).
    """
    if raw is None:
        try:
            raw = book_config.load(repo_root()) if tomllib else {}
        except (OSError, book_config.ConfigError):
            raw = {}
    override = os.environ.get(settings_variable(raw))
    if override:
        return Path(override)
    return Path(os.path.expanduser(str(raw.get("private_settings") or DEFAULT_PRIVATE_SETTINGS)))


# The settings a person's private file may supply, by section: what is theirs, not the project's.
PRIVATE_KEYS = {("requirements", "sources"): ("live_credential",)}
# Where load_config puts the private `[book] foreign_names`, apart from the project's own [book] keys.
PRIVATE_FOREIGN_NAMES = "private_foreign_names"


def load_config(root: Path) -> Config:
    if tomllib is None:
        raise FactoryError("book needs Python 3.11 or later (tomllib).")
    if not (root / CONFIG_RELATIVE).is_file():
        raise FileNotFoundError(root / CONFIG_RELATIVE)
    try:
        raw = book_config.load(root)
    except book_config.ConfigError as error:
        raise FactoryError(f"The Book's settings are unreadable: {error}.") from None
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
        # Optional: other projects' names `book verify` must never find (N20), private because they name clients.
        names = (mine.get("book") or {}).get("foreign_names")
        if isinstance(names, list):
            raw.setdefault("book", {})[PRIVATE_FOREIGN_NAMES] = [str(name) for name in names if name]
    return Config(raw)


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
    that failed must stop the check, not read as nothing. git_out stays for
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
    """
    Where this branch left the base. No answer — a shallow clone, a base ref that does not
    resolve — stops the caller: falling back to HEAD made the change look empty, and the CI tier
    drop to `checks`.
    """
    base = git_strict(root, "merge-base", "HEAD", base_ref)
    if not base:
        raise FactoryError(f"No merge base between HEAD and {base_ref}; fetch it and re-run.")
    return base


def own_commits(root: Path, base: str) -> tuple[str, ...]:
    """
    The `git log` range of the commits this branch would bring to the base branch: reachable
    from HEAD, not from the merge base and not from the base branch. `dev` merged in is
    excluded, because it is already there; any other branch merged in is counted, because the
    pull request carries it.
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
    resolved by hand, or a file slipped into a `--no-commit` merge — was invisible to
    `book impact`. Each merge is redone
    by git and only the difference is the branch's own.
    """
    excluded = own_commits(root, base)[3:]  # the merge base, and the base branch when it is known
    merges = git_strict(root, "rev-list", "--merges", "--parents", "HEAD", "--not", *excluded).splitlines()
    lines: list[str] = []
    for merge in merges:
        commit, *parents = merge.split()
        if len(parents) > 2:
            # git cannot redo an octopus merge as one tree; count all it brought over the first
            # parent — more than its own work, never less.
            lines += [line for line in git_strict(root, "diff", "--numstat", parents[0], commit).splitlines() if line]
            continue
        _, auto = git(root, "merge-tree", "--write-tree", *parents)
        auto_tree = auto.splitlines()[0] if auto else ""
        # No tree back — a git before 2.38, or any merge-tree error: count the whole merge over its
        # first parent rather than nothing. Over-counting fails safe; skipping it hid the merge.
        base = auto_tree if re.fullmatch(r"[0-9a-f]{40,64}", auto_tree) else parents[0]
        lines += [line for line in git_strict(root, "diff", "--numstat", base, commit).splitlines() if line]
    return lines


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def requirement_prefix(config: Config) -> str:
    return config.raw.get("requirements", {}).get("id_prefix", "REQ")


def requirement_ids_in(text: str, config: Config) -> list[str]:
    """Every requirement id in the text: `<id_prefix>-<section>-R<n>`."""
    return re.findall(rf"\b{re.escape(requirement_prefix(config))}-{SECTION_AND_RULE}\b", text)


MODIFIED_TIME = re.compile(r"modifiedTime\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")
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


MENTIONED_EMAIL = re.compile(r"[@+]?\b([A-Za-z0-9._%+-]+)@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")


def without_emails(text: str) -> str:
    """
    A Doc comment names whoever it @-mentions by their email address, and the snapshot is committed:
    no person's email goes in the repository (CLAUDE.md, Private settings). The API gives no author's
    address to map it back to a name, so the mention keeps only the part before the @.
    """
    return MENTIONED_EMAIL.sub(lambda m: "@" + m.group(1), text)


def render_threads(threads: list[dict], modified: str, today: str) -> str:
    """The comment threads as a snapshot: what was asked, on which text, and what the BA answered."""
    ordered = sorted(threads, key=lambda t: t.get("createdTime", ""))
    lines = [f"# FSD comment threads — {today}", "",
             f"> Saved by `book fsd-threads --save` from the live FSD Doc (modifiedTime {modified}): "
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
    return without_emails("\n".join(lines))


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


FSD_REVISION = re.compile(r" - V(\d+)\.pdf$")
CONTENTS_LINE = re.compile(r"^(\s*)(\S.*?)\s*\.{4,}\s*(\d+)\s*$")
PDFTOTEXT_SECONDS = 120


@dataclass(frozen=True)
class ContentsEntry:
    title: str
    level: int
    listed_page: int  # the page number the contents print, not the PDF's page index
    page: int | None  # the PDF page (1-based) its heading is on; None when the heading was not found
    line: int  # the heading's line within that page, -1 when not found


def latest_fsd(root: Path, sources: dict) -> Path:
    """The FSD revision to build from: the highest `V<n>` numerically — a lexical sort puts V9 above V13."""
    folder = root / sources.get("fsd_dir", "requirements/fsd")
    revisions = [(int(m.group(1)), path) for path in folder.glob("*.pdf") if (m := FSD_REVISION.search(path.name))]
    if not revisions:
        raise FactoryError(f"No `… - V<n>.pdf` in {folder.relative_to(root).as_posix()}.")
    return max(revisions)[1]


def pdf_pages(pdf: Path) -> list[str] | None:
    """
    The PDF's text, one string per page — pdftotext's `-layout`, printed to stdout (its last argument is
    `-`, so it never writes a file beside the PDF). None when pdftotext is not installed: the standard
    library cannot decode the FSD's fonts.
    """
    tool = shutil.which("pdftotext")
    if not tool:
        return None
    try:
        done = subprocess.run([tool, "-layout", "-enc", "UTF-8", str(pdf), "-"], capture_output=True,
                              timeout=PDFTOTEXT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FactoryError(f"pdftotext could not read {pdf.name}: {error}") from None
    if done.returncode != 0:
        raise FactoryError(f"pdftotext could not read {pdf.name}: {done.stderr.decode('utf-8', 'replace').strip()}")
    pages = done.stdout.decode("utf-8", "replace").replace("\r\n", "\n").split("\f")
    return pages[:-1] if pages and not pages[-1].strip() else pages


ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), " ")


def normal(text: str) -> str:
    """Whitespace collapsed and case folded. A zero-width character counts as a space: a numbered FSD's
    headings print one after the number (`2.2.7.\u200b  Pagination …`), which `str.split` keeps."""
    return " ".join(text.translate(ZERO_WIDTH).split()).casefold()


SECTION_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.*)$")


def heading_keys(title: str) -> set[str]:
    """What names a heading exactly: its title, and for a numbered FSD (`2.2.7. Pagination …`) also its
    number alone (`2.2.7`) and its words alone (`Pagination …`)."""
    text = normal(title).rstrip(".")
    keys = {text}
    if m := SECTION_NUMBER.match(text):
        keys |= {m.group(1), m.group(2).rstrip(".")}
    return keys


def contents(pages: list[str]) -> list[ContentsEntry]:
    """
    The table of contents — every dot-leader line — with each heading's PDF page, found by walking
    the text after the contents in order, so a repeated sub-heading (*Flow of Events*) lands under its
    own parent. Levels come from indentation: `-layout` indents each contents page from its own
    shallowest entry, so a page's indents are ranked from the deepest, which every page reaches.

    A numbered FSD (`2.2.7. Pagination …`) takes the level from the number instead, and also finds a
    heading by its number opening a line: `-layout` can set the number apart from its words
    (`2.5.3.` on one line, `Edit` beside the text below), and a number of two parts or more names one
    heading only. The text starts on the line after the last contents line - the same page, when the
    contents end part-way down it.
    """
    listed, last = [], (0, 0)
    for number, page in enumerate(pages, 1):
        for index, line in enumerate(page.split("\n")):
            if m := CONTENTS_LINE.match(line):
                last = (number, index + 1)
                listed.append((number, len(m.group(1).expandtabs()), m.group(2), int(m.group(3))))
    if not listed:
        return []
    indents: dict[int, list[int]] = {}
    for number, indent, *_ in listed:
        indents.setdefault(number, [])
        if indent not in indents[number]:
            indents[number].append(indent)
    depth = max(len(v) for v in indents.values())
    entries = []
    page, line = last  # the text starts after the last contents line
    for number, indent, title, listed_page in listed:
        ranks = sorted(indents[number])
        level = depth - len(ranks) + ranks.index(indent)
        numbered = SECTION_NUMBER.match(normal(title))
        if numbered:
            level = numbered.group(1).count(".")
        opener = numbered.group(1) + "." if numbered and "." in numbered.group(1) else None
        found = None
        for p in range(page, len(pages) + 1):
            lines = pages[p - 1].split("\n")
            for i in range(line if p == page else 0, len(lines)):
                text = normal(lines[i])
                if text == normal(title) or (opener and text.split(" ", 1)[0] == opener):
                    found = (p, i)
                    break
            if found:
                break
        if found:
            page, line = found[0], found[1] + 1
        entries.append(ContentsEntry(title, level, listed_page, found[0] if found else None,
                                     found[1] if found else -1))
    return entries


def section_pages(entries: list[ContentsEntry], index: int, pages: list[str]) -> tuple[int, int] | None:
    """The PDF pages a section spans: its heading's page to the page before the next heading at its level
    or above — that page too when the next heading starts below text on it."""
    entry = entries[index]
    if entry.page is None:
        return None
    for after in entries[index + 1:]:
        if after.level <= entry.level and after.page is not None:
            above = [l for l in pages[after.page - 1].split("\n")[:after.line] if l.strip()]
            return entry.page, max(entry.page, after.page if above else after.page - 1)
    return entry.page, len(pages)


def command_fsd_page(root: Path, config: Config, args: argparse.Namespace) -> tuple[str, bool]:
    """Which pages of the highest FSD revision hold a section; with --print, those pages' text. Read only."""
    sources = config.raw.get("requirements", {}).get("sources") or {}
    pdf = latest_fsd(root, sources)
    rel = pdf.relative_to(root).as_posix()
    head = [f"FSD: {rel} (the highest V<n>, numerically)"]
    pages = pdf_pages(pdf)
    if pages is None:
        return "\n".join(head + [
            "Could not locate the section: pdftotext is not installed, and no page index exists outside the PDF.",
            "Not guessed. Install pdftotext (poppler-utils or xpdf), or read the PDF's contents yourself — "
            "the book plugin's book:fact-check/references/reading-the-fsd.md."]), False
    entries = contents(pages)
    if not entries:
        return "\n".join(head + ["No table of contents (dot-leader lines) in the text: not located, not guessed."]), False
    wanted = normal(args.section).rstrip(".")
    matches = [i for i, e in enumerate(entries) if wanted in heading_keys(e.title)]
    if not matches:
        matches = [i for i, e in enumerate(entries) if wanted in normal(e.title)]
    if not matches:
        return "\n".join(head + [f"\"{args.section}\" is not a heading in the contents: not found, not guessed. "
                                 "Use the heading's words as the contents print them."]), False
    lines, ok = head, True
    for i in matches:
        entry = entries[i]
        parents, level = [], entry.level
        for before in reversed(entries[:i]):
            if before.level < level:
                parents.insert(0, before.title)
                level = before.level
        trail = " › ".join(parents + [entry.title])
        span = section_pages(entries, i, pages)
        if span is None:
            ok = False
            lines.append(f"{trail}: listed on contents page {entry.listed_page}, but its heading was not found in "
                         "the text (garbled or renamed) — not located, not guessed.")
            continue
        first, last = span
        where = f"PDF page {first}" if first == last else f"PDF pages {first}–{last}"
        lines.append(f"{trail}: {where} (contents page {entry.listed_page})")
        lines.append(f"  pdftotext -layout -f {first} -l {last} \"{rel}\" -")
        if args.print:
            lines += ["", *[pages[p - 1].rstrip() for p in range(first, last + 1)], ""]
    return "\n".join(lines), ok
def folder_for(label: str, mapping: dict) -> str:
    """The folder a LINKS line's snapshots live in: the longest `folders` key its label starts with."""
    lowered = label.lower()
    keys = [key for key in mapping if lowered.startswith(key.lower())
            and (len(lowered) == len(key) or not lowered[len(key)].isalnum())]
    return mapping[max(keys, key=len)] if keys else ""


def snapshot_files(folder: Path) -> list[Path]:
    """Each capture in a folder: its files, and a versioned sub-folder (wireframes-V5/) as one."""
    found = []
    for item in sorted(folder.iterdir()) if folder.is_dir() else []:
        if item.is_dir():
            if snapshots.version(item.name) >= 0:
                found.append(item)
        elif snapshots.is_snapshot(f"{folder.name}/{item.name}", folder.name):
            found.append(item)
    return found


def last_commit_day(root: Path, refs: list[str], rel: str) -> str:
    """The day (%cs) the last commit on `refs` added or replaced a capture under `rel`; "" if none."""
    out = git_out(root, "-c", "core.quotePath=false", "log", "--no-merges", "--diff-filter=AM",
                  "--format=@@%cs", "--name-only", *refs, "--", f":(literal){rel}")
    day = ""
    for line in out.splitlines():
        if line.startswith("@@"):
            day = line[2:]
        elif line and day and (line == rel or snapshots.is_snapshot(line, rel)):
            return day
    return ""


def newest_snapshot(root: Path, rel: str) -> tuple[str, str]:
    """
    (name, day) of a folder's newest capture: the highest V<n>, numerically, where the names carry one;
    else the file a commit last added or replaced. Its day comes from its name, else that commit.
    """
    files = snapshot_files(root / rel)
    if not files:
        return "", ""
    days = {item: last_commit_day(root, ["HEAD"], f"{rel}/{item.name}") for item in files}
    versioned = [item for item in files if snapshots.version(item.name) >= 0]
    if versioned:
        newest = max(versioned, key=lambda item: snapshots.version(item.name))
    else:
        newest = max(files, key=lambda item: (days[item], item.name))
    name = newest.name + ("/" if newest.is_dir() else "")
    return name, snapshots.name_date(newest.name) or days[newest] or "not committed"


def capture_refs(root: Path, config: Config) -> list[str]:
    """
    Where this week's capture may already be, as last fetched (never fetched here): the base branch,
    this branch, and every general-refresh branch not yet merged — an open capture counts too.
    """
    base_ref = config.raw.get("base_ref", "origin/dev")
    refs = [ref for ref in (base_ref, "HEAD") if git(root, "rev-parse", "--verify", "--quiet", ref)[0] == 0]
    unmerged = git_out(root, "branch", "-r", "--no-merged", base_ref, "--list", "origin/chore/docs-*")
    return refs + [line.strip() for line in unmerged.splitlines() if line.strip() and "->" not in line]


def capture_due(root: Path, refs: list[str], rel: str, today: datetime.date) -> str:
    """The weekly rule, for one folder: whether this week's capture is still to take."""
    last = snapshots.parse_day(last_commit_day(root, refs, rel)) if refs else None
    if last and snapshots.week_of(last) == snapshots.week_of(today):
        following = snapshots.monday(today) + datetime.timedelta(days=7)
        return f"capture: taken this week ({last}), the next from {following}"
    return (f"capture: open this week (last {last or 'none in git'}), at the weekly sweep if the live check "
            "finds a move")


def requirements_status(root: Path, config: Config, today: datetime.date) -> str:
    """
    The mechanical part of `/book:check-sources all`, from the repository only: every source in the
    index, whether it has a live link, our newest snapshot, the last check, and the weekly rule. It
    never fetches anything, so a source with a live link is "needs a live check" — never up to date.
    """
    sources = config.raw.get("requirements", {}).get("sources") or {}
    index_rel = sources.get("links_file", "requirements/read-me.txt")
    index = root / index_rel
    if not index.is_file():
        raise FactoryError(f"No requirements index at {index_rel} ([requirements.sources] links_file).")
    text = index.read_text(encoding="utf-8", errors="replace")
    base = Path(index_rel).parent.as_posix()
    mapping = sources.get("folders") or {}
    out_of_scope = set(sources.get("out_of_scope") or [])
    refs = capture_refs(root, config)
    live, stale, unverified, lines, placed = [], [], [], [], set()
    shown: set[str] = set()

    def folder_facts(folder: str) -> str:
        """Once per folder: several LINKS lines (the QA sheets) share one."""
        rel = f"{base}/{folder}"
        if folder in shown:
            return "as above"
        shown.add(folder)
        if not (root / rel).is_dir():
            return f"no folder {rel}/"
        name, day = newest_snapshot(root, rel)
        read_me = root / rel / "read-me.txt"
        checked, outcome = snapshots.last_checked(read_me.read_text(encoding="utf-8", errors="replace")
                                                  if read_me.is_file() else "")
        facts = [f"newest {name!r} ({day})" if name else "no snapshot",
                 f"last checked {checked} ({outcome})" if checked else "no Last checked line"]
        return "; ".join(facts)

    def recorded(folder: str) -> None:
        read_me = root / base / folder / "read-me.txt"
        line = read_me.read_text(encoding="utf-8", errors="replace") if read_me.is_file() else ""
        found = snapshots.LAST_CHECKED.search(line)
        said = found.group(1).lower() if found else ""
        # By folder: its one Last checked line cannot say which of the sources sharing it moved.
        if "not captured" in said or "captured at the next" in said:
            stale.append(f"{folder}/")
        elif "could not check" in said:
            unverified.append(f"{folder}/ (its last check could not check it)")

    for label, value in snapshots.links(text):
        state, detail = snapshots.link_state(value)
        folder = folder_for(label, mapping)
        where = f"{folder}/" if folder else "no folder in [requirements.sources.folders]"
        facts = folder_facts(folder) if folder else ""
        placed.add(folder)
        if state != "live":
            lines.append(f"- {label} — {state}: {detail} — {where}" + (f"; {facts}" if facts else ""))
            continue
        if folder in out_of_scope:
            lines.append(f"- {label} — live link, out of scope (book:check-sources does not fetch it) — {where}; {facts}")
            continue
        due = capture_due(root, refs, f"{base}/{folder}", today) if folder else "unknown: no folder"
        lines.append(f"- {label} — live link — {where}; {facts}" + ("" if facts == "as above" else f"; {due}")
                     + "; needs a live check")
        live.append(label)
        if folder:
            recorded(folder)
    for section, why in ((snapshots.LIVE_SECTION, "a live source with no line in LINKS"),
                         (snapshots.NO_LIVE_SECTIONS, "no live link: delivered files or our own code, nothing to check")):
        for folder in snapshots.folders(text, section):
            if folder not in placed:
                lines.append(f"- {folder}/ — {why}; {folder_facts(folder)}")
                if section == snapshots.LIVE_SECTION:
                    unverified.append(f"{folder}/ (no line in LINKS)")
    stale, unverified = list(dict.fromkeys(stale)), list(dict.fromkeys(unverified))
    if stale:
        verdict = (f"STALE — {len(stale)} source folder(s) recorded as moved and not yet captured: {', '.join(stale)}"
                   + (f"; and {len(live)} source(s) need a live check" if live else ""))
    elif live or unverified:
        named = live + unverified
        verdict = (f"COULD NOT VERIFY — {len(named)} source(s) need a live check, which this command never makes: "
                   + ", ".join(named))
    else:
        verdict = "UP TO DATE — no source has a live link to check"
    others = len(refs) - len([ref for ref in refs if not ref.startswith("origin/chore/docs-")])
    read = ", ".join(ref for ref in refs if not ref.startswith("origin/chore/docs-")) or "no ref"
    read += f" and {others} unmerged origin/chore/docs-* branch(es)" if others else ""
    footer = (f"Read from the repository only ({index_rel} and the folders beside it); nothing was fetched. "
              f"Weekly rule: {snapshots.WEEKLY_RULE}; this week began {snapshots.monday(today)}; captures read "
              f"from {read}, as last fetched (`git fetch origin` first).")
    return "\n".join([verdict, "", *lines, "", footer])


def command_requirements(root: Path, config: Config, args: argparse.Namespace) -> str:
    """`requirements status`: the index and our snapshots, offline (requirements_status). `requirements import`:
    the old audit into the register (requirements_register.py, beside the checks)."""
    if args.action == "import":
        checks = str(root / "devtools" / "checks")
        if checks not in sys.path:
            sys.path.insert(0, checks)
        import requirements_register
        return requirements_register.import_command(root, args.apply, datetime.date.today().isoformat())
    return requirements_status(root, config, datetime.date.today())


def glob_match(path: str, pattern: str) -> bool:
    """
    A repository glob: `*` spans folders, and `**/` also matches no folder at all — so
    `tests/**/*.py` holds `tests/test_pricing.py`. fnmatch alone needs a folder there, so a
    flat test folder matched nothing. Case-insensitive on every OS, as the guard reads them:
    fnmatch.fnmatch folds case only on Windows, so on Linux CI `.Claude/settings.local.json`
    passed the AI-files gate while NTFS reads it as `.claude/`.
    """
    path, pattern = path.lower(), pattern.lower()
    return fnmatch.fnmatchcase(path, pattern) or ("**/" in pattern
                                                  and fnmatch.fnmatchcase(path, pattern.replace("**/", "")))


def bitbucket_client(root: Path, personal: bool = False) -> bitbucket.Bitbucket:
    """The seam tests replace. `personal`: the person's own stored credential, never BITBUCKET_TOKEN."""
    try:
        return bitbucket.Bitbucket.for_repository(root, personal)
    except bitbucket.BitbucketError as error:
        raise FactoryError(str(error)) from error


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
    register = register_files(root)
    if register:
        # The register names each requirement's tests, not its code: a changed test file ties its rows.
        names = {Path(path).stem for path in paths if Path(path).suffix.lower() not in NOT_CODE}
        for _, _, header, rows in register_tables(register):
            for line in rows:
                row = dict(zip(header, book_config.table_cells(line)))
                if any(re.search(rf"\b{re.escape(name)}\b", row.get("Tests", "")) for name in names):
                    requirement_ids.add(row["Id"].strip("*` "))
    elif traceability.is_file():
        names = {path.rsplit("/", 1)[-1] for path in paths if path.startswith(source_roots)}
        for line in traceability.read_text(encoding="utf-8", errors="replace").splitlines():
            if any(name in line for name in names):
                requirement_ids.update(requirement_ids_in(line, config))
    regenerate = [rule["note"] for rule in settings.get("regenerate", [])
                  if any(re.search(rule["match"], path) for path in paths)]
    return {"paths": paths, "modules": modules, "cross_cutting": cross_cutting,
            "requirement_ids": sorted(requirement_ids), "regenerate": regenerate}


def render_impact(report: dict) -> str:
    """The impact report for the terminal: every path repository-relative, as the person runs it from the root."""
    lines = []
    if report["modules"]:
        lines.append("**Modules**")
        lines += [f"- {m['name']} — tests `{m['tests']}`"
                  + (f"; guide `{m['guide']}`" if m.get("guide") else "")
                  + (f"; plan `{m['plan']}`" if m.get("plan") else "")
                  for m in report["modules"]]
    if report["cross_cutting"]:
        lines.append("\n**Cross-cutting** — no module owns these, so the whole integration suite runs:")
        lines += [f"- `{p}`" for p in report["cross_cutting"][:15]]
    if report["requirement_ids"]:
        lines.append("\n**Requirement ids** the requirements record ties to these files: "
                     + ", ".join(report["requirement_ids"]))
    if report["regenerate"]:
        lines.append("\n**Regenerate**")
        lines += [f"- {item}" for item in report["regenerate"]]
    if not lines and report["paths"]:
        # Paths that reach nothing are not "nothing changed": a docs or tooling change has no module to test.
        count = len(report["paths"])
        lines.append(f"{count} path{'s' if count != 1 else ''}; none reaches a module, a cross-cutting path or a "
                     "generated doc (docs or tooling only).")
    elif not lines:
        lines.append("Nothing changed against the base yet — name the paths you expect to touch: "
                     "`book impact <paths…>`.")
    return "\n".join(lines)


def integration_tests(config: Config) -> str:
    """`[commands] integration_project`: the folder of the integration tests ("" = none), with a slash."""
    folder = str((config.raw.get("commands") or {}).get("integration_project") or "").strip("/")
    return folder + "/" if folder else ""


def in_process(config: Config) -> str:
    """`[commands] integration_filter`: the filter for the in-process half of the integration tests — the
    tests that need a deployed host skip without it, and the skip gate fails any run that skips."""
    return str((config.raw.get("commands") or {}).get("integration_filter") or "")


def with_filter(base: str, module_tests: str) -> str:
    return f"{base}&(({module_tests}))" if base else f"(({module_tests}))"


def ci_tier(root: Path, config: Config, base: str, paths: list[str] | None = None) -> dict:
    """
    How much of devtools/checks/ci-local.sh a change needs (docs/plans/local-ci-tiers.md §3): the
    highest tier any changed path reaches. Full for a cross-cutting or high-tier path, for an
    integration test no module owns — the module map cannot say what such a file reaches — and for
    more than one module, which CLAUDE.md's testing rule runs in full.
    """
    report = impact(root, config, base, paths)
    paths = report["paths"]
    high = [path for path in paths
            if any(re.search(pattern, path, re.IGNORECASE) for pattern in config.raw.get("tier", {}).get("high", []))]
    folder = integration_tests(config)
    stray = [path for path in paths if folder and path.startswith(folder)
             and not any(re.search(m["match"], path, re.IGNORECASE) for m in config.modules)]
    if report["cross_cutting"] or high or stray:
        reached = list(dict.fromkeys(report["cross_cutting"] + high + stray))
        return {"tier": "full", "filter": in_process(config), "modules": [],
                "reason": "reaches every module: " + ", ".join(reached[:3]) + (", …" if len(reached) > 3 else "")}
    names = [m["name"] for m in report["modules"]]
    if len(names) > 1:
        return {"tier": "full", "filter": in_process(config), "modules": names,
                "reason": "more than one module: " + ", ".join(names)}
    if names:
        module = report["modules"][0]
        return {"tier": "modules", "filter": with_filter(in_process(config), module['tests']), "modules": names, "reason": names[0]}
    # `checks` is an allow-list: a path it does not name (a seed script, .editorconfig, the pipeline)
    # can change what builds or what the tests read, so it runs at least the build.
    checks_only = config.raw.get("tier", {}).get("checks_only", [])
    built = [path for path in paths if not any(glob_match(path, glob) for glob in checks_only)]
    if built:
        return {"tier": "build", "filter": "", "modules": [],
                "reason": "outside docs and tooling, no module's integration tests: " + ", ".join(built[:3])
                          + (", …" if len(built) > 3 else "")}
    return {"tier": "checks", "filter": "", "modules": [], "reason": "no code changed"}


def render_ci_tier(result: dict) -> str:
    """One key per line, for ci-local.sh to read with sed."""
    return f"tier={result['tier']}\nfilter={result['filter']}\nreason={result['reason']}"


def jira_project(config: Config) -> str:
    """The [jira] project key (`PROJ`): what makes a PR Jira-tracked. Empty when none is set."""
    return str((config.raw.get("jira") or {}).get("project") or "")


def jira_settings(config: Config) -> tuple[str, str]:
    settings = config.raw.get("jira") or {}
    if not settings.get("site") or not settings.get("project"):
        raise FactoryError("factory.toml has no [jira] site and project.")
    return settings["site"], settings["project"]


def command_jira(root: Path, config: Config, args: argparse.Namespace) -> str:
    """One Jira issue, read only, for a session to read — the credential stays in this process."""
    site, project = jira_settings(config)
    key = args.key.upper()
    if not key.startswith(f"{project}-"):
        raise FactoryError(f"{key} is not a {project} issue — [jira] project in factory.toml.")
    try:
        return jira.render(jira.read_issue(site, key, jira.credential(root, site)), site)
    except jira.JiraError as error:
        raise FactoryError(f"Could not read {key} from Jira: {error}.") from None


def review_marker(root: Path) -> Path:
    """Where /book:self-review's last verdict is kept: inside .git, never in the repository."""
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
    """while Pipelines is switched off, devtools/checks/ci-local.sh gates a pull request."""
    return bool((config.raw.get("local_ci") or {}).get("required"))


def recorded_local_ci(root: Path) -> tuple[str, str]:
    """The last result devtools/checks/ci-local.sh recorded inside .git and its commit, or ("", "")."""
    path = Path(git_out(root, "rev-parse", "--git-path", "factory-local-ci").strip())
    marker = path if path.is_absolute() else root / path
    if not marker.exists():
        return "", ""
    parts = marker.read_text(encoding="utf-8").split()
    return (parts[0], parts[1]) if len(parts) >= 2 else ("", "")


FINGERPRINT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def content_fingerprint(root: Path, config: Config) -> str:
    """
    The one owner of the review fingerprint: what /book:self-review reviewed, and what /book:commit compares
    to reuse that verdict. It hashes the merge base with the base branch (origin/dev, even on a
    stacked branch) and the tree the working copy would commit: every tracked file as it stands,
    staged or not, and every untracked file .gitignore does not exclude, by name, mode and content.
    Committing exactly that work leaves the hash unchanged, so a verdict given before the commit can
    be recorded for it afterwards. Any edit, an untracked file added, renamed or left out, or a new
    base changes it.

    The hand-typed snippet it replaces hashed `git diff <base>` plus the untracked files' contents.
    That hash changed the moment a new file was committed, because the file moved from raw content
    into the diff, and it never saw an untracked file's name. Built in a throwaway index, so the
    person's staging is left as it was; the only write is git's own objects inside .git.
    """
    base_ref = config.raw["base_ref"]
    try:
        base = merge_base(root, base_ref)
    except FactoryError:
        raise FactoryError(f"No merge base between HEAD and {base_ref} (git merge-base failed): there is "
                           f"nothing to measure the change against. Fetch {base_ref} and re-run.") from None
    with tempfile.TemporaryDirectory() as folder:
        index = Path(folder) / "index"
        env = {**os.environ, "GIT_INDEX_FILE": str(index)}

        def run(*args: str, stdin: str | None = None) -> str:
            done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=GIT_SECONDS, env=env, input=stdin)
            if done.returncode != 0:
                raise FactoryError(f"git {args[0]} failed (exit {done.returncode}) while taking the fingerprint; "
                                   "no hash rather than a wrong one.")
            return done.stdout.strip()

        real = Path(git_strict(root, "rev-parse", "--git-path", "index").strip())
        real = real if real.is_absolute() else root / real
        if real.is_file():
            shutil.copyfile(real, index)  # its stat cache spares hashing every file again
        else:
            run("read-tree", "HEAD")
        run("add", "-A")
        tree = run("write-tree")
        return run("hash-object", "--stdin", stdin=f"base {base}\ntree {tree}\n")


def command_fingerprint(root: Path, config: Config, args: argparse.Namespace) -> str:
    """Fetch, then print the fingerprint of the change a review covers (content_fingerprint)."""
    code, _ = git(root, "fetch", "origin")
    if code != 0:
        raise FactoryError(f"Could not fetch origin (exit {code}): the fingerprint is taken against the current "
                           f"{config.raw['base_ref']}. Fix the fetch and re-run; a review without one is not recorded.")
    return content_fingerprint(root, config)


def command_reviewed(root: Path, config: Config, args: argparse.Namespace) -> str:
    """
    Records /book:self-review's verdict for the current commit, so `book pr open` can require a READY
    one (CLAUDE.md, Git: run /book:self-review before opening a PR). A review covers uncommitted work too,
    so a verdict is recorded only for a clean tree — otherwise it would vouch for a commit it never saw.

    With --fingerprint, the hash `book fingerprint` printed when the review began: the verdict is
    recorded only when this commit holds exactly that content against the same base. That is how
    /book:commit records the verdict of a review it ran over uncommitted work, once it has committed all of
    it, so `pr open` needs no second review; an edit after the review, an untracked file added or left
    out, or a new base refuses.
    """
    status = git_strict(root, "status", "--porcelain")
    if status.strip():
        count = len(status.splitlines())
        raise FactoryError(f"The tree has uncommitted changes ({count} path{'s' if count != 1 else ''}): a verdict is "
                           "recorded only for a commit that holds everything reviewed. Commit it all, or review again "
                           "after committing, then record.")
    commit = head(root)
    extra = ""
    if args.fingerprint is not None:
        given = args.fingerprint.strip().lower()
        if not FINGERPRINT.fullmatch(given):
            raise FactoryError("--fingerprint takes the hash `book fingerprint` printed when the review began.")
        now = content_fingerprint(root, config)
        if now != given:
            raise FactoryError(f"This commit is not what was reviewed: its fingerprint is {now[:12]}, the review's was "
                               f"{given[:12]}. Something changed after the review — an edit, an untracked file added or "
                               "left out, or a new base. Run /book:self-review on this commit.")
        extra = f" {given}"
    marker = review_marker(root)
    marker.write_text(f"{args.verdict} {commit} {utc_now()}{extra}\n", encoding="utf-8")
    return f"Recorded {args.verdict} for {commit[:8]}" + (" (the reviewed content, by fingerprint)." if extra else ".")


def command_pr(root: Path, config: Config, args: argparse.Namespace) -> str:
    """
    Pull request work, for a session as well as a person: show a PR, list its
    comments, open one from the current branch, replace an open one's description, post one comment or a
    review's findings. The token is read inside this
    process (bitbucket.py) and goes only to Bitbucket — never printed, never in the transcript, so the
    session needs no credential of its own (CLAUDE.md, Private settings). Writes act in the person's
    name, with their own stored credential. Nothing here approves, merges or declines.
    """
    action = args.action
    branch = current_branch(root)
    destination = args.destination or config.raw["base_ref"].removeprefix("origin/")
    if action == "open":
        if branch in config.raw["protected_branches"] or branch in ("", "HEAD"):
            raise FactoryError(f"{branch or 'a detached HEAD'} is not a work branch; open a PR from your own branch.")
        if destination in config.raw["protected_branches"] and destination != config.raw["base_ref"].removeprefix("origin/"):
            raise FactoryError(f"A PR into {destination} is a promotion — a person's step, not this command's.")
        if not args.title.strip():
            raise FactoryError("Give the PR a --title.")
        # A git that could not reach origin is said as that, not as "push first".
        pushed = git_strict(root, "ls-remote", "origin", f"refs/heads/{branch}").split("\t")[0]
        if pushed != head(root):
            raise FactoryError(f"origin does not have this commit on {branch} yet. Push first: "
                               f"`git push -u origin {branch}`.")
        verdict, reviewed_at = recorded_review(root)
        if verdict != "READY" or reviewed_at != head(root):
            last = ("No verdict is recorded." if not verdict
                    else "Its last verdict was for another commit." if reviewed_at != head(root)
                    else f"It answered {verdict}.")
            raise FactoryError(f"Run /book:self-review on this commit first; it must answer READY (CLAUDE.md, Git). {last}")
        if local_ci_required(config):
            result, passed_at = recorded_local_ci(root)
            if result != "PASS" or passed_at != head(root):
                last = ("No local CI result is recorded." if not result
                        else "Its last pass was for another commit." if passed_at != head(root)
                        else f"It recorded {result}.")
                raise FactoryError("CI is switched off: run `sh devtools/checks/ci-local.sh` on this commit "
                                   f"first and paste its Local CI section into the description. {last}")
    body = ""
    if action in ("open", "describe", "comment"):
        if not args.body_file:
            raise FactoryError("Give the text with --body-file <path>.")
        body = pr_body_text(root, args.body_file)
    if action == "open":
        # The description pr-check reads in CI, checked before the PR exists: while CI is suspended,
        # nothing else would.
        problems = bitbucket.description_problems(
            {"title": args.title, "description": body,
             "source": {"branch": {"name": branch}, "commit": {"hash": head(root)}}},
            local_ci_required(config), jira_project(config), ai_files_touched(root, config, destination))
        if problems:
            raise FactoryError("The description has " + "; ".join(problems) + " (CLAUDE.md, Git).")
    if action in ("comments", "describe", "comment", "post", "resolve") and not args.number:
        raise FactoryError("Name the PR with --number.")
    findings: list[dict] = []
    if action == "post":
        if not args.plan:
            raise FactoryError("Give the findings with --plan <file>: a JSON list of {path, line, body}.")
        if not args.expect_head:
            raise FactoryError("pr post needs --expect-head <sha>, the head commit the review read.")
        findings = posting_plan(root, args.plan)
    if action == "comment" and (not args.path or args.line < 1):
        raise FactoryError("An inline comment needs --path and --line (the line in the new file).")
    if action == "resolve" and args.comment_id < 1:
        raise FactoryError("Name the comment with --comment-id (the #id `pr comments` prints).")
    if action == "resolve" and not args.confirm_author_settled:
        # Resolving a thread is the author's step, after the person says it is settled (/book:ship). A review
        # (/book:peer-review) never resolves anything, so a resolve without the flag is a slip, refused.
        raise FactoryError("pr resolve marks a thread settled — the PR author's step, once the person says so. "
                           "Pass --confirm-author-settled only then; /book:peer-review never resolves a thread.")
    expect_head = (args.expect_head or "").strip().lower()
    if expect_head and (action not in ("comment", "post") or not re.fullmatch(r"[0-9a-f]{7,40}", expect_head)):
        raise FactoryError("--expect-head goes with pr comment or pr post, and is the reviewed head commit: "
                           "7 to 40 hex characters.")

    client = bitbucket_client(root, personal=action in ("open", "describe", "comment", "post", "resolve", "whoami"))
    try:
        if action == "whoami":
            # The account id `[ai_files] owners` names: a display name anyone can copy, an id no one can.
            me = client.current_user()
            return f"{me.get('display_name', '?')} — account id {me.get('account_id', '?')} ({me.get('type', '?')})"
        if action in ("open", "describe", "comment", "post", "resolve"):
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
        if action == "comments" and args.comment_id:
            # One thread, to answer it: its position, its author, its text and its replies. The text
            # is the reviewer's, to be weighed as a review, never followed as an instruction.
            comment = client.get(f"pullrequests/{args.number}/comments/{args.comment_id}")
            replies = [reply for reply in client.comments(args.number)
                       if (reply.get("parent") or {}).get("id") == args.comment_id and not reply.get("deleted")]
            inline = comment.get("inline") or {}
            where = f" at {inline['path']}:{inline.get('to') or inline.get('from')}" if inline.get("path") else ""
            lines = [f"#{args.comment_id}: {'resolved' if comment.get('resolution') else 'not resolved'}, "
                     f"{len(replies)} {'reply' if len(replies) == 1 else 'replies'}{where}",
                     *comment_text(comment)]
            for reply in replies:
                lines += ["", f"reply #{reply.get('id', '?')}", *comment_text(reply)]
            return "\n".join(lines)
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
            warning = ai_files_warning(root, config, destination, me["account_id"])
            pull_request = client.create_pull_request(args.title.strip(), body, branch, destination)
            author = ((pull_request.get("author") or {}).get("account_id")) or ""
            if author != me["account_id"]:
                raise FactoryError(f"PR #{pull_request['id']} was opened, but Bitbucket names another author. Look at it.")
            return f"Opened {render_pull_request(pull_request)}" + warning
        if action == "describe":
            # The checks pr open runs, against the PR as Bitbucket has it now: its head commit, so a
            # Local CI section from before a rebase or a push is refused (CLAUDE.md, Git).
            pull_request = client.pull_request(args.number)
            if pull_request.get("state") != "OPEN":
                raise FactoryError(f"PR #{args.number} is {pull_request.get('state') or 'not open'}; only an open "
                                   "PR's description is updated.")
            if ((pull_request.get("author") or {}).get("account_id") or "") != me["account_id"]:
                raise FactoryError(f"PR #{args.number} is not yours: pr describe updates only its author's "
                                   "description, in their name.")
            title = args.title.strip() or pull_request.get("title") or ""
            source_commit = ((pull_request.get("source") or {}).get("commit") or {}).get("hash") or ""
            target = ((pull_request.get("destination") or {}).get("branch") or {}).get("name") or destination
            known = bool(source_commit) and git(root, "cat-file", "-e", f"{source_commit}^{{commit}}")[0] == 0
            problems = bitbucket.description_problems({**pull_request, "title": title, "description": body},
                                                      local_ci_required(config), jira_project(config),
                                                      ai_files_touched(root, config, target, source_commit) if known else None)
            if not known:
                problems.append(f"PR #{args.number}'s head commit {source_commit[:12] or '(unknown)'} is not in this "
                                "clone, so which files that steer the AI it changes is unknown - fetch its branch")
            if problems:
                raise FactoryError("The description has " + "; ".join(problems) + " (CLAUDE.md, Git). Nothing sent.")
            updated = client.update_description(args.number, title, body)
            return f"Updated the description of {render_pull_request(updated)}"
        if action == "resolve":
            client.resolve_comment(args.number, args.comment_id)
            return f"Comment #{args.comment_id} on PR #{args.number} marked resolved."
        if expect_head:
            # A review's line numbers belong to the head it read: on a newer head they can land on the wrong
            # line, or on code that changed, so nothing from that review is posted there.
            current = (((client.pull_request(args.number).get("source") or {}).get("commit") or {}).get("hash") or "").lower()
            if not same_commit(expect_head, current):
                raise FactoryError(f"PR #{args.number}'s head is now {current[:12] or 'unknown'}, not the reviewed "
                                   f"{expect_head[:12]} — nothing posted. Review the new head before commenting.")
        if action == "post":
            return post_findings(client, args.number, me["account_id"], findings, expect_head)
        posted = client.post_comment(args.number, body, args.path, args.line)
        return f"Comment #{posted.get('id', '?')} posted on PR #{args.number} at {args.path}:{args.line}."
    except bitbucket.BitbucketError as error:
        raise FactoryError(str(error)) from error


def same_commit(expected: str, current: str) -> bool:
    """One commit, however short each is written — Bitbucket gives a PR's head as 12 characters — down to 7."""
    shorter = min(len(expected), len(current))
    return shorter >= 7 and expected[:shorter] == current[:shorter]


def pr_body_text(root: Path, body_file: str) -> str:
    """
    The text `pr open` and `pr comment` publish, read only from a file a session may write — the
    repository or the temp folder (the scratchpad is under it) — and never one the guard calls private,
    nor holding a secret or a home path. What this posts, every reviewer on the PR reads.
    """
    guard = guard_module()
    body = pr_text_file(root, body_file).strip()
    if not body:
        raise FactoryError(f"{body_file} is empty.")
    if guard.secret_findings("pull request text", body):
        raise FactoryError(f"{body_file} holds what looks like a secret or a home-folder path. "
                           "Nothing like that goes on a pull request.")
    return body


def guard_module():
    """The guard's own definitions of private and secret: one place, not two."""
    hooks = str(Path(__file__).resolve().parent / "hooks")
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    import guard
    return guard


# How much of a comment's text, whitespace folded, says it is the same finding as one already posted.
BODY_START = 80


def body_start(text: str) -> str:
    return " ".join(text.split())[:BODY_START]


def posting_plan(root: Path, plan_file: str) -> list[dict]:
    """
    `pr post`'s findings: a JSON list of {path, line, body}, from a file `pr comment` could read its body
    from. Refused whole, before Bitbucket is reached, when any finding is malformed, holds a secret or a
    hidden marker, or shares its line with another — two findings on one line are merged or moved at
    plan time (/book:peer-review Step 5), never posted as two.
    """
    guard = guard_module()
    try:
        plan = json.loads(pr_text_file(root, plan_file).lstrip("\ufeff"))
    except ValueError as error:
        raise FactoryError(f"{plan_file} is not JSON: {error}") from error
    if not isinstance(plan, list) or not plan:
        raise FactoryError(f"{plan_file} must be a non-empty JSON list of {{path, line, body}}.")
    findings, seen = [], set()
    for number, item in enumerate(plan, 1):
        where = f"{plan_file}, finding {number}"
        if not isinstance(item, dict) or set(item) - {"path", "line", "body"}:
            raise FactoryError(f"{where}: each finding is an object with path, line and body only.")
        path, line, body = item.get("path"), item.get("line"), item.get("body")
        path = path.strip() if isinstance(path, str) else path
        if not isinstance(path, str) or not path or "\\" in path:
            raise FactoryError(f"{where}: path is the file as the diff names it, with forward slashes.")
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            raise FactoryError(f"{where}: line is the line in the new file, a whole number from 1.")
        if not isinstance(body, str) or not body.strip():
            raise FactoryError(f"{where}: body is the comment's text, and it is empty.")
        body = body.strip()
        if "<!--" in body:
            raise FactoryError(f"{where}: an HTML comment renders as visible text on Bitbucket; "
                               "nothing machine-readable goes in a body.")
        if guard.secret_findings("pull request text", body):
            raise FactoryError(f"{where} holds what looks like a secret or a home-folder path. "
                               "Nothing like that goes on a pull request.")
        if (path, line) in seen:
            raise FactoryError(f"{where}: {path}:{line} has another finding already. Merge the two, or move one "
                               "to the line it belongs on — one comment per line.")
        seen.add((path, line))
        findings.append({"path": path, "line": line, "body": body})
    return findings


def own_inline_comments(client: bitbucket.Bitbucket, number: int, account_id: str) -> dict[tuple[str, int], list[tuple[int, str]]]:
    """This account's top-level, undeleted inline comments: (path, line) → [(id, body start)]."""
    mine: dict[tuple[str, int], list[tuple[int, str]]] = {}
    for comment in client.comments(number):
        inline = comment.get("inline") or {}
        if comment.get("parent") or comment.get("deleted") or not inline.get("path"):
            continue
        if (comment.get("user") or {}).get("account_id") != account_id:
            continue
        line = inline.get("to") or inline.get("from") or 0
        text = (comment.get("content") or {}).get("raw") or ""
        mine.setdefault((inline["path"], line), []).append((comment.get("id", 0), body_start(text)))
    return mine


def already_posted(mine: dict, finding: dict) -> int | None:
    """The id of this account's comment at the finding's line that starts as the finding does, if any."""
    start = body_start(finding["body"])
    return next((cid for cid, text in mine.get((finding["path"], finding["line"]), []) if text == start), None)


def post_findings(client: bitbucket.Bitbucket, number: int, account_id: str, findings: list[dict],
                  head_sha: str) -> tuple[str, bool]:
    """
    /book:peer-review Step 5's posting, after the person said yes: in plan order, one comment per finding;
    a finding this account already posted on that line is skipped, so a re-run resumes rather than
    duplicates. The first failure stops the run — a POST that got no answer may have landed, so it is
    never sent again here. Then the comments are read back and every posted finding is looked for on
    its line, rather than trusting what the posting said.
    """
    mine = own_inline_comments(client, number, account_id)
    rows, posted, skipped, failed = [], 0, 0, None
    for index, finding in enumerate(findings):
        where = f"{finding['path']}:{finding['line']}"
        existing = already_posted(mine, finding)
        if existing is not None:
            skipped += 1
            rows.append(f"skipped  #{existing}  {where}  (already posted by this account)")
            continue
        try:
            answer = client.post_comment(number, finding["body"], finding["path"], finding["line"])
        except bitbucket.BitbucketError as error:
            failed = index
            rows.append(f"failed   {where}  — {error}")
            break
        posted += 1
        finding["id"] = answer.get("id", "?")
        rows.append(f"posted   #{finding['id']}  {where}")
    untried = findings[failed + 1:] if failed is not None else []
    rows += [f"not attempted  {f['path']}:{f['line']}" for f in untried]
    summary = (f"PR #{number} at head {head_sha[:12]}: {posted} posted, {skipped} skipped, "
               f"{0 if failed is None else 1} failed, {len(untried)} not attempted (of {len(findings)}).")
    # Reconcile: what is on the PR now, not what the posting said.
    try:
        after = own_inline_comments(client, number, account_id)
    except bitbucket.BitbucketError as error:
        return "\n".join([summary, *rows, f"Could not read the comments back to reconcile ({error}) — "
                                          "run `pr comments` before trusting these counts."]), False
    missing = [f"{f['path']}:{f['line']}" for f in findings[:len(findings) if failed is None else failed]
               if already_posted(after, f) is None]
    lines = [summary, *rows]
    if missing:
        lines.append("Not on the PR when read back: " + ", ".join(missing) + " — look in Bitbucket before posting again.")
    else:
        lines.append("Read back: every posted and skipped finding is on the PR at its line.")
    if failed is not None:
        landed = already_posted(after, findings[failed])
        where = f"{findings[failed]['path']}:{findings[failed]['line']}"
        lines.append(f"The failed {where} is on the PR after all, as #{landed}." if landed is not None else
                     f"The failed {where} is not on the PR when read back. Stopped there; nothing was retried. "
                     "Fix the cause, then run pr post again — it skips what is already posted.")
    return "\n".join(lines), failed is None and not missing


def pr_text_file(root: Path, body_file: str) -> str:
    """A file a PR's text may come from — tracked in the repository, or in the temp folder — read whole."""
    guard = guard_module()
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
    return path.read_text(encoding="utf-8")


def comment_text(comment: dict) -> list[str]:
    """A comment's author and its raw text, indented under them; a deleted one says so."""
    author = (comment.get("user") or {}).get("display_name", "?")
    if comment.get("deleted"):
        return [f"  {author}: (deleted)"]
    text = ((comment.get("content") or {}).get("raw") or "").strip()
    return [f"  {author}:", *(f"    {line}" for line in text.splitlines() or [""])]


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
    problems = bitbucket.description_problems(pull_request, local_ci_required(config), jira_project(config))
    if problems:
        return f"PR #{number}'s description: " + "; ".join(problems) + " (CLAUDE.md, Git).", False
    return f"PR #{number}'s description has what CLAUDE.md asks for.", True


def ai_files_settings(root: Path, config: Config, destination: str) -> tuple[dict, str]:
    """
    `[ai_files]` as the destination branch has it, never as the pull request does: a PR that added
    its author to `owners`, or narrowed `paths`, would otherwise approve itself.
    Only before the destination has the section at all — the PR that brings it — is the branch's own
    used, and the step says so.
    """
    # The destination's own settings, merged over the defaults as that branch holds them.
    try:
        held = book_config.load_at(root, f"origin/{destination}")
    except book_config.ConfigError:
        raise FactoryError(f"origin/{destination}'s factory.toml (or a Book defaults file there) is not valid "
                           "TOML.") from None
    settings = (held or {}).get("ai_files")
    if settings and settings.get("owners") is not None:
        return settings, ""
    return (config.raw.get("ai_files") or {},
            f" (origin/{destination} has no [ai_files] yet, so this branch's own list was used)")


def ai_files_changed(root: Path, settings: dict, base: str, head_ref: str = "HEAD") -> list[str]:
    """
    The files this branch changed that steer the AI (`[ai_files] paths`, less `except`). A rename
    names both its paths: with git's rename detection only the new one showed, so moving
    `.claude/settings.json` out of the paths passed as nothing changed.
    """
    return [path for path in git_strict(root, "diff", "--no-renames", "--name-only", base, head_ref).splitlines()
            if is_ai_file(path, settings)]


def ai_files_touched(root: Path, config: Config, destination: str, head_ref: str = "HEAD") -> list[str]:
    """The AI files a branch (ending at `head_ref`) changed against origin/<destination>; refuses when unknown."""
    settings, _ = ai_files_settings(root, config, destination)
    if not settings.get("paths"):
        raise FactoryError("[ai_files] paths is empty, so no file counts as one that steers the AI - set it in "
                           "devtools/factory/factory.toml")
    base = git_out(root, "merge-base", f"origin/{destination}", head_ref)
    if not base:
        raise FactoryError(f"no merge base between origin/{destination} and {head_ref[:12]}, so the files that steer "
                           "the AI this change touches are unknown. Fetch both and run again.")
    return ai_files_changed(root, settings, base, head_ref)


def ai_files_line(root: Path, config: Config, destination: str) -> str:
    """
    The *AI files* line of the Local CI section (devtools/checks/ci-local.sh): which files that steer the
    AI the branch changed, so the description names them and the owner's approval is asked for (U1b #7).
    """
    touched = ai_files_touched(root, config, destination)
    if not touched:
        return "AI files: none changed"
    listing = ", ".join(touched[:12]) + (f" and {len(touched) - 12} more" if len(touched) > 12 else "")
    return f"AI files: {len(touched)} changed ({listing}) - an [ai_files] owner makes or approves this PR"


def ai_files_warning(root: Path, config: Config, destination: str, account_id: str) -> str:
    """
    `pr open`'s note when the person opening is not an AI-files owner and the PR changes files that
    steer the AI. `book ai-files` is the check, in CI; while CI is suspended nothing runs it before
    the merge, so the person opening is told here. It warns and never refuses, and the description is
    left as written. The person is the Bitbucket account the open uses — the id `[ai_files] owners`
    names, from the destination branch — compared here and never printed.
    """
    try:
        settings, source = ai_files_settings(root, config, destination)
        base = git_out(root, "merge-base", f"origin/{destination}", "HEAD")
        if not base:
            return (f"\nWARN  could not check whether this PR changes files that steer the AI: no merge base "
                    f"with origin/{destination}.")
        touched = ai_files_changed(root, settings, base)
    except FactoryError as error:
        return f"\nWARN  could not check whether this PR changes files that steer the AI: {error}"
    owners = {str(owner.get("account_id", "")) for owner in settings.get("owners", [])} - {""}
    if not touched or account_id in owners:
        return ""
    listing = ", ".join(touched[:8]) + (f" and {len(touched) - 8} more" if len(touched) > 8 else "")
    held = ("while CI is suspended, the person merging holds it" if local_ci_required(config)
            else "CI's ai-files-owner step checks it")
    return (f"\nWARN  {len(touched)} file(s) in this PR steer the AI ({listing}){source}. You are not an owner in "
            f"[ai_files]: an owner must make or approve this PR after its latest commit (CLAUDE.md, Git); {held}.")


def is_ai_file(path: str, settings: dict) -> bool:
    return (any(glob_match(path, glob) for glob in settings.get("paths", []))
            and not any(glob_match(path, glob) for glob in settings.get("except", [])))


# The guard's own files, among the AI files: its code, its rules and where Claude Code wires it in.
GUARD_OWN = ("devtools/factory/hooks/**", CONFIG_RELATIVE, ".claude/settings.json")


def ai_files_staged(root: Path, config: Config) -> tuple[str, bool]:
    """
    The pre-commit hook's note: the guard asks before a session changes the files that steer the
    AI only when it sees the write — an edit, or a shell write it recognises. A script (`python edit.py`)
    or `git apply` changes them where no hook can look, so the commit is where every such change is
    named, whoever or whatever wrote it. It warns and never refuses: a person who is not an owner may
    still change these, with an owner's approval on the pull request, which `book ai-files` checks.
    Whether the committer is an owner is a hint from git's user.name against the owners' names — a name
    anyone can set, so it decides nothing; it is compared here and never printed.
    """
    destination = config.raw["base_ref"].removeprefix("origin/")
    settings, source = ai_files_settings(root, config, destination)
    staged = git_strict(root, "diff", "--cached", "--no-renames", "--name-only").splitlines()
    touched = [path for path in staged if is_ai_file(path, settings)]
    if not touched:
        return "", True
    guard = [path for path in touched if any(glob_match(path, glob) for glob in GUARD_OWN)]
    listing = ", ".join(touched[:8]) + (f" and {len(touched) - 8} more" if len(touched) > 8 else "")
    name = git_out(root, "config", "user.name").strip().casefold()
    names = {str(owner.get("name", "")).strip().casefold() for owner in settings.get("owners", [])} - {""}
    who = "an owner" if name and name in names else "not an owner"
    lines = [f"pre-commit: this commit changes {len(touched)} file(s) that steer the AI: {listing}{source}."]
    if guard:
        lines.append(f"  {len(guard)} of them are the guard's own. The guard asks before a session changes "
                     "them only when it sees the write; a script or `git apply` changes them unseen, so this "
                     "is where such a change is named. Check the diff is one you meant.")
    lines.append(f"  Your git name is {who} in [ai_files]. The pull request passes only when an owner makes it, "
                 "or approves it after its latest commit (`book ai-files`, CLAUDE.md Git); while CI is "
                 "suspended, the person merging holds that.")
    return "\n".join(lines), True


def command_ai_files(root: Path, config: Config, args: argparse.Namespace) -> tuple[str, bool]:
    """
    CI, on every pull request: a change to the files that steer the AI — its rules, skills, the
    tools, the guard, the checks, the hooks, CI — is made or approved by an owner: an owner's own pull
    request passes, and anyone else's passes once an owner approved it after its latest commit (decided by
    the owner, 2026-10-04; Bitbucket never lets an author approve their own PR, so making it is approving
    it). The guard asks before a session edits them; this holds for a person editing by hand too. An
    approval arrives after the build, so the owner re-runs this step. `--staged` is the pre-commit
    hook's note instead: the staged AI files, named, never refused (ai_files_staged).
    """
    if getattr(args, "staged", False):
        return ai_files_staged(root, config)
    if getattr(args, "list", False):
        try:
            return ai_files_line(root, config, config.raw["base_ref"].removeprefix("origin/")), True
        except FactoryError as error:
            return f"Could not list the files that steer the AI: {error}", False
    destination = os.environ.get("BITBUCKET_PR_DESTINATION_BRANCH") or config.raw["base_ref"].removeprefix("origin/")
    settings, source = ai_files_settings(root, config, destination)
    owners = {owner["account_id"]: owner["name"] for owner in settings.get("owners", [])}
    who = ", ".join(owners.values()) or "an owner ([ai_files] owners is empty)"
    base = git_out(root, "merge-base", f"origin/{destination}", "HEAD")
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
        # counts.
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


PLUGIN_PREFIX = "book:"


def review_scope(config: Config, paths: list[str]) -> dict:
    """
    Which parts of the review checklist a change needs read: the method and Pass A always, and each
    Pass B dimension unless every triage row a path matched lists it as normally N/A — the table's own
    rule (review-method.md, How to use this framework). It over-includes when unsure: a path no row
    places loads every Pass B file, since a path pattern can hide a cross-cutting effect.
    """
    settings = config.raw.get("review_scope") or {}
    dimensions: dict[str, str] = settings.get("dimensions") or {}
    every = list(dimensions)
    loaded: set[str] = set()
    matched: dict[str, int] = {}
    reasons: dict[str, set[str]] = {}
    unplaced: list[str] = []
    for path in paths:
        rows = [row for row in settings.get("rows", []) if any(glob_match(path, g) for g in row["match"])]
        if not rows:
            unplaced.append(path)
            loaded.update(every)
            continue
        loaded.update(d for d in every if not all(d in row["not_applicable"] for row in rows))
        for row in rows:
            matched[row["row"]] = matched.get(row["row"], 0) + 1
            for d in row["not_applicable"]:
                reasons.setdefault(d, set()).add(row["row"])
    folder = settings.get("folder", "")

    def place(name: str) -> str:
        # `book:<skill>/<path>` is a file in the book plugin; anything else is the project's, under `folder`.
        return name if name.startswith(PLUGIN_PREFIX) else f"{folder}/{name}"

    files = [place(name) for name in settings.get("always", [])]
    files += [place(dimensions[d]) for d in every if d in loaded]
    left_out = {d: sorted(reasons.get(d, set())) for d in every if d not in loaded}
    return {"files": files, "matched": matched, "unplaced": unplaced, "left_out": left_out, "paths": paths}


def render_review_scope(report: dict) -> str:
    lines = ["Read these review checklist files, and no others:"]
    lines += [f"  {path}" for path in report["files"]]
    if any(path.startswith(PLUGIN_PREFIX) for path in report["files"]):
        lines.append(f"A `{PLUGIN_PREFIX}<skill>/<path>` file is in the book plugin; any other path is the project's.")
    if not report["paths"]:
        lines.append("Nothing changed against the base: only the method and Pass A.")
    if report["matched"]:
        lines.append("Triage rows matched: " + "; ".join(f"{row} ({n})" for row, n in report["matched"].items()))
    if report["unplaced"]:
        shown = ", ".join(report["unplaced"][:10]) + (", …" if len(report["unplaced"]) > 10 else "")
        lines.append(f"No triage row places {len(report['unplaced'])} path(s), so every Pass B file loads: {shown}")
    if report["left_out"]:
        lines.append("Not loaded — each is still a row in the coverage table, N/A for the triage reason:")
        grouped: dict[tuple, list[str]] = {}
        for d, rows in report["left_out"].items():
            grouped.setdefault(tuple(rows), []).append(d)
        lines += [f"  {', '.join(ds)}: " + (f"normally N/A for {'; '.join(rows)}" if rows
                                            else "N/A — nothing changed against the base")
              for rows, ds in grouped.items()]
    return "\n".join(lines)


def command_review_scope(root: Path, config: Config, args: argparse.Namespace) -> str:
    if args.paths:
        paths = list(args.paths)
    elif args.range:
        # Both names of a rename: the path a file left matters as much as the one it reached.
        paths = sorted({line for line in git_strict(root, "diff", "--name-only", "--no-renames", args.range).splitlines()
                        if line})
    else:
        base = merge_base(root, config.raw["base_ref"])
        names = set(changed_paths(root, base))
        names.update(line for line in git_strict(root, "diff", "--name-only", "--no-renames", base).splitlines() if line)
        paths = sorted(names)
    return render_review_scope(review_scope(config, paths))


REGISTER = "docs/requirements"  # the requirements register, from Book 1.6 (.claude/book/docs.md, Requirements register)
NOT_CODE = (".md", ".txt", ".toml", ".json", ".yml", ".yaml", ".csv", ".pdf")


def register_files(root: Path) -> list[Path]:
    """The register's module files, as check-book reads them; none in a Book before 1.6."""
    folder = root / REGISTER
    return sorted(p for p in folder.glob("*.md") if not p.name.startswith("_")) if folder.is_dir() else []


def register_tables(files: list[Path]):
    """(file, its `# ` title, a table's header cells, that table's row lines) for each table in each file."""
    for path in files:
        title, header, rows = "", None, []
        for line in path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n"):
            if line.startswith("# ") and not title:
                title = line
            if not line.lstrip().startswith("|"):
                if header:
                    yield path, title, header, rows
                header, rows = None, []
                continue
            if header is None:
                header = book_config.table_cells(line)
            elif not set(line.replace("|", "").strip()) <= set("-: "):
                rows.append(line)
        if header:
            yield path, title, header, rows


def register_rows(root: Path, files: list[Path], needle: str) -> str:
    """The register's answer to --block: a module whose file or title names the text, whole; else matching rows."""
    wanted = needle.casefold()
    whole, matching = [], []
    for path, title, header, rows in register_tables(files):
        rel = path.relative_to(root).as_posix()
        head = "| " + " | ".join(header) + " |"
        if wanted in path.stem.replace("-", " ").casefold() or wanted in title.casefold():
            whole.append(f"{rel}\n{title}\n{head}\n" + "\n".join(rows))
        else:
            hits = [line for line in rows if wanted in line.casefold()]
            if hits:
                matching.append(f"{rel}\n{title}\n{head}\n" + "\n".join(hits))
    if whole:
        return f"{REGISTER}/: {len(whole)} module(s) whose file or title names {needle!r}\n\n" + "\n\n".join(whole)
    if matching:
        return f"{REGISTER}/: {needle!r} in {len(matching)} table(s), matching rows only\n\n" + "\n\n".join(matching)
    return (f"{REGISTER}/: no register row names {needle!r}. A section with no rows is unbuilt — name the plan "
            "that would own it from docs/project.md.")


def traceability_blocks(root: Path, config: Config, needle: str) -> str:
    """
    The part of the traceability matrix one question needs, so /book:impact reads that and not the
    whole file (hundreds of KB). The matrix is one `## ` block per module, its heading naming the FSD
    sections, pages and plan. A heading that holds the text returns its whole block; failing that, each
    block whose body holds it returns its heading and only those lines (a code path, a requirement id).
    Case is ignored. No match says so: a section with no rows is unbuilt, which is a finding, not an error.
    """
    files = register_files(root)
    if files:
        # From Book 1.6 the register is the record and the matrix a retired stub (Book D106).
        return register_rows(root, files, needle)
    rel = (config.raw.get("requirements") or {}).get("traceability", "")
    path = root / rel
    if not rel or not path.is_file():
        raise FactoryError(f"No traceability matrix at {rel or '[requirements] traceability'}: /book:fact-check writes it. "
                           "Nothing to read, which is not the same as nothing traced.")
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    blocks, current = [], None
    for line in text.split("\n"):
        if line.startswith("## "):
            current = [line]
            blocks.append(current)
        elif current is not None:
            current.append(line)
    wanted = needle.casefold()
    by_heading = [block for block in blocks if wanted in block[0].casefold()]
    if by_heading:
        return f"{rel}: {len(by_heading)} block(s) whose heading names {needle!r}\n\n" + "\n\n".join(
            "\n".join(block).rstrip() for block in by_heading)
    by_body = [(block[0], [line for line in block[1:] if wanted in line.casefold()]) for block in blocks]
    by_body = [(heading, lines) for heading, lines in by_body if lines]
    if not by_body:
        return (f"{rel}: no block names {needle!r}. A section with no rows is unbuilt — name the plan that would "
                "own it from docs/project.md.")
    return f"{rel}: {needle!r} in {len(by_body)} block(s), matching lines only\n\n" + "\n\n".join(
        heading + "\n" + "\n".join(lines) for heading, lines in by_body)


def command_impact(root: Path, config: Config, args: argparse.Namespace) -> str:
    if args.block:
        return traceability_blocks(root, config, args.block)
    # Named paths need no base; only reading the branch's own change does.
    base = "HEAD" if args.paths else merge_base(root, config.raw["base_ref"])
    if args.ci:
        return render_ci_tier(ci_tier(root, config, base, args.paths or None))
    report = impact(root, config, base, args.paths or None)
    return render_impact(report)


def command_deploy(root: Path, config: Config, args: argparse.Namespace) -> tuple[str, bool]:
    """/book:deploy's mechanical steps (deploy.py); DEV and QA only, the host from factory.toml."""
    try:
        return deploy.main(root, config.raw, args)
    except deploy.DeployError as error:
        raise FactoryError(str(error)) from None


def private_presence(raw: dict) -> dict:
    """
    For `book verify`: whether the private settings file exists and each PRIVATE_KEYS key is set,
    and whether the deploy variables ([deploy] test_db_variable, smoke_url_variable, smoke_credentials and
    each smoke_pairs account) are set — names and yes/no only, never a value. The file was already read in
    this process by load_config, as every book command reads it; nothing here reads it again.
    """
    keys = {f"[{section}.{table}] {key}": bool(((raw.get(section) or {}).get(table) or {}).get(key))
            for (section, table), names in PRIVATE_KEYS.items() for key in names}
    deploy_settings = raw.get("deploy") or {}
    names = [deploy_settings.get("test_db_variable"), deploy_settings.get("smoke_url_variable"),
             *deploy_settings.get("smoke_credentials", [])]
    for pair in deploy_settings.get("smoke_pairs", []):
        names += [pair.get("email"), pair.get("password")]
    variables = {name: bool(os.environ.get(name)) for name in dict.fromkeys(names) if name}  # `test -n`, in-process
    return {"file": "present" if private_settings_path(raw).is_file() else "absent", "keys": keys,
            "variables": variables}


def settings_location(raw: dict) -> str:
    """
    Where the private settings file is, for a person to find it — the home folder written `~`, so no
    person's home path is printed. A file the settings variable puts outside the home folder is named
    by the variable alone: its path is that person's, not the repository's.
    """
    path = private_settings_path(raw)
    for candidate, home in ((path, Path.home()), (path.resolve(), Path.home().resolve())):
        try:
            return "~/" + candidate.relative_to(home).as_posix()
        except ValueError:
            continue
    if os.environ.get(settings_variable(raw)):
        return f"the file {settings_variable(raw)} names"
    return path.as_posix()  # factory.toml's own `private_settings`, outside any home folder: already committed


def command_settings(root: Path, config: Config | None, args: argparse.Namespace) -> tuple[str, bool]:
    """
    `book settings --check`: each private setting the tooling reads — PRIVATE_KEYS from the private
    settings file, and the deploy smoke variables from the environment — by name, `set` or `not set`.
    The file is read in this process only to test which keys it holds (load_config, as every command
    reads it); no value, part of a value or length leaves this function, and the file is never quoted.
    Exit 1 only when the file is not valid TOML.
    """
    if tomllib is None:
        raise FactoryError("book needs Python 3.11 or later (tomllib).")
    committed = book_config.load(root)  # the repository's own settings: `private_settings` comes from here
    try:
        presence = private_presence(load_config(root).raw)
        state = presence["file"]
    except FactoryError:  # the private file is not valid TOML; load_config's message names its full path
        presence = private_presence(committed)
        presence["keys"] = {name: False for name in presence["keys"]}
        state = "invalid"
    described = {"present": "present",
                 "absent": "absent - a person writes their own (devtools/README.md, Private settings)",
                 "invalid": "NOT VALID TOML - fix it yourself; it is never quoted here"}[state]
    lines = [f"Private settings file: {settings_location(committed)} ({described})"]
    lines += [f"  {name}: {'set' if is_set else 'not set'}" for name, is_set in presence["keys"].items()]
    if presence["variables"]:
        lines.append("Deploy smoke variables (environment):")
        lines += [f"  {name}: {'set' if is_set else 'not set'}" for name, is_set in presence["variables"].items()]
    lines.append("Names and set / not set only: no value is read out.")
    return "\n".join(lines), state != "invalid"


def command_config(root: Path, config: Config | None, args: argparse.Namespace) -> str:
    """
    One value of the repository's settings - its factory.toml merged over the Book's defaults - by dotted key,
    for a script or a skill (Publish-FeGuidesToDrive.ps1 reads `fe_guides.drive_folder_id`), so the value
    keeps one owner and no second TOML parser. The private settings are never merged in, so none of them can
    be printed. A string, number or boolean prints as it is; with `--json` any value prints as JSON, a list or
    a table included. Without `--json` a table or a list is refused. With no key, `--json` prints the whole
    merged settings; without `--json` a missing key is refused.
    """
    if tomllib is None:
        raise FactoryError("book needs Python 3.11 or later (tomllib).")
    key = getattr(args, "key", None)
    if not key and not getattr(args, "json", False):
        raise FactoryError("Name a key (table.key), or add --json to print the whole merged settings.")
    try:
        settings = book_config.load(root)
        value: object = book_config.value(settings, key) if key else settings
    except book_config.ConfigError as error:
        raise FactoryError(f"The Book's settings are unreadable: {error}.") from None
    except KeyError:
        raise FactoryError(f"{CONFIG_RELATIVE} and the Book's defaults set no {args.key}.") from None
    if getattr(args, "json", False):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if not isinstance(value, (str, int, float)):
        raise FactoryError(f"{args.key} in {CONFIG_RELATIVE} is a table or a list; name one value.")
    return str(value)


def command_book(root: Path, config: Config | None, args: argparse.Namespace) -> tuple[str, bool]:
    """Whether the Book is set up (book.py). Runs even where the private settings file is not valid TOML."""
    try:
        config = load_config(root)  # reads the private file in its own process, as every command does
        private = private_presence(config.raw)
        raw = config.raw
    except FactoryError:
        private = {"file": "invalid", "keys": {}}
        raw = {}
        if tomllib is not None and (root / CONFIG_RELATIVE).is_file():
            try:
                raw = book_config.load(root)
            except (OSError, ValueError) as error:
                raise FactoryError(f"{CONFIG_RELATIVE} is unreadable ({error}); `book verify` cannot check on an "
                                   "empty config.") from None
    except (OSError, ValueError) as error:
        # Not an empty config: that would report every file as missing and say nothing of why.
        raise FactoryError(f"The factory configuration is unreadable ({error}); `book verify` cannot check on an "
                           "empty config.") from None
    try:
        return book.main(root, raw, args, private)
    except book.BookError as error:
        raise FactoryError(str(error)) from None


# /book:sync-sources's record of its last pass, and the docs its delta set covers (the skill's Modes).
SYNC_RECORD = "docs/project.md"
# The line's shape: `<date> · dev <sha> (FSD V<n>, matrix V<n>) · <mode> — <notes>` - the revisions in
# brackets are optional, and the mode is what the pass wrote ("delta", "full", "phases 1 and 5 only").
SYNC_DOCS = ("docs", "CLAUDE.md", "README.md")
LAST_SYNC = re.compile(r"^\*\*Last requirements sync:\*\*\s*(\d{4}-\d{2}-\d{2})\s*·\s*dev\s*`?([0-9a-f]{7,40})`?"
                       r"(?:\s*\([^)\n]*\))?\s*·\s*([^·—:,\n]*[^·—:,\s])", re.M)
SYNC_MODES = ("delta", "full", "verify-only")
SYNC_LISTED = 60


def commit_of(root: Path, ref: str) -> str:
    """The full commit a ref or short SHA names in this clone, or "" when it names none."""
    return git_out(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")


def listed(paths: list[str], indent: str = "    ") -> list[str]:
    more = len(paths) - SYNC_LISTED
    return [indent + path for path in paths[:SYNC_LISTED]] + ([f"{indent}… and {more} more"] if more > 0 else [])


def qa_deployed(root: Path, config: Config) -> tuple[list[str], str, bool]:
    """
    QA's deployed commit through `book deploy status --target qa` — the function, not a subprocess,
    so it stays the one read-only code path. Returns its lines, the commit it named and whether that was
    a live read. Nothing it raises stops the plan: the plan says COULD NOT READ and carries on, and the
    skill stops at its QA baseline instead.
    """
    try:
        text, live = deploy.command_status(root, config.raw, argparse.Namespace(target="qa"))
    except (deploy.DeployError, OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        return [f"  COULD NOT READ — {error}"], "", False
    found = re.search(r"Commit:\s+([0-9a-f]{7,40})", text)
    return [f"  {line}" for line in text.splitlines()], found.group(1) if found else "", live


def range_lines(root: Path, base: str, dev: str, label: str, paths: bool) -> list[str]:
    """DEV minus a baseline: its commits, its merges and the paths it changed."""
    commits = git_strict(root, "rev-list", "--count", "--no-merges", f"{base}..{dev}")
    merges = git_strict(root, "rev-list", "--count", "--merges", "--first-parent", f"{base}..{dev}")
    changed = [line for line in git_strict(root, "diff", "--name-only", f"{base}...{dev}").splitlines() if line]
    lines = [f"  DEV minus {label} ({base[:12]}..{dev[:12]}): {commits} commit(s), {merges} merge(s) on dev, "
             f"{len(changed)} path(s) changed"]
    if paths and changed:
        # By area, not path by path: which modules the code reaches is Phase 5's, from task-evidence.py.
        areas: dict[str, int] = {}
        for path in changed:
            parts = path.split("/")
            area = "/".join(parts[:2]) if parts[0] in ("src", "tests", "docs") and len(parts) > 2 else parts[0]
            areas[area] = areas.get(area, 0) + 1
        lines.append("    by area: " + ", ".join(f"{area} {count}" for area, count in sorted(areas.items())))
    return lines


def command_sync_plan(root: Path, config: Config, args: argparse.Namespace) -> str:
    """
    /book:sync-sources's Phase 0, the mechanical half (skills audit L5): fetch; pin the SHAs the pass
    compares; read the skill's last-sync line from origin/dev; the mode it implies and the delta set;
    QA's deployed commit; DEV minus QA. Read-only — it writes no file and changes no branch (the fetch
    updates remote-tracking refs, nothing else). The skip rules are printed with the facts it has and
    decided by none of it: they are judgement, and stay the skill's, as do the branch and the baseline
    confirmation.
    """
    lines: list[str] = []
    if git(root, "fetch", "--quiet", "origin", "dev", "qa")[0] != 0:
        lines.append("WARN  git fetch failed — the SHAs below are origin as last fetched")
    dev = commit_of(root, "origin/dev")
    if not dev:
        raise FactoryError("origin/dev does not resolve in this clone; fetch it and re-run.")
    qa = commit_of(root, "origin/qa")
    branch = current_branch(root) or "?"
    dirty = len([line for line in git_out(root, "status", "--porcelain").splitlines() if line])
    lines += ["Baseline — pinned; compare against these SHAs, not the moving refs. If dev moves during the run, say so:",
              f"  origin/dev   {dev}",
              f"  origin/qa    {qa or 'not found'} — the qa branch: NOT the QA baseline (nothing deploys from qa yet)",
              f"  checkout     {branch}, " + ("clean" if not dirty else f"{dirty} uncommitted path(s)"),
              "", "QA's deployed commit — the QA baseline (book deploy status --target qa, read-only):"]
    status, deployed, live = qa_deployed(root, config)
    lines += status
    baseline = commit_of(root, deployed) if deployed else ""
    if deployed and not baseline:
        lines.append(f"  → baseline: {deployed[:12]} is not in this clone — fetch, or a person looks; no range below")
    elif baseline and live:
        lines.append(f"  → baseline: {baseline[:12]} (live read)")
    elif baseline:
        lines.append(f"  → baseline: {baseline[:12]} from the ledger — NOT a live read: the person confirms it first")
    else:
        lines.append("  → baseline: none — stop and ask the person; never fall back to the qa branch")

    lines += ["", f"Last sync — the `**Last requirements sync:**` line in {SYNC_RECORD} on origin/dev:"]
    found = LAST_SYNC.search(git_out(root, "show", f"{dev}:{SYNC_RECORD}"))
    synced = commit_of(root, found.group(2)) if found else ""
    on_dev = bool(synced) and git(root, "merge-base", "--is-ancestor", synced, dev)[0] == 0
    if not found:
        lines.append("  none — run full")
    else:
        when = datetime.date.fromisoformat(found.group(1))
        days = (datetime.date.today() - when).days
        lines.append(f"  {when} · dev {found.group(2)} · {found.group(3)} — {days} day(s) ago")
        if not on_dev:
            lines.append(f"  its commit {found.group(2)} is not in this clone or not on origin/dev — run full")
    suggested = "delta" if found and on_dev else "full"
    mode = args.mode or suggested
    lines += ["", f"Mode: {mode}" + (" (asked)" if args.mode else " (suggested)") +
              " — full on a first run, a long gap or when asked; verify-only when only DEV minus QA is asked. "
              "The skill says the mode in one line."]
    delta: list[str] = []
    if on_dev:
        delta = [line for line in git_strict(root, "diff", "--name-only", f"{synced}..{dev}", "--", *SYNC_DOCS)
                 .splitlines() if line]
        lines.append(f"Delta set: {len(delta)} file(s) under {', '.join(SYNC_DOCS)} changed since {synced[:12]}"
                     + ("" if mode == "delta" else f" — for reference; {mode} mode reads " +
                        ("every living doc" if mode == "full" else "none")) + ":")
        lines += listed(delta)
        lines.append("  plus every doc that cites a section this pass's FSD change list names — the skill adds "
                     "those after Phase 1")

    lines += ["", "DEV minus QA:"]
    if baseline:
        lines += range_lines(root, baseline, dev, "QA's deployed commit", paths=True)
        lines.append(f"  Phase 5 sorts it: python devtools/checks/task-evidence.py --since {baseline[:12]}")
    else:
        lines.append("  no QA baseline — Phase 5 waits for the person")
    if qa:
        lines += range_lines(root, qa, dev, "the qa branch, for reference only", paths=False)

    size = f"{len(delta)} file(s)" if on_dev else "unknown"
    lines += ["", "Skip rules — the skill confirms each and says which applied; this tool decides none:",
              "  Phase 2  skip when no source moved and no comment thread brought a new answer — Phase 1's finding",
              f"  Phase 3  skip when the delta set is empty (now {size}) and id_status.py and check-book.py are "
              "both clean — not run here",
              f"  Phase 4  skip when the last trim is under two weeks old — the skill checks — and the delta set "
              f"is under about 10 files (now {size})",
              "  Phase 5  never skipped",
              "", "Wrap-up: sh devtools/checks/ci-local.sh — the checks (repository) tier for a docs-only pass; a "
              "code commit raises it."]
    return "\n".join(lines)


def repo_root() -> Path:
    override = os.environ.get("FACTORY_ROOT")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[2]


def parser(prog: str = "book") -> argparse.ArgumentParser:
    # No abbreviations: an abbreviated option once hid the subcommand from the guard.
    top = argparse.ArgumentParser(prog=prog, description="The AI tools: pull requests, Jira, the files that steer the AI.",
                                  allow_abbrev=False)
    sub = top.add_subparsers(dest="command", required=True)
    pr = sub.add_parser("pr", help="a pull request — show, comments, open, describe, comment, post, resolve, whoami; the token stays in this process")
    pr.add_argument("action", choices=["show", "comments", "open", "describe", "comment", "post", "resolve", "whoami"])
    pr.add_argument("--number", type=int, default=0, help="the PR; show defaults to the current branch's open PR")
    pr.add_argument("--title", default="", help="open: the PR's title; describe: a new title (default: kept)")
    pr.add_argument("--body-file", default="", help="open, describe, comment: a file holding the description or comment")
    pr.add_argument("--destination", default="", help="open: the branch to merge into; default: the base branch")
    pr.add_argument("--path", default="", help="comment: the file, as the diff names it")
    pr.add_argument("--line", type=int, default=0, help="comment: the line in the new file")
    pr.add_argument("--comment-id", type=int, default=0, help="resolve, comments: the comment's id, as `pr comments` prints it; with comments, says whether that one is resolved")
    pr.add_argument("--confirm-author-settled", action="store_true",
                    help="resolve: required — the PR's author resolves, once the person says the thread is settled (/book:ship); "
                         "/book:peer-review never passes it")
    pr.add_argument("--expect-head", default="",
                    help="comment, post: the head commit the review read; refused when the PR's head is now another commit")
    pr.add_argument("--plan", default="",
                    help="post: a JSON list of findings, each {path, line, body}, posted in order after the person said yes")
    reviewed = sub.add_parser("reviewed", help="record /book:self-review's verdict for this commit (inside .git)")
    reviewed.add_argument("verdict", choices=["READY", "NOT-READY"])
    reviewed.add_argument("--fingerprint", default=None,
                          help="the hash `book fingerprint` printed when the review began; recorded only when "
                               "this commit holds exactly that content")
    sub.add_parser("fingerprint", help="the hash of what a review covers: the change against origin/dev, committed "
                                       "or not, untracked files included")
    protection = sub.add_parser("protection", help="read the branch restrictions back from Bitbucket")
    protection.add_argument("--check", action="store_true", help="exit 1 when a decided rule is missing")
    pr_check = sub.add_parser("pr-check", help="check a PR description has what CLAUDE.md asks for")
    pr_check.add_argument("number", type=int, nargs="?", default=0)
    ai_files = sub.add_parser("ai-files", help="CI: a change to the files that steer the AI is made or approved by an owner")
    ai_files.add_argument("number", type=int, nargs="?", default=0)
    ai_files.add_argument("--staged", action="store_true",
                          help="pre-commit: name the staged files that steer the AI and which are the guard's own; never refuses")
    ai_files.add_argument("--list", action="store_true",
                          help="ci-local.sh: the Local CI section's 'AI files:' line for this branch against base_ref")
    jira_parser = sub.add_parser("jira", help="read one Jira issue — a Task or a Bug; read only, the token stays in this process")
    jira_parser.add_argument("key", help="the issue key, e.g. PROJ-376")
    threads = sub.add_parser("fsd-threads", help="the live FSD against the last check; --save keeps its comment threads")
    threads.add_argument("--save", action="store_true", help="write requirements/fsd/comment-threads - <date>.md")
    page = sub.add_parser("fsd-page", help="which PDF pages of the highest FSD revision hold a section; read only")
    page.add_argument("section", help="the heading as the FSD's contents print it, e.g. \"Log In\"")
    page.add_argument("--print", action="store_true", help="also print those pages' text")
    requirements = sub.add_parser("requirements", help="offline: every requirements source, our newest snapshot, "
                                                       "its last check and the weekly capture rule; never fetches")
    requirements.add_argument("action", choices=["status", "import"],
                              help="import: the old audit (docs/Audit/spec-traceability.md) into the requirements "
                                   "register, once; report first")
    requirements.add_argument("--apply", action="store_true", help="import: write the register")
    sync = sub.add_parser("sync-plan", help="read-only: /book:sync-sources's pinned SHAs, last sync, mode, delta set, "
                                            "QA's deployed commit and the skip rules to confirm")
    sync.add_argument("--mode", choices=SYNC_MODES, default="", help="the mode the person asked for; default: suggested")
    scope = sub.add_parser("review-scope", help="which review checklist files the change needs read (over-includes when unsure)")
    scope.add_argument("paths", nargs="*", help="the changed paths; default: this branch against origin/dev, committed or not")
    scope.add_argument("--range", default="", help="a git diff range instead, e.g. origin/dev...origin/feature/x (an open PR)")
    impact_parser = sub.add_parser("impact", help="what the change reaches")
    impact_parser.add_argument("paths", nargs="*")
    impact_parser.add_argument("--ci", action="store_true", help="the local CI tier and integration filter (ci-local.sh)")
    impact_parser.add_argument("--block", default="", help="only the requirements register's rows for this text: a module "
                               "whose file or title names it, else the rows that do (before Book 1.6, the traceability "
                               "matrix's blocks)")
    if deploy is not None:
        deploy.add_parser(sub)
    book.add_parser(sub, prog)
    settings = sub.add_parser("settings", help="each private setting the tooling reads, by name: set or not set — "
                                               "never a value")
    settings.add_argument("--check", action="store_true", required=True,
                          help="report each key of the private settings file and each deploy smoke variable")
    config_parser = sub.add_parser("config", help="print one value of the settings (factory.toml over the Book's "
                                                  "defaults), e.g. fe_guides.drive_folder_id, or with --json and no "
                                                  "key all of them; never the private settings")
    config_parser.add_argument("key", nargs="?", help="the dotted key: table.key (with --json and no key, the "
                                                      "whole merged settings)")
    config_parser.add_argument("--json", action="store_true",
                               help="print the value as JSON - a list or a table too")
    return top


COMMANDS: dict[str, Callable] = {
    "pr": command_pr, "reviewed": command_reviewed, "fingerprint": command_fingerprint, "review-scope": command_review_scope, "protection": command_protection, "pr-check": command_pr_check,
    "ai-files": command_ai_files, "jira": command_jira, "fsd-threads": command_fsd_threads, "fsd-page": command_fsd_page,
    "requirements": command_requirements, "impact": command_impact, "sync-plan": command_sync_plan,
    "deploy": command_deploy, "book": command_book, "settings": command_settings,
    "config": command_config,
}


def main(argv: list[str] | None = None, prog: str = "book") -> int:
    """The command, run by devtools/book.py as `book`."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parser(prog).parse_args(argv)
    root = repo_root()
    try:
        # `book` and `settings` load their own: they must report a broken private settings file, not stop on it.
        # `config` reads factory.toml alone, never the private file.
        config = load_config(root) if args.command not in ("book", "settings", "config") else None
        result = COMMANDS[args.command](root, config, args)
    except FactoryError as error:
        print(f"{prog}: {error}", file=sys.stderr)
        return 1
    text, ok = result if isinstance(result, tuple) else (result, True)
    if text:
        print(text)
    return 0 if ok else 1


# Not a command from v1.5: a hook, pipeline or habit still running this file is told so and fails, rather than
# passing without having run.
NOT_A_COMMAND = ("factory.py is not a command from Book v1.5: run python devtools/book.py <command> "
                 "(`book verify`, `book pr open`, ...).")


if __name__ == "__main__":
    print(NOT_A_COMMAND, file=sys.stderr)
    sys.exit(2)
