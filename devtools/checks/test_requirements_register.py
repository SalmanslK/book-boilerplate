"""Self-tests for requirements_register: the register's commit checks and the conversion of an old audit."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requirements_register as register  # noqa: E402

HEAD = "| Id | FSD | Requirement | Plan task | Tests | Status | Verified at | Note |\n|---|---|---|---|---|---|---|---|\n"
UNTRACED = "\n## Untraced behaviour\n\n| Id | Code | What it does | Status | Note |\n|---|---|---|---|---|\n"


class Checks(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        (self.root / register.FOLDER).mkdir(parents=True)

    def tearDown(self):
        self.folder.cleanup()

    def write(self, rows, untraced="", name="things.md", mark=""):
        (self.root / register.FOLDER / name).write_text(f"# Things\n\n{mark}\n\n{HEAD}{rows}{UNTRACED}{untraced}",
                                                        encoding="utf-8")

    def test_a_good_register_passes(self):
        self.write("| R-01 | §1 | A thing | T1 | `ThingTests` | VERIFIED | abc1234 2026-11-20 | — |\n"
                   "| R-02 | §2 | Another | — | — | CONTRADICTED | — | D12 |\n"
                   "| R-03 | §3 | Later | — | — | DEFERRED | — | release 2 |\n",
                   "| U-01 | `Things.cs` | Logs a count | ACCEPTED | Q4: kept |\n")
        self.assertEqual(register.problems(self.root, "R-"), ([], []))
        self.assertEqual(register.counts(self.root), {"things": {"VERIFIED": 1, "CONTRADICTED": 1, "DEFERRED": 1}})

    def test_each_broken_rule_fails(self):
        self.write("| R-01 | §1 | A | — | — | DONE | — | — |\n"
                   "| R-02 | §2 | B | — | — | CONTRADICTED | — | — |\n"
                   "| R-03 | §3 | C | — | — | DEFERRED | — | — |\n"
                   "| R-02 | §4 | D | — | — | VERIFIED | — | — |\n"
                   "| X-05 | §5 | E | — | — | BUILT | — | — |\n")
        failures, _ = register.problems(self.root, "R-")
        text = "\n".join(failures)
        for words in ("DONE is not one of", "CONTRADICTED but names no defect", "DEFERRED with no release",
                      "R-02 is also at", "VERIFIED but its Tests cell names no test", "does not start with"):
            self.assertIn(words, text)

    def test_a_converted_register_warns_of_a_verified_row_with_no_test(self):
        self.write("| R-01 | §1 | A | — | — | VERIFIED | — | — |\n| R-02 | §2 | B | — | — | REVERIFY | — | — |\n",
                   mark=register.CONVERTED)
        failures, warnings = register.problems(self.root)
        self.assertEqual(failures, [])
        self.assertEqual(len(warnings), 2)

    def test_a_table_with_other_columns_fails(self):
        (self.root / register.FOLDER / "odd.md").write_text("| Id | Status |\n|---|---|\n| R-1 | BUILT |\n",
                                                            encoding="utf-8")
        self.assertIn("columns", register.problems(self.root)[0][0])


AUDIT = """# Spec traceability

## Labs — FSD §2.7 — plan `docs/plans/labs.md`

| ID | FSD ref | Requirement | Plan ref | Code evidence | Test evidence | Status |
|---|---|---|---|---|---|---|
| FSD-2.7-R01 | §2.7.1 | Lists labs | `labs.md` §1 | `ListLabs.cs` | `LabsTests.Lists` | VERIFIED (traced) |
| FSD-2.7-R02 | §2.7.2 | Adds a lab | `labs.md` §2 | `AddLab.cs` | — | VERIFIED (traced) |
| FSD-2.7-R03 | §2.7.3 | Colours | — | — | — | NOT_APPLICABLE — FE rendering |
| FSD-2.7-R04 | §2.7.4 | Deletes | — | — | — | CONTRADICTED — D9 |
| FSD-2.7-R05 | §2.7.5 | Exports | — | — | — | UNVERIFIED |

### Untraced behaviour — Labs

| ID | Code evidence | Requirement | Status |
|---|---|---|---|
| U-LAB-1 | `LabAudit.cs` | Audits each change | ACCEPTED — Q3 |
"""


class Import(unittest.TestCase):
    def test_the_audit_converts_with_every_id_and_its_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "docs/Audit").mkdir(parents=True)
            (root / register.AUDIT).write_text(AUDIT, encoding="utf-8")
            (root / "docs/Audit/summary.md").write_text("See [labs](spec-traceability.md#labs).\n", encoding="utf-8")
            report, _ = register.import_command(root, False, "2026-11-23")
            self.assertIn("labs.md  5 requirement(s) (2 BUILT, 1 CONTRADICTED, 1 NOT_BACKEND, 1 VERIFIED), "
                          "1 untraced, 1 to review", report)
            self.assertFalse((root / register.FOLDER).exists(), "a report writes nothing")
            register.import_command(root, True, "2026-11-23")
            text = (root / register.FOLDER / "labs.md").read_text(encoding="utf-8")
            for ident in ("FSD-2.7-R01", "FSD-2.7-R05", "U-LAB-1"):
                self.assertIn(ident, text)
            self.assertIn("| LabsTests.Lists | VERIFIED |", text)
            self.assertIn("| FSD-2.7-R02 | §2.7.2 | Adds a lab | `labs.md` §2 | — | BUILT |", text)
            self.assertIn("`AddLab.cs`", (root / register.FOLDER / "evidence/labs.md").read_text(encoding="utf-8"))
            self.assertTrue((root / "docs/archive/spec-traceability-2026-11-23.md").is_file())
            self.assertIn("Retired", (root / register.AUDIT).read_text(encoding="utf-8"))
            self.assertEqual("See [labs](../archive/spec-traceability-2026-11-23.md#labs).\n",
                             (root / "docs/Audit/summary.md").read_text(encoding="utf-8"))
            self.assertEqual(register.problems(root)[0], [])
            self.assertIn("already holds a register", register.import_command(root, False, "2026-11-23")[0])

    def convert(self, audit: str) -> tuple[Path, str, bool]:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "docs/Audit").mkdir(parents=True)
        (root / register.AUDIT).write_text(audit, encoding="utf-8")
        report, ok = register.import_command(root, True, "2026-11-23")
        return root, report, ok

    def test_two_sections_for_one_module_keep_every_id(self):
        head = "| ID | FSD ref | Requirement | Plan ref | Code evidence | Test evidence | Status |\n|---|---|---|---|---|---|---|\n"
        audit = ("# Spec traceability\n\n## Labs — FSD §2.7\n\n" + head
                 + "| FSD-1 | §2.7.1 | Lists | — | `A.cs` | `LabsTests.A` | VERIFIED (traced) |\n"
                 + "| FSD-2 | §2.7.2 | Adds | — | `B.cs` | `LabsTests.B` | VERIFIED (traced) |\n"
                 + "\n## Labs — FSD §2.8\n\n" + head
                 + "| FSD-3 | §2.8.1 | Exports | — | `C.cs` | `LabsTests.C` | VERIFIED (traced) |\n")
        root, report, ok = self.convert(audit)
        self.assertTrue(ok, report)
        self.assertIn("labs.md  3 requirement(s) (3 VERIFIED)", report)
        self.assertIn("from 2 audit sections, merged in order: Labs — FSD §2.7; Labs — FSD §2.8", report)
        text = (root / register.FOLDER / "labs.md").read_text(encoding="utf-8")
        self.assertLess(text.index("FSD-1"), text.index("FSD-2"))
        self.assertLess(text.index("FSD-2"), text.index("FSD-3"))
        self.assertIn("sections *Labs — FSD §2.7* and *Labs — FSD §2.8*", text)
        evidence = (root / register.FOLDER / "evidence/labs.md").read_text(encoding="utf-8")
        for code in ("`A.cs`", "`B.cs`", "`C.cs`"):
            self.assertIn(code, evidence)
        self.assertEqual(register.id_problems(root, ["FSD-1", "FSD-2", "FSD-3"]), [])

    def test_the_written_register_is_checked_for_every_audit_id_once(self):
        root, report, ok = self.convert(AUDIT)
        self.assertTrue(ok, report)
        self.assertEqual(register.id_problems(root, ["FSD-2.7-R01", "U-LAB-1"]), [])
        self.assertEqual(register.id_problems(root, ["FSD-9"]), ["FSD-9 is in the register 0 time(s), not once"])
        path = root / register.FOLDER / "labs.md"
        text = path.read_text(encoding="utf-8")
        row = next(line for line in text.splitlines() if line.startswith("| FSD-2.7-R01 "))
        path.write_text(text.replace(row, row + "\n" + row), encoding="utf-8")
        self.assertEqual(register.id_problems(root, ["FSD-2.7-R01"]),
                         ["FSD-2.7-R01 is in the register 2 time(s), not once"])

    def test_a_pipe_in_a_cell_survives_the_round_trip(self):
        root, report, ok = self.convert(AUDIT.replace("| Lists labs |", "| Lists labs a\\|b |"))
        self.assertTrue(ok, report)
        self.assertEqual(register.problems(root)[0], [])

    def test_an_observed_row_with_no_test_is_counted_to_review(self):
        root, report, ok = self.convert(AUDIT.replace("| `LabsTests.Lists` | VERIFIED (traced) |",
                                                      "| — | VERIFIED (observed) |"))
        self.assertIn("2 to review", report)


# Book D74: the column headers two real audits use, which the import once read by their exact names.
VARIED = """# Spec traceability

## Farms — FSD Farm Management — plan `docs/plans/farms.md`

| ID | FSD ref | Requirement / plan item | Plan ref | Code evidence | Test evidence | Status |
|---|---|---|---|---|---|---|
| PLN-FARM-01 | plan §2 | Farms are soft-deleted | §2 | `DeleteFarm.cs:12` | `FarmTests.Soft` | VERIFIED (observed) |
| FSD-FARM-R01 | p.20 | Lists farms A \\| B | §1 | see [Q1](../open-questions.md) and [the legend](summary.md#paths) | — | PARTIAL — no paging |

### Untraced behaviour in this module

| ID | Code evidence | What it does | Nearest justification found | Status |
|---|---|---|---|---|
| UNT-FARM-001 | `FarmList.cs:9` | Sorts by name | plan §1 says "alphabetical" | UNTRACED — convention |

### Module-specific evidence on global rows

| ID | Requirement | Code evidence (Farms) | Test evidence | Note |
|---|---|---|---|---|
| ↳ FSD-GLB-R01 | Timestamps in UTC | `Farm.cs:5` | `UtcTests.Farm` | holds |
| FSD-FARM-R02 | Farm names are unique | `Farm.cs:9` | — | an index, no test |

#### Untraced behaviour — exports

| ID | Code evidence | What it does | Status |
|---|---|---|---|
| UNT-FARM-002 | `Export.cs:3` | Exports CSV | UNTRACED |

| Short | Full path |
|---|---|
| `Farm.cs` | `src/Farms/Farm.cs` |

## Rows added by the delta pass (2026-11-07, `origin/dev@449da03`, FSD V9)

| ID | FSD section / source | Requirement | Plan | Code | Test | Status |
|---|---|---|---|---|---|---|
| FSD-DELTA-R01 | Q29 | Every delete is soft | data §2 | `DeleteFarm.cs:72` | `HardDeleteTests.None` | VERIFIED (traced) |

Untraced behaviour:

| ID | Code evidence | What it does | Nearest justification | Status |
|---|---|---|---|---|
| UNT-DELTA-001 | `Lock.cs:3` | Takes a lock | none | UNTRACED |
"""


class VariedAudits(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "docs/Audit").mkdir(parents=True)
        (self.root / "docs/open-questions.md").write_text("# Questions\n", encoding="utf-8")
        (self.root / "docs/Audit/summary.md").write_text("# Summary\n\n## Paths\n", encoding="utf-8")
        (self.root / register.AUDIT).write_text(VARIED, encoding="utf-8")
        self.report, self.ok = register.import_command(self.root, True, "2026-11-23")
        self.farms = (self.root / register.FOLDER / "farms.md").read_text(encoding="utf-8")
        self.evidence = (self.root / register.FOLDER / "evidence/farms.md").read_text(encoding="utf-8")

    def rows(self, untraced: bool) -> dict[str, dict[str, str]]:
        return {row.cells["Id"]: row.cells for row in register.read(self.root).rows if row.untraced == untraced}

    def test_every_header_maps_by_meaning_and_no_requirement_is_untraced(self):
        self.assertTrue(self.ok, self.report)
        requirements, untraced = self.rows(False), self.rows(True)
        self.assertEqual(sorted(untraced), ["UNT-DELTA-001", "UNT-FARM-001", "UNT-FARM-002"])
        for ident in ("PLN-FARM-01", "FSD-FARM-R01", "FSD-FARM-R02", "FSD-DELTA-R01"):
            self.assertIn(ident, requirements)
            self.assertNotIn(requirements[ident]["Requirement"], ("", "—"), ident)
        self.assertEqual(requirements["PLN-FARM-01"]["Status"], "VERIFIED")
        self.assertEqual(requirements["PLN-FARM-01"]["Tests"], "FarmTests.Soft")
        self.assertEqual(requirements["FSD-DELTA-R01"]["FSD"], "Q29")
        self.assertEqual(requirements["FSD-DELTA-R01"]["Status"], "VERIFIED", "Code and Test columns are evidence")
        self.assertIn("Code: `DeleteFarm.cs:72` · Test: `HardDeleteTests.None`", "".join(
            p.read_text(encoding="utf-8") for p in (self.root / register.FOLDER / "evidence").glob("rows-*.md")))
        self.assertIn("Nearest justification found: plan §1 says \"alphabetical\"", untraced["UNT-FARM-001"]["Note"])
        self.assertEqual(untraced["UNT-FARM-001"]["What it does"], "Sorts by name")

    def test_a_table_with_no_status_and_its_supplements_keep_every_cell(self):
        requirements = self.rows(False)
        self.assertIn("an index, no test", requirements["FSD-FARM-R02"]["Note"])
        self.assertIn("review", requirements["FSD-FARM-R02"]["Note"], "no status in the audit: flagged to review")
        self.assertNotIn("↳ FSD-GLB-R01", requirements)
        self.assertIn("**↳ FSD-GLB-R01** (a supplement to that requirement) — Requirement: Timestamps in UTC · "
                      "Code evidence (Farms): `Farm.cs:5` · Test evidence: `UtcTests.Farm` · Note: holds",
                      self.evidence)
        self.assertIn("| `Farm.cs` | `src/Farms/Farm.cs` |", self.evidence, "a table with no ID column, kept")
        self.assertIn("1 supplement row(s) in the appendix", self.report)
        self.assertIn("1 audit table(s) with no ID column are kept whole", self.report)
        self.assertEqual(register.cell_problems(self.root, register.by_file(register.parse_audit(VARIED)),
                                                "docs/archive/spec-traceability-2026-11-23.md"), [])

    def test_a_cell_the_register_lost_fails_the_import(self):
        evidence = self.root / register.FOLDER / "evidence/farms.md"
        evidence.write_text(self.evidence.replace("`DeleteFarm.cs:12`", "gone"), encoding="utf-8")
        lost = register.cell_problems(self.root, register.by_file(register.parse_audit(VARIED)),
                                      "docs/archive/spec-traceability-2026-11-23.md")
        self.assertEqual(lost, ["PLN-FARM-01: its Code evidence cell is in neither the register nor the appendix"])

    def test_links_are_rebased_and_an_escaped_pipe_is_not_escaped_again(self):
        # Book D75: the appendix is one folder deeper than the audit, and `\\|` once became `\\\\|`.
        self.assertIn("Lists farms A \\| B", self.farms)
        self.assertNotIn("\\\\|", self.farms)
        self.assertIn("[Q1](../../open-questions.md)", self.evidence)
        self.assertIn("[the legend](../../Audit/summary.md#paths)", self.evidence)
        self.assertNotIn("CONFIRM docs/requirements", self.report)
        self.assertEqual(register.link_problems(self.root, ["docs/requirements/farms.md",
                                                            "docs/requirements/evidence/farms.md"]), [])
        self.assertEqual(register.problems(self.root)[0], [])

    def test_ids_off_the_prefix_are_named_once(self):
        # Book D84: each converted PLN- row once gave its own warning, on every check-book run.
        failures, warnings = register.problems(self.root, "FSD")
        self.assertEqual(failures, [])
        self.assertIn("docs/requirements/farms.md: 1 id(s) the audit gave do not start with [requirements] id_prefix "
                      "'FSD' (PLN 1)", warnings)
        self.assertFalse([w for w in warnings if "PLN-FARM-01 does not start" in w])
        self.assertIn("  id prefixes: FSD 3, PLN 1", self.report)
        root = self.root / "again"
        (root / "docs/Audit").mkdir(parents=True)
        (root / "devtools/factory").mkdir(parents=True)
        (root / "devtools/factory/factory.toml").write_text('[requirements]\nid_prefix = "FSD"\n', encoding="utf-8")
        (root / register.AUDIT).write_text(VARIED, encoding="utf-8")
        report, _ = register.import_command(root, False, "2026-11-23")
        self.assertIn("CONFIRM 1 requirement id(s) do not start with [requirements] id_prefix 'FSD' (PLN 1)", report)

    def test_the_archived_audits_own_links_still_resolve(self):
        # Book D83: the audit was copied to docs/archive/ verbatim, so its links to its siblings broke.
        archived = "docs/archive/spec-traceability-2026-11-23.md"
        text = (self.root / archived).read_text(encoding="utf-8")
        self.assertIn("[the legend](../Audit/summary.md#paths)", text)
        self.assertIn("[Q1](../open-questions.md)", text)
        self.assertEqual(register.link_problems(self.root, [archived]), [])

    def test_a_broken_link_is_named_and_the_import_is_not_ok(self):
        (self.root / "docs/open-questions.md").unlink()
        broken = register.link_problems(self.root, ["docs/requirements/evidence/farms.md"])
        self.assertEqual(len(broken), 1, broken)
        self.assertIn("links to ../../open-questions.md, which does not exist", broken[0])

    def test_a_pass_section_is_named_for_a_person(self):
        # Book D76: a dated delta pass became a register file the roll-up counted as a module.
        self.assertIn("CONFIRM rows-added-by-the-delta-pass-2026-11-07-origin-dev-449da03-fsd-v9.md: a pass, not "
                      "a module", self.report)
        self.assertNotIn("CONFIRM farms.md: a pass", self.report)
        self.assertTrue(register.is_pass("FSD V5 delta — Invite Email (2026-11-02)"))
        self.assertFalse(register.is_pass("Labs — FSD §2.7 — plan `docs/plans/labs.md`"))


# Book D81: rows with an unescaped pipe in a cell, as the two real audits have them; the import once read them by
# position, dropped the cells past the header and reported OK.
PIPES = """# Spec traceability

## Labels — FSD §2.13

| ID | FSD ref | Requirement | Plan ref | Code evidence | Test evidence | Status |
|---|---|---|---|---|---|---|
| FSD-L-R07 | §2.13.1 | Line 2: Breed · Reg # | §4 | `Label.cs:52` | `LabelTests.Two_lines` | VERIFIED (observed) · Line 2 is now *Breed | Reg # | Tattoo*: joined by a space |
| FSD-L-R38 | §2.13.2 | One event per match | §4 | `Repo.cs:89` (one `Where` with `||`) | `SearchTests.An_event_appears_once` | VERIFIED (observed) |
| FSD-L-R40 | §2.13.3 | Prints A | B | §4 | `Print.cs:1` | — | PARTIAL |
"""


class Pipes(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "docs/Audit").mkdir(parents=True)
        (self.root / register.AUDIT).write_text(PIPES, encoding="utf-8")
        self.report, self.ok = register.import_command(self.root, True, "2026-11-24")
        self.rows = {row.cells["Id"]: row.cells for row in register.read(self.root).rows}
        self.evidence = (self.root / register.FOLDER / "evidence/labels.md").read_text(encoding="utf-8")

    def test_a_pipe_in_emphasis_stays_in_its_cell(self):
        row = self.rows["FSD-L-R07"]
        self.assertEqual(row["Status"], "VERIFIED")
        self.assertEqual(row["Tests"], "LabelTests.Two_lines")
        self.assertIn("Line 2 is now *Breed \\| Reg # \\| Tattoo*: joined by a space", row["Note"])
        self.assertIn("CONFIRM FSD-L-R07: a cell holds an unescaped pipe in a code span or emphasis", self.report)

    def test_a_pipe_in_a_code_span_stays_in_its_cell(self):
        row = self.rows["FSD-L-R38"]
        self.assertEqual(row["Status"], "VERIFIED")
        self.assertEqual(row["Tests"], "SearchTests.An_event_appears_once")
        self.assertIn("Code evidence: `Repo.cs:89` (one `Where` with `||`)", self.evidence)

    def test_a_row_that_cannot_be_matched_is_kept_whole_and_the_import_is_not_ok(self):
        self.assertFalse(self.ok, self.report)
        self.assertIn("CONFIRM FSD-L-R40: 8 cells where the header has 7 - kept whole in the appendix; split it by "
                      "hand (write a pipe as \\|)", self.report)
        row = self.rows["FSD-L-R40"]
        self.assertEqual(row["Status"], "BUILT")
        self.assertIn("review: the audit's row has 8 cells where its header has 7", row["Note"])
        self.assertIn("- **FSD-L-R40** — the audit's row, kept whole (8 cells under a 7-column header): "
                      "| FSD-L-R40 | §2.13.3 | Prints A | B | §4 | `Print.cs:1` | — | PARTIAL |", self.evidence)
        modules = register.by_file(register.parse_audit(PIPES))
        archived = "docs/archive/spec-traceability-2026-11-24.md"
        self.assertEqual(register.cell_problems(self.root, modules, archived), [])
        path = self.root / register.FOLDER / "evidence/labels.md"
        path.write_text(self.evidence.replace("| Prints A | B |", "| Prints A |"), encoding="utf-8")
        self.assertEqual(register.cell_problems(self.root, modules, archived),
                         ["FSD-L-R40: 8 cells where the header has 7, and the appendix does not keep the row whole"])
        report, ok = self.report_only()
        self.assertFalse(ok, report)
        self.assertIn("CONFIRM FSD-L-R40: 8 cells", report)

    def report_only(self) -> tuple[str, bool]:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "docs/Audit").mkdir(parents=True)
        (root / register.AUDIT).write_text(PIPES, encoding="utf-8")
        return register.import_command(root, False, "2026-11-24")

    def test_each_cell_is_looked_for_under_its_own_id(self):
        modules = register.by_file(register.parse_audit(PIPES))
        path = self.root / register.FOLDER / "evidence/labels.md"
        moved = self.evidence.replace("Code evidence: `Repo.cs:89` (one `Where` with `||`) · ", "")
        path.write_text(moved + "- **FSD-L-R07** — Code evidence: `Repo.cs:89` (one `Where` with `||`)\n",
                        encoding="utf-8")
        self.assertEqual(register.cell_problems(self.root, modules, "docs/archive/spec-traceability-2026-11-24.md"),
                         ["FSD-L-R38: its Code evidence cell is in neither the register nor the appendix"])


class StatusHistory(unittest.TestCase):
    """Book D82: an audit's status cell that keeps its history once gave the row its first status."""

    def test_the_last_move_is_the_status(self):
        for cell, tests, status in (
                ("MISSING — D525 (added 2026-10-13) → **VERIFIED (observed, 2026-10-21)** — built", "`ATests.B`",
                 "VERIFIED"),
                ("VERIFIED (observed) → CONTRADICTED at V12 (D127) → VERIFIED (observed) (PR #140)", "`ATests.B`",
                 "VERIFIED"),
                ("VERIFIED (observed) -> VERIFIED (traced) -> PARTIAL (2026-11-05): no test", "`ATests.B`", "BUILT"),
                ("UNVERIFIED → **NOT_APPLICABLE (2026-10-21, FSD V25)** — gone from the field table", "", "NOT_BACKEND"),
                ("VERIFIED (observed) · **2026-11-22 (V31):** now *A | B* — CONTRADICTED (traced), [D616](x.md)",
                 "`ATests.B`", "CONTRADICTED"),
                ("VERIFIED (observed, 2026-11-05) — was MISSING (traced) → SA-034, fixed by D59", "`ATests.B`",
                 "VERIFIED"),
                ("MISSING -> VERIFIED (traced, 2026-11-04)", "—", "BUILT")):
            with self.subTest(cell=cell):
                self.assertEqual(register.convert_status(cell, tests)[0], status)

    def test_the_whole_history_is_kept_and_an_unverified_start_stays_to_review(self):
        status, flagged, rest, _ = register.convert_status("UNVERIFIED — waits on Q80 → PARTIAL (traced)", "")
        self.assertEqual((status, flagged), ("BUILT", True))
        self.assertEqual(rest, "UNVERIFIED — waits on Q80 → PARTIAL (traced)")
        self.assertEqual(register.convert_status("MISSING → VERIFIED (observed)", "")[:2], ("VERIFIED", True))

    def test_the_note_keeps_the_status_a_history_began_with(self):
        """Book D88: the Note began after the first status, so `MISSING → VERIFIED` kept only `→ VERIFIED`."""
        cell = "MISSING → **VERIFIED (observed)** (2026-11-01)"
        self.assertEqual(register.convert_status(cell, "`LabTests.B`")[:3],
                         ("VERIFIED", False, "MISSING → VERIFIED (observed) (2026-11-01)"))
        cell = "VERIFIED (observed) · 2026-11-22 (V31): two lines — CONTRADICTED (traced), [D616](x.md)"
        self.assertEqual(register.convert_status(cell, "`ATests.B`")[::2], ("CONTRADICTED", cell))
        # A cell that names one status still starts its Note after it.
        self.assertEqual(register.convert_status("VERIFIED (observed) — read 2026-11-01", "`ATests.B`")[2],
                         "read 2026-11-01")

    def test_a_move_written_in_words_is_the_status(self):
        """Book D89: only an arrow, or a dash before `WORD (traced|observed)`, was read as a move."""
        for cell, status in (
                ("VERIFIED (observed) · later: CONTRADICTED (traced), [D1](x.md)", "CONTRADICTED"),
                ("CONTRADICTED (traced), [D1](x.md); now VERIFIED (observed, 2026-11-05)", "VERIFIED"),
                ("PARTIAL — untested · 2026-10-21: stays PARTIAL", "BUILT"),
                ("MISSING · Became NOT_APPLICABLE at V25", "NOT_BACKEND")):
            with self.subTest(cell=cell):
                status_now, _, rest, unclear = register.convert_status(cell, "`LabTests.B`")
                self.assertEqual((status_now, rest, unclear), (status, cell, False))

    def test_a_cell_that_does_not_say_which_status_is_current_takes_the_last_and_is_unclear(self):
        cell = "VERIFIED (observed) — by Labs · 2026-11-22 (V31): the table says 3 — UNVERIFIED, the FSD disagrees"
        self.assertEqual(register.convert_status(cell, "`LabTests.B`"), ("BUILT", True, cell, True))
        # A status named in passing, or one the cell says it was, leaves the first status current.
        for cell, status in (
                ("VERIFIED (observed) — the code caps at 20 — was: MISSING — planned", "VERIFIED"),
                ("PARTIAL — farms VERIFIED (observed); recipients Blocked", "BUILT"),
                ("VERIFIED (observed) — same mechanism; the gaps (R03 PARTIAL there)", "VERIFIED"),
                ("VERIFIED (observed) → CONTRADICTED (traced), [D1](x.md) · 2026-10-21: CONTRADICTED (traced)",
                 "CONTRADICTED")):
            with self.subTest(cell=cell):
                self.assertEqual(register.convert_status(cell, "`LabTests.B`")[::3], (status, False))

    def check(self, cases, unclear: bool) -> None:
        for cell, status in cases:
            with self.subTest(cell=cell):
                found = register.convert_status(cell, "`LabTests.B`")
                self.assertEqual(found[::3], (status, unclear))
                if unclear:
                    self.assertEqual(found[2], cell, "the Note holds the whole cell")

    def test_a_status_no_move_settles_is_unclear_and_the_status_stays(self):
        """Book D92: a cell that named two statuses, neither a recognised move, got its first with no word."""
        self.check((("VERIFIED (observed), then PARTIAL since 2026-11", "VERIFIED"),
                    ("VERIFIED (observed) / CONTRADICTED", "VERIFIED"),
                    ("VERIFIED (observed) → PARTIAL / CONTRADICTED", "BUILT"),
                    ("VERIFIED (observed) - CONTRADICTED by V9", "VERIFIED"),
                    ("UNVERIFIED — Q17 is open; flips to VERIFIED on the answer", "BUILT"),
                    ("PARTIAL VERIFIED", "BUILT"),
                    ("PARTIAL — on retest VERIFIED", "BUILT"),
                    ("PARTIAL — the server half now VERIFIED", "VERIFIED")), True)

    def test_a_condition_is_no_move(self):
        """Book D92: `becomes PARTIAL if Q9 is refused` filed the row as if it had."""
        self.check([(cell, "VERIFIED") for cell in (
            "VERIFIED — becomes PARTIAL if Q9 is refused", "VERIFIED (observed) → PARTIAL if Q9 is refused",
            "VERIFIED (observed) · becomes PARTIAL when V9 lands", "VERIFIED (observed) · becomes PARTIAL once V9 lands",
            "VERIFIED (observed) — PARTIAL (traced) unless Q9 holds", "VERIFIED (observed) · stays PARTIAL until V9",
            "VERIFIED (observed); would become PARTIAL", "VERIFIED (observed); could become PARTIAL",
            "VERIFIED (observed); may become PARTIAL", "VERIFIED (observed) · becomes PARTIAL (if refused)",
            "VERIFIED (observed); if Q9 is refused, now PARTIAL", "VERIFIED (observed) · once D5 lands: PARTIAL (traced)",
            "VERIFIED (observed) · if refused → PARTIAL")], True)
        # A condition in the move's own aside, or the one the status before it waited on, leaves it a move.
        self.check((("MISSING until D5 → VERIFIED (observed) (2026-11-01, when D5 merged)", "VERIFIED"),
                    ("MISSING → VERIFIED (observed) — the message shows when the list is empty", "VERIFIED")), False)

    def test_a_cell_that_opens_with_words_is_read_by_the_same_rules(self):
        """Book D92: `was MISSING; now VERIFIED (observed)` was filed BUILT, as a cell with no status."""
        self.check((("was MISSING; now VERIFIED (observed)", "VERIFIED"),
                    ("was MISSING → VERIFIED (observed)", "VERIFIED"),
                    ("Re-read 2026-11: VERIFIED (observed)", "VERIFIED"),
                    ("was VERIFIED (observed); now CONTRADICTED (traced), [D1](x.md)", "CONTRADICTED")), False)
        for cell in ("was UNVERIFIED; now VERIFIED (observed)", "not VERIFIED", "farms VERIFIED (observed)",
                     "if Q9 is answered, VERIFIED", "read by hand"):
            with self.subTest(cell=cell):
                self.assertTrue(register.convert_status(cell, "`LabTests.B`")[1], "to review")
        self.assertEqual(register.convert_status("if Q9 is answered, VERIFIED", "`LabTests.B`")[0], "BUILT")
        self.check((("Produced By VERIFIED (observed); Freeze Date CONTRADICTED (traced) → [D549](x.md)", "BUILT"),),
                   True)

    def test_a_status_plainly_of_a_part_or_of_another_row_leaves_the_leading_one(self):
        self.check((("PARTIAL — farms VERIFIED", "BUILT"),
                    ("PARTIAL — transfers and errors VERIFIED (observed); recipients Blocked", "BUILT"),
                    ("NOT_APPLICABLE - FE text; server half VERIFIED (named test)", "NOT_BACKEND"),
                    ("VERIFIED (observed) — eligibility is FE (PARTIAL half)", "VERIFIED"),
                    ("VERIFIED (observed) — its message is R20 (CONTRADICTED → D538)", "VERIFIED"),
                    ("VERIFIED (observed) (PR #246; was CONTRADICTED at `4c6a42f2`)", "VERIFIED"),
                    ("VERIFIED (observed) - the old \"MISSING → Phase 4\" row is superseded", "VERIFIED")), False)
        self.check((("VERIFIED (observed) — farms PARTIAL", "VERIFIED"),
                    ("NOT_APPLICABLE — FE text; the server's 200 is VERIFIED (observed)", "NOT_BACKEND")), True)

    def test_the_import_names_a_files_unclear_status_cells_in_one_line(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "docs/Audit").mkdir(parents=True)
        cell = "VERIFIED (observed) · 2026-11-22: the FSD says 3 — UNVERIFIED, the FSD contradicts itself"
        audit = PIPES.replace("| `SearchTests.An_event_appears_once` | VERIFIED (observed) |",
                              f"| `SearchTests.An_event_appears_once` | **{cell}** |")
        audit += "| FSD-L-R41 | §2.13.4 | Prints C | §4 | `Print.cs:2` | `PrintTests.C` | VERIFIED / CONTRADICTED |\n"
        (root / register.AUDIT).write_text(audit, encoding="utf-8")
        report, _ = register.import_command(root, True, "2026-11-24")
        rows = {r.cells["Id"]: r.cells for r in register.read(root).rows}
        self.assertEqual((rows["FSD-L-R38"]["Status"], rows["FSD-L-R41"]["Status"]), ("BUILT", "VERIFIED"))
        self.assertTrue(rows["FSD-L-R38"]["Note"].startswith(cell), rows["FSD-L-R38"]["Note"])
        lines = [line for line in report.splitlines() if "does not settle which is current" in line]
        self.assertEqual(len(lines), 1, report)
        self.assertIn("CONFIRM labels.md: 2 row(s)", lines[0])
        self.assertTrue(lines[0].endswith("FSD-L-R38 BUILT, FSD-L-R41 VERIFIED"), lines[0])
        self.assertNotIn("its Status cell is in neither", report)

    def test_the_real_shape_keeps_its_defect(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "docs/Audit").mkdir(parents=True)
        (root / "docs/defects.md").write_text("# Defects\n", encoding="utf-8")
        audit = PIPES.replace("Tattoo*: joined by a space |", "Tattoo*: joined by a space — CONTRADICTED (traced), "
                                                             "[D616](../defects.md) |")
        (root / register.AUDIT).write_text(audit, encoding="utf-8")
        report, _ = register.import_command(root, True, "2026-11-24")
        row = {r.cells["Id"]: r.cells for r in register.read(root).rows}["FSD-L-R07"]
        self.assertEqual(row["Status"], "CONTRADICTED")
        self.assertIn("— CONTRADICTED (traced), [D616](../defects.md)", row["Note"])
        self.assertNotIn("CONFIRM FSD-L-R07: its", report)
        self.assertFalse([p for p in register.problems(root)[1] if "FSD-L-R07" in p], "names its defect")


if __name__ == "__main__":
    unittest.main()
