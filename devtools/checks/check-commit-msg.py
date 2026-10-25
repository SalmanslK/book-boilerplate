#!/usr/bin/env python3
"""
Holds a commit message to CLAUDE.md's rules, from `.githooks/commit-msg`:

- **No AI or tooling attribution, and no trailer lines** (CLAUDE.md, Git): anywhere in the message,
  no `Co-Authored-By:` naming an AI or a bot, no "Generated with" line, no robot emoji — and no git
  trailer (`Signed-off-by:`, `Reviewed-by:`, `Change-Id:`, `X-Foo-Bar:` …), wherever it sits. Prose
  that happens to open with a word and a colon (`Note: this is prose.`, `Self-tests: 32 pass`) is not
  a trailer.
- **A fix names what it fixes** (CLAUDE.md, Features): on a fix branch the message carries the defect
  or question id, or the tracker key — `D4`, `Q3`, `SHOP-12`. Merges and reverts are exempt.

The fix branch is the prefix of `[backlog.branches] defect` in devtools/factory/factory.toml (`fix/`
unless it says otherwise); what counts as an id is `[checks] fix_id`, a regular expression, when the
project sets one. Everything below git's scissors line (`git commit -v` puts the diff there) is not the
message, and neither are `#` comment lines.

    python devtools/checks/check-commit-msg.py <message-file>
    python devtools/checks/check-commit-msg.py --commits <range>    # CI: every commit in a pull request

Exits 1 with the reason; git then refuses the commit, for a person and an AI session alike (the guard
refuses `--no-verify`, so the hook cannot be skipped). Python 3.11+, standard library only.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FACTORY_TOML = ROOT / "devtools" / "factory" / "factory.toml"
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
# A defect (`D4`), a question (`Q3`) or a tracker key (`SHOP-12`), unless [checks] fix_id says otherwise.
DEFAULT_FIX_ID = r"\b([DQ]\d+|[A-Z][A-Z0-9]+-\d+)\b"
DEFAULT_FIX_BRANCH = "fix/"


def settings(config: Path = FACTORY_TOML) -> tuple[str, re.Pattern]:
    """The fix branch's prefix and the id pattern, from factory.toml; the defaults without one."""
    try:
        table = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        table = {}
    pattern = ((table.get("backlog") or {}).get("branches") or {}).get("defect", "")
    prefix = pattern.split("{", 1)[0] if "{" in pattern else ""
    fix_id = (table.get("checks") or {}).get("fix_id") or DEFAULT_FIX_ID
    return prefix or DEFAULT_FIX_BRANCH, re.compile(fix_id)


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


def problems(message: str, current_branch: str, fix_branch: str = DEFAULT_FIX_BRANCH,
             fix_id: re.Pattern = re.compile(DEFAULT_FIX_ID)) -> list[str]:
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
    if current_branch.startswith(fix_branch) and not exempt and not fix_id.search(text):
        found.append(f"{current_branch} is a fix branch: name the defect or question id, or the tracker key, "
                     "it fixes (CLAUDE.md, Features)")
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
    fix_branch, fix_id = settings()
    if len(sys.argv) == 3 and sys.argv[1] == "--commits":
        # CI checks out a commit, not a branch: the branch name comes from the CI's own variable.
        current = os.environ.get("BITBUCKET_BRANCH") or os.environ.get("GITHUB_HEAD_REF") or branch()
        failed = 0
        for sha in commits_to_judge(sys.argv[2]):
            message = subprocess.run(["git", "log", "-1", "--format=%B", sha], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace").stdout
            for problem in problems(message, current, fix_branch, fix_id):
                failed += 1
                print(f"commit-msg: {sha[:10]} — {problem}", file=sys.stderr)
        return 1 if failed else 0
    if len(sys.argv) != 2:
        print("usage: check-commit-msg.py <message-file> | --commits <range>", file=sys.stderr)
        return 2
    message = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
    found = problems(message, branch(), fix_branch, fix_id)
    for problem in found:
        print(f"commit-msg: refused — {problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
