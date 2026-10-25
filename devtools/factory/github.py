"""
GitHub for the referee: a pull request's head commit and its approvals, read through the `gh` CLI.

`gh` holds the credential (`gh auth login`); this module never reads, prints or stores a token. An approval
counts only when it was given on the pull request's current head commit, by someone who is not the author
and not an excluded identity (GitHub records the commit on every review, so no activity log is needed).
Python 3, standard library only.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REMOTE = re.compile(r"github\.com[:/](?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?$")


class GitHubError(Exception):
    """A GitHub read that could not be completed, with the reason."""


def repository(root: Path) -> str:
    """owner/name — from the origin remote."""
    done = subprocess.run(["git", "remote", "get-url", "origin"], cwd=root, capture_output=True, text=True)
    match = REMOTE.search(done.stdout.strip())
    if not match:
        raise GitHubError("origin is not a github.com repository.")
    return match.group("repo")


def gh_json(args: list[str], cwd: Path | None = None) -> object:
    try:
        done = subprocess.run(["gh", "api", *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as error:
        raise GitHubError("The GitHub CLI (`gh`) is not installed. Install it and run `gh auth login`.") from error
    except subprocess.TimeoutExpired as error:
        raise GitHubError("`gh` did not answer in 60 seconds.") from error
    if done.returncode != 0:
        reason = (done.stderr or done.stdout).strip().splitlines()[:1]
        raise GitHubError(f"gh api {args[-1]} failed: {reason[0] if reason else 'no message'} "
                          "(run `gh auth status` to check the sign-in).")
    try:
        return json.loads(done.stdout or "null")
    except json.JSONDecodeError as error:
        raise GitHubError(f"gh api {args[-1]} did not return JSON.") from error


@dataclass(frozen=True)
class GitHub:
    repo: str
    root: Path | None = None

    @classmethod
    def for_repository(cls, root: Path) -> GitHub:
        return cls(repository(root), root)

    def pull_request(self, number: int) -> dict:
        """`number`, `author`, `branch`, `commit` (the head), `state` — GitHub's own fields, flattened."""
        raw = gh_json([f"repos/{self.repo}/pulls/{number}"], self.root)
        if not isinstance(raw, dict):
            raise GitHubError(f"PR #{number} was not found in {self.repo}.")
        return {"number": raw.get("number", number), "author": (raw.get("user") or {}).get("login", ""),
                "branch": (raw.get("head") or {}).get("ref", ""), "commit": (raw.get("head") or {}).get("sha", ""),
                "state": raw.get("state", "")}

    def reviews(self, number: int) -> list[dict]:
        raw = gh_json(["--paginate", "--slurp", f"repos/{self.repo}/pulls/{number}/reviews"], self.root)
        pages = raw if isinstance(raw, list) else []
        return [review for page in pages for review in (page if isinstance(page, list) else [page])]


def approvers(pull_request: dict, reviews: list[dict], excluded: set[str]) -> list[str]:
    """
    Logins whose latest decisive review is an approval of the PR's current head commit.
    A later CHANGES_REQUESTED or DISMISSED review replaces an approval; a COMMENTED review changes nothing.
    The author and the excluded identities never count.
    """
    commit = pull_request.get("commit", "")
    latest: dict[str, dict] = {}
    for review in sorted(reviews, key=lambda r: r.get("submitted_at") or ""):
        login = (review.get("user") or {}).get("login", "")
        if login and review.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[login] = review
    skip = {name.lower() for name in excluded} | {pull_request.get("author", "").lower()}
    return [login for login, review in latest.items()
            if review.get("state") == "APPROVED" and review.get("commit_id") == commit and login.lower() not in skip]
