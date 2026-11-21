#!/usr/bin/env python3
"""
Generate docs/api/api-contracts.md from the API's endpoint source.

Statically parses every `src/Boilerplate.API/Endpoints/**/*Endpoints.cs` and emits
one Markdown reference listing every registered route with its verb, full path,
the permission its command or query declares, its request shape (route / query /
body / form), its declared responses and its endpoint name. Routes mapped on the
root app in any other file under `src/Boilerplate.API/` (the health probes, the API
docs, pages a browser opens) are listed as unversioned routes,
and a registration in any API file that the generator did not parse stops the run.

The permission is not on the endpoint. This API protects everything with a
default-deny fallback policy and declares the permission on the request record
(`IRequirePermission`), so the generator follows the handler's `sender.Send(new
XCommand(...))` to that record and reads `Permission => Permissions.A.B` off it.

Request-body types are resolved to their positional-record parameters or public
properties by scanning `src/**/*.cs`, so the doc shows the field list a client
has to send. Response DTOs are resolved the same way, with every type their fields name,
so the doc also shows the fields a client receives.

Usage (from the repository root):
    python3 devtools/checks/gen-api-contracts.py            # writes docs/api/api-contracts.md
    python3 devtools/checks/gen-api-contracts.py --check    # exit 1 if the doc is stale

Runs in CI and in .githooks/pre-commit in --check mode. Re-run after adding,
removing or changing any endpoint registration, and commit the result with it.
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
API = SRC / "Boilerplate.API"
APPLICATION = SRC / "Boilerplate.Application"
ENDPOINTS_DIR = API / "Endpoints"
HEALTH_ENDPOINTS_FILE = API / "Configuration" / "HealthEndpoints.cs"
# Constants endpoint files use for their prefix, tags and health paths.
SHARED_CONSTANT_FILES = [ENDPOINTS_DIR / "ApiRoutes.cs", ENDPOINTS_DIR / "ApiTags.cs", HEALTH_ENDPOINTS_FILE]
PERMISSIONS_FILE = APPLICATION / "Permissions.cs"
OUT = ROOT / "docs" / "api" / "api-contracts.md"
POSTMAN_COLLECTION = ROOT / "devtools" / "api" / "postman" / "boilerplate.postman_collection.json"
POSTMAN_ENVIRONMENT = ROOT / "devtools" / "api" / "postman" / "boilerplate-local.postman_environment.json"
# The framework's calls that do register a route: never a non-route call, whatever factory.toml says.
FRAMEWORK_ROUTE_CALLS = {"Map", "MapGet", "MapPost", "MapPut", "MapDelete", "MapPatch", "MapMethods",
                         "MapHealthChecks", "MapOpenApi", "MapScalarApiReference", "MapControllers",
                         "MapControllerRoute", "MapFallback", "MapFallbackToFile", "MapHub", "MapGroup"}

def contract_settings(root: Path = ROOT) -> dict:
    """
    `[api_contracts]` in devtools/factory/factory.toml: the project's own conventions, none by default.

        [api_contracts]
        # A route behind this endpoint filter needs this header; the Postman request sends {{variable}}.
        header_filters = [{ filter = "ApiKeyFilter", header = "X-Api-Key", variable = "apiKey" }]
        # The marker interface of a request that accepts any of several permissions,
        # read from `AnyOf => [Permissions.A.B, ...];` ("" when the project has none).
        any_permission = "IRequireAnyOf"
        # What each of the project's own unversioned routes is for (mapped on the root app outside
        # the endpoint files); a route with no note stops the run.
        unversioned_notes = { "/welcome" = "page a browser opens from an invitation link" }
        # A heading for an endpoint file whose name differs from the specification's, by file stem.
        section_titles = { Fulfilment = "Picking, Packing & Dispatch" }
        # Map-prefixed calls of the project's that register no route (NON_ROUTE_CALLS has the common ones).
        non_route_calls = ["MapDomainEvents"]
    """
    try:
        with open(root / "devtools" / "factory" / "factory.toml", "rb") as handle:
            config = tomllib.load(handle)
    except (OSError, ValueError):
        config = {}
    table = config.get("api_contracts") or {}
    # The project's word for its scoping boundary (`[book] scoping_word`: company, lab); "tenant" without one.
    scoping = str((config.get("book") or {}).get("scoping_word") or "").strip() or "tenant"
    filters = table.get("header_filters") or []
    if not all(isinstance(f, dict) and {"filter", "header", "variable"} <= set(f) for f in filters):
        raise SystemExit("factory.toml [api_contracts] header_filters: each entry names filter, header and variable")
    tables = {}
    for key in ("unversioned_notes", "section_titles"):
        value = table.get(key) or {}
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            raise SystemExit(f"factory.toml [api_contracts] {key}: a table of strings, name = \"text\"")
        tables[key] = dict(value)
    calls = table.get("non_route_calls") or []
    if not isinstance(calls, list) or not all(isinstance(c, str) and re.fullmatch(r"Map\w+", c) for c in calls):
        raise SystemExit("factory.toml [api_contracts] non_route_calls: a list of Map… method names")
    routing = sorted(set(calls) & FRAMEWORK_ROUTE_CALLS)
    if routing:
        raise SystemExit(f"factory.toml [api_contracts] non_route_calls: {', '.join(routing)} register routes, and "
                         "listing one would hide every route it maps. Name only the project's own Map… calls "
                         "that map none.")
    return {"header_filters": filters, "any_permission": str(table.get("any_permission") or ""), **tables,
            "non_route_calls": list(calls), "scoping_word": scoping}


SETTINGS = contract_settings()

# The route template every group uses; the doc renders the one live version.
VERSION_TEMPLATE = "{version:apiVersion}"
VERSION = "1"

# Handler parameters that are DI / framework plumbing, not part of the contract.
SKIP_PARAM_TYPES = {
    "ISender", "CancellationToken", "HttpContext", "HttpRequest", "HttpResponse",
    "ClaimsPrincipal", "LinkGenerator", "ILoggerFactory", "IServiceProvider",
    "IWebHostEnvironment", "IHostEnvironment", "TimeProvider",
}
FORM_TYPES = {"IFormFile", "IFormFileCollection", "IFormCollection"}
PRIMITIVES = {
    "int", "long", "short", "byte", "bool", "string", "decimal", "double", "float",
    "DateTime", "DateTimeOffset", "DateOnly", "TimeOnly", "Guid", "char",
}

# Response types that carry no schema of the project's own: the error shapes api-conventions.md describes,
# a result the handler writes itself, and the collections that only wrap the type inside them.
FRAMEWORK_RESPONSE_TYPES = {"ProblemDetails", "ValidationProblemDetails", "HttpValidationProblemDetails", "IResult"}
WRAPPER_TYPES = {"List", "IEnumerable", "IReadOnlyList", "IReadOnlyCollection", "ICollection", "IList", "Dictionary",
                 "IDictionary", "IReadOnlyDictionary", "Task", "Nullable", "IAsyncEnumerable"}

STATUS_NAMES = {
    "Status200OK": 200, "Status201Created": 201, "Status202Accepted": 202,
    "Status204NoContent": 204, "Status400BadRequest": 400, "Status401Unauthorized": 401,
    "Status403Forbidden": 403, "Status404NotFound": 404, "Status409Conflict": 409,
    "Status410Gone": 410, "Status413PayloadTooLarge": 413,
    "Status415UnsupportedMediaType": 415, "Status422UnprocessableEntity": 422,
    "Status423Locked": 423, "Status429TooManyRequests": 429,
    "Status500InternalServerError": 500, "Status501NotImplemented": 501,
    "Status503ServiceUnavailable": 503,
}


# --------------------------------------------------------------------------- #
# Generic C# text helpers
# --------------------------------------------------------------------------- #

def strip_comments(text: str) -> str:
    """Remove // and /* */ comments while preserving string literals and line count."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n:
                if text[j] == '\\':
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("\n" * text[i:j].count("\n"))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def match_paren(text: str, open_idx: int, open_ch="(", close_ch=")") -> int:
    """Return index of the matching close for the open at open_idx (skips strings)."""
    depth = 0
    i, n = open_idx, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == '\\':
                    i += 1
                i += 1
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def split_top_level(s: str, sep=",") -> list[str]:
    """Split on sep at bracket depth 0 (handles <>, (), [], {})."""
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in "<([{":
            depth += 1
        elif ch in ">)]}":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


CONST_STRING_RE = re.compile(r"\bconst\s+string\s+(\w+)\s*=\s*([^;]+);")


def split_concatenation(expr: str) -> list[str]:
    """Split a C# string expression on the + operators outside string literals."""
    parts, cur, in_string, i = [], [], False, 0
    while i < len(expr):
        c = expr[i]
        if in_string and c == "\\":
            cur.append(expr[i:i + 2])
            i += 2
            continue
        if c == '"':
            in_string = not in_string
        if c == "+" and not in_string:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur).strip())
    return parts


def resolve_string(expr: str, constants: dict[str, str], where: str) -> str:
    """The value of a route, tag or name argument: string literals and string
    constants, joined with +. Anything else is refused rather than guessed, so a
    route the generator cannot read stops the run instead of leaving the doc."""
    out = []
    for part in split_concatenation(expr):
        if len(part) >= 2 and part[0] == '"' and part[-1] == '"':
            out.append(part[1:-1])
        elif len(part) >= 3 and part.startswith('$"') and part[-1] == '"':
            # An interpolated literal, $"/{route}": each hole a constant or a bound parameter.
            def hole(m: re.Match) -> str:
                if m.group(1) not in constants:
                    raise SystemExit(f"{where}: cannot resolve '{{{m.group(1)}}}' in {part} — "
                                     "use a string literal or a const string")
                return constants[m.group(1)]
            out.append(re.sub(r"\{([\w.]+)\}", hole, part[2:-1]))
        elif part in constants:
            out.append(constants[part])
        else:
            raise SystemExit(f"{where}: cannot resolve '{part}' — use a string literal or a const string")
    return "".join(out)


def load_constants(path: Path, qualifier: str | None = None, strict: bool = True) -> dict[str, str]:
    """The const strings a file declares, by bare name — and as qualifier.Name too
    when other files refer to them through their class (ApiTags.Companies). Not strict,
    a constant the parser cannot read is left out: a route that needs it still stops the run."""
    text = strip_comments(path.read_text(encoding="utf-8-sig"))
    found: dict[str, str] = {}
    # A constant may be built from one declared before it (Root + "/live").
    for m in CONST_STRING_RE.finditer(text):
        try:
            found[m.group(1)] = resolve_string(m.group(2), found, path.name)
        except SystemExit:
            if strict:
                raise
    if qualifier:
        found.update({f"{qualifier}.{name}": value for name, value in list(found.items())})
    return found


def shared_constants() -> dict[str, str]:
    """The string constants endpoint registrations refer to by class."""
    out: dict[str, str] = {}
    for path in SHARED_CONSTANT_FILES:
        out.update({k: v for k, v in load_constants(path, path.stem).items() if "." in k})
    return out


def strip_attributes(s: str) -> tuple[list[str], str]:
    """Split leading [Attr] [Attr2(...)] off a parameter declaration."""
    attrs = []
    s = s.strip()
    while s.startswith("["):
        end = match_paren(s, 0, "[", "]")
        if end == -1:
            break
        attrs.append(s[1:end])
        s = s[end + 1:].strip()
    return attrs, s


def parse_param(decl: str) -> tuple[list[str], str, str, str | None]:
    """'[FromBody] Foo? bar = null' → (attrs, type, name, default)."""
    attrs, rest = strip_attributes(decl)
    default = None
    if "=" in rest:
        rest, default = rest.split("=", 1)
        default = default.strip()
    rest = rest.strip()
    m = re.match(r"^(.*?)\s+(@?\w+)$", rest, re.S)
    if not m:
        return attrs, rest, "", default
    return attrs, re.sub(r"\s+", "", m.group(1)), m.group(2).lstrip("@"), default


def source_files() -> list[Path]:
    return [
        p for p in sorted(SRC.rglob("*.cs"))
        if not any(part in ("obj", "bin") for part in p.parts)
    ]


# --------------------------------------------------------------------------- #
# Type index — resolve request types to their fields, and know every enum
# --------------------------------------------------------------------------- #

@dataclass
class TypeInfo:
    name: str
    kind: str          # record | class
    fields: list[tuple[str, str, str | None]]  # (name, type, default)
    file: Path


class TypeIndex:
    def __init__(self):
        self._index: dict[str, list[tuple[Path, str]]] = defaultdict(list)
        self._cache: dict[str, TypeInfo | None] = {}
        self.enums: dict[str, list[str]] = {}   # name → members, in declaration order

    def build(self, files: list[Path]):
        decl_re = re.compile(r"\b(record|class)\s+(\w+)\b")
        enum_re = re.compile(r"\benum\s+(\w+)\b[^{]*\{")
        member_re = re.compile(r"^\s*(?:\[[^\]]*\]\s*)*(\w+)\s*(?:=\s*[^,]+)?,?\s*$", re.M)
        for path in files:
            text = strip_comments(path.read_text(encoding="utf-8-sig"))
            for m in decl_re.finditer(text):
                self._index[m.group(2)].append((path, text))
            for m in enum_re.finditer(text):
                close = match_paren(text, m.end() - 1, "{", "}")
                self.enums[m.group(1)] = member_re.findall(text[m.end():close])

    def resolve(self, name: str) -> TypeInfo | None:
        base = re.sub(r"\?$", "", name)
        if base in self._cache:
            return self._cache[base]
        info = None
        for path, text in self._index.get(base, []):
            info = self._parse(base, path, text)
            if info:
                break
        self._cache[base] = info
        return info

    def _parse(self, name: str, path: Path, text: str) -> TypeInfo | None:
        # Positional record:  record Name(  ...  ), or a generic one:  record PagedResult<T>(  ...  )
        m = re.search(r"\brecord\s+" + re.escape(name) + r"\s*(?:<[^<>()]*>)?\s*\(", text)
        if m:
            open_idx = m.end() - 1
            close = match_paren(text, open_idx)
            body = text[open_idx + 1:close]
            fields = []
            for decl in split_top_level(body):
                _, ptype, pname, default = parse_param(decl)
                if pname:
                    fields.append((pname, ptype, default))
            fields.extend(self._body_props(text, close))
            return TypeInfo(name, "record", fields, path)
        # Class or non-positional record with a { ... } body
        m = re.search(r"\b(record|class)\s+" + re.escape(name) + r"\b\s*(?:<[^<>()]*>)?[^{;(]*\{", text)
        if m:
            open_idx = m.end() - 1
            return TypeInfo(name, m.group(1), self._body_props(text, open_idx, at_brace=True), path)
        return None

    @staticmethod
    def _body_props(text: str, after_idx: int, at_brace=False) -> list[tuple[str, str, str | None]]:
        if at_brace:
            open_idx = after_idx
        else:
            j = after_idx + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            k = text.find("{", j)
            semi = text.find(";", j)
            if k == -1 or (semi != -1 and semi < k):
                return []
            open_idx = k
        close = match_paren(text, open_idx, "{", "}")
        body = TypeIndex._strip_nested_types(text[open_idx + 1:close])
        props = []
        prop_re = re.compile(
            r"public\s+(?:required\s+)?(?:new\s+)?(?!static|const|class|record|enum|interface|delegate|event|override|virtual|abstract)"
            r"([\w<>\[\],\.\?\s]+?)\s+(\w+)\s*\{\s*(?:get|init|set)[^}]*\}\s*(?:=\s*([^;]+);)?")
        for pm in prop_re.finditer(body):
            ptype = re.sub(r"\s+", "", pm.group(1))
            props.append((pm.group(2), ptype, pm.group(3).strip() if pm.group(3) else None))
        return props

    @staticmethod
    def _strip_nested_types(body: str) -> str:
        out = body
        while True:
            m = re.search(r"\b(?:public|private|internal|protected)?\s*(?:sealed\s+)?(?:record|class|enum)\s+\w+[^{;]*\{", out)
            if not m:
                return out
            open_idx = m.end() - 1
            close = match_paren(out, open_idx, "{", "}")
            if close == -1:
                return out
            out = out[:m.start()] + out[close + 1:]

    def files_declaring(self, name: str) -> list[Path]:
        """The files that declare a class or record of this name."""
        return [path for path, _ in self._index.get(name, [])]

    def is_simple(self, t: str) -> bool:
        t = t.rstrip("?")
        if t.endswith("[]"):
            return self.is_simple(t[:-2])
        gm = re.match(r"^(?:List|IEnumerable|IReadOnlyList|IReadOnlyCollection|ICollection|IList)<(.+)>$", t)
        if gm:
            return self.is_simple(gm.group(1))
        return t in PRIMITIVES or t in self.enums


# --------------------------------------------------------------------------- #
# Permissions — the catalogue, and which request record declares which
# --------------------------------------------------------------------------- #

def load_permission_catalogue() -> dict[str, str]:
    """'Companies.Create' → 'companies.create', from the nested static classes in Permissions.cs."""
    text = strip_comments(PERMISSIONS_FILE.read_text(encoding="utf-8-sig"))
    catalogue: dict[str, str] = {}
    for m in re.finditer(r"static\s+class\s+(\w+)\s*\{", text):
        group = m.group(1)
        if group == "Permissions":
            continue
        close = match_paren(text, m.end() - 1, "{", "}")
        body = text[m.end():close]
        for cm in re.finditer(r'const\s+string\s+(\w+)\s*=\s*"([^"]+)"', body):
            catalogue[f"{group}.{cm.group(1)}"] = cm.group(2)
    return catalogue


def load_request_permissions(files: list[Path], catalogue: dict[str, str]) -> dict[str, str]:
    """'CreateWidgetCommand' → 'widgets.create', from `Permission => Permissions.A.B`."""
    out: dict[str, str] = {}
    decl_re = re.compile(r"\b(?:record|class)\s+(\w+)\b")
    perm_re = re.compile(r"Permission\s*=>\s*Permissions\.(\w+)\.(\w+)\s*;")
    any_re = re.compile(r"AnyOf\s*=>\s*\[([^\]]*)\]\s*;")
    for path in files:
        if APPLICATION not in path.parents:
            continue
        text = strip_comments(path.read_text(encoding="utf-8-sig"))
        any_permission = SETTINGS["any_permission"]
        if "IRequirePermission" not in text and not (any_permission and any_permission in text):
            continue
        for pm in perm_re.finditer(text):
            decls = [d for d in decl_re.finditer(text[:pm.start()])]
            if not decls:
                continue
            request = decls[-1].group(1)
            key = f"{pm.group(1)}.{pm.group(2)}"
            if key not in catalogue:
                raise SystemExit(f"{path.relative_to(ROOT).as_posix()}: {request} declares "
                                 f"Permissions.{key}, which the catalogue parser did not find")
            out[request] = catalogue[key]
        if not any_permission:
            continue
        # `AnyOf => [Permissions.A.B, Permissions.C.D];` — a request that accepts any of several.
        for am in any_re.finditer(text):
            decls = [d for d in decl_re.finditer(text[:am.start()])]
            if not decls:
                continue
            request = decls[-1].group(1)
            keys = [f"{g}.{n}" for g, n in re.findall(r"Permissions\.(\w+)\.(\w+)", am.group(1))]
            missing = [k for k in keys if k not in catalogue]
            if not keys or missing:
                raise SystemExit(f"{path.relative_to(ROOT).as_posix()}: {request} declares AnyOf "
                                 f"{missing or 'nothing'}, which the catalogue parser did not find")
            out[request] = " or ".join(catalogue[k] for k in keys)
        # A class that says it accepts any of several but whose AnyOf the parser cannot read would
        # render as authenticated-only: stop instead.
        accepting = {m.group(1) for m in re.finditer(
            rf"\b(?:record|class)\s+(\w+)[^{{;]*?\b{re.escape(any_permission)}\b", text)}
        unread = sorted(name for name in accepting if name not in out)
        if unread:
            raise SystemExit(f"{path.relative_to(ROOT).as_posix()}: {', '.join(unread)} implements "
                             f"{any_permission} but no `AnyOf => [Permissions.A.B, ...];` was read")
    return out


# --------------------------------------------------------------------------- #
# Endpoint model
# --------------------------------------------------------------------------- #

@dataclass
class Route:
    method: str
    path: str
    name: str = ""
    request_type: str | None = None
    permission: str | None = None
    anonymous: bool = False
    responses: list[tuple[int, str | None]] = field(default_factory=list)
    route_params: list[tuple[str, str]] = field(default_factory=list)
    query_params: list[tuple[str, str, str | None]] = field(default_factory=list)
    body_type: str | None = None
    form_params: list[tuple[str, str]] = field(default_factory=list)
    header_params: list[str] = field(default_factory=list)
    content_types: list[str] = field(default_factory=list)
    size_limited: bool = False
    file: str = ""
    line: int = 0


@dataclass
class Group:
    tag: str
    prefix: str
    area: str
    file: str
    routes: list[Route] = field(default_factory=list)
    heading: str = ""


@dataclass
class GroupDecl:
    prefix: str
    tag: str
    start: int
    end: int


def route_params_from_path(path: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2) or "string")
            for m in re.finditer(r"\{(\w+)(?::(\w+))?\}", path)]


REGISTRATION_RE = re.compile(r"\.Map(?:Get|Post|Put|Delete|Patch)\s*\(")


def find_chain_end(text: str, idx: int) -> int:
    """The `;` that ends the call chain starting at idx (brackets and strings skipped)."""
    depth = 0
    n = len(text)
    i = idx
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == '\\':
                    i += 1
                i += 1
        elif c in "(<{[":
            depth += 1
        elif c in ")>}]":
            depth -= 1
        elif c == ";" and depth <= 0:
            return i
        i += 1
    return n


class EndpointFileParser:
    METHOD_RE = re.compile(r"(\w+|\))\s*\.\s*Map(Get|Post|Put|Delete|Patch)\s*\(")
    GROUP_RE = re.compile(r"(?:\bvar\s+(\w+)\s*=\s*)?(\w+)\s*\.\s*MapGroup\s*\(")

    def __init__(self, path: Path, types: TypeIndex, request_perms: dict[str, str],
                 constants: dict[str, str]):
        self.path = path
        self.rel = path.relative_to(ROOT).as_posix()
        self.text = strip_comments(path.read_text(encoding="utf-8-sig"))
        self.types = types
        self.request_perms = request_perms
        # Its own route-name constants, and the shared prefix and tags.
        self.constants = {**constants, **load_constants(path)}
        self.groups: dict[str, GroupDecl] = {}   # var → group; "" for an inline chain
        self.spans: list[GroupDecl] = []
        self.area = path.parent.name

    def _argument(self, call: str, chain: str) -> str | None:
        """The resolved single argument of the first .call(...) in chain, if any."""
        m = re.search(r"\." + call + r"\(", chain)
        if not m:
            return None
        close = match_paren(chain, m.end() - 1)
        return resolve_string(chain[m.end():close], self.constants, self.rel)

    def _parse_groups(self):
        for m in self.GROUP_RE.finditer(self.text):
            var = m.group(1)
            close = match_paren(self.text, m.end() - 1)
            prefix = resolve_string(self.text[m.end():close], self.constants, self.rel)
            end = self._find_chain_end(close + 1)
            chain = self.text[close + 1:end]
            tag = self._argument("WithTags", chain)
            decl = GroupDecl(prefix.replace(VERSION_TEMPLATE, VERSION),
                             tag if tag is not None else (var or "(untagged)"), m.start(), end)
            if var:
                self.groups[var] = decl
            self.spans.append(decl)

    def _group_for(self, receiver: str, pos: int) -> GroupDecl:
        if receiver in self.groups:
            return self.groups[receiver]
        # A route chained straight onto the MapGroup(...) call, with no variable.
        for decl in self.spans:
            if decl.start <= pos <= decl.end:
                return decl
        # A private helper that takes the group as a parameter (the business-unit logo routes).
        if len(self.spans) == 1:
            return self.spans[0]
        raise SystemExit(f"{self.rel}: route on unknown receiver '{receiver}'")

    def parse(self) -> list[Group]:
        self._parse_groups()
        groups: "OrderedDict[str, Group]" = OrderedDict()
        for m in self.METHOD_RE.finditer(self.text):
            receiver, verb = m.group(1), m.group(2).upper()
            open_idx = m.end() - 1
            close = match_paren(self.text, open_idx)
            arg_parts = split_top_level(self.text[open_idx + 1:close])
            if not arg_parts or not arg_parts[0].startswith('"'):
                continue
            route_path = arg_parts[0].strip('"')
            handler_src = arg_parts[1] if len(arg_parts) > 1 else ""

            decl = self._group_for(receiver, m.start())
            prefix, tag = decl.prefix, decl.tag

            full = prefix.rstrip("/") + ("/" + route_path.strip("/") if route_path.strip("/") else "")
            r = Route(method=verb, path=full, file=self.rel,
                      line=self.text[:m.start()].count("\n") + 1)

            chain = self.text[close + 1:self._find_chain_end(close + 1)]
            self._apply_chain(r, chain)
            self._apply_handler(r, handler_src)

            g = groups.setdefault(tag, Group(tag=tag, prefix=prefix, area=self.area, file=self.rel))
            g.routes.append(r)
        return list(groups.values())

    def _find_chain_end(self, idx: int) -> int:
        return find_chain_end(self.text, idx)

    def _apply_chain(self, r: Route, chain: str):
        r.name = self._argument("WithName", chain) or ""
        r.anonymous = ".AllowAnonymous(" in chain
        for header_filter in SETTINGS["header_filters"]:  # [api_contracts] header_filters
            if f".AddEndpointFilter<{header_filter['filter']}>(" in chain:
                r.header_params.append(header_filter["header"])
        r.size_limited = "RequestSizeLimitAttribute" in chain

        resp: list[tuple[int, str | None]] = []
        produces_re = re.compile(
            r"\.Produces(Problem)?(?:<((?:[^<>]|<(?:[^<>]|<[^<>]*>)*>)*)>)?\s*\(")
        for pm in produces_re.finditer(chain):
            close = match_paren(chain, pm.end() - 1)
            args = split_top_level(chain[pm.end():close])
            problem, typ = pm.group(1) is not None, pm.group(2)
            typ = re.sub(r"\s+", "", typ) if typ else None
            code = 200
            if args:
                sm = re.search(r"Status(\d+\w*)", args[0])
                if sm:
                    code = STATUS_NAMES.get("Status" + sm.group(1), 0)
                elif re.match(r"^\d+$", args[0]):
                    code = int(args[0])
            if problem:
                typ = "ProblemDetails"
            content = [a.strip('"') for a in args[1:] if a.startswith('"')]
            if content:
                r.content_types.extend(content)
            resp.append((code, typ))
        if ".ProducesValidationProblem(" in chain:
            resp.append((400, "ValidationProblemDetails"))
        seen = set()
        r.responses = [x for x in resp if not (x in seen or seen.add(x))]

    def _apply_handler(self, r: Route, handler_src: str):
        r.route_params = route_params_from_path(r.path)
        route_names = {n for n, _ in r.route_params}

        lm = re.match(r"^(?:async\s*)?\(", handler_src)
        if not lm:
            return
        close = match_paren(handler_src, lm.end() - 1)
        params_src = handler_src[lm.end():close]
        body_src = handler_src[close + 1:]

        req_m = re.search(r"\bnew\s+(\w+(?:Command|Query))\s*\(", body_src)
        if req_m:
            r.request_type = req_m.group(1)
            r.permission = self.request_perms.get(r.request_type)

        for decl in split_top_level(params_src):
            attrs, ptype, pname, default = parse_param(decl)
            if not pname:
                continue
            base = ptype.rstrip("?")
            attr_str = " ".join(attrs)
            if base in SKIP_PARAM_TYPES or "FromServices" in attr_str:
                continue
            if re.match(r"^I[A-Z]", base) and base not in FORM_TYPES:
                continue
            if pname in route_names:
                continue
            if "FromHeader" in attr_str:
                hn = re.search(r'Name\s*=\s*"([^"]*)"', attr_str)
                r.header_params.append(hn.group(1) if hn else pname)
                continue
            if base in FORM_TYPES or "FromForm" in attr_str:
                r.form_params.append((pname, ptype))
                continue
            if "FromBody" in attr_str:
                r.body_type = ptype
                continue
            if "AsParameters" in attr_str:
                # One record standing for several query parameters — list each of
                # its fields under the camelCase name ASP.NET binds it by.
                info = self.types.resolve(base)
                if info is None:
                    raise SystemExit(f"[AsParameters] type {base} not found for {r.method} {r.path}")
                for fname, ftype, fdefault in info.fields:
                    qname = fname[:1].lower() + fname[1:]
                    if qname not in route_names:
                        r.query_params.append((qname, ftype, fdefault))
                continue
            if "FromQuery" in attr_str or self.types.is_simple(base):
                r.query_params.append((pname, ptype, default))
                continue
            if r.method in ("POST", "PUT", "PATCH", "DELETE"):
                r.body_type = ptype
            else:
                r.query_params.append((pname, ptype, default))


# --------------------------------------------------------------------------- #
# Unversioned routes — mapped on the root app, in any file outside the endpoint files
# --------------------------------------------------------------------------- #

# Every route-registering call: `.Map<Name>(`, but not MapGroup (a prefix, not a route) and not
# the API's own Map… extension methods, which only call these (OWN_MAP_METHOD_RE finds them).
ROUTE_CALL_RE = re.compile(r"\.(Map(?!Group\b)\w+)\s*(?:<[^<>()]*>)?\s*\(")
# Map-prefixed calls that register no route (API versioning, middleware branching, object mapping,
# claim mapping), left out of the cross-check; a project adds its own in [api_contracts]
# non_route_calls. Any other Map… call still counts, so an unknown one stops the run, never hides.
NON_ROUTE_CALLS = {"MapToApiVersion", "MapWhen", "MapFrom", "MapInboundClaims", "MapJsonKey",
                   "MapUniqueJsonKey", *SETTINGS["non_route_calls"]}
# The framework's plain `.Map(pattern, handler)`, which answers every verb, whatever its pattern is: a
# literal, `@"…"`, a constant, a local or a named argument. The one `.Map(` that is not a route is
# IEndpoint.Map(root, versionSet), called on an IEndpoint (plain_map_calls leaves it out).
PLAIN_MAP_CALL_RE = re.compile(r"(?:\b(\w+)\s*)?\.\s*Map\s*\(")
MAPPING_LAMBDA_RE = re.compile(r"\s*(?:\w+|\([^()]*\))\s*=>")
OWN_MAP_METHOD_RE = re.compile(r"\b(?:static|private|public|internal)\b[^;{}=()]*?\b(Map\w+)\s*\(")
# Calls whose first argument is the path, and the method each answers.
PATH_CALLS = {"MapGet": "GET", "MapPost": "POST", "MapPut": "PUT", "MapDelete": "DELETE",
              "MapPatch": "PATCH", "MapHealthChecks": "GET"}
# Framework calls that map a path of their own (the document name is the one version, v1).
FIXED_PATH_CALLS = {"MapOpenApi": "/openapi/v1.json", "MapScalarApiReference": "/scalar/"}
# What each unversioned route is for; a new one stops the run until it is described here or in
# [api_contracts] unversioned_notes in factory.toml (a project's own routes).
UNVERSIONED_NOTES = {
    "/openapi/v1.json": "OpenAPI document — only when `OpenApi:Exposed` is on",
    "/scalar/": "interactive API reference — only when `OpenApi:Exposed` is on",
    "/": "redirect to `/scalar/` — only when `OpenApi:Exposed` is on",
    **SETTINGS["unversioned_notes"],
}
METHOD_DECL_RE = re.compile(r"\b(\w+)\s*\(([^()]*)\)\s*\{")
NOT_METHODS = {"if", "for", "foreach", "while", "switch", "using", "catch", "lock", "fixed", "when"}


@dataclass
class UnversionedRoute:
    method: str
    path: str
    note: str
    file: str   # relative to src/Boilerplate.API/


def api_files() -> list[Path]:
    """Every C# file of the API, in an order that is the same on every platform."""
    return sorted((p for p in API.rglob("*.cs") if not any(part in ("obj", "bin") for part in p.parts)),
                  key=lambda p: p.relative_to(ROOT).as_posix())


def endpoint_files() -> list[Path]:
    """The versioned endpoint files EndpointFileParser reads."""
    return sorted(ENDPOINTS_DIR.rglob("*Endpoints.cs"), key=lambda p: p.relative_to(ROOT).as_posix())


def class_constants(expr: str, types: TypeIndex) -> dict[str, str]:
    """The constants an expression names through their class (LinkRoutes.CreateAccount)."""
    out: dict[str, str] = {}
    for cls in sorted(set(re.findall(r"\b([A-Z]\w*)\.[A-Z]\w*\b", expr))):
        for path in types.files_declaring(cls):
            out.update({k: v for k, v in load_constants(path, cls, strict=False).items() if "." in k})
    return out


def enclosing_method(text: str, pos: int) -> re.Match | None:
    """The innermost method declaration whose body holds pos."""
    found = None
    for m in METHOD_DECL_RE.finditer(text):
        if m.start() > pos:
            break
        if m.group(1) in NOT_METHODS:
            continue
        if match_paren(text, m.end() - 1, "{", "}") > pos:
            found = m
    return found


def route_paths(text: str, pos: int, expr: str, constants: dict[str, str], types: TypeIndex,
                where: str) -> list[str]:
    """The path, or paths, a registration's path argument stands for. A helper that maps the path
    it is given (`Map(app, LinkRoutes.CreateAccount, …)`) yields one path per call of it: a static
    helper's calls in its own file, an extension method's (`app.MapPage(LinkRoutes.Welcome)`) in any
    API file, where the receiver stands for its `this` parameter."""
    method = enclosing_method(text, pos)
    declared = split_top_level(method.group(2)) if method else []
    params = [parse_param(p)[2] for p in declared]
    bound = [p for p in params if p and re.search(r"\b" + re.escape(p) + r"\b", expr)]
    if not bound:
        return [resolve_string(expr, {**constants, **class_constants(expr, types)}, where)]
    name = method.group(1)
    extension = re.match(r"\s*this\s", declared[0]) is not None
    owners = re.findall(r"\bclass\s+(\w+)", text[:method.start()])
    owner = owners[-1] if owners else ""
    sources = [text]
    if extension:
        sources += [t for t in (strip_comments(p.read_text(encoding="utf-8-sig")) for p in api_files()) if t != text]
    paths = []
    for source in sources:
        for call in re.finditer(r"(?<!\w)" + re.escape(name) + r"\s*\(", source):
            if source is text and call.start() == method.start():
                continue   # the declaration itself
            close = match_paren(source, call.end() - 1)
            args = split_top_level(source[call.end():close])
            before = source[:call.start()].rstrip()
            if before.endswith("."):
                if not extension:
                    continue   # another type's method of the same name
                receiver = re.search(r"(\w+)\s*$", before[:-1])
                if not (receiver and receiver.group(1) == owner):
                    args = ["this", *args]   # the receiver is the `this` parameter
                # else `Owner.MapPage(app, …)`: the static form, which passes `this` itself
            if len(args) != len(params):
                raise SystemExit(f"{where}: a call of {name}(...) passes {len(args)} arguments where the helper "
                                 f"that maps {expr} takes {len(params)}. If it is another overload of {name}, "
                                 "give that overload its own name: the generator binds a helper's calls by name, "
                                 "so it cannot tell which overload maps which route")
            bindings = {}
            for param in bound:
                arg = args[params.index(param)]
                bindings[param] = resolve_string(arg, {**constants, **class_constants(arg, types)}, where)
            paths.append(resolve_string(expr, {**constants, **bindings}, where))
    if not paths:
        raise SystemExit(f"{where}: the route {expr} depends on {', '.join(bound)} of {name}, and no call "
                         "of it was found — the generator binds a static helper's calls in its own file "
                         "and an extension method's in any API file")
    return paths


def parse_unversioned_file(path: Path, types: TypeIndex) -> tuple[list[UnversionedRoute], int]:
    """The routes one file maps on the root app, and how many registration calls it read."""
    text = strip_comments(path.read_text(encoding="utf-8-sig"))
    rel = path.relative_to(API).as_posix()
    where = path.relative_to(ROOT).as_posix()
    constants = load_constants(path, strict=False)
    routes: list[UnversionedRoute] = []
    calls = 0
    for m in ROUTE_CALL_RE.finditer(text):
        call = m.group(1)
        if call not in PATH_CALLS and call not in FIXED_PATH_CALLS:
            continue   # an own extension method, or a call the cross-check reports
        close = match_paren(text, m.end() - 1)
        chain = text[close + 1:find_chain_end(text, close + 1)]
        if call in FIXED_PATH_CALLS:
            paths = [FIXED_PATH_CALLS[call]]
        else:
            args = split_top_level(text[m.end():close])
            if not args:
                raise SystemExit(f"{where}: {call}() with no path")
            paths = route_paths(text, m.start(), args[0], constants, types, where)
        calls += 1
        for route in paths:
            # The section says every unversioned route is anonymous; a protected one must not hide there.
            if ".AllowAnonymous(" not in chain:
                raise SystemExit(f"{where}: {route} is mapped outside the endpoint files without "
                                 ".AllowAnonymous(); the generator lists those as anonymous")
            if call == "MapHealthChecks":
                note = "health check"
            elif route in UNVERSIONED_NOTES:
                note = UNVERSIONED_NOTES[route]
            else:
                raise SystemExit(f"{where}: {route} has no note — add what it is for to "
                                 "[api_contracts] unversioned_notes in devtools/factory/factory.toml")
            routes.append(UnversionedRoute(PATH_CALLS.get(call, "GET"), route, note, rel))
    return routes, calls


def parse_unversioned_routes(types: TypeIndex) -> tuple[list[UnversionedRoute], dict[str, int]]:
    """Every route mapped in an API file other than the endpoint files, and the registration calls
    read in each file."""
    skip = set(endpoint_files())
    routes: list[UnversionedRoute] = []
    calls: dict[str, int] = {}
    for path in api_files():
        if path in skip:
            continue
        found, read = parse_unversioned_file(path, types)
        routes.extend(found)
        if read:
            calls[path.relative_to(ROOT).as_posix()] = read
    return routes, calls


def plain_map_calls(text: str) -> int:
    """The plain `.Map(` registrations in a file: every `.Map(` but the one on an IEndpoint, whose
    receiver the file declares as one (`IEndpoint endpoint`, `foreach (var endpoint in …IEndpoint…)`),
    and an object mapping's `.Map(dest => …, src => …)`, whose first argument is a lambda, never a pattern."""
    count = 0
    for m in PLAIN_MAP_CALL_RE.finditer(text):
        if MAPPING_LAMBDA_RE.match(text, m.end()):
            continue
        receiver = m.group(1)
        if receiver and re.search(
                r"\bIEndpoint\s+" + re.escape(receiver) + r"\b"
                r"|\bforeach\s*\(\s*var\s+" + re.escape(receiver) + r"\s+in\b[^;{]*\bIEndpoint\b", text):
            continue
        count += 1
    return count


def unparsed_route_calls(files: list[Path], parsed: dict[str, int]) -> list[str]:
    """Each file whose route-registering calls outnumber, or fall short of, those the generator
    parsed — so a route mapped in a shape or a file the generator does not read stops the run,
    instead of leaving the doc that claims to list every route without it."""
    texts = {p.relative_to(ROOT).as_posix(): strip_comments(p.read_text(encoding="utf-8-sig")) for p in files}
    own = {name for text in texts.values() for name in OWN_MAP_METHOD_RE.findall(text)}
    own -= set(PATH_CALLS) | set(FIXED_PATH_CALLS)   # a helper may not hide the framework's own
    problems = []
    for rel, text in texts.items():
        found = [m.group(1) for m in ROUTE_CALL_RE.finditer(text)
                 if m.group(1) not in own and m.group(1) not in NON_ROUTE_CALLS]
        found += ["Map"] * plain_map_calls(text)
        if len(found) != parsed.get(rel, 0):
            problems.append(f"{rel}: {len(found)} route registrations ({', '.join(sorted(set(found)))}) "
                            f"but {parsed.get(rel, 0)} parsed")
    return problems


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #

def anchor(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower())
    # One hyphen per space, as GitHub slugs it: "A — B" loses the dash and keeps both spaces.
    return re.sub(r"\s", "-", s.strip())


# The FSD can name a section differently from its endpoint file: [api_contracts] section_titles.
STAGE_TITLES = SETTINGS["section_titles"]


def area_title(folder: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", folder)


def fmt_default(d: str | None) -> str:
    if d in (None, "null", "default"):
        return ""
    return d


def md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ").strip()


def fmt_request(r: Route, referenced: "OrderedDict[str, None]") -> str:
    parts = []
    if r.route_params:
        parts.append("route: " + ", ".join(f"`{n}`:{t}" for n, t in r.route_params))
    if r.query_params:
        qs = []
        for n, t, d in r.query_params:
            qs.append(f"`{n}`:{md_escape(t)}" + (f"={md_escape(fmt_default(d))}" if fmt_default(d) else ""))
        parts.append("query: " + ", ".join(qs))
    if r.header_params:
        parts.append("header: " + ", ".join(f"`{h}`" for h in r.header_params))
    if r.form_params:
        parts.append("form: " + ", ".join(f"`{n}`:{t}" for n, t in r.form_params)
                     + (" (size-limited)" if r.size_limited else ""))
    if r.body_type:
        bt = r.body_type.rstrip("?")
        referenced[bt] = None
        parts.append(f"body: [`{bt}`](#{anchor(bt)})")
    return "<br>".join(parts) if parts else "—"


def fmt_responses(r: Route) -> str:
    if not r.responses:
        return "—"
    out = []
    for code, typ in r.responses:
        if typ == "IResult" and r.content_types:
            out.append(f"{code} " + ", ".join(f"`{c}`" for c in r.content_types))
        elif typ:
            out.append(f"{code} `{md_escape(typ)}`")
        else:
            out.append(str(code))
    return "<br>".join(out)


def fmt_auth(r: Route) -> str:
    if r.anonymous:
        return "anonymous"
    if r.permission:
        return f"`{r.permission}`"
    return "authenticated"


def type_names(t: str) -> list[str]:
    """The project types a C# type names: `PagedResult<FarmDto>` → PagedResult, FarmDto; never a primitive,
    a collection that only wraps, a generic parameter (`T`) or a framework error shape."""
    names = re.findall(r"[A-Za-z_]\w*", t)
    return [n for n in names if n not in PRIMITIVES and n not in WRAPPER_TYPES and n not in FRAMEWORK_RESPONSE_TYPES
            and not re.fullmatch(r"T(?:[A-Z]\w*)?", n) and n not in ("object", "dynamic", "void")]


def response_types(groups: list[Group], types: TypeIndex, listed: set[str]) -> "OrderedDict[str, None]":
    """Every response body type the routes declare, and every type their fields name, in the order first met.
    A media type (`image/jpeg`) is not a type. Types under Request schemas are listed there, not again. A
    declared type the generator cannot find is listed with a note; a field's type it cannot find (`Uri`,
    `JsonElement`) is the framework's, and left out."""
    found: "OrderedDict[str, None]" = OrderedDict()
    queue = [(n, True) for g in groups for r in g.routes for _, typ in r.responses
             if typ and "/" not in typ for n in type_names(typ)]
    while queue:
        name, declared = queue.pop(0)
        if name in found or name in listed or name in types.enums:
            continue
        info = types.resolve(name)
        if info or declared:
            found[name] = None
        if info:
            queue.extend((n, False) for _, ftype, _ in info.fields for n in type_names(ftype))
    return found


def render(groups: list[Group], unversioned: list[UnversionedRoute], types: TypeIndex) -> str:
    by_area: "OrderedDict[str, list[Group]]" = OrderedDict()
    for g in sorted(groups, key=lambda g: (g.area, g.prefix, g.heading)):
        by_area.setdefault(g.area, []).append(g)

    referenced: "OrderedDict[str, None]" = OrderedDict()
    total = sum(len(g.routes) for g in groups)
    anonymous = sum(1 for g in groups for r in g.routes if r.anonymous)
    unpermissioned = sum(1 for g in groups for r in g.routes if not r.anonymous and not r.permission)
    lines: list[str] = []
    w = lines.append

    w("# API contracts — every implemented route")
    w("")
    w("> **Generated file — do not hand-edit.** Regenerate with")
    w("> `python3 devtools/checks/gen-api-contracts.py` from the repository root after any")
    w("> change to a route under `src/Boilerplate.API/`, and commit the result in the same")
    w("> change. The same run then writes the Postman collection in `devtools/api/postman/`")
    w("> from the same parse — a runnable user journey through every versioned route below. CI and")
    w("> `.githooks/pre-commit` run it with `--check` and fail when either file no longer")
    w("> matches the source.")
    w(">")
    w("> This is the **implemented** surface: every route below is parsed from a live")
    w("> `Map{Verb}` registration, with its permission read from the command or query it")
    w("> dispatches. It is not a roadmap and not the front end's contract —")
    w("> [`fe-api-coverage.md`](fe-api-coverage.md) tracks what **they** call and whether we")
    w("> have it; [`../fe-integration-guides/`](../fe-integration-guides/) explains each")
    w("> built module to them. What every route does the same way — bearer auth, the")
    w(f"> error shape, paging, {SETTINGS['scoping_word']} scoping — is in")
    w("> [`api-conventions.md`](../fe-integration-guides/api-conventions.md) and is not")
    w("> repeated here. Request and response field lists are at the end; the OpenAPI document")
    w("> (`/openapi/v1.json`) has the rest.")
    w("")
    w(f"**{total} routes** across **{len(groups)} endpoint groups** — "
      f"{anonymous} anonymous, {unpermissioned} authenticated-only, "
      f"{total - anonymous - unpermissioned} permission-gated.")
    w("")
    w("## How to read a row")
    w("")
    w("- **Path** is the live `v1` route; the source template is `/api/v{version:apiVersion}/…`.")
    w("- **Auth** — `anonymous` is an explicit `.AllowAnonymous()`; a permission string is what the")
    w("  dispatched command or query declares through `IRequirePermission` (the catalogue is")
    w("  `src/Boilerplate.Application/Permissions.cs`); `authenticated` means the default-deny fallback")
    w("  policy requires a valid bearer token and the request declares no further permission.")
    w("- **Request** — `route:` path parameters · `query:` query-string parameters as `name`:type=default")
    w("  · `body:` the JSON body type, linked to its field list in [Request schemas](#request-schemas)")
    w("  · `form:` multipart fields · `header:` required headers.")
    w("- **Responses** — the declared status codes and body types. `ProblemDetails` and")
    w("  `ValidationProblemDetails` follow the error shape in `api-conventions.md`.")
    w("- **Dispatches** — the command or query the endpoint sends, which is where the behaviour lives.")
    w("")
    w("## Unversioned routes")
    w("")
    w("Mapped on the root app outside the endpoint files, all anonymous. They are probes, the API")
    w("docs and pages a browser or another app opens, not routes the front end calls, so the")
    w("Postman collection does not walk them. Source is under `src/Boilerplate.API/`.")
    w("")
    w("| Method | Path | Notes | Source |")
    w("|---|---|---|---|")
    for r in unversioned:
        w(f"| {r.method} | `{r.path}` | {r.note} | `{r.file}` |")
    w("")
    w("## Table of contents")
    w("")
    for area, gs in by_area.items():
        w(f"- **{area_title(area)}**")
        for g in gs:
            w(f"  - [{g.heading}](#{anchor(g.heading)}) — `{g.prefix}` ({len(g.routes)})")
    w("- [Request schemas](#request-schemas)")
    w("- [Response schemas](#response-schemas)")
    w("")

    for area, gs in by_area.items():
        w(f"## {area_title(area)}")
        w("")
        for g in gs:
            w(f"### {g.heading}")
            w("")
            w(f"Prefix `{g.prefix}` · source `{g.file}`")
            w("")
            w("| Method | Path | Auth | Request | Responses | Dispatches |")
            w("|---|---|---|---|---|---|")
            for r in g.routes:
                dispatch = f"`{r.request_type}`" if r.request_type else "—"
                w(f"| {r.method} | `{r.path}` | {fmt_auth(r)} | {fmt_request(r, referenced)} "
                  f"| {fmt_responses(r)} | {dispatch} |")
            w("")

    w("## Request schemas")
    w("")
    w("Field lists for every request body type referenced above, resolved from the C# record or")
    w("class declaration (positional-record parameters first, then public properties). Nested types")
    w("are shown by name only — see the OpenAPI document for their shape. `?` = nullable.")
    w("")
    unresolved = []
    for tname in referenced:
        info = types.resolve(tname)
        w(f"### {tname}")
        w("")
        if not info:
            unresolved.append(tname)
            w("_(declaration not found by the generator — see the OpenAPI document)_")
            w("")
            continue
        w(f"`{info.kind}` · `{info.file.relative_to(ROOT).as_posix()}`")
        w("")
        if not info.fields:
            w("_(no fields)_")
            w("")
            continue
        w("| Field | Type | Default |")
        w("|---|---|---|")
        for n, t, d in info.fields:
            w(f"| `{n}` | `{md_escape(t)}` | {md_escape(fmt_default(d))} |")
        w("")
    if unresolved:
        raise SystemExit(f"request types not found in src/: {', '.join(unresolved)}")

    w("## Response schemas")
    w("")
    w("Field lists for every response body type the routes declare, and for every type those fields name,")
    w("resolved the same way. A field is sent in camelCase (`DisplayName` is `displayName`). The error")
    w("shapes are in `api-conventions.md`; a type listed under Request schemas is not repeated here.")
    w("")
    for tname in response_types(groups, types, set(referenced)):
        info = types.resolve(tname)
        w(f"### {tname}")
        w("")
        if not info:
            w("_(declaration not found by the generator — see the OpenAPI document)_")
            w("")
            continue
        w(f"`{info.kind}` · `{info.file.relative_to(ROOT).as_posix()}`")
        w("")
        if not info.fields:
            w("_(no fields)_")
            w("")
            continue
        w("| Field | Type |")
        w("|---|---|")
        for n, t, _ in info.fields:
            w(f"| `{n}` | `{md_escape(t)}` |")
        w("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #

def collect() -> tuple[list[Group], list[UnversionedRoute], TypeIndex]:
    files = source_files()
    types = TypeIndex()
    types.build(files)
    request_perms = load_request_permissions(files, load_permission_catalogue())
    groups: list[Group] = []
    registered = 0
    constants = shared_constants()
    for path in endpoint_files():
        parser = EndpointFileParser(path, types, request_perms, constants)
        registered += len(REGISTRATION_RE.findall(parser.text))
        groups.extend(parser.parse())
    parsed = sum(len(g.routes) for g in groups)
    if parsed != registered:
        # A registration the parser could not read would otherwise vanish from the
        # doc without a trace, and --check would pass on the shorter file.
        raise SystemExit(f"{registered} Map{{Verb}}( registrations in the endpoint files "
                         f"but {parsed} routes parsed; the generator needs teaching the new shape")
    unversioned, calls = parse_unversioned_routes(types)
    # Every registration in every API file, not only the files read above: a route mapped
    # anywhere else would otherwise be missing from the doc without a trace.
    per_file: dict[str, int] = defaultdict(int, calls)
    for g in groups:
        for r in g.routes:
            per_file[r.file] += 1
    problems = unparsed_route_calls(api_files(), per_file)
    if problems:
        raise SystemExit("route registrations the generator did not parse — teach it the shape:"
                         + "".join(f"\n  {problem}" for problem in problems))
    by_tag: dict[str, list[Group]] = defaultdict(list)
    for g in groups:
        by_tag[g.tag].append(g)
    for g in groups:
        g.heading = g.tag
        if len(by_tag[g.tag]) > 1:
            # Several files can share one Swagger tag: name each
            # section after the file so the headings, and their anchors, stay distinct.
            stem = Path(g.file).stem.removesuffix("Endpoints").removeprefix(g.area)
            g.heading = f"{g.tag} — {STAGE_TITLES.get(stem, area_title(stem))}"
    return groups, unversioned, types


def outputs(groups: list[Group], unversioned: list[UnversionedRoute],
            types: TypeIndex) -> list[tuple[Path, str]]:
    """Every file one run produces, in the order it writes them: the doc first, then the
    Postman collection and its environment from the same parse — never one without the other."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import api_contracts_postman as postman
    postman.HEADER_FILTERS = SETTINGS["header_filters"]

    return [
        (OUT, render(groups, unversioned, types)),
        (POSTMAN_COLLECTION, postman.render_collection(groups, types)),
        (POSTMAN_ENVIRONMENT, postman.render_environment()),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="fail if api-contracts.md or the Postman collection is out of date")
    args = ap.parse_args()

    groups, unversioned, types = collect()
    files = outputs(groups, unversioned, types)
    if args.check:
        stale = []
        for path, content in files:
            # A Windows checkout may hold the file with CRLF; only the content is compared.
            current = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else ""
            if current != content:
                stale.append(path.relative_to(ROOT).as_posix())
        if stale:
            print(f"STALE: {', '.join(stale)} does not match the endpoint source. "
                  f"Run devtools/checks/gen-api-contracts.py and commit the result.", file=sys.stderr)
            return 1
        print("api-contracts.md and the Postman collection are up to date")
        return 0
    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT).as_posix()}")
    total = sum(len(g.routes) for g in groups)
    print(f"{total} routes in {len(groups)} groups")
    return 0


if __name__ == "__main__":
    sys.exit(main())
