"""
Google Drive for the referee: the live FSD's last change (`modifiedTime`) and its comment threads —
read only. The spec step's live check asks the Doc itself instead of taking a session's word, and
`factory fsd-threads --save` keeps the threads beside the FSD snapshots.

Which token is the person's choice, so it is in their private settings file outside the repository
(`[requirements.sources] live_credential` in the private settings file, CLAUDE.md "Private settings"):

- `rclone:<remote>` — a remote made for this alone with `scope=drive.readonly` (e.g.
  `rclone:gdrive-readonly`, as factory.toml and devtools/README.md say), so the token itself
  cannot write. A harmless `rclone about` refreshes it; `rclone config dump` is read for the
  access token.
- anything else — a command that prints an OAuth access token with a Drive read scope.

The token is never printed, logged, stored or put on a command line: it reaches curl on stdin,
as in `bitbucket.py`, whose curl list this shares (the office network lets one curl through on
one day and another the next). Python 3.11+, standard library only.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.error
import urllib.parse
import urllib.request

import bitbucket

API = "https://www.googleapis.com/drive/v3"
ATTEMPTS = 3
THREAD_FIELDS = ("nextPageToken,comments(id,createdTime,modifiedTime,resolved,author/displayName,content,"
                 "quotedFileContent/value,replies(author/displayName,content,modifiedTime,action))")


class DriveError(Exception):
    """Drive could not be asked — never a reason to pass a check, only to say "could not check"."""


def run_step(argv: list[str], step: str, timeout: int) -> str:
    """
    One step of getting the token; its stdout, or a DriveError naming the step and the first line of
    what it said on stderr. Never its stdout, which can hold the token or the whole rclone config.
    """
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise DriveError(f"no Drive token: {step} could not run ({type(error).__name__})") from None
    if done.returncode != 0:
        # rclone names its config file, under the person's home folder, when it is missing.
        said = bitbucket.without_home(next((line.strip() for line in (done.stderr or "").splitlines()
                                            if line.strip()), ""))
        raise DriveError(f"no Drive token: {step} exited {done.returncode}" + (f": {said[:200]}" if said else ""))
    return done.stdout or ""


def command_words(command: str, windows: bool = os.name == "nt") -> list[str]:
    """
    The token command's words. On Windows a backslash is a path separator, not an escape — POSIX
    splitting turned `C:\\tools\\gcloud.cmd` into `C:toolsgcloud.cmd`. So on Windows a double quote,
    anywhere in a word, only groups what is between it and its partner and is itself taken off, as a
    Windows program reads its command line: a quoted path with a space stays one word, and
    `--format="value(token)"` reaches the program as `--format=value(token)`. A single quote is
    an ordinary character there, as in `C:\\Users\\O'Brien`.
    """
    if not windows:
        return shlex.split(command)
    words: list[str] = []
    word, quote, started = "", "", False
    for char in command:
        if quote:
            if char == quote:
                quote = ""
            else:
                word += char
        elif char == '"':
            quote, started = char, True
        elif char.isspace():
            if started:
                words.append(word)
            word, started = "", False
        else:
            word += char
            started = True
    if quote:
        raise DriveError("the token command has a quote that never closes.")
    if started:
        words.append(word)
    return words


def access_token(credential: str) -> str:
    if not credential.startswith("rclone:"):
        token = run_step(command_words(credential), "the token command", 90).strip()
        if not token:
            raise DriveError("no Drive token: the token command printed nothing")
        return token
    remote = credential.split(":", 1)[1]
    # The refresh: a token past its hour is renewed only when rclone uses the remote.
    run_step(["rclone", "about", f"{remote}:"], f"`rclone about {remote}:` (the token refresh)", 90)
    dump = run_step(["rclone", "config", "dump"], "`rclone config dump`", 30)
    try:
        entry = json.loads(dump or "{}").get(remote)
    except (ValueError, AttributeError):
        raise DriveError("no Drive token: `rclone config dump` printed something that is not JSON") from None
    if not isinstance(entry, dict):
        raise DriveError(f"no Drive token: rclone's config has no remote named {remote}")
    try:
        token = json.loads(entry.get("token") or "{}").get("access_token", "")
    except (ValueError, AttributeError):
        token = ""
    if not token:
        raise DriveError(f"no Drive token: the remote {remote} holds no readable access token")
    return token


def fetch(url: str, token: str) -> tuple[int, str]:
    """(status, body). curl when there is one, the token on stdin; urllib otherwise."""
    curls = bitbucket.curl_binaries()
    if not curls:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            raise DriveError("Google Drive could not be reached") from None
    config = f'header = "Authorization: Bearer {token}"\nheader = "Accept: application/json"\n'
    try:
        return bitbucket.get_through_curls(curls, url, config, ATTEMPTS)
    except bitbucket.CurlFailure as failure:
        raise DriveError(f"Google Drive could not be reached: {failure}") from None


def get_json(path: str, token: str, query: dict | None = None) -> dict:
    url = f"{API}/{path}" + ("?" + urllib.parse.urlencode(query) if query else "")
    status, body = fetch(url, token)
    if status != 200:
        raise DriveError(f"Google Drive answered {status} for {path.split('?')[0]}")
    try:
        return json.loads(body)
    except ValueError:
        raise DriveError("Google Drive answered with something that is not JSON") from None


def modified_time(file_id: str, token: str) -> str:
    return get_json(f"files/{file_id}", token, {"fields": "modifiedTime", "supportsAllDrives": "true"})["modifiedTime"]


def export_text(file_id: str, token: str) -> str:
    """The Doc's text as Drive exports it to plain text — what a section check compares."""
    query = urllib.parse.urlencode({"mimeType": "text/plain", "supportsAllDrives": "true"})
    status, body = fetch(f"{API}/files/{file_id}/export?{query}", token)
    if status != 200:
        raise DriveError(f"Google Drive answered {status} for the text export")
    return body


def comment_threads(file_id: str, token: str) -> list[dict]:
    """Every thread, resolved or not, with its replies. A thread's modifiedTime moves with any reply."""
    threads: list[dict] = []
    page = ""
    while True:
        query = {"fields": THREAD_FIELDS, "pageSize": "100", "includeDeleted": "false"}
        if page:
            query["pageToken"] = page
        data = get_json(f"files/{file_id}/comments", token, query)
        threads += data.get("comments", [])
        page = data.get("nextPageToken", "")
        if not page:
            return threads
