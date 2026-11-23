"""
book_config: the project's settings merged over the Book's defaults, by the rules its docstring states — and the
template's own defaults files, which every Book copies, holding no project's name.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import book_config  # noqa: E402

TEMPLATE = HERE / "book-template"


def reader(files: dict[str, str]):
    return lambda rel: files.get(rel)


class Merge(unittest.TestCase):
    def test_tables_merge_per_key_and_the_project_wins(self):
        base = {"guard": {"read_only": ["requirements/**"], "dotnet_ef": False}, "book": {"roles": ["BE"]}}
        over = {"guard": {"dotnet_ef": True}, "book": {"name": "Acme"}}
        self.assertEqual(book_config.merge(base, over),
                         {"guard": {"read_only": ["requirements/**"], "dotnet_ef": True},
                          "book": {"roles": ["BE"], "name": "Acme"}})

    def test_a_list_the_project_sets_replaces_the_default(self):
        self.assertEqual(book_config.merge({"ask": ["a", "b"]}, {"ask": ["c"]}), {"ask": ["c"]})

    def test_an_empty_list_replaces_too(self):
        self.assertEqual(book_config.merge({"ask": ["a"]}, {"ask": []}), {"ask": []})

    def test_tables_with_an_id_merge_by_id(self):
        base = {"code_rules": [{"id": "x", "pattern": "p", "allowed": []}, {"id": "y", "pattern": "q"}]}
        over = {"code_rules": [{"id": "x", "allowed": [{"file": "f", "count": 1, "reason": "r"}]},
                               {"id": "z", "pattern": "s"}]}
        merged = book_config.merge(base, over)["code_rules"]
        self.assertEqual([rule["id"] for rule in merged], ["x", "y", "z"])
        self.assertEqual(merged[0], {"id": "x", "pattern": "p", "allowed": [{"file": "f", "count": 1, "reason": "r"}]})

    def test_a_code_rule_switched_off_by_id_leaves_the_merged_settings(self):
        files = {"devtools/factory/book-defaults.toml":
                 '[[code_rules]]\nid = "a"\npattern = "x"\n\n[[code_rules]]\nid = "b"\npattern = "y"\n'}
        project = {"code_rules": [{"id": "a", "enabled": False}],
                   "stacks": [{"root": "web", "code_rules": [{"id": "c", "pattern": "z", "enabled": False},
                                                             {"id": "d", "pattern": "w"}]}]}
        merged = book_config.merged(project, reader(files))
        self.assertEqual([rule["id"] for rule in merged["code_rules"]], ["b"])
        self.assertEqual([rule["id"] for rule in merged["stacks"][0]["code_rules"]], ["d"])
        on = book_config.merged({"code_rules": [{"id": "a", "enabled": True}]}, reader(files))
        self.assertEqual([rule["id"] for rule in on["code_rules"]], ["a", "b"])

    def test_load_the_view_every_reader_takes_drops_a_switched_off_rule(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "devtools/factory").mkdir(parents=True)
            (root / book_config.DEFAULTS).write_text('[[code_rules]]\nid = "a"\npattern = "x"\n', encoding="utf-8")
            (root / book_config.PROJECT).write_text('[[code_rules]]\nid = "a"\nenabled = false\n', encoding="utf-8")
            self.assertEqual(book_config.load(root)["code_rules"], [])

    def test_a_list_of_tables_without_ids_is_replaced(self):
        base = {"rows": [{"row": "a"}, {"row": "b"}]}
        self.assertEqual(book_config.merge(base, {"rows": [{"row": "c"}]}), {"rows": [{"row": "c"}]})

    def test_neither_side_is_changed(self):
        base, over = {"t": {"a": 1}}, {"t": {"b": 2}}
        book_config.merge(base, over)
        self.assertEqual((base, over), ({"t": {"a": 1}}, {"t": {"b": 2}}))


class Layers(unittest.TestCase):
    def test_core_then_stack_then_packs_in_order(self):
        project = {"book": {"stack": "dotnet", "packs": ["deploy", "fe-guides"]}}
        self.assertEqual(book_config.layers(project), [
            "devtools/factory/book-defaults.toml", "devtools/factory/book-defaults/dotnet.toml",
            "devtools/factory/book-defaults/deploy.toml", "devtools/factory/book-defaults/fe-guides.toml"])

    def test_a_stack_free_book_reads_the_none_layer(self):
        self.assertEqual(book_config.layers({}), ["devtools/factory/book-defaults.toml",
                                                  "devtools/factory/book-defaults/none.toml"])

    def test_merged_takes_each_layer_and_the_project_last(self):
        files = {"devtools/factory/book-defaults.toml": 'a = 1\nb = 1\nc = 1\n',
                 "devtools/factory/book-defaults/dotnet.toml": 'b = 2\nc = 2\n'}
        merged = book_config.merged({"book": {"stack": "dotnet"}, "c": 3}, reader(files))
        self.assertEqual((merged["a"], merged["b"], merged["c"]), (1, 2, 3))

    def test_no_defaults_file_is_a_book_older_than_1_6(self):
        self.assertEqual(book_config.merged({"x": 1}, reader({})), {"x": 1})
        self.assertEqual(book_config.merged({"book": {"book_version": "1.5.0"}}, reader({}))["book"],
                         {"book_version": "1.5.0"})

    def test_a_1_6_book_without_its_core_defaults_file_is_an_error(self):
        with self.assertRaises(book_config.ConfigError) as caught:
            book_config.merged({"book": {"book_version": "1.6.0"}}, reader({}))
        self.assertIn("devtools/factory/book-defaults.toml is missing", str(caught.exception))
        with self.assertRaises(book_config.ConfigError):
            book_config.merged({"book": {"book_version": "1.10.2"}}, reader({}))

    def test_a_defaults_file_the_lock_records_must_be_there(self):
        lock = book_config.lock_text({"devtools/factory/book-defaults.toml": b"a = 1\n",
                                      "devtools/factory/book-defaults/deploy.toml": b"b = 1\n"})
        files = {book_config.LOCK: lock, "devtools/factory/book-defaults.toml": "a = 1\n"}
        project = {"book": {"book_version": "1.5.0", "packs": ["deploy", "bitbucket"]}}
        with self.assertRaises(book_config.ConfigError) as caught:
            book_config.merged(project, reader(files))
        self.assertIn("book-defaults/deploy.toml is missing", str(caught.exception))
        # A pack with no defaults file of its own (bitbucket here) adds nothing, as before.
        files["devtools/factory/book-defaults/deploy.toml"] = "b = 1\n"
        self.assertEqual(book_config.merged(project, reader(files))["b"], 1)
        del files["devtools/factory/book-defaults/deploy.toml"]
        # Declining a defaults file never makes its absence acceptable: its guard lists would go (Book D71).
        project["book"]["declined"] = ["devtools/factory/book-defaults/deploy.toml"]
        with self.assertRaises(book_config.ConfigError):
            book_config.merged(project, reader(files))

    def test_declining_the_core_defaults_or_a_rules_file_is_never_honoured(self):
        project = {"book": {"book_version": "1.6.0", "declined": ["devtools/factory/book-defaults.toml"]}}
        with self.assertRaises(book_config.ConfigError):
            book_config.merged(project, reader({}))
        for rel in ("devtools/factory/book-defaults.toml", "./devtools/factory/book-defaults/dotnet.toml",
                    ".claude/book/rules.md", "DEVTOOLS/factory/Book-Defaults.toml"):
            self.assertTrue(book_config.undeclinable(rel), rel)
        for rel in ("devtools/checks/x.py", "docs/fe-integration-guides/README.md", ".claude/skills/a/SKILL.md"):
            self.assertFalse(book_config.undeclinable(rel), rel)

    def test_a_defaults_file_that_is_not_toml_is_an_error_naming_it(self):
        with self.assertRaises(book_config.ConfigError) as caught:
            book_config.merged({}, reader({"devtools/factory/book-defaults.toml": "a = ["}))
        self.assertIn("book-defaults.toml", str(caught.exception))

    def test_defaults_alone_leave_the_project_out(self):
        files = {"devtools/factory/book-defaults.toml": 'a = 1\n'}
        self.assertEqual(book_config.defaults({"a": 5}, reader(files)), {"a": 1})


class Extend(unittest.TestCase):
    FILES = {"devtools/factory/book-defaults.toml": '[guard]\nask = ["CLAUDE.md", ".claude/**"]\n'}

    def test_extend_appends_without_duplicates(self):
        project = {"extend": {"guard": {"ask": ["tools/**", "CLAUDE.md"]}}}
        merged = book_config.merged(project, reader(self.FILES))
        self.assertEqual(merged["guard"]["ask"], ["CLAUDE.md", ".claude/**", "tools/**"])
        self.assertNotIn("extend", merged)

    def test_extend_adds_to_the_projects_own_list_too(self):
        project = {"guard": {"ask": ["x"]}, "extend": {"guard": {"ask": ["y"]}}}
        self.assertEqual(book_config.merged(project, reader(self.FILES))["guard"]["ask"], ["x", "y"])

    def test_extend_of_a_list_nothing_sets_is_that_list(self):
        merged = book_config.merged({"extend": {"guard": {"service_accounts": ["deploy"]}}}, reader(self.FILES))
        self.assertEqual(merged["guard"]["service_accounts"], ["deploy"])

    def test_extend_with_a_scalar_is_refused(self):
        with self.assertRaises(book_config.ConfigError):
            book_config.merged({"extend": {"guard": {"dotnet_ef": True}}}, reader(self.FILES))

    def test_extend_of_a_scalar_setting_is_refused(self):
        files = {"devtools/factory/book-defaults.toml": '[guard]\ndotnet_ef = false\n'}
        with self.assertRaises(book_config.ConfigError):
            book_config.merged({"extend": {"guard": {"dotnet_ef": ["x"]}}}, reader(files))


class Value(unittest.TestCase):
    def test_a_dotted_key(self):
        self.assertEqual(book_config.value({"fe_guides": {"drive_folder_id": "abc"}}, "fe_guides.drive_folder_id"), "abc")

    def test_an_unset_key_names_itself(self):
        with self.assertRaises(KeyError):
            book_config.value({"fe_guides": {}}, "fe_guides.drive_folder_id")


class Git(unittest.TestCase):
    """load_at and load_staged read every layer as the commit or the index holds it."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="book-config-"))
        self.git("init", "-q")
        (self.root / "devtools/factory").mkdir(parents=True)
        self.write("devtools/factory/book-defaults.toml", "[guard]\nask = [\"a\"]\n")
        self.write("devtools/factory/factory.toml", "base_ref = \"origin/main\"\n")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "x")

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def write(self, rel: str, text: str) -> None:
        (self.root / rel).write_text(text, encoding="utf-8")

    def test_at_a_commit(self):
        self.write("devtools/factory/book-defaults.toml", "[guard]\nask = [\"changed\"]\n")
        self.assertEqual(book_config.load_at(self.root, "HEAD")["guard"]["ask"], ["a"])
        self.assertEqual(book_config.load(self.root)["guard"]["ask"], ["changed"])

    def test_a_commit_without_factory_toml_is_none(self):
        self.git("rm", "-q", "devtools/factory/factory.toml")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "y")
        self.assertIsNone(book_config.load_at(self.root, "HEAD"))

    def test_staged_reads_the_index_and_the_working_copy_where_nothing_is_staged(self):
        self.write("devtools/factory/book-defaults.toml", "[guard]\nask = [\"staged\"]\n")
        self.git("add", "devtools/factory/book-defaults.toml")
        self.write("devtools/factory/book-defaults.toml", "[guard]\nask = [\"working\"]\n")
        self.assertEqual(book_config.load_staged(self.root)["guard"]["ask"], ["staged"])


class TheTemplatesDefaults(unittest.TestCase):
    """The defaults files the template ships: valid, name-free, and one per stack that has review dimensions."""

    def files(self) -> list[Path]:
        found = [HERE / "book-defaults.toml", *sorted((HERE / "book-defaults").glob("*.toml"))]
        return found + sorted(TEMPLATE.glob("packs/*/devtools/factory/book-defaults/*.toml"))

    def test_each_is_valid_toml(self):
        for path in self.files():
            with self.subTest(path.name), open(path, "rb") as handle:
                tomllib.load(handle)

    def test_a_pack_file_is_named_for_its_pack(self):
        for path in TEMPLATE.glob("packs/*/devtools/factory/book-defaults/*.toml"):
            self.assertEqual(path.stem, path.parents[3].name)

    def test_none_names_the_template(self):
        manifest = tomllib.loads((TEMPLATE / "manifest.toml").read_text(encoding="utf-8"))
        names = [manifest["source"][key] for key in ("name", "slug", "env_prefix")]
        for path in self.files():
            text = path.read_text(encoding="utf-8")
            for name in names:
                with self.subTest(path.name, name=name):
                    self.assertNotIn(name, text)
            self.assertNotIn("{{", text)

    def test_every_stack_has_its_review_dimensions(self):
        for stack, path in (("none", HERE / "book-defaults/none.toml"),
                            ("dotnet", TEMPLATE / "packs/dotnet/devtools/factory/book-defaults/dotnet.toml"),
                            ("pytest", TEMPLATE / "packs/pytest/devtools/factory/book-defaults/pytest.toml")):
            with self.subTest(stack):
                scope = tomllib.loads(path.read_text(encoding="utf-8"))["review_scope"]
                self.assertTrue(scope["dimensions"] and scope["rows"] and scope["always"])

    def test_the_deploy_rule_is_no_setting(self):
        """DEV and QA are the guard's own code: no defaults file names the environments a deploy may reach."""
        for path in self.files():
            config = tomllib.loads(path.read_text(encoding="utf-8"))
            guard = config.get("guard") or {}
            with self.subTest(path.name):
                self.assertFalse({"deploy_targets", "allowed_targets", "deploy_environments"} & set(guard))


class Imports(unittest.TestCase):
    """CLAUDE.md read as Claude Code reads it: `@path` lines in place, relative to the file, outside fences."""

    def test_an_import_is_read_in_place_and_a_fenced_one_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".claude/book").mkdir(parents=True)
            (root / "docs").mkdir()
            (root / ".claude/book/rules.md").write_text("THE RULES\n", encoding="utf-8")
            (root / ".claude/book/docs.md").write_text("DOC RULES\n@rules.md\n", encoding="utf-8")
            (root / "docs/CLAUDE.md").write_text("# Docs\n@../.claude/book/docs.md\n```\n@nope.md\n```\nOwn\n",
                                                 encoding="utf-8")
            text = book_config.expanded(root, "docs/CLAUDE.md")
            self.assertEqual(text, "# Docs\nDOC RULES\nTHE RULES\n```\n@nope.md\n```\nOwn\n")
            self.assertEqual(book_config.imports((root / "docs/CLAUDE.md").read_text(encoding="utf-8")),
                             ["../.claude/book/docs.md"])
            self.assertEqual(book_config.missing_imports(root, "docs/CLAUDE.md"), [])
            (root / "CLAUDE.md").write_text("@.claude/book/gone.md\n", encoding="utf-8")
            self.assertEqual(book_config.missing_imports(root, "CLAUDE.md"), ["CLAUDE.md: @.claude/book/gone.md"])
            self.assertIsNone(book_config.expanded(root, "no/CLAUDE.md"))

    def test_imports_stop_after_four_hops(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for n in range(7):
                (root / f"f{n}.md").write_text(f"L{n}\n@f{n + 1}.md\n", encoding="utf-8")
            text = book_config.expanded(root, "f0.md")
            self.assertIn("L4", text)
            self.assertNotIn("L5", text)


class Drift(unittest.TestCase):
    """A Book-owned file the project changed fails, naming where the change belongs; drift_accepted warns."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.files = {".claude/book/rules.md": b"rule\n", "devtools/checks/x.py": b"x = 1\n",
                      "devtools/factory/book-template/packs/a.md": b"t\n"}
        for rel, data in self.files.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_bytes(data)
        (self.root / "devtools/factory").mkdir(parents=True, exist_ok=True)
        (self.root / book_config.LOCK).write_text(book_config.lock_text(self.files), encoding="utf-8")

    def tearDown(self):
        self.folder.cleanup()

    def test_files_as_written_pass_and_line_endings_do_not_count(self):
        (self.root / "devtools/checks/x.py").write_bytes(b"x = 1\r\n")
        self.assertEqual(book_config.drift(self.root, {}), ([], []))
        self.assertNotIn("book-template", (self.root / book_config.LOCK).read_text(encoding="utf-8"))

    def test_a_changed_file_fails_with_where_its_change_belongs(self):
        (self.root / ".claude/book/rules.md").write_text("rule\nmy own\n", encoding="utf-8")
        problems, warnings = book_config.drift(self.root, {})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(".claude/book/rules.md: differs from the Book's copy", problems[0])
        self.assertIn("in the CLAUDE.md that imports this file", problems[0])
        self.assertEqual(warnings, [])

    def test_an_accepted_or_declined_file_does_not_fail(self):
        (self.root / ".claude/book/rules.md").write_text("changed\n", encoding="utf-8")
        (self.root / "devtools/checks/x.py").write_text("changed\n", encoding="utf-8")
        config = {"book": {"drift_accepted": [{"file": ".claude/book/rules.md", "reason": "moving it in PR 4"}],
                           "declined": ["devtools/checks/x.py"]}}
        problems, warnings = book_config.drift(self.root, config)
        self.assertEqual(problems, [])
        self.assertIn("accepted for now (moving it in PR 4)", warnings[0])
        (self.root / "devtools/checks/x.py").unlink()
        self.assertEqual(book_config.drift(self.root, config)[0], [], "a declined file may go")

    def test_a_deleted_book_file_fails_unless_declined_or_accepted(self):
        (self.root / "devtools/checks/x.py").unlink()
        problems, _ = book_config.drift(self.root, {})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("devtools/checks/x.py: missing", problems[0])
        accepted = {"book": {"drift_accepted": [{"file": "devtools/checks/x.py", "reason": "until PR 5"}]}}
        problems, warnings = book_config.drift(self.root, accepted)
        self.assertEqual(problems, [])
        self.assertIn("missing, accepted for now (until PR 5)", warnings[0])

    def test_a_declined_defaults_or_rules_file_fails_and_is_still_compared(self):
        # Book D71: `declined` once let a deleted defaults file pass the drift check and empty the guard's lists.
        (self.root / ".claude/book/rules.md").unlink()
        config = {"book": {"declined": [".claude/book/rules.md", "devtools/factory/book-defaults.toml"]}}
        problems, _ = book_config.drift(self.root, config)
        text = " ".join(problems)
        self.assertIn("[book] declined names .claude/book/rules.md, which a project cannot decline", text)
        self.assertIn("[book] declined names devtools/factory/book-defaults.toml", text)
        self.assertIn(".claude/book/rules.md: missing", text)
        self.assertNotIn("list it under `[book] declined`", text)

    def test_an_accepted_entry_needs_a_reason(self):
        problems, _ = book_config.drift(self.root, {"book": {"drift_accepted": [{"file": "a.md"}]}})
        self.assertIn("drift_accepted a.md has no reason", problems[0])

    def test_no_lock_is_nothing_to_compare(self):
        (self.root / book_config.LOCK).unlink()
        self.assertEqual(book_config.drift(self.root, {}), ([], []))


if __name__ == "__main__":
    unittest.main()
