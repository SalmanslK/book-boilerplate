"""
Self-tests for the bitbucket pack's checks, beside the Book's own in test_checks.py (whose helpers they use).
`book init` copies this file in with the pack:

    python3 devtools/checks/run-self-tests.py devtools/checks
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_checks import (HERE, Repo, RepoTestCase, load, run_main, setUpModule, tearDownModule,  # noqa: E402,F401
                         AWS_KEY)

check_pipeline = load("check-pipeline")


RUNNER_STEP = """\
definitions:
  steps:
    - step: &build
        name: Build
        # a comment between keys is not a key
        runs-on:
          - self.hosted
          - linux
        max-time: 45
        script:
          - dotnet build
    - step: &quick
        name: Quick
        runs-on: [self.hosted, linux]
        max-time: 10
        script:
          - echo ok
pipelines:
  pull-requests:
    '**':
      - step: *build
      - step: *quick
  branches:
    dev:
      - step: *build
"""



class PipelineTests(unittest.TestCase):
    def test_runner_steps_with_time_limits_and_no_default_pass(self):
        self.assertEqual(check_pipeline.problems(RUNNER_STEP), [])

    def test_a_step_without_runs_on_would_bill_the_cloud(self):
        text = RUNNER_STEP.replace("        runs-on: [self.hosted, linux]\n", "")
        found = check_pipeline.problems(text)
        self.assertEqual(len(found), 1, found)
        self.assertIn("'Quick' has no `runs-on", found[0])

    def test_runs_on_without_self_hosted_is_the_cloud_too(self):
        found = check_pipeline.problems(RUNNER_STEP.replace("[self.hosted, linux]", "[linux]"))
        self.assertIn("'Quick' has no `runs-on", found[0])

    def test_a_runs_on_only_in_a_comment_does_not_count(self):
        text = RUNNER_STEP.replace("        runs-on: [self.hosted, linux]\n", "        # runs-on: [self.hosted]\n")
        self.assertIn("'Quick' has no `runs-on", check_pipeline.problems(text)[0])

    def test_a_runs_on_inside_the_script_is_not_the_steps_own(self):
        text = RUNNER_STEP.replace("        runs-on: [self.hosted, linux]\n", "").replace(
            "          - echo ok\n", "          - echo ok\n          - runs-on: [self.hosted]\n")
        self.assertIn("'Quick' has no `runs-on", check_pipeline.problems(text)[0])

    def test_a_missing_or_too_long_time_limit_fails(self):
        self.assertIn("'Quick' needs a `max-time`",
                      check_pipeline.problems(RUNNER_STEP.replace("        max-time: 10\n", ""))[0])
        found = check_pipeline.problems(RUNNER_STEP.replace("max-time: 10", "max-time: 240"))
        self.assertIn("(has '240')", found[0])

    def test_a_default_pipeline_fails(self):
        text = RUNNER_STEP.replace("pipelines:\n", "pipelines:\n  default:\n    - step: *quick\n")
        found = check_pipeline.problems(text)
        self.assertEqual(len(found), 1, found)
        self.assertIn("`default` pipeline", found[0])

    def test_a_default_pipeline_fails_at_any_indent_width(self):
        text = (RUNNER_STEP.split("pipelines:")[0]
                + "pipelines:\n    default:\n        - step: *quick\n    branches:\n        dev:\n            - step: *build\n")
        found = check_pipeline.problems(text)
        self.assertEqual(len(found), 1, found)
        self.assertIn("`default` pipeline", found[0])

    def test_a_step_named_default_under_branches_is_not_the_default_pipeline(self):
        text = RUNNER_STEP.replace("    dev:\n", "    default:\n")
        self.assertEqual(check_pipeline.problems(text), [])

    def test_an_inline_step_is_checked_like_a_definition(self):
        text = RUNNER_STEP + "  custom:\n    once:\n      - step:\n          name: Inline\n          script:\n            - echo\n"
        found = check_pipeline.problems(text)
        self.assertEqual(len(found), 2, found)
        self.assertTrue(all("'Inline'" in problem for problem in found))

    def test_a_file_with_no_steps_is_not_a_pass(self):
        self.assertIn("no step definitions", check_pipeline.problems("pipelines:\n  branches: {}\n")[0])

    def test_the_repositorys_own_pipeline_passes(self):
        self.assertEqual(check_pipeline.main([str(HERE.parents[1] / "bitbucket-pipelines.yml")]), 0)

    def test_a_missing_file_is_not_a_pass(self):
        code, out = run_main(check_pipeline.main, [str(HERE / "no-such-pipeline.yml")])
        self.assertEqual(code, 1)
        self.assertIn("does not exist", out)


# ---------------------------------------------------------------- check-vulnerable-packages
