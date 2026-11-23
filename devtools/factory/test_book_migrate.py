"""Self-tests for book_migrate: the 1.6 move of the Book's common text and settings out of a project's files."""

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import book_migrate  # noqa: E402

COMMON = """# Acme

## Branches

- **Never push to a protected branch.**

## Where knowledge lives

| Topic | Owner |
|---|---|
| A defect | `docs/defects.md` |
"""

PROJECT = """# Acme

The product, in a line.

## Branches

- **Never push to a protected branch.**
- `dev` deploys to DEV.

## Where knowledge lives

| Topic | Owner |
|---|---|
| A defect | `docs/defects.md` |
| Smoke tests | `docs/smoke.md` |

## Private settings

- **Never print a secret.**
"""


class SplitInstructions(unittest.TestCase):
    def split(self, project=PROJECT, common=COMMON):
        return book_migrate.split_instructions(project, book_migrate.block_texts(common), ["@.claude/book/rules.md"],
                                               "# This project's own rules")

    def test_the_books_blocks_go_and_the_projects_stay(self):
        text, moved, kept, sections = self.split()
        self.assertNotIn("Never push to a protected branch", text)
        self.assertNotIn("| A defect |", text)
        for own in ("`dev` deploys to DEV.", "| Smoke tests |", "Never print a secret.", "The product, in a line."):
            self.assertIn(own, text)
        self.assertTrue(moved >= 2 and kept >= 3)
        self.assertEqual(sections, ["Branches", "Where knowledge lives", "Private settings"])

    def test_a_table_keeps_its_header_while_a_row_stays(self):
        text = self.split()[0]
        self.assertIn("| Topic | Owner |\n|---|---|\n| Smoke tests |", text)

    def test_the_import_follows_the_preamble(self):
        text = self.split()[0]
        self.assertLess(text.index("The product, in a line."), text.index("@.claude/book/rules.md"))
        self.assertLess(text.index("@.claude/book/rules.md"), text.index("# This project's own rules"))

    def test_a_section_left_empty_goes(self):
        text = self.split(PROJECT.replace("- `dev` deploys to DEV.\n", ""))[0]
        self.assertNotIn("## Branches", text)

    def test_a_section_the_new_skeleton_keeps_comes_back_when_all_of_it_moved(self):
        slot = "<!-- " + "FILL: a branch rule of this project's own -->"  # split, so verify's N20 finds no slot here
        skeleton = f"# Acme\n\n## Branches\n\n{slot}\n"
        text = book_migrate.split_instructions(PROJECT.replace("- `dev` deploys to DEV.\n", ""),
                                               book_migrate.block_texts(COMMON), ["@.claude/book/rules.md"],
                                               "# This project's own rules", skeleton)[0]
        self.assertIn(f"## Branches\n\n{slot}", text)

    def test_a_file_with_no_section_keeps_its_own_lines_under_the_title(self):
        # The dotnet pack's src/CLAUDE.md: a title, the Book's rules and the project's, and no `## ` section.
        common = "# Acme — source\n\n- **Code goes in a layer.**\n"
        project = "# Acme — source\n\n- **Code goes in a layer.**\n- Our rule: name jobs `*Job`.\n"
        text = book_migrate.split_instructions(project, book_migrate.block_texts(common),
                                               ["@../.claude/book/dotnet/src.md"], "# This project's own rules here")[0]
        self.assertEqual(text, "# Acme — source\n\n@../.claude/book/dotnet/src.md\n\n# This project's own rules here"
                               "\n\n- Our rule: name jobs `*Job`.\n")

    def test_a_reworded_rule_stays(self):
        text = self.split(PROJECT.replace("Never push to a protected branch.", "Never push to `main`."))[0]
        self.assertIn("Never push to `main`.", text)


DEFAULTS = tomllib.loads("""
[book]
size_warning = 400
roles = ["dev", "qa"]

[guard]
read_only = ["requirements/"]

[[code_rules]]
id = "no-print"
pattern = "print\\\\("
""")


class SplitSettings(unittest.TestCase):
    TEMPLATE = book_migrate.template_comments(["# The   default\n[book]\nsize_warning = 400\n"])

    def split(self, text, defaults=DEFAULTS):
        return book_migrate.split_settings(text, defaults, "2026-11-23", ["AGENTS.md"], self.TEMPLATE)

    def test_a_key_equal_to_the_default_goes_with_the_templates_comment(self):
        text = '[book]\nname = "Acme"\n# the default\nsize_warning = 400\nroles = ["dev", "qa"]\n'
        new, dropped, notes = self.split(text.replace("# the default", "# The default"))
        self.assertNotIn("size_warning", new)
        self.assertNotIn("# The default", new)
        self.assertIn('name = "Acme"', new)
        self.assertEqual(sorted(dropped), ["book.roles", "book.size_warning"])
        self.assertFalse(any("comment" in note for note in notes), notes)

    def test_a_projects_own_comment_above_a_default_key_is_kept_and_named(self):
        text = ('[book]\nname = "Acme"\n# PROJECT-COMMENT: the client confirmed BE is ours (Q7)\n'
                'size_warning = 400\n')
        new, dropped, notes = self.split(text)
        self.assertIn("# PROJECT-COMMENT: the client confirmed BE is ours (Q7)", new)
        self.assertNotIn("size_warning", new)
        self.assertEqual(dropped, ["book.size_warning"])
        self.assertIn("CONFIRM factory.toml: a comment above book.size_warning was kept", "\n".join(notes))

    def test_the_split_leaves_no_orphaned_or_misleading_comment(self):
        # Book D78: a template comment naming a dropped key, one left with nothing under it, and the project's
        # kept comment reading as the new keys' description.
        template = ("[book]\n# The product's title; `roles` lists who works here.\ntitle = \"x\"\nroles = []\n\n"
                    "# [[code_rules]] - the pack's rules.\n# Set the names to the project's own.\n\n"
                    "[[code_rules]]\nid = \"no-print\"\npattern = \"x\"\n")
        comments = book_migrate.template_comments([template])
        text = ('[book]\nname = "Acme"\n# The product\'s title; `roles` lists who works here.\ntitle = "Acme"\n'
                'roles = ["dev", "qa"]\n# PROJECT-COMMENT-1: why our size\nsize_warning = 400\n\n'
                "# [[code_rules]] - the pack's rules.\n# Set the names to the project's own.\n\n"
                '[[code_rules]]\nid = "no-print"\npattern = "print\\\\("\n')
        new, dropped, _ = book_migrate.split_settings(text, DEFAULTS, "2026-11-23", [], comments)
        self.assertIn("book.roles", dropped)
        self.assertNotIn("`roles`", new)
        self.assertNotIn("the pack's rules", new)
        self.assertIn('title = "Acme"', new)
        self.assertIn("# PROJECT-COMMENT-1: why our size\n\n# The day this Book took", new)

    def test_a_template_comment_with_a_placeholder_goes_with_its_key(self):
        comments = book_migrate.template_comments(['# smoke = [{ email = "{{env_prefix}}..._EMAIL" }]\n'])
        text = '[book]\nname = "Acme"\n# smoke = [{ email = "ACME_..._EMAIL" }]\nsize_warning = 400\n'
        new, _, notes = book_migrate.split_settings(text, DEFAULTS, "2026-11-23", [], comments)
        self.assertNotIn("ACME_..._EMAIL", new)
        self.assertFalse(any("comment" in note for note in notes), notes)

    def test_a_kept_comment_keeps_its_table(self):
        new, _, notes = self.split('[book]\nname = "Acme"\n\n[guard]\n# ours: see Q3\nread_only = ["requirements/"]\n')
        self.assertIn("[guard]\n# ours: see Q3", new)
        tomllib.loads(new)

    def test_a_header_whose_sub_tables_all_went_goes(self):
        defaults = dict(DEFAULTS, requirements={"sources": {"fsd_link_label": "FSD"}})
        new, _, _ = self.split('[book]\nname = "Acme"\n\n[requirements]\n\n[requirements.sources]\n'
                               'fsd_link_label = "FSD"\n\n[review]\nx = 1\n', defaults)
        self.assertNotIn("[requirements", new)
        self.assertIn("[review]\nx = 1", new)

    def test_a_different_value_stays(self):
        new, _, _ = self.split('[book]\nname = "Acme"\nsize_warning = 600\n')
        self.assertIn("size_warning = 600", new)

    def test_a_default_code_rule_goes_by_its_id(self):
        text = '[book]\nname = "Acme"\n\n[[code_rules]]\nid = "no-print"\npattern = "print\\\\("\n'
        new, dropped, _ = self.split(text)
        self.assertNotIn("code_rules", new)
        self.assertIn("[[code_rules]] no-print", dropped)

    def test_a_longer_list_is_kept_and_named(self):
        new, _, notes = self.split('[book]\nname = "Acme"\n\n[guard]\nread_only = ["requirements/", "docs/x/"]\n')
        self.assertIn('"docs/x/"', new)
        self.assertTrue(any("[extend.guard] read_only" in note for note in notes))

    def test_the_new_book_keys_are_added(self):
        new = tomllib.loads(self.split('[book]\nname = "Acme"\n')[0])
        self.assertEqual(new["book"]["type_source_from"], "2026-11-23")
        self.assertEqual([entry["file"] for entry in new["book"]["drift_accepted"]], ["AGENTS.md"])

    def test_an_emptied_table_goes(self):
        new, _, _ = self.split('[book]\nname = "Acme"\n\n[guard]\nread_only = ["requirements/"]\n')
        self.assertNotIn("[guard]", new)


class Plan(unittest.TestCase):
    """What the 1.6 move's report says about factory.toml."""

    DEFAULTS_TEXT = ('[book]\nsize_warning = 400\nroles = ["dev", "qa"]\n\n[guard]\nread_only = ["requirements/"]\n'
                     '\n[[code_rules]]\nid = "no-print"\npattern = "x"\n\n[[code_rules]]\nid = "no-sleep"\npattern = "y"\n')

    def plan(self, settings: str, accepted=("AGENTS.md",), installed=None, files=None):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / "devtools/factory").mkdir(parents=True)
            (target / "devtools/factory/factory.toml").write_text(settings, encoding="utf-8")
            for rel, text in (files or {}).items():
                (target / rel).parent.mkdir(parents=True, exist_ok=True)
                (target / rel).write_text(text, encoding="utf-8")
            return book_migrate.plan(target, tomllib.loads(settings), {},
                                     {"devtools/factory/book-defaults.toml": self.DEFAULTS_TEXT.encode()},
                                     "Acme", list(accepted), today="2026-11-23", installed_settings=installed)[1]

    def test_the_split_line_names_the_book_keys_it_adds(self):
        notes = "\n".join(self.plan('[book]\nname = "Acme"\n'))
        self.assertIn("[book] type_source_from = 2026-11-23; 1 file(s) in [book] drift_accepted", notes)

    def test_a_second_run_does_not_claim_the_keys_again(self):
        settings = ('[book]\nname = "Acme"\ntype_source_from = "2026-11-20"\ndrift_accepted = [\n'
                    '    { file = "AGENTS.md", reason = "r" },\n]\n')
        # Nothing to move, so no SPLIT line at all (Book D85), and never one claiming the keys again.
        self.assertEqual([line for line in self.plan(settings) if "SPLIT" in line and "factory.toml" in line], [])
        split = next(line for line in self.plan(settings + 'size_warning = 400\n')
                     if "SPLIT" in line and "factory.toml" in line)
        self.assertIn("1 key(s) equal to the Book's defaults go", split)
        self.assertNotIn("type_source_from", split)
        self.assertNotIn("drift_accepted", split)

    def test_a_later_run_does_not_write_drift_accepted_again_once_a_person_removed_it(self):
        # Book D91: the next --apply, while a CONFLICT held the version, wrote the removed list back.
        notes = self.plan('[book]\nname = "Acme"\ntype_source_from = "2026-11-20"\n')
        self.assertFalse([line for line in notes if "drift_accepted" in line or "SPLIT" in line], notes)

    def test_each_default_the_old_file_did_not_hold_is_named(self):
        # The project had deleted size_warning and the no-sleep rule; the defaults bring both back.
        notes = "\n".join(self.plan('[book]\nname = "Acme"\nroles = ["dev"]\n\n[guard]\nread_only = []\n\n'
                                    '[[code_rules]]\nid = "no-print"\npattern = "z"\n'))
        self.assertIn("the project had removed 1 setting(s) the Book's defaults now give - book.size_warning;", notes)
        self.assertIn("removed 1 code rule(s) the Book's defaults now give - no-sleep; to switch one off", notes)
        self.assertIn("`enabled = false`", notes)

    def test_nothing_is_named_when_the_old_file_held_every_default(self):
        notes = "\n".join(self.plan('[book]\nsize_warning = 1\nroles = []\n\n[guard]\nread_only = []\n\n'
                                    '[[code_rules]]\nid = "no-print"\npattern = "z"\n\n'
                                    '[[code_rules]]\nid = "no-sleep"\npattern = "z"\n'))
        self.assertNotIn("had removed", notes)

    def test_only_what_the_installed_release_wrote_counts_as_removed(self):
        # Book D77: a key new in 1.6, or one 1.5's template never wrote, was never the project's to remove.
        installed = tomllib.loads('[book]\nsize_warning = 1500\n\n[[code_rules]]\nid = "no-print"\npattern = "x"\n')
        notes = "\n".join(self.plan('[book]\nname = "Acme"\n', installed=installed))
        self.assertIn("removed 1 setting(s) the Book's defaults now give - book.size_warning;", notes)
        self.assertIn("removed 1 code rule(s) the Book's defaults now give - no-print;", notes)
        self.assertNotIn("roles", notes.split("now give")[1].split(";")[0])
        self.assertNotIn("no-sleep", notes)

    def test_a_second_report_repeats_no_removed_default_and_keeps_the_import_note(self):
        # Book D77: after the first --apply the file is split and docs/requirements/ exists (its .gitkeep).
        audit = {"docs/Audit/spec-traceability.md": "# Spec traceability\n", "docs/requirements/.gitkeep": ""}
        first = "\n".join(self.plan('[book]\nname = "Acme"\n', files=audit))
        self.assertIn("had removed", first)
        split = '[book]\nname = "Acme"\ntype_source_from = "2026-11-23"\n'
        second = "\n".join(self.plan(split, files=audit))
        self.assertNotIn("had removed", second)
        self.assertIn("requirements import", second)
        converted = dict(audit, **{"docs/requirements/labs.md": "# Labs\n"})
        self.assertNotIn("requirements import", "\n".join(self.plan(split, files=converted)))

    def test_each_run_this_line_goes_once_its_work_is_done(self):
        # Book D90: the four lines were printed on every run, the work done or not.
        split = '[book]\nname = "Acme"\ntype_source_from = "2026-11-23"\n'
        accepted = split + 'drift_accepted = [\n    { file = "AGENTS.md", reason = "r" },\n]\n'
        stale = "# Devtools\n\n<!-- rules-table:start -->\n| x | `CLAUDE.md` |\n<!-- rules-table:end -->\n"
        undone = {"docs/open-questions.md": "# Q\n\n## Still open\n\n#### Q1 — A\n\n**Recommendation:** x\n",
                  "docs/defects.md": DEFECTS, "devtools/README.md": stale,
                  "docs/Audit/spec-traceability.md": "# Spec traceability\n"}
        done = {"docs/open-questions.md": "# Q\n\n## Still open\n\n#### Q1 — A\n\n**State:** OPEN · NOT POSTED\n",
                "docs/defects.md": book_migrate.classify(DEFECTS)[0],
                "devtools/README.md": stale.replace("`CLAUDE.md`", "`.claude/book/rules.md`"),
                "docs/Audit/spec-traceability.md": "# Spec traceability\n",
                "docs/requirements/labs.md": "# Labs\n"}
        lines = ("/book:check-sources", "book defects classify", "check-rules.py --write",
                 "each file in [book] drift_accepted", "requirements import")
        first = "\n".join(self.plan('[book]\nname = "Acme"\n', files=undone))  # the run that splits
        again = "\n".join(self.plan(accepted, files=undone))
        for line in lines:
            with self.subTest(line=line):
                self.assertIn(line, first)
                self.assertIn(line, again)
        for work, files, settings in (
                ("/book:check-sources", dict(undone, **{"docs/open-questions.md": done["docs/open-questions.md"]}),
                 accepted),
                ("book defects classify", dict(undone, **{"docs/defects.md": done["docs/defects.md"]}), accepted),
                ("check-rules.py --write", dict(undone, **{"devtools/README.md": done["devtools/README.md"]}),
                 accepted),
                ("each file in [book] drift_accepted", undone, split),
                ("each file in [book] drift_accepted", undone, split + "drift_accepted = []\n"),
                ("requirements import", dict(undone, **{"docs/requirements/labs.md": "# Labs\n"}), accepted)):
            with self.subTest(done=work, settings=settings):
                notes = "\n".join(self.plan(settings, files=files))
                self.assertNotIn(work, notes)
                self.assertEqual(sum(line in notes for line in lines), len(lines) - 1, notes)
        self.assertFalse([line for line in self.plan(split, files=done) if "CONFIRM  run" in line
                          or "drift_accepted: move" in line or "accepted for now" in line])


DEFECTS = """# Defects

## Open

| Id | Sev | Status | Defect | Where | Found by | Note |
|---|---|---|---|---|---|---|
| D7 | High | Open | Wrong total | `Totals` | QA, build 12 | Class: `api-contract` · found 2026-11-02 |
| D8 | Low | Open | Odd label | `Labels` | someone | none |
"""


class ClassifyDefects(unittest.TestCase):
    def test_type_and_source_columns_replace_found_by(self):
        new, report = book_migrate.classify(DEFECTS)
        self.assertIn("| Id | Sev | Status | Type | Source | Defect | Where | Note |", new)
        self.assertIn("| D7 | High | Open | spec-divergence | qa: build 12 | Wrong total |", new)
        self.assertTrue(report[0].startswith("PROPOSED D7"))

    def test_a_row_with_nothing_to_go_on_is_a_guess_to_confirm(self):
        new, report = book_migrate.classify(DEFECTS)
        self.assertIn("| D8 | Low | Open | bug | developer: someone |", new)
        self.assertTrue(report[1].startswith("CONFIRM  D8"))

    def test_an_escaped_pipe_stays_in_its_cell_and_a_code_span_pipe_splits_as_it_renders(self):
        # One splitter for every Book table (Book D73): GitHub-style Markdown splits a code span's bare pipe.
        rows = ("| D1 | High | Open | Login fails `a\\|b` Class: `access-control` | src/Auth | PR review, #12 | n1 |\n"
                "| D2 | Low | Open | Totals a\\|b wrong | `Totals` | QA, build 3 | n2 |\n"
                "| D3 | Low | Open | Login fails `a|b` | src/Auth | PR review, #12 | n3 |\n")
        self.assertEqual(book_migrate.cells(rows.splitlines()[0])[3], "Login fails `a\\|b` Class: `access-control`")
        self.assertEqual(book_migrate.cells(rows.splitlines()[1])[3], "Totals a\\|b wrong")
        new, report = book_migrate.classify(DEFECTS.split("| D7")[0] + rows)
        self.assertIn("| D1 | High | Open | security | code-review: #12 | Login fails `a\\|b` Class: `access-control` "
                      "| src/Auth | n1 |", new)
        self.assertIn("| D2 | Low | Open | bug | qa: build 3 | Totals a\\|b wrong | `Totals` | n2 |", new)
        self.assertTrue(report[0].startswith("PROPOSED D1"), report)
        self.assertIn(rows.splitlines()[2], new.splitlines(), "a row its pipes misalign is left as it is")
        self.assertIn("CONFIRM  D3: 8 cells where the table has 7", report[-1])

    def test_a_row_whose_cells_do_not_match_the_header_is_left_and_named(self):
        row = "| D9 | Low | Open | a | b plain pipe | x | QA, build 1 | n |"
        new, report = book_migrate.classify(DEFECTS + row + "\n")
        self.assertIn(row, new.splitlines())
        self.assertIn("CONFIRM  D9: 8 cells where the table has 7 - left as it is", report[-1])

    def test_an_unclosed_backtick_is_plain_text(self):
        self.assertEqual(book_migrate.cells("| a ` b | c |"), ["a ` b", "c"])

    def test_a_ledger_with_the_columns_is_left(self):
        self.assertIsNone(book_migrate.classify(book_migrate.classify(DEFECTS)[0])[0])

    def test_the_command_writes_only_with_apply(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / "docs").mkdir()
            path = target / "docs" / "defects.md"
            path.write_text(DEFECTS, encoding="utf-8")
            book_migrate.classify_command(target, False)
            self.assertEqual(path.read_text(encoding="utf-8"), DEFECTS)
            book_migrate.classify_command(target, True)
            self.assertIn("| Type | Source |", path.read_text(encoding="utf-8"))


class Applies(unittest.TestCase):
    def test_only_an_upgrade_across_1_6_0_moves(self):
        self.assertTrue(book_migrate.applies("1.5.0", "1.6.0"))
        self.assertTrue(book_migrate.applies("1.5.1", "1.7.0"))
        self.assertFalse(book_migrate.applies("1.6.0", "1.6.1"))
        self.assertFalse(book_migrate.applies("1.4.0", "1.5.1"))


if __name__ == "__main__":
    unittest.main()
