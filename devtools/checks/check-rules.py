#!/usr/bin/env python3
"""
Every rule the instruction files mark *[enforced: …]* is still enforced.

A marker names what enforces its rule — a test, a check script, the guard, the commit-msg hook, a
`factory` command. This check confirms that each named thing still exists and still runs where the
marker says: a test is defined in a tracked file; a script marked "CI" is in a CI file, one marked
"pre-commit" is in `.githooks/pre-commit`, and one marked neither runs in one of those or in
`factory.toml`'s commands; the guard is wired into the session settings. A marker that names nothing,
or names something that is gone or no longer runs, fails — so the count of enforced rules stays a fact
anyone can check.

The instruction files are every tracked `CLAUDE.md`. The CI files are `[checks] ci_files` in
devtools/factory/factory.toml (the usual Bitbucket, GitHub and GitLab files when it names none).

    python devtools/checks/check-rules.py            # report; exit 1 if a rule is not intact
    python devtools/checks/check-rules.py --check    # the same, as pre-commit runs it

Python 3.11+, standard library only.
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FACTORY_TOML = "devtools/factory/factory.toml"
PRE_COMMIT = ".githooks/pre-commit"
COMMIT_MSG = ".githooks/commit-msg"
DEFAULT_CI_FILES = ["bitbucket-pipelines.yml", ".github/workflows/*.yml", ".github/workflows/*.yaml",
                    ".gitlab-ci.yml"]
MARKER = re.compile(r"\*\[enforced([^\]]*)\]\*", re.S)
SCRIPT = re.compile(r"([\w.-]+\.(?:py|sh))$")
# How a test is defined in the usual stacks: a class or a function, or a described block.
TEST_DEFINITION = r"\b(class|def|function)\s+{name}\b|\b(describe|test|it)\(\s*['\"]{name}['\"]"


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
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def tracked(root: Path = ROOT) -> list[str]:
    done = subprocess.run(["git", "-C", str(root), "-c", "core.quotePath=false", "ls-files"],
                          capture_output=True, text=True, encoding="utf-8")
    return done.stdout.splitlines()


def executable(rel: str, root: Path = ROOT) -> bool:
    """Committed (or staged) with the executable bit: off Windows, git runs a hook only then."""
    done = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "--", rel], capture_output=True, text=True)
    return done.stdout.startswith("100755 ")


def live(text: str) -> str:
    """YAML, TOML or shell without its comments: a line commented out runs nothing."""
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
    """The sentence the marker closes, from the start of its bullet or paragraph."""
    start = max(source.rfind("\n- ", 0, at), source.rfind("\n\n", 0, at), source.rfind(". ", 0, at))
    sentence = re.sub(r"\.?\s*Both$", "", plain(source[start + 1:at]).lstrip("- ").strip())
    if len(sentence) < 15:
        # "… Both *[enforced]*" covers the bullet's rules: name the bullet by its opening words.
        bullet = source.rfind("\n- ", 0, at)
        sentence = plain(source[bullet + 1:at]).lstrip("- ").split(". ")[0]
    sentence = sentence[0].upper() + sentence[1:] if sentence else sentence
    return sentence if len(sentence) <= 110 else sentence[:107].rsplit(" ", 1)[0] + " …"


def ci_files(root: Path = ROOT) -> list[str]:
    try:
        checks = tomllib.loads(read(FACTORY_TOML, root)).get("checks") or {}
    except tomllib.TOMLDecodeError:
        checks = {}
    return list(checks.get("ci_files") or DEFAULT_CI_FILES)


def test_defined(name: str, files: list[str], root: Path = ROOT) -> bool:
    """A tracked source file (not a document) defines a test of this name."""
    pattern = re.compile(TEST_DEFINITION.format(name=re.escape(name)))
    return any(pattern.search(read(rel, root)) for rel in files
               if not rel.endswith((".md", ".txt")) and not rel.startswith("docs/"))


def judge(marker: str, context: dict, root: Path = ROOT) -> tuple[list[str], list[str], list[str]]:
    # A branch pattern (`fix/*`) says where the rule applies, not what enforces it.
    named = [n for n in re.findall(r"`([^`]+)`", marker) if "/*" not in n]
    words = marker.lower()
    enforcers, ran, problems = [], [], []
    files, ci, precommit, config = context["files"], context["ci"], context["precommit"], context["config"]
    if re.search(r"\bguard\b", words):
        named.append("guard")
    if "commit-msg" in words and "commit-msg" not in named:
        named.append("commit-msg")
    if not named:
        return [], [], ["names no test or check — say which one enforces it"]
    for name in named:
        enforcers.append(name)
        script = SCRIPT.search(name)
        if name.endswith("Tests") or name.startswith("test_"):
            if test_defined(name, files, root):
                ran.append("the tests")
            else:
                problems.append(f"no test {name} in the tracked code")
        elif script:
            file = script.group(1)
            present = name in files if "/" in name else any(
                fnmatch.fnmatchcase(rel, f"devtools/*/{file}") or rel == f"devtools/{file}" for rel in files)
            if not present:
                problems.append(f"{name} is gone")
                continue
            for where, text in (("CI", ci), ("pre-commit", precommit)):
                if re.search(rf"\b{where}\b", marker):
                    if runs(file, text):
                        ran.append(where)
                    else:
                        problems.append(f"{file} is not in {where}")
            if not re.search(r"\bCI\b|pre-commit", marker):
                where = ("CI" if runs(file, ci) else "pre-commit" if runs(file, precommit)
                         else "factory.toml" if runs(file, config) else "")
                if where:
                    ran.append(where)
                else:
                    problems.append(f"{file} runs nowhere")
            if "pre-commit" in ran and not executable(PRE_COMMIT, root):
                problems.append(f"{PRE_COMMIT} is not committed executable (mode 100755)")
        elif name == "guard":
            if "devtools/factory/hooks/guard.py" not in files:
                problems.append("the guard is gone")
            elif "guard.py" not in read(".claude/settings.json", root):
                problems.append("the guard is not wired into .claude/settings.json")
            else:
                ran.append("every session")
        elif name == "commit-msg":
            if not runs("check-commit-msg.py", read(COMMIT_MSG, root)) or "devtools/checks/check-commit-msg.py" not in files:
                problems.append("the commit-msg hook no longer runs check-commit-msg.py")
            elif not executable(COMMIT_MSG, root):
                problems.append(f"{COMMIT_MSG} is not committed executable (mode 100755)")
            else:
                ran.append("every commit")
        elif name.startswith("factory "):
            command = name.split()[1]
            if f'add_parser("{command}"' not in live(read("devtools/factory/factory.py", root)):
                problems.append(f"factory has no {command} command")
            elif re.search(r"\bCI\b", marker) and f"factory.py {command}" not in live(ci):
                problems.append(f"CI does not run factory {command}")
            else:
                ran.append("CI" if re.search(r"\bCI\b", marker) else name)
        elif name.startswith("npm run "):
            # A front end's own script, defined in a tracked package.json (the React rule pack: `npm run typecheck`).
            script_name = name.split()[2] if len(name.split()) > 2 else ""
            if any(rel.endswith("package.json") and f'"{script_name}"' in read(rel, root) for rel in files):
                ran.append(name)
            else:
                problems.append(f"no package.json defines {script_name}")
        elif name.startswith("--"):
            # A flag a front end's script passes (`--max-warnings 0` on its lint).
            if any(rel.endswith("package.json") and name in read(rel, root) for rel in files):
                ran.append(name)
            else:
                problems.append(f"no package.json script passes {name}")
        elif re.fullmatch(r"@[\w-]+/[\w-]+|[\w-]+/[\w-]+|no-[\w-]+", name):
            # A lint rule (`@typescript-eslint/no-explicit-any`), switched on in a tracked ESLint config.
            if any(re.search(r"(^|/)(eslint\.config\.[cm]?[jt]s|\.eslintrc(\.\w+)?)$", rel) and name in read(rel, root)
                   for rel in files):
                ran.append("eslint")
            else:
                problems.append(f"no ESLint config switches {name} on")
        elif re.fullmatch(r"[\w.-]+\.(json|js|cjs|mjs|ts|toml|ya?ml|props)", name):
            # A tracked configuration file that holds the rule itself (`tsconfig.json`'s strict mode).
            if any(rel == name or rel.endswith("/" + name) for rel in files):
                ran.append(name)
            else:
                problems.append(f"{name} is not in the repository")
        else:
            problems.append(f"cannot tell what {name} is")
    return enforcers, sorted({r for r in ran if r}), problems


def rules(root: Path = ROOT) -> list[Rule]:
    files = tracked(root)
    patterns = ci_files(root)
    context = {
        "files": files,
        "ci": "\n".join(read(rel, root) for rel in files if any(fnmatch.fnmatchcase(rel, p) for p in patterns)),
        "precommit": read(PRE_COMMIT, root),
        "config": read(FACTORY_TOML, root),
    }
    found: list[Rule] = []
    for rel in sorted(f for f in files if f == "CLAUDE.md" or f.endswith("/CLAUDE.md")):
        source = read(rel, root)
        for match in MARKER.finditer(source):
            marker = " ".join(match.group(1).split())
            if marker == ": X":  # the notation's own explanation
                continue
            enforcers, ran, problems = judge(marker, context, root)
            found.append(Rule(rel, source.count("\n", 0, match.start()) + 1,
                              rule_text(source, match.start()), enforcers, ran, problems))
    return found


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    found = rules(root)
    broken = [r for r in found if r.problems]
    for rule in broken:
        print(f"{rule.file}:{rule.line}: {rule.text} — {'; '.join(rule.problems)}", file=sys.stderr)
    print(f"{len(found) - len(broken)} of {len(found)} enforced rules intact.")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
