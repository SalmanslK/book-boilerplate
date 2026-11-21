"""
Self-tests for drive.py, against a fake rclone, curl and Drive — no network, no token, no rclone config.
CI runs them with the tools'.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import drive  # noqa: E402

SECRET = "ya29." + "7" * 16
REMOTE = "test-remote"


def done(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def dump(remote: str = REMOTE, token: str = SECRET) -> str:
    return json.dumps({remote: {"type": "drive", "scope": "drive.readonly",
                                "token": json.dumps({"access_token": token, "token_type": "Bearer"})}})


class AccessTokenTests(unittest.TestCase):
    def token(self, *answers: subprocess.CompletedProcess) -> tuple[str, mock.Mock]:
        with mock.patch.object(drive.subprocess, "run", side_effect=list(answers)) as run:
            return drive.access_token(f"rclone:{REMOTE}"), run

    def failure(self, *answers: subprocess.CompletedProcess) -> str:
        with mock.patch.object(drive.subprocess, "run", side_effect=list(answers)):
            with self.assertRaises(drive.DriveError) as raised:
                drive.access_token(f"rclone:{REMOTE}")
        return str(raised.exception)

    def test_the_access_token_is_read_from_the_remotes_entry(self):
        token, run = self.token(done(), done(stdout=dump()))
        self.assertEqual(token, SECRET)
        self.assertEqual(run.call_args_list[0].args[0], ["rclone", "about", f"{REMOTE}:"], "refreshed first")
        self.assertEqual(run.call_args_list[1].args[0], ["rclone", "config", "dump"])

    def test_a_missing_remote_says_so(self):
        message = self.failure(done(), done(stdout=dump(remote="another-remote")))
        self.assertIn(REMOTE, message)
        self.assertNotIn(SECRET, message)

    def test_a_dump_that_is_not_json_is_a_drive_error(self):
        message = self.failure(done(), done(stdout="{not json"))
        self.assertIn("rclone config dump", message)

    def test_a_failed_refresh_is_named_not_left_to_surface_as_a_401(self):
        message = self.failure(done(1, stderr="Failed to about: couldn't fetch token: invalid_grant\nmore\n"),
                               done(stdout=dump()))
        self.assertIn("rclone about", message)
        self.assertIn("invalid_grant", message)

    def test_a_failed_config_dump_says_why_and_never_prints_the_config(self):
        message = self.failure(done(), done(3, stdout=dump(), stderr="Failed to load config file: permission denied"))
        self.assertIn("rclone config dump", message)
        self.assertIn("permission denied", message)
        self.assertNotIn(SECRET, message)
        self.assertNotIn("drive.readonly", message)

    def test_a_home_folder_in_what_rclone_said_never_reaches_the_message(self):
        home = os.path.expanduser("~")
        windows_home = "C:" + "\\" + "Users" + "\\" + "some.person"
        for folder in (home, windows_home, "/home/someperson", "/Users/someperson", "/c/Users/someperson"):
            with self.subTest(kind=folder.split(os.sep)[0][:3] if folder == home else folder[:3]):
                said = f'Failed to load config file: Config file "{folder}{os.sep}.config{os.sep}rclone.conf" not found'
                message = self.failure(done(1, stderr=said + "\n"), done(stdout=dump()))
                self.assertNotIn(folder, message)
                self.assertNotIn("some.person", message)
                self.assertNotIn("someperson", message)
                self.assertIn('Config file "~', message)

    def test_a_token_command_that_fails_is_named(self):
        with mock.patch.object(drive.subprocess, "run", return_value=done(2, stderr="not signed in")):
            with self.assertRaises(drive.DriveError) as raised:
                drive.access_token("gcloud auth print-access-token")
        self.assertIn("not signed in", str(raised.exception))

    def test_a_windows_token_command_keeps_its_backslashes(self):
        backslash = "\\"
        tool = backslash.join(["C:", "tools", "gcloud.cmd"])
        spaced = backslash.join(["C:", "Program Files", "gcloud", "gcloud.cmd"])
        for command, first in ((f"{tool} auth print-access-token", tool),
                               (f'"{spaced}" auth print-access-token', spaced)):
            with self.subTest(command):
                self.assertEqual(drive.command_words(command, windows=True), [first, "auth", "print-access-token"])
        self.assertEqual(drive.command_words("'/opt/my tools/gcloud' auth", windows=False),
                         ["/opt/my tools/gcloud", "auth"])

    def test_a_windows_token_command_takes_off_quotes_inside_a_word(self):
        self.assertEqual(drive.command_words('gcloud auth print-access-token --format="value(token)"', windows=True),
                         ["gcloud", "auth", "print-access-token", "--format=value(token)"])
        self.assertEqual(drive.command_words('a "b c"d ""', windows=True), ["a", "b cd", ""])
        with self.assertRaises(drive.DriveError):
            drive.command_words('gcloud "auth', windows=True)

    def test_a_single_quote_is_part_of_a_windows_word(self):
        backslash = "\\"
        tool = backslash.join(["C:", "Users", "O'Brien", "gcloud.cmd"])
        self.assertEqual(drive.command_words(f"{tool} auth", windows=True), [tool, "auth"])

    def test_the_token_command_is_split_for_this_os(self):
        with mock.patch.object(drive.subprocess, "run", return_value=done(stdout="tok\n")) as run, \
                mock.patch.object(drive, "command_words", wraps=drive.command_words) as split:
            self.assertEqual(drive.access_token("gcloud auth print-access-token"), "tok")
        split.assert_called_once_with("gcloud auth print-access-token")
        self.assertEqual(run.call_args.args[0], ["gcloud", "auth", "print-access-token"])


class TransportTests(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.object(drive.bitbucket.time, "sleep"),
                      mock.patch.object(drive.bitbucket, "curl_binaries", return_value=["curl-a", "curl-b"])):
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_token_never_reaches_a_command_line_an_error_or_a_repr(self):
        with mock.patch.object(drive.subprocess, "run", return_value=done(stdout='{"a": 1}\n200')) as run:
            self.assertEqual(drive.fetch(f"{drive.API}/files/x", SECRET), (200, '{"a": 1}'))
        argv = run.call_args.args[0]
        self.assertFalse([arg for arg in argv if SECRET in arg], "only on stdin")
        self.assertEqual(argv[argv.index("-K") + 1], "-")
        self.assertIn(SECRET, run.call_args.kwargs["input"])
        for failing in (lambda url, token: (401, "{}"), lambda url, token: (200, "not json")):
            with mock.patch.object(drive, "fetch", side_effect=failing):
                with self.assertRaises(drive.DriveError) as raised:
                    drive.get_json("files/x", SECRET)
            self.assertNotIn(SECRET, str(raised.exception))
            self.assertNotIn(SECRET, repr(raised.exception))
        with mock.patch.object(drive.subprocess, "run", return_value=done(60)):
            with self.assertRaises(drive.DriveError) as raised:
                drive.fetch(f"{drive.API}/files/x", SECRET)
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertNotIn(SECRET, repr(raised.exception))

    def test_a_permanent_failure_on_one_curl_moves_on_to_the_next(self):
        # Git's OpenSSL curl distrusts the office proxy's certificate; Windows' schannel curl gets through.
        with mock.patch.object(drive.subprocess, "run", side_effect=[done(60), done(stdout="{}\n200")]) as run:
            self.assertEqual(drive.fetch(f"{drive.API}/files/x", SECRET), (200, "{}"))
        self.assertEqual([c.args[0][0] for c in run.call_args_list], ["curl-a", "curl-b"])

    def test_every_curl_failing_for_good_is_one_error_naming_each(self):
        with mock.patch.object(drive.subprocess, "run", return_value=done(60)) as run:
            with self.assertRaises(drive.DriveError) as raised:
                drive.fetch(f"{drive.API}/files/x", SECRET)
        self.assertEqual(run.call_count, 2, "each curl once: a bad certificate does not get better by asking again")
        message = str(raised.exception)
        self.assertIn("curl-a: curl exited 60", message)
        self.assertIn("curl-b: curl exited 60", message)
        self.assertIn("certificate", message)
        self.assertNotIn(SECRET, message)

    def test_a_transient_curl_failure_is_retried_on_the_next_curl(self):
        answers = [done(7), done(stdout="{}\n200")]
        with mock.patch.object(drive.subprocess, "run", side_effect=answers) as run:
            self.assertEqual(drive.fetch(f"{drive.API}/files/x", SECRET), (200, "{}"))
        self.assertEqual([c.args[0][0] for c in run.call_args_list], ["curl-a", "curl-b"])

    def test_curl_never_answering_is_a_drive_error_with_the_true_count(self):
        with mock.patch.object(drive.subprocess, "run", return_value=done(28)) as run:
            with self.assertRaises(drive.DriveError) as raised:
                drive.fetch(f"{drive.API}/files/x", SECRET)
        self.assertEqual(run.call_count, drive.ATTEMPTS * 2)
        self.assertIn("could not be reached", str(raised.exception))
        self.assertIn(f"{drive.ATTEMPTS * 2} attempts", str(raised.exception))


class AnswerTests(unittest.TestCase):
    def test_a_refusal_is_an_error_never_an_empty_result(self):
        # An empty answer would read as "no threads" — a check passed that was never made.
        for status in (401, 403, 404, 500):
            with self.subTest(status), mock.patch.object(drive, "fetch", return_value=(status, '{"comments": []}')):
                with self.assertRaisesRegex(drive.DriveError, str(status)):
                    drive.comment_threads("doc", "t")

    def test_an_answer_that_is_not_json_is_an_error(self):
        with mock.patch.object(drive, "fetch", return_value=(200, "<html>sign in</html>")):
            with self.assertRaisesRegex(drive.DriveError, "not JSON"):
                drive.comment_threads("doc", "t")

    def test_threads_follow_every_page(self):
        asked = []

        def fetch(url, token):
            asked.append(url)
            if "pageToken=p2" in url:
                return 200, json.dumps({"comments": [{"id": "b"}]})
            return 200, json.dumps({"comments": [{"id": "a"}], "nextPageToken": "p2"})
        with mock.patch.object(drive, "fetch", side_effect=fetch):
            self.assertEqual([t["id"] for t in drive.comment_threads("doc", "t")], ["a", "b"])
        self.assertEqual(len(asked), 2)
        self.assertNotIn("pageToken", asked[0])

    def test_the_modified_time_is_read(self):
        with mock.patch.object(drive, "fetch", return_value=(200, '{"modifiedTime": "2026-10-01T05:00:00Z"}')):
            self.assertEqual(drive.modified_time("doc", "t"), "2026-10-01T05:00:00Z")


if __name__ == "__main__":
    unittest.main()
