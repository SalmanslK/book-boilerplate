"""
factory deploy — the mechanical half of /book:deploy: every probe, gate, count and verdict the
skill used to spell out as shell, run the same way every time and tested. The skill keeps the
decisions: what to ask, when to stop, keep or revert.

    factory deploy check   --env DEV|QA [--branch dev] --pre-tests run|skip --post-tests run|skip [--fix]
    factory deploy gate    [--build-only]
    factory deploy changes
    factory deploy up
    factory deploy smoke   [--break-stale-lock]
    factory deploy revert  --to <sha>
    factory deploy report
    factory deploy status  [--target dev|qa|all]

Each step reads and writes one state file inside .git (`factory-deploy.json`), so a later step knows
what an earlier one found: `up` refuses QA without a passing gate for the commit it deploys, `revert`
goes only to the commit `changes` recorded, and `report` reads every count from what was measured,
never from memory.

`report` also appends one JSON line per finished deploy to the private deploy ledger, beside the
person's private settings file and outside every repository. `status` is read-only: it asks each
environment what it runs, and reads the ledger only when the host cannot be reached — and says so.

**DEV and QA only.** The environments are the two tables under `[deploy]` in factory.toml and the
host is its `ssh_host`; neither is ever an argument, so nothing here can be pointed at another box.
UAT and production go through the team's release process. The guard refuses `factory deploy` naming
any other environment as well (devtools/README.md, the guard).

Nothing here prints a secret: the server's `.env` is tested for existence only, the settings check
prints key names only, and the smoke credentials are checked for presence only.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import secrets
import shlex
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ENVIRONMENTS = ("DEV", "QA")
REFUSED_TARGET = "Refused — /book:deploy targets DEV and QA only. {value} is not a permitted target."
REFUSED_QA_SKIP = "Refused — QA always runs the pre-deploy tests. Use --pre-tests run, or deploy to DEV."
# A branch name goes into a remote shell script (quoted as well); refuse anything git would not call a
# plain branch name rather than trust the quoting alone.
BRANCH = re.compile(r"^(?!.*\.\.)(?!/)(?!.*//)[A-Za-z0-9][A-Za-z0-9._/-]{0,199}(?<![./])$")
SHA = re.compile(r"^[0-9a-f]{7,40}$")
STATE_NAME = "factory-deploy.json"
LEDGER_NAME = "deploy-ledger.jsonl"
DEFAULT_PRIVATE_SETTINGS = "~/.ai-factory/local.toml"  # factory.py's, when factory.toml names none
SSH_SECONDS = 120
BUILD_SECONDS = 3600  # `up -d --build` builds the image from source on the box
TEST_SECONDS = 3600
DAY_MINUTES = 26 * 60  # a nightly job is "fresh" for 26 hours: a day and a margin


class DeployError(Exception):
    """A refusal or a failure the skill reports as it stands."""


# ------------------------------------------------------------------ running things


@dataclass
class Done:
    code: int
    out: str


def run(argv: list[str], *, cwd: Path | None = None, stdin: str | None = None, timeout: int = SSH_SECONDS,
        env: dict | None = None) -> Done:
    # Bytes in and out, never text mode: on Windows a text-mode pipe writes every "\n" as "\r\n", and
    # the server's bash then reads `set +e\r` and `rev-parse HEAD\r` — the script fails line by line.
    try:
        done = subprocess.run(argv, cwd=cwd, input=None if stdin is None else stdin.encode("utf-8"),
                              capture_output=True, timeout=timeout, env=env)
    except FileNotFoundError:
        return Done(127, f"{argv[0]} is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        return Done(124, f"{argv[0]} did not finish within {timeout} s")
    out = (done.stdout + done.stderr).decode("utf-8", errors="replace").replace("\r\n", "\n")
    return Done(done.returncode, out.rstrip())


def http_status(url: str, timeout: int = 10) -> int:
    """The status a GET answers, or 0 when nothing answered."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 — a DEV/QA address from factory.toml
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, OSError):
        return 0


# Replaced in the self-tests.
RUN = run
HTTP = http_status


# ------------------------------------------------------------------ configuration


@dataclass(frozen=True)
class Target:
    env: str
    branch: str
    host: str
    user: str
    key: str
    directory: str
    compose_files: tuple[str, ...]
    profile: str
    project: str
    settings_environment: str
    api_port: int
    public_url: str
    raw: dict

    @property
    def compose(self) -> str:
        """`docker compose -f a -f b`, quoted for the remote shell."""
        return "docker compose " + " ".join(f"-f {shlex.quote(name)}" for name in self.compose_files)

    @property
    def lan_url(self) -> str:
        return f"http://{self.host}:{self.api_port}"


def environment(value: str) -> str:
    name = (value or "").strip().upper()
    if name not in ENVIRONMENTS:
        raise DeployError(REFUSED_TARGET.format(value=value))
    return name


# A value still to fill, anywhere in it: the deploy pack's skeleton writes `<the DEV/QA host>`, `~/.ssh/<the key>`.
# Not a shell redirect (`< in.txt > out.txt`), a comparison (`a <= b`, `1<2>3`) or a closing tag; an opening HTML
# tag (`<title>`) still reads as one, so a [deploy] value holds no `<…>` of its own (the pack's comment says so).
PLACEHOLDER = re.compile(r"<[^<>=\s/\d][^<>]*>")
NEEDED = ("ssh_host", "ssh_user", "ssh_key")
TARGET_NEEDED = ("directory", "compose_files", "project", "settings_environment", "api_port")
# The other environment's table is not this deploy's; the compose and notes tables are read, never run.
NOT_SCANNED = {*ENVIRONMENTS, "compose", "notes"}
# The steps /book:deploy reads a `[deploy.notes]` entry with: a key that names none is never read.
NOTE_STEPS = ("check", "gate", "changes", "up", "smoke", "revert", "report")


def placeholders(deploy: dict, env: str) -> list[str]:
    """
    Every value of [deploy] (the shared keys and [deploy.tests]) and [deploy.<env>] that still holds a `<…>`
    placeholder, named as `[table] key`: a deploy never runs one, since a host or a probe it guessed is worse
    than none.
    """
    found: list[str] = []

    def walk(section: str, key: str, value) -> None:
        if isinstance(value, str):
            if PLACEHOLDER.search(value) and f"{section} {key}" not in found:
                found.append(f"{section} {key}")
        elif isinstance(value, list):
            for item in value:
                walk(section, key, item)
        elif isinstance(value, dict):
            for inner in value.values():
                walk(section, key, inner)

    for key, value in deploy.items():
        if key in NOT_SCANNED:
            continue
        if key == "tests" and isinstance(value, dict):
            for name, path in value.items():
                walk("[deploy.tests]", name, path)
        else:
            walk("[deploy]", key, value)
    for key, value in (deploy.get(env) or {}).items():
        walk(f"[deploy.{env}]", key, value)
    return found


def unfilled(deploy: dict, env: str) -> list[str]:
    """What a deploy to env still needs filled: the keys it cannot run without, then every placeholder."""
    table = deploy.get(env) or {}
    missing = [f"[deploy] {key}" for key in NEEDED if not deploy.get(key)]
    missing += [f"[deploy.{env}] {key}" for key in TARGET_NEEDED if not table.get(key)]
    return missing + [key for key in placeholders(deploy, env) if key not in missing]


def target(config_raw: dict, env: str, branch: str) -> Target:
    env = environment(env)
    if not BRANCH.match(branch or ""):
        raise DeployError(f"{branch!r} is not a branch name this tool deploys.")
    deploy = config_raw.get("deploy") or {}
    table = deploy.get(env) or {}
    # Never guess a host: a missing or placeholder value stops the deploy and says which.
    todo = unfilled(deploy, env)
    if todo:
        them = "it" if len(todo) == 1 else "them"
        raise DeployError(f"factory.toml {', '.join(todo)}: not filled in yet — fill {them} in before the first deploy.")
    return Target(env=env, branch=branch, host=deploy["ssh_host"], user=deploy["ssh_user"],
                  key=os.path.expanduser(deploy["ssh_key"]), directory=table["directory"],
                  compose_files=tuple(table["compose_files"]), profile=table.get("profile", ""),
                  project=table["project"], settings_environment=table["settings_environment"],
                  api_port=int(table["api_port"]), public_url=table.get("public_url", ""), raw=deploy)


def ssh(t: Target, script: str, timeout: int = SSH_SECONDS) -> Done:
    """
    One remote shell, the script on stdin. BatchMode: a missing key fails at once instead of waiting
    on a password prompt — the key is the only way in, and a password is never accepted.
    """
    return RUN(["ssh", "-i", t.key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{t.user}@{t.host}",
                "bash -l -s"], stdin=script, timeout=timeout)


# ------------------------------------------------------------------ state


def state_path(root: Path) -> Path:
    done = RUN(["git", "rev-parse", "--git-path", STATE_NAME], cwd=root)
    if done.code != 0 or not done.out.strip():
        raise DeployError(f"git could not say where .git is (exit {done.code}): run this from the repository.")
    path = Path(done.out.strip())
    return path if path.is_absolute() else root / path


def load_state(root: Path) -> dict:
    path = state_path(root)
    if not path.exists():
        return {}
    # A state file that is there but unreadable must not look like "no deploy under way": the
    # revert step would then run on an empty record of what the server held before.
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DeployError(
            f"The deploy state file {path.name} is unreadable ({error}): fix or remove it, "
            "then run `factory deploy check` again.") from None


def save_state(root: Path, state: dict) -> None:
    state_path(root).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def started(state: dict) -> dict:
    if not state.get("env"):
        raise DeployError("No deploy is under way: run `factory deploy check` first.")
    return state


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git(root: Path, *args: str) -> Done:
    return RUN(["git", *args], cwd=root)


# ------------------------------------------------------------------ check (the skill's step 1)


def probe_script(t: Target) -> str:
    """
    Every read-only probe of the box in one SSH call. Each section prints something in every outcome,
    so a missing section means the call broke, not that the check passed.
    """
    d = t.raw
    q = shlex.quote
    backups = d.get("backup_root", "")
    lines = ["set +e", f"D={q(t.directory)}; P={q(t.project)}",
             'echo "== env"', 'test -f "$D/.env" && echo "env ok" || echo MISSING',
             # The commit the server runs now. Read here, before the settings check pulls the new one into the
             # same checkout: read later, it is the new commit, and a revert would rebuild what just failed.
             'echo "== head"', 'git -C "$D" rev-parse HEAD 2>&1',
             'echo "== disk"', "df -BG --output=avail / | tail -1",
             'docker system df --format "{{.Type}} {{.Size}} reclaimable {{.Reclaimable}}"',
             'echo "== backups"']
    if backups:
        # Cron appends every backup's output to cron.log, which nobody reads: a deploy is the one routine
        # moment someone is on the box. Both environments, whichever is deployed — they share the disk.
        for e in d.get("backup_environments", []):
            lines += [f"newest=$(ls -1t {q(backups)}/{q(e)}/{q(e)}-*.dump 2>/dev/null | head -1)",
                      'if [ -z "$newest" ]; then age=NONE; '
                      f'elif [ -n "$(find "$newest" -mmin -{DAY_MINUTES})" ]; then age=fresh; else age=STALE; fi',
                      f"last=$(awk -v e={q(e)} '"
                      '$1 == "Backing" && $2 == "up" { mine = ($3 == e); if (mine) status = "unfinished" } '
                      'mine && /^FAILED/ { status = "FAILED" } mine && /^Kept / { status = "ok" } '
                      f"END {{ print status }}' {q(backups)}/cron.log 2>/dev/null)",
                      f'echo "{e} newest=$(basename "${{newest:-none}}") age=$age last-run=${{last:-no-log}}"']
        lines += ['echo "== cron-log"', f"tail -n 40 {q(backups)}/cron.log 2>/dev/null"]
    ci = d.get("ci_daily") or {}
    lines.append('echo "== nightly"')
    for ref in ci.get("refs", []):
        folder = ci.get("folder", "")
        # The folder is under the deploy account's home: `~` must expand on the server, so it stays unquoted.
        lines.append(f'printf "%s " {q(ref)}; cat {folder}/results-{q(ref)}/latest 2>/dev/null || echo NONE')
    for ref in ci.get("refs", []):
        folder = ci.get("folder", "")
        lines.append(f'case "$(cat {folder}/results-{q(ref)}/latest 2>/dev/null)" in FAIL*) echo "== nightly-log {ref}"; '
                     f'tail -n 30 "$(ls -1t {folder}/results-{q(ref)}/*.log 2>/dev/null | head -1)" 2>&1;; esac')
    lines.append('echo "== mounts"')
    for name in d.get("bind_mounts", []):
        lines.append(f'printf "%s " {q(name)}; stat -c "%u %a" "$D"/{q(name)} 2>/dev/null || echo MISSING')
    lines.append('echo "== volumes"')
    for name in d.get("volumes", []):
        # `%u`, the numeric owner: the container's uid has no account on the host, so `%U` says UNKNOWN.
        lines.append(f'printf "%s " {q(name)}; if mp=$(docker volume inspect "${{P}}_"{q(name)} '
                     '--format "{{.Mountpoint}}" 2>&1); then sudo -n stat -c "%u %a" "$mp" 2>&1; '
                     'else echo "MISSING: $mp"; fi')
    lines.append('echo "== end"')
    return "\n".join(lines) + "\n"


def sections(out: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    current = None
    for line in out.splitlines():
        if line.startswith("== "):
            current = line[3:].strip()
            found[current] = []
        elif current is not None:
            found[current].append(line)
    return found


def nightly_findings(lines: list[str], today: datetime.datetime) -> list[tuple[str, str]]:
    """`PASS <sha> <when>` within 26 hours is expected; anything else is a loud warning."""
    findings = []
    for line in lines:
        ref, _, result = line.partition(" ")
        words = result.split()
        when = None
        if len(words) >= 3:
            try:
                when = datetime.datetime.strptime(words[2], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                when = None
        fresh = when is not None and (today - when) <= datetime.timedelta(minutes=DAY_MINUTES)
        if words[:1] == ["PASS"] and fresh and "(filter:" in result:
            # ci-daily.sh marks a run narrowed by a filter, so it is never mistaken for a full nightly PASS.
            findings.append(("WARN", f"nightly {ref}: {result} — a narrowed run, not the full suite"))
        elif words[:1] == ["PASS"] and fresh:
            findings.append(("OK", f"nightly {ref}: {result}"))
        elif words[:1] == ["RUNNING"] and fresh:
            findings.append(("OK", f"nightly {ref}: still running ({result})"))
        else:
            hint = " — quote its log and give it a defects.md row" if words[:1] == ["FAIL"] else ""
            findings.append(("WARN", f"nightly {ref}: {result or 'NONE'}{' (stale)' if words and not fresh else ''}{hint}"))
    return findings


def owner_findings(kind: str, lines: list[str], uid: str, t: Target) -> tuple[list[tuple[str, str]], list[str]]:
    findings, fixes = [], []
    for line in lines:
        name, _, rest = line.partition(" ")
        owner = rest.split()[0] if rest.split() else ""
        if owner == uid:
            findings.append(("OK", f"{kind} {name}: owner {rest}"))
        elif kind == "mount":
            # A bind mount Docker creates itself is root's, and the container (uid 1654) cannot write it:
            # nothing shows until an invitation mail or a day's log goes missing.
            path = shlex.quote(f"{t.directory}/{name}")
            fixes.append(f"mkdir -p {path} && sudo -n chown {uid}:{uid} {path}")
            findings.append(("FIX", f"mount {name}: {rest or 'MISSING'} — must be owned by {uid}"))
        elif rest.startswith("MISSING"):
            # A new named volume is seeded from the image, whose path is already the container's.
            findings.append(("OK", f"volume {name}: not created yet; the first `up` seeds it from the image"))
        elif not owner.isdigit():
            findings.append(("WARN", f"volume {name}: could not check ({rest}) — sudo needs a password here"))
        else:
            # An old volume keeps the owner it was first created with; rebuilding the image never reseeds it.
            volume = shlex.quote(f"{t.project}_{name}")
            fixes.append(f'sudo -n chown {uid}:{uid} "$(docker volume inspect {volume} --format "{{{{.Mountpoint}}}}")"')
            findings.append(("FIX", f"volume {name}: owner {rest} — must be {uid}; every upload fails until it is"))
    return findings, fixes


def judge_probe(out: str, t: Target, today: datetime.datetime) -> tuple[list[tuple[str, str]], list[str], dict]:
    found = sections(out)
    findings: list[tuple[str, str]] = []
    fixes: list[str] = []
    backups: dict[str, str] = {}
    if "end" not in found:
        return [("BLOCK", "the probe of the box did not finish — its output stopped early:\n" + out[-800:])], [], {}
    if found.get("env", [""])[:1] != ["env ok"]:
        findings.append(("BLOCK", f"{t.directory}/.env is missing. This deploys to an environment that exists; "
                                  "first-time setup is the deployment plan's runbook, not a deploy."))
    disk = found.get("disk", [])
    match = re.search(r"(\d+)G", disk[0]) if disk else None
    minimum = int(t.raw.get("min_free_gb", 10))
    if not match:
        findings.append(("BLOCK", f"could not read the free disk space: {disk[:1]}"))
    elif int(match.group(1)) < minimum:
        # A disk that fills mid-build fails confusingly, and one that fills afterwards stops PostgreSQL's writes.
        findings.append(("BLOCK", f"{match.group(1)} GB free, under {minimum} GB. Suggest `docker system prune -f` "
                                  "to the person — it removes every dangling image on the box, QA's too, so it is "
                                  "their decision, never this deploy's."))
    else:
        findings.append(("OK", f"disk: {match.group(1)} GB free" + "".join(f"; {line}" for line in disk[1:])))
    for line in found.get("backups", []):
        env, _, rest = line.partition(" ")
        state = "fresh" if "age=fresh" in rest else re.search(r"age=(\w+)", rest).group(1) if "age=" in rest else "?"
        last = re.search(r"last-run=([\w-]+)", rest)
        backups[env] = f"{state} {last.group(1) if last else '?'}"
        if "age=fresh" in rest and "last-run=ok" in rest:
            findings.append(("OK", f"backup {line}"))
        else:
            # Warn, never block: a deploy does not touch the data, and repairing a backup is an admin's job.
            findings.append(("WARN", f"backup {line}"))
    if any(level == "WARN" and text.startswith("backup") for level, text in findings):
        findings.append(("WARN", "cron.log, last 40 lines:\n" + "\n".join(found.get("cron-log", []))))
    findings += nightly_findings(found.get("nightly", []), today)
    for name, log in found.items():
        if name.startswith("nightly-log "):
            findings.append(("WARN", f"{name[len('nightly-log '):]} nightly run failed — it needs a docs/defects.md row. Its log, last 30 lines:\n" + "\n".join(log)))
    uid = str(t.raw.get("container_uid", ""))
    for kind, section in (("mount", "mounts"), ("volume", "volumes")):
        more, fix = owner_findings(kind, found.get(section, []), uid, t)
        findings += more
        fixes += fix
    return findings, fixes, backups


def settings_script(t: Target) -> str:
    """
    Pulls the server's checkout and proves the target supplies every setting its appsettings file marks
    REPLACE. The last check made while the old container still serves; the pull starts nothing.
    """
    q = shlex.quote
    files = " ".join(f"-f {q(name)}" for name in t.compose_files)
    check = t.raw.get("settings_check", "devtools/checks/check-required-settings.sh")
    return (f"set -e\ncd {q(t.directory)}\ngit fetch origin\ngit checkout {q(t.branch)}\n"
            f"git pull --ff-only origin {q(t.branch)}\nsh ./{check} {q(t.settings_environment)} {files}\n")


def command_check(root: Path, config_raw: dict, args) -> tuple[str, bool]:
    t = target(config_raw, args.env, args.branch)
    if t.env == "QA" and args.pre_tests == "skip":
        raise DeployError(REFUSED_QA_SKIP)
    findings: list[tuple[str, str]] = []

    def stop(text: str) -> tuple[str, bool]:
        findings.append(("BLOCK", text))
        return report_lines(t, findings, args), False

    # A new deploy starts from nothing: a check that stops early must not leave an earlier run's passing
    # check, gate or changes for `up` to trust.
    save_state(root, {})
    status = git(root, "status", "--porcelain")
    if status.code != 0 or status.out.strip():
        # The server pulls from Bitbucket: uncommitted work here is not what it would deploy.
        return stop("the local tree has uncommitted changes (or git failed) — commit or stash them first:\n" + status.out)
    current = git(root, "rev-parse", "--abbrev-ref", "HEAD").out.strip()
    if current != t.branch and not args.switch:
        # The gate tests what is checked out here, so the check moves this tree to the branch it deploys.
        return stop(f"this tree is on {current or 'an unknown branch'}; the deploy needs it on {t.branch} so the gate "
                    "tests what is deployed. Ask the person, then run check again with --switch.")
    for step in (["fetch", "origin"], ["checkout", t.branch], ["pull", "--ff-only", "origin", t.branch]):
        done = git(root, *step)
        if done.code != 0:
            return stop(f"git {' '.join(step)} failed — the gate must test the commit that is deployed:\n{done.out}")
    local, remote = git(root, "rev-parse", "HEAD").out.strip(), git(root, "rev-parse", f"origin/{t.branch}").out.strip()
    if not local or local != remote:
        return stop(f"local {local[:12]} and origin/{t.branch} {remote[:12]} differ — {which_way(root, t.branch)}; "
                    "never push on the person's behalf.")
    findings.append(("OK", f"local tree clean, on {t.branch} at {local[:12]}"))

    reach = ssh(t, "echo ok\n", timeout=30)
    if reach.code != 0 or "ok" not in reach.out:
        return stop(f"SSH to {t.user}@{t.host} with the key failed — the person installs the deploy key "
                    f"(`ssh-copy-id`); never a password:\n{reach.out}")

    probe = ssh(t, probe_script(t))
    more, fixes, backups = judge_probe(probe.out, t, datetime.datetime.now(datetime.timezone.utc))
    findings += more
    server_before = (sections(probe.out).get("head") or [""])[0].strip()
    if "end" in sections(probe.out) and not SHA.match(server_before):
        findings.append(("BLOCK", f"could not read the commit the server is on ({server_before or 'no answer'}) — "
                                  "a revert would have nothing to go back to."))
    if fixes and args.fix:
        done = ssh(t, "set -e\n" + "\n".join(fixes) + "\n")
        findings.append(("OK" if done.code == 0 else "BLOCK",
                         ("fixed: " if done.code == 0 else "the fix failed: ") + "; ".join(fixes) +
                         ("" if done.code == 0 else "\n" + done.out)))
        if done.code == 0:
            findings = [(("OK", text + " (fixed)") if level == "FIX" else (level, text)) for level, text in findings]
    elif fixes:
        findings.append(("FIX", "run `factory deploy check` again with --fix to apply: " + "; ".join(fixes)))

    if not any(level == "BLOCK" for level, _ in findings):
        settings = ssh(t, settings_script(t))
        if settings.code == 0:
            # DEV's file holds concrete values, so "nothing to check" with exit 0 is a pass there.
            findings.append(("OK", f"required settings for {t.settings_environment}: supplied"))
        elif settings.code == 2:
            findings.append(("BLOCK", f"the settings check did not run (exit 2) — nothing was checked:\n{settings.out}"))
        else:
            findings.append(("BLOCK", "the target does not supply these settings. Each goes in the overlay's api "
                                      "`environment:` — a pull request, never an edit on the box; a .env line alone "
                                      f"reaches no container:\n{settings.out}"))

    ok = not any(level in ("BLOCK", "FIX") for level, _ in findings)
    save_state(root, {"env": t.env, "branch": t.branch, "started": now(), "pre_tests": args.pre_tests,
                      "post_tests": args.post_tests, "commit": local, "server_before": server_before,
                      "check": {"ok": ok, "findings": findings, "backups": backups}})
    return report_lines(t, findings, args), ok


def which_way(root: Path, branch: str) -> str:
    """Which way local and origin differ: ahead is unpushed work, behind is a pull that was refused."""
    counts = git(root, "rev-list", "--left-right", "--count", f"origin/{branch}...HEAD").out.split()
    behind, ahead = (int(counts[0]), int(counts[1])) if len(counts) == 2 and all(c.isdigit() for c in counts) else (0, 0)
    if ahead and behind:
        return f"they have diverged ({ahead} local, {behind} on origin) — a person decides"
    if ahead:
        return f"{ahead} local commit(s) not on origin would not be deployed"
    if behind:
        return f"local is {behind} commit(s) behind — the pull was refused"
    return "git could not say which way"


def report_lines(t: Target, findings: list[tuple[str, str]], args=None) -> str:
    head = f"Deploying to: {t.env}\nBranch:       {t.branch}\nHost:         {t.user}@{t.host}:{t.directory}"
    if args is not None:
        # The choices the person made, shown before anything runs, so a skipped gate is never a surprise.
        head += ("\nPre-deploy:   " + ("SKIPPED by request" if args.pre_tests == "skip" else "run")
                 + "\nPost-deploy:  " + ("SKIPPED by request" if args.post_tests == "skip" else "run"))
    return head + "\n" + "\n".join(f"{level:5} {text}" for level, text in findings)


# ------------------------------------------------------------------ gate (step 2)


def trx_counts(path: Path) -> dict | None:
    """
    The counts a trx file holds, or None when there is no file or no Counters line — a crashed test
    host writes none. Skips are counted from the results: VSTest leaves `notExecuted="0"` for an xUnit
    skip, so a check reading the Counters attribute passes every skip.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    counters = re.search(r"<Counters\b[^>]*>", text)
    if not counters:
        return None
    attrs = dict(re.findall(r'(\w+)="(\d+)"', counters.group(0)))
    summary = re.search(r'<ResultSummary outcome="([A-Za-z]+)"', text)
    return {"executed": int(attrs.get("executed", 0)), "passed": int(attrs.get("passed", 0)),
            "failed": int(attrs.get("failed", 0)),
            "skipped": len(re.findall(r'<UnitTestResult [^>]*outcome="NotExecuted"', text)),
            "outcome": summary.group(1) if summary else "",
            "failing": re.findall(r'<UnitTestResult [^>]*testName="([^"]+)"[^>]*outcome="Failed"', text),
            "cleanup": re.findall(r"Cleanup Failure[^<]*", text)[:3],
            "messages": re.findall(r"<Message>([^<]{0,80})", text)}


def command_gate(root: Path, config_raw: dict, args) -> tuple[str, bool]:
    state = started(load_state(root))
    deploy = config_raw.get("deploy") or {}
    tests = deploy.get("tests") or {}
    head = git(root, "rev-parse", "HEAD").out.strip()
    if head != state["commit"]:
        raise DeployError(f"HEAD moved to {head[:12]} since the check ({state['commit'][:12]}) — start again.")
    lines = []
    # The whole solution, not only the test projects: the smoke half runs --no-build after the deploy,
    # so a project that does not compile must fail here, while the old container still serves.
    build = RUN(["dotnet", "build", "--nologo"], cwd=root, timeout=TEST_SECONDS)
    if build.code != 0:
        state["gate"] = {"ok": False, "commit": state["commit"], "build_only": args.build_only}
        save_state(root, state)
        return "BLOCK the build failed (warnings are errors):\n" + build.out[-3000:], False
    lines.append("OK    build")
    counts = {}
    if not args.build_only:
        for name in ("unit", "architecture", "integration"):
            project = tests.get(name)
            if not project:
                raise DeployError(f"factory.toml [deploy.tests] has no {name} project.")
            trx = root / project / "TestResults" / f"{name}.trx"
            # Fixed names under a folder that survives between deploys: without the delete, a crashed host
            # leaves the previous deploy's numbers to be read as this one's.
            trx.unlink(missing_ok=True)
            argv = ["dotnet", "test", project, "--nologo", "--no-build", "--logger", f"trx;LogFileName={name}.trx"]
            if name == "integration":
                # The deployed-host and S3 halves would skip here, and a skip must mean "no database".
                argv[4:4] = ["--filter", deploy.get("gate_filter", "Category!=Smoke&Category!=S3")]
            done = RUN(argv, cwd=root, timeout=TEST_SECONDS)
            found = trx_counts(trx)
            counts[name] = found
            if done.code != 0 or found is None or found["executed"] == 0 or found["skipped"] or found["failed"]:
                why = ("no trx — the test host crashed or the filter matched nothing" if found is None else
                       f"{found['failed']} failed: {', '.join(found['failing'][:10])}" if found["failed"] else
                       "executed nothing" if found["executed"] == 0 else
                       (f"{found['skipped']} skipped — start Docker, or set {deploy.get('test_db_variable', 'the test database variable')}"
                        if name == "integration" else f"{found['skipped']} skipped — these projects cannot skip")
                       if found["skipped"] else f"exit {done.code}")
                state["gate"] = {"ok": False, "commit": state["commit"], "counts": counts}
                save_state(root, state)
                return "\n".join(lines + [f"BLOCK {name}: {why}. Never skip or delete a failing test.",
                                          done.out[-2000:]]), False
            lines.append(f"OK    {name}: {found['executed']} executed, {found['passed']} passed, 0 skipped")
    state["gate"] = {"ok": True, "commit": state["commit"], "build_only": args.build_only, "counts": counts}
    save_state(root, state)
    return "\n".join(lines), True


# ------------------------------------------------------------------ changes (step 3)


def command_changes(root: Path, config_raw: dict, args) -> tuple[str, bool]:
    state = started(load_state(root))
    t = target(config_raw, state["env"], state["branch"])
    # The commit the server ran before this deploy touched it, as `check` read it ahead of its pull. A fresh
    # read here would see the commit the settings check already pulled.
    previous = state.get("server_before", "")
    if not SHA.match(previous):
        raise DeployError("`factory deploy check` recorded no commit the server was on — run check again.")
    to = f"origin/{t.branch}"
    if git(root, "cat-file", "-e", f"{previous}^{{commit}}").code != 0:
        raise DeployError(f"the server is on {previous[:12]}, which origin does not have — a person looks first.")
    log = git(root, "log", "--oneline", f"{previous}..{to}").out
    migrations = git(root, "diff", "--name-only", f"{previous}..{to}", "--",
                     (config_raw.get("deploy") or {}).get("migrations", "")).out.split()
    ancestor = git(root, "merge-base", "--is-ancestor", previous, to).code == 0
    state["changes"] = {"previous": previous, "target": state["commit"], "commits": len(log.splitlines()),
                        "migrations": migrations, "ancestor": ancestor}
    save_state(root, state)
    status = ("not-ancestor" if not ancestor else "nothing-new" if previous == state["commit"] else "new")
    lines = [f"STATUS {status}", f"server {previous[:12]} → {state['commit'][:12]}"]
    if status == "not-ancestor":
        lines.append("The server is not on an ancestor of the target — a detached HEAD from a rollback. Confirm first.")
    elif status == "nothing-new":
        lines.append("Nothing new. A rebuild still matters when only .env or a base image changed — ask first.")
    lines += [log] if log else []
    lines.append("migrations: " + (", ".join(migrations) if migrations else "none"))
    return "\n".join(lines), True


# ------------------------------------------------------------------ up and verify (step 4)


def verify(t: Target) -> tuple[list[str], dict]:
    """Liveness for a minute, then readiness, default-deny, and the LAN path from this machine."""
    port = t.api_port
    probe = t.raw.get("protected_probe", "")
    if not probe.startswith("/api/"):
        raise DeployError("factory.toml [deploy] has no protected_probe — a real route that needs a token.")
    # --max-time on every curl: an API that accepts and never answers would hang the attempt for ever.
    script = (f"ok=0; for i in $(seq 1 30); do curl -fsS --max-time 5 http://localhost:{port}/health/live >/dev/null "
              f"&& ok=1 && break; sleep 2; done; echo \"live $ok\"\n"
              f"echo \"ready $(curl -s -o /dev/null -w '%{{http_code}}' --max-time 10 http://localhost:{port}/health/ready)\"\n"
              f"echo \"deny $(curl -s -o /dev/null -w '%{{http_code}}' --max-time 10 http://localhost:{port}{shlex.quote(probe)})\"\n")
    done = ssh(t, script, timeout=180)
    found = dict(line.split(" ", 1) for line in done.out.splitlines() if line[:5] in ("live ", "ready", "deny "))
    health = {"live": found.get("live") == "1", "ready": found.get("ready", "000"), "deny": found.get("deny", "000"),
              "probe": probe, "lan": HTTP(f"{t.lan_url}/health/ready")}
    lines = []
    if not health["live"]:
        logs = ssh(t, f"cd {shlex.quote(t.directory)} && {t.compose} ps api; {t.compose} logs --tail=50 api\n")
        lines.append("BLOCK liveness never answered. `Restarting` below is a start-up restart loop; the first "
                     "exception in the log is the cause:\n" + logs.out)
        return lines, health
    lines.append("OK    live 200")
    if health["ready"] == "503":
        lines.append("BLOCK ready 503 with live 200: the app started and cannot reach its database — look at PostgreSQL.")
    elif health["ready"] != "200":
        lines.append(f"BLOCK ready {health['ready']}")
    else:
        lines.append("OK    ready 200")
    if health["deny"] == "401":
        lines.append(f"OK    {probe} 401 with no token")
    else:
        lines.append(f"BLOCK {probe} answered {health['deny']}, not 401 — "
                     + ("authorization is not applied" if health["deny"] == "200" else "the API is not answering"))
    if health["lan"] == 200:
        lines.append(f"OK    {t.lan_url}/health/ready from this machine")
    else:
        lines.append(f"WARN  {t.lan_url} does not answer from this machine ({health['lan'] or 'no answer'}) though "
                     "loopback does: the host firewall, not the deploy.")
    return lines, health


def command_up(root: Path, config_raw: dict, args) -> tuple[str, bool]:
    state = started(load_state(root))
    t = target(config_raw, state["env"], state["branch"])
    if not (state.get("check") or {}).get("ok"):
        raise DeployError("`factory deploy check` did not pass for this deploy — nothing is replaced.")
    if "changes" not in state:
        raise DeployError("run `factory deploy changes` first: a revert needs the commit the server was on.")
    head = git(root, "rev-parse", "HEAD").out.strip()
    if head != state["commit"]:
        raise DeployError(f"HEAD moved to {head[:12]} since the check ({state['commit'][:12]}) — start again.")
    gate = state.get("gate") or {}
    gate_passed = gate.get("ok") and gate.get("commit") == head and not gate.get("build_only")
    if state["pre_tests"] == "run" and not gate_passed:
        raise DeployError("the pre-deploy gate has not passed for this commit — run `factory deploy gate`.")
    if state["pre_tests"] == "skip" and state["env"] != "DEV":
        raise DeployError(REFUSED_QA_SKIP)
    if state["post_tests"] == "run" and not (gate.get("ok") and gate.get("commit") == head):
        raise DeployError("the smoke tests run --no-build: run `factory deploy gate --build-only` first.")
    q = shlex.quote
    profile = f" --profile {q(t.profile)}" if t.profile else ""
    # One SSH call so a dropped connection cannot leave it half applied; --ff-only because a merge made on
    # a deploy server is a divergence nobody sees again. `up --build` runs `migrate` to completion before
    # the API, so a failed migration leaves the old API serving. No DOCKER_BUILDKIT=0: that is CI's only.
    script = (f"set -e\ncd {q(t.directory)}\ngit fetch origin\ngit checkout {q(t.branch)}\n"
              f"git pull --ff-only origin {q(t.branch)}\n{t.compose}{profile} up -d --build\n")
    done = ssh(t, script, timeout=BUILD_SECONDS)
    if done.code != 0:
        logs = ssh(t, f"cd {q(t.directory)} && {t.compose} logs --tail=50 migrate; {t.compose} logs --tail=50 api\n")
        state["up"] = {"ok": False}
        save_state(root, state)
        return ("BLOCK the deploy failed — report it, never retry. The first exception in the API log is the real "
                f"one:\n{done.out[-2000:]}\n{logs.out}"), False
    # A zero exit does not mean the API started: it has no healthcheck and restarts on failure.
    lines, health = verify(t)
    ok = not any(line.startswith("BLOCK") for line in lines)
    state["up"] = {"ok": ok, "health": health, "at": now(),
                   "warnings": [line[6:] for line in lines if line.startswith("WARN")]}
    save_state(root, state)
    return "\n".join(["OK    up -d --build finished", *lines]), ok


# ------------------------------------------------------------------ smoke (step 5)

# One smoke run per host at a time (docs/dev-smoke-test-suite.md): two runs against one host race on
# the same accounts and data. The lock lives on the host, in the environment's checkout under .git —
# invisible to `git status` and to a pull — so it holds across every machine that runs this tool. An
# atomic `mkdir` takes it; its `owner` file holds when, which commit, this run's random token and the
# address the server saw the SSH connection come from — never a credential.
SMOKE_LOCK = "factory-smoke.lock"
SMOKE_LOCK_STALE_SECONDS = 2 * 60 * 60  # generous: a whole smoke run takes minutes


def lock_head(t: Target) -> str:
    """cd into the environment's checkout and name the lock directory, or say why not."""
    return (f"cd {shlex.quote(t.directory)} || {{ echo 'LOCK error no-directory'; exit 0; }}\n"
            f"lock=$(git rev-parse --git-path {SMOKE_LOCK}) || {{ echo 'LOCK error no-git'; exit 0; }}\n"
            "age() { echo $(( $(date +%s) - $(stat -c %Y \"$lock\" 2>/dev/null || date +%s) )); }\n")


def lock_take_script(t: Target, owner: str, break_stale: bool) -> str:
    """
    Take the lock or report who holds it. A stale lock is broken only with `break_stale`, and only while
    it is still older than the limit when this script looks — a fresh lock is never broken.
    """
    q = shlex.quote
    taken = ("if mkdir \"$lock\" 2>/dev/null; then\n"
             "  from=${SSH_CLIENT:-unknown}; from=${from%% *}\n"
             f"  printf '%s from %s\\n' {q(owner)} \"$from\" > \"$lock/owner\"\n"
             "  echo 'LOCK taken'\n"
             "  exit 0\n"
             "fi\n")
    script = lock_head(t) + taken
    if break_stale:
        script += (f"if [ \"$(age)\" -ge {SMOKE_LOCK_STALE_SECONDS} ]; then\n"
                   "  echo \"LOCK broke $(age) $(head -n 1 \"$lock/owner\" 2>/dev/null)\"\n"
                   "  mv \"$lock\" \"$lock.broken.$$\" 2>/dev/null && rm -rf \"$lock.broken.$$\"\n"
                   "fi\n" + taken)
    return script + "echo \"LOCK held $(age) $(head -n 1 \"$lock/owner\" 2>/dev/null)\"\n"


def lock_release_script(t: Target, token: str) -> str:
    """Remove the lock only while it is still this run's: a lock someone broke and retook is theirs."""
    return lock_head(t) + (f"if grep -qF {shlex.quote(token)} \"$lock/owner\" 2>/dev/null; then\n"
                           "  rm -rf \"$lock\" && echo 'LOCK released'\n"
                           "else\n"
                           "  echo 'LOCK not-ours'\n"
                           "fi\n")


def take_smoke_lock(t: Target, commit: str, break_stale: bool) -> tuple[str, list[str]]:
    """This run's token once the lock is held, and what to report; a refusal raises."""
    token = secrets.token_hex(8)
    owner = f"{now()} {commit[:12]} {token}"
    done = ssh(t, lock_take_script(t, owner, break_stale))
    lines = [line for line in done.out.splitlines() if line.startswith("LOCK ")]
    notes = [f"WARN  broke a stale smoke lock on {t.env}: {line[11:]}" for line in lines if line.startswith("LOCK broke ")]
    if "LOCK taken" in lines:
        return token, notes
    held = next((line for line in lines if line.startswith("LOCK held ")), "")
    if not held:
        raise DeployError(f"could not take {t.env}'s smoke lock over SSH (exit {done.code}"
                          + (f", {lines[0][5:]}" if lines else "") + ") — the smoke tests did not run.")
    parts = held.split(" ", 3)
    age = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    who = parts[3] if len(parts) > 3 and parts[3].strip() else "no owner recorded"
    since = f"{age // 3600} h {age % 3600 // 60} min" if age >= 3600 else f"{age // 60} min"
    if age >= SMOKE_LOCK_STALE_SECONDS:
        raise DeployError(f"{t.env}'s smoke lock is STALE — taken {since} ago ({who}), older than "
                          f"{SMOKE_LOCK_STALE_SECONDS // 3600} h, so its run most likely died. It is never broken on "
                          "its own: make sure no smoke run is still going against this host, then run "
                          "`factory deploy smoke --break-stale-lock`.")
    raise DeployError(f"another smoke run holds {t.env}'s lock — taken {since} ago ({who}). One run per host at "
                      "a time: wait for it to finish, then run `factory deploy smoke` again.")


def release_smoke_lock(t: Target, token: str) -> str:
    """A line for the output when the lock could not be removed; empty when it was."""
    done = ssh(t, lock_release_script(t, token))
    if "LOCK released" in done.out:
        return ""
    if "LOCK not-ours" in done.out:
        return f"WARN  {t.env}'s smoke lock was no longer this run's — someone broke it; left as it is."
    return (f"WARN  {t.env}'s smoke lock could not be removed (exit {done.code}) and still holds the host. "
            f"After {SMOKE_LOCK_STALE_SECONDS // 3600} h, `factory deploy smoke --break-stale-lock` clears it.")


def command_smoke(root: Path, config_raw: dict, args) -> tuple[str, bool]:
    state = started(load_state(root))
    t = target(config_raw, state["env"], state["branch"])
    deploy = config_raw.get("deploy") or {}
    if state["post_tests"] == "skip":
        return "SKIPPED at the person's request.", True
    if not (state.get("up") or {}).get("ok"):
        raise DeployError("no deploy in this run passed its health checks — report that; the smoke tests wait.")
    pairs = smoke_pairs(deploy, t.env)
    credentials = deploy.get("smoke_credentials", [])
    # Presence only — never prompt for, accept or print them.
    present = all(os.environ.get(name) for name in credentials)
    lan_ok = ((state.get("up") or {}).get("health") or {}).get("lan") == 200
    # A reverse proxy usually 301s plain http and clients then drop a POST body: the public URL is https.
    url = t.lan_url if lan_ok or not t.public_url else t.public_url
    project = (deploy.get("tests") or {}).get("integration", "")
    trx = root / project / "TestResults" / "smoke.trx"
    trx.unlink(missing_ok=True)
    env = dict(os.environ, **{deploy.get("smoke_url_variable", "DEPLOYED_API_URL"): url})
    token, notes = take_smoke_lock(t, state.get("commit", ""), getattr(args, "break_stale_lock", False))
    try:
        done = RUN(["dotnet", "test", project, "--nologo", "--no-build", "--filter",
                    deploy.get("smoke_filter", "Category=Smoke"), "--logger", "trx;LogFileName=smoke.trx"],
                   cwd=root, timeout=TEST_SECONDS, env=env)
    finally:
        # Released however the run ends — a failure, an exception, Ctrl+C. Only a killed process leaves it,
        # and that is the stale lock a person breaks with --break-stale-lock.
        notes.append(release_smoke_lock(t, token))
    found = trx_counts(trx)
    reasons = deploy.get("smoke_skip_reasons") or {}
    known = {label: sum(1 for message in (found or {}).get("messages", []) if message.startswith(prefix))
             for prefix, label in reasons.items()}
    state["smoke"] = {"exit": done.code, "counts": found, "known_skips": known, "credentials": present, "url": url,
                      "pairs": pairs}
    save_state(root, state)
    verdict_line, kind = smoke_verdict(state)
    tail = done.out[-1500:] if kind in (FAILED, DID_NOT_RUN) else ""
    tally = (f"smoke: {found['executed']} executed, {found['passed']} passed, {found['failed']} failed, "
             f"{found['skipped']} skipped") if found else ""
    return ("\n".join(filter(None, [verdict_line, tally, f"credentials: {'present' if present else 'absent'}",
                                    *notes, tail])),
            kind in (PASSED, IN_PART))


def smoke_pairs(deploy: dict, env: str) -> list[dict]:
    """
    `[deploy] smoke_pairs`: the optional accounts, each an email and password pair - optional only as a pair,
    and on the environments a pair's `required_on` names not at all. One half set without the other would skip
    that pair's checks exactly as a deliberate opt-out does, so it stops here, before the run. Presence only.

        smoke_pairs = [
          { name = "reviewer", email = "APP_..._EMAIL", password = "APP_..._PASSWORD", reason = "No reviewer account", required_on = ["QA"] },
        ]

    `reason` is the start of the message the smoke suite skips with; `[deploy] smoke_skip_reasons` labels it.
    """
    reasons = deploy.get("smoke_skip_reasons") or {}
    pairs = []
    for pair in deploy.get("smoke_pairs") or []:
        email, password = bool(os.environ.get(pair["email"])), bool(os.environ.get(pair["password"]))
        if email != password:
            missing = pair["password"] if email else pair["email"]
            raise DeployError(f"{missing} is not set but its pair is — the {pair['name']} checks would skip as if no "
                              "account were meant. The person sets it in their shell; never prompt for it.")
        required = env in pair.get("required_on", [])
        if required and not email:
            raise DeployError(f"{env} has a {pair['name']} account, so {pair['email']} and {pair['password']} are "
                              "required — a skip there hides a missing variable, not an opt-out.")
        pairs.append({"name": pair["name"], "label": reasons.get(pair["reason"], pair["reason"]),
                      "present": email, "required": required})
    return pairs


SKIPPED, DID_NOT_RUN, FAILED, IN_PART, PASSED = "skipped", "did-not-run", "failed", "in-part", "passed"


def smoke_verdict(state: dict) -> tuple[str, str]:
    """
    The report's first line and its kind, rows read in order, first match wins. Every row but the last
    exists because its absence once read as success: a crashed host writes no trx; an unrecognised skip
    is a check that did not happen; a run of credential skips is a partial run `failed="0"` calls complete.
    """
    if state.get("post_tests") == "skip":
        return "Deployed — smoke SKIPPED at the user's request. Only the health checks verified it.", SKIPPED
    smoke = state.get("smoke") or {}
    found = smoke.get("counts")
    if not found:
        return "Deployed — smoke DID NOT RUN. The test host did not finish (no trx, or no Counters line).", DID_NOT_RUN
    if found["executed"] == 0:
        return ("Deployed — smoke DID NOT RUN. Nothing executed: a --filter typo, --no-build against a stale "
                "assembly, or no [SmokeTest] classes."), DID_NOT_RUN
    if found["failed"] or smoke.get("exit") or found.get("outcome") == "Failed":
        # `outcome="Failed"` with failed="0" is a collection cleanup failure (the fixture's logout).
        detail = (", ".join(found["failing"][:15]) if found["failing"] else
                  "; ".join(found.get("cleanup") or []) or f"exit {smoke.get('exit')}")
        return f"Deployed — post-deploy smoke FAILED. {detail}", FAILED
    counts = smoke.get("known_skips") or {}
    for pair in smoke.get("pairs") or []:
        # A pair that was set, or is required here, and still skipped holds a blank value or was never carried over.
        if counts.get(pair["label"]) and (pair["present"] or pair["required"]):
            return (f"Deployed — post-deploy smoke FAILED. {counts[pair['label']]} checks skipped — {pair['label']}, "
                    f"though the {pair['name']} pair is " + ("set (a value is blank)" if pair["present"] else
                                                             "required on this environment") + "."), FAILED
    known = sum(counts.values())
    if found["skipped"] > known:
        return (f"Deployed — post-deploy smoke FAILED. {found['skipped'] - known} checks skipped for an "
                "unrecognised reason."), FAILED
    if found["skipped"]:
        skipped = "; ".join(f"{n} skipped — {label}" for label, n in counts.items() if n)
        return (f"Deployed — smoke passed IN PART. {found['executed']} ran; {skipped}. The missing variables for this "
                "environment go in the person's shell."), IN_PART
    return "Deployment complete — smoke passed.", PASSED


# ------------------------------------------------------------------ revert (step 6)


def command_revert(root: Path, config_raw: dict, args) -> tuple[str, bool]:
    state = started(load_state(root))
    t = target(config_raw, state["env"], state["branch"])
    previous = (state.get("changes") or {}).get("previous", "")
    if smoke_verdict(state)[1] != FAILED:
        raise DeployError("keep or revert is asked only after a failed smoke run — this one did not fail.")
    # Only to the commit the server was on before this deploy, and only on the person's explicit answer.
    if not previous or len(args.to) < 7 or not previous.startswith(args.to):
        raise DeployError(f"revert goes only to the commit this deploy replaced ({previous[:12] or 'none recorded'}).")
    q = shlex.quote
    profile = f" --profile {q(t.profile)}" if t.profile else ""
    done = ssh(t, f"set -e\ncd {q(t.directory)}\ngit checkout {q(previous)}\n{t.compose}{profile} up -d --build\n",
               timeout=BUILD_SECONDS)
    if done.code != 0:
        state["revert"] = {"ok": False}
        save_state(root, state)
        return f"BLOCK the revert failed — report it; never try another commit:\n{done.out[-2000:]}", False
    lines, health = verify(t)
    ok = not any(line.startswith("BLOCK") for line in lines)
    state["revert"] = {"ok": ok, "to": previous, "health": health}
    save_state(root, state)
    lines.append(f"The server is on a detached HEAD at {previous[:12]}; `git checkout {t.branch} && git pull` there "
                 "returns it to normal.")
    return "\n".join(lines), ok


# ------------------------------------------------------------------ report (step 7)


def command_report(root: Path, config_raw: dict, args) -> tuple[str, bool]:
    state = started(load_state(root))
    t = target(config_raw, state["env"], state["branch"])
    first, kind = smoke_verdict(state)
    up = state.get("up")
    if up is None:
        first, kind = "NOT deployed — the run stopped before `factory deploy up`.", DID_NOT_RUN
    elif not up.get("ok"):
        # Never "Deployed": a failed migration keeps the old API serving, and a failed health check means the
        # new one is up but not working. Either way the smoke never ran.
        first, kind = ("Deploy FAILED — `up` failed or the API failed its health checks; the smoke tests did not "
                       "run. See `factory deploy up`'s output for the first exception."), DID_NOT_RUN
    revert = state.get("revert") or {}
    if revert.get("to"):
        first = f"Reverted to {revert['to'][:12]} after the smoke failed. " + first
    lines = [first]
    if state.get("pre_tests") == "skip":
        lines.append("Pre-deploy tests were SKIPPED at the user's request — the build, unit, architecture and "
                     "in-process integration suites did not run before this deploy.")
    warnings = [text for level, text in (state.get("check") or {}).get("findings", []) if level == "WARN"]
    warnings += (state.get("up") or {}).get("warnings") or []
    # Whole, with any log lines under them: a warning cut to its first line hides why it was raised.
    lines += ["WARNING: " + text.replace("\n", "\n         ") for text in warnings]
    changes = state.get("changes") or {}
    health = (state.get("up") or {}).get("health") or {}
    gate = (state.get("gate") or {}).get("counts") or {}
    smoke = state.get("smoke") or {}
    counts = smoke.get("counts") or {}
    backups = (state.get("check") or {}).get("backups") or {}
    known = smoke.get("known_skips") or {}
    lines += [
        f"  Environment:  {t.env}",
        f"  Branch:       {t.branch}",
        f"  Commit:       {(revert.get('to') or state['commit'])[:12]}",
        f"  Changed:      {str(changes['commits']) + ' commits' if changes.get('commits') else 'rebuild only — no new commits'}",
        f"  Migrations:   {', '.join(changes.get('migrations') or []) or 'none pending'}",
        f"  Health:       live {'200' if health.get('live') else 'NO ANSWER'} · ready {health.get('ready', '-')}"
        f" · {health.get('probe', '-')} {health.get('deny', '-')}",
        f"  Backups:      " + " · ".join(f"{env} {value}" for env, value in backups.items()),
        f"  Reached at:   {smoke.get('url') or t.lan_url}",
        "  Pre-deploy:   " + ("SKIPPED by request" if state.get("pre_tests") == "skip" else
                             "did not pass — see `factory deploy gate`" if not (state.get("gate") or {}).get("ok") else
                             " · ".join(f"{name} {c['passed']} passed" for name, c in gate.items() if c)
                             + f", {sum(c['skipped'] for c in gate.values() if c)} skipped"),
        "  Post-deploy:  " + ("SKIPPED by request" if state.get("post_tests") == "skip" else
                             f"smoke {counts.get('executed', 0)} executed, {counts.get('passed', 0)} passed, "
                             f"{counts.get('failed', 0)} failed"
                             + "".join(f" · {n} skipped — {label}" for label, n in known.items() if n)),
    ]
    if revert or kind == FAILED:
        lines.append(f"  Outcome:      {'reverted to ' + revert['to'][:12] if revert.get('to') else 'kept ' + state['commit'][:12]}")
    if (up or {}).get("ok"):
        lines.append("  Ledger:       " + record_deploy(root, config_raw, state, kind))
    return "\n".join(lines), True


# ------------------------------------------------------------------ the private deploy ledger


def ledger_path(config_raw: dict) -> Path:
    """
    Beside the person's private settings file — factory.private_settings_path()'s folder: factory.toml's
    `private_settings`, or <[book] env_prefix>LOCAL_SETTINGS — outside every repository. Only the folder
    is used; the settings file is never opened here.
    """
    variable = str((config_raw.get("book") or {}).get("env_prefix") or "BOOK_") + "LOCAL_SETTINGS"
    settings = os.environ.get(variable) or os.path.expanduser(
        str(config_raw.get("private_settings") or DEFAULT_PRIVATE_SETTINGS))
    return Path(settings).parent / LEDGER_NAME


def shown(path: Path) -> str:
    """A path as a report prints it: the home folder as `~`."""
    home = os.path.expanduser("~")
    text = str(path)
    return "~" + text[len(home):] if text.startswith(home) else text


def ledger_record(state: dict, kind: str) -> dict:
    """
    One finished deploy, from what the steps measured. Commits, counts and choices only: no host, user, key,
    URL, test message or setting value is ever written.
    """
    revert = state.get("revert") or {}
    changes = state.get("changes") or {}
    gate = state.get("gate") or {}

    def tally(counts: dict | None) -> dict | None:
        return {key: counts.get(key, 0) for key in ("executed", "passed", "failed", "skipped")} if counts else None

    if state.get("pre_tests") == "skip":
        pre = {"choice": "skip", "result": "skipped"}
    else:
        pre = {"choice": "run", "result": "passed" if gate.get("ok") else "did-not-pass",
               "counts": {name: tally(counts) for name, counts in (gate.get("counts") or {}).items()}}
    smoke = state.get("smoke") or {}
    post = {"choice": state.get("post_tests", "run"), "result": kind, "counts": tally(smoke.get("counts"))}
    return {"when": now(), "target": state["env"], "branch": state.get("branch", ""),
            "commit": revert.get("to") or state["commit"], "deployed": state["commit"],
            "previous": changes.get("previous", ""), "reverted": bool(revert.get("to")),
            "migrations": changes.get("migrations") or [], "pre_tests": pre, "post_tests": post,
            "verdict": "reverted" if revert.get("to") else kind}


def record_deploy(root: Path, config_raw: dict, state: dict, kind: str) -> str:
    """Appends this deploy to the ledger once — a second `report` for the same run adds nothing."""
    path = ledger_path(config_raw)
    if state.get("ledger"):
        return f"already recorded in {shown(path)}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(ledger_record(state, kind), sort_keys=True) + "\n")
    except OSError as error:
        # The deploy happened whatever the ledger says: a failed write is a warning, never a failed report.
        return f"NOT recorded — {shown(path)} could not be written ({type(error).__name__})"
    state["ledger"] = {"at": now()}
    save_state(root, state)
    return f"recorded in {shown(path)}"


def ledger_records(config_raw: dict) -> list[dict]:
    try:
        text = ledger_path(config_raw).read_text(encoding="utf-8")
    except OSError:
        return []
    records = []
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue  # a torn last line from an interrupted write: skip it, keep the rest
        if isinstance(record, dict):
            records.append(record)
    return records


def last_record(records: list[dict], env: str) -> dict | None:
    return next((record for record in reversed(records) if record.get("target") == env), None)


# ------------------------------------------------------------------ status (read-only)


def status_script(t: Target) -> str:
    """
    What the environment runs, read and nothing else: no pull, no compose, no `.env`. The checkout's
    commit, and when the API container was created — the moment `up` last replaced it.
    """
    q = shlex.quote
    return "\n".join([
        "set +e", f"D={q(t.directory)}; P={q(t.project)}",
        'echo "== head"', 'git -C "$D" rev-parse HEAD 2>&1',
        'echo "== branch"', 'git -C "$D" rev-parse --abbrev-ref HEAD 2>&1',
        'echo "== committed"', 'git -C "$D" log -1 --format=%cI HEAD 2>&1',
        'echo "== api"',
        'docker ps -a --filter "label=com.docker.compose.project=$P" --filter "label=com.docker.compose.service=api" '
        '--format "{{.CreatedAt}}|{{.Status}}" 2>&1 | head -1',
        'echo "== end"']) + "\n"


def behind(root: Path, sha: str, base: str = "origin/dev") -> str:
    if not SHA.match(sha or "") or git(root, "cat-file", "-e", f"{sha}^{{commit}}").code != 0:
        return f"unknown — this clone does not have {sha[:12] or 'that commit'}; fetch, or a person looks"
    count = git(root, "rev-list", "--count", f"{sha}..{base}").out.strip()
    if not count.isdigit():
        return f"unknown — git could not count {sha[:12]}..{base}"
    ancestor = git(root, "merge-base", "--is-ancestor", sha, base).code == 0
    text = "level with" if count == "0" and ancestor else f"{count} commit{'' if count == '1' else 's'} behind"
    return f"{text} {base}" + ("" if ancestor else " — and not an ancestor of it: a revert or another branch")


def container_time(text: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime(text[:25], "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def status_one(root: Path, config_raw: dict, env: str, records: list[dict]) -> tuple[list[str], bool]:
    t = target(config_raw, env, "dev")
    last = last_record(records, t.env)
    done = ssh(t, status_script(t), timeout=30)
    found = sections(done.out)
    head = (found.get("head") or [""])[0].strip()
    if done.code != 0 or "end" not in found or not SHA.match(head):
        why = (done.out.splitlines() or ["no answer"])[-1][:200]
        lines = [f"{t.env} — COULD NOT READ LIVE (exit {done.code}: {why})"]
        if last:
            lines += [f"  From the ledger's last record — NOT a live read; the host may run something else since:",
                      f"  Commit:       {last.get('commit', '')[:12]} (branch {last.get('branch') or '?'})",
                      f"  Deployed:     {last.get('when', '?')} (recorded by `factory deploy report`)",
                      f"  Verdict:      {last.get('verdict', '?')}",
                      f"  Behind:       {behind(root, last.get('commit', ''))}"]
        else:
            lines.append(f"  The ledger ({shown(ledger_path(config_raw))}) holds no record for {t.env} either.")
        return lines, False
    branch = (found.get("branch") or ["?"])[0].strip()
    api = (found.get("api") or [""])[0].strip()
    created, _, running = api.partition("|")
    lines = [f"{t.env} — live read of {t.directory}",
             f"  Commit:       {head[:12]} ({'detached HEAD' if branch == 'HEAD' else 'branch ' + branch})",
             f"  Deployed:     " + (f"{created} (API container created; {running})" if running else
                                    "no API container — nothing is running"),
             f"  Behind:       {behind(root, head)}"]
    if last:
        lines.append(f"  Ledger:       last record {last.get('when', '?')}, {last.get('commit', '')[:12]}, "
                     f"{last.get('verdict', '?')}")
    committed = (found.get("committed") or [""])[0].strip()
    try:
        commit_time = datetime.datetime.fromisoformat(committed)
    except ValueError:
        commit_time = None
    started_at = container_time(created)
    if commit_time and started_at and commit_time > started_at:
        # `check` pulls the checkout before `up` rebuilds: a deploy that stopped in between leaves a checkout
        # newer than the container, and the API still runs an earlier commit.
        lines.append("WARN  the checkout's commit is newer than the API container — a deploy may have stopped after "
                     "`check` pulled; the API may run an earlier commit" +
                     (f" (the ledger's last: {last.get('commit', '')[:12]})" if last else ""))
    elif last and last.get("commit") and last["commit"] != head:
        lines.append(f"WARN  the ledger's last deploy was {last['commit'][:12]}, not {head[:12]} — "
                     "something changed the checkout since")
    return lines, True


def command_status(root: Path, config_raw: dict, args) -> tuple[str, bool]:
    """Read-only: what DEV and QA run. Changes nothing on the host and nothing in the deploy state."""
    value = (getattr(args, "target", "") or "all").strip()
    envs = list(ENVIRONMENTS) if value.lower() == "all" else [environment(value)]
    for env in envs:
        target(config_raw, env, "dev")  # every refusal before anything runs
    fetch = git(root, "fetch", "--quiet", "origin", "dev")
    lines = [] if fetch.code == 0 else ["WARN  git fetch failed — counts are against origin/dev as last fetched"]
    records = ledger_records(config_raw)
    ok = True
    for env in envs:
        more, live = status_one(root, config_raw, env, records)
        lines += more
        ok = ok and live
    return "\n".join(lines), ok


COMMANDS = {"check": command_check, "gate": command_gate, "changes": command_changes, "up": command_up,
            "smoke": command_smoke, "revert": command_revert, "report": command_report, "status": command_status}


def add_parser(sub) -> None:
    deploy = sub.add_parser("deploy", allow_abbrev=False,
                            help="/book:deploy's steps, DEV and QA only — check, gate, changes, up, smoke, revert, "
                                 "report — and status, read-only")
    deploy.add_argument("step", choices=list(COMMANDS))
    deploy.add_argument("--env", default="DEV", help="check: DEV or QA — nothing else is accepted")
    deploy.add_argument("--branch", default="dev", help="check: the branch the server checks out")
    deploy.add_argument("--pre-tests", choices=["run", "skip"], default="run", help="check: skip is DEV only")
    deploy.add_argument("--post-tests", choices=["run", "skip"], default="run", help="check")
    deploy.add_argument("--fix", action="store_true", help="check: apply the ownership fixes it found")
    deploy.add_argument("--switch", action="store_true", help="check: the person agreed to move this tree to --branch")
    deploy.add_argument("--build-only", action="store_true", help="gate: compile only (pre-tests skipped, smoke to run)")
    deploy.add_argument("--break-stale-lock", action="store_true",
                        help="smoke: the person checked no smoke run is going and breaks a lock older than 2 h")
    deploy.add_argument("--to", default="", help="revert: the commit this deploy replaced, as `changes` printed it")
    deploy.add_argument("--target", default="all", help="status: dev, qa or all — nothing else is accepted")


def main(root: Path, config_raw: dict, args) -> tuple[str, bool]:
    return COMMANDS[args.step](root, config_raw, args)
