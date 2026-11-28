#!/usr/bin/env python3
"""
What a branch must never change, judged by the files it changed — however it changed them.

Two of CLAUDE.md's "never" rules are about files, not commands:

- **`requirements/` is read-only** (CLAUDE.md, Layout): the client's documents are snapshots. A new
  snapshot or backup is added as a new file; a file already on the destination branch is never
  modified, deleted or renamed — except the few `/book:check-sources` maintains in place (the
  read-me.txt files, a tracker's current copy, whose predecessor it moves into backup/). Those may be
  edited, but never deleted or renamed away: capture replaces them in place.
- **Never edit an applied migration** (`[guard] applied`; the dotnet pack's Persistence rules): a migration already on
  the destination branch is never modified, deleted or renamed. Add a new one. The model snapshot
  (`never_by_hand`) is not judged here, as the guard does not judge it as applied: `dotnet ef
  migrations add` rewrites it with every new migration, and gen-database-erd.py --check already catches
  a snapshot that drifts. A new migration and its Designer file are additions, so they pass.

And one rule about how often a file may be added: **at most one capture per requirements source per
week**, Monday to Sunday (requirements/read-me.txt, Cadence; /book:check-sources, Phase 4). A capture is
a snapshot file the branch adds or replaces in a folder the index lists under HAS A LIVE LINK — not a
read-me.txt, a backup/ copy, a dotfile or a saved comment-threads record — dated by the branch commit
that first wrote it. Captures on one day are one capture. A second day in the same ISO week, or any day
in a week the destination's history already holds a capture of that source, fails. The index is read
from the destination, as the rules are, by devtools/factory/snapshots.py — the reading `factory
requirements status` uses. Captures dated before WEEKLY_FROM are not judged: the week of 2026-11-09 holds
four FSD captures from before this rule was checked, and history is never rewritten.

The session guard refuses these edits as a session makes them, but no list of refused commands covers
every program that can write a file (`python -c`, `tar -x`, `curl -o`, a person's editor). This check
reads `git diff --no-renames --name-status <merge base>..<head>`: a rename is a delete plus an add, so
renaming a protected file away is caught as its deletion. The merge base keeps a branch that merged the
destination from answering for the destination's own changes, and a promotion (dev → qa) — only
commits already merged — passes, since none of them changed a protected file.

The patterns are the guard's own — `read_only`, `read_only_maintained`, `applied` and `never_by_hand`
under `[guard]` in devtools/factory/factory.toml — matched the guard's way (case-insensitive, `*` spans folders). They
are read from the destination branch, so a branch cannot loosen the rules it is judged by. A destination
that does not hold factory.toml yet (qa, uat, main until a promotion reaches them) borrows origin/dev's
copy, where the reviewed rules live; only when neither holds it (the first landing) does the head lend
its own. `read_only` and `applied` must be present and non-empty, or there is nothing to judge.
The output names which copy judged.

    python devtools/checks/check-protected-paths.py [--base origin/dev] [--head HEAD]

A change already in dev's history from before this check existed cannot be removed without rewriting
history; `[guard] accepted_rewrites` in factory.toml (as the base holds it) excuses it by its full commit and
path, so the promotions that carry it pass —
only while the head still holds exactly what that commit wrote, and every other commit in the range that
touched the path is a merge that kept one parent's existing copy.

The base is the pull request's destination in CI (`BITBUCKET_PR_DESTINATION_BRANCH`), else
`origin/dev`. Exits 0 when nothing protected changed, 1 naming each offending path and its rule —
never a file's contents. "Could not check" — an unknown ref, no merge base (a shallow clone), no
readable rules, a git command that failed — always exits 1: an empty answer is not "nothing changed". The
one exception, outside CI, is a new repository whose base names a remote it does not have yet (`origin/dev`
before the first push): no base exists to protect, so it passes and says so.

Python 3.11+, standard library only.
"""
from __future__ import annotations

import datetime
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "devtools" / "factory" / "hooks"))
import guard  # noqa: E402 — the guard's glob semantics, so a pattern means the same thing in both
sys.path.insert(0, str(ROOT / "devtools" / "factory"))
import snapshots  # noqa: E402 — the requirements index and the weekly rule, read as factory reads them
# The settings: factory.toml merged over the Book's defaults (devtools/factory/book_config.py).
_FACTORY = next((p / "devtools" / "factory" for p in Path(__file__).resolve().parents
                 if (p / "devtools" / "factory" / "book_config.py").is_file()), None)
if _FACTORY is not None:
    sys.path.insert(0, str(_FACTORY))
import book_config  # noqa: E402

FACTORY_TOML = "devtools/factory/factory.toml"
def configured_base(root: Path) -> str:
    """`base_ref` in devtools/factory/factory.toml (origin/dev when it names none): the branch work merges into."""
    try:
        return str(book_config.load(root).get("base_ref") or "origin/dev")
    except (OSError, ImportError, ValueError):
        return "origin/dev"


# Where the reviewed rules live when the destination holds none yet: the base branch.
REVIEWED = configured_base(Path(__file__).resolve().parents[2])
NAME = "check-protected-paths"
CHANGES = {"M": "modified", "D": "deleted (or renamed away)", "T": "changed type", "U": "left unmerged"}
# A maintained file is edited in place by /book:check-sources; deleting one is never part of its work.
MAINTAINED_REASON = "a maintained requirements file is edited, never deleted."
# The first day the weekly capture rule is judged: the week of 2026-11-16. The week before holds four FSD
# captures (V10-V13) from before the rule was checked; a promotion still carries them.
WEEKLY_FROM = datetime.date(2026, 11, 16)
DEFAULT_LINKS = "requirements/read-me.txt"

# Changes already in the integration branch's history that broke a rule before this check existed. History is never rewritten
# (CLAUDE.md, Git), so a promotion carries them until every later branch holds them: each is excused by
# its full commit and path — only that commit's change to that path; any other commit touching the file
# is still judged. They are the project's own, `[guard] accepted_rewrites` as the base branch holds it (a
# pull request cannot excuse itself), so this file stays the Book's. A person reviews every entry (factory.toml
# is an AI file).
ACCEPTED: dict[tuple[str, str], str] = {}


class CouldNotCheck(Exception):
    """What the branch changed, or the rules it is held to, is unknown: nothing may pass as clean."""


def git(*args: str, root: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=root, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def must(done: subprocess.CompletedProcess, what: str) -> str:
    if done.returncode != 0:
        raise CouldNotCheck(f"`git {what}` failed ({done.returncode}): {done.stderr.strip()}")
    return done.stdout


def commit_of(ref: str, root: Path) -> str:
    done = git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", root=root)
    if done.returncode != 0 or not done.stdout.strip():
        raise CouldNotCheck(f"{ref} is not a commit here — fetch it before this step")
    return done.stdout.strip()


def rules_at(commit: str, root: Path, ref: str = "") -> dict | None:
    """`[guard]` as `commit` (named `ref`) holds it - its factory.toml over the Book's defaults there, the staged
    index when `commit` is "" - or None when it has no factory.toml."""
    listed = must(git("ls-tree", "--name-only", commit, "--", FACTORY_TOML, root=root), "ls-tree") if commit \
        else must(git("ls-files", "--", FACTORY_TOML, root=root), "ls-files")
    if not listed.strip():
        return None
    try:
        config = book_config.load_at(root, commit) or {}
        table = config["guard"]
    except (book_config.ConfigError, KeyError, TypeError) as error:
        raise CouldNotCheck(f"no readable [guard] in {FACTORY_TOML} ({type(error).__name__})") from error
    if not isinstance(table, dict):
        raise CouldNotCheck(f"no readable [guard] in {FACTORY_TOML} (not a table)")
    rules = {}
    for key in ("read_only", "read_only_maintained", "applied", "never_by_hand"):
        value = table.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise CouldNotCheck(f"[guard] {key} in {FACTORY_TOML} is not a list of patterns")
        rules[key] = tuple(value)
    # An absent or empty read_only would protect nothing and pass everything. `applied` may be empty: a
    # stack-free Book has no migrations (the dotnet pack's factory.toml names them).
    missing = [key for key in ("read_only",) if not rules[key]]
    if missing:
        raise CouldNotCheck(f"[guard] in {ref or commit}:{FACTORY_TOML} has no {'/'.join(missing)}")
    for key, default in (("read_only_reason", "is read-only."),
                         ("applied_reason", "is a migration already on the destination; add a new one.")):
        rules[key] = str(table.get(key, default))
    try:
        links = config.get("requirements", {}).get("sources", {}).get("links_file")
    except AttributeError:
        links = None
    rules["links_file"] = links if isinstance(links, str) and links else DEFAULT_LINKS
    rules["accepted"] = accepted_rewrites(table.get("accepted_rewrites", []), ref or commit)
    return rules


def accepted_rewrites(entries: object, where: str) -> dict[tuple[str, str], str]:
    """`[guard] accepted_rewrites`: {(full commit, path): reason}, each entry named, never guessed."""
    if not isinstance(entries, list):
        raise CouldNotCheck(f"[guard] accepted_rewrites in {where}:{FACTORY_TOML} is not a list")
    found: dict[tuple[str, str], str] = {}
    for entry in entries:
        commit, path, reason = ((entry.get(key) for key in ("commit", "path", "reason"))
                                if isinstance(entry, dict) else (None,) * 3)
        if not (isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit) and isinstance(path, str) and path
                and isinstance(reason, str) and reason.strip()):
            raise CouldNotCheck(f"[guard] accepted_rewrites in {where}:{FACTORY_TOML}: each entry is {{ commit = "
                                f"\"<the full 40-character commit>\", path = \"...\", reason = \"...\" }} ({entry!r})")
        found[(commit, path)] = reason.strip()
    return found


def changed(merge_base: str, head: str, root: Path) -> list[tuple[str, str]]:
    """(status letter, path) for every path the branch changed; renames off, so a rename is D + A."""
    out = must(git("diff", "--no-renames", "--no-ext-diff", "--name-status", "-z", merge_base, head, root=root),
               "diff --name-status")
    fields = out.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 2:
        raise CouldNotCheck("`git diff --name-status` answered in a shape this check does not read")
    return [(fields[i][:1], fields[i + 1]) for i in range(0, len(fields), 2)]


def offences(changes: list[tuple[str, str]], rules: dict) -> list[tuple[str, str]]:
    """(path, the line naming it and its rule) for every protected file the branch changed."""
    found = []
    for status, path in changes:
        if status == "A":
            continue  # a new snapshot, a new backup, a new migration
        how = CHANGES.get(status, f"changed ({status})")
        if guard.matches(path, rules["read_only"]):
            if not guard.matches(path, rules["read_only_maintained"]):
                found.append((path, f"  {path} — {how}: {rules['read_only_reason']}"))
            elif status == "D":
                found.append((path, f"  {path} — {how}: {MAINTAINED_REASON}"))
        # The guard's order: a `never_by_hand` file (the model snapshot) is a tool's output, not an applied
        # migration — every new migration rewrites it.
        if guard.matches(path, rules["applied"]) and not guard.matches(path, rules["never_by_hand"]):
            found.append((path, f"  {path} — {how}: {rules['applied_reason']}"))
    return found


def blob_at(commit: str, path: str, root: Path) -> str:
    """The blob `commit` holds at `path`; "" when it holds none there (deleted, or never had it)."""
    return git("rev-parse", "--verify", "--quiet", f"{commit}:{path}", root=root).stdout.strip()


def excused(path: str, merge_base: str, head: str, accepted: dict[tuple[str, str], str], root: Path) -> bool:
    """An accepted commit changed `path`, the head still holds exactly what it wrote, and every other
    commit in the range that touched `path` is a merge that kept one parent's existing copy.

    Without an accepted commit nothing is excused: a merge of a branch that never held the file (a stale
    branch cut before it, unrelated history) that deletes or renames it in the resolution keeps that
    parent's "nothing" — a missing copy is never one a merge may keep."""
    out = must(git("log", "--full-history", "--no-renames", "--format=%H %P", f"{merge_base}..{head}", "--",
                   path, root=root), "log")
    commits = [line.split() for line in out.splitlines() if line.strip()]
    accepted_blobs = {blob_at(commit, path, root) for commit, *_ in commits if (commit, path) in accepted}
    if not accepted_blobs:
        return False
    head_blob = blob_at(head, path, root)
    if not head_blob or head_blob not in accepted_blobs:
        return False  # the accepted change is not what is there now
    for commit, *parents in commits:
        if (commit, path) in accepted:
            continue
        if len(parents) < 2:
            return False
        kept = {blob_at(parent, path, root) for parent in parents} - {""}
        if blob_at(commit, path, root) not in kept:
            return False
    return True


def snapshot_folders(base_commit: str, links_file: str, root: Path) -> list[str]:
    """The folders under HAS A LIVE LINK in the destination's index; none when it has no index."""
    listed = must(git("ls-tree", "--name-only", base_commit, "--", links_file, root=root), "ls-tree")
    if not listed.strip():
        return []
    text = must(git("show", f"{base_commit}:{links_file}", root=root), f"show {links_file}")
    parent = Path(links_file).parent.as_posix()
    return [f"{parent}/{folder}" if parent not in ("", ".") else folder for folder in snapshots.live_folders(text)]


def second_captures(changes: list[tuple[str, str]], base_commit: str, head_commit: str, folders: list[str],
                    root: Path) -> list[str]:
    """The weekly rule: one line per source the branch captures twice in a week, or in a week already taken."""
    branch: dict[str, dict[datetime.date, list[str]]] = {}
    for status, path in changes:
        folder = next((f for f in folders if status in ("A", "M") and snapshots.is_snapshot(path, f)), None)
        if folder is None:
            continue
        out = must(git("log", "--no-merges", "--reverse", "--format=%cs", f"{base_commit}..{head_commit}", "--",
                       f":(literal){path}", root=root), "log")
        day = snapshots.parse_day(out.splitlines()[0]) if out.strip() else None
        if day is not None:
            branch.setdefault(folder, {}).setdefault(day, []).append(path.rsplit("/", 1)[-1])
    base: dict[str, list[datetime.date]] = {}
    for folder, days in branch.items():
        since = snapshots.monday(min(days)) - datetime.timedelta(days=1)
        out = must(git("log", "--no-merges", "--diff-filter=AM", "--format=@@%cs", "--name-only",
                       f"--since={since.isoformat()}", base_commit, "--", f":(literal){folder}/", root=root), "log")
        day = None
        for line in out.splitlines():
            if line.startswith("@@"):
                day = snapshots.parse_day(line[2:])
            elif line and day and snapshots.is_snapshot(line, folder):
                base.setdefault(folder, []).append(day)
    return snapshots.second_captures(branch, base, WEEKLY_FROM)


def rules_for(base: str, base_commit: str, head: str, head_commit: str, root: Path,
              staged: bool = False) -> tuple[dict, str]:
    """The destination's rules; else the base branch's (`base_ref`, origin/dev by default), where the
    reviewed rules live; else — the first landing only — the head's, or, before the commit that installs the
    Book (`--staged`, in pre-commit), the staged index's (Book D59). Returns the rules and the ref they
    came from."""
    rules = rules_at(base_commit, root, base)
    if rules is not None:
        return rules, base
    reviewed_ref = configured_base(root)
    reviewed = git("rev-parse", "--verify", "--quiet", f"{reviewed_ref}^{{commit}}", root=root)
    if reviewed.returncode == 0 and reviewed.stdout.strip():
        rules = rules_at(reviewed.stdout.strip(), root, reviewed_ref)
        if rules is not None:
            return rules, reviewed_ref
    rules = rules_at(head_commit, root, head)
    if rules is not None:
        return rules, head
    if staged:
        rules = rules_at("", root, "the staged index")
        if rules is not None:
            return rules, "the staged index"
    raise CouldNotCheck(f"neither {base}, {reviewed_ref} nor {head} holds {FACTORY_TOML}, so there are no rules to apply")


def option(args: list[str], name: str, default: str) -> str:
    if name not in args:
        return default
    at = args.index(name) + 1
    if at >= len(args):
        raise CouldNotCheck(f"{name} needs a ref")
    return args[at]


def main(argv: list[str] | None = None, root: Path = ROOT, env: dict[str, str] | None = None,
         accepted: dict[tuple[str, str], str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    destination = env.get("BITBUCKET_PR_DESTINATION_BRANCH", "")
    try:
        base = option(args, "--base", f"origin/{destination}" if destination else REVIEWED)
        head = option(args, "--head", "HEAD")
        remote = base.split("/", 1)[0] if "/" in base else ""
        if remote and not destination and remote not in must(git("remote", root=root), "remote").split():
            # Book D109: before a new repository's first push there is no base, so nothing it holds to protect.
            print(f"{NAME}: nothing to compare — no {remote} remote yet, so {base} cannot exist; "
                  "this check runs from the first commit after the push")
            return 0
        base_commit, head_commit = commit_of(base, root), commit_of(head, root)
        merge_base = git("merge-base", base_commit, head_commit, root=root)
        if merge_base.returncode != 0 or not merge_base.stdout.strip():
            raise CouldNotCheck(f"no merge base between {base} and {head} — fetch both with enough history")
        rules, source = rules_for(base, base_commit, head, head_commit, root, "--staged" in args)
        accepted = {**ACCEPTED, **rules.get("accepted", {})} if accepted is None else accepted
        fork = merge_base.stdout.strip()
        changes = changed(fork, head_commit, root)
        found = [line for path, line in offences(changes, rules)
                 if not excused(path, fork, head_commit, accepted, root)]
        twice = second_captures(changes, base_commit, head_commit,
                                snapshot_folders(base_commit, rules["links_file"], root), root)
    except CouldNotCheck as error:
        print(f"{NAME}: could not check — {error}", file=sys.stderr)
        return 1
    if found:
        print(f"{NAME}: {head} changes {len(found)} protected file(s) that {base} already holds "
              f"(rules: [guard] in {source}:{FACTORY_TOML}). Add a new file instead — never change "
              "a snapshot or an applied migration:", file=sys.stderr)
        print("\n".join(found), file=sys.stderr)
    if twice:
        print(f"{NAME}: {head} takes a second capture of a requirements source in one week — "
              f"{snapshots.WEEKLY_RULE}. Report the move and capture it at the next weekly sweep:", file=sys.stderr)
        print("\n".join(twice), file=sys.stderr)
    if found or twice:
        return 1
    print(f"{NAME}: nothing protected changed between {base} and {head} "
          f"(rules: [guard] in {source}:{FACTORY_TOML}).")
    return 0


if __name__ == "__main__":
    # UTF-8 out, so an em dash prints on a Windows console's code page too (Book D45).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
