"""
Bitbucket Cloud for the referee: a pull request's approvals, description and
activity, and the repository's branch restrictions — and two writes, opening a
pull request (`factory open-pr`, `factory pr open`) and posting a comment
(`factory pr comment`), as the person whose token it is.

The token comes from BITBUCKET_TOKEN (CI's secured variable) or, on a laptop,
from git's credential store — the person's own, asked with GCM_INTERACTIVE=never,
or Git Credential Manager blocks on a window. An action in a person's name (the
two writes, `pr whoami`) uses only their stored credential, never the variable.
It is passed to curl on stdin and never printed, logged or put on a command line.

The network to api.bitbucket.org drops requests on the office connection, so
every GET is retried on transient errors — harmless, since a read changes nothing. A POST
is retried only when it never left (could not resolve, could not connect): once it may have
reached the server, a retry could open a second pull request or post a second comment.

Python 3.11+, standard library only; curl when there is one (Git for Windows
ships one that works where Windows' own and Python's urllib do not).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

API = "https://api.bitbucket.org/2.0"
API_HOST = urllib.parse.urlsplit(API).hostname
ABSOLUTE_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
ATTEMPTS = 5
# Transient curl exits a GET is retried on: resolve, connect, timeout, TLS, empty reply, recv.
TRANSIENT_CURL_EXITS = frozenset({6, 7, 28, 35, 52, 56})
REMOTE = re.compile(r"bitbucket\.org[:/](?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?$")

Fetch = Callable[[str, str], tuple[int, str]]
Send = Callable[[str, str, str], tuple[int, str]]
# A POST is retried only when it never left — could not resolve, could not connect, or TLS failed
# before any request went out (35 handshake, 58 client certificate, 59 cipher, 60 server certificate
# not trusted, 77 CA certificates unreadable). After that it may have landed, and a second one would
# open a second pull request.
NEVER_SENT_CURL_EXITS = frozenset({6, 7, 35, 58, 59, 60, 77})
# What a curl exit means, for the message when one is not worth retrying (curl's man page, EXIT CODES).
CURL_EXIT_MEANINGS = {
    1: "unsupported protocol", 2: "curl could not start", 3: "malformed URL", 5: "could not resolve the proxy",
    6: "could not resolve the host", 7: "could not connect", 22: "HTTP error", 23: "could not write the answer",
    26: "could not read the request", 27: "out of memory", 28: "timed out", 35: "TLS handshake failed",
    47: "too many redirects", 52: "empty reply", 55: "could not send", 56: "could not receive",
    58: "client certificate problem", 59: "no usable TLS cipher", 60: "the server's certificate is not trusted",
    77: "could not read the CA certificates", 92: "HTTP/2 stream error",
}


def curl_failure(exit_code: int) -> str:
    """`curl exited 60 (the server's certificate is not trusted)` — never the URL's query or a header."""
    meaning = CURL_EXIT_MEANINGS.get(exit_code)
    return f"curl exited {exit_code}" + (f" ({meaning})" if meaning else "")


class BitbucketError(Exception):
    """A Bitbucket read or write that could not be completed, with the reason."""


# A home folder in something a tool said — rclone's `Config file "<home>\...\rclone.conf" not found` —
# never reaches a message: it names the person (CLAUDE.md "Private settings").
HOME_FOLDERS = (re.compile(r"(?i)(?<![\w.])/[a-z]/Users/[^/\\\s\"'`]+"),       # Git Bash: /c/Users/<name>
                re.compile(r"(?i)(?<![\w.])[a-z]:[\\/]+Users[\\/]+[^/\\\s\"'`]+"),  # C:\Users\<name>
                re.compile(r"(?<![\w.])/(?:home|Users)/[^/\\\s\"'`]+"))           # /home/<name>, /Users/<name>


def without_home(text: str) -> str:
    """`text` with the user's home folder, and any other person's, written as `~`."""
    home = os.path.expanduser("~")
    if home and home != "~":
        for spelling in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
            text = re.sub(re.escape(spelling), "~", text, flags=re.IGNORECASE)
    for pattern in HOME_FOLDERS:
        text = pattern.sub("~", text)
    return text


class CurlFailure(Exception):
    """
    Every curl was asked and none answered: each one either failed for good (`refused`, its exit code)
    or ran out of attempts. A message naming each curl and what its exit means; never a header.
    """

    def __init__(self, attempts: int, refused: dict[str, int], curls: int):
        self.attempts, self.refused, self.curls = attempts, refused, curls
        each = "; ".join(f"{without_home(curl)}: {curl_failure(code)}" for curl, code in refused.items())
        if len(refused) == curls:
            message = f"every curl failed for good — {each}"
        else:
            message = f"no answer after {attempts} attempts" + (f" ({each}; the rest were transient)" if each else "")
        super().__init__(message)


def get_through_curls(curls: list[str], url: str, config: str, attempts: int) -> tuple[int, str]:
    """
    (status, body) from the first curl that answers; `config` — the headers, token included — on stdin.
    A transient exit is retried, `attempts` times per curl, alternating. A permanent one (a certificate
    the office proxy re-signs, say) is final for that curl only: another curl, with its own TLS stack,
    may get through, so it is the next one's turn. CurlFailure when none did.
    """
    refused: dict[str, int] = {}
    made = 0
    for _ in range(attempts):
        for curl in curls:
            if curl in refused:
                continue
            made += 1
            try:
                done = subprocess.run([curl, "-s", "--max-time", "45", "-K", "-", "-w", "\n%{http_code}", url],
                                      input=config, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=60)
            except subprocess.TimeoutExpired:
                time.sleep(made)  # a hung connection is as transient as a dropped one
                continue
            body, _, code = done.stdout.rpartition("\n")
            if done.returncode == 0 and code.isdigit() and code != "000":
                return int(code), body
            if done.returncode != 0 and done.returncode not in TRANSIENT_CURL_EXITS:
                refused[curl] = done.returncode
                continue
            time.sleep(made)
        if len(refused) == len(curls):
            break
    raise CurlFailure(made, refused, len(curls))


# ---------------------------------------------------------------- transport

def curl_binaries() -> list[str]:
    """
    Every curl worth trying. On the office network which one gets through changes from
    day to day — Git for Windows' one day, Windows' own the next — so a read that fails
    to connect moves on to the next rather than failing.
    """
    found: list[str] = []
    if os.name == "nt":
        git_exe = shutil.which("git")
        if git_exe:
            found.append(str(Path(git_exe).resolve().parent.parent / "mingw64" / "bin" / "curl.exe"))
        found.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe"))
    on_path = shutil.which("curl")
    if on_path:
        found.append(on_path)
    unique: list[str] = []
    for candidate in found:
        if os.path.exists(candidate) and os.path.normcase(candidate) not in map(os.path.normcase, unique):
            unique.append(candidate)
    return unique


def fetch_with_curl(url: str, token: str) -> tuple[int, str]:
    curls = curl_binaries()
    if not curls:
        return fetch_with_urllib(url, token)
    config = f'header = "Authorization: Bearer {token}"\nheader = "Accept: application/json"\n'
    try:
        return get_through_curls(curls, url, config, ATTEMPTS)
    except CurlFailure as failure:
        raise BitbucketError(f"api.bitbucket.org did not answer {url}: {failure} "
                             f"(up to {ATTEMPTS} with each of {len(curls)} curl{'s' if len(curls) != 1 else ''})") from None


def fetch_with_urllib(url: str, token: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(1 + attempt)
    raise BitbucketError(f"api.bitbucket.org did not answer {url} after {ATTEMPTS} attempts")


def send_with_curl(url: str, token: str, body: str) -> tuple[int, str]:
    """
    POST a JSON body. The token goes on stdin as before; the body through a temporary file,
    since stdin is taken. Never retried once it may have reached the server.
    """
    curls = curl_binaries()
    if not curls:
        return send_with_urllib(url, token, body)
    config = (f'header = "Authorization: Bearer {token}"\nheader = "Accept: application/json"\n'
              'header = "Content-Type: application/json"\n')
    with tempfile.TemporaryDirectory() as folder:
        payload = Path(folder) / "body.json"
        payload.write_text(body, encoding="utf-8")
        for curl in curls:
            try:
                done = subprocess.run([curl, "-s", "--max-time", "60", "-X", "POST", "-K", "-",
                                       "--data-binary", f"@{payload}", "-w", "\n%{http_code}", url],
                                      input=config, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=90)
            except subprocess.TimeoutExpired as error:
                raise BitbucketError(f"No answer to the POST to {url}; it may have been created — "
                                     "look before trying again.") from error
            body_out, _, code = done.stdout.rpartition("\n")
            if done.returncode == 0 and code.isdigit() and code != "000":
                return int(code), body_out
            if done.returncode not in NEVER_SENT_CURL_EXITS:
                raise BitbucketError(f"curl exited {done.returncode} on the POST to {url}; it may have been "
                                     "created — look before trying again.")
    raise BitbucketError(f"Could not reach api.bitbucket.org to POST to {url}; nothing was sent.")


def send_with_urllib(url: str, token: str, body: str) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body.encode("utf-8"), method="POST", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, ConnectionError, TimeoutError) as error:
        raise BitbucketError(f"The POST to {url} failed ({type(error).__name__}); it may have been created — "
                             "look before trying again.") from error


def repository(root: Path) -> str:
    """workspace/repo — from Pipelines' variable, else from the origin remote."""
    name = os.environ.get("BITBUCKET_REPO_FULL_NAME")
    if name:
        return name
    done = subprocess.run(["git", "remote", "get-url", "origin"], cwd=root, capture_output=True, text=True)
    match = REMOTE.search(done.stdout.strip())
    if not match:
        raise BitbucketError("origin is not a bitbucket.org repository; set BITBUCKET_REPO_FULL_NAME.")
    return match.group("repo")


def token(root: Path, repo: str, personal: bool = False) -> str:
    """
    BITBUCKET_TOKEN, else git's credential store. Never printed. `personal` skips the variable:
    an action taken in a person's name (`open-pr`) uses only their own stored credential — a
    repository access token acts as a bot user, and a PR it opens would not be theirs.
    """
    from_env = None if personal else os.environ.get("BITBUCKET_TOKEN")
    if from_env:
        return from_env
    env = {**os.environ, "GCM_INTERACTIVE": "never", "GIT_TERMINAL_PROMPT": "0"}
    for _ in range(3):
        try:
            done = subprocess.run(["git", "credential", "fill"], cwd=root, env=env, capture_output=True, text=True,
                                  input=f"protocol=https\nhost=bitbucket.org\npath={repo}.git\n\n", timeout=30)
        except subprocess.TimeoutExpired:
            continue  # a credential helper waiting on a prompt that cannot show
        found = next((line.split("=", 1)[1] for line in done.stdout.splitlines() if line.startswith("password=")), "")
        if found:
            return found
    raise BitbucketError("No Bitbucket token: set BITBUCKET_TOKEN, or store a credential for bitbucket.org in git.")


@dataclass(frozen=True)
class Bitbucket:
    repo: str
    token: str = field(repr=False)  # never in a traceback, a debug print or a log line
    fetch: Fetch = fetch_with_curl
    send: Send = send_with_curl

    @classmethod
    def for_repository(cls, root: Path, personal: bool = False) -> Bitbucket:
        repo = repository(root)
        return cls(repo, token(root, repo, personal))

    def current_user(self) -> dict:
        """Whose credential this is: `type` is "user" for a person, otherwise an app or bot account."""
        return self.get(f"{API}/user")

    def get(self, path: str) -> dict:
        """
        A path under the repository, or a whole URL — a `next` link from an answer. The token goes
        only to the API's own host over https: a link elsewhere is refused before anything is sent.
        """
        url = path if ABSOLUTE_URL.match(path) else f"{API}/repositories/{self.repo}/{path}"
        try:
            parts = urllib.parse.urlsplit(url)
            where = (parts.scheme, parts.hostname, parts.port, parts.username)
        except ValueError:  # a port that is not a number
            where = None
        if where != ("https", API_HOST, None, None):
            raise BitbucketError(f"Refused to send the Bitbucket token to {url.split('?')[0]}: "
                                 f"it goes only to https://{API_HOST}.")
        status, body = self.fetch(url, self.token)
        if status != 200:
            raise BitbucketError(f"Bitbucket answered {status} for {url.split('?')[0]}"
                                 + (" — the token cannot see this repository, or the id is wrong" if status in (403, 404) else ""))
        try:
            return json.loads(body)
        except ValueError as error:
            raise BitbucketError(f"Bitbucket answered 200 for {url} with a body that is not JSON") from error

    def paged(self, path: str) -> list[dict]:
        values: list[dict] = []
        page: str | None = path
        while page:
            data = self.get(page)
            values += data.get("values", [])
            page = data.get("next")
        return values

    def pull_request(self, number: int) -> dict:
        return self.get(f"pullrequests/{number}")

    def branch_restrictions(self) -> list[dict]:
        return self.paged("branch-restrictions?pagelen=100")

    def activity(self, number: int) -> list[dict]:
        return self.paged(f"pullrequests/{number}/activity?pagelen=50")

    def open_pull_requests_from(self, branch: str) -> list[dict]:
        query = urllib.parse.quote(f'source.branch.name="{branch}" AND state="OPEN"')
        return self.paged(f"pullrequests?q={query}&pagelen=50")

    def create_pull_request(self, title: str, description: str, source: str, destination: str) -> dict:
        """Opens a pull request as whoever owns the token — the person running the tool."""
        url = f"{API}/repositories/{self.repo}/pullrequests"
        body = json.dumps({"title": title, "description": description, "close_source_branch": False,
                           "source": {"branch": {"name": source}},
                           "destination": {"branch": {"name": destination}}})
        status, answer = self.send(url, self.token, body)
        if status not in (200, 201):
            raise BitbucketError(f"Bitbucket answered {status} opening the pull request: {answer[:300]}")
        return json.loads(answer)

    def comments(self, number: int) -> list[dict]:
        return self.paged(f"pullrequests/{number}/comments?pagelen=100")

    def post_comment(self, number: int, raw: str, path: str = "", line: int = 0) -> dict:
        """One comment, inline on `path` at `line` of the new file when a path is given."""
        url = f"{API}/repositories/{self.repo}/pullrequests/{number}/comments"
        payload: dict = {"content": {"raw": raw}}
        if path:
            payload["inline"] = {"path": path, "to": line}
        status, answer = self.send(url, self.token, json.dumps(payload))
        if status not in (200, 201):
            raise BitbucketError(f"Bitbucket answered {status} posting the comment: {answer[:300]}")
        return json.loads(answer)


def commit_arrived(activity: list[dict], commit: str) -> str:
    """
    When the PR first showed `commit`, by Bitbucket's clock — its activity's `update` entries
    carry the source commit and the time. A commit's own date is whatever its author set
    (the re-review's #8), so it cannot say when an approval came after the code. "" if unseen.
    """
    times = [entry["update"].get("date", "") for entry in activity
             if "update" in entry and commit and
             ((entry["update"].get("source") or {}).get("commit") or {}).get("hash", "x").startswith(commit[:12])]
    return min((t for t in times if t), default="")


# ---------------------------------------------------------------- what the referee asks of them

def approvers(pull_request: dict, excluded: set[str], since: str = "") -> list[str]:
    """
    Who approved, apart from the author and any excluded identity (the factory's own) — and, given
    `since` (an ISO time), only an approval made after it: one from before the commit approved older code.
    """
    author = (pull_request.get("author") or {}).get("display_name", "")
    cutoff = parse_time(since)
    if since and cutoff is None:
        # A time that does not parse must not drop the "after the commit" condition (the harness review).
        raise BitbucketError(f"Could not read the time {since!r}, so no approval can be dated after it.")
    names = []
    for participant in pull_request.get("participants", []):
        if not participant.get("approved"):
            continue
        when = parse_time(participant.get("participated_on") or "")
        if cutoff and (when is None or when < cutoff):
            continue
        user = participant.get("user") or {}
        identities = {user.get("display_name"), user.get("nickname"), user.get("account_id"), user.get("uuid")}
        if user.get("display_name") == author or identities & excluded:
            continue
        names.append(user.get("display_name") or user.get("nickname") or "unknown")
    return names


def parse_time(text: str) -> datetime | None:
    """Bitbucket's and git's ISO times, both with an offset; None when absent or unreadable."""
    try:
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00")) if text.strip() else None
    except ValueError:
        return None


def protection_gaps(rules: list[dict], wanted: dict) -> tuple[dict[str, list[str]], list[str]]:
    """What each branch lacks against the decided rules, and exemptions worth a person's look."""
    # Every rule of a kind, not the last one read: a second `push` rule's exemptions, or a weaker
    # second minimum, would otherwise go unreported.
    live: dict[str, dict[str, list[dict]]] = {}
    for rule in rules:
        branch = rule.get("pattern") or rule.get("branch_type") or ""
        live.setdefault(branch, {}).setdefault(rule["kind"], []).append(rule)
    missing: dict[str, list[str]] = {}
    exemptions: list[str] = []
    for branch in wanted["branches"]:
        have = live.get(branch, {})
        lacks = [kind for kind in wanted["kinds"] if kind not in have]
        for kind, minimum in wanted.get("minimums", {}).items():
            if kind not in have:
                lacks.append(f"{kind} >= {minimum}")
                continue
            value = min((rule.get("value") or 0) for rule in have[kind])
            if value < minimum:
                lacks.append(f"{kind} >= {minimum} (is {value})")
        if lacks:
            missing[branch] = lacks
        for kind in ("push", "restrict_merges"):
            for rule in have.get(kind, []):
                who = [u.get("display_name", "?") for u in rule.get("users", [])] + \
                      [g.get("name", "?") for g in rule.get("groups", [])]
                if who:
                    exemptions.append(f"{branch}: {kind} allows {', '.join(who)}")
    return missing, exemptions


DESCRIPTION_SECTIONS = {
    "summary": re.compile(r"(?im)^\s*(#+\s*|\*\*)summary\b"),
    "test plan": re.compile(r"(?im)^\s*(#+\s*|\*\*)test plan\b"),
}
DEV_TESTING = re.compile(r"(?im)^\s*(#+\s*|\*\*)dev testing\b")
JIRA_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
LOCAL_CI = re.compile(r"(?im)^\s*(#+\s*|\*\*)local ci\b")
NEXT_HEADING = re.compile(r"(?m)^\s*(#+\s|\*\*\w)")
# The line ci-local.sh prints, PASS on `<12-character sha>` (...); nothing looser counts.
PASS_LINE = re.compile(r"(?m)^PASS on `(?P<sha>[0-9a-f]{12,40})`")


def local_ci_problems(text: str, head: str) -> list[str]:
    """
    While CI is switched off: the description's "Local CI" section is a PASS from the local CI run for the pull
    request's head commit — not an earlier one, since a later push is what it never saw.
    """
    opening = LOCAL_CI.search(text)
    if not opening:
        return ["no 'Local CI' section — run `sh devtools/checks/ci-local.sh` and paste what it prints"]
    rest = text[opening.end():]
    following = NEXT_HEADING.search(rest)
    section = rest[:following.start()] if following else rest
    head = head.lower()
    named = [sha for sha in PASS_LINE.findall(section) if head and (sha.startswith(head) or head.startswith(sha))]
    if not named:
        return [f"the 'Local CI' section is not a PASS for the head commit {head[:12] or '(unknown)'} — "
                "run `sh devtools/checks/ci-local.sh` on it and paste the result"]
    return []


def description_problems(pull_request: dict, local_ci_required: bool = False) -> list[str]:
    """
    CLAUDE.md: a summary and a test plan; a Jira-tracked PR also Dev testing & notes for FE/QA; and while
    CI is suspended, a Local CI section for the head commit.
    """
    text = pull_request.get("description") or ""
    problems = [f"no '{name}' section" for name, pattern in DESCRIPTION_SECTIONS.items() if not pattern.search(text)]
    source = ((pull_request.get("source") or {}).get("branch") or {}).get("name", "")
    if (JIRA_KEY.search(source) or JIRA_KEY.search(pull_request.get("title") or "")) and not DEV_TESTING.search(text):
        problems.append("a Jira-tracked PR needs a 'Dev testing & notes for FE/QA' section")
    if local_ci_required:
        head = (((pull_request.get("source") or {}).get("commit") or {}).get("hash") or "")
        problems += local_ci_problems(text, head)
    return problems
