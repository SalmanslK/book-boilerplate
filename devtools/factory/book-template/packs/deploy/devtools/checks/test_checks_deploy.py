"""
Self-tests for the deploy pack's checks, beside the Book's own in test_checks.py (whose helpers they use).
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

check_compose = load("check-compose")


def compose(api: list[dict], postgres: list[dict], caddy: str = "9443", subnet: str = "172.30.0.0/16") -> dict:
    return {"services": {"api": {"ports": api}, "postgres": {"ports": postgres},
                         "caddy": {"ports": [{"published": caddy}]}},
            "networks": {"default": {"ipam": {"config": [{"subnet": subnet}]}}}}


class ComposeTests(unittest.TestCase):
    # What [deploy.compose] in factory.toml names; the values are the project's, never the check's (Book D16).
    WANT = {"laptop": {"api": ["8080"], "postgres": ["127.0.0.1:5432"]},
            "DEV": {"api": ["8280"], "postgres": ["127.0.0.1:5532"], "caddy": ["9443"], "subnet": ["172.30.0.0/16"]},
            "QA": {"api": ["8281"], "postgres": [], "caddy": ["9444"], "subnet": ["172.31.0.0/16"]}}
    LAPTOP = compose([{"published": "8080"}], [{"host_ip": "127.0.0.1", "published": "5432"}])
    DEV = compose([{"published": "8280"}], [{"host_ip": "127.0.0.1", "published": "5532"}])
    QA = compose([{"published": "8281"}], [], caddy="9444", subnet="172.31.0.0/16")

    def problems(self, **renders: dict) -> list[str]:
        return check_compose.problems({"laptop": self.LAPTOP, "DEV": self.DEV, "QA": self.QA, **renders}, self.WANT)

    def test_the_intended_ports_pass(self):
        self.assertEqual(self.problems(), [])

    def test_an_overlay_that_appends_a_port_fails(self):
        qa = compose([{"published": "8280"}, {"published": "8281"}], [], caddy="9444", subnet="172.31.0.0/16")
        self.assertEqual(self.problems(QA=qa), ["QA api ports: ['8280', '8281'], expected ['8281']"])

    def test_postgres_on_every_interface_fails(self):
        dev = compose([{"published": "8280"}], [{"published": "5532"}])
        self.assertIn("0.0.0.0:5532", self.problems(DEV=dev)[0])

    def test_qa_postgres_with_no_ports_key_publishes_nothing(self):
        qa = compose([{"published": "8281"}], [], caddy="9444", subnet="172.31.0.0/16")
        qa["services"]["postgres"] = {}
        self.assertEqual(self.problems(QA=qa), [])

    def test_each_named_service_and_subnet_is_compared(self):
        dev = compose([{"published": "8080"}], [{"host_ip": "127.0.0.1", "published": "5432"}],
                      caddy="443", subnet="172.28.0.0/16")
        self.assertEqual(len(self.problems(DEV=dev)), 4)

    def test_a_service_the_settings_name_that_the_render_lacks_fails(self):
        dev = compose([{"published": "8280"}], [{"host_ip": "127.0.0.1", "published": "5532"}])
        del dev["services"]["caddy"]
        self.assertEqual(self.problems(DEV=dev), ["DEV has no service 'caddy', which [deploy.compose.DEV] names"])

    def test_the_expected_sets_come_from_factory_toml_and_none_is_a_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "devtools" / "factory").mkdir(parents=True)
            config = root / "devtools" / "factory" / "factory.toml"
            config.write_text("[deploy]\nssh_host = 'h'\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, r"no \[deploy.compose\]"):
                check_compose.expected(root)
            config.write_text("[deploy.compose.DEV]\napi = [8280]\n", encoding="utf-8")
            self.assertEqual(check_compose.expected(root), {"laptop": {}, "DEV": {"api": ["8280"]}, "QA": {}})

    def test_the_projects_own_placeholders_are_a_setting(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "devtools" / "factory").mkdir(parents=True)
            config = root / "devtools" / "factory" / "factory.toml"
            config.write_text("[deploy.compose_placeholders]\nEXTRA_KEY = 'ci-placeholder'\n", encoding="utf-8")
            self.assertEqual(check_compose.project_placeholders(root), {"EXTRA_KEY": "ci-placeholder"})
            config.write_text("[deploy]\nssh_host = 'h'\n", encoding="utf-8")
            self.assertEqual(check_compose.project_placeholders(root), {})
            config.write_text("[deploy.compose_placeholders]\nEXTRA_KEY = 3\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "compose_placeholders"):
                check_compose.project_placeholders(root)


class ComposeThrowawaySettingTests(unittest.TestCase):
    """Throwaway PostgreSQL settings belong in the tests' container, never in a compose file."""

    def problems(self, name: str, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / name).write_text(text, encoding="utf-8")
            return check_compose.throwaway_problems(Path(folder))

    def test_a_compose_file_without_them_passes(self):
        self.assertEqual(self.problems("compose.yaml", "services:\n  postgres:\n    image: postgres:17\n"), [])

    def test_every_throwaway_setting_fails_in_any_compose_file(self):
        for setting in ("fsync", "synchronous_commit", "full_page_writes"):
            for value in ("off", "false", "'off'"):
                with self.subTest(setting=setting, value=value):
                    found = self.problems("compose.dev.yaml",
                                          f"services:\n  postgres:\n    command: [\"-c\", \"{setting}={value}\"]\n")
                    self.assertEqual(len(found), 1, found)
                    self.assertIn("compose.dev.yaml:3", found[0])
                    self.assertIn(setting, found[0])

    def test_a_setting_glued_to_its_flag_still_fails(self):
        self.assertEqual(len(self.problems("compose.yaml", "services:\n  postgres:\n    command: postgres -cfsync=off\n")), 1)

    def test_a_durability_setting_left_on_passes(self):
        text = "services:\n  postgres:\n    command: [\"-c\", \"fsync=on\", \"-c\", \"synchronous_commit=remote_apply\"]\n"
        self.assertEqual(self.problems("compose.yaml", text), [])

    def test_a_tmpfs_mount_of_postgres_data_fails(self):
        for text in ("services:\n  postgres:\n    tmpfs:\n      - /var/lib/postgresql/data\n",
                     "services:\n  postgres:\n    tmpfs: /var/lib/postgresql/data\n",
                     "services:\n  postgres:\n    volumes:\n      - type: tmpfs\n        target: /var/lib/postgresql/data\n"):
            with self.subTest(text):
                self.assertEqual(len(self.problems("compose.yaml", text)), 1)

    def test_a_tmpfs_for_tmp_on_another_container_passes(self):
        text = ("services:\n  api:\n    read_only: true\n    tmpfs:\n      - /tmp\n"
                "  postgres:\n    volumes:\n      - pgdata:/var/lib/postgresql/data\n")
        self.assertEqual(self.problems("compose.yaml", text), [])

    def test_a_comment_that_names_one_does_not(self):
        self.assertEqual(self.problems("compose.yaml", "# never fsync=off here\nservices: {}  # tmpfs no\n"), [])

    def test_a_hash_inside_quotes_is_not_a_comment(self):
        text = "services:\n  postgres:\n    command: [\"-c\", \"application_name=a#b\", \"-c\", \"fsync=off\"]\n"
        self.assertEqual(len(self.problems("compose.yaml", text)), 1)

    def test_docker_compose_files_in_subfolders_are_read(self):
        with tempfile.TemporaryDirectory() as folder:
            nested = Path(folder) / "deploy" / "docker-compose.override.yml"
            nested.parent.mkdir()
            nested.write_text("services:\n  postgres:\n    command: -c fsync=off\n", encoding="utf-8")
            found = check_compose.throwaway_problems(Path(folder))
        self.assertEqual(len(found), 1, found)
        self.assertIn("deploy/docker-compose.override.yml:3", found[0])

    def test_the_repository_compose_files_keep_durability(self):
        self.assertEqual(check_compose.throwaway_problems(check_compose.ROOT), [])


# ---------------------------------------------------------------- run-self-tests.py
