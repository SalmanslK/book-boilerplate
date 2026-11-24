
"""
Self-tests for the checks in devtools/checks — each one fails what it should and passes what it should.

    python3 -m unittest discover -s devtools/checks -p "test_*.py" -v

Standard library only; no network. Git runs in throwaway repositories with no global or system
config, so the developer's own identity, hooks and settings never reach a result.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_saved_env: dict[str, str | None] = {}
_empty_config = tempfile.NamedTemporaryFile(delete=False)
_empty_config.close()


def setUpModule() -> None:
    # The packs' test modules import this pair, so it runs once per module in one process: the empty
    # global config is made again here, after an earlier module's teardown removed it.
    open(_empty_config.name, "a").close()
    # No automatic maintenance: newer git starts it in the background after a merge or commit, and it
    # can still be writing to .git/objects while a test deletes the repository.
    isolated = {"GIT_CONFIG_GLOBAL": _empty_config.name, "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CEILING_DIRECTORIES": str(Path(tempfile.gettempdir()).resolve()),
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "maintenance.auto", "GIT_CONFIG_VALUE_0": "false",
                "GIT_CONFIG_KEY_1": "gc.auto", "GIT_CONFIG_VALUE_1": "0"}
    for key, value in isolated.items():
        _saved_env[key] = os.environ.get(key)
        os.environ[key] = value


def tearDownModule() -> None:
    for key, value in _saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    with contextlib.suppress(FileNotFoundError):
        os.unlink(_empty_config.name)


def find_sh() -> str | None:
    """
    The sh git runs the hooks with: on PATH, or - on Windows, where only Git Bash puts it on PATH - the one
    Git for Windows ships (bin/sh.exe, which sets up its own tools, else usr/bin/sh.exe), found from git.
    """
    found = shutil.which("sh")
    if found:
        return found
    done = subprocess.run(["git", "--exec-path"], capture_output=True, text=True)
    for root in Path(done.stdout.strip()).parents if done.returncode == 0 and done.stdout.strip() else []:
        for candidate in (root / "bin" / "sh.exe", root / "usr" / "bin" / "sh.exe"):
            if candidate.is_file():
                return str(candidate)
    return None


def load(script: str):
    """Import a hyphenated script as a module."""
    spec = importlib.util.spec_from_file_location(script.replace("-", "_"), HERE / f"{script}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


commit_msg = load("check-commit-msg")
check_skills = load("check-skills")
private_data = load("check-private-data")
protected_paths = load("check-protected-paths")
check_rules = load("check-rules")
check_book = load("check-book")
code_rules = load("check-code-rules")
test_trend = load("test-trend")
task_rollup = load("gen-task-rollup")
AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"


class Repo:
    """A throwaway git repository."""

    def __init__(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name).resolve()
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.autocrlf", "false")

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                              cwd=self.root, capture_output=True, text=True, encoding="utf-8")
        if done.returncode != 0:
            raise AssertionError(f"git {' '.join(args)}: {done.stderr}")
        return done.stdout

    def write(self, rel: str, content: str | bytes, add: bool = True) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
        if add:
            self.git("add", "--", rel)
        return path

    def commit(self, message: str = "Change") -> None:
        self.git("commit", "-q", "--no-verify", "-m", message)

    def close(self) -> None:
        self._dir.cleanup()


class RepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo()
        self.addCleanup(self.repo.close)


def run_main(function, *args, **kwargs) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = function(*args, **kwargs)
    return code, out.getvalue()


# ---------------------------------------------------------------- check-commit-msg

class ModuleFixtureTests(unittest.TestCase):
    """The packs' test modules import setUpModule/tearDownModule, so one process runs the pair once per module."""

    def test_a_second_module_runs_after_the_first_ones_teardown(self):
        tearDownModule()
        tearDownModule()  # a module whose teardown finds the shared file already gone
        setUpModule()
        self.assertTrue(os.path.isfile(_empty_config.name), "the next module's git still has its empty config")
        self.assertEqual(os.environ["GIT_CONFIG_GLOBAL"], _empty_config.name)


class CommitMessageTests(unittest.TestCase):
    # The ids a project with a Jira key accepts: [checks] fix_id and the [jira] project key.
    WITH_KEY = re.compile(r"(?:\b[DQ]\d+\b)|\bPROJ-\d+\b")

    def problems(self, message: str, branch: str = "chore/x") -> list[str]:
        with mock.patch.object(commit_msg, "FIX_ID", self.WITH_KEY):
            return commit_msg.problems(message, branch)

    def test_the_fix_id_comes_from_checks_fix_id_and_the_jira_key(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "devtools" / "factory" / "factory.toml"
            config.parent.mkdir(parents=True)
            config.write_text("[checks]\nfix_id = '\\bBUG\\d+\\b'\n[jira]\nproject = \"ACME\"\n", encoding="utf-8")
            pattern = commit_msg.fix_id_pattern(Path(folder))
            self.assertTrue(pattern.search("Fix BUG12"))
            self.assertTrue(pattern.search("Fix ACME-4"))
            self.assertFalse(pattern.search("Fix D12"))
            config.write_text("", encoding="utf-8")
            pattern = commit_msg.fix_id_pattern(Path(folder))
            self.assertTrue(pattern.search("Fix D12"))
            self.assertFalse(pattern.search("Fix ACME-4"))

    def test_a_plain_message_passes(self):
        self.assertEqual(self.problems("Add the widget\n\nIt draws widgets.\n"), [])

    def test_attribution_in_the_middle_of_the_body_is_refused(self):
        found = self.problems("Add the widget\n\nCo-Authored-By: Claude <claude@anthropic.com>\nNotes follow\n")
        self.assertTrue(any("attribution" in p for p in found))
        self.assertTrue(any("trailer" in p for p in found))

    def test_a_generated_with_footer_is_refused(self):
        found = self.problems("Add the widget\n\n\U0001F916 Generated with [Claude Code](https://claude.com/claude-code)\n")
        self.assertTrue(any("attribution" in p for p in found))

    def test_a_trailer_before_prose_is_still_refused(self):
        found = self.problems("Add the widget\n\nSigned-off-by: T <t@example.invalid>\nMore prose here.\n")
        self.assertTrue(any("trailer" in p and "Signed-off-by" in p for p in found))

    def test_a_capitalised_hyphenated_key_is_a_trailer(self):
        self.assertTrue(self.problems("Add the widget\n\nX-Foo-Bar: baz\n"))
        self.assertTrue(self.problems("Add the widget\n\nChange-Id: I1234\n"))

    def test_a_final_prose_line_with_a_colon_is_allowed(self):
        self.assertEqual(self.problems("Add the widget\n\nNote: this is prose.\n"), [])

    def test_lower_case_hyphenated_prose_is_allowed(self):
        self.assertEqual(self.problems("Add the widget\n\nSelf-tests: guard 32, tools 34.\n"
                                       "best-effort: a failure is logged.\n"), [])

    def test_a_jira_key_opening_the_subject_is_not_a_trailer(self):
        self.assertEqual(self.problems("PROJ-333: Fix login\n", "fix/PROJ-333-login"), [])

    def test_prose_that_mentions_a_trailer_mid_sentence_is_allowed(self):
        self.assertEqual(self.problems("Log D1\n\nThe commit carried a Co-Authored-By: Claude trailer.\n"), [])

    def test_a_fix_branch_needs_an_id(self):
        found = self.problems("Fix the login\n", "fix/login")
        self.assertTrue(any("fix branch" in p for p in found))

    def test_a_fix_branch_accepts_a_defect_question_or_jira_id(self):
        for message in ("Fix the login (D12)", "Answer Q3", "Fix PROJ-310"):
            self.assertEqual(self.problems(message, "fix/login"), [], message)

    def test_merges_and_reverts_are_exempt_from_the_id_rule(self):
        self.assertEqual(self.problems("Merge branch 'dev' into fix/login\n", "fix/login"), [])
        self.assertEqual(self.problems('Revert "Fix the login"\n\nThis reverts commit abc.\n', "fix/login"), [])

    def test_text_below_the_scissors_line_is_not_checked(self):
        message = ("Add the widget\n"
                   "# ------------------------ >8 ------------------------\n"
                   "Co-Authored-By: Claude <claude@anthropic.com>\n"
                   "Signed-off-by: T <t@example.invalid>\n")
        self.assertEqual(self.problems(message), [])

    def test_an_id_only_below_the_scissors_line_does_not_count(self):
        message = "Fix the login\n# ------------------------ >8 ------------------------\n+ D12\n"
        self.assertTrue(self.problems(message, "fix/login"))

    def test_comment_lines_are_not_the_message(self):
        self.assertEqual(self.problems("Add the widget\n# Signed-off-by: T <t@example.invalid>\n"), [])

    LEDGER = {"docs/defects.md": "## Open\n\n| D12 | 🟡 | Open | x |\n\n## Fixed\n\n- **D7** — was wrong. PR #7.\n",
              "docs/archive/defects-closed.md": "## Fixed\n\n| **D3** | 🟡 | x |\n\n#### D5 — an old one\n"}

    def with_ledger(self, message: str, branch: str = "fix/login") -> list[str]:
        defined = commit_msg.ledger_ids(self.LEDGER.get)
        with mock.patch.object(commit_msg, "FIX_ID", self.WITH_KEY):
            return commit_msg.problems(message, branch, defined)

    def test_a_fix_naming_a_defect_the_ledger_holds_passes(self):
        for message in ("Fix the login (D12)", "Close D7", "Fix D3 and D5", "Answer Q3", "Fix PROJ-310"):
            with self.subTest(message):
                self.assertEqual([], self.with_ledger(message))

    def test_a_fix_naming_a_defect_the_ledger_lacks_fails(self):
        found = self.with_ledger("Fix the login (D12, D99)")
        self.assertEqual(1, len(found), found)
        self.assertIn("it names D99, which docs/defects.md and its archive do not hold", found[0])

    def test_a_mention_in_a_row_is_not_a_definition(self):
        defined = commit_msg.ledger_ids({"docs/defects.md": "| D12 | x | like D40 |\n"}.get)
        self.assertEqual({"12"}, defined)

    def test_no_ledger_or_another_branch_leaves_the_existence_rule_out(self):
        with mock.patch.object(commit_msg, "FIX_ID", self.WITH_KEY):
            self.assertEqual([], commit_msg.problems("Fix D99", "fix/login", None))
        self.assertEqual([], self.with_ledger("Note D99 for later", "chore/notes"))

    def test_a_failed_git_read_stops_the_check_never_reads_as_no_ledger(self):
        """A shallow clone or a bad sha must not let every made-up id through."""
        failed = subprocess.CompletedProcess([], 128, "", "fatal: bad object deadbeef")
        with mock.patch.object(commit_msg.subprocess, "run", return_value=failed), \
                self.assertRaises(SystemExit) as raised:
            commit_msg.git_text("deadbeef", "docs/defects.md")
        self.assertIn("could not check the defect ids", str(raised.exception))
        absent = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(commit_msg.subprocess, "run", return_value=absent):
            self.assertIsNone(commit_msg.git_text("deadbeef", "docs/defects.md"), "listed as absent: no ledger")
        listed = subprocess.CompletedProcess([], 0, "docs/defects.md\n", "")
        shown = subprocess.CompletedProcess([], 0, "| D1 |\n", "")
        with mock.patch.object(commit_msg.subprocess, "run", side_effect=[listed, shown]):
            self.assertEqual("| D1 |\n", commit_msg.git_text("", "docs/defects.md"))


class CommitMessageRangeTests(RepoTestCase):
    """`--commits <range>`, as CI runs it over a pull request's commits, in a throwaway repository."""

    RULE = "devtools/checks/check-commit-msg.py"

    def setUp(self) -> None:
        super().setUp()
        self.repo.write("readme.txt", "base\n")
        self.repo.commit("Base")
        self.repo.git("checkout", "-q", "-b", "work")

    def commit_with_the_rule(self, message: str, rel: str = "a.txt") -> str:
        """A commit whose tree holds the check — one the hook would have run for."""
        self.repo.write(self.RULE, "# the rule\n")
        self.repo.write(rel, message + "\n")
        self.repo.commit(message)
        return self.repo.git("rev-parse", "HEAD").strip()

    def run_ci(self, revisions: str = "main..HEAD", branch: str = "chore/x") -> subprocess.CompletedProcess:
        env = {**os.environ, "BITBUCKET_BRANCH": branch}
        return subprocess.run([sys.executable, str(HERE / "check-commit-msg.py"), "--commits", revisions],
                              cwd=self.repo.root, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")

    def judged(self, revisions: str) -> list[str]:
        here = os.getcwd()
        os.chdir(self.repo.root)
        try:
            return commit_msg.commits_to_judge(revisions)
        finally:
            os.chdir(here)

    def test_a_good_range_passes(self):
        self.commit_with_the_rule("Add the widget")
        done = self.run_ci()
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_bad_message_in_the_range_fails_naming_its_commit(self):
        self.commit_with_the_rule("Add the widget")
        bad = self.commit_with_the_rule("Add the gadget\n\nSigned-off-by: T <t@example.invalid>", "b.txt")
        self.commit_with_the_rule("Add the gizmo", "c.txt")
        done = self.run_ci()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn(f"commit-msg: {bad[:10]}", done.stderr)
        self.assertIn("trailer", done.stderr)

    def test_a_fix_branch_commit_without_an_id_fails_by_the_ci_branch(self):
        self.commit_with_the_rule("Fix the login")
        done = self.run_ci(branch="fix/login")
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("fix/login is a fix branch", done.stderr)
        self.assertEqual(self.run_ci(branch="chore/login").returncode, 0, "the branch, not the message, failed it")

    def test_a_fix_branch_commit_with_an_id_passes(self):
        self.commit_with_the_rule("Fix the login (D12)")
        done = self.run_ci(branch="fix/login")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_range_git_cannot_list_exits_non_zero(self):
        self.commit_with_the_rule("Add the widget")
        done = self.run_ci("origin/nowhere..HEAD")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("could not list", done.stderr)

    def test_commits_made_before_the_check_are_not_judged_and_every_later_one_is(self):
        self.repo.write("old.txt", "old\n")
        self.repo.commit("Old work\n\nSigned-off-by: T <t@example.invalid>")  # before the rule: history
        later = [self.commit_with_the_rule("Add the widget"), self.commit_with_the_rule("Add the gadget", "b.txt")]
        self.assertEqual(sorted(self.judged("main..HEAD")), sorted(later))
        done = self.run_ci()
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_range_with_commits_is_never_judged_as_empty(self):
        shas = [self.commit_with_the_rule("Add the widget"), self.commit_with_the_rule("Add the gadget", "b.txt")]
        judged = self.judged("main..HEAD")
        self.assertTrue(judged)
        self.assertEqual(sorted(judged), sorted(shas))


# ---------------------------------------------------------------- check-private-data

class PrivateDataTests(RepoTestCase):
    def test_a_staged_secret_is_found_with_its_file_and_line(self):
        self.repo.write("config.txt", f"first line\nkey = {AWS_KEY}\n")
        code, out = run_main(private_data.main, ["--staged"], root=self.repo.root)
        self.assertEqual(code, 1)
        self.assertIn("config.txt:2: an AWS access key", out)

    def test_the_secret_text_is_never_printed(self):
        self.repo.write("config.txt", f"key = {AWS_KEY}\n")
        for mode in ("--staged", "--tracked"):
            code, out = run_main(private_data.main, [mode], root=self.repo.root)
            self.assertEqual(code, 1, mode)
            self.assertNotIn(AWS_KEY, out)
            self.assertNotIn("ABCDEFGHIJKLMNOP", out)

    def test_a_staged_file_with_a_non_ascii_name_is_scanned(self):
        self.repo.write("café.txt", f"key = {AWS_KEY}\n")
        code, out = run_main(private_data.main, ["--staged"], root=self.repo.root)
        self.assertEqual(code, 1)
        self.assertIn("café.txt:1:", out)

    def test_a_staged_file_with_a_space_in_its_name_is_named_cleanly(self):
        self.repo.write("my notes.txt", f"key = {AWS_KEY}\n")
        code, out = run_main(private_data.main, ["--staged"], root=self.repo.root)
        self.assertEqual(code, 1)
        self.assertIn("  my notes.txt:1: an AWS access key", out)

    def test_a_quoted_diff_header_is_unquoted(self):
        self.assertEqual(private_data.new_path('+++ "b/caf\\303\\251 \\"x\\"\\tz.txt"'), 'café "x"\tz.txt')
        self.assertEqual(private_data.new_path("+++ b/plain.txt"), "plain.txt")
        self.assertIsNone(private_data.new_path("+++ /dev/null"))

    def test_a_tracked_file_that_is_not_utf8_is_still_scanned(self):
        self.repo.write("latin1.txt", b"caf\xe9 " + AWS_KEY.encode() + b"\n")
        code, out = run_main(private_data.main, ["--tracked"], root=self.repo.root)
        self.assertEqual(code, 1)
        self.assertIn("latin1.txt:1: an AWS access key", out)

    def test_a_binary_tracked_file_is_skipped(self):
        self.repo.write("blob.bin", b"\x00\x01" + AWS_KEY.encode())
        code, out = run_main(private_data.main, ["--tracked"], root=self.repo.root)
        self.assertEqual(code, 0, out)

    def test_a_clean_repository_passes(self):
        self.repo.write("readme.txt", "nothing to see\n")
        self.assertEqual(run_main(private_data.main, ["--staged"], root=self.repo.root)[0], 0)
        self.assertEqual(run_main(private_data.main, ["--tracked"], root=self.repo.root)[0], 0)

    def test_a_failing_git_command_fails_the_check(self):
        for mode in (["--tracked"], ["--staged"], ["--commits", "HEAD~1..HEAD"]):
            with tempfile.TemporaryDirectory() as not_a_repo:
                code, out = run_main(private_data.main, mode, root=Path(not_a_repo))
            self.assertEqual(code, 1, mode)
            self.assertIn("could not list", out)
            self.assertNotIn("No personal or secret data", out)

    # -- whatever the committer's own diff settings, the staged diff is read

    def test_a_secret_is_found_when_the_committer_turned_diff_prefixes_off(self):
        self.repo.git("config", "diff.noprefix", "true")
        self.repo.write("config.txt", f"key = {AWS_KEY}\n")
        code, out = run_main(private_data.main, ["--staged"], root=self.repo.root)
        self.assertEqual(code, 1, out)
        self.assertIn("config.txt:1: an AWS access key", out)

    def test_a_secret_is_found_with_mnemonic_diff_prefixes(self):
        self.repo.git("config", "diff.mnemonicPrefix", "true")
        self.repo.write("config.txt", f"key = {AWS_KEY}\n")
        code, out = run_main(private_data.main, ["--staged"], root=self.repo.root)
        self.assertEqual(code, 1, out)
        self.assertIn("config.txt:1: an AWS access key", out)

    def test_a_header_with_no_b_side_is_a_failure_not_a_skip(self):
        with self.assertRaises(private_data.UnreadableDiff):
            private_data.new_path("+++ config.txt")
        diff = "diff --git config.txt config.txt\n+++ config.txt\n@@ -0,0 +1 @@\n+key\n"
        with self.assertRaises(private_data.UnreadableDiff):
            list(private_data.added_lines(diff))

    def test_an_added_line_that_starts_with_two_pluses_is_scanned(self):
        self.repo.write("notes.txt", f"++ key = {AWS_KEY}\n")
        code, out = run_main(private_data.main, ["--staged"], root=self.repo.root)
        self.assertEqual(code, 1, out)
        self.assertIn("notes.txt:1: an AWS access key", out)

    def test_a_staged_deletion_passes(self):
        self.repo.write("readme.txt", "nothing to see\n")
        self.repo.commit()
        self.repo.git("rm", "-q", "readme.txt")
        self.assertEqual(run_main(private_data.main, ["--staged"], root=self.repo.root)[0], 0)

    # -- who is committing: only with --staged

    def test_the_committers_email_is_found_when_staged_and_ignored_when_tracked(self):
        self.repo.git("config", "user.email", "someone@example.invalid")
        self.repo.write("contact.txt", "Ask Someone@Example.invalid\n")
        code, out = run_main(private_data.main, ["--staged"], root=self.repo.root)
        self.assertEqual(code, 1)
        self.assertIn("contact.txt:1: the committer's email address", out)
        self.assertNotIn("someone@", out.lower())
        self.assertEqual(run_main(private_data.main, ["--tracked"], root=self.repo.root)[0], 0)

    def test_the_machine_name_is_found_as_a_whole_word_only(self):
        self.repo.write("hosts.txt", "built on buildbox-42\n")
        self.repo.write("other.txt", "buildbox-427 is someone else\n")
        with mock.patch.object(private_data.platform, "node", return_value="buildbox-42"):
            code, out = run_main(private_data.main, ["--staged"], root=self.repo.root)
            tracked = run_main(private_data.main, ["--tracked"], root=self.repo.root)[0]
        self.assertEqual(code, 1)
        self.assertIn("hosts.txt:1: this machine's name", out)
        self.assertNotIn("other.txt", out)
        self.assertEqual(tracked, 0)

    # -- home folders, in every tracked file

    def test_home_folder_paths_are_found_in_tracked_files(self):
        sep = "\\"
        paths = {"win.txt": "C:" + sep + "Users" + sep + "alice" + sep + "repo",
                 "linux.txt": "/" + "home/alice/repo",
                 "mac.txt": "/" + "Users/alice/repo"}
        for rel, path in paths.items():
            self.repo.write(rel, f"cd {path}\n")
        self.repo.write("tilde.txt", "cd ~/repo\n")
        code, out = run_main(private_data.main, ["--tracked"], root=self.repo.root)
        self.assertEqual(code, 1)
        for rel in paths:
            self.assertIn(f"{rel}:1: a path inside someone's home folder", out)
        self.assertNotIn("tilde.txt", out)
        self.assertNotIn("alice", out)

    # -- every commit in a range, not only the final tree

    def branch_with(self, *steps: tuple[str, str]) -> None:
        self.repo.write("readme.txt", "base\n")
        self.repo.commit("Base")
        self.repo.git("checkout", "-q", "-b", "work")
        for rel, content in steps:
            if content is None:
                self.repo.git("rm", "-q", rel)
            else:
                self.repo.write(rel, content)
            self.repo.commit()

    def test_a_secret_added_then_removed_is_found_in_the_commits(self):
        self.branch_with(("config.txt", f"key = {AWS_KEY}\n"), ("config.txt", "key = REPLACE\n"))
        self.assertEqual(run_main(private_data.main, ["--tracked"], root=self.repo.root)[0], 0)
        code, out = run_main(private_data.main, ["--commits", "main..HEAD"], root=self.repo.root)
        self.assertEqual(code, 1, out)
        first = self.repo.git("rev-list", "--reverse", "main..HEAD").split()[0]
        self.assertIn(f"{first[:8]} config.txt:1: an AWS access key", out)
        self.assertNotIn(AWS_KEY, out)

    def test_a_clean_history_passes_the_commits_check(self):
        self.branch_with(("a.txt", "one\n"), ("a.txt", "two\n"), ("a.txt", None))
        code, out = run_main(private_data.main, ["--commits", "main..HEAD"], root=self.repo.root)
        self.assertEqual(code, 0, out)
        self.assertIn("added by any commit in main..HEAD", out)

    # -- merges: what the merge itself introduced, never what it brought from a parent

    def conflicting_merge(self, resolution: str) -> str:
        """work and main each change line 2 of f.txt; work merges main and resolves it as `resolution`."""
        self.repo.write("f.txt", "a\nb\nc\n")
        self.repo.commit("Base")
        self.repo.git("checkout", "-q", "-b", "work")
        self.repo.write("f.txt", "a\nWORK\nc\n")
        self.repo.commit("Work")
        self.repo.git("checkout", "-q", "main")
        self.repo.write("f.txt", "a\nMAIN\nc\n")
        self.repo.commit("Main")
        self.repo.git("checkout", "-q", "work")
        merged = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "merge", "-q", "main"],
                                cwd=self.repo.root, capture_output=True, text=True)
        self.assertNotEqual(merged.returncode, 0, "the merge was meant to conflict")
        self.repo.write("f.txt", f"a\n{resolution}\nc\n")
        self.repo.commit("Merge branch 'main' into work")
        return self.repo.git("rev-parse", "HEAD").strip()

    def test_a_secret_written_only_in_a_merge_resolution_is_found(self):
        merge = self.conflicting_merge(f"key = {AWS_KEY}")
        self.repo.write("f.txt", "a\nkey = REPLACE\nc\n")
        self.repo.commit("Remove it")
        self.assertEqual(run_main(private_data.main, ["--tracked"], root=self.repo.root)[0], 0)
        code, out = run_main(private_data.main, ["--commits", "main..HEAD"], root=self.repo.root)
        self.assertEqual(code, 1, out)
        self.assertIn(f"{merge[:8]} f.txt:2: an AWS access key", out)
        self.assertNotIn(AWS_KEY, out)

    def test_a_merge_does_not_report_what_it_brought_from_a_parent(self):
        self.repo.write("readme.txt", "base\n")
        self.repo.commit("Base")
        self.repo.git("checkout", "-q", "-b", "work")
        self.repo.write("mine.txt", "mine\n")
        self.repo.commit("Work")
        self.repo.git("checkout", "-q", "main")
        self.repo.write("shared.txt", f"key = {AWS_KEY}\n")  # already on the base: not this branch's to answer for
        self.repo.commit("On the base")
        self.repo.git("checkout", "-q", "work")
        self.repo.git("merge", "-q", "--no-edit", "main")
        code, out = run_main(private_data.main, ["--commits", "main..HEAD"], root=self.repo.root)
        self.assertEqual(code, 0, out)

    def test_a_combined_diff_yields_only_the_lines_in_no_parent_numbered_in_the_result(self):
        diff = ("commit abc\n"
                "diff --cc f.txt\n"
                "index 1,2..3\n"
                "--- a/f.txt\n"
                "+++ b/f.txt\n"
                "@@@ -1,1 -1,1 +1,3 @@@\n"
                " +from main\n"
                "- old\n"
                "++evil\n"
                "+ from work\n")
        self.assertEqual(list(private_data.added_lines(diff)), [("abc", "f.txt", 2, "evil")])

    # -- the archive and the client's documents: credentials only

    def test_a_credential_in_the_archive_or_requirements_is_found_but_a_home_path_is_not(self):
        home = "/" + "home/alice/repo"
        self.repo.write("docs/archive/x.md", f"key = {AWS_KEY}\ncd {home}\n")
        self.repo.write("requirements/notes.txt", f"cd {home}\nkey = {AWS_KEY}\n")
        self.repo.write("graphify-out/graph.json", f"key = {AWS_KEY}\n")
        for mode in ("--staged", "--tracked"):
            code, out = run_main(private_data.main, [mode], root=self.repo.root)
            self.assertEqual(code, 1, mode)
            self.assertIn("docs/archive/x.md:1: an AWS access key", out)
            self.assertIn("requirements/notes.txt:2: an AWS access key", out)
            self.assertNotIn("home folder", out)
            self.assertNotIn("graphify-out", out)
        self.repo.commit("Base")
        self.repo.git("checkout", "-q", "-b", "work")
        self.repo.write("docs/archive/y.md", f"key = {AWS_KEY}\n")
        self.repo.commit()
        code, out = run_main(private_data.main, ["--commits", "main..HEAD"], root=self.repo.root)
        self.assertEqual(code, 1, out)
        self.assertIn("docs/archive/y.md:1: an AWS access key", out)

    # -- findings already in shared history, accepted by full commit, file, line and kind

    @staticmethod
    def entry(sha: str, rel: str, line: int, kind: str = "an AWS access key") -> str:
        return f"# reviewed\n{sha} | {rel}:{line} | {kind} | 2026-10-04 | Already on a shared branch.\n"

    def on_base(self, content: str) -> None:
        """Commit the accepted list on main — the base `--commits main..HEAD` reads it from — and come back."""
        here = self.repo.git("rev-parse", "--abbrev-ref", "HEAD").strip()
        self.repo.git("checkout", "-q", "main")
        self.repo.write(private_data.ACCEPTED.as_posix(), content)
        self.repo.commit("Accept")
        self.repo.git("checkout", "-q", here)

    def accept(self, sha: str, rel: str, line: int, kind: str = "an AWS access key") -> None:
        self.on_base(self.entry(sha, rel, line, kind))

    def test_an_entry_added_only_on_the_branch_does_not_excuse_its_finding(self):
        self.branch_with(("config.txt", f"key = {AWS_KEY}\n"))
        sha = self.repo.git("rev-parse", "HEAD").strip()
        self.repo.write(private_data.ACCEPTED.as_posix(), self.entry(sha, "config.txt", 1))
        self.repo.commit("Accept my own finding")
        code, out = run_main(private_data.main, ["--commits", "main..HEAD"], root=self.repo.root)
        self.assertEqual(code, 1, out)
        self.assertIn(f"{sha[:8]} config.txt:1: an AWS access key", out)
        self.assertNotIn("accepted finding(s)", out)

    def test_accepted_from_reads_the_named_branch_list_for_a_promotion(self):
        # dev holds a finding and the reviewed entry for it; main (the promotion's destination) holds neither.
        self.repo.write("readme.txt", "base\n")
        self.repo.commit("Base")
        self.repo.git("checkout", "-q", "-b", "dev")
        self.repo.write("config.txt", f"key = {AWS_KEY}\n")
        self.repo.commit("Leak")
        leak = self.repo.git("rev-parse", "HEAD").strip()
        self.repo.write("config.txt", "key = REPLACE\n")
        self.repo.write(private_data.ACCEPTED.as_posix(), self.entry(leak, "config.txt", 1))
        self.repo.commit("Remove it and accept the finding")
        self.assertEqual(run_main(private_data.main, ["--commits", "main..dev"], root=self.repo.root)[0], 1)
        code, out = run_main(private_data.main, ["--commits", "main..dev", "--accepted-from", "dev"], root=self.repo.root)
        self.assertEqual(code, 0, out)
        self.assertIn("1 accepted finding(s)", out)
        # A branch off dev that accepts its own new finding is still caught: dev's list does not hold it.
        self.repo.git("checkout", "-q", "-b", "work")
        self.repo.write("other.txt", f"key = {AWS_KEY}\n")
        self.repo.commit("Leak again")
        again = self.repo.git("rev-parse", "HEAD").strip()
        self.repo.write(private_data.ACCEPTED.as_posix(), self.entry(again, "other.txt", 1))
        self.repo.commit("Accept my own finding")
        code, out = run_main(private_data.main, ["--commits", "dev..work", "--accepted-from", "dev"], root=self.repo.root)
        self.assertEqual(code, 1, out)
        self.assertIn(f"{again[:8]} other.txt:1: an AWS access key", out)

    def test_accepted_from_an_unknown_ref_fails(self):
        self.branch_with(("a.txt", "one\n"))
        code, out = run_main(private_data.main, ["--commits", "main..HEAD", "--accepted-from", "origin/nowhere"],
                             root=self.repo.root)
        self.assertEqual(code, 1)
        self.assertIn("could not list", out)

    def test_a_base_without_the_accepted_list_accepts_nothing(self):
        self.branch_with(("config.txt", f"key = {AWS_KEY}\n"))
        sha = self.repo.git("rev-parse", "HEAD").strip()
        self.repo.write(private_data.ACCEPTED.as_posix(), self.entry(sha, "config.txt", 1), add=False)
        self.assertEqual(private_data.accepted_findings(self.repo.root, "main"), set())
        self.assertEqual(run_main(private_data.main, ["--commits", "main..HEAD"], root=self.repo.root)[0], 1)

    def test_an_accepted_finding_passes_the_commits_check(self):
        self.branch_with(("config.txt", f"key = {AWS_KEY}\n"), ("config.txt", "key = REPLACE\n"))
        first = self.repo.git("rev-list", "--reverse", "main..HEAD").split()[0]
        self.accept(first, "config.txt", 1)
        code, out = run_main(private_data.main, ["--commits", "main..HEAD"], root=self.repo.root)
        self.assertEqual(code, 0, out)
        self.assertIn("1 accepted finding(s)", out)

    def test_the_same_content_in_a_new_commit_is_still_caught(self):
        self.branch_with(("config.txt", f"key = {AWS_KEY}\n"), ("config.txt", "key = REPLACE\n"))
        first = self.repo.git("rev-list", "--reverse", "main..HEAD").split()[0]
        self.accept(first, "config.txt", 1)
        self.repo.write("config.txt", f"key = {AWS_KEY}\n")
        self.repo.commit("Again")
        code, out = run_main(private_data.main, ["--commits", "main..HEAD"], root=self.repo.root)
        self.assertEqual(code, 1, out)
        latest = self.repo.git("rev-parse", "HEAD").strip()
        self.assertIn(f"{latest[:8]} config.txt:1: an AWS access key", out)
        self.assertNotIn(f"{first[:8]} config.txt", out)

    def test_an_accepted_entry_must_match_the_full_sha_file_line_and_kind(self):
        self.branch_with(("config.txt", f"one\nkey = {AWS_KEY}\n"))
        sha = self.repo.git("rev-parse", "HEAD").strip()
        for entry in ((sha, "config.txt", 1, "an AWS access key"), (sha, "other.txt", 2, "an AWS access key"),
                      (sha, "config.txt", 2, "a path inside someone's home folder"),
                      (sha[:39] + ("0" if sha[39] != "0" else "1"), "config.txt", 2, "an AWS access key")):
            self.accept(*entry)
            code, out = run_main(private_data.main, ["--commits", "main..HEAD"], root=self.repo.root)
            self.assertEqual(code, 1, entry)
            self.assertIn("config.txt:2: an AWS access key", out)

    def test_a_malformed_accepted_list_fails_rather_than_accepting(self):
        self.branch_with(("config.txt", f"key = {AWS_KEY}\n"))
        sha = self.repo.git("rev-parse", "HEAD").strip()
        for line in (f"{sha} | config.txt:1 | an AWS access key", f"{sha[:8]} | config.txt:1 | an AWS access key | 2026-10-04 | why",
                     f"{sha} | config.txt:1 | an AWS access key | 2026-10-04 | "):
            self.on_base(line + "\n")
            code, out = run_main(private_data.main, ["--commits", "main..HEAD"], root=self.repo.root)
            self.assertEqual(code, 1, line)
            self.assertIn("could not read the accepted list", out)

    def test_the_accepted_list_is_not_consulted_for_tracked_files(self):
        self.repo.write("config.txt", f"key = {AWS_KEY}\n")
        self.repo.commit("Add")
        self.accept(self.repo.git("rev-parse", "HEAD").strip(), "config.txt", 1)
        self.assertEqual(run_main(private_data.main, ["--tracked"], root=self.repo.root)[0], 1)

    def test_the_commits_failure_never_advises_rewriting_a_shared_branch(self):
        self.branch_with(("config.txt", f"key = {AWS_KEY}\n"))
        code, out = run_main(private_data.main, ["--commits", "main..HEAD"], root=self.repo.root)
        self.assertEqual(code, 1)
        self.assertNotIn("rewrite the branch", out)
        self.assertIn("never rewrite a shared branch", out)
        self.assertIn("in a new commit", out)
        self.assertIn(private_data.ACCEPTED.as_posix(), out)
        self.assertIn("rotated", out)

    def test_the_repositorys_accepted_list_reads_cleanly(self):
        entries = private_data.accepted_findings()
        self.assertTrue(all(len(sha) == 40 for sha, _, _, _ in entries))

    def test_commits_with_no_merge_base_fail(self):
        self.branch_with(("a.txt", "one\n"))
        self.repo.git("checkout", "-q", "--orphan", "stranger")
        self.repo.write("b.txt", "two\n")
        self.repo.commit("Unrelated")
        code, out = run_main(private_data.main, ["--commits", "main..stranger"], root=self.repo.root)
        self.assertEqual(code, 1)
        self.assertIn("could not list", out)

    def test_commits_with_an_unknown_base_fail(self):
        self.branch_with(("a.txt", "one\n"))
        code, out = run_main(private_data.main, ["--commits", "origin/nowhere..HEAD"], root=self.repo.root)
        self.assertEqual(code, 1)
        self.assertIn("could not list", out)


# ---------------------------------------------------------------- the hooks without Python

class HooksWithoutPythonTests(RepoTestCase):
    """A machine where neither `python3`, `python` nor `py -3` runs: the hooks refuse, not pass."""

    def setUp(self) -> None:
        super().setUp()
        self.sh = find_sh()
        if self.sh is None:
            self.fail("no sh on PATH or beside git to run the hooks with")
        for hook in ("pre-commit", "commit-msg"):
            self.repo.write(f".githooks/{hook}", (HERE.parents[1] / ".githooks" / hook).read_text(encoding="utf-8"))
        self.repo.write("devtools/checks/check-appsettings-secrets.sh", "exit 0\n")
        self.bin = self.repo.root / "stub-bin"
        self.bin.mkdir()

    def python_stubs(self, code: int, launcher: int | None = None) -> dict[str, str]:
        """`python3` and `python` exit with `code`; `py` (the python.org launcher) with `launcher`, or
        `code` — and only when called as `py -3`, logging every call's arguments to py.log."""
        for name in ("python3", "python"):
            stub = self.bin / name
            stub.write_text(f"#!/bin/sh\nexit {code}\n", encoding="utf-8", newline="\n")
            stub.chmod(0o755)
        stub = self.bin / "py"
        stub.write_text(f'#!/bin/sh\necho "$*" >> "{(self.bin / "py.log").as_posix()}"\n'
                        f'[ "$1" = -3 ] || exit 9\nexit {code if launcher is None else launcher}\n',
                        encoding="utf-8", newline="\n")
        stub.chmod(0o755)
        return {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")}

    def py_calls(self) -> list[str]:
        log = self.bin / "py.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def hook(self, name: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([self.sh, f".githooks/{name}", *args], cwd=self.repo.root, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")

    def test_pre_commit_refuses_and_names_the_checks_it_could_not_run(self):
        done = self.hook("pre-commit", self.python_stubs(1))
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("no working Python", done.stderr)
        self.assertIn("tried python3, python and py -3", done.stderr)
        self.assertIn("email and machine name", done.stderr)
        self.assertIn("Install Python 3", done.stderr)
        self.assertEqual(self.py_calls(), ["-3 -c pass"])

    def test_pre_commit_runs_the_checks_with_the_py_launcher_when_it_is_the_only_python(self):
        done = self.hook("pre-commit", self.python_stubs(1, launcher=0))
        self.assertEqual(done.returncode, 0, done.stderr)
        calls = [call.replace("\\", "/") for call in self.py_calls()]
        self.assertEqual(calls[0], "-3 -c pass")
        self.assertTrue(any(call.startswith("-3 ") and call.endswith("/devtools/checks/check-private-data.py --staged")
                            for call in calls), calls)
        # The AI files a commit changes are named, however they were written.
        self.assertTrue(any(call.startswith("-3 ") and call.endswith("/devtools/book.py ai-files --staged")
                            for call in calls), calls)

    def test_pre_commit_notes_but_does_not_refuse_when_the_ai_files_note_cannot_run(self):
        # Every Python runs, but the book command fails (an older Python without tomllib, say): the note is a
        # note, and the owner's approval on the pull request stays the gate.
        for name in ("python3", "python"):
            stub = self.bin / name
            stub.write_text('#!/bin/sh\ncase "$*" in */devtools/book.py*) exit 1 ;; esac\nexit 0\n',
                            encoding="utf-8", newline="\n")
            stub.chmod(0o755)
        env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")}
        done = self.hook("pre-commit", env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("could not list the files that steer the AI", done.stderr)

    def test_commit_msg_runs_with_the_py_launcher_when_it_is_the_only_python(self):
        message = self.repo.write("msg.txt", "Add the widget\n", add=False)
        done = self.hook("commit-msg", self.python_stubs(1, launcher=0), str(message))
        self.assertEqual(done.returncode, 0, done.stderr)
        last = self.py_calls()[-1].replace("\\", "/")
        self.assertTrue(last.startswith("-3 ") and "/devtools/checks/check-commit-msg.py " in last, last)

    def test_pre_commit_passes_when_python_runs(self):
        self.assertEqual(self.hook("pre-commit", self.python_stubs(0)).returncode, 0)

    def real_book_stubs(self) -> dict[str, str]:
        """python3/python that exit 0, except for `book.py config`, which runs for real: the hook reads `[book] declined`."""
        env = self.python_stubs(0)
        for name in ("python3", "python"):
            stub = self.bin / name
            stub.write_text('#!/bin/sh\ncase "$*" in *book.py*config*) exec "' + Path(sys.executable).as_posix()
                            + '" "$@" ;; esac\nexit 0\n', encoding="utf-8", newline="\n")
            stub.chmod(0o755)
        shutil.copytree(HERE.parent / "factory", self.repo.root / "devtools/factory", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("book-template", "__pycache__", "test_*", "hooks"))
        shutil.copy(HERE.parent / "book.py", self.repo.root / "devtools/book.py")
        return env

    def test_pre_commit_skips_a_declined_test_setup_check_with_a_note(self):
        # Book D95 and Book D101: the note shows when `[book] declined` names the check, never for a mere mention of it.
        env = self.real_book_stubs()
        self.repo.write("devtools/factory/factory.toml", '[book]\ndeclined = ["devtools/checks/check-test-setup.py"]\n')
        done = self.hook("pre-commit", env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("check-test-setup.py is declined", done.stderr)
        self.assertIn("check-rules.py --write", done.stderr)
        self.repo.write("devtools/factory/factory.toml", '[book]\n# check-test-setup.py is not used here\n')
        self.assertNotIn("is declined", self.hook("pre-commit", env).stderr)

    def test_commit_msg_refuses(self):
        message = self.repo.write("msg.txt", "Add the widget\n", add=False)
        done = self.hook("commit-msg", self.python_stubs(1), str(message))
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("no working Python", done.stderr)
        self.assertIn("tried python3, python and py -3", done.stderr)


# ---------------------------------------------------------------- check-appsettings-secrets

GUARD_TOML = """[guard]
read_only = ["requirements/**"]
read_only_maintained = ["requirements/read-me.txt", "requirements/**/read-me.txt",
                        "requirements/release-plan/Release Plan.csv"]
read_only_reason = "is a snapshot of a client document and is read-only."
applied = ["src/App.Persistence/Migrations/*.cs"]
applied_reason = "is a migration already on dev."
never_by_hand = ["src/App.Persistence/Migrations/*ModelSnapshot.cs"]
"""
FSD_V1 = "requirements/fsd/FSD - V1.pdf"
MIGRATIONS = "src/App.Persistence/Migrations/"
SNAPSHOT = MIGRATIONS + "AppDbContextModelSnapshot.cs"
APPLIED = MIGRATIONS + "20260901_AddCompanies.cs"


class ProtectedPathsTests(RepoTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo.write(protected_paths.FACTORY_TOML, GUARD_TOML)
        self.repo.write("requirements/read-me.txt", "index v1\n")
        self.repo.write("requirements/fsd/read-me.txt", "fsd v1\n")
        self.repo.write(FSD_V1, "fsd one\n")
        self.repo.write("requirements/release-plan/Release Plan.csv", "plan v1\n")
        self.repo.write(APPLIED, "class AddCompanies {}\n")
        self.repo.write(SNAPSHOT, "snapshot v1\n")
        self.repo.commit("Base")
        self.repo.git("checkout", "-q", "-b", "work")

    def check(self, base: str = "main", head: str | None = None,
              env: dict[str, str] | None = None) -> tuple[int, str]:
        argv = ["--base", base] + (["--head", head] if head else [])
        return run_main(protected_paths.main, argv, root=self.repo.root, env=env or {})

    def change(self, rel: str, content: str = "changed\n") -> None:
        self.repo.write(rel, content)
        self.repo.commit()

    def assertFails(self, *named: str) -> str:
        code, out = self.check()
        self.assertEqual(code, 1, out)
        for text in named:
            self.assertIn(text, out)
        return out

    def assertPasses(self) -> str:
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("nothing protected changed", out)
        return out

    def test_the_commit_that_installs_the_book_reads_the_staged_rules(self):
        # A repository whose base holds no Book yet: the commit installing it has only the index's rules (Book D59).
        repo = Repo()
        self.addCleanup(repo.close)
        repo.write("README.md", "# A project\n")
        repo.commit("Start")
        repo.write(protected_paths.FACTORY_TOML, GUARD_TOML)
        code, out = run_main(protected_paths.main, ["--staged", "--base", "main"], root=repo.root, env={})
        self.assertEqual(code, 0, out)
        self.assertIn("rules: [guard] in the staged index", out)
        code, out = run_main(protected_paths.main, ["--base", "main"], root=repo.root, env={})
        self.assertEqual(code, 1, "without --staged the index is not read")
        self.assertIn("no rules to apply", out)

    def test_nothing_protected_changed_passes_with_one_line(self):
        self.change("src/Other.cs")
        self.assertEqual(self.assertPasses().count("\n"), 1)

    def test_modifying_an_existing_fsd_snapshot_fails(self):
        self.change(FSD_V1, "edited\n")
        out = self.assertFails(FSD_V1, "modified", "is a snapshot of a client document")
        self.assertNotIn("edited", out)  # names the path and the rule, never the contents

    def test_adding_a_new_snapshot_passes(self):
        self.change("requirements/fsd/FSD - V2.pdf", "fsd two\n")
        self.assertPasses()

    def test_editing_a_read_me_passes(self):
        self.repo.write("requirements/read-me.txt", "index v2\n")
        self.change("requirements/fsd/read-me.txt", "fsd v2\n")
        self.assertPasses()

    def test_replacing_a_maintained_tracker_with_its_backup_passes(self):
        self.repo.write("requirements/release-plan/backup/Release Plan 2026-09-01.csv", "plan v1\n")
        self.change("requirements/release-plan/Release Plan.csv", "plan v2\n")
        self.assertPasses()

    def test_deleting_a_maintained_read_me_fails(self):
        self.repo.git("rm", "-q", "--", "requirements/fsd/read-me.txt")
        self.repo.commit()
        self.assertFails("requirements/fsd/read-me.txt", "deleted",
                         "a maintained requirements file is edited, never deleted")

    def test_deleting_a_trackers_current_copy_fails(self):
        self.repo.write("requirements/release-plan/backup/Release Plan 2026-09-01.csv", "plan v1\n")
        self.repo.git("rm", "-q", "--", "requirements/release-plan/Release Plan.csv")
        self.repo.commit()
        out = self.assertFails("requirements/release-plan/Release Plan.csv",
                               "a maintained requirements file is edited, never deleted")
        self.assertNotIn("backup/", out)  # the backup is an addition, not the offence

    def test_renaming_a_maintained_read_me_away_fails(self):
        self.repo.git("mv", "requirements/read-me.txt", "requirements/read-me (old).txt")
        self.repo.commit()
        out = self.assertFails("requirements/read-me.txt", "renamed away",
                               "a maintained requirements file is edited, never deleted")
        self.assertNotIn("read-me (old).txt", out)

    def test_a_backup_is_read_only_once_on_the_destination(self):
        self.repo.git("checkout", "-q", "main")
        self.change("requirements/release-plan/backup/Release Plan 2026-09-01.csv", "plan v1\n")
        self.repo.git("checkout", "-q", "work")
        self.repo.git("merge", "-q", "--no-edit", "main")
        self.change("requirements/release-plan/backup/Release Plan 2026-09-01.csv", "rewritten\n")
        self.assertFails("backup/Release Plan 2026-09-01.csv")

    def test_editing_an_applied_migration_fails(self):
        self.change(APPLIED, "class AddCompanies { /* hand edit */ }\n")
        self.assertFails(APPLIED, "modified", "is a migration already on dev")

    def test_adding_a_migration_and_regenerating_the_snapshot_passes(self):
        self.repo.write(MIGRATIONS + "20261001_AddWidgets.cs", "class AddWidgets {}\n")
        self.repo.write(MIGRATIONS + "20261001_AddWidgets.Designer.cs", "partial class AddWidgets {}\n")
        self.change(SNAPSHOT, "snapshot v2\n")
        self.assertPasses()

    def test_a_branch_may_still_change_its_own_unmerged_migration(self):
        self.change(MIGRATIONS + "20261001_AddWidgets.cs", "class AddWidgets {}\n")
        self.change(MIGRATIONS + "20261001_AddWidgets.cs", "class AddWidgets { int x; }\n")
        self.assertPasses()

    def test_deleting_a_protected_file_fails(self):
        self.repo.git("rm", "-q", "--", FSD_V1, APPLIED)
        self.repo.commit()
        self.assertFails(FSD_V1, APPLIED, "deleted")

    def test_renaming_a_protected_file_fails(self):
        self.repo.git("mv", FSD_V1, "requirements/fsd/FSD - V1 (old).pdf")
        self.repo.git("mv", APPLIED, MIGRATIONS + "20260901_AddCompaniesRenamed.cs")
        self.repo.commit()
        out = self.assertFails(FSD_V1, APPLIED, "renamed away")
        self.assertNotIn("V1 (old).pdf", out)  # the new name is an addition, not the offence

    def test_a_rename_is_caught_whatever_diff_renames_says(self):
        self.repo.git("config", "diff.renames", "copies")
        self.repo.git("mv", FSD_V1, "requirements/fsd/elsewhere.pdf")
        self.repo.commit()
        self.assertFails(FSD_V1)

    def test_a_branch_that_merged_the_destination_answers_only_for_its_own_changes(self):
        self.change("src/Feature.cs", "feature\n")
        self.repo.git("checkout", "-q", "main")
        # The destination's own legitimate changes after the branch was cut — a tracker replaced in place,
        # a new snapshot, a new migration with its snapshot — must not count against the branch.
        self.repo.write("requirements/release-plan/Release Plan.csv", "plan v2\n")
        self.repo.write("requirements/fsd/FSD - V2.pdf", "fsd two\n")
        self.repo.write(MIGRATIONS + "20261002_AddRacks.cs", "class AddRacks {}\n")
        self.change(SNAPSHOT, "snapshot v2\n")
        self.repo.git("checkout", "-q", "work")
        self.repo.git("merge", "-q", "--no-edit", "main")
        self.assertPasses()

    def test_a_promotion_passes(self):
        self.repo.git("branch", "qa", "main")
        self.repo.git("checkout", "-q", "main")
        self.repo.write("requirements/fsd/FSD - V2.pdf", "fsd two\n")
        self.repo.write("requirements/read-me.txt", "index v2\n")
        self.repo.write(MIGRATIONS + "20261002_AddRacks.cs", "class AddRacks {}\n")
        self.change(SNAPSHOT, "snapshot v2\n")
        code, out = self.check(base="qa", head="main")
        self.assertEqual(code, 0, out)
        self.assertIn("between qa and main", out)

    def promote_an_edit(self) -> str:
        """qa stays behind; main edits FSD V1 in place. Returns the editing commit."""
        self.repo.git("branch", "qa", "main")
        self.repo.git("checkout", "-q", "main")
        self.change(FSD_V1, "replaced in place\n")
        return self.repo.git("rev-parse", "HEAD").strip()

    def test_a_promotion_carrying_an_old_violation_fails_unless_accepted(self):
        commit = self.promote_an_edit()
        code, out = run_main(protected_paths.main, ["--base", "qa", "--head", "main"], root=self.repo.root,
                             env={}, accepted={})
        self.assertEqual(code, 1, out)
        code, out = run_main(protected_paths.main, ["--base", "qa", "--head", "main"], root=self.repo.root,
                             env={}, accepted={(commit, FSD_V1): "before the rule"})
        self.assertEqual(code, 0, out)

    def test_an_accepted_rewrite_is_a_setting_the_base_holds(self):
        """`[guard] accepted_rewrites` as the base branch holds it excuses the change; the head cannot add one."""
        commit = self.promote_an_edit()
        entry = f'accepted_rewrites = [{{ commit = "{commit}", path = "{FSD_V1}", reason = "before the rule" }}]\n'
        # On the head only (main): the base (qa) holds no such entry, so the head cannot excuse itself.
        self.change(protected_paths.FACTORY_TOML, GUARD_TOML.replace("[guard]\n", "[guard]\n" + entry, 1))
        code, out = run_main(protected_paths.main, ["--base", "qa", "--head", "main"], root=self.repo.root, env={})
        self.assertEqual(code, 1, out)
        # On the base too: excused.
        self.repo.git("checkout", "-q", "qa")
        self.change(protected_paths.FACTORY_TOML, GUARD_TOML.replace("[guard]\n", "[guard]\n" + entry, 1))
        code, out = run_main(protected_paths.main, ["--base", "qa", "--head", "main"], root=self.repo.root, env={})
        self.assertEqual(code, 0, out)

    def test_an_accepted_rewrite_without_a_reason_cannot_be_checked(self):
        self.promote_an_edit()
        self.repo.git("checkout", "-q", "qa")
        self.change(protected_paths.FACTORY_TOML, GUARD_TOML.replace(
            "[guard]\n", f'[guard]\naccepted_rewrites = [{{ commit = "{"a" * 40}", path = "{FSD_V1}" }}]\n', 1))
        code, out = run_main(protected_paths.main, ["--base", "qa", "--head", "main"], root=self.repo.root, env={})
        self.assertEqual(code, 1)
        self.assertIn("accepted_rewrites", out)

    def test_an_accepted_commit_excuses_no_other_change_to_the_file(self):
        commit = self.promote_an_edit()
        self.change(FSD_V1, "edited again\n")
        code, out = run_main(protected_paths.main, ["--base", "qa", "--head", "main"], root=self.repo.root,
                             env={}, accepted={(commit, FSD_V1): "before the rule"})
        self.assertEqual(code, 1, out)
        self.assertIn(FSD_V1, out)

    def test_a_promotion_through_a_merge_still_excuses_the_accepted_commit(self):
        commit = self.promote_an_edit()
        self.repo.git("checkout", "-q", "work")
        self.change("src/Feature.cs", "feature\n")
        self.repo.git("checkout", "-q", "main")
        self.repo.git("merge", "-q", "--no-ff", "--no-edit", "work")
        code, out = run_main(protected_paths.main, ["--base", "qa", "--head", "main"], root=self.repo.root,
                             env={}, accepted={(commit, FSD_V1): "before the rule"})
        self.assertEqual(code, 0, out)

    def test_the_base_defaults_to_the_pull_requests_destination(self):
        self.change(FSD_V1, "edited\n")
        code, out = run_main(protected_paths.main, [], root=self.repo.root,
                             env={"BITBUCKET_PR_DESTINATION_BRANCH": "no-such-destination"})
        self.assertEqual(code, 1)
        self.assertIn("origin/no-such-destination", out)

    def test_no_merge_base_fails_closed(self):
        self.repo.git("checkout", "-q", "--orphan", "unrelated")
        self.repo.git("rm", "-rq", "--cached", ".")
        self.repo.write("x.txt", "x\n")
        self.repo.commit("Unrelated")
        code, out = self.check(base="main", head="unrelated")
        self.assertEqual(code, 1)
        self.assertIn("could not check", out)
        self.assertIn("no merge base", out)

    def test_an_unknown_ref_fails_closed(self):
        code, out = self.check(base="no-such-branch")
        self.assertEqual(code, 1)
        self.assertIn("could not check", out)

    def test_a_failed_diff_fails_closed(self):
        real = protected_paths.git

        def failing_diff(*args, root):
            if args[0] == "diff":
                return subprocess.CompletedProcess(args, 128, "", "fatal: bad object")
            return real(*args, root=root)

        self.change(FSD_V1, "edited\n")
        with mock.patch.object(protected_paths, "git", failing_diff):
            code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("could not check", out)
        self.assertIn("failed", out)

    def test_the_patterns_come_from_factory_toml(self):
        self.repo.git("checkout", "-q", "main")
        self.change(protected_paths.FACTORY_TOML, GUARD_TOML.replace(
            'applied = ["src/App.Persistence/Migrations/*.cs"]', 'applied = ["db/*.sql"]'))
        self.change("db/001_init.sql", "create table t ();\n")
        self.repo.git("checkout", "-q", "work")
        self.repo.git("merge", "-q", "--no-edit", "main")
        self.repo.write(APPLIED, "class AddCompanies { int y; }\n")  # no longer protected
        self.change("db/001_init.sql", "drop table t;\n")  # now protected
        out = self.assertFails("db/001_init.sql")
        self.assertNotIn(APPLIED, out)

    def test_a_branch_cannot_loosen_the_rules_it_is_judged_by(self):
        self.repo.write(protected_paths.FACTORY_TOML, GUARD_TOML.replace(
            'read_only = ["requirements/**"]', "read_only = []"))
        self.change(FSD_V1, "edited\n")
        out = self.assertFails(FSD_V1)
        self.assertIn("main:" + protected_paths.FACTORY_TOML, out)

    def test_a_destination_without_factory_toml_lends_the_heads_rules(self):
        self.repo.git("checkout", "-q", "main")
        self.repo.git("rm", "-q", "--", protected_paths.FACTORY_TOML)
        self.repo.commit()
        self.repo.git("checkout", "-q", "work")
        self.change(FSD_V1, "edited\n")
        out = self.assertFails(FSD_V1)
        self.assertIn("HEAD:" + protected_paths.FACTORY_TOML, out)

    def test_no_rules_anywhere_fails_closed(self):
        self.repo.git("rm", "-q", "--", protected_paths.FACTORY_TOML)
        self.repo.commit()
        self.repo.git("checkout", "-q", "main")
        self.repo.git("rm", "-q", "--", protected_paths.FACTORY_TOML)
        self.repo.commit()
        code, out = self.check(base="main", head="work")
        self.assertEqual(code, 1)
        self.assertIn("could not check", out)

    def test_a_factory_toml_without_guard_rules_fails_closed(self):
        self.repo.git("checkout", "-q", "main")
        self.change(protected_paths.FACTORY_TOML, 'base_ref = "origin/dev"\n')
        self.repo.git("checkout", "-q", "work")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("no readable [guard]", out)

    # an accepted commit must be among the commits that touched the path, and a merge may keep only a
    # parent's existing copy — never the "nothing" of a branch that never held the file.

    def merge_a_branch_without(self, branch: str, *remove: str, rename: bool = False) -> None:
        """Merge `branch` (which never held the files) into work, deleting or renaming them in the merge."""
        self.repo.git("merge", "-q", "--no-commit", "--no-ff", "--allow-unrelated-histories", branch)
        for path in remove:
            if rename:
                self.repo.git("mv", path, path + ".old")
            else:
                self.repo.git("rm", "-q", "--", path)
        self.repo.commit("Merge")

    def cut_a_stale_branch(self) -> None:
        """`stale` is cut from main before FSD V2 and a new migration land there; work then takes them."""
        self.repo.git("branch", "stale", "main")
        self.repo.git("checkout", "-q", "stale")
        self.change("src/Stale.cs", "stale\n")
        self.repo.git("checkout", "-q", "main")
        self.repo.write("requirements/fsd/FSD - V2.pdf", "fsd two\n")
        self.change(MIGRATIONS + "20261002_AddRacks.cs", "class AddRacks {}\n")
        self.repo.git("checkout", "-q", "work")
        self.repo.git("merge", "-q", "--ff-only", "main")

    def test_deleting_in_the_merge_of_a_stale_branch_fails(self):
        self.cut_a_stale_branch()
        self.merge_a_branch_without("stale", "requirements/fsd/FSD - V2.pdf", MIGRATIONS + "20261002_AddRacks.cs")
        self.assertFails("requirements/fsd/FSD - V2.pdf", "20261002_AddRacks.cs", "deleted")

    def test_renaming_in_the_merge_of_a_stale_branch_fails(self):
        self.cut_a_stale_branch()
        self.merge_a_branch_without("stale", "requirements/fsd/FSD - V2.pdf", rename=True)
        self.assertFails("requirements/fsd/FSD - V2.pdf", "renamed away")

    def test_deleting_in_the_merge_of_an_unrelated_history_fails(self):
        self.repo.git("checkout", "-q", "--orphan", "unrelated")
        self.repo.git("rm", "-rq", "--cached", ".")
        self.repo.write("other.txt", "other\n")
        self.repo.commit("Unrelated")
        self.repo.git("clean", "-fdq")
        self.repo.git("checkout", "-q", "-f", "work")
        self.merge_a_branch_without("unrelated", FSD_V1)
        self.assertFails(FSD_V1, "deleted")

    def test_an_accepted_commit_followed_by_an_edit_on_another_branch_fails(self):
        commit = self.promote_an_edit()
        self.repo.git("checkout", "-q", "-b", "later", "main")
        self.change(FSD_V1, "edited again\n")
        self.repo.git("checkout", "-q", "main")
        self.repo.git("merge", "-q", "--no-ff", "--no-edit", "later")
        code, out = run_main(protected_paths.main, ["--base", "qa", "--head", "main"], root=self.repo.root,
                             env={}, accepted={(commit, FSD_V1): "before the rule"})
        self.assertEqual(code, 1, out)
        self.assertIn(FSD_V1, out)

    def test_an_accepted_edit_later_deleted_fails(self):
        commit = self.promote_an_edit()
        self.repo.git("rm", "-q", "--", FSD_V1)
        self.repo.commit()
        code, out = run_main(protected_paths.main, ["--base", "qa", "--head", "main"], root=self.repo.root,
                             env={}, accepted={(commit, FSD_V1): "before the rule"})
        self.assertEqual(code, 1, out)
        self.assertIn(FSD_V1, out)

    def test_a_destination_guard_without_read_only_fails_closed(self):
        # An empty `applied` is a stack-free Book (test_an_empty_applied_list_...); an empty read_only never is.
        for kept in ('applied = ["src/App.Persistence/Migrations/*.cs"]\n', 'applied = []\n'):
            with self.subTest(kept=kept):
                self.repo.git("checkout", "-q", "main")
                self.change(protected_paths.FACTORY_TOML, "[guard]\n" + kept)
                self.repo.git("checkout", "-q", "work")
                code, out = self.check()
                self.assertEqual(code, 1, out)
                self.assertIn("could not check", out)
                self.assertIn("main:" + protected_paths.FACTORY_TOML + " has no", out)

    def test_a_destination_guard_with_empty_lists_fails_closed(self):
        self.repo.git("checkout", "-q", "main")
        self.change(protected_paths.FACTORY_TOML, "[guard]\nread_only = []\napplied = []\n")
        self.repo.git("checkout", "-q", "work")
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("has no read_only", out)

    def test_an_empty_applied_list_is_a_stack_free_book_not_a_failure(self):
        self.repo.git("checkout", "-q", "main")
        self.change(protected_paths.FACTORY_TOML, '[guard]\nread_only = ["requirements/**"]\napplied = []\n')
        self.repo.git("checkout", "-q", "work")
        code, out = self.check()
        self.assertNotIn("has no", out)

    def test_a_destination_without_factory_toml_borrows_devs_rules(self):
        self.repo.git("update-ref", "refs/remotes/" + protected_paths.configured_base(self.repo.root), "main")  # dev holds the rules
        self.repo.git("checkout", "-q", "-b", "qa", "main")
        self.repo.git("rm", "-q", "--", protected_paths.FACTORY_TOML)
        self.repo.commit()
        self.repo.git("checkout", "-q", "work")
        self.repo.write(protected_paths.FACTORY_TOML, GUARD_TOML.replace(
            'read_only = ["requirements/**"]', 'read_only = ["nothing/**"]'))
        self.change(FSD_V1, "edited\n")
        code, out = self.check(base="qa")
        self.assertEqual(code, 1, out)
        self.assertIn(FSD_V1, out)
        self.assertIn(protected_paths.configured_base(self.repo.root) + ":" + protected_paths.FACTORY_TOML, out)


# The requirements index as /book:check-sources keeps it: fsd/ has a live link, fe-api-contracts/ none.
WEEKLY_INDEX = """Master index.

=== LINKS - every live requirements source ===

FSD: https://docs.google.com/document/d/abc/edit

=== HAS A LIVE LINK (the link is in LINKS, above) ===

fsd/
  -> the specification

release-plan/
  -> the client's release plan

=== NO LIVE LINK ===

fe-api-contracts/
  -> as delivered
"""


class WeeklyCaptureTests(RepoTestCase):
    """The weekly rule: at most one capture per requirements source per week, Monday to Sunday."""

    def setUp(self) -> None:
        super().setUp()
        self.repo.write(protected_paths.FACTORY_TOML, GUARD_TOML)
        self.repo.write("requirements/read-me.txt", WEEKLY_INDEX)
        self.repo.write("requirements/fsd/read-me.txt", "fsd\n")
        self.repo.write(FSD_V1, "fsd one\n")
        self.repo.write("requirements/release-plan/Release Plan.csv", "plan v1\n")
        self.on("2026-12-01", "Base")
        self.repo.git("checkout", "-q", "-b", "work")

    def on(self, day: str, message: str = "Change") -> None:
        """Commit what is staged, dated `day` (author and committer)."""
        stamp = f"{day}T12:00:00"
        with mock.patch.dict(os.environ, {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}):
            self.repo.commit(message)

    def add(self, rel: str, day: str, content: str = "capture\n") -> None:
        self.repo.write(rel, content)
        self.on(day)

    def on_main(self, rel: str, day: str) -> None:
        self.repo.git("checkout", "-q", "main")
        self.add(rel, day)
        self.repo.git("checkout", "-q", "work")

    def check(self) -> tuple[int, str]:
        return run_main(protected_paths.main, ["--base", "main"], root=self.repo.root, env={})

    def test_one_capture_in_a_week_passes(self):
        self.add("requirements/fsd/FSD - V2.pdf", "2027-01-05")
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_a_capture_in_a_week_the_destination_already_captured_fails(self):
        self.on_main("requirements/fsd/FSD - V2.pdf", "2027-01-04")
        self.add("requirements/fsd/FSD - V3.pdf", "2027-01-06")
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("second capture", out)
        self.assertIn("requirements/fsd/ — week 2027-W01", out)
        self.assertIn("already captured it on 2027-01-04", out)

    def test_two_capture_days_in_one_week_on_the_branch_fail(self):
        self.add("requirements/fsd/FSD - V2.pdf", "2027-01-05")
        self.add("requirements/fsd/FSD - V3.pdf", "2027-01-10")  # Sunday: still the same week
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("2027-01-05 (FSD - V2.pdf); 2027-01-10 (FSD - V3.pdf)", out)

    def test_several_files_and_commits_on_one_day_are_one_capture(self):
        self.repo.write("requirements/release-plan/backup/Release Plan - 2026-12-01.csv", "plan v1\n")
        self.add("requirements/release-plan/Release Plan.csv", "2027-01-05", "plan v2\n")
        self.add("requirements/release-plan/Other tab.csv", "2027-01-05")
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_a_fix_to_the_branchs_own_capture_dates_from_the_capture(self):
        self.add("requirements/fsd/FSD - V2.pdf", "2027-01-05")
        self.add("requirements/fsd/FSD - V2.pdf", "2027-01-07", "fixed export\n")
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_the_next_week_passes(self):
        self.on_main("requirements/fsd/FSD - V2.pdf", "2027-01-10")  # Sunday
        self.add("requirements/fsd/FSD - V3.pdf", "2027-01-11")  # Monday
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_a_tracker_replaced_in_a_week_already_captured_fails(self):
        self.on_main("requirements/release-plan/Release Plan.csv", "2027-01-04")
        self.repo.git("merge", "-q", "--no-edit", "main")
        self.repo.write("requirements/release-plan/backup/Release Plan - 2027-01-04.csv", "capture\n")
        self.add("requirements/release-plan/Release Plan.csv", "2027-01-06", "plan v3\n")
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("requirements/release-plan/", out)

    def test_records_backups_and_read_mes_are_not_captures(self):
        self.on_main("requirements/fsd/comment-threads - 2027-01-04.md", "2027-01-04")
        self.repo.write("requirements/fsd/comment-threads - 2027-01-05.md", "threads\n")
        self.repo.write("requirements/fsd/read-me.txt", "Last checked: 2027-01-05 - captured V2\n")
        self.add("requirements/fsd/FSD - V2.pdf", "2027-01-05")
        self.add("requirements/fsd/comment-threads - 2027-01-07.md", "2027-01-07")
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_a_folder_with_no_live_link_is_not_judged(self):
        self.add("requirements/fe-api-contracts/contract-1.json", "2027-01-05")
        self.add("requirements/fe-api-contracts/contract-2.json", "2027-01-06")
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_captures_before_the_rule_was_checked_are_not_judged(self):
        self.add("requirements/fsd/FSD - V2.pdf", "2026-11-10")
        self.add("requirements/fsd/FSD - V3.pdf", "2026-11-12")
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_the_index_is_read_from_the_destination(self):
        # A branch that moves fsd/ under NO LIVE LINK in its own index is still judged by main's.
        self.repo.write("requirements/read-me.txt", WEEKLY_INDEX.replace("fsd/\n  -> the specification\n", ""))
        self.add("requirements/fsd/FSD - V2.pdf", "2027-01-05")
        self.add("requirements/fsd/FSD - V3.pdf", "2027-01-06")
        code, out = self.check()
        self.assertEqual(code, 1, out)


# ---------------------------------------------------------------- check-rules

class RulesJudgeTests(RepoTestCase):
    PIPELINES = "steps:\n  - script:\n      - python3 devtools/checks/check-x.py\n      - dotnet test\n"

    def setUp(self) -> None:
        super().setUp()
        self.repo.write("devtools/checks/check-x.py", "")
        self.repo.write("devtools/checks/check-commit-msg.py", "")
        self.repo.write(".githooks/pre-commit", '#!/bin/sh\npython3 "$root/devtools/checks/check-x.py"\n')
        self.repo.write(".githooks/commit-msg", '#!/bin/sh\nexec python3 "$root/devtools/checks/check-commit-msg.py" "$1"\n')
        self.repo.git("update-index", "--chmod=+x", ".githooks/pre-commit", ".githooks/commit-msg")

    def judge(self, marker: str, pipelines: str = PIPELINES, precommit: str | None = None,
              classes: set[str] | None = None):
        precommit = check_rules.read(".githooks/pre-commit", self.repo.root) if precommit is None else precommit
        return check_rules.judge(marker, classes or set(), pipelines, precommit, root=self.repo.root)

    def test_rule_text_starts_after_a_code_block_in_a_numbered_item(self):
        source = ("1. **Regenerate the contracts** - one run writes both:\n   ```bash\n   python3 gen.py\n   ```\n"
                  "   Never hand-edit either output; fix the generator *[enforced: `gen.py`, CI]*.\n")
        text = check_rules.rule_text(source, source.index("*[enforced"))
        self.assertTrue(text.startswith("Never hand-edit either output"), text)

    def test_rule_text_starts_at_a_numbered_item(self):
        source = "Intro.\n\n1. Do one thing.\n2. **Place a new endpoint** first *[enforced: `gen.py`, CI]*.\n"
        text = check_rules.rule_text(source, source.index("*[enforced"))
        self.assertTrue(text.startswith("Place a new endpoint"), text)

    def test_an_intact_script_rule_is_ok(self):
        enforcers, runs, problems = self.judge(": `check-x.py`, CI + pre-commit")
        self.assertEqual(problems, [])
        self.assertEqual(enforcers, ["check-x.py"])
        self.assertEqual(runs, ["CI", "pre-commit"])

    def test_a_marker_naming_nothing_is_broken(self):
        self.assertEqual(self.judge(": the reviewers")[2], ["names no test or check — say which one enforces it"])

    def test_pr_open_for_local_ci_is_judged_by_the_local_ci_requirement_not_the_review(self):
        reviewed_only = ("def command_reviewed(root, config, args): ...\n"
                         "def command_pr(root, config, args):\n    verdict, at = recorded_review(root)\n")
        self.repo.write("devtools/factory/factory.py", reviewed_only)
        self.assertEqual(self.judge(": `factory pr open`")[2], [])
        self.assertEqual(self.judge(": `factory pr open`, local CI")[2],
                         ["book pr open no longer requires a local CI pass"])
        self.repo.write("devtools/factory/factory.py", reviewed_only +
                        "def recorded_local_ci(root): ...\n    result, at = recorded_local_ci(root)\n")
        self.assertEqual(self.judge(": `factory pr open`, local CI")[2], [])
        # The command is `book`; a project's own CLAUDE.md may still name it `factory`, and reads the same.
        self.assertEqual(self.judge(": `book pr open`, local CI")[2], [])

    def test_a_book_command_marker_is_judged_under_either_name(self):
        self.repo.write("devtools/factory/factory.py", 'sub.add_parser("impact")\n')
        self.assertEqual(self.judge(": `book impact`")[2], [])
        self.assertEqual(self.judge(": `factory impact`")[2], [])
        self.assertEqual(self.judge(": `book gone`")[2], ["book has no gone command"])
        self.assertEqual(self.judge(": `book impact`, CI")[2], ["CI does not run book impact"])
        self.assertEqual(self.judge(": `book impact`, CI", "      - python3 devtools/book.py impact --ci\n")[2], [])
        # From v1.5 factory.py is no command: a pipeline still running it runs nothing.
        self.assertEqual(self.judge(": `book impact`, CI", "      - python3 devtools/factory/factory.py impact --ci\n")[2],
                         ["CI does not run book impact"])

    def test_the_ai_files_fallback_to_an_older_destination_is_no_problem_and_no_proof(self):
        # The AI-files step runs the destination's own factory.py on a branch older than v1.4.0: deliberate. With
        # book.py beside it the rule holds; on its own it proves nothing about this Book's command.
        self.repo.write("devtools/factory/factory.py", 'sub.add_parser("ai-files")\n')
        fallback = ('            FACTORY_ROOT="$PWD" python3 /tmp/destination/devtools/factory/factory.py ai-files '
                    '|| { echo "failed"; exit 1; }\n')
        both = ('            FACTORY_ROOT="$PWD" python3 /tmp/destination/devtools/book.py ai-files\n' + fallback
                + '            python3 devtools/book.py ai-files\n')
        self.assertEqual(self.judge(": `book ai-files`, CI", both)[2], [])
        self.assertEqual(self.judge(": `book ai-files`, CI", fallback)[2], ["CI does not run book ai-files"])

    def test_a_script_the_project_declined_is_not_broken(self):
        # Book D95: an enforced rule naming a declined optional check holds where the project keeps the check.
        self.assertIn("check-test-setup.py is gone", self.judge(": `check-test-setup.py`, CI + pre-commit")[2])
        self.repo.write("devtools/factory/book-defaults.toml", "")
        self.repo.write("devtools/factory/factory.toml",
                        '[book]\ndeclined = ["devtools/checks/check-test-setup.py"]\n')
        enforcers, runs, problems = self.judge(": `check-test-setup.py`, CI + pre-commit")
        self.assertEqual(problems, [])
        self.assertEqual(runs, ["declined"])

    def test_a_declined_core_check_is_still_gone(self):
        # Book D100: only a pack's optional check holds when declined; a core one names a missing enforcer.
        scripts = ("check-book.py", "check-commit-msg.py", "check-protected-paths.py", "check-private-data.py",
                   "gen-task-rollup.py")
        self.repo.write("devtools/factory/book-defaults.toml", "")
        self.repo.write("devtools/factory/factory.toml",
                        "[book]\ndeclined = [" + ", ".join(f'"devtools/checks/{name}"' for name in scripts) + "]\n")
        os.remove(self.repo.root / "devtools/checks/check-commit-msg.py")
        self.repo.git("rm", "-q", "--cached", "devtools/checks/check-commit-msg.py")
        for script in scripts:
            self.assertIn(f"{script} is gone", self.judge(f": `{script}`, CI + pre-commit")[2], script)

    def test_a_deleted_script_is_broken(self):
        self.assertIn("check-gone.py is gone", self.judge(": `check-gone.py`, CI")[2])

    def test_a_script_run_only_in_a_comment_is_broken(self):
        pipelines = "steps:\n  - script:\n      # - python3 devtools/checks/check-x.py\n"
        self.assertIn("check-x.py is not in CI", self.judge(": `check-x.py`, CI", pipelines)[2])
        precommit = '#!/bin/sh\n# python3 "$root/devtools/checks/check-x.py"\n'
        self.assertIn("check-x.py is not in pre-commit",
                      self.judge(": `check-x.py`, pre-commit", precommit=precommit)[2])

    def test_a_script_named_only_as_the_tail_of_another_does_not_run(self):
        pipelines = "      - python3 devtools/checks/precheck-x.py\n"
        self.assertIn("check-x.py is not in CI", self.judge(": `check-x.py`, CI", pipelines)[2])

    def test_a_gone_test_class_is_broken(self):
        self.repo.write("tests/Arch/Rules.cs", "// class GoneTests : IClassFixture\n"
                                               'var s = "class QuotedTests";\n'
                                               "public sealed class LiveTests { }\n")
        classes = check_rules.test_classes(self.repo.root)
        self.assertEqual(classes, {"LiveTests"})
        self.assertIn("no test class GoneTests in tests/", self.judge(": `GoneTests`", classes=classes)[2])
        self.assertIn("no test class QuotedTests in tests/", self.judge(": `QuotedTests`", classes=classes)[2])
        self.assertEqual(self.judge(": `LiveTests`", classes=classes)[2], [])

    def test_tests_ci_does_not_run_are_broken(self):
        self.assertIn("CI no longer runs the tests",
                      self.judge(": `LiveTests`", pipelines="# - dotnet test\n", classes={"LiveTests"})[2])

    def test_an_intact_commit_msg_hook_is_ok(self):
        enforcers, runs, problems = self.judge(": `commit-msg` hook")
        self.assertEqual(problems, [])
        self.assertEqual(runs, ["every commit"])

    def test_a_non_executable_commit_msg_hook_is_broken(self):
        self.repo.git("update-index", "--chmod=-x", ".githooks/commit-msg")
        self.assertIn(".githooks/commit-msg is not committed executable (mode 100755)",
                      self.judge(": `commit-msg` hook")[2])

    def test_a_commit_msg_hook_that_only_mentions_the_script_in_a_comment_is_broken(self):
        self.repo.write(".githooks/commit-msg", "#!/bin/sh\n# devtools/checks/check-commit-msg.py\nexit 0\n")
        self.assertIn("the commit-msg hook no longer runs check-commit-msg.py", self.judge(": `commit-msg` hook")[2])

    def test_a_non_executable_pre_commit_hook_is_broken(self):
        self.repo.git("update-index", "--chmod=-x", ".githooks/pre-commit")
        self.assertIn(".githooks/pre-commit is not committed executable (mode 100755)",
                      self.judge(": `check-x.py`, pre-commit")[2])


# ---------------------------------------------------------------- check-book


class TableCells(unittest.TestCase):
    """Book D73: check-book, book verify, defects classify and the register split a table row the same way."""

    CASES = {
        "| a | b |": ["a", "b"],
        "| a \\| b | c |": ["a \\| b", "c"],
        "| `x|y` | c |": ["`x", "y`", "c"],      # GitHub-style Markdown splits a code span's bare pipe too
        "| `x\\|y` | c |": ["`x\\|y`", "c"],
        "| a ` b | c |": ["a ` b", "c"],
        "| a | ends \\|": ["a", "ends \\|"],
        "a | b": ["a", "b"],
        "|  | b |": ["", "b"],
    }

    def test_every_reader_splits_each_case_alike(self):
        sys.path.insert(0, str(HERE.parent / "factory"))
        import book
        import book_migrate
        import requirements_register
        readers = {"check-book": check_book.table_cells, "book verify": book.cells,
                   "defects classify": book_migrate.cells, "register": requirements_register.cells}
        for line, want in self.CASES.items():
            for name, split in readers.items():
                self.assertEqual(split(line), want, f"{name}: {line}")


OWNER_TABLE = """# Instructions

## Where knowledge lives

| Topic | Owner |
|---|---|
| The overview | `docs/overview.md` |
| Plans | `docs/plans/` |

## Next section
"""


class BookTests(RepoTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo.write("CLAUDE.md", OWNER_TABLE)
        self.repo.write("docs/overview.md", "# Overview\n\n## Intro\n\nSee [the plan](plans/a.md#scope) "
                                            "and [intro](#intro).\n")
        self.repo.write("docs/plans/a.md", "# A\n\n## Scope\n\nText.\n")

    def check(self) -> tuple[int, str]:
        return run_main(check_book.main, self.repo.root)

    def test_a_claude_md_without_an_owner_table_is_a_finding_not_a_crash(self):
        self.repo.write("CLAUDE.md", "# Existing rules\n")  # a brownfield repository's, before the clean-up
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("names no paths", out)

    def test_a_clean_book_passes(self):
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("keeps its rules (3 documents)", out)

    def test_a_tracked_document_deleted_from_the_working_tree_is_named_not_a_crash(self):
        # Book D86: every tracked document was read, so a deleted one stopped check-book with a traceback.
        self.repo.commit()
        (self.repo.root / "docs/plans/a.md").unlink()
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("docs/overview.md:5: links to plans/a.md#scope, which does not exist", out)

    OPEN_QUESTIONS = ("# Open questions\n\n## Still open\n\n### The open questions in full\n\n"
                 "#### Q7 · Widgets may be renamed\n**Status:** OPEN · **Raised:** 2026-11-20\n\nThe question.\n\n"
                 "{heading} rename them in place.\n\n"
                 "#### Q6 · Widgets have colours\n**Status:** ANSWERED · **Raised:** 2026-11-01\n\n"
                 "**Recommendation:** an older heading, answered.\n\n## Resolved\n")

    def questions(self, heading: str, project_heading: str) -> tuple[list[str], list[str]]:
        self.repo.write("docs/open-questions.md", self.OPEN_QUESTIONS.format(heading=heading))
        return check_book.recommendation_problems(self.repo.root, project_heading)

    def test_an_open_question_uses_the_projects_recommendation_heading(self):
        # Book D51: a skill wrote the template's **Recommendation:** where the project had set its own heading.
        self.assertEqual(self.questions("**What we suggest:**", "**What we suggest:**"), ([], []))
        self.assertEqual(self.questions("**Recommendation:**", "**Recommendation:**"), ([], []))
        problems, warnings = self.questions("**Recommendation:**", "**What we suggest:**")
        self.assertEqual(len(problems), 1, "the default heading where the project set another fails")
        self.assertIn("Q7 is open and its recommendation is headed **Recommendation:**", problems[0])
        self.assertEqual(warnings, [], "the answered Q6 keeps the heading it was written with")
        problems, warnings = self.questions("We think:", "**What we suggest:**")
        self.assertEqual(problems, [], "no heading at all is only warned of")
        self.assertIn("Q7 is open with no **What we suggest:** line", warnings[0])

    def test_the_heading_comes_from_factory_toml_by_default(self):
        self.repo.write("docs/open-questions.md", self.OPEN_QUESTIONS.format(heading="**Recommendation:**"))
        with mock.patch.object(check_book, "RECOMMENDATION", "**What we suggest:**"):
            problems, _ = check_book.recommendation_problems(self.repo.root)
        self.assertEqual(len(problems), 1)

    DEFECTS = ("# Defects\n\n## Open\n\n| Id | Sev | Status | Defect | Where | Found by | Note |\n"
               "|---|---|---|---|---|---|---|\n{rows}\n")
    TYPED = ("# Defects\n\n## Open\n\n| Id | Sev | Status | Type | Source | Defect | Where | Note |\n"
               "|---|---|---|---|---|---|---|---|\n{rows}\n\n## Fixed\n\n{fixed}\n")
    OPEN = "| {id} | 🟡 | {status} | {type} | {source} | x | y | found {found} |"

    def ledger(self, *rows: str, fixed: str = "", gate: str = "2026-11-20",
               table: str | None = None) -> tuple[list[str], list[str]]:
        self.repo.write("docs/defects.md", (table or self.TYPED).format(rows="\n".join(rows), fixed=fixed))
        with mock.patch.object(check_book, "TYPE_SOURCE_FROM", gate):
            return check_book.ledger_problems(self.repo.root)

    def row(self, status: str = "Open", type: str = "bug", source: str = "qa: PROJ-3", found: str = "2026-11-21",
            id: str = "D9") -> str:
        return self.OPEN.format(id=id, status=status, type=type, source=source, found=found)

    def test_an_open_row_with_a_listed_status_type_and_source_passes(self):
        for status in ("Open", "Mitigated", "Partial", "Blocked — Q12", "**Blocked — the BA**"):
            with self.subTest(status):
                self.assertEqual(([], []), self.ledger(self.row(status=status)))
        for kind in check_book.TYPES:
            for source in check_book.SOURCES:
                with self.subTest(kind=kind, source=source):
                    self.assertEqual(([], []), self.ledger(self.row(type=f"`{kind}`", source=f"{source}: ref 1")))

    def test_an_open_row_with_another_status_fails(self):
        for status in ("In progress", "Blocked", "Fixed", ""):
            with self.subTest(status):
                problems, _ = self.ledger(self.row(status=status))
                self.assertEqual(1, len(problems), problems)
                self.assertIn("D9's Status", problems[0])

    def test_an_open_row_without_a_found_date_fails(self):
        problems, _ = self.ledger("| D9 | 🟡 | Open | bug | qa: X-1 | x | y | no date here |")
        self.assertEqual(1, len(problems), problems)
        self.assertIn("does not say when it was found", problems[0])

    def test_an_unknown_type_or_source_fails_whenever_the_row_was_found(self):
        for found in ("2026-11-21", "2026-01-01"):
            with self.subTest(found):
                problems, _ = self.ledger(self.row(type="logic", found=found))
                self.assertEqual(1, len(problems), problems)
                self.assertIn("D9's Type 'logic' is not one of spec-divergence", problems[0])
                problems, _ = self.ledger(self.row(source="PR review: PR #4", found=found))
                self.assertEqual(1, len(problems), problems)
                self.assertIn("does not start with one of qa, fe", problems[0])

    def test_a_source_without_its_reference_fails(self):
        for source in ("qa", "code-review:", "`audit`:  "):
            with self.subTest(source):
                problems, _ = self.ledger(self.row(source=source))
                self.assertEqual(1, len(problems), problems)
                self.assertIn("has no reference", problems[0])

    def test_a_missing_type_or_source_fails_from_the_gate_and_warns_before_it(self):
        problems, warnings = self.ledger(self.row(type="—", source="", found="2026-11-20"))
        self.assertEqual(1, len(problems), problems)
        self.assertIn("D9 has no Type and Source", problems[0])
        problems, warnings = self.ledger(self.row(type="—", found="2026-11-19"))
        self.assertEqual([], problems)
        self.assertIn("D9 has no Type", warnings[0])
        self.assertIn("book defects classify", warnings[0])

    def test_a_blocked_row_need_not_carry_them_but_what_it_carries_is_checked(self):
        self.assertEqual(([], []), self.ledger(self.row(status="Blocked — Q3", type="—", source="—")))
        problems, _ = self.ledger(self.row(status="Blocked — Q3", type="oops", source="—"))
        self.assertEqual(1, len(problems), problems)

    def test_a_ledger_without_the_columns_only_warns(self):
        old = ("# Defects\n\n## Open\n\n| Id | Sev | Status | Defect | Where | Found by | Note |\n"
               "|---|---|---|---|---|---|---|\n{rows}\n\n## Fixed\n\n{fixed}\n")
        problems, warnings = self.ledger("| D9 | 🟡 | Open | x | y | Review | *Class: other · found 2026-11-21* |",
                                         table=old)
        self.assertEqual([], problems)
        self.assertEqual(1, len(warnings), warnings)
        self.assertIn("no Type and Source columns", warnings[0])

    def test_the_archive_and_the_fixed_entries_are_exempt(self):
        self.repo.write("docs/archive/defects-closed.md", "# Closed\n\n| Id | Sev | Status | Type | Source | Defect |\n"
                                                          "|---|---|---|---|---|---|\n| D3 | 🟡 | x | oops | who | y |\n")
        fixed = "| Id | Defect | Fixed in |\n|---|---|---|\n| D4 | a thing, found 2026-12-01 | PR #4 |"
        self.assertEqual(([], []), self.ledger(fixed=fixed))

    def test_a_gate_that_is_not_a_date_could_not_be_checked(self):
        for gate in ("2026/11/20", "20 Nov 2026", ""):
            with self.subTest(gate):
                problems, _ = self.ledger(self.row(), gate=gate)
                self.assertEqual(1, len(problems), problems)
                self.assertIn("not a YYYY-MM-DD date", problems[0])

    def test_the_lists_are_the_ones_the_books_rules_give(self):
        text = (check_book.ROOT / ".claude" / "book" / "docs.md").read_text(encoding="utf-8")
        section = text.split("\n## Defects", 1)[1].split("\n## ", 1)[0]
        listed = re.findall(r"^\s*\| `([a-z][\w-]*)` \|", section, re.M)
        self.assertEqual(listed, list(check_book.TYPES) + list(check_book.SOURCES))

    def test_the_counts_are_by_type_and_by_source(self):
        self.ledger(self.row(id="D1"), self.row(id="D2", type="security", source="code-review: PR #2"),
                    self.row(id="D3", source="qa: PROJ-9"), self.row(id="D4", type="—", source="—"))
        types, sources = check_book.defect_counts(self.repo.root)
        self.assertEqual(types, {"bug": 2, "security": 1, "—": 1})
        self.assertEqual(sources, {"qa": 2, "code-review": 1, "—": 1})

    STATES = ("# Questions\n\n## Still open\n\n### The open questions in full\n\n{open}\n\n"
              "## Answered, waiting for the FSD text\n\n{answered}\n\n## Superseded\n\n## Resolved\n\n")

    def states(self, open: str = "", answered: str = "", archived: str = "") -> tuple[list[str], list[str]]:
        self.repo.write("docs/open-questions.md", self.STATES.format(open=open, answered=answered))
        if archived:
            self.repo.write("docs/archive/questions-resolved.md", "# Resolved\n\n" + archived + "\n")
        return check_book.question_state_problems(self.repo.root)

    @staticmethod
    def question(ident: str, state: str, posted: str = "—", answered: str = "—", text: str = "not yet") -> str:
        return (f"#### {ident} — A question\n\n**State:** {state} · **Posted:** {posted} · **Answered:** {answered} · "
                f"**In FSD text:** {text} · **Thread resolved:** no\n\nThe question.\n")

    def test_the_fields_of_a_state_line_are_read(self):
        fields = check_book.state_fields("**State:** OPEN · POSTED · **Posted:** 2026-11-02, thread 4 · "
                                         "**Answered:** — · **In FSD text:** not yet · **Thread resolved:** no")
        self.assertEqual(fields, {"State": "OPEN · POSTED", "Posted": "2026-11-02, thread 4", "Answered": "—",
                                  "In FSD text": "not yet", "Thread resolved": "no"})

    def test_each_state_in_its_place_with_its_fields_passes(self):
        problems, warnings = self.states(
            open=self.question("Q1", "OPEN · NOT POSTED") + self.question("Q2", "OPEN · POSTED", posted="2026-11-02"),
            answered=self.question("Q3", "ANSWERED · THREAD ONLY", posted="2026-11-02", answered="2026-11-05, BA"),
            archived=self.question("Q4", "RESOLVED", posted="2026-11-01", answered="2026-11-03, BA",
                                   text="V12 §2.4").replace("####", "##"))
        self.assertEqual((problems, warnings), ([], []))

    def test_each_state_without_the_field_it_needs_fails(self):
        cases = [(self.question("Q2", "OPEN · POSTED"), "", "Q2 is OPEN · POSTED but has no Posted"),
                 ("", self.question("Q3", "ANSWERED · THREAD ONLY", posted="2026-11-02"),
                  "Q3 is ANSWERED · THREAD ONLY but has no Answered")]
        for open_, answered, expected in cases:
            with self.subTest(expected):
                problems, _ = self.states(open=open_, answered=answered)
                self.assertEqual(1, len(problems), problems)
                self.assertIn(expected, problems[0])
        problems, _ = self.states(archived=self.question("Q4", "RESOLVED", answered="2026-11-03, BA").replace("####", "##"))
        self.assertEqual(1, len(problems), problems)
        self.assertIn("Q4 is RESOLVED but has no In FSD text", problems[0])

    def test_a_state_off_the_list_fails(self):
        problems, _ = self.states(open=self.question("Q1", "ANSWERED"))
        self.assertEqual(1, len(problems), problems)
        self.assertIn("Q1's State 'ANSWERED' is not one of", problems[0])

    def test_a_question_in_the_wrong_place_fails(self):
        resolved = self.question("Q5", "RESOLVED", answered="2026-11-03, BA", text="V12 §2")
        problems, _ = self.states(open=resolved)
        self.assertIn("is RESOLVED but still in docs/open-questions.md", problems[0])
        problems, _ = self.states(open=self.question("Q3", "ANSWERED · THREAD ONLY", posted="a", answered="b"))
        self.assertIn("belongs under ## Answered, waiting for the FSD text", problems[0])
        problems, _ = self.states(answered=self.question("Q1", "OPEN · NOT POSTED"))
        self.assertIn("belongs under ## Still open", problems[0])
        problems, _ = self.states(archived=self.question("Q6", "OPEN · NOT POSTED").replace("####", "##"))
        self.assertIn("is archived but its State is OPEN · NOT POSTED", problems[0])

    def test_a_question_without_a_state_only_warns_and_an_old_archive_is_not_judged(self):
        old = "#### Q7 — Old\n\n**Status:** OPEN · **Raised:** 2026-09-01\n\n**Recommendation:** x\n"
        problems, warnings = self.states(open=old, archived="## Q1 — Older\n\n**Status:** ANSWERED\n")
        self.assertEqual(problems, [])
        self.assertEqual(1, len(warnings), warnings)
        self.assertIn("Q7 has no **State:** line", warnings[0])

    def test_the_counts_are_per_state(self):
        self.states(open=self.question("Q1", "OPEN · NOT POSTED") + self.question("Q2", "OPEN · NOT POSTED")
                    + "#### Q8 — Old\n\n**Status:** OPEN\n",
                    answered=self.question("Q3", "ANSWERED · THREAD ONLY", posted="a", answered="b"))
        self.assertEqual(check_book.question_counts(self.repo.root),
                         {"OPEN · NOT POSTED": 2, "OPEN · POSTED": 0, "ANSWERED · THREAD ONLY": 1, "no State yet": 1})

    def test_an_open_question_with_a_state_line_still_needs_its_recommendation(self):
        self.repo.write("docs/open-questions.md", self.STATES.format(open=self.question("Q1", "OPEN · NOT POSTED"),
                                                                     answered=""))
        _, warnings = check_book.recommendation_problems(self.repo.root)
        self.assertIn("Q1 is open with no", warnings[0])

    QUESTIONS = ("# Questions\n\n## Still open\n\n| Id | Question |\n|---|---|\n{rows}\n\n"
                 "### The open questions in full\n\n{open}\n\n## Superseded\n\n{superseded}\n\n"
                 "## Resolved\n\n### Topic\n\n{resolved}\n")

    def ledgers(self, defects: str = "| D2 | 🟡 | Open | x | y | Test | z |",
                fixed: str = "- **D1** — it was wrong. Fixed in PR #1.",
                archived_d: str = "| D1 | 🟡 | x | y | Test | z | abc | PR #1 |",
                rows: str = "| [Q3](#q3--open) | Open? |", open_q: str = "#### Q3 — Open?\n\n**Status:** OPEN",
                superseded: str = "#### Q2 — Old?\n\n**Superseded by FSD V8.**",
                resolved: str = "#### Q1 — Answered?\n\nYes.", archived_q: str = "## Q1 — Answered?\n\nYes.\n\n"
                                                                                "## Q2 — Old?\n\nYes.") -> list[str]:
        self.repo.write("docs/defects.md", self.DEFECTS.format(rows=defects) + "\n## Fixed\n\n" + fixed + "\n")
        self.repo.write("docs/archive/defects-closed.md", "# Closed\n\n## Fixed\n\n| Id | Sev |\n|---|---|\n"
                        + archived_d + "\n")
        self.repo.write("docs/open-questions.md", self.QUESTIONS.format(rows=rows, open=open_q, superseded=superseded,
                                                                        resolved=resolved))
        self.repo.write("docs/archive/questions-resolved.md", "# Resolved\n\n" + archived_q + "\n")
        return check_book.id_problems(self.repo.root)

    def test_ledgers_with_each_id_once_pass_and_a_closed_id_s_entry_beside_its_archived_row_is_not_a_repeat(self):
        self.assertEqual(self.ledgers(), [])

    def test_an_id_mentioned_outside_a_heading_or_row_start_is_a_reference(self):
        self.assertEqual(self.ledgers(defects="| D2 | 🟡 | Open | like D1 and Q1 | y | Test | z |",
                                      open_q="#### Q3 — Open?\n\nSee Q1, Q2 and D1.\n\n- **D1** is cited here"), [])

    def test_a_defect_id_on_two_open_rows_fails(self):
        problems = self.ledgers(defects="| D2 | 🟡 | Open | x | y | Test | z |\n| **D2** | 🔵 | Open | a | b | QA | c |")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("docs/defects.md: D2 heads a row 2 times (lines 7, 8)", problems[0])

    def test_a_defect_id_twice_in_the_fixed_index_or_the_archive_fails(self):
        self.assertEqual(len(self.ledgers(fixed="- **D1** — a.\n- **D1** — b.")), 1)
        problems = self.ledgers(archived_d="| D1 | 🟡 | x |\n| D1 | 🔵 | y |")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("docs/archive/defects-closed.md: D1 heads a row 2 times", problems[0])

    def test_an_open_defect_also_fixed_or_archived_fails(self):
        problems = self.ledgers(defects="| D1 | 🟡 | Open | x | y | Test | z |")
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("lists it as fixed", problems[0])
        self.assertIn("holds its closed row", problems[1])

    def test_a_question_heading_twice_fails_wherever_the_sections_are(self):
        problems = self.ledgers(resolved="#### Q1 — Answered?\n\nYes.\n\n#### Q3 — Again?\n\nNo.")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("docs/open-questions.md: Q3 heads a section 2 times", problems[0])

    def test_a_question_summary_row_twice_fails(self):
        problems = self.ledgers(rows="| [Q3](#q3--open) | Open? |\n| [Q3](#q3--open) | Again? |")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("heads a summary row 2 times", problems[0])

    def test_a_question_twice_in_the_archive_fails(self):
        problems = self.ledgers(archived_q="## Q1 — Answered?\n\n## Q2 — Old?\n\n## Q1 — Answered again?")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("docs/archive/questions-resolved.md: Q1 heads a section 2 times", problems[0])

    def test_the_archive_s_history_headings_are_not_definitions(self):
        self.assertEqual(self.ledgers(archived_q="## Q1 — Answered?\n\n## Q2 — Old?\n\n"
                                                 "## Earlier wording\n\n### Q1, as it stood on 2026-11-10 — Answered?"), [])

    def test_a_still_open_question_also_archived_fails(self):
        problems = self.ledgers(archived_q="## Q1 — Answered?\n\n## Q2 — Old?\n\n## Q3 — Open?")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("Q3 is still open, but docs/archive/questions-resolved.md", problems[0])

    def test_fixed_entries_as_table_rows_and_a_won_t_fix_section_are_read_by_section(self):
        # A ledger may keep its Fixed entries as a table, and close by decision under Won't fix.
        fixed = "| Id | Defect | Fixed in |\n|---|---|---|\n| **D1** | It was wrong. | PR #1 |"
        wont = "\n\n## Won't fix, or not ours\n\n| Id | Defect | Why |\n|---|---|---|\n| **D4** | Not ours. | Decision |"
        self.assertEqual(self.ledgers(fixed=fixed + wont, archived_d="| D1 | 🟡 | x |\n| D4 | 🟡 | y |"), [])
        self.assertEqual(len(self.ledgers(fixed=fixed + "\n| **D1** | Again. | PR #2 |" + wont)), 1)
        problems = self.ledgers(fixed=fixed + wont.replace("D4", "D1"))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("lists it as fixed", problems[0])
        problems = self.ledgers(fixed=fixed + wont.replace("D4", "D2"))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("docs/defects.md: D2 heads a row 2 times", problems[0])

    def test_an_archive_with_question_sections_at_level_four_is_read_too(self):
        self.assertEqual(self.ledgers(archived_q="## Resolved\n\n#### Q1 · Answered?\n\n#### Q2 · Old?"), [])
        problems = self.ledgers(archived_q="#### Q1 · Answered?\n\n#### Q2 · Old?\n\n#### Q1 · Again?")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("Q1 heads a section 2 times", problems[0])

    def test_a_question_table_inside_the_questions_in_full_is_a_reference(self):
        self.assertEqual(self.ledgers(open_q="#### Q3 — Open?\n\n| [Q3](#q3--open) | itself, cited |"), [])

    def test_a_duplicate_id_fails_the_whole_check(self):
        self.ledgers(fixed="- **D1** — a.\n- **D1** — b.")
        self.repo.write("CLAUDE.md", OWNER_TABLE.replace("| Plans | `docs/plans/` |",
                                                         "| Plans | `docs/plans/` |\n| Ledgers | `docs/` |"))
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("Ids: 1 problem(s)", out)
        self.assertNotIn("Links:", out)

    def test_an_unowned_doc_fails(self):
        self.repo.write("docs/stray.md", "# Stray\n")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("docs/stray.md: no owner", out)

    def test_a_link_to_a_missing_file_fails(self):
        self.repo.write("docs/plans/b.md", "# B\n\nSee [gone](../gone.md).\n")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("docs/plans/b.md:3: links to ../gone.md, which does not exist", out)

    def test_a_link_to_a_missing_heading_fails(self):
        self.repo.write("docs/plans/b.md", "# B\n\nSee [nowhere](a.md#no-such-heading).\n")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("that heading does not exist", out)

    def test_struck_through_text_in_a_live_doc_fails(self):
        self.repo.write("docs/plans/b.md", "# B\n\n~~This was true once.~~\n")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("struck-through text", out)

    def test_a_converted_registers_struck_text_warns_and_a_written_ones_fails(self):
        # Book D74: a converted register carries the audit's own history until a person tidies it.
        mark = check_book.requirements_register.CONVERTED
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "docs/requirements/evidence").mkdir(parents=True)
            (root / "docs/requirements/labs.md").write_text(f"# Labs\n\n{mark}\n\n~~old~~ new\n", encoding="utf-8")
            (root / "docs/requirements/evidence/labs.md").write_text(f"# E\n\n{mark}\n\n~~x~~\n", encoding="utf-8")
            (root / "docs/requirements/own.md").write_text("# Own\n\n~~old~~ new\n", encoding="utf-8")
            files = ["docs/requirements/labs.md", "docs/requirements/evidence/labs.md", "docs/requirements/own.md"]
            failures = check_book.history_problems(files, root)
            warnings = check_book.history_problems(files, root, converted=True)
        self.assertEqual([p.split(":")[0] for p in failures], ["docs/requirements/own.md"])
        self.assertEqual([p.split(":")[0] for p in warnings], files[:2])

    def test_a_link_inside_a_code_fence_is_not_checked(self):
        self.repo.write("docs/plans/b.md", "# B\n\n```\n[gone](../gone.md)\n```\n")
        self.assertEqual(self.check()[0], 0)


# ---------------------------------------------------------------- test-trend

class TestTrendTests(RepoTestCase):
    def test_count_finds_every_fact_and_theory(self):
        self.repo.write("tests/A.cs", "[Fact]\nvoid A() {}\n[Theory]\n[InlineData(1)]\nvoid B(int x) {}\n"
                                      "[DatabaseFact]\nvoid C() {}\n")
        self.repo.write("src/B.cs", "[Fact]\n")  # outside tests/: not counted
        self.repo.commit()
        self.assertEqual(test_trend.count("HEAD", self.repo.root), 3)

    def test_history_counts_each_first_parent_state(self):
        self.repo.write("tests/A.cs", "[Fact]\n[Fact]\n")
        self.repo.commit("Merged in one (pull request #1)")
        self.repo.write("tests/A.cs", "[Fact]\n")
        self.repo.commit("Merged in two (pull request #2)")
        rows = test_trend.history(self.repo.root, "main")
        self.assertEqual([n for *_, n in rows], [2, 1])
        self.assertEqual([(where, by) for _, where, by in test_trend.falls(rows)], [("PR #2", 1)])

    def test_falls_names_each_drop_and_skips_growth(self):
        day = date(2026, 9, 1)
        rows = [(day, "a" * 40, "Merged in x (pull request #10)", 5),
                (day, "b" * 40, "Merged in y (pull request #11)", 7),
                (day, "c" * 40, "Direct commit", 4)]
        self.assertEqual(test_trend.falls(rows), [(day, "cccccccc", 3)])

    def test_only_the_states_before_the_first_test_are_dropped_and_a_fall_to_zero_shows(self):
        self.repo.write("tests/A.cs", "void A() {}\n")
        self.repo.commit("Before any test")
        self.repo.write("tests/A.cs", "[Fact]\n[Fact]\n")
        self.repo.commit("Merged in one (pull request #1)")
        self.repo.write("tests/A.cs", "void A() {}\n")
        self.repo.commit("Merged in two (pull request #2)")
        rows = test_trend.history(self.repo.root, "main")
        self.assertEqual([n for *_, n in rows], [2, 0])
        self.assertEqual([(where, by) for _, where, by in test_trend.falls(rows)], [("PR #2", 2)])
        code, out = run_main(test_trend.main, [], root=self.repo.root, dev="main")
        self.assertEqual(code, 0, out)
        self.assertIn("PR #2 (−2)", out)

    def test_a_state_with_no_tests_counts_zero(self):
        self.repo.write("tests/A.cs", "void A() {}\n")  # git grep exits 1: no match, not a failure
        self.repo.commit()
        self.assertEqual(test_trend.count("HEAD", self.repo.root), 0)

    def test_a_failed_grep_is_an_error_not_zero_tests(self):
        self.repo.write("tests/A.cs", "[Fact]\n")
        self.repo.commit()
        with self.assertRaises(test_trend.GitFailed):
            test_trend.count("0" * 40, self.repo.root)

    def test_a_failed_grep_stops_the_report_rather_than_showing_a_fall(self):
        self.repo.write("tests/A.cs", "[Fact]\n[Fact]\n")
        self.repo.commit("Merged in one (pull request #1)")
        self.repo.write("tests/A.cs", "[Fact]\n[Fact]\n[Fact]\n")
        self.repo.commit("Merged in two (pull request #2)")
        last = self.repo.git("rev-parse", "HEAD").strip()
        real = test_trend.git

        def grep_fails_at_the_last(*args, root, allowed=(0,)):
            if args[0] == "grep" and last in args:
                return real("grep", "-c", "x", "no-such-revision", root=root, allowed=allowed)
            return real(*args, root=root, allowed=allowed)

        with mock.patch.object(test_trend, "git", grep_fails_at_the_last):
            code, out = run_main(test_trend.main, [], root=self.repo.root, dev="main")
        self.assertEqual(code, 1)
        self.assertIn("could not count", out)
        self.assertNotIn("fell", out)


# ---------------------------------------------------------------- gen-task-rollup

PLAN = """# Widgets — Design

**Status:** In progress · **Owner:** the BE team

## Tasks

| # | Task | FSD | Role | Status | Jira | PR | Merged |
|---|---|---|---|---|---|---|---|
{rows}

## After
"""
BUILT = "| 1 | Build it | 3.1 | BE | Built | — | #5 | 2026-09-01 |"
OPEN = "| 2 | Wire it | 3.2 | FE | Not started | — | — | — |"
PROJECT = """# Project

<!-- task-rollup:begin -->
<!-- task-rollup:end -->

<!-- open-tasks:begin -->
<!-- open-tasks:end -->

<!-- pace:begin -->
<!-- pace:end -->
"""


class TaskRollupTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.plans = self.root / "plans"
        self.plans.mkdir()
        self.project = self.root / "project.md"
        self.project.write_text(PROJECT, encoding="utf-8")
        # main() reads [book] derived_status_enforced from ROOT's factory.toml: the temp root holds
        # none unless a test writes one, so the fallback (on) applies, whatever this repository sets.
        self.enterContext(mock.patch.object(task_rollup, "ROOT", self.root))

    def plan(self, *rows: str) -> Path:
        path = self.plans / "widgets.md"
        path.write_text(PLAN.format(rows="\n".join(rows)), encoding="utf-8")
        return path

    def test_a_valid_task_table_has_no_problems(self):
        rows, problems = task_rollup.task_table(self.plan(BUILT, OPEN))
        self.assertEqual(problems, [])
        self.assertEqual([r["Status"] for r in rows], ["Built", "Not started"])
        self.assertEqual(task_rollup.summary(rows), ("1 of 1 built", "—", "FE 1"))

    def test_out_of_order_numbers_and_unknown_roles_are_problems(self):
        _, problems = task_rollup.task_table(self.plan(BUILT, OPEN.replace("| 2 |", "| 3 |").replace("FE", "UX")))
        self.assertTrue(any("out of order" in p for p in problems))
        self.assertTrue(any("role 'UX'" in p for p in problems))

    def test_a_removed_plan_leaves_the_progress_and_open_tables_but_stays_in_the_pace(self):
        path = self.plan(BUILT, OPEN)
        path.write_text(
            path.read_text(encoding="utf-8").replace("**Status:** In progress", "**Status:** `Removed`"),
            encoding="utf-8")
        tables, problems = task_rollup.render(self.plans)
        self.assertEqual(problems, [])
        self.assertIn("No plan has a task table yet", tables["task-rollup"])
        self.assertIn("Nothing open", tables["open-tasks"])
        self.assertIn("2026-08-31", tables["pace"])

    def carry_over(self) -> Path:
        """A second plan, beside widgets.md, that a Moved task can point at: one open BE task."""
        path = self.plans / "carry-over.md"
        path.write_text(PLAN.replace("# Widgets", "# Carry-over").format(
            rows="| 1 | Wire it later | 3.2 | BE | Not started | — | — | — |"), encoding="utf-8")
        return path

    def test_a_moved_task_keeps_its_number_and_leaves_progress_and_the_open_tasks(self):
        self.carry_over()
        moved = "| 2 | Wire it, moved to [carry-over task 1](carry-over.md#tasks) | 3.2 | BE | Moved | — | — | — |"
        drawn = "| 3 | Draw it, moved to [carry-over task 1](carry-over.md#tasks) | 3.2 | FE | Moved | — | — | — |"
        rows, problems = task_rollup.task_table(self.plan(BUILT, moved, drawn))
        self.assertEqual(problems, [])
        self.assertEqual(task_rollup.summary(rows), ("1 of 1 built", "—", "—"), "neither Moved row counts, BE or FE")
        tables, problems = task_rollup.render(self.plans)
        self.assertEqual(problems, [])
        self.assertIn("Wire it later", tables["open-tasks"], "the task is open where it went")
        self.assertNotIn("Wire it, moved", tables["open-tasks"])
        self.assertNotIn("Draw it", tables["open-tasks"])

    def test_a_dropped_task_counts_neither_in_progress_nor_as_open(self):
        rows = [{"#": "1", "Task": "Build it", "Role": "BE", "Status": "Built"},
                {"#": "2", "Task": "Dropped (owner): a core", "Role": "BE", "Status": "Dropped"}]
        self.assertEqual(task_rollup.summary(rows), ("1 of 1 built", "—", "—"))
        self.assertEqual(task_rollup.derived_status(rows), "Built")

    def test_a_moved_task_must_link_to_where_it_went(self):
        _, problems = task_rollup.task_table(self.plan(BUILT, OPEN.replace("Not started", "Moved")))
        self.assertTrue(any("a Moved task links to the plan task it moved to" in p for p in problems))

    def test_a_moved_task_must_link_to_another_plan_with_a_task_table(self):
        self.carry_over()
        (self.plans / "questions.md").write_text("# Questions\n\n## Q1\n", encoding="utf-8")
        for link in ["questions.md#q1", "widgets.md#tasks", "../project.md#tasks", "missing.md#tasks"]:
            row = f"| 2 | Wire it, moved to [there]({link}) | 3.2 | BE | Moved | — | — | — |"
            _, problems = task_rollup.task_table(self.plan(BUILT, row))
            self.assertTrue(any("a Moved task links to the plan task it moved to" in p for p in problems), link)

    def test_only_a_built_task_has_a_merged_date(self):
        _, problems = task_rollup.task_table(self.plan(BUILT.replace("Built", "In progress")))
        self.assertTrue(any("only a Built task has a Merged date" in p for p in problems))

    def test_check_passes_after_a_write_and_fails_when_a_plan_moves(self):
        self.plan(BUILT, OPEN)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.assertEqual(task_rollup.main(["--check"], self.plans, self.project), 1)  # empty tables: stale
            self.assertEqual(task_rollup.main([], self.plans, self.project), 0)
            self.assertEqual(task_rollup.main(["--check"], self.plans, self.project), 0)
            self.plan(BUILT, OPEN.replace("Not started", "In progress"))
            self.assertEqual(task_rollup.main(["--check"], self.plans, self.project), 1)
        self.assertIn("2026-08-31 | 1 | 1 | 1", self.project.read_text(encoding="utf-8"))

    def test_an_impossible_merged_date_fails_naming_the_plan_and_task(self):
        self.plan(BUILT.replace("2026-09-01", "2026-13-45"))
        code, out = run_main(task_rollup.main, ["--check"], self.plans, self.project)
        self.assertEqual(code, 1)
        self.assertIn("widgets.md task 1: Merged '2026-13-45' is not a date", out)
        self.assertNotIn("Traceback", out)

    def test_missing_markers_are_a_problem(self):
        self.plan(BUILT)
        self.project.write_text("# Project\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            self.assertEqual(task_rollup.main(["--check"], self.plans, self.project), 1)
        self.assertIn("markers are missing", out.getvalue())

    # A plan's status line and its Part 1 row follow from its BE tasks (/book:progress, step 4).
    PART1 = ("# Part 1 — Features\n\n| Feature | Plan | Status | Owner |\n|---|---|---|---|\n"
             "| Widgets | [widgets](plans/widgets.md) | `{status}` — the key ring is open | the BE team |\n\n"
             "# Part 2 — Questions\n\n| Plan | Status |\n|---|---|\n| [widgets](plans/widgets.md) | `Removed` |\n")

    def with_part1(self, status: str) -> None:
        self.project.write_text(PROJECT + "\n" + self.PART1.format(status=status), encoding="utf-8")

    def status_of(self, path: Path) -> str:
        return task_rollup.status_line(path)[1].group(2)

    def test_the_status_follows_the_back_end_tasks(self):
        moved = "| 2 | Wire it, moved to [carry-over task 1](carry-over.md#tasks) | 3.2 | BE | Moved | — | — | — |"
        self.carry_over()
        cases = [((BUILT, OPEN), "Built"), ((BUILT, moved), "Built"),
                 ((BUILT, OPEN.replace("FE", "BE")), "In progress"),
                 ((BUILT, OPEN.replace("FE", "BE").replace("Not started", "Blocked")), "In progress"),
                 ((OPEN.replace("| 2 |", "| 1 |").replace("FE", "BE"),), "Not started"),
                 ((OPEN.replace("| 2 |", "| 1 |"),), None)]
        for rows, expected in cases:
            table, _ = task_rollup.task_table(self.plan(*rows))
            self.assertEqual(task_rollup.derived_status(table), expected, rows)

    def test_check_fails_on_a_status_line_and_a_part1_row_that_disagree_and_the_write_fixes_only_the_word(self):
        path = self.plan(BUILT, OPEN)  # every BE task built; the line says In progress
        self.with_part1("In progress")
        code, out = run_main(task_rollup.main, [], self.plans, self.project)  # the tables, and the statuses
        self.assertEqual(code, 0)
        self.assertIn("widgets.md: status line In progress -> Built", out)
        self.assertEqual(self.status_of(path), "Built")
        text = self.project.read_text(encoding="utf-8")
        self.assertIn("| `Built` — the key ring is open | the BE team |", text, "the open items stay")
        self.assertIn("| [widgets](plans/widgets.md) | `Removed` |", text, "outside Part 1 is not touched")
        self.assertEqual(run_main(task_rollup.main, ["--check"], self.plans, self.project)[0], 0)

        path.write_text(path.read_text(encoding="utf-8").replace("**Status:** Built", "**Status:** In progress"),
                        encoding="utf-8")
        code, out = run_main(task_rollup.main, ["--check"], self.plans, self.project)
        self.assertEqual(code, 1)
        self.assertIn("widgets.md: status line In progress should be Built", out)
        self.assertEqual(self.status_of(path), "In progress", "the check writes nothing")

        self.plan(BUILT, OPEN.replace("FE", "BE"))  # a BE task open: Built in Part 1 is wrong
        code, out = run_main(task_rollup.main, ["--check"], self.plans, self.project)
        self.assertEqual(code, 1)
        self.assertIn("Part 1: widgets.md Built should be In progress", out)
        self.assertNotIn("status line", out)

    def factory_toml(self, book: str) -> None:
        """This temp root's devtools/factory/factory.toml, with `book` as its [book] table's body."""
        path = self.root / "devtools" / "factory" / "factory.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'base_ref = "origin/main"\n\n[book]\nname = "Widgets"\n{book}', encoding="utf-8")

    def test_derived_status_enforced_is_read_from_factory_toml_with_the_constant_as_fallback(self):
        self.assertTrue(task_rollup.DERIVED_STATUS_ENFORCED, "the fallback is on")
        self.assertIs(task_rollup.configured_enforced(self.root), True, "no factory.toml: the fallback")
        self.factory_toml("")
        self.assertIs(task_rollup.configured_enforced(self.root), True, "no key: the fallback")
        self.factory_toml("derived_status_enforced = false\n")
        self.assertIs(task_rollup.configured_enforced(self.root), False)
        self.factory_toml("derived_status_enforced = true\n")
        self.assertIs(task_rollup.configured_enforced(self.root), True)
        self.factory_toml('derived_status_enforced = "no"\n')
        self.assertIs(task_rollup.configured_enforced(self.root), True, "not a true/false: the fallback")
        self.factory_toml("derived_status_enforced = \n")
        self.assertIs(task_rollup.configured_enforced(self.root), True, "unparsable: the fallback")
        with mock.patch.object(task_rollup, "DERIVED_STATUS_ENFORCED", False):
            self.assertIs(task_rollup.configured_enforced(self.root), False, "the fallback is the constant")

    def test_roles_and_our_role_are_read_from_factory_toml_with_fallbacks(self):
        fallback = (task_rollup.FALLBACK_ROLES, task_rollup.FALLBACK_OURS)
        self.assertEqual(task_rollup.configured_roles(self.root), fallback, "no factory.toml: the fallback")
        self.factory_toml("")
        self.assertEqual(task_rollup.configured_roles(self.root), fallback, "no keys: the fallback")
        self.factory_toml('roles = ["FE", "QA", "Design"]\nour_role = "FE"\n')
        self.assertEqual(task_rollup.configured_roles(self.root), (["FE", "QA", "Design"], "FE"))
        self.factory_toml('roles = ["BE", "FE"]\n')
        self.assertEqual(task_rollup.configured_roles(self.root), (["BE", "FE"], "BE"), "our_role falls back")
        self.factory_toml('roles = ["FE", "QA"]\n')
        self.assertEqual(task_rollup.configured_roles(self.root), fallback, "fallback our_role not in roles")
        self.factory_toml('roles = "BE"\nour_role = "BE"\n')
        self.assertEqual(task_rollup.configured_roles(self.root), (task_rollup.FALLBACK_ROLES, "BE"),
                         "roles not a list: the fallback list")
        self.factory_toml('roles = ["BE", ""]\nour_role = "BE"\n')
        self.assertEqual(task_rollup.configured_roles(self.root)[0], task_rollup.FALLBACK_ROLES, "an empty role")
        self.factory_toml("roles = \n")
        self.assertEqual(task_rollup.configured_roles(self.root), fallback, "unparsable: the fallback")

    def test_a_configured_role_list_is_what_a_task_row_may_name(self):
        with mock.patch.object(task_rollup, "ROLES", ["FE", "QA"]), mock.patch.object(task_rollup, "OURS", "FE"):
            _, problems = task_rollup.task_table(self.plan(BUILT, OPEN))
        self.assertTrue(any("role 'BE' is not one of FE, QA" in p for p in problems), problems)

    def test_derived_status_enforced_on_fails_the_check_and_the_write_corrects_it(self):
        path = self.plan(BUILT, OPEN)  # every BE task built; the line says In progress
        self.with_part1("In progress")
        self.factory_toml("derived_status_enforced = true\n")
        run_main(task_rollup.main, ["--check"], self.plans, self.project)  # fill the tables first
        code, out = run_main(task_rollup.main, ["--check"], self.plans, self.project)
        self.assertEqual(code, 1)
        self.assertIn("widgets.md: status line In progress should be Built", out)
        self.assertNotIn("warning", out)
        self.assertEqual(run_main(task_rollup.main, [], self.plans, self.project)[0], 0)
        self.assertEqual(self.status_of(path), "Built")

    def test_derived_status_enforced_off_only_warns_and_writes_no_status(self):
        path = self.plan(BUILT, OPEN)
        self.with_part1("In progress")
        self.factory_toml("derived_status_enforced = false\n")
        code, out = run_main(task_rollup.main, [], self.plans, self.project)  # writes the tables only
        self.assertEqual(code, 0)
        self.assertIn("warning, not enforced ([book] derived_status_enforced = false): "
                      "widgets.md: status line In progress should be Built", out)
        self.assertEqual(self.status_of(path), "In progress", "the write leaves the status line")
        text = self.project.read_text(encoding="utf-8")
        self.assertIn("| `In progress` — the key ring is open |", text, "and the Part 1 cell")
        self.assertIn("1 of 1 built", text, "the generated tables are still written")
        code, out = run_main(task_rollup.main, ["--check"], self.plans, self.project)
        self.assertEqual(code, 0, out)
        self.assertIn("warning, not enforced", out)
        # The switch is the caller's too: an explicit argument wins over factory.toml.
        self.assertEqual(run_main(task_rollup.main, ["--check"], self.plans, self.project, enforced=True)[0], 1)

    def test_derived_status_enforced_off_still_fails_a_broken_table(self):
        self.plan(BUILT, OPEN.replace("| 2 |", "| 3 |"))
        self.factory_toml("derived_status_enforced = false\n")
        code, out = run_main(task_rollup.main, ["--check"], self.plans, self.project)
        self.assertEqual(code, 1)
        self.assertIn("out of order", out)

    def test_a_plan_not_yet_started_may_say_in_design_and_a_removed_plan_keeps_its_status(self):
        path = self.plan(OPEN.replace("| 2 |", "| 1 |").replace("FE", "BE"))
        path.write_text(path.read_text(encoding="utf-8").replace("**Status:** In progress", "**Status:** `In design`"),
                        encoding="utf-8")
        self.with_part1("In design")
        run_main(task_rollup.main, [], self.plans, self.project)
        self.assertEqual(run_main(task_rollup.main, ["--check"], self.plans, self.project)[0], 0)
        self.assertEqual(self.status_of(path), "In design")

        path = self.plan(BUILT, OPEN.replace("FE", "BE"))
        path.write_text(path.read_text(encoding="utf-8").replace("**Status:** In progress", "**Status:** `Removed`"),
                        encoding="utf-8")
        self.with_part1("Removed")
        run_main(task_rollup.main, [], self.plans, self.project)
        self.assertEqual(run_main(task_rollup.main, ["--check"], self.plans, self.project)[0], 0)
        self.assertEqual(self.status_of(path), "Removed")

    def test_a_status_line_or_part1_cell_with_no_status_is_a_problem(self):
        path = self.plan(BUILT)
        self.with_part1("Built")
        self.project.write_text(self.project.read_text(encoding="utf-8").replace("`Built` — the", "Done — the"),
                                encoding="utf-8")
        code, out = run_main(task_rollup.main, [], self.plans, self.project)
        self.assertEqual(code, 1)
        self.assertIn("the row for widgets.md has no `status` opening its Status cell", out)
        path.write_text(path.read_text(encoding="utf-8").replace("**Status:** In progress", "**Status:** Done"),
                        encoding="utf-8")
        code, out = run_main(task_rollup.main, ["--check"], self.plans, self.project)
        self.assertEqual(code, 1)
        self.assertIn("widgets.md: the status line does not open with one of", out)


class TaskRowsKeptTests(RepoTestCase):
    """A task row keeps its number and is never deleted, compared with the base branch (_template.md §9)."""

    THIRD = "| 3 | Test it | 3.3 | QA | Not started | — | — | — |"

    def setUp(self) -> None:
        super().setUp()
        self.plans = self.repo.root / "docs" / "plans"
        self.repo.write("docs/plans/widgets.md", PLAN.format(rows="\n".join([BUILT, OPEN, self.THIRD])))
        self.repo.commit()
        self.repo.git("update-ref", f"refs/remotes/{task_rollup.BASE}", "HEAD")

    def rows(self, *rows: str) -> list[str]:
        self.repo.write("docs/plans/widgets.md", PLAN.format(rows="\n".join(rows)), add=False)
        problems, notice = task_rollup.lost_tasks(self.plans)
        self.assertIsNone(notice)
        return problems

    def test_a_row_may_change_its_status_and_text(self):
        self.assertEqual(self.rows(BUILT, OPEN.replace("Not started", "Built").replace("Wire it", "Wire it all")
                                   .replace("| — | — | — |", "| — | #6 | 2026-09-02 |"), self.THIRD), [])

    def test_a_row_may_be_added(self):
        self.assertEqual(self.rows(BUILT, OPEN, self.THIRD, self.THIRD.replace("| 3 |", "| 4 |")), [])

    def test_a_deleted_last_row_fails_naming_its_number(self):
        problems = self.rows(BUILT, OPEN)
        self.assertTrue(any(f"widgets.md: task 3 is on {task_rollup.BASE} and gone here" in p for p in problems), problems)
        self.assertTrue(any(f"2 task rows where {task_rollup.BASE} has 3" in p for p in problems), problems)

    def test_a_deleted_row_with_the_rest_renumbered_fails(self):
        problems = self.rows(BUILT, self.THIRD.replace("| 3 |", "| 2 |"))
        self.assertTrue(any(f"task 3 is on {task_rollup.BASE} and gone here" in p for p in problems), problems)

    def test_a_plan_origin_dev_does_not_have_is_not_compared(self):
        self.repo.write("docs/plans/gadgets.md", PLAN.format(rows=BUILT), add=False)
        self.assertEqual(self.rows(BUILT, OPEN, self.THIRD), [])

    def test_without_origin_dev_it_says_so_and_skips(self):
        self.repo.git("update-ref", "-d", f"refs/remotes/{task_rollup.BASE}")
        self.repo.write("docs/plans/widgets.md", PLAN.format(rows=BUILT), add=False)
        problems, notice = task_rollup.lost_tasks(self.plans)
        self.assertEqual(problems, [])
        self.assertIn(f"could not compare the task tables with {task_rollup.BASE}", notice)

    def test_check_fails_on_a_deleted_row_and_prints_the_notice_without_origin_dev(self):
        project = self.repo.root / "docs" / "project.md"
        project.write_text(PROJECT, encoding="utf-8")
        self.assertEqual(run_main(task_rollup.main, [], self.plans, project)[0], 0)
        self.assertEqual(run_main(task_rollup.main, ["--check"], self.plans, project)[0], 0)
        self.repo.write("docs/plans/widgets.md", PLAN.format(rows="\n".join([BUILT, OPEN])), add=False)
        run_main(task_rollup.main, [], self.plans, project)
        code, out = run_main(task_rollup.main, ["--check"], self.plans, project)
        self.assertEqual(code, 1)
        self.assertIn(f"task 3 is on {task_rollup.BASE} and gone here", out)
        self.repo.git("update-ref", "-d", f"refs/remotes/{task_rollup.BASE}")
        code, out = run_main(task_rollup.main, ["--check"], self.plans, project)
        self.assertEqual(code, 0)
        self.assertIn(f"could not compare the task tables with {task_rollup.BASE}", out)


# ---------------------------------------------------------------- check-pipeline

SKILL = """---
name: {name}
description: >
  Use when the tests need a skill. {extra}
---

Body.

## What next

- Nothing.
"""


class SkillsTests(unittest.TestCase):
    def make(self, root: Path, folder: str, text: str) -> None:
        path = root / ".claude" / "skills" / folder / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def check(self, *skills: tuple[str, str]) -> tuple[list[str], list[str], int]:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, text in skills:
                self.make(root, name, text)
            return check_skills.problems(root)

    def test_a_well_formed_skill_passes_and_its_folded_description_is_measured(self):
        failures, warnings, total = self.check(("alpha", SKILL.format(name="alpha", extra="More.")))
        self.assertEqual((failures, warnings), ([], []))
        self.assertEqual(total, len("Use when the tests need a skill. More."))

    def test_no_frontmatter_fails(self):
        failures, _, _ = self.check(("alpha", "# Alpha\n\n## What next\n"))
        self.assertIn("no frontmatter", failures[0])

    def test_a_name_that_is_not_the_folder_fails(self):
        failures, _, _ = self.check(("alpha", SKILL.format(name="beta", extra="")))
        self.assertIn("must be the folder's name, 'alpha'", failures[0])

    def test_an_unknown_key_fails(self):
        text = SKILL.format(name="alpha", extra="").replace("name: alpha\n", "name: alpha\ntriggers: x\n")
        failures, _, _ = self.check(("alpha", text))
        self.assertIn("does not read: triggers", failures[0])

    def test_a_missing_or_overlong_description_fails(self):
        empty = "---\nname: alpha\n---\n\n## What next\n"
        self.assertIn("no description", self.check(("alpha", empty))[0][0])
        long = SKILL.format(name="beta", extra="x" * 1100)
        self.assertIn("the limit is 1,024", self.check(("beta", long))[0][0])

    def test_a_skill_without_what_next_fails(self):
        text = SKILL.format(name="alpha", extra="").replace("## What next", "## Afterwards")
        self.assertIn("no `## What next` section", self.check(("alpha", text))[0][0])

    def test_over_the_listing_budget_warns_and_never_fails(self):
        many = [(f"s{i}", SKILL.format(name=f"s{i}", extra="y" * 990)) for i in range(11)]
        failures, warnings, total = self.check(*many)
        self.assertEqual(failures, [])
        self.assertGreater(total, check_skills.LISTING_BUDGET)
        self.assertIn("over the 10,000 budget", warnings[0])

    def test_crlf_files_read_the_same(self):
        failures, _, _ = self.check(("alpha", SKILL.format(name="alpha", extra="").replace("\n", "\r\n")))
        self.assertEqual(failures, [])

    def test_this_repositorys_skills_pass(self):
        self.assertEqual(check_skills.problems()[0], [])

    def agent(self, name: str, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".claude" / "agents" / f"{name}.md"
            path.parent.mkdir(parents=True)
            path.write_text(text, encoding="utf-8")
            return check_skills.problems(Path(folder))[0]

    def test_an_agent_needs_its_name_a_description_and_a_tools_list(self):
        good = "---\nname: reviewer\ndescription: Use to review.\ntools: Read, Grep\n---\n\nBody.\n"
        self.assertEqual(self.agent("reviewer", good), [])
        self.assertIn("must be the file's name", self.agent("other", good)[0])
        self.assertIn("no `tools` list", self.agent("reviewer", good.replace("tools: Read, Grep\n", ""))[0])
        self.assertIn("does not read for an agent: when", self.agent("reviewer", good.replace("tools:", "when: x\ntools:"))[0])


class SelfTestRunnerTests(unittest.TestCase):
    """The sharded runner must fail exactly when `unittest discover` would, and never hide a test."""

    PASSING = "import unittest\n\nclass T(unittest.TestCase):\n" + "".join(
        f"    def test_{n}(self):\n        pass\n" for n in range(5))

    def run_runner(self, files: dict[str, str]) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as folder:
            for name, text in files.items():
                (Path(folder) / name).write_text(text, encoding="utf-8")
            done = subprocess.run([sys.executable, str(HERE / "run-self-tests.py"), folder, "--workers", "3"],
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout + done.stderr

    def test_every_test_passing_is_ok_with_the_full_count(self):
        code, output = self.run_runner({"test_a.py": self.PASSING, "test_b.py": self.PASSING})
        self.assertEqual(code, 0, output)
        self.assertIn("Ran 10 tests", output)
        self.assertIn("OK", output)

    def test_a_failing_test_fails_the_run_and_shows_its_output(self):
        failing = self.PASSING + "    def test_broken(self):\n        self.fail('the marker')\n"
        code, output = self.run_runner({"test_a.py": failing})
        self.assertEqual(code, 1, output)
        self.assertIn("the marker", output)
        self.assertIn("FAILED", output)

    def test_a_shard_that_crashes_fails_the_run(self):
        crashing = self.PASSING + "    def test_exit(self):\n        import os\n        os._exit(3)\n"
        code, output = self.run_runner({"test_a.py": crashing})
        self.assertEqual(code, 1, output)
        self.assertIn("FAILED", output)

    def test_a_module_that_cannot_import_fails_the_run(self):
        code, output = self.run_runner({"test_a.py": self.PASSING, "test_b.py": "import no_such_module_here\n"})
        self.assertEqual(code, 1, output)

    def test_zero_tests_fails(self):
        code, output = self.run_runner({"test_a.py": "import unittest\n"})
        self.assertEqual(code, 1, output)
        self.assertIn("Ran 0 tests", output)

    def test_a_skipped_test_fails(self):
        skipping = self.PASSING + "    @unittest.skip('why')\n    def test_skip(self):\n        pass\n"
        code, output = self.run_runner({"test_a.py": skipping})
        self.assertEqual(code, 1, output)
        self.assertIn("skipped=1", output)


# ---------------------------------------------------------------- check-test-setup.py

class LedgerIdsTests(unittest.TestCase):
    """The shared reader of the ledgers: every status a question or defect can have."""

    QUESTIONS = ("# Questions\n\n## Still open\n\n| Q | What |\n|---|---|\n| [Q1](#q1) | x |\n\n#### Q1 — Open?\n\n"
                 "## Superseded\n\n#### Q2 — Replaced\n\n## Resolved\n\n#### Q3 — Settled\n")
    DEFECTS = ("# Defects\n\n## Open\n\n| Id | Sev | Status | Defect |\n|---|---|---|---|\n| D4 | 🔵 | Mitigated | x |\n"
               "| **D5** | 🔵 | Open | y |\n\n## Fixed\n\n- **D6** — z. Fixed.\n")

    def setUp(self) -> None:
        sys.path.insert(0, str(HERE))
        import ledger_ids
        self.ledger_ids = ledger_ids

    def test_each_status_is_read_from_its_ledger(self):
        ledgers = self.ledger_ids.Ledgers.read(self.QUESTIONS, "#### Q9 — Old\n", self.DEFECTS,
                                               "| D7 | 🔵 | Fixed | old |\n")
        self.assertEqual({"Q1": "open", "Q2": "superseded", "Q3": "resolved", "Q9": "resolved", "D4": "mitigated",
                          "D5": "open", "D6": "fixed", "D7": "fixed"}, ledgers.status)
        self.assertEqual({"Q1"}, ledgers.open_rows)
        self.assertEqual({"D4", "D5"}, ledgers.ids("D", open_=True))

    def test_a_missing_heading_raises_rather_than_reading_nothing(self):
        with self.assertRaisesRegex(self.ledger_ids.LedgerMissing, "Still open"):
            self.ledger_ids.Ledgers.read("# Questions\n", "", self.DEFECTS, "")
        with self.assertRaisesRegex(self.ledger_ids.LedgerMissing, "Open"):
            self.ledger_ids.Ledgers.read(self.QUESTIONS, "", "# Defects\n", "")
        moved = self.QUESTIONS.replace("#### Q1 — Open?", "### Q1: Open?")
        with self.assertRaisesRegex(self.ledger_ids.LedgerMissing, "Still open"):
            self.ledger_ids.Ledgers.read(moved, "", self.DEFECTS, "")

    def test_a_book_with_every_question_answered_reads(self):
        answered = "# Questions\n\n## Still open\n\nNone.\n\n## Superseded\n\n## Resolved\n\n#### Q3 — Settled\n"
        ledgers = self.ledger_ids.Ledgers.read(answered, "", self.DEFECTS, "")
        self.assertEqual(set(), ledgers.ids("Q", open_=True))
        self.assertEqual("resolved", ledgers.status["Q3"])

    def test_a_citation_is_judged_by_its_own_words(self):
        flags = [self.ledger_ids.citation_flag(status, self.ledger_ids.own_words(line, next(self.ledger_ids.ID.finditer(line))))
                 for line, status in (("Q3 is still open.", "resolved"), ("Q1 was answered.", "open"))]
        self.assertEqual(["closed, cited as open", "open, cited as closed"], flags)
        not_fixed = "D4 is not fixed yet."
        match = next(self.ledger_ids.ID.finditer(not_fixed))
        self.assertIsNone(self.ledger_ids.citation_flag("open", self.ledger_ids.own_words(not_fixed, match)))


# ---------------------------------------------------------------- check-code-rules

TEMPLATE = HERE.parents[1] / "devtools" / "factory" / "book-template"


def shipped_rules(source: Path) -> dict:
    """The [[code_rules]] a defaults file ships, by id: the core book-defaults.toml or a stack's."""
    import tomllib
    text = source.read_text(encoding="utf-8")
    text = re.sub(r"\{\{(?:owners_toml|review_scope)\}\}", "", text)
    text = text.replace("[{{protected_branches}}]", '["x"]').replace("[{{packs}}]", "[]")
    text = text.replace("{{local_ci_required}}", "false")
    text = re.sub(r"\{\{[a-z_]+\}\}", "x", text)  # the rest sit inside quoted strings
    return {rule["id"]: rule for rule in tomllib.loads(text).get("code_rules", [])}


class CodeRulesTests(RepoTestCase):
    BAN = ('[[code_rules]]\nid = "anon"\npattern = \'\\.AllowAnonymous\\(\\)\'\nfiles = ["src/**/*.cs"]\n'
           'skip_lines = \'^\\s*//\'\nmessage = "listed or not at all."\n')
    REQUIRE = ('[[code_rules]]\nid = "pending"\npattern = \'HasPendingModelChanges\\(\\)\'\n'
               'files = ["tests/**/*.cs"]\nrequire = 1\nrequire_when = ["src/**/Migrations/*.cs"]\n'
               'message = "a migrations test."\n')

    def config(self, extra: str = "") -> None:
        self.repo.write("devtools/factory/factory.toml", self.BAN + extra)

    def run_full(self) -> tuple[int, str]:
        return run_main(code_rules.main, [], root=self.repo.root)

    def run_staged(self) -> tuple[int, str]:
        return run_main(code_rules.main, ["--staged"], root=self.repo.root)

    def test_a_plain_ban_is_refused_and_a_comment_is_not(self):
        self.config()
        self.repo.write("src/A.cs", "// .AllowAnonymous() in a comment\nmap.AllowAnonymous();\n")
        code, out = self.run_full()
        self.assertEqual(1, code)
        self.assertIn("src/A.cs:2: anon", out)
        self.assertNotIn("src/A.cs:1", out)

    def test_a_default_rule_the_project_switches_off_is_not_checked(self):
        self.repo.write("devtools/factory/book-defaults.toml", self.BAN)
        self.repo.write("devtools/factory/factory.toml", '[[code_rules]]\nid = "anon"\nenabled = false\n')
        self.repo.write("src/A.cs", "map.AllowAnonymous();\n")
        code, out = self.run_full()
        self.assertEqual(0, code, out)
        self.repo.write("devtools/factory/factory.toml", '[[code_rules]]\nid = "anon"\nenabled = true\n')
        code, out = self.run_full()
        self.assertEqual(1, code, out)
        self.assertIn("src/A.cs:1: anon", out)

    def test_an_allow_listed_file_passes_at_its_count(self):
        self.config('allowed = [{ file = "src/A.cs", count = 2, reason = "sign-in" }]\n')
        self.repo.write("src/A.cs", "a.AllowAnonymous();\nb.AllowAnonymous();\n")
        code, out = self.run_full()
        self.assertEqual(0, code, out)

    def test_a_new_site_in_an_allow_listed_file_fails(self):
        self.config('allowed = [{ file = "src/A.cs", count = 1, reason = "sign-in" }]\n')
        self.repo.write("src/A.cs", "a.AllowAnonymous();\nb.AllowAnonymous();\n")
        code, out = self.run_full()
        self.assertEqual(1, code)
        self.assertIn("2 match(es), the allow-list says 1 (sign-in): a new site", out)

    def test_a_site_in_a_file_not_listed_fails(self):
        self.config('allowed = [{ file = "src/A.cs", count = 1, reason = "sign-in" }]\n')
        self.repo.write("src/A.cs", "a.AllowAnonymous();\n")
        self.repo.write("src/B.cs", "b.AllowAnonymous();\n")
        code, out = self.run_full()
        self.assertEqual(1, code)
        self.assertIn("src/B.cs:1: anon", out)
        self.assertNotIn("src/A.cs", out)

    def test_fewer_sites_than_listed_is_a_stale_count(self):
        self.config('allowed = [{ file = "src/A.cs", count = 3, reason = "sign-in" }]\n')
        self.repo.write("src/A.cs", "a.AllowAnonymous();\n")
        code, out = self.run_full()
        self.assertEqual(1, code)
        self.assertIn("fewer than listed", out)

    def test_an_entry_for_a_file_that_is_gone_is_stale(self):
        self.config('allowed = [{ file = "src/Gone.cs", count = 1, reason = "was sign-in" }]\n')
        self.repo.write("src/A.cs", "nothing\n")
        code, out = self.run_full()
        self.assertEqual(1, code)
        self.assertIn("src/Gone.cs: anon — on the allow-list, but not a tracked file", out)

    def test_an_entry_without_a_reason_could_not_be_checked(self):
        self.config('allowed = [{ file = "src/A.cs", count = 1 }]\n')
        self.repo.write("src/A.cs", "a.AllowAnonymous();\n")
        code, out = self.run_full()
        self.assertEqual(1, code)
        self.assertIn("could not check", out)
        self.assertIn("gives no reason", out)

    def test_an_entry_with_a_count_of_zero_could_not_be_checked(self):
        self.config('allowed = [{ file = "src/A.cs", count = 0, reason = "r" }]\n')
        code, out = self.run_full()
        self.assertEqual(1, code)
        self.assertIn("a count of 1 or more", out)

    def test_staged_a_match_added_within_the_count_passes_and_beyond_it_fails(self):
        self.config('allowed = [{ file = "src/A.cs", count = 2, reason = "sign-in" }]\n')
        self.repo.write("src/A.cs", "a.AllowAnonymous();\n")
        self.repo.commit()
        self.repo.write("src/A.cs", "a.AllowAnonymous();\nb.AllowAnonymous();\n")
        self.assertEqual(0, self.run_staged()[0])
        self.repo.write("src/A.cs", "a.AllowAnonymous();\nb.AllowAnonymous();\nc.AllowAnonymous();\n")
        code, out = self.run_staged()
        self.assertEqual(1, code)
        self.assertIn("3 match(es), the allow-list says 2", out)

    def test_staged_a_change_without_a_match_never_counts_the_file(self):
        self.config('allowed = [{ file = "src/A.cs", count = 5, reason = "sign-in" }]\n')
        self.repo.write("src/A.cs", "a.AllowAnonymous();\n")
        self.repo.commit()
        self.repo.write("src/A.cs", "a.AllowAnonymous();\nunrelated\n")
        self.assertEqual(0, self.run_staged()[0], "a stale count is the full run's to report")

    def test_a_required_pattern_missing_fails_once_it_is_due(self):
        self.repo.write("devtools/factory/factory.toml", self.REQUIRE)
        self.repo.write("tests/T.cs", "class T {}\n")
        self.assertEqual(0, self.run_full()[0], "no migration yet: not due")
        self.repo.write("src/P/Migrations/1_Init.cs", "class Init {}\n")
        code, out = self.run_full()
        self.assertEqual(1, code)
        self.assertIn("pending — 0 match(es) where at least 1 must be", out)
        self.repo.write("tests/T.cs", "Assert.False(db.Database.HasPendingModelChanges());\n")
        self.assertEqual(0, self.run_full()[0])

    def test_a_required_pattern_is_not_judged_on_a_commit(self):
        self.repo.write("devtools/factory/factory.toml", self.REQUIRE)
        self.repo.write("src/P/Migrations/1_Init.cs", "class Init {}\n")
        self.assertEqual(0, self.run_staged()[0])

    def test_require_and_allowed_together_could_not_be_checked(self):
        self.repo.write("devtools/factory/factory.toml", self.REQUIRE +
                        'allowed = [{ file = "tests/T.cs", count = 1, reason = "r" }]\n')
        code, out = self.run_full()
        self.assertEqual(1, code)
        self.assertIn("not both", out)

    def test_the_skeleton_ddl_rule_refuses_schema_and_leaves_temp_tables_and_data(self):
        rule = shipped_rules(HERE.parent / "factory" / "book-defaults.toml")["data-script-ddl"]
        pattern = re.compile(rule["pattern"])
        for ddl in ("CREATE TABLE widgets (id int);", "alter table widgets add column x int;",
                    "CREATE UNIQUE INDEX ix ON t (a);", "DROP VIEW v;", "CREATE OR REPLACE FUNCTION f()",
                    "CREATE UNLOGGED TABLE staging (id int);"):
            self.assertTrue(pattern.search(ddl), ddl)
        for data in ("CREATE TEMP TABLE map (id bigint) ON COMMIT DROP;", "CREATE TEMPORARY TABLE m (id int);",
                     "(88953, 2232, 'Alter do Chão'),", "INSERT INTO t VALUES (1);"):
            self.assertFalse(pattern.search(data), data)
        self.assertEqual(["database-scripts/**/*.sql"], rule["files"])

    def test_a_ddl_line_in_a_seed_fails_the_full_run(self):
        skeleton = (HERE.parent / "factory" / "book-defaults.toml").read_text(encoding="utf-8")
        block = re.search(r'\[\[code_rules\]\]\nid = "data-script-ddl"\n.*?\nmessage = [^\n]*\n', skeleton, re.S)
        self.repo.write("devtools/factory/factory.toml", block.group(0))
        self.repo.write("database-scripts/seed/a.sql", "-- CREATE TABLE in a comment\nCREATE TEMP TABLE m (i int);\n")
        self.assertEqual(0, self.run_full()[0])
        self.repo.write("database-scripts/seed/a.sql", "CREATE TABLE widgets (id int);\n")
        code, out = self.run_full()
        self.assertEqual(1, code)
        self.assertIn("database-scripts/seed/a.sql:1: data-script-ddl", out)

    def test_the_dotnet_pack_rules_load_and_match_what_they_mean(self):
        rules = shipped_rules(TEMPLATE / "packs" / "dotnet" / "devtools" / "factory" / "book-defaults" / "dotnet.toml")
        self.assertEqual({"anonymous-route", "handler-side-effect", "config-by-literal", "pending-model-test"},
                         set(rules))
        for entry in rules.values():
            code_rules.compile_rule(entry, "[[code_rules]]", "")
        literal = re.compile(rules["config-by-literal"]["pattern"])
        for line in ('var k = configuration["Jwt:Key"];', 'var k = _configuration["Jwt:Key"];',
                     'var k = _config["Jwt:Key"];', 'var k = builder.Configuration["Jwt:Key"];'):
            with self.subTest(line):
                self.assertTrue(literal.search(line))
        self.assertFalse(literal.search('var k = _reconfig["x"];'))
        self.assertTrue(literal.search('config.GetValue<int>("Port")'))
        self.assertFalse(literal.search("configuration.GetSection(JwtOptions.SectionName)"))
        handler = re.compile(rules["handler-side-effect"]["pattern"])
        self.assertTrue(handler.search("public sealed class Sent(IEmailSender sender)"))
        self.assertFalse(handler.search("public sealed class Sent(IOutbox outbox)"))
        self.assertEqual(1, rules["pending-model-test"]["require"])


class PrintsUtf8(unittest.TestCase):
    """Book D45: a script that prints an em dash failed on a Windows console's code page (cp437)."""

    def scripts(self) -> list[Path]:
        folders = [HERE, *sorted((HERE.parents[1] / "devtools/factory/book-template/packs").glob("*/devtools/checks"))]
        return [path for folder in folders for path in sorted(folder.glob("*.py"))
                if not path.name.startswith("test_") and "print(" in path.read_text(encoding="utf-8")]

    def test_every_script_that_prints_writes_utf8(self):
        lacking = [path.name for path in self.scripts()
                   if 'stdout.reconfigure(encoding="utf-8"' not in path.read_text(encoding="utf-8")
                   and "stream.reconfigure(encoding=\"utf-8\")" not in path.read_text(encoding="utf-8")]
        self.assertEqual(lacking, [])

    def test_an_em_dash_prints_under_a_legacy_code_page(self):
        # check-book.py prints its problems; under cp437 (a Windows console) an em dash in one failed before the guard.
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "speak.py"
            body = (HERE / "check-book.py").read_text(encoding="utf-8").split('if __name__ == "__main__":', 1)[1]
            guard = body.split("raise SystemExit", 1)[0].split("sys.exit", 1)[0]
            script.write_text("import sys\nif True:" + guard + "print('—')\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(script)], capture_output=True,
                                    env=dict(os.environ, PYTHONIOENCODING="cp437"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode("utf-8").strip(), "—")


if __name__ == "__main__":
    unittest.main()
