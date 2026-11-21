"""
Self-tests for deploy.py, `factory deploy`. Nothing here reaches a server or git: every command goes
to a fake that answers from a script, so the tests prove what each step concludes from what it is told.

    python3 -m unittest discover -s devtools/factory -p "test_*.py" -v
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import deploy  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

import fixture_project  # noqa: E402

# A full project's factory.toml, [deploy] included: a stack-free Book's own has no deploy.
CONFIG = fixture_project.PROJECT

NOW = datetime.datetime.now(datetime.timezone.utc)
STAMP = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
OLD = (NOW - datetime.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
SHA_NEW = "a" * 40
SHA_OLD = "b" * 40


def probe_output(**changes: str) -> str:
    sections = {
        "env": "env ok",
        "head": SHA_OLD,
        "disk": "  58G\nImages 20GB reclaimable 4GB",
        "backups": "dev newest=dev-2026.dump age=fresh last-run=ok\nqa newest=qa-2026.dump age=fresh last-run=ok",
        "cron-log": "Kept 7 dumps",
        "nightly": f"dev PASS {SHA_NEW[:12]} {STAMP}",
        "mounts": "mail-drop 1654 755\nlogs 1654 755",
        "volumes": "uploads 1654 755",
    }
    sections.update(changes)
    return "".join(f"== {name}\n{body}\n" for name, body in sections.items() if body is not None) + "== end\n"


class Fake:
    """Answers each command from the first rule whose words all appear in it; records every call."""

    def __init__(self, root: Path, rules: list[tuple[tuple[str, ...], deploy.Done]]):
        self.rules = rules
        self.calls: list[tuple[list[str], str]] = []
        self.root = root

    def __call__(self, argv, *, cwd=None, stdin=None, timeout=0, env=None):
        self.calls.append((argv, stdin or ""))
        text = " ".join(argv) + "\n" + (stdin or "")
        if argv[:3] == ["git", "rev-parse", "--git-path"]:
            return deploy.Done(0, str(self.root / argv[3]))
        for words, answer in self.rules:
            if all(word in text for word in words):
                return answer
        return deploy.Done(0, "")

    def ran(self, *words: str) -> bool:
        return any(all(word in " ".join(argv) + "\n" + stdin for word in words) for argv, stdin in self.calls)


def args(**values) -> argparse.Namespace:
    base = {"env": "DEV", "branch": "dev", "pre_tests": "run", "post_tests": "run", "fix": False,
            "switch": False, "build_only": False, "to": "", "target": "all", "break_stale_lock": False}
    base.update(values)
    return argparse.Namespace(**base)


TRX = textwrap.dedent("""\
    <TestRun><Results>
    <UnitTestResult testName="Smoke.Health_is_live" outcome="Passed" />
    <UnitTestResult testName="Smoke.Login_works" outcome="NotExecuted"><Output><ErrorInfo><Message>No account to log in as. Set X</Message></ErrorInfo></Output></UnitTestResult>
    <UnitTestResult testName="Smoke.Create_round_trip" outcome="Failed" />
    </Results>
    <ResultSummary outcome="Failed"><Counters total="3" executed="2" passed="1" failed="1" notExecuted="0" /></ResultSummary>
    </TestRun>
    """)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="deploy-tests-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.saved = deploy.RUN, deploy.HTTP
        self.addCleanup(self.restore)
        deploy.HTTP = lambda url, timeout=10: 200
        # The ledger goes beside the private settings file: here, a folder inside this test's own temp root,
        # so no test ever writes to the person's real one.
        self.ledger = self.root / "private" / deploy.LEDGER_NAME
        patch = mock.patch.dict(os.environ, {"APP_LOCAL_SETTINGS": str(self.root / "private" / "local.toml")})
        patch.start()
        self.addCleanup(patch.stop)

    def restore(self):
        deploy.RUN, deploy.HTTP = self.saved

    def fake(self, *rules) -> Fake:
        fake = Fake(self.root, list(rules))
        deploy.RUN = fake
        return fake


class RunTests(unittest.TestCase):
    def test_a_script_reaches_the_shell_with_unix_line_endings(self):
        # The real runner, a real child process: it prints the bytes it was given.
        echo = "import sys; sys.stdout.write(repr(sys.stdin.buffer.read()))"
        done = deploy.run([sys.executable, "-c", echo], stdin="set +e\necho ok\n", timeout=60)
        self.assertEqual(done.code, 0, done.out)
        self.assertEqual(done.out, repr(b"set +e\necho ok\n"))

    def test_output_comes_back_as_text_without_carriage_returns(self):
        done = deploy.run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a\\r\\nb\\r\\n')"], timeout=60)
        self.assertEqual(done.out, "a\nb")


class TargetTests(unittest.TestCase):
    def test_dev_and_qa_in_any_case(self):
        self.assertEqual(deploy.environment("qa"), "QA")
        self.assertEqual(deploy.environment(" Dev "), "DEV")

    def test_anything_else_is_refused_word_for_word(self):
        for value in ["UAT", "prod", "PRODUCTION", "LIVE", "staging", "deploy-host", "", "DEV,QA"]:
            with self.subTest(value=value), self.assertRaises(deploy.DeployError) as caught:
                deploy.environment(value)
            self.assertEqual(str(caught.exception), deploy.REFUSED_TARGET.format(value=value))

    def test_the_host_comes_only_from_the_config(self):
        t = deploy.target(CONFIG, "QA", "dev")
        self.assertEqual(t.host, CONFIG["deploy"]["ssh_host"])
        self.assertEqual(t.directory, CONFIG["deploy"]["QA"]["directory"])

    def test_a_placeholder_stops_the_deploy_and_names_the_key(self):
        raw = {"deploy": dict(CONFIG["deploy"], ssh_host="<the server>")}
        with self.assertRaisesRegex(deploy.DeployError, "ssh_host"):
            deploy.target(raw, "DEV", "dev")

    def test_branch_names_that_could_reach_the_shell_are_refused(self):
        for branch in ["dev; rm -rf /", "$(id)", "../x", "a..b", "-x", "x/", "fix//y", ""]:
            with self.subTest(branch=branch), self.assertRaises(deploy.DeployError):
                deploy.target(CONFIG, "DEV", branch)
        for branch in ["dev", "fix/PROJ-310-short-slug", "feature/a.b_c"]:
            self.assertEqual(deploy.target(CONFIG, "DEV", branch).branch, branch)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.t = deploy.target(CONFIG, "DEV", "dev")

    def judge(self, out):
        return deploy.judge_probe(out, self.t, NOW)

    def levels(self, findings):
        return [level for level, _ in findings]

    def test_a_healthy_box_is_all_ok(self):
        findings, fixes, backups = self.judge(probe_output())
        self.assertEqual(set(self.levels(findings)), {"OK"})
        self.assertEqual(fixes, [])
        self.assertEqual(backups, {"dev": "fresh ok", "qa": "fresh ok"})

    def test_a_probe_that_stopped_early_blocks(self):
        findings, _, _ = self.judge(probe_output().replace("== end\n", ""))
        self.assertEqual(self.levels(findings), ["BLOCK"])

    def test_no_env_file_blocks(self):
        findings, _, _ = self.judge(probe_output(env="MISSING"))
        self.assertIn("BLOCK", self.levels(findings))

    def test_a_full_disk_blocks_and_never_prunes(self):
        findings, fixes, _ = self.judge(probe_output(disk="  6G"))
        block = [text for level, text in findings if level == "BLOCK"]
        self.assertTrue(block and "docker system prune -f" in block[0] and "their decision" in block[0])
        self.assertEqual(fixes, [])

    def test_a_stale_or_failed_backup_warns_with_the_log_and_never_blocks(self):
        findings, _, backups = self.judge(probe_output(
            backups="dev newest=dev.dump age=STALE last-run=FAILED\nqa newest=none age=NONE last-run=no-log"))
        self.assertNotIn("BLOCK", self.levels(findings))
        warnings = [text for level, text in findings if level == "WARN"]
        self.assertEqual(len(warnings), 3)
        self.assertIn("Kept 7 dumps", warnings[-1])
        self.assertEqual(backups, {"dev": "STALE FAILED", "qa": "NONE no-log"})

    def test_the_nightly_run(self):
        cases = [(f"dev PASS {SHA_NEW[:12]} {STAMP}", "OK"), (f"dev PASS {SHA_NEW[:12]} {OLD}", "WARN"),
                 (f"dev FAIL {SHA_NEW[:12]} {STAMP} integration", "WARN"), ("dev NONE", "WARN"),
                 (f"dev RUNNING dev {OLD}", "WARN"),
                 # ci-daily.sh marks a narrowed run; a PASS of part of the suite is not the nightly PASS.
                 (f"dev PASS {SHA_NEW[:12]} {STAMP} (filter: FullyQualifiedName~Widgets)", "WARN")]
        for line, level in cases:
            with self.subTest(line=line):
                self.assertEqual(self.levels(deploy.nightly_findings([line], NOW)), [level])
        self.assertIn("defects.md", deploy.nightly_findings([cases[2][0]], NOW)[0][1])

    def test_a_mount_root_owns_is_a_fix_it_names(self):
        findings, fixes, _ = self.judge(probe_output(mounts="mail-drop 0 755\nlogs MISSING"))
        self.assertEqual(self.levels(findings).count("FIX"), 2)
        self.assertEqual(len(fixes), 2)
        self.assertTrue(all("chown 1654:1654" in fix and self.t.directory in fix for fix in fixes))

    def test_volumes(self):
        cases = [("uploads MISSING: no such volume", "OK", 0),
                 ("uploads sudo: a password is required", "WARN", 0),
                 ("uploads 0 755", "FIX", 1)]
        for line, level, fixes in cases:
            with self.subTest(line=line):
                found, fix, _ = self.judge(probe_output(volumes=line))
                self.assertIn(level, self.levels(found))
                self.assertEqual(len(fix), fixes)

    def test_the_probe_script_keeps_awk_fields_and_quotes_values(self):
        script = deploy.probe_script(self.t)
        self.assertIn('$1 == "Backing" && $2 == "up"', script)
        self.assertIn("results-dev/latest", script)
        self.assertIn("docker volume inspect \"${P}_\"uploads", script)

    def test_the_settings_script_quotes_the_branch(self):
        t = deploy.target(CONFIG, "QA", "fix/a.b")
        script = deploy.settings_script(t)
        self.assertIn("git checkout fix/a.b\n", script)
        self.assertIn("check-required-settings.sh QA -f compose.yaml -f compose.qa.yaml", script)


class TrxTests(unittest.TestCase):
    def test_counts_skips_from_results_not_counters(self):
        path = Path(tempfile.mkdtemp()) / "smoke.trx"
        path.write_text(TRX, encoding="utf-8")
        found = deploy.trx_counts(path)
        self.assertEqual((found["executed"], found["passed"], found["failed"], found["skipped"]), (2, 1, 1, 1))
        self.assertEqual(found["failing"], ["Smoke.Create_round_trip"])
        self.assertEqual(found["outcome"], "Failed")
        self.assertTrue(found["messages"][0].startswith("No account to log in as"))
        shutil.rmtree(path.parent)

    def test_no_file_or_no_counters_is_none(self):
        folder = Path(tempfile.mkdtemp())
        self.assertIsNone(deploy.trx_counts(folder / "missing.trx"))
        (folder / "empty.trx").write_text("<TestRun/>", encoding="utf-8")
        self.assertIsNone(deploy.trx_counts(folder / "empty.trx"))
        shutil.rmtree(folder)


def smoke(executed=10, passed=10, failed=0, skipped=0, outcome="Completed", exit_code=0, known=None, failing=()):
    return {"post_tests": "run", "smoke": {"exit": exit_code, "known_skips": known or {},
            "counts": {"executed": executed, "passed": passed, "failed": failed, "skipped": skipped,
                       "outcome": outcome, "failing": list(failing), "cleanup": ["Cleanup Failure: logout"]}}}


class VerdictTests(unittest.TestCase):
    def test_rows_in_order(self):
        cases = [
            ({"post_tests": "skip"}, deploy.SKIPPED, "SKIPPED"),
            ({"post_tests": "run", "smoke": {"exit": 1, "counts": None}}, deploy.DID_NOT_RUN, "DID NOT RUN"),
            (smoke(executed=0, passed=0), deploy.DID_NOT_RUN, "Nothing executed"),
            (smoke(failed=1, failing=["T.a"], exit_code=1), deploy.FAILED, "T.a"),
            (smoke(outcome="Failed", exit_code=1), deploy.FAILED, "Cleanup Failure"),
            (smoke(exit_code=1), deploy.FAILED, "FAILED"),
            (smoke(skipped=3, known={"no credentials": 2}), deploy.FAILED, "1 checks skipped for an unrecognised"),
            (smoke(skipped=2, known={"no credentials": 2}), deploy.IN_PART, "IN PART"),
            (smoke(), deploy.PASSED, "Deployment complete"),
        ]
        for state, kind, words in cases:
            with self.subTest(kind=kind, words=words):
                line, found = deploy.smoke_verdict(state)
                self.assertEqual(found, kind)
                self.assertIn(words, line)


class FlowTests(Base):
    def ok_box(self, **probe):
        # The ssh rules come first: the probe itself runs `git rev-parse HEAD` on the server.
        return self.fake(
            (("ssh", "echo ok"), deploy.Done(0, "ok")),
            (("ssh", "== env"), deploy.Done(0, probe_output(**probe))),
            (("ssh", "check-required-settings"), deploy.Done(0, "nothing to check")),
            (("git", "status"), deploy.Done(0, "")),
            (("git", "rev-parse", "--abbrev-ref"), deploy.Done(0, "dev")),
            (("git", "rev-parse", "HEAD"), deploy.Done(0, SHA_NEW)),
            (("git", "rev-parse", "origin/dev"), deploy.Done(0, SHA_NEW)),
        )

    def test_check_passes_on_a_healthy_box_and_records_the_run(self):
        fake = self.ok_box()
        text, ok = deploy.command_check(self.root, CONFIG, args(env="qa"))
        self.assertTrue(ok, text)
        state = deploy.load_state(self.root)
        self.assertEqual((state["env"], state["commit"], state["pre_tests"]), ("QA", SHA_NEW, "run"))
        self.assertTrue(fake.ran("git", "checkout", "dev"))
        self.assertTrue(fake.ran("ssh", "-o", "BatchMode=yes"))
        self.assertIn("Pre-deploy:   run", text)
        self.assertIn("Post-deploy:  run", text)

    def test_the_target_block_shows_a_skipped_choice(self):
        self.ok_box()
        text, _ = deploy.command_check(self.root, CONFIG, args(env="dev", pre_tests="skip", post_tests="skip"))
        self.assertIn("Pre-deploy:   SKIPPED by request", text)
        self.assertIn("Post-deploy:  SKIPPED by request", text)

    def test_a_check_that_stops_leaves_nothing_of_an_earlier_run(self):
        self.ok_box()
        _, ok = deploy.command_check(self.root, CONFIG, args())
        self.assertTrue(ok)
        state = deploy.load_state(self.root)
        state["gate"] = {"ok": True, "commit": SHA_NEW}
        deploy.save_state(self.root, state)
        deploy.RUN.rules.insert(0, (("git", "status"), deploy.Done(0, " M x")))
        _, ok = deploy.command_check(self.root, CONFIG, args())
        self.assertFalse(ok)
        self.assertEqual(deploy.load_state(self.root), {})
        with self.assertRaisesRegex(deploy.DeployError, "check"):
            deploy.command_up(self.root, CONFIG, args())

    def test_qa_never_skips_the_gate(self):
        self.ok_box()
        with self.assertRaises(deploy.DeployError) as caught:
            deploy.command_check(self.root, CONFIG, args(env="QA", pre_tests="skip"))
        self.assertEqual(str(caught.exception), deploy.REFUSED_QA_SKIP)

    def test_an_uncommitted_tree_stops_before_anything_remote(self):
        fake = self.fake((("git", "status"), deploy.Done(0, " M src/x.cs")))
        _, ok = deploy.command_check(self.root, CONFIG, args())
        self.assertFalse(ok)
        self.assertFalse(fake.ran("ssh"))

    def test_local_and_origin_differ_and_it_says_which_way(self):
        for counts, words in [("0\t2", "2 local commit(s) not on origin"), ("3\t0", "3 commit(s) behind"),
                              ("1\t1", "diverged"), ("", "could not say")]:
            with self.subTest(counts=counts):
                self.fake((("git", "rev-parse", "--abbrev-ref"), deploy.Done(0, "dev")),
                          (("git", "rev-parse", "HEAD"), deploy.Done(0, SHA_NEW)),
                          (("git", "rev-parse", "origin/dev"), deploy.Done(0, SHA_OLD)),
                          (("git", "rev-list", "--left-right"), deploy.Done(0, counts)))
                text, ok = deploy.command_check(self.root, CONFIG, args())
                self.assertFalse(ok)
                self.assertIn("never push", text)
                self.assertIn(words, text)

    def test_a_state_file_that_is_there_but_unreadable_is_an_error_not_an_empty_record(self):
        # An empty record would let a later step run with nothing of what the server held before.
        self.ok_box()
        _, ok = deploy.command_check(self.root, CONFIG, args(env="qa"))
        self.assertTrue(ok)
        deploy.state_path(self.root).write_text("{ not json", encoding="utf-8")
        with self.assertRaisesRegex(deploy.DeployError, "unreadable"):
            deploy.load_state(self.root)

    def test_a_fix_is_applied_only_with_fix(self):
        fake = self.ok_box(mounts="mail-drop 0 755\nlogs 1654 755")
        _, ok = deploy.command_check(self.root, CONFIG, args())
        self.assertFalse(ok)
        self.assertFalse(fake.ran("ssh", "chown"))
        fake = self.ok_box(mounts="mail-drop 0 755\nlogs 1654 755")
        text, ok = deploy.command_check(self.root, CONFIG, args(fix=True))
        self.assertTrue(ok, text)
        self.assertTrue(fake.ran("ssh", "chown 1654:1654"))

    def test_settings_not_supplied_block_and_a_wrong_name_is_not_a_pass(self):
        for code, words in [(1, "a pull request"), (2, "nothing was checked")]:
            with self.subTest(code=code):
                self.ok_box()
                deploy.RUN.rules.insert(0, (("ssh", "check-required-settings"), deploy.Done(code, "Email__X")))
                text, ok = deploy.command_check(self.root, CONFIG, args())
                self.assertFalse(ok)
                self.assertIn(words, text)

    def test_another_branch_stops_until_the_person_agrees_to_switch(self):
        fake = self.ok_box()
        fake.rules.insert(0, (("git", "rev-parse", "--abbrev-ref"), deploy.Done(0, "feature/x")))
        text, ok = deploy.command_check(self.root, CONFIG, args())
        self.assertFalse(ok)
        self.assertIn("--switch", text)
        self.assertFalse(fake.ran("git", "checkout"))
        self.assertFalse(fake.ran("ssh"))
        text, ok = deploy.command_check(self.root, CONFIG, args(switch=True))
        self.assertTrue(ok, text)
        self.assertTrue(fake.ran("git", "checkout", "dev"))

    def test_check_records_the_commit_the_server_was_on_before_its_pull(self):
        self.ok_box()
        _, ok = deploy.command_check(self.root, CONFIG, args())
        self.assertTrue(ok)
        self.assertEqual(deploy.load_state(self.root)["server_before"], SHA_OLD)

    def test_an_unreadable_server_commit_blocks(self):
        self.ok_box(head="fatal: not a git repository")
        text, ok = deploy.command_check(self.root, CONFIG, args())
        self.assertFalse(ok)
        self.assertIn("could not read the commit the server is on", text)

    def test_changes_uses_the_recorded_commit_not_a_fresh_read(self):
        # After check's pull the server's checkout already holds the new commit; a fresh read would make
        # the revert target the commit being deployed.
        fake = self.fake((("ssh", "rev-parse"), deploy.Done(0, SHA_NEW)),
                         (("git", "merge-base"), deploy.Done(0, "")))
        self.state(server_before=SHA_OLD)
        text, ok = deploy.command_changes(self.root, CONFIG, args())
        self.assertTrue(ok, text)
        self.assertEqual(deploy.load_state(self.root)["changes"]["previous"], SHA_OLD)
        self.assertFalse(fake.ran("ssh"))
        self.state()
        with self.assertRaisesRegex(deploy.DeployError, "run check again"):
            deploy.command_changes(self.root, CONFIG, args())

    def test_a_failed_nightly_run_brings_its_log(self):
        log = "Failed App.IntegrationTests.Users.Create [2 s]\nTotal tests: 900"
        self.ok_box(nightly=f"dev FAIL {SHA_NEW[:12]} {STAMP} integration", **{"nightly-log dev": log})
        _, ok = deploy.command_check(self.root, CONFIG, args())
        self.assertTrue(ok)
        warnings = [text for level, text in deploy.load_state(self.root)["check"]["findings"] if level == "WARN"]
        self.assertTrue(any("Users.Create" in text and "defects.md" in text for text in warnings), warnings)

    def state(self, **values):
        state = {"env": "QA", "branch": "dev", "pre_tests": "run", "post_tests": "run", "commit": SHA_NEW,
                 "check": {"ok": True, "findings": [], "backups": {}},
                 "changes": {"previous": SHA_OLD, "migrations": [], "commits": 2}}
        state.update(values)
        deploy.save_state(self.root, state)

    def test_up_refuses_qa_without_a_gate_for_this_commit(self):
        self.fake((("git", "rev-parse", "HEAD"), deploy.Done(0, SHA_NEW)))
        for gate in [None, {"ok": True, "commit": SHA_OLD}, {"ok": False, "commit": SHA_NEW},
                     {"ok": True, "commit": SHA_NEW, "build_only": True}]:
            with self.subTest(gate=gate):
                self.state(gate=gate)
                with self.assertRaisesRegex(deploy.DeployError, "gate"):
                    deploy.command_up(self.root, CONFIG, args())

    def test_up_refuses_a_failed_check_and_a_moved_head(self):
        self.fake((("git", "rev-parse", "HEAD"), deploy.Done(0, SHA_OLD)))
        self.state(check={"ok": False})
        with self.assertRaisesRegex(deploy.DeployError, "check"):
            deploy.command_up(self.root, CONFIG, args())
        self.state()
        with self.assertRaisesRegex(deploy.DeployError, "HEAD moved"):
            deploy.command_up(self.root, CONFIG, args())

    def test_up_deploys_and_verifies(self):
        fake = self.fake((("git", "rev-parse", "HEAD"), deploy.Done(0, SHA_NEW)),
                         (("ssh", "up -d --build"), deploy.Done(0, "")),
                         (("ssh", "health/live"), deploy.Done(0, "live 1\nready 200\ndeny 401")))
        self.state(gate={"ok": True, "commit": SHA_NEW})
        text, ok = deploy.command_up(self.root, CONFIG, args())
        self.assertTrue(ok, text)
        self.assertTrue(fake.ran("ssh", "git pull --ff-only origin dev", "-f compose.qa.yaml up -d --build"))
        self.assertFalse(fake.ran("DOCKER_BUILDKIT"))
        self.assertIn("/api/v1/memberships 401", text)

    def test_no_protected_probe_stops_rather_than_guess_a_route(self):
        raw = {"deploy": {key: value for key, value in CONFIG["deploy"].items() if key != "protected_probe"}}
        self.fake()
        with self.assertRaisesRegex(deploy.DeployError, "protected_probe"):
            deploy.verify(deploy.target(raw, "DEV", "dev"))

    def test_a_deny_that_answers_200_blocks(self):
        self.fake((("git", "rev-parse", "HEAD"), deploy.Done(0, SHA_NEW)),
                  (("ssh", "health/live"), deploy.Done(0, "live 1\nready 200\ndeny 200")))
        self.state(gate={"ok": True, "commit": SHA_NEW})
        text, ok = deploy.command_up(self.root, CONFIG, args())
        self.assertFalse(ok)
        self.assertIn("authorization is not applied", text)

    def test_revert_only_after_a_failed_smoke_and_only_to_the_replaced_commit(self):
        self.fake((("ssh", "health/live"), deploy.Done(0, "live 1\nready 200\ndeny 401")))
        self.state(**smoke())
        with self.assertRaisesRegex(deploy.DeployError, "did not fail"):
            deploy.command_revert(self.root, CONFIG, args(to=SHA_OLD))
        self.state(**smoke(failed=1, exit_code=1, failing=["T.a"]))
        with self.assertRaisesRegex(deploy.DeployError, "only to the commit"):
            deploy.command_revert(self.root, CONFIG, args(to="c" * 40))
        text, ok = deploy.command_revert(self.root, CONFIG, args(to=SHA_OLD[:12]))
        self.assertTrue(ok, text)
        self.assertTrue(deploy.RUN.ran("ssh", f"git checkout {SHA_OLD}"))

    def test_the_report_says_what_was_skipped(self):
        self.fake()
        self.state(pre_tests="skip", env="DEV", up={"ok": True, "health": {"live": True, "ready": "200",
                   "deny": "401", "probe": "/api/v1/memberships"}}, **smoke(skipped=2, known={"no credentials": 2}))
        text, _ = deploy.command_report(self.root, CONFIG, args())
        first, second = text.splitlines()[:2]
        self.assertIn("IN PART", first)
        self.assertIn("Pre-deploy tests were SKIPPED", second)
        self.assertIn("2 skipped — no credentials", text)
        self.assertNotIn("Outcome", text)

    def test_the_report_keeps_every_warning_whole_and_the_real_counts(self):
        self.fake()
        gate = {"ok": True, "commit": SHA_NEW, "counts": {"unit": {"passed": 900, "skipped": 0},
                                                         "integration": {"passed": 1200, "skipped": 3}}}
        self.state(env="DEV", gate=gate,
                   check={"ok": True, "backups": {}, "findings": [("WARN", "backup cron failed:\nError: disk full")]},
                   up={"ok": True, "health": {"live": True}, "warnings": ["http://lan does not answer from this machine"]},
                   **smoke())
        text, _ = deploy.command_report(self.root, CONFIG, args())
        self.assertIn("Error: disk full", text)
        self.assertIn("does not answer from this machine", text)
        self.assertIn(", 3 skipped", text)
        self.state(env="DEV", gate={"ok": False, "commit": SHA_NEW}, up={"ok": True, "health": {}}, **smoke())
        self.assertIn("Pre-deploy:   did not pass", deploy.command_report(self.root, CONFIG, args())[0])

    def test_a_deploy_that_failed_never_reads_as_deployed(self):
        self.fake()
        for up, words in [(None, "NOT deployed"), ({"ok": False}, "Deploy FAILED")]:
            with self.subTest(words=words):
                self.state(**({"up": up} if up else {}))
                first = deploy.command_report(self.root, CONFIG, args())[0].splitlines()[0]
                self.assertTrue(first.startswith(words), first)
                self.assertNotIn("Deployed", first)


REVIEWER = ("APP_DEPLOYED_API_REVIEWER_EMAIL", "APP_DEPLOYED_API_REVIEWER_PASSWORD")
# An optional account the smoke suite logs in as too (`[deploy] smoke_pairs`): required on QA.
PAIRS_CONFIG = {**CONFIG, "deploy": {**CONFIG["deploy"], "smoke_pairs": [
    {"name": "reviewer", "email": REVIEWER[0], "password": REVIEWER[1], "reason": "No reviewer account",
     "required_on": ["QA"]}]}}


class SmokeAccountTests(Base):
    """`[deploy] smoke_pairs`: an optional account is optional only as a pair, and required where it says."""

    def environ(self, *names: str):
        values = {name: "" for name in REVIEWER}
        values.update({name: "set" for name in names})
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_half_a_pair_stops_before_the_run_and_names_the_missing_variable(self):
        self.environ(REVIEWER[0])
        with self.assertRaisesRegex(deploy.DeployError, REVIEWER[1]):
            deploy.smoke_pairs(PAIRS_CONFIG["deploy"], "DEV")

    def test_qa_requires_the_pair_and_dev_does_not(self):
        self.environ()
        with self.assertRaisesRegex(deploy.DeployError, "reviewer.*required"):
            deploy.smoke_pairs(PAIRS_CONFIG["deploy"], "QA")
        pairs = deploy.smoke_pairs(PAIRS_CONFIG["deploy"], "DEV")
        self.assertEqual([(p["name"], p["present"], p["required"]) for p in pairs], [("reviewer", False, False)])

    def test_the_smoke_step_refuses_half_a_pair_before_running_anything(self):
        self.environ(REVIEWER[1])
        fake = self.fake()
        deploy.save_state(self.root, {"env": "DEV", "branch": "dev", "post_tests": "run",
                                      "up": {"ok": True, "health": {"lan": 200}}})
        with self.assertRaisesRegex(deploy.DeployError, REVIEWER[0]):
            deploy.command_smoke(self.root, PAIRS_CONFIG, args())
        self.assertFalse(fake.ran("dotnet"))

    def test_a_pair_skip_fails_when_the_pair_was_set_or_is_required(self):
        def state(present, required):
            pair = {"name": "reviewer", "label": "no reviewer account", "present": present, "required": required}
            values = smoke(skipped=2, known={"no reviewer account": 2})
            values["smoke"]["pairs"] = [pair]
            return values
        for present, required, kind in [(True, False, deploy.FAILED), (False, True, deploy.FAILED),
                                        (False, False, deploy.IN_PART)]:
            with self.subTest(present=present, required=required):
                line, found = deploy.smoke_verdict(state(present, required))
                self.assertEqual(found, kind, line)
                self.assertIn("no reviewer account", line)

    def test_in_part_names_every_reason_that_skipped(self):
        line, kind = deploy.smoke_verdict(smoke(skipped=5, known={"no credentials": 3, "no reviewer account": 2}))
        self.assertEqual(kind, deploy.IN_PART)
        self.assertIn("3 skipped — no credentials; 2 skipped — no reviewer account", line)


HEALTH = {"live": True, "ready": "200", "deny": "401", "probe": "/api/v1/memberships"}


class LedgerTests(Base):
    def state(self, **values):
        state = {"env": "QA", "branch": "dev", "pre_tests": "run", "post_tests": "run", "commit": SHA_NEW,
                 "check": {"ok": True, "findings": [], "backups": {}},
                 "changes": {"previous": SHA_OLD, "migrations": ["src/x/Migrations/2026_Add.cs"], "commits": 2},
                 "gate": {"ok": True, "commit": SHA_NEW, "counts": {
                     "unit": {"executed": 900, "passed": 900, "failed": 0, "skipped": 0, "messages": ["secret?"]}}},
                 "up": {"ok": True, "health": HEALTH}}
        state.update(values)
        deploy.save_state(self.root, state)

    def records(self) -> list[dict]:
        return [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()]

    def test_the_ledger_sits_beside_the_private_settings_file(self):
        with mock.patch.dict(os.environ, {"APP_LOCAL_SETTINGS": ""}):
            path = deploy.ledger_path({"private_settings": "~/.app/local.toml", "book": {"env_prefix": "APP_"}})
            self.assertEqual(path, Path(os.path.expanduser("~/.app")) / deploy.LEDGER_NAME)
        self.assertEqual(deploy.ledger_path(CONFIG), self.ledger)

    def test_a_successful_deploy_appends_one_line_once(self):
        self.fake()
        self.state(**smoke())
        text, _ = deploy.command_report(self.root, CONFIG, args())
        self.assertIn("Ledger:       recorded in", text)
        [record] = self.records()
        self.assertEqual((record["target"], record["commit"], record["previous"], record["verdict"]),
                         ("QA", SHA_NEW, SHA_OLD, deploy.PASSED))
        self.assertEqual(record["migrations"], ["src/x/Migrations/2026_Add.cs"])
        self.assertEqual(record["pre_tests"], {"choice": "run", "result": "passed", "counts": {
            "unit": {"executed": 900, "passed": 900, "failed": 0, "skipped": 0}}})
        self.assertEqual(record["post_tests"]["counts"], {"executed": 10, "passed": 10, "failed": 0, "skipped": 0})
        self.assertRegex(record["when"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        # A second report of the same run adds nothing.
        text, _ = deploy.command_report(self.root, CONFIG, args())
        self.assertIn("already recorded", text)
        self.assertEqual(len(self.records()), 1)

    def test_the_ledger_holds_no_host_key_or_test_message(self):
        self.fake()
        self.state(pre_tests="skip", env="DEV", post_tests="skip")
        deploy.command_report(self.root, CONFIG, args())
        line = self.ledger.read_text(encoding="utf-8")
        for value in (CONFIG["deploy"]["ssh_host"], CONFIG["deploy"]["ssh_user"], CONFIG["deploy"]["ssh_key"],
                      "secret?", ".env"):
            self.assertNotIn(value, line)
        [record] = self.records()
        self.assertEqual(record["pre_tests"], {"choice": "skip", "result": "skipped"})
        self.assertEqual(record["post_tests"]["result"], deploy.SKIPPED)

    def test_a_revert_records_the_commit_now_running(self):
        self.fake()
        self.state(**smoke(failed=1, exit_code=1, failing=["T.a"]), revert={"ok": True, "to": SHA_OLD, "health": HEALTH})
        deploy.command_report(self.root, CONFIG, args())
        [record] = self.records()
        self.assertEqual((record["commit"], record["deployed"], record["reverted"], record["verdict"]),
                         (SHA_OLD, SHA_NEW, True, "reverted"))

    def test_a_deploy_that_did_not_succeed_records_nothing(self):
        self.fake()
        for up in (None, {"ok": False}):
            with self.subTest(up=up):
                self.state(up=up)
                text, _ = deploy.command_report(self.root, CONFIG, args())
                self.assertNotIn("Ledger", text)
                self.assertFalse(self.ledger.exists())


class SmokeLockTests(Base):
    """One smoke run per host at a time: a lock on the host, taken before the tests and always given back."""

    def setUp(self):
        super().setUp()
        self.state()

    def state(self, post_tests="run"):
        self.fake()  # answers git's --git-path, so the state file lands in this test's own folder
        deploy.save_state(self.root, {"env": "QA", "branch": "dev", "pre_tests": "run", "post_tests": post_tests,
                                      "commit": SHA_NEW, "up": {"ok": True, "health": dict(HEALTH, lan=200)}})

    def box(self, take: str, release: str = "LOCK released", test=None):
        fake = self.fake((("ssh", "LOCK released"), deploy.Done(0, release)),
                         (("ssh", "mkdir"), deploy.Done(0, take)))
        if test is not None:
            inner = fake.__call__

            def call(argv, **kwargs):
                if argv[:2] == ["dotnet", "test"]:
                    fake.calls.append((argv, ""))
                    return test()
                return inner(argv, **kwargs)
            deploy.RUN = call
        return fake

    @staticmethod
    def take(fake) -> str:
        return next(stdin for argv, stdin in fake.calls if argv[0] == "ssh" and "mkdir" in stdin)

    def order(self, fake) -> list[str]:
        """What ran, in order: take, test, release."""
        names = []
        for argv, stdin in fake.calls:
            if argv[:3] == ["git", "rev-parse", "--git-path"]:
                continue  # where the local state file is
            names.append("test" if argv[:2] == ["dotnet", "test"] else "release" if "LOCK released" in stdin
                         else "take" if "mkdir" in stdin else " ".join(argv))
        return names

    def test_the_lock_is_taken_before_the_tests_and_released_after(self):
        fake = self.box("LOCK taken")
        text, _ = deploy.command_smoke(self.root, CONFIG, args())
        self.assertEqual(self.order(fake), ["take", "test", "release"])
        take = self.take(fake)
        self.assertIn("cd /srv/app/qa/backend", take, "the lock is the environment's own")
        self.assertIn("git rev-parse --git-path factory-smoke.lock", take)
        self.assertIn(SHA_NEW[:12], take)
        self.assertNotIn("mv ", take, "nothing is broken without --break-stale-lock")
        release = next(stdin for argv, stdin in fake.calls if "LOCK released" in stdin)
        token = take.split(SHA_NEW[:12] + " ", 1)[1].split()[0].strip("'")
        self.assertIn(f"grep -qF {token}", release, "only this run's own lock is removed")
        self.assertNotIn("WARN", text)

    def test_the_lock_is_released_when_the_test_run_fails_or_raises(self):
        fake = self.box("LOCK taken", test=lambda: deploy.Done(1, "Failed!"))
        text, ok = deploy.command_smoke(self.root, CONFIG, args())
        self.assertFalse(ok)
        self.assertEqual(self.order(fake), ["take", "test", "release"])

        def interrupted():
            raise KeyboardInterrupt
        fake = self.box("LOCK taken", test=interrupted)
        with self.assertRaises(KeyboardInterrupt):
            deploy.command_smoke(self.root, CONFIG, args())
        self.assertEqual(self.order(fake), ["take", "test", "release"])

    def test_a_held_lock_refuses_the_run_and_says_who_and_when(self):
        fake = self.box(f"LOCK held 600 2026-11-16T09:00:00Z {SHA_OLD[:12]} 0f0f from deploy-host")
        with self.assertRaisesRegex(deploy.DeployError, "another smoke run holds QA's lock") as raised:
            deploy.command_smoke(self.root, CONFIG, args())
        self.assertIn("10 min ago", str(raised.exception))
        self.assertIn(SHA_OLD[:12], str(raised.exception))
        self.assertIn("2026-11-16T09:00:00Z", str(raised.exception))
        self.assertEqual(self.order(fake), ["take"], "no test ran, and someone else's lock is never removed")

    def test_a_fresh_lock_is_never_broken_even_when_asked(self):
        fake = self.box("LOCK held 60 2026-11-16T09:00:00Z x y from z")
        with self.assertRaisesRegex(deploy.DeployError, "another smoke run"):
            deploy.command_smoke(self.root, CONFIG, args(break_stale_lock=True))
        take = self.take(fake)
        self.assertIn(f'-ge {deploy.SMOKE_LOCK_STALE_SECONDS} ]', take, "the host breaks only a lock past the limit")
        self.assertEqual(self.order(fake), ["take"])

    def test_a_stale_lock_is_reported_and_needs_the_flag(self):
        fake = self.box(f"LOCK held {3 * 3600} 2026-11-16T06:00:00Z {SHA_OLD[:12]} ab from deploy-host")
        with self.assertRaisesRegex(deploy.DeployError, "STALE.*--break-stale-lock") as raised:
            deploy.command_smoke(self.root, CONFIG, args())
        self.assertIn("3 h 0 min ago", str(raised.exception))
        self.assertEqual(self.order(fake), ["take"])
        fake = self.box(f"LOCK broke {3 * 3600} 2026-11-16T06:00:00Z {SHA_OLD[:12]} ab from deploy-host\nLOCK taken")
        text, _ = deploy.command_smoke(self.root, CONFIG, args(break_stale_lock=True))
        self.assertIn("mv ", self.take(fake))
        self.assertIn("broke a stale smoke lock on QA", text)
        self.assertEqual(self.order(fake), ["take", "test", "release"])

    def test_a_lock_that_cannot_be_taken_stops_the_run(self):
        for out in ["", "LOCK error no-directory"]:
            with self.subTest(out=out):
                fake = self.box(out)
                with self.assertRaisesRegex(deploy.DeployError, "could not take QA's smoke lock"):
                    deploy.command_smoke(self.root, CONFIG, args())
                self.assertEqual(self.order(fake), ["take"])

    def test_a_release_that_fails_is_said_not_hidden(self):
        self.box("LOCK taken", release="")
        text, _ = deploy.command_smoke(self.root, CONFIG, args())
        self.assertIn("could not be removed", text)
        self.box("LOCK taken", release="LOCK not-ours")
        text, _ = deploy.command_smoke(self.root, CONFIG, args())
        self.assertIn("no longer this run's", text)

    def test_skipped_smoke_takes_no_lock_and_the_lock_holds_no_secret(self):
        self.state(post_tests="skip")
        fake = self.box("LOCK taken")
        deploy.command_smoke(self.root, CONFIG, args())
        self.assertEqual(self.order(fake), [])
        self.state()
        secret = "s3cret-" + "value"
        names = CONFIG["deploy"]["smoke_credentials"]
        with mock.patch.dict(os.environ, {name: secret for name in names}):
            fake = self.box("LOCK taken")
            deploy.command_smoke(self.root, CONFIG, args())
        scripts = [stdin for argv, stdin in fake.calls if argv[0] == "ssh"]
        self.assertEqual(len(scripts), 2)
        for script in scripts:
            self.assertNotIn(secret, script)
            for name in names:
                self.assertNotIn(name, script)


def status_output(head=SHA_OLD, branch="dev", committed="2026-11-15T10:00:00+00:00",
                  api="2026-11-16 09:00:00 +0000 UTC|Up 3 hours") -> str:
    return f"== head\n{head}\n== branch\n{branch}\n== committed\n{committed}\n== api\n{api}\n== end\n"


class StatusTests(Base):
    def live(self, out=None, behind="4"):
        return self.fake((("ssh", "== head"), deploy.Done(0, out or status_output())),
                         (("git", "rev-list", "--count"), deploy.Done(0, behind)))

    def write_ledger(self, *records):
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text("".join(json.dumps(r) + "\n" for r in records) + "{torn", encoding="utf-8")

    def test_status_reads_dev_and_qa_live(self):
        fake = self.live()
        text, ok = deploy.command_status(self.root, CONFIG, args(target="all"))
        self.assertTrue(ok, text)
        for env in ("DEV", "QA"):
            self.assertIn(f"{env} — live read of {CONFIG['deploy'][env]['directory']}", text)
            self.assertTrue(fake.ran("ssh", CONFIG["deploy"][env]["directory"]))
        self.assertIn(f"Commit:       {SHA_OLD[:12]} (branch dev)", text)
        self.assertIn("2026-11-16 09:00:00 +0000 UTC (API container created; Up 3 hours)", text)
        self.assertIn("4 commits behind origin/dev", text)
        self.assertNotIn("WARN", text)

    def test_one_target_reads_only_that_one(self):
        fake = self.live(behind="0")
        text, ok = deploy.command_status(self.root, CONFIG, args(target="qa"))
        self.assertTrue(ok, text)
        self.assertIn("QA — live read", text)
        self.assertNotIn("DEV", text)
        self.assertFalse(fake.ran("ssh", CONFIG["deploy"]["DEV"]["directory"]))
        self.assertIn("level with origin/dev", text)

    def test_status_changes_nothing_on_the_host_and_never_names_env(self):
        fake = self.live()
        deploy.command_status(self.root, CONFIG, args(target="all"))
        scripts = [stdin for argv, stdin in fake.calls if argv[0] == "ssh"]
        self.assertEqual(len(scripts), 2)
        for script in scripts:
            for word in (".env", "pull", "fetch", "checkout", "docker compose", " up ", "chown", "rm ", "restart"):
                self.assertNotIn(word, script)
        self.assertFalse(deploy.state_path(self.root).exists())

    def test_anything_but_dev_qa_or_all_is_refused_before_anything_runs(self):
        for value in ["uat", "UAT", "prod", "production", "staging", "deploy-host", "dev,qa"]:
            with self.subTest(value=value):
                fake = self.live()
                with self.assertRaises(deploy.DeployError) as caught:
                    deploy.command_status(self.root, CONFIG, args(target=value))
                self.assertEqual(str(caught.exception), deploy.REFUSED_TARGET.format(value=value))
                self.assertEqual(fake.calls, [])

    def test_an_unreachable_host_falls_back_to_the_ledger_and_says_so(self):
        self.fake((("ssh",), deploy.Done(255, "ssh: connect to host port 22: Connection timed out")),
                  (("git", "rev-list", "--count"), deploy.Done(0, "2")))
        self.write_ledger({"target": "QA", "commit": "c" * 40, "when": OLD, "verdict": "passed", "branch": "dev"},
                          {"target": "QA", "commit": SHA_NEW, "when": STAMP, "verdict": "in-part", "branch": "dev"},
                          {"target": "DEV", "commit": SHA_OLD, "when": STAMP, "verdict": "passed", "branch": "dev"})
        text, ok = deploy.command_status(self.root, CONFIG, args(target="qa"))
        self.assertFalse(ok)
        self.assertIn("QA — COULD NOT READ LIVE (exit 255: ssh: connect to host port 22: Connection timed out)", text)
        self.assertIn("NOT a live read", text)
        self.assertIn(f"Commit:       {SHA_NEW[:12]}", text)
        self.assertIn(f"Deployed:     {STAMP} (recorded by `factory deploy report`)", text)
        self.assertIn("2 commits behind origin/dev", text)
        self.assertNotIn(SHA_OLD[:12], text)

    def test_an_unreachable_host_with_no_record_says_there_is_none(self):
        self.fake((("ssh",), deploy.Done(255, "Permission denied (publickey).")))
        text, ok = deploy.command_status(self.root, CONFIG, args(target="dev"))
        self.assertFalse(ok)
        self.assertIn("holds no record for DEV either", text)

    def test_a_checkout_newer_than_the_container_warns(self):
        self.live(status_output(committed="2026-11-16T12:00:00+00:00"))
        text, ok = deploy.command_status(self.root, CONFIG, args(target="dev"))
        self.assertTrue(ok)
        self.assertIn("WARN  the checkout's commit is newer than the API container", text)

    def test_a_commit_this_clone_lacks_is_unknown_not_zero(self):
        self.live()
        deploy.RUN.rules.insert(0, (("git", "cat-file"), deploy.Done(1, "")))
        text, _ = deploy.command_status(self.root, CONFIG, args(target="dev"))
        self.assertIn("Behind:       unknown", text)


if __name__ == "__main__":
    unittest.main()
