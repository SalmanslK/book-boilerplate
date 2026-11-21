#!/usr/bin/env python3
"""
An API change reaches the guide of the module it changed, in the same branch (CLAUDE.md, Features: "A
change that adds, removes or reshapes an endpoint … updates the FE guide").

`docs/api/api-contracts.md` is regenerated whenever a route, a request shape or a permission changes — CI
fails a stale copy — so it is the exact signal of what changed. This check reads it at the merge base and
at HEAD, finds each route row that changed (added, removed or reworded — its permission, request or
responses), and a schema that changed counts as a change to every row that sends or returns it, or a
type holding it (a field list under Request or Response schemas; the declaring file's path is not part of
it). Each changed row's section (`### Heading`) has an owner guide under `docs/fe-integration-guides/`, found in this order:

1. `[fe_guides] owners` in devtools/factory/factory.toml, by the section's heading, with a reason:
   `{ section = "Preferences", guides = ["profile-and-preferences"], reason = "…" }`; `guides = []`
   says no front-end guide owns it (probes, a page a browser opens), still with its reason.
2. The guide whose title is the heading, or the heading's first part: "Order Returns — Order
   Return" belongs to the guide titled "Order Returns"; "Warehouses" to "Warehouses and Bins".
3. The guide whose `**Base:**` path is the most specific prefix of the section's `Prefix`.

and also every guide whose `**Base:**` holds a placeholder (`/api/v1/<module>/changes`) the row's path
matches: a protocol shared by several modules. `api-conventions.md` owns no module.

Each owner must change on the branch, and its `**Last checked against the code:**` line must move: a
guide touched without being checked again is not checked. A changed section with no owner fails with
"add an owner"; an `owners` entry for a heading the contracts no longer have is stale and fails.

    python devtools/checks/check-fe-guides.py [--base origin/dev]

The base is the pull request's destination in CI (`BITBUCKET_PR_DESTINATION_BRANCH`), else
`origin/dev`. Without a merge base — a shallow clone, a laptop that never fetched — it says it could
not check. For a pull request in CI (`BITBUCKET_PR_ID` set) that fails the build, since the PR is
exactly what must be checked; anywhere else (pre-commit, a pipeline on dev itself) it exits 0 with
the message. A `git` command that fails is always a failure: its empty output is not "nothing changed".
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = "docs/api/api-contracts.md"
GUIDES = "docs/fe-integration-guides/"
CONVENTIONS = "api-conventions"
FACTORY_TOML = "devtools/factory/factory.toml"
LAST_CHECKED = "Last checked against the code"
# The field and its value: the date and the commit, on the field's line or the next.
# The field may wrap after its colon, or between the date and the `(commit)` on the next line.
LAST_CHECKED_FIELD = re.compile(r"\*\*Last\s+checked\s+against\s+the\s+code:\*\*[ \t]*\n?[^\n]*(?:\n[ \t]*\([^\n]*)?")
ROW = re.compile(r"^\|\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*\|\s*`([^`]+)`")
PREFIX = re.compile(r"^Prefix `([^`]+)`")
SCHEMAS = ("Request schemas", "Response schemas")


class CouldNotCheck(Exception):
    pass


def git(*args: str, root: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def listed(root: Path, revision: str, rel: str) -> list[str]:
    """The paths a revision holds at or under `rel`; an empty list when none, CouldNotCheck when git fails."""
    done = git("ls-tree", "-r", "-z", "--name-only", revision, "--", rel, root=root)
    if done.returncode != 0:
        raise CouldNotCheck(f"`git ls-tree {revision} -- {rel}` failed ({done.returncode}): {done.stderr.strip()}")
    return [path for path in done.stdout.split("\0") if path]


def show(root: Path, revision: str, rel: str) -> str | None:
    """A file at a revision, or None when it does not exist there; a git that fails is never "absent"."""
    if not listed(root, revision, rel):
        return None
    done = git("show", f"{revision}:{rel}", root=root)
    if done.returncode != 0:
        raise CouldNotCheck(f"`git show {revision}:{rel}` failed ({done.returncode}): {done.stderr.strip()}")
    return done.stdout


def anchor(heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def normal(title: str) -> str:
    """A heading or title compared loosely: case, and the dash between parts, do not matter."""
    return re.sub(r"\s+", " ", re.sub(r"\s+[-–—]\s+", " — ", title.strip().lower()))


class Contracts:
    """api-contracts.md read into sections: each `###` heading's prefix and route rows, and the schemas."""

    def __init__(self, text: str | None):
        self.prefix: dict[str, str] = {}
        self.rows: dict[str, set[str]] = {}
        self.schemas: dict[str, str] = {}
        top = heading = ""
        for line in (text or "").splitlines():
            if line.startswith("## "):
                top = heading = line[3:].strip()
                continue
            if line.startswith("### "):
                heading = line[4:].strip()
                if top in SCHEMAS:
                    self.schemas[heading] = ""
                continue
            if top in SCHEMAS and heading in self.schemas:
                # The fields and a not-found note, never the declaring file's path: a type moved to
                # another file sends the client what it did.
                if line.startswith("|") or line.startswith("_("):
                    self.schemas[heading] += line + "\n"
                continue
            match = PREFIX.match(line)
            if match:
                self.prefix[heading] = match.group(1)
            if ROW.match(line):
                self.rows.setdefault(heading, set()).add(line.strip())

    def sections(self) -> set[str]:
        return set(self.rows) | set(self.prefix)

    def linking(self, schema: str) -> set[tuple[str, str]]:
        """The rows that send or return `schema`: a request's link to it, or a response naming it."""
        target, named = f"(#{anchor(schema)})", names(schema)
        return {(heading, row) for heading, rows in self.rows.items() for row in rows
                if target in row or any(named(code) for code in re.findall(r"`([^`]+)`", row))}

    def holding(self, schema: str) -> set[str]:
        """The schemas with a field of type `schema`: a change to it is a change to them."""
        named = names(schema)
        return {outer for outer, text in self.schemas.items()
                if any(named(cell) for cell in re.findall(r"^\|[^|]*\|\s*`([^`]+)`", text, re.M))}


def names(schema: str):
    """A test: does a C# type written in the contracts (`PagedResult<FarmDto>`) name `schema`?"""
    pattern = re.compile(rf"(?<![\w.]){re.escape(schema)}(?![\w])")
    return lambda written: bool(pattern.search(written))


def changed_rows(before: Contracts, after: Contracts) -> list[tuple[str, str]]:
    """(section, row) for each route row added, removed or reworded, and each row that sends or returns a
    changed schema, or a schema holding one (a change to `AddressDto` reaches every route returning `FarmDto`)."""
    found: set[tuple[str, str]] = set()
    for heading in set(before.rows) | set(after.rows):
        found |= {(heading, row) for row in before.rows.get(heading, set()) ^ after.rows.get(heading, set())}
    changed = {schema for schema in set(before.schemas) | set(after.schemas)
               if before.schemas.get(schema) != after.schemas.get(schema)}
    queue = list(changed)
    while queue:
        inner = queue.pop()
        for outer in (after.holding(inner) | before.holding(inner)) - changed:
            changed.add(outer)
            queue.append(outer)
    for schema in changed:
        found |= after.linking(schema) | before.linking(schema)
    return sorted(found)


class Guide:
    def __init__(self, stem: str, text: str):
        self.stem = stem
        first = next((line for line in text.splitlines() if line.startswith("# ")), "# ")
        self.title = normal(re.split(r"\s+[-–—]\s+front-end integration guide", first[2:], flags=re.I)[0])
        match = re.search(r"\*\*Base:\*\*(.*?)(?:·\s*\*\*|\n\s*\n|$)", text, re.S)
        self.bases = re.findall(r"`(/[^`]*)`", match.group(1)) if match else []
        # The header's field, `· **Last checked against the code:** <date> (<sha>)`, not prose that names it.
        # A wrapped header may break the field across lines, so any run of white space counts as one space.
        match = LAST_CHECKED_FIELD.search(text)
        self.checked = re.sub(r"\s+", " ", match.group(0)) if match else None

    def shared(self) -> list[str]:
        """The Base paths with a placeholder: a protocol several modules follow."""
        return [base for base in self.bases if any(placeholder(part) for part in segments(base))]


def segments(path: str) -> list[str]:
    return [part for part in path.split("/") if part]


def placeholder(part: str) -> bool:
    return bool(re.fullmatch(r"<[^>]+>|\{[^}]+\}", part))


def base_match(base: str, path: str) -> int:
    """How many literal segments of `base` lead `path` (a placeholder matches any one), or -1 if it does not."""
    want, have = segments(base), segments(path)
    if len(want) > len(have):
        return -1
    score = 0
    for part, actual in zip(want, have):
        if placeholder(part):
            continue
        if part != actual:
            return -1
        score += 1
    return score


def owners_of(heading: str, row: str, contracts: Contracts, guides: list[Guide],
              configured: dict[str, list[str]]) -> list[str] | None:
    """The guides that own a changed row, or None when nothing does."""
    path = ROW.match(row).group(2)
    also = {guide.stem for guide in guides if any(base_match(base, path) >= 0 for base in guide.shared())}
    if heading in configured:
        return sorted(set(configured[heading]) | also)
    name = normal(heading)
    titled = [guide for guide in guides if guide.title == name] or \
             [guide for guide in guides if name.startswith(guide.title + " — ") or guide.title.startswith(name + " ")]
    if titled:
        return sorted({guide.stem for guide in titled} | also)
    prefix = contracts.prefix.get(heading, path)
    scored = [(max((base_match(base, prefix) for base in guide.bases if base not in guide.shared()), default=-1),
               guide.stem) for guide in guides]
    best = max((score for score, _ in scored), default=-1)
    owners = {stem for score, stem in scored if best > 0 and score == best} | also
    return sorted(owners) or None


def configured_owners(root: Path) -> dict[str, list[str]]:
    path = root / FACTORY_TOML
    if not path.is_file():
        return {}
    try:
        section = tomllib.loads(path.read_text(encoding="utf-8")).get("fe_guides") or {}
    except tomllib.TOMLDecodeError as error:
        raise CouldNotCheck(f"{FACTORY_TOML} is not valid TOML ({error})") from error
    return owners_from(section)


def owners_from(section: dict) -> dict[str, list[str]]:
    """`[fe_guides] owners`, read from the table already parsed: {contracts section: its guides}."""
    found = {}
    for entry in section.get("owners", []) or []:
        heading = entry.get("section") if isinstance(entry, dict) else None
        guides = entry.get("guides") if isinstance(entry, dict) else None
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if not isinstance(heading, str) or not isinstance(guides, list) or not all(isinstance(g, str) for g in guides):
            raise CouldNotCheck(f"[fe_guides] owners: an entry needs a section and a list of guides ({entry!r})")
        if not isinstance(reason, str) or not reason.strip():
            raise CouldNotCheck(f"[fe_guides] owners: the entry for {heading!r} gives no reason")
        found[heading] = [guide.removesuffix(".md") for guide in guides]
    return found


def guides_at(root: Path, revision: str) -> dict[str, Guide]:
    found = {}
    for path in listed(root, revision, GUIDES):
        name = path[len(GUIDES):]
        # `_template.md` is the shape a new guide starts from, not a guide.
        if name.endswith(".md") and "/" not in name and name != f"{CONVENTIONS}.md" and not name.startswith("_"):
            found[name[:-3]] = Guide(name[:-3], show(root, revision, path) or "")
    return found


def judge(root: Path, merge_base: str) -> list[str]:
    before, after = show(root, merge_base, CONTRACTS), show(root, "HEAD", CONTRACTS)
    configured = configured_owners(root)
    new_contracts = Contracts(after)
    problems = [f"{FACTORY_TOML}: [fe_guides] owners names the section {heading!r}, which {CONTRACTS} does not "
                "have — remove the stale entry" for heading in sorted(configured)
                if after is not None and heading not in new_contracts.sections()]
    if before == after:
        return problems
    old_contracts = Contracts(before)
    rows = changed_rows(old_contracts, new_contracts)
    if not rows:
        return problems
    old_guides, new_guides = guides_at(root, merge_base), guides_at(root, "HEAD")
    listed = list(new_guides.values()) or list(old_guides.values())
    required: dict[str, set[str]] = {}
    for heading, row in rows:
        contracts = new_contracts if heading in new_contracts.rows else old_contracts
        owners = owners_of(heading, row, contracts, listed, configured)
        route = " ".join(ROW.match(row).groups())
        if owners is None:
            problems.append(f"{CONTRACTS}: {route} changed in '{heading}', which no guide owns — add an owner: a guide "
                            f"titled '{heading}', a **Base:** line that covers it, or [fe_guides] owners in "
                            f"{FACTORY_TOML} with a reason")
            continue
        for owner in owners:
            required.setdefault(owner, set()).add(route)
    for owner, routes in sorted(required.items()):
        rel = f"{GUIDES}{owner}.md"
        shown = ", ".join(sorted(routes)[:3]) + (f" and {len(routes) - 3} more" if len(routes) > 3 else "")
        new, old = new_guides.get(owner), old_guides.get(owner)
        if new is None:
            problems.append(f"{rel}: owns {shown}, but there is no such guide — write it, or fix [fe_guides] owners")
        elif new.checked is None:
            problems.append(f"{rel}: owns {shown} but has no '**{LAST_CHECKED}:** YYYY-MM-DD (<sha>)' line")
        elif old is not None and old.checked == new.checked:
            touched = show(root, merge_base, rel) != show(root, "HEAD", rel)
            problems.append(f"{rel}: {shown} changed, but its '{LAST_CHECKED}' line did not move — "
                            + ("check the guide against the code and update the line" if touched else
                               "update the guide (or say in its Deviations why the front end sees no change) "
                               "and move the line"))
    return problems


def main(argv: list[str] | None = None, root: Path = ROOT, env: dict[str, str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    destination = env.get("BITBUCKET_PR_DESTINATION_BRANCH", "")
    base = args[args.index("--base") + 1] if "--base" in args else (f"origin/{destination}" if destination else "origin/dev")
    merge_base = git("merge-base", base, "HEAD", root=root)
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        if env.get("BITBUCKET_PR_ID"):
            print(f"check-fe-guides: could not check pull request {env['BITBUCKET_PR_ID']} — no merge base "
                  f"with {base}. Fetch the destination branch with enough history before this step.",
                  file=sys.stderr)
            return 1
        print(f"check-fe-guides: could not check — no merge base with {base}")
        return 0
    try:
        problems = judge(root, merge_base.stdout.strip())
    except CouldNotCheck as error:
        print(f"check-fe-guides: could not check — {error}", file=sys.stderr)
        return 1
    if problems:
        print(f"check-fe-guides: {len(problems)} problem(s) — an API change reaches its module's FE guide "
              "(CLAUDE.md, Features):", file=sys.stderr)
        print("\n".join(f"  {problem}" for problem in problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
