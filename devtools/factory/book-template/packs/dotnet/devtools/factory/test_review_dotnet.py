"""
The dotnet pack's review checklist: its dimension files (review-b1…b11 in `.claude/review/`) hold checks
16–161 once each, each file its range (161 in B2, as in the plugin's B0), and `book review-scope` reads the triage table. The method and
Pass A (checks 1–15, the triage table, the one READY rule) are the book plugin's, tested there.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["APP_LOCAL_SETTINGS"] = str(Path(tempfile.gettempdir()) / "factory-tests-no-private-settings"
                                         / "local.toml")
os.environ["BOILERPLATE_LOCAL_SETTINGS"] = os.environ["APP_LOCAL_SETTINGS"]
for name in [name for name in os.environ if name.startswith("BITBUCKET_")]:
    del os.environ[name]
os.environ.update({"GIT_CONFIG_COUNT": "2",
                   "GIT_CONFIG_KEY_0": "maintenance.auto", "GIT_CONFIG_VALUE_0": "false",
                   "GIT_CONFIG_KEY_1": "gc.auto", "GIT_CONFIG_VALUE_1": "0"})

import factory  # noqa: E402

REAL_ROOT = HERE.parents[1]
import fixture_project  # noqa: E402 — a full project's factory.toml
CONFIG = factory.Config(fixture_project.PROJECT)


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return done.stdout.strip()


SCOPE = CONFIG.raw["review_scope"]
FOLDER = REAL_ROOT / SCOPE["folder"]
ALWAYS = list(SCOPE["always"])  # the plugin's method and Pass A, named `book:<skill>/<path>`


def pass_b(*dimensions: str) -> list[str]:
    return [f"{SCOPE['folder']}/{SCOPE['dimensions'][d]}" for d in dimensions]


EVERY_B = pass_b(*SCOPE["dimensions"])

# The plugin's triage table (book:peer-review/references/review-method.md), row by row; the plugin's tests
# pin the same list, so a row changed in one repository fails here until the other follows.
TRIAGE_ROWS = [
    "Product code under `src/`", "An endpoint, request/response DTO or permission", "A migration", "Tests only",
    "Docs only (`docs/`, other `*.md`)", "Tooling (`devtools/`, `.claude/`, `.githooks/`, CI)",
    "A `CLAUDE.md` (root or nested)", "`requirements/**`", "`database-scripts/`, `scripts/`",
    "Root build and deploy config \u2014 `Directory.Packages.props`, `compose*.yaml`, `Dockerfile`, "
    "`appsettings*.json`, `bitbucket-pipelines.yml`",
    "Generated files \u2014 `docs/api/api-contracts.md`, `docs/database-erd.md`, a migration's `.Designer.cs`, "
    "the model snapshot",
]


class ReviewScopeTests(unittest.TestCase):
    """
    The one-file checklist's safeguards, each with its test:

    C1  every check 1–161 is still there, once ................ ChecklistSplitTests
    C2  the method and Pass A load on every review ............ test_the_method_and_pass_a_always_load
    C3  a path the triage cannot place loads all of Pass B .... test_an_unknown_path_loads_everything
    C4  a path matching several rows presses every row's
        dimensions; N/A only when every row says so ........... test_several_rows_press_every_rows_dimensions
    C5  the coverage table still has all 12 rows .............. test_what_is_not_loaded_is_named_for_the_coverage_table
    C6  the tool's rows are the plugin's triage table ......... test_the_rows_are_the_triage_table
    C7  a rename counts under both names ...................... test_a_rename_counts_under_both_names
    """

    def scope(self, *paths: str) -> dict:
        return factory.review_scope(CONFIG, list(paths))

    def test_the_method_and_pass_a_always_load(self):
        for paths in ((), ("docs/defects.md",), ("requirements/fsd/x.pdf",), ("src/App.Domain/A.cs",)):
            with self.subTest(paths):
                self.assertEqual(self.scope(*paths)["files"][:2], ALWAYS)

    def test_a_docs_only_branch_loads_no_pass_b(self):
        report = self.scope("docs/defects.md", "docs/plans/authorization.md", "README.md",
                            "docs/api/api-contracts.md")
        self.assertEqual(report["files"], ALWAYS)
        self.assertEqual(report["unplaced"], [])

    def test_a_tooling_branch_leaves_out_the_dotnet_dimensions(self):
        # The Tooling row marks B1, B3 and B5 N/A; B6 stays, since the guard runs on every tool call.
        expected = ALWAYS + pass_b("B2", "B4", "B6", "B7", "B8", "B9", "B10", "B11")
        for path in ("devtools/factory/factory.py", ".claude/skills/check-guides/SKILL.md", ".githooks/pre-commit"):
            with self.subTest(path):
                report = self.scope(path)
                self.assertEqual(report["files"], expected)
                self.assertEqual(sorted(report["left_out"]), ["B1", "B3", "B5"])

    def test_the_pipeline_file_still_loads_everything(self):
        # bitbucket-pipelines.yml is Tooling and root build config too; that row marks nothing N/A.
        self.assertEqual(self.scope("bitbucket-pipelines.yml")["files"], ALWAYS + EVERY_B)

    def test_tooling_beside_product_code_loads_everything(self):
        self.assertEqual(self.scope("devtools/factory/factory.py", "src/App.Domain/A.cs")["files"],
                         ALWAYS + EVERY_B)

    def test_a_code_branch_touching_tenancy_and_auth_loads_every_relevant_dimension(self):
        report = self.scope("src/App.Persistence/Tenancy/TenantQueryFilter.cs",
                            "src/App.API/Authorization/PermissionChecker.cs",
                            "tests/App.IntegrationTests/Features/Tenancy/IsolationTests.cs")
        self.assertEqual(report["files"], ALWAYS + EVERY_B)
        for needed in ("B4", "B5", "B9"):
            self.assertIn(pass_b(needed)[0], report["files"])

    def test_tests_only_leave_out_what_the_table_marks_na(self):
        report = self.scope("tests/App.UnitTests/Queries/EventSearchDateTests.cs")
        self.assertEqual(report["files"], ALWAYS + pass_b("B9", "B10", "B11"))

    def test_an_unknown_path_loads_everything(self):
        for path in ("App.slnx", ".editorconfig", "global.json", ".cursor/rules/x.mdc"):
            with self.subTest(path):
                report = self.scope("docs/defects.md", path)
                self.assertEqual(report["files"], ALWAYS + EVERY_B)
                self.assertEqual(report["unplaced"], [path])

    def test_several_rows_press_every_rows_dimensions(self):
        # A unit test (B1–B8 N/A) beside a doc (all N/A): B9–B11 still load, from the test's row.
        self.assertEqual(self.scope("tests/A.cs", "docs/a.md")["files"], ALWAYS + pass_b("B9", "B10", "B11"))
        # A CLAUDE.md is a *.md (docs: all N/A) and its own row (none N/A): its row wins.
        self.assertEqual(self.scope("docs/CLAUDE.md")["files"], ALWAYS + EVERY_B)

    def test_what_is_not_loaded_is_named_for_the_coverage_table(self):
        report = self.scope("tests/A.cs")
        self.assertEqual(sorted(report["left_out"]), sorted(["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8"]))
        text = factory.render_review_scope(report)
        self.assertIn("still a row in the coverage table", text)
        loaded = sum(1 for d in SCOPE["dimensions"] if pass_b(d)[0] in report["files"])
        self.assertEqual(loaded + len(report["left_out"]), 11, "Pass A plus 11 Pass B rows: all 12 dimensions")

    def test_the_rows_are_the_triage_table(self):
        self.assertEqual([row["row"] for row in SCOPE["rows"]], TRIAGE_ROWS)

    def test_every_file_it_names_exists(self):
        for name in SCOPE["dimensions"].values():
            self.assertTrue((FOLDER / name).is_file(), name)
        self.assertTrue(all(name.startswith(factory.PLUGIN_PREFIX) for name in SCOPE["always"]),
                        "the method and Pass A are the plugin's")

    def test_a_rename_counts_under_both_names(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "scripts").mkdir()
            (root / "scripts" / "backup-database.sh").write_text("#!/bin/sh\necho backup\n" * 20, encoding="utf-8")
            git(root, "init", "-q", "-b", "dev")
            git(root, "config", "user.name", "Test Person")
            git(root, "config", "user.email", "person@example.invalid")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "Base")
            (root / "docs").mkdir()
            git(root, "mv", "scripts/backup-database.sh", "docs/backup-database.md")
            git(root, "commit", "-q", "-m", "Move it")
            args = factory.parser().parse_args(["review-scope", "--range", "HEAD~1...HEAD"])
            text = factory.command_review_scope(root, CONFIG, args)
        self.assertIn("database-scripts/`, `scripts/` (1)", text, "the path it left is a scripts change")
        self.assertIn(pass_b("B5")[0], text)


class ChecklistSplitTests(unittest.TestCase):
    """C1: every numbered check 16–161 appears exactly once across the pack's dimension files, and each
    holds exactly the range its heading names. Pass A's 1–15 are the plugin's."""

    ROW = re.compile(r"^\| (\d+) \|", re.M)

    def files(self) -> dict[str, str]:
        return {path.name: path.read_text(encoding="utf-8") for path in sorted(FOLDER.glob("review-*.md"))}

    def test_every_check_appears_exactly_once(self):
        rows = [int(n) for text in self.files().values() for n in self.ROW.findall(text)]
        duplicated = sorted({n for n in rows if rows.count(n) > 1})
        self.assertEqual(duplicated, [], "a check in two places")
        self.assertEqual(sorted(set(rows)), list(range(16, 162)))

    def test_each_dimension_file_holds_its_named_range(self):
        checked = 0
        for name, text in self.files().items():
            match = re.match(r"# B\d+ · .* — checks (\d+)–(\d+)((?:, \d+)*)\n", text)
            if not match:
                continue
            checked += 1
            with self.subTest(name):
                low, high = int(match.group(1)), int(match.group(2))
                # A check added after the numbering was laid out (161, Code the requirement did not ask for)
                # follows its dimension's range, as the plugin's B0 lists it.
                extra = [int(n) for n in re.findall(r"\d+", match.group(3))]
                self.assertEqual([int(n) for n in self.ROW.findall(text)], list(range(low, high + 1)) + extra)
        self.assertEqual(checked, 11)

    def test_no_dimension_file_restates_the_ready_rule(self):
        # The plugin's review-method.md states it, once.
        others = [name for name, text in self.files().items() if "NOT READY" in text]
        self.assertEqual(others, [], "the READY rule is stated once, in the method")


if __name__ == "__main__":
    unittest.main()
