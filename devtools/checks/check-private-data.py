#!/usr/bin/env python3
"""
Keep what is personal or secret out of the repository (CLAUDE.md, Private settings).

A person's settings and credentials live in their private settings file outside every repository
(`private_settings` in factory.toml), or in the tool that owns them — git's credential store, rclone's config,
user secrets. This check fails a commit, or a build, that would put one in a tracked file:

- what looks like a credential — the guard's own list (a private key, an AWS, Atlassian, Bitbucket,
  GitHub, Slack, Anthropic or Google token);
- a path inside someone's home folder, which names them and their machine (`~` or `<you>` instead);
- with --staged, what identifies the person committing: their git email and their machine's name.

--commits scans the lines every commit in a range added, not only the final tree: a secret added in one
commit and removed in the next is still in the history a push publishes. A merge counts for the lines it
introduced itself — a conflict resolved by writing new text — not for what it brought from a parent. A
finding already in a shared branch's history cannot be removed without rewriting it, which CLAUDE.md
forbids; once an AI-file owner has reviewed it, it is listed in private-data-accepted.txt by full commit,
file, line and kind, and --commits passes it. Only that exact finding: the same text added by any other
commit is still caught.

Which accepted list: never the branch's own copy, so a pull request cannot accept its own finding.
--commits <base>..<tip> reads it as <base> holds it; --accepted-from <ref> reads it from <ref> instead —
CI names dev, where every change to the list is reviewed, because a promotion's destination (qa, uat,
main) does not hold the list until that promotion brings it. No list there, or a range with no base and
no --accepted-from, accepts nothing. --staged and --tracked consult no list: the accepted findings are
history, and neither mode reads history.

docs/archive/ and requirements/ — written once and never maintained, and the client's own documents — are
scanned for credentials only: a home-folder path or a person's identity there is a question for a person,
a credential is still a build failure. graphify-out/ is generated and never scanned.

It names the file, the line and the kind of thing — never the matched text, which may be the secret.
The guard refuses the same things as a session writes them; this catches what reaches a commit some
other way, and CI catches a clone that never enabled the hook.

Usage (from the repository root):
    python3 devtools/checks/check-private-data.py --staged    # pre-commit: what this commit adds
    python3 devtools/checks/check-private-data.py --tracked   # CI: every tracked file
    python3 devtools/checks/check-private-data.py --commits <base>..HEAD [--accepted-from <ref>]   # CI: what each commit added

Python 3, standard library only.
"""
from __future__ import annotations

import platform
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "devtools" / "factory" / "hooks"))
import guard  # noqa: E402 — the patterns live once, in the guard

# Generated output: never scanned.
NOT_SCANNED = ("graphify-out/",)
# What is written once and never maintained, and the client's own documents: scanned for credentials only.
# A home-folder path or a person's identity there is a question for a person, not a build failure.
CREDENTIALS_ONLY = ("docs/archive/", "requirements/")


# Known findings in shared history, reviewed and accepted (see the file's header). --commits only, read
# from the range's base.
ACCEPTED = Path("devtools") / "checks" / "private-data-accepted.txt"


class GitFailed(Exception):
    """A git command failed: what it would have listed is unknown, so nothing may pass as clean."""


def git(*args: str, root: Path = ROOT, allowed: tuple[int, ...] = (0,)) -> str:
    # quotePath off: a non-ASCII path arrives as itself; a path git still quotes is unquoted below.
    done = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=root, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if done.returncode not in allowed:
        raise GitFailed(f"`git {' '.join(args)}` failed ({done.returncode}): {done.stderr.strip()}")
    return done.stdout


ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def unquote(path: str) -> str:
    """A path as git wrote it — C-quoted (`"b/caf\303\251"`) when it holds a special character."""
    if not (len(path) >= 2 and path.startswith('"') and path.endswith('"')):
        return path
    body, out, i = path[1:-1], bytearray(), 0
    while i < len(body):
        char = body[i]
        if char == "\\" and i + 1 < len(body):
            octal = re.match(r"[0-7]{3}", body[i + 1:i + 4])
            if octal:
                out.append(int(octal.group(0), 8))
                i += 4
                continue
            out.append(ESCAPES.get(body[i + 1], ord(body[i + 1])))
            i += 2
            continue
        out += char.encode("utf-8")
        i += 1
    return out.decode("utf-8", errors="replace")


def identity_patterns(root: Path = ROOT) -> list[tuple[str, re.Pattern]]:
    """What names the person committing: their git email and their machine. Only on a person's machine."""
    found = []
    email = git("config", "user.email", root=root, allowed=(0, 1)).strip()  # 1: not set
    if "@" in email:
        found.append(("the committer's email address", re.compile(re.escape(email), re.IGNORECASE)))
    machine = platform.node().strip()
    if len(machine) >= 4:
        found.append(("this machine's name", re.compile(rf"(?<![\w-]){re.escape(machine)}(?![\w-])", re.IGNORECASE)))
    return found


def kinds(line: str, extra: list[tuple[str, re.Pattern]], credentials_only: bool = False) -> list[str]:
    hits = [label for label, pattern in guard.SECRET_PATTERNS if pattern.search(line)]
    if credentials_only:
        return hits
    if guard.personal_home_path(line):
        hits.append("a path inside someone's home folder")
    return hits + [label for label, pattern in extra if pattern.search(line)]


# Every diff this script reads names its sides a/ and b/, whatever the committer's diff.noprefix or
# diff.mnemonicPrefix say; and is the plain text of each file, never an external or converted view.
DIFF_OPTIONS = ("-U0", "--no-color", "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/")


class UnreadableDiff(GitFailed):
    """The diff was not in the shape this script reads: what it adds is unknown, so nothing may pass."""


def new_path(header: str) -> str | None:
    """The file a `+++ ` header names, or None for a deletion."""
    # git ends a name holding a space with a tab, for patch's sake.
    path = unquote(header[4:].rstrip("\t"))
    if path == "/dev/null":
        return None
    if not path.startswith("b/"):
        raise UnreadableDiff(f"a diff header names no b/ side: {header[:4]}…")
    return path[2:]


def added_lines(diff: str):
    """(commit or None, file, line number, text) for every line a `-U0` diff adds.

    A `commit <sha>` line (from `git log --format`) starts a new commit. A `+++ ` line is a header only
    between `diff --git` (or a merge's `diff --cc`) and the first hunk; inside a hunk it is an added line
    that begins with `++ `. A merge's combined diff has one prefix column per parent (`@@@` for two): a
    line with `+` in every column is one the merge introduced, in no parent; one with `+` in only some
    came from another parent and still moves the line number on; one with `-` anywhere is not in the
    result. An added line with no file known — and the file not deleted — means the diff was misread: fail."""
    sha, current, deleted, in_header, number, columns = None, None, False, False, 0, 1
    for line in diff.splitlines():
        if line.startswith("commit "):  # no header or hunk line of a -U0 diff starts so
            sha, current, deleted, in_header = line.split(" ", 1)[1].strip(), None, False, False
        elif line.startswith(("diff --git ", "diff --cc ", "diff --combined ")):
            current, deleted, in_header = None, False, True
        elif in_header and line.startswith("+++ "):
            current = new_path(line)
            deleted = current is None
        elif line.startswith("@@"):
            in_header = False
            columns = len(line) - len(line.lstrip("@")) - 1
            match = re.search(r"\+(\d+)", line)
            number = int(match.group(1)) if match else 0
        elif not in_header and "+" in line[:columns] and "-" not in line[:columns]:
            if current is None:
                if deleted:
                    continue
                raise UnreadableDiff("an added line arrived before any file header")
            if line[:columns] == "+" * columns:
                yield sha, current, number, line[columns:]
            number += 1


def staged_lines(root: Path = ROOT):
    """(None, file, line number, text) for every line this commit adds."""
    yield from added_lines(git("diff", "--cached", *DIFF_OPTIONS, root=root))


def range_base(revisions: str) -> str | None:
    """The base of `<base>..<tip>` (an empty side is HEAD), or None for anything else."""
    if ".." in revisions and "..." not in revisions:
        return revisions.split("..", 1)[0] or "HEAD"
    return None


def commit_lines(revisions: str, root: Path = ROOT):
    """(commit, file, line number, text) for every line each commit in a range added — for a merge, only
    the lines it introduced itself (`--cc`: in no parent), never what it brought from one.

    A range `<base>..<tip>` whose two ends share no history could not be judged against its base: it fails,
    rather than passing on whatever happened to be listed."""
    base = range_base(revisions)
    if base is not None:
        git("merge-base", base, revisions.split("..", 1)[1] or "HEAD", root=root)
    yield from added_lines(git("log", "-p", "--cc", "--format=commit %H", *DIFF_OPTIONS, revisions,
                               "--", root=root))


def tracked_lines(root: Path = ROOT):
    """(None, file, line number, text) for every line of every tracked text file."""
    # -z: names exactly as stored, never quoted.
    for rel in filter(None, git("ls-files", "-z", root=root).split("\0")):
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue  # gone from the working tree
        if b"\0" in data:
            continue  # binary
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            yield None, rel, number, line


class BadAcceptedList(Exception):
    """The accepted list has a line this script cannot read: what it accepts is unknown, so nothing passes."""


def accepted_text(root: Path = ROOT, base: str | None = None) -> str:
    """The accepted list as `base` holds it, or the working tree's copy when no base is given.

    A base without the file accepts nothing; a base git cannot read fails (GitFailed)."""
    if base is None:
        path = root / ACCEPTED
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    if not git("ls-tree", "--name-only", base, "--", ACCEPTED.as_posix(), root=root).strip():
        return ""
    return git("show", f"{base}:{ACCEPTED.as_posix()}", root=root)


def accepted_findings(root: Path = ROOT, base: str | None = None) -> set[tuple[str, str, int, str]]:
    """(full commit sha, file, line, kind) for every finding the accepted list holds — as `base` holds it,
    or the working tree's copy when no base is given.

    Each line: `<40-hex commit> | <file>:<line> | <kind, as this check prints it> | <YYYY-MM-DD> | <reason>`.
    A line with no reason, no date or a short commit is refused rather than skipped."""
    found = set()
    for number, line in enumerate(accepted_text(root, base).splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = [field.strip() for field in line.split("|")]
        where = fields[1].rpartition(":") if len(fields) == 5 else ("", "", "")
        if (len(fields) != 5 or not re.fullmatch(r"[0-9a-f]{40}", fields[0]) or not where[0]
                or not where[2].isdigit() or not fields[2]
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fields[3]) or not fields[4]):
            raise BadAcceptedList(f"{ACCEPTED.as_posix()}:{number} is not "
                                  "`<full commit sha> | <file>:<line> | <kind> | <YYYY-MM-DD> | <reason>`")
        found.add((fields[0], where[0], int(where[2]), fields[2]))
    return found


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    valid = ((len(argv) == 1 and argv[0] in ("--staged", "--tracked"))
             or (len(argv) == 2 and argv[0] == "--commits")
             or (len(argv) == 4 and argv[0] == "--commits" and argv[2] == "--accepted-from" and argv[3]))
    if not valid:
        print(__doc__.split("Usage", 1)[1].strip().split("\n\n")[0])
        return 2
    mode = argv[0]
    problems, skipped = [], 0
    try:
        extra = identity_patterns(root) if mode == "--staged" else []
        # --commits: never the branch's own list — the one --accepted-from names, else the base's.
        source = (argv[3] if len(argv) == 4 else range_base(argv[1])) if mode == "--commits" else None
        accepted = accepted_findings(root, source) if source is not None else set()
        lines = (staged_lines(root) if mode == "--staged" else
                 commit_lines(argv[1], root) if mode == "--commits" else tracked_lines(root))
        for sha, rel, number, line in lines:
            if rel.startswith(NOT_SCANNED):
                continue
            for kind in kinds(line, extra, credentials_only=rel.startswith(CREDENTIALS_ONLY)):
                if sha and (sha, rel, number, kind) in accepted:
                    skipped += 1
                    continue
                problems.append(f"  {sha[:8] + ' ' if sha else ''}{rel}:{number}: {kind}")
    except GitFailed as failure:
        print(f"check-private-data: could not list what to scan — {failure}")
        return 1
    except BadAcceptedList as failure:
        print(f"check-private-data: could not read the accepted list — {failure}")
        return 1
    note = f" ({skipped} accepted finding(s) in shared history skipped: {ACCEPTED.as_posix()})" if skipped else ""
    if problems:
        print("Personal or secret data would reach the repository (the matched text is not shown):")
        print("\n".join(problems))
        print("Move it to your private settings file or the tool that owns it (CLAUDE.md, Private settings).")
        if mode == "--commits":
            print("A line a commit added stays in the history even when a later commit removes it. Remove it "
                  "in a new commit; never rewrite a shared branch's history. If the commit is already in a "
                  f"shared branch, ask an AI-file owner to add the finding to {ACCEPTED.as_posix()} with a "
                  "reason; a real credential must be rotated as well, since the history still holds it.")
        if note:
            print(note.strip())
        return 1
    where = {"--staged": "in this commit.", "--tracked": "in the tracked files.",
             "--commits": f"added by any commit in {argv[1] if len(argv) > 1 else ''}."}[mode]
    print("No personal or secret data " + where + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
