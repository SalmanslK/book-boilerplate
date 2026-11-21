#!/usr/bin/env python3
"""
Compose renders the intended published ports, for DEV and for QA, and the QA overlay supplies every
setting the QA appsettings file marks REPLACE.

An overlay whose `ports:` lacks `!override` APPENDS to the base file's list rather than replacing it,
so QA once published its base port as well as its own and would have collided with DEV on the shared host. Both
files read correctly; only the rendered result showed it. `docker compose config` parses and merges
without starting anything, so this needs Docker's CLI but no running container.

    python devtools/checks/check-compose.py

Asserts the published set, not the file contents. What each render must publish is the project's,
`[deploy.compose]` in devtools/factory/factory.toml - one table per render (`laptop` is compose.yaml
alone, `DEV` adds compose.dev.yaml, `QA` adds compose.qa.yaml), one list per service, compared whole:

    [deploy.compose.laptop]
    api = ["8080"]
    postgres = ["127.0.0.1:5432"]      # host_ip:port - an entry with a colon compares the binding
    [deploy.compose.DEV]
    api = ["8080"]
    postgres = ["127.0.0.1:5433"]
    caddy = ["9443"]
    subnet = ["172.30.0.0/16"]         # the default network's subnets
    [deploy.compose.QA]
    api = ["8081"]
    postgres = []                      # publishes nothing
    caddy = ["9444"]
    subnet = ["172.31.0.0/16"]

A service a table does not name is not checked; no [deploy.compose] at all fails, rather than pass on
nothing. The binding is the assertion that matters for PostgreSQL - without a host_ip the database is on
every interface, and DEV shares a box with QA and the front end.
- No compose file switches off a durability setting (fsync, synchronous_commit, full_page_writes) or
  puts PostgreSQL's data on tmpfs: they make the integration tests' database fast by giving up durability, and belong in the
  tests' own container, never in a file a real environment runs.
  Every compose*.y*ml and docker-compose*.y*ml under the repository is read as text, comments ignored
  (a `#` inside quotes is not one), so an overlay no render covers is checked too.
- The ports and subnets another project on a shared server already holds stay out of every list above.

Called by bitbucket-pipelines.yml and devtools/checks/ci-local.sh, so both hold the same assertions
and the same placeholders. Needs Docker's CLI, and jq for check-required-settings.sh.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Placeholders only, to satisfy compose.qa.yaml's required-variable checks: nothing is signed or
# connected to. Every `${VAR:?...}` in compose.qa.yaml needs an entry here, or `config` refuses to
# render and nothing is checked.
QA_PLACEHOLDERS = {
    "JWT_ISSUER": "https://ci.invalid",
    "JWT_KEY": "ci-placeholder-never-signs-anything",
    "CORS_ORIGIN": "https://ci.invalid",
    "POSTGRES_PASSWORD": "ci-placeholder",
    "FRONTEND_BASE_URL": "https://ci.invalid",
    "SMTP_PASSWORD": "ci-placeholder",
}


# Settings that make PostgreSQL fast by making it unsafe to lose power: right for a database that is
# thrown away after a test run, wrong for one that holds data. Only the switched-off forms count -
# `fsync=on` is the default and harmless - and the name is matched without a word boundary before it,
# so `-cfsync=off` and `"-c", "fsync=off"` both fail. A `tmpfs` counts only when what it mounts is
# PostgreSQL's data (`/var/lib/postgresql`): a `/tmp` tmpfs is ordinary.
THROWAWAY_SETTING = re.compile(r"(fsync|synchronous_commit|full_page_writes)\s*=\s*['\"]?(off|false)\b", re.IGNORECASE)
POSTGRES_DATA = "/var/lib/postgresql"
COMPOSE_NAMES = ("compose*.y*ml", "docker-compose*.y*ml")
SKIPPED_FOLDERS = {".git", "node_modules", "bin", "obj", "graphify-out"}


def without_comment(line: str) -> str:
    """The line up to a YAML comment: a `#` starts one outside quotes, at the start or after a space."""
    quote = ""
    for index, character in enumerate(line):
        if quote:
            if character == quote:
                quote = ""
        elif character in ("'", '"'):
            quote = character
        elif character == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def tmpfs_block(lines: list[str], at: int) -> str:
    """The text of the YAML entry the `tmpfs` on line `at` belongs to: its own line and what is nested
    under it, or - for `- type: tmpfs` / `type: tmpfs` inside a list item - the whole item."""
    start, base = at, indent(lines[at])
    if not lines[at].lstrip().startswith("- ") and base >= 2:
        for back in range(at - 1, -1, -1):
            if lines[back].strip() and indent(lines[back]) < base:
                if indent(lines[back]) == base - 2 and lines[back].lstrip().startswith("- "):
                    start = back
                break
    item = lines[start].lstrip().startswith("- ")
    base = indent(lines[start])
    block, end = [lines[start]], start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip():
            deeper = indent(line) > base
            sibling = indent(line) == base and line.lstrip().startswith("- ") and not item
            if not (deeper or sibling):
                break
        block.append(line)
        end += 1
    return "\n".join(block)


def compose_files(root: Path) -> list[Path]:
    """Every compose file under the repository, so an overlay no render covers is checked too."""
    found = {path for pattern in COMPOSE_NAMES for path in root.rglob(pattern)
             if not SKIPPED_FOLDERS & set(path.relative_to(root).parts)}
    return sorted(found)


def throwaway_problems(root: Path) -> list[str]:
    """Every line of every compose file, comments dropped, that switches off a durability setting or
    mounts PostgreSQL's data on tmpfs."""
    found = []
    for path in compose_files(root):
        lines = [without_comment(line) for line in path.read_text(encoding="utf-8").splitlines()]
        name = path.relative_to(root).as_posix()
        for number, line in enumerate(lines, start=1):
            setting = THROWAWAY_SETTING.search(line)
            if setting:
                found.append(f"{name}:{number} switches off '{setting.group(1)}', a throwaway PostgreSQL setting "
                             "that must not reach a real environment")
            elif re.search(r"(?<![\w-])tmpfs(?![\w-])", line) and POSTGRES_DATA in tmpfs_block(lines, number - 1):
                found.append(f"{name}:{number} mounts PostgreSQL's data on tmpfs, a throwaway setting "
                             "that must not reach a real environment")
    return found


def render(args: list[str], env: dict[str, str], root: Path) -> dict:
    done = subprocess.run(["docker", "compose", *args, "config", "--format", "json"], cwd=root, env=env,
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"`docker compose {' '.join(args)} config` failed: {done.stderr.strip()}")
    return json.loads(done.stdout)


def published(service: dict) -> list[str]:
    return [str(port.get("published")) for port in service.get("ports") or []]


def bound(service: dict) -> list[str]:
    return [f"{port.get('host_ip') or '0.0.0.0'}:{port.get('published')}" for port in service.get("ports") or []]


# Placeholders only, for compose.dev.yaml's required variables, as above.
DEV_PLACEHOLDERS = {
    "JWT_KEY": "ci-placeholder-never-signs-anything",
    "CORS_ORIGIN": "https://ci.invalid",
    "SMTP_PASSWORD": "ci-placeholder",
}


def subnets(project: dict) -> list[str]:
    return [pool.get("subnet") for pool in project.get("networks", {}).get("default", {}).get("ipam", {}).get("config") or []]


RENDERS = ("laptop", "DEV", "QA")


def expected(root: Path = ROOT) -> dict[str, dict[str, list[str]]]:
    """`[deploy.compose]` from factory.toml: what each render must publish (the module docstring)."""
    try:
        with open(root / "devtools" / "factory" / "factory.toml", "rb") as handle:
            table = ((tomllib.load(handle).get("deploy") or {}).get("compose") or {})
    except (OSError, ValueError) as error:
        raise RuntimeError(f"devtools/factory/factory.toml is unreadable ({error})") from None
    if not any(table.get(name) for name in RENDERS):
        raise RuntimeError("devtools/factory/factory.toml has no [deploy.compose]: name what each render must "
                           "publish (the check-compose.py docstring shows the shape)")
    return {name: {key: [str(v) for v in values] for key, values in (table.get(name) or {}).items()}
            for name in RENDERS}


def problems(renders: dict[str, dict], want: dict[str, dict[str, list[str]]]) -> list[str]:
    """Each service's published set (or binding, where an entry names a host_ip) against `want`, whole."""
    found = []
    for name in RENDERS:
        for service, wanted in (want.get(name) or {}).items():
            project = renders[name]
            if service == "subnet":
                what, have = f"{name} subnet", subnets(project)
            elif service not in project.get("services", {}):
                found.append(f"{name} has no service {service!r}, which [deploy.compose.{name}] names")
                continue
            elif any(":" in entry for entry in wanted):
                what, have = f"{name} {service} binding", bound(project["services"][service])
            else:
                what, have = f"{name} {service} ports", published(project["services"][service])
            if have != wanted:
                found.append(f"{what}: {have}, expected {wanted}")
    return found


def main(root: Path = ROOT) -> int:
    qa_env = {**os.environ, **QA_PLACEHOLDERS}
    throwaway = throwaway_problems(root)
    for problem in throwaway:
        print(f"check-compose: {problem}.", file=sys.stderr)
    if throwaway:
        return 1
    try:
        want = expected(root)
        renders = {
            "laptop": render(["-f", "compose.yaml", "--profile", "api"], dict(os.environ), root),
            "DEV": render(["-f", "compose.yaml", "-f", "compose.dev.yaml", "--profile", "api"],
                          {**os.environ, **DEV_PLACEHOLDERS}, root),
            "QA": render(["-f", "compose.yaml", "-f", "compose.qa.yaml"], qa_env, root),
        }
    except (OSError, RuntimeError, ValueError) as error:
        print(f"check-compose: could not render - {error}", file=sys.stderr)
        return 1
    found = problems(renders, want)
    for problem in found:
        print(f"check-compose: {problem}.", file=sys.stderr)
    if found:
        return 1
    print("Compose renders the intended ports.")
    # And the QA overlay supplies every setting appsettings.QA.json marks REPLACE, rendered with
    # the same placeholders. Its own output names any missing key; values are never printed.
    settings = subprocess.run(["sh", "devtools/checks/check-required-settings.sh", "QA", "compose.yaml",
                               "compose.qa.yaml"], cwd=root, env=qa_env)
    return settings.returncode


if __name__ == "__main__":
    raise SystemExit(main())
