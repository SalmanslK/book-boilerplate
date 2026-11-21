#!/usr/bin/env python3
"""
Every rule the instruction files mark *[enforced: …]* is still enforced.

A marker names what enforces its rule — a test class, a check script, the guard, the commit-msg
hook, `factory pr open`. This check confirms that each named thing still exists and still runs
where the marker says: a test class is in the test projects CI runs; a script marked "CI" is in a
CI file, one marked "pre-commit" is in `.githooks/pre-commit`; the guard is wired into the session
settings and its tests run in CI.

The instruction files are every tracked `CLAUDE.md` outside `devtools/factory/book-template/` (a
pack's rules are live only once `factory book init` copies them). "CI" is the CI files
(`[checks] ci_files` in devtools/factory/factory.toml) together with `devtools/checks/ci-local.sh`,
the local run that stands in for CI while there is none; the test command CI must run is `[commands]
test`. A marker that names nothing, or names
something that is gone or no longer runs, fails — so the count of enforced rules stays a fact anyone can
check.

    python devtools/checks/check-rules.py            # report; exit 1 if a rule is not intact
    python devtools/checks/check-rules.py --write    # also rewrite the table in devtools/README.md
    python devtools/checks/check-rules.py --check    # what CI and pre-commit run: also fail on a stale table
"""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = "devtools/factory/book-template/"
LOCAL_CI = "devtools/checks/ci-local.sh"


def instructions(root: Path = ROOT) -> list[str]:
    """Every tracked CLAUDE.md, root first; the template's packs are not live rules."""
    done = subprocess.run(["git", "-C", str(root), "-c", "core.quotePath=false", "ls-files"],
                          capture_output=True, text=True, encoding="utf-8")
    found = [rel for rel in done.stdout.splitlines()
             if (rel == "CLAUDE.md" or rel.endswith("/CLAUDE.md")) and not rel.startswith(TEMPLATE)]
    return sorted(found, key=lambda rel: (rel != "CLAUDE.md", rel))


def settings(root: Path = ROOT) -> dict:
    try:
        with open(root / "devtools" / "factory" / "factory.toml", "rb") as handle:
            return tomllib.load(handle)
    except (OSError, ValueError):
        return {}


def ci_text(root: Path = ROOT) -> str:
    """The CI files and the local CI run, as one text: what "CI" in a marker means."""
    files = list((settings(root).get("checks") or {}).get("ci_files") or []) + [LOCAL_CI]
    return "\n".join(read(rel, root) for rel in files)


def test_command(root: Path = ROOT) -> str:
    """`[commands] test` as CI names it: its first path (a script), or the whole command."""
    command = str((settings(root).get("commands") or {}).get("test") or "dotnet test")
    return next((word for word in command.split() if "/" in word), command)
README = "devtools/README.md"
START, END = "<!-- rules-table:start -->", "<!-- rules-table:end -->"
MARKER = re.compile(r"\*\[enforced([^\]]*)\]\*", re.S)


@dataclass
class Rule:
    file: str
    line: int
    text: str
    enforcers: list[str]
    runs: list[str]
    problems: list[str]


def read(rel: str, root: Path = ROOT) -> str:
    path = root / rel
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def tracked(pattern: str, root: Path = ROOT) -> list[str]:
    done = subprocess.run(["git", "-C", str(root), "ls-files", pattern], capture_output=True, text=True)
    return done.stdout.splitlines()


def executable(rel: str, root: Path = ROOT) -> bool:
    """Committed with the executable bit: off Windows, git runs a hook only then."""
    done = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "--", rel], capture_output=True, text=True)
    return done.stdout.startswith("100755 ")


def live(text: str) -> str:
    """YAML or shell without its comments: a line commented out runs nothing."""
    lines = (re.sub(r"\s#.*$", "", line) for line in text.splitlines() if not line.lstrip().startswith("#"))
    return "\n".join(lines)


def runs(file: str, text: str) -> bool:
    """`file` is named on a live line of `text` — as itself, not as the tail of a longer name."""
    return re.search(rf"(?<![\w-]){re.escape(file)}(?![\w-])", live(text)) is not None


def plain(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*`]", "", text)
    return " ".join(text.split())


def rule_text(source: str, at: int) -> str:
    """The sentence the marker closes, from the start of its bullet, numbered item or paragraph."""
    start = max(source.rfind("\n- ", 0, at), source.rfind("\n\n", 0, at), source.rfind(". ", 0, at))
    # A numbered item, or a code block before the sentence, starts it too: without them a rule
    # after a fenced command took the item's heading and the command as its text.
    for boundary in re.finditer(r"\n\s*(?:\d+\.\s|```[^\n]*\n)", source[:at]):
        start = max(start, boundary.end() - 1)
    sentence = re.sub(r"\.?\s*Both$", "", plain(source[start + 1:at]).lstrip("- ").strip())
    if len(sentence) < 15:
        # "… Both *[enforced]*" covers the bullet's rules: name the bullet by its opening words.
        bullet = source.rfind("\n- ", 0, at)
        sentence = plain(source[bullet + 1:at]).lstrip("- ").split(". ")[0]
    sentence = sentence[0].upper() + sentence[1:] if sentence else sentence
    return sentence if len(sentence) <= 110 else sentence[:107].rsplit(" ", 1)[0] + " …"


def without_comments(source: str) -> str:
    """C# without its comments and string literals: a class named only there does not exist."""
    return re.sub(r'//[^\n]*|/\*.*?\*/|@?"(?:[^"\\\n]|\\.)*"', "", source, flags=re.S)


def test_classes(root: Path = ROOT) -> set[str]:
    names: set[str] = set()
    for rel in tracked("tests/*.cs", root):
        names.update(re.findall(r"\bclass\s+(\w+Tests)\b", without_comments(read(rel, root))))
    return names


def judge(marker: str, classes: set[str], pipelines: str, precommit: str,
          root: Path = ROOT) -> tuple[list[str], list[str], list[str]]:
    # A branch pattern (`fix/*`) says where the rule applies, not what enforces it.
    named = [n for n in re.findall(r"`([^`]+)`", marker) if "/*" not in n]
    words = marker.lower()
    enforcers, ran, problems = [], [], []
    if re.search(r"\bguard\b", words):
        named.append("guard")
    if "commit-msg" in words and "commit-msg" not in named:
        named.append("commit-msg")
    if not named:
        return [], [], ["names no test or check — say which one enforces it"]
    for name in named:
        enforcers.append(name)
        script = re.match(r"([\w.-]+\.(?:py|sh))\b", name)
        if name.endswith("Tests"):
            if name not in classes:
                problems.append(f"no test class {name} in tests/")
            elif test_command(root) not in live(pipelines):
                problems.append("CI no longer runs the tests")
            else:
                ran.append("CI tests")
        elif script:
            file = script.group(1)
            if not tracked(f"devtools/*/{file}", root) and not tracked(f"devtools/{file}", root):
                problems.append(f"{file} is gone")
                continue
            for where, text in (("CI", pipelines), ("pre-commit", precommit)):
                if re.search(rf"\b{where}\b", marker):
                    if runs(file, text):
                        ran.append(where)
                    else:
                        problems.append(f"{file} is not in {where}")
            if not re.search(r"\bCI\b|pre-commit", marker):
                ran.append("CI" if runs(file, pipelines) else "pre-commit" if runs(file, precommit) else "")
                if not ran[-1]:
                    problems.append(f"{file} runs nowhere")
            if "pre-commit" in ran and not executable(".githooks/pre-commit", root):
                problems.append(".githooks/pre-commit is not committed executable (mode 100755)")
        elif name == "guard":
            settings = read(".claude/settings.json", root)
            if not tracked("devtools/factory/hooks/guard.py", root):
                problems.append("the guard is gone")
            elif "guard.py" not in settings:
                problems.append("the guard is not wired into .claude/settings.json")
            elif "devtools/factory/hooks" not in live(pipelines):
                problems.append("CI no longer runs the guard's tests")
            else:
                ran.append("every session")
        elif name == "commit-msg":
            hook = read(".githooks/commit-msg", root)
            if not runs("check-commit-msg.py", hook) or not tracked("devtools/checks/check-commit-msg.py", root):
                problems.append("the commit-msg hook no longer runs check-commit-msg.py")
            elif not executable(".githooks/commit-msg", root):
                problems.append(".githooks/commit-msg is not committed executable (mode 100755)")
            else:
                ran.append("every commit")
        elif name.startswith("factory pr open"):
            factory = live(read("devtools/factory/factory.py", root))
            if "ai files" in words:
                # the marker names the AI files line, which pr open requires when the branch changes one.
                if "ai_files_touched(root, config" not in factory                         or "AI_FILES.search(" not in live(read("devtools/factory/bitbucket.py", root)):
                    problems.append("factory pr open no longer checks the description's AI files line")
                else:
                    ran.append("factory pr open")
            elif "description" in words:
                # the marker names the description check, which pr open runs before the PR exists.
                if "bitbucket.description_problems(" not in factory \
                        or "EFFORT.search(" not in live(read("devtools/factory/bitbucket.py", root)):
                    problems.append("factory pr open no longer checks the description's Effort line")
                else:
                    ran.append("factory pr open")
            elif "local ci" in words:
                # the marker names the local CI pass, not the review.
                if "def recorded_local_ci" not in factory or "recorded_local_ci(root)" not in factory:
                    problems.append("factory pr open no longer requires a local CI pass")
                else:
                    ran.append("factory pr open")
            elif "def command_reviewed" not in factory or "recorded_review(root)" not in factory:
                problems.append("factory pr open no longer requires a READY review")
            else:
                ran.append("factory pr open")
        elif name.startswith("factory "):
            command = name.split()[1]
            if f'add_parser("{command}"' not in live(read("devtools/factory/factory.py", root)):
                problems.append(f"factory has no {command} command")
            elif re.search(r"\bCI\b", marker) and f"factory.py {command}" not in live(pipelines):
                problems.append(f"CI does not run factory {command}")
            else:
                ran.append("CI" if re.search(r"\bCI\b", marker) else name)
        else:
            problems.append(f"cannot tell what {name} is")
    return enforcers, sorted({r for r in ran if r}), problems


def rules() -> list[Rule]:
    classes = test_classes()
    pipelines, precommit = ci_text(), read(".githooks/pre-commit")
    found: list[Rule] = []
    for rel in instructions():
        source = read(rel)
        for match in MARKER.finditer(source):
            marker = " ".join(match.group(1).split())
            if marker == ": X":  # the notation's own explanation
                continue
            enforcers, runs, problems = judge(marker, classes, pipelines, precommit)
            found.append(Rule(rel, source.count("\n", 0, match.start()) + 1,
                              rule_text(source, match.start()), enforcers, runs, problems))
    return found


def table(found: list[Rule]) -> str:
    intact = sum(not r.problems for r in found)
    rows = [f"| {r.text} | `{r.file}` | {', '.join(f'`{e}`' if e not in ('guard',) else e for e in r.enforcers) or '—'} "
            f"| {', '.join(r.runs) or '—'} | {'yes' if not r.problems else '**no** — ' + '; '.join(r.problems)} |"
            for r in found]
    return "\n".join([START, "",
                      f"Generated by `devtools/checks/check-rules.py --write` — never hand-edit; CI checks it. "
                      f"**{intact} of {len(found)} enforced rules intact.**", "",
                      "| Rule | In | Enforced by | Runs | Intact |", "|---|---|---|---|---|", *rows, "", END])


def main() -> int:
    args = sys.argv[1:]
    sys.stdout.reconfigure(encoding="utf-8")
    found = rules()
    broken = [r for r in found if r.problems]
    for rule in broken:
        print(f"{rule.file}:{rule.line}: {rule.text} — {'; '.join(rule.problems)}", file=sys.stderr)
    readme = read(README)
    block = re.search(re.escape(START) + r".*?" + re.escape(END), readme, re.S)
    if "--write" in args:
        if not block:
            print(f"check-rules: {README} has no {START} … {END} block to write into.", file=sys.stderr)
            return 1
        (ROOT / README).write_text(readme[:block.start()] + table(found) + readme[block.end():],
                                   encoding="utf-8", newline="\n")
    elif "--check" in args and (not block or block.group(0) != table(found)):
        print(f"check-rules: the rules table in {README} is stale — run "
              "`python devtools/checks/check-rules.py --write`.", file=sys.stderr)
        return 1
    print(f"{len(found) - len(broken)} of {len(found)} enforced rules intact.")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
