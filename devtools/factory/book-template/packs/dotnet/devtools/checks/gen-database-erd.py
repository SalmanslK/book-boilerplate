#!/usr/bin/env python3
"""
Generate docs/database-erd.md from the EF Core model snapshot.

Statically parses `src/Boilerplate.Persistence/Migrations/ApplicationDbContextModelSnapshot.cs`
— the file EF rewrites on every `migrations add`, so it is always the shape the
next migration will produce — and emits one Markdown file of Mermaid `erDiagram`
blocks: an overview of how the modules relate, then one diagram per module with
every column, key and foreign key.

Why the snapshot and not the entity classes: the snapshot already has the
resolved relational model — table names, column names, PostgreSQL types,
nullability, owned-type flattening and the real foreign keys. The entity classes
have none of that; half of it lives in the `IEntityTypeConfiguration`s and the
naming convention. Parsing the snapshot cannot disagree with the database,
because the migrations are generated from the same model.

Owned types are folded the way EF maps them: an owned type that shares its
owner's table (`Address`, `Email`, `PhoneNumber`, `WebsiteUrl`) contributes its
columns to the owner, and an owned collection with its own table
becomes a table of its own with an identifying relationship back to the owner.

The universal edges — `tenant_id` and the three audit actors — are drawn once,
in their own section, and suppressed everywhere else. Every tenant-scoped table
points at `tenants` and every audited table points three times at `users`; drawn
in place they turn each diagram into a star around two nodes and hide the
business relationships the diagram exists to show. Which table carries which is
in the scoping table instead, so nothing is lost.

Usage (from the repository root):
    python3 devtools/checks/gen-database-erd.py            # writes docs/database-erd.md
    python3 devtools/checks/gen-database-erd.py --check    # exit 1 if the doc is stale

Runs in CI and in .githooks/pre-commit in --check mode, beside
gen-api-contracts.py. Re-run after any `dotnet ef migrations add`, and commit
the result with the migration.
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
# The settings: factory.toml merged over the Book's defaults (devtools/factory/book_config.py).
_FACTORY = next((p / "devtools" / "factory" for p in Path(__file__).resolve().parents
                 if (p / "devtools" / "factory" / "book_config.py").is_file()), None)
if _FACTORY is not None:
    sys.path.insert(0, str(_FACTORY))
import book_config  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "src" / "Boilerplate.Persistence" / "Migrations" / "ApplicationDbContextModelSnapshot.cs"
OUT = ROOT / "docs" / "database-erd.md"

# Drawn once in "Tenancy, scoping and auditing" and suppressed in every other
# diagram. Keyed by the CLR property the foreign key is declared on.
UNIVERSAL_FK_PROPERTIES = {"TenantId", "CreatedBy", "ModifiedBy", "DeletedBy"}

# Mermaid's attribute type accepts a bare identifier. `character varying(20)`
# has a space and brackets and breaks the parse, so the length moves into the
# comment column and the type is normalised to something renderable.
TYPE_ALIASES = {
    "character varying": "varchar",
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
    "time without time zone": "time",
    "double precision": "double",
}

MODULE_TITLE_OVERRIDES = {
    "ReferenceLists": "Reference lists",
}


# --------------------------------------------------------------------------- #
# C# text helpers
# --------------------------------------------------------------------------- #

def match_brace(text: str, open_idx: int) -> int:
    """Index just past the `}` matching the `{` at open_idx. Snapshot strings hold
    no braces, so a depth count is enough."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == '"':
            j = i + 1
            while j < len(text) and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise SystemExit(f"unbalanced braces in {SNAPSHOT.name} at offset {open_idx}")


def statements(body: str) -> list[str]:
    """Split a builder lambda body into whole `;`-terminated statements, keeping any
    nested lambda block inside the statement it belongs to."""
    out, start, i, n = [], 0, 0, len(body)
    while i < n:
        c = body[i]
        if c == '"':
            i += 1
            while i < n and body[i] != '"':
                i += 2 if body[i] == "\\" else 1
            i += 1
        elif c == "{":
            i = match_brace(body, i)
        elif c == ";":
            stmt = body[start:i].strip()
            if stmt:
                out.append(stmt)
            i += 1
            start = i
        else:
            i += 1
    return out


def quoted_args(stmt: str, call: str) -> list[str]:
    """Every string literal in the argument list of `call(` within stmt."""
    idx = stmt.find(call + "(")
    if idx == -1:
        return []
    depth, i, n = 0, idx + len(call), len(stmt)
    args: list[str] = []
    while i < n:
        c = stmt[i]
        if c == '"':
            j = i + 1
            while j < n and stmt[j] != '"':
                j += 2 if stmt[j] == "\\" else 1
            args.append(stmt[i + 1:j])
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return args


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

@dataclass
class Column:
    name: str
    sql_type: str
    nullable: bool
    max_length: int | None = None
    identity: bool = False
    default: str | None = None
    concurrency: bool = False
    owned_prefix: str | None = None  # navigation name, for an owned type in this table


@dataclass
class Index:
    columns: list[str]
    unique: bool
    filter: str | None


@dataclass
class ForeignKey:
    principal_entity: str
    columns: list[str]
    props: list[str]
    required: bool
    delete_behavior: str
    owner: bool = False  # an owned collection's key back to its owner


@dataclass
class Table:
    name: str
    entity: str
    module: str
    is_view: bool = False
    columns: "OrderedDict[str, Column]" = field(default_factory=OrderedDict)
    key: list[str] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)
    fks: list[ForeignKey] = field(default_factory=list)
    checks: list[tuple[str, str]] = field(default_factory=list)
    owner_table: str | None = None

    @property
    def fk_columns(self) -> set[str]:
        return {c for fk in self.fks for c in fk.columns}


class Scope:
    """One builder lambda: the entity it configures, and the property→column map
    that its own `HasKey`/`HasIndex`/`HasForeignKey` names resolve against."""

    def __init__(self, table: Table, props: dict[str, str], prefix: str | None = None):
        self.table = table
        self.prefix = prefix
        # EF writes each entity twice; the property→column map is built in the
        # first pass and every `HasForeignKey("OwnerId")` in the second resolves
        # against it, so it belongs to the entity rather than to one pass.
        self.props = props


# --------------------------------------------------------------------------- #
# Snapshot parser
# --------------------------------------------------------------------------- #

ENTITY_RE = re.compile(r'modelBuilder\.Entity\("([^"]+)", b =>')
# The CLR type is matched lazily up to the `>("` that closes it, so a nested
# generic such as `Dictionary<string, string>` is read whole rather than cut at
# its first `>`.
IDENTITY_RE = re.compile(r'UseIdentityByDefaultColumn\(\w+\.Property<.+?>\("([^"]+)"\)\)')
PROPERTY_RE = re.compile(r'^\w+\.(?:Property|PrimitiveCollection)<(.+?)>\("([^"]+)"\)')
# Every statement that declares a property, whatever form it takes — the generic
# `Property<T>("X")`, `Property(typeof(T), "X")`, and the complex-type and
# primitive-collection builders. Counted in the raw snapshot for verify(), so it
# deliberately matches more than PROPERTY_RE: the difference is what the parser
# failed to read. The identity annotation
# (`NpgsqlPropertyBuilderExtensions.UseIdentityByDefaultColumn(b.Property<long>("Id"))`)
# does not start with a builder call, so it is excluded here as it is skipped in
# read(): it only marks a column that its own `Property` statement declares.
PROPERTY_DECLARATION_RE = re.compile(
    r'^\s*\w+\.(?:Property|ComplexProperty|ComplexCollection|PrimitiveCollection)\s*[<(]', re.M)
OWNS_RE = re.compile(r'^\w+\.(OwnsOne|OwnsMany)\("([^"]+)", "([^"]+)", (\w+) =>')
HASONE_RE = re.compile(r'^\w+\.HasOne\("([^"]+)", (?:null|"([^"]+)")\)')


class SnapshotParser:
    def __init__(self, text: str):
        self.text = text
        self.tables: "OrderedDict[str, Table]" = OrderedDict()
        self.entity_table: dict[str, str] = {}
        self.entity_props: dict[str, dict[str, str]] = defaultdict(dict)
        # Entities mapped with ToView over an existing table — AuditActor reads
        # `users`. They are a query-side projection, not a database object, so
        # they are listed in prose rather than drawn as a table of their own.
        self.views: "OrderedDict[str, str]" = OrderedDict()
        # Owned types flattened into their owner's table, counted for verify().
        self.in_parent_owned = 0
        # Property statements read into a column, and the ones that looked like a
        # property declaration but matched no reader — both counted for verify().
        self.properties_read = 0
        self.unread_properties: list[str] = []
        # Deferred because an owned type's FK can name a principal whose own
        # block has not been read yet; resolved once every table exists.
        self.pending: list[tuple[Scope, str, list[str], bool, str, bool]] = []

    # -- entry point ------------------------------------------------------- #

    def parse(self) -> "OrderedDict[str, Table]":
        for entity, body in self.entity_blocks():
            table = self.tables.get(self.entity_table.get(entity, ""))
            if table is None:
                table = Table(name="", entity=entity, module=module_of(entity))
            scope = Scope(table, self.entity_props[entity])
            self.read(scope, body)
            if table.is_view:
                self.views[entity] = table.name
                continue
            if not table.name:
                raise SystemExit(f"{entity} maps to no table or view; the generator needs teaching the new shape")
            self.entity_table[entity] = table.name
            self.tables.setdefault(table.name, table)
        self.resolve_pending()
        self.verify()
        return self.tables

    def verify(self) -> None:
        """A shape the parser cannot read would otherwise vanish from the diagrams
        without a trace, and --check would pass on the shorter file. The tables,
        the property declarations and the foreign keys are all countable in the
        snapshot text, so count them and refuse to disagree — and every foreign key
        must land on a table that is drawn, or its edge would be silently dropped."""
        named = {m.group(1) for m in re.finditer(r'ToTable\("([a-z_0-9]+)"', self.text)}
        drawn = set(self.tables) | set(self.views.values())
        if missing := named - drawn:
            raise SystemExit(f"{len(missing)} table(s) named in the snapshot but not drawn: "
                             f"{', '.join(sorted(missing))}; the generator needs teaching the new shape")
        # Every foreign key in the model is declared with exactly one
        # `HasForeignKey`, whether it hangs off a `HasOne` or a `WithOwner`. Counting
        # that rather than `HasOne` keeps the guard independent of the call the
        # parser matches on — a renamed `HasOne` would otherwise drop a relationship
        # from both sides of the comparison and pass. The only ones with no foreign
        # key of their own are owned types sharing their owner's table: that key
        # *is* the owner's primary key.
        expected = self.text.count("HasForeignKey(") - self.in_parent_owned
        actual = sum(len(t.fks) for t in self.tables.values())
        if actual != expected:
            raise SystemExit(f"{expected} foreign keys in the snapshot but {actual} parsed; "
                             f"the generator needs teaching the new shape")
        # A property statement PROPERTY_RE does not match would fall through read()
        # and drop its column. Count every declaration in the raw text — including
        # the ones nested inside a builder the parser does not descend into — and
        # compare with what was read.
        declared = len(PROPERTY_DECLARATION_RE.findall(self.text))
        if self.unread_properties or declared != self.properties_read:
            unread = "".join(f"\n    {stmt}" for stmt in self.unread_properties) or " (none identified)"
            raise SystemExit(f"{declared} property declarations in the snapshot but {self.properties_read} "
                             f"read; unread statements:{unread}\nthe generator needs teaching the new shape")
        # Views are never a principal (a keyless projection cannot be referenced),
        # an owned type's key back to its owner resolves to the owner's table, and a
        # self-reference resolves to the table itself — so every legitimate foreign
        # key has a drawn principal, and one without is a shape the parser missed.
        for table in self.tables.values():
            for fk in table.fks:
                if principal_table(self.tables, fk) is None:
                    raise SystemExit(f"foreign key {table.name}({', '.join(fk.columns)}) references "
                                     f"{fk.principal_entity or '(no principal)'}, which maps to no drawn table; "
                                     f"the generator needs teaching the new shape")

    def entity_blocks(self):
        """EF writes each entity twice — properties first, then relationships and
        owned types — so both passes are yielded and merged onto one table."""
        for m in ENTITY_RE.finditer(self.text):
            open_idx = self.text.index("{", m.end())
            yield m.group(1), self.text[open_idx + 1:match_brace(self.text, open_idx) - 1]

    # -- one builder lambda ------------------------------------------------ #

    def read(self, scope: Scope, body: str) -> None:
        identity = set(IDENTITY_RE.findall(body))
        for stmt in statements(body):
            flat = " ".join(stmt.split())
            if m := PROPERTY_RE.match(flat):
                self.properties_read += 1
                self.read_property(scope, stmt, m, identity)
            elif PROPERTY_DECLARATION_RE.match(flat):
                self.unread_properties.append(flat[:160])
            elif m := OWNS_RE.match(flat):
                self.read_owned(scope, stmt, m)
            elif flat.startswith(("b.HasKey(", "b1.HasKey(")):
                scope.table.key = [scope.props.get(p, p) for p in quoted_args(flat, "HasKey")]
            elif ".HasIndex(" in flat:
                self.read_index(scope, flat)
            elif ".ToTable(" in flat:
                self.read_to_table(scope, stmt, flat)
            elif ".ToView(" in flat:
                scope.table.name = quoted_args(flat, "ToView")[0]
                scope.table.is_view = True
            elif m := HASONE_RE.match(flat):
                self.read_has_one(scope, flat, m)
            elif ".WithOwner(" in flat and ".HasForeignKey(" in flat:
                self.read_with_owner(scope, flat)

    def read_property(self, scope: Scope, stmt: str, m: re.Match, identity: set[str]) -> None:
        clr, prop = m.group(1), m.group(2)
        flat = " ".join(stmt.split())
        column = (quoted_args(flat, "HasColumnName") or [snake(prop)])[0]
        sql = (quoted_args(flat, "HasColumnType") or ["?"])[0]
        length = re.search(r"HasMaxLength\((\d+)\)", flat)
        default = re.search(r"HasDefaultValue\(([^)]*)\)", flat)
        # A snapshot property is nullable unless its CLR type says otherwise or
        # `.IsRequired()` does; a reference type carries neither, so `IsRequired`
        # is the only signal for a string.
        nullable = clr.endswith("?") or (clr in ("string", "byte[]") and ".IsRequired()" not in flat)
        scope.props[prop] = column
        scope.table.columns[column] = Column(
            name=column,
            sql_type=sql,
            nullable=nullable,
            max_length=int(length.group(1)) if length else None,
            identity=prop in identity,
            default=default.group(1) if default else None,
            concurrency=".IsConcurrencyToken()" in flat,
            owned_prefix=scope.prefix,
        )

    def read_owned(self, scope: Scope, stmt: str, m: re.Match) -> None:
        kind, owned_entity, navigation = m.group(1), m.group(2), m.group(3)
        open_idx = stmt.index("{")
        inner = stmt[open_idx + 1:match_brace(stmt, open_idx) - 1]
        probe = Table(name="", entity=owned_entity, module=scope.table.module)
        probe_scope = Scope(probe, {}, prefix=navigation)
        self.read(probe_scope, inner)
        shares_owner_table = probe.name == scope.table.name
        if not shares_owner_table:
            for col in probe.columns.values():
                col.owned_prefix = None
        if shares_owner_table:
            self.in_parent_owned += 1
            # Shares the owner's table: fold its columns in, drop the key column
            # it shares with the owner, and hand its foreign keys to the owner.
            for name, col in probe.columns.items():
                if name not in scope.table.columns:
                    scope.table.columns[name] = col
            scope.table.indexes.extend(probe.indexes)
            probe_scope.table = scope.table
            return
        # Its own table: a table of its own, identified by the owner's key.
        probe.owner_table = scope.table.name
        probe.module = scope.table.module
        self.tables.setdefault(probe.name, probe)
        if kind == "OwnsMany":
            probe.entity = owned_entity

    def read_index(self, scope: Scope, flat: str) -> None:
        props = quoted_args(flat, "HasIndex")
        filt = quoted_args(flat, "HasFilter")
        scope.table.indexes.append(Index(
            columns=[scope.props.get(p, p) for p in props],
            unique=".IsUnique()" in flat,
            filter=filt[0] if filt else None,
        ))

    def read_to_table(self, scope: Scope, stmt: str, flat: str) -> None:
        args = quoted_args(flat, "ToTable")
        if args:
            scope.table.name = args[0]
        for check in re.finditer(r'HasCheckConstraint\("([^"]+)", "([^"]+)"\)', stmt):
            scope.table.checks.append((check.group(1), check.group(2)))

    def read_has_one(self, scope: Scope, flat: str, m: re.Match) -> None:
        props = quoted_args(flat, "HasForeignKey")
        behavior = re.search(r"OnDelete\(DeleteBehavior\.(\w+)\)", flat)
        self.pending.append((
            scope, m.group(1), props, ".IsRequired()" in flat,
            behavior.group(1) if behavior else "NoAction", False,
        ))

    def read_with_owner(self, scope: Scope, flat: str) -> None:
        if scope.table.owner_table is None and scope.prefix is None:
            return
        props = quoted_args(flat, "HasForeignKey")
        self.pending.append((scope, "", props, True, "Cascade", True))

    # -- second pass ------------------------------------------------------- #

    def resolve_pending(self) -> None:
        for scope, principal, props, required, behavior, owner in self.pending:
            table = scope.table
            if owner:
                if table.owner_table is None:
                    continue
                principal = next(e for e, t in self.entity_table.items() if t == table.owner_table)
            columns = [scope.props.get(p, p) for p in props]
            table.fks.append(ForeignKey(
                principal_entity=principal,
                columns=columns,
                props=props,
                required=required,
                delete_behavior=behavior,
                owner=owner,
            ))


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def module_of(entity: str) -> str:
    if entity.startswith("Boilerplate.Domain.Features."):
        return entity[len("Boilerplate.Domain.Features."):].split(".")[0]
    if entity.startswith("Boilerplate.Persistence."):
        return entity[len("Boilerplate.Persistence."):].split(".")[0]
    return entity.rsplit(".", 2)[-2]


def module_title(module: str) -> str:
    titles = {**MODULE_TITLE_OVERRIDES, **ERD_SETTINGS.get("titles", {})}
    if module in titles:
        return titles[module]
    words = re.findall(r"[A-Z][a-z0-9]*", module) or [module]
    return " ".join([words[0]] + [w.lower() for w in words[1:]])


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def mermaid_type(col: Column) -> str:
    sql = col.sql_type
    base = re.sub(r"\(.*\)$", "", sql).strip()
    base = TYPE_ALIASES.get(base, base)
    return re.sub(r"[^A-Za-z0-9_]", "_", base) or "unknown"


def column_note(table: Table, col: Column) -> str:
    bits = []
    if col.owned_prefix:
        bits.append(col.owned_prefix)
    if col.max_length:
        bits.append(f"max {col.max_length}")
    # A key column is not null whatever the CLR type says: EF makes it required
    # when it becomes part of the key, and the snapshot records only the type.
    if col.nullable and col.name not in table.key:
        bits.append("nullable")
    if col.identity:
        bits.append("identity")
    if col.default is not None:
        bits.append(f"default {col.default}")
    if col.concurrency:
        bits.append("concurrency token")
    return ", ".join(bits)


def key_marker(table: Table, col: Column) -> str:
    if col.name in table.key:
        return "PK"
    if col.name in table.fk_columns:
        return "FK"
    if any(i.unique and i.columns == [col.name] for i in table.indexes):
        return "UK"
    return ""


def cardinality(fk: ForeignKey) -> str:
    """principal-side ‖ dependent-side. A required foreign key is exactly one
    principal row; a nullable one is zero or one."""
    left = "||" if fk.required else "|o"
    right = "|{" if fk.owner else "o{"
    return f"{left}--{right}"


def edge_label(fk: ForeignKey) -> str:
    label = ", ".join(fk.columns) if fk.columns else "owns"
    # Restrict is the default on all but a handful of these, so naming it on
    # every edge would say nothing. Naming the exceptions is what carries
    # information: a cascade is a row that disappears with its parent.
    if fk.delete_behavior != "Restrict":
        label += f" ({fk.delete_behavior.lower()})"
    return label


def business_fks(table: Table) -> list[ForeignKey]:
    return [fk for fk in table.fks if not set(fk.props) & UNIVERSAL_FK_PROPERTIES]


def render_attributes(table: Table) -> list[str]:
    lines = [f"    {table.name} {{"]
    for col in table.columns.values():
        note = column_note(table, col)
        marker = key_marker(table, col)
        row = f"        {mermaid_type(col)} {col.name}"
        if marker:
            row += f" {marker}"
        if note:
            row += f' "{note}"'
        lines.append(row)
    lines.append("    }")
    return lines


def render_overview(tables: "OrderedDict[str, Table]") -> str:
    lines = ["```mermaid", "erDiagram"]
    for table in tables.values():
        lines.append(f"    {table.name} {{")
        lines.append("    }")
    for table in tables.values():
        for fk in business_fks(table):
            principal = principal_table(tables, fk)
            if principal:
                lines.append(f"    {principal} {cardinality(fk)} {table.name} : \"{edge_label(fk)}\"")
    lines.append("```")
    return "\n".join(lines)


def principal_table(tables: "OrderedDict[str, Table]", fk: ForeignKey) -> str | None:
    for name, table in tables.items():
        if table.entity == fk.principal_entity:
            return name
    return None


def render_module(tables: "OrderedDict[str, Table]", module_tables: list[Table]) -> str:
    own = {t.name for t in module_tables}
    lines = ["```mermaid", "erDiagram"]
    for table in module_tables:
        lines.extend(render_attributes(table))
    # Tables outside the module that this one references are drawn as empty
    # boxes, so the edge has somewhere to land without repeating their columns.
    stubs: list[str] = []
    for table in module_tables:
        for fk in business_fks(table):
            principal = principal_table(tables, fk)
            if principal and principal not in own and principal not in stubs:
                stubs.append(principal)
    for stub in stubs:
        lines.append(f"    {stub} {{")
        lines.append("    }")
    for table in module_tables:
        for fk in business_fks(table):
            principal = principal_table(tables, fk)
            if principal:
                lines.append(f"    {principal} {cardinality(fk)} {table.name} : \"{edge_label(fk)}\"")
    lines.append("```")
    return "\n".join(lines)


def render_universal() -> str:
    """One representative diagram for the edges every other diagram suppresses."""
    lines = ["```mermaid", "erDiagram", "    tenants {", "    }", "    users {", "    }",
             "    any_tenant_scoped_table {", "        bigint tenant_id FK",
             '        bigint created_by FK "nullable"', '        bigint modified_by FK "nullable"',
             '        bigint deleted_by FK "nullable"', "    }",
             '    tenants ||--o{ any_tenant_scoped_table : "tenant_id"',
             '    users |o--o{ any_tenant_scoped_table : "created_by"',
             '    users |o--o{ any_tenant_scoped_table : "modified_by"',
             '    users |o--o{ any_tenant_scoped_table : "deleted_by"', "```"]
    return "\n".join(lines)


def scoping_column(root: Path = ROOT) -> str:
    """The column a user's data is scoped by inside the tenant: `<[book] scoping_word>_id` from
    devtools/factory/factory.toml (`store` gives `store_id`), or "" when the project has none."""
    try:
        word = str((book_config.load(root).get("book") or {}).get("scoping_word") or "")
    except (OSError, ValueError):
        word = ""
    return f"{word.strip().lower().replace(' ', '_')}_id" if word.strip() else ""


SCOPING_COLUMN = scoping_column()


def erd_settings(root: Path = ROOT) -> dict:
    """`[database_erd]`: `titles` (a module's heading where splitting its name is wrong) and `notes` (lines the
    project adds under *Tenancy, scoping and auditing*, a rule its schema holds everywhere)."""
    try:
        table = book_config.load(root).get("database_erd") or {}
    except (OSError, ValueError):
        return {}
    titles = table.get("titles") or {}
    notes = table.get("notes") or []
    if not isinstance(titles, dict) or not isinstance(notes, list):
        raise SystemExit("gen-database-erd: [database_erd] titles is a table and notes a list of lines")
    return {"titles": {str(k): str(v) for k, v in titles.items()}, "notes": [str(n) for n in notes]}


ERD_SETTINGS = erd_settings()


def scoping_words() -> str:
    """`tenancy, lab scoping` with `[book] scoping_word = "lab"`; `tenancy, scoping` without one."""
    word = SCOPING_COLUMN[:-len("_id")].replace("_", " ") if SCOPING_COLUMN else ""
    return f"tenancy, {word} scoping" if word else "tenancy, scoping"


def scoping_rows(tables: "OrderedDict[str, Table]") -> list[str]:
    rows = []
    for table in sorted(tables.values(), key=lambda t: t.name):
        cols = table.columns
        flags = [
            "yes" if "tenant_id" in cols else "—",
            *(["yes" if SCOPING_COLUMN in cols else "—"] if SCOPING_COLUMN else []),
            "yes" if "deleted_at" in cols else "—",
            "yes" if "created_by" in cols else "—",
        ]
        kind = "view" if table.is_view else ("owned" if table.owner_table else "table")
        rows.append(f"| `{table.name}` | {kind} | {' | '.join(flags)} |")
    return rows


def render(tables: "OrderedDict[str, Table]", views: "OrderedDict[str, str]") -> str:
    by_module: dict[str, list[Table]] = defaultdict(list)
    for table in tables.values():
        by_module[table.module].append(table)
    modules = sorted(by_module, key=module_title)

    fk_total = sum(len(t.fks) for t in tables.values())
    column_total = sum(len(t.columns) for t in tables.values())

    out: list[str] = []
    w = out.append

    w("# Database ERD — every table in the schema")
    w("")
    w("> **Generated file — do not hand-edit.** Regenerate with")
    w("> `python3 devtools/checks/gen-database-erd.py` from the repository root after any")
    w("> `dotnet ef migrations add`, and commit the result in the same change. CI and")
    w("> `.githooks/pre-commit` run it with `--check` and fail when this file no longer")
    w("> matches the model.")
    w(">")
    w("> The source is `src/Boilerplate.Persistence/Migrations/ApplicationDbContextModelSnapshot.cs`,")
    w("> which EF rewrites on every migration — so this is the schema the next migration")
    w("> will produce, not a drawing of it. Why the schema is shaped this way belongs to")
    w(f"> the feature plans in [`plans/`](plans/); the rules it follows — {scoping_words()},")
    w("> soft delete, audit actors — are in")
    w("> [`architecture.md`](architecture.md) and [`../CLAUDE.md`](../CLAUDE.md), and are")
    w("> not restated here.")
    w("")
    w(f"**{len(tables)} tables** · **{column_total} columns** · "
      f"**{fk_total} foreign keys** · **{len(modules)} modules**.")
    w("")
    if views:
        w("Not drawn: " + "; ".join(
            f"`{entity.rsplit('.', 1)[-1]}`, a keyless read-only projection over `{table}`"
            for entity, table in views.items()
        ) + (". Each is" if len(views) > 1 else ". It is")
          + " a mapping, not a database object — there is no second table.")
        w("")

    w("## How to read these diagrams")
    w("")
    w("- Boxes are **tables**, named as PostgreSQL has them. An empty box in a module")
    w("  diagram is a table that lives in another module, drawn so its edge has")
    w("  somewhere to land.")
    w("- `PK` is the primary key, `FK` a foreign key, `UK` a single-column unique index.")
    w("  Composite unique indexes are listed under each module's diagram, not marked on")
    w("  the column.")
    w("- The quoted note carries the length limit, nullability, identity and default, and")
    w("  for a value object the value object it was flattened from — `email` is `Email`.")
    w("- `||--o{` is a required foreign key (exactly one parent row), `|o--o{` a nullable")
    w("  one (zero or one). `||--|{` is an owned collection, which cannot exist without")
    w("  its owner.")
    w("- An edge is labelled with its foreign key column. **`(cascade)` means the child row")
    w("  is deleted with its parent** — everything else is `Restrict` and is not annotated,")
    w("  so the annotated edges are the ones worth knowing about.")
    w("- **`tenant_id`, `created_by`, `modified_by` and `deleted_by` edges are suppressed**")
    w("  in every diagram below and drawn once in the next section. They are on most")
    w("  tables; drawn in place they bury every business relationship under a star around")
    w("  `tenants` and `users`. The columns are still shown.")
    w("")

    w("## Tenancy, scoping and auditing")
    w("")
    w("Every tenant-scoped table carries these four, and they are wired identically")
    w("everywhere — a `Restrict` foreign key with no navigation property, per the audit")
    w("rules in [`../CLAUDE.md`](../CLAUDE.md)." + (" " + " ".join(ERD_SETTINGS["notes"]) if ERD_SETTINGS.get("notes") else ""))
    w("")
    w(render_universal())
    w("")
    w("Which table carries what:")
    w("")
    scoped = f" `{SCOPING_COLUMN}` |" if SCOPING_COLUMN else ""
    w(f"| Table | Kind | `tenant_id` |{scoped} `deleted_at` | `created_by` |")
    w("|---|---|---|" + ("---|" if SCOPING_COLUMN else "") + "---|---|")
    out.extend(scoping_rows(tables))
    w("")

    w("## Overview — how the modules relate")
    w("")
    w("Every table and every business foreign key, without the columns. Use it to find")
    w("the module you want, then read that module's diagram below.")
    w("")
    w(render_overview(tables))
    w("")

    for module in modules:
        module_tables = sorted(by_module[module], key=lambda t: t.name)
        w(f"## {module_title(module)}")
        w("")
        w(render_module(tables, module_tables))
        w("")
        notes = module_notes(module_tables)
        if notes:
            out.extend(notes)
            w("")

    return "\n".join(out).rstrip("\n") + "\n"


def module_notes(module_tables: list[Table]) -> list[str]:
    lines: list[str] = []
    for table in module_tables:
        composite = [i for i in table.indexes if i.unique and len(i.columns) > 1]
        filtered = [i for i in table.indexes if i.unique and len(i.columns) == 1 and i.filter]
        rows: list[str] = []
        for index in composite + filtered:
            cols = ", ".join(f"`{c}`" for c in index.columns)
            suffix = f" where `{index.filter}`" if index.filter else ""
            rows.append(f"  - unique on {cols}{suffix}")
        for name, expression in table.checks:
            rows.append(f"  - check `{name}`: `{expression}`")
        if table.is_view:
            rows.append("  - keyless view, not a table — read-only projection")
        if rows:
            lines.append(f"- **`{table.name}`**")
            lines.extend(rows)
    return lines


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="fail if docs/database-erd.md is out of date")
    args = ap.parse_args()

    persistence = SNAPSHOT.parents[1]
    if not SNAPSHOT.exists() and not any(p for p in persistence.rglob("*.cs") if not {"obj", "bin"} & set(p.parts)):
        # A greenfield Book, before its first migration: nothing to draw yet, so its first commit passes (Book D59).
        print("database-erd.md: N/A, no model snapshot yet (no C# under "
              f"{persistence.relative_to(ROOT).as_posix()}/)")
        return 0
    if not SNAPSHOT.exists():
        raise SystemExit(f"model snapshot not found at {SNAPSHOT.relative_to(ROOT).as_posix()}")

    parser = SnapshotParser(SNAPSHOT.read_text(encoding="utf-8-sig"))
    tables = parser.parse()
    if not tables:
        raise SystemExit("no tables parsed from the model snapshot; the generator needs teaching the new shape")
    content = render(tables, parser.views)

    if args.check:
        # A Windows checkout may hold the file with CRLF; only the content is compared.
        current = OUT.read_text(encoding="utf-8").replace("\r\n", "\n") if OUT.exists() else ""
        if current != content:
            print(f"STALE: {OUT.relative_to(ROOT).as_posix()} does not match the EF model snapshot. "
                  f"Run devtools/checks/gen-database-erd.py and commit the result.", file=sys.stderr)
            return 1
        print("database-erd.md is up to date")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT).as_posix()}")
    print(f"{len(tables)} tables, {sum(len(t.fks) for t in tables.values())} foreign keys")
    return 0


if __name__ == "__main__":
    # UTF-8 out, so an em dash prints on a Windows console's code page too (Book D45).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    sys.exit(main())
