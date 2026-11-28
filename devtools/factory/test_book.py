"""
Self-tests for book.py, `book verify` and `book init`. Each check runs against a
throwaway repository: a correct fixture passes it, a broken one fails it. The existing checks
(check-book.py and the rest) are answered by a fake, so nothing here runs them or the network.

    python3 -m unittest discover -s devtools/factory -p "test_book.py" -v
"""

from __future__ import annotations

import hashlib
import re
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import book_config  # noqa: E402
import book  # noqa: E402
import factory  # noqa: E402

MANIFEST = book.load_manifest()
MATCHER = "|".join(MANIFEST["guard_matcher"])

ROOT_CLAUDE = """# Acme

## How these instructions are organised

Two more load when you work in their folder:

| Folder | Its rules |
|---|---|
| `docs/` | [`docs/CLAUDE.md`](docs/CLAUDE.md) |
| `tests/` | [`tests/CLAUDE.md`](tests/CLAUDE.md) |

## Where knowledge lives

| Topic | Owner |
|---|---|
| Project state | `docs/project.md` |
| Questions | `docs/open-questions.md` |
| Defects | `docs/defects.md` |
| Plans | its plan in `docs/plans/` |
| Release notes | `docs/release-notes/qa-build-<n>.md` |

## Git
"""

DOCS_CLAUDE = """# Docs

## Current state, not history

| Live file | Stays live | Moves |
|---|---|---|
| `defects.md` | Open | [`archive/defects-closed.md`](archive/defects-closed.md) |

| Class | The mistake |
|---|---|
| `api-contract` | shape |
| `other` | other |
"""

PROJECT = """# Project state

## What the work needs

# Part 1 — Features and phasing

### Phases

| Feature | Plan | FE guide | Status | Owner | Depends on |
|---|---|---|---|---|---|
| Things | [things](plans/things.md) | — | `Built` | A | — |

# Part 2 — Questions

# Part 3 — Requirements history
"""

QUESTIONS = """# Open questions

**Status:** `Live` · the ledger's own line

### Which document wins

## Still open

### The open questions in full

#### Q1 — Is it so?

**Recommendation:** yes.

**Status:** OPEN

## Resolved
"""

DEFECTS = """# Defects

## Open

| Id | Sev | Status | Defect | Where | Found by | Note |
|---|---|---|---|---|---|---|
| D1 | Low | Open | wrong | x | Test | Class: api-contract · found 2026-11-16 |

## Fixed
"""

PLAN = ("# Things — Design\n\n**Status:** `Built` · **Owner:** A · **FSD:** §1 · **Checked against:** FSD V1 · "
        "**FE guide:** — · **Depends on:** nothing\n")

GUIDE = "# Things\n\n**Last checked against\nthe code:** 2026-11-16 (abc1234)\n\n## Deviations\n\nNone.\n"

INDEX = """Index.

=== LINKS ===

FSD (specification): https://docs.google.com/document/d/abc/edit

=== HAS A LIVE LINK ===

fsd/
  -> the specification

qa-artifacts/
  -> QA's trackers

=== NO LIVE LINK ===

email-templates/
  -> designs
"""

PINNED = {"book-plugins": {"source": {"source": "github", "repo": "owner/book-plugin", "ref": "v0.8.0"},
                           "autoUpdate": False}}
SETTINGS = {"attribution": {"commit": "", "pr": ""}, "extraKnownMarketplaces": PINNED,
            "enabledPlugins": {"book@book-plugins": True}, "hooks":{"PreToolUse": [{"matcher": MATCHER, "hooks": [
    {"type": "command", "timeout": 30, "command":
     "for py in python3; do exec \"$py\" devtools/factory/hooks/guard.py; done; echo '{\"permissionDecision\":\"deny\"}'"}]}]}}

FACTORY_TOML = """base_ref = "origin/dev"

[book]
name = "Acme"
slug = "acme"
env_prefix = "ACME_"

[guard]
read_only_maintained = ["requirements/read-me.txt", "requirements/qa-artifacts/Tracker.xlsx"]
ask = ["CLAUDE.md", ".claude/**"]
ask_except = []

[requirements]
traceability = "docs/Audit/spec-traceability.md"

[requirements.sources]
fsd_dir = "requirements/fsd"
fsd_link_label = "FSD"

[fe_guides]
drive_folder_id = "F1"

[ai_files]
owners = [{ name = "A", account_id = "123" }]
paths = [".claude/**", "CLAUDE.md"]
except = []
"""

SKILL = "---\nname: {name}\ndescription: >\n  Use when it is needed.\n---\n\nBody.\n"


def fixture_files() -> dict[str, str]:
    files = {
        "CLAUDE.md": ROOT_CLAUDE, "docs/CLAUDE.md": DOCS_CLAUDE, "tests/CLAUDE.md": "# Tests\n",
        "AGENTS.md": "See CLAUDE.md; `docs/` and `tests/` have their own.\n",
        ".cursor/rules/docs.mdc": "Read docs/CLAUDE.md\n", ".cursor/rules/tests.mdc": "Read tests/CLAUDE.md\n",
        ".claude/settings.json": json.dumps(SETTINGS),
        "devtools/factory/hooks/guard-config.json": '{"mode": "enforce"}',
        "devtools/factory/factory.toml": FACTORY_TOML,
        "devtools/checks/check-rules.py": 'INSTRUCTIONS = ["CLAUDE.md", "docs/CLAUDE.md", "tests/CLAUDE.md"]\n',
        ".gitignore": ".claude/settings.local.json\n.claude/worktrees/\n.factory/\n.env*\n!.env.example\n",
        ".gitattributes": ".githooks/* text eol=lf\n*.py text eol=lf\n*.sh text eol=lf\n",
        "docs/project.md": PROJECT, "docs/open-questions.md": QUESTIONS, "docs/defects.md": DEFECTS,
        "docs/archive/defects-closed.md": "# Closed\n", "docs/plans/_template.md": PLAN, "docs/plans/things.md": PLAN,
        "docs/fe-integration-guides/things.md": GUIDE, "docs/api/.gitkeep": "", "docs/release-notes/.gitkeep": "",
        "docs/Audit/spec-traceability.md": "# Trace\n",
        "requirements/read-me.txt": INDEX,
        "requirements/fsd/read-me.txt": "Link: in requirements/read-me.txt, LINKS.\n\nLast checked: 2026-11-16 - captured V1\n",
        "requirements/fsd/Acme - Functional Specifications - V1.pdf": "pdf",
        "requirements/fsd/Acme - Functional Specifications - V12.pdf": "pdf",
        "requirements/email-templates/read-me.txt": "No live link. Designs.\n",
        "requirements/qa-artifacts/Tracker.xlsx": "x",
        "requirements/qa-artifacts/read-me.txt": "Link: in requirements/read-me.txt.\n\nLast checked: never\n",
        ".claude/skills/publish-guides/SKILL.md":
            SKILL.format(name="publish-guides") + "The folder: `book config fe_guides.drive_folder_id`.\n",
        ".claude/skills/publish-guides/Publish-FeGuidesToDrive.ps1":
            "param([string]$FolderId)\nif (-not $FolderId) { $FolderId = book config fe_guides.drive_folder_id }\n",
    }
    for skill in MANIFEST["skills"]:
        files.setdefault(f".claude/skills/{skill}/SKILL.md", SKILL.format(name=skill))
    return files


class FakeRunner:
    """git runs for real (in the throwaway repository); every Python script answers `answers` or 0."""

    def __init__(self, answers: dict[str, tuple[int, str]] | None = None, raise_for: dict[str, Exception] | None = None):
        self.answers = answers or {}
        self.raise_for = raise_for or {}
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
        self.calls.append(command)
        if command[0] == "git":
            return book.real_runner(command, cwd, timeout)
        script = command[1]
        for name, error in self.raise_for.items():
            if script.endswith(name):
                raise error
        for name, answer in self.answers.items():
            if script.endswith(name):
                return answer
        return 0, ""


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class BookCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="book-test-"))
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "core.hooksPath", ".githooks")
        for rel, text in fixture_files().items():
            self.write(rel, text)
        self.manifest = dict(MANIFEST, folders=["docs/api", "docs/release-notes"])

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def ctx(self, runner: FakeRunner | None = None, private: dict | None = None) -> book.Context:
        git(self.root, "add", "-A")
        return book.Context(self.root, book.load_target_config(self.root), self.manifest, runner or FakeRunner(),
                            private if private is not None else {"file": "present", "keys": {"live_credential": True}})

    def statuses(self, check, runner=None, private=None) -> list[str]:
        return [line.status for line in check(self.ctx(runner, private))]

    def assertPasses(self, check, **kwargs) -> None:
        lines = check(self.ctx(**kwargs))
        self.assertTrue(lines and all(l.status == book.OK for l in lines), [l.render() for l in lines])

    def assertFails(self, check, status: str = book.WRONG, **kwargs) -> None:
        lines = check(self.ctx(**kwargs))
        self.assertIn(status, [l.status for l in lines], [l.render() for l in lines])


class EachCheck(BookCase):
    def test_n1_hooks_path(self):
        self.assertPasses(book.n1_hooks_path)
        git(self.root, "config", "core.hooksPath", "elsewhere")
        self.assertFails(book.n1_hooks_path)
        git(self.root, "config", "--unset", "core.hooksPath")
        self.assertFails(book.n1_hooks_path, book.MISSING)

    def test_n2_settings(self):
        self.assertPasses(book.n2_settings)
        broken = json.loads(json.dumps(SETTINGS))
        broken["hooks"]["PreToolUse"][0]["matcher"] = "Edit|Write|Bash"
        self.write(".claude/settings.json", json.dumps(broken))
        self.assertFails(book.n2_settings)
        broken = json.loads(json.dumps(SETTINGS))
        broken["attribution"]["commit"] = "Generated with a tool"
        self.write(".claude/settings.json", json.dumps(broken))
        self.assertFails(book.n2_settings)

    def test_n3_guard_mode(self):
        self.assertPasses(book.n3_guard_mode)
        self.write("devtools/factory/hooks/guard-config.json", '{"mode": "warn"}')
        self.assertFails(book.n3_guard_mode)

    def test_n4_gitignore(self):
        self.assertPasses(book.n4_gitignore)
        self.write(".gitignore", ".factory/\nBackup*/\n")
        self.assertFails(book.n4_gitignore)

    def test_n5_gitattributes(self):
        self.assertPasses(book.n5_gitattributes)
        self.write(".gitattributes", "* text=auto\n")
        self.assertFails(book.n5_gitattributes)

    def test_n6_instruction_files(self):
        self.assertPasses(book.n6_instruction_files)
        self.write("src/CLAUDE.md", "# Code\n")
        lines = book.n6_instruction_files(self.ctx())
        wrong = [l.element for l in lines if l.status == book.WRONG]
        # No table row, the prose count, check-rules' list, no .cursor pointer, AGENTS.md: each is found.
        self.assertEqual(len(wrong), 5, [l.render() for l in lines])

    def test_n7_owners_exist(self):
        self.assertPasses(book.n7_owners_exist)
        os.remove(self.root / "docs/defects.md")
        self.assertFails(book.n7_owners_exist, book.MISSING)

    def test_n7_honours_skip_skeletons_in_an_imported_owner_table(self):
        # Book D93: the fe-guides pack's imported table names docs/ui-standard.md; a project that skipped it was told MISSING.
        self.write(".claude/book/fe-guides.md", "## Where knowledge lives\n\n| Topic | Owner |\n|---|---|\n| Screens | `docs/ui-standard.md` |\n")
        root = self.root / "CLAUDE.md"
        root.write_text(root.read_text(encoding="utf-8").replace("## Git\n", "@.claude/book/fe-guides.md\n\n## Git\n", 1),
                        encoding="utf-8", newline="\n")
        lines = book.n7_owners_exist(self.ctx())
        self.assertEqual([l.status for l in lines], [book.MISSING], [l.render() for l in lines])
        self.assertIn("docs/ui-standard.md", lines[0].detail)
        self.config('skip_skeletons = ["docs/ui-standard.md"]\n')
        self.assertPasses(book.n7_owners_exist)

    def test_n8_archive(self):
        self.assertPasses(book.n8_archive)
        os.remove(self.root / "docs/archive/defects-closed.md")
        self.assertFails(book.n8_archive, book.MISSING)

    def test_n9_project(self):
        self.assertPasses(book.n9_project)
        self.write("docs/plans/other.md", PLAN)
        self.assertFails(book.n9_project)
        os.remove(self.root / "docs/plans/other.md")
        self.write("docs/project.md", PROJECT.replace("# Part 2 — Questions\n", ""))
        self.assertFails(book.n9_project)

    def test_n10_ledgers(self):
        self.assertPasses(book.n10_ledgers)
        self.write("docs/open-questions.md", QUESTIONS.replace("**Recommendation:** yes.", ""))
        self.assertFails(book.n10_ledgers)
        self.write("docs/open-questions.md", QUESTIONS)
        self.write("docs/defects.md", DEFECTS.replace("| Open |", "| Waiting |"))
        self.assertFails(book.n10_ledgers)
        self.write("docs/defects.md", DEFECTS.replace("Class: api-contract · found 2026-11-16", "see PR"))
        self.assertFails(book.n10_ledgers)

    def test_n10_takes_a_classified_defect_row_in_the_1_6_shape(self):
        # The 1.6 Open table: Type and Source columns, and the Note says only when the row was found.
        classified = ("# Defects\n\n## Open\n\n"
                      "| Id | Sev | Status | Type | Source | Defect | Where | Note |\n"
                      "|---|---|---|---|---|---|---|---|\n"
                      "| D1 | High | Open | bug | code-review: PR #60 | Login fails | src/Auth | found 2026-11-23 |\n"
                      "\n## Fixed\n")
        self.write("docs/defects.md", classified)
        self.assertPasses(book.n10_ledgers)
        self.write("docs/defects.md", classified.replace("found 2026-11-23", "see PR"))
        self.assertIn('D1: no "found <date>"', book.n10_ledgers(self.ctx())[1].detail)
        self.write("docs/defects.md", classified.replace("| bug |", "| oops |"))
        self.assertIn("Type 'oops'", book.n10_ledgers(self.ctx())[1].detail)
        self.write("docs/defects.md", classified.replace("| bug | code-review: PR #60 |", "| — | — |"))
        self.assertPasses(book.n10_ledgers)  # an unclassified row is check-book's warning, not verify's failure

    def test_n10_knows_the_types_check_book_knows(self):
        text = (HERE.parent / "checks" / "check-book.py").read_text(encoding="utf-8")
        self.assertEqual(eval(re.search(r"^TYPES = (\(.*\))$", text, re.M).group(1)), book.DEFECT_TYPES)

    def test_n11_plan_status(self):
        self.assertPasses(book.n11_plan_status)
        self.write("docs/plans/things.md", PLAN.replace(" · **FSD:** §1", ""))
        self.assertFails(book.n11_plan_status)

    def test_n12_fe_guides(self):
        self.assertPasses(book.n12_fe_guides)
        self.write("docs/fe-integration-guides/things.md", "# Things\n\nNo header.\n")
        self.assertFails(book.n12_fe_guides)

    def test_the_guide_template_is_not_a_guide(self):
        self.write("docs/fe-integration-guides/_template.md",
                   "# <Module>\n\n**Last checked against the code:** YYYY-MM-DD (<commit>)\n")
        self.assertPasses(book.n12_fe_guides)

    def test_with_the_pack_the_guide_template_and_the_packs_docs_must_exist(self):
        # Book D44: a Book installed before upgrade offered the guide template lacked it, and verify said nothing.
        with_pack = FACTORY_TOML.replace('env_prefix = "ACME_"\n', 'env_prefix = "ACME_"\npacks = ["fe-guides"]\n')
        self.write("devtools/factory/factory.toml", with_pack)
        lines = book.n12_fe_guides(self.ctx())
        self.assertEqual(lines[0].status, book.MISSING)
        for rel in ("docs/fe-integration-guides/_template.md", "docs/ui-standard.md",
                    "docs/fe-integration-guides/api-conventions.md", "docs/api/fe-api-coverage.md"):
            self.assertIn(rel, lines[0].detail)
        for rel in book.pack_docs(self.manifest, "fe-guides"):
            self.write(rel, "# A document\n\n**Last checked against the code:** 2026-11-16 (abc1234)\n")
        self.assertPasses(book.n12_fe_guides)

    def test_a_project_can_skip_a_packs_optional_document(self):
        with_pack = FACTORY_TOML.replace('env_prefix = "ACME_"\n', 'env_prefix = "ACME_"\npacks = ["fe-guides"]\n'
                                         'skip_skeletons = ["docs/ui-standard.md"]\n')
        self.write("devtools/factory/factory.toml", with_pack)
        for rel in book.pack_docs(self.manifest, "fe-guides"):
            if rel != "docs/ui-standard.md":
                self.write(rel, "# A document\n\n**Last checked against the code:** 2026-11-16 (abc1234)\n")
        self.assertPasses(book.n12_fe_guides)

    def test_n13_requirements(self):
        self.assertPasses(book.n13_requirements)
        self.write("requirements/release-plan/plan.csv", "x")
        self.assertFails(book.n13_requirements)
        os.remove(self.root / "requirements/release-plan/plan.csv")
        self.write("requirements/read-me.txt", INDEX.replace("https://docs.google.com/document/d/abc/edit", "<link>"))
        self.assertFails(book.n13_requirements, book.TO_FILL)
        self.write("requirements/read-me.txt", INDEX)
        self.write("requirements/fsd/read-me.txt", "Link: here.\n\nLast checked: a\nLast checked: b\n")
        self.assertFails(book.n13_requirements)

    def test_n14_snapshots(self):
        lines = book.n14_snapshots(self.ctx())
        self.assertIn("highest V12", lines[0].detail)  # compared as numbers: V12 above V1, never lexically
        self.write("requirements/fsd/spec-final.pdf", "pdf")
        self.assertFails(book.n14_snapshots)

    def config(self, book_keys: str = "", sources: str = "") -> None:
        """The fixture's factory.toml with lines added to [book] and [requirements.sources]."""
        self.write("devtools/factory/factory.toml", FACTORY_TOML
                   .replace('env_prefix = "ACME_"\n', f'env_prefix = "ACME_"\n{book_keys}')
                   .replace('fsd_link_label = "FSD"\n', f'fsd_link_label = "FSD"\n{sources}'))

    def test_snapshot_dirs_are_required_and_an_unnamed_folder_is_not_judged(self):
        self.write("requirements/permissions-matrix/matrix-final.pdf", "pdf")
        self.assertEqual([l.element for l in book.n14_snapshots(self.ctx())], ["requirements/fsd/ snapshot names"])
        self.config(sources='snapshot_dirs = ["requirements/permissions-matrix/", "requirements/release-plan"]\n')
        lines = {l.element: l.status for l in book.n14_snapshots(self.ctx())}
        self.assertEqual(lines, {"requirements/fsd/ snapshot names": book.OK,
                                 "requirements/permissions-matrix/ snapshot names": book.WRONG,
                                 "requirements/release-plan/ snapshot names": book.MISSING})
        os.remove(self.root / "requirements/permissions-matrix/matrix-final.pdf")
        self.write("requirements/release-plan/Plan - V3.xlsx", "x")
        lines = {l.element: l.status for l in book.n14_snapshots(self.ctx())}
        self.assertEqual(lines["requirements/permissions-matrix/ snapshot names"], book.TO_FILL)
        self.assertEqual(lines["requirements/release-plan/ snapshot names"], book.OK)

    def test_a_folder_under_no_live_needs_no_read_me(self):
        os.remove(self.root / "requirements/email-templates/read-me.txt")
        self.write("requirements/email-templates/welcome.html", "x")
        self.assertPasses(book.n13_requirements)
        # Any block whose title starts NO LIVE counts: "NO LIVE SOURCE (delivered by the client)" too.
        self.write("requirements/read-me.txt", INDEX.replace("=== NO LIVE LINK ===",
                                                             "=== NO LIVE SOURCE (delivered by the client) ==="))
        self.assertPasses(book.n13_requirements)
        self.write("requirements/read-me.txt", INDEX.replace("=== NO LIVE LINK ===\n\nemail-templates/",
                                                             "=== NO LIVE LINK ===\n\nfonts/"))
        self.assertFails(book.n13_requirements)  # not under NO LIVE any more: it needs its read-me

    def test_a_linked_folder_needs_a_link_read_me_and_every_entry_sits_under_a_folder_block(self):
        self.write("requirements/qa-artifacts/read-me.txt", "No live link. Trackers.\n")
        lines = book.n13_requirements(self.ctx())
        self.assertEqual(lines[0].status, book.WRONG)
        self.assertIn("though the index lists it under HAS A LIVE LINK", lines[0].detail)
        self.write("requirements/qa-artifacts/read-me.txt", "Link: in requirements/read-me.txt.\n\nLast checked: never\n")
        # A folder named only in the LINKS block has no entry: the folder blocks are what count.
        self.write("requirements/read-me.txt", INDEX.replace("qa-artifacts/\n  -> QA's trackers\n", "")
                   .replace("=== HAS A LIVE LINK", "qa-artifacts/ is linked above\n\n=== HAS A LIVE LINK"))
        found = [l for l in book.n13_requirements(self.ctx()) if l.element == "the index has an entry per folder"]
        self.assertEqual(found[0].status, book.WRONG)
        self.assertIn("qa-artifacts", found[0].detail)

    def test_the_phase_columns_come_from_factory_toml_and_an_optional_one_may_be_absent(self):
        self.write("docs/project.md", PROJECT.replace(" Depends on |", "").replace(" — |\n", "\n", 1)
                   .replace("|---|---|---|---|---|---|", "|---|---|---|---|---|"))
        self.assertFails(book.n9_project)  # Depends on is required by default
        self.config('optional_phase_columns = ["Depends on"]\n')
        self.assertPasses(book.n9_project)
        self.config('phase_columns = ["Feature", "FSD", "Plan", "Owner", "Status"]\n'
                    'optional_phase_columns = ["Depends on"]\n')
        self.write("docs/project.md", PROJECT.replace("| Feature | Plan | FE guide | Status | Owner | Depends on |",
                                                      "| Feature | FSD | Plan | Owner | Depends on | Status |"))
        self.assertPasses(book.n9_project)
        self.write("docs/project.md", PROJECT)
        lines = book.n9_project(self.ctx())
        self.assertIn("Feature | FSD | Plan | Owner | Status (Depends on optional)", lines[0].detail)

    def test_the_recommendation_heading_comes_from_factory_toml(self):
        self.config('recommendation_heading = "**What we suggest:**"\n')
        lines = book.n10_ledgers(self.ctx())
        self.assertIn("open question without What we suggest:", lines[0].detail)
        self.write("docs/open-questions.md", QUESTIONS.replace("**Recommendation:**", "**What we suggest:**"))
        self.assertPasses(book.n10_ledgers)

    def test_an_fe_guide_header_may_name_the_branch_checked(self):
        self.write("docs/fe-integration-guides/things.md",
                   GUIDE.replace("(abc1234)", "(dev abc1234, before the rename)"))
        self.assertPasses(book.n12_fe_guides)
        self.write("docs/fe-integration-guides/things.md", GUIDE.replace("(abc1234)", "(dev)"))
        self.assertFails(book.n12_fe_guides)

    def test_the_projects_title_and_aliases_are_its_own_names(self):
        self.manifest = dict(MANIFEST, source=dict(MANIFEST["source"], foreign_names=["Acme Shop", "Shopline"]))
        self.write("docs/defects.md", DEFECTS + "\nAcme Shop runs on Shopline.\n")
        self.assertFails(book.n20_names)
        self.config('title = "Acme Shop"\naliases = ["Shopline"]\n')
        self.assertPasses(book.n20_names)

    def test_n15_factory_config(self):
        self.assertPasses(book.n15_factory_config)
        os.remove(self.root / "requirements/qa-artifacts/Tracker.xlsx")
        self.assertFails(book.n15_factory_config)
        self.write("requirements/qa-artifacts/Tracker.xlsx", "x")
        self.write("devtools/factory/factory.toml", FACTORY_TOML.replace('ask = ["CLAUDE.md", ".claude/**"]', 'ask = []'))
        self.assertFails(book.n15_factory_config)

    def test_n15_names_a_deploy_note_no_step_reads(self):
        self.write("devtools/factory/factory.toml", FACTORY_TOML + '\n[deploy.notes]\ncheck = "x"\nup = "y"\n')
        self.assertPasses(book.n15_factory_config)
        self.write("devtools/factory/factory.toml", FACTORY_TOML + '\n[deploy.notes]\ncheck = "x"\nchek = "y"\n')
        lines = book.n15_factory_config(self.ctx())
        self.assertIn(book.WRONG, [l.status for l in lines])
        self.assertIn("[deploy.notes] chek", " ".join(l.render() for l in lines))

    def test_n16_skills(self):
        self.assertPasses(book.n16_skills)
        self.write(".claude/skills/commit/SKILL.md", SKILL.format(name="kommit"))
        self.assertFails(book.n16_skills)
        shutil.rmtree(self.root / ".claude/skills/commit")
        self.assertPasses(book.n16_skills)
        # From v1.5 the template owns no skill: a Book with none of its own has no .claude/skills/ at all.
        shutil.rmtree(self.root / ".claude/skills")
        self.assertPasses(book.n16_skills)
        self.assertIn("none of the project's own", " ".join(l.render() for l in book.n16_skills(self.ctx())))
        # The Book's skills are the plugin's: settings that do not enable it leave the Book without them.
        for settings in ('{"enabledPlugins": {"book@book-plugins": false}}\n', "{}\n", "not json\n"):
            with self.subTest(settings):
                self.write(".claude/settings.json", settings)
                self.assertFails(book.n16_skills, book.MISSING)

    def test_n16_the_plugin_is_pinned_to_a_release_no_older_than_the_template_needs(self):
        def with_market(entry) -> None:
            settings = json.loads(json.dumps(SETTINGS))
            settings["extraKnownMarketplaces"] = {"book-plugins": entry} if entry is not None else {}
            self.write(".claude/settings.json", json.dumps(settings))

        def pin_status() -> tuple[str, str]:
            line = next(l for l in book.n16_skills(self.ctx()) if l.element == "the book plugin is pinned to a release")
            return line.status, line.render()

        self.assertEqual(book.OK, pin_status()[0], "the fixture pins v0.8.0, this template's plugin_min")
        source = {"source": "github", "repo": "owner/book-plugin"}
        for ref, status in (("v9.0.0", book.OK), ("v0.3.0", book.WRONG), ("v0.4.0", book.WRONG), ("v0.5.0", book.WRONG), ("v0.5.1", book.WRONG), ("v0.6.0", book.WRONG), ("v0.6.1", book.WRONG), ("v0.7.0", book.WRONG), ("v0.7.1", book.WRONG), ("v0.7.2", book.WRONG),
                            (None, book.WRONG),
                            ("main", book.WRONG),
                            ("e1e60175b0fbacdcc28342946e20b41461fe7d7a", book.CONFIRM),
                            ("E1E60175B0FBACDCC28342946E20B41461FE7D7A", book.CONFIRM)):
            with self.subTest(ref):
                with_market({"source": dict(source, ref=ref) if ref else source})
                got, text = pin_status()
                self.assertEqual(status, got, text)
                if ref in (None, "main"):
                    self.assertIn("unpinned", text)
        # Pinned, but not to anything this check can compare: said so, never "unpinned".
        for ref in ("0.4.0", "v0.5.0-rc.1", "V0.4.0", "refs/tags/v0.4.0", "e1e6017"):
            with self.subTest(ref):
                with_market({"source": dict(source, ref=ref)})
                got, text = pin_status()
                self.assertEqual(book.WRONG, got, text)
                self.assertIn(f"pinned to {ref!r}, which is not a vX.Y.Z release tag or a full commit SHA", text)
                self.assertNotIn("unpinned", text)
        with_market(None)
        self.assertEqual(book.CONFIRM, pin_status()[0], "no marketplace entry: say where the plugin comes from")
        with_market({"source": dict(source, ref="v0.4.0")})
        self.manifest = dict(self.manifest, plugin_min="0.4")
        with self.assertRaises(book.BookError, msg="a malformed plugin_min stops verify, not a skipped floor"):
            book.n16_skills(self.ctx())
        self.manifest = dict(self.manifest, plugin_min=MANIFEST["plugin_min"])
        self.write(".claude/settings.json", '{"enabledPlugins": {}}')
        self.assertNotIn("the book plugin is pinned to a release",
                         [l.element for l in book.n16_skills(self.ctx())], "no plugin enabled, nothing to pin")

    def test_n17_drive_folder(self):
        self.assertPasses(book.n17_drive_folder)
        script = ".claude/skills/publish-guides/Publish-FeGuidesToDrive.ps1"
        skill = ".claude/skills/publish-guides/SKILL.md"
        good_script = (self.root / script).read_text(encoding="utf-8")
        good_skill = (self.root / skill).read_text(encoding="utf-8")
        # A second copy of the id — the same one or another — is the drift N17 prevents.
        self.write(script, "param([string]$FolderId = 'F2')\n" + good_script)
        self.assertFails(book.n17_drive_folder)
        self.write(script, good_script + "# folder F1\n")
        self.assertFails(book.n17_drive_folder)
        self.write(script, "param([string]$FolderId)\n")
        self.assertFails(book.n17_drive_folder)
        self.write(script, good_script)
        self.write(skill, good_skill + "[folder](https://drive.google.com/drive/folders/F2)\n")
        self.assertFails(book.n17_drive_folder)
        self.write(skill, SKILL.format(name="publish-guides"))
        self.assertFails(book.n17_drive_folder)
        self.write(skill, good_skill)
        self.assertPasses(book.n17_drive_folder)
        self.write("devtools/factory/factory.toml", FACTORY_TOML.replace('drive_folder_id = "F1"', ""))
        self.assertFails(book.n17_drive_folder, book.MISSING)
        self.write("devtools/factory/factory.toml", FACTORY_TOML.replace(
            'drive_folder_id = "F1"', 'drive_folder_id = "<FILL-FE-DRIVE-FOLDER-ID>"'))
        self.assertFails(book.n17_drive_folder, book.TO_FILL)

    def test_n17_from_v140_the_pack_is_known_by_book_packs(self):
        # The skill and its script are the book plugin's now: the project keeps only the rename stub.
        stub = ("---\nname: publish-guides\ndescription: Renamed to /book:publish-guides, in the book plugin. This old "
                "name is removed in v1.5.\ndisable-model-invocation: true\n---\n\n# /publish-guides was renamed\n")
        self.write(".claude/skills/publish-guides/SKILL.md", stub)
        (self.root / ".claude/skills/publish-guides/Publish-FeGuidesToDrive.ps1").unlink()
        self.assertEqual([book.NA], self.statuses(book.n17_drive_folder), "no [book] packs: no fe-guides pack")
        with_pack = FACTORY_TOML.replace('env_prefix = "ACME_"\n', 'env_prefix = "ACME_"\npacks = ["fe-guides"]\n')
        self.write("devtools/factory/factory.toml", with_pack)
        self.assertPasses(book.n17_drive_folder)
        self.write("devtools/factory/factory.toml", with_pack.replace('drive_folder_id = "F1"', ""))
        self.assertFails(book.n17_drive_folder, book.MISSING)
        # A project copy of the script still there holds to the same one-owner rule.
        self.write("devtools/factory/factory.toml", with_pack)
        self.write(".claude/skills/publish-guides/Publish-FeGuidesToDrive.ps1", "param([string]$FolderId = 'F2')\n")
        self.assertFails(book.n17_drive_folder)

    def test_n18_private_settings_names_only(self):
        self.assertPasses(book.n18_private_settings)
        self.assertFails(book.n18_private_settings, book.TO_FILL, private={"file": "absent", "keys": {}})
        self.assertFails(book.n18_private_settings, book.TO_FILL,
                         private={"file": "present", "keys": {"live_credential": False}})
        self.assertFails(book.n18_private_settings, book.WRONG, private={"file": "invalid", "keys": {}})
        ctx = self.ctx()
        ctx.private = None  # another repository's settings are not read
        self.assertEqual([l.status for l in book.n18_private_settings(ctx)], [book.COULD_NOT])
        self.write("devtools/factory/factory.toml", FACTORY_TOML.replace(
            'fsd_link_label = "FSD"', 'fsd_link_label = "FSD"\nlive_credential = "rclone:mine"'))
        lines = book.n18_private_settings(self.ctx())
        self.assertEqual(lines[0].status, book.WRONG)
        self.assertNotIn("rclone:mine", lines[0].render())

    def test_private_presence_never_holds_a_value(self):
        raw = {"requirements": {"sources": {"live_credential": "rclone:secret-remote"}},
               "deploy": {"smoke_credentials": ["BOOK_TEST_SECRET"]}, "private_settings": str(self.root / "none.toml")}
        os.environ["BOOK_TEST_SECRET"] = "hunter2-value"
        try:
            presence = factory.private_presence(raw)
        finally:
            del os.environ["BOOK_TEST_SECRET"]
        text = json.dumps(presence)
        self.assertNotIn("secret-remote", text)
        self.assertNotIn("hunter2", text)
        self.assertEqual(presence["variables"], {"BOOK_TEST_SECRET": True})
        self.assertEqual(presence["keys"], {"[requirements.sources] live_credential": True})

    def test_n19_gitkeep(self):
        self.assertPasses(book.n19_gitkeep)
        os.remove(self.root / "docs/api/.gitkeep")
        git(self.root, "add", "-A")
        self.assertFails(book.n19_gitkeep)

    def test_n20_names(self):
        self.assertPasses(book.n20_names)
        self.write("docs/defects.md", DEFECTS + "\nSee src/Boilerplate.API.\n")
        self.assertFails(book.n20_names)
        self.write("docs/defects.md", DEFECTS)
        self.write("devtools/factory/book-template/notes.md", "Copied from Boilerplate as it is.\n")
        self.assertPasses(book.n20_names)  # the template's own name, where names_exempt keeps it on purpose
        self.write("docs/defects.md", DEFECTS + "\nInit writes `<!-- FILL -->` slots.\n")
        self.assertPasses(book.n20_names)  # prose naming the slot is not a slot
        self.write("docs/defects.md", DEFECTS + "\n<!-- FILL: who holds defects -->\n")
        self.assertFails(book.n20_names, book.TO_FILL)

    def test_n20_reads_the_template_s_name_as_a_name_not_as_the_english_word(self):
        # "Boilerplate" is also a word: a feature named "Boilerplate and architecture" is not a leftover.
        for prose in ("| Boilerplate and architecture | §2.2 |", "We kept the boilerplate.", "It is Boilerplate."):
            with self.subTest(prose=prose):
                self.write("docs/defects.md", f"{DEFECTS}\n{prose}\n")
                self.assertPasses(book.n20_names)
        for leftover in ("See src/Boilerplate.Persistence/.", "Set BOILERPLATE_TEST_DB.", "# Book Boilerplate"):
            with self.subTest(leftover=leftover):
                self.write("docs/defects.md", f"{DEFECTS}\n{leftover}\n")
                self.assertFails(book.n20_names)

    def test_n20_finds_a_private_name_everywhere_the_template_included(self):
        # Other projects' names come from a person's private settings (factory.py merges them as
        # private_foreign_names): naming them in the manifest would publish them in every Book.
        def n20() -> list:
            ctx = self.ctx()
            ctx.config.setdefault("book", {})["private_foreign_names"] = ["Gizmo"]
            return book.n20_names(ctx)

        self.assertEqual([line.status for line in n20()], [book.OK])
        self.write("devtools/factory/book-template/notes.md", "Built from Gizmo.\n")
        lines = n20()
        self.assertEqual(lines[0].status, book.WRONG)
        self.assertIn("book-template/notes.md (Gizmo)", lines[0].detail)

    def test_a_name_ending_in_underscore_is_a_prefix(self):
        # A prefix such as GIZMO_ must match GIZMO_LOCAL_SETTINGS; a whole name must not match a longer word.
        self.assertTrue(re.search(book.name_pattern("GIZMO_"), "set GIZMO_LOCAL_SETTINGS"))
        self.assertFalse(re.search(book.name_pattern("Gizmo"), "GizmoX"))
        self.assertTrue(re.search(book.name_pattern("Gizmo"), "see Gizmo."))

    def test_a_check_that_breaks_is_never_ok(self):
        def broken(ctx):
            raise ValueError("boom")
        original = book.N_CHECKS
        book.N_CHECKS = [("N1", broken)]
        try:
            text, ok = book.verify(self.ctx())
        finally:
            book.N_CHECKS = original
        self.assertFalse(ok)
        self.assertIn("COULD NOT CHECK", text.splitlines()[1])


class ExistingChecks(BookCase):
    def scripts(self):
        for rel in ("check-book.py", "check-rules.py", "gen-task-rollup.py", "check-private-data.py",
                    "check-protected-paths.py", "run-self-tests.py"):
            if not (self.root / "devtools/checks" / rel).is_file():
                self.write(f"devtools/checks/{rel}", "")

    def test_calls_the_existing_checks(self):
        self.scripts()
        runner = FakeRunner()
        lines = book.run_existing(self.ctx(runner))
        called = {c[1] for c in runner.calls if c[0] != "git"}
        for script in ("check-book.py", "check-rules.py", "gen-task-rollup.py", "check-private-data.py", "run-self-tests.py"):
            self.assertIn(f"devtools/checks/{script}", called)
        # origin/dev is not fetched in a fresh repository: the base checks could not run, and are not passed.
        protected = next(l for l in lines if l.element == "check-protected-paths.py")
        self.assertEqual(protected.status, book.COULD_NOT)
        # No pipeline file, no src/: not applicable, not passed either.
        self.assertEqual(next(l for l in lines if l.element == "check-pipeline.py").status, book.NA)

    def test_a_failing_check_is_wrong_and_a_timeout_could_not_check(self):
        self.scripts()
        runner = FakeRunner(answers={"check-book.py": (1, "check-book: docs/x.md: no owner")},
                            raise_for={"gen-task-rollup.py": subprocess.TimeoutExpired("x", 1)})
        lines = {l.element: l for l in book.run_existing(self.ctx(runner))}
        self.assertEqual(lines["check-book.py"].status, book.WRONG)
        self.assertIn("no owner", lines["check-book.py"].detail)
        self.assertEqual(lines["gen-task-rollup.py --check"].status, book.COULD_NOT)

    def test_a_missing_script_is_missing_not_ok(self):
        lines = {l.element: l for l in book.run_existing(self.ctx())}
        self.assertEqual(lines["check-book.py"].status, book.MISSING)

    def test_skipped_self_tests_are_not_passed(self):
        self.scripts()
        ctx = self.ctx()
        ctx.self_tests = False
        lines = {l.element: l for l in book.run_existing(ctx)}
        self.assertEqual(lines["self-tests"].status, book.COULD_NOT)


class Verdict(unittest.TestCase):
    def test_verdicts(self):
        line = lambda status: book.Line(status, "x")  # noqa: E731
        self.assertEqual(book.verdict([line(book.OK), line(book.NA), line(book.CONFIRM)]), ("READY", True))
        self.assertTrue(book.verdict([line(book.OK), line(book.TO_FILL)])[0].startswith("READY WITH GAPS"))
        for status in (book.MISSING, book.WRONG, book.COULD_NOT):
            text, ok = book.verdict([line(book.OK), line(book.TO_FILL), line(status)])
            self.assertTrue(text.startswith("NOT READY"), status)
            self.assertFalse(ok)


class VerifyWritesNothing(BookCase):
    def snapshot(self) -> dict[str, bytes]:
        found = {}
        for path in self.root.rglob("*"):
            if path.is_file():
                found[path.relative_to(self.root).as_posix()] = path.read_bytes()
        return found

    def test_verify_writes_nothing(self):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "Fixture")  # the fixture's .githooks holds no hooks
        before = self.snapshot()
        ctx = book.Context(self.root, book.load_target_config(self.root), self.manifest, FakeRunner(),
                           {"file": "present", "keys": {"live_credential": True}})
        text, _ = book.verify(ctx)
        self.assertIn("Verdict:", text)
        self.assertEqual(before, self.snapshot())


PARAMS = {"name": "Acme", "title": "Acme Shop", "slug": "acme", "env_prefix": "ACME_", "stack": "none", "mode": "greenfield",
          "base_branch": "dev", "protected_branches": ["dev", "main"], "git_host": "bitbucket", "ci_live": False,
          "jira_site": "", "jira_key": "", "fsd_label": "FSD", "fsd_link": "", "fe_drive_folder_id": "",
          "owners": []}


class Init(unittest.TestCase):
    """init against a small template of its own: what it creates, what it keeps, and that it never guesses."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="book-init-"))
        self.template = self.base / "template"
        self.target = self.base / "target"
        files = {
            "AGENTS.md": "Agents for Gizmo; settings in GIZMO_LOCAL_SETTINGS.\n",
            ".cursor/rules/gizmo.mdc": "Gizmo rules\n",
            "devtools/factory/book-template/manifest.toml": "keeps Gizmo as written\n",
            "devtools/factory/book-template/skeletons/CLAUDE.md.tmpl": "# {{name}}\n\n<!-- FILL: what it is -->\n",
            "devtools/factory/book-template/skeletons/factory.toml.tmpl": 'base_ref = "{{base_ref}}"\n[book]\nname = "{{name}}"\n',
        }
        for rel, text in files.items():
            path = self.template / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        self.target.mkdir()
        git(self.target, "init", "-q")
        self.manifest = {
            "version": "test", "source": {"name": "Gizmo", "slug": "gizmo", "env_prefix": "GIZMO_",
                                          "names_exempt": ["devtools/factory/book-template/*"]},
            "copy": {"book": ["AGENTS.md", ".cursor/rules/gizmo.mdc", "devtools/factory/book-template/**/*"]},
            "skeletons": {"book": {"CLAUDE.md": "CLAUDE.md.tmpl", "devtools/factory/factory.toml": "factory.toml.tmpl"}},
            "folders": ["docs/plans", "requirements/fsd"],
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def init(self) -> str:
        text, _ = book.init(self.target, dict(PARAMS), self.manifest, self.template, book.real_runner)
        return text

    def test_creates_what_is_absent_with_the_new_names(self):
        report = self.init()
        self.assertEqual((self.target / "AGENTS.md").read_text(encoding="utf-8"),
                         "Agents for Acme; settings in ACME_LOCAL_SETTINGS.\n")
        self.assertTrue((self.target / ".cursor/rules/acme.mdc").is_file())  # the path is renamed too
        self.assertEqual((self.target / "devtools/factory/book-template/manifest.toml").read_text(encoding="utf-8"),
                         "keeps Gizmo as written\n")  # the template itself is copied as it is
        self.assertIn("<!-- FILL: what it is -->", (self.target / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertIn('base_ref = "origin/dev"', (self.target / "devtools/factory/factory.toml").read_text(encoding="utf-8"))
        self.assertTrue((self.target / "docs/plans/.gitkeep").is_file())
        self.assertIn("CREATED", report)
        self.assertEqual(subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=self.target,
                                        capture_output=True, text=True).stdout.strip(), ".githooks")

    def test_never_overwrites(self):
        (self.target / "CLAUDE.md").write_text("ours\n", encoding="utf-8")
        (self.target / "AGENTS.md").write_text("ours too\n", encoding="utf-8")
        report = self.init()
        self.assertEqual((self.target / "CLAUDE.md").read_text(encoding="utf-8"), "ours\n")
        self.assertEqual((self.target / "AGENTS.md").read_text(encoding="utf-8"), "ours too\n")
        self.assertIn("KEPT     CLAUDE.md", report)
        self.assertIn("KEPT     AGENTS.md", report)

    def test_idempotent(self):
        self.init()
        before = {p: p.read_bytes() for p in self.target.rglob("*") if p.is_file() and ".git" not in p.parts}
        report = self.init()
        after = {p: p.read_bytes() for p in self.target.rglob("*") if p.is_file() and ".git" not in p.parts}
        self.assertEqual(before, after)
        self.assertIn("- 0 created,", report.splitlines()[0])

    def test_never_guesses_an_answer(self):
        params = self.base / "book.toml"
        params.write_text('[book]\nname = "Acme"\n', encoding="utf-8")
        with self.assertRaises(book.BookError) as raised:
            book.load_params(params)
        self.assertIn("no answer for", str(raised.exception))
        with self.assertRaises(book.BookError):
            book.load_params(self.base / "absent.toml")

    def test_owners_is_a_list_of_name_and_account_id_tables(self):
        # owners = ["id"] used to stop init with an AttributeError, far from the answer that caused it.
        params = self.base / "book.toml"
        answers = "".join(f"{k} = {json.dumps(v)}\n" for k, v in PARAMS.items() if k != "owners")
        for owners in ('["acc-1"]', '[{ name = "A" }]', '{ name = "A", account_id = "1" }', '[{ name = "A", account_id = 1 }]'):
            with self.subTest(owners):
                params.write_text(f"[book]\n{answers}owners = {owners}\n", encoding="utf-8")
                with self.assertRaises(book.BookError) as raised:
                    book.load_params(params)
                self.assertIn('owners = [{ name = "A Person", account_id = ', str(raised.exception))
        params.write_text(f'[book]\n{answers}owners = [{{ name = "A", account_id = "acc-1" }}]\n', encoding="utf-8")
        self.assertEqual(book.load_params(params)["owners"], [{"name": "A", "account_id": "acc-1"}])

    def test_the_title_is_an_answer_and_dotted_names_are_allowed(self):
        params = self.base / "book.toml"
        answers = {k: v for k, v in PARAMS.items() if k != "title"}

        def write(values: dict) -> None:
            params.write_text("[book]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in values.items()),
                              encoding="utf-8")

        write(answers)
        with self.assertRaises(book.BookError) as raised:
            book.load_params(params)
        self.assertIn("title", str(raised.exception))
        write(dict(answers, name="Acme.Shop", title="Acme-Shop 2"))
        self.assertEqual(book.load_params(params)["title"], "Acme-Shop 2")
        write(dict(answers, title="Acme <Shop>"))
        with self.assertRaises(book.BookError):
            book.load_params(params)

    def test_the_title_heads_the_documents_and_replaces_the_sources_title(self):
        self.assertEqual(book.fill_skeleton("# {{title}} — architecture\n", dict(PARAMS)), "# Acme Shop — architecture\n")
        manifest = {"source": {"name": "Template", "title": "The Template Kit", "slug": "template",
                               "env_prefix": "TEMPLATE_"}}
        pairs = book.substitutions(manifest, dict(PARAMS))
        self.assertIn(("The Template Kit", "Acme Shop"), pairs)
        self.assertEqual(book.renamed("About The Template Kit and Template", pairs), "About Acme Shop and Acme")

    def test_refuses_a_folder_that_is_not_a_repository(self):
        shutil.rmtree(self.target / ".git")
        with self.assertRaises(book.BookError):
            self.init()

    def test_brownfield_leaves_the_hooks_for_the_last_stage(self):
        text, _ = book.init(self.target, dict(PARAMS, mode="brownfield"), self.manifest, self.template, book.real_runner)
        self.assertIn("TO FILL  git config core.hooksPath .githooks", text)
        self.assertEqual(subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=self.target,
                                        capture_output=True, text=True).stdout.strip(), "")


UPGRADE_MANIFEST = """version = "{version}"

[source]
name = "Widget"
title = "Widget Kit"
slug = "widget"
env_prefix = "WIDGET_"
names_exempt = ["devtools/factory/book-template/*"]

[copy]
book = ["AGENTS.md", "docs/plans/_template.md", "requirements/read-me.txt", ".claude/skills/**/*",
        "devtools/checks/*.py", "devtools/factory/book-template/**/*"]

[skeletons.book]
"docs/project.md" = "project.md.tmpl"
"devtools/factory/factory.toml" = "factory.toml.tmpl"
"""

UPGRADE_CONFIG = """# The Book's configuration.
book_version = "{{book_version}}"
base_ref = "{{base_ref}}"

[book]
name = "{{name}}"
title = "{{title}}"
slug = "{{slug}}"
env_prefix = "{{env_prefix}}"
stack = "none"
packs = []
"""


class Classify(unittest.TestCase):
    def test_a_file_the_template_dropped_is_for_a_person_only_where_the_project_still_has_it(self):
        # A file the project never had (a pack's file another pack left out): nothing for a person to remove.
        self.assertEqual(book.classify(None, b"core\n", None, True), book.CURRENT)
        self.assertEqual(book.classify(b"core\n", b"core\n", None, True), book.DROPPED)


class InstalledSettings(unittest.TestCase):
    def test_the_installed_releases_settings_are_rebuilt_as_its_init_wrote_them(self):
        # Book D77: what a project removed is told from what this release's init wrote, not from the new defaults.
        with tempfile.TemporaryDirectory() as folder:
            tree = Path(folder)
            (tree / book.SKELETONS_DIR).mkdir(parents=True)
            (tree / book.PACKS_DIR / "dotnet").mkdir(parents=True)
            (tree / book.SKELETONS_DIR / "factory.toml.tmpl").write_text(
                '[book]\nname = "{{name}}"\nsize_warning = 1500\n\n{{review_scope}}\n', encoding="utf-8")
            (tree / book.PACKS_DIR / "dotnet" / book.REVIEW_SCOPE).write_text('[review_scope]\nalways = ["B0"]\n',
                                                                               encoding="utf-8")
            (tree / book.PACKS_DIR / "dotnet" / book.APPEND).write_text(
                '\n[[code_rules]]\nid = "no-print"\npattern = "x"\n', encoding="utf-8")
            config = {"book": {"name": "Acme", "slug": "acme", "env_prefix": "ACME", "stack": "dotnet", "packs": []}}
            manifest = {"skeletons": {"devtools/factory/factory.toml": "factory.toml.tmpl"}}
            params = dict(book.target_params(config), name="Acme")
            with unittest.mock.patch.object(book, "skeleton_map",
                                            return_value={book.CONFIG_RELATIVE: "factory.toml.tmpl"}):
                settings = book.installed_settings(tree, manifest, params, ["dotnet"], config)
        self.assertEqual(settings["book"], {"name": "Acme", "size_warning": 1500})
        self.assertEqual(settings["review_scope"]["always"], ["B0"])
        self.assertEqual(settings["code_rules"][0]["id"], "no-print")


class TemplateDocsSince(unittest.TestCase):
    def test_a_guide_template_the_installed_version_did_not_own_is_new_not_removed(self):
        # Book D44: from 1.5.1 the template owns it; a 1.5.0 Book that lacks it never deleted it.
        guide = "docs/fe-integration-guides/_template.md"
        files = {guide: b"# <Module>\n", "docs/plans/_template.md": b"# Plan\n"}
        self.assertEqual(sorted(book.owned_then(files, "1.5.0")), ["docs/plans/_template.md"])
        self.assertEqual(sorted(book.owned_then(files, "1.5.1")), sorted(files))
        installed = book.owned_then(files, "1.5.0").get(guide)
        self.assertEqual(book.classify(None, installed, files[guide], True), book.NEW)


class PackSkeletonNotes(unittest.TestCase):
    def test_a_pack_document_the_project_lacks_is_named_with_its_skeleton(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            notes = "\n".join(book.pack_skeleton_notes(target, MANIFEST, ["fe-guides"]))
            self.assertIn("MISSING docs/ui-standard.md: the fe-guides pack's document", notes)
            self.assertIn("skeletons/ui-standard.md.tmpl", notes)
            (target / "docs").mkdir()
            (target / "docs/ui-standard.md").write_text("# UI standard\n", encoding="utf-8")
            self.assertNotIn("ui-standard", "\n".join(book.pack_skeleton_notes(target, MANIFEST, ["fe-guides"])))
            self.assertEqual(book.pack_skeleton_notes(target, MANIFEST, []), [])

    def test_a_skeleton_path_takes_the_projects_name(self):
        manifest = {"packs": {"dotnet": {"skeletons": {"src/Widget.API/CLAUDE.md": "api-claude.md.tmpl"}}}}
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            notes = "\n".join(book.pack_skeleton_notes(target, manifest, ["dotnet"], pairs=[("Widget", "Acme")]))
            self.assertIn("MISSING src/Acme.API/CLAUDE.md", notes)
            self.assertNotIn("Widget", notes)
            (target / "src/Acme.API").mkdir(parents=True)
            (target / "src/Acme.API/CLAUDE.md").write_text("# API\n", encoding="utf-8")
            self.assertEqual(book.pack_skeleton_notes(target, manifest, ["dotnet"], pairs=[("Widget", "Acme")]), [])

    def test_declining_the_test_setup_check_names_the_rules_table_step_until_it_is_done(self):
        # Book D103: the table is stale until `check-rules.py --write` runs, and the hook fails on it.
        declined = {"book": {"declined": ["devtools/checks/check-test-setup.py"]}}
        row = "| A test class never starts its own server | `tests/CLAUDE.md` | `check-test-setup.py` | {} | yes |\n"
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            self.assertEqual(book.declined_rules_notes(target, declined), [], "no README yet")
            (target / "devtools").mkdir()
            readme = target / "devtools" / "README.md"
            readme.write_text(row.format("CI, pre-commit"), encoding="utf-8")
            notes = book.declined_rules_notes(target, declined)
            self.assertEqual(len(notes), 1)
            self.assertTrue(notes[0].startswith(book.CONFIRM))
            self.assertIn("check-rules.py --write", notes[0])
            self.assertEqual(book.declined_rules_notes(target, {}), [], "kept: nothing to do")
            readme.write_text(row.format("declined"), encoding="utf-8")
            self.assertEqual(book.declined_rules_notes(target, declined), [], "written: nothing left")

    def test_a_skipped_skeleton_is_not_asked_for(self):
        with tempfile.TemporaryDirectory() as folder:
            notes = book.pack_skeleton_notes(Path(folder), MANIFEST, ["fe-guides"], {"docs/ui-standard.md"})
            self.assertNotIn("ui-standard", "\n".join(notes))


class RequirementsReadMeOffers(unittest.TestCase):
    """Book D47: a read-me written before v1.3 kept `/requirements-check`, and upgrade never compared it."""

    PARAMS = {"name": "Acme", "title": "Acme Shop", "slug": "acme", "env_prefix": "ACME_"}

    def old_copy(self, skeleton: str) -> str:
        text = (HERE / "book-template/skeletons" / skeleton).read_text(encoding="utf-8")
        text = text.replace("{{title}}", "Acme Shop").replace("{{fsd_label}}", "FSD").replace("{{fsd_link}}", "<link>")
        return text.replace("book:check-sources", "requirements-check")

    def offers(self, files: dict[str, str]):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            for rel, text in files.items():
                (target / rel).parent.mkdir(parents=True, exist_ok=True)
                (target / rel).write_text(text, encoding="utf-8", newline="\n")
            return book.requirements_read_me_offers(target, MANIFEST, HERE.parents[1], self.PARAMS, [])

    def test_an_otherwise_unchanged_copy_is_offered_with_only_its_names_changed(self):
        index = self.old_copy("requirements-index.txt.tmpl").replace(
            "Permissions matrix: <link>", "Permissions matrix: https://example.invalid/matrix")
        linked = self.old_copy("requirements-linked.txt.tmpl").replace(
            "Last checked: never", "Last checked: 2026-11-16 - captured V3\n  (the comments too)")
        self.assertIn("`/requirements-check all`", index)
        offers, notes = self.offers({"requirements/read-me.txt": index, "requirements/fsd/read-me.txt": linked})
        self.assertEqual(notes, [])
        self.assertEqual(sorted(offers), ["requirements/fsd/read-me.txt", "requirements/read-me.txt"])
        new_index = offers["requirements/read-me.txt"][1].decode("utf-8")
        self.assertIn("`/book:check-sources all`", new_index)
        self.assertIn("the book:check-sources skill", new_index)
        self.assertIn("https://example.invalid/matrix", new_index, "the project's LINKS stay")
        self.assertEqual(new_index, index.replace("requirements-check", "book:check-sources"))
        self.assertIn("Last checked: 2026-11-16 - captured V3", offers["requirements/fsd/read-me.txt"][1].decode())

    def test_a_copy_with_the_projects_own_text_is_named_for_a_person(self):
        index = self.old_copy("requirements-index.txt.tmpl").replace(
            "Format per entry", "Our own paragraph.\n\nFormat per entry")
        qa = "Versioning: the superseded copy moves into backup/. See requirements-check skill, Phase 4.\n"
        offers, notes = self.offers({"requirements/read-me.txt": index, "requirements/qa-artifacts/read-me.txt": qa})
        self.assertEqual(offers, {})
        self.assertIn("2 requirements read-me file(s)", notes[0])
        self.assertIn("requirements/qa-artifacts/read-me.txt (line 1)", notes[0])
        self.assertIn("requirements/read-me.txt (lines 4, 7)", notes[0])

    def test_current_names_and_a_branch_name_are_left_alone(self):
        self.assertEqual(book.new_skill_names("on a `chore/book-setup-<slug>` branch"),
                         "on a `chore/book-setup-<slug>` branch")
        self.assertEqual(book.new_skill_names("run /review-branch, then review-pr"), "run /book:self-review, then book:peer-review")
        offers, notes = self.offers({"requirements/read-me.txt": "Run /book:check-sources all.\n"})
        self.assertEqual((offers, notes), ({}, []))


class PluginNotes(unittest.TestCase):
    """A v1.2.9 Book upgrading onto the book plugin is told each step upgrade cannot take for it."""

    MANIFEST = {"plugin": "book@book-plugins"}
    OLD_SCOPE = {"review_scope": {"folder": ".claude/skills/review-pr/references"}}

    def notes(self, settings: str | None, config: dict, rows: list[tuple[str, str]]) -> str:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            if settings is not None:
                (target / ".claude").mkdir()
                (target / ".claude/settings.json").write_text(settings, encoding="utf-8")
            return "\n".join(book.plugin_notes(target, config, self.MANIFEST, rows))

    def test_a_v129_book_is_told_all_three_steps(self):
        rows = [(book.DROPPED, ".claude/agents/book-reviewer.md"),
                (book.DROPPED, ".claude/skills/review-pr/references/review-method.md"),
                (book.CONFLICT, ".claude/skills/commit/SKILL.md"),
                (book.CONFLICT, "docs/plans/_template.md"),
                (book.DROPPED, "devtools/checks/old.py")]
        text = self.notes('{"enabledPlugins": {}}', self.OLD_SCOPE, rows)
        self.assertIn("does not enable book@book-plugins", text)
        self.assertIn("[review_scope] still reads .claude/skills/review-pr/references/", text)
        self.assertIn("3 old skill or agent file(s)", text, "the agent, the reference and the edited SKILL.md")

    def test_a_v134_pack_book_is_told_its_pack_skills_moved_too(self):
        # v1.4.0: an edited pack skill is a CONFLICT (it took the stub then), its scripts are DROPPED.
        rows = [(book.CONFLICT, ".claude/skills/deploy/SKILL.md"),
                (book.DROPPED, ".claude/skills/check-guides/scripts/guides_check.py"),
                (book.CHANGED, ".claude/skills/release-notes/SKILL.md")]
        text = self.notes('{"enabledPlugins": {"book@book-plugins": true}}', {}, rows)
        self.assertIn("2 old skill or agent file(s) a move into the book plugin retired", text)
        self.assertIn("v1.4.0 the packs' skills", text)
        self.assertIn("[deploy]", text, "where an edited pack skill's project facts go")

    def test_a_v14_books_old_name_stubs_are_dropped_and_the_old_names_stop_working(self):
        stub = ("---\nname: {0}\ndescription: Renamed to /book:{1}, in the book plugin. This old name is removed in "
                "v1.5.\ndisable-model-invocation: true\n---\n\n# /{0} was renamed\n")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            # The project's own copy of the template carries the pack's stub too: one old name, two files.
            pack_copy = "devtools/factory/book-template/packs/deploy/.claude/skills/deploy/SKILL.md"
            for rel, old, new in ((".claude/skills/review-branch/SKILL.md", "review-branch", "self-review"),
                                  (".claude/skills/deploy/SKILL.md", "deploy", "deploy"), (pack_copy, "deploy", "deploy")):
                path = target / rel
                path.parent.mkdir(parents=True)
                path.write_text(stub.format(old, new), encoding="utf-8")
            # A stub the project turned into a skill of its own is no stub: it keeps the ordinary note.
            edited = target / ".claude/skills/commit/SKILL.md"
            edited.parent.mkdir(parents=True)
            edited.write_text("---\nname: commit\ndescription: Our own commit steps.\n---\n", encoding="utf-8")
            rows = [(book.DROPPED, ".claude/skills/deploy/SKILL.md"),
                    (book.DROPPED, ".claude/skills/review-branch/SKILL.md"),
                    (book.DROPPED, ".claude/skills/commit/SKILL.md"),
                    (book.DROPPED, pack_copy)]
            manifest = {**self.MANIFEST, "version": "1.5.0"}
            text = "\n".join(book.plugin_notes(target, {}, manifest, rows))
            (target / "AGENTS.md").write_text("Skills are under [`.claude/skills/`](.claude/skills/).\n", encoding="utf-8")
            linked = "\n".join(book.plugin_notes(target, {}, manifest, rows))
        self.assertIn("WRONG 3 old-name stub(s) are DROPPED below (/deploy -> /book:deploy, /review-branch -> "
                      "/book:self-review): template 1.5.0 retires the old names, so they stop working after this "
                      "upgrade", text)
        self.assertIn("under .claude/skills/ (and 1 in devtools/factory/book-template/packs/)", text)
        self.assertIn("1 old skill or agent file(s)", text, "only the edited one, which may hold a local step")
        self.assertNotIn("AGENTS.md", text)
        self.assertIn("AGENTS.md links to .claude/skills/", linked)
        self.assertEqual(book.stub_target(target / "gone"), "")

    def test_a_dropped_test_that_loads_a_retired_script_is_named_with_it(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            test = target / "devtools/checks/test_fe_guides_check.py"
            test.parent.mkdir(parents=True)
            test.write_text('GUIDES_CHECK = HERE / "check-guides" / "scripts" / "guides_check.py"\n', encoding="utf-8")
            (target / "devtools/checks/test_other.py").write_text("import ledger_ids\n", encoding="utf-8")
            rows = [(book.DROPPED, ".claude/skills/check-guides/scripts/guides_check.py"),
                    (book.DROPPED, "devtools/checks/test_fe_guides_check.py"),
                    (book.DROPPED, "devtools/checks/test_other.py")]
            text = "\n".join(book.plugin_notes(target, {}, self.MANIFEST, rows))
        self.assertIn("devtools/checks/test_fe_guides_check.py (DROPPED) load(s) a retired script", text)
        self.assertNotIn("test_other.py", text)

    def test_a_book_already_on_the_plugin_hears_nothing(self):
        config = {"review_scope": {"folder": ".claude/review"}}
        self.assertEqual(self.notes('{"enabledPlugins": {"book@book-plugins": true}}', config,
                                    [(book.CURRENT, ".claude/skills/commit/SKILL.md")]), "")

    def test_a_pin_older_than_the_new_templates_plugin_min_is_named(self):
        config = {"review_scope": {"folder": ".claude/review"}}
        self.MANIFEST = {"plugin": "book@book-plugins", "plugin_min": "0.4.0", "version": "1.3.4"}
        for ref, named in (("v0.3.0", True), ("v0.4.0", False), ("main", False)):
            with self.subTest(ref):
                settings = json.dumps({"enabledPlugins": {"book@book-plugins": True}, "extraKnownMarketplaces": {
                    "book-plugins": {"source": {"source": "github", "repo": "o/p", "ref": ref}}}})
                text = self.notes(settings, config, [])
                self.assertEqual(named, "pins the book plugin at v0.3.0, older than template 1.3.4's plugin_min "
                                        "0.4.0" in text, text)

    def test_a_malformed_plugin_min_stops_the_upgrade_and_the_templates_parses(self):
        self.MANIFEST = {"plugin": "book@book-plugins", "plugin_min": "v0.4"}
        with self.assertRaises(book.BookError):
            self.notes('{"enabledPlugins": {}}', {}, [])
        self.assertTrue(book.version_key(book.plugin_floor(book.load_manifest())), "the template names a semver")

    def test_missing_or_broken_settings_count_as_not_enabled(self):
        for settings in (None, "not json", "[]"):
            with self.subTest(settings):
                self.assertIn("does not enable", self.notes(settings, {}, []))

    def test_a_template_with_no_plugin_adds_no_notes(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(book.plugin_notes(Path(folder), self.OLD_SCOPE, {}, []), [])


class OldCommandNotes(unittest.TestCase):
    """From v1.5 factory.py is no command: a kept CI file, script or hook still running it is named."""

    def test_a_kept_pipeline_running_factory_py_is_named_and_a_test_or_a_mere_path_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            files = {
                "bitbucket-pipelines.yml": '          - python3 devtools/factory/factory.py pr-check "$PR_NUMBER"\n',
                ".githooks/pre-commit": '$py "$root/devtools/book.py" ai-files --staged\n',
                "devtools/checks/ci-local.sh": 'git cat-file -e "$DEST:devtools/factory/factory.py"\n',
                "devtools/factory/hooks/test_guard.py": '("x", "python devtools/factory/factory.py deploy up", set()),\n',
                "ci/old.yml": "  - py -3 devtools\\factory\\factory.py ai-files\n",
                "ci/versioned.yml": "  - python3.12 devtools/factory/factory.py pr-check 1\n",
                "ci/uv.sh": "uv run devtools/factory/factory.py impact --ci\n",
                "ci/direct.sh": "set -e\n./devtools/factory/factory.py config base_ref\n",
                "ci/variable.sh": '$PY "$root/devtools/factory/factory.py" ai-files --staged\n',
                "ci/braced.sh": "${PY} devtools/factory/factory.py fingerprint\n",
                # The template's own pipeline names factory.py only as a path git reads: no note.
                "ci/reads.yml": ('elif git cat-file -e "$DEST:devtools/factory/factory.py" 2>/dev/null; then\n'
                                 '  echo "a Book from before v1.4.0"\n'),
                "ci/redirect.sh": "ls devtools/factory/factory.py 2>/dev/null\n",
                # The AI-files step's fallback runs the destination's copy on a branch older than v1.4.0: no note.
                "ci/destination.yml": ('  FACTORY_ROOT="$PWD" python3 /tmp/destination/devtools/factory/factory.py '
                                       'ai-files || { echo "failed"; exit 1; }\n'),
            }
            for rel, text in files.items():
                (target / rel).parent.mkdir(parents=True, exist_ok=True)
                (target / rel).write_text(text, encoding="utf-8")
            rows = [(book.CONFLICT, "bitbucket-pipelines.yml"), (book.CHANGED, ".githooks/pre-commit"),
                    (book.CHANGED, "devtools/checks/ci-local.sh"), (book.CONFLICT, "devtools/factory/hooks/test_guard.py"),
                    (book.UNKNOWN, "ci/old.yml")] + [(book.CHANGED, f"ci/{name}") for name in (
                        "versioned.yml", "uv.sh", "direct.sh", "variable.sh", "braced.sh", "reads.yml", "redirect.sh",
                        "destination.yml")]
            text = "\n".join(book.old_command_notes(target, rows))
            self.assertEqual(book.old_command_notes(target, [(book.BEHIND, "bitbucket-pipelines.yml")]), [],
                             "a file upgrade rewrites needs no note")
        self.assertIn("WRONG bitbucket-pipelines.yml (CONFLICT), ci/old.yml (UNKNOWN), ci/versioned.yml (CHANGED), "
                      "ci/uv.sh (CHANGED), ci/direct.sh (CHANGED), ci/variable.sh (CHANGED), ci/braced.sh (CHANGED) "
                      "still run(s) devtools/factory/factory.py as a command", text)
        for name in ("pre-commit", "ci-local.sh", "test_guard.py", "reads.yml", "redirect.sh", "destination.yml"):
            self.assertNotIn(name, text)

    def test_the_templates_own_pipeline_kept_by_a_project_gets_no_note(self):
        # A project that kept the template's 1.5 pipeline (CHANGED) runs the destination's old factory.py only on
        # a branch older than v1.4.0, deliberately: not a caller still on the old name.
        pipeline = (book.TEMPLATE_DIR / "packs/bitbucket/bitbucket-pipelines.yml").read_text(encoding="utf-8")
        self.assertIn("/tmp/destination/devtools/factory/factory.py ai-files", pipeline)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / "bitbucket-pipelines.yml").write_text(pipeline, encoding="utf-8")
            self.assertEqual(book.old_command_notes(target, [(book.CHANGED, "bitbucket-pipelines.yml")]), [])
            (target / "bitbucket-pipelines.yml").write_text(
                pipeline + "          - python3 devtools/factory/factory.py ai-files\n", encoding="utf-8")
            self.assertEqual(len(book.old_command_notes(target, [(book.CHANGED, "bitbucket-pipelines.yml")])), 1,
                             "the project's own factory.py run as a command is still named")


class LongLivedNotes(unittest.TestCase):
    """A protected branch still on a Book from before v1.4.0 keeps its own old check: named, for information."""

    def test_a_protected_branch_before_v140_is_named_and_one_on_14_or_with_no_tools_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            git(repo, "init", "-q", "-b", "dev")
            git(repo, "config", "user.email", "t@example.invalid")
            git(repo, "config", "user.name", "t")
            (repo / "README.md").write_text("x\n", encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "no tools")
            git(repo, "branch", "main")  # no AI tools at all: the pull request judges itself, as before
            (repo / "devtools/factory").mkdir(parents=True)
            (repo / "devtools/factory/factory.py").write_text("old\n", encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "a 1.3 Book")
            git(repo, "branch", "qa")
            (repo / "devtools/book.py").write_text("new\n", encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "a 1.4 Book")
            config = {"protected_branches": ["dev", "qa", "uat", "main"], "book": {"packs": ["bitbucket"]}}
            notes = book.long_lived_notes(repo, config, book.real_runner)
            self.assertEqual(book.long_lived_notes(repo, {**config, "book": {"packs": []}}, book.real_runner), [],
                             "no bitbucket pack, no pipeline to fail")
        self.assertEqual(len(notes), 2, notes)
        self.assertTrue(notes[0].startswith("qa hold(s) a Book from before v1.4.0"), notes[0])
        self.assertIn("for information", notes[0])
        self.assertIn("nothing to promote before this upgrade", notes[0])
        self.assertTrue(notes[1].startswith("no local or origin ref for uat"), notes[1])
        for note in notes:  # information, never a stop: promoting early would release unfinished work
            self.assertFalse(note.startswith((book.WRONG, book.CONFIRM)), note)


UNVERSIONED_CONTRACTS = ("# API contracts\n\n## Unversioned routes\n\n| Method | Path | Notes | Source |\n"
                         "|---|---|---|---|\n| GET | `/health/live` | probe | `src/A.cs` |\n"
                         "| GET | `/` | redirect | `src/A.cs` |\n\n## Table of contents\n")


class MustMergeNotes(unittest.TestCase):
    """devtools/book.py (from v1.4.0) needs the new factory.py and guard.py: a kept older copy is merged first."""

    def test_a_new_book_entry_over_a_kept_factory_or_guard_must_merge_them(self):
        rows = [(book.NEW, "devtools/book.py"), (book.CONFLICT, "devtools/factory/factory.py"),
                (book.CONFLICT, "devtools/factory/hooks/guard.py")]
        text = "\n".join(book.must_merge_notes(rows))
        self.assertTrue(text.startswith("WRONG "), text)
        self.assertIn("devtools/factory/factory.py and devtools/factory/hooks/guard.py", text)
        self.assertIn("merge these before using book", text)
        guard_only = [(book.NEW, "devtools/book.py"), (book.BEHIND, "devtools/factory/factory.py"),
                      (book.CONFLICT, "devtools/factory/hooks/guard.py")]
        self.assertIn("keeps its own devtools/factory/hooks/guard.py (", "\n".join(book.must_merge_notes(guard_only)))

    def test_each_kept_file_gets_its_own_clause_and_no_other(self):
        def note(factory_status, guard_status):
            return "\n".join(book.must_merge_notes([(book.NEW, "devtools/book.py"),
                                                    (factory_status, "devtools/factory/factory.py"),
                                                    (guard_status, "devtools/factory/hooks/guard.py")]))
        both = note(book.CONFLICT, book.CONFLICT)
        self.assertIn("merge these before using book", both)
        self.assertIn("`book` refuses to run", both)
        self.assertIn("the guard judges a deploy only under the name factory.py", both)
        factory_only = note(book.CONFLICT, book.CURRENT)
        self.assertIn("merge it before using book", factory_only)
        self.assertIn("`book` refuses to run", factory_only)
        self.assertNotIn("guard", factory_only)
        guard_only = note(book.CURRENT, book.CONFLICT)
        self.assertIn("merge it before using book", guard_only)
        self.assertIn("the guard judges a deploy only under the name factory.py", guard_only)
        self.assertNotIn("refuses to run", guard_only)

    def test_nothing_to_merge_is_no_note(self):
        self.assertEqual(book.must_merge_notes([(book.NEW, "devtools/book.py"),
                                                (book.BEHIND, "devtools/factory/factory.py")]), [])
        self.assertEqual(book.must_merge_notes([(book.CURRENT, "devtools/book.py"),
                                                (book.CONFLICT, "devtools/factory/factory.py")]), [])


class DeployNotes(unittest.TestCase):
    """v1.4.0: /book:deploy reads the project's facts from [deploy]; the keys still to fill are a person's to confirm."""

    FILLED = {"ssh_host": "h", "ssh_user": "u", "ssh_key": "~/.ssh/k", "runbook": "docs/plans/deployment.md",
              "audit_record": "", **{env: {"directory": "/srv", "compose_files": ["c.yaml"], "project": "p",
                                           "settings_environment": env, "api_port": 8080} for env in ("DEV", "QA")}}

    def notes(self, deploy: dict | None, packs: list[str] = ["deploy"]) -> str:
        config = {"book": {"packs": packs}, **({"deploy": deploy} if deploy is not None else {})}
        return "\n".join(book.deploy_notes(config))

    def test_a_filled_table_or_no_deploy_pack_mean_nothing_to_confirm(self):
        self.assertEqual(self.notes(self.FILLED), "")
        self.assertEqual(self.notes(None, packs=["fe-guides"]), "")

    def test_the_keys_to_fill_and_the_keys_v140_added_are_named(self):
        deploy = {key: value for key, value in self.FILLED.items() if key not in ("runbook", "audit_record")}
        deploy["ssh_host"] = "<the DEV/QA host>"
        deploy["QA"] = dict(deploy["QA"], api_port=0)
        text = self.notes(deploy)
        self.assertTrue(text.startswith("CONFIRM "), text)
        self.assertIn("[deploy] ssh_host, [deploy.QA] api_port to fill", text)
        self.assertIn("[deploy] runbook, [deploy] audit_record to add", text)
        self.assertNotIn("ssh_user", text)
        self.assertIn("[deploy] ssh_host, [deploy] ssh_user", self.notes({}), "a Book with no [deploy] yet")
        deep = dict(self.FILLED, ssh_key="~/.ssh/<the deploy key>", tests={"unit": "<the unit test project>"})
        self.assertIn("[deploy] ssh_key, [deploy.tests] unit to fill", self.notes(deep), "a placeholder anywhere")


class FeGuidesNotes(unittest.TestCase):
    """Unversioned routes in the contracts that nothing owns are for a person to confirm (Book D36)."""

    def notes(self, files: dict[str, str], owners: list | None = None) -> str:
        with tempfile.TemporaryDirectory() as folder:
            for rel, text in files.items():
                (Path(folder) / rel).parent.mkdir(parents=True, exist_ok=True)
                (Path(folder) / rel).write_text(text, encoding="utf-8")
            config = {"fe_guides": {"owners": owners}} if owners is not None else {}
            return "\n".join(book.fe_guides_notes(Path(folder), config))

    def test_unowned_unversioned_routes_are_for_a_person_to_confirm(self):
        for owners in (None, [], [{"section": "Preferences", "guides": [], "reason": "x"}]):
            with self.subTest(owners):
                text = self.notes({book.CONTRACTS_DOC: UNVERSIONED_CONTRACTS}, owners)
                self.assertTrue(text.startswith("CONFIRM "), text)
                self.assertIn("2 unversioned route(s) nothing owns (/health/live, /)", text)

    def test_an_owner_entry_a_titled_guide_or_no_such_routes_mean_nothing_to_confirm(self):
        entry = [{"section": "Unversioned routes", "guides": [], "reason": "probes"}]
        guides = book.FE_GUIDES_DIR + "/"
        self.assertEqual(self.notes({book.CONTRACTS_DOC: UNVERSIONED_CONTRACTS}, entry), "")
        self.assertEqual(self.notes({book.CONTRACTS_DOC: UNVERSIONED_CONTRACTS,
                                     guides + "root.md": "# Unversioned routes — front-end integration guide\n"}), "")
        self.assertEqual(self.notes({book.CONTRACTS_DOC: UNVERSIONED_CONTRACTS, guides + "probes.md":
                                     "# Unversioned routes and probes — front-end integration guide\n"}), "",
                         "a title that starts with the section's name owns it, as in check-fe-guides")
        self.assertIn("2 unversioned route(s)", self.notes({book.CONTRACTS_DOC: UNVERSIONED_CONTRACTS, guides +
                                                            "x.md": "# Unversioned routesx\n"}), "a whole word only")
        self.assertEqual(self.notes({}), "", "no contracts (a stack without them)")
        self.assertEqual(self.notes({book.CONTRACTS_DOC: "# API contracts\n\n## Unversioned routes\n\nNone.\n"}), "")

    def test_a_base_line_owns_only_the_routes_it_covers(self):
        files = {book.CONTRACTS_DOC: UNVERSIONED_CONTRACTS, book.FE_GUIDES_DIR + "/health.md":
                 "# Health — front-end integration guide\n\n**Module:** Health · **Base:** `/health` · **Last checked "
                 "against the code:** 2026-11-01 (abc1234)\n"}
        self.assertIn("1 unversioned route(s) nothing owns (/)", self.notes(files))


class Upgrade(unittest.TestCase):
    """
    `book upgrade`: a project installed at template 1.0.0, edited in one template file, upgraded to
    1.1.0. A template repository of its own, tagged at each version, stands in for the boilerplate.
    """

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="book-upgrade-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.template = self.base / "template"
        self.target = self.base / "target"
        for root in (self.template, self.target):
            root.mkdir()
            git(root, "init", "-q")
            git(root, "config", "user.email", "test@example.invalid")
            git(root, "config", "user.name", "Test")
            git(root, "config", "core.autocrlf", "false")
        self.release("1.0.0", {
            "AGENTS.md": "Agents for Widget.\n",
            "docs/plans/_template.md": "# Plan template, v1\n",
            "requirements/read-me.txt": "The Widget index, v1\n",
            ".claude/skills/s/SKILL.md": "# Widget skill, v1\n",
            "devtools/checks/tool.py": "print('Widget tool, v1')\n",
            "devtools/checks/old.py": "print('dropped in 1.1.0')\n",
            "devtools/factory/book-template/skeletons/factory.toml.tmpl": UPGRADE_CONFIG,
            "devtools/factory/book-template/skeletons/project.md.tmpl": "# {{title}}, v1\n",
        })
        text, _ = book.init(self.target, dict(PARAMS), self.manifest(), self.template, book.real_runner)
        self.assertNotIn("MISSING", text)
        git(self.target, "add", "-A")
        git(self.target, "commit", "-q", "-m", "Install the Book")
        # The project edits one template file, and its own content.
        self.write(self.target, ".claude/skills/s/SKILL.md", "# Acme skill, v1, with our own step\n")
        self.write(self.target, "docs/project.md", "# Acme Shop, our project\n")
        self.release("1.1.0", {
            "docs/plans/_template.md": "# Plan template, v2\n",
            "requirements/read-me.txt": "The Widget index, v2\n",
            ".claude/skills/s/SKILL.md": "# Widget skill, v2\n",
            "devtools/checks/tool.py": "print('Widget tool, v2')\n",
            "devtools/checks/new.py": "print('Widget, new in 1.1.0')\n",
            "devtools/factory/book-template/skeletons/project.md.tmpl": "# {{title}}, v2\n",
        }, remove=["devtools/checks/old.py"])

    @staticmethod
    def write(root: Path, rel: str, content: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    def release(self, version: str, files: dict[str, str], remove: tuple[str, ...] | list[str] = (),
                plugin_min: str = "", copy: tuple[str, ...] = ()) -> None:
        """Commit the template at `version` and tag it v<version>, as the boilerplate tags each release. With
        `plugin_min`, the template names the book plugin and owns .claude/settings.json, which pins it."""
        manifest = UPGRADE_MANIFEST.format(version=version)
        if copy:
            manifest = manifest.replace('"devtools/checks/*.py"', ", ".join(['"devtools/checks/*.py"',
                                                                             *(f'"{rel}"' for rel in copy)]), 1)
        if plugin_min:
            manifest = manifest.replace("\n\n[source]", f'\nplugin = "book@book-plugins"\nplugin_min = "{plugin_min}"'
                                                        "\n\n[source]", 1).replace(
                '".claude/skills/**/*"', '".claude/settings.json", ".claude/skills/**/*"', 1)
        files = dict(files, **{"devtools/factory/book-template/manifest.toml": manifest})
        for rel, content in files.items():
            self.write(self.template, rel, content)
        for rel in remove:
            (self.template / rel).unlink()
        git(self.template, "add", "-A")
        git(self.template, "commit", "-q", "-m", f"Release {version}")
        git(self.template, "tag", f"v{version}")

    def manifest(self) -> dict:
        return book.load_manifest(self.template / "devtools/factory/book-template")

    def upgrade(self, apply: bool = False) -> str:
        """The report, and after it the BEHIND and NEW diffs it wrote to their own file (Book D79)."""
        diffs = self.target.parent / "upgrade.diff"
        if diffs.exists():
            diffs.unlink()
        text, ok = book.upgrade(self.target, book.load_target_config(self.target), self.manifest(), self.template,
                                apply, book.real_runner, diffs)
        self.assertTrue(ok)
        self.routine = diffs.read_text(encoding="utf-8") if diffs.exists() else ""
        return text

    def snapshot(self) -> dict[str, bytes]:
        return {p.relative_to(self.target).as_posix(): p.read_bytes() for p in self.target.rglob("*")
                if p.is_file() and ".git" not in p.relative_to(self.target).parts}

    def read(self, rel: str) -> str:
        return (self.target / rel).read_text(encoding="utf-8")

    def test_init_stamps_the_installed_version(self):
        self.assertEqual(book.load_target_config(self.target)["book_version"], "1.0.0")

    def test_the_report_asks_about_unversioned_routes_nothing_owns(self):
        self.write(self.target, book.CONTRACTS_DOC, UNVERSIONED_CONTRACTS)
        self.assertIn("CONFIRM docs/api/api-contracts.md lists 2 unversioned route(s) nothing owns", self.upgrade())
        self.write(self.target, book.FE_GUIDES_DIR + "/probes.md", "# Unversioned routes\n\nThe probes.\n")
        self.assertNotIn("unversioned route(s)", self.upgrade(), "a guide titled after the section owns it")

    def test_the_report_names_each_files_state_shows_diffs_and_writes_nothing(self):
        before = self.snapshot()
        report = self.upgrade()
        self.assertEqual(before, self.snapshot(), "the report is read-only")
        self.assertIn("installed 1.0.0, template 1.1.0 - report only", report.splitlines()[0])
        for line in ["CURRENT   AGENTS.md", "BEHIND    devtools/checks/tool.py", "BEHIND    docs/plans/_template.md",
                     "BEHIND    devtools/factory/book-template/manifest.toml", "NEW       devtools/checks/new.py",
                     "CONFLICT  .claude/skills/s/SKILL.md", "DROPPED   devtools/checks/old.py"]:
            self.assertIn(line, report.splitlines())
        # A diff for each file it would write, with the project's names in the template's text: in their own
        # file, which the report names, since --apply takes them without a decision (Book D79).
        self.assertIn("--- a/devtools/checks/tool.py (project)\n+++ b/devtools/checks/tool.py (template 1.1.0)",
                      self.routine)
        self.assertIn("-print('Acme tool, v1')\n+print('Acme tool, v2')", self.routine)
        self.assertIn("--- /dev/null\n+++ b/devtools/checks/new.py (template 1.1.0)", self.routine)
        self.assertNotIn("+++ b/devtools/checks/tool.py", report)
        self.assertIn("The BEHIND and NEW diffs (5 file(s), which --apply takes without a decision): ", report)
        self.assertEqual(sum(line.endswith(" devtools/checks/old.py") for line in report.splitlines()), 1,
                         "a file for a person is listed once")
        # The conflict shows what the template changed, for a person to carry over.
        self.assertIn("-# Acme skill, v1\n+# Acme skill, v2", report)
        # The project's own content is never compared, even where a template pattern reaches it.
        for rel in ("docs/project.md", "requirements/read-me.txt", "devtools/factory/factory.toml"):
            self.assertNotIn(rel, report)

    def test_the_default_diffs_file_is_one_per_checkout_and_the_header_names_it(self):
        # Book D87: two checkouts with the same folder name once wrote the same file in the temporary folder.
        text, ok = book.upgrade(self.target, book.load_target_config(self.target), self.manifest(), self.template,
                                False, book.real_runner, None)
        self.assertTrue(ok)
        head = text.splitlines()[0]
        self.assertIn("report only, nothing written in the project", head)
        where = Path(head.split("; the BEHIND and NEW diffs are written to ", 1)[1])
        self.addCleanup(where.unlink, missing_ok=True)
        self.assertTrue(where.is_file())
        unique = hashlib.sha256(str(self.target.resolve()).encode("utf-8")).hexdigest()[:8]
        self.assertEqual(where.name, f"book-upgrade-{self.target.name}-{unique}-1.1.0.diff")

    def test_an_unchanged_template_copy_file_the_template_dropped_is_removed_by_apply(self):
        # Book D79: nothing reads the project's copy of the template, so an unchanged dropped file is no decision.
        rel = "devtools/factory/book-template/skeletons/project.md.tmpl"
        self.release("1.2.0", {}, remove=[rel])
        report = self.upgrade()
        self.assertIn(f"RETIRED   {rel}", report.splitlines())
        self.assertNotIn(rel, report.split("For a person", 1)[1] if "For a person" in report else "")
        self.assertIn("--apply removes them (RETIRED below)", report)
        applied = self.upgrade(apply=True)
        self.assertIn("removed (staged)", applied)
        self.assertFalse((self.target / rel).exists())
        staged = subprocess.run(["git", "diff", "--cached", "--name-status"], cwd=self.target, capture_output=True,
                                text=True).stdout
        self.assertIn(f"D\t{rel}", staged)

    def test_apply_updates_only_what_the_project_did_not_change_and_bumps_the_version(self):
        config_before = self.read("devtools/factory/factory.toml")
        report = self.upgrade(apply=True)
        self.assertEqual(self.read("devtools/checks/tool.py"), "print('Acme tool, v2')\n")
        self.assertEqual(self.read("docs/plans/_template.md"), "# Plan template, v2\n")
        self.assertEqual(self.read("devtools/checks/new.py"), "print('Acme, new in 1.1.0')\n")
        self.assertIn('version = "1.1.0"', self.read("devtools/factory/book-template/manifest.toml"))
        # The kept file: the project's edit stands, and a person is told.
        self.assertEqual(self.read(".claude/skills/s/SKILL.md"), "# Acme skill, v1, with our own step\n")
        kept = report.split("For a person", 1)[1]
        self.assertIn("CONFLICT  .claude/skills/s/SKILL.md", kept)
        self.assertIn("DROPPED   devtools/checks/old.py", kept)
        self.assertTrue((self.target / "devtools/checks/old.py").is_file(), "never deleted")
        # The project's content is untouched.
        self.assertEqual(self.read("docs/project.md"), "# Acme Shop, our project\n")
        self.assertEqual(self.read("requirements/read-me.txt"), "The Acme index, v1\n")
        # A conflict is left to merge, so the version stays and the file is listed (Book D56); nothing else changes.
        self.assertEqual(self.read("devtools/factory/factory.toml"), config_before.replace(
            "[book]\n", '[book]\npending_merge = [".claude/skills/s/SKILL.md"]\npending_merge_for = "1.1.0"\n', 1))
        self.assertIn("book_version stays 1.0.0: 1 file(s) to merge by hand are listed in [book] pending_merge", report)
        # Taking pending_merge_for out with the list relists every file (Book D68): the message says to keep it.
        self.assertIn("leave [book] pending_merge_for as it is", report)
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=self.target, capture_output=True,
                                text=True).stdout.split()
        self.assertEqual(staged, ["devtools/checks/new.py", "devtools/factory/book-files.lock"],
                         "only the new file and the lock are staged; the rest is left to review")
        # Run again: the conflict is still one, with the template's change to carry over, and the list is named.
        again = self.upgrade()
        self.assertIn("installed 1.0.0, template 1.1.0", again.splitlines()[0])
        self.assertIn("CURRENT   devtools/checks/tool.py", again.splitlines())
        self.assertIn("CONFLICT  .claude/skills/s/SKILL.md", again.splitlines())
        self.assertIn("-# Acme skill, v1\n+# Acme skill, v2", again)
        self.assertIn("1 file(s) an earlier --apply left to merge by hand ([book] pending_merge): "
                      ".claude/skills/s/SKILL.md - book_version stays 1.0.0", again)
        self.assertIn("keep [book] pending_merge_for", again)
        # A person merges it and takes it off the list: the next --apply bumps the version and drops the list.
        self.write(self.target, ".claude/skills/s/SKILL.md", "# Acme skill, v2, with our own step\n")
        config = self.target / "devtools/factory/factory.toml"
        config.write_text(self.read("devtools/factory/factory.toml").replace(
            'pending_merge = [".claude/skills/s/SKILL.md"]', "pending_merge = []"), encoding="utf-8", newline="\n")
        self.assertIn("RESOLVED  .claude/skills/s/SKILL.md", self.upgrade().splitlines())
        self.assertIn("book_version 1.0.0 -> 1.1.0", self.upgrade(apply=True))
        self.assertEqual(self.read("devtools/factory/factory.toml"),
                         config_before.replace('book_version = "1.0.0"', 'book_version = "1.1.0"'))
        # Run again: the installed version is now 1.1.0, so the project's edit is its own change.
        last = self.upgrade().splitlines()
        self.assertIn("installed 1.1.0, template 1.1.0", last[0])
        self.assertIn("CHANGED   .claude/skills/s/SKILL.md", last)

    def test_a_book_from_before_init_stamped_it_reads_the_copied_manifest_and_apply_adds_the_stamp(self):
        config = self.target / "devtools/factory/factory.toml"
        config.write_text(self.read("devtools/factory/factory.toml").replace('book_version = "1.0.0"\n', ""),
                          encoding="utf-8", newline="\n")
        self.assertIn("installed 1.0.0", self.upgrade().splitlines()[0])
        # With a conflict left, apply writes the installed version, as the copied manifest it came from moves on.
        self.upgrade(apply=True)
        import tomllib
        self.assertEqual(tomllib.loads(self.read("devtools/factory/factory.toml"))["book_version"], "1.0.0")
        self.write(self.target, ".claude/skills/s/SKILL.md", "# Acme skill, v2\n")
        self.upgrade(apply=True)
        self.assertEqual(tomllib.loads(self.read("devtools/factory/factory.toml"))["book_version"], "1.1.0")
        self.assertTrue(self.read("devtools/factory/factory.toml").startswith("# The Book's configuration.\n# The Book"))

    def test_the_installed_version_is_read_from_its_tag_never_a_branch_of_the_same_name(self):
        # A branch named like the release, moved on since the tag: git resolves a bare v1.0.0 to it (Book D58).
        git(self.template, "checkout", "-q", "-b", "v1.0.0", "v1.0.0")
        self.write(self.template, "devtools/checks/tool.py", "print('Widget tool, a moved branch')\n")
        git(self.template, "commit", "-q", "-am", "Move the branch")
        git(self.template, "checkout", "-q", "-")
        report = self.upgrade()
        self.assertIn("BEHIND    devtools/checks/tool.py", report.splitlines(), "compared with the tag, not the branch")
        self.assertNotIn("a moved branch", report)
        git(self.template, "tag", "-d", "v1.0.0")
        self.assertIn("the template repository has no tag v1.0.0", self.upgrade(), "the branch alone is no release")

    def test_without_the_installed_versions_tag_nothing_that_differs_is_written(self):
        git(self.template, "tag", "-d", "v1.0.0")
        report = self.upgrade(apply=True)
        self.assertIn("COULD NOT CHECK the installed version 1.0.0: the template repository has no tag v1.0.0", report)
        self.assertIn("UNKNOWN   devtools/checks/tool.py", report.splitlines())
        self.assertEqual(self.read("devtools/checks/tool.py"), "print('Acme tool, v1')\n", "kept, for a person")
        self.assertEqual(self.read(".claude/skills/s/SKILL.md"), "# Acme skill, v1, with our own step\n")
        self.assertTrue((self.target / "devtools/checks/new.py").is_file(), "a new file overwrites nothing")

    def test_a_rename_stub_is_offered_only_for_a_name_the_installed_version_had(self):
        stub = ("---\nname: {0}\ndescription: Renamed to /book:{0}, in the book plugin.\n"
                "disable-model-invocation: true\n---\n\n# /{0} was renamed\n")
        self.release("1.2.0", {".claude/skills/s/SKILL.md": stub.format("s"),
                               ".claude/skills/never-had/SKILL.md": stub.format("never-had"),
                               # A stub elsewhere (a pack's, in the template copy) is an ordinary file.
                               "devtools/factory/book-template/packs/p/.claude/skills/old/SKILL.md": stub.format("old")})
        report = self.upgrade()
        self.assertIn("CONFLICT  .claude/skills/s/SKILL.md", report.splitlines())
        self.assertNotIn(".claude/skills/never-had/SKILL.md", report)
        self.assertIn("NEW       devtools/factory/book-template/packs/p/.claude/skills/old/SKILL.md", report.splitlines())
        self.assertIn("1 rename stub(s) not offered: the installed version had none of these old names (never-had)",
                      report)
        self.upgrade(apply=True)
        self.assertFalse((self.target / ".claude/skills/never-had").exists())

    def test_a_file_differing_only_in_line_endings_is_current(self):
        (self.target / "AGENTS.md").write_bytes(b"Agents for Acme.\r\n")
        self.assertIn("CURRENT   AGENTS.md", self.upgrade().splitlines())

    def test_a_file_the_project_deleted_is_left_absent(self):
        (self.target / "devtools/checks/tool.py").unlink()
        report = self.upgrade(apply=True)
        self.assertIn("REMOVED   devtools/checks/tool.py", report.splitlines())
        self.assertFalse((self.target / "devtools/checks/tool.py").exists())
        import tomllib
        recorded = tomllib.loads(self.read("devtools/factory/factory.toml"))
        self.assertEqual(recorded["book_version"], "1.0.0", "a REMOVED file is left to a person, as a CONFLICT is")
        self.assertEqual(recorded["book"]["pending_merge"], [".claude/skills/s/SKILL.md", "devtools/checks/tool.py"])
        self.assertIn("REMOVED   devtools/checks/tool.py", self.upgrade().splitlines(), "still named on the next run")

    def test_the_plugin_pin_note_is_worked_out_after_apply_writes_the_settings(self):
        settings = ('{"enabledPlugins": {"book@book-plugins": true}, "extraKnownMarketplaces": {"book-plugins": '
                    '{"source": {"source": "github", "repo": "o/p", "ref": "v%s"}}}}\n')
        self.write(self.target, ".claude/skills/s/SKILL.md", "# Acme skill, v2\n")  # no conflict left
        self.release("1.1.1", {".claude/settings.json": settings % "0.1.0"}, plugin_min="0.1.0")
        self.upgrade(apply=True)
        self.assertIn('"ref": "v0.1.0"', self.read(".claude/settings.json"))
        self.release("1.2.0", {".claude/settings.json": settings % "0.2.0"}, plugin_min="0.2.0")
        stale = "pins the book plugin at v0.1.0, older than template 1.2.0's plugin_min 0.2.0"
        self.assertIn(stale, self.upgrade(), "the report names the old pin")
        applied = self.upgrade(apply=True)
        self.assertIn("BEHIND    .claude/settings.json", applied.splitlines())
        self.assertIn('"ref": "v0.2.0"', self.read(".claude/settings.json"))
        self.assertNotIn(stale, applied, "the same run moved the pin (Book D57)")

    def test_verify_names_the_files_left_to_merge(self):
        self.upgrade(apply=True)
        ctx = book.Context(self.target, book.load_target_config(self.target), self.manifest(), book.real_runner)
        lines = [line for line in book.n15_factory_config(ctx) if "pending_merge" in line.detail]
        self.assertEqual([line.status for line in lines], [book.TO_FILL])
        self.assertIn("1 template file(s) the upgrade to 1.1.0 left to merge by hand (.claude/skills/s/SKILL.md)",
                      lines[0].detail)

    def test_a_declined_file_is_never_written_and_is_nothing_for_a_person(self):
        # A project whose own script does a pack file's job declines it: not REMOVED, not created.
        (self.target / "devtools/checks/tool.py").unlink()
        config = self.target / "devtools/factory/factory.toml"
        config.write_text(self.read("devtools/factory/factory.toml").replace(
            "packs = []\n", 'packs = []\ndeclined = ["devtools/checks/tool.py", "devtools/checks/new.py", '
                            '"devtools/checks/gone.py"]\n'), encoding="utf-8", newline="\n")
        report = self.upgrade(apply=True)
        lines = report.splitlines()
        self.assertIn("DECLINED  devtools/checks/tool.py", lines)
        self.assertIn("DECLINED  devtools/checks/new.py", lines)
        self.assertFalse((self.target / "devtools/checks/tool.py").exists())
        self.assertFalse((self.target / "devtools/checks/new.py").exists())
        self.assertNotIn("devtools/checks/tool.py", report.split("For a person", 1)[-1] if "For a person" in report else "")
        self.assertIn("[book] declined names 1 file(s) the template does not own: devtools/checks/gone.py", report)

    def test_a_book_that_never_recorded_its_stack_and_packs_is_told_to(self):
        config = self.target / "devtools/factory/factory.toml"
        config.write_text(self.read("devtools/factory/factory.toml").replace('stack = "none"\npacks = []\n', ""),
                          encoding="utf-8", newline="\n")
        self.assertIn("[book] has no stack or packs: no pack file is compared", self.upgrade())

    def test_going_to_1_6_a_nested_claude_md_the_book_owned_is_split_never_dropped(self):
        # A dotnet Book's src/CLAUDE.md was the Book's at 1.5 and is the project's own (a skeleton) from 1.6:
        # deleting it, as DROPPED advises, would lose the project's rule and the import of the stack's rules.
        self.release("1.5.0", {"src/CLAUDE.md": "# Source\n\n- **Code goes in a layer.**\n"}, copy=("src/CLAUDE.md",))
        config = self.target / "devtools/factory/factory.toml"
        config.write_text(self.read("devtools/factory/factory.toml").replace('"1.0.0"', '"1.5.0"')
                          .replace('stack = "none"', 'stack = "dotnet"'), encoding="utf-8")
        self.write(self.target, "src/CLAUDE.md", "# Source\n\n- **Code goes in a layer.**\n- Our rule: `*Job`.\n")
        self.release("1.6.0", {".claude/book/rules.md": "# Rules\n",
                               ".claude/book/dotnet/src.md": "- **Code goes in a layer.**\n"},
                     copy=(".claude/book/**/*",))
        report = self.upgrade()
        self.assertIn("SPLIT     src/CLAUDE.md", report)
        self.assertNotIn("DROPPED   src/CLAUDE.md", report)
        self.assertIn("SPLIT    src/CLAUDE.md: 1 block(s) are the Book's", report)
        # What the move takes out of each file is shown in the report, before and after (Book D79).
        decide = report.split("Diffs to decide on", 1)[1]
        self.assertIn("# src/CLAUDE.md: the 1.6 move (SPLIT)", decide)
        self.assertIn("+@../.claude/book/dotnet/src.md", decide)
        self.assertIn("# devtools/factory/factory.toml: the 1.6 move (SPLIT)", decide)
        self.upgrade(apply=True)
        text = self.read("src/CLAUDE.md")
        self.assertIn("@../.claude/book/dotnet/src.md", text)
        self.assertIn("Our rule: `*Job`.", text)
        # Book D85: a re-run while a file waits to be merged has no move left to show.
        again = self.upgrade()
        self.assertIn("book_version stays 1.5.0", again)
        self.assertNotIn("SPLIT    devtools/factory/factory.toml", again)
        self.assertNotIn("the 1.6 move (SPLIT)", again)

    def test_refuses_to_go_back_or_to_upgrade_the_template_itself(self):
        config = self.target / "devtools/factory/factory.toml"
        config.write_text(self.read("devtools/factory/factory.toml").replace('"1.0.0"', '"9.0.0"'), encoding="utf-8")
        # A Book at 1.6 or later reads its core defaults file: without one its settings are refused first.
        self.write(self.target, "devtools/factory/book-defaults.toml", "")
        with self.assertRaises(book.BookError) as raised:
            self.upgrade()
        self.assertIn("never goes back", str(raised.exception))
        with self.assertRaises(book.BookError) as raised:
            book.upgrade(self.template, {}, self.manifest(), self.template)
        self.assertIn("cannot upgrade itself", str(raised.exception))

    def test_the_command_line_takes_upgrade_and_apply(self):
        import argparse
        parser = argparse.ArgumentParser()
        book.add_parser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["book", "upgrade", "--target", "x", "--apply"])
        self.assertEqual((args.step, args.target, args.apply), ("upgrade", "x", True))
        self.assertFalse(parser.parse_args(["book", "upgrade"]).apply, "the report is the default")


class PacksAndSources(unittest.TestCase):
    def ctx(self, index: str, config: dict) -> book.Context:
        folder = Path(tempfile.mkdtemp(prefix="book-packs-"))
        self.addCleanup(shutil.rmtree, folder, True)
        (folder / "requirements").mkdir()
        (folder / "requirements/read-me.txt").write_text(index, encoding="utf-8")
        return book.Context(folder, config, MANIFEST)

    def test_the_installed_packs_are_the_stack_and_the_packs(self):
        self.assertEqual(book.installed_packs({"book": {"stack": "none", "packs": []}}), [])
        self.assertEqual(book.installed_packs({"book": {"stack": "dotnet", "packs": ["deploy"]}}), ["dotnet", "deploy"])

    def test_an_fsd_delivered_as_a_file_needs_no_link_or_credential(self):
        ctx = self.ctx("=== LINKS ===\n\nFSD (specification document): none - delivered as a file\n", {})
        self.assertTrue(book.fsd_delivered_as_file(ctx))
        self.assertEqual([l.status for l in book.n18_private_settings(
            book.Context(ctx.root, {}, MANIFEST, private={"file": "absent", "keys": {}}))], [book.NA])
        # A private file kept for other settings (foreign_names) is not asked for a credential no source needs.
        present = book.Context(ctx.root, {}, MANIFEST,
                               private={"file": "present", "keys": {"[requirements.sources] live_credential": False}})
        self.assertEqual([l.status for l in book.n18_private_settings(present)], [book.OK])
        linked = self.ctx("=== LINKS ===\n\nFSD (specification document): <link>\n", {})
        self.assertFalse(book.fsd_delivered_as_file(linked))

    def test_fe_guides_are_not_applicable_without_their_pack(self):
        ctx = self.ctx("", {"book": {"stack": "none", "packs": []}})
        self.assertEqual(book.n12_fe_guides(ctx)[0].status, book.NA)
        ctx = self.ctx("", {"book": {"stack": "none", "packs": ["fe-guides"]}})
        self.assertEqual(book.n12_fe_guides(ctx)[0].status, book.MISSING)


def is_template_source() -> bool:
    """Whether this repository is the template's own (the boilerplate): `[book] name` is the manifest's
    `[source] name`. A Book installed from it carries the template too, but its own files carry its own
    names, so it cannot stand in as the source `init` copies from."""
    import tomllib
    with open(HERE / "factory.toml", "rb") as handle:
        name = (tomllib.load(handle).get("book") or {}).get("name")
    return name == MANIFEST["source"]["name"]


class TheRealTemplate(unittest.TestCase):
    def test_every_path_the_manifest_names_exists(self):
        root = HERE.parents[1]
        missing = [p for p in book.expand(root, MANIFEST["copy"]["book"]) if not (root / p).is_file()]
        self.assertEqual(missing, [])
        skeletons = dict(MANIFEST["skeletons"]["book"])
        for pack in MANIFEST["packs"].values():
            skeletons.update(pack.get("skeletons", {}))
        absent = [s for s in skeletons.values() if not (HERE / "book-template/skeletons" / s).is_file()]
        self.assertEqual(absent, [])

    # Names that belong to the projects the dotnet rules were first written in. Written in two halves so this
    # file does not itself carry them.
    OTHER_PROJECTS_NAMES = tuple(a + b for a, b in (
        ("Company", "Scoped"), ("Business", "Unit"), ("Shared", "StubUserHost"), ("Shared", "ProductCallerHost"),
        ("Session", "EndpointTests"), ("sister", " project"), ("not enforced", " yet"), ("QA_", "PLACEHOLDERS")))

    def test_the_dotnet_pack_names_no_other_projects_types_and_the_old_placeholder_home(self):
        # Book D94, Book D96 and Book D98: the shipped text says "for example" and names the kind of thing, and
        # points to settings; no rule, checklist or check names a type that only another project has.
        pack = HERE / "book-template/packs/dotnet"
        files = [p for p in pack.rglob("*") if p.is_file() and p.suffix in (".md", ".py", ".toml", ".sh")
                 and "__pycache__" not in p.parts and not p.name.startswith("test_")]
        self.assertTrue(files)
        for path in files:
            text = path.read_text(encoding="utf-8")
            for taken in self.OTHER_PROJECTS_NAMES:
                self.assertNotIn(taken, text, path.name)
        folder = pack / ".claude/book/dotnet"
        text = "\n".join((folder / name).read_text(encoding="utf-8") for name in ("src.md", "tests.md", "api.md"))
        self.assertIn("[deploy.compose_placeholders]", text)
        self.assertIn("where the project's check exists", text)
        self.assertIn("where the project keeps that check", text)
        security = " ".join((pack / ".claude/review/review-b4-security.md").read_text(encoding="utf-8").split())
        self.assertIn("the project's scope interface", security)

    def test_no_project_or_client_name_is_in_the_shipped_documents(self):
        # The repository is public: the ledgers, README and template name no project or client. The names are
        # written in halves so this file does not carry them.
        names = [("nb", "ryo"), ("cattly", "tics"), ("emb", "ryo"), (r"\b" + "iv", r"f\b"), ("fol", "io"),
                 ("aspir", "ation"), ("canis", "ter")]
        pattern = re.compile("|".join(a + b for a, b in names), re.IGNORECASE)
        root = HERE.parents[1]
        # This file is copied into every project, whose own docs name the project: only the Book's own
        # repository (slug "boilerplate") holds its docs and README to this; a project holds the template.
        slug = tomllib.loads((HERE / "factory.toml").read_text(encoding="utf-8")).get("book", {}).get("slug")
        folders = ("docs", "devtools/factory/book-template") if slug == "boilerplate" else ("devtools/factory/book-template",)
        files = [p for folder in folders for p in (root / folder).rglob("*")
                 if p.is_file() and p.suffix in (".md", ".toml", ".tmpl", ".yml", ".py", ".sh", ".txt")
                 and "__pycache__" not in p.parts] + ([root / "README.md"] if slug == "boilerplate" else [])
        hits = [f"{p.relative_to(root)}: {m.group(0)}" for p in files
                for m in [pattern.search(p.read_text(encoding="utf-8", errors="replace"))] if m]
        self.assertEqual(hits, [])

    @staticmethod
    def script_items(text: str) -> list[tuple[int, str]]:
        """The `- ` items under each `script:` key of a pipeline file, as (line number, text)."""
        found, floor = [], None
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            if floor is not None and indent <= floor:
                floor = None
            if floor is not None and stripped.startswith("- "):
                found.append((number, stripped[2:]))
            elif stripped == "script:":
                floor = indent
        return found

    def test_every_pipeline_script_item_is_one_string(self):
        # Book D99: an unquoted item holding a colon and a space is a one-key mapping, not a command.
        pipelines = [p for p in (HERE / "book-template").rglob("*") if p.suffix in (".yml", ".yaml")]
        self.assertTrue(pipelines)
        for path in pipelines:
            text = path.read_text(encoding="utf-8")
            items = self.script_items(text)
            self.assertTrue(items, path.name)
            for number, item in items:
                plain = not item.startswith(("'", '"', "|", ">"))
                self.assertFalse(plain and re.search(r":(\s|$)", item), f"{path.name}:{number}: {item}")
            try:
                import yaml
            except ImportError:
                continue
            def walk(node):
                if isinstance(node, dict):
                    for key, value in node.items():
                        if key == "script":
                            self.assertTrue(all(isinstance(i, str) for i in value), f"{path.name}: {value}")
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)
            walk(yaml.safe_load(text))

    def test_every_python_file_in_the_template_compiles(self):
        # The packs' self-tests run only in an installed Book, so a syntax error in one would pass here unseen.
        template = HERE / "book-template"
        broken = []
        for path in sorted(template.rglob("*.py")):
            try:
                compile(path.read_bytes(), str(path), "exec")
            except SyntaxError as error:
                broken.append(f"{path.relative_to(template).as_posix()}:{error.lineno}: {error.msg}")
        self.assertEqual([], broken)

    def test_every_pack_has_files_and_names_no_source_project(self):
        packs = HERE / "book-template" / "packs"
        self.assertEqual(sorted(p.name for p in packs.iterdir() if p.is_dir()), sorted(MANIFEST["packs"]))
        # The manifest names only the template itself (its name, prefix and title): other projects' names would
        # be published with it.
        source = MANIFEST["source"]
        own = {source["name"], source["title"], source["env_prefix"]}
        foreign = [n for n in source["foreign_names"] if n not in own and n.rstrip("._") not in own]
        self.assertEqual(foreign, [])
        for pack in MANIFEST["packs"]:
            files = [p for p in (packs / pack).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
            self.assertTrue(files, pack)
            for path in files:
                text = path.read_text(encoding="utf-8", errors="replace")
                self.assertEqual([n for n in foreign if re.search(book.name_pattern(n), text)], [], str(path))

    def test_no_link_in_a_fresh_book_dangles_for_any_pack_combination(self):
        # Book D102: the dotnet rules linked to the deploy pack's smoke-test doc, so a dotnet Book without that
        # pack failed check-book's Links at its first commit.
        root = HERE.parents[1]
        # `init` copies the shared documents (docs/plans/_template.md) from this repository, so a project's own
        # wording would be tested here, not the Book's: only the Book's own repository holds it to this (Book D105).
        if tomllib.loads((HERE / "factory.toml").read_text(encoding="utf-8")).get("book", {}).get("slug") != "boilerplate":
            return
        for stack, packs in (("dotnet", ["fe-guides"]), ("dotnet", []), ("dotnet", ["bitbucket"]),
                             ("dotnet", ["deploy"]), ("pytest", ["fe-guides"])):
            with self.subTest(stack=stack, packs=packs), tempfile.TemporaryDirectory(prefix="book-links-") as folder:
                target = Path(folder)
                git(target, "init", "-q")
                book.init(target, dict(PARAMS, stack=stack, packs=packs), MANIFEST, root, book.real_runner)
                git(target, "add", "-A")
                done = subprocess.run([sys.executable, str(target / "devtools/checks/check-book.py")], cwd=target,
                                      capture_output=True, text=True, encoding="utf-8", errors="replace")
                links = [l for l in (done.stdout + done.stderr).splitlines() if "does not exist" in l]
                self.assertEqual(links, [])

    def test_init_installs_each_stack_and_pack_from_the_real_template(self):
        root = HERE.parents[1]
        for stack, packs in (("none", []), ("dotnet", ["deploy", "bitbucket", "fe-guides"]), ("pytest", [])):
            with self.subTest(stack=stack, packs=packs), tempfile.TemporaryDirectory(prefix="book-pack-") as folder:
                target = Path(folder)
                git(target, "init", "-q")
                text, _ = book.init(target, dict(PARAMS, stack=stack, packs=packs), MANIFEST, root, book.real_runner)
                self.assertNotIn("MISSING", text)
                self.assertTrue((target / "devtools/checks/check-book.py").is_file())
                self.assertEqual((target / "src/Acme.API/CLAUDE.md").is_file(), stack == "dotnet")
                self.assertEqual((target / "docs/fe-integration-guides/api-conventions.md").is_file(),
                                 "fe-guides" in packs)
                self.assertFalse((target / ".claude/skills").exists(), "from v1.5 the template owns no skill, nor stub")
                config = (target / "devtools/factory/factory.toml").read_text(encoding="utf-8")
                merged = book_config.load(target)
                rule_ids = [rule["id"] for rule in merged.get("code_rules", [])]
                self.assertIn("data-script-ddl", rule_ids, "every stack keeps the core rule")
                self.assertEqual("anonymous-route" in rule_ids, stack == "dotnet")
                self.assertEqual("dotnet_checks" in merged, stack == "dotnet")
                # The project's file keeps its own settings; the shared ones are the defaults it was given.
                self.assertNotRegex(config, r"(?m)^\[\[code_rules\]\]")
                self.assertTrue((target / "devtools/factory/book-defaults.toml").is_file())
                self.assertEqual((target / "devtools/factory/book-defaults/dotnet.toml").is_file(), stack == "dotnet")
                # The deploy pack's table names the project's own variables, and leaves only `<…>` to fill.
                self.assertEqual('test_db_variable = "ACME_TEST_DB"' in config, "deploy" in packs)
                self.assertNotIn("{{", config)
                # The Book records what it installed, so verify and the pack checks see it (Book D30).
                self.assertEqual(book.installed_packs(tomllib.loads(config)),
                                 ([stack] if stack != "none" else []) + packs)
                claude = (target / "CLAUDE.md").read_text(encoding="utf-8")
                # The Book's rules are imported, not copied: the project's CLAUDE.md holds its own only.
                self.assertIn("\n@.claude/book/rules.md\n", claude)
                self.assertNotIn("Never commit a real secret", claude)
                self.assertEqual("@.claude/book/fe-guides.md" in claude, "fe-guides" in packs, "a pack's rule comes with it")
                read = book_config.expanded(target, "CLAUDE.md")
                self.assertIn("Never commit a real secret", read)
                self.assertEqual("check-fe-guides.py" in read, "fe-guides" in packs)
                self.assertNotIn("{{", claude)
                for nested in ("docs/CLAUDE.md", "tests/CLAUDE.md") + (
                        ("src/CLAUDE.md", "src/Acme.API/CLAUDE.md", "src/Acme.Persistence/CLAUDE.md")
                        if stack == "dotnet" else ()):
                    self.assertEqual(book_config.missing_imports(target, nested), [], nested)
                    self.assertGreater(len(book_config.expanded(target, nested)),
                                       2 * len((target / nested).read_text(encoding="utf-8")), nested)
                # Every marker names a check the install ships (decision Q): a new Book's rules hold on day one,
                # as init left them - every file staged, the hooks executable, the table written.
                rules = subprocess.run([sys.executable, "devtools/checks/check-rules.py", "--check"], cwd=target,
                                       capture_output=True, text=True)
                self.assertEqual(rules.returncode, 0, rules.stdout + rules.stderr)
                self.assertIn("enforced rules intact", rules.stdout)
                self.assertNotIn("broken", rules.stdout.lower())
                unstaged = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "--modified"], cwd=target,
                                          capture_output=True, text=True).stdout.split()
                self.assertEqual([p for p in unstaged if p != "book.toml"], [], "init stages all it writes")
                mode = subprocess.run(["git", "ls-files", "-s", ".githooks/pre-commit"], cwd=target,
                                      capture_output=True, text=True).stdout
                self.assertTrue(mode.startswith("100755"), mode)
                # Every link resolves and every document has its owner's row: the pack rows are written in.
                linked = subprocess.run([sys.executable, "devtools/checks/check-book.py"], cwd=target,
                                        capture_output=True, text=True)
                self.assertEqual(linked.returncode, 0, linked.stdout + linked.stderr)
                self.assertReviewChecklistIsWhole(target, merged)
                # What upgrade compares (owned_files) is what init wrote, at the same path: CURRENT, never REMOVED.
                # A verbatim template file keeps its path too (packs/dotnet/src/Boilerplate.API/ is not renamed).
                owned = book.owned_files(root, MANIFEST, dict(PARAMS), book.installed_packs({"book": dict(PARAMS, stack=stack, packs=packs)}))
                drifted = [rel for rel, data in owned.items()
                           if not (target / rel).is_file() or (target / rel).read_bytes() != data]
                self.assertEqual(drifted, [])
                self.assertIn(".claude/book/rules.md", owned, "the Book's rules are the Book's, replaced on upgrade")
                self.assertNotIn("CLAUDE.md", owned)
                if stack == "dotnet":
                    self.assertIn(".claude/book/dotnet/api.md", owned)
                    self.assertNotIn("src/Acme.API/CLAUDE.md", owned, "a nested CLAUDE.md is the project's own")
                # The guide template is the template's, as the plan template is: upgrade offers it (Book D44).
                self.assertEqual("docs/fe-integration-guides/_template.md" in owned, "fe-guides" in packs)
                self.assertNotIn("docs/ui-standard.md", owned, "a skeleton's target is the project's own")
                for path in target.rglob("*"):
                    if path.is_file() and ".git" not in path.parts and "book-template" not in path.parts \
                            and path.name not in ("book.py", "test_book.py") and path.suffix in (".md", ".py", ".sh", ".mdc", ".yml"):
                        text = path.read_text(encoding="utf-8", errors="replace")
                        self.assertNotRegex(text, r"\bBoilerplate\b|BOILERPLATE_", str(path))
                        # The slug renames the template's own slug, so the English word becomes it in prose.
                        self.assertNotRegex(text, r"(?<![\w./`'-])acme(?![\w./`-])", str(path))
                        # Nor inside an identifier (a database name): a slug may hold a hyphen.
                        self.assertNotRegex(text, r"(?<![\w-])acme_\w", str(path))

    def assertReviewChecklistIsWhole(self, target: Path, config: dict) -> None:
        """One file per review dimension [review_scope] names, each check in exactly one of them."""
        scope = config["review_scope"]
        folder = target / scope["folder"]
        # A `book:` file is the plugin's; only the project's own checklist files are on disk.
        names = [n for n in scope["always"] + list(scope["dimensions"].values()) if not n.startswith("book:")]
        self.assertEqual([n for n in names if not (folder / n).is_file()], [], "a file [review_scope] names")
        on_disk = sorted(p.name for p in folder.glob("review-*.md"))
        self.assertEqual(on_disk, sorted(names), "no checklist file [review_scope] leaves out")
        rows = [int(n) for name in names for n in re.findall(r"^\| (\d+) \|", (folder / name).read_text(encoding="utf-8"), re.M)]
        self.assertEqual(sorted({n for n in rows if rows.count(n) > 1}), [], "a check in two files")

    def test_the_skeletons_parse_once_filled(self):
        import tomllib
        text = (HERE / "book-template/skeletons/factory.toml.tmpl").read_text(encoding="utf-8")
        filled = tomllib.loads(book.fill_skeleton(text, dict(PARAMS)))
        self.assertEqual(filled["fe_guides"]["drive_folder_id"], "<FILL-FE-DRIVE-FOLDER-ID>", "never a guessed folder")
        filled = tomllib.loads(book.fill_skeleton(text, dict(PARAMS, owners=[{"name": "A", "account_id": "1"}],
                                                             fe_drive_folder_id="F9")))
        self.assertEqual(filled["fe_guides"]["drive_folder_id"], "F9")


if not is_template_source():
    # These two install from this repository as the source; only the boilerplate's own files can be.
    del TheRealTemplate.test_every_path_the_manifest_names_exists
    del TheRealTemplate.test_init_installs_each_stack_and_pack_from_the_real_template


if __name__ == "__main__":
    unittest.main()
