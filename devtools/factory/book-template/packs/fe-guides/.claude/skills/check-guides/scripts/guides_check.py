#!/usr/bin/env python3
"""The mechanical half of /check-guides.

Compares every front-end integration guide with what the repository says is built, and prints candidate
findings for the AI to read. It decides nothing: every line is a candidate, read before it is reported.

    python .claude/skills/check-guides/scripts/guides_check.py [REPO] [--ref origin/dev] [--json]

Reads the guides, the contracts and the ledgers with `git show <ref>:<path>`, never the working tree, so a
dirty checkout cannot hide drift; the project's settings come from devtools/factory/factory.toml:

  docs/fe-integration-guides/*.md     the guides (`_template.md` is the shape a new one starts from)
  docs/api/api-contracts.md           every implemented route: method, path, auth, request, responses,
                                      and the request and response field lists (generated)
  docs/api/fe-api-coverage.md         the front end's own contract, once one has been received
  the question and defect ledgers     through devtools/checks/ledger_ids.py, shared with /book:sync-sources
  the source files                    through `git grep`, for error codes and unparsed routes

Who owns a route: the guide whose endpoint table lists it; else check-fe-guides.py's rule (an `[fe_guides]
owners` entry, the guide titled as the contracts' section, the most specific **Base:**) — one rule, read
from that check, never a copy.

Exit 2 when a source it needs is missing (the contracts, the guides, a ledger heading): an empty parse
would report every guide clean while checking nothing. Exit 0 otherwise, findings or not.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve()
BOOK = HERE.parents[4]
sys.path.insert(0, str(BOOK / "devtools" / "checks"))
from ledger_ids import ID, Ledgers, LedgerMissing, citation_flag, own_words  # noqa: E402

GUIDES_DIR = "docs/fe-integration-guides"
CONVENTIONS = "api-conventions.md"
CONTRACTS = "docs/api/api-contracts.md"
COVERAGE = "docs/api/fe-api-coverage.md"
FACTORY_TOML = "devtools/factory/factory.toml"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
# What a stack's source looks like, unless `[fe_guides]` says otherwise: the files that hold error codes and
# route registrations, the call that registers a route, and a module's own folders ({module} is the folder
# its endpoint file sits in).
STACK_DEFAULTS = {
    "dotnet": {"source_files": ["src/*.cs"], "route_calls": r"\.Map(Get|Post|Put|Patch|Delete)\(",
               "module_sources": ["src/*.Application/Features/{module}", "src/*.Domain/Features/{module}"]},
}
OTHER_STACKS = {"source_files": ["src"], "route_calls": "", "module_sources": []}
# An error code, quoted as a value in the source (`Error.Conflict("Widget.InUse", …)`, `Code = "Widget.InUse"`),
# never a doc comment's `cref="…"`. Every match is a code the source has: a guide naming it is not wrong.
CODE_USE = re.compile(r"(?:^\s*|\(\s*|(?<!cref)=\s*|,\s*|:\s*)\"([A-Z][A-Za-z]+\.[A-Z][A-Za-z]+)\"")
# A guide must name a code only when it is not merely assigned to some name (D38): an assignment counts when the
# name says it is a code (`ErrorCode`, `CONFLICT_CODES`) or is the code's own last part (`NotFound =
# "Widget.NotFound"`); `ApplicationName = "Widget.App"` or a meter's name is a dotted constant, not a code.
CODE_PLACED = re.compile(r"(?:^\s*|\(\s*|,\s*|:\s*)\"([A-Z][A-Za-z]+\.[A-Z][A-Za-z]+)\"")
CODE_ASSIGNED = re.compile(r"\b(\w+)\s*=\s*\"([A-Z][A-Za-z]+\.([A-Z][A-Za-z]+))\"")


def returned_codes(line: str) -> set[str]:
    """The codes on a source line that a front end may meet, by the rule above."""
    found = set(CODE_PLACED.findall(line))
    for name, code, last in CODE_ASSIGNED.findall(line):
        if re.search(r"codes?$", name, re.I) or name == last:
            found.add(code)
    return found
GUIDE_CODE = re.compile(r"`([A-Z][a-z][A-Za-z]+\.[A-Z][A-Za-z]+)`")
JSON_KEY = re.compile(r'"([a-z][A-Za-z0-9]*)"\s*:')
# A section listing what the front end asked for and we do not build: its routes are meant not to exist.
DEVIATION_HEADING = re.compile(r"deviation|what is not here|not built", re.I)
# Words near an open question that tell the front end it may change.
OPEN_MARK = re.compile(r"\bopen\b|with the BA|awaiting|PROPOSED|recommend|until .{0,40}answer|unanswered|"
                       r"pending|not yet (?:answered|settled|decided)|may change", re.I)
HEADER_LINES = 14


class SourceMissing(Exception):
    pass


# ------------------------------------------------------------------------------------------------- git


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
    except OSError as error:
        raise SourceMissing(f"git could not run ({error})") from error


def git(repo: Path, *args: str) -> str:
    done = run_git(repo, *args)
    if done.returncode != 0:
        raise SourceMissing(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


def show(repo: Path, ref: str, path: str, required: bool = True) -> str:
    done = run_git(repo, "show", f"{ref}:{path}")
    if done.returncode != 0:
        if required:
            raise SourceMissing(f"{ref}:{path} — {done.stderr.strip()}")
        return ""
    return done.stdout


def grep(repo: Path, ref: str, files: list[str], *args: str) -> str:
    """`git grep` over the source at ref: no match is an empty answer; any other failure could not check."""
    done = run_git(repo, "grep", *args, ref, "--", *files)
    if done.returncode > 1:
        raise SourceMissing(f"git grep over {files} at {ref}: {done.stderr.strip()}")
    return done.stdout


def load_check():
    """check-fe-guides.py beside this Book's other checks, for its owner rule and its reading of a guide's header."""
    path = BOOK / "devtools" / "checks" / "check-fe-guides.py"
    if not path.is_file():
        raise SourceMissing("devtools/checks/check-fe-guides.py — the fe-guides pack's check, whose owner rule "
                            "this script uses")
    spec = importlib.util.spec_from_file_location("check_fe_guides", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:  # a check that does not load cannot answer for its owner rule
        raise SourceMissing(f"devtools/checks/check-fe-guides.py did not load ({error})") from error
    return module


# -------------------------------------------------------------------------------------------- settings


@dataclass
class Settings:
    source_files: list[str]
    route_calls: str
    module_sources: list[str]
    internal_codes: list[tuple[str, str]]   # (code or `Module.*`, reason)


def factory_config(repo: Path, ref: str) -> dict:
    """factory.toml as it is at ref, like everything else checked; the checkout's only where ref has none."""
    text = show(repo, ref, FACTORY_TOML, required=False)
    if not text and (repo / FACTORY_TOML).is_file():
        text = (repo / FACTORY_TOML).read_text(encoding="utf-8")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise SourceMissing(f"{FACTORY_TOML} is not valid TOML ({error})") from error


def settings(config: dict) -> Settings:
    # `[book] stack` where the Book records it; an installed Book often does not, so the dotnet pack is also
    # known by its own check beside this one.
    stack = str((config.get("book") or {}).get("stack") or "")
    if not stack and (BOOK / "devtools" / "checks" / "check-dotnet.py").is_file():
        stack = "dotnet"
    table = config.get("fe_guides") or {}
    defaults = STACK_DEFAULTS.get(stack, OTHER_STACKS)
    internal = []
    for entry in table.get("internal_error_codes", []) or []:
        code = entry.get("code") if isinstance(entry, dict) else None
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Za-z]+\.(?:[A-Z][A-Za-z]+|\*)", code):
            raise SourceMissing(f"{FACTORY_TOML} [fe_guides] internal_error_codes: {entry!r} needs a code, "
                                "`Module.Code` or `Module.*`")
        if not isinstance(reason, str) or not reason.strip():
            raise SourceMissing(f"{FACTORY_TOML} [fe_guides] internal_error_codes: {code} gives no reason")
        internal.append((code, reason))
    return Settings(list(table.get("source_files") or defaults["source_files"]),
                    str(table.get("route_calls", defaults["route_calls"]) or ""),
                    list(table.get("module_sources") or defaults["module_sources"]), internal)


# --------------------------------------------------------------------------------------------- paths


class Paths:
    """A route as the guides and the contracts both write it, made comparable: `/farms/{id}?version=` under
    the API's base `/api/v1` is `/api/v1/farms/{}`. A path the contracts list as unversioned stays as it is."""

    def __init__(self, version_base: str, unversioned: list[str]):
        self.base = version_base.rstrip("/")
        self.absolute = {"/" + p.strip("/").split("/")[0] for p in unversioned if p.strip("/")}

    def norm(self, raw: str) -> str | None:
        path = raw.strip().strip("`").split("?")[0].split("#")[0].rstrip(".,;:)")
        if (not path.startswith("/") or "<" in path or "…" in path or "..." in path or " " in path
                or re.search(r"\{(?:module|unpaged)", path, re.I)):
            return None
        first = "/" + path.strip("/").split("/")[0] if path.strip("/") else "/"
        if self.base and not path.startswith(self.base + "/") and path != self.base and first not in self.absolute \
                and not path.startswith("/api/"):
            path = self.base + path
        parts = ["{}" if re.fullmatch(r"\{[^}]*\}|\d+|v?\d+\.\d+\.\d+", part) else part.lower()
                 for part in path.split("/")]
        return "/".join(parts).rstrip("/") or "/"


def camel(name: str) -> str:
    return name[:1].lower() + name[1:]


# ----------------------------------------------------------------------------------------- contracts


@dataclass
class Route:
    method: str
    path: str
    key: str
    auth: str               # anonymous | authenticated | perm[ or perm]
    statuses: set[str]
    heading: str
    source: str
    line: int
    row: str
    body_types: list[str] = field(default_factory=list)
    params: list[str] = field(default_factory=list)


@dataclass
class Contracts:
    routes: dict[str, Route]
    request_fields: dict[str, list[str]]
    response_fields: dict[str, list[str]]
    has_responses: bool
    unversioned_sources: list[str]
    text: str


def parse_contracts(text: str, paths_from) -> Contracts:
    """The contracts as the generator writes them; `paths_from(unversioned)` builds the Paths to key routes."""
    unversioned = [match.group(1) for match in re.finditer(r"^\|\s*[A-Z]+\s*\|\s*`(/[^`]*)`\s*\|", text, re.M)
                   if not match.group(1).startswith("/api/")]
    paths = paths_from(unversioned)
    routes: dict[str, Route] = {}
    request: dict[str, list[str]] = {}
    response: dict[str, list[str]] = {}
    unversioned_sources: list[str] = []
    top = heading = source = ""
    schema: list[str] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            top, heading, schema = line[3:].strip(), line[3:].strip(), None
            continue
        if line.startswith("### "):
            heading = line[4:].strip()
            if top in ("Request schemas", "Response schemas"):
                schema = (request if top == "Request schemas" else response).setdefault(heading, [])
            continue
        if schema is not None:
            match = re.match(r"\| `(\w+)` \|", line)
            if match:
                schema.append(match.group(1))
            continue
        match = re.match(r"Prefix `[^`]+` · source `([^`]+)`", line)
        if match:
            source = match.group(1)
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if len(cells) < 2 or cells[0] not in METHODS:
            continue
        path = cells[1].strip("`")
        if len(cells) >= 5:      # a versioned row: Method | Path | Auth | Request | Responses | Dispatches
            auth, request_cell, responses = cells[2].replace("`", ""), cells[3], cells[4]
            row_source = source
        else:                    # an unversioned row: Method | Path | Notes | Source
            auth, request_cell, responses = "anonymous", "", ""
            row_source = cells[3].strip("`") if len(cells) > 3 else ""
            if row_source:
                unversioned_sources.append(row_source)
        key = f"{cells[0]} {paths.norm(path)}"
        route = Route(cells[0], path, key, auth, set(re.findall(r"\b([1-5]\d\d)\b", responses)), heading,
                      row_source, number, line.strip())
        route.body_types = re.findall(r"body: \[`(\w+)`\]", request_cell)
        for part in re.findall(r"(?:query|form|route): ([^<]*)", request_cell):
            route.params += re.findall(r"`(\w+)`", part)
        routes[key] = route
    return Contracts(routes, request, response, bool(re.search(r"^## Response schemas", text, re.M)),
                     unversioned_sources, text)


# -------------------------------------------------------------------------------------------- guides


@dataclass
class TableRow:
    key: str
    method: str
    path: str
    line: int
    permission: str | None   # None: the table has no permission column
    kind: str                # permission | anonymous-flag | none
    returns: str


@dataclass
class Guide:
    path: str
    text: str
    lines: list[str]
    bases: list[str]
    shared: bool                    # its Base holds a placeholder: a protocol several modules follow
    last_checked: str | None
    last_commit: str | None
    rows: list[TableRow]
    mentions: dict[str, int]        # route key → first line
    named_paths: set[str]
    tables: list[list[str]]         # each endpoint table's header


def parse_guide(path: str, text: str, paths: Paths, known: set[str], check) -> Guide:
    lines = text.splitlines()
    header_guide = check.Guide(Path(path).stem, text)
    # A Base with a `<placeholder>` is a protocol several modules follow; one with a `{parameter}` is a path.
    protocol = [base for base in header_guide.bases if "<" in base]
    bases = [b for b in (paths.norm(base) for base in header_guide.bases if base not in protocol) if b]
    checked = re.sub(r"\s+", " ", header_guide.checked or "")
    stamp = re.search(r"(\d{4}-\d{2}-\d{2})(?:\s*\([^)]*?\b([0-9a-f]{7,40})\b)?", checked)

    def resolve(method: str, raw: str) -> str | None:
        """The route a guide means: as written; else under one of its Bases (`/{id}/resend`); else the one
        route under a Base that ends with it (a table written relative to `/orders/{orderId}`)."""
        norm = paths.norm(raw)
        if not norm:
            return None
        key = f"{method} {norm}"
        if key in known or raw.strip("`").startswith("/api/"):
            return key
        relative = raw.strip("`")
        for base in bases:
            alt = paths.norm(base + relative)
            if alt and f"{method} {alt}" in known:
                return f"{method} {alt}"
        tail = norm[len(paths.base):] if paths.base and norm.startswith(paths.base + "/") else norm
        ending = [k for k in known if k.startswith(method + " ") and k.endswith(tail)
                  and any(k.split(" ", 1)[1].startswith(base + "/") for base in bases)]
        if len(ending) == 1:
            return ending[0]
        if bases and relative.startswith("/{"):
            return f"{method} {paths.norm(bases[0] + relative)}"
        return key

    rows: list[TableRow] = []
    mentions: dict[str, int] = {}
    named: set[str] = set()
    tables: list[list[str]] = []
    in_deviation = False
    columns: dict[str, int] = {}
    kind = "none"
    for number, line in enumerate(lines, 1):
        if line.startswith("#"):
            in_deviation = bool(DEVIATION_HEADING.search(line))
            continue
        for raw in re.findall(r"`(/[^`\s]+)`", line):
            norm = paths.norm(raw)
            if norm:
                named.add(norm)
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            low = [cell.lower().strip("*") for cell in cells]
            if len(cells) >= 2 and "path" in low[:2]:          # an endpoint table's header
                tables.append(cells)
                columns, kind = {"path": low.index("path")}, "none"
                for index, name in enumerate(low):
                    if name in ("permission", "needs", "auth"):
                        columns["permission"], kind = index, "permission"
                    elif name.startswith("anonymous"):
                        columns["permission"], kind = index, "anonymous-flag"
                    elif name.startswith("returns"):
                        columns["returns"] = index
                continue
            method = cells[0].strip("`") if cells else ""
            if method in METHODS and len(cells) >= 2 and not in_deviation:
                key = resolve(method, cells[columns.get("path", 1)] if columns else cells[1])
                if key:
                    at = columns.get("permission")
                    rows.append(TableRow(key, method, cells[columns.get("path", 1)].strip("`"), number,
                                         cells[at] if at is not None and at < len(cells) else None, kind,
                                         cells[columns["returns"]] if "returns" in columns
                                         and columns["returns"] < len(cells) else ""))
                continue
        if in_deviation or "(#deviations)" in line:
            continue
        for match in re.finditer(r"\b(GET|POST|PUT|PATCH|DELETE)\s+`?(/[^\s`|)\]]+)", line):
            key = resolve(match.group(1), match.group(2))
            if key:
                mentions.setdefault(key, number)
    return Guide(path, text, lines, bases, bool(protocol), stamp.group(1) if stamp else None,
                 stamp.group(2) if stamp and stamp.group(2) else None, rows, mentions, named, tables)


# --------------------------------------------------------------------------------------- permissions


PERM = re.compile(r"\b([a-z][a-z-]*)\.([a-z][a-z.-]*[a-z])\b")
SHORT = re.compile(r"(?<![a-z])\.([a-z]+)\b")
BARE_FILLER = {"or", "and", "a", "an", "the", "only", "token", "each", "needs", "its", "own"}


def stated_permissions(cell: str) -> set[str]:
    plain = cell.replace("`", "")
    perms = {f"{a}.{b}" for a, b in PERM.findall(plain)}
    if perms:   # `farms.view`, `.edit` — the shorthand repeats the first one's module
        module = sorted(perms)[0].split(".")[0]
        perms |= {f"{module}.{short}" for short in SHORT.findall(plain)}
    return perms


def permission_mismatch(row: TableRow, route: Route) -> str | None:
    cell, want = row.permission or "", route.auth
    if row.kind == "anonymous-flag":
        says = "yes" in cell.lower()
        return None if says == (want == "anonymous") else \
            f"guide says anonymous={'yes' if says else 'no'}, contracts say {want}"
    stated = stated_permissions(cell)
    if want == "anonymous":
        return None if re.search(r"anonymous|no token|none", cell, re.I) else f"guide says {cell!r}, contracts say anonymous"
    if want == "authenticated":
        return f"guide names {sorted(stated)}, contracts say authenticated only" if stated else None
    wanted = {perm.strip() for perm in want.split(" or ")}
    # A bare word (`create`) is shorthand for the module's own permission: the permission's last part.
    bare = set(re.findall(r"(?<![\w.])([a-z]+)(?![\w.])", cell.replace("`", "").replace("*", ""))) - BARE_FILLER
    stated |= {perm for perm in wanted if perm.rsplit(".", 1)[-1] in bare}
    return None if wanted <= stated else f"guide says {cell!r}, contracts say {want}"


# ------------------------------------------------------------------------------------------- source


def error_codes(repo: Path, ref: str, files: list[str]) -> tuple[dict[str, str], set[str]]:
    """{code: "path:line"} for every `Module.Code` the source passes as a value, and those of them a guide must
    name (returned_codes)."""
    out = grep(repo, ref, files, "-n", "-E", r"\"[A-Z][A-Za-z]+\.[A-Z][A-Za-z]+\"")
    codes: dict[str, str] = {}
    returned: set[str] = set()
    for line in out.splitlines():
        parts = line.split(":", 3)
        if len(parts) < 4 or parts[3].lstrip().startswith(("//", "#", "*")):
            continue
        for code in CODE_USE.findall(parts[3]):
            codes.setdefault(code, f"{parts[1]}:{parts[2]}")
        returned |= returned_codes(parts[3])
    return codes, returned


def identifiers(repo: Path, ref: str, files: list[str]) -> set[str]:
    """Every identifier in the source, as written and camelCased (`OrderCount`, `orderCount`)."""
    out = grep(repo, ref, files, "-h", "-o", "-E", r"[A-Za-z_][A-Za-z0-9_]*")
    return {name for token in set(out.split()) for name in (token, camel(token))}


def named_codes(text: str) -> set[str]:
    """Every error code a guide names, the shorthand expanded: `` `Item.NameTooLong`, `…Characters` `` names
    Item.NameCharacters too."""
    named: set[str] = set()
    for line in text.splitlines():
        full = re.findall(r"\b([A-Z][A-Za-z]+)\.([A-Z][A-Za-z]+)\b", line)
        named |= {f"{module}.{name}" for module, name in full}
        for suffix in re.findall(r"(?:…|\.\.\.)([A-Z][A-Za-z]+)", line):
            for module, name in full:
                for at in range(1, len(name)):
                    if name[at].isupper():
                        named.add(f"{module}.{name[:at]}{suffix}")
    return named


def internal(code: str, allowed: list[tuple[str, str]]) -> bool:
    return any(code == entry or (entry.endswith(".*") and code.startswith(entry[:-1])) for entry, _ in allowed)


def json_keys(guide: Guide) -> dict[str, int]:
    keys: dict[str, int] = {}
    in_block = False
    for number, line in enumerate(guide.lines, 1):
        if line.lstrip().startswith("```"):
            in_block = not in_block
            continue
        if in_block:
            for key in JSON_KEY.findall(line):
                keys.setdefault(key, number)
        else:   # an inline `{ "id": … }` in prose or a table
            for inline in re.findall(r"`\{[^`]*`", line):
                for key in JSON_KEY.findall(inline):
                    keys.setdefault(key, number)
    return keys


UNKNOWN_COMMIT = -1


def commits_since(repo: Path, ref: str, commit: str | None, paths: list[str]) -> int | None:
    """Commits on paths after commit, up to ref; None with nothing to count; UNKNOWN_COMMIT when ref does
    not hold that commit, so the count could not be made."""
    if not commit or not paths:
        return None
    done = run_git(repo, "rev-list", "--count", f"{commit}..{ref}", "--", *paths)
    return int(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else UNKNOWN_COMMIT


def source_roots(source_files: list[str]) -> tuple[str, ...]:
    """The top folders `[fe_guides] source_files` reads (`src/*.cs` → `src/`): a contracts row's source under
    one of them is a parsed file."""
    return tuple(sorted({re.split(r"[*?\[]", p)[0].split("/")[0].strip("/") + "/" for p in source_files}))


def module_sources(endpoint_files: set[str], patterns: list[str]) -> list[str]:
    """The endpoint files, and each module's own folders: {module} is the folder an endpoint file sits in.
    A pathspec with a wildcard must match the whole path, so a folder pattern gets `/*` (git's `*` spans `/`)."""
    paths = set(endpoint_files)
    for rel in endpoint_files:
        module = Path(rel).parent.name
        for pattern in patterns:
            folder = pattern.replace("{module}", module).rstrip("/")
            paths.add(folder + "/*" if any(c in folder for c in "*?[") else folder)
    return sorted(paths)


# -------------------------------------------------------------------------------------------- check


def finding(kind: str, line: int | None, text: str) -> dict:
    return {"kind": kind, "line": line, "text": text}


def check(repo: Path, ref: str) -> dict:
    fe = load_check()
    factory = factory_config(repo, ref)
    config = settings(factory)
    if not grep(repo, ref, config.source_files, "-l", "-I", "-E", "."):
        raise SourceMissing(f"[fe_guides] source_files {config.source_files} match no file at {ref}")
    names = [name for name in git(repo, "ls-tree", "--name-only", f"{ref}:{GUIDES_DIR}").split()
             if name.endswith(".md") and not name.startswith("_")]
    if not names:
        raise SourceMissing(f"no guides under {ref}:{GUIDES_DIR}")
    texts = {f"{GUIDES_DIR}/{name}": show(repo, ref, f"{GUIDES_DIR}/{name}") for name in names}
    conventions = texts.get(f"{GUIDES_DIR}/{CONVENTIONS}", "")
    base = (fe.Guide("api-conventions", conventions).bases or ["/api/v1"])[0] if conventions else "/api/v1"
    contracts_text = show(repo, ref, CONTRACTS)
    paths_holder: dict[str, Paths] = {}

    def paths_from(unversioned: list[str]) -> Paths:
        paths_holder["paths"] = Paths(base, unversioned)
        return paths_holder["paths"]

    contracts = parse_contracts(contracts_text, paths_from)
    paths = paths_holder["paths"]
    routes = contracts.routes
    if not routes:
        raise SourceMissing(f"{CONTRACTS} parsed to no routes")
    guides = [parse_guide(path, text, paths, set(routes), fe) for path, text in texts.items()]
    try:
        ledgers = Ledgers.read(show(repo, ref, "docs/open-questions.md"),
                               show(repo, ref, "docs/archive/questions-resolved.md", required=False),
                               show(repo, ref, "docs/defects.md"),
                               show(repo, ref, "docs/archive/defects-closed.md", required=False))
    except LedgerMissing as error:
        raise SourceMissing(str(error)) from error
    highest: dict[str, int] = {}
    for ident in ledgers.status:
        highest[ident[0]] = max(highest.get(ident[0], 0), int(ident[1:]))
    codes, returned = error_codes(repo, ref, config.source_files)
    head = git(repo, "rev-parse", "--short", ref).strip()

    # Who owns each route: the guide whose table lists it, else check-fe-guides.py's rule.
    by_stem = {Path(g.path).stem: g for g in guides}
    header_guides = [fe.Guide(stem, g.text) for stem, g in by_stem.items() if stem != "api-conventions"]
    try:
        configured = fe.owners_from(factory.get("fe_guides") or {})
    except fe.CouldNotCheck as error:
        raise SourceMissing(str(error)) from error
    section_rows = fe.Contracts(contracts_text)
    owned: dict[str, list[Route]] = {g.path: [] for g in guides}
    unowned: list[Route] = []
    for route in routes.values():
        listing = [g for g in guides if route.key in {row.key for row in g.rows}]
        if listing:
            stems = [Path(g.path).stem for g in listing]
        elif route.heading in section_rows.rows:
            stems = fe.owners_of(route.heading, route.row, section_rows, header_guides, configured) or []
        else:
            stems = []
        for stem in stems:
            if stem in by_stem:
                owned[by_stem[stem].path].append(route)
        if not stems:
            unowned.append(route)

    documented = {key for g in guides for key in {row.key for row in g.rows} | set(g.mentions)}
    named_paths = {path for g in guides for path in g.named_paths}

    def is_documented(route: Route) -> bool:
        # A route with its method, or an unversioned path named in backticks with none (`/health/live`).
        return route.key in documented or (route.source and not route.path.startswith("/api/")
                                           and route.key.split(" ", 1)[1] in named_paths)

    # The words an example's key may be: a field in the contracts, a parameter, a convention's key, or any
    # identifier in the source (error details and sync payloads are fields no schema lists). A key none of
    # them spells is a name the code no longer has.
    vocabulary = ({camel(f) for fields in contracts.request_fields.values() for f in fields}
                  | {camel(f) for fields in contracts.response_fields.values() for f in fields}
                  | {p for r in routes.values() for p in r.params} | set(JSON_KEY.findall(conventions))
                  | identifiers(repo, ref, config.source_files))

    report = {"ref": ref, "head": head, "routes_in_contracts": len(routes), "guides": [],
              "response_fields": ("example keys checked against the contracts' field lists and the source"
                                  if contracts.has_responses else
                                  "the contracts carry no Response schemas: example keys checked against the "
                                  "source's identifiers only — sample the response shapes against the code")}
    for g in guides:
        stem = Path(g.path).stem
        found: list[dict] = []
        table_keys = {row.key for row in g.rows}
        mine = table_keys | set(g.mentions) | {r.key for r in owned[g.path] if r.key.split(" ", 1)[1] in g.named_paths}
        if stem != "api-conventions" and not g.shared:
            if not g.tables:
                found.append(finding("T-TABLE", None, "no endpoint table — the template's "
                                     "`| Method | Path | Permission | Returns |`"))
            elif not any("permission" in [c.lower().strip("*") for c in table] for table in g.tables):
                found.append(finding("T-TABLE", None, f"its endpoint table has no Permission column "
                                     f"({' | '.join(g.tables[0])}) — the template's `| Method | Path | Permission "
                                     "| Returns |`, in the contracts' words"))
        for row in g.rows:
            route = routes.get(row.key)
            if not route:
                found.append(finding("R-STALE", row.line, f"{row.method} {row.path} — not in {CONTRACTS}"))
                continue
            if row.permission is not None:
                why = permission_mismatch(row, route)
                if why:
                    found.append(finding("R-PERM", row.line, f"{row.method} {row.path}: {why} ({CONTRACTS}:{route.line})"))
            # Status codes, never a number inside an example (`{ "id": 123 }`); 401 and 403 hold on every
            # protected route (api-conventions.md), declared or not.
            plain = re.sub(r"\{[^}]*\}|\[[^\]]*\]|\"[^\"]*\"", " ", row.returns)
            extra = set(re.findall(r"(?<![\w.])([1-5]\d\d)(?![\w.])", plain)) - route.statuses
            if route.auth != "anonymous":
                extra -= {"401", "403"}
            if extra and route.statuses:
                found.append(finding("R-STATUS", row.line, f"{row.method} {row.path}: guide returns {sorted(extra)}, "
                                     f"contracts declare {sorted(route.statuses)} ({CONTRACTS}:{route.line})"))
        for key, line in g.mentions.items():
            if key not in routes and key not in table_keys:
                found.append(finding("R-MENTION", line, f"{key} — named in the text, not in {CONTRACTS}"))
        for route in owned[g.path]:
            if not is_documented(route):
                found.append(finding("R-MISSING", None, f"{route.method} {route.path} ({route.auth}) — this guide's, "
                                     f"documented by no guide ({CONTRACTS}:{route.line})"))
            elif route.key not in mine:
                found.append(finding("R-ELSEWHERE", None, f"{route.method} {route.path} — this guide's, documented "
                                     "only in another guide"))
        # Request fields of the routes it is the reference for: its table's, or what it owns when it has none.
        for key in sorted((table_keys or {r.key for r in owned[g.path]}) & set(routes)):
            route = routes[key]
            wanted = [camel(f) for body in route.body_types for f in contracts.request_fields.get(body, [])] + \
                     [p for p in route.params if not re.search(rf"\{{{p}\b", route.path)]
            missing = [w for w in dict.fromkeys(wanted) if not re.search(rf"\b{re.escape(w)}\b", g.text + conventions)]
            if missing:
                found.append(finding("F-MISSING", None, f"{route.method} {route.path}: never names {missing} "
                                     f"({', '.join(route.body_types) or 'query/form'})"))
        for key, line in json_keys(g).items():
            if key not in vocabulary:
                found.append(finding("F-UNKNOWN", line, f'JSON key "{key}" — no field, parameter, convention or '
                                     "identifier in the source spells it"))
        seen: set[str] = set()
        for number, line in enumerate(g.lines, 1):
            for code in GUIDE_CODE.findall(line):
                if code not in codes and code not in seen and not re.search(r"\.(md|json|cs|py|txt)$", code, re.I):
                    seen.add(code)
                    found.append(finding("E-UNKNOWN", number, f"error code `{code}` — the source never returns it"))
        cited: dict[str, int] = {}
        for number, line in enumerate(g.lines, 1):
            for match in ID.finditer(line):
                ident = match.group(1)
                cited.setdefault(ident, number)
                status = ledgers.status.get(ident)
                if status is None:
                    continue
                words = own_words(line, match)
                flag = citation_flag(status, words)
                if flag:
                    found.append(finding("ID-STALE", number, f"{ident} is {status}, {flag.split(', ', 1)[1]}: "
                                         f"…{words.strip()}…"))
        # An open question cited with nothing near it saying it is open: shown as settled, so the front end
        # may build on a rule that can still change.
        for ident, number in cited.items():
            if ledgers.status.get(ident) == "open":
                around = re.sub(r"\s+", " ", re.sub(r"\]\([^)]*\)", "]", " ".join(g.lines[max(0, number - 2):number + 1])))
                if not OPEN_MARK.search(around):
                    found.append(finding("ID-OPEN", number, f"{ident} is open; nothing near it says so — "
                                         "an open question shown as settled"))
            elif ident not in ledgers.status and (int(ident[1:]) > highest.get(ident[0], 0) or re.search(
                    rf"\[{ident}\]\([^)]*(?:open-questions|defects)", g.text)):
                # Only a citation: a link to a ledger, or an id past the last one taken. `D1` in "D1 to D7"
                # may be a day, not a defect.
                found.append(finding("ID-UNKNOWN", number, f"{ident} — no row or section holds it"))
        # Age: source commits after the guide's own last edit are code it has not seen.
        sources = module_sources({r.source for r in owned[g.path] if r.source.startswith(source_roots(config.source_files))},
                                 config.module_sources)
        since = commits_since(repo, ref, g.last_commit, sources)
        last_edit = git(repo, "log", "-1", "--format=%h %ad", "--date=short", ref, "--", g.path).split()
        after_edit = commits_since(repo, ref, last_edit[0], sources) if last_edit else None
        age = {"last_checked": g.last_checked, "commit": g.last_commit, "commits_since_on_its_sources": since,
               "last_edit": " ".join(last_edit), "source_commits_after_last_edit": after_edit, "sources": sources}
        if g.last_checked is None:
            found.append(finding("AGE", 1, "no **Last checked against the code:** line in the header"))
        elif since == UNKNOWN_COMMIT:
            found.append(finding("AGE", 1, f"Last checked commit {g.last_commit} is not in {ref}: its age could not "
                                 "be counted — fix the line's commit"))
        elif after_edit and after_edit > 0:
            found.append(finding("AGE", None, f"{after_edit} commit(s) on its sources since the guide was last "
                                 f"edited ({' '.join(last_edit)}) — code it has not seen"))
        elif since and since > 0 and last_edit and last_edit[1] > g.last_checked:
            found.append(finding("AGE-LINE", 1, f"Last checked {g.last_checked} ({g.last_commit}), but edited "
                                 f"{last_edit[1]} ({last_edit[0]}) after {since} source commit(s) without moving "
                                 "the line — confirm the edit covered them, then move it"))
        report["guides"].append({"guide": g.path, "bases": g.bases, "table_routes": len(g.rows),
                                 "owned_routes": len(owned[g.path]),
                                 "ids": {i: ledgers.status.get(i, "unknown")
                                         for i in sorted(cited, key=lambda s: (s[0], int(s[1:])))},
                                 "age": age, "findings": found})

    report["undocumented"] = [f"{r.method} {r.path} ({r.auth}) — {CONTRACTS}:{r.line}"
                              for r in routes.values() if not is_documented(r)]
    named = named_codes("\n".join(texts.values()))
    report["unnamed_error_codes"] = [f"{code} — {where}" for code, where in sorted(codes.items())
                                     if code in returned and code not in named
                                     and not internal(code, config.internal_codes)]
    report["stale_internal_codes"] = [f"{entry} — matches no code in the source ({reason})"
                                      for entry, reason in config.internal_codes if not any(
                                          internal(code, [(entry, reason)]) for code in codes)]
    # Routes the generator never parsed: a registration in a file no contracts row names as its source.
    if config.route_calls:
        parsed = {r.source for r in routes.values() if r.source.startswith(source_roots(config.source_files))}
        calls = grep(repo, ref, config.source_files, "-n", "-E", config.route_calls)
        report["unparsed_routes"] = []
        for line in calls.splitlines():
            parts = line.split(":", 3)
            if len(parts) == 4 and parts[1] not in parsed and not any(
                    parts[1].endswith("/" + source) for source in contracts.unversioned_sources):
                report["unparsed_routes"].append(f"{parts[1]}:{parts[2]}  {parts[3].strip()[:100]}")
    else:
        report["unparsed_routes"] = None
    coverage = show(repo, ref, COVERAGE, required=False)
    rows = [line for line in coverage.splitlines() if re.match(r"^\|\s*`?(GET|POST|PUT|PATCH|DELETE)\b", line)]
    report["coverage"] = (f"{COVERAGE} missing — not applicable" if not coverage else
                          f"{len(rows)} front-end contract rows" if rows else
                          "not applicable: no front-end contract received (never \"passed\")")
    return report


def print_report(report: dict) -> None:
    print(f"guides_check — {report['ref']} at {report['head']}; {report['routes_in_contracts']} routes in the "
          f"contracts; response fields: {report['response_fields']}")
    totals: dict[str, int] = {}
    for g in report["guides"]:
        age = g["age"]
        print(f"\n== {g['guide']}  base {', '.join(g['bases']) or '—'} · {g['table_routes']} table routes · owns "
              f"{g['owned_routes']} · last checked {age['last_checked']} ({age['commit']}), "
              f"{age['commits_since_on_its_sources']} source commits since")
        for item in g["findings"]:
            totals[item["kind"]] = totals.get(item["kind"], 0) + 1
            where = f":{item['line']}" if item["line"] else ""
            print(f"  {item['kind']:<11} {g['guide'].rsplit('/', 1)[-1]}{where}  {item['text']}")
        if g["ids"]:
            print("  ids: " + ", ".join(f"{k} {v}" for k, v in g["ids"].items()))
    print("\n== routes no guide documents")
    for line in report["undocumented"] or ["none"]:
        print(f"  {line}")
    print(f"\n== C-UNPARSED: route registrations in a file no row of {CONTRACTS} names (the generator's gap)")
    unparsed = report["unparsed_routes"]
    for line in (["not checked: no `route_calls` for this stack in [fe_guides]"] if unparsed is None else unparsed or ["none"]):
        print(f"  {line}")
    print("\n== E-UNNAMED: error codes in the source that no guide names, less [fe_guides] internal_error_codes")
    for line in report["unnamed_error_codes"] or ["none"]:
        print(f"  {line}")
    if report["stale_internal_codes"]:
        print("\n== stale [fe_guides] internal_error_codes entries")
        for line in report["stale_internal_codes"]:
            print(f"  {line}")
    print(f"\n== coverage: {report['coverage']}")
    print("\n== totals: " + (", ".join(f"{k} {v}" for k, v in sorted(totals.items())) or "none")
          + f"; undocumented routes {len(report['undocumented'])}, unparsed routes "
          f"{'not checked' if unparsed is None else len(unparsed)}, unnamed error codes "
          f"{len(report['unnamed_error_codes'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("repo", nargs="?", default=str(BOOK))
    parser.add_argument("--ref", default="origin/dev")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = check(Path(args.repo).resolve(), args.ref)
    except SourceMissing as error:
        print(f"guides_check: could not check — {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
