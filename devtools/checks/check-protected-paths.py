#!/usr/bin/env python3
"""
What a branch must never change, judged by the files it changed — however it changed them.

Two kinds of "never" rule are about files, not commands:

- **Read-only paths** (`[guard] read_only`, the requirements snapshots by default): a file already on
  the base branch is never modified, deleted or renamed. A new snapshot is added as a new file. The
  few files kept up to date in place (`read_only_maintained`, the read-me files) may be edited, but
  never deleted or renamed away.
- **Applied files** (`[guard] applied`, such as database migrations already on the base branch): never
  modified, deleted or renamed. Add a new one. A file a tool rewrites with every new one
  (`never_by_hand`, such as a migration snapshot) is not judged as applied.

The session guard refuses these edits as a session makes them, but no list of refused commands covers
every program that can write a file (`python -c`, `tar -x`, `curl -o`, a person's editor). This check
reads `git diff --no-renames --name-status <merge base> <head>`: a rename is a delete plus an add, so
renaming a protected file away is caught as its deletion. The merge base keeps a branch that merged the
base from answering for the base's own changes, and a promotion (only commits already merged) passes.

The patterns are the guard's own, under `[guard]` in devtools/factory/factory.toml, matched the guard's
way (case-insensitive, `*` spans folders). They are read from the base branch, so a branch cannot loosen
the rules it is judged by; only when the base does not hold factory.toml yet (the first landing) does the
head lend its own. `read_only` or `applied` must name something, or there is nothing to judge.

    python devtools/checks/check-protected-paths.py [--base <ref>] [--head HEAD]
    python devtools/checks/check-protected-paths.py --staged      # pre-commit: what this commit stages

The base is the pull request's destination in CI (`BITBUCKET_PR_DESTINATION_BRANCH` or
`GITHUB_BASE_REF`), else `base_ref` in factory.toml. With --staged the head is the index: what the
commit about to be made holds. The very first commit of a repository has nothing before it to protect,
and passes; a base not fetched yet is replaced by HEAD, which is stricter, never looser.

A change already in the base's history from before this check existed cannot be removed without
rewriting history; ACCEPTED, below, excuses it by its full commit and path — only while the head still
holds exactly what that commit wrote, and every other commit in the range that touched the path is a
merge that kept one parent's existing copy.

Exits 0 when nothing protected changed, 1 naming each offending path and its rule — never a file's
contents. "Could not check" — an unknown ref, no merge base (a shallow clone), no readable rules, a git
command that failed — always exits 1: an empty answer is not "nothing changed".

Python 3.11+, standard library only.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "devtools" / "factory" / "hooks"))
import guard  # noqa: E402 — the guard's glob semantics, so a pattern means the same thing in both

FACTORY_TOML = "devtools/factory/factory.toml"
NAME = "check-protected-paths"
INDEX = "the staged commit"
CHANGES = {"M": "modified", "D": "deleted (or renamed away)", "T": "changed type", "U": "left unmerged"}
# A maintained file is edited in place; deleting one is never part of keeping it current.
MAINTAINED_REASON = "a maintained read-only file is edited, never deleted."

# Changes already in the base's history that broke a rule before this check existed. History is never
# rewritten, so each is excused by its full commit and path — only that commit's change to that path;
# any other commit touching the file is still judged. A person reviews every entry. For example:
#   ("<full commit sha>", "requirements/fsd/Shop - V2.pdf"): "replaced in place before the rule; reviewed",
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


def config_text(commit: str, root: Path) -> str | None:
    """factory.toml as `commit` holds it ("" is the index); None when it holds none."""
    if commit == "":
        done = git("show", f":{FACTORY_TOML}", root=root)
        return done.stdout if done.returncode == 0 else None
    listed = must(git("ls-tree", "--name-only", commit, "--", FACTORY_TOML, root=root), "ls-tree")
    if not listed.strip():
        return None
    return must(git("show", f"{commit}:{FACTORY_TOML}", root=root), f"show {FACTORY_TOML}")


def rules_from(text: str, ref: str) -> dict:
    """`[guard]`'s patterns and reasons from factory.toml's text, as `ref` holds it."""
    try:
        table = tomllib.loads(text)["guard"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        raise CouldNotCheck(f"no readable [guard] in {ref}:{FACTORY_TOML} ({type(error).__name__})") from error
    if not isinstance(table, dict):
        raise CouldNotCheck(f"no readable [guard] in {ref}:{FACTORY_TOML} (not a table)")
    rules = {}
    for key in ("read_only", "read_only_maintained", "applied", "never_by_hand"):
        value = table.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise CouldNotCheck(f"[guard] {key} in {FACTORY_TOML} is not a list of patterns")
        rules[key] = tuple(value)
    # Both lists empty would protect nothing and pass everything: a misconfiguration, not a clean branch.
    if not rules["read_only"] and not rules["applied"]:
        raise CouldNotCheck(f"[guard] in {ref}:{FACTORY_TOML} has neither read_only nor applied")
    for key, default in (("read_only_reason", "is read-only."),
                         ("applied_reason", "is already on the base branch; add a new one instead.")):
        rules[key] = str(table.get(key, default))
    return rules


def changed(merge_base: str, head: str, root: Path) -> list[tuple[str, str]]:
    """(status letter, path) for every path the branch changed ("" head: the index); renames off."""
    target = ["--cached", merge_base] if head == "" else [merge_base, head]
    out = must(git("diff", "--no-renames", "--no-ext-diff", "--name-status", "-z", *target, root=root),
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
            continue  # a new snapshot, a new migration
        how = CHANGES.get(status, f"changed ({status})")
        if guard.matches(path, rules["read_only"]):
            if not guard.matches(path, rules["read_only_maintained"]):
                found.append((path, f"  {path} — {how}: {rules['read_only_reason']}"))
            elif status == "D":
                found.append((path, f"  {path} — {how}: {MAINTAINED_REASON}"))
        # The guard's order: a `never_by_hand` file is a tool's output, not an applied file.
        if guard.matches(path, rules["applied"]) and not guard.matches(path, rules["never_by_hand"]):
            found.append((path, f"  {path} — {how}: {rules['applied_reason']}"))
    return found


def blob_at(commit: str, path: str, root: Path) -> str:
    """The blob `commit` ("" the index) holds at `path`; "" when it holds none there."""
    return git("rev-parse", "--verify", "--quiet", f"{commit}:{path}", root=root).stdout.strip()


def excused(path: str, merge_base: str, head: str, accepted: dict[tuple[str, str], str], root: Path) -> bool:
    """An accepted commit changed `path`, the head still holds exactly what it wrote, and every other
    commit in the range that touched `path` is a merge that kept one parent's existing copy."""
    if not accepted:
        return False
    tip = head or "HEAD"
    out = must(git("log", "--full-history", "--no-renames", "--format=%H %P", f"{merge_base}..{tip}", "--",
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


def rules_for(base: str, base_commit: str, head: str, head_commit: str, root: Path) -> tuple[dict, str]:
    """The base's rules; else — the first landing only — the head's. Returns the rules and their ref."""
    for commit, ref in ((base_commit, base), (head_commit, head)):
        text = config_text(commit, root)
        if text is not None:
            return rules_from(text, ref), ref
    raise CouldNotCheck(f"neither {base} nor {head} holds {FACTORY_TOML}, so there are no rules to apply")


def default_base(env, root: Path) -> str:
    """The pull request's destination in CI, else factory.toml's base_ref."""
    destination = env.get("BITBUCKET_PR_DESTINATION_BRANCH") or env.get("GITHUB_BASE_REF") or ""
    if destination:
        return f"origin/{destination}"
    try:
        base = tomllib.loads((root / FACTORY_TOML).read_text(encoding="utf-8")).get("base_ref", "")
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise CouldNotCheck(f"no readable {FACTORY_TOML} to name the base ({type(error).__name__})") from error
    if not base:
        raise CouldNotCheck(f"{FACTORY_TOML} has no base_ref; name one with --base")
    return base


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
    accepted = ACCEPTED if accepted is None else accepted
    staged = "--staged" in args
    try:
        if staged and git("rev-parse", "--verify", "--quiet", "HEAD", root=root).returncode != 0:
            print(f"{NAME}: the first commit has nothing before it to protect.")
            return 0
        base = option(args, "--base", "") or default_base(env, root)
        head = INDEX if staged else option(args, "--head", "HEAD")
        if staged and git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", root=root).returncode != 0:
            # Not fetched yet: judged against the last commit instead, which is stricter, never looser.
            print(f"{NAME}: {base} is not here; judging the staged commit against HEAD.")
            base = "HEAD"
        base_commit = commit_of(base, root)
        head_commit = "" if staged else commit_of(head, root)
        merge_base = git("merge-base", base_commit, head_commit or "HEAD", root=root)
        if merge_base.returncode != 0 or not merge_base.stdout.strip():
            raise CouldNotCheck(f"no merge base between {base} and {head} — fetch both with enough history")
        rules, source = rules_for(base, base_commit, head, head_commit, root)
        fork = merge_base.stdout.strip()
        found = [line for path, line in offences(changed(fork, head_commit, root), rules)
                 if not excused(path, fork, head_commit, accepted, root)]
    except CouldNotCheck as error:
        print(f"{NAME}: could not check — {error}", file=sys.stderr)
        return 1
    if found:
        print(f"{NAME}: {head} changes {len(found)} protected file(s) that {base} already holds "
              f"(rules: [guard] in {source}:{FACTORY_TOML}). Add a new file instead — never change "
              "a snapshot or an applied file:", file=sys.stderr)
        print("\n".join(found), file=sys.stderr)
        return 1
    print(f"{NAME}: nothing protected changed between {base} and {head} "
          f"(rules: [guard] in {source}:{FACTORY_TOML}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
