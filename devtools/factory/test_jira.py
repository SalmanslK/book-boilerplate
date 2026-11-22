"""
Self-tests for jira.py and `book jira`, against a fake Jira — no network, no credential.
CI runs them with the tools'.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import factory  # noqa: E402
import jira  # noqa: E402

import fixture_project  # noqa: E402 — a full project's factory.toml
CONFIG = factory.Config(fixture_project.PROJECT)
SITE, _ = factory.jira_settings(CONFIG)


def doc(*content: dict) -> dict:
    return {"type": "doc", "version": 1, "content": list(content)}


def para(*content: dict) -> dict:
    return {"type": "paragraph", "content": list(content)}


def text(value: str, href: str = "") -> dict:
    node = {"type": "text", "text": value}
    if href:
        node["marks"] = [{"type": "link", "attrs": {"href": href}}]
    return node


def someone(name: str) -> dict:
    return {"displayName": name, "emailAddress": f"{name.split()[0].lower()}@example.com", "accountId": "5e9e-secret-id"}


def issue(kind: str = "Bug") -> dict:
    return {
        "key": "PROJ-376",
        "names": {"customfield_10050": "Steps to Reproduce", "customfield_10060": "Story Points"},
        "fields": {
            "summary": "Add Event's required fields arrive late",
            "issuetype": {"name": kind}, "status": {"name": "In Progress"}, "priority": {"name": "High"},
            "assignee": someone("Alex Morgan"), "reporter": someone("Qa Tester"),
            "updated": "2026-10-03T10:00:00.000+0500", "created": "2026-10-01T09:00:00.000+0500",
            "labels": ["release-1"],
            "description": doc(para(text("The form shows "), text("Save", ""), text(" too early.")),
                               {"type": "orderedList", "content": [
                                   {"type": "listItem", "content": [para(text("Open Add Event"))]},
                                   {"type": "listItem", "content": [para(text("Click Save"))]}]},
                               {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "x"}}]}),
            "customfield_10050": doc(para(text("See "), text("the guide", "https://example.org/guide"))),
            "customfield_10060": 3,
            "issuelinks": [{"type": {"outward": "blocks", "inward": "is blocked by"},
                            "outwardIssue": {"key": "PROJ-377", "fields": {"summary": "Next", "status": {"name": "To Do"}}}}],
            "attachment": [{"filename": "screen.png", "mimeType": "image/png", "created": "2026-10-01T09:05:00.000+0500"}],
            "comment": {"comments": [{"author": someone("Ahmad Ibtehaj"), "created": "2026-10-02T11:00:00.000+0500",
                                      "body": doc(para({"type": "mention", "attrs": {"text": "@Alex Morgan"}},
                                                       text(" please check")))}]},
        },
    }


class ReadOnlyTests(unittest.TestCase):
    def test_the_module_can_only_read(self):
        # No write can be added by accident: no POST, PUT or DELETE, no request body, anywhere in it.
        source = (HERE / "jira.py").read_text(encoding="utf-8")
        for verb in ("POST", "PUT", "DELETE", "PATCH", "--data", "-X", "data="):
            self.assertNotIn(verb, source)
        names = {node.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef)}
        self.assertFalse({n for n in names if n.startswith(("send", "post", "create", "update", "comment"))})


TENANT = (200, json.dumps({"cloudId": "c1"}))
GATEWAY = "https://api.atlassian.com/ex/jira/c1/rest/api/3/issue/PROJ-376?"


class ReadIssueTests(unittest.TestCase):
    def read(self, status: int, body: str = "{}") -> dict:
        def get(url: str, basic: str) -> tuple[int, str]:
            if url == f"https://{SITE}/_edge/tenant_info":
                return TENANT
            self.assertTrue(url.startswith(GATEWAY), url)  # never the site: it ignores a scoped token
            return status, body
        return jira.read_issue(SITE, "PROJ-376", "c2VjcmV0", get)

    def test_a_scoped_token_reads_through_the_gateway(self):
        self.assertEqual(self.read(200, json.dumps(issue()))["key"], "PROJ-376")

    def test_a_refusal_says_a_scoped_token_is_needed(self):
        with self.assertRaisesRegex(jira.JiraError, "scoped token"):
            self.read(401)

    def test_a_missing_issue_is_named(self):
        with self.assertRaisesRegex(jira.JiraError, "no PROJ-376"):
            self.read(404)

    def test_no_cloud_id_stops_before_the_credential_is_sent(self):
        sent = []
        with self.assertRaisesRegex(jira.JiraError, "cloud id"):
            jira.read_issue(SITE, "PROJ-376", "c2VjcmV0", lambda url, basic: (sent.append(basic), (500, ""))[1])
        self.assertEqual(sent, [""])

    def test_something_not_a_key_is_never_sent(self):
        with self.assertRaises(jira.JiraError):
            jira.read_issue(SITE, "PROJ-1/../../x", "c2VjcmV0", lambda url, basic: self.fail("sent"))

    def test_the_credential_never_appears_in_a_url(self):
        seen = []
        jira.read_issue(SITE, "PROJ-376", "c2VjcmV0",
                        lambda url, basic: (seen.append(url), TENANT if "tenant_info" in url else (200, "{}"))[1])
        self.assertFalse([url for url in seen if "c2VjcmV0" in url])


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.out = jira.render(issue(), SITE)

    def test_it_reads_like_the_issue_page(self):
        for expected in ("# PROJ-376 · Bug · Add Event's required fields arrive late", "**Status:** In Progress",
                         "**Assignee:** Alex Morgan", "1. Open Add Event", "2. Click Save",
                         "## Steps to Reproduce", "[the guide](https://example.org/guide)",
                         "blocks PROJ-377 — Next (To Do)", "screen.png", "@Alex Morgan please check",
                         "[an attached image or file]", f"https://{SITE}/browse/PROJ-376"):
            self.assertIn(expected, self.out)

    def test_people_appear_by_name_only(self):
        self.assertNotIn("@example.com", self.out)
        self.assertNotIn("5e9e-secret-id", self.out)

    def test_only_useful_custom_fields_are_shown(self):
        self.assertNotIn("Story Points", self.out)

    def test_the_summary_is_key_type_status_and_time_only(self):
        self.assertEqual(jira.summary(issue()), {"key": "PROJ-376", "type": "Bug", "status": "In Progress",
                                                 "updated": "2026-10-03T10:00:00.000+0500"})


class CredentialTests(unittest.TestCase):
    def test_none_stored_says_how_to_store_one(self):
        empty = mock.Mock(returncode=0, stdout="")
        with mock.patch.object(jira.subprocess, "run", return_value=empty):
            with self.assertRaisesRegex(jira.JiraError, "README"):
                jira.credential(HERE, SITE)

    def test_a_stored_one_becomes_a_basic_header(self):
        stored = mock.Mock(returncode=0, stdout="protocol=https\nhost=x\nusername=me@example.com\npassword=tok\n")
        with mock.patch.object(jira.subprocess, "run", return_value=stored) as run:
            self.assertEqual(jira.credential(HERE, SITE), "bWVAZXhhbXBsZS5jb206dG9r")
        self.assertIn(f"host={SITE}", run.call_args.kwargs["input"])
        self.assertEqual(run.call_args.kwargs["env"]["GCM_INTERACTIVE"], "never")

    def test_a_failing_credential_lookup_is_reported_not_swallowed(self):
        failed = subprocess.CompletedProcess([], 128, "", "fatal: credential helper crashed")
        with mock.patch.object(jira.subprocess, "run", return_value=failed) as run:
            with self.assertRaises(jira.JiraError) as raised:
                jira.credential(HERE, SITE)
        self.assertEqual(run.call_args.args[0], ["git", "credential", "fill"])
        self.assertIn("git credential fill", str(raised.exception))
        self.assertIn("128", str(raised.exception))

    def test_a_lookup_that_never_answers_is_reported(self):
        with mock.patch.object(jira.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 30)):
            with self.assertRaisesRegex(jira.JiraError, "timed out"):
                jira.credential(HERE, SITE)


SECRET_PASSWORD = "ATATT" + "3" * 16


class TransportTests(unittest.TestCase):
    """The real path — git's credential store, then curl — with subprocess faked."""

    def setUp(self):
        for patch in (mock.patch.object(jira.bitbucket.time, "sleep"),
                      mock.patch.object(jira.bitbucket, "curl_binaries", return_value=["curl-a", "curl-b"])):
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_token_goes_on_curls_stdin_never_its_command_line(self):
        stored = subprocess.CompletedProcess([], 0, f"username=me@example.com\npassword={SECRET_PASSWORD}\n", "")
        tenant = subprocess.CompletedProcess([], 0, '{"cloudId": "c1"}\n200', "")
        answer = subprocess.CompletedProcess([], 0, json.dumps(issue()) + "\n200", "")
        with mock.patch.object(jira.subprocess, "run", side_effect=[stored, tenant, answer]) as run:
            basic = jira.credential(HERE, SITE)
            self.assertEqual(jira.read_issue(SITE, "PROJ-376", basic)["key"], "PROJ-376")
        fill, _, read = run.call_args_list
        self.assertEqual(fill.args[0], ["git", "credential", "fill"])
        for call in run.call_args_list[1:]:
            argv = call.args[0]
            self.assertEqual(argv[argv.index("-K") + 1], "-", "the config, header included, comes from stdin")
            self.assertFalse([arg for arg in argv if basic in arg or SECRET_PASSWORD in arg])
        self.assertIn(f"Authorization: Basic {basic}", read.kwargs["input"])
        self.assertTrue(read.args[0][-1].startswith(GATEWAY))

    def test_a_permanent_failure_on_one_curl_moves_on_to_the_next(self):
        # Git's OpenSSL curl distrusts the office proxy's certificate; Windows' schannel curl gets through.
        bad_certificate = subprocess.CompletedProcess([], 60, "", "")
        answer = subprocess.CompletedProcess([], 0, '{"cloudId": "c1"}\n200', "")
        with mock.patch.object(jira.subprocess, "run", side_effect=[bad_certificate, answer]) as run:
            self.assertEqual(jira.fetch(GATEWAY, "c2VjcmV0"), (200, '{"cloudId": "c1"}'))
        self.assertEqual([c.args[0][0] for c in run.call_args_list], ["curl-a", "curl-b"])

    def test_every_curl_failing_for_good_is_one_error_naming_each(self):
        bad_certificate = subprocess.CompletedProcess([], 60, "", "")
        with mock.patch.object(jira.subprocess, "run", return_value=bad_certificate) as run:
            with self.assertRaises(jira.JiraError) as raised:
                jira.fetch(GATEWAY, "c2VjcmV0")
        self.assertEqual(run.call_count, 2, "each curl once, never again")
        message = str(raised.exception)
        self.assertIn("curl-a: curl exited 60 (the server's certificate is not trusted)", message)
        self.assertIn("curl-b: curl exited 60", message)
        self.assertNotIn("c2VjcmV0", message)

    def test_a_permanent_failure_is_named_with_its_meaning(self):
        malformed = subprocess.CompletedProcess([], 3, "", "")
        with mock.patch.object(jira.subprocess, "run", return_value=malformed):
            with self.assertRaises(jira.JiraError) as raised:
                jira.fetch(GATEWAY, "c2VjcmV0")
        self.assertIn("curl exited 3", str(raised.exception))
        self.assertIn("URL", str(raised.exception))
        self.assertNotIn("c2VjcmV0", str(raised.exception))

    def test_a_transient_failure_is_retried_and_counted(self):
        dropped = subprocess.CompletedProcess([], 56, "", "")
        with mock.patch.object(jira.subprocess, "run", return_value=dropped) as run:
            with self.assertRaises(jira.JiraError) as raised:
                jira.fetch(GATEWAY, "c2VjcmV0")
        self.assertEqual(run.call_count, jira.ATTEMPTS * 2)
        self.assertIn(f"could not be reached: no answer after {jira.ATTEMPTS * 2} attempts", str(raised.exception))


class CommandTests(unittest.TestCase):
    """`book jira <key>`: this project's issues only, read through the stored credential."""

    def jira(self, key: str) -> str:
        return factory.command_jira(HERE.parents[1], CONFIG, mock.Mock(key=key))

    def test_another_projects_key_is_refused(self):
        with self.assertRaises(factory.FactoryError):
            self.jira("OTHER-1")

    def test_an_issue_is_read_and_rendered(self):
        issue = {"key": "PROJ-376", "fields": {"summary": "A bug"}}
        with mock.patch.object(jira, "credential", return_value="basic") as credential, \
                mock.patch.object(jira, "read_issue", return_value=issue) as read, \
                mock.patch.object(jira, "render", return_value="PROJ-376 rendered") as render:
            self.assertEqual(self.jira("proj-376"), "PROJ-376 rendered")
        self.assertEqual(read.call_args.args[:2], (SITE, "PROJ-376"))
        self.assertEqual(credential.call_args.args[1], SITE)
        render.assert_called_once()

    def test_a_jira_error_is_said_plainly(self):
        with mock.patch.object(jira, "credential", return_value="basic"), \
                mock.patch.object(jira, "read_issue", side_effect=jira.JiraError("401")):
            with self.assertRaises(factory.FactoryError) as raised:
                self.jira("PROJ-1")
        self.assertIn("Could not read PROJ-1", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
