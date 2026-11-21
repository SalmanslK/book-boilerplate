"""
Self-tests for factory.py, the AI tools. CI runs them beside the guard's:

    python3 -m unittest discover -s devtools/factory -p "test_*.py" -v

The pull-request tests run through a throwaway git repository with Bitbucket replaced. Needs git on PATH.
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
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# A test never reads whoever runs it's private settings: every machine tests the same configuration.
# In a folder of its own: the guard holds the settings file's whole folder private, and the temp folder
# itself is where the tests write a PR's text.
os.environ["APP_LOCAL_SETTINGS"] = str(Path(tempfile.gettempdir()) / "factory-tests-no-private-settings"
                                         / "local.toml")
# The same for this repository's own variable, where a test reads the real factory.toml.
os.environ["BOILERPLATE_LOCAL_SETTINGS"] = os.environ["APP_LOCAL_SETTINGS"]
# Nor the pipeline it runs in: in CI, BITBUCKET_COMMIT and BITBUCKET_PR_DESTINATION_BRANCH name the real
# pull request, and the tests' own PR would be judged against them. A test that wants one sets it.
for name in [name for name in os.environ if name.startswith("BITBUCKET_")]:
    del os.environ[name]
# No automatic maintenance: newer git starts it in the background after a merge or commit, and it can
# still be writing to .git/objects while a test deletes its throwaway repository.
os.environ.update({"GIT_CONFIG_COUNT": "2",
                   "GIT_CONFIG_KEY_0": "maintenance.auto", "GIT_CONFIG_VALUE_0": "false",
                   "GIT_CONFIG_KEY_1": "gc.auto", "GIT_CONFIG_VALUE_1": "0"})

import factory  # noqa: E402
import snapshots  # noqa: E402

REAL_ROOT = HERE.parents[1]
import fixture_project  # noqa: E402 — a full project's factory.toml
CONFIG = factory.Config(fixture_project.PROJECT)
# A repository holding only the fixture project's factory.toml, for a command that reads the repository's own.
FIXTURE_ROOT = Path(tempfile.mkdtemp(prefix="factory-fixture-project-"))
(FIXTURE_ROOT / factory.CONFIG_RELATIVE).parent.mkdir(parents=True)
(FIXTURE_ROOT / factory.CONFIG_RELATIVE).write_text(fixture_project.PROJECT_TOML, encoding="utf-8")


class ImpactTests(unittest.TestCase):
    def test_paths_map_to_their_modules(self):
        report = factory.impact(REAL_ROOT, CONFIG, "HEAD", [
            "src/App.Application/Features/Companies/CompanyFactory.cs",
            "src/App.API/Endpoints/Geography/GeographyEndpoints.cs",
            "src/App.API/Endpoints/Roles/RoleEndpoints.cs",
        ])
        self.assertEqual([m["name"] for m in report["modules"]], ["Companies", "Geography", "Roles"])
        self.assertEqual(report["cross_cutting"], [])
        self.assertTrue(report["regenerate"])

    def test_a_route_file_anywhere_in_the_api_project_regenerates_the_contracts(self):
        # The generator reads every C# file of the API project, so a route under Configuration/ counts too.
        contracts = "docs/api/api-contracts.md — python3 devtools/checks/gen-api-contracts.py"
        for path in ("src/App.API/Configuration/PageEndpoints.cs", "src/App.API/Endpoints/Roles/RoleEndpoints.cs",
                     "src/App.Application/Features/Roles/Dtos/RoleDto.cs"):
            with self.subTest(path):
                self.assertIn(contracts, factory.impact(REAL_ROOT, CONFIG, "HEAD", [path])["regenerate"])
        for path in ("src/App.API/appsettings.json", "tests/App.API.Tests/RouteTests.cs",
                     "src/App.Application/Features/Roles/RoleService.cs"):
            with self.subTest(path):
                self.assertNotIn(contracts, factory.impact(REAL_ROOT, CONFIG, "HEAD", [path])["regenerate"])

    def test_an_unowned_source_path_is_cross_cutting(self):
        report = factory.impact(REAL_ROOT, CONFIG, "HEAD", ["src/App.Persistence/ApplicationDbContext.cs"])
        self.assertEqual(report["cross_cutting"], ["src/App.Persistence/ApplicationDbContext.cs"])

    def test_packages_and_shared_fixtures_are_cross_cutting(self):
        # CLAUDE.md names both as full-suite changes, so impact must report them as cross-cutting.
        paths = ["Directory.Packages.props", "tests/App.IntegrationTests/Fixtures/ApiFactory.cs",
                 "tests/App.IntegrationTests/Features/Companies/CompanyReadRepositoryTests.cs", "README.md"]
        report = factory.impact(REAL_ROOT, CONFIG, "HEAD", paths)
        self.assertEqual(report["cross_cutting"], paths[:2])

    def ci(self, *paths):
        return factory.ci_tier(REAL_ROOT, CONFIG, "HEAD", list(paths))

    def test_docs_and_devtools_are_the_checks_tier(self):
        result = self.ci("docs/defects.md", "devtools/checks/check-book.py", "README.md")
        self.assertEqual((result["tier"], result["filter"]), ("checks", ""))

    def test_a_path_outside_the_checks_allow_list_is_at_least_the_build_tier(self):
        # PR #335: anything not on the allow-list landed in `checks`, with no build and no tests.
        for path in ("bitbucket-pipelines.yml", "scripts/backup-database.sh", "NuGet.config"):
            with self.subTest(path):
                result = self.ci("docs/defects.md", path)
                self.assertEqual(result["tier"], "build")
                self.assertIn(path, result["reason"])

    def test_seed_scripts_and_editorconfig_are_full(self):
        # The integration tests run the seed scripts; .editorconfig decides which warnings fail the build.
        for path in ("database-scripts/seed/01-reference-data.sql", ".editorconfig", "src/.editorconfig"):
            with self.subTest(path):
                self.assertEqual(self.ci(path)["tier"], "full")

    def test_a_unit_test_alone_is_the_build_tier(self):
        result = self.ci("tests/App.UnitTests/Queries/EventSearchDateTests.cs")
        self.assertEqual((result["tier"], result["filter"]), ("build", ""))

    def test_one_modules_paths_run_only_that_module(self):
        result = self.ci("src/App.Application/Features/Companies/CompanyFactory.cs",
                         "tests/App.IntegrationTests/Features/Companies/CompanyReadRepositoryTests.cs")
        self.assertEqual(result["tier"], "modules")
        self.assertEqual(result["modules"], ["Companies"])
        self.assertEqual(result["filter"], "Category!=Smoke&Category!=S3&((FullyQualifiedName~Features.Companies))")

    def test_more_than_one_module_is_full(self):
        # CLAUDE.md, Building and testing: a change to more than one module's behaviour runs the full suite.
        result = self.ci("src/App.Application/Features/Companies/CompanyFactory.cs",
                         "src/App.Application/Features/Geography/Queries/GeographyLookups.cs")
        self.assertEqual((result["tier"], result["filter"]), ("full", "Category!=Smoke&Category!=S3"))
        self.assertIn("Companies, Geography", result["reason"])

    def test_a_modules_own_integration_tests_stay_in_that_module(self):
        result = self.ci("tests/App.IntegrationTests/Features/Geography/GeographyReadRepositoryTests.cs")
        self.assertEqual((result["tier"], result["modules"]), ("modules", ["Geography"]))

    def test_the_store_order_event_and_activity_log_modules_run_their_own_tests(self):
        # A change under Features/Stores alone used to land on `build`, so the store
        # integration tests never ran before the pull request.
        for path, module, tests in (
            ("src/App.Application/Features/Stores/Commands/CreateStore/CreateStoreCommand.cs", "Stores", "Features.Stores"),
            ("src/App.API/Endpoints/OrderEvents/OrderEventEndpoints.cs", "Order events", "Features.OrderEvents"),
            ("src/App.Domain/Features/ActivityLog/ActivityEntry.cs", "Activity log", "Features.ActivityLog"),
        ):
            with self.subTest(module):
                result = self.ci(path)
                self.assertEqual((result["tier"], result["modules"]), ("modules", [module]))
                self.assertIn(tests, result["filter"])

    def test_a_memberships_change_is_full_like_the_users_module_it_replaced(self):
        self.assertEqual(
            self.ci("src/App.Application/Features/Memberships/Commands/InviteMember/InviteMemberCommand.cs")["tier"],
            "full")

    def test_an_unowned_source_path_is_full(self):
        self.assertEqual(self.ci("src/App.Persistence/ApplicationDbContext.cs")["tier"], "full")

    def test_a_high_tier_module_is_full(self):
        self.assertEqual(self.ci("src/App.Application/Features/Users/Commands/InviteUser/InviteUserCommand.cs")["tier"],
                         "full")

    def test_an_unowned_integration_test_is_full(self):
        self.assertEqual(self.ci("tests/App.IntegrationTests/ForwardedHeadersTests.cs")["tier"], "full")

    def test_full_runs_everything_but_smoke(self):
        result = self.ci("Directory.Packages.props", "docs/defects.md")
        self.assertEqual((result["tier"], result["filter"]), ("full", "Category!=Smoke&Category!=S3"))

    def test_block_returns_the_blocks_whose_heading_names_the_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "docs" / "Audit").mkdir(parents=True)
            (root / "docs" / "Audit" / "spec-traceability.md").write_text(
                "# Matrix\n\n## Auditing — FSD Activity log\n| FSD-9-R01 | src/ActivityLog/Entry.cs |\n\n"
                "## Authentication — FSD Log in\n| FSD-1-R01 | src/Auth/Login.cs |\n", encoding="utf-8")
            text = factory.traceability_blocks(root, CONFIG, "activity log")
        self.assertIn("block(s) whose heading names", text)
        self.assertIn("## Auditing", text)
        self.assertNotIn("## Authentication", text)

    def test_block_falls_back_to_matching_rows_under_their_heading(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "docs" / "Audit").mkdir(parents=True)
            (root / "docs" / "Audit" / "spec-traceability.md").write_text(
                "# Matrix\n\n## Stores — FSD Add Store\n| FSD-3-R01 | src/Stores/AddStore.cs |\n| FSD-3-R02 | other |\n\n"
                "## Users — FSD Invite User\n| FSD-2-R01 | src/Users/Invite.cs |\n", encoding="utf-8")
            text = factory.traceability_blocks(root, CONFIG, "src/stores/addstore.cs")
            self.assertIn("## Stores — FSD Add Store\n| FSD-3-R01 | src/Stores/AddStore.cs |", text)
            self.assertNotIn("FSD-3-R02", text)
            self.assertNotIn("## Users", text)
            self.assertIn("no block names", factory.traceability_blocks(root, CONFIG, "Customer Search"))

    def test_block_without_a_matrix_fails_rather_than_reading_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(factory.FactoryError):
                factory.traceability_blocks(Path(folder), CONFIG, "anything")

    def test_impact_ci_prints_one_key_per_line(self):
        out = factory.render_ci_tier(self.ci("src/App.API/Endpoints/Geography/GeographyEndpoints.cs"))
        self.assertEqual(out.splitlines(), ["tier=modules", "filter=Category!=Smoke&Category!=S3&((FullyQualifiedName~Features.Geography))",
                                            "reason=Geography"])

    def test_every_modules_guide_and_plan_exists(self):
        for module in factory.load_config(REAL_ROOT).modules:
            for key in ("guide", "plan"):
                if module.get(key):
                    with self.subTest(module=module["name"], key=key):
                        self.assertTrue((REAL_ROOT / module[key]).exists(), module[key])


class GlobTests(unittest.TestCase):
    """Globs fold case on every OS — fnmatch.fnmatch does only on Windows, and CI runs on Linux."""

    def ai_file(self, path: str) -> bool:
        settings = CONFIG.raw["ai_files"]
        return (any(factory.glob_match(path, glob) for glob in settings.get("paths", []))
                and not any(factory.glob_match(path, glob) for glob in settings.get("except", [])))

    def test_an_ai_file_in_another_case_is_still_an_ai_file(self):
        for path in (".Claude/settings.local.json", "src/X/claude.md", "Claude.md", "DevTools/Factory/guard.py"):
            with self.subTest(path):
                self.assertTrue(self.ai_file(path))
        self.assertFalse(self.ai_file("docs/plans/business-units.md"))

    def test_case_folds_whatever_fnmatch_does_on_this_os(self):
        with mock.patch.object(factory.fnmatch, "fnmatch", side_effect=AssertionError("os-dependent")):
            self.assertTrue(factory.glob_match(".Claude/settings.local.json", ".claude/**"))
            self.assertTrue(factory.glob_match("src/X/claude.md", "**/CLAUDE.md"))
            self.assertTrue(factory.glob_match("Tests/test_pricing.py", "tests/**/*.py"))
            self.assertFalse(factory.glob_match("src/X/claude.txt", "**/CLAUDE.md"))


class PrivateSettingsTests(unittest.TestCase):
    """A person's settings come from their private file only, and only the documented keys."""

    def test_only_the_documented_keys_come_from_the_private_settings_file(self):
        with tempfile.TemporaryDirectory() as folder:
            private = Path(folder) / "local.toml"
            private.write_text('protected_branches = ["nothing"]\n[requirements.sources]\n'
                               'live_credential = "rclone:mine"\nfsd_dir = "elsewhere"\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {"APP_LOCAL_SETTINGS": str(private)}):
                config = factory.load_config(FIXTURE_ROOT)
        sources = config.raw["requirements"]["sources"]
        self.assertEqual(sources["live_credential"], "rclone:mine")
        self.assertEqual(sources["fsd_dir"], CONFIG.raw["requirements"]["sources"]["fsd_dir"], "not a private key")
        self.assertEqual(config.raw["protected_branches"], CONFIG.raw["protected_branches"])

    def test_other_projects_names_come_from_the_private_file_apart_from_the_projects_own(self):
        with tempfile.TemporaryDirectory() as folder:
            private = Path(folder) / "local.toml"
            private.write_text('[book]\nname = "NotOurs"\nforeign_names = ["Gizmo", "GIZMO_"]\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {"APP_LOCAL_SETTINGS": str(private)}):
                config = factory.load_config(FIXTURE_ROOT)
        self.assertEqual(config.raw["book"][factory.PRIVATE_FOREIGN_NAMES], ["Gizmo", "GIZMO_"])
        self.assertEqual(config.raw["book"]["name"], CONFIG.raw["book"]["name"], "the project's own name stays its own")
        self.assertNotIn("foreign_names", config.raw["book"])

    def test_a_broken_private_file_is_named_never_quoted(self):
        with tempfile.TemporaryDirectory() as folder:
            private = Path(folder) / "local.toml"
            private.write_text('live_credential = "rclone:secret-remote', encoding="utf-8")
            with mock.patch.dict(os.environ, {"APP_LOCAL_SETTINGS": str(private)}):
                with self.assertRaises(factory.FactoryError) as raised:
                    factory.load_config(FIXTURE_ROOT)
        self.assertNotIn("secret-remote", str(raised.exception))


class ConfigValueTests(unittest.TestCase):
    """`factory config <table.key>`: one value from factory.toml, never from the private settings."""

    def run_config(self, key: str, private_text: str | None = None) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as folder:
            private = Path(folder) / "local.toml"
            if private_text is not None:
                private.write_text(private_text, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"APP_LOCAL_SETTINGS": str(private)}), \
                    mock.patch.object(factory, "repo_root", return_value=FIXTURE_ROOT), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = factory.main(["config", key])
        return code, out.getvalue(), err.getvalue()

    def test_the_drive_folder_id_comes_from_factory_toml(self):
        code, out, _ = self.run_config("fe_guides.drive_folder_id")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), CONFIG.raw["fe_guides"]["drive_folder_id"])
        self.assertRegex(out.strip(), r"^[\w-]+$", "what Publish-FeGuidesToDrive.ps1 accepts")

    def test_a_private_value_is_never_printed(self):
        code, out, err = self.run_config("requirements.sources.live_credential",
                                         '[requirements.sources]\nlive_credential = "dummy-private-3Zk"\n')
        self.assertEqual(code, 1)
        self.assertNotIn("dummy-private-3Zk", out + err)

    def test_a_broken_private_file_does_not_stop_it(self):
        code, out, _ = self.run_config("base_ref", 'live_credential = "unterminated')
        self.assertEqual((code, out.strip()), (0, CONFIG.raw["base_ref"]))

    def test_a_missing_key_or_a_table_is_refused(self):
        self.assertEqual(self.run_config("fe_guides.no_such_key")[0], 1)
        self.assertEqual(self.run_config("fe_guides")[0], 1)
        self.assertEqual(self.run_config("protected_branches")[0], 1)


class SettingsCheckTests(unittest.TestCase):
    """`factory settings --check`: names and set / not set only — never a value, its length, or a home path."""

    DUMMY = "dummy-private-value-7Qx9"
    VARIABLES = [CONFIG.raw["deploy"]["smoke_url_variable"], *CONFIG.raw["deploy"]["smoke_credentials"]]

    def check(self, contents: str | None, variables: dict[str, str]) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as folder:
            private = Path(folder) / "local.toml"
            if contents is not None:
                private.write_text(contents, encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if key not in self.VARIABLES}
            env.update(variables, APP_LOCAL_SETTINGS=str(private))
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(factory, "repo_root", return_value=FIXTURE_ROOT), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = factory.main(["settings", "--check"])
        return code, out.getvalue() + err.getvalue(), folder

    def assertNothingPrivate(self, output: str, folder: str) -> None:
        self.assertNotIn(self.DUMMY, output)
        self.assertNotIn("7Qx9", output, "not even part of a value")
        # Its length as a number on its own: a random temp folder's name may hold the same digits.
        self.assertNotRegex(output, rf"(?<![\w-]){len(self.DUMMY)}(?![\w-])", "nor its length")
        self.assertNotIn(str(Path.home()), output)
        self.assertNotIn(Path.home().as_posix(), output)
        self.assertNotIn(folder, output)
        self.assertNotIn(Path(folder).as_posix(), output)

    def test_set_keys_and_variables_are_named_never_shown(self):
        code, output, folder = self.check(
            f'[requirements.sources]\nlive_credential = "{self.DUMMY}"\nother = "{self.DUMMY}"\n',
            {name: self.DUMMY for name in self.VARIABLES})
        self.assertEqual(code, 0, output)
        self.assertIn("[requirements.sources] live_credential: set", output)
        for name in self.VARIABLES:
            self.assertIn(f"{name}: set", output)
        self.assertNotIn("other", output, "a key the tooling does not read is not reported")
        self.assertNothingPrivate(output, folder)

    def test_missing_keys_variables_and_file_are_not_set(self):
        code, output, folder = self.check(None, {})
        self.assertEqual(code, 0, output)
        self.assertIn("absent", output)
        self.assertIn("[requirements.sources] live_credential: not set", output)
        for name in self.VARIABLES:
            self.assertIn(f"{name}: not set", output)
        self.assertNothingPrivate(output, folder)

    def test_an_empty_value_is_not_set(self):
        code, output, folder = self.check('[requirements.sources]\nlive_credential = ""\n',
                                          {self.VARIABLES[0]: ""})
        self.assertIn("live_credential: not set", output)
        self.assertIn(f"{self.VARIABLES[0]}: not set", output)
        self.assertNothingPrivate(output, folder)

    def test_a_broken_file_is_reported_never_quoted(self):
        code, output, folder = self.check(f'[requirements.sources]\nlive_credential = "{self.DUMMY}', {})
        self.assertEqual(code, 1)
        self.assertIn("NOT VALID TOML", output)
        self.assertIn("live_credential: not set", output)
        self.assertNothingPrivate(output, folder)

    def test_the_home_folder_is_written_as_a_tilde(self):
        raw = {"private_settings": "~/.example/local.toml", "book": {"env_prefix": "APP_"}}
        with mock.patch.dict(os.environ, {"APP_LOCAL_SETTINGS": ""}):
            self.assertEqual(factory.settings_location(raw), "~/.example/local.toml")
        with mock.patch.dict(os.environ, {"APP_LOCAL_SETTINGS": str(Path.home() / "x" / "local.toml")}):
            self.assertEqual(factory.settings_location(raw), "~/x/local.toml")
        outside = Path(Path.home().anchor) / "elsewhere-settings" / "local.toml"
        with mock.patch.dict(os.environ, {"APP_LOCAL_SETTINGS": str(outside)}):
            self.assertEqual(factory.settings_location(raw), "the file APP_LOCAL_SETTINGS names")


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return done.stdout.strip()


@unittest.skipUnless(shutil.which("git"), "needs git")
class RepositoryTests(unittest.TestCase):
    """
    Pull requests, the AI-file check and what a branch changed, through factory.main as a person or
    session would call it, in a throwaway repository.
    """

    @classmethod
    def setUpClass(cls):
        """
        The base repository is built once and copied for each test: seven git processes per test
        were most of this class's run time, and no test depends on its repository having been made
        by its own `git init` - only on having one of its own to change.
        """
        cls.template = tempfile.TemporaryDirectory()
        base = Path(cls.template.name)
        (base / "devtools" / "factory").mkdir(parents=True)
        (base / factory.CONFIG_RELATIVE).write_text(fixture_project.PROJECT_TOML, encoding="utf-8")
        (base / "docs" / "plans").mkdir(parents=True)
        (base / "docs" / "plans" / "business-units.md").write_text("# Business units\n", encoding="utf-8")
        git(base, "init", "-q", "-b", "dev")
        git(base, "config", "user.name", "Test Person")
        git(base, "config", "user.email", "person@example.invalid")
        git(base, "config", "core.autocrlf", "false")
        git(base, "add", "-A")
        git(base, "commit", "-q", "-m", "Base")
        git(base, "update-ref", "refs/remotes/origin/dev", "HEAD")

    @classmethod
    def tearDownClass(cls):
        cls.template.cleanup()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        shutil.copytree(self.template.name, self.root, dirs_exist_ok=True)
        env = {key: value for key, value in os.environ.items() if key not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        env["FACTORY_ROOT"] = str(self.root)
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def factory(self, *argv: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = factory.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    ME = {"type": "user", "account_id": "acc-me", "display_name": "Test Person"}

    def bitbucket(self, existing=None, created_author="acc-me", me=None) -> mock.Mock:
        client = mock.Mock()
        client.current_user.return_value = me or self.ME
        client.open_pull_requests_from.return_value = existing or []
        client.create_pull_request.return_value = {"id": 77, "links": {"html": {"href": "https://bb/pr/77"}},
                                                   "author": {"account_id": created_author}}
        return client

    def local_ci(self, result: str = "PASS") -> None:
        """What devtools/checks/ci-local.sh records inside .git for the commit it ran on."""
        path = Path(git(self.root, "rev-parse", "--git-path", "factory-local-ci").strip())
        marker = path if path.is_absolute() else self.root / path
        marker.write_text(f"{result} {git(self.root, 'rev-parse', 'HEAD').strip()} 2026-10-09T10:00:00Z\n",
                          encoding="utf-8")

    def pushed_branch(self, name: str = "chore/x", reviewed: bool = True, local_ci: bool = True,
                      files: dict[str, str] | None = None, ai_line: bool = True) -> Path:
        """A work branch, its commit on origin and, unless told otherwise, passed by /book:self-review and by
        ci-local.sh; returns a body file. `files` are written into the same commit."""
        origin = Path(self.folder.name + "-origin")
        git(Path(self.folder.name).parent, "init", "-q", "--bare", str(origin))
        self.addCleanup(shutil.rmtree, origin, True)
        git(self.root, "remote", "add", "origin", str(origin))
        git(self.root, "checkout", "-q", "--no-track", "-b", name)
        (self.root / "docs" / "plans" / "business-units.md").write_text("# Business units, changed\n", encoding="utf-8")
        for rel, text in (files or {}).items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text, encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "A change")
        git(self.root, "push", "-q", "origin", name)
        if reviewed:
            code, out = self.factory("reviewed", "READY")
            self.assertEqual(code, 0, out)
        if local_ci:
            self.local_ci()
        body = Path(self.folder.name + "-body.md")
        sha = git(self.root, "rev-parse", "HEAD")[:12]
        ai = "- AI files: changed, see the list\n" if ai_line and files else ""
        body.write_text(f"## Summary\nx\n\n## Test plan\ny\n\n## Local CI\n\nPASS on `{sha}`\n{ai}\n"
                        "**Effort:** 1 hour, 1 review round\n", encoding="utf-8")
        self.addCleanup(body.unlink, True)
        return body

    def ai_pr(self, approved_on: str = "", by: str = "acc-other", pushed_by: str = "acc-dev") -> mock.Mock:
        client = mock.Mock()
        client.pull_request.return_value = {
            "author": {"account_id": "acc-dev", "display_name": "A Developer"},
            "participants": [{"approved": bool(approved_on), "participated_on": approved_on,
                              "user": {"account_id": by, "display_name": "Someone"}}]}
        client.activity.return_value = [{"update": {"date": "2026-10-03T10:00:00+00:00",
                                                    "author": {"account_id": pushed_by},
                                                    "source": {"commit": {"hash": git(self.root, "rev-parse", "HEAD")}}}}]
        return client

    def owner_pr(self, *pushers: str) -> tuple[dict, mock.Mock]:
        """A PR an owner opened that changes a rule, with one push by each of `pushers` (the owner when
        none or "" is named); nobody approved."""
        owner = factory.load_config(self.root).raw["ai_files"]["owners"][0]
        git(self.root, "checkout", "-q", "--no-track", "-b", "chore/x")
        (self.root / "CLAUDE.md").write_text("# Rules, changed\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "Change a rule")
        client = self.ai_pr(pushed_by=owner["account_id"])
        client.pull_request.return_value["author"]["account_id"] = owner["account_id"]
        update = client.activity.return_value[0]["update"]
        client.activity.return_value = [{"update": {**update, "author": {"account_id": pusher or owner["account_id"]}}}
                                        for pusher in pushers or ("",)]
        return owner, client

    def test_only_an_owner_lets_a_change_to_the_ai_files_through(self):
        owner = factory.load_config(self.root).raw["ai_files"]["owners"][0]["account_id"]
        git(self.root, "checkout", "-q", "--no-track", "-b", "chore/x")
        (self.root / "docs" / "plans" / "business-units.md").write_text("# Business units, changed\n", encoding="utf-8")
        (self.root / "devtools" / "checks").mkdir(parents=True)
        (self.root / "devtools" / "checks" / "api_contracts_postman.py").write_text("JOURNEY = []\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "Feature work")
        code, out = self.factory("ai-files", "7")
        self.assertEqual(code, 0, out)
        self.assertIn("No file that steers the AI changed", out, "a plan and the Postman journey are ordinary work")

        (self.root / "CLAUDE.md").write_text("# Rules, loosened\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "Change a rule")
        for approval, by, passes in (("", "acc-other", False),                           # nobody approved
                                     ("2026-10-03T11:00:00+00:00", "acc-other", False),  # not an owner
                                     ("2026-10-03T09:00:00+00:00", owner, False),        # before the latest commit
                                     ("2026-10-03T11:00:00+00:00", owner, True)):        # an owner, after it
            with self.subTest(approval=approval, by=by):
                with mock.patch.object(factory, "bitbucket_client", return_value=self.ai_pr(approval, by)):
                    code, out = self.factory("ai-files", "7")
                self.assertEqual(code == 0, passes, out)
                self.assertIn("approved it" if passes else "CLAUDE.md", out)

    def test_an_owners_own_pr_passes_without_an_approval(self):
        """Made by an owner is approved by an owner (the owner's decision, 2026-10-04): Bitbucket never
        lets an author approve their own pull request."""
        owner, client = self.owner_pr()
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("ai-files", "7")
        self.assertEqual(code, 0, out)
        self.assertIn(f"its author, {owner['name']}, is an owner and every push to it was an owner's", out)

    def test_a_non_owners_push_to_an_owners_pr_needs_an_owners_approval(self):
        """Opened by an owner is not made by one: someone else's commits on it are judged like any PR's."""
        _, client = self.owner_pr("", "acc-dev")
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("ai-files", "7")
        self.assertEqual(code, 1, out)
        self.assertIn("must approve it after its latest commit", out)

    def test_with_no_owners_nothing_passes(self):
        config = self.root / "devtools" / "factory" / "factory.toml"
        text = config.read_text(encoding="utf-8")
        config.write_text(re.sub(r"owners = \[.*?\n\]", "owners = []", text, count=1, flags=re.S), encoding="utf-8")
        git(self.root, "commit", "-qam", "No owners")
        git(self.root, "update-ref", "refs/remotes/origin/dev", "HEAD")
        git(self.root, "checkout", "-q", "--no-track", "-b", "chore/x")
        (self.root / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "Change a rule")
        with mock.patch.object(factory, "bitbucket_client", return_value=self.ai_pr("2026-10-03T11:00:00+00:00", "acc-dev")):
            code, out = self.factory("ai-files", "7")
        self.assertEqual(code, 1, out)
        self.assertIn("owners is empty", out)

    def test_a_file_renamed_out_of_the_ai_paths_is_still_a_change_to_them(self):
        settings = self.root / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_text('{"hooks": {}}\n', encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "Wire the guard")
        git(self.root, "update-ref", "refs/remotes/origin/dev", "HEAD")
        git(self.root, "checkout", "-q", "--no-track", "-b", "chore/x")
        git(self.root, "mv", ".claude/settings.json", "docs/settings.json")
        git(self.root, "commit", "-qm", "Move the settings out of the way")
        with mock.patch.dict(os.environ, {"BITBUCKET_PR_ID": ""}):
            code, out = self.factory("ai-files")
        self.assertEqual(code, 1, out)
        self.assertIn(".claude/settings.json", out)

    def test_a_commit_bitbucket_has_not_shown_does_not_pass_on_an_older_approval(self):
        owner = factory.load_config(self.root).raw["ai_files"]["owners"][0]["account_id"]
        git(self.root, "checkout", "-q", "--no-track", "-b", "chore/x")
        (self.root / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "Change a rule")
        client = self.ai_pr("2026-10-03T11:00:00+00:00", owner)  # approved after this commit arrived
        (self.root / "CLAUDE.md").write_text("# Rules, loosened further\n", encoding="utf-8")
        git(self.root, "commit", "-qam", "Then change it again")
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("ai-files", "7")
        self.assertEqual(code, 1, out)
        self.assertIn("Could not check", out)

    def test_the_branchs_own_list_is_used_only_when_the_destination_has_none_and_it_says_so(self):
        config = self.root / "devtools" / "factory" / "factory.toml"
        whole = config.read_text(encoding="utf-8")
        config.write_text(whole.replace("[ai_files]", "[ai_files_not_yet]"), encoding="utf-8")
        git(self.root, "commit", "-qam", "No owners on dev yet")
        git(self.root, "update-ref", "refs/remotes/origin/dev", "HEAD")
        git(self.root, "checkout", "-q", "--no-track", "-b", "chore/x")
        config.write_text(whole, encoding="utf-8")
        git(self.root, "commit", "-qam", "Bring the owners")
        with mock.patch.dict(os.environ, {"BITBUCKET_PR_ID": ""}):
            code, out = self.factory("ai-files")
        self.assertEqual(code, 1, out)
        self.assertIn("has no [ai_files] yet", out)

    def test_a_destination_config_that_does_not_parse_stops_the_check(self):
        config = self.root / "devtools" / "factory" / "factory.toml"
        whole = config.read_text(encoding="utf-8")
        config.write_text("owners = [ not toml\n", encoding="utf-8")
        git(self.root, "commit", "-qam", "Break it on dev")
        git(self.root, "update-ref", "refs/remotes/origin/dev", "HEAD")
        git(self.root, "checkout", "-q", "--no-track", "-b", "chore/x")
        config.write_text(whole, encoding="utf-8")
        git(self.root, "commit", "-qam", "Mend it here")
        code, out = self.factory("ai-files", "7")
        self.assertEqual(code, 1, out)
        self.assertIn("not valid TOML", out)

    def test_without_a_pr_or_a_merge_base_nothing_passes(self):
        git(self.root, "checkout", "-q", "--no-track", "-b", "chore/x")
        (self.root / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "Change a rule")
        with mock.patch.dict(os.environ, {"BITBUCKET_PR_ID": ""}):
            code, out = self.factory("ai-files")
        self.assertEqual(code, 1, out)
        self.assertIn("must approve", out)
        git(self.root, "checkout", "-q", "--orphan", "chore/unrelated")
        git(self.root, "commit", "-qm", "No shared history")
        code, out = self.factory("ai-files", "7")
        self.assertEqual(code, 1, out)
        self.assertIn("no merge base", out)

    def test_an_owners_approval_the_commit_cannot_be_dated_against_does_not_count(self):
        owner = factory.load_config(self.root).raw["ai_files"]["owners"][0]["account_id"]
        git(self.root, "checkout", "-q", "--no-track", "-b", "chore/x")
        (self.root / "CLAUDE.md").write_text("# Rules, loosened\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "Change a rule")
        client = self.ai_pr("2026-10-03T11:00:00+00:00", owner)
        client.activity.return_value = []  # Bitbucket has not shown the push yet
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("ai-files", "7")
        self.assertEqual(code, 1, out)
        self.assertIn("Could not check", out)

    # ---- `ai-files --staged`, the pre-commit note

    def stage_guard_change_by_script(self) -> None:
        """What `python edit.py` or `git apply` leaves: the guard changed with no tool call a hook could see."""
        guard = self.root / "devtools" / "factory" / "hooks" / "guard.py"
        guard.parent.mkdir(parents=True, exist_ok=True)
        guard.write_text("# the guard\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "The guard")
        git(self.root, "update-ref", "refs/remotes/origin/dev", "HEAD")
        git(self.root, "checkout", "-q", "--no-track", "-b", "fix/x")
        patch = Path(self.folder.name + "-guard.patch")
        self.addCleanup(patch.unlink, True)
        guard.write_text("# the guard, loosened\n", encoding="utf-8")
        patch.write_text(git(self.root, "diff") + "\n", encoding="utf-8")
        git(self.root, "checkout", "--", ".")
        git(self.root, "apply", "--index", str(patch))

    def test_staged_names_a_guard_change_a_script_made_and_never_refuses(self):
        self.stage_guard_change_by_script()
        code, out = self.factory("ai-files", "--staged")
        self.assertEqual(code, 0, "a note, never a refusal: a non-owner may change it with an owner's approval")
        self.assertIn("devtools/factory/hooks/guard.py", out)
        self.assertIn("1 of them are the guard's own", out)
        self.assertIn("`git apply`", out)
        self.assertIn("Your git name is not an owner", out)
        self.assertIn("factory ai-files", out)
        self.assertNotIn("Test Person", out, "the committer's identity is compared, never printed")
        self.assertNotIn("example.invalid", out)

    def test_staged_says_an_owner_without_printing_the_name(self):
        owner = factory.load_config(self.root).raw["ai_files"]["owners"][0]["name"]
        self.stage_guard_change_by_script()
        git(self.root, "config", "user.name", owner.upper())
        code, out = self.factory("ai-files", "--staged")
        self.assertEqual(code, 0, out)
        self.assertIn("Your git name is an owner", out)
        self.assertNotIn(owner.casefold(), out.casefold())

    def test_staged_judges_by_the_destinations_owners_not_the_commits(self):
        git(self.root, "checkout", "-q", "--no-track", "-b", "chore/x")
        config = self.root / "devtools" / "factory" / "factory.toml"
        config.write_text(config.read_text(encoding="utf-8").replace(
            "owners = [", 'owners = [{ name = "Test Person", account_id = "acc-dev" }, ', 1), encoding="utf-8")
        git(self.root, "add", "-A")
        code, out = self.factory("ai-files", "--staged")
        self.assertEqual(code, 0, out)
        self.assertIn("devtools/factory/factory.toml", out)
        self.assertIn("1 of them are the guard's own", out)
        self.assertIn("Your git name is not an owner", out, "a commit cannot name its own committer an owner")

    def test_staged_says_nothing_about_ordinary_work(self):
        (self.root / "docs" / "plans" / "business-units.md").write_text("# Business units, changed\n", encoding="utf-8")
        (self.root / "devtools" / "checks").mkdir(parents=True)
        (self.root / "devtools" / "checks" / "api_contracts_postman.py").write_text("JOURNEY = []\n", encoding="utf-8")
        git(self.root, "add", "-A")
        self.assertEqual(self.factory("ai-files", "--staged"), (0, ""))

    def test_staged_names_ai_files_that_are_not_the_guards_without_the_guard_line(self):
        (self.root / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
        git(self.root, "add", "-A")
        code, out = self.factory("ai-files", "--staged")
        self.assertEqual(code, 0, out)
        self.assertIn("1 file(s) that steer the AI: CLAUDE.md", out)
        self.assertNotIn("guard's own", out)

    def test_a_pr_cannot_make_its_own_author_an_owner(self):
        git(self.root, "checkout", "-q", "--no-track", "-b", "chore/x")
        config = self.root / "devtools" / "factory" / "factory.toml"
        text = config.read_text(encoding="utf-8")
        config.write_text(text.replace("owners = [", 'owners = [{ name = "A Developer", account_id = "acc-dev" }, ', 1),
                          encoding="utf-8")
        self.assertIn("acc-dev", factory.load_config(self.root).raw["ai_files"]["owners"][0]["account_id"])
        git(self.root, "commit", "-qam", "Make myself an owner")
        with mock.patch.object(factory, "bitbucket_client", return_value=self.ai_pr()):
            code, out = self.factory("ai-files", "7")
        self.assertEqual(code, 1, "the owners are the destination's, not the pull request's")
        self.assertIn("factory.toml", out)

    def test_pr_open_wants_a_ready_review_of_the_very_commit_it_opens(self):
        body = self.pushed_branch(reviewed=False)
        with mock.patch.object(factory, "bitbucket_client") as client:
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body))
            self.assertEqual(code, 1)
            self.assertIn("No verdict is recorded", out)
            self.assertEqual(self.factory("reviewed", "NOT-READY")[0], 0)
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body))
            self.assertEqual(code, 1)
            self.assertIn("It answered NOT-READY", out)
            self.assertEqual(self.factory("reviewed", "READY")[0], 0)
            (self.root / "docs" / "plans" / "business-units.md").write_text("# Business units, after the review\n", encoding="utf-8")
            code, out = self.factory("reviewed", "READY")
            self.assertEqual(code, 1, "a verdict is never recorded over uncommitted work")
            self.assertIn("uncommitted", out)
            git(self.root, "commit", "-qam", "After the review")
            git(self.root, "push", "-q", "origin", "chore/x")
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body))
            self.assertEqual(code, 1)
            self.assertIn("another commit", out)
        client.assert_not_called()
        self.assertEqual(git(self.root, "status", "--porcelain"), "",
                         "the verdict lives inside .git, never in the working tree")

    def test_pr_open_wants_a_local_ci_pass_of_the_very_commit_it_opens_while_ci_is_off(self):
        body = self.pushed_branch(local_ci=False)
        with mock.patch.object(factory, "bitbucket_client") as client:
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body))
            self.assertEqual(code, 1)
            self.assertIn("No local CI result is recorded", out)
            self.assertIn("ci-local.sh", out)
            self.local_ci("FAIL")
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body))
            self.assertEqual(code, 1)
            self.assertIn("It recorded FAIL", out)
            self.local_ci()
            (self.root / "docs" / "plans" / "business-units.md").write_text("# Business units, after local CI\n", encoding="utf-8")
            git(self.root, "commit", "-qam", "After local CI")
            git(self.root, "push", "-q", "origin", "chore/x")
            self.assertEqual(self.factory("reviewed", "READY")[0], 0)
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body))
            self.assertEqual(code, 1)
            self.assertIn("another commit", out)
        client.assert_not_called()

    def test_pr_open_needs_no_local_ci_once_it_is_switched_off(self):
        config = self.root / "devtools" / "factory" / "factory.toml"
        text = config.read_text(encoding="utf-8")
        self.assertIn("[local_ci]\nrequired = true", text)
        config.write_text(text.replace("[local_ci]\nrequired = true", "[local_ci]\nrequired = false"), encoding="utf-8")
        git(self.root, "commit", "-qam", "CI runs on the runner")
        git(self.root, "update-ref", "refs/remotes/origin/dev", "HEAD")
        body = self.pushed_branch(local_ci=False)
        client = self.bitbucket()
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body))
        self.assertEqual(code, 0, out)

    def test_pr_open_opens_a_work_branchs_pr_in_the_persons_name_from_a_session(self):
        git(self.root, "update-ref", "refs/remotes/origin/chore/parent", "HEAD")
        body = self.pushed_branch()
        client = self.bitbucket()
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}), \
                mock.patch.object(factory, "bitbucket_client", return_value=client) as made:
            code, out = self.factory("pr", "open", "--title", "A change", "--body-file", str(body),
                                     "--destination", "chore/parent")
        self.assertEqual(code, 0, out)
        self.assertEqual(made.call_args.kwargs, {"personal": True}, "the person's own credential, never BITBUCKET_TOKEN")
        title, text, source, destination = client.create_pull_request.call_args.args
        self.assertEqual((title, source, destination), ("A change", "chore/x", "chore/parent"))
        self.assertIn("## Test plan", text)
        self.assertIn("PR #77", out)

    # ---- `pr open`'s note on the files that steer the AI

    def test_pr_open_warns_a_non_owner_whose_pr_steers_the_ai_and_still_opens_it(self):
        body = self.pushed_branch(files={"CLAUDE.md": "# Rules, changed\n", ".claude/skills/x/SKILL.md": "x\n"})
        client = self.bitbucket()
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "open", "--title", "A change", "--body-file", str(body))
        self.assertEqual(code, 0, "a warning, never a refusal")
        self.assertIn("PR #77", out)
        self.assertIn("WARN  2 file(s) in this PR steer the AI (.claude/skills/x/SKILL.md, CLAUDE.md)", out)
        self.assertIn("an owner must make or approve this PR", out)
        self.assertIn("while CI is suspended, the person merging holds it", out)
        self.assertNotIn("acc-me", out, "the account is compared, never printed")
        self.assertNotIn("example.invalid", out)
        _, text, _, _ = client.create_pull_request.call_args.args
        self.assertEqual(text, body.read_text(encoding="utf-8").strip(), "the description is left as written")

    def test_pr_open_says_nothing_to_an_owner_or_about_ordinary_work(self):
        owner = factory.load_config(self.root).raw["ai_files"]["owners"][0]
        body = self.pushed_branch(files={"CLAUDE.md": "# Rules, changed\n"})
        me = {"type": "user", "account_id": owner["account_id"], "display_name": owner["name"]}
        client = self.bitbucket(me=me, created_author=owner["account_id"])
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "open", "--title", "A change", "--body-file", str(body))
        self.assertEqual(code, 0, out)
        self.assertNotIn("WARN", out)

    def test_pr_open_says_nothing_when_no_file_steers_the_ai(self):
        body = self.pushed_branch()
        with mock.patch.object(factory, "bitbucket_client", return_value=self.bitbucket()):
            code, out = self.factory("pr", "open", "--title", "A change", "--body-file", str(body))
        self.assertEqual(code, 0, out)
        self.assertNotIn("WARN", out)

    def test_pr_open_that_cannot_list_the_ai_files_refuses(self):
        """The description's AI files line cannot be judged without the list, so nothing opens (U1b #7)."""
        body = self.pushed_branch(files={"CLAUDE.md": "# Rules, changed\n"})
        client = self.bitbucket()
        with mock.patch.object(factory, "bitbucket_client", return_value=client), \
                mock.patch.object(factory, "ai_files_settings", side_effect=factory.FactoryError("not valid TOML")):
            code, out = self.factory("pr", "open", "--title", "A change", "--body-file", str(body))
        self.assertEqual(code, 1, out)
        self.assertIn("not valid TOML", out)
        client.create_pull_request.assert_not_called()

    def test_pr_open_refuses_a_description_without_the_ai_files_line_when_ai_files_changed(self):
        body = self.pushed_branch(files={"CLAUDE.md": "# Rules, changed\n"}, ai_line=False)
        client = self.bitbucket()
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "open", "--title", "A change", "--body-file", str(body))
        self.assertEqual(code, 1, out)
        self.assertIn("no 'AI files' line, though the PR changes 1 file(s) that steer the AI (CLAUDE.md)", out)
        client.create_pull_request.assert_not_called()

    def test_ai_files_list_prints_the_local_ci_line(self):
        code, out = self.factory("ai-files", "--list")
        self.assertEqual(code, 0, out)
        self.assertIn("AI files: none changed", out)
        git(self.root, "checkout", "-q", "--no-track", "-b", "chore/x")
        (self.root / "CLAUDE.md").write_text("# Rules, changed\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "Change a rule")
        code, out = self.factory("ai-files", "--list")
        self.assertEqual(code, 0, out)
        self.assertIn("AI files: 1 changed (CLAUDE.md)", out)
        git(self.root, "update-ref", "-d", "refs/remotes/origin/dev")
        code, out = self.factory("ai-files", "--list")
        self.assertEqual(code, 1, "no merge base: the list is unknown, never 'none'")
        self.assertIn("Could not list", out)

    def test_pr_open_defaults_to_the_base_branch_and_never_opens_a_second(self):
        body = self.pushed_branch()
        client = self.bitbucket(existing=[{"id": 5, "title": "Open already", "links": {"html": {"href": "u"}}}])
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "open", "--title", "A change", "--body-file", str(body))
        self.assertEqual(code, 0, out)
        client.create_pull_request.assert_not_called()
        self.assertIn("Already open", out)

    # ---- `pr describe`: a description replaced after a rebase or a push

    def described(self, head: str = "", author: str = "acc-me", state: str = "OPEN", me=None) -> mock.Mock:
        """A client whose PR #77 Bitbucket has from chore/x, at `head` (this commit by default)."""
        client = self.bitbucket(me=me)
        client.pull_request.return_value = {
            "id": 77, "state": state, "title": "A change", "author": {"account_id": author},
            "source": {"branch": {"name": "chore/x"},
                       "commit": {"hash": (head or git(self.root, "rev-parse", "HEAD"))[:12]}},
            "destination": {"branch": {"name": "dev"}}}
        client.update_description.side_effect = lambda number, title, text: {
            **client.pull_request.return_value, "title": title, "description": text}
        return client

    def test_pr_describe_replaces_the_description_of_the_persons_own_pr(self):
        body = self.pushed_branch()
        client = self.described()
        with mock.patch.object(factory, "bitbucket_client", return_value=client) as made:
            code, out = self.factory("pr", "describe", "--number", "77", "--body-file", str(body))
        self.assertEqual(code, 0, out)
        self.assertEqual(made.call_args.kwargs, {"personal": True}, "the person's own credential, never BITBUCKET_TOKEN")
        client.update_description.assert_called_once_with(77, "A change", body.read_text(encoding="utf-8").strip())
        self.assertIn("Updated the description of PR #77", out)
        client.create_pull_request.assert_not_called()

        client = self.described()
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "describe", "--number", "77", "--body-file", str(body), "--title", "Renamed")
        self.assertEqual(code, 0, out)
        self.assertEqual(client.update_description.call_args.args[1], "Renamed")

    def test_pr_describe_wants_a_local_ci_pass_for_the_prs_current_head(self):
        body = self.pushed_branch()
        client = self.described(head="0123456789ab" + "0" * 28)
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "describe", "--number", "77", "--body-file", str(body))
        self.assertEqual(code, 1, "a Local CI section from before a rebase names a commit the PR no longer has")
        self.assertIn("not a PASS for the head commit 0123456789ab", out)
        client.update_description.assert_not_called()

    def test_pr_describe_runs_pr_opens_description_checks(self):
        body = self.pushed_branch()
        body.write_text(body.read_text(encoding="utf-8").replace("**Effort:** 1 hour, 1 review round\n", "")
                        .replace("## Test plan", "## Testing"), encoding="utf-8")
        client = self.described()
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "describe", "--number", "77", "--body-file", str(body))
        self.assertEqual(code, 1)
        self.assertIn("no 'test plan' section", out)
        self.assertIn("no 'Effort' line", out)
        client.update_description.assert_not_called()

    def test_pr_describe_wants_the_ai_files_line_for_the_prs_own_head(self):
        body = self.pushed_branch(files={"CLAUDE.md": "# Rules, changed\n"}, ai_line=False)
        client = self.described()
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "describe", "--number", "77", "--body-file", str(body))
        self.assertEqual(code, 1)
        self.assertIn("no 'AI files' line", out)
        client.update_description.assert_not_called()
        client = self.described(head="0123456789ab" + "0" * 28)
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "describe", "--number", "77", "--body-file", str(body))
        self.assertEqual(code, 1)
        self.assertIn("is not in this clone", out)
        client.update_description.assert_not_called()

    def test_pr_describe_refuses_a_bot_another_persons_pr_a_closed_pr_and_no_number(self):
        body = self.pushed_branch()
        for client, says in ((self.described(me={"type": "app_user", "account_id": "acc-bot"}), "not a person's account"),
                             (self.described(author="acc-other"), "is not yours"),
                             (self.described(state="MERGED"), "is MERGED")):
            with self.subTest(says), mock.patch.object(factory, "bitbucket_client", return_value=client):
                code, out = self.factory("pr", "describe", "--number", "77", "--body-file", str(body))
                self.assertEqual(code, 1)
                self.assertIn(says, out)
                client.update_description.assert_not_called()
        with mock.patch.object(factory, "bitbucket_client") as made:
            code, out = self.factory("pr", "describe", "--body-file", str(body))
        self.assertEqual(code, 1)
        self.assertIn("--number", out)
        made.assert_not_called()

    def test_pr_text_never_comes_from_a_private_file_or_carries_a_secret(self):
        body = self.pushed_branch()
        private = str(Path.home() / ".app" / "local.toml")
        with mock.patch.object(factory, "bitbucket_client") as client:
            code, out = self.factory("pr", "comment", "--number", "7", "--path", "a", "--line", "1",
                                     "--body-file", private)
            self.assertEqual(code, 1)
            self.assertIn("never from a private", out)
            # Inside the allowed temp folder, so only the private-marker check can refuse it.
            store = Path(self.folder.name + "-store") / ".git-credentials"
            store.parent.mkdir()
            self.addCleanup(shutil.rmtree, store.parent, True)
            store.write_text("x\n", encoding="utf-8")
            code, out = self.factory("pr", "comment", "--number", "7", "--path", "a", "--line", "1",
                                     "--body-file", str(store))
            self.assertEqual(code, 1)
            self.assertIn("never from a private", out)
            # A git-ignored file in the repository is local-only.
            (self.root / ".gitignore").write_text("local.env\n", encoding="utf-8")
            (self.root / "local.env").write_text("DB_PASSWORD=hunter2\n", encoding="utf-8")
            code, out = self.factory("pr", "comment", "--number", "7", "--path", "a", "--line", "1",
                                     "--body-file", str(self.root / "local.env"))
            self.assertEqual(code, 1)
            self.assertIn("git-ignored", out)
            body.write_text("the key is AKIA" + "ABCDEFGHIJKLMNOP\n", encoding="utf-8")
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body))
            self.assertEqual(code, 1)
            self.assertIn("looks like a secret", out)
        client.assert_not_called()

    def test_pr_comment_posts_in_the_persons_name_only(self):
        body = self.pushed_branch()
        bot = self.bitbucket(me={"type": "app_user", "account_id": "bot-1", "display_name": "Repo token"})
        with mock.patch.object(factory, "bitbucket_client", return_value=bot) as made:
            code, out = self.factory("pr", "comment", "--number", "7", "--path", "a", "--line", "1",
                                     "--body-file", str(body))
        self.assertEqual(made.call_args.kwargs, {"personal": True}, "the person's own credential, never BITBUCKET_TOKEN")
        self.assertEqual(code, 1)
        self.assertIn("not a person's account", out)
        bot.post_comment.assert_not_called()

    def test_pr_open_refuses_a_promotion_and_a_protected_source(self):
        body = self.pushed_branch()
        with mock.patch.object(factory, "bitbucket_client") as client:
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body), "--destination", "qa")
        self.assertEqual(code, 1)
        self.assertIn("promotion", out)
        git(self.root, "checkout", "-q", "-B", "dev")
        with mock.patch.object(factory, "bitbucket_client") as client:
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body))
        self.assertEqual(code, 1)
        self.assertIn("not a work branch", out)
        client.assert_not_called()

    def test_pr_open_wants_the_commit_pushed_and_a_persons_credential(self):
        body = self.pushed_branch()
        bot = self.bitbucket(me={"type": "app_user", "account_id": "bot-1", "display_name": "Repo token"})
        with mock.patch.object(factory, "bitbucket_client", return_value=bot):
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body))
        self.assertEqual(code, 1)
        self.assertIn("not a person's account", out)
        bot.create_pull_request.assert_not_called()
        (self.root / "docs" / "plans" / "business-units.md").write_text("# Business units, not pushed\n", encoding="utf-8")
        git(self.root, "commit", "-qam", "Not pushed")
        with mock.patch.object(factory, "bitbucket_client") as client:
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body))
        self.assertEqual(code, 1)
        self.assertIn("Push first", out)
        client.assert_not_called()

    def test_pr_comment_posts_one_inline_comment_and_needs_its_line(self):
        body = self.pushed_branch()
        client = self.bitbucket()
        client.post_comment.return_value = {"id": 501}
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "comment", "--number", "7", "--path", "src/A.cs", "--body-file", str(body))
            self.assertEqual(code, 1)
            self.assertIn("--line", out)
            code, out = self.factory("pr", "comment", "--number", "7", "--path", "src/A.cs", "--line", "12",
                                     "--body-file", str(body))
        self.assertEqual(code, 0, out)
        client.post_comment.assert_called_once_with(7, body.read_text(encoding="utf-8").strip(), "src/A.cs", 12)

    def test_pr_resolve_marks_one_comment_and_needs_its_id(self):
        self.pushed_branch()
        client = self.bitbucket()
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "resolve", "--number", "7")
            self.assertEqual(code, 1)
            self.assertIn("--comment-id", out)
            code, out = self.factory("pr", "resolve", "--number", "7", "--comment-id", "501",
                                     "--confirm-author-settled")
        self.assertEqual(code, 0, out)
        client.resolve_comment.assert_called_once_with(7, 501)

    def test_pr_resolve_without_the_authors_confirmation_is_refused_before_bitbucket(self):
        self.pushed_branch()
        with mock.patch.object(factory, "bitbucket_client") as client:
            code, out = self.factory("pr", "resolve", "--number", "7", "--comment-id", "501")
        self.assertEqual(code, 1)
        self.assertIn("--confirm-author-settled", out)
        self.assertIn("/book:peer-review never resolves", out)
        client.assert_not_called()

    def test_pr_comment_with_expect_head_posts_only_onto_that_head(self):
        body = self.pushed_branch()
        client = self.bitbucket()
        client.post_comment.return_value = {"id": 501}
        client.pull_request.return_value = {"source": {"commit": {"hash": "abc1234def56"}}}
        comment = ("pr", "comment", "--number", "7", "--path", "src/A.cs", "--line", "12", "--body-file", str(body))
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            for reviewed in ("abc1234def56", "abc1234", "ABC1234DEF56" + "7" * 28):
                with self.subTest(reviewed=reviewed):
                    code, out = self.factory(*comment, "--expect-head", reviewed)
                    self.assertEqual(code, 0, out)
            self.assertEqual(client.post_comment.call_count, 3)
            client.post_comment.reset_mock()
            code, out = self.factory(*comment, "--expect-head", "abc1235")
            self.assertEqual(code, 1)
            self.assertIn("head is now abc1234def56", out)
            self.assertIn("nothing posted", out)
            for wrong in ("abc12", "not-a-sha"):
                code, out = self.factory(*comment, "--expect-head", wrong)
                self.assertEqual(code, 1)
                self.assertIn("7 to 40 hex", out)
            client.pull_request.return_value = {"source": {}}
            code, out = self.factory(*comment, "--expect-head", "abc1234")
            self.assertEqual(code, 1)
            self.assertIn("head is now unknown", out)
        client.post_comment.assert_not_called()

    HEAD = {"source": {"commit": {"hash": "abc1234def56"}}}

    def plan(self, findings) -> Path:
        """A posting plan in the temp folder, where a session's scratchpad is."""
        import json
        plan = Path(self.folder.name + "-plan.json")
        plan.write_text(findings if isinstance(findings, str) else json.dumps(findings), encoding="utf-8")
        self.addCleanup(plan.unlink, True)
        return plan

    @staticmethod
    def inline(cid, path, line, body, account="acc-me", **extra) -> dict:
        return {"id": cid, "inline": {"path": path, "to": line}, "content": {"raw": body},
                "user": {"account_id": account, "display_name": "x"}, **extra}

    def post(self, plan: Path, head: str = "abc1234") -> tuple[int, str]:
        return self.factory("pr", "post", "--number", "7", "--plan", str(plan), "--expect-head", head)

    def test_pr_post_posts_in_order_skips_its_own_and_reads_back(self):
        self.pushed_branch()
        findings = [{"path": "src/A.cs", "line": 12, "body": "**HIGH — no transaction**\n\nWrap both saves."},
                    {"path": "src/B.cs", "line": 3, "body": "**LOW — a name**"},
                    {"path": "src/C.cs", "line": 5, "body": "**MEDIUM — a reply's line**"},
                    {"path": "src/D.cs", "line": 9, "body": "**MEDIUM — reworded**"},
                    {"path": "src/E.cs", "line": 1, "body": "**INFO — deleted before**"}]
        before = [self.inline(40, "src/A.cs", 12, "**HIGH —   no transaction**\nWrap both saves."),  # this run's, earlier
                  self.inline(41, "src/B.cs", 3, "**LOW — a name**", account="acc-other"),  # someone else's
                  self.inline(42, "src/C.cs", 5, "**MEDIUM — a reply's line**", parent={"id": 1}),  # a reply
                  self.inline(43, "src/D.cs", 9, "**MEDIUM — said otherwise**"),  # own, another finding
                  self.inline(44, "src/E.cs", 1, "**INFO — deleted before**", deleted=True),
                  {"id": 45, "content": {"raw": "general"}, "user": {"account_id": "acc-me"}}]
        after = before[:1] + [self.inline(cid, f["path"], f["line"], f["body"])
                              for cid, f in zip((501, 502, 503, 504), findings[1:])]
        client = self.bitbucket()
        client.pull_request.return_value = self.HEAD
        client.comments.side_effect = [before, after]
        client.post_comment.side_effect = [{"id": 501}, {"id": 502}, {"id": 503}, {"id": 504}]
        with mock.patch.object(factory, "bitbucket_client", return_value=client) as made:
            code, out = self.post(self.plan(findings))
        self.assertEqual(code, 0, out)
        self.assertEqual(made.call_args.kwargs, {"personal": True})
        self.assertEqual([c.args for c in client.post_comment.call_args_list],
                         [(7, f["body"], f["path"], f["line"]) for f in findings[1:]], "in plan order, the skip left out")
        self.assertIn("4 posted, 1 skipped, 0 failed, 0 not attempted (of 5)", out)
        self.assertIn("skipped  #40  src/A.cs:12", out)
        for cid, f in zip((501, 502, 503, 504), findings[1:]):
            self.assertIn(f"posted   #{cid}  {f['path']}:{f['line']}", out)
        self.assertIn("every posted and skipped finding is on the PR", out)
        client.pull_request.assert_called_once_with(7)

    def test_pr_post_refuses_a_moved_head_before_reading_or_posting(self):
        self.pushed_branch()
        client = self.bitbucket()
        client.pull_request.return_value = self.HEAD
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.post(self.plan([{"path": "a", "line": 1, "body": "x"}]), head="abc1235")
        self.assertEqual(code, 1)
        self.assertIn("head is now abc1234def56", out)
        self.assertIn("nothing posted", out)
        client.post_comment.assert_not_called()
        client.comments.assert_not_called()

    def test_pr_post_stops_on_the_first_failure_and_never_repeats_it(self):
        self.pushed_branch()
        findings = [{"path": "a", "line": 1, "body": "one"}, {"path": "b", "line": 2, "body": "two"},
                    {"path": "c", "line": 3, "body": "three"}]
        timed_out = factory.bitbucket.BitbucketError("No answer to the POST to x; it may have been created — "
                                                     "look before trying again.")
        for landed in (False, True):
            with self.subTest(landed=landed):
                client = self.bitbucket()
                client.pull_request.return_value = self.HEAD
                after = [self.inline(1, "a", 1, "one")] + ([self.inline(2, "b", 2, "two")] if landed else [])
                client.comments.side_effect = [[], after]
                client.post_comment.side_effect = [{"id": 1}, timed_out, {"id": 3}]
                with mock.patch.object(factory, "bitbucket_client", return_value=client):
                    code, out = self.post(self.plan(findings))
                self.assertEqual(code, 1)
                self.assertEqual(client.post_comment.call_count, 2, "stopped at the failure, never re-sent")
                self.assertIn("1 posted, 0 skipped, 1 failed, 1 not attempted (of 3)", out)
                self.assertIn("failed   b:2  — No answer to the POST", out)
                self.assertIn("not attempted  c:3", out)
                self.assertIn("posted   #1  a:1", out)
                self.assertIn("is on the PR after all, as #2" if landed else "nothing was retried", out)

    def test_pr_post_says_what_the_read_back_could_not_find(self):
        self.pushed_branch()
        client = self.bitbucket()
        client.pull_request.return_value = self.HEAD
        client.comments.side_effect = [[], []]
        client.post_comment.return_value = {"id": 9}
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.post(self.plan([{"path": "a", "line": 1, "body": "one"}]))
        self.assertEqual(code, 1)
        self.assertIn("1 posted", out)
        self.assertIn("Not on the PR when read back: a:1", out)
        client.comments.side_effect = [[], factory.bitbucket.BitbucketError("Bitbucket answered 500")]
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.post(self.plan([{"path": "a", "line": 1, "body": "one"}]))
        self.assertEqual(code, 1)
        self.assertIn("Could not read the comments back", out)

    def test_pr_post_refuses_a_bad_plan_before_bitbucket(self):
        self.pushed_branch()
        good = {"path": "src/A.cs", "line": 12, "body": "x"}
        cases = {
            "not JSON": ("[{", "is not JSON"),
            "empty": ([], "non-empty JSON list"),
            "an object": ({"path": "a"}, "non-empty JSON list"),
            "an extra key": ([{**good, "id": 3}], "path, line and body only"),
            "no line": ([{"path": "a", "body": "x"}], "whole number from 1"),
            "line 0": ([{**good, "line": 0}], "whole number from 1"),
            "line true": ([{**good, "line": True}], "whole number from 1"),
            "line as text": ([{**good, "line": "12"}], "whole number from 1"),
            "a backslash path": ([{**good, "path": "src\\A.cs"}], "forward slashes"),
            "an empty body": ([{**good, "body": "  "}], "is empty"),
            "a hidden marker": ([{**good, "body": "<!-- review:1 -->x"}], "HTML comment"),
            "a secret": ([{**good, "body": "the key is AKIA" + "ABCDEFGHIJKLMNOP"}], "looks like a secret"),
            "two on one line": ([good, {**good, "body": "y"}], "one comment per line"),
        }
        with mock.patch.object(factory, "bitbucket_client") as client:
            for name, (findings, expected) in cases.items():
                with self.subTest(name):
                    code, out = self.post(self.plan(findings))
                    self.assertEqual(code, 1)
                    self.assertIn(expected, out)
            plan = self.plan([good])
            for argv, expected in (
                    (("pr", "post", "--number", "7", "--expect-head", "abc1234"), "--plan"),
                    (("pr", "post", "--number", "7", "--plan", str(plan)), "--expect-head"),
                    (("pr", "post", "--plan", str(plan), "--expect-head", "abc1234"), "--number"),
                    (("pr", "post", "--number", "7", "--plan", str(plan), "--expect-head", "abc12"), "7 to 40 hex")):
                with self.subTest(argv=argv):
                    code, out = self.factory(*argv)
                    self.assertEqual(code, 1)
                    self.assertIn(expected, out)
            (self.root / ".gitignore").write_text("local.json\n", encoding="utf-8")
            (self.root / "local.json").write_text("[]", encoding="utf-8")
            code, out = self.post(self.root / "local.json")
            self.assertEqual(code, 1)
            self.assertIn("git-ignored", out)
        client.assert_not_called()

    def test_pr_post_posts_in_the_persons_name_only(self):
        self.pushed_branch()
        bot = self.bitbucket(me={"type": "app_user", "account_id": "bot-1", "display_name": "Repo token"})
        with mock.patch.object(factory, "bitbucket_client", return_value=bot):
            code, out = self.post(self.plan([{"path": "a", "line": 1, "body": "x"}]))
        self.assertEqual(code, 1)
        self.assertIn("not a person's account", out)
        bot.post_comment.assert_not_called()

    def test_same_commit_needs_seven_characters_in_common(self):
        self.assertTrue(factory.same_commit("a" * 40, "a" * 12))
        self.assertTrue(factory.same_commit("abcdef1", "abcdef1234"))
        self.assertFalse(factory.same_commit("abcdef", "abcdef1234"))
        self.assertFalse(factory.same_commit("abcdef2", "abcdef1234"))
        self.assertFalse(factory.same_commit("abcdef1", ""))

    def test_pr_comments_with_an_id_says_whether_that_comment_is_resolved(self):
        self.pushed_branch()
        client = self.bitbucket()
        client.get.side_effect = [{"id": 1, "resolution": {"type": "comment_resolution"}}, {"id": 2}]
        client.comments.return_value = []
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            _, resolved = self.factory("pr", "comments", "--number", "7", "--comment-id", "1")
            _, open_one = self.factory("pr", "comments", "--number", "7", "--comment-id", "2")
        self.assertIn("#1: resolved", resolved)
        self.assertIn("#2: not resolved", open_one)

    def test_pr_comments_with_an_id_prints_the_thread_its_text_and_its_replies(self):
        self.pushed_branch()
        client = self.bitbucket()
        client.get.return_value = {
            "id": 1, "inline": {"path": "src/A.cs", "to": 12}, "user": {"display_name": "Reviewer"},
            "content": {"raw": "Rename this.\nIt says what it is not."}}
        client.comments.return_value = [
            {"id": 1, "inline": {"path": "src/A.cs", "to": 12}},
            {"id": 2, "parent": {"id": 1}, "user": {"display_name": "Author"}, "content": {"raw": "Done."}},
            {"id": 3, "parent": {"id": 1}, "deleted": True},
            {"id": 4, "parent": {"id": 9}, "content": {"raw": "another thread"}},
        ]
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "comments", "--number", "7", "--comment-id", "1")
        self.assertEqual(code, 0, out)
        self.assertIn("#1: not resolved, 1 reply at src/A.cs:12", out)
        self.assertIn("  Reviewer:\n    Rename this.\n    It says what it is not.", out)
        self.assertIn("reply #2\n  Author:\n    Done.", out)
        self.assertNotIn("another thread", out)

    def test_pr_comments_lists_top_level_inline_positions_only(self):
        self.pushed_branch()
        client = self.bitbucket()
        client.comments.return_value = [
            {"id": 1, "inline": {"path": "src/A.cs", "to": 12}, "user": {"display_name": "Reviewer"}},
            {"id": 2, "inline": {"path": "src/A.cs", "to": 12}, "parent": {"id": 1}},
            {"id": 3, "content": {"raw": "general"}},
        ]
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr", "comments", "--number", "7")
        self.assertEqual(code, 0, out)
        self.assertIn("1 inline, 1 general", out)
        self.assertIn("src/A.cs:12  #1", out)
        self.assertNotIn("#2", out)

    # ---- what a branch changed: its own commits and merges, never dev's

    def branch(self) -> str:
        git(self.root, "checkout", "-q", "--no-track", "-b", "feature/x")
        return git(self.root, "rev-parse", "HEAD")

    def test_dev_merged_in_is_not_the_branchs_change(self):
        base = self.branch()
        (self.root / "docs" / "plans" / "business-units.md").write_text("# Business units, the branch's change\n", encoding="utf-8")
        git(self.root, "commit", "-qam", "The branch's work")
        git(self.root, "checkout", "-q", "dev")
        (self.root / "devtools" / "tooling.py").write_text("# someone else's\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "Other work on dev")
        git(self.root, "update-ref", "refs/remotes/origin/dev", "HEAD")
        git(self.root, "checkout", "-q", "feature/x")
        git(self.root, "merge", "-q", "--no-edit", "origin/dev")
        changed = factory.changed_paths(self.root, base)
        self.assertIn("docs/plans/business-units.md", changed)
        self.assertNotIn("devtools/tooling.py", changed)

    def test_content_written_into_a_merge_counts_as_the_branchs_change(self):
        base = self.branch()
        git(self.root, "checkout", "-q", "dev")
        (self.root / "docs" / "dev-work.md").write_text("dev's own\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "Dev work")
        git(self.root, "update-ref", "refs/remotes/origin/dev", "HEAD")
        git(self.root, "checkout", "-q", "feature/x")
        git(self.root, "merge", "-q", "--no-commit", "--no-ff", "origin/dev")
        migration = self.root / "src" / "App.Persistence" / "Migrations" / "20261001_Sneaky.cs"
        migration.parent.mkdir(parents=True)
        migration.write_text("// schema\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "--no-edit")
        sneaky = "src/App.Persistence/Migrations/20261001_Sneaky.cs"
        changed = factory.changed_paths(self.root, base)
        self.assertIn(sneaky, changed)
        self.assertNotIn("docs/dev-work.md", changed, "dev's own work is still not the branch's")
        # A git that cannot redo the merge (before 2.38) counts the whole merge, never nothing.
        real_git = factory.git
        with mock.patch.object(factory, "git", side_effect=lambda root, *args: (
                (129, "") if args[:1] == ("merge-tree",) else real_git(root, *args))):
            self.assertIn(sneaky, factory.changed_paths(self.root, base))

    def test_another_branch_merged_in_counts_as_the_branchs_change(self):
        base = self.branch()
        git(self.root, "checkout", "-q", "-b", "feature/colleague", "dev")
        (self.root / "devtools" / "tooling.py").write_text("# not in dev yet\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "A colleague's unmerged work")
        git(self.root, "checkout", "-q", "feature/x")
        git(self.root, "merge", "-q", "--no-edit", "feature/colleague")
        self.assertIn("devtools/tooling.py", factory.changed_paths(self.root, base))

    def test_without_a_merge_base_impact_fails_rather_than_reading_nothing_changed(self):
        # PR #335: a failed merge-base fell back to HEAD, the change looked empty, and the tier dropped to checks.
        self.branch()
        git(self.root, "update-ref", "-d", "refs/remotes/origin/dev")
        code, out = self.factory("impact", "--ci")
        self.assertEqual(code, 1, out)
        self.assertIn("merge-base", out)
        self.assertNotIn("tier=checks", out)

    def test_named_paths_need_no_merge_base(self):
        git(self.root, "update-ref", "-d", "refs/remotes/origin/dev")
        code, out = self.factory("impact", "--ci", "docs/defects.md")
        self.assertEqual(code, 0, out)
        self.assertIn("tier=checks", out)

    def test_impact_reads_the_branch_and_says_when_nothing_changed(self):
        self.branch()
        code, out = self.factory("impact")
        self.assertEqual(code, 0, out)
        self.assertIn("factory impact <paths", out)
        country_file = self.root / "src" / "App.Domain" / "Features" / "Geography" / "Country.cs"
        country_file.parent.mkdir(parents=True)
        country_file.write_text("class Country {}\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "Touch Geography")
        code, out = self.factory("impact")
        self.assertEqual(code, 0, out)
        self.assertIn("Geography", out)

    # ---- the branch rules and a PR's description, read back through the command

    def rules_client(self, drop: str = "") -> mock.Mock:
        wanted = factory.load_config(self.root).raw["protection"]
        rules = [{"pattern": branch, "kind": kind, "users": [], "groups": []}
                 for branch in wanted["branches"] for kind in wanted["kinds"]]
        rules += [{"pattern": branch, "kind": kind, "value": value, "users": [], "groups": []}
                  for branch in wanted["branches"] for kind, value in wanted["minimums"].items()]
        client = mock.Mock()
        client.branch_restrictions.return_value = [r for r in rules if not (r["pattern"] == "dev" and r["kind"] == drop)]
        return client

    def test_protection_check_fails_only_when_a_rule_is_missing_and_asked_to(self):
        for drop, argv, code_wanted in (("", ["protection", "--check"], 0), ("force", ["protection", "--check"], 1),
                                        ("force", ["protection"], 0)):
            with self.subTest(drop=drop, argv=argv):
                with mock.patch.object(factory, "bitbucket_client", return_value=self.rules_client(drop)):
                    code, out = self.factory(*argv)
                self.assertEqual(code, code_wanted, out)
                if drop:
                    self.assertIn("dev: missing force", out)

    def test_pr_check_reads_the_pr_named_or_the_pipelines_and_refuses_without_either(self):
        client = mock.Mock()
        client.pull_request.return_value = {"description": "## Summary\nx\n\n## Test plan\ny\n\nEffort: 1 hour\n\n## Local CI\n\n"
                                                           "PASS on `abc123def456` (2026-10-09)\n",
                                            "source": {"branch": {"name": "chore/x"},
                                                       "commit": {"hash": "abc123def456"}}, "title": "x"}
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            self.assertEqual(self.factory("pr-check", "12")[0], 0)
            self.assertEqual(client.pull_request.call_args.args, (12,))
            with mock.patch.dict(os.environ, {"BITBUCKET_PR_ID": "34"}):
                self.assertEqual(self.factory("pr-check")[0], 0)
            self.assertEqual(client.pull_request.call_args.args, (34,))
            with mock.patch.dict(os.environ, {"BITBUCKET_PR_ID": ""}):
                code, out = self.factory("pr-check")
        self.assertEqual(code, 1)
        self.assertIn("Name the PR", out)
        client.pull_request.return_value["description"] = "## Summary\nx\n"
        with mock.patch.object(factory, "bitbucket_client", return_value=client):
            code, out = self.factory("pr-check", "12")
        self.assertEqual(code, 1)
        self.assertIn("test plan", out.lower())

    def test_pr_check_holds_only_the_configured_jira_projects_key_to_the_dev_testing_section(self):
        project = factory.load_config(self.root).raw["jira"]["project"]
        client = mock.Mock()
        for branch, title, code_wanted in ((f"fix/{project}-310-slug", "A change", 1), ("chore/x", f"{project}-310 A change", 1),
                                           ("chore/x", "Read files as UTF-8", 0), ("chore/fsd-V14-2", "Capture V14-2", 0)):
            client.pull_request.return_value = {
                "description": "## Summary\nx\n\n## Test plan\ny\n\nEffort: 1 hour\n\n## Local CI\n\nPASS on `abc123def456` (2026-10-09)\n",
                "source": {"branch": {"name": branch}, "commit": {"hash": "abc123def456"}}, "title": title}
            with self.subTest(branch=branch, title=title), mock.patch.object(factory, "bitbucket_client", return_value=client):
                code, out = self.factory("pr-check", "12")
                self.assertEqual(code, code_wanted, out)
                self.assertEqual("Dev testing" in out, code_wanted == 1, out)

    def test_pr_open_says_when_origin_cannot_be_reached(self):
        body = self.pushed_branch()
        git(self.root, "remote", "set-url", "origin", str(Path(self.folder.name + "-nowhere")))
        with mock.patch.object(factory, "bitbucket_client") as client:
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body))
        self.assertEqual(code, 1)
        self.assertNotIn("Push first", out, "a git that failed is not an unpushed commit")
        client.assert_not_called()


@unittest.skipUnless(shutil.which("git"), "needs git")
class SyncPlanTests(unittest.TestCase):
    """
    `factory sync-plan`, /book:sync-sources's Phase 0: the SHAs pinned, the last-sync line read from
    origin/dev, the mode and the delta set, QA's deployed commit through `deploy status` — with SSH faked
    as test_deploy.py fakes it, so nothing reaches a server — and nothing written.
    """

    @classmethod
    def setUpClass(cls):
        """The repository is built once and copied for each test, as RepositoryTests does: git is slow to start."""
        cls.template = tempfile.TemporaryDirectory()
        cls.root = Path(cls.template.name)
        (cls.root / "devtools" / "factory").mkdir(parents=True)
        (cls.root / factory.CONFIG_RELATIVE).write_text(fixture_project.PROJECT_TOML, encoding="utf-8")
        (cls.root / "docs" / "plans").mkdir(parents=True)
        (cls.root / "src").mkdir()
        git(cls.root, "init", "-q", "-b", "dev")
        git(cls.root, "config", "user.name", "Test Person")
        git(cls.root, "config", "user.email", "person@example.invalid")
        git(cls.root, "config", "core.autocrlf", "false")
        cls.write(cls, "docs/plans/stores.md", "# Stores\n")
        cls.write(cls, "src/App.cs", "class App {}\n")
        cls.qa = cls.commit(cls, "What QA runs")
        git(cls.root, "update-ref", "refs/remotes/origin/qa", "HEAD")
        cls.write(cls, "docs/plans/stores.md", "# Stores, before the sync\n")
        cls.synced = cls.commit(cls, "Before the last sync")
        cls.write(cls, "docs/project.md", "# Project\n\n## Part 3\n\n**Last requirements sync:** 2026-11-16 · dev "
                                         f"`{cls.synced[:8]}` · delta, 3 files: notes · tokens not measured\n")
        cls.commit(cls, "Record the sync")
        cls.write(cls, "docs/plans/stores.md", "# Stores, after the sync\n")
        cls.write(cls, "src/App.cs", "class App { int x; }\n")
        cls.write(cls, "README.md", "# Readme\n")
        cls.dev = cls.commit(cls, "Work since the sync")
        git(cls.root, "update-ref", "refs/remotes/origin/dev", "HEAD")

    @classmethod
    def tearDownClass(cls):
        cls.template.cleanup()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / "repo"
        shutil.copytree(self.template.name, self.root)
        env = {key: value for key, value in os.environ.items() if key not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        env["FACTORY_ROOT"] = str(self.root)
        # The deploy ledger, beside a private settings file that lives in this test's own folder.
        env["APP_LOCAL_SETTINGS"] = str(Path(self.folder.name) / "private" / "local.toml")
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        saved = factory.deploy.RUN
        self.addCleanup(setattr, factory.deploy, "RUN", saved)
        self.ssh: list[str] = []

    def write(self, rel: str, text: str) -> None:
        (self.root / rel).write_text(text, encoding="utf-8")

    def commit(self, message: str) -> str:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", message)
        return git(self.root, "rev-parse", "HEAD")

    def host(self, code: int = 0, head: str = "") -> None:
        """deploy.py's runner: the QA host answers the status script; deploy's own git calls answer empty."""
        def run(argv, *, cwd=None, stdin=None, timeout=0, env=None):
            if argv[0] == "ssh":
                self.ssh.append(stdin or "")
                if code:
                    return factory.deploy.Done(code, "ssh: connect to host port 22: Connection timed out")
                return factory.deploy.Done(0, f"== head\n{head or self.qa}\n== branch\ndev\n== committed\n"
                                              "2026-11-15T10:00:00+00:00\n== api\n"
                                              "2026-11-16 09:00:00 +0000 UTC|Up 3 hours\n== end\n")
            return factory.deploy.Done(0, "")
        factory.deploy.RUN = run

    def plan(self, *argv: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = factory.main(["sync-plan", *argv])
        return code, out.getvalue() + err.getvalue()

    def test_it_pins_the_shas_reads_the_last_sync_and_works_out_the_delta_set(self):
        self.host()
        before = git(self.root, "status", "--porcelain", "--ignored")
        code, out = self.plan()
        self.assertEqual(code, 0, out)
        self.assertIn(f"origin/dev   {self.dev}", out)
        self.assertIn(f"origin/qa    {self.qa} — the qa branch: NOT the QA baseline", out)
        self.assertIn("checkout     dev, clean", out)
        self.assertIn(f"2026-11-16 · dev {self.synced[:8]} · delta", out)
        self.assertIn("Mode: delta (suggested)", out)
        delta = out.split("Delta set:")[1].split("plus every doc")[0]
        self.assertIn("3 file(s)", delta)
        for path in ("docs/plans/stores.md", "docs/project.md", "README.md"):
            self.assertIn(path, delta)
        self.assertNotIn("src/App.cs", delta, "the delta set is docs, CLAUDE.md and README.md only")
        self.assertIn("plus every doc that cites a section", out)
        self.assertEqual(before, git(self.root, "status", "--porcelain", "--ignored"), "read-only: nothing written")

    def test_qa_baseline_is_the_deployed_commit_read_live_through_deploy_status(self):
        self.host()
        code, out = self.plan()
        self.assertEqual(code, 0, out)
        self.assertIn("QA — live read of", out)
        self.assertIn(f"→ baseline: {self.qa[:12]} (live read)", out)
        self.assertIn(f"DEV minus QA's deployed commit ({self.qa[:12]}..{self.dev[:12]}): 3 commit(s)", out)
        self.assertIn("by area: README.md 1, docs 1, docs/plans 1, src 1", out)
        self.assertIn(f"task-evidence.py --since {self.qa[:12]}", out)
        self.assertIn("DEV minus the qa branch, for reference only", out)
        self.assertEqual(len(self.ssh), 1, "one read of QA, never DEV")
        for word in (".env", "pull", "fetch", "checkout", "docker compose", " up "):
            self.assertNotIn(word, self.ssh[0], "the status script reads and changes nothing")

    def test_an_unreachable_host_says_could_not_read_and_the_plan_carries_on(self):
        self.host(code=255)
        code, out = self.plan()
        self.assertEqual(code, 0, out)
        self.assertIn("COULD NOT READ LIVE", out)
        self.assertIn("→ baseline: none — stop and ask the person; never fall back to the qa branch", out)
        self.assertIn("no QA baseline — Phase 5 waits for the person", out)
        self.assertNotIn("DEV minus QA's deployed commit", out)
        self.assertIn("Mode: delta", out, "the rest of the plan still runs")

    def test_a_ledger_record_is_a_baseline_the_person_confirms(self):
        self.host(code=255)
        ledger = Path(self.folder.name) / "private" / factory.deploy.LEDGER_NAME
        ledger.parent.mkdir(parents=True)
        ledger.write_text('{"target": "QA", "commit": "%s", "when": "2026-11-15T10:00:00Z", "verdict": "passed", '
                          '"branch": "dev"}\n' % self.qa, encoding="utf-8")
        code, out = self.plan()
        self.assertEqual(code, 0, out)
        self.assertIn(f"→ baseline: {self.qa[:12]} from the ledger — NOT a live read: the person confirms it first", out)

    def test_a_deploy_status_that_refuses_is_could_not_read(self):
        with mock.patch.object(factory.deploy, "command_status",
                               side_effect=factory.deploy.DeployError("factory.toml [deploy] has no ssh_host yet")):
            code, out = self.plan()
        self.assertEqual(code, 0, out)
        self.assertIn("COULD NOT READ — factory.toml [deploy] has no ssh_host yet", out)

    def test_no_last_sync_line_means_full_and_an_asked_mode_wins(self):
        self.host()
        self.write("docs/project.md", "# Project\n")
        self.commit("No sync recorded")
        git(self.root, "update-ref", "refs/remotes/origin/dev", "HEAD")
        code, out = self.plan()
        self.assertEqual(code, 0, out)
        self.assertIn("none — run full", out)
        self.assertIn("Mode: full (suggested)", out)
        self.assertIn("Phase 3  skip when the delta set is empty (now unknown)", out)
        code, out = self.plan("--mode", "verify-only")
        self.assertIn("Mode: verify-only (asked)", out)

    def test_skip_rules_are_printed_for_the_skill_never_decided(self):
        self.host()
        code, out = self.plan()
        rules = out.split("Skip rules")[1]
        self.assertIn("this tool decides none", rules)
        self.assertIn("Phase 2  skip when no source moved", rules)
        self.assertIn("(now 3 file(s))", rules)
        self.assertIn("Phase 5  never skipped", rules)
        self.assertIn("sh devtools/checks/ci-local.sh", out)

    def test_a_line_with_its_revisions_in_brackets_is_read(self):
        self.host()
        self.write("docs/project.md", "# Project\n\n# Part 3\n\n**Last requirements sync:** 2026-11-03 · dev "
                                      f"`{self.synced[:8]}` (FSD V29, matrix V11) · phases 1 and 5 only — notes\n")
        self.commit("Record the sync as this repository writes it")
        git(self.root, "update-ref", "refs/remotes/origin/dev", "HEAD")
        code, out = self.plan()
        self.assertEqual(code, 0, out)
        self.assertIn(f"2026-11-03 · dev {self.synced[:8]} · phases 1 and 5 only", out)
        self.assertIn("Mode: delta (suggested)", out)

    def test_without_origin_dev_it_stops(self):
        git(self.root, "update-ref", "-d", "refs/remotes/origin/dev")
        code, out = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("origin/dev does not resolve", out)


class FsdThreadsTests(unittest.TestCase):
    """`factory fsd-threads`: the right Doc, the threads as a snapshot, and never a quiet pass."""

    DOC = "FsdDoc0000000000000000000000"

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        fsd = self.root / "requirements" / "fsd"
        fsd.mkdir(parents=True)
        (fsd / "read-me.txt").write_text(
            'Last checked 2026-10-01 (modifiedTime 2026-10-01T05:00:00Z), threads read through '
            '"modified after 2026-10-01T06:00:00Z"\n', encoding="utf-8")
        (self.root / "requirements" / "read-me.txt").write_text(
            "=== LINKS ===\n"
            "Backend Questions (Product Q&A) Google Doc: https://docs.google.com/document/d/QandA0000000000000000000/edit\n"
            f"FSD Google Doc: https://docs.google.com/document/d/{self.DOC}/edit\n", encoding="utf-8")
        self.sources = {"fsd_dir": "requirements/fsd", "live_check_record": "requirements/fsd/read-me.txt",
                        "links_file": "requirements/read-me.txt", "fsd_link_label": "FSD",
                        "live_credential": "rclone:test-remote"}

    def tearDown(self):
        self.folder.cleanup()

    def threads(self, save: bool = False, sources: dict | None = None, **patches) -> str:
        config = factory.Config({"requirements": {"sources": sources if sources is not None else self.sources},
                                 "private_settings": "~/.app/local.toml"})
        defaults = {"access_token": mock.Mock(return_value="t"),
                    "modified_time": mock.Mock(return_value="2026-10-01T05:00:00Z"),
                    "comment_threads": mock.Mock(return_value=[
                        {"id": "a", "modifiedTime": "2026-10-01T05:30:00Z", "resolved": True},
                        {"id": "b", "modifiedTime": "2026-10-02T09:00:00Z", "resolved": False}])}
        defaults.update(patches)
        with contextlib.ExitStack() as stack:
            for name, value in defaults.items():
                stack.enter_context(mock.patch.object(factory.drive, name, value))
            return factory.command_fsd_threads(self.root, config, mock.Mock(save=save))

    def test_the_fsd_link_is_read_from_the_one_links_file_by_its_label(self):
        # Every link lives in requirements/read-me.txt; another Doc listed first must not be taken for the FSD.
        self.assertEqual(factory.fsd_doc_id(self.root, self.sources, ""), self.DOC)
        self.assertEqual(factory.fsd_doc_id(self.root, {**self.sources, "fsd_link_label": ""}, ""), "",
                         "no label, no guess")

    def test_saved_threads_keep_the_question_the_text_and_the_answer(self):
        threads = [{"id": "q1", "createdTime": "2026-09-01T00:00:00Z", "modifiedTime": "2026-09-02T00:00:00Z",
                    "resolved": True, "author": {"displayName": "A Developer"}, "content": "Is Type required?",
                    "quotedFileContent": {"value": "Type"},
                    "replies": [{"author": {"displayName": "The BA"}, "content": "Yes, required.",
                                 "modifiedTime": "2026-09-02T00:00:00Z"}]}]
        text = factory.render_threads(threads, "2026-10-01T05:32:39Z", "2026-10-03")
        for expected in ("## Resolved · 2026-09-02T00:00:00Z · `q1`", "> Type", "**A Developer:** Is Type required?",
                         "**The BA** (2026-09-02T00:00:00Z): Yes, required.", "1 threads, 0 open"):
            self.assertIn(expected, text)

    def test_saved_threads_name_a_mention_without_its_email_address(self):
        # A Doc comment @-mentions by address; the snapshot is committed, and no person's email goes in it.
        threads = [{"id": "q1", "createdTime": "2026-09-01T00:00:00Z", "modifiedTime": "2026-09-02T00:00:00Z",
                    "author": {"displayName": "A Developer"}, "content": "@the.ba@example.co.uk [Q1] Is Type required?",
                    "quotedFileContent": {"value": "Ask +someone@example.com"},
                    "replies": [{"author": {"displayName": "The BA"}, "content": "cc reviewer@example.com",
                                 "modifiedTime": "2026-09-02T00:00:00Z"}]}]
        text = factory.render_threads(threads, "2026-10-01T05:32:39Z", "2026-10-03")
        self.assertNotIn("example.", text)
        for expected in ("**A Developer:** @the.ba [Q1] Is Type required?", "> Ask @someone", "cc @reviewer"):
            self.assertIn(expected, text)

    def test_threads_changed_are_counted_from_the_last_read_through(self):
        out = self.threads()
        self.assertIn("1 changed after 2026-10-01T06:00:00Z", out)
        self.assertIn("recorded 2026-10-01T05:00:00Z", out)

    def test_without_a_credential_it_names_the_private_file_and_reads_nothing(self):
        sources = {key: value for key, value in self.sources.items() if key != "live_credential"}
        with self.assertRaises(factory.FactoryError) as raised:
            self.threads(sources=sources)
        self.assertIn("live_credential", str(raised.exception))
        self.assertIn("private settings file", str(raised.exception))

    def test_drive_not_answering_is_said_never_passed(self):
        with self.assertRaises(factory.FactoryError) as raised:
            self.threads(access_token=mock.Mock(side_effect=factory.drive.DriveError("401")))
        self.assertIn("Could not ask Google Drive", str(raised.exception))
        # And the command fails: a script or a skill reading only the exit code must not take it for a pass.
        config = factory.Config({"requirements": {"sources": self.sources}, "private_settings": "~/.app/local.toml"})
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(factory, "repo_root", return_value=self.root), \
                mock.patch.object(factory, "load_config", return_value=config), \
                mock.patch.object(factory.drive, "access_token", side_effect=factory.drive.DriveError("401")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = factory.main(["fsd-threads"])
        self.assertEqual(code, 1)
        self.assertIn("Could not ask Google Drive", err.getvalue())
        self.assertEqual(out.getvalue(), "")

    def test_a_save_never_overwrites_a_snapshot(self):
        today = factory.utc_now()[:10]
        out = self.threads(save=True)
        saved = self.root / "requirements" / "fsd" / f"comment-threads - {today}.md"
        self.assertTrue(saved.is_file(), out)
        with self.assertRaises(factory.FactoryError) as raised:
            self.threads(save=True)
        self.assertIn("already exists", str(raised.exception))



class BookConfigTests(unittest.TestCase):
    def test_book_verify_stops_on_a_configuration_it_cannot_read_instead_of_checking_an_empty_one(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / factory.CONFIG_RELATIVE
            config.parent.mkdir(parents=True)
            config.write_text("this is = = not toml", encoding="utf-8")
            with self.assertRaises(factory.FactoryError) as raised:
                factory.command_book(root, None, None)
        self.assertIn("unreadable", str(raised.exception))


class FsdPageNumberedTests(unittest.TestCase):
    """A numbered FSD (`2.2.7. Pagination …`): found by number or words, through a zero-width space after the
    number, a number set apart from its words, and contents that end part-way down a page of text."""

    CONTENTS = ("1. Table of Contents.............................. 2\n"
                "2. Product Specifications......................... 3\n"
                "    2.1. User Authentication...................... 3\n"
                "         2.1.1. Login Using Email & Password...... 3\n")
    # The contents' second page restarts its indents, and the text begins below its last line.
    LAST = ("2.2. Global Application Requirements............. 4\n"
            "     2.2.7. Pagination for Listing Screens....... 4\n"
            "     2.2.8. Toast Messages:...................... 5\n"
            "\n2.\u200b  Product Specifications\n2.1.\u200b  User Authentication\n"
            "2.1.1.\u200b  Login Using Email & Password\n1. Enter the email.\n")
    BODY = ["2.2.\u200b  Global Application Requirements\n"
            "2.2.7.\u200b  Pagination for Listing Screens\n1. Twenty rows a page.\n",
            "2.2.8.          1. Shown for three seconds.\n      Toast Messages:\n"]

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.fsd = self.root / "requirements" / "fsd"
        self.fsd.mkdir(parents=True)
        for n in (9, 30):
            (self.fsd / f"Product - Functional Specifications - V{n}.pdf").write_bytes(b"%PDF-1.4")
        self.config = factory.Config({"requirements": {"sources": {"fsd_dir": "requirements/fsd"}}})

    def page(self, section: str) -> tuple[str, bool]:
        pages = ["Title page\n", self.CONTENTS, self.LAST, *self.BODY]
        with mock.patch.object(factory, "pdf_pages", return_value=pages):
            return factory.command_fsd_page(self.root, self.config, mock.Mock(section=section, print=False))

    def test_by_number_or_by_words(self):
        for section in ("2.2.7", "2.2.7.", "Pagination for Listing Screens", "2.2.7. Pagination for Listing Screens"):
            out, ok = self.page(section)
            self.assertTrue(ok, out)
            self.assertIn("2. Product Specifications › 2.2. Global Application Requirements › "
                          "2.2.7. Pagination for Listing Screens: PDF page 4 (contents page 4)", out)
            self.assertIn("V30.pdf", out)
            self.assertNotIn("2.2.8", out, section)

    def test_the_text_starts_below_the_contents_on_their_last_page(self):
        out, ok = self.page("Login Using Email & Password")
        self.assertTrue(ok, out)
        self.assertIn("2.1.1. Login Using Email & Password: PDF page 3", out)

    def test_a_number_set_apart_from_its_words_is_found(self):
        out, ok = self.page("2.2.8")
        self.assertTrue(ok, out)
        self.assertIn("2.2.8. Toast Messages:: PDF page 5", out)
        out, _ = self.page("2.2")
        self.assertIn("2.2. Global Application Requirements: PDF pages 4–5", out)

    def test_a_number_not_in_the_contents_is_not_guessed(self):
        out, ok = self.page("2.2.9")
        self.assertFalse(ok)
        self.assertIn("not found, not guessed", out)
INDEX = """Master index.

=== LINKS - every live requirements source, in one place ===

FSD (specification document): https://docs.google.com/document/d/abcdefabcdefabcdefabcdef/edit
Permissions matrix: none - every permission is in the FSD (owner, 2027-01-01)
QA artifacts folder: https://drive.google.com/drive/folders/qa
QA smoke sheet: https://docs.google.com/spreadsheets/d/smoke/edit
FE integration bugs sheet: not set up yet (owner, 2027-01-01)
Release plan sheet: <link>
Wireframes / design board: https://www.figma.com/design/board

(Delete the lines the project does not have.)

=== HAS A LIVE LINK (the link is in LINKS, above) ===

fsd/
  -> the specification
qa-artifacts/
  -> QA's own
wireframes/ (wireframes-V1/, ...)
  -> designs

=== NO LIVE SOURCE (delivered directly by the client; nothing to fetch or diff) ===

permissions-matrix/
  -> none exists
email-templates/
  -> as delivered

=== NO LIVE LINK APPLICABLE (source is our own code, not a client document) ===

fe-api-contracts/FE-API-CONTRACTS.md
  -> what the frontend calls
"""
FOLDERS = {"FSD": "fsd", "Permissions matrix": "permissions-matrix", "QA": "qa-artifacts",
           "FE integration bugs": "fe-integration-bugs", "Release plan": "release-plan", "Wireframes": "wireframes"}


class FsdPageTests(unittest.TestCase):
    """`factory fsd-page`: the highest revision numerically, the section's pages, and never a guess."""

    CONTENTS = ("Shop App Specifications........................ 3\n"
                "    Onboarding and Sign In........................ 3\n"
                "         Sign Up................................... 3\n"
                "               Flow of Events...................... 4\n"
                "         Log In.................................... 5\n"
                "               Flow of Events...................... 5\n"
                "    Company Management............................ 6\n"
                "         Garbled Heading........................... 6\n")
    # `-layout` indents the second contents page from its own shallowest entry.
    CONTENTS_2 = ("Store Management.................................. 7\n"
                  "     Stores Listing............................... 7\n"
                  "          Flow of Events......................... 7\n")
    BODY = ["Shop App Specifications\n\nOnboarding and Sign In\nSign Up\n1. A user signs up.\n",
            "Flow of Events\n1. The code is sent.\n",
            "Log In\n1. Welcome back.\nFlow of Events\n1. Logged in.\n",
            "    Company Management\n1. A company has a name.\n",
            "Store Management\nStores Listing\nFlow of Events\n1. Newest first.\n"]

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.fsd = self.root / "requirements" / "fsd"
        self.fsd.mkdir(parents=True)
        for n in (2, 9, 10):
            (self.fsd / f"Product - Functional Specifications - V{n}.pdf").write_bytes(b"%PDF-1.4 V" + str(n).encode())
        (self.fsd / "Product - Functional Specifications - V11.docx").write_bytes(b"not a pdf")
        self.config = factory.Config({"requirements": {"sources": {"fsd_dir": "requirements/fsd"}}})

    def pages(self) -> list[str]:
        return ["Title page\n", self.CONTENTS, self.CONTENTS_2, *self.BODY]

    def page(self, section: str, pages=None, print_text: bool = False) -> tuple[str, bool]:
        args = mock.Mock(section=section, print=print_text)
        with mock.patch.object(factory, "pdf_pages", return_value=self.pages() if pages is None else pages) as read:
            result = factory.command_fsd_page(self.root, self.config, args)
        if pages is not False:
            self.assertEqual(read.call_args.args[0].name, "Product - Functional Specifications - V10.pdf")
        return result

    def test_the_highest_revision_is_chosen_numerically(self):
        self.assertEqual(factory.latest_fsd(self.root, {"fsd_dir": "requirements/fsd"}).name,
                         "Product - Functional Specifications - V10.pdf")

    def test_a_section_spans_to_the_next_heading_at_its_level(self):
        out, ok = self.page("log in")
        self.assertTrue(ok)
        self.assertIn("Shop App Specifications › Onboarding and Sign In › Log In: PDF page 6 (contents page 5)", out)
        self.assertIn('pdftotext -layout -f 6 -l 6 "requirements/fsd/Product - Functional Specifications - V10.pdf" -',
                      out)
        out, _ = self.page("Onboarding and Sign In")
        self.assertIn("Onboarding and Sign In: PDF pages 4–6", out, "its sub-sections are in it; Company Management "
                                                                   "starts a page of its own")
        out, _ = self.page("Sign Up")
        self.assertIn("Sign Up: PDF pages 4–5", out)

    def test_the_contents_levels_hold_across_contents_pages(self):
        out, ok = self.page("Store Management")
        self.assertTrue(ok)
        self.assertIn("Shop App Specifications › Store Management: PDF page 8", out, "a sibling of Company Management")
        self.assertIn("Company Management: PDF page 7 (", self.page("Company Management")[0],
                      "Store Management opens page 8")
        pages = self.pages()
        pages[-1] = "3. The company is kept.\n" + pages[-1]
        self.assertIn("Company Management: PDF pages 7–8", self.page("Company Management", pages)[0],
                      "Store Management starts below Company Management's text on page 8")

    def test_a_repeated_heading_is_listed_under_each_parent(self):
        out, ok = self.page("Flow of Events")
        self.assertTrue(ok)
        self.assertIn("Sign Up › Flow of Events: PDF page 5", out)
        self.assertIn("Log In › Flow of Events: PDF page 6", out)
        self.assertIn("Stores Listing › Flow of Events: PDF page 8", out)

    def test_print_adds_the_pages_text(self):
        out, _ = self.page("Log In", print_text=True)
        self.assertIn("1. Welcome back.", out)
        self.assertNotIn("A company has a name", out)

    def test_a_heading_not_found_is_said_and_never_guessed(self):
        out, ok = self.page("Garbled Heading")
        self.assertFalse(ok)
        self.assertIn("its heading was not found in the text", out)
        self.assertNotIn("PDF page", out)
        out, ok = self.page("Customers")
        self.assertFalse(ok)
        self.assertIn("not found, not guessed", out)

    def test_without_pdftotext_it_prints_the_pdf_and_fails(self):
        with mock.patch.object(factory.shutil, "which", return_value=None):
            out, ok = factory.command_fsd_page(self.root, self.config, mock.Mock(section="Log In", print=False))
        self.assertFalse(ok)
        self.assertIn("requirements/fsd/Product - Functional Specifications - V10.pdf", out)
        self.assertIn("pdftotext is not installed", out)
        self.assertNotIn("PDF page", out)

    def test_it_runs_pdftotext_printing_and_never_writes_under_requirements(self):
        before = {p: p.read_bytes() for p in self.fsd.iterdir()}
        done = subprocess.CompletedProcess([], 0, stdout="\f".join(self.pages()).encode() + b"\f", stderr=b"")
        with mock.patch.object(factory.shutil, "which", return_value="pdftotext"), \
                mock.patch.object(factory.subprocess, "run", return_value=done) as run:
            out, ok = factory.command_fsd_page(self.root, self.config, mock.Mock(section="Log In", print=False))
        self.assertTrue(ok, out)
        self.assertEqual(run.call_args.args[0][-1], "-", "pdftotext prints; it never writes a file beside the PDF")
        self.assertEqual({p: p.read_bytes() for p in self.fsd.iterdir()}, before)

    def test_no_revision_is_a_refusal(self):
        for path in self.fsd.glob("*.pdf"):
            path.unlink()
        with self.assertRaises(factory.FactoryError):
            factory.command_fsd_page(self.root, self.config, mock.Mock(section="Log In", print=False))
INDEX = """Master index.

=== LINKS - every live requirements source, in one place ===

FSD (specification document): https://docs.google.com/document/d/abcdefabcdefabcdefabcdef/edit
Permissions matrix: none - every permission is in the FSD (owner, 2027-01-01)
QA artifacts folder: https://drive.google.com/drive/folders/qa
QA smoke sheet: https://docs.google.com/spreadsheets/d/smoke/edit
FE integration bugs sheet: not set up yet (owner, 2027-01-01)
Release plan sheet: <link>
Wireframes / design board: https://www.figma.com/design/board

(Delete the lines the project does not have.)

=== HAS A LIVE LINK (the link is in LINKS, above) ===

fsd/
  -> the specification
qa-artifacts/
  -> QA's own
wireframes/ (wireframes-V1/, ...)
  -> designs

=== NO LIVE LINK ===

permissions-matrix/
  -> none exists
email-templates/
  -> as delivered
"""
FOLDERS = {"FSD": "fsd", "Permissions matrix": "permissions-matrix", "QA": "qa-artifacts",
           "FE integration bugs": "fe-integration-bugs", "Release plan": "release-plan", "Wireframes": "wireframes"}


@unittest.skipUnless(shutil.which("git"), "needs git")
class RequirementsStatusTests(unittest.TestCase):
    """`factory requirements status`: the index and our snapshots, offline; nothing live is ever up to date."""

    TODAY = __import__("datetime").date(2027, 1, 6)  # a Wednesday; its week began 2027-01-04

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name).resolve()
        git(self.root, "init", "-q", "-b", "dev")
        self.write("requirements/read-me.txt", INDEX)
        self.write("requirements/fsd/read-me.txt", "Versioning.\n\nLast checked: 2027-01-05 - captured V10 (modifiedTime x).\n")
        self.write("requirements/fsd/App - Functional Specifications - V9.pdf", "nine")
        self.write("requirements/fsd/comment-threads - 2027-01-06.md", "threads")
        self.write("requirements/qa-artifacts/read-me.txt", "Last checked: 2026-12-20 - unchanged.\n")
        self.write("requirements/qa-artifacts/Checklist.xlsx", "old")
        self.write("requirements/permissions-matrix/read-me.txt", "Last checked: 2026-12-01 - none exists.\n")
        self.write("requirements/permissions-matrix/.gitkeep", "")
        self.write("requirements/wireframes/wireframes-V2/board.png", "png")
        self.write("requirements/wireframes/wireframes-V10/board.png", "png")
        self.commit("2026-12-20")
        self.write("requirements/fsd/App - Functional Specifications - V10.pdf", "ten")
        self.commit("2027-01-05")

    def tearDown(self):
        self.folder.cleanup()

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, day: str) -> None:
        stamp = f"{day}T12:00:00"
        with mock.patch.dict(os.environ, {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}):
            git(self.root, "add", "-A")
            git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q",
                "--no-verify", "-m", "Capture")

    def status(self) -> str:
        config = factory.Config({"base_ref": "origin/dev", "requirements": {"sources": {
            "links_file": "requirements/read-me.txt", "folders": FOLDERS, "out_of_scope": ["wireframes"]}}})
        # Offline: a call to Drive, or any fetch, fails the test.
        with mock.patch.object(factory.drive, "access_token", side_effect=AssertionError("fetched Drive")), \
                mock.patch.object(factory.drive, "modified_time", side_effect=AssertionError("fetched Drive")):
            return factory.requirements_status(self.root, config, self.TODAY)

    def line(self, out: str, label: str) -> str:
        return next(line for line in out.splitlines() if line.startswith(f"- {label} "))

    def test_a_live_source_needs_a_live_check_and_is_never_up_to_date(self):
        out = self.status()
        verdict = out.splitlines()[0]
        self.assertTrue(verdict.startswith("COULD NOT VERIFY — 3 source(s) need a live check"), verdict)
        self.assertNotIn("UP TO DATE", out)
        self.assertIn("needs a live check", self.line(out, "FSD (specification document)"))
        self.assertIn("nothing was fetched", out)

    def test_the_newest_snapshot_is_the_highest_version_numerically(self):
        fsd = self.line(self.status(), "FSD (specification document)")
        self.assertIn("newest 'App - Functional Specifications - V10.pdf' (2027-01-05)", fsd)
        self.assertIn("last checked 2027-01-05 (captured V10)", fsd)

    def test_a_capture_this_week_closes_the_week(self):
        fsd = self.line(self.status(), "FSD (specification document)")
        self.assertIn("capture: taken this week (2027-01-05), the next from 2027-01-11", fsd)

    def test_a_comment_threads_record_is_not_a_capture(self):
        out = self.status()
        self.assertNotIn("comment-threads", out)

    def test_an_open_week_says_so(self):
        qa = self.line(self.status(), "QA artifacts folder")
        self.assertIn("newest 'Checklist.xlsx' (2026-12-20)", qa)
        self.assertIn("capture: open this week (last 2026-12-20)", qa)

    def test_sources_sharing_a_folder_show_it_once(self):
        self.assertIn("qa-artifacts/; as above; needs a live check", self.line(self.status(), "QA smoke sheet"))

    def test_none_and_not_set_up_carry_the_index_reason(self):
        out = self.status()
        matrix = self.line(out, "Permissions matrix")
        self.assertIn("none: every permission is in the FSD (owner, 2027-01-01)", matrix)
        self.assertIn("no snapshot", matrix)  # .gitkeep is not one
        self.assertIn("not set up: not set up yet (owner, 2027-01-01)", self.line(out, "FE integration bugs sheet"))

    def test_a_placeholder_link_is_not_configured_and_not_counted(self):
        out = self.status()
        self.assertIn("not configured", self.line(out, "Release plan sheet"))
        self.assertNotIn("Release plan sheet", out.splitlines()[0])

    def test_an_out_of_scope_source_is_listed_but_not_counted(self):
        out = self.status()
        wireframes = self.line(out, "Wireframes / design board")
        self.assertIn("out of scope", wireframes)
        self.assertIn("newest 'wireframes-V10/'", wireframes)
        self.assertNotIn("Wireframes", out.splitlines()[0])

    def test_a_folder_with_no_live_link_is_nothing_to_check(self):
        self.assertIn("- email-templates/ — no live link: delivered files or our own code, nothing to check", self.status())

    def test_a_move_recorded_and_not_captured_is_stale(self):
        self.write("requirements/qa-artifacts/read-me.txt",
                   "Last checked: 2027-01-05 - moved, not captured: the checklist changed.\n")
        verdict = self.status().splitlines()[0]
        self.assertTrue(verdict.startswith("STALE — 1 source folder(s) recorded as moved and not yet captured: qa-artifacts/;"),
                        verdict)

    def test_the_cli_takes_status_only(self):
        self.assertEqual(factory.parser().parse_args(["requirements", "status"]).action, "status")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            factory.parser().parse_args(["requirements", "capture"])


class SnapshotsTests(unittest.TestCase):
    """The index read as data (snapshots.py), shared with check-protected-paths."""

    def test_link_states(self):
        self.assertEqual(snapshots.link_state("https://x/y"), ("live", "https://x/y"))
        self.assertEqual(snapshots.link_state("none - no such document"), ("none", "no such document"))
        self.assertEqual(snapshots.link_state("not shared yet"), ("not shared", "not shared yet"))
        self.assertEqual(snapshots.link_state("<link>")[0], "not configured")

    def test_folder_sections(self):
        self.assertEqual(snapshots.live_folders(INDEX), ["fsd", "qa-artifacts", "wireframes"])
        self.assertEqual(snapshots.folders(INDEX, snapshots.NO_LIVE_SECTIONS), ["permissions-matrix", "email-templates"])
        # An index may split NO LIVE LINK into the client's delivered files and our own code.
        split = ("=== NO LIVE SOURCE (delivered directly by the client) ===\n\nemail-templates/\n\n"
                 "=== NO LIVE LINK APPLICABLE (our own code) ===\n\nfe-api-contracts/\n")
        self.assertEqual(snapshots.folders(split, snapshots.NO_LIVE_SECTIONS), ["email-templates", "fe-api-contracts"])
        self.assertEqual([label for label, _ in snapshots.links(INDEX)][:2], ["FSD (specification document)",
                                                                             "Permissions matrix"])

    def test_what_a_capture_is(self):
        self.assertTrue(snapshots.is_snapshot("requirements/fsd/X - V2.pdf", "requirements/fsd"))
        for path in ("requirements/fsd/read-me.txt", "requirements/fsd/comment-threads - 2027-01-01.md",
                     "requirements/qa/backup/X - 2027-01-01.xlsx", "requirements/fsd/.gitkeep", "requirements/other/X.pdf"):
            self.assertFalse(snapshots.is_snapshot(path, "requirements/fsd" if "/fsd/" in path or "other" in path
                                                   else "requirements/qa"), path)

    def test_versions_sort_numerically(self):
        self.assertGreater(snapshots.version("FSD - V14.pdf"), snapshots.version("FSD - V9.pdf"))
        self.assertEqual(snapshots.version("NBR01 079v4 - WIFI.pdf"), -1)

    def test_last_checked(self):
        self.assertEqual(snapshots.last_checked("x\nLast checked: 2027-01-05 - captured V14 (modifiedTime t).\n"),
                         ("2027-01-05", "captured V14"))
        self.assertEqual(snapshots.last_checked("no line"), ("", ""))


if __name__ == "__main__":
    unittest.main()
