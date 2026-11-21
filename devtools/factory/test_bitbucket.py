"""
Self-tests for bitbucket.py, against a fake Bitbucket — no network, no token.
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

import bitbucket  # noqa: E402
import factory  # noqa: E402

import fixture_project  # noqa: E402 — a full project's factory.toml
CONFIG = factory.Config(fixture_project.PROJECT)
HEAD = "7357b87f" + "0" * 32


def user(name: str, account: str = "") -> dict:
    return {"display_name": name, "account_id": account or name.lower()}


ARRIVED = "2026-09-30T05:00:00+00:00"  # when Bitbucket saw the commit; approvals below come after it


def activity(commit: str = HEAD[:12], date: str = ARRIVED) -> list[dict]:
    return [{"update": {"date": date, "source": {"commit": {"hash": commit}}}},
            {"approval": {"date": "2026-09-30T08:00:00+00:00"}}]


def pull_request(approved: list[str], author: str = "Alex Morgan", branch: str = "chore/x",
                 commit: str = HEAD[:12], approved_on: str = "2026-09-30T08:00:00+00:00", description: str = "## Summary\nx\n\n## Test plan\ny\n\n**Effort:** 2 hours, 1 review round") -> dict:
    return {
        "id": 7, "title": "A change", "description": description,
        "author": user(author),
        "source": {"branch": {"name": branch}, "commit": {"hash": commit}},
        "participants": [{"user": user(name), "approved": True, "participated_on": approved_on} for name in approved],
        "links": {"html": {"href": "https://bitbucket.org/example-workspace/app/pull-requests/7"}},
    }


class ProtectionTests(unittest.TestCase):
    WANTED = {"branches": ["dev", "main"], "kinds": ["delete", "force"],
              "minimums": {"require_approvals_to_merge": 1}}

    def rule(self, branch: str, kind: str, value=None, users=()) -> dict:
        return {"pattern": branch, "kind": kind, "value": value, "users": [user(u) for u in users], "groups": []}

    def test_everything_live_has_no_gaps(self):
        rules = [self.rule(b, k) for b in ("dev", "main") for k in ("delete", "force")] + \
                [self.rule(b, "require_approvals_to_merge", 1) for b in ("dev", "main")]
        self.assertEqual(bitbucket.protection_gaps(rules, self.WANTED), ({}, []))

    def test_a_missing_rule_and_a_low_minimum_are_named(self):
        rules = [self.rule("dev", "delete"), self.rule("dev", "require_approvals_to_merge", 0),
                 self.rule("main", "delete"), self.rule("main", "force")]
        missing, _ = bitbucket.protection_gaps(rules, self.WANTED)
        self.assertEqual(missing["dev"], ["force", "require_approvals_to_merge >= 1 (is 0)"])
        self.assertEqual(missing["main"], ["require_approvals_to_merge >= 1"])

    def test_an_exemption_is_reported_not_failed(self):
        rules = [self.rule(b, k) for b in ("dev", "main") for k in ("delete", "force")] + \
                [self.rule(b, "require_approvals_to_merge", 1) for b in ("dev", "main")] + \
                [self.rule("dev", "push", users=["Alex Morgan"])]
        missing, exemptions = bitbucket.protection_gaps(rules, self.WANTED)
        self.assertEqual(missing, {})
        self.assertEqual(exemptions, ["dev: push allows Alex Morgan"])

    def test_every_rule_of_a_kind_counts_not_the_last_one_read(self):
        # Two push rules on dev: the first one's exemption, and the weaker of two minimums, are what matter.
        rules = [self.rule(b, k) for b in ("dev", "main") for k in ("delete", "force")] +                 [self.rule("dev", "push", users=["Alex Morgan"]), self.rule("dev", "push"),
                 self.rule("dev", "require_approvals_to_merge", 0), self.rule("dev", "require_approvals_to_merge", 2),
                 self.rule("main", "require_approvals_to_merge", 1)]
        missing, exemptions = bitbucket.protection_gaps(rules, self.WANTED)
        self.assertEqual(missing, {"dev": ["require_approvals_to_merge >= 1 (is 0)"]})
        self.assertEqual(exemptions, ["dev: push allows Alex Morgan"])


class DescriptionTests(unittest.TestCase):
    def test_a_summary_and_a_test_plan_pass(self):
        self.assertEqual(bitbucket.description_problems(pull_request([])), [])

    def test_bold_headings_count(self):
        pr = pull_request([], description="**Summary**\nx\n**Test plan**\ny\nEffort: half a day, 2 review rounds")
        self.assertEqual(bitbucket.description_problems(pr), [])

    def test_a_missing_test_plan_is_named(self):
        self.assertEqual(bitbucket.description_problems(pull_request([], description="## Summary\nx\n- Effort: 1 hour")),
                         ["no 'test plan' section"])

    def test_a_missing_or_empty_effort_line_is_named(self):
        for text in ("## Summary\nx\n\n## Test plan\ny", "## Summary\nx\n\n## Test plan\ny\n**Effort:**",
                     "## Summary\nx\n\n## Test plan\nmeasure the effort later"):
            with self.subTest(text):
                problems = bitbucket.description_problems(pull_request([], description=text))
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("'Effort' line", problems[0])

    def test_the_effort_line_forms_count(self):
        for line in ("**Effort:** 3 hours, 1 review round", "Effort: 2 days", "- **Effort**: 1 hour",
                     "## Effort\n4 hours"):
            with self.subTest(line):
                pr = pull_request([], description=f"## Summary\nx\n\n## Test plan\ny\n\n{line}")
                self.assertEqual(bitbucket.description_problems(pr), [])

    def test_changed_ai_files_need_an_ai_files_line(self):
        self.assertEqual(bitbucket.description_problems(pull_request([]), ai_files=[]), [], "none changed: no line needed")
        missing = bitbucket.description_problems(pull_request([]), ai_files=["CLAUDE.md"])
        self.assertEqual(len(missing), 1, missing)
        self.assertIn("no 'AI files' line", missing[0])
        for line in ("- AI files: 1 changed (CLAUDE.md)", "**AI files:** CLAUDE.md", "**AI files**: CLAUDE.md"):
            with self.subTest(line):
                pr = pull_request([], description=f"## Summary\nx\n\n## Test plan\ny\n\n{line}\n\nEffort: 1 hour")
                self.assertEqual(bitbucket.description_problems(pr, ai_files=["CLAUDE.md"]), [])
        for text in ("## Summary\nthe AI files line\n\n## Test plan\ny\n\nEffort: 1 hour",
                     "## Summary\nAI files are fine\n\n## Test plan\ny\n\nEffort: 1 hour"):
            with self.subTest(text):
                pr = pull_request([], description=text)
                self.assertEqual(len(bitbucket.description_problems(pr, ai_files=["CLAUDE.md"])), 1,
                                 "prose is not the line")

    def test_while_ci_is_off_a_local_ci_pass_for_the_head_commit_is_required(self):
        self.assertEqual(bitbucket.description_problems(pull_request([]), local_ci_required=False), [])
        missing = bitbucket.description_problems(pull_request([]), local_ci_required=True)
        self.assertEqual(len(missing), 1)
        self.assertIn("no 'Local CI' section", missing[0])
        passed = pull_request([], description=f"## Summary\nx\n\n## Test plan\ny\n\n## Local CI\n\nPASS on `{HEAD[:12]}`\n\nEffort: 1 hour\n")
        self.assertEqual(bitbucket.description_problems(passed, local_ci_required=True), [])

    def test_a_tiered_local_ci_pass_line_counts(self):
        # The line ci-local.sh prints since docs/plans/local-ci-tiers.md: the tier and its reason follow the sha.
        for line in (f"PASS on `{HEAD[:12]}` (2026-10-14T09:00:00Z, 9 min, tier modules - Companies, Business Units, "
                     "`sh devtools/checks/ci-local.sh`)",
                     f"PASS on `{HEAD[:12]}` (2026-10-14T09:00:00Z, 1 min, tier full - carried from 0123abcd4567: "
                     "only checks-tier files changed since, `sh devtools/checks/ci-local.sh`)"):
            with self.subTest(line=line):
                described = pull_request([], description=f"## Summary\nx\n\n## Test plan\ny\n\nEffort: 1 hour\n\n## Local CI\n\n{line}\n")
                self.assertEqual(bitbucket.description_problems(described, local_ci_required=True), [])

    def test_a_local_ci_pass_for_an_earlier_commit_does_not_count(self):
        stale = pull_request([], description="## Summary\nx\n\n## Test plan\ny\n\nEffort: 1 hour\n\n## Local CI\n\nPASS on `0123abcd4567`\n")
        found = bitbucket.description_problems(stale, local_ci_required=True)
        self.assertIn(f"not a PASS for the head commit {HEAD[:12]}", found[0])

    def test_only_the_line_ci_local_prints_counts_as_a_pass(self):
        for body in (f"Checks passed over uncommitted changes - NOTHING RECORDED. PASS {HEAD[:12]}",
                     f"I ran it and it was a PASS for {HEAD[:12]}",
                     f"PASS on `{HEAD[:7]}`"):
            pr = pull_request([], description=f"## Summary\nx\n\n## Test plan\ny\n\nEffort: 1 hour\n\n## Local CI\n\n{body}\n")
            self.assertIn("not a PASS", bitbucket.description_problems(pr, local_ci_required=True)[0], body)

    def test_the_head_commit_named_under_another_heading_does_not_count(self):
        elsewhere = pull_request([], description=f"## Summary\nx\n\n## Local CI\n\nnot run\n\n## Test plan\nPASS on {HEAD[:12]}\n\nEffort: 1 hour\n")
        self.assertIn("not a PASS", bitbucket.description_problems(elsewhere, local_ci_required=True)[0])

    def test_a_jira_branch_needs_the_dev_testing_section(self):
        pr = pull_request([], branch="fix/PROJ-347-company-name-no")
        self.assertEqual(bitbucket.description_problems(pr, jira_project="PROJ"),
                         ["a Jira-tracked PR needs a 'Dev testing & notes for FE/QA' section"])
        with_section = pull_request([], branch="fix/PROJ-347-company-name-no",
                                    description="## Summary\nx\n\n## Test plan\ny\n\n## Dev testing & notes for FE/QA\n"
                                                "z\n\nEffort: 1 hour")
        self.assertEqual(bitbucket.description_problems(with_section, jira_project="PROJ"), [])

    def test_a_real_key_in_the_branch_or_the_title_makes_a_pr_jira_tracked(self):
        for branch, title in (("fix/PROJ-310-short-slug", "A change"), ("feature/PROJ-12", "A change"),
                              ("chore/x", "PROJ-310 Fix the company name"), ("chore/x", "Fix it (PROJ-9)")):
            with self.subTest(branch=branch, title=title):
                self.assertTrue(bitbucket.jira_tracked(branch, title, "PROJ"))

    def test_text_shaped_like_a_key_is_not_a_jira_key(self):
        for branch, title in (("chore/x", "Read files as UTF-8"), ("chore/x", "Capture FSD V14-2 changes"),
                              ("fix/D12-save-the-draft", "A change"), ("chore/docs-utf-8", "ISO-8601 dates"),
                              ("fix/UTF-8-reading", "A change"), ("fix/slug-PROJ-310", "A change"),
                              ("fix/PROJ-310x-slug", "A change"), ("chore/x", "XPROJ-3 or PROJ-3-2")):
            with self.subTest(branch=branch, title=title):
                self.assertFalse(bitbucket.jira_tracked(branch, title, "PROJ"))
                pr = pull_request([], branch=branch)
                pr["title"] = title
                self.assertEqual(bitbucket.description_problems(pr, jira_project="PROJ"), [])

    def test_without_a_jira_project_no_pr_is_jira_tracked(self):
        self.assertFalse(bitbucket.jira_tracked("fix/PROJ-310-slug", "PROJ-310", ""))


class TransportTests(unittest.TestCase):
    def test_the_token_never_reaches_a_command_line_an_error_or_a_repr(self):
        secret = "tok-" + "9" * 12
        client = bitbucket.Bitbucket("example-workspace/app", secret, fetch=lambda url, token: (500, "{}"))
        self.assertNotIn(secret, repr(client))
        with self.assertRaises(bitbucket.BitbucketError) as raised:
            client.pull_request(1)
        self.assertNotIn(secret, str(raised.exception))
        done = mock.Mock(returncode=0, stdout="{}\n200")
        with mock.patch.object(bitbucket, "curl_binaries", return_value=["curl"]), \
             mock.patch.object(bitbucket.subprocess, "run", return_value=done) as run:
            bitbucket.fetch_with_curl("https://api.bitbucket.org/2.0/x", secret)
        self.assertFalse([arg for arg in run.call_args.args[0] if secret in arg], "only on stdin")
        self.assertIn(secret, run.call_args.kwargs["input"])

    def test_a_404_says_what_it_likely_means(self):
        client = bitbucket.Bitbucket("example-workspace/app", "t", fetch=lambda url, token: (404, "{}"))
        with self.assertRaises(bitbucket.BitbucketError) as raised:
            client.pull_request(1)
        self.assertIn("cannot see this repository", str(raised.exception))

    def test_pages_are_followed(self):
        pages = {
            "https://api.bitbucket.org/2.0/repositories/r/branch-restrictions?pagelen=100":
                '{"values": [1], "next": "https://api.bitbucket.org/2.0/p2"}',
            "https://api.bitbucket.org/2.0/p2": '{"values": [2]}',
        }
        client = bitbucket.Bitbucket("r", "t", fetch=lambda url, token: (200, pages[url]))
        self.assertEqual(client.branch_restrictions(), [1, 2])

    def test_the_repository_is_read_from_any_remote_shape(self):
        for url in ("https://bitbucket.org/example-workspace/app.git", "https://user@bitbucket.org/example-workspace/app.git",
                    "git@bitbucket.org:example-workspace/app.git", "http://bitbucket.org/example-workspace/app"):
            with self.subTest(url):
                self.assertEqual(bitbucket.REMOTE.search(url).group("repo"), "example-workspace/app")

    def test_a_pull_request_is_opened_with_its_title_branches_and_body(self):
        sent = {}

        def send(url, token, body):
            sent.update(url=url, body=json.loads(body))
            return 201, '{"id": 9, "links": {"html": {"href": "u"}}}'
        client = bitbucket.Bitbucket("example-workspace/app", "t", send=send)
        self.assertEqual(client.create_pull_request("D1: x", "## Summary", "fix/x", "dev")["id"], 9)
        self.assertTrue(sent["url"].endswith("/repositories/example-workspace/app/pullrequests"))
        self.assertEqual((sent["body"]["source"]["branch"]["name"], sent["body"]["destination"]["branch"]["name"]),
                         ("fix/x", "dev"))

    def test_a_description_is_replaced_with_a_put_of_the_title_and_description_only(self):
        sent = {}

        def put(url, token, body):
            sent.update(url=url, body=json.loads(body))
            return 200, '{"id": 7, "title": "x"}'

        def send(url, token, body):
            raise AssertionError("an update is a PUT, never a POST")
        client = bitbucket.Bitbucket("example-workspace/app", "t", send=send, put=put)
        self.assertEqual(client.update_description(7, "A title", "## Summary")["id"], 7)
        self.assertTrue(sent["url"].endswith("/repositories/example-workspace/app/pullrequests/7"))
        self.assertEqual(sent["body"], {"title": "A title", "description": "## Summary"})

    def test_a_refused_description_update_says_why(self):
        client = bitbucket.Bitbucket("r", "t", put=lambda url, token, body: (403, '{"error": "not allowed"}'))
        with self.assertRaises(bitbucket.BitbucketError) as raised:
            client.update_description(7, "t", "d")
        self.assertIn("not allowed", str(raised.exception))

    def test_a_put_goes_out_as_a_put(self):
        sent = subprocess.CompletedProcess([], 0, '{"id": 1}\n200', "")
        with mock.patch.object(bitbucket, "curl_binaries", return_value=["curl-a"]), \
                mock.patch.object(bitbucket.subprocess, "run", return_value=sent) as run:
            self.assertEqual(bitbucket.put_with_curl("https://api/x", "secret-token", "{}"), (200, '{"id": 1}'))
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-X") + 1], "PUT")
        self.assertNotIn("secret-token", " ".join(command), "the token goes on stdin, never the command line")

    def test_a_refused_pull_request_says_why(self):
        client = bitbucket.Bitbucket("r", "t", send=lambda url, token, body: (400, '{"error": "no such branch"}'))
        with self.assertRaises(bitbucket.BitbucketError) as raised:
            client.create_pull_request("t", "d", "fix/x", "dev")
        self.assertIn("no such branch", str(raised.exception))

    def test_a_comment_is_posted_inline_on_the_new_files_line(self):
        sent = {}

        def send(url, token, body):
            sent.update(url=url, body=json.loads(body))
            return 201, '{"id": 501}'
        client = bitbucket.Bitbucket("example-workspace/app", "t", send=send)
        self.assertEqual(client.post_comment(7, "**🟠 HIGH — x**", "src/A.cs", 12)["id"], 501)
        self.assertTrue(sent["url"].endswith("/repositories/example-workspace/app/pullrequests/7/comments"))
        self.assertEqual(sent["body"], {"content": {"raw": "**🟠 HIGH — x**"}, "inline": {"path": "src/A.cs", "to": 12}})

    def test_a_comment_is_resolved_through_its_own_endpoint(self):
        sent = {}

        def send(url, token, body):
            sent.update(url=url)
            return 200, '{"type": "comment_resolution"}'
        client = bitbucket.Bitbucket("example-workspace/app", "t", send=send)
        client.resolve_comment(7, 501)
        self.assertTrue(sent["url"].endswith("/repositories/example-workspace/app/pullrequests/7/comments/501/resolve"))

    def test_a_refused_resolve_says_why(self):
        client = bitbucket.Bitbucket("r", "t", send=lambda url, token, body: (409, '{"error": "already resolved"}'))
        with self.assertRaises(bitbucket.BitbucketError) as raised:
            client.resolve_comment(7, 501)
        self.assertIn("already resolved", str(raised.exception))

    def test_a_post_that_may_have_landed_is_never_repeated(self):
        # A retry after a timeout or a dropped answer could open a second pull request.
        timed_out = subprocess.CompletedProcess([], 28, "", "")
        with mock.patch.object(bitbucket, "curl_binaries", return_value=["curl-a", "curl-b"]), \
                mock.patch.object(bitbucket.subprocess, "run", return_value=timed_out) as run:
            with self.assertRaises(bitbucket.BitbucketError) as raised:
                bitbucket.send_with_curl("https://api/x", "t", "{}")
        self.assertEqual(run.call_count, 1)
        self.assertIn("may have been created", str(raised.exception))

    def test_a_post_that_never_connected_tries_the_next_curl(self):
        never = subprocess.CompletedProcess([], 7, "", "")
        sent = subprocess.CompletedProcess([], 0, '{"id": 1}\n201', "")
        with mock.patch.object(bitbucket, "curl_binaries", return_value=["curl-a", "curl-b"]), \
                mock.patch.object(bitbucket.subprocess, "run", side_effect=[never, sent]) as run:
            self.assertEqual(bitbucket.send_with_curl("https://api/x", "t", "{}"), (201, '{"id": 1}'))
        self.assertEqual(run.call_count, 2)

    def test_a_post_refused_at_the_tls_handshake_tries_the_next_curl(self):
        # Exit 60: the server's certificate was not trusted, so no request was sent.
        untrusted = subprocess.CompletedProcess([], 60, "", "")
        sent = subprocess.CompletedProcess([], 0, '{"id": 1}\n201', "")
        with mock.patch.object(bitbucket, "curl_binaries", return_value=["curl-a", "curl-b"]), \
                mock.patch.object(bitbucket.subprocess, "run", side_effect=[untrusted, sent]) as run:
            self.assertEqual(bitbucket.send_with_curl("https://api/x", "t", "{}"), (201, '{"id": 1}'))
        self.assertEqual([c.args[0][0] for c in run.call_args_list], ["curl-a", "curl-b"])

    def test_a_post_no_curl_could_send_says_nothing_was_sent(self):
        untrusted = subprocess.CompletedProcess([], 60, "", "")
        with mock.patch.object(bitbucket, "curl_binaries", return_value=["curl-a", "curl-b"]), \
                mock.patch.object(bitbucket.subprocess, "run", return_value=untrusted) as run:
            with self.assertRaises(bitbucket.BitbucketError) as raised:
                bitbucket.send_with_curl("https://api/x", "t", "{}")
        self.assertEqual(run.call_count, 2)
        self.assertNotIn("may have been created", str(raised.exception))
        self.assertIn("nothing was sent", str(raised.exception))

    def test_the_token_never_follows_a_link_to_another_host(self):
        fetched = []

        def fetch(url, token):
            fetched.append(url)
            return 200, '{"values": [1], "next": "https://attacker.example/2.0/p2"}'
        client = bitbucket.Bitbucket("r", "t", fetch=fetch)
        for url in ("https://attacker.example/2.0/user", "https://api.bitbucket.org.attacker.example/2.0/x",
                    "https://api.bitbucket.org@attacker.example/2.0/x", "http://api.bitbucket.org/2.0/x"):
            with self.subTest(url):
                with self.assertRaises(bitbucket.BitbucketError):
                    client.get(url)
        self.assertEqual(fetched, [], "nothing sent")
        with self.assertRaises(bitbucket.BitbucketError) as raised:
            client.branch_restrictions()
        self.assertEqual(len(fetched), 1, "the first page only; the foreign next link is never asked")
        self.assertIn("attacker.example", str(raised.exception))

    def test_a_read_that_never_answers_says_how_many_times_it_was_asked(self):
        dropped = subprocess.CompletedProcess([], 7, "", "")
        with mock.patch.object(bitbucket, "curl_binaries", return_value=["curl-a", "curl-b"]), \
                mock.patch.object(bitbucket.time, "sleep"), \
                mock.patch.object(bitbucket.subprocess, "run", return_value=dropped) as run:
            with self.assertRaises(bitbucket.BitbucketError) as raised:
                bitbucket.fetch_with_curl("https://api/x", "t")
        self.assertEqual(run.call_count, bitbucket.ATTEMPTS * 2)
        self.assertIn(f"after {bitbucket.ATTEMPTS * 2} attempts", str(raised.exception))

    def test_a_permanent_curl_failure_is_not_retried_on_that_curl_and_says_what_it_means(self):
        bad_certificate = subprocess.CompletedProcess([], 60, "", "")
        with mock.patch.object(bitbucket, "curl_binaries", return_value=["curl-a", "curl-b"]), \
                mock.patch.object(bitbucket.time, "sleep"), \
                mock.patch.object(bitbucket.subprocess, "run", return_value=bad_certificate) as run:
            with self.assertRaises(bitbucket.BitbucketError) as raised:
                bitbucket.fetch_with_curl("https://api/x", "t")
        self.assertEqual(run.call_count, 2, "each curl once")
        self.assertIn("curl-a: curl exited 60 (", str(raised.exception))
        self.assertIn("curl-b: curl exited 60 (", str(raised.exception))

    def test_a_permanent_failure_on_one_curl_moves_on_to_the_next(self):
        bad_certificate = subprocess.CompletedProcess([], 60, "", "")
        answered = subprocess.CompletedProcess([], 0, "{}\n200", "")
        with mock.patch.object(bitbucket, "curl_binaries", return_value=["curl-a", "curl-b"]), \
                mock.patch.object(bitbucket.time, "sleep"), \
                mock.patch.object(bitbucket.subprocess, "run", side_effect=[bad_certificate, answered]) as run:
            self.assertEqual(bitbucket.fetch_with_curl("https://api/x", "t"), (200, "{}"))
        self.assertEqual([c.args[0][0] for c in run.call_args_list], ["curl-a", "curl-b"])

    def test_a_refused_curl_drops_out_while_a_transient_one_keeps_its_attempts(self):
        def answer(argv, **_):
            return subprocess.CompletedProcess([], 60 if argv[0] == "curl-a" else 7, "", "")
        with mock.patch.object(bitbucket, "curl_binaries", return_value=["curl-a", "curl-b"]), \
                mock.patch.object(bitbucket.time, "sleep"), \
                mock.patch.object(bitbucket.subprocess, "run", side_effect=answer) as run:
            with self.assertRaises(bitbucket.BitbucketError) as raised:
                bitbucket.fetch_with_curl("https://api/x", "t")
        self.assertEqual([c.args[0][0] for c in run.call_args_list].count("curl-a"), 1)
        self.assertEqual(run.call_count, 1 + bitbucket.ATTEMPTS)
        self.assertIn("curl-a: curl exited 60", str(raised.exception))

    def test_a_home_folder_never_reaches_a_message(self):
        home = os.path.expanduser("~")
        windows_home = "C:" + "\\" + "Users" + "\\" + "some.person"
        for folder in (home, windows_home, windows_home.replace("\\", "/"), "/home/someperson", "/c/Users/someperson"):
            with self.subTest(kind=folder.split(os.sep)[0][:3] if folder == home else folder[:3]):
                scrubbed = bitbucket.without_home(f'Config file "{folder}{os.sep}rclone.conf" not found')
                self.assertNotIn(folder, scrubbed)
                self.assertTrue(scrubbed.startswith('Config file "~'), scrubbed)
        self.assertEqual(bitbucket.without_home("C:/Program Files/Git/mingw64/bin/curl.exe"),
                         "C:/Program Files/Git/mingw64/bin/curl.exe")

    def test_a_personal_token_ignores_the_ci_variable(self):
        # BITBUCKET_TOKEN is a repository token acting as a bot: a PR it opened would not be the person's.
        stored = subprocess.CompletedProcess([], 0, "protocol=https\nhost=bitbucket.org\npassword=mine\n", "")
        with mock.patch.dict("os.environ", {"BITBUCKET_TOKEN": "ci-service-token"}), \
                mock.patch.object(bitbucket.subprocess, "run", return_value=stored) as run:
            self.assertEqual(bitbucket.token(HERE, "example-workspace/app", personal=True), "mine")
            self.assertEqual(run.call_args.args[0], ["git", "credential", "fill"])
            run.reset_mock()
            self.assertEqual(bitbucket.token(HERE, "example-workspace/app"), "ci-service-token")
            run.assert_not_called()

    def test_pipelines_names_the_repository(self):
        with mock.patch.dict("os.environ", {"BITBUCKET_REPO_FULL_NAME": "example-workspace/app"}):
            self.assertEqual(bitbucket.repository(HERE), "example-workspace/app")


if __name__ == "__main__":
    unittest.main()
