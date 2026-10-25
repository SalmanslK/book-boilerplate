#!/usr/bin/env python3
"""
Claude Code PreToolUse guard: refuses, before it happens, what CLAUDE.md says
never to do.

Wired in .claude/settings.json for the file-writing tools (Edit, Write,
MultiEdit, NotebookEdit), the shell tools (Bash, PowerShell, Monitor) and the
read tools (Read, Grep, Glob, NotebookRead — for the private-settings rule).
Claude Code passes the tool call as JSON on stdin; this prints one JSON decision
on stdout and always exits 0. When it cannot judge a call — its rules or the run
state unreadable, an unexpected error — it refuses anything but a plain read.

Three outcomes per rule:

    deny   refused with the reason. In warn mode (guard-config.json) it is
           allowed instead, and the session is told it would have been refused.
    ask    Claude Code asks the person at the keyboard. Used where the agent
           may legitimately need the change but must not make it alone — the
           files that steer the AI (the guard, the checks, hooks, CI, the
           instruction files), Claude Code's own settings outside the
           repository, a force-push, dropping a database, and the person's
           private settings file. Never downgraded.
    always a deny that warn mode does not downgrade — the factory's own integrity:
           gate-approval, session-marker, test-locked, run-record, run-unreadable.

This is a first layer, not the authority: the commit hooks and CI re-check what
reaches a pull request, and a shell command can always write a file some way
this cannot parse. Where it cannot parse a command reliably, it refuses it. See docs/plans/ai-factory-harness.md §2.3 and §5.1.

Every decision other than allow is appended to .factory/guard-log.jsonl with
the rule, the tool and the path — never the command or the content, which may
hold the secret being refused.

Python 3, standard library only; test_guard.py is the self-test table CI runs.
"""

from __future__ import annotations

import datetime
import fnmatch
import hashlib
import itertools
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Iterable

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "guard-config.json")
LOG_RELATIVE = os.path.join(".factory", "guard-log.jsonl")

FACTORY_CONFIG = os.path.join(os.path.dirname(HERE), "factory.toml")

DENY = "deny"
ASK = "ask"
ALWAYS = "always"

FILE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
SHELL_TOOLS = frozenset({"Bash", "PowerShell", "Monitor"})

PERSON_ONLY_COMMANDS = frozenset({"approve", "unlock", "abandon", "open-pr", "answer", "allow"})
RECORD_FENCE = "```json factory-record"  # factory.RECORD_FENCE — the self-test holds them equal
LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class Rules:
    """This repository's "never" rules — `[guard]` in factory.toml. The guard carries none of its own."""
    protected_branches: frozenset[str]
    base_ref: str
    read_only: tuple[str, ...]
    read_only_maintained: tuple[str, ...]
    read_only_reason: str
    applied: tuple[str, ...]
    applied_reason: str
    never_by_hand: tuple[str, ...]
    never_by_hand_reason: str
    ask: tuple[str, ...]
    ask_except: tuple[str, ...]
    generated: dict[str, str]
    non_dev_target: re.Pattern
    dotnet_ef: bool
    appsettings_secrets: bool
    runs_dir: str = "docs/factory/runs"
    service_accounts: tuple[str, ...] = ()
    private_settings: str = ""
    # `[guard] hooks_path`: the folder (or folders) core.hooksPath may name; empty for the defaults (DEFAULT_HOOKS).
    hooks_path: tuple[str, ...] = ()


class RulesUnavailable(Exception):
    """The guard's rules could not be read; the guard then says so rather than guess."""


class GitUnavailable(Exception):
    """git did not answer (missing, or too slow); the guard then refuses rather than read silence as "no"."""


class TooSlowToJudge(Exception):
    """The globs a command names took longer to match than the guard may spend: a hook past its time limit
    does not block, so the guard refuses instead of running on."""


_RULES: list[Rules] = []


def rules() -> Rules:
    if _RULES:
        return _RULES[0]
    try:
        import tomllib
    except ModuleNotFoundError as error:
        raise RulesUnavailable("the guard needs Python 3.11+ to read factory.toml") from error
    try:
        with open(FACTORY_CONFIG, "rb") as handle:
            raw = tomllib.load(handle)
        guard = raw["guard"]
        words = "|".join(re.escape(word) for word in guard.get("non_dev_targets", []))
        loaded = Rules(
            protected_branches=frozenset(raw.get("protected_branches", [])),
            base_ref=raw.get("base_ref", "origin/main"),
            read_only=tuple(guard.get("read_only", [])),
            read_only_maintained=tuple(guard.get("read_only_maintained", [])),
            read_only_reason=guard.get("read_only_reason", "is read-only."),
            applied=tuple(guard.get("applied", [])),
            applied_reason=guard.get("applied_reason", "is already on the base branch; never change it."),
            never_by_hand=tuple(guard.get("never_by_hand", [])),
            never_by_hand_reason=guard.get("never_by_hand_reason", "is written by a tool, never by hand."),
            ask=tuple(guard.get("ask", [])),
            ask_except=tuple(guard.get("ask_except", [])),
            generated=dict(guard.get("generated", {})),
            non_dev_target=re.compile(rf"\b({words})\b" if words else r"(?!)", re.IGNORECASE),
            dotnet_ef=bool(guard.get("dotnet_ef", False)),
            appsettings_secrets=bool(guard.get("appsettings_secrets", False)),
            runs_dir=str(raw.get("runs_dir", "docs/factory/runs")).rstrip("/"),
            service_accounts=tuple(guard.get("service_accounts", [])),
            private_settings=str(raw.get("private_settings", "")),
            hooks_path=configured_hooks(guard.get("hooks_path")),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise RulesUnavailable(f"could not read [guard] from {FACTORY_CONFIG}: {type(error).__name__}") from error
    _RULES.append(loaded)
    return loaded


def configured_hooks(value) -> tuple[str, ...]:
    """`[guard] hooks_path` as a tuple: one folder, a list of them, or nothing."""
    if value is None:
        return ()
    return (str(value),) if isinstance(value, str) else tuple(str(item) for item in value)


def matches(rel: str, patterns: Iterable[str]) -> bool:
    """Case-insensitive globs from the repository root; `*` and `**` both span folders."""
    lower = rel.lower()
    return any(fnmatch.fnmatchcase(lower, pattern.lower()) for pattern in patterns)

SECRET_PATTERNS = (
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("an AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("an Atlassian API token", re.compile(r"\bATATT[0-9A-Za-z_\-=]{20,}")),
    ("a Bitbucket app password", re.compile(r"\bATBB[0-9A-Za-z]{20,}")),
    ("a GitHub token", re.compile(r"\b(?:gh[pousr]_[0-9A-Za-z]{30,}|github_pat_[0-9A-Za-z_]{30,})")),
    ("a Slack token", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}")),
    ("an Anthropic API key", re.compile(r"\bsk-ant-[0-9A-Za-z_\-]{20,}")),
    ("a Google access token", re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}")),
    ("a Google refresh token", re.compile(r"\b1//0[0-9A-Za-z_\-]{30,}")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
)
# A path inside someone's home folder names that person and their machine; a repository file says
# `~` or `<you>` instead. Placeholders (`<…>`, `{…}`, `$…`, `%…%`) are fine.
HOME_FOLDER_PATH = re.compile(
    r"(?:\b[A-Za-z]:[\\/]{1,2}(?i:users)[\\/]{1,2}|(?<![\w.…}])/(?:c/)?Users/|(?<![\w.…}])/home/)(?![<{$%~.])"
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9._ -]*)(?=[\\/])")


def personal_home_path(text: str) -> bool:
    """A home-folder path that is a person's — not a shared service account `[guard] service_accounts` names
    (the office server's deploy account, say)."""
    try:
        shared = {name.lower() for name in rules().service_accounts}
    except RulesUnavailable:
        shared = set()
    return any(match.group("name").lower() not in shared for match in HOME_FOLDER_PATH.finditer(text))

# ---------------------------------------------------------------- private settings and credential stores
#
# A person's own settings and credentials live outside every repository (CLAUDE.md, Private settings).
# A session may not read them, print them, or put them in a file: what it never sees it cannot leak.

READ_TOOLS = frozenset({"Read", "Grep", "Glob", "NotebookRead"})
# Pieces of a path or command that name a credential store wherever it sits.
# `/.ai-factory/` is the kit's default settings folder: private even when FACTORY_LOCAL_SETTINGS points elsewhere.
PRIVATE_MARKERS = ("/local.toml", "/.ai-factory/", ".git-credentials", "rclone.conf", ".netrc", ".credentials.json",
                   "id_rsa", "id_ecdsa", "id_ed25519", "/.ssh/", "/.aws/", "/gcloud/", "/.docker/config.json")
SECRET_VARIABLE = re.compile(r"(PASSWORD|PASSWD|TOKEN|SECRET|CREDENTIAL|API_?KEY|PRIVATE_?KEY)", re.IGNORECASE)
PRINTERS = frozenset({"echo", "printf", "write-output", "write-host", "print", "out-host", "out-string"})
# Builtins that print every exported variable with its value when given nothing else.
ENVIRONMENT_PRINTERS = (["export"], ["export", "-p"], ["declare"], ["declare", "-p"], ["declare", "-x"],
                        ["typeset"], ["typeset", "-p"], ["typeset", "-x"])


# What moves the private settings file (its own folder is protected wherever factory.toml puts it; `/local.toml`
# above holds one in any folder while the config cannot be read).
SETTINGS_VARIABLES = ("FACTORY_LOCAL_SETTINGS",)


def private_settings_path() -> str:
    """factory.private_settings_path(), without importing the referee — the self-test holds them equal."""
    for variable in SETTINGS_VARIABLES:
        if os.environ.get(variable):
            return os.environ[variable]
    try:
        configured = rules().private_settings
    except RulesUnavailable:
        configured = ""
    return os.path.expanduser(configured or "~/.ai-factory/local.toml")


# The home folder and the roaming application-data folder in every shell's spelling: `$HOME`, `${HOME}`,
# `$env:USERPROFILE`, `${env:USERPROFILE}`, `$USERPROFILE`, `%USERPROFILE%` (once: `rg token $USERPROFILE`
# searched the home folder unseen), and `$APPDATA`'s kin.
HOME_VARIABLE = re.compile(r"\$\{(?:env:)?(?:home|userprofile)\}|\$(?:env:)?(?:home|userprofile)(?!\w)|"
                           r"%(?:home|userprofile)%", re.IGNORECASE)
APPDATA_VARIABLE = re.compile(r"\$\{(?:env:)?appdata\}|\$(?:env:)?appdata(?!\w)|%appdata%", re.IGNORECASE)


def expand_home(text: str) -> str:
    home = os.path.expanduser("~").replace("\\", "/")
    appdata = (os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")).replace("\\", "/")
    text = HOME_VARIABLE.sub(lambda _: home, text)
    text = APPDATA_VARIABLE.sub(lambda _: appdata, text)
    if text.startswith("~") and (len(text) == 1 or text[1] in "/\\"):
        text = home + text[1:]
    return text


def normal_path(text: str, cwd: str | None = None) -> str:
    """
    `text` as the guard compares paths: `~` and the home folder's variables written out, forward slashes, a
    relative path resolved from `cwd` (the folder the guard follows), `.`, `..` and doubled slashes folded,
    lower case. Once, `~/./.git-cred*`, `~//.git-cred*` and `/proc//self/environ` named a store the
    comparison did not see.
    """
    text = to_native(expand_home(text.strip().strip("'\""))).replace("\\", "/")
    if not text:
        return ""
    if cwd is not None and not os.path.isabs(text):
        text = posixpath.join(str(cwd).replace("\\", "/"), text)
    trailing = text.endswith("/")
    text = posixpath.normpath(text)
    if text.startswith("//"):
        text = text[1:]  # POSIX keeps a leading `//`; no path the guard compares means anything by it
    return (text + "/" if trailing and text != "/" else text).lower()


def private_places() -> list[tuple[str, bool]]:
    """Where the credential stores PRIVATE_MARKERS names usually are, and whether each is a folder — what a
    wildcard (`~/.git-cred*`, `~/.my*/local.toml`) is compared against, since it holds no marker itself."""
    home = normal_path("~").rstrip("/")
    appdata = normal_path(os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"))
    mine = normal_path(private_settings_path())
    return [(mine, False), (posixpath.dirname(mine), True), (f"{home}/.git-credentials", False),
            (f"{home}/.netrc", False), (f"{home}/.ssh", True), (f"{home}/.aws", True), (f"{home}/.config/gcloud", True),
            (f"{appdata}/gcloud", True), (f"{appdata}/rclone/rclone.conf", False),
            (f"{home}/.config/rclone/rclone.conf", False), (f"{home}/.docker/config.json", False),
            (f"{home}/.claude/.credentials.json", False)]


def wildcard_segment(name: str, glob: str, dotglob: bool) -> bool:
    """Whether the path segment `name` matches the wildcard segment `glob`. Without `dotglob` a wildcard does not
    match a leading dot, as bash's does not (`~/.claude/*.json` is not `.credentials.json`); PowerShell's does."""
    if not dotglob and name.startswith(".") and not glob.startswith("."):
        return False
    return fnmatch.fnmatchcase(name, glob)


def could_name(pattern: str, place: str, folder: bool, dotglob: bool = True) -> bool:
    """Whether the wildcard path `pattern` can match `place`, a folder above it, or — `folder` — what is inside it.
    Segment by segment, so `*` stays inside one folder as the shell's does."""
    wanted, parts = pattern.rstrip("/").split("/"), place.split("/")
    if not all(wildcard_segment(part, glob, dotglob) for part, glob in zip(parts, wanted)):
        return False
    return len(wanted) <= len(parts) or folder


# Every process's environment, readable as a file on Linux and in Git Bash: `cat /proc/self/environ` prints it.
PROC_ENVIRON = re.compile(r"(^|/)proc/[^/]+/environ/")


def is_private(path_or_token: str, cwd: str | None = None, dotglob: bool = True) -> bool:
    """Does this name a person's private settings or a credential store — or could it, as a wildcard? A relative
    path is resolved from `cwd` when the guard knows it (`cd ~ && cat .git-cred*`). `dotglob`: whether a
    wildcard matches a leading dot (PowerShell, the tools) or not (bash)."""
    text = normal_path(path_or_token, cwd)
    mine = normal_path(private_settings_path())
    # A trailing slash, so the folder itself (`~/.ssh`) matches a marker written for what is inside it.
    text = text.rstrip("/") + "/"
    if any(char in text for char in "*?[") and os.path.isabs(text):
        # A wildcard names no store itself, but the shell expands it to one: `cat ~/.git-cred*`.
        if any(could_name(text, place, folder, dotglob) for place, folder in private_places()):
            return True
    return text.startswith(posixpath.dirname(mine) + "/") or any(m in text for m in PRIVATE_MARKERS) \
        or bool(PROC_ENVIRON.search(text))


def search_root(path: str, cwd: str | None = None) -> str:
    """The folder a search of `path` starts from, as normal_path writes it: a wildcard's own folder
    (`findstr /s x ~/*` searches the home folder), and the filesystem root kept as `/`."""
    text = normal_path(path, cwd)
    if any(char in text for char in "*?["):
        text = posixpath.dirname(text.rstrip("/"))
        while any(char in text for char in "*?["):
            text = posixpath.dirname(text)
    return text.rstrip("/") or ("/" if text.startswith("/") else "")


def home_or_above(path: str, cwd: str | None = None) -> bool:
    """Whether `path` is the home folder or a folder holding it — the root of a search that reaches every store.
    A wildcard's own folder counts: `findstr /s x ~/*`."""
    text = search_root(path, cwd)
    home = normal_path("~").rstrip("/")
    return bool(text) and os.path.isabs(text + "/") and (home == text or home.startswith(text.rstrip("/") + "/"))


def search_reaches_store(path: str, cwd: str | None = None) -> bool:
    """
    Whether a recursive search rooted at `path` reads a credential store: the home folder or above, or any
    folder with a store beneath it (`~/.config`, `~/.claude`, `~/.docker`, `%APPDATA%/rclone`). A
    search inside the repository reaches none.
    """
    if home_or_above(path, cwd) or is_private(path, cwd):
        return True
    root = search_root(path, cwd)
    if not root or not os.path.isabs(root + "/"):
        return False
    prefix = root.rstrip("/") + "/"
    return any(place.startswith(prefix) for place, _ in private_places())


PRIVATE_REASON = ("a person's private settings or a credential store. Those stay outside the repository and are "
                  "never read, printed or copied by a session; the person edits them themselves "
                  "(CLAUDE.md, Private settings).")


def private_finding(what: str) -> Finding:
    return Finding("private-settings", DENY, f"{what} {PRIVATE_REASON}", None)


def is_private_settings_file(target: str, cwd: str | None = None) -> bool:
    return normal_path(target, cwd).rstrip("/") == normal_path(private_settings_path())


def private_file_tool_findings(tool_input: dict, cwd: str | None = None) -> list[Finding]:
    """Writing: the private settings file itself is the person's to confirm; any other store is refused."""
    target = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if not target or not is_private(target, cwd):
        return []
    if is_private_settings_file(target, cwd):
        return [Finding("private-settings", ASK,
                        "This is the person's private settings file. A person confirms every change to it, and "
                        "its contents are never shown in a session.", None)]
    return [private_finding("This writes")]


STORE_SEARCH = ("This searches a folder that holds a credential store (the home folder or above, or a folder with a "
                "store beneath it). Search the folder you need instead; it holds")


def read_tool_findings(tool: str, tool_input: dict, cwd: str) -> list[Finding]:
    """The read tools: a path naming a store, a Grep glob that can match one, and a Grep whose folder holds one —
    its `path`, or the working folder without one, resolved from there (once: a `..` chain, `.` in the home
    folder)."""
    path_keys = ("file_path", "notebook_path", "path")
    targets = [str(tool_input.get(key) or "") for key in path_keys]
    if any(target and is_private(target, cwd) for target in targets):
        return [private_finding("This reads")]
    if any(tool_input.get(key) and is_private(str(tool_input[key])) for key in ("pattern", "glob")):
        return [private_finding("This reads")]
    if tool == "Grep" and tool_input.get("glob"):
        # Grep's glob filters the names beneath its path: `~/.config` with `rclone.conf` reads the store.
        if is_private(f"{tool_input.get('path') or '.'}/**/{tool_input['glob']}", cwd):
            return [private_finding("This reads")]
    if tool == "Grep" and search_reaches_store(str(tool_input.get("path") or "."), cwd):
        return [private_finding(STORE_SEARCH)]
    return []


SSH_CLIENTS = frozenset({"ssh", "scp", "sftp"})
IDENTITY_OPTION = re.compile(r"^identityfile\s*[= ]\s*(.+)$", re.IGNORECASE)
# Each client's options that take a value; every other letter is a flag. The first word that is not an
# option ends them: ssh's destination, then its remote command; scp's and sftp's files and hosts.
SSH_OPTIONS_WITH_VALUE = {"ssh": "BbcDEeFIiJLlmOoPpQRSWw", "scp": "cDFiJlmoPSX", "sftp": "BbcDFiJlNoPRSsX"}


def identity_files(name: str, argv: list[str]) -> set[int]:
    """
    The positions in `argv` where ssh, scp or sftp is handed a key file (`-i <key>`, `-i<key>`, `-vi <key>`,
    `-o IdentityFile=<key>`). ssh reads the key in its own process and sends only a signature to the
    server: the session never sees it — the way /deploy-dev-qa reaches the office server. Every other
    word is still judged, so `cat ~/.ssh/id_ed25519` and a remote command naming a store stay refused.
    Only the client's own options are read — up to `--` or the first word that is not one — and a key is
    never a word starting with `-` (once: `scp -i -i <store> host:` copies the store). A key inside the
    private settings folder is not let through.
    """
    if name not in SSH_CLIENTS:
        return set()
    mine = os.path.dirname(private_settings_path().replace("\\", "/").lower()) + "/"
    with_value = SSH_OPTIONS_WITH_VALUE[name]

    def a_key(value: str) -> bool:
        return bool(value) and not value.startswith("-") and \
            not to_native(expand_home(value.strip("'\""))).replace("\\", "/").lower().startswith(mine)

    handed: set[int] = set()
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg == "--" or not arg.startswith("-") or arg == "-":
            break
        for position, letter in enumerate(arg[1:], 1):
            if letter not in with_value:
                continue
            attached = arg[position + 1:]
            where = index if attached else index + 1
            value = attached or (argv[index + 1] if index + 1 < len(argv) else "")
            if letter == "o":
                match = IDENTITY_OPTION.match(value)
                value = match.group(1) if match else ""
            if letter in "io" and a_key(value):
                handed.add(where)
            index = where
            break
        index += 1
    return handed


def commit_messages(name: str, argv: list[str]) -> set[int]:
    """The positions in `argv` of `git commit`'s message text (`-m <text>`, `-am <text>`, `-m<text>`,
    `--message=<text>`): a message may mention a store by name and reads nothing. Its `-F`/`--file` and
    `-t`/`--template` are files git reads into the commit, so they are still judged."""
    if name != "git":
        return set()
    start = 1 + git_subcommand(argv[1:])
    if argv[start:start + 1] != ["commit"]:
        return set()
    messages: set[int] = set()
    for index in range(start + 1, len(argv)):
        arg = argv[index]
        if arg in ("-m", "--message") or re.fullmatch(r"-[A-Za-z]+m", arg):
            messages.add(index + 1)
        elif arg.startswith("--message=") or (arg.startswith("-m") and len(arg) > 2):
            messages.add(index)
    return messages


CONTENT_SEARCHERS = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "ack", "findstr", "select-string", "sls"})
# Options of grep and rg that take the next word as their value; a word after one is not a folder.
SEARCH_OPTIONS_WITH_VALUE = frozenset({"-e", "-f", "-m", "-A", "-B", "-C", "-d", "-D", "-g", "-t", "-T", "-j", "-M",
                                       "-E", "-r", "--regexp", "--file", "--glob", "--iglob", "--type", "--type-not",
                                       "--max-count", "--context", "--after-context", "--before-context", "--include",
                                       "--exclude", "--exclude-dir", "--threads", "--max-columns", "--encoding",
                                       "--replace", "--max-depth"})


def recursive_search_roots(name: str, args: list[str]) -> list[str]:
    """
    The folders a recursive content search starts from (`grep -r`, `rg`, `ag`, `ack`, `findstr /s`), its pattern
    among them (a pattern judged as a folder only refuses more); none for a search that is not recursive. With no
    folder named it searches the working folder, `.` (once: `cd ~ && rg token`).
    """
    lowered = [arg.lower() for arg in args]
    recursive = name in ("rg", "ag", "ack") or (
        name in ("grep", "egrep", "fgrep") and any(
            arg in ("--recursive", "--dereference-recursive", "--directories=recurse") or
            re.fullmatch(r"-[A-Za-z]*[rR][A-Za-z]*", arg) or (arg == "-d" and lowered[index + 1:index + 2] == ["recurse"])
            for index, arg in enumerate(args))) or (name == "findstr" and "/s" in lowered)
    if not recursive:
        return []
    if name == "findstr":
        return [arg for arg in args if not re.fullmatch(r"/[A-Za-z](:.*)?", arg)] or ["."]
    words, skip, given_pattern = [], False, False
    for arg in args:
        if skip:
            skip = False
        elif arg in SEARCH_OPTIONS_WITH_VALUE and not (name in ("grep", "egrep", "fgrep") and arg in ("-r", "-E", "-T")):
            skip = True
            given_pattern = given_pattern or arg in ("-e", "-f", "--regexp", "--file")
        elif arg.startswith(("--regexp=", "--file=")):
            given_pattern = True
        elif not arg.startswith("-"):
            words.append(arg)
    folders = words if given_pattern else words[1:]
    return words + ([] if folders else ["."])


# Commands that list folders, and those that read the files a listing hands them through a pipe.
LISTERS = frozenset({"find", "ls", "dir", "get-childitem", "gci", "get-item", "gi"})
PIPE_READERS = CONTENT_SEARCHERS | frozenset({"cat", "type", "get-content", "gc", "head", "tail", "less", "more",
                                              "foreach-object", "%", "xargs", "copy-item", "cpi", "cp"})


def listing_roots(name: str, args: list[str], powershell: bool) -> list[str] | None:
    """The folders a listing command names (`ls x y`, `Get-ChildItem -Path x`, `find x …`), `.` without one; None
    when `name` lists nothing."""
    if name not in LISTERS:
        return None
    if name == "find" and not powershell:
        kept = [arg for arg in args if arg not in ("-H", "-L", "-P")]
        starts = list(itertools.takewhile(lambda arg: not arg.startswith("-") and arg not in ("(", "!", ","), kept))
        names = find_names(kept[len(starts):])
        return [f"{start.rstrip('/') or '/'}/**/{each}" for start in starts or ["."] for each in names] if names \
            else starts or ["."]
    values: list[str] = []
    skip = False
    narrowed = None
    for index, arg in enumerate(args):
        lowered = arg.lower()
        if skip:
            skip = False
        elif lowered in POWERSHELL_PATH_OPTIONS or lowered == "-literalpath":
            continue  # its value is a path: the next word is kept
        # `-i` is `-Include` too (once: read as no option, `gci -r -i Migrations` listed a folder named Migrations
        # here, and `| ri -r` passed).
        elif powershell and (len(lowered) > 2 or lowered == "-i") and (
                "-filter".startswith(lowered) or "-include".startswith(lowered)):
            narrowed = args[index + 1] if index + 1 < len(args) and narrowed is None else ""
            skip = True
        elif lowered.startswith("-") and powershell and any(option.startswith(lowered) for option in
                                                             (*POWERSHELL_VALUE_OPTIONS, "-depth") if len(lowered) > 2):
            skip = True
        elif not arg.startswith("-"):
            values.extend(part for part in arg.split(",") if part) if powershell else values.append(arg)
    roots = values or ["."]
    if narrowed and "," not in narrowed:  # `-Filter *.tmp`: only what the filter names, beneath each root
        deep = recursive_listing(name, args, powershell)
        roots = [f"{root.rstrip('/')}/{'**/' if deep else ''}{narrowed}" for root in roots]
    return roots


def find_names(expression: list[str]) -> list[str] | None:
    """
    The one name a `find` expression narrows what it reaches to (`-name x` with any other test, which narrows
    further), or None when it can reach anything — several names (`-name bin -o -name obj`), `-o`, `!` or a
    path test. A name read only after an action (`-exec`, `-delete`, `-fprint`) narrows nothing.
    """
    head = list(itertools.takewhile(lambda word: word not in FIND_EXEC and word != "-delete"
                                    and word not in FIND_FILE_WRITERS, expression))
    names = [expression[i + 1] for i, word in enumerate(expression[:-1]) if word in ("-name", "-iname")]
    named_first = [head[i + 1] for i, word in enumerate(head[:-1]) if word in ("-name", "-iname")]
    # clean-up by name read `find . -name bin -o -name obj | xargs rm -rf` as reaching only what is so
    # named; three reviews in a row found a way round it, so several names reach everything again, as before.
    return named_first if len(names) == 1 and len(named_first) == 1 and not FIND_WIDENING & set(expression) else None


def bare_names_elsewhere(name: str, args: list[str], powershell: bool, roots: list[str], cwd: str) -> bool:
    """
    Whether a listing prints bare names of what lies in a folder other than the working one: `ls x` prints
    `a.md`, not `x/a.md`, and xargs then acts on `./a.md` — `ls x | xargs rm -rf` removes what the working folder
    holds under those names. So does PowerShell's `Get-ChildItem x -Name`. `ls -d`, a glob (`ls x/*.md` prints the
    paths it matched) and a file name print paths; `find` prints paths from its start.
    """
    if powershell:
        if name not in ("get-childitem", "gci", "dir", "ls") or not any(
                len(arg) >= 3 and "-name".startswith(arg.lower()) for arg in args):
            return False
    elif name not in ("ls", "dir") or any(arg == "--directory" or re.fullmatch(r"-[A-Za-z1]*d[A-Za-z1]*", arg)
                                          for arg in args):
        return False
    here = normal_path(str(cwd)).rstrip("/")
    for root in roots:
        globbed = any(char in root for char in "*?[")
        if globbed and not powershell:
            continue
        folder = root[:root.rfind("/", 0, min(root.index(char) for char in "*?[" if char in root))] \
            if globbed and "/" in root else ("." if globbed else root)
        if normal_path(folder or "/", cwd).rstrip("/") == here:
            continue
        if not powershell and os.path.isfile(to_native(normal_path(root, cwd))):
            continue
        return True
    return False


def recursive_listing(name: str, args: list[str], powershell: bool) -> bool:
    """Whether a listing walks every folder beneath its roots: `find`, `ls -R`, `Get-ChildItem -Recurse`, `dir /s`."""
    lowered = [arg.lower() for arg in args]
    if name == "find" and not powershell:
        return True
    if name in ("get-childitem", "gci", "dir", "ls") and powershell:
        # `-r` and `-Depth` recurse too (once: `gci -r -Include Migrations | Remove-Item` was read as listing only
        # the working folder, and passed). A switch may carry its value after a colon — `-Recurse:1` recurses,
        # only `-Recurse:0`/`:$false` does not.
        def recurses(arg: str) -> bool:
            switch, colon, value = arg.partition(":")
            if colon and value.strip().lstrip("$") in ("0", "false"):
                return False
            return (len(switch) >= 2 and "-recurse".startswith(switch)) or (len(switch) >= 3 and "-depth".startswith(switch))
        return any(recurses(arg) for arg in lowered) or "/s" in lowered
    return name in ("ls", "dir") and any(re.fullmatch(r"-[A-Za-z]*R[A-Za-z]*", arg) or arg == "--recursive"
                                         for arg in args)


# PowerShell reads an environment variable as an item too: `(Get-Item env:GITHUB_TOKEN).Value` prints it.
ENVIRONMENT_ITEM_READERS = frozenset({"get-childitem", "gci", "dir", "ls", "get-item", "gi", "get-content", "gc", "cat",
                                      "type", "get-itemproperty", "gp", "get-itempropertyvalue", "gpv"})
# .NET's own reads of the environment: one variable by name, or all of them.
ENVIRONMENT_GETTER = re.compile(r"GetEnvironmentVariable\s*\(\s*(['\"]?)([^'\",)]*)\1", re.IGNORECASE)
ENVIRONMENT_DUMP = re.compile(r"GetEnvironmentVariables\s*\(", re.IGNORECASE)


def secret_reference_pattern(secret_names: Iterable[str] = ()) -> re.Pattern:
    """A variable named like a secret (`$GITHUB_TOKEN`, `${env:X_PASSWORD}`, `%API_KEY%`) — or one this command line
    filled from one (once: `x=$GITHUB_TOKEN; echo $x`)."""
    copies = "|".join(re.escape(name) for name in sorted(secret_names))
    named = r"[A-Za-z0-9_]*" + SECRET_VARIABLE.pattern + (rf"|(?:{copies})(?![A-Za-z0-9_])" if copies else "")
    return re.compile(r"(?:\$\{?(?:env:)?|%)(?:" + named + ")", re.IGNORECASE)


def secret_copies(segments_: list[list[str]], powershell: bool) -> set[str]:
    """The variables a command line fills from a secret-named one (`x=$GITHUB_TOKEN`, `$t = $env:API_TOKEN`, and
    from those in turn): printing one prints the secret, so each is treated as secret-named."""
    found: set[str] = set()
    while True:
        pattern = secret_reference_pattern(found)
        before = len(found)
        for segment in segments_:
            for index, word in enumerate(segment):
                assignment = re.match(r"^\$?([A-Za-z_]\w*)\s*=(.*)$", word) if not powershell else \
                    re.match(r"^\$([A-Za-z_]\w*)=(.*)$", word)
                if assignment and pattern.search(assignment.group(2)):
                    found.add(assignment.group(1))
                elif powershell and re.fullmatch(r"\$[A-Za-z_]\w*", word) and segment[index + 1:index + 2] == ["="] \
                        and any(pattern.search(rest) for rest in segment[index + 2:]):
                    found.add(word[1:])
        if len(found) == before:
            return found


def private_shell_findings(argv: list[str], segment: list[str], cwd: str | None = None, powershell: bool = False,
                           secret_names: Iterable[str] = ()) -> list[Finding]:
    """Commands that would read or print a credential: the stores themselves, and the tools that dump them."""
    name = program(argv[0]) if argv else ""
    args = argv[1:]
    lowered = [a.lower() for a in args]
    skipped = identity_files(name, argv) | commit_messages(name, argv)
    if any(is_private(token, cwd, dotglob=powershell) for index, token in enumerate(argv) if index not in skipped):
        return [private_finding("This command names")]
    if any(search_reaches_store(root, cwd) for root in recursive_search_roots(name, args)):
        return [private_finding(STORE_SEARCH)]
    if (name == "rclone" and lowered[:1] == ["config"] and len(lowered) > 1 and lowered[1] in ("show", "dump")) \
            or (name == "git" and "credential" in lowered[:2]) \
            or (name == "gcloud" and "auth" in lowered and any(a.startswith("print-") for a in lowered)) \
            or (name == "az" and "get-access-token" in lowered) \
            or name == "printenv" or segment == ["env"] or segment == ["set"] or segment in ENVIRONMENT_PRINTERS \
            or (name in ("get-childitem", "gci", "dir", "ls") and any(a.rstrip("/\\").lower() == "env:" for a in args)):
        return [private_finding("This command prints")]
    # `Get-Item env:GITHUB_TOKEN`, `Get-ChildItem env:*TOKEN*`: the environment read as items.
    if powershell and name in ENVIRONMENT_ITEM_READERS and any(
            re.match(r"^env:", arg, re.IGNORECASE) and (SECRET_VARIABLE.search(arg) or any(c in arg for c in "*?["))
            for arg in args):
        return [private_finding("This command prints")]
    secret_reference = secret_reference_pattern(secret_names)
    # A variable standing alone is printed by PowerShell (`$env:GITHUB_TOKEN`) and run by bash, whose error
    # then shows the value; an assignment from one (`$t = $env:X`, `T=$X`) prints nothing.
    alone = bool(argv) and secret_reference.search(argv[0]) and not ASSIGNMENT.match(argv[0]) and argv[1:2] != ["="] \
        and not re.match(r"^\$[A-Za-z_]\w*=", argv[0])
    if alone or (name in PRINTERS and any(secret_reference.search(token) for token in args)):
        return [Finding("private-settings", DENY,
                        "This prints a variable that holds a secret. Check that it is set (`test -n \"$VAR\"`) — "
                        "never print it (CLAUDE.md, Private settings).", None)]
    return []


def environment_read_findings(text: str) -> list[Finding]:
    """`[Environment]::GetEnvironmentVariable('<secret-like>')` prints the secret in PowerShell's output; a name built
    when it runs could be any of them, and `GetEnvironmentVariables()` prints them all."""
    if ENVIRONMENT_DUMP.search(text):
        return [private_finding("This command prints")]
    for match in ENVIRONMENT_GETTER.finditer(text):
        name = match.group(2).strip()
        if not re.fullmatch(r"[A-Za-z_]\w*", name) or SECRET_VARIABLE.search(name):
            return [Finding("private-settings", DENY,
                            "This reads an environment variable that holds a secret, or one named when it runs. Check "
                            "that it is set (`test -n \"$VAR\"`) — never print it (CLAUDE.md, Private settings).", None)]
    return []

# The same three rules as devtools/checks/check-appsettings-secrets.sh, applied
# to what is about to be written rather than to what was staged.
APPSETTINGS_NAME = re.compile(r"(^|/)appsettings[^/]*\.json$", re.IGNORECASE)
APPSETTINGS_EXEMPT = re.compile(r"(^|/)appsettings\.development\.json$", re.IGNORECASE)
JWT_KEY_VALUE = re.compile(r'"Key"\s*:\s*"([^"]*)"')
CONNECTION_PASSWORD = re.compile(r"Password=([^;\"']*)", re.IGNORECASE)
JSON_PASSWORD = re.compile(r'"Password"\s*:\s*"([^"]*)"')

HEREDOC_START = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
HERE_STRING_START = re.compile(r"@['\"]\s*$")

SHELL_SEPARATORS = frozenset({"&&", "||", ";", "|", "&", "|&", ";;"})
REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>"})


@dataclass(frozen=True)
class Wrapper:
    """A command that runs the command after it: its own options that take a value (short letters, long
    names), how many words it takes before the command, and the options that change the folder it runs in."""
    short: str = ""
    long: frozenset[str] = frozenset()
    words: int = 0
    chdir: frozenset[str] = frozenset()


# Each wrapper's own options are skipped, with their values, before the command is chosen:
# `env -u X git push …` runs git, not `-u`.
WRAPPERS = {
    "env": Wrapper("uCSP", frozenset({"--unset", "--chdir", "--split-string"}), chdir=frozenset({"-C", "--chdir"})),
    "sudo": Wrapper("ughpCDrtUTR", frozenset({"--user", "--group", "--host", "--prompt", "--close-from", "--chdir",
                                              "--role", "--type", "--other-user", "--command-timeout", "--chroot"}),
                    chdir=frozenset({"-D", "--chdir"})),
    "doas": Wrapper("uC"),
    "time": Wrapper("fo", frozenset({"--format", "--output"})),
    "timeout": Wrapper("ks", frozenset({"--kill-after", "--signal"}), words=1),
    "nice": Wrapper("n", frozenset({"--adjustment"})),
    "ionice": Wrapper("cnpPu", frozenset({"--class", "--classdata", "--pid", "--pgid", "--uid"})),
    "stdbuf": Wrapper("ioe", frozenset({"--input", "--output", "--error"})),
    "exec": Wrapper("a"),
    "nohup": Wrapper(), "setsid": Wrapper(), "command": Wrapper(), "builtin": Wrapper(),
    "rtk": Wrapper(), "proxy": Wrapper(),
}

# Tools that write every path they are given, and those that write only their destination.
IN_PLACE_EDITORS = frozenset({"sed", "perl"})
# `-i` anywhere in a cluster of short flags edits in place: `sed -Ei`, `perl -pi`, `perl -0pi.bak`.
IN_PLACE_FLAG = re.compile(r"^(--in-place|-[A-Za-z0-9]*i)")
WRITES_ALL_ARGS = frozenset({
    "rm", "mv", "touch", "truncate", "tee", "shred", "unlink",
    "remove-item", "move-item", "new-item", "set-content", "add-content", "clear-content",
    "out-file", "rename-item", "ri", "mi", "ni", "sc", "ac", "del", "erase", "rd", "rmdir",
})
WRITES_DESTINATION = frozenset({"cp", "copy-item", "cpi", "copy", "install", "rsync", "ln"})
# Of those, the ones that remove or move a folder whole, everything under it with it.
WHOLE_FOLDER_WRITERS = frozenset({
    "rm", "mv", "shred", "unlink", "remove-item", "move-item", "rename-item", "ri", "mi", "del", "erase", "rd", "rmdir",
})
# Of those, the ones whose last argument (or -Destination) is where things go, not what is moved.
MOVERS = frozenset({"mv", "move-item", "mi", "rename-item"})


@dataclass(frozen=True)
class Finding:
    rule: str
    level: str
    reason: str
    path: str | None = None


class UnknownCwd(str):
    """
    A working folder the guard cannot name — `cd "$X"`, `cd "$(…)"`, `popd` past what it saw. Its value
    is the repository root, so path rules judge a relative path from there; the branch rules see the type
    and do not take the root's branch for the folder's.
    """


class Repo:
    """What the rules need to know about the repository. Tests pass a fake."""

    def __init__(self, root: str):
        self.root = root

    def knows(self, cwd: str | None) -> bool:
        """Whether git can be asked about `cwd` itself: a folder the guard could name, and one that exists."""
        return not isinstance(cwd, UnknownCwd) and (cwd is None or os.path.isdir(cwd))

    def _git(self, *args: str, cwd: str | None = None) -> str | None:
        """
        git's answer, or None when git answered "no" (a non-zero exit: no such ref, not tracked). git that
        does not answer at all — missing, or past the timeout — raises GitUnavailable: read as "no", the
        branch and applied-file rules would allow the very call they exist to stop. A folder that does not
        exist is not git missing: callers ask `knows` first, and get no answer here.
        """
        where = cwd or self.root
        if cwd and not os.path.isdir(cwd):
            return None
        try:
            done = subprocess.run(["git", *args], cwd=where, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as error:
            raise GitUnavailable(f"git did not answer ({type(error).__name__})") from error
        return done.stdout.strip() if done.returncode == 0 else None

    def worktrees(self) -> list[str]:
        """The folders of every worktree of this repository, this one included."""
        listing = self._git("worktree", "list", "--porcelain") or ""
        return [line.removeprefix("worktree ").strip() for line in listing.splitlines() if line.startswith("worktree ")]

    def exists(self, rel: str) -> bool:
        return os.path.exists(os.path.join(self.root, rel))

    def is_folder(self, rel: str) -> bool:
        return os.path.isdir(os.path.join(self.root, rel))

    def _in_time(self) -> None:
        """Raise TooSlowToJudge once this evaluation's glob work has run past GLOB_SECONDS."""
        started = self.__dict__.setdefault("_glob_started", time.monotonic())
        if time.monotonic() - started > GLOB_SECONDS:
            raise TooSlowToJudge(f"matching the globs took over {GLOB_SECONDS} seconds")

    def _walk(self, base: str) -> list[tuple[str, list[str], list[str]]]:
        """
        Every folder beneath `base` (from the root, itself included) with its child folders and files, build output
        and the tools' caches left out — walked once per evaluation and kept (once: each `**` glob walked the tree
        again, so 400 of them ran past the hook's time limit, and a hook past it does not block).
        """
        walks: dict[str, list[tuple[str, list[str], list[str]]]] = self.__dict__.setdefault("_walks", {})
        if base not in walks:
            found = []
            for folder, children, files in os.walk(os.path.join(self.root, base) if base else self.root):
                self._in_time()
                children[:] = [child for child in children if child.lower() not in UNWALKED_FOLDERS]
                here = os.path.relpath(folder, self.root).replace("\\", "/")
                found.append(("" if here == "." else here, list(children), files))
            walks[base] = found
        return walks[base]

    def _listing(self, base: str) -> list[str]:
        """The names in one folder (from the root), listed once per evaluation; none for a folder that is not one."""
        listings: dict[str, list[str]] = self.__dict__.setdefault("_names", {})
        if base not in listings:
            self._in_time()
            try:
                listings[base] = os.listdir(os.path.join(self.root, base))
            except (OSError, ValueError):
                listings[base] = []
        return listings[base]

    def holds(self, rel: str, name: str) -> bool:
        """Whether a file called `name` lies beneath `rel` (a folder, or a glob) — for a `**/<name>` rule path."""
        fixed = list(itertools.takewhile(lambda part: not any(char in part for char in "*?["),
                                         rel.rstrip("/").split("/")))
        for folder, _, files in self._walk("/".join(part for part in fixed if part not in ("", "."))):
            self._in_time()
            for file in files:
                if fnmatch.fnmatchcase(file.lower(), name.lower()):
                    found = posixpath.join(folder, file) if folder else file
                    if beneath(found, rel):
                        return True
        return False

    def matching(self, rel: str) -> list[str] | None:
        """
        The files the shell glob `rel` (from the root) matches now, segment by segment — `*` stays inside one
        folder, as the shell's does, and finds hidden names too, as PowerShell's does; `**` is any number of
        folders, none included, as bash's globstar reads it (once: it was read as one folder, so
        `src/**/<applied migration>` matched nothing). None past MAX_MATCHES: the caller then judges the glob
        by every rule path it could match.
        """
        found = [""]
        for part in rel.split("/"):
            following: list[str] = []
            for base in found:
                self._in_time()
                if part == "**":
                    following.append(base)
                    for here, children, _ in self._walk(base):
                        following += [here + "/" + child if here else child for child in children]
                        if len(following) > MAX_MATCHES:
                            return None
                    continue
                if not any(char in part for char in "*?["):
                    following.append(posixpath.join(base, part) if base else part)
                    continue
                following += [posixpath.join(base, name) if base else name for name in self._listing(base)
                              if fnmatch.fnmatchcase(name.lower(), part.replace("**", "*").lower())]
            if len(following) > MAX_MATCHES:
                return None
            found = list(dict.fromkeys(following))
        return [path for path in found if path and os.path.isfile(os.path.join(self.root, path))]

    def _base_listing(self, folder: str) -> set[str] | None:
        """
        Every file beneath `folder` on the base branch (or, without one, in the index), from one git call cached
        for this evaluation — once, asking git once per file a glob matched took seven seconds for the migrations.
        """
        cache: dict[str, set[str] | None] = self.__dict__.setdefault("_listings", {})
        if folder not in cache:
            base = rules().base_ref
            if "_has_base" not in self.__dict__:
                self.__dict__["_has_base"] = self._git("rev-parse", "--verify", "--quiet", base) is not None
            spec = ["--", folder] if folder else []
            listed = self._git("ls-tree", "-r", "-z", "--name-only", base, *spec) if self.__dict__["_has_base"] \
                else self._git("ls-files", "-z", *spec)
            cache[folder] = None if listed is None else {name for name in listed.split("\0") if name}
        return cache[folder]

    def on_base(self, rel: str) -> bool:
        """Whether the file already exists on the base branch (`base_ref` in factory.toml)."""
        listing = self._base_listing(posixpath.dirname(rel))
        return listing is not None and rel in listing

    def on_base_folder(self, folder: str) -> bool:
        """Whether the base branch holds any file beneath `folder` — a folder the team reviewed in, not one
        this session just made. Without a base branch, the index would be what this session staged: no."""
        return bool(self._base_listing(folder)) and bool(self.__dict__.get("_has_base"))

    def current_branch(self, cwd: str | None = None) -> str | None:
        return self._git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)

    def upstream_branch(self, cwd: str | None = None) -> str | None:
        upstream = self._git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", cwd=cwd)
        return upstream.split("/", 1)[1] if upstream and "/" in upstream else upstream

    def factory_view(self) -> FactoryView | None:
        """The open factory run on this branch, read through the referee; None when there is none."""
        if not hasattr(self, "_view"):
            self._view = load_factory_view(self.root)
        return self._view


@dataclass(frozen=True)
class FactoryView:
    run_file: str
    step: str
    gate: bool
    writes: tuple[str, ...]
    lock: frozenset[str]
    text: str
    driving_session: str | None = None
    driving_step: str | None = None
    escalated: bool = False
    # A run file the referee cannot read: why, and the paths held until it reads again.
    unreadable: str | None = None
    protected: tuple[str, ...] = ()
    unreadable_reason: str | None = None
    # Open runs held by other branches, but whose files are in this checkout: (path, text).
    # Their Records and locks hold here too — switching branch freed them (the third review).
    other_runs: tuple[tuple[str, str], ...] = ()
    # False when no open run belongs to this branch: no step, so no step scope.
    has_step: bool = True

    def run_texts(self) -> dict[str, str]:
        texts = dict(self.other_runs)
        if self.run_file:
            texts[self.run_file] = self.text
        return texts


def unreadable_view(root: str, factory, config) -> FactoryView | None:
    """
    A run file on the branch that does not parse. Dropping the run rules then would free every
    locked test and the Record at once (the re-review's MEDIUM-4), so the run files and the
    tests are held instead until a person repairs it.
    """
    from pathlib import Path
    folder = Path(root) / config.runs_dir
    if not folder.is_dir():
        return None
    for path in sorted(folder.iterdir()):
        if not factory.RUN_FILE_NAME.match(path.name):
            continue
        try:
            factory.parse_run(path.read_text(encoding="utf-8"), path)
        except (factory.FactoryError, OSError, ValueError):
            return FactoryView(
                run_file=f"{config.runs_dir}/{path.name}", step="unreadable", gate=False, writes=(),
                lock=locks_without_referee(root, config.runs_dir), text="", unreadable=f"{config.runs_dir}/{path.name}",
                protected=held_patterns(config.runs_dir, test_globs(factory, config)))
    return None


def test_globs(factory, config) -> tuple[str, ...]:
    """Every stack's test and lock globs (the referee's merged adapter with [[stacks]]); none when the config cannot say."""
    try:
        settings = factory.adapter(config)
    except (KeyError, TypeError, AttributeError, ValueError):
        settings = config.raw.get("adapter", {})
    return tuple(settings.get("test_files", [])) + tuple(settings.get("lock_files", []))


# What a test is called, and where tests live, in the stacks the kit installs into: .NET, JavaScript and
# TypeScript (Vitest, Jest, Playwright), Python, Go, Java. A held view cannot trust the configuration that
# says which files are this project's tests — it is held because that configuration or the referee broke —
# so it holds these as well as whatever globs could still be read. A literal `tests/*` held only a top-level
# folder: a React project's locked src/cart/Cart.test.tsx stayed writable (the kit audit, 2026-10-18).
# `*` spans folders in matches(), so every pattern reaches any depth. Holding a few files that are not tests
# while a person repairs the run state is the price of failing closed.
TEST_FILE_NAMES = (
    "tests/*", "test/*", "*/tests/*", "*/test/*", "__tests__/*", "*/__tests__/*", "*Tests/*",
    "*Tests.cs", "*Test.cs", "*.test.*", "*.spec.*", "test_*.py", "*/test_*.py", "*_test.py", "conftest.py",
    "*/conftest.py", "*_test.go", "*Test.java", "*Tests.java",
)


def held_patterns(runs_dir: str, tests: Iterable[str] = ()) -> tuple[str, ...]:
    """What a held view protects: the run files, the project's own test globs when known, and every test name."""
    return tuple(dict.fromkeys((f"{runs_dir}/*", *tests, *TEST_FILE_NAMES)))


def locks_without_referee(root: str, runs_dir: str) -> frozenset[str]:
    """
    Every path an open run in `runs_dir` has locked, read from the run files' Records without the referee —
    the held views know what was proven red even when factory.py or factory.toml is what broke. A run file
    whose Record does not parse adds nothing here; TEST_FILE_NAMES and the configured globs hold for it.
    """
    from pathlib import Path
    folder = Path(root) / runs_dir
    locked: set[str] = set()
    try:
        paths = sorted(folder.iterdir()) if folder.is_dir() else []
    except OSError:
        return frozenset()
    for path in paths:
        if not RUN_FILE_NAME.match(path.name):
            continue
        try:
            record = record_without_referee(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("status") != "open" \
                or "merge-gate" in (record.get("gates") or {}):
            continue
        lock = record.get("lock")
        if isinstance(lock, (dict, list)):
            locked.update(str(item) for item in lock)
    return frozenset(locked)


RUN_FILE_NAME = re.compile(r"^R-(\d{3})\.md$")  # factory.RUN_FILE_NAME


def record_without_referee(text: str):
    """factory.parse_record: the one Record fence under `## Record`, or None."""
    if text.count(RECORD_FENCE) != 1:
        return None
    heading = re.search(r"^## Record[ \t]*$", text, re.MULTILINE)
    start = text.find(RECORD_FENCE)
    if heading is None or start < heading.end():
        return None
    body = text.find("\n", start) + 1
    end = text.find("\n```", body)
    return json.loads(text[body:end]) if end >= 0 else None


def test_globs_without_referee(root: str) -> tuple[str, ...]:
    """The test and lock globs factory.toml names — `[adapter]`, or each `[[stacks]]` prefixed with its root —
    read with tomllib, without the referee; none when the file cannot be read."""
    import tomllib
    from pathlib import Path
    try:
        raw = tomllib.loads((Path(root) / "devtools" / "factory" / "factory.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    found: list[str] = []
    try:
        adapters = [("", raw.get("adapter") or {})] + [
            (str(stack.get("root", "")).strip("/"), stack.get("adapter") or {})
            for stack in raw.get("stacks") or [] if isinstance(stack, dict)]
        for prefix, settings in adapters:
            for key in ("test_files", "lock_files"):
                found += [f"{prefix}/{glob}" if prefix else str(glob) for glob in settings.get(key, [])]
    except (AttributeError, TypeError):
        return tuple(found)
    return tuple(found)


def held_view(why: str, runs_dir: str = "docs/factory/runs", tests: tuple[str, ...] = (),
              root: str | None = None) -> FactoryView:
    """
    The run state could not be read. "No run" would free every locked test and every Record (the
    harness review), so the run files and the tests are held until a person repairs what broke —
    with `root`, every path an open run locked as well (locks_without_referee).
    """
    return FactoryView(run_file="", step="unreadable", gate=False, writes=(),
                       lock=locks_without_referee(root, runs_dir) if root else frozenset(), text="",
                       unreadable=runs_dir, unreadable_reason=why, protected=held_patterns(runs_dir, tests))


def holds_no_runs(root, factory) -> bool:
    """
    A checkout whose factory.toml defines no steps and that has no runs folder: the tools without the
    factory, as `dev` carries them. It can hold no run, so it locks nothing. Judging another worktree
    (F-12) loaded such a checkout's config with this referee, failed on the missing steps and refused
    every test change there. A config that does not parse, or a runs folder beside a config
    without steps, still fails closed in load_factory_view.
    """
    import tomllib
    try:
        raw = tomllib.loads((root / factory.CONFIG_RELATIVE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    runs = raw.get("runs_dir", "docs/factory/runs")
    return "steps" not in raw and not (root / runs).exists()


def runs_dir_without_referee(root: str) -> str:
    """The runs folder factory.toml names, read without the referee; the default when it cannot be read."""
    import tomllib
    from pathlib import Path
    try:
        raw = tomllib.loads((Path(root) / "devtools" / "factory" / "factory.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "docs/factory/runs"
    return str(raw.get("runs_dir", "docs/factory/runs"))


def load_factory_view(root: str) -> FactoryView | None:
    factory = referee()
    if factory is None:
        # A checkout with run files and no working referee: "no run" would free every locked test and every
        # Record (the 15 Oct review's #5), so they are held until a person repairs factory.py.
        from pathlib import Path
        runs = runs_dir_without_referee(root)
        if (Path(root) / runs).is_dir():
            return held_view("the referee (devtools/factory/factory.py) could not be imported, so which tests a run "
                             "has locked cannot be known; a person repairs it", runs, test_globs_without_referee(root),
                             root)
        return None
    from pathlib import Path
    if holds_no_runs(Path(root), factory):
        return None
    try:
        config = factory.load_config(Path(root))
    except (factory.FactoryError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        # The file may still parse as TOML: its runs folder and test globs, read without the referee, are held.
        return held_view(f"the referee could not load factory.toml ({type(error).__name__}), so which tests a run "
                         "has locked cannot be known; a person repairs factory.toml",
                         runs_dir_without_referee(root), test_globs_without_referee(root), root)
    # A run file that does not parse must not free what it held.
    tests = test_globs(factory, config)
    try:
        broken = unreadable_view(root, factory, config)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        broken = held_view(f"the run files could not be read ({type(error).__name__})", config.runs_dir, tests, root)
    if broken is not None:
        return broken
    # Every open run in this checkout, whatever branch it names: a lock or a Record that holds only
    # on its own branch was freed by `git checkout -b tmp` (the third review's HIGH #1). A run past
    # its merge gate holds nothing — its tests are the team's again.
    try:
        runs = factory.load_runs(Path(root), config)
        branch = factory.current_branch(Path(root))
    except (factory.FactoryError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return held_view(f"the open runs could not be read ({type(error).__name__})", config.runs_dir, tests, root)
    open_runs = [run for run in runs if run.path is not None and run.record.get("status") == "open"
                 and "merge-gate" not in (run.record.get("gates") or {})]
    if not open_runs:
        return None

    def rel(run) -> str:
        return Path(run.path).resolve().relative_to(Path(root).resolve()).as_posix()

    lock = frozenset(path for run in open_runs for path in run.record.get("lock", {}))
    mine = [run for run in open_runs if run.record.get("branch") == branch]
    others = tuple((rel(run), Path(run.path).read_text(encoding="utf-8")) for run in open_runs if run not in mine)
    if len(mine) > 1:
        names = ", ".join(rel(run) for run in mine)
        return FactoryView(
            run_file="", step="unreadable", gate=False, writes=(), lock=lock, text="", unreadable=names,
            protected=held_patterns(config.runs_dir, tests),
            unreadable_reason=f"{len(mine)} open factory runs claim branch {branch} ({names}), so which "
                              "one's step and locks apply cannot be told")
    if not mine:
        return FactoryView(run_file="", step="elsewhere", gate=False, writes=(), lock=lock, text="",
                           other_runs=others, has_step=False)
    run = mine[0]
    try:
        step = factory.current_step(run.record, config)
    except (factory.FactoryError, KeyError, TypeError, AttributeError):
        step = None
    return FactoryView(
        run_file=rel(run),
        step=step.name if step else "done",
        gate=bool(step and step.gate),
        writes=step.writes if step else (),
        lock=lock,
        text=Path(run.path).read_text(encoding="utf-8"),
        driving_session=(run.record.get("driving") or {}).get("session"),
        driving_step=(run.record.get("driving") or {}).get("step"),
        escalated=bool(run.section_text("Escalation")),
        other_runs=others,
    )


MAX_STOP_BLOCKS = 3


def stop_decision(payload: dict, repo: Repo) -> dict | None:
    """
    Stop hook. A session `factory drive` is driving through a step may not end until the
    referee has passed that step or the session has escalated — "it stops early" is the
    failure the factory exists to prevent. Three refusals per step at most, so a session
    that truly cannot continue ends and the conductor reports it.
    """
    view = repo.factory_view()
    session = payload.get("session_id")
    if view is None or not view.driving_session or session != view.driving_session:
        return None
    if view.escalated or view.step != view.driving_step:
        return None
    counter = os.path.join(repo.root, ".factory", "stops", f"{session}-{view.driving_step}")
    try:
        with open(counter, encoding="utf-8") as handle:
            count = int(handle.read().strip() or 0)
    except (OSError, ValueError):
        count = 0
    if count >= MAX_STOP_BLOCKS:
        return None
    os.makedirs(os.path.dirname(counter), exist_ok=True)
    with open(counter, "w", encoding="utf-8") as handle:
        handle.write(str(count + 1))
    return {"decision": "block", "reason": (
        f"guard: step {view.driving_step} of this factory run has not passed. Run "
        "`python3 devtools/factory/factory.py check` and fix what it reports. If it cannot pass, "
        f"add an `## Escalation` section to {view.run_file} saying what blocks you and what a "
        "person must decide, then stop.")}


# ---------------------------------------------------------------- paths

def to_native(path: str) -> str:
    """Git Bash writes D:\\x as /d/x; turn it back so relpath can compare it."""
    if os.name == "nt":
        match = re.match(r"^/([a-zA-Z])(/|$)(.*)", path)
        if match:
            return f"{match.group(1).upper()}:/{match.group(3)}"
    return path


def relative_to_root(path: str, cwd: str, root: str) -> str | None:
    """The path relative to the repository root with forward slashes, or None outside it."""
    if not path:
        return None
    path = to_native(path.strip().strip("'\""))
    absolute = path if os.path.isabs(path) else os.path.join(cwd, path)
    try:
        # realpath: Windows names one folder two ways (a short 8.3 name like ALEXAN~1 and the full one), and
        # comparing them unresolved put a file inside the repository outside it — found by the kit's selftest.
        rel = os.path.relpath(os.path.realpath(absolute), os.path.realpath(root))
    except ValueError:
        return None
    rel = rel.replace("\\", "/")
    if rel == "." or rel.startswith("../") or rel == "..":
        return None
    return rel


def held_under(rel: str, patterns: Iterable[str], repo: Repo | None = None) -> str | None:
    """
    The first rule path a command acting on `rel` whole reaches, or None: a folder reaches every rule
    path beneath it (`rm -rf requirements` removes every snapshot), the root (".") reaches them all,
    and a glob reaches what it can match, segment by segment (`requirements/*`, `req*` and `*` reach
    the snapshots; `*.log` and `src/*/bin` do not). A `**/<name>` rule path could lie under any folder,
    so it is reached only where such a file is found (`rm -rf tests` reaches tests/CLAUDE.md;
    `rm -rf bin` reaches nothing).
    """
    if rel == ".":
        return next((str(pattern) for pattern in patterns), None)
    wanted = rel.rstrip("/").lower().split("/")
    globbed = any(char in rel for char in "*?[")
    for pattern in patterns:
        if str(pattern).startswith("**/"):
            if repo is not None and repo.holds(rel, str(pattern)[3:]):
                return str(pattern)
            continue
        parts = str(pattern).lower().split("/")
        if reaches(parts, wanted, globbed):
            return str(pattern)
    return None


# Folders a `**/<name>` search never looks in: build output and the tools' own caches.
UNWALKED_FOLDERS = frozenset({".git", "bin", "obj", "node_modules", "__pycache__", "graphify-out", ".factory"})


def meet(first: list[str], second: list[str]) -> bool:
    """
    Whether two paths, each split into segments and either holding wildcards, can name the same path: segment
    by segment, either side's wildcard matching the other's text, and a `**` segment on either side standing for
    any number of folders, none included — as bash's globstar and the rule paths read it.
    """
    first, second = [part.lower() for part in first], [part.lower() for part in second]
    seen: dict[tuple[int, int], bool] = {}

    def from_(i: int, j: int) -> bool:
        if (i, j) in seen:
            return seen[(i, j)]
        if i == len(first) and j == len(second):
            result = True
        elif i < len(first) and first[i] == "**":
            result = from_(i + 1, j) or (j < len(second) and from_(i, j + 1))
        elif j < len(second) and second[j] == "**":
            result = from_(i, j + 1) or (i < len(first) and from_(i + 1, j))
        elif i == len(first) or j == len(second):
            result = False
        else:
            result = (fnmatch.fnmatchcase(first[i], second[j]) or fnmatch.fnmatchcase(second[j], first[i])) \
                and from_(i + 1, j + 1)
        seen[(i, j)] = result
        return result

    return from_(0, 0)


def beneath(found: str, rel: str) -> bool:
    """Whether the file `found` lies inside `rel` — a folder, or a glob matched segment by segment — or is what the
    glob itself matches (`rm -rf tests/CLAUDE.m?`)."""
    wanted = rel.rstrip("/").split("/")
    return meet(wanted + ["**"], found.split("/")) if any(c in rel for c in "*?[") else \
        meet(wanted + ["*", "**"], found.split("/"))


def reaches(parts: list[str], wanted: list[str], globbed: bool) -> bool:
    """Whether the rule path `parts` lies beneath the path `wanted` — or, for a glob, is matched by it."""
    return meet(wanted + (["**"] if globbed else ["*", "**"]), parts)


def whole_target(target: str, cwd: str, root: str, pathspec: bool = False) -> str | None:
    """
    What a command acting on `target` whole reaches, relative to the root: "." for the root itself
    (`.` there, `:/`, its absolute path), and a glob kept as a glob under the folder it starts in
    (`requirements/*`, `src/*/bin`). None for an empty target, a git exclusion, or one outside the
    repository. `pathspec`: a git pathspec, whose `*` crosses `/` (`'*.md'` is every Markdown file in
    the tree), so a glob there is the whole folder it starts in; its magic (`:(top)`, `:(icase)`) is read.
    """
    text = target.strip().strip("'\"")
    if not text:
        return None
    if pathspec and text.startswith(":(") and ")" in text:
        magic, text = text[2:text.index(")")].lower(), text[text.index(")") + 1:]
        if "exclude" in magic.split(","):
            return None
        if "top" in magic.split(","):
            text = ":/" + text
    elif pathspec and text[:2] in (":!", ":^"):  # an exclusion restores nothing
        return None
    if text.startswith(":/"):  # git's root-relative pathspec
        text = os.path.join(root, text[2:])
    text = posixpath.normpath(to_native(text).replace("\\", "/"))  # `src/*/../../req*` is `req*`
    wildcard = next((index for index, char in enumerate(text) if char in "*?["), None)
    base, pattern = text, ""
    if wildcard is not None:
        cut = text.rfind("/", 0, wildcard)
        base, pattern = (text[:cut], text[cut + 1:]) if cut >= 0 else ("", text)
        if pathspec:
            pattern = ""
    path = base.rstrip("/") or "."
    absolute = path if os.path.isabs(path) else os.path.join(cwd, path)
    try:
        rel = os.path.relpath(os.path.realpath(absolute), os.path.realpath(root)).replace("\\", "/")
    except ValueError:
        return None
    if rel == ".." or rel.startswith("../"):
        return None
    return (pattern if rel == "." else f"{rel}/{pattern}") if pattern else rel


def path_findings(rel: str, repo: Repo, through_shell: bool = False, whole_folder: bool = False) -> list[Finding]:
    """
    The rules for one path: an open run's first, then the repository's. `whole_folder`: the command
    removes, moves or restores the path whole, so a folder is judged by what lies beneath it as well.
    """
    if rel == "." and not whole_folder:
        return []
    view = repo.factory_view()
    # A glob reaches what it can match, as a folder reaches what lies beneath it: `sed -i … tests/*.cs`
    # writes a locked test as surely as `rm -rf tests` does.
    reaching = whole_folder or any(char in rel for char in "*?[")
    if view is not None and view.unreadable:
        if matches(rel, view.protected):
            why = view.unreadable_reason or (
                f"{view.unreadable} is a factory run file the referee cannot read, so what its Record holds — "
                "the test locks, the gates — cannot be checked")
            return [Finding(
                "run-unreadable", ALWAYS,
                f"{why}. Nothing under the runs or tests changes until a person repairs it from their own "
                "terminal.", rel)]
        if rel in view.lock or (reaching and held_under(rel, view.lock)):
            return [Finding("test-locked", ALWAYS, f"{rel} is locked by an open factory run.", rel)]
        return static_path_findings(rel, repo, whole_folder)
    if view is not None:
        if rel in view.lock or (reaching and held_under(rel, view.lock)):
            return [Finding(
                "test-locked", ALWAYS,
                f"{rel} was proven red and locked for an open run. Make the code pass it; a person can release "
                "it with `factory unlock`.", rel)]
        if through_shell and (rel in view.run_texts() or (reaching and held_under(rel, view.run_texts()))):
            return [Finding(
                "run-record", ALWAYS,
                f"{rel} is a factory run file. Change its sections with the file tools; its Record is the "
                "referee's.", rel)]
    return static_path_findings(rel, repo, whole_folder) + step_scope_findings(rel, view)


def step_scope_findings(rel: str, view: FactoryView | None) -> list[Finding]:
    if view is None or not view.has_step or rel in view.run_texts() or rel.startswith(".factory/"):
        return []
    if view.gate:
        return [Finding(
            "step-scope", ASK,
            f"The run is waiting at {view.step} for a person's approval; nothing should change until then.", rel)]
    if any(fnmatch.fnmatch(rel, pattern) for pattern in view.writes):
        return []
    allowed = ", ".join(view.writes) or "only the run file"
    return [Finding(
        "step-scope", ASK,
        f"The run is at step {view.step}, which may write {allowed} — not {rel}. A person confirms.", rel)]


def static_path_findings(rel: str, repo: Repo, whole_folder: bool = False) -> list[Finding]:
    """
    The repository's own rules for one path. `whole_folder`: the command removes, moves or restores the
    path whole, so a folder is judged by the protected paths beneath it as well (`rm -rf requirements`,
    `git checkout <rev> -- .claude`), not only by its own name.
    """
    r = rules()

    def under(patterns: Iterable[str]) -> str | None:
        return held_under(rel, patterns, repo) if whole_folder else None

    if rel == "." and not whole_folder:
        return []
    if not whole_folder and any(char in rel for char in "*?["):
        return glob_findings(rel, repo)

    if (matches(rel, r.read_only) and not matches(rel, r.read_only_maintained) and repo.exists(rel)) or under(r.read_only):
        return [Finding("read-only", DENY, f"{rel} {r.read_only_reason}", rel)]
    if matches(rel, r.never_by_hand) or under(r.never_by_hand):
        return [Finding("never-by-hand", DENY, f"{rel} {r.never_by_hand_reason}", rel)]
    if (matches(rel, r.applied) and repo.on_base(rel)) or under(r.applied):
        return [Finding("applied-file", DENY, f"{rel} {r.applied_reason}", rel)]
    generator = next((command for path, command in r.generated.items() if rel.lower() == path.lower()), None)
    inside = under(r.generated)
    if generator or inside:
        return [Finding(
            "generated-file", DENY,
            f"{inside or rel} is generated. Regenerate it with `{generator or r.generated[inside]}`; never hand-edit it.",
            rel)]
    if (matches(rel, r.ask) or under(r.ask)) and not matches(rel, r.ask_except) and "__pycache__" not in rel.lower():
        return [Finding(
            "guard-integrity", ASK,
            f"{rel} steers the AI (the guard, the tools, the checks, git hooks, CI, the instructions or skills). "
            "A person confirms every change to it.", rel)]
    return []


# Past this many files a glob is judged by every rule path it could match, not file by file: asking git
# about each would outrun the hook's time limit.
MAX_MATCHES = 1000
# The seconds one evaluation may spend matching globs against the tree. Claude Code's hook time limit is 30
# seconds and a hook past it does not block, so past this the guard refuses (`hidden-command`) rather than run on.
GLOB_SECONDS = 10


def could_match(rel: str, pattern: str) -> bool:
    """Whether the glob `rel` and the rule path `pattern` can name the same file: segment by segment, either
    side's wildcard matching the other's text, and a `**` on either side holding any number of folders."""
    return meet(rel.split("/"), pattern.split("/"))


def glob_findings(rel: str, repo: Repo, unknown: bool = False) -> list[Finding]:
    """
    A write to a glob (`sed -i x Migrations/2026*.cs`, `cp x requirements/fsd/*V16.pdf`) writes whatever it
    matches, so it gets the strictest finding any match would get. Each file it matches now is judged as
    written by name — the read-only and applied-file rules hold only for files that exist — and the rules
    that hold for any file (never by hand, generated, steering the AI) also for every rule path it could
    match. A `**/<name>` rule path is found only among the matches. Past MAX_MATCHES, and for `unknown` (a
    name part of which is built when it runs, `out-$(date +%s).txt`), every rule path it could match counts,
    `**/<name>` ones included.
    """
    r = rules()
    found = None if unknown else repo.matching(rel)
    findings = [finding for match in found or [] for finding in path_findings(match, repo)]

    def reached(patterns: Iterable[str]) -> str | None:
        return next((str(p) for p in patterns
                     if (found is None or not str(p).startswith("**/")) and could_match(rel, str(p))), None)

    if found is None:  # too many to judge one by one: every rule path it could match counts
        if reached(r.read_only):
            findings.append(Finding("read-only", DENY, f"{rel} {r.read_only_reason}", rel))
        if reached(r.applied):
            findings.append(Finding("applied-file", DENY, f"{rel} {r.applied_reason}", rel))
    if reached(r.never_by_hand):
        findings.append(Finding("never-by-hand", DENY, f"{rel} {r.never_by_hand_reason}", rel))
    generated = reached(r.generated)
    if generated:
        findings.append(Finding(
            "generated-file", DENY,
            f"{rel} can match {generated}, which is generated. Regenerate it with `{r.generated[generated]}`; "
            "never hand-edit it.", rel))
    if reached(r.ask) and "__pycache__" not in rel.lower():
        findings.append(Finding(
            "guard-integrity", ASK,
            f"{rel} can match a file that steers the AI (the guard, the tools, the checks, git hooks, CI, the "
            "instructions or skills). A person confirms every change to it.", rel))
    # Every rule the strictest level holds, once each — not the first finding met, which depended on the
    # order the filesystem listed the folder: CI's Linux named the snapshot first, Windows the migration.
    strictest = [finding for finding in findings if finding.level == DENY] or findings
    return list({finding.rule: finding for finding in reversed(strictest)}.values())[::-1]


# ---------------------------------------------------------------- content

def secret_findings(rel: str | None, text: str) -> list[Finding]:
    findings = [
        Finding("secret-in-content", DENY,
                f"The content being written holds what looks like {label}. Secrets come from user "
                "secrets, the server's .env or Key Vault — never a file.", rel)
        for label, pattern in SECRET_PATTERNS if pattern.search(text)
    ]
    if rel and personal_home_path(text):
        findings.append(Finding(
            "personal-path-in-content", DENY,
            f"{rel}: the content holds a path inside someone's home folder, which names them and their machine. "
            "Write `~` or `<you>` instead (CLAUDE.md, Private settings).", rel))
    if rel and rules().appsettings_secrets and APPSETTINGS_NAME.search(rel) and not APPSETTINGS_EXEMPT.search(rel):
        for value in JWT_KEY_VALUE.findall(text):
            if not value.upper().startswith("REPLACE") and "not-a-secret" not in value:
                findings.append(Finding(
                    "appsettings-secret", DENY,
                    f"{rel}: Jwt:Key must start with REPLACE or say not-a-secret.", rel))
                break
        for value in CONNECTION_PASSWORD.findall(text):
            if value and value != "postgres" and not value.upper().startswith("REPLACE") and value[0] not in "${":
                findings.append(Finding(
                    "appsettings-secret", DENY,
                    f"{rel}: a connection-string Password must be postgres or a REPLACE placeholder.", rel))
                break
        for value in JSON_PASSWORD.findall(text):
            if value and not value.upper().startswith("REPLACE"):
                findings.append(Finding(
                    "appsettings-secret", DENY,
                    f"{rel}: a Password setting must be a REPLACE placeholder.", rel))
                break
    return findings


def written_text(tool: str, tool_input: dict) -> str:
    if tool == "Write":
        return str(tool_input.get("content", ""))
    if tool == "Edit":
        return str(tool_input.get("new_string", ""))
    if tool == "MultiEdit":
        return "\n".join(str(edit.get("new_string", "")) for edit in tool_input.get("edits", []) or [])
    if tool == "NotebookEdit":
        return str(tool_input.get("new_source", ""))
    return ""


def referee():
    """The referee module, imported the way load_factory_view does; None if it cannot be."""
    parent = os.path.dirname(HERE)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    try:
        import factory
    except Exception:  # noqa: BLE001 — a referee that does not compile or fails as it loads is as missing as an absent one
        return None
    return factory


def text_after(tool: str, tool_input: dict, current: str) -> str | None:
    """
    The run file as it would read after the call, or None when the call would fail anyway
    (an old_string that is absent, or not unique without replace_all) or cannot be worked out.
    """
    if tool == "Write":
        return str(tool_input.get("content", ""))
    if tool not in ("Edit", "MultiEdit"):
        return None
    edits = (tool_input.get("edits") or []) if tool == "MultiEdit" else [tool_input]
    text = current
    for edit in edits:
        old, new = str(edit.get("old_string", "")), str(edit.get("new_string", ""))
        count = text.count(old) if old else 0
        if count == 0 or (count > 1 and not edit.get("replace_all")):
            return None
        text = text.replace(old, new) if edit.get("replace_all") else text.replace(old, new, 1)
    return text


def run_file_findings(tool: str, tool_input: dict, rel: str, current: str) -> list[Finding]:
    """
    Judged on the file as it would be after the call, not on the strings the call carries:
    two edits, each harmless-looking, planted a second Record fence and then armed it (the
    re-review's HIGH-1). The Record, the title and the one fence must come out unchanged,
    and the file must still be one the referee can read. A call whose result cannot be
    worked out is refused: Claude Code matches curly quotes to straight ones, so an
    old_string absent here can still land in the Record (the third review's HIGH #2).
    """
    def refuse(why: str) -> list[Finding]:
        return [Finding("run-record", ALWAYS,
                        f"That {why} in {rel}, which only the referee writes. Edit the session's sections; "
                        "the referee updates the rest through `factory` commands.", rel)]

    after = text_after(tool, tool_input, current)
    factory = referee()
    if factory is None:
        return refuse("cannot be checked — the referee cannot be loaded — so it is a change")
    if after is None:
        return refuse("edit's result cannot be worked out exactly (its old_string is not in the file once, "
                      "character for character), so it is a change")
    if after.count(RECORD_FENCE) != 1:
        return refuse("would leave other than one Record block")
    if after.replace("\r\n", "\n").split("\n", 1)[0] != current.replace("\r\n", "\n").split("\n", 1)[0]:
        return refuse("changes the title line")
    try:
        factory.parse_run(after)
    except factory.FactoryError:
        return refuse("would leave a file the referee cannot read")
    if factory.parse_record(after) != factory.parse_record(current):
        return refuse("changes the Record")
    # The header, Checks and Gates are drawn from the Record; an edit there shows a forged
    # approval or status in the PR's diff even though the Record is untouched (the branch review).
    if factory.referee_parts(after) != factory.referee_parts(current):
        return refuse("changes the header, Checks or Gates")
    return []


def other_worktree(target: str, cwd: str, repo: Repo) -> Repo | None:
    """
    The other worktree of this repository that holds `target`, as a Repo of its own: its open runs and
    their locks are its own. A session in one checkout edited a locked test in another, unjudged, because
    the path lay outside its own root (the dry run's F-12).
    """
    if not target or relative_to_root(target, cwd, repo.root):
        return None
    for other in repo.worktrees():
        if other and os.path.normcase(os.path.realpath(other)) != os.path.normcase(os.path.realpath(repo.root)) \
                and relative_to_root(target, cwd, other):
            return Repo(other)
    return None


def file_tool_findings(tool: str, tool_input: dict, cwd: str, repo: Repo) -> list[Finding]:
    target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    elsewhere = other_worktree(str(target), cwd, repo)
    if elsewhere is not None:
        return file_tool_findings(tool, tool_input, cwd, elsewhere)
    rel = relative_to_root(str(target), cwd, repo.root)
    findings = path_findings(rel, repo) if rel else claude_settings_findings(str(target), cwd)
    view = repo.factory_view() if rel else None
    if rel and new_run_file(rel, repo) and not (view is not None and rel in view.run_texts()):
        findings.append(Finding(
            "run-record", ALWAYS,
            f"{rel} would be a new factory run file. Only `factory start` opens a run; a second open run on "
            "a branch would leave which one holds unclear.", rel))
    elif view is not None and not view.unreadable and rel in view.run_texts():
        findings += run_file_findings(tool, tool_input, rel, view.run_texts()[rel])
    return findings + git_config_file_findings(str(target), cwd) + secret_findings(rel, written_text(tool, tool_input))


# git's own configuration files: an `include.path`, an alias or a `core.fsmonitor` written there runs a
# command no later call shows.
GIT_CONFIG_FILE = re.compile(r"(^|/)(\.git/(config|config\.worktree|worktrees/[^/]+/config(\.worktree)?)|\.gitconfig|"
                             r"\.config/git/config)$", re.IGNORECASE)


def absolute_path(target: str, cwd: str) -> str:
    """`target` as an absolute path with forward slashes: `~`, `$HOME` and their kin expanded, `..` resolved."""
    text = to_native(expand_home(target.strip().strip("'\""))).replace("\\", "/")
    return posixpath.normpath(text if os.path.isabs(text) else posixpath.join(cwd.replace("\\", "/"), text))


def git_config_file_findings(target: str, cwd: str) -> list[Finding]:
    if target.strip() and GIT_CONFIG_FILE.search(absolute_path(target, cwd)):
        return [hidden("This writes git's own configuration, where an include, an alias or a command setting "
                       "would hide what later git commands run.")]
    return []


# Claude Code's own settings outside the repository. Any of them can switch the guard off for every
# session (`disableAllHooks`, a hook removed, an `env` that sets FACTORY_GUARD_MODE), so a person
# confirms every change. Inside the repository, `[guard] ask` covers .claude/settings*.json.
CLAUDE_SETTINGS = frozenset({"settings.json", "settings.local.json"})
CLAUDE_STATE = frozenset({".claude.json", "managed-settings.json"})
CLAUDE_CONFIG_DIR_FORMS = ("$env:CLAUDE_CONFIG_DIR", "%CLAUDE_CONFIG_DIR%", "${CLAUDE_CONFIG_DIR}", "$CLAUDE_CONFIG_DIR")


def expand_known(text: str) -> str:
    """`text` with the home folder and $CLAUDE_CONFIG_DIR, in any shell's spelling, written out."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "")
    text = text.strip().strip("'\"")
    for form in CLAUDE_CONFIG_DIR_FORMS:
        text = re.sub(re.escape(form), (configured or "~/.claude").replace("\\", "/"), text, flags=re.IGNORECASE)
    return to_native(expand_home(text)).replace("\\", "/")


def is_claude_settings(target: str, cwd: str, folder_too: bool = False) -> bool:
    """`~/.claude/settings.json`, `settings.local.json`, `~/.claude.json`, their $CLAUDE_CONFIG_DIR
    equivalents, and the machine's managed-settings.json. `folder_too`: the settings folder itself as
    well — what is copied, moved or linked into it, or removed with it, lands on its settings."""
    text = expand_known(target)
    if not text:
        return False
    if isinstance(cwd, UnknownCwd) and not os.path.isabs(text):
        # From a folder the guard cannot name, `settings.json` could be the one in ~/.claude.
        name = posixpath.basename(posixpath.normpath(text)).lower()
        return name in CLAUDE_SETTINGS or name in CLAUDE_STATE or (folder_too and name.startswith(".claude"))
    absolute = posixpath.normpath(text if os.path.isabs(text) else posixpath.join(cwd.replace("\\", "/"), text)).lower()
    name, folder = posixpath.basename(absolute), posixpath.dirname(absolute)
    if name in CLAUDE_STATE:
        return True
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "")
    config_dir = posixpath.normpath(to_native(configured).replace("\\", "/")).lower() if configured else ""
    if folder_too and ((name.startswith(".claude") and not name.endswith(".json")) or absolute == config_dir):
        return True
    return name in CLAUDE_SETTINGS and (posixpath.basename(folder).startswith(".claude") or folder == config_dir)


def claude_settings_findings(target: str, cwd: str, folder_too: bool = False) -> list[Finding]:
    if not is_claude_settings(target, cwd, folder_too):
        return []
    return [Finding("claude-settings", ASK,
                    "This changes Claude Code's own settings, which can switch the guard off for every session. "
                    "A person confirms every change to them.", None)]


def new_run_file(rel: str, repo: Repo) -> bool:
    """A file tool creating R-nnn.md in the runs folder — `factory start`'s job, never the session's."""
    folder, _, name = rel.rpartition("/")
    return (folder.lower() == rules().runs_dir.lower() and re.fullmatch(r"R-\d{3}\.md", name) is not None
            and not repo.exists(rel))


# ---------------------------------------------------------------- shell
#
# The guard reads a command the way bash or PowerShell will, or refuses it. Each stage below
# models one part of the shells' grammar; what a stage does not model it refuses as `hidden-command`
# rather than guess: a quote that never closes, bash's `$"…"`, a brace expansion, a `case` inside `$( )`.
#
#   split_heredocs       comments out, heredoc and here-string bodies out — quote-aware, line by line
#   lift_substitutions   every `$( … )`, backtick and process substitution out, each judged on its own
#   normalize            every quoted word rewritten in one form shlex reads as the shell does
#   tokenize             shlex, with no comment rule of its own

# A command longer than this is refused unread: judging it could outrun the hook's 30-second timeout.
MAX_COMMAND = 64 * 1024

# Characters a shell reads as text — quoted or escaped — that would otherwise be syntax: a quoted `;`
# separates nothing and a single-quoted `$` expands nothing. normalize() writes them as these stand-ins,
# so the words keep the difference; unmark() turns them back.
LITERAL = {char: chr(0xE000 + index) for index, char in enumerate("$;|&()<>")}
# Written before a `$` inside double quotes: what that variable holds stays one word, and is not globbed.
DQ = chr(0xE010)
UNLITERAL = str.maketrans({**{mark: char for char, mark in LITERAL.items()}, DQ: None})
# A glob character the shell reads as text (a quoted variable's value: `rm -rf "$X"` with `X='req*'`), and text
# built when it runs inside a write target, judged as a wildcard. Neither is ever turned back.
TEXT_GLOB = str.maketrans({"*": chr(0xE011), "?": chr(0xE012), "[": chr(0xE013)})
RUN_TIME = chr(0xE014)
GUARD_MARKS = frozenset({*LITERAL.values(), DQ, chr(0xE011), chr(0xE012), chr(0xE013), RUN_TIME})


def mark(char: str) -> str:
    return LITERAL.get(char, char)


def unmark(text: str) -> str:
    return text.translate(UNLITERAL)


# PowerShell reads typographic quotes as quotes and typographic dashes as `-`.
POWERSHELL_LOOKALIKES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'",
                                       "“": '"', "”": '"', "„": '"',
                                       "–": "-", "—": "-", "―": "-"})

HEREDOC_OPENER = re.compile(r"<<(-?)[ \t]*(\\?)(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\3")


class Unparsed(Exception):
    """The guard cannot tell where a command starts or ends; it refuses rather than guess."""


class _Reading:
    """Where a reading of a command stands, carried from line to line: inside which quote, and inside
    which `$( … )`, backtick or `$(( … ))`."""

    def __init__(self) -> None:
        self.quote = ""  # "", "'", '"', "$'" (bash's ANSI-C quoting) or "<#" (a PowerShell block comment)
        self.depth = 0  # `(` open in the current context
        self.stack: list[tuple[str, int, str]] = []  # each enclosing context: its quote, its depth, its closer


def scan_line(line: str, reading: _Reading, powershell: bool) -> tuple[str, list[tuple[str, bool]], bool | None]:
    """
    One line of a command, read as the shell reads it: the line without its comments, the heredocs it
    opens (each terminator, and whether the body expands), and whether it opens a PowerShell here-string
    (and whether that expands). A `<<` or `#` inside quotes is text; one inside `$( … )` is not.
    """
    out: list[str] = []
    openers: list[tuple[str, bool]] = []
    escape = "`" if powershell else "\\"
    word_start = " \t;&|(){}" if powershell else " \t;&|()<>"
    index, size = 0, len(line)
    while index < size:
        char, quote = line[index], reading.quote
        if quote == "<#":
            end = line.find("#>", index)
            index = size if end < 0 else end + 2
            reading.quote = "" if end >= 0 else quote
            out.append(" ")
            continue
        if quote == "'":
            reading.quote = "" if char == "'" else quote
            out.append(char)
            index += 1
            continue
        if quote == "$'":
            if char == "\\":
                out.append(line[index:index + 2])
                index += 2
                continue
            reading.quote = "" if char == "'" else quote
            out.append(char)
            index += 1
            continue
        if char == escape:
            out.append(line[index:index + 2])
            index += 2
            continue
        closer = reading.stack[-1][2] if reading.stack else ""
        if quote == '"':
            if char == '"':
                reading.quote = ""
            elif char == "$" and line.startswith("(", index + 1):
                reading.stack.append((quote, reading.depth, ")"))
                reading.quote, reading.depth = "", 0
                out.append("$(")
                index += 2
                continue
            elif char == "`" and not powershell:
                reading.stack.append((quote, reading.depth, "`"))
                reading.quote, reading.depth = "", 0
            out.append(char)
            index += 1
            continue
        if char == "#" and (index == 0 or line[index - 1] in word_start) and not line.endswith("${", 0, index):
            break  # a comment, to the end of the line
        if powershell and line.startswith("<#", index):
            reading.quote = "<#"
            out.append(" ")
            index += 2
            continue
        if powershell and char == "@" and line[index + 1:index + 2] in ("'", '"') and not line[index + 2:].strip():
            out.append("''")  # the here-string's body is taken out; this word stands for it
            return "".join(out), openers, line[index + 1] == '"'
        if char == "'":
            reading.quote = "'"
        elif char == '"':
            reading.quote = '"'
        elif char == "$" and not powershell and line.startswith("'", index + 1):
            reading.quote = "$'"
            out.append("$'")
            index += 2
            continue
        elif not powershell and (line.startswith("$((", index) or (line.startswith("((", index) and closer != "))")):
            width = 3 if char == "$" else 2
            reading.stack.append((quote, reading.depth, "))"))
            reading.depth = 0
            out.append(line[index:index + width])
            index += width
            continue
        elif (char == "$" or (char in "<>" and not powershell)) and line.startswith("(", index + 1):
            reading.stack.append((quote, reading.depth, ")"))
            reading.depth = 0
            out.append(line[index:index + 2])
            index += 2
            continue
        elif char == "(":
            reading.depth += 1
        elif char == ")":
            if reading.depth:
                reading.depth -= 1
            elif closer == "))" and line.startswith("))", index):
                reading.quote, reading.depth, _ = reading.stack.pop()
                out.append("))")
                index += 2
                continue
            elif closer == ")":
                reading.quote, reading.depth, _ = reading.stack.pop()
        elif char == "`" and not powershell:
            if closer == "`":
                reading.quote, reading.depth, _ = reading.stack.pop()
            else:
                reading.stack.append((quote, reading.depth, "`"))
                reading.depth = 0
        elif not powershell and line.startswith("<<<", index):
            out.append("<<<")
            index += 3
            continue
        elif not powershell and line.startswith("<<", index) and closer != "))":
            opener = HEREDOC_OPENER.match(line, index)
            if opener:
                openers.append((opener.group(4), not (opener.group(2) or opener.group(3))))
                out.append(opener.group(0))
                index = opener.end()
                continue
        out.append(char)
        index += 1
    return "".join(out), openers, None


def split_heredocs(command: str, powershell: bool = False) -> tuple[str, str]:
    """
    Take out comments, and the bodies of heredocs and PowerShell here-strings: they are data — a commit
    message, a file's content — not commands. The line that opens one is kept, so `cat > file <<EOF` is
    still judged on its redirect. Returned beside it: the bodies the shell expands (an unquoted `<<EOF`, a
    `@"…"@` here-string), whose `$(…)` still runs. Quotes are followed across lines: a `<<EOF` or a `#`
    inside a quoted string opens nothing. A quote or `$(` still open at the end is refused.
    """
    kept: list[str] = []
    expanded: list[str] = []
    pending: list[tuple[str, bool]] = []  # each open heredoc's terminator, and whether its body expands
    here_string: bool | None = None  # inside one: whether it expands
    reading = _Reading()
    for line in command.replace("\r", "").split("\n"):
        if here_string is not None:
            closing = line.lstrip()
            if not closing.startswith(("'@", '"@')):
                if here_string:
                    expanded.append(line)
                continue
            here_string, line = None, closing[2:]
        elif pending:
            if line.strip() == pending[0][0]:
                pending.pop(0)
            elif pending[0][1]:
                expanded.append(line)
            continue
        text, pending, here_string = scan_line(line, reading, powershell)
        kept.append(text)
    if reading.quote and reading.quote != "<#" or reading.stack:
        raise Unparsed("a quote, `$(` or backtick that never closes")
    if reading.quote:
        raise Unparsed("a `<#` comment that never closes")
    return "\n".join(kept), "\n".join(expanded)


SUBSTITUTION = "__guard_substitution_{}__"
SUBSTITUTION_WORD = re.compile(r"__guard_substitution_(\d+)__")
# A `case` pattern's `)` closes nothing; inside `$( … )` the guard cannot pair the parentheses.
CASE_WORD = re.compile(r"(?:^|[\s;&|(])case\s")


def closing_paren(command: str, start: int, powershell: bool) -> int:
    """The index of the `)` that closes the one opened just before `start`, past quotes, escapes and the
    `$( … )` a double-quoted string inside it opens."""
    depth, quote, index = 1, "", start
    stack: list[tuple[str, int]] = []
    escape = "`" if powershell else "\\"
    while index < len(command):
        char = command[index]
        if quote == "$'":
            if char == "\\":
                index += 2
                continue
            quote = "" if char == "'" else quote
        elif char == escape and quote != "'":
            index += 2
            continue
        elif quote == "'":
            quote = "" if char == "'" else quote
        elif quote == '"':
            if char == '"':
                quote = ""
            elif char == "$" and command.startswith("(", index + 1):
                stack.append((quote, depth))
                quote, depth = "", 1
                index += 1
        elif char == "$" and not powershell and command.startswith("'", index + 1):
            quote = "$'"
            index += 1
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and stack:
                quote, depth = stack.pop()
            elif depth == 0:
                if not powershell and CASE_WORD.search(command[start:index]):
                    raise Unparsed("a `case` inside `$( … )`, whose parentheses the guard cannot pair")
                return index
        index += 1
    raise Unparsed("a `(` that never closes")


def arithmetic_end(command: str, index: int, powershell: bool) -> int | None:
    """
    Where a bash `$(( … ))` that starts at `index` ends (its last `)`), or None when it is not one. bash
    reads `$((` as arithmetic only when the two closing parentheses stand together; `$( (cmd) )` is a
    command substitution holding a subshell, and anything the guard cannot pair is left to that reading.
    """
    if powershell or not command.startswith("$((", index):
        return None
    try:
        inner_end = closing_paren(command, index + 3, powershell)
        outer_end = closing_paren(command, index + 2, powershell)
    except Unparsed:
        return None
    return outer_end if inner_end == outer_end - 1 else None


def lift_substitutions(command: str, powershell: bool, quoting: bool = True) -> tuple[str, list[str]]:
    """
    Take every command substitution out of `command` — `$(…)`, `` `…` `` (bash), `<(…)` and `>(…)` —
    and put a placeholder word in its place. Returns the command and, by placeholder number, the
    commands they hold: each runs, so each is judged as if typed, where it stands. Single quotes (and
    bash's `$'…'`) hide a substitution; double quotes do not. `quoting` False reads a heredoc body, where
    quotes are text. For PowerShell, a script block's braces end the command before them and start the
    one inside.
    """
    out: list[str] = []
    inner: list[str] = []
    quote, index = "", 0
    escape = "`" if powershell else "\\"

    def lift(body: str) -> None:
        out.append(SUBSTITUTION.format(len(inner)))
        inner.append(body)

    while index < len(command):
        char = command[index]
        if quote == "'":
            quote = "" if char == "'" else quote
            out.append(char)
            index += 1
        elif quote == "$'":
            if char == "\\":
                out.append(command[index:index + 2])
                index += 2
                continue
            quote = "" if char == "'" else quote
            out.append(char)
            index += 1
        elif char == escape and index + 1 < len(command):
            out.append(command[index:index + 2])
            index += 2
        elif quoting and not quote and not powershell and char == "$" and command.startswith("'", index + 1):
            quote = "$'"
            out.append("$'")
            index += 2
        elif quoting and char == '"' or (quoting and char == "'" and not quote):
            quote = "" if quote == char else char
            out.append(char)
            index += 1
        elif arithmetic_end(command, index, powershell) is not None:
            # `$(( … ))` is arithmetic: its value is a number, never a command, so its words are not read as
            # one. A `$( … )` inside it still runs: each is lifted, and the expression stands as one word made
            # of their placeholders (or `0`), so each is judged where it stands.
            end = arithmetic_end(command, index, powershell)
            _, nested = lift_substitutions(command[index + 3:end - 1], powershell, quoting)
            first = len(inner)
            inner.extend(nested)
            out.append("".join(SUBSTITUTION.format(first + n) for n in range(len(nested))) or "0")
            index = end + 1
        elif char == "$" and command.startswith("(", index + 1) or (
                char in "<>" and not quote and not powershell and command.startswith("(", index + 1)):
            end = closing_paren(command, index + 2, powershell)
            lift(command[index + 2:end])
            index = end + 1
        elif char == "`" and not powershell:
            end = command.find("`", index + 1)
            while end > 0 and command[end - 1] == "\\":
                end = command.find("`", end + 1)
            if end < 0:
                raise Unparsed("a backtick that never closes")
            lift(command[index + 1:end].replace("\\`", "`"))
            index = end + 1
        elif powershell and not quote and char in "{}":
            if char == "{" and index > 0 and command[index - 1] == "$":  # `${env:NAME}` is a variable
                end = command.find("}", index)
                end = len(command) - 1 if end < 0 else end
                out.append(command[index:end + 1])
                index = end + 1
            else:
                out.append(" ; ")
                index += 1
        else:
            out.append(char)
            index += 1
    return "".join(out), inner


def quoted(chars: Iterable[str]) -> str:
    """Characters already read as text, as one single-quoted word shlex reads back unchanged."""
    return "'" + "".join(chars).replace("'", "'\\''") + "'"


ANSI_C_ESCAPES = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
                  "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}
ANSI_C_NUMBER = re.compile(r"([0-7]{1,3})|x([0-9A-Fa-f]{1,2})|u([0-9A-Fa-f]{1,4})|U([0-9A-Fa-f]{1,8})")


def ansi_c(text: str, start: int) -> tuple[str, int]:
    """The value of bash's `$'…'` whose `$` is at `start`, and the index past its closing quote. Inside
    it `\\'` is a quote, not its end — a way round the guard that has been closed."""
    value: list[str] = []
    index = start + 2
    while index < len(text):
        char = text[index]
        if char == "'":
            return "".join(value), index + 1
        if char == "\\" and index + 1 < len(text):
            following = text[index + 1]
            number = ANSI_C_NUMBER.match(text, index + 1)
            if following in ANSI_C_ESCAPES:
                value.append(ANSI_C_ESCAPES[following])
                index += 2
            elif number:
                octal, *hexadecimal = number.groups()
                code = int(octal, 8) & 0xFF if octal else int(next(h for h in hexadecimal if h), 16)
                value.append(chr(min(code, 0x10FFFF)))
                index = number.end()
            elif following == "c" and index + 2 < len(text):
                value.append(chr(ord(text[index + 2]) & 0x1F))
                index += 3
            else:
                value.append(char + following)
                index += 2
            continue
        value.append(char)
        index += 1
    raise Unparsed("a `$'` string that never closes")


def brace_expansion(text: str, start: int) -> bool:
    """Whether the `{` at `start` opens a bash brace expansion — `{a,b}` or `{1..3}` — in an unquoted word."""
    depth, seen, index = 0, False, start
    while index < len(text):
        char = text[index]
        if char in " \t\n;&|<>()":
            return False
        if char in "'\"":
            end = text.find(char, index + 1)
            index = len(text) if end < 0 else end + 1
            continue
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return seen
        elif depth == 1 and (char == "," or text.startswith("..", index)):
            seen = True
        index += 1
    return False


def normalize_bash(text: str) -> str:
    out: list[str] = []
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        if char == "\\":
            following = text[index + 1] if index + 1 < size else ""
            if following == "\n":
                pass  # a line continuation joins the lines
            elif not following:
                out.append(quoted("\\"))
            else:
                out.append(mark(following) if following in LITERAL else char + following)
            index += 2
        elif char == "'":
            end = text.find("'", index + 1)
            if end < 0:
                raise Unparsed("a quote that never closes")
            out.append(quoted(mark(c) for c in text[index + 1:end]))
            index = end + 1
        elif char == '"':
            value: list[str] = []
            index += 1
            while True:
                if index >= size:
                    raise Unparsed("a quote that never closes")
                inside = text[index]
                if inside == '"':
                    break
                if inside == "\\" and index + 1 < size and text[index + 1] in '$`"\\\n':
                    if text[index + 1] != "\n":
                        value.append(mark(text[index + 1]))
                    index += 2
                    continue
                value.append(DQ + "$" if inside == "$" else mark(inside))  # `$` still expands here, as one word
                index += 1
            out.append(quoted(value))
            index += 1
        elif char == "$" and text.startswith("'", index + 1):
            value_text, index = ansi_c(text, index)
            out.append(quoted(mark(c) for c in value_text))
        elif char == "$" and text.startswith('"', index + 1):
            raise Unparsed('a `$"…"` string, which the guard does not read')
        elif char == "{" and (index == 0 or text[index - 1] != "$") and brace_expansion(text, index):
            raise Unparsed("a brace expansion (`{a,b}`), whose words the guard does not expand")
        else:
            out.append(char)
            index += 1
    return "".join(out)


POWERSHELL_ESCAPES = {"0": " ", "a": " ", "b": " ", "e": " ", "f": " ", "n": "\n", "r": " ", "t": "\t", "v": " "}
STOP_PARSING = re.compile(r"--%(?=\s|$)")


def normalize_powershell(text: str) -> str:
    def text_char(char: str) -> str:
        return "/" if char == "\\" else mark(char)

    out: list[str] = []
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        if char == "`":
            following = text[index + 1] if index + 1 < size else ""
            if following == "\n":
                out.append(" ")  # a line continuation
            elif following:
                out.append(text_char(following) if following in LITERAL or following == "\\" else "\\" + following)
            index += 2
        elif char == "'":
            value: list[str] = []
            index += 1
            while True:
                if index >= size:
                    raise Unparsed("a quote that never closes")
                if text[index] == "'":
                    if text.startswith("'", index + 1):  # `''` is a quote inside the string
                        value.append("'")
                        index += 2
                        continue
                    break
                value.append(text_char(text[index]))
                index += 1
            out.append(quoted(value))
            index += 1
        elif char == '"':
            value = []
            index += 1
            while True:
                if index >= size:
                    raise Unparsed("a quote that never closes")
                inside = text[index]
                if inside == "`" and index + 1 < size:
                    following = text[index + 1]
                    value.append(text_char(POWERSHELL_ESCAPES.get(following, following)))
                    index += 2
                    continue
                if inside == '"':
                    if text.startswith('"', index + 1):  # `""` is a quote inside the string
                        value.append('"')
                        index += 2
                        continue
                    break
                value.append(DQ + "$" if inside == "$" else text_char(inside))  # `$` still expands here, as one word
                index += 1
            out.append(quoted(value))
            index += 1
        elif STOP_PARSING.match(text, index) and (index == 0 or text[index - 1] in " \t"):
            # `--%`: the rest of the line goes to the program as it stands, split as Windows splits it.
            end = min([position for position in (text.find("\n", index), text.find("|", index)) if position >= 0]
                      or [size])
            rest = text[index + 3:end]
            if rest.count('"') % 2 or re.search(r"%[A-Za-z0-9_]+%", rest):
                raise Unparsed("a `--%` argument the guard cannot read")
            words = re.findall(r'(?:"[^"]*"|[^\s"])+', rest)
            out.append(" ".join(quoted(text_char(c) for c in word.replace('"', "")) for word in words))
            index = end
        else:
            out.append(text_char(char) if char == "\\" else char)
            index += 1
    return "".join(out)


def normalize(command: str, powershell: bool) -> str:
    """
    The command with every quoted word rewritten as one single-quoted word, its text marked (LITERAL),
    so shlex reads each word as the shell would: bash's `$'…'` decoded, PowerShell's backtick escapes,
    `''`, `""` and `--%` read, a line continuation joined. A quoting the guard does not model is refused.
    """
    return normalize_powershell(command) if powershell else normalize_bash(command)


def tokenize(command: str) -> list[str]:
    """The words and operators of a normalized command (normalize), comments already taken out."""
    command = command.replace("\n", " ; ")
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError as error:
        raise Unparsed(f"words the guard cannot split ({error})") from error


# shlex returns a run of operator characters as one word (`);`, `)&&`); these are its pieces.
OPERATOR = re.compile(r"&>>|&>|>>|>\||>&|<&|<<-|<<|\|\||&&|;;|\|&|[()<>;|&]")
OPERATOR_CHARS = frozenset("();|&<>")
SUBSHELL_OPEN, SUBSHELL_CLOSE, CALL, PIPE = ["("], [")"], ["&"], ["|"]


def pieces(tokens: list[str]) -> Iterable[list[str] | str]:
    """The simple commands of a tokenized command line (each a list of words) and, as strings, what separates
    them: `;`, `&&`, `|` and the like, and a group's `(`, `)`, `{` and `}`."""
    current: list[str] = []
    for token in tokens:
        parts = OPERATOR.findall(token) if token and set(token) <= OPERATOR_CHARS else [token]
        for piece in parts:
            if piece in SHELL_SEPARATORS or piece in ("(", ")", "{", "}"):
                if current:
                    yield current
                current = []
                yield piece
            else:
                current.append(piece)
    if current:
        yield current


def segments(tokens: list[str]) -> Iterable[list[str]]:
    """
    One simple command at a time. `;`, `&&`, `|` and the like end one; so do a group's `(`, `)`, `{`
    and `}`, which are yielded on their own as SUBSHELL_OPEN and SUBSHELL_CLOSE for `(` and `)` — a
    `cd` inside `( … )` does not outlast it. A lone `&` is yielded as CALL: PowerShell's call operator
    runs the command after it, whose name may be a variable. A pipe is yielded as PIPE: the command after
    it reads what the one before it printed.
    """
    for piece in pieces(tokens):
        if isinstance(piece, list):
            yield piece
        elif piece in ("(", ")", "&"):
            yield [piece]
        elif piece in ("|", "|&"):
            yield PIPE


@dataclass(frozen=True)
class SimpleCommand:
    """A simple command where it stands: what comes before and after it, and how deep inside groups and compound
    commands (`( … )`, `{ …; }`, `if`/`for`/`while`/`case`) it is."""
    words: list[str]  # raw, LITERAL marks kept
    before: str | None
    after: str | None
    depth: int


COMPOUND_OPENERS = frozenset({"if", "for", "while", "until", "case", "select"})
COMPOUND_CLOSERS = frozenset({"fi", "done", "esac"})


def simple_commands(tokens: list[str]) -> list[SimpleCommand]:
    """Every simple command in order — the same commands, in the same order, segments() yields."""
    found: list[SimpleCommand] = []
    before: str | None = None
    group = compound = 0  # `( … )` and `{ … }` open; `if`/`for`/`while`/`case` open (a `case` pattern's `)` closes neither)
    open_command = False
    for piece in pieces(tokens):
        if isinstance(piece, str):
            if open_command:
                last = found[-1]
                found[-1] = SimpleCommand(last.words, last.before, piece, last.depth)
                open_command = False
            group = group + 1 if piece in ("(", "{") else max(group - 1, 0) if piece in (")", "}") else group
            before = piece
            continue
        head = unmark(piece[0])
        if head in COMPOUND_CLOSERS:
            compound = max(compound - 1, 0)
        found.append(SimpleCommand(piece, before, None, group + compound))
        open_command = True
        if head in COMPOUND_OPENERS:
            compound += 1
        before = None
    return found


# Words that open or continue a compound command, or run the command after them: what follows is
# what runs. `.` sources a script (bash) or runs a script block (PowerShell).
SHELL_KEYWORDS = frozenset({"if", "then", "elif", "else", "fi", "do", "done", "while", "until", "for", "case",
                            "esac", "in", "function", "select", "coproc", "!", "."})
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Builtins that set the variables after them: `export X=requirements` sets X as `X=requirements` does.
DECLARERS = frozenset({"export", "local", "declare", "typeset", "readonly"})
# xargs's options, read as GNU getopt reads them; the first word after them is the command. Short options cluster
# (`-0I{}`, `-rI @`): a letter that takes a value takes the rest of the word, or else the next word (once: only a
# word starting `-I` or `-i` was read as naming the replace string, so `xargs -0I{} rm -rf {}/../x` passed).
XARGS_LETTERS_WITH_VALUE = frozenset("adEILnPs")
# These take a value only written in the same word (`-i@`, `-l5`); `-i` alone replaces `{}`.
XARGS_LETTERS_OPTIONAL_VALUE = frozenset("eil")
# Long options, which getopt also accepts shortened (`--repl`): True takes a value (`--max-args 1` or `=1`), False
# takes one only as `=value` (once: `--replace` read as taking the next word, `xargs --replace rm -rf {}` ran `-rf`),
# None takes none.
XARGS_LONG_OPTIONS: dict[str, bool | None] = {
    "arg-file": True, "delimiter": True, "max-args": True, "max-procs": True, "max-chars": True,
    "process-slot-var": True, "eof": False, "replace": False, "max-lines": False, "null": None,
    "no-run-if-empty": None, "verbose": None, "exit": None, "interactive": None, "open-tty": None,
    "show-limits": None, "help": None, "version": None}


@dataclass
class Unwrapped:
    """The command a segment runs, past assignments, keywords, wrappers and their options, and xargs."""
    argv: list[str]
    raw: list[str]  # the same words with their LITERAL marks
    cwd: str
    through_xargs: bool = False
    xargs_replaces: bool = False  # `xargs -I {}`: its input is put inside the words
    xargs_replace: str | None = None  # the replace string itself: `{}`, or what `-I`, `-i` or `--replace=` names
    prints_environment: bool = False  # `env [options]` alone
    findings: list[Finding] | None = None


def unwrap(plain: list[str], raw: list[str], cwd: str, repo: Repo, powershell: bool) -> Unwrapped:
    """Drop leading NAME=value assignments, shell keywords, and wrappers like rtk, env or sudo — with each
    wrapper's own options and their values (once: `env -u X git push …` runs git) — and xargs."""
    plain, raw = list(plain), list(raw)
    result = Unwrapped([], [], cwd, findings=[])
    index, saw_env = 0, False
    while index < len(plain):
        word = plain[index]
        name = program(word)
        if ASSIGNMENT.match(word) or word.lower() in SHELL_KEYWORDS:
            index += 1
            continue
        if name in WRAPPERS and not (powershell and name == "time"):
            spec = WRAPPERS[name]
            index += 1
            while index < len(plain) and plain[index].startswith("-") and plain[index] != "-":
                option = plain[index]
                if option == "--":
                    index += 1
                    break
                long_name, _, attached = option.partition("=") if option.startswith("--") else (option, "", "")
                letter = next((c for c in option[1:] if c in spec.short), "") if not option.startswith("--") else ""
                takes_value = long_name in spec.long or bool(letter)
                if option.startswith("--") and not attached and takes_value:
                    value_at = index + 1
                elif letter and option.endswith(letter):
                    value_at = index + 1
                else:
                    value_at = None
                value = plain[value_at] if value_at is not None and value_at < len(plain) else (
                    attached or (option[option.index(letter) + 1:] if letter else ""))
                raw_value = raw[value_at] if value_at is not None and value_at < len(raw) else value
                if name == "env" and (letter == "S" or long_name == "--split-string"):
                    if "$" in raw_value or SUBSTITUTION_WORD.search(raw_value):
                        result.findings.append(hidden("`env -S` splits text built when it runs."))
                        return result
                    try:
                        words = shlex.split(unmark(value))
                    except ValueError:
                        result.findings.append(hidden("`env -S` holds words the guard cannot split."))
                        return result
                    after = (value_at + 1) if value_at is not None else index + 1
                    plain = plain[:index] + words + plain[after:]
                    raw = raw[:index] + words + raw[after:]
                    continue
                if (long_name in spec.chdir or ("-" + letter) in spec.chdir) and takes_value:
                    result.cwd = change_dir(result.cwd, value, repo)
                if name == "time" and (letter == "o" or long_name == "--output") and takes_value:
                    result.findings.extend(write_path_findings(value, result.cwd, repo))
                index = (value_at + 1) if value_at is not None else index + 1
            index += spec.words
            saw_env = saw_env or name == "env"
            continue
        if name == "xargs":
            index += 1
            while index < len(plain) and plain[index].startswith("-"):
                option = plain[index]
                if option == "--":
                    index += 1
                    break
                index = xargs_option(plain, index, result)
            result.through_xargs = True
            continue
        if name in LAUNCHERS and not powershell:
            index += 1
            while index < len(plain) and (plain[index].startswith("/") and len(plain[index]) <= 4 or plain[index] == ""):
                index += 1  # `/b`, `/min`, `/wait`, an empty window title
            continue
        break
    result.argv, result.raw = plain[index:], raw[index:]
    result.prints_environment = saw_env and not result.argv
    return result


def xargs_option(plain: list[str], index: int, result: Unwrapped) -> int:
    """Read the xargs option at `plain[index]` — noting a replace string on `result` — and return the index of the
    word after it and its value."""
    option = plain[index]
    if option.startswith("--"):
        written, has_value, value = option[2:].partition("=")
        known = [name for name in XARGS_LONG_OPTIONS if name.startswith(written)] if written else []
        full = written if written in XARGS_LONG_OPTIONS else (known[0] if len(known) == 1 else None)
        if full == "replace":
            result.xargs_replaces = True
            result.xargs_replace = value or "{}"
        return index + (2 if full is not None and XARGS_LONG_OPTIONS[full] is True and not has_value else 1)
    letters = option[1:]
    for position, letter in enumerate(letters):
        rest = letters[position + 1:]
        if letter in "Ii":
            result.xargs_replaces = True
            if rest or letter == "i":
                result.xargs_replace = rest or "{}"
                return index + 1
            result.xargs_replace = plain[index + 1] if index + 1 < len(plain) else None
            return index + 2
        if letter in XARGS_LETTERS_OPTIONAL_VALUE:
            return index + 1
        if letter in XARGS_LETTERS_WITH_VALUE:
            return index + (1 if rest else 2)
    return index + 1


def program(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def flag_value(args: list[str], *names: str) -> str | None:
    for index, arg in enumerate(args):
        for name in names:
            if arg == name and index + 1 < len(args):
                return args[index + 1]
            if arg.startswith(name + "="):
                return arg.split("=", 1)[1]
    return None


def target_directory(args: list[str]) -> str | None:
    """`-t` inside a cluster of short flags: `mv -vt /tmp …` and `mv -vt/tmp …` both name /tmp."""
    for index, arg in enumerate(args):
        cluster = re.fullmatch(r"-[a-su-zA-Z]+t(.*)", arg)
        if cluster:
            return cluster.group(1) or (args[index + 1] if index + 1 < len(args) else None)
    return None


GIT_GLOBAL_OPTIONS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--config-env", "--namespace"})


def git_subcommand(args: list[str]) -> int:
    """The index of git's subcommand in `args`, past its global options; len(args) when there is none."""
    index = 0
    while index < len(args) and args[index].startswith("-"):
        index += 2 if args[index] in GIT_GLOBAL_OPTIONS_WITH_VALUE and index + 1 < len(args) else 1
    return index


def git_findings(args: list[str], cwd: str, repo: Repo) -> list[Finding]:
    index = git_subcommand(args)
    for position, arg in enumerate(args[:index]):
        if arg == "-C" and position + 1 < index:
            cwd = change_dir(cwd, args[position + 1], repo)
    findings: list[Finding] = []
    # An alias set for one command hides that command: `git -c alias.x=push x origin dev` is a push.
    if any(arg in ("-c", "--config-env") and args[i + 1].lower().startswith("alias.")
           or arg.lower().startswith("--config-env=alias.") for i, arg in enumerate(args[:index]) if i + 1 < len(args)):
        findings.append(GIT_ALIAS_FINDING)
    # So does a setting that runs a command, or pulls in a file of settings.
    for position, arg in enumerate(args[:index]):
        setting = args[position + 1] if arg in ("-c", "--config-env") and position + 1 < len(args) else (
            arg.split("=", 1)[1] if arg.lower().startswith("--config-env=") else None)
        if setting is not None:
            key, _, value = setting.partition("=")
            if command_setting(key, value if arg == "-c" else None):
                findings.append(GIT_CONFIG_COMMAND_FINDING)
            if push_setting(key, value if arg == "-c" else None):
                findings.append(PUSH_SETTING_FINDING)
    if index >= len(args):
        return findings
    sub, rest = args[index], args[index + 1:]

    def short_flag(letter: str) -> bool:
        return any(re.match(r"^-[A-Za-z]+$", arg) and letter in arg[1:] for arg in rest)

    # Pointing core.hooksPath elsewhere skips the hooks as surely as --no-verify (the review's LOW-2).
    # Setting it to the project's own hooks folder is its setup step, and reading it is harmless.
    def elsewhere(value: str) -> bool:
        return not project_hooks_folder(value, repo)

    moves_hooks = any(args[i] == "-c" and args[i + 1].lower().startswith("core.hookspath=")
                      and elsewhere(args[i + 1].split("=", 1)[1]) for i in range(min(index, len(args) - 1)))
    if sub == "config":
        values, skip = [], False
        for arg in rest:
            if skip:
                skip = False
            elif arg in ("--file", "-f", "--blob"):
                skip = True  # its value names a file, not the key
            elif not arg.startswith("-"):
                values.append(arg)
        # git 2.46+ names the action first: `git config set|unset core.hooksPath …`.
        verb = values.pop(0).lower() if values and values[0].lower() in ("set", "unset", "get", "list") else ""
        if values and values[0].lower() == "core.hookspath" and (
                "--unset" in rest or "--unset-all" in rest or verb == "unset" or (verb != "get" and len(values) > 1 and elsewhere(values[1]))):
            moves_hooks = True
        if values and values[0].lower().startswith("alias.") and verb not in ("get", "unset") and len(values) > 1 \
                and not any(arg in ("--get", "--unset", "--unset-all") for arg in rest):
            findings.append(GIT_ALIAS_FINDING)  # a saved alias hides the command from every later call
        if values and verb not in ("get", "unset", "list") and len(values) > 1 \
                and not any(arg in ("--get", "--get-all", "--get-regexp", "--unset", "--unset-all") for arg in rest) \
                and command_setting(values[0], values[1]):
            findings.append(GIT_CONFIG_COMMAND_FINDING)  # saved, it runs under every later git command
        if values and verb not in ("get", "unset", "list") and len(values) > 1 \
                and not any(arg in ("--get", "--get-all", "--get-regexp", "--unset", "--unset-all") for arg in rest) \
                and push_setting(values[0], values[1]):
            findings.append(PUSH_SETTING_FINDING)  # saved, it steers every later bare `git push`
    # git takes any unambiguous prefix of a long option: `--no-verif` skips the hooks too.
    no_verify = any(len(arg) >= len("--no-v") and "--no-verify".startswith(arg) for arg in rest)
    if no_verify or (sub == "commit" and short_flag("n")) or moves_hooks:
        findings.append(Finding(
            "no-verify", DENY, "Never skip the git hooks. If a hook fails, fix what it found.", None))

    # `git show/log/diff --output=<file>` writes the file — and those commands are pre-approved for
    # the unattended session, so the run file's Record or a locked test could be rewritten past every
    # other rule (the fourth review). No step needs git to write a file; the file tools do that.
    writes_output = any(arg.split("=", 1)[0] in OUTPUT_OPTIONS for arg in rest) or (
        sub == "format-patch" and any(arg == "-o" or arg.startswith("-o") and len(arg) > 2 for arg in rest))
    if writes_output:
        findings.append(Finding(
            "git-output-file", DENY,
            "git writes a file with --output. Read with git; write files with the file tools.", None))

    if sub in ("rm", "mv"):
        # git rm and git mv write the paths they name: a snapshot or a locked test removed by `git rm` is still removed.
        targets = [arg for arg in rest if not arg.startswith("-")]
        known = [expanded_targets(target, cwd) for target in targets]
        if any(each is None for each in known):
            findings.append(hidden(f"`git {sub}` writes a path built when it runs."))
        targets = [each[0] for each in known if each]
        for index, target in enumerate(targets):
            # `git mv <source>… <destination>`: the destination receives; only the sources move whole.
            if sub == "mv" and index == len(targets) - 1 and len(targets) > 1:
                rel = relative_to_root(target, cwd, repo.root)
                findings += path_findings(rel, repo, through_shell=True) if rel else []
                continue
            rel = whole_target(target, cwd, repo.root, pathspec=True)
            if rel:
                findings += path_findings(rel, repo, through_shell=True, whole_folder=True)

    # Commands that put older content back write their paths too: `git checkout <rev> -- <snapshot>`
    # changes a read-only file as surely as an edit, and restoring the run file from an earlier commit
    # emptied its lock (the re-review's HIGH-2).
    restored, whole_tree, from_revision = git_restored_paths(sub, rest, cwd, repo)
    for rel in restored:
        findings += path_findings(rel, repo, through_shell=True, whole_folder=from_revision)
    if whole_tree:
        findings += whole_tree_findings(repo)

    findings += tracking_findings(sub, rest)
    # A mirror remote pushes every branch, dev included, on a bare `git push <remote>`.
    if sub == "remote" and rest[:1] == ["add"] and any(arg == "--mirror" or arg.startswith("--mirror=") for arg in rest):
        findings.append(PUSH_SETTING_FINDING)
    if sub == "push":
        findings += push_findings(rest, cwd, repo)
    elif sub == "branch" and any(arg in ("-d", "-D", "--delete") or re.match(r"^-[a-zA-Z]*[dD]", arg) for arg in rest):
        findings.append(Finding(
            "branch-delete", DENY, "Never delete a branch. Say it looks done, and stop there.", None))
    elif sub in ("commit", "merge", "rebase", "cherry-pick", "revert", "am"):
        known = repo.knows(cwd)
        branch = repo.current_branch(cwd if known else None)
        if branch in rules().protected_branches:
            findings.append(Finding(
                "commit-protected-branch", DENY,
                f"The checkout is on {branch}, which is reached by pull request only. "
                "`git checkout -b <branch>` keeps the changes.", None))
        elif not known:
            findings.append(UNKNOWN_CHECKOUT_FINDING)
    return findings


# Settings whose value git runs as a command, or that pull in another file of settings: one set with
# `-c`, `git config` or the environment hides what a later git command runs.
COMMAND_SETTINGS = re.compile(
    r"^(include\.path|includeif\..+\.path|core\.(fsmonitor|sshcommand|pager|editor|askpass|gitproxy)|"
    r"sequence\.editor|diff\.external|diff\..+\.(textconv|command)|difftool\..+\.cmd|mergetool\..+\.cmd|"
    r"filter\..+\.(clean|smudge|process)|merge\..+\.driver|credential(\..+)?\.helper|gpg(\..+)?\.program|"
    r"pager\..+|remote\..+\.(receivepack|uploadpack|vcs)|uploadpack\.packobjectshook)$", re.IGNORECASE)
HARMLESS_SETTING_VALUES = frozenset({"", "cat", "less", "more", "false", "true", "0", "1", "no", "off"})


def command_setting(key: str, value: str | None) -> bool:
    """Whether setting `key` to `value` (None: a value the command line does not show) hides a command.
    Aliases and core.hooksPath have findings of their own."""
    if not COMMAND_SETTINGS.match(key.strip("'\"")):
        return False
    return value is None or value.strip("'\"").lower() not in HARMLESS_SETTING_VALUES


# Settings that decide where a push lands without the push naming it: a bare `git push` then reaches dev
# while the guard reads only the branch and its upstream.
PUSH_SETTINGS = re.compile(r"^(remote\..+\.(push|mirror)|remote\.pushdefault|push\.default|"
                           r"branch\..+\.(pushremote|merge))$", re.IGNORECASE)
# push.default values that push the current branch to itself or to its upstream — both read by the guard.
HARMLESS_PUSH_DEFAULTS = frozenset({"simple", "current", "upstream", "tracking", "nothing"})
FALSE_VALUES = frozenset({"false", "no", "off", "0"})


def push_setting(key: str, value: str | None) -> bool:
    """Whether setting `key` to `value` (None: a value the command line does not show) can send a later
    push somewhere the guard does not read: a push refspec, another push remote, `push.default=matching`,
    `remote.<name>.mirror` (a bare push then pushes every branch, dev included), or an upstream
    (`branch.<x>.merge`) on a protected branch."""
    key = key.strip("'\"").lower()
    if not PUSH_SETTINGS.match(key):
        return False
    value = None if value is None else value.strip("'\"")
    if key == "push.default":
        return value is None or value.lower() not in HARMLESS_PUSH_DEFAULTS
    if key.endswith(".mirror"):
        return value is None or value.lower() not in FALSE_VALUES
    if key.endswith(".merge"):
        return value is None or re.sub(r"^(refs/)?heads/", "", value) in rules().protected_branches
    return True


PUSH_SETTING_FINDING = Finding(
    "hidden-command", DENY,
    "A push setting (`remote.<name>.push`, `remote.<name>.mirror`, `git remote add --mirror`, `push.default`, "
    "`remote.pushDefault`, `branch.<name>.pushRemote`, an upstream on a protected branch) decides where a later `git push` lands without the push naming it, so the "
    "guard cannot see it — a bare push could reach dev. Name the destination on the push itself.", None)
GIT_CONFIG_COMMAND_FINDING = Finding(
    "hidden-command", DENY,
    "A git setting that runs a command or pulls in another file of settings (`include.path`, `core.fsmonitor`, "
    "`core.sshCommand`, a pager or editor…) hides what git will run from the guard. Run the command itself.", None)
UNKNOWN_CHECKOUT_FINDING = Finding(
    "unknown-checkout", ASK,
    "This runs in a folder the guard cannot name (a `cd` to a variable or a substitution, a folder that does not "
    "exist yet), so it cannot tell which branch it lands on. A person confirms it.", None)
# git reads its settings from the environment too: whole files (GIT_CONFIG_GLOBAL, GIT_CONFIG_SYSTEM,
# GIT_CONFIG), single keys (GIT_CONFIG_KEY_<n>, GIT_CONFIG_PARAMETERS), and commands to run.
GIT_CONFIG_FILE_IN_ENV = re.compile(r"^GIT_CONFIG(_GLOBAL|_SYSTEM)?=", re.IGNORECASE)
GIT_CONFIG_KEY_IN_ENV = re.compile(r"^GIT_CONFIG_KEY_\d+=(.*)$", re.IGNORECASE)
GIT_COMMAND_IN_ENV = re.compile(r"^(GIT_SSH|GIT_SSH_COMMAND|GIT_PAGER|GIT_EDITOR|GIT_SEQUENCE_EDITOR|GIT_ASKPASS|"
                                r"GIT_EXTERNAL_DIFF|GIT_PROXY_COMMAND|GIT_EXEC_PATH|GIT_TEMPLATE_DIR)=(.*)$", re.IGNORECASE)


# PowerShell's names for an environment variable: `$env:X`, `${env:X}`, and the item `env:X` — with `=value` attached
# when written without spaces (once: `$env:GIT_CONFIG_PARAMETERS="…"` set a push refspec unseen).
ENVIRONMENT_WORD = re.compile(r"^(?:\$\{env:([A-Za-z_]\w*)\}|\$env:([A-Za-z_]\w*)|env:([A-Za-z_]\w*))(?:[-+]?=(.*))?$",
                              re.IGNORECASE | re.DOTALL)
ENVIRONMENT_SETTERS = frozenset({"set-item", "si", "new-item", "ni"})
# .NET's own write of the environment: `[Environment]::SetEnvironmentVariable('NAME', 'value'[, scope])`.
ENVIRONMENT_SETTER_CALL = re.compile(r"SetEnvironmentVariable\s*\(\s*(['\"]?)([^'\",)]*)\1\s*,\s*(['\"]?)([^'\")]*)\3",
                                     re.IGNORECASE)


def environment_assignments(segment: list[str]) -> list[str]:
    """Every `NAME=value` a command sets: bash's prefix and `export`, PowerShell's `$env:NAME = value`,
    `$env:NAME=value`, `${env:NAME} = value` and `Set-Item [-Path] env:NAME [-Value] value`."""
    found = [word for word in segment if ASSIGNMENT.match(word)]
    setter = bool(segment) and program(segment[0]) in ENVIRONMENT_SETTERS
    for index, word in enumerate(segment):
        match = ENVIRONMENT_WORD.match(word)
        if not match:
            continue
        name = match.group(1) or match.group(2) or match.group(3)
        if match.group(4) is not None:
            found.append(f"{name}={match.group(4)}")
        elif segment[index + 1:index + 2] and re.fullmatch(r"[-+]?=", segment[index + 1]):
            found.append(f"{name}={segment[index + 2] if index + 2 < len(segment) else ''}")
        elif index and setter:
            lowered = [arg.lower() if arg.startswith("-") else arg for arg in segment[index + 1:]]
            value = flag_value(lowered, "-value")
            following = [arg for position, arg in enumerate(lowered) if not arg.startswith("-") and not (
                position and lowered[position - 1] in ("-value", "-type", "-itemtype"))]
            found.append(f"{name}={value if value is not None else (following[0] if following else '')}")
    return found


def environment_setter_assignments(text: str) -> list[str] | None:
    """The `NAME=value` each `[Environment]::SetEnvironmentVariable(…)` in `text` makes, or None when one names a
    variable or a value the guard cannot read as text."""
    found = []
    for match in ENVIRONMENT_SETTER_CALL.finditer(text):
        name, value = match.group(2).strip(), match.group(4)
        if not re.fullmatch(r"[A-Za-z_]\w*", name) or "$" in value:
            return None
        found.append(f"{name}={value}")
    if len(found) != len(re.findall(r"SetEnvironmentVariable\s*\(", text, re.IGNORECASE)):
        return None
    return found


def environment_findings(segment: list[str], assignments: list[str] | None = None) -> list[Finding]:
    """What setting git's environment does — from a command's words, or from `assignments` already read."""
    findings: list[Finding] = []
    assignments = environment_assignments(segment) if assignments is None else assignments
    if any(HOOKS_PATH_IN_ENV.match(assignment) for assignment in assignments):
        findings.append(HOOKS_PATH_FINDING)  # git reads core.hooksPath from GIT_CONFIG_* too
    if any(ALIAS_IN_ENV.match(assignment) for assignment in assignments):
        findings.append(GIT_ALIAS_FINDING)
    for assignment in assignments:
        key = GIT_CONFIG_KEY_IN_ENV.match(assignment)
        command = GIT_COMMAND_IN_ENV.match(assignment)
        if GIT_CONFIG_FILE_IN_ENV.match(assignment) or assignment.upper().startswith("GIT_CONFIG_PARAMETERS=") or (
                key and command_setting(key.group(1), None)) or (
                command and command.group(2).strip("'\"").lower() not in HARMLESS_SETTING_VALUES):
            findings.append(GIT_CONFIG_COMMAND_FINDING)
        if key and push_setting(key.group(1), None):
            findings.append(PUSH_SETTING_FINDING)
    return findings


def protected_remote(ref: str) -> bool:
    """`origin/dev`, `remotes/origin/dev` or `refs/remotes/origin/dev` — a remote protected branch."""
    name = re.sub(r"^(refs/)?(remotes/)?", "", ref)
    return "/" in name and name.split("/", 1)[1] in rules().protected_branches


def tracking_findings(sub: str, rest: list[str]) -> list[Finding]:
    """
    A work branch cut from a protected branch must not track it: its upstream would be `dev`, and the
    IDE's push would then land there (CLAUDE.md, Branches). Refuses creating a branch from a
    protected remote branch without `--no-track`, and pointing a branch's upstream at one.
    """
    if "--no-track" in rest:
        return []
    message = ("Cut work branches from a protected branch with --no-track, so a push cannot land on it: "
               f"`git checkout --no-track -b <branch> {rules().base_ref}`.")
    sets_upstream = any(arg.startswith("--set-upstream-to") for arg in rest) or "-u" in rest
    targets = [arg.split("=", 1)[1] if arg.startswith("--set-upstream-to=") else arg for arg in rest]
    if sub == "branch" and sets_upstream and any(protected_remote(t) for t in targets):
        return [Finding("branch-tracks-protected", DENY, message, None)]
    creates = (sub == "checkout" and any(a in ("-b", "-B", "-t", "--track") for a in rest)) or \
              (sub == "switch" and any(a in ("-c", "-C", "--create", "--force-create", "-t", "--track") for a in rest)) or \
              (sub == "branch" and not any(a.startswith("-") for a in rest)) or \
              (sub == "worktree" and rest[:1] == ["add"] and any(a in ("-b", "-B") for a in rest))
    if creates and any(protected_remote(arg) for arg in rest if not arg.startswith("-")):
        return [Finding("branch-tracks-protected", DENY, message, None)]
    return []


# git accepts any unambiguous prefix of a long option; `--ou` already means --output or
# --output-directory, so every prefix from there up is a write.
OUTPUT_OPTIONS = frozenset(name[:length] for name in ("--output", "--output-directory")
                           for length in range(len("--ou"), len(name) + 1))
WHOLE_TREE = frozenset({".", ":/", "*", "./"})
GIT_OPTIONS_WITH_VALUE = frozenset({"-s", "--source", "-b", "-B", "--orphan", "-m", "--message", "--pathspec-from-file"})


def git_restored_paths(sub: str, rest: list[str], cwd: str, repo: Repo) -> tuple[list[str], bool, bool]:
    """
    The paths a checkout, restore, stash, apply or hard reset writes in the working tree, whether it
    writes the whole tree, and whether they come from a revision. Only content from a revision can change
    a protected file that was never touched, so only then is a folder, `.` or a glob judged by everything
    it holds; `git checkout -- .` alone puts back what is already committed. Branch switches and
    staged-only restores write nothing here.
    """
    def positional(args: list[str]) -> list[str]:
        values, skip = [], False
        for arg in args:
            if skip:
                skip = False
            elif arg in GIT_OPTIONS_WITH_VALUE:
                skip = True
            elif not arg.startswith("-"):
                values.append(arg)
        return values

    def resolve(targets: list[str], from_revision: bool) -> tuple[list[str], bool, bool]:
        known = [expanded_targets(target, cwd) for target in targets]
        targets = [each[0] for each in known if each]
        # Paths the command line cannot show — a file of them, or one built when it runs — are the whole tree.
        hidden_paths = any(arg.startswith("--pathspec-from-file") for arg in rest) or None in known
        whole = any(target in WHOLE_TREE for target in targets) or hidden_paths
        if from_revision:
            if hidden_paths:
                targets = [*targets, "."]
            return [rel for target in targets if (rel := whole_target(target, cwd, repo.root, pathspec=True))], whole, True
        rels = [rel for target in targets if target not in WHOLE_TREE and (rel := relative_to_root(target, cwd, repo.root))]
        # A folder or a glob writes every held path under or matching it: `git checkout HEAD~1 --
        # docs/factory/runs` restored the run file the exact-path test missed (the third review).
        held = held_paths(repo)
        covered = [path for spec in rels for path in held
                   if path != spec and (path.startswith(spec.rstrip("/") + "/")
                                        or (any(ch in spec for ch in "*?[") and fnmatch.fnmatch(path, spec)))]
        return rels + covered, whole, False

    after_dashes = rest[rest.index("--") + 1:] if "--" in rest else None
    before_dashes = rest[:rest.index("--")] if "--" in rest else rest
    if sub == "checkout":
        if after_dashes is not None:
            return resolve(after_dashes, bool(positional(before_dashes)))  # `git checkout <rev> -- <paths…>`
        if any(arg in ("-b", "-B", "--orphan", "-") for arg in rest):
            return [], False, False
        values = positional(rest)
        if len(values) > 1 or (values and any(arg.startswith("--pathspec-from-file") for arg in rest)):
            return resolve(values[1:], True)  # `git checkout <rev> <paths…>`
        if values and (values[0] in WHOLE_TREE or ((rel := relative_to_root(values[0], cwd, repo.root))
                                                   and repo.exists(rel))):
            return resolve(values, False)  # `git checkout <path>` — a path, not a branch
        return [], False, False
    if sub == "restore":
        if "--staged" in rest and not any(arg in ("--worktree", "-W") for arg in rest):
            return [], False, False
        source = any(arg in ("-s", "--source") or arg.startswith("--source=") or (arg.startswith("-s") and len(arg) > 2)
                     for arg in before_dashes)
        return resolve(after_dashes if after_dashes is not None else positional(rest), source)
    if sub == "stash":
        verb = rest[0] if rest and not rest[0].startswith("-") else "push"
        if verb in ("list", "show", "drop", "clear", "create", "store", "branch"):
            return [], False, False
        if after_dashes:
            return resolve(after_dashes, False)
        return [], True, False
    if sub == "apply":
        return [], not any(arg in ("--check", "--stat", "--numstat", "--summary", "--cached") for arg in rest), False
    if sub == "reset":
        return [], any(arg in ("--hard", "--merge", "--keep") for arg in rest), False
    if sub in ("revert", "cherry-pick"):
        # Uncommitted, they leave whatever they change in the working tree — any path at all.
        return [], any(arg in ("-n", "--no-commit") for arg in rest), False
    return [], False, False


def held_paths(repo: Repo) -> list[str]:
    """What the open runs hold: their run files and every locked test."""
    view = repo.factory_view()
    if view is None:
        return []
    if view.unreadable:
        files = [view.unreadable] if "/" in view.unreadable and "," not in view.unreadable else []
        return files + sorted(view.lock)
    return sorted(view.run_texts()) + sorted(view.lock)


def whole_tree_findings(repo: Repo) -> list[Finding]:
    """A command that rewrites the whole working tree writes every run file and every locked test."""
    view = repo.factory_view()
    if view is None:
        return []
    if view.unreadable:
        target = view.unreadable if "," not in view.unreadable else f"{rules().runs_dir}/R-000.md"
        return path_findings(target, repo, through_shell=True)
    findings: list[Finding] = []
    for rel in held_paths(repo):
        findings += path_findings(rel, repo, through_shell=True)
    return findings


def protected_names() -> str:
    """`protected_branches` for a message: "dev, main"."""
    return ", ".join(sorted(rules().protected_branches)) or "the protected branches"


def push_findings(rest: list[str], cwd: str, repo: Repo) -> list[Finding]:
    findings: list[Finding] = []
    flags = [arg for arg in rest if arg.startswith("-")]
    positional = [arg for arg in rest if not arg.startswith("-")]
    refspecs = positional[1:]

    if any(flag in ("-d", "--delete") for flag in flags) or any(ref.startswith(":") for ref in refspecs):
        findings.append(Finding(
            "branch-delete", DENY, "Never delete a branch, locally or on the remote.", None))
    if any(flag in ("--all", "--mirror", "--branches") for flag in flags) or any("*" in ref for ref in refspecs):
        findings.append(Finding(
            "push-protected-branch", DENY,
            f"--all, --mirror and a `*` refspec push to every branch, {protected_names()} among them.", None))

    # Which checkout's branch HEAD means: one the guard cannot name is not the root's.
    known = repo.knows(cwd)
    here = cwd if known else None
    targets: list[str] = []
    for ref in refspecs:
        destination = ref.lstrip("+").split(":", 1)[-1]
        destination = re.sub(r"^(refs/)?heads/", "", destination)
        if "$" in destination or "%" in destination or SUBSTITUTION_WORD.search(destination):
            findings.append(hidden("This push's destination is built when it runs."))
        targets.append((repo.current_branch(here) or "") if destination in ("HEAD", "@") else destination)
    if not refspecs:
        targets += [repo.current_branch(here) or "", repo.upstream_branch(here) or ""]
    protected = sorted({target for target in targets if target in rules().protected_branches})
    if protected:
        findings.append(Finding(
            "push-protected-branch", DENY,
            f"This push would land on {', '.join(protected)}, a protected branch: it changes through a pull "
            "request (or promotion) only.", None))
    elif not known and (not refspecs or any(ref.lstrip("+").split(":", 1)[-1] in ("HEAD", "@") for ref in refspecs)):
        findings.append(UNKNOWN_CHECKOUT_FINDING)

    forced = any(flag in ("-f", "--force") or flag.startswith("--force-with-lease") for flag in flags) \
        or any(ref.startswith("+") for ref in refspecs)
    if forced and not protected:
        findings.append(Finding(
            "force-push", ASK, "A force-push rewrites history others may have. A person confirms it.", None))
    return findings


def dotnet_ef_findings(args: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    if any(arg in ("-s", "--startup-project") or arg.startswith("--startup-project=") for arg in args):
        findings.append(Finding(
            "ef-startup-project", DENY,
            "Never pass --startup-project to dotnet ef — the project's own design-time factory is the "
            "one that refuses a non-local host.", None))
    connection = flag_value(args, "--connection")
    if connection:
        host = re.search(r"(?:Host|Server)\s*=\s*([^;,]+)", connection, re.IGNORECASE)
        if host and host.group(1).strip().lower() not in LOCAL_DB_HOSTS:
            findings.append(Finding(
                "ef-remote-database", DENY,
                "dotnet ef points at a database that is not on this machine. Migrations reach shared "
                "databases through the project's deployment only.", None))
    if len(args) >= 2 and args[0] == "database" and args[1] == "drop":
        findings.append(Finding(
            "ef-database-drop", ASK, "Dropping a database. A person confirms which one.", None))
    return findings


# The options of sed and perl whose next word is the script, not a file: `-e`, `-f`, and a cluster of
# short flags ending in one (`sed -ne`, `perl -ple`) — but not after `i`, whose letters are the backup
# suffix (`sed -ie`, `perl -pie` edit in place with the suffix `e`).
SCRIPT_OPTION = {"sed": re.compile(r"-[A-Za-hj-z]*[ef]|--expression|--file"),
                 "perl": re.compile(r"-[A-Za-hj-z0-9]*[eE]")}
# The writing cmdlets' parameters whose value is content, a type or a filter — never the path written.
POWERSHELL_VALUE_OPTIONS = ("-value", "-itemtype", "-type", "-encoding", "-target", "-inputobject", "-width",
                            "-stream", "-filter", "-include", "-exclude", "-delimiter")
POWERSHELL_WRITER_ALIASES = frozenset({"sc", "ac", "ni", "ri", "mi", "cpi"})
# The cmdlets that write content to one path, and the parameters that name it.
CONTENT_WRITERS = frozenset({"set-content", "add-content", "sc", "ac", "out-file", "new-item", "ni"})
POWERSHELL_PATH_OPTIONS = frozenset({"-path", "-literalpath", "-filepath", "-pspath", "-lp"})


def in_place_files(name: str, args: list[str]) -> list[str]:
    """The files `sed -i` or `perl -pi` edits: its words but the script — the value of `-e`, or else the first."""
    files, scripted, skip = [], False, False
    for arg in args:
        if skip:
            skip = False
        elif SCRIPT_OPTION[name].fullmatch(arg):
            scripted = skip = True
        elif arg.startswith(("--expression=", "--file=")):
            scripted = True
        elif not arg.startswith("-"):
            files.append(arg)
    return files if scripted else files[1:]


def write_targets(name: str, args: list[str]) -> list[str]:
    positional = [arg for arg in args if not arg.startswith("-")]
    if name in IN_PLACE_EDITORS:
        return in_place_files(name, args) if any(IN_PLACE_FLAG.match(arg) for arg in args) else []
    if "-" in name or name in POWERSHELL_WRITER_ALIASES:
        # `Set-Content -Path f -Value $text`: the value is what is written, not where.
        positional = [arg for index, arg in enumerate(args) if not arg.startswith("-") and not (
            index and len(args[index - 1]) > 2 and any(option.startswith(args[index - 1].lower())
                                                       for option in POWERSHELL_VALUE_OPTIONS))]
    if name in CONTENT_WRITERS:
        # Their path is -Path (or its kin), else the first word; a second word is the content (`sc f $text`).
        paths = [args[index + 1] for index, arg in enumerate(args[:-1]) if arg.lower() in POWERSHELL_PATH_OPTIONS]
        paths = paths or positional[:1]
        child = flag_value([arg.lower() if arg.startswith("-") else arg for arg in args], "-name")
        return [posixpath.join(paths[0] if paths else ".", child)] if name in ("new-item", "ni") and child else paths
    if name in WRITES_ALL_ARGS:
        return positional
    if name == "ln":
        # A link, hard or symbolic, makes its source writable under another name: every file it names is written to,
        # not only the link (the 15 Oct review's #7). `-t <dir>`'s folder is among them, as the destination.
        return positional
    if name in WRITES_DESTINATION:
        destination = flag_value(args, "-Destination", "-destination", "--target-directory", "-t")
        return [destination] if destination else positional[-1:]
    if name == "dd":
        return [arg[3:] for arg in args if arg.startswith("of=")]
    return []


NULL_DEVICES = frozenset({"/dev/null", "$null", "nul"})


def redirect_targets(argv: list[str]) -> list[str]:
    return [
        argv[index + 1] for index, token in enumerate(argv[:-1])
        if token in REDIRECTS and not argv[index + 1].startswith("&") and unmark(argv[index + 1]).lower() not in NULL_DEVICES
    ]


FACTORY_OPTIONS_WITH_VALUE = ("--run",)


# ---------------------------------------------------------------- write targets built when they run
#
# A write target holding a variable is judged on what the variable holds only when the guard can read that
# plainly; otherwise it is refused as `hidden-command`, with how to write it plainly. Experience taught the
# guard to follow every assignment, loop and glob, and a review found the ways round that modelling at once
# (`read X`, `X+=…`, an array, a reassignment in a branch, a value the shell splits). So the guard now models
# less and refuses more: a variable is trusted only when the command line sets it exactly once, plainly, and
# names it nowhere else.

@dataclass(frozen=True)
class LineVariable:
    """What a command line sets a variable to, as the guard reads it."""
    values: list[str] | None  # raw (LITERAL marks kept); None: not read plainly — distrusted
    loop: bool = False  # a `for` loop's words: each a value, a glob among them expanded by the loop
    order: int = 0  # the simple command that sets it (simple_commands); a use before it is not of this value


Variables = "LineVariables"
# The folders a target may name by a variable the guard can read, in any shell's spelling.
KNOWN_FOLDER = re.compile(r"\$\{(?:env:)?([A-Za-z_]\w*)\}|\$(?:env:)?([A-Za-z_]\w*)|%([A-Za-z_]\w*)%", re.IGNORECASE)
KNOWN_FOLDER_NAMES = frozenset({"pwd", "tmpdir", "temp", "tmp", "home", "userprofile", "appdata", "claude_config_dir",
                                "claude_project_dir"})
CMD_EXPANSION = re.compile(r"%[A-Za-z_]\w*%")
# Past this many values a target's variables multiply to, it is refused.
MAX_VALUES = 64
# `$(mktemp …)` makes a file or folder in the temporary folder: a variable set once from it is a temporary path
# — but only without a template, or with one put in the temporary folder by `-t`, `--tmpdir` or
# `-p <temporary folder>`. A bare template (`mktemp -d tmpXXX`) is made in the working folder, and a
# template with a folder in it, or `-p`/`--tmpdir=` another folder, could be anywhere: not read.
MKTEMP_TEMPLATE = re.compile(r"[\w.-]*X{3,}[\w.-]*")
MKTEMP_FLAGS = re.compile(r"-[dqut]+|--directory|--dry-run|--quiet|--tmpdir|--suffix=[\w.-]+")
MKTEMP_TEMPORARY_FOLDERS = frozenset({"/tmp", "$tmpdir", "${tmpdir}", "${tmpdir:-/tmp}", "$temp", "$tmp"})
# Text built when it runs that can be no path but a number or a date — `$$`, `$RANDOM`, `$(date +%s)`: a name
# part made of it is judged as a wildcard (`out-$(date +%s).txt` is `out-*.txt`).
SAFE_SUBSTITUTION = re.compile(r"\s*date(?:\s+(?:-u|--utc|-I\w*|--iso-8601(?:=\w+)?|\+[\w%:.,-]*))*\s*")
SAFE_PARAMETERS = frozenset({"$", "?", "!", "#"})
SAFE_VARIABLES = frozenset({"random", "seconds", "epochseconds", "epochrealtime", "bashpid", "ppid"})
# A variable used in a target, in any shell's spelling, with a bash `${NAME<operator>…}` tail; a special parameter;
# a substitution's placeholder.
TARGET_EXPANSION = re.compile(
    DQ + r"?(?:\$\{env:(?P<braced_env>[A-Za-z_]\w*)\}|\$env:(?P<env>[A-Za-z_]\w*)"
    r"|\$\{(?P<braced>[A-Za-z_]\w*)(?P<tail>[^}]*)\}|\$(?P<plain>[A-Za-z_]\w*)|%(?P<cmd>[A-Za-z_]\w*)%"
    r"|\$(?P<special>[$?!#@*0-9-])|__guard_substitution_(?P<substitution>\d+)__)", re.IGNORECASE)

# A value bash leaves as one word, unglobbed, when a variable holding it is used unquoted.
UNQUOTED_PLAIN = re.compile(r"[^\s*?\[\]()\\]*")
DISTRUSTED_VARIABLE = (
    "This writes to a path held in a variable the command line sets more than once, or in a way the guard does "
    "not read plainly (`read`, `+=`, an array, `printf -v`, a loop or a branch that changes it, a value the shell "
    "splits or globs, a `${…}` operator). Write the path literally.")
BUILT_AT_RUN_TIME = (
    "This writes to a path built when it runs (a substitution, or a variable this command line does not set "
    "plainly). Write the path literally; for many files, list them — `sed -i … a.cs b.cs`, not `$(git ls-files …)`.")


def known_folder(name: str, cwd: str, root: str | None = None) -> str | None:
    """The working folder, the temporary folder, the home folder, Claude Code's settings folder or the project folder
    (`$CLAUDE_PROJECT_DIR`, the repository root) that a variable names — or None for any other variable."""
    lower = name.lower()
    if lower == "pwd":
        value = str(cwd)
    elif lower in ("tmpdir", "temp", "tmp"):
        value = os.environ.get(name.upper()) or tempfile.gettempdir()
    elif lower in ("home", "userprofile"):
        value = os.path.expanduser("~")
    elif lower == "appdata":
        value = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    elif lower == "claude_config_dir":
        value = expand_home(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")
    elif lower == "claude_project_dir" and root:
        value = root
    else:
        return None
    return value.replace("\\", "/")


def temporary_path() -> str:
    """Where `$(mktemp)` puts what it makes, as the guard judges it."""
    return posixpath.join(tempfile.gettempdir().replace("\\", "/"), "guard-mktemp")


def makes_temporary(body: str, variable: Variables | None = None) -> bool:
    """
    Whether the substitution `body` is a `mktemp` that makes its file or folder in the temporary folder: no
    template, or one placed there by `-t`, `--tmpdir` or `-p <temporary folder>` — and the line does not set
    the TMPDIR it reads.
    """
    words = body.replace('"', "").replace("'", "").split()
    if words[:1] != ["mktemp"] or (variable is not None and variable.sets_environment("TMPDIR")):
        return False
    templates, placed, index = 0, False, 1
    while index < len(words):
        word = words[index]
        if word == "-p" or word.startswith("--tmpdir="):
            folder = word.split("=", 1)[1] if word.startswith("--tmpdir=") else (words[index + 1:index + 2] or [""])[0]
            if unmark(folder).lower().rstrip("/") not in MKTEMP_TEMPORARY_FOLDERS:
                return False
            placed = True
            index += 1 if word.startswith("--tmpdir=") else 2
            continue
        if MKTEMP_FLAGS.fullmatch(word):
            placed = placed or word == "--tmpdir" or (not word.startswith("--") and "t" in word)
        elif MKTEMP_TEMPLATE.fullmatch(word):
            templates += 1
        else:
            return False
        index += 1
    return templates == 0 or (templates == 1 and placed)


def built_at_run_time(text: str) -> bool:
    """Whether `text` (with its LITERAL marks, so a single-quoted `$` is text) still holds an expansion:
    a variable, `${…}`, `%VAR%`, `$env:X` or a substitution."""
    return bool(EXPANSION.search(text) or CMD_EXPANSION.search(text) or SUBSTITUTION_WORD.search(text))


class LineVariables:
    """
    The variables a command line sets, read strictly. A variable is trusted only when the line sets it
    exactly once — `NAME=<value>`, `export|local|declare|typeset|readonly NAME=<value>` on its own, unconditionally
    and outside any group, branch or loop (bash), or `$name = <value>` (PowerShell) — or names it as a `for` loop's
    variable (bash), and names it nowhere else but as `$NAME` or `${NAME}`. The value must be text, a temporary path
    (`$(mktemp)`, PowerShell's `Join-Path $env:TEMP x`) or built only from the known folders. Anything else —
    `read X`, `X+=…`, an array, `printf -v X`, `declare -n`, `${X:=…}`, a second assignment, a cast or scope prefix,
    `Set-Variable` — distrusts it, and a write target naming it is refused.
    """

    def __init__(self, commands: list[SimpleCommand], inner: list[str], text: str, powershell: bool, cwd: str,
                 root: str):
        self.commands, self.inner, self.text, self.powershell = commands, inner, text, powershell
        self.cwd, self.root = cwd, root
        self.cache: dict[str, LineVariable | None] = {}
        self.unreadable = self._sets_names_unseen()

    def __call__(self, name: str, order: int) -> LineVariable | None:
        """The variable as the line sets it: None when the line does not name it at all."""
        if self.unreadable:
            return LineVariable(None)
        key = name.lower() if self.powershell else name
        if key not in self.cache:
            self.cache[key] = LineVariable(None)  # a value naming itself, in a cycle, is not read
            self.cache[key] = self._read(key)
        return self.cache[key]

    def _sets_names_unseen(self) -> bool:
        """
        Whether the line runs something that can set a variable whose name the text need not spell — `read`,
        `mapfile`, `printf -v`, `declare` with options or a name built when it runs, `unset`, `source`/`.`, `eval`
        (bash); `Set-Variable` and its kin, `variable:`, `-OutVariable` and the other variable parameters,
        dot-sourcing, `[ref]` and `PSVariable` (PowerShell). Then no variable on the line is trusted, the known
        folders' included: `read "$(printf '\\x58')"` sets X unseen.
        """
        lowered = self.text.lower()
        if self.powershell:
            if any(marker in lowered for marker in ("variable:", "psvariable", "[ref]")):
                return True
            for command in self.commands:
                words = [unmark(word).lower() for word in command.words]
                if words[0] in ("set-variable", "sv", "new-variable", "nv", "clear-variable", "clv", "remove-variable",
                                "rv", ".") or any(re.fullmatch(r"-(outvariable|ov|pipelinevariable|pv|errorvariable|ev|"
                                                               r"warningvariable|wv|informationvariable|iv)(:.*)?", word)
                                                  for word in words):
                    return True
            return False
        for command in self.commands:
            words = [unmark(word) for word in command.words]
            index = 0
            while index < len(words) and (ASSIGNMENT.match(words[index]) or words[index] in SHELL_KEYWORDS - {"."}
                                          or program(words[index]) in WRAPPERS):
                index += 1
            name, args, raw_args = (program(words[index]) if index < len(words) else ""), words[index + 1:], \
                command.words[index + 1:]
            if name in ("read", "mapfile", "readarray", "getopts", "let", "unset", "source", ".", "eval"):
                return True
            if name == "printf" and any(re.fullmatch(r"-\w*v.*", arg) for arg in args):
                return True
            if name in DECLARERS and any(arg.startswith(("-", "+")) or built_at_run_time(raw.split("=", 1)[0])
                                         for arg, raw in zip(args, raw_args)):
                return True
        return False

    def named(self, name: str) -> bool:
        """Whether the line names `name` anywhere but as a plain use."""
        return self(name, len(self.commands)) is not None

    def _bindings(self, name: str) -> int:
        """How often the text names `name` other than as a plain `$NAME`/`${NAME}` use: each assignment, `read`,
        `for`, `printf -v`, `+=`, array, cast, scope prefix, `Set-Variable` and the like is one."""
        escaped = re.escape(name)
        if not self.powershell:
            # A backslash does not hide a name from bash (`read \X`), so it hides it from this count neither.
            bare = re.findall(rf"(?<![\w$./-])(?<!\$\{{)(?<!\$\{{[#!]){escaped}(?![\w./\\-])", self.text)
            # `${X:=…}`, `${X/a/b}`, `${#X}`: an operator the guard does not read. `${X:-…}` and `${X-…}` assign
            # nothing and are read at the target.
            operators = [tail for tail in re.findall(rf"\$\{{[#!]?{escaped}([^}}]*)\}}", self.text)
                         if tail and not tail.startswith((":-", "-"))]
            operators += re.findall(rf"\$\{{[#!]{escaped}", self.text)
            return len(bare) + len(operators)
        flags = re.IGNORECASE
        patterns = (
            rf"\$\{{?{escaped}\}}?\s*(?:[-+*/%]|\?\?)?=(?!=)",  # `$x = …`, `${x}=…`, `$x += …`
            rf"\$\{{?{escaped}\}}?\s*(?:\+\+|--)",
            rf"\$\{{?(?!env:)\w+:{escaped}(?!\w)",  # `$script:x`, `$global:x`, `$using:x`
            rf"\]\s*\$\{{?{escaped}(?!\w)",  # `[string]$x`
            rf"\$\{{?{escaped}\}}?\s*,",  # `$x, $y = …`
            rf"\$\{{?{escaped}\}}?\s+in\b",  # `foreach ($x in …)`
            rf"(?<![\w$:{{./\\-]){escaped}(?![\w./\\-])",  # `Set-Variable x`, `-Name x`, `-OutVariable x`
            rf"-\w+:{escaped}(?!\w)",  # `-OutVariable:x`
        )
        return sum(len(re.findall(pattern, self.text, flags)) for pattern in patterns)

    def _value(self, raw: str) -> list[str] | None:
        """A plain assignment's value as the guard reads it, or None when it cannot."""
        if not unmark(raw):
            return None  # empty, or an array's `X=(…)`
        substitution = SUBSTITUTION_WORD.fullmatch(raw)
        if substitution:
            body = self.inner[int(substitution.group(1))] if int(substitution.group(1)) < len(self.inner) else ""
            if makes_temporary(body, self):
                return [temporary_path()]
            # `TS=$(date +%F)` holds a date, as `$(date +%F)` in the target itself would: a wildcard name part.
            return [RUN_TIME] if not self.powershell and SAFE_SUBSTITUTION.fullmatch(
                body.replace("'", "").replace('"', "")) else None
        if SUBSTITUTION_WORD.search(raw):
            return None
        for match in KNOWN_FOLDER.finditer(raw):
            name = match.group(1) or match.group(2) or match.group(3)
            if name.lower() not in KNOWN_FOLDER_NAMES or self.named(name) or self.sets_environment(name):
                return None
        value = KNOWN_FOLDER.sub(lambda m: known_folder(m.group(1) or m.group(2) or m.group(3), self.cwd, self.root)
                                 or m.group(0), raw)
        return None if built_at_run_time(value) else [value]

    def sets_environment(self, name: str) -> bool:
        """Whether the line sets the environment variable `name` (`$env:TEMP = …`, `Set-Item env:TEMP …`,
        `[Environment]::SetEnvironmentVariable('TEMP', …)`, bash's `TEMP=…`): then it is no known folder."""
        escaped = re.escape(name)
        return bool(re.search(rf"env:{escaped}\}}?\s*[-+]?=|(?<![$\w{{])env:{escaped}(?!\w)|"
                              rf"SetEnvironmentVariable\s*\(\s*['\"]?{escaped}(?!\w)", self.text, re.IGNORECASE)) \
            or (not self.powershell and self.named(name))

    def _read(self, name: str) -> LineVariable | None:
        definitions: list[LineVariable] = []
        for order, command in enumerate(self.commands):
            words = [unmark(word) for word in command.words]
            if self.powershell:
                definitions += self._powershell_definition(name, order, command.words, words)
                continue
            head = next((index for index, word in enumerate(words) if word not in ("do", "then", "else")), 0)
            if words[head:head + 1] in (["for"], ["select"]) and words[head + 2:head + 3] == ["in"]:
                if words[head + 1] == name:
                    values = command.words[head + 3:]
                    literal = bool(values) and not any(built_at_run_time(value) for value in values)
                    definitions.append(LineVariable(values if literal else None, True, order))
                continue
            plain = command.depth == 0 and command.before in (None, ";") and command.after in (None, ";", "&&", "||")
            start = 1 if words and words[0] in DECLARERS else 0
            if plain and len(words) > start and all(ASSIGNMENT.match(word) for word in words[start:]):
                for raw, word in zip(command.words[start:], words[start:]):
                    if word.split("=", 1)[0] == name:
                        definitions.append(LineVariable(self._value(raw.split("=", 1)[1]), False, order))
        bindings = self._bindings(name)
        if not definitions and not bindings:
            return None
        if len(definitions) != 1 or bindings != 1:
            return LineVariable(None)
        return definitions[0]

    def _powershell_definition(self, name: str, order: int, raw: list[str], words: list[str]) -> list[LineVariable]:
        """`$name = <value>` (or `$name=<value>`) as the whole command, and `$name = Join-Path <folder> <child>`."""
        target = "$" + name
        if len(words) == 1 and words[0].lower().startswith(target + "=") and len(words[0]) > len(target) + 1:
            return [LineVariable(self._value(raw[0].split("=", 1)[1]), False, order)]
        if len(words) < 3 or words[0].lower() != target or words[1] != "=":
            return []
        if len(words) == 3:
            return [LineVariable(self._value(raw[2]), False, order)]
        if words[2].lower() == "join-path" and len(words) == 5 and not any(word.startswith("-") for word in words[3:]):
            parent, child = self._value(raw[3]), self._value(raw[4])
            return [LineVariable([parent[0].rstrip("/\\") + "/" + child[0]] if parent and child else None, False, order)]
        return [LineVariable(None, False, order)]


# The kind of an expansion that stands for `$(mktemp)`'s path: a plain path, but one whose `..` the guard does not
# fold — the folder it stands for is not the one mktemp makes.
TEMPORARY = "temporary"
TEMPORARY_PARENT = ("This climbs out of a temporary folder with `..`, to a place the guard cannot name. Write the "
                    "path literally.")


@dataclass
class Resolution:
    """What a write target names once the shell expands it: plain paths, paths with run-time parts (RUN_TIME
    marks where they stand, and whether every such part is a number or a date), or a refusal."""
    paths: list[str]
    dynamic: list[tuple[str, bool]]
    refusal: str | None = None


def resolve_target(raw: str, cwd: str, root: str | None = None, variable: Variables | None = None,
                   powershell: bool = False, order: int = 0, inner: list[str] | None = None) -> Resolution:
    """
    The paths a write target names once the shell expands it (`raw` keeps its LITERAL and DQ marks). A variable the
    line sets plainly stands for its value (LineVariables) — in bash, unquoted, only if the value holds no space or
    glob, and quoted, as text; one the line names in any other way refuses the target. The working, temporary, home,
    settings and project folders are written out ($PWD is the folder the guard follows), with a `${NAME:-default}`
    meaning either. What is left built at run time is marked RUN_TIME for write_path_findings to judge.
    """
    alternatives: list[list[tuple[str, str | None]]] = []
    last = 0
    for match in TARGET_EXPANSION.finditer(raw):
        alternatives.append([(raw[last:match.start()], None)])
        quoted = match.group(0).startswith(DQ)
        options = expansion_values(match, raw, quoted, cwd, root, variable, powershell, order, inner or [])
        if isinstance(options, str):
            return Resolution([], [], options)
        alternatives.append(options)
        last = match.end()
    alternatives.append([(raw[last:], None)])
    count = 1
    for options in alternatives:
        count *= len(options)
    if count > MAX_VALUES:
        return Resolution([], [], "This writes to more paths than the guard reads (a variable holding many). "
                                  "Write the paths literally.")
    paths, dynamic = [], []
    for combination in itertools.product(*alternatives):
        text = "".join(piece for piece, _ in combination).replace(DQ, "")
        if not powershell and re.match(r"~[+-](/|$)", text):  # bash's `~+` is $PWD, `~-` is $OLDPWD
            if text[1] == "-":
                return Resolution([], [], BUILT_AT_RUN_TIME)
            text = str(cwd).replace("\\", "/") + text[2:]
        text = expand_home(text) if text.startswith("~") else text
        temporary = next((index for index, (_, kind) in enumerate(combination) if kind == TEMPORARY), None)
        if temporary is not None and ".." in re.split(r"[/\\]", unmark("".join(
                piece for piece, _ in combination[temporary + 1:]))):
            return Resolution([], [], TEMPORARY_PARENT)
        kinds = [kind for _, kind in combination if kind and kind != TEMPORARY]
        if kinds:
            dynamic.append((unmark(text), all(kind == "safe" for kind in kinds)))
        elif CMD_EXPANSION.search(text) or EXPANSION.search(text):
            dynamic.append((unmark(EXPANSION.sub(RUN_TIME, CMD_EXPANSION.sub(RUN_TIME, text))), False))
        else:
            paths.append(unmark(text))
    return Resolution(paths, dynamic)


def expansion_values(match: re.Match, raw: str, quoted: bool, cwd: str, root: str | None, variable: Variables | None,
                     powershell: bool, order: int, inner: list[str]) -> list[tuple[str, str | None]] | str:
    """What one expansion in a write target stands for: its values (each with None), RUN_TIME with "safe" (a number or
    a date) or "unsafe" (anything), or — a string — why the target is refused."""
    unknown, safe = [(RUN_TIME, "unsafe")], [(RUN_TIME, "safe")]
    if match.group("substitution") is not None:
        number = int(match.group("substitution"))
        body = inner[number] if number < len(inner) else ""
        if makes_temporary(body, variable):
            return [(temporary_path(), TEMPORARY)]
        return safe if SAFE_SUBSTITUTION.fullmatch(body.replace("'", "").replace('"', "")) else unknown
    if match.group("special") is not None:
        return safe if match.group("special") in SAFE_PARAMETERS else unknown
    environment = match.group("braced_env") or match.group("env") or match.group("cmd")
    if environment:
        folder = known_folder(environment, cwd, root)
        return [(folder, None)] if folder and not (variable and variable.sets_environment(environment)) else unknown
    name = match.group("braced") or match.group("plain")
    tail = match.group("tail") or ""
    default: list[tuple[str, str | None]] = []
    if tail:
        if not tail.startswith((":-", "-")):
            return BUILT_AT_RUN_TIME
        text = tail[2:] if tail.startswith(":-") else tail[1:]
        if built_at_run_time(text):
            return BUILT_AT_RUN_TIME
        default = [(text, None)]
    following = raw[match.end():match.end() + 1]
    # bash splits an unquoted expansion on IFS and globs each piece: one the line changes splits anything.
    splits = not powershell and not quoted
    if splits and variable is not None and variable.named("IFS"):
        return DISTRUSTED_VARIABLE
    if splits and any(not UNQUOTED_PLAIN.fullmatch(unmark(text)) for text, _ in default):
        return DISTRUSTED_VARIABLE
    line = variable(name, order) if variable else None
    if line is not None:
        if line.values is None or line.order >= order:
            return DISTRUSTED_VARIABLE
        if powershell and not quoted and following in (".", "["):
            return BUILT_AT_RUN_TIME  # a property or an element of what it holds, not the value itself
        values: list[tuple[str, str | None]] = []
        for value in line.values:
            if value == RUN_TIME:  # set from `$(date …)`: a number or a date
                values += safe
                continue
            text = unmark(value)
            if powershell:
                values += [(part, None) for part in value.split(",") if part]
                continue
            # Unquoted, bash splits a space and globs `*?[` and extglob's `@(…)`; a loop's own glob words are meant.
            if splits and (re.search(r"\s", text) if line.loop else not UNQUOTED_PLAIN.fullmatch(text)):
                return DISTRUSTED_VARIABLE
            values.append((value.translate(TEXT_GLOB) if quoted and not line.loop else value,
                           TEMPORARY if value == temporary_path() else None))
        return values + default
    folder = known_folder(name, cwd, root)
    if folder and not (variable and variable.sets_environment(name)) and \
            not (powershell and following in (".", "[") and not quoted):
        if splits and re.search(r"\s", folder):
            return DISTRUSTED_VARIABLE  # `rm -rf $HOME/x` with a space in the home folder's name removes two paths
        return [(folder, None)] + default
    if name.lower() in SAFE_VARIABLES and not powershell:
        return safe
    return unknown


def expanded_targets(raw: str, cwd: str, variable: Variables | None = None) -> list[str] | None:
    """The paths `raw` names once expanded, or None when any of it is built at run time or refused (git's and
    find's paths, which name what they act on whole)."""
    resolution = resolve_target(raw, cwd, variable=variable)
    return None if resolution.refusal or resolution.dynamic else resolution.paths


def temporary_folders() -> list[str]:
    folders = {tempfile.gettempdir(), os.environ.get("TMPDIR") or "", os.environ.get("TEMP") or "",
               os.environ.get("TMP") or "", "/tmp"}
    return [normal_path(folder).rstrip("/") for folder in folders if folder]


def run_time_findings(text: str, safe: bool, cwd: str, repo: Repo, whole: bool) -> list[Finding]:
    """
    A write target part of which is built when it runs. Under the temporary folder — when that folder is
    neither inside nor above the repository or Claude Code's settings — it is outside the repository whatever the rest is
    (`/tmp/$$.txt`, `"$TMPDIR/$name.log"`). Elsewhere only a name part made of a number or a date is read
    (`out-$(date +%s).txt`), as a wildcard no protected path can match; a whole name or folder built when it runs
    (`$(git ls-files)`, `$X/b`) is refused.
    """
    fixed, _, rest = text.partition(RUN_TIME)
    if ".." in re.split(r"[/\\]", rest.replace(RUN_TIME, "x")):
        return [hidden(BUILT_AT_RUN_TIME)]
    fixed_folder = fixed.rsplit("/", 1)[0] if "/" in fixed else ""
    if fixed_folder:
        absolute = normal_path(fixed_folder, cwd).rstrip("/")
        holds = [normal_path(repo.root).rstrip("/"),
                 normal_path(known_folder("claude_config_dir", cwd) or "~/.claude").rstrip("/")]
        if any(absolute == temp or absolute.startswith(temp + "/") for temp in temporary_folders()) \
                and not any(held == absolute or held.startswith(absolute + "/") or absolute.startswith(held + "/")
                            for held in holds):
            return []  # a temporary folder, neither inside the repository or the settings nor holding them
    folder, _, name = text.rpartition("/")
    if not safe or RUN_TIME in folder or not name.replace(RUN_TIME, ""):
        return [hidden(BUILT_AT_RUN_TIME)]
    pattern = text.replace(RUN_TIME, "*")
    rel = whole_target(pattern, cwd, repo.root)
    if rel is None:
        return claude_settings_findings(pattern, cwd, folder_too=True)
    if whole:
        return path_findings(rel, repo, through_shell=True, whole_folder=True)
    return path_findings(rel, repo, through_shell=True) + glob_findings(rel, repo, unknown=True)


def write_path_findings(target: str, cwd: str, repo: Repo, whole: bool = False, raw: str | None = None,
                        variable: Variables | None = None, powershell: bool = False, order: int = 0,
                        inner: list[str] | None = None) -> list[Finding]:
    """
    What writing `target` — or, `whole`, removing or moving it with all it holds — reaches. `raw` is the
    same word with its LITERAL marks; a target built when it runs is judged on what it can name or refused
    (resolve_target, run_time_findings). PowerShell's `a,b` is two targets.
    """
    resolution = resolve_target(target if raw is None else raw, cwd, repo.root, variable, powershell, order, inner)
    if resolution.refusal:
        return [hidden(resolution.refusal)]
    paths = [part for path in resolution.paths for part in (path.split(",") if powershell else [path]) if part]
    findings = [finding for each in paths for finding in written_path_findings(each, cwd, repo, whole)]
    for text, safe in resolution.dynamic:
        findings += run_time_findings(text, safe, cwd, repo, whole)
    return findings


def written_path_findings(target: str, cwd: str, repo: Repo, whole: bool) -> list[Finding]:
    findings = git_config_file_findings(target, cwd)
    # A glob is kept as one, from the folder it starts in, so path_findings can judge what it matches.
    globbed = any(char in target for char in "*?[")
    owner = other_worktree(target, cwd, repo) or repo  # another worktree's file keeps its own runs' locks
    rel = whole_target(target, cwd, owner.root) if whole or globbed else relative_to_root(target, cwd, owner.root)
    if rel:
        findings += path_findings(rel, owner, through_shell=True, whole_folder=whole)
        if not isinstance(cwd, UnknownCwd):
            return findings
    return findings + claude_settings_findings(target, cwd, folder_too=True)


def change_dir(cwd: str, target: str, repo: Repo) -> str:
    """The folder `cd <target>` moves to from `cwd`: `~`, `$HOME`, `$env:USERPROFILE` and the like written
    out, and an UnknownCwd where the guard cannot name it — a variable, a substitution, `-`."""
    text = expand_known(target)
    if not text or text == "-" or text.startswith("~") or any(char in text for char in "$%`") \
            or SUBSTITUTION_WORD.search(text):
        return UnknownCwd(repo.root)
    native = to_native(text)
    if isinstance(cwd, UnknownCwd) and not os.path.isabs(native):
        return UnknownCwd(repo.root)
    return os.path.normpath(os.path.join(cwd, native))


# The hooks folders core.hooksPath may name when `[guard] hooks_path` names none: `.githooks` (this repository's,
# and the kit's), and husky's — `.husky` (husky 4–8) and `.husky/_` (husky 9's `prepare` step) — the latter only
# where the base branch keeps a `.husky/` folder (DEFAULT_HOOKS_ON_BASE). A project with no husky hooks that
# accepted `.husky` would let a session skip its hooks by pointing git at a folder that holds none.
DEFAULT_HOOKS = (".githooks",)
DEFAULT_HOOKS_ON_BASE = {".husky": ".husky", ".husky/_": ".husky"}


def hooks_folder_name(value: str) -> str:
    """A core.hooksPath value as a comparable folder: no quotes, no `./`, no trailing slash, lower case."""
    text = value.strip("\"'").replace("\\", "/").rstrip("/").lower()
    while text.startswith("./"):
        text = text[2:]
    return text


def project_hooks_folder(value: str, repo: Repo) -> bool:
    """
    Whether core.hooksPath = `value` names this project's own hooks: what `[guard] hooks_path` lists, or
    without it the defaults above. Any other folder is skipping the hooks. A path that leaves its folder
    (`.husky/../tmp`) is not the folder.
    """
    name = hooks_folder_name(value)
    if not name or ".." in name.split("/"):
        return False
    try:
        configured = rules().hooks_path
    except RulesUnavailable:
        configured = ()
    if configured:
        return name in {hooks_folder_name(folder) for folder in configured}
    if name in DEFAULT_HOOKS:
        return True
    folder = DEFAULT_HOOKS_ON_BASE.get(name)
    return folder is not None and repo.on_base_folder(folder)
SESSION_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
HOOKS_PATH_IN_ENV = re.compile(r"^(GIT_CONFIG_KEY_\d+=core\.hookspath|GIT_CONFIG_PARAMETERS=.*core\.hookspath)",
                               re.IGNORECASE)
MARKER_CLEARING_PROGRAMS = frozenset({
    "unset", "export", "declare", "typeset", "local", "readonly",
    "remove-item", "ri", "rm", "del", "erase", "clear-item", "set-item", "si", "remove-variable", "rv",
})
SET_ENV_CALL = re.compile(r"SetEnvironmentVariable\s*\(\s*['\"]?(" + "|".join(SESSION_MARKERS) + r")\b",
                          re.IGNORECASE)
POSIX_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
POWERSHELL_HOSTS = frozenset({"pwsh", "powershell"})
# pwsh's and powershell's own options that take a value; `-Command`, `-File` and `-EncodedCommand` are read apart.
POWERSHELL_HOST_OPTIONS_WITH_VALUE = ("-executionpolicy", "-windowstyle", "-workingdirectory", "-configurationname",
                                      "-inputformat", "-outputformat", "-psconsolefile", "-settingsfile",
                                      "-custompipename", "-configurationfile", "-ep", "-ex", "-wd", "-if", "-of")
# `start /b x` and `start "" x` launch x (cmd's start, from bash): x is what is judged.
LAUNCHERS = frozenset({"start"})


def takes_a_value(option: str) -> bool:
    """argparse accepts any unambiguous prefix: `--ru R-002` is `--run R-002` (the re-review)."""
    return "=" not in option and len(option) >= len("--r") and any(o.startswith(option) for o in FACTORY_OPTIONS_WITH_VALUE)


def is_person_only_factory_command(argv: list[str]) -> bool:
    """The subcommand after factory.py — skipping options and the values they take, so
    `factory.py --run R-002 approve spec` is still an approval (the review found it was not)."""
    for index, token in enumerate(argv):
        if program(token) not in ("factory", "factory.py"):
            continue
        rest = argv[index + 1:]
        position = 0
        while position < len(rest) and rest[position].startswith("-"):
            position += 2 if takes_a_value(rest[position]) else 1
        return position < len(rest) and rest[position] in PERSON_ONLY_COMMANDS
    return False


def marker_name(token: str) -> str:
    """The variable a token names — `$env:CLAUDECODE`, `Env:CLAUDECODE`, `CLAUDECODE=` — upper-cased."""
    name = token.split("=", 1)[0]
    for prefix in ("$env:", "env:"):  # PowerShell's drive, in any case
        if name.lower().startswith(prefix):
            name = name[len(prefix):].lstrip("/\\")  # `Env:\CLAUDECODE` reaches here as `Env:/CLAUDECODE`
    return name.upper()


def tampers_with_session_marker(segment: list[str]) -> bool:
    """Clearing or blanking CLAUDECODE is how a session would pass for a person's terminal."""
    first = program(segment[0]) if segment else ""
    for index, token in enumerate(segment):
        if any(ch.isspace() for ch in token):
            continue  # a quoted message or argument, not an assignment
        name = marker_name(token)
        following = segment[index + 1] if index + 1 < len(segment) else ""
        if name in SESSION_MARKERS and ("=" in token or following.startswith("=")):
            return True  # `CLAUDECODE=`, `$env:CLAUDECODE=''`, `$env:CLAUDECODE = $null`
        if name in SESSION_MARKERS and first in MARKER_CLEARING_PROGRAMS:
            return True  # unset, export -n, declare +x, Remove-Item Env:…
        if token.startswith("--unset=") and marker_name(token[len("--unset="):]) in SESSION_MARKERS:
            return True
        if name in SESSION_MARKERS and index > 0 and segment[index - 1] in ("-u", "--unset", "unset"):
            return True
        if program(token) == "env" and following in ("-i", "--ignore-environment", "-"):
            return True  # `env -i` clears every variable, the marker with them
    return False



# PowerShell's Start-Process: its program and its -ArgumentList are what runs.
START_PROCESS = frozenset({"start-process", "saps", "start"})
START_PROCESS_OPTIONS_WITH_VALUE = ("-filepath", "-argumentlist", "-workingdirectory", "-redirectstandardoutput",
                                    "-redirectstandarderror", "-redirectstandardinput", "-verb", "-windowstyle",
                                    "-credential", "-environment", "-path", "-pspath", "-args")
CMD_VARIABLE = re.compile(r"%[^%\s]+%|![^!\s]+!")
# A `$` the shell expands: a name, `${`, `$(`, or a special parameter — not a lone `$`.
EXPANSION = re.compile(r"\$[\w{(@*#?!$-]")
REDIRECT_WORD = re.compile(r"<<<|<<-|<<|<|>>|>|>&|<&|&>|&>>|>\||<>")
# Substitutions whose answer the guard knows: the checkout's own branch, and its own top folder.
CURRENT_BRANCH = re.compile(r"\s*git\s+(branch\s+--show-current|rev-parse\s+--abbrev-ref\s+HEAD|"
                            r"symbolic-ref\s+--short\s+HEAD)\s*")
SHOW_TOPLEVEL = re.compile(r"\s*git\s+rev-parse\s+--show-toplevel\s*")
# PowerShell that turns text into a script block and runs it.
SCRIPT_BLOCK_BUILDERS = re.compile(r"\[\s*(system\.management\.automation\.)?scriptblock\s*\]|invokescript|"
                                   r"newscriptblock", re.IGNORECASE)


def redirect_words(argv: list[str]) -> set[int]:
    """The positions in `argv` (past the command's name) of the shell's redirects — the operator, its file
    or descriptor, and a descriptor number before it (`2>&1`, `> out`, `<<EOF`): none is an argument."""
    dropped: set[int] = set()
    for index, word in enumerate(argv[1:], 1):
        following = argv[index + 1] if index + 1 < len(argv) else ""
        if (REDIRECT_WORD.fullmatch(word) or (word.isdigit() and REDIRECT_WORD.fullmatch(following))
                or (index > 1 and REDIRECT_WORD.fullmatch(argv[index - 1]))):
            dropped.add(index)
    return dropped


def pdftotext_prints(argv: list[str]) -> bool:
    """Whether pdftotext's last argument — redirects aside — is `-`, so it prints rather than writes a file."""
    dropped = redirect_words(argv)
    words = [argv[index] for index in range(1, len(argv)) if index not in dropped]
    return bool(words) and words[-1] == "-"


def nested_findings(argv: list[str], raw: list[str], cwd: str, repo: Repo, depth: int,
                    xargs_replaces: bool) -> list[Finding] | None:
    """
    What a shell started by this command runs — `bash -c '…'`, `pwsh -Command …`, `cmd /c …` — judged as
    if typed; None when the command starts no shell. A script built when it runs (a `$` the outer shell
    expands, `%VAR%`, a substitution, xargs' input) and a shell left to read its commands from its input
    (`… | bash`, `bash <<EOF`) are refused: the guard cannot see them.
    """
    name = program(argv[0])
    if name not in POSIX_SHELLS | POWERSHELL_HOSTS | {"cmd"}:
        return None
    # A redirect and its word are not the shell's arguments: `bash <<EOF` and `bash < x` read their input.
    dropped = redirect_words(argv)
    kept = [index for index in range(1, len(argv)) if index not in dropped]
    args, raw_args = [argv[index] for index in kept], [raw[index] for index in kept]
    stdin = [hidden(f"`{name}` reads its commands from its input.")]

    def judge(raw_text: str, powershell: bool, where: str = cwd, cmd: bool = False) -> list[Finding]:
        if not raw_text.strip():
            return stdin
        if "$" in raw_text or SUBSTITUTION_WORD.search(raw_text) or (cmd and CMD_VARIABLE.search(raw_text)):
            return [hidden(f"`{name}` runs a script built when it runs.")]
        if xargs_replaces:
            return [hidden("xargs puts its input inside this script.")]
        return shell_findings(unmark(raw_text), where, repo, powershell, depth + 1)

    if name == "cmd":
        for index, arg in enumerate(args):
            if arg.lower() in ("/c", "/k", "/r"):
                text = " ".join(raw_args[index + 1:])
                if "'" in text or "^" in text:
                    return [hidden("cmd's own quoting (`^`, a `'` that is text to it) is not read by the guard.")]
                # Judged with PowerShell's tokenizing, which keeps Windows paths' backslashes as separators.
                return judge(text, True, cmd=True)
        return [] if "/?" in args else stdin
    if name in POSIX_SHELLS:
        index, command_string = 0, False
        while index < len(args):
            arg = args[index]
            if arg in ("--version", "--help"):
                return []
            if arg == "-" or (arg.startswith("-") and not arg.startswith("--") and "s" in arg[1:]):
                return stdin
            if arg == "--":
                index += 1
                break
            if arg.startswith("--"):
                index += 2 if arg in ("--rcfile", "--init-file") else 1
                continue
            if len(arg) > 1 and arg[0] in "-+":
                command_string = command_string or (arg[0] == "-" and "c" in arg[1:])
                index += 2 if arg[-1] in "oO" else 1
                continue
            break
        if command_string:
            return judge(raw_args[index] if index < len(raw_args) else "", False)
        return [] if index < len(args) else stdin  # a script file runs; nothing else is read from the input
    if name in POWERSHELL_HOSTS:
        index, where = 0, cwd
        while index < len(args):
            arg = args[index]
            if not arg or arg[0] not in "-/":
                # Windows PowerShell runs a bare word as a command; pwsh runs it as a script file.
                return judge(" ".join(raw_args[index:]), True, where) if name == "powershell" else []
            option, _, attached = ("-" + arg[1:].lower()).partition(":")
            if len(option) >= 2 and "-command".startswith(option):
                rest = ([attached] if attached else []) + raw_args[index + 1:]
                return stdin if rest[:1] == ["-"] else judge(" ".join(rest), True, where)
            if len(option) >= 2 and "-file".startswith(option):
                return stdin if (args[index + 1:index + 2] or [attached or "-"]) == ["-"] else []
            if option in ("-e", "-ec") or (len(option) >= 3 and "-encodedcommand".startswith(option)):
                return []  # refused as an encoded command
            if option in ("-?", "-h", "-help") or (len(option) >= 2 and "-version".startswith(option)):
                return []
            if len(option) >= 2 and any(full.startswith(option) for full in POWERSHELL_HOST_OPTIONS_WITH_VALUE):
                value = attached or (args[index + 1] if index + 1 < len(args) else "")
                if option in ("-wd",) or (len(option) >= 3 and "-workingdirectory".startswith(option)):
                    where = change_dir(where, value, repo)
                index += 1 if attached else 2
                continue
            index += 1
        return stdin
    return None


SESSION_MARKER_FINDING = Finding(
    "session-marker", ALWAYS,
    "Clearing or blanking CLAUDECODE would make this session pass for a person's terminal. "
    "A person's decisions are made from their own terminal.", None)


def start_process_findings(argv: list[str], raw: list[str], cwd: str, repo: Repo, depth: int) -> list[Finding]:
    """PowerShell's `Start-Process <program> -ArgumentList …` runs the program with those arguments: they are
    judged as typed, or refused when built at run time."""
    values: dict[str, tuple[str, str]] = {}
    positional: list[tuple[str, str]] = []
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg.startswith("-") and len(arg) > 1:
            option, _, attached = arg.lower().partition(":")
            full = next((name for name in START_PROCESS_OPTIONS_WITH_VALUE if len(option) >= 2 and name.startswith(option)),
                        None)
            if full and attached:
                values[full] = (attached, attached)
                index += 1
            elif full:
                values[full] = (argv[index + 1], raw[index + 1]) if index + 1 < len(argv) else ("", "")
                index += 2
            else:
                index += 1
            continue
        positional.append((arg, raw[index]))
        index += 1
    file = values.get("-filepath") or values.get("-path") or values.get("-pspath") or (positional[:1] or [("", "")])[0]
    arguments = values.get("-argumentlist") or values.get("-args") or (positional[1:2] or [("", "")])[0]
    where = change_dir(cwd, values["-workingdirectory"][0], repo) if "-workingdirectory" in values else cwd
    findings = [finding for key in ("-redirectstandardoutput", "-redirectstandarderror") if key in values
                for finding in write_path_findings(values[key][0], where, repo)]
    raw_text = f"{file[1]} {arguments[1]}"
    if not file[0] or "$" in raw_text or SUBSTITUTION_WORD.search(raw_text) or arguments[0].startswith("@"):
        return findings + [hidden("Start-Process runs a program or arguments built when it runs.")]
    text = quoted(unmark(file[1])) + " " + unmark(arguments[1]).replace(",", " ")
    return findings + shell_findings(text, where, repo, True, depth + 1)


HOOKS_PATH_FINDING = Finding(
    "no-verify", DENY, "Never skip the git hooks. If a hook fails, fix what it found.", None)
GIT_ALIAS_FINDING = Finding(
    "hidden-command", DENY,
    "A git alias hides the command it runs from the guard. Run the git command itself.", None)
ALIAS_IN_ENV = re.compile(r"^(GIT_CONFIG_KEY_\d+=alias\.|GIT_CONFIG_PARAMETERS=.*alias\.)", re.IGNORECASE)

# Deeper than this, a command inside a command inside a command… is refused rather than judged.
MAX_NESTING = 5
EVALUATORS = frozenset({"eval"})
POWERSHELL_EVALUATORS = frozenset({"invoke-expression", "iex"})
# Programs that run text they are given: a found file's name handed to one is a command no one has read.
RUNS_TEXT = POSIX_SHELLS | POWERSHELL_HOSTS | EVALUATORS | POWERSHELL_EVALUATORS | {"cmd", "git", "xargs", "find"}


def hidden(reason: str) -> Finding:
    return Finding("hidden-command", DENY, f"{reason} The guard cannot see what it would run, so it refuses it.", None)


def encoded_command(argv: list[str]) -> bool:
    """`pwsh -EncodedCommand <base64>` (or -enc, -e, -ec): a command no one can read before it runs. Every
    host option is read up to -Command or -File, past the values of options like -ExecutionPolicy."""
    if program(argv[0]) not in POWERSHELL_HOSTS:
        return False
    for arg in argv[1:]:
        if not arg or arg[0] not in "-/":
            continue  # a value, or a script: either way not the end of the host's own options
        option = ("-" + arg.lower()[1:]).partition(":")[0]
        if option in ("-e", "-ec") or (len(option) >= 3 and "-encodedcommand".startswith(option)):
            return True
        if len(option) >= 2 and ("-command".startswith(option) or "-file".startswith(option)):
            return False
    return False


def evaluated_findings(name: str, args: list[str], raw_args: list[str], cwd: str, repo: Repo, depth: int,
                       xargs_replaces: bool = False) -> list[Finding]:
    """`eval '<command>'` and `Invoke-Expression '<command>'` run their text: it is judged as typed. Text
    built at run time — a variable, a substitution, piped input — cannot be seen, so it is refused."""
    powershell = name in POWERSHELL_EVALUATORS
    kept = [raw_args[index] for index, arg in enumerate(args)
            if not (powershell and len(arg) >= 2 and "-command".startswith(arg.lower()))]
    text = " ".join(kept)
    if not text.strip() or "$" in text or SUBSTITUTION_WORD.search(text) or xargs_replaces:
        return [hidden(f"`{name}` runs text built when it runs.")]
    return shell_findings(unmark(text), cwd, repo, powershell, depth + 1)


FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
FIND_FILE_WRITERS = {"-fprint": 1, "-fprint0": 1, "-fls": 1, "-fprintf": 2}
# Expressions after which one -name no longer narrows what find reaches.
FIND_WIDENING = frozenset({"-o", "-or", ",", "!", "-not", "-path", "-ipath", "-wholename", "-iwholename", "-regex",
                           "-iregex"})


def find_findings(args: list[str], raw_args: list[str], cwd: str, repo: Repo, depth: int) -> list[Finding]:
    """
    `find <folders> … -exec <command> {} ;` runs the command on what it finds, `-delete` removes it and
    `-fprint` writes a file. `{}` is judged as everything the search can reach — `<folder>/**/<name>`
    for one `-name`, else the folder whole — and a found name handed to a program that runs text is refused.
    """
    index = 0
    while index < len(args) and (args[index] in ("-H", "-L", "-P", "-D") or args[index].startswith("-O")):
        index += 2 if args[index] == "-D" else 1
    starts: list[str] = []
    findings: list[Finding] = []
    while index < len(args) and not args[index].startswith("-") and args[index] not in ("(", ")", "!", ","):
        # `find "$PWD/requirements" -delete` starts where $PWD is; `find "$X"` starts somewhere unknown.
        expanded = expanded_targets(raw_args[index], cwd)
        if expanded is None and any(word in FIND_EXEC or word in FIND_FILE_WRITERS or word == "-delete" for word in args):
            findings.append(hidden("find acts on what it finds from a folder built when it runs."))
        starts += expanded or []
        index += 1
    expression, raw_expression = args[index:], raw_args[index:]
    names = find_names(expression)
    reached = [f"{start.rstrip('/') or '/'}/**/{each}" for start in starts or ["."] for each in names] if names \
        else starts or ["."]
    position = 0
    while position < len(expression):
        word = expression[position]
        if word in FIND_FILE_WRITERS and position + 1 < len(expression):
            findings += write_path_findings(expression[position + 1], cwd, repo)
            position += 1 + FIND_FILE_WRITERS[word]
            continue
        if word == "-delete":
            findings += shell_findings("rm -rf " + " ".join(quoted(target) for target in reached), cwd, repo, False,
                                       depth + 1)
        elif word in FIND_EXEC:
            end = position + 1
            while end < len(expression) and raw_expression[end] not in (LITERAL[";"], ";", "+"):
                end += 1
            words = expression[position + 1:end]
            command = unwrap(words, raw_expression[position + 1:end], cwd, repo, False)
            if words and any("{}" in w for w in words) and command.argv and program(command.argv[0]) in RUNS_TEXT:
                findings.append(hidden("find hands the names it finds to a command that runs text."))
            elif word in ("-execdir", "-okdir") and command.argv and not judged_where_typed(command.argv):
                findings.append(hidden(EXECDIR_ELSEWHERE))
            elif any(".." in re.split(r"[/\\]", w.split("{}", 1)[1]) for w in words if "{}" in w):
                # `{}` is folded with the `..` after it, so `-exec rm -rf {}/../Migrations` climbed out of
                # `**/bin` to a root-level name.
                findings.append(hidden(FIND_CLIMBS))
            elif words:
                for target in reached:
                    text = " ".join(quoted(w.replace("{}", target)) for w in words)
                    findings += shell_findings(text, cwd, repo, False, depth + 1)
            position = end + 1
            continue
        position += 1
    return findings


def known_substitution(word: str, inner: list[str], pattern: re.Pattern) -> bool:
    """Whether `word` is a placeholder for a substitution whose command is `pattern` — whose answer the guard knows."""
    found = SUBSTITUTION_WORD.fullmatch(word.strip())
    return bool(found) and int(found.group(1)) < len(inner) and bool(pattern.fullmatch(inner[int(found.group(1))]))


# Writers that take what they write from a pipe when no path is named on them: PowerShell's cmdlets read a
# listed item's path from its input (`Get-ChildItem requirements | Remove-Item`), and xargs puts its input on the
# command line of whatever it runs (`ls requirements/fsd/* | xargs rm`).
PIPE_FED_CMDLETS = frozenset({"remove-item", "ri", "del", "erase", "rd", "rmdir", "rm", "move-item", "mi", "mv", "move",
                              "copy-item", "cpi", "cp", "copy", "rename-item", "rni", "ren", "set-content", "sc",
                              "add-content", "ac", "clear-content", "clc", "set-item", "si", "clear-item", "cli"})
# Commands a listing passes through on its way to a writer, its paths unchanged as far as the guard can tell.
PIPE_FILTERS = frozenset({"where-object", "where", "?", "select-object", "select", "sort-object", "sort", "grep", "head",
                          "tail", "uniq"})
# find's actions that print something other than the paths it finds, or nothing on its output:
# `-printf '%h'` prints the folder holding each, `-exec dirname {} ;` another command's output, `-ls` a long listing.
FIND_PRINT_CHANGERS = FIND_EXEC | frozenset(FIND_FILE_WRITERS) | frozenset({"-printf", "-ls"})
# Select-Object's options that pass the objects handed to it unchanged; the rest (`-ExpandProperty Parent`,
# `-Property`, a positional property) hand on something else.
SELECT_KEEPING = ("-first", "-last", "-skip", "-skiplast", "-index", "-unique", "-wait")
SELECT_KEEPING_WITH_VALUE = ("-first", "-last", "-skip", "-skiplast", "-index")
SELECT_CHANGING = ("-property", "-excludeproperty", "-expandproperty", "-inputobject", "-caseinsensitive")
# grep's options that print the matching lines whole; any other (`-o`, `-n`, `-c`, `-b`, `-H`) prints something else.
GREP_KEEPING_LETTERS = frozenset("vixwEFGPsqz")
GREP_KEEPING_LONG = frozenset({"--invert-match", "--ignore-case", "--word-regexp", "--line-regexp",
                               "--extended-regexp", "--fixed-strings", "--basic-regexp", "--perl-regexp",
                               "--no-messages", "--quiet", "--silent", "--null-data", "--regexp", "--file"})
XARGS_INSIDE_WORD = ("xargs puts each path it is handed inside a longer word here, so the writer acts on somewhere "
                     "else. Hand it the path whole (`rm -rf {}`), or name the paths on the command itself.")
FIND_CLIMBS = ("find hands each path it finds to a command that climbs out of it with `..`. Act on `{}` itself, or "
               "name the paths on the command itself.")
# `-execdir` and `-okdir` run their command in each found item's folder, while the guard judges it from the working
# one (once: `-execdir sh -c 'rm -rf ../Migrations'` and `-execdir git restore ../Migrations` passed). Only a
# reader, or a writer whose only path is `{}`, is judged as typed; anything else is refused.
EXECDIR_READERS = CONTENT_SEARCHERS | frozenset({"cat", "head", "tail", "less", "more", "wc", "file", "ls", "stat",
                                                 "du", "md5sum", "sha1sum", "sha256sum", "echo", "printf", "basename",
                                                 "dirname", "realpath", "readlink", "diff", "cmp"})
EXECDIR_WRITERS = WRITES_ALL_ARGS | WRITES_DESTINATION | CONTENT_WRITERS | IN_PLACE_EDITORS
EXECDIR_ELSEWHERE = ("-execdir runs its command in each folder it finds, not this one. Name the paths, or use "
                     "-exec.")


def judged_where_typed(argv: list[str]) -> bool:
    """Whether an `-execdir` command does the same in a found item's folder as in the working one: a reader that
    does not climb with `..`, or a writer handed nothing but `{}` and its options."""
    name = program(argv[0])
    if name in EXECDIR_READERS:
        return not any(re.search(r"(^|[/\\=])\.\.([/\\]|$)", arg) for arg in argv[1:])
    # An option holding a value (`--target-directory=../x`) is a path the guard would judge from the wrong folder.
    return name in EXECDIR_WRITERS and all(arg == "{}" or re.fullmatch(r"--?[A-Za-z][A-Za-z-]*", arg)
                                           for arg in argv[1:])


def changes_paths(name: str, args: list[str], powershell: bool) -> bool:
    """
    Whether a listing or a filter on its way to a writer hands on something other than the paths it lists or is
    handed: `-printf '%h'` or `Select-Object -ExpandProperty Parent` hands it the folders that hold them,
    which the guard would otherwise judge as the paths listed.
    """
    lowered = [arg.lower() for arg in args]
    if name == "find" and not powershell:
        return bool(FIND_PRINT_CHANGERS & set(args))
    if name in ("select-object", "select") and powershell:
        skip = False
        for arg in lowered:
            if skip:
                skip = False
            elif not arg.startswith("-") or len(arg) < 2 or any(option.startswith(arg) for option in SELECT_CHANGING) \
                    or not any(option.startswith(arg) for option in SELECT_KEEPING):
                return True
            else:
                skip = any(option.startswith(arg) for option in SELECT_KEEPING_WITH_VALUE)
        return False
    if name == "grep":
        skip = False
        for arg in args:
            if skip:
                skip = False
            elif arg.startswith("--"):
                if arg.split("=", 1)[0] not in GREP_KEEPING_LONG:
                    return True
                skip = arg in ("--regexp", "--file")
            elif arg.startswith("-") and len(arg) > 1:
                letters = arg[1:]
                for position, letter in enumerate(letters):
                    if letter in "ef":
                        skip = position == len(letters) - 1  # `-e PATTERN`; `-ePATTERN` holds its value
                        break
                    if letter not in GREP_KEEPING_LETTERS:
                        return True
        return False
    if name in ("head", "tail"):
        return any(arg.startswith(("-c", "--bytes")) for arg in args)
    if name == "uniq":
        return any(arg == "--count" or (re.fullmatch(r"-[A-Za-z]+", arg) and "c" in arg) for arg in args)
    return False


# Of those, the ones that copy or move what they are handed into a destination.
PIPE_COPIERS = frozenset({"copy-item", "cpi", "cp", "copy"})
PIPE_MOVERS = frozenset({"mv", "move-item", "mi", "move"})
PIPE_FED_UNKNOWN = ("This writes the paths it is handed through a pipe or by xargs, and the guard cannot read them "
                    "from what feeds it. Name the paths on the command itself.")
NUL_FINDING_REASON = "The command holds a NUL character, which no path or word the guard reads can hold."


# PowerShell switches a listing or writer takes, and the colon values that turn one on or off.
POWERSHELL_SWITCHES = ("-recurse", "-force", "-file", "-directory", "-hidden", "-system", "-readonly", "-name",
                       "-followsymlink", "-whatif", "-confirm")
SWITCH_VALUES = {"true": True, "1": True, "false": False, "0": False}


def is_switch(parameter: str) -> bool:
    """Whether `-X` abbreviates one of POWERSHELL_SWITCHES, as PowerShell accepts any unambiguous prefix."""
    lowered = parameter.lower()
    return len(lowered) >= 2 and any(switch.startswith(lowered) for switch in POWERSHELL_SWITCHES)


def colon_parameters(argv: list[str], raw: list[str]) -> tuple[list[str], list[str]]:
    """PowerShell's `-Path:value` as `-Path value` (once: `Set-Content -Path:requirements/… -Value x` wrote past the
    path rules). A switch's `-Recurse:$true` is the switch alone."""
    words, raws = argv[:1], raw[:1]
    for word, raw_word in zip(argv[1:], raw[1:]):
        match = re.fullmatch(r"(-[A-Za-z]+):(.+)", word, re.DOTALL)
        if match:
            switch, value = match.group(1), match.group(2).strip().lower()
            if is_switch(switch) and value.lstrip("$") in SWITCH_VALUES:
                # `-Recurse:1` is the switch on, as `-Recurse:$true` is; `:0` and `:$false` are it off.
                if SWITCH_VALUES[value.lstrip("$")]:
                    words.append(switch)
                    raws.append(switch)
                continue
            raw_value = raw_word[len(switch) + 1:]
            words.append(switch)
            raws.append(switch)
            if value not in ("$true", "$false"):
                words.append(match.group(2))
                raws.append(raw_value)
            continue
        words.append(word)
        raws.append(raw_word)
    return words, raws


def is_folder_target(landing: str, resolved: list[str], cwd: str, repo: Repo, sources: int,
                     named_by_option: bool) -> bool:
    """Whether a copy or move's destination is a folder the sources land in under their own names: written with a
    trailing slash, named by `-t`/`--target-directory`, the destination of several sources, or a folder now.
    `resolved`: the paths the destination names once expanded (resolve_target) — once, `"$CLAUDE_PROJECT_DIR/docs"`
    was asked about unexpanded, read as no folder, and `cp x/database-erd.md "$CLAUDE_PROJECT_DIR/docs"` passed."""
    if named_by_option or sources > 1 or landing.endswith(("/", "\\")):
        return True
    for path in resolved:
        rel = relative_to_root(path, cwd, repo.root)
        if (repo.is_folder(rel) if rel else os.path.isdir(to_native(expand_home(path)))):
            return True
    return False


def shell_findings(command: str, cwd: str, repo: Repo, powershell: bool, depth: int = 0) -> list[Finding]:
    if len(command) > MAX_COMMAND:
        return [hidden(f"The command is over {MAX_COMMAND // 1024} KB, longer than the guard reads within its "
                       "time limit.")]
    if depth > MAX_NESTING:
        return [hidden("This nests commands deeper than the guard follows.")]
    if depth == 0 and (any(mark in command for mark in GUARD_MARKS) or "__guard_substitution_" in command):
        return [hidden("The command holds the characters the guard uses to mark what it has read.")]
    if "\0" in command:
        return [hidden(NUL_FINDING_REASON)]
    if powershell:
        command = command.translate(POWERSHELL_LOOKALIKES)
    try:
        kept, expanded = split_heredocs(command, powershell)
        outer, inner = lift_substitutions(kept, powershell)
        bodies = lift_substitutions(expanded, powershell, quoting=False)[1]
        tokens = tokenize(normalize(outer, powershell))
    except Unparsed as error:
        return [hidden(f"The command has {error}.")]
    if any("\0" in token for token in tokens):
        return [hidden(NUL_FINDING_REASON)]  # `$'\x00'`
    findings = [finding for body in bodies for finding in shell_findings(body, cwd, repo, powershell, depth + 1)]
    # Clearing CLAUDECODE would make this session pass for a person's terminal, in any spelling.
    if SET_ENV_CALL.search(outer):
        findings.append(SESSION_MARKER_FINDING)
    if powershell:
        findings += environment_read_findings(outer)
        set_by_dotnet = environment_setter_assignments(outer)
        if set_by_dotnet is None:
            findings.append(hidden("`SetEnvironmentVariable` sets a variable or a value built when it runs. Write "
                                   "`$env:NAME = 'value'`."))
        else:
            findings += environment_findings([], set_by_dotnet)
    subshells: list[str] = []
    pushed: list[str] = []  # pushd's stack
    called = False
    commands = simple_commands(tokens)
    # The variables this command line sets, read strictly (LineVariables); and those it fills from a secret.
    variables = LineVariables(commands, inner, command, powershell, cwd, repo.root)
    secret_names = secret_copies([[unmark(word) for word in each.words] for each in commands], powershell)
    order = -1
    piped = False
    listed: tuple[list[str], str] | None = None  # what the command before a pipe lists, and from where
    listed_store = False  # whether that is a walk of a folder holding a credential store
    feeds: tuple[list[str], str] | None = None
    feeds_store = False

    for raw_segment in segments(tokens):
        if raw_segment == PIPE:
            piped, feeds, feeds_store = True, listed, listed_store
            continue
        if raw_segment == SUBSHELL_OPEN:
            subshells.append(cwd)
            continue
        if raw_segment == SUBSHELL_CLOSE:
            cwd = subshells.pop() if subshells else cwd
            continue
        if raw_segment == CALL:
            called = True
            continue
        order += 1
        call, called = called, False
        fed, fed_paths, fed_store = piped, feeds, feeds_store
        piped, feeds, feeds_store, listed, listed_store = False, None, False, None, False
        segment = [unmark(token) for token in raw_segment]
        # A substitution runs where it stands, before the command around it: judged first, from here.
        for token in segment:
            for number in SUBSTITUTION_WORD.findall(token):
                if int(number) < len(inner):
                    findings += shell_findings(inner[int(number)], cwd, repo, powershell, depth + 1)
        findings += environment_findings(segment)
        if tampers_with_session_marker(segment):
            findings.append(SESSION_MARKER_FINDING)
        if powershell and any(SCRIPT_BLOCK_BUILDERS.search(token) for token in segment):
            findings.append(hidden("A script block built from text runs that text."))
        if program(segment[0]) in ("set-alias", "sal", "new-alias", "nal") or (
                program(segment[0]) == "alias" and any("=" in arg for arg in segment[1:])):
            findings.append(hidden("An alias hides the command it runs from the guard. Run the command itself."))
        unwrapped = unwrap(segment, raw_segment, cwd, repo, powershell)
        findings += unwrapped.findings or []
        argv, raw, here = unwrapped.argv, unwrapped.raw, unwrapped.cwd
        if powershell and argv:
            argv, raw = colon_parameters(argv, raw)
        # Before the empty check: `env` alone strips to nothing, and it prints every variable.
        findings += private_shell_findings(argv or segment, segment, here, powershell, secret_names)
        if unwrapped.prints_environment and segment != ["env"]:
            findings.append(private_finding("This command prints"))
        if not argv:
            continue
        # bash runs a word built at run time as a command; PowerShell does after `&` or `.`.
        if (not powershell or call or segment[0] == ".") and (
                (EXPANSION.search(raw[0]) and EXPANSION.search(expand_known(argv[0]))) or SUBSTITUTION_WORD.search(raw[0])):
            findings.append(hidden("The command's name is built when it runs."))
        name = program(argv[0])
        args, raw_args = argv[1:], raw[1:]

        # What this command lists, for a writer or a reader after a pipe.
        roots = listing_roots(name, args, powershell)
        if roots is not None:
            listed = None if bare_names_elsewhere(name, args, powershell, roots, here) or \
                changes_paths(name, args, powershell) else (roots, here)
            listed_store = recursive_listing(name, args, powershell) and any(
                search_reaches_store(root, here) for root in roots)
        elif fed and name in PIPE_FILTERS:
            # A filter that hands on something else hands on what the guard cannot read.
            listed, listed_store = None if changes_paths(name, args, powershell) else fed_paths, fed_store
        if fed and fed_store and (name in PIPE_READERS or unwrapped.through_xargs):
            findings.append(private_finding(STORE_SEARCH))

        if encoded_command(argv):
            findings.append(hidden("An encoded PowerShell command cannot be read before it runs."))
        findings += nested_findings(argv, raw, here, repo, depth, unwrapped.xargs_replaces) or []
        if name in EVALUATORS or name in POWERSHELL_EVALUATORS:
            findings += evaluated_findings(name, args, raw_args, here, repo, depth, unwrapped.xargs_replaces)
        if powershell and name in START_PROCESS and not (name == "start" and args[:1] and args[0].startswith("/")):
            findings += start_process_findings(argv, raw, here, repo, depth)
        elif name in LAUNCHERS:
            launched = unwrap(segment, raw_segment, cwd, repo, False)
            if launched.argv and launched.argv != argv:
                findings += shell_findings(" ".join(quoted(word) for word in launched.argv), here, repo, powershell,
                                           depth + 1)
        if name == "find" and not powershell:
            findings += find_findings(args, raw_args, here, repo, depth)
            # `find ~ -exec grep token {} +` reads every file it finds.
            if FIND_EXEC & set(args) and listed_store:
                findings.append(private_finding(STORE_SEARCH))

        if name in ("cd", "chdir", "set-location", "sl", "pushd", "push-location", "popd", "pop-location"):
            if name in ("popd", "pop-location"):
                cwd = pushed.pop() if pushed else UnknownCwd(repo.root)
                continue
            where = flag_value(args, "-Path", "-path", "-LiteralPath", "-literalpath") or next(
                (arg for arg in args if not arg.startswith("-") or arg == "-"), None)
            if name in ("pushd", "push-location"):
                pushed.append(cwd)
            if where is None:
                cwd = change_dir(cwd, "~" if name in ("cd", "chdir") and not powershell else "-", repo)
            elif known_substitution(where, inner, SHOW_TOPLEVEL):
                cwd = repo.root if repo.knows(cwd) and (relative_to_root(cwd, repo.root, repo.root) or
                                                         os.path.normcase(os.path.realpath(cwd)) ==
                                                         os.path.normcase(os.path.realpath(repo.root))) \
                    else UnknownCwd(repo.root)
            else:
                cwd = change_dir(cwd, where, repo)
            continue

        if is_person_only_factory_command(argv):
            findings.append(Finding(
                "gate-approval", ALWAYS,
                "Approving a gate, unlocking a test, abandoning a run, opening its pull request, answering a question "
                "and allowing a step new files are a person's decisions, made from their own terminal — never by the agent.", None))
        if name == "git":
            # `$(git branch --show-current)` is HEAD: the guard knows that answer.
            git_args = ["HEAD" if known_substitution(arg, inner, CURRENT_BRANCH) else arg for arg in args]
            findings += git_findings(git_args, here, repo)
            if unwrapped.through_xargs and args[git_subcommand(args):][:1] == ["push"]:
                findings.append(hidden("xargs adds this push's destination from its input."))
        elif name == "dotnet" and args[:1] == ["ef"] and rules().dotnet_ef:
            findings += dotnet_ef_findings(args[1:])
        elif name == "dotnet-ef" and rules().dotnet_ef:
            findings += dotnet_ef_findings(args)
        elif name == "pdftotext" and not pdftotext_prints(argv) and not (
                args and set(args) <= {"-v", "-h", "-help", "--help", "-?"}):  # version or help: writes nothing
            # pdftotext writes its last argument, or `<input>.txt` beside the PDF without one. The FSD
            # is read with it (/fsd-audit, /review-branch, the spec auditor), so it may only print:
            # `pdftotext … <file> -`. A redirect is the shell's, not an argument: `pdftotext f.pdf - 2>&1` prints.
            findings.append(Finding(
                "pdftotext-output-file", DENY,
                "pdftotext writes a file unless its last argument is `-`. Print to the terminal instead.", None))

        if name in ("ssh", "scp", "rsync", "az", "kubectl", "helm") or (
            name in ("docker", "docker-compose", "podman") and ("compose" in args or name == "docker-compose")
        ):
            if rules().non_dev_target.search(" ".join(args)):
                findings.append(Finding(
                    "deploy-non-dev", DENY,
                    "This names an environment `[guard] non_dev_targets` keeps out of reach (UAT or production, "
                    "say). Deploying there goes through the team's release process, not a session.", None))

        positional = [arg for arg in args if not arg.startswith("-")]
        raw_positional = [raw_arg for arg, raw_arg in zip(args, raw_args) if not arg.startswith("-")]
        destination_options = ("-t", "--target-directory", "-Destination", "-destination", "-NewName", "-newname")
        by_option = flag_value(args, *destination_options) or (
            target_directory(args) if name in ("mv", "cp", "install", "ln") else None)
        raw_by_option = flag_value(raw_args, *destination_options) or (
            target_directory(raw_args) if name in ("mv", "cp", "install", "ln") else None)
        destination = by_option or (positional[-1] if name in MOVERS and len(positional) > 1 else None)
        # Read from the raw words, so a quoted `>` redirects nothing and a single-quoted `$` expands nothing.
        targets = write_targets(name, raw_args)
        for raw_target in redirect_targets(raw) + targets:
            target = unmark(raw_target)
            whole = name in WHOLE_FOLDER_WRITERS and target in args and not (name in MOVERS and target == destination)
            findings += write_path_findings(target, here, repo, whole, raw_target, variables, powershell, order, inner)

        # A writer handed its paths by a pipe or by xargs is judged on what the listing before it names.
        writes = name in WRITES_ALL_ARGS or name in WRITES_DESTINATION or name in CONTENT_WRITERS or (
            name in IN_PLACE_EDITORS and any(IN_PLACE_FLAG.match(arg) for arg in args))
        sources_from_pipe: list[str] = []
        if (powershell and fed and name in PIPE_FED_CMDLETS and not cmdlet_paths(args)) or (
                unwrapped.through_xargs and writes):
            replace = unwrapped.xargs_replace if unwrapped.through_xargs else None
            if replace and any(replace in arg and arg != replace for arg in args):
                # `xargs -I{} rm -rf {}/../Migrations`: the paths reach the writer changed.
                findings.append(hidden(XARGS_INSIDE_WORD))
            elif fed and fed_paths is not None:
                paths, where = fed_paths
                copies = name in WRITES_DESTINATION or name in PIPE_COPIERS
                # A copy or a move lands each path in its destination (once: a mover's destination went unjudged).
                if copies or name in PIPE_MOVERS:
                    sources_from_pipe = paths
                for path in [] if copies else paths:
                    if name in IN_PLACE_EDITORS:
                        # An in-place editor changes the files listed, not whole folders: a folder listed
                        # hands it what lies beneath.
                        findings += write_path_findings(path, where, repo, False, powershell=powershell)
                        if not any(char in path for char in "*?["):
                            findings += write_path_findings(path.rstrip("/") + "/**/*", where, repo, False,
                                                            powershell=powershell)
                    else:
                        findings += write_path_findings(path, where, repo, True, powershell=powershell)
            else:
                findings.append(hidden(PIPE_FED_UNKNOWN))

        # A copy, move or link into a folder lands each source there under its own name.
        landing = destination if name in MOVERS else (write_targets(name, args) or [None])[0] \
            if name in WRITES_DESTINATION else None
        raw_landing = raw_by_option or (raw_positional[-1] if landing and raw_positional else None) or landing
        sources = [(arg, raw_arg) for arg, raw_arg in zip(positional, raw_positional) if landing and arg != landing]
        # A listed folder hands over what it holds, under any name; a glob, names it matches (`x/**/*.md`: `*.md`).
        for path in sources_from_pipe:
            last = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            sources.append((last, last) if any(char in last for char in "*?[") else ("*", "*"))
        if landing and sources:
            landed = resolve_target(raw_landing, here, repo.root, variables, powershell, order, inner)
            into_folder = is_folder_target(landing, [] if landed.refusal else landed.paths, here, repo, len(sources),
                                           bool(by_option))
            for source, raw_source in sources:
                name_there = source.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
                findings += claude_settings_findings(landing.rstrip("/\\") + "/" + name_there, here, folder_too=True)
                if into_folder and name_there not in ("", ".", ".."):
                    raw_there = raw_landing.rstrip("/\\") + "/" + raw_source.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
                    findings += write_path_findings(unmark(raw_there), here, repo, False, raw_there, variables, powershell,
                                                    order, inner)
    return findings


def cmdlet_paths(args: list[str]) -> list[str]:
    """The paths a PowerShell cmdlet names itself: `-Path`/`-LiteralPath` and its positional words, not the values
    of its other parameters (`-Destination`, `-Value`, `-Filter`…)."""
    paths, skip = [], False
    for index, arg in enumerate(args):
        lowered = arg.lower()
        if skip:
            skip = False
        elif lowered in POWERSHELL_PATH_OPTIONS:
            paths.extend(args[index + 1:index + 2])
            skip = True
        elif lowered.startswith("-") and len(lowered) > 2 and any(
                option.startswith(lowered) for option in (*POWERSHELL_VALUE_OPTIONS, "-destination", "-newname",
                                                          "-credential", "-erroraction", "-warningaction")):
            skip = True
        elif not arg.startswith("-"):
            paths.append(arg)
    return paths


# ---------------------------------------------------------------- decision

def evaluate(payload: dict, repo: Repo) -> list[Finding]:
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or repo.root
    if tool in READ_TOOLS:
        return read_tool_findings(tool, tool_input, cwd)
    if tool in FILE_TOOLS:
        return private_file_tool_findings(tool_input, cwd) + file_tool_findings(tool, tool_input, cwd, repo)
    if tool in SHELL_TOOLS:
        try:
            return shell_findings(str(tool_input.get("command", "")), cwd, repo, powershell=tool == "PowerShell")
        except TooSlowToJudge:
            return [hidden(f"The command names more globs than the guard can match within its time limit "
                           f"({GLOB_SECONDS} seconds): too much to judge in time. Name fewer paths, or the files "
                           "themselves.")]
    return []


def decide(findings: list[Finding], mode: str) -> dict | None:
    """The hook's stdout, or None to allow silently."""
    if not findings:
        return None
    refusals = [f for f in findings if f.level == ALWAYS or (f.level == DENY and mode == "enforce")]
    warnings = [f for f in findings if f.level == DENY and mode != "enforce"]
    questions = [f for f in findings if f.level == ASK]

    def lines(group: list[Finding]) -> str:
        return " ".join(f"[{f.rule}] {f.reason}" for f in group)

    if refusals:
        output = {"permissionDecision": "deny", "permissionDecisionReason": "guard: " + lines(refusals)}
    elif questions:
        output = {"permissionDecision": "ask", "permissionDecisionReason": "guard: " + lines(questions)}
    else:
        output = {}
    if warnings:
        output["additionalContext"] = (
            "guard (warn-only, not yet enforcing): this call breaks a CLAUDE.md rule and will be refused "
            "once the guard enforces. " + lines(warnings)
        )
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", **output}}


def load_mode() -> str:
    override = os.environ.get("FACTORY_GUARD_MODE", "").strip().lower()
    if override in ("warn", "enforce"):
        return override
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            mode = str(json.load(handle).get("mode", "enforce")).lower()
    except (OSError, ValueError, AttributeError):  # unreadable, not JSON, or not an object
        return "enforce"
    return mode if mode in ("warn", "enforce") else "enforce"


def log(root: str, payload: dict, findings: list[Finding], mode: str,
        now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.timezone.utc)) -> None:
    command = str((payload.get("tool_input") or {}).get("command", ""))
    entry = {
        "at": now().isoformat(timespec="seconds"),
        "mode": mode,
        "tool": payload.get("tool_name"),
        "session": payload.get("session_id"),
        "findings": [{"rule": f.rule, "level": f.level, "path": f.path} for f in findings],
        "commandSha256": hashlib.sha256(command.encode("utf-8")).hexdigest() if command else None,
    }
    try:
        path = os.path.join(root, LOG_RELATIVE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError as error:
        # The decision stands either way; the person is told the record of it is missing.
        sys.stderr.write(f"guard: could not write {LOG_RELATIVE} ({type(error).__name__}); the decision stands.\n")


def find_root(payload: dict) -> str:
    start = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        done = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start,
                              capture_output=True, text=True, timeout=10)
        if done.returncode == 0 and done.stdout.strip():
            return os.path.normpath(done.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.normpath(start)


def main() -> int:
    """
    Never fails silently. Claude Code runs the tool call when a hook exits non-zero, so an
    unexpected error here would look exactly like a pass (the review's LOW-1): it is caught —
    the one broad catch in the guard, for that reason — and said in the session.
    """
    try:
        return guard()
    except Exception as error:  # noqa: BLE001 — see the docstring
        sys.stdout.write(json.dumps(did_not_run(LAST_TOOL[0], f"unexpected {type(error).__name__}")))
        return 0


LAST_TOOL = [""]  # the tool of the call being judged, for did_not_run after a crash


def did_not_run(tool: str, why: str) -> dict:
    """
    The guard could not judge this call. It refuses anything that could write, run a command or reach
    a protected file — a guard that fails open turns every rule off for the session (the harness
    review) — and lets a plain read through with a note, so the session can still look at what broke.
    A person repairs the guard; a session cannot route around it.
    """
    message = (f"guard: did not run — {why}. Reads still work; writes and commands are refused until a person "
               "repairs the guard (devtools/README.md, the guard). Report this.")
    if tool in READ_TOOLS:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": message}}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": message}}


def guard() -> int:
    try:
        # As UTF-8, which is what Claude Code sends: Windows' default codepage turned `…` into three
        # other characters, and a pattern then read text that was never written.
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    except ValueError:
        sys.stdout.write(json.dumps(did_not_run("", "the call it was sent was not JSON")))
        return 0
    LAST_TOOL[0] = str(payload.get("tool_name", ""))
    root = find_root(payload)
    if payload.get("hook_event_name") == "Stop":
        blocked = stop_decision(payload, Repo(root))
        if blocked is not None:
            sys.stdout.write(json.dumps(blocked))
        return 0
    mode = load_mode()
    try:
        findings = evaluate(payload, Repo(root))
    except (RulesUnavailable, GitUnavailable) as error:
        # Refuse, rather than allow: a guard that did not run must not look like one that passed.
        sys.stdout.write(json.dumps(did_not_run(LAST_TOOL[0], str(error))))
        return 0
    decision = decide(findings, mode)
    if decision is not None:
        log(root, payload, findings, mode)
        sys.stdout.write(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
