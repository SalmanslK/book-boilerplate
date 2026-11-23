#!/usr/bin/env python3
"""
The dotnet pack's shape checks: three rules that need C#'s structure, not one line of it. The one-line
rules (an anonymous route, a side effect in an event handler, a setting read by its key, a migrations
test that must exist, schema in a data script) are `[[code_rules]]` with allow-lists, for every stack
(check-code-rules.py); these three are what is left.

1. **A list of references in a request refuses a null item** (`list-items`). For every FluentValidation
   validator, the type it validates — and the types it holds, three levels down — is read for list
   properties (`List<T>`, `IReadOnlyList<T>`, `T[]` …) of a reference type. Each needs
   `RuleForEach(x => x.Path).NotNull()` (or `.NotEmpty()`) in the validator or a base it derives from:
   `{"ids":[null]}` otherwise binds and answers 500. Value types (numbers, `Guid`, enums, structs) are
   left out: a null fails binding with a 400 already. An item declared nullable (`string?`) is a choice.
2. **Options are validated on start, by a validator** (`options-validated`). Every `AddOptions<T>()`
   chain calls `.ValidateOnStart()`, and something stands behind it: a `.Validate(…)`, an
   `IValidateOptions<T>`, or `.ValidateDataAnnotations()` on a `T` with an annotation. `ValidateOnStart`
   alone validates nothing.
3. **A unique index has the right shape** (`unique-index`). On an entity carrying the tenant key
   (`tenant_key`) it leads with that key; on an entity mapping the soft-delete column (`deleted_at`,
   by its property `DeletedAt`, inherited or declared — not only by an interface), it filters
   `deleted_at IS NULL`, or a deleted row blocks a live one forever. A filter written as a constant
   (`SoftDeleteFilter.Live`, `$"{…} AND …"`) is resolved from the source.

The project's exceptions are its own, in devtools/factory/factory.toml, each with a reason:

    [dotnet_checks]
    tenant_key = "TenantId"            # empty: that leg is not judged, and the run says so
    soft_delete_column = "deleted_at"
    options_unvalidated = [{ type = "JwtBearerOptions", reason = "the framework's; bound by AddJwtBearer" }]
    unique_indexes = [{ index = "User(NormalizedEmail)", reason = "one account per address, across tenants" }]
    list_items_nullable = [{ property = "ImportCommand.Rows", reason = "a null row is reported per row" }]

An entry that matches nothing is stale and fails, so the lists stay true.

    python devtools/checks/check-dotnet.py              # every tracked C# file under src/

Reads source text, not the compiled model: what it cannot read (a column named by a helper, a filter
built at run time) it reports as a failure to look at, never as a pass. Exits 1 on a breach or when it
could not check (no factory.toml, an entry without a reason, a git command that failed).

Python 3.11+, standard library only.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
# The settings: factory.toml merged over the Book's defaults (devtools/factory/book_config.py).
_FACTORY = next((p / "devtools" / "factory" for p in Path(__file__).resolve().parents
                 if (p / "devtools" / "factory" / "book_config.py").is_file()), None)
if _FACTORY is not None:
    sys.path.insert(0, str(_FACTORY))
import book_config  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FACTORY_TOML = "devtools/factory/factory.toml"
NAME = "check-dotnet"
SOURCES = re.compile(r"^src/.+\.cs$")
DEPTH = 3  # how far into a request's own types the list rule looks


class CouldNotCheck(Exception):
    """The settings, or the source, could not be read: nothing may pass as clean."""


# ------------------------------------------------------------------ reading C#

# A raw, verbatim or plain string, a character literal, or a comment: the first one at each point wins.
TOKEN = re.compile(r'"""[\s\S]*?"""|(?:\$@|@\$|@)"(?:[^"]|"")*"|\$?"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\\\'\n])\'|'
                   r"//[^\n]*|/\*[\s\S]*?\*/")


def strip_comments(text: str) -> str:
    """The text with every comment blanked (newlines kept, so line numbers hold); strings left as they are."""
    def blank(match: re.Match) -> str:
        token = match.group(0)
        return re.sub(r"[^\n]", " ", token) if token.startswith(("//", "/*")) else token
    return TOKEN.sub(blank, text)


STRING_END = (re.compile(r'(?:\\.|[^"\\\n])*"?'), re.compile(r'(?:[^"]|"")*"?'))


def skip_string(text: str, i: int) -> int:
    """The index after a string literal starting at text[i] (a `"`)."""
    if text.startswith('"""', i):
        end = text.find('"""', i + 3)
        return len(text) if end < 0 else end + 3
    verbatim = "@" in text[max(0, i - 2):i]
    return STRING_END[verbatim].match(text, i + 1).end()


_BRACKETS: dict[tuple[str, str], re.Pattern] = {}


def balanced(text: str, i: int, open_: str, close: str) -> int:
    """The index after the bracket that closes the one at text[i], strings skipped."""
    pattern = _BRACKETS.setdefault((open_, close), re.compile("[\"" + re.escape(open_ + close) + "]"))
    depth, position = 0, i
    while True:
        match = pattern.search(text, position)
        if not match:
            return len(text)
        if match.group(0) == '"':
            position = skip_string(text, match.start())
            continue
        depth += 1 if match.group(0) == open_ else -1
        position = match.end()
        if depth == 0:
            return position


def split_top(text: str, sep: str = ",") -> list[str]:
    """Split at `sep` outside brackets and strings."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(text):
        c = text[i]
        if c == '"':
            i = skip_string(text, i)
            continue
        if c in "<([{":
            depth += 1
        elif c in ">)]}":
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


def statement_end(text: str, i: int) -> int:
    """The index of the `;` that ends the statement holding text[i], brackets and strings skipped."""
    depth = 0
    while i < len(text):
        c = text[i]
        if c == '"':
            i = skip_string(text, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth < 0:
                return i
        elif c == ";" and depth == 0:
            return i
        i += 1
    return len(text)


def simple_name(type_text: str) -> str:
    """`Ns.Widget<T>?` → `Widget`."""
    return re.sub(r"<.*", "", type_text.strip().rstrip("?").replace("[]", "")).split(".")[-1].strip()


def generic_args(type_text: str) -> list[str]:
    start = type_text.find("<")
    if start < 0:
        return []
    return split_top(type_text[start + 1:balanced(type_text, start, "<", ">") - 1])


@dataclass
class TypeDecl:
    name: str
    kind: str
    type_params: list[str]
    bases: list[str]
    body: str
    file: str
    line: int
    abstract: bool
    properties: dict[str, str] = field(default_factory=dict)  # name -> type text
    bindable: set[str] = field(default_factory=set)  # the properties a JSON body can set: positional, set, init


DECLARATION = re.compile(r"\b(?P<mods>(?:(?:public|internal|private|protected|sealed|abstract|static|partial|"
                         r"file|readonly|ref|unsafe|new)\s+)*)(?P<kind>record\s+struct|record\s+class|record|class|"
                         r"struct|interface|enum)\s+(?P<name>[A-Za-z_]\w*)")
PROPERTY = re.compile(r"^[ \t]*(?:\[[^\]\n]*\][ \t]*)*(?P<head>(?:(?:public|internal|protected|private|required|"
                      r"virtual|override|abstract|sealed|new|static|readonly)[ \t]+)*)(?P<type>[\w.]+(?:<[^;{}()=\n]*>)?"
                      r"(?:\[\])?\??)[ \t]+(?P<name>[A-Z]\w*)\s*\{\s*(?:(?:public|private|internal|protected)\s+)?"
                      r"(?:get|init|set)\b", re.M)


def declarations(rel: str, text: str) -> list[TypeDecl]:
    found = []
    for match in DECLARATION.finditer(text):
        i = match.end()
        type_params: list[str] = []
        while i < len(text) and text[i].isspace():
            i += 1
        if text.startswith("<", i):
            end = balanced(text, i, "<", ">")
            type_params = [re.sub(r"^(?:in|out)\s+", "", p) for p in split_top(text[i + 1:end - 1])]
            i = end
        while i < len(text) and text[i].isspace():
            i += 1
        positional = ""
        if text.startswith("(", i):
            end = balanced(text, i, "(", ")")
            positional = text[i + 1:end - 1]
            i = end
        head_end = i
        while head_end < len(text) and text[head_end] not in "{;":
            if text[head_end] == "(":
                head_end = balanced(text, head_end, "(", ")")
                continue
            head_end += 1
        head = text[i:head_end]
        bases: list[str] = []
        if head.strip().startswith(":"):
            listed = re.split(r"\bwhere\b", head.strip()[1:], maxsplit=1)[0]
            bases = [re.sub(r"\(.*", "", base, flags=re.S).strip() for base in split_top(listed)]
        body = ""
        if head_end < len(text) and text[head_end] == "{":
            body = text[head_end + 1:balanced(text, head_end, "{", "}") - 1]
        kind = re.sub(r"\s+", " ", match.group("kind"))
        decl = TypeDecl(match.group("name"), kind, type_params, bases, body, rel,
                        text.count("\n", 0, match.start()) + 1, "abstract" in match.group("mods"))
        for parameter in split_top(positional):
            parameter = re.sub(r"^\s*(?:\[[^\]]*\]\s*)+", "", parameter).split("=")[0].strip()
            words = parameter.rsplit(None, 1)
            if len(words) == 2 and re.fullmatch(r"[A-Za-z_]\w*", words[1]):
                decl.properties[words[1]] = words[0]
                decl.bindable.add(words[1])
        for prop in PROPERTY.finditer(body):
            if re.search(r"\bstatic\b", prop.group("head")):
                continue
            decl.properties[prop.group("name")] = re.sub(r"\s+", "", prop.group("type"))
            brace = body.index("{", prop.end("name"))
            accessors = body[brace:balanced(body, brace, "{", "}")]
            if re.search(r"\b(?:set|init)\b", accessors):
                decl.bindable.add(prop.group("name"))
        found.append(decl)
    return found


class Source:
    """Every tracked C# file under src/, comments blanked, and the types they declare by simple name."""

    def __init__(self, files: dict[str, str]):
        self.files = {rel: strip_comments(text) for rel, text in files.items()}
        self.types: dict[str, list[TypeDecl]] = {}
        for rel, text in sorted(self.files.items()):
            for decl in declarations(rel, text):
                self.types.setdefault(decl.name, []).append(decl)
        self.constants: dict[str, str] = {}
        for decls in self.types.values():
            for decl in decls:
                for const in re.finditer(r"\b(?:const|static\s+readonly)\s+string\s+(\w+)\s*=\s*(\$?)\"([^\"\n]*)\"",
                                         decl.body):
                    self.constants[f"{decl.name}.{const.group(1)}"] = const.group(3)
                    self.constants.setdefault(const.group(1), const.group(3))

    def kind(self, name: str) -> str | None:
        decls = self.types.get(name)
        return decls[0].kind if decls else None

    def all_properties(self, name: str, seen: set[str] | None = None, bindable: bool = False) -> dict[str, str]:
        """
        A type's properties with those of every base and interface the source declares; with `bindable`,
        only those a JSON body can set (positional, `set` or `init`).
        """
        seen = set() if seen is None else seen
        if name in seen:
            return {}
        seen.add(name)
        props: dict[str, str] = {}
        for decl in self.types.get(name, []):
            for base in decl.bases:
                props.update(self.all_properties(simple_name(base), seen, bindable))
            props.update({key: value for key, value in decl.properties.items()
                          if not bindable or key in decl.bindable})
        return props

    def line(self, rel: str, index: int) -> int:
        return self.files[rel].count("\n", 0, index) + 1


# ------------------------------------------------------------------ 1. list items

VALUE_TYPES = {"bool", "byte", "sbyte", "char", "decimal", "double", "float", "int", "uint", "nint", "nuint",
               "long", "ulong", "short", "ushort", "Guid", "DateTime", "DateTimeOffset", "DateOnly", "TimeOnly",
               "TimeSpan", "Boolean", "Byte", "Int16", "Int32", "Int64", "UInt16", "UInt32", "UInt64", "Single",
               "Double", "Decimal", "Char"}
LIST = re.compile(r"^(?:[\w.]+\.)?(?:I?ReadOnlyList|IReadOnlyCollection|I?List|I?Collection|IEnumerable|HashSet|"
                  r"I?Set|IReadOnlySet|ImmutableArray|ImmutableList|IImmutableList)<(?P<item>.+)>\??$")
RULE_FOR_EACH = re.compile(r"RuleForEach\s*\(\s*(\w+)\s*=>\s*\1\.([\w.]+)\s*\)")


def list_item(type_text: str) -> str | None:
    """The item type of a list type, or None when it is not one."""
    type_text = type_text.strip()
    if type_text.rstrip("?").endswith("[]"):
        return type_text.rstrip("?")[:-2]
    match = LIST.match(type_text)
    return match.group("item").strip() if match else None


def is_reference(source: Source, type_text: str) -> bool:
    """Whether a null fits this item type silently: a reference type not declared nullable."""
    if type_text.endswith("?"):
        return False  # string? or long?: a null item is declared, so a choice
    name = simple_name(type_text)
    if name in VALUE_TYPES or source.kind(name) in ("enum", "struct", "record struct"):
        return False
    return True


def validated_type(source: Source, decl: TypeDecl, mapping: dict[str, str], seen: set[str]) -> tuple[str | None, list[TypeDecl]]:
    """(the type a validator validates, the validator and every base it derives from that the source holds)."""
    if decl.name in seen:
        return None, []
    seen.add(decl.name)
    for base in decl.bases:
        args = [mapping.get(arg, arg) for arg in generic_args(base)]
        name = simple_name(base)
        if name == "AbstractValidator" and args:
            return args[0], [decl]
        for parent in source.types.get(name, []):
            if parent.kind != "class":
                continue
            target, chain = validated_type(source, parent, dict(zip(parent.type_params, args)), seen)
            if target:
                return target, [decl, *chain]
    return None, []


def list_paths(source: Source, type_name: str, depth: int = DEPTH, seen: frozenset = frozenset()) -> list[tuple[str, str]]:
    """(path, item type) of each list property, the type's own and those of the types it holds."""
    if depth == 0 or type_name in seen:
        return []
    found = []
    for name, type_text in sorted(source.all_properties(type_name, bindable=True).items()):
        item = list_item(type_text)
        if item is not None:
            found.append((name, item))
        elif source.kind(simple_name(type_text)) in ("class", "record", "record class"):
            found += [(f"{name}.{path}", item_type) for path, item_type
                      in list_paths(source, simple_name(type_text), depth - 1, seen | {type_name})]
    return found


RULE_FOR = re.compile(r"RuleFor\s*\(\s*(\w+)\s*=>\s*\1\.([\w.]+)\s*\)")
SET_VALIDATOR = re.compile(r"\.SetValidator\s*\(\s*new\s+(\w+)")


def item_rules(source: Source, chain: list[TypeDecl], depth: int, seen: set[str]) -> dict[str, bool]:
    """
    {path: whether a null item is refused} from `RuleForEach(x => x.Path)` in a validator's chain — and,
    through `RuleFor(x => x.Part).SetValidator(new Child())`, from the child validator's own, under Part.
    """
    rules: dict[str, bool] = {}
    for link in chain:
        for match in RULE_FOR_EACH.finditer(link.body):
            end = statement_end(link.body, match.end())
            chained = re.split(r"\.(?:ChildRules|SetValidator)\s*\(", link.body[match.end():end])[0]
            refused = bool(re.search(r"\.Not(?:Null|Empty)\s*\(", chained))
            rules[match.group(2)] = rules.get(match.group(2)) or refused
        for match in RULE_FOR.finditer(link.body):
            child = SET_VALIDATOR.search(link.body[match.end():statement_end(link.body, match.end())])
            if not child or depth == 0 or child.group(1) in seen:
                continue
            for child_decl in source.types.get(child.group(1), []):
                _, child_chain = validated_type(source, child_decl, {}, set())
                for path, refused in item_rules(source, child_chain, depth - 1, seen | {child.group(1)}).items():
                    key = f"{match.group(2)}.{path}"
                    rules[key] = rules.get(key) or refused
    return rules


def list_item_problems(source: Source, allowed: dict[str, str], used: set[str]) -> list[str]:
    problems = []
    for decls in source.types.values():
        for decl in decls:
            if decl.kind != "class" or decl.abstract:
                continue
            target, chain = validated_type(source, decl, {}, set())
            if not target:
                continue
            target = simple_name(target)
            rules = item_rules(source, chain, DEPTH, {decl.name})
            for path, item in list_paths(source, target):
                if not is_reference(source, item):
                    continue
                key = f"{target}.{path}"
                if key in allowed:
                    used.add(key)
                    continue
                if rules.get(path):
                    continue
                problems.append(f"{decl.file}:{decl.line}: list-items — {decl.name} validates {key}, a list of "
                                f"{item}, without `RuleForEach(x => x.{path}).NotNull()`: a null item binds and answers "
                                f"500 (allow-list: [dotnet_checks] list_items_nullable, property = \"{key}\")")
    return problems


# ------------------------------------------------------------------ 2. options validated on start

ADD_OPTIONS = re.compile(r"\.AddOptions\s*<\s*([\w.]+)\s*>")
ANNOTATION = re.compile(r"\[\s*(?:Required|Range|MinLength|MaxLength|StringLength|RegularExpression|Url|EmailAddress|"
                        r"Length|AllowedValues|DeniedValues|Base64String|ValidateObjectMembers|ValidateEnumeratedItems)\b")


def options_problems(source: Source, allowed: dict[str, str], used: set[str]) -> list[str]:
    problems = []
    validated_elsewhere = {simple_name(m.group(1)) for text in source.files.values()
                           for m in re.finditer(r"IValidateOptions\s*<\s*([\w.]+)\s*>", text)}
    for rel, text in sorted(source.files.items()):
        for match in ADD_OPTIONS.finditer(text):
            options = simple_name(match.group(1))
            chain = text[match.end():statement_end(text, match.end())]
            if options in allowed:
                used.add(options)
                continue
            where = f"{rel}:{source.line(rel, match.start())}: options-validated — AddOptions<{options}>"
            hint = f"(allow-list: [dotnet_checks] options_unvalidated, type = \"{options}\")"
            if not re.search(r"\.ValidateOnStart\s*\(", chain):
                problems.append(f"{where} does not call .ValidateOnStart(): a bad setting is found on first use, "
                                f"days later, not at start {hint}")
                continue
            annotated = any(ANNOTATION.search(decl.body) for decl in source.types.get(options, []))
            unknown = options not in source.types
            if (re.search(r"\.Validate(?!OnStart|DataAnnotations)\w*\s*[<(]", chain) or options in validated_elsewhere
                    or (re.search(r"\.ValidateDataAnnotations\s*\(", chain) and (annotated or unknown))):
                continue
            problems.append(f"{where} calls .ValidateOnStart() with nothing to validate: add an IValidateOptions<{options}>, "
                            f"a .Validate(…), or annotations with .ValidateDataAnnotations() {hint}")
    return problems


# ------------------------------------------------------------------ 3. unique indexes

ENTITY_MARK = re.compile(r"IEntityTypeConfiguration\s*<\s*([\w.]+)\s*>|EntityTypeBuilder\s*<\s*([\w.]+)\s*>|"
                         r"\.Entity\s*<\s*([\w.]+)\s*>\s*\(")
HAS_INDEX = re.compile(r"\.HasIndex\s*\(")
# `.IsUnique()`, `.IsUnique(true)` or `.IsUnique(unique: true)`; an `[Index(…, IsUnique = true)]` attribute is not read.
UNIQUE = re.compile(r"\.IsUnique\s*\(\s*(?:(?:unique\s*:\s*)?true\s*)?\)")
OWNS = re.compile(r"\.Owns(?:One|Many)\s*(?:<[^>]*>)?\s*\(\s*\(?\s*\w+\s*\)?\s*=>\s*\w+\.(\w+)\s*,\s*\(?\s*(\w+)\s*\)?\s*=>")


def index_columns(arguments: str) -> list[str]:
    lambda_ = re.match(r"\s*\(?\s*(\w+)\s*\)?\s*=>\s*(.*)$", arguments, re.S)
    if lambda_:
        body = lambda_.group(2).strip()
        anonymous = re.match(r"new\s*\{(.*)\}\s*$", body, re.S)
        parts = split_top(anonymous.group(1)) if anonymous else [body]
        return [part.split("=")[-1].strip().split(".")[-1] for part in parts]
    return [re.sub(r"^nameof\((?:[\w.]+\.)?(\w+)\)$", r"\1", part).strip('"') for part in split_top(arguments)]


def resolve_filter(source: Source, expression: str) -> str:
    """A HasFilter argument as text, its string constants resolved where the source declares them."""
    def constant(match: re.Match) -> str:
        return source.constants.get(match.group(1), match.group(0))
    text = re.sub(r"\{([\w.]+)\}", constant, expression)
    return re.sub(r"(?<![\"\w])([A-Z]\w*(?:\.[A-Z]\w*)+)(?![\"\w])", constant, text)


def pascal(column: str) -> str:
    return "".join(part.capitalize() for part in column.split("_"))


def index_problems(source: Source, tenant_key: str, column: str, allowed: dict[str, str], used: set[str]) -> list[str]:
    problems = []
    soft_property = pascal(column)
    for rel, text in sorted(source.files.items()):
        if "/Migrations/" in rel:
            continue  # the snapshot restates the model; the configuration is where it is written
        marks = [(m.start(), simple_name(next(g for g in m.groups() if g))) for m in ENTITY_MARK.finditer(text)]
        for match in HAS_INDEX.finditer(text):
            arguments_end = balanced(text, match.end() - 1, "(", ")")
            chain = text[arguments_end:statement_end(text, arguments_end)]
            if not UNIQUE.search(chain):
                continue
            mark = next(((start, name) for start, name in reversed(marks) if start < match.start()), None)
            if mark is None or mark[1] not in source.types:
                # What it cannot read is a failure, never a pass: a generic base configuration's `T`, an entity
                # declared outside the source, or a HasIndex with no entity before it.
                named = f"the entity {mark[1]}" if mark else "no entity before it"
                problems.append(f"{rel}:{source.line(rel, match.start())}: unique-index — could not read {named}, so "
                                "its tenant key and soft-delete filter are unknown; configure the index on the "
                                "entity's own configuration")
                continue
            entity = mark[1]
            # An owned type's index (`OwnsOne(u => u.Email, email => email.HasIndex(…))`) sits on its owner's
            # table: named Owner.Navigation, judged by the owner's columns.
            receiver = re.search(r"(\w+)\s*$", text[:match.start()])
            owned = [m.group(1) for m in OWNS.finditer(text, mark[0], match.start())
                     if receiver and m.group(2) == receiver.group(1)]
            columns = index_columns(text[match.end():arguments_end - 1])
            key = f"{entity}{'.' + owned[-1] if owned else ''}({', '.join(columns)})"
            if key in allowed:
                used.add(key)
                continue
            where = f"{rel}:{source.line(rel, match.start())}: unique-index — {key}"
            hint = f"(allow-list: [dotnet_checks] unique_indexes, index = \"{key}\")"
            props = source.all_properties(entity)
            if tenant_key and tenant_key in props and columns[:1] != [tenant_key]:
                problems.append(f"{where} is on a tenant-scoped entity but does not lead with {tenant_key}: one "
                                f"tenant's row blocks another's {hint}")
            if soft_property in props:
                filter_ = re.search(r"\.HasFilter\s*\(", chain)
                resolved = ""
                if filter_:
                    start = filter_.end() - 1
                    resolved = resolve_filter(source, chain[start + 1:balanced(chain, start, "(", ")") - 1])
                if not re.search(rf"[\\\"]*\b{re.escape(column)}\b[\\\"]*\s+IS\s+NULL", resolved, re.I):
                    problems.append(f"{where} is on an entity mapping {column} but does not filter "
                                    f"`{column} IS NULL`{' (its filter: ' + resolved.strip() + ')' if resolved else ''}: "
                                    f"a deleted row blocks a live one {hint}")
    return problems


# ------------------------------------------------------------------ settings and run

def settings(root: Path) -> dict:
    path = root / FACTORY_TOML
    if not path.is_file():
        raise CouldNotCheck(f"no {FACTORY_TOML}")
    try:
        config = book_config.load(root)
    except book_config.ConfigError as error:
        raise CouldNotCheck(str(error)) from error
    return config.get("dotnet_checks") or {}


def allow_list(section: dict, key: str, field_: str) -> dict[str, str]:
    found = {}
    for entry in section.get(key, []) or []:
        name = entry.get(field_) if isinstance(entry, dict) else None
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise CouldNotCheck(f"[dotnet_checks] {key}: an entry has no {field_} ({entry!r})")
        if not isinstance(reason, str) or not reason.strip():
            raise CouldNotCheck(f"[dotnet_checks] {key}: {name} gives no reason — every exception says why")
        found[name.strip()] = reason.strip()
    return found


def tracked_sources(root: Path) -> dict[str, str]:
    done = subprocess.run(["git", "-c", "core.quotePath=false", "ls-files", "-z"], cwd=root, capture_output=True)
    if done.returncode != 0:
        raise CouldNotCheck(f"`git ls-files` failed ({done.returncode})")
    files = {}
    for rel in done.stdout.decode("utf-8", errors="replace").split("\0"):
        if SOURCES.match(rel) and (root / rel).is_file():
            files[rel] = (root / rel).read_text(encoding="utf-8", errors="replace")
    return files


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        section = settings(root)
        lists = {"list_items_nullable": allow_list(section, "list_items_nullable", "property"),
                 "options_unvalidated": allow_list(section, "options_unvalidated", "type"),
                 "unique_indexes": allow_list(section, "unique_indexes", "index")}
        tenant_key = str(section.get("tenant_key") or "").strip()
        column = str(section.get("soft_delete_column") or "deleted_at").strip()
        source = Source(tracked_sources(root))
    except CouldNotCheck as error:
        print(f"{NAME}: could not check — {error}", file=sys.stderr)
        return 1
    used: dict[str, set[str]] = {key: set() for key in lists}
    problems = (list_item_problems(source, lists["list_items_nullable"], used["list_items_nullable"])
                + options_problems(source, lists["options_unvalidated"], used["options_unvalidated"])
                + index_problems(source, tenant_key, column, lists["unique_indexes"], used["unique_indexes"]))
    for key, entries in lists.items():
        problems += [f"{FACTORY_TOML}: [dotnet_checks] {key} lists {name}, which matches nothing now — remove the "
                     "stale entry" for name in sorted(set(entries) - used[key])]
    if not tenant_key:
        print(f"{NAME}: [dotnet_checks] tenant_key is empty, so no unique index is judged for its tenant key.")
    if problems:
        print(f"{NAME}: {len(problems)} problem(s) in {len(source.files)} C# file(s):", file=sys.stderr)
        print("\n".join(f"  {problem}" for problem in problems), file=sys.stderr)
        return 1
    print(f"{NAME}: list items, options and unique indexes hold in {len(source.files)} C# file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
