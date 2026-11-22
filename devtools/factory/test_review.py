"""
Self-tests for the review path in factory.py: `book fingerprint`, `book reviewed --fingerprint`
and `book review-scope`, and the split review checklist they serve. CI and ci-local.sh run them with
the other factory tests:

    python3 devtools/checks/run-self-tests.py devtools/factory

Each safeguard the hand-typed fingerprint snippet and the one-file checklist carried has a test here,
named for it (the deploy split lost safeguards that had none). Throwaway repositories, no network.
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


class FingerprintTests(unittest.TestCase):
    """
    The old flow's safeguards, each with its test:

    S1  taken against the merge base with origin/dev ........ test_a_new_base_changes_it_and_reviewed_refuses
    S2  a failed fetch prints no hash ....................... test_a_failed_fetch_prints_no_hash
    S3  no merge base prints no hash ........................ test_no_merge_base_prints_no_hash
    S4  uncommitted edits to tracked files count ............ test_an_edit_changes_it
    S5  untracked files count ............................... test_an_untracked_file_is_included
    S6  ignored files do not ................................ test_an_ignored_file_is_not_included
    S7  the same content gives the same hash ................ test_same_content_same_hash
    S8  a verdict is recorded only on a clean tree .......... test_a_dirty_tree_refuses
    S9  the record is for one commit; pr open wants HEAD .... test_the_record_is_per_commit
    S10 the record lives inside .git ........................ test_committing_exactly_the_reviewed_work_records_ready
    S11 a git failure never reads as clean .................. test_a_failed_status_never_reads_as_clean
    New, for recording after /book:commit:
    N1  committing the reviewed work keeps the hash ......... test_committing_exactly_the_reviewed_work_records_ready
    N2  an edit after the review refuses .................... test_an_edit_after_the_review_refuses
    N3  an untracked file left out or added refuses ......... test_an_untracked_file_left_out_refuses,
                                                              test_an_untracked_file_added_after_the_review_refuses
    N4  a malformed fingerprint refuses ..................... test_a_malformed_fingerprint_refuses
    N5  staging is left as the person had it ................ test_the_persons_staging_is_untouched
    """

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        top = Path(self.folder.name)
        self.root, self.origin = top / "repo", top / "origin.git"
        (self.root / "devtools" / "factory").mkdir(parents=True)
        (self.root / factory.CONFIG_RELATIVE).write_text(fixture_project.PROJECT_TOML, encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "notes.md").write_text("# Notes\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("bin/\n", encoding="utf-8")
        git(top, "init", "-q", "--bare", "-b", "dev", str(self.origin))
        git(self.root, "init", "-q", "-b", "dev")
        for key, value in (("user.name", "Test Person"), ("user.email", "person@example.invalid"),
                           ("core.autocrlf", "false")):
            git(self.root, "config", key, value)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "Base")
        git(self.root, "remote", "add", "origin", str(self.origin))
        git(self.root, "push", "-q", "origin", "dev")
        git(self.root, "fetch", "-q", "origin")
        git(self.root, "checkout", "-q", "--no-track", "-b", "feature/x")
        self.write("docs/notes.md", "# Notes, on the branch\n")
        git(self.root, "commit", "-qam", "A committed change")
        env = {key: value for key, value in os.environ.items() if key not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        env["FACTORY_ROOT"] = str(self.root)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def factory(self, *argv: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = factory.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def fingerprint(self) -> str:
        code, out = self.factory("fingerprint")
        self.assertEqual(code, 0, out)
        self.assertRegex(out.strip(), r"^[0-9a-f]{40}([0-9a-f]{24})?$")
        return out.strip()

    def reviewed(self, fingerprint: str, verdict: str = "READY") -> tuple[int, str]:
        return self.factory("reviewed", verdict, "--fingerprint", fingerprint)

    def commit_all(self, message: str = "Commit the reviewed work") -> None:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)

    def uncommitted_work(self) -> None:
        """What /book:commit usually reviews: an edit to a tracked file and a new, untracked one."""
        self.write("docs/notes.md", "# Notes, edited before the commit\n")
        self.write("devtools/new_tool.py", "print('new')\n")

    # The hash itself

    def test_same_content_same_hash(self):
        self.uncommitted_work()
        self.assertEqual(self.fingerprint(), self.fingerprint())

    def test_an_edit_changes_it(self):
        before = self.fingerprint()
        self.write("docs/notes.md", "# Notes, edited\n")
        self.assertNotEqual(self.fingerprint(), before)

    def test_an_untracked_file_is_included(self):
        before = self.fingerprint()
        self.write("devtools/new_tool.py", "print('new')\n")
        with_file = self.fingerprint()
        self.assertNotEqual(with_file, before, "an untracked file's content is part of what was reviewed")
        (self.root / "devtools" / "new_tool.py").rename(self.root / "devtools" / "renamed_tool.py")
        self.assertNotEqual(self.fingerprint(), with_file,
                            "and so is its name — the snippet it replaces hashed contents only")

    def test_an_ignored_file_is_not_included(self):
        before = self.fingerprint()
        self.write("bin/output.dll", "built\n")
        self.assertEqual(self.fingerprint(), before)

    def test_the_persons_staging_is_untouched(self):
        self.uncommitted_work()
        git(self.root, "add", "docs/notes.md")
        staged = git(self.root, "diff", "--cached", "--name-only")
        status = git(self.root, "status", "--porcelain")
        self.fingerprint()
        self.assertEqual(git(self.root, "diff", "--cached", "--name-only"), staged)
        self.assertEqual(git(self.root, "status", "--porcelain"), status)

    def test_a_failed_fetch_prints_no_hash(self):
        git(self.root, "remote", "set-url", "origin", str(Path(self.folder.name) / "gone.git"))
        code, out = self.factory("fingerprint")
        self.assertEqual(code, 1)
        self.assertIn("Could not fetch origin", out)
        self.assertIsNone(re.search(r"\b[0-9a-f]{40}\b", out), "no hash at all, never a stale one")

    def test_no_merge_base_prints_no_hash(self):
        git(self.root, "checkout", "-q", "--orphan", "unrelated")
        git(self.root, "commit", "-q", "-m", "No history in common with dev")
        code, out = self.factory("fingerprint")
        self.assertEqual(code, 1)
        self.assertIn("No merge base between HEAD and origin/dev", out)

    # Recording the verdict after /book:commit

    def test_committing_exactly_the_reviewed_work_records_ready(self):
        self.uncommitted_work()
        reviewed = self.fingerprint()
        self.commit_all()
        self.assertEqual(self.fingerprint(), reviewed,
                         "committing the reviewed work, new file included, leaves the fingerprint unchanged")
        code, out = self.reviewed(reviewed)
        self.assertEqual(code, 0, out)
        self.assertEqual(factory.recorded_review(self.root), ("READY", git(self.root, "rev-parse", "HEAD")))
        self.assertEqual(git(self.root, "status", "--porcelain"), "", "the verdict lives inside .git")

    def test_committing_in_several_groups_still_matches(self):
        self.uncommitted_work()
        reviewed = self.fingerprint()
        git(self.root, "add", "devtools/new_tool.py")
        git(self.root, "commit", "-q", "-m", "Tooling first")
        self.commit_all("Docs second")
        self.assertEqual(self.reviewed(reviewed)[0], 0)

    def test_an_edit_after_the_review_refuses(self):
        self.uncommitted_work()
        reviewed = self.fingerprint()
        self.write("docs/notes.md", "# Notes, changed after the review\n")
        self.commit_all()
        code, out = self.reviewed(reviewed)
        self.assertEqual(code, 1)
        self.assertIn("not what was reviewed", out)
        self.assertEqual(factory.recorded_review(self.root), ("", ""))

    def test_an_edit_committed_after_the_recorded_commit_is_not_covered(self):
        self.uncommitted_work()
        reviewed = self.fingerprint()
        self.commit_all()
        self.write("docs/notes.md", "# Notes, a later commit\n")
        self.commit_all("A later change")
        code, out = self.reviewed(reviewed)
        self.assertEqual(code, 1, "the review's fingerprint names the earlier content, not this commit's")
        self.assertIn("not what was reviewed", out)

    def test_an_untracked_file_left_out_refuses(self):
        self.uncommitted_work()
        reviewed = self.fingerprint()
        git(self.root, "add", "docs/notes.md")
        git(self.root, "commit", "-q", "-m", "Only the tracked edit")
        (self.root / "devtools" / "new_tool.py").unlink()
        code, out = self.reviewed(reviewed)
        self.assertEqual(code, 1, "the reviewed new file is not in the commit")
        self.assertIn("not what was reviewed", out)

    def test_an_untracked_file_added_after_the_review_refuses(self):
        self.uncommitted_work()
        reviewed = self.fingerprint()
        self.write("devtools/unreviewed.py", "print('never reviewed')\n")
        self.commit_all()
        code, out = self.reviewed(reviewed)
        self.assertEqual(code, 1)
        self.assertIn("not what was reviewed", out)

    def test_a_dirty_tree_refuses(self):
        self.uncommitted_work()
        reviewed = self.fingerprint()
        git(self.root, "add", "docs/notes.md")
        git(self.root, "commit", "-q", "-m", "Part of it")
        code, out = self.reviewed(reviewed)
        self.assertEqual(code, 1, "the new file is still uncommitted")
        self.assertIn("uncommitted", out)
        self.assertEqual(factory.recorded_review(self.root), ("", ""))

    def test_a_new_base_changes_it_and_reviewed_refuses(self):
        reviewed = self.fingerprint()
        git(self.root, "checkout", "-q", "dev")
        self.write("docs/other.md", "# Landed on dev meanwhile\n")
        self.commit_all("Someone else's change")
        git(self.root, "push", "-q", "origin", "dev")
        git(self.root, "checkout", "-q", "feature/x")
        git(self.root, "merge", "-q", "--no-edit", "dev")
        self.assertNotEqual(self.fingerprint(), reviewed)
        self.assertEqual(self.reviewed(reviewed)[0], 1)

    def test_a_malformed_fingerprint_refuses(self):
        for given in ("", "abc123", "Fingerprint: " + "a" * 40, "g" * 40):
            with self.subTest(given):
                code, out = self.reviewed(given)
                self.assertEqual(code, 1)
                self.assertIn("--fingerprint takes the hash", out)

    def test_the_record_is_per_commit(self):
        self.uncommitted_work()
        reviewed = self.fingerprint()
        self.commit_all()
        self.assertEqual(self.reviewed(reviewed)[0], 0)
        recorded_for = git(self.root, "rev-parse", "HEAD")
        self.write("docs/notes.md", "# Notes, next commit\n")
        self.commit_all("Next commit")
        verdict, commit = factory.recorded_review(self.root)
        self.assertEqual((verdict, commit), ("READY", recorded_for))
        self.assertNotEqual(commit, git(self.root, "rev-parse", "HEAD"))
        git(self.root, "push", "-q", "origin", "feature/x")
        body = Path(self.folder.name) / "body.md"
        body.write_text("## Summary\nx\n\n## Test plan\ny\n\n**Effort:** 1 hour, 1 review round\n", encoding="utf-8")
        with mock.patch.object(factory, "bitbucket_client") as client:
            code, out = self.factory("pr", "open", "--title", "x", "--body-file", str(body))
        self.assertEqual(code, 1)
        self.assertIn("another commit", out, "pr open still wants READY for the exact head commit")
        client.assert_not_called()

    def test_a_not_ready_verdict_is_recorded_and_stops_pr_open(self):
        self.uncommitted_work()
        reviewed = self.fingerprint()
        self.commit_all()
        self.assertEqual(self.reviewed(reviewed, "NOT-READY")[0], 0)
        self.assertEqual(factory.recorded_review(self.root)[0], "NOT-READY")

    def test_a_failed_status_never_reads_as_clean(self):
        real = factory.git

        def failing(root, *args):
            return (128, "") if args[:1] == ("status",) else real(root, *args)

        with mock.patch.object(factory, "git", side_effect=failing):
            code, out = self.factory("reviewed", "READY")
        self.assertEqual(code, 1)
        self.assertIn("git status", out)
        self.assertEqual(factory.recorded_review(self.root), ("", ""))


if __name__ == "__main__":
    unittest.main()
