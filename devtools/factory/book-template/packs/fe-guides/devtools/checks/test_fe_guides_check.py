"""
Self-tests for /check-guides's script (.claude/skills/check-guides/scripts/guides_check.py), beside the
Book's own in test_checks.py (whose helpers they use). `factory book init` copies this file in with the
fe-guides pack, whatever the stack:

    python3 devtools/checks/run-self-tests.py devtools/checks
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_checks import HERE, RepoTestCase, run_main, setUpModule, tearDownModule  # noqa: E402,F401

GUIDES_CHECK = HERE.parents[1] / ".claude" / "skills" / "check-guides" / "scripts" / "guides_check.py"


def load_guides_check():
    spec = importlib.util.spec_from_file_location("guides_check", GUIDES_CHECK)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GuidesCheckTests(RepoTestCase):
    """Each candidate the script prints, on a small Book whose fixture guide carries one of each."""

    FACTORY = ('[book]\nstack = "dotnet"\n\n[fe_guides]\ninternal_error_codes = [\n'
               '  { code = "Outbox.*", reason = "the mail queue\'s own" },\n'
               '  { code = "Gone.Code", reason = "was returned once" },\n]\n')
    CONTRACTS = "\n".join([
        "# API contracts", "", "## Unversioned routes", "",
        "| Method | Path | Notes | Source |", "|---|---|---|---|",
        "| GET | `/health/live` | probe | `Configuration/Health.cs` |", "",
        "## Farms", "", "### Farms", "",
        "Prefix `/api/v1/farms` · source `src/App.API/Endpoints/Farms/FarmEndpoints.cs`", "",
        "| Method | Path | Auth | Request | Responses | Dispatches |", "|---|---|---|---|---|---|",
        "| GET | `/api/v1/farms` | `farms.list` | query: `page`:int, `search`:string | 200 `PagedResult<FarmDto>` "
        "| `ListFarms` |",
        "| POST | `/api/v1/farms` | `farms.create` | body: [`AddFarmRequest`](#addfarmrequest) "
        "| 201<br>400 `ValidationProblemDetails` | `AddFarm` |",
        "| DELETE | `/api/v1/farms/{id:guid}` | `farms.delete` | route: `id`:Guid | 204<br>409 `ProblemDetails` "
        "| `DeleteFarm` |",
        "| GET | `/api/v1/farms/lookup` | authenticated | — | 200 | `LookupFarms` |",
        "| PUT | `/api/v1/farms/{id:guid}` | `farms.edit` | route: `id`:Guid | 204 | `EditFarm` |", "",
        "## Request schemas", "", "### AddFarmRequest", "",
        "| Field | Type | Default |", "|---|---|---|", "| `Name` | `string` | |", "| `CountryId` | `int` | |", "",
        "## Response schemas", "", "### FarmDto", "",
        "| Field | Type |", "|---|---|", "| `Id` | `Guid` |", "| `Name` | `string` |", ""])
    QUESTIONS = ("# Questions\n\n## Still open\n\n| Q | What |\n|---|---|\n| [Q1](#q1) | x |\n\n#### Q1 — Default?\n\n"
                 "## Superseded\n\n## Resolved\n\n#### Q2 — Settled\n")
    DEFECTS = ("# Defects\n\n## Open\n\n| Id | Sev | Status | Defect |\n|---|---|---|---|\n| D5 | 🔵 | Open | x |\n\n"
               "## Fixed\n\n- **D4** — y. Fixed.\n")
    FARMS = "\n".join([
        "# Farms — front-end integration guide", "",
        "**Module:** Farms · **Base:** `/api/v1/farms` · **Last checked against the code:** 2026-11-01 (SHA)", "",
        "| Method | Path | Permission | Returns |", "|---|---|---|---|",
        '| `GET` | `/api/v1/farms` | `farms.list` | `200` + `{ "id": 123 }` |',
        "| `POST` | `/api/v1/farms` | `farms.view` | `201`, `403`, `418` |",
        "| `PUT` | `/{id}` | edit | `204` |",
        "| `GET` | `/api/v1/farms/old` | `farms.view` | `200` |", "",
        "Send `name` and `search`; the lookup is `GET /lookup`, and `POST /api/v1/farms/import` imports.", "",
        "Errors: `Farm.InUse`, `Farm.Vanished`, `Farm.Moved`, `Widget.App`.", "",
        "The default follows Q1.", "", "", "Q2 is still open.", "",
        "See [D9](../defects.md#d9). Orders move from D1 to D3.", "",
        "```json", '{ "name": "x", "legacyCode": 1 }', "```", "",
        "## Deviations", "", "| `GET` | `/api/v1/farms/export` | `farms.list` | `200` |", ""])
    SOURCE = {
        "src/App.API/Endpoints/Farms/FarmEndpoints.cs":
            'group.MapGet("/", List);\ngroup.MapPost("/", Add);\nreturn Error.Conflict("Farm.InUse", "in use");\n'
            'return Error.NotFound("Farm.Hidden", "x");\nCode = "Outbox.Full";\n'
            # A code held in a constant named for a code, or for its own last part, counts; a dotted name that is
            # not a code does not, though a guide naming it is not wrong either (D38).
            'public const string ErrorCode = "Farm.Taken";\nprivate const string ApplicationName = "Widget.App";\n'
            'public const string Moved = "Farm.Moved";\nstatic readonly string Locked = "Farm.Locked";\n',
        "src/App.API/Configuration/Health.cs": 'app.MapGet("/health/live", () => Results.Ok());\n',
        "src/App.API/Configuration/Extra.cs": 'app.MapGet("/secret", () => Results.Ok());\n',
        "src/App.Application/Features/Farms/AddFarm.cs": "public sealed record AddFarm(string Name, int CountryId);\n",
    }
    GUIDES = "docs/fe-integration-guides/"

    def setUp(self) -> None:
        super().setUp()
        self.script = load_guides_check()
        self.repo.write("devtools/factory/factory.toml", self.FACTORY)
        self.repo.write("docs/api/api-contracts.md", self.CONTRACTS)
        self.repo.write("docs/open-questions.md", self.QUESTIONS)
        self.repo.write("docs/defects.md", self.DEFECTS)
        for rel, text in self.SOURCE.items():
            self.repo.write(rel, text)
        self.repo.write(self.GUIDES + "api-conventions.md", "# API conventions\n\n**Base:** `/api/v1` · **Last "
                        "checked against the code:** 2026-11-01 (abc1234)\n\nPaging takes `{ \"page\": 1 }`; "
                        "probes at `/health/live`.\n")
        self.repo.write(self.GUIDES + "devices.md", "# Devices — front-end integration guide\n\n**Base:** "
                        "`/api/v1/devices` · **Last checked against the code:** 2026-11-01 (abc1234)\n\n"
                        "| | Path | Returns |\n|---|---|---|\n")
        self.repo.write(self.GUIDES + "offline-sync.md", "# Offline and sync\n\n**Base:** `/api/v1/<module>/changes`"
                        " · **Last checked against the code:** 2026-11-01 (abc1234)\n\nThe pull.\n")
        self.repo.write(self.GUIDES + "_template.md", "# <Module> — front-end integration guide\n\n| Method | Path "
                        "| Permission | Returns |\n|---|---|---|---|\n| `GET` | `/api/v1/nothing` | x | 200 |\n")
        self.repo.write(self.GUIDES + "farms.md", self.FARMS.replace("SHA", "0000000"))
        self.repo.commit("Base")
        self.base_sha = self.repo.git("rev-parse", "--short", "HEAD").strip()
        self.repo.write(self.GUIDES + "farms.md", self.FARMS.replace("SHA", self.base_sha))
        self.repo.commit("Checked")

    def run_check(self) -> dict:
        code, out = run_main(self.script.main, [str(self.repo.root), "--ref", "HEAD", "--json"])
        self.assertEqual(0, code, out)
        return json.loads(out)

    @staticmethod
    def findings(report: dict, guide: str) -> list[tuple[str, str]]:
        entry = next(g for g in report["guides"] if g["guide"].endswith("/" + guide))
        return [(f["kind"], f["text"]) for f in entry["findings"]]

    def kinds(self, report: dict, guide: str) -> list[str]:
        return [kind for kind, _ in self.findings(report, guide)]

    def test_routes_are_checked_against_the_contracts(self):
        found = self.findings(self.run_check(), "farms.md")
        self.assertIn(("R-STALE", "GET /api/v1/farms/old — not in docs/api/api-contracts.md"), found)
        self.assertTrue(any(k == "R-PERM" and "POST /api/v1/farms" in t and "farms.create" in t for k, t in found))
        self.assertFalse(any(k == "R-PERM" and t.startswith("PUT") for k, t in found), "a bare `edit` is farms.edit")
        statuses = [t for k, t in found if k == "R-STATUS"]
        self.assertEqual(1, len(statuses), found)
        self.assertIn("['418']", statuses[0], "an id in an example and a 403 are never statuses")
        self.assertTrue(any(k == "R-MENTION" and "import" in t for k, t in found), found)
        self.assertTrue(any(k == "R-MISSING" and "DELETE /api/v1/farms/{id:guid}" in t for k, t in found), found)
        self.assertFalse(any("lookup" in t for k, t in found if k == "R-MISSING"), "a relative mention documents it")
        self.assertFalse(any("export" in t for _, t in found), "a Deviations row is meant not to exist")

    def test_fields_codes_and_examples_are_checked(self):
        report = self.run_check()
        found = self.findings(report, "farms.md")
        missing = [t for k, t in found if k == "F-MISSING"]
        self.assertEqual(1, len(missing), found)
        self.assertIn("['countryId']", missing[0], "name and search are named; page is a convention's")
        self.assertEqual(['JSON key "legacyCode" — no field, parameter, convention or identifier in the source '
                          "spells it"], [t for k, t in found if k == "F-UNKNOWN"])
        self.assertEqual(["error code `Farm.Vanished` — the source never returns it"],
                         [t for k, t in found if k == "E-UNKNOWN"])
        self.assertEqual(["Farm.Hidden", "Farm.Locked", "Farm.Taken"],
                         [line.split(" ")[0] for line in report["unnamed_error_codes"]],
                         "Outbox.Full is internal; the guide names Farm.Moved and Widget.App (that an application's "
                         "name is no code is test_a_guide_need_not_name_a_dotted_constant_that_is_no_code's)")
        self.assertEqual(1, len(report["stale_internal_codes"]))
        self.assertIn("Gone.Code", report["stale_internal_codes"][0])

    def test_a_guide_need_not_name_a_dotted_constant_that_is_no_code(self):
        script = self.script
        self.assertEqual({"Widget.InUse"}, script.returned_codes('ERROR_CODE = "Widget.InUse";'))
        self.assertEqual({"Widget.NotFound"}, script.returned_codes('const string NotFound = "Widget.NotFound";'))
        self.assertEqual({"Widget.Gone"}, script.returned_codes('Error.NotFound(code: "Widget.Gone")'))
        self.assertEqual(set(), script.returned_codes('const string ApplicationName = "Widget.App";'))

    def test_ids_are_judged_against_the_ledgers(self):
        found = self.findings(self.run_check(), "farms.md")
        self.assertTrue(any(k == "ID-OPEN" and t.startswith("Q1 ") for k, t in found), found)
        self.assertTrue(any(k == "ID-STALE" and t.startswith("Q2 is resolved") for k, t in found), found)
        unknown = [t for k, t in found if k == "ID-UNKNOWN"]
        self.assertEqual(1, len(unknown), unknown)
        self.assertTrue(unknown[0].startswith("D9 "), "D1 and D3 are days, not citations")

    def test_the_guide_shape_and_the_template(self):
        report = self.run_check()
        self.assertIn("T-TABLE", self.kinds(report, "devices.md"))
        self.assertNotIn("T-TABLE", self.kinds(report, "offline-sync.md"), "a protocol guide has no module table")
        self.assertNotIn("T-TABLE", self.kinds(report, "farms.md"))
        self.assertFalse(any(g["guide"].endswith("_template.md") for g in report["guides"]))

    def test_unparsed_routes_coverage_and_response_fields(self):
        report = self.run_check()
        self.assertEqual(1, len(report["unparsed_routes"]), report["unparsed_routes"])
        self.assertIn("src/App.API/Configuration/Extra.cs", report["unparsed_routes"][0])
        self.assertIn("not applicable", report["coverage"])
        self.assertIn("field lists", report["response_fields"])

    def test_age_counts_source_commits_the_guide_has_not_seen(self):
        self.assertFalse([k for k in self.kinds(self.run_check(), "farms.md") if k.startswith("AGE")])
        self.repo.write("src/App.Application/Features/Farms/AddFarm.cs",
                        "public sealed record AddFarm(string Name, int CountryId, bool Active);\n")
        self.repo.commit("Change the module")
        found = self.findings(self.run_check(), "farms.md")
        self.assertTrue(any(k == "AGE" and "1 commit(s)" in t for k, t in found), found)
        self.repo.write(self.GUIDES + "farms.md", self.FARMS.replace("SHA", self.base_sha) + "\nMore.\n")
        self.repo.commit("Edit the guide without moving its line")
        self.assertIn("AGE-LINE", self.kinds(self.run_check(), "farms.md"))

    def test_a_missing_source_stops_with_could_not_check(self):
        self.repo.git("rm", "-q", "docs/api/api-contracts.md")
        self.repo.commit("No contracts")
        code, out = run_main(self.script.main, [str(self.repo.root), "--ref", "HEAD"])
        self.assertEqual(2, code)
        self.assertIn("could not check", out)

    def test_an_internal_code_without_a_reason_stops_the_run(self):
        self.repo.write("devtools/factory/factory.toml", '[fe_guides]\ninternal_error_codes = [{ code = "Outbox.*" }]\n')
        self.run_check()  # settings are read at the ref, like the guides: the checkout's edit is not seen yet
        self.repo.commit("Drop the reason")
        code, out = run_main(self.script.main, [str(self.repo.root), "--ref", "HEAD"])
        self.assertEqual(2, code)
        self.assertIn("gives no reason", out)

    def test_source_files_that_match_nothing_stop_the_run(self):
        self.repo.write("devtools/factory/factory.toml", '[fe_guides]\nsource_files = ["nowhere"]\n')
        self.repo.commit("Point at no source")
        code, out = run_main(self.script.main, [str(self.repo.root), "--ref", "HEAD"])
        self.assertEqual(2, code, "no source read is a check that did not run, never an empty E-UNNAMED")
        self.assertIn("match no file", out)

    def test_a_last_checked_commit_the_ref_does_not_hold_is_reported(self):
        self.repo.write(self.GUIDES + "farms.md", self.FARMS.replace("SHA", "fedcba9"))
        self.repo.commit("Name a commit that is not here")
        found = self.findings(self.run_check(), "farms.md")
        self.assertTrue(any(k == "AGE" and "fedcba9 is not in HEAD" in t for k, t in found), found)
