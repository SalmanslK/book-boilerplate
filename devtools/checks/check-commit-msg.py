#!/usr/bin/env python3
"""
Holds a commit message to CLAUDE.md's rules, from `.githooks/commit-msg`:

- **No AI or tooling attribution, and no trailer lines** (CLAUDE.md, Git): anywhere in the message,
  no `Co-Authored-By:` naming an AI or a bot, no "Generated with" line, no robot emoji — and no git
  trailer (`Signed-off-by:`, `Reviewed-by:`, `Change-Id:`, `X-Foo-Bar:` …), wherever it sits. Prose
  that happens to open with a word and a colon (`Note: this is prose.`, `Self-tests: 32 pass`) is not
  a trailer.
- **A fix names what it fixes** (CLAUDE.md, Features): on a `fix/*` branch the message carries the
  defect or question id, or the Jira key — `D379`, `Q201`, `PROJ-333`. The id pattern is `[checks]
  fix_id` in devtools/factory/factory.toml, plus `[jira] project`'s key when one is set. Merges and
  reverts are exempt.
- **The defect it names exists** (docs/CLAUDE.md, Defects): on a `fix/*` branch every D id the message
  names heads a row, a Fixed entry or an archived heading of docs/defects.md or its archive, as the commit
  holds them — the row is written the day the defect is confirmed, so it is there before its fix.

Everything below git's scissors line (`git commit -v` puts the diff there) is not the message, and
neither are `#` comment lines.

    python devtools/checks/check-commit-msg.py <message-file>

Exits 1 with the reason; git then refuses the commit, for a person and an AI session alike (the guard
refuses `--no-verify`, so the hook cannot be skipped).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

SCISSORS = re.compile(r"^# -+ >8 -+$")
TOOLS = r"(claude|anthropic|copilot|chatgpt|gpt|openai|cursor|gemini|codex|devin|\bai\b|bot\b|\[bot\])"
# A line that credits a tool: a co-author, or a "Generated with …" footer. Prose that mentions one
# mid-sentence ("the commit carried a Co-Authored-By: Claude trailer") is not attribution.
ATTRIBUTION = re.compile(
    rf"^\W*((generated|assisted|written|authored|created|made) (with|by)\b.*{TOOLS}"
    rf"|co-authored-by:.*{TOOLS})"
    r"|🤖|noreply@anthropic\.com", re.I | re.M)
# Trailer keys git and the usual tools write. Matched case-insensitively.
KNOWN_TRAILERS = {
    "co-authored-by", "signed-off-by", "reviewed-by", "acked-by", "tested-by", "reported-by",
    "suggested-by", "helped-by", "requested-by", "cc", "change-id", "fixes", "closes",
    "refs", "see-also", "generated-by", "assisted-by", "on-behalf-of",
}
KEY = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):(\s|$)")
# A key of capitalised hyphenated words (`X-Foo-Bar`, `Change-Id`) is trailer-shaped whatever it says;
# a lower-case one (`Self-tests:`, `best-effort:`) is prose unless an identity follows it.
CAPITALISED_KEY = re.compile(r"^[A-Z][A-Za-z0-9]*(-[A-Z][A-Za-z0-9]*)+$")
HYPHENATED_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9]*(-[A-Za-z][A-Za-z0-9]*)+$")
IDENTITY = re.compile(r"<[^<>\s]+@[^<>\s]+>")
def fix_id_pattern(root: Path | None = None) -> re.Pattern:
    """`[checks] fix_id` (a defect or question id by default) or the `[jira] project` key's issue id."""
    config = {}
    path = (root or Path(__file__).resolve().parents[2]) / "devtools" / "factory" / "factory.toml"
    try:
        import tomllib
        with open(path, "rb") as handle:
            config = tomllib.load(handle)
    except (OSError, ImportError, ValueError):
        config = {}
    ids = str((config.get("checks") or {}).get("fix_id") or r"\b[DQ]\d+\b")
    key = str((config.get("jira") or {}).get("project") or "")
    if key:
        ids = rf"(?:{ids})|\b{re.escape(key)}-\d+\b"
    return re.compile(ids)


FIX_ID = fix_id_pattern()


def branch() -> str:
    out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
    return out.stdout.strip()


def message_lines(message: str) -> list[str]:
    """The message as git will record it: cut at the scissors line, `#` comment lines dropped."""
    lines = []
    for line in message.splitlines():
        if SCISSORS.match(line):
            break
        if not line.startswith("#"):
            lines.append(line)
    return lines


def is_trailer(line: str) -> bool:
    match = KEY.match(line)
    if not match:
        return False
    key, value = match.group(1), line[match.end():]
    if key.lower() in KNOWN_TRAILERS or CAPITALISED_KEY.match(key):
        return True
    return bool(HYPHENATED_KEY.match(key) and IDENTITY.search(value))


LEDGERS = ("docs/defects.md", "docs/archive/defects-closed.md")
NAMED_DEFECT = re.compile(r"\bD(\d+)\b")
# Where a defect id is defined: the head of a ledger row, a Fixed entry, or an archived heading.
DEFINED_DEFECT = re.compile(r"^(?:\|\s*\*{0,2}D(\d+)\*{0,2}\s*\||- \*\*D(\d+)\*\*|#{2,4} D(\d+)\b)", re.M)


def ledger_ids(read) -> set[str] | None:
    """The D ids the ledger defines; `read(path)` gives a file's text or None. None when there is no ledger."""
    texts = [text for text in (read(rel) for rel in LEDGERS) if text is not None]
    if not texts:
        return None
    return {next(group for group in match.groups() if group) for text in texts for match in DEFINED_DEFECT.finditer(text)}


def git_text(revision: str, rel: str) -> str | None:
    """
    A file at a commit ("" for the index), or None when that commit does not hold it. A git that fails —
    a shallow clone without the object, a bad sha — stops the check: read as "no ledger", it would let
    every made-up id through.
    """
    listing = (["ls-tree", "--name-only", revision, "--", rel] if revision
               else ["ls-files", "--cached", "--", rel])
    listed = subprocess.run(["git", *listing], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if listed.returncode != 0:
        raise SystemExit(f"commit-msg: could not check the defect ids — `git {' '.join(listing)}` failed: "
                         f"{listed.stderr.strip()}")
    if not listed.stdout.strip():
        return None
    done = subprocess.run(["git", "show", f"{revision}:{rel}"], capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if done.returncode != 0:
        raise SystemExit(f"commit-msg: could not check the defect ids — `git show {revision}:{rel}` failed: "
                         f"{done.stderr.strip()}")
    return done.stdout


def staged_or_working(rel: str) -> str | None:
    """A ledger as this commit will hold it: staged, else the working copy."""
    staged = git_text("", rel)
    if staged is not None:
        return staged
    path = Path(__file__).resolve().parents[2] / rel
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None


def problems(message: str, current_branch: str, defined: set[str] | None = None) -> list[str]:
    """
    The message's breaches. `defined` is the D ids the ledger holds as the commit has it: on a fix branch
    every D id the message names must be one of them (docs/CLAUDE.md, Defects), so a typo or a row never
    written is found at the commit. None (no ledger) leaves that leg out.
    """
    lines = message_lines(message)
    text = "\n".join(lines).strip()
    found = []
    if ATTRIBUTION.search(text):
        found.append("it carries AI or tooling attribution — commit messages name no tool (CLAUDE.md, Git)")
    trailers = [line for line in lines if is_trailer(line)]
    if trailers:
        keys = ", ".join(sorted({line.split(":", 1)[0] for line in trailers}))
        found.append(f"it carries trailer lines ({keys}) — commit messages carry none (CLAUDE.md, Git)")
    subject = next((line for line in lines if line.strip()), "")
    exempt = subject.startswith(("Merge ", "Revert "))
    if current_branch.startswith("fix/") and not exempt and not FIX_ID.search(text):
        found.append(f"{current_branch} is a fix branch: name the D or Q id, or the Jira key, it fixes "
                     "(CLAUDE.md, Features)")
    if current_branch.startswith("fix/") and not exempt and defined is not None:
        missing = sorted({f"D{number}" for number in NAMED_DEFECT.findall(text)} - {f"D{n}" for n in defined},
                         key=lambda ident: int(ident[1:]))
        if missing:
            found.append(f"it names {', '.join(missing)}, which docs/defects.md and its archive do not hold — write "
                         "the defect's row first, the day it is confirmed (docs/CLAUDE.md, Defects)")
    return found


def commits_to_judge(revisions: str) -> list[str]:
    """
    The non-merge commits in a range that were made with this check in their tree: the hook would have
    run for them. Older ones came before the rule and are history, never rewritten (CLAUDE.md, Git).
    """
    listed = subprocess.run(["git", "rev-list", "--no-merges", revisions], capture_output=True, text=True)
    if listed.returncode != 0:
        raise SystemExit(f"commit-msg: could not list {revisions}: {listed.stderr.strip()}")
    rule = "devtools/checks/check-commit-msg.py"
    return [sha for sha in listed.stdout.split()
            if subprocess.run(["git", "cat-file", "-e", f"{sha}:{rule}"], capture_output=True).returncode == 0]


def main() -> int:
    """
    `check-commit-msg.py <message-file>` — the commit-msg hook. `check-commit-msg.py --commits <range>` —
    CI, over a pull request's commits, so a clone without the hook (or without Python) is still held to it.
    """
    sys.stderr.reconfigure(encoding="utf-8")
    if len(sys.argv) == 3 and sys.argv[1] == "--commits":
        current = os.environ.get("BITBUCKET_BRANCH") or branch()
        failed = 0
        for sha in commits_to_judge(sys.argv[2]):
            message = subprocess.run(["git", "log", "-1", "--format=%B", sha], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace").stdout
            defined = ledger_ids(lambda rel: git_text(sha, rel))
            for problem in problems(message, current, defined):
                failed += 1
                print(f"commit-msg: {sha[:10]} — {problem}", file=sys.stderr)
        return 1 if failed else 0
    if len(sys.argv) != 2:
        print("usage: check-commit-msg.py <message-file> | --commits <range>", file=sys.stderr)
        return 2
    message = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
    found = problems(message, branch(), ledger_ids(staged_or_working))
    for problem in found:
        print(f"commit-msg: refused — {problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
