"""
Renders the Postman collection that gen-api-contracts.py writes beside
docs/api/api-contracts.md, from the same parse of the endpoint source.

The parser knows every route, its parameters, its body type and its declared
responses. What it cannot know is the order a user would call them in, and what
a realistic body looks like — that is JOURNEY and SAMPLES below, kept by hand.
Both are checked against the parse on every run: a route the source registers
that JOURNEY does not place, a JOURNEY entry naming a route that no longer
exists, or a sample carrying a field its model does not declare, each fail the
run rather than produce a collection that is quietly wrong.

The collection is runnable top to bottom in Postman's Runner against a fresh
local or DEV database: Login stores the bearer token, every create stores the
id it returned into a collection variable, and later requests use those
variables. Every DELETE and whole-record exit is in the final Teardown folder in
reverse dependency order, so the Runner does not tear down what a later folder
still needs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

COLLECTION_NAME = "Boilerplate API"
SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"

# The environment file ships the local defaults. The seeded development admin
# is a public constant in this repository (README, devtools/README) and exists
# only where Seeding__DevelopmentAdmin is on; DEV and QA hosts are listed
# disabled, and UAT and production are absent on purpose.
ENVIRONMENT = [
    ("baseUrl", "https://localhost:7102", True),
    ("baseUrlDev", "https://dev.api.example.test", False),
    ("baseUrlQa", "https://qa.api.example.test", False),
    ("adminEmail", "admin@example.com", True),
    ("adminPassword", "development-only", True),
]

# The endpoint filters that require a header ([api_contracts] header_filters, set by gen-api-contracts.py):
# each request behind one sends {{variable}}, and the environment carries the variable to fill.
HEADER_FILTERS: list[dict] = []


def environment() -> list[tuple[str, str, bool]]:
    return ENVIRONMENT + [(f["variable"], f"REPLACE-with-the-{f['header']}", True) for f in HEADER_FILTERS]


class Var(str):
    """A `{{variable}}` reference to a number inside a sample body — emitted unquoted."""


class TextVar(Var):
    """A `{{variable}}` reference to a string — emitted inside quotes."""


def V(name: str) -> Var:
    return Var(name)


def T(name: str) -> TextVar:
    return TextVar(name)


@dataclass
class Step:
    name: str                       # the endpoint's WithName
    label: str = ""                 # request title; defaults to a spaced name
    body: Any = None                # sample body; None → skeleton from the schema
    capture: dict[str, str] = field(default_factory=dict)   # variable → JS expression over `body`
    path: dict[str, str] = field(default_factory=dict)      # route param → variable or literal
    query: dict[str, Any] = field(default_factory=dict)     # enabled query params and their values
    note: str = ""
    expect: int = 0                 # the status this step is meant to answer when it is not the route's success (a deliberate refusal)


@dataclass
class Folder:
    name: str
    steps: list["Step | Folder"]
    id_var: str = ""                # what a bare `{id}` route parameter means in this folder
    note: str = ""
    also_accept: tuple[int, ...] = ()   # statuses the folder's tests pass on besides the declared success


S = Step

# --------------------------------------------------------------------------- #
# Sample bodies, keyed by request label (a body type used twice with different
# data gets two labels) or by body type when one sample serves every use.
# --------------------------------------------------------------------------- #

# The request body each route's step sends, by model name: the project's own, written as routes arrive.
SAMPLES: dict[str, Any] = {
}

# Values for multipart fields that are not files: an upload's own UUID and the client's time.
FORM_VALUES: dict[str, str] = {
}

FILE_TYPES = {"IFormFile", "IFormFileCollection"}


def form_field(name: str, type_name: str) -> dict:
    """A file part to pick in Postman, or a text part with its sample value."""
    if type_name.rstrip("?") in FILE_TYPES:
        return {"key": name, "type": "file", "src": ""}
    if name not in FORM_VALUES:
        raise SystemExit(f"multipart field `{name}` has no sample in FORM_VALUES")
    return {"key": name, "type": "text", "value": FORM_VALUES[name]}


# Query-string values for the parameters that must be sent, or that make a
# lookup return something. Everything else is emitted disabled with a hint.
QUERY_VALUES: dict[str, Any] = {
    "page": 1, "pageSize": 20, "take": 20, "search": "", "sortDescending": "false",
    "isActive": "true", "limit": 20, "cursor": "", "excludeId": "",
    "from": "2026-01-01", "to": "2026-12-31", "updatedFrom": "", "updatedTo": "",
    "lastUpdatedFrom": "", "lastUpdatedTo": "",
}

# --------------------------------------------------------------------------- #
# The journey. Folder order follows the order a user meets the product; within a folder,
# create → read → list/lookup → update → status, so every id exists before it is
# used. A bare {id} means the folder's id_var unless a step says otherwise.
# --------------------------------------------------------------------------- #

FIRST_ID = "body.id"

# One Folder per module, in the order a user meets the product; the project writes it.
JOURNEY: list[Folder] = [
]


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def spaced(name: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def camel(name: str) -> str:
    return name[0].lower() + name[1:]


NUMERIC_MARK = "@num@"   # wraps a numeric Var so the quotes around it can be stripped after dumping


def dump_body(value: Any) -> str:
    """JSON with numeric `Var` references emitted unquoted and text ones quoted."""
    text = json.dumps(value, indent=2)
    return re.sub('"' + NUMERIC_MARK + r'(\{\{\w+\}\})' + NUMERIC_MARK + '"', r"\1", text)


def _prepare(value: Any) -> Any:
    if isinstance(value, TextVar):
        return "{{" + str(value) + "}}"
    if isinstance(value, Var):
        return NUMERIC_MARK + "{{" + str(value) + "}}" + NUMERIC_MARK
    if isinstance(value, dict):
        return {k: _prepare(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_prepare(v) for v in value]
    return value


def skeleton(type_name: str, types, depth: int = 0) -> Any:
    """A body with every field present and a type-shaped placeholder, for a type with no sample."""
    info = types.resolve(type_name)
    if not info or depth > 2:
        return {}
    out = {}
    for name, ftype, _ in info.fields:
        out[camel(name)] = placeholder(ftype, types, depth)
    return out


def placeholder(ftype: str, types, depth: int) -> Any:
    base = ftype.rstrip("?")
    gm = re.match(r"^(?:IReadOnlyList|IReadOnlyCollection|List|IEnumerable|ICollection)<(.+)>$", base)
    if gm:
        return [placeholder(gm.group(1), types, depth)]
    if base in ("int", "long", "short", "decimal", "double", "float"):
        return 0
    if base == "bool":
        return False
    if base in ("DateOnly",):
        return "2026-01-01"
    if base in ("TimeOnly",):
        return "09:00"
    if base in ("DateTime", "DateTimeOffset"):
        return "2026-01-01T09:00:00Z"
    if base in types.enums:
        return types.enums[base][0]
    if base == "string":
        return ""
    return skeleton(base, types, depth + 1)


def check_sample(label: str, sample: Any, type_name: str, types) -> None:
    """A sample field the model does not declare is a typo that would 400 on every run."""
    info = types.resolve(type_name)
    if not info or not isinstance(sample, dict):
        return
    declared = {camel(n): t for n, t, _ in info.fields}
    unknown = sorted(set(sample) - set(declared))
    if unknown:
        raise SystemExit(f"postman sample '{label}' has fields {type_name} does not declare: {', '.join(unknown)}")
    for name, value in sample.items():
        ftype = declared[name].rstrip("?")
        gm = re.match(r"^(?:IReadOnlyList|IReadOnlyCollection|List|IEnumerable|ICollection)<(.+)>$", ftype)
        if gm and isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    check_sample(f"{label}.{name}[]", item, gm.group(1), types)
        elif isinstance(value, dict):
            check_sample(f"{label}.{name}", value, ftype, types)
        elif ftype in types.enums and isinstance(value, str) and not isinstance(value, Var):
            if value not in types.enums[ftype]:
                raise SystemExit(f"postman sample '{label}.{name}': {value!r} is not a member of {ftype}")


def success_code(route) -> int:
    for code, _ in route.responses:
        if 200 <= code < 300:
            return code
    return 200


def test_script(route, step: Step, also_accept: tuple[int, ...] = ()) -> list[str]:
    ok = step.expect or success_code(route)
    if also_accept:
        codes = ", ".join(str(c) for c in (ok, *also_accept))
        lines = [f'pm.test("{step.label or spaced(step.name)} → {ok} (or {", ".join(map(str, also_accept))} while still referenced)", function () {{',
                 f"    pm.expect(pm.response.code).to.be.oneOf([{codes}]);",
                 "});"]
    else:
        lines = [f'pm.test("{step.label or spaced(step.name)} → {ok}", function () {{',
                 f"    pm.response.to.have.status({ok});",
                 "});"]
    if step.capture:
        # Each capture is its own test, so a response that lacks the value (an empty
        # list, a DEV API that does not echo tokens) shows up in the Runner by name
        # instead of aborting the script before the later captures run.
        lines.append("if (pm.response.code < 300 && pm.response.text()) {")
        lines.append("    const body = pm.response.json();")
        for var, expr in step.capture.items():
            lines.append(f'    pm.test("captured {var}", function () {{')
            lines.append(f"        const value = {expr};")
            lines.append(f"        pm.expect(value, \"{expr}\").to.not.be.undefined;")
            lines.append(f'        pm.collectionVariables.set("{var}", value);')
            lines.append("    });")
        lines.append("}")
    return lines


def route_path(route, step: Step, folder_id_var: str, variables: set[str]) -> tuple[str, list[str]]:
    """The path with each {param} replaced by a {{variable}} the journey captures, or by a literal."""
    def sub(m):
        name = m.group(1)
        value = step.path.get(name)
        if value is None:
            if name == "id" and folder_id_var:
                value = folder_id_var
            elif name.endswith("Id"):
                value = name
            else:
                raise SystemExit(f"postman: {step.name} has route parameter {{{name}}} and the journey does not say what to put in it")
        if value in variables:
            return "{{" + value + "}}"
        if value.endswith("Id"):
            raise SystemExit(f"postman: {step.name} puts '{value}' in {{{name}}}, but no step captures a variable of that name")
        return value
    path = re.sub(r"\{(\w+)(?::\w+)?\}", sub, route.path)
    return path, [seg for seg in path.split("/") if seg]


def query_entries(route, step: Step, types) -> list[dict]:
    out = []
    for name, ptype, default in route.query_params:
        base = ptype.rstrip("?")
        required = not ptype.endswith("?") and default is None and not base.endswith("[]")
        enabled = name in step.query or required
        if name in step.query:
            value = step.query[name]
            value = "{{" + str(value) + "}}" if isinstance(value, Var) else str(value)
        elif base in types.enums:
            value = types.enums[base][0]
        elif name in QUERY_VALUES:
            value = str(QUERY_VALUES[name])
        elif name.endswith("Id"):
            value = "{{" + name + "}}"
        else:
            value = ""
        entry = {"key": name, "value": value, "description": ptype}
        if not enabled:
            entry["disabled"] = True
        out.append(entry)
    return out


def request_item(route, step: Step, folder: Folder, types, variables: set[str]) -> dict:
    folder_id_var = folder.id_var
    raw, segments = route_path(route, step, folder_id_var, variables)
    query = query_entries(route, step, types)
    url = {"raw": "{{baseUrl}}" + raw + ("?" + "&".join(f"{q['key']}={q['value']}" for q in query if not q.get("disabled")) if any(not q.get("disabled") for q in query) else ""),
           "host": ["{{baseUrl}}"], "path": segments}
    if query:
        url["query"] = query
    request: dict[str, Any] = {"method": route.method, "header": [], "url": url}
    description = []
    if step.note:
        description.append(step.note)
    if route.permission:
        description.append(f"Permission: `{route.permission}`")
    elif route.anonymous:
        description.append("Anonymous — no bearer token is sent.")
    else:
        description.append("Any authenticated user.")
    description.append(f"Dispatches `{route.request_type}` · `{route.file}`")
    request["description"] = "\n\n".join(description)

    if route.anonymous:
        request["auth"] = {"type": "noauth"}

    variables_by_header = {f["header"]: f["variable"] for f in HEADER_FILTERS}
    for header in route.header_params:
        if header in variables_by_header:
            request["header"].append({"key": header, "value": "{{" + variables_by_header[header] + "}}"})

    if route.body_type:
        label = step.label or ""
        sample = SAMPLES.get(label) if label in SAMPLES else SAMPLES.get(route.body_type)
        if sample is None:
            sample = skeleton(route.body_type, types)
        else:
            check_sample(label or route.body_type, sample, route.body_type, types)
        request["header"].append({"key": "Content-Type", "value": "application/json"})
        request["body"] = {"mode": "raw", "raw": dump_body(_prepare(sample)),
                           "options": {"raw": {"language": "json"}}}
    elif route.form_params:
        request["body"] = {"mode": "formdata", "formdata": [form_field(n, t) for n, t in route.form_params]}

    return {"name": step.label or spaced(step.name), "request": request,
            "event": [{"listen": "test", "script": {"type": "text/javascript",
                                                    "exec": test_script(route, step, folder.also_accept)}}],
            "response": []}


def folder_item(folder: Folder, by_name: dict, types, used: dict[str, int], variables: set[str]) -> dict:
    items = []
    for entry in folder.steps:
        if isinstance(entry, Folder):
            items.append(folder_item(entry, by_name, types, used, variables))
            continue
        if entry.name not in by_name:
            raise SystemExit(f"postman journey names '{entry.name}', which no endpoint registers")
        used[entry.name] = used.get(entry.name, 0) + 1
        items.append(request_item(by_name[entry.name], entry, folder, types, variables))
    item = {"name": folder.name, "item": items}
    if folder.note:
        item["description"] = folder.note
    return item


def collection_variables(groups) -> list[dict]:
    names: list[str] = ["accessToken", "refreshToken"]
    def walk(folders):
        for f in folders:
            for s in f.steps:
                if isinstance(s, Folder):
                    walk([s])
                else:
                    for var in s.capture:
                        if var not in names:
                            names.append(var)
    walk(JOURNEY)
    return [{"key": n, "value": ""} for n in names]


def render_collection(groups, types) -> str:
    by_name = {}
    for g in groups:
        for r in g.routes:
            if not r.name:
                raise SystemExit(f"{r.file}: {r.method} {r.path} has no WithName, which the journey needs")
            if r.name in by_name:
                raise SystemExit(f"endpoint name {r.name} is registered twice")
            by_name[r.name] = r
    used: dict[str, int] = {}
    variables = {v["key"] for v in collection_variables(groups)} | {k for k, _, _ in environment()}
    folders = [folder_item(f, by_name, types, used, variables) for f in JOURNEY]
    unplaced = sorted(set(by_name) - set(used))
    if unplaced:
        raise SystemExit("postman journey does not place these endpoints: " + ", ".join(unplaced)
                         + " — add each to JOURNEY in api_contracts_postman.py")
    collection = {
        "info": {
            "name": COLLECTION_NAME,
            "description": (
                "Generated by devtools/checks/gen-api-contracts.py from the endpoint source, in the same run "
                "that writes docs/api/api-contracts.md. Do not edit — re-run the generator.\n\n"
                "Folders follow the user journey in docs/system-overview.md §6 and are runnable top to bottom "
                "on a fresh local or DEV database: 01 stores the bearer token, every create stores the id it "
                "returned, and later requests use those variables. Select the environment file beside this "
                "collection for baseUrl and the admin credentials."
            ),
            "schema": SCHEMA,
        },
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{accessToken}}", "type": "string"}]},
        "variable": collection_variables(groups),
        "item": folders,
    }
    return json.dumps(collection, indent=2, ensure_ascii=False) + "\n"


def render_environment() -> str:
    env = {
        "name": "Boilerplate — local",
        "values": [{"key": k, "value": v, "enabled": enabled, "type": "default"} for k, v, enabled in environment()],
        "_postman_variable_scope": "environment",
    }
    return json.dumps(env, indent=2, ensure_ascii=False) + "\n"
