"""
Self-tests for the fe-guides pack's check and skill script, beside the Book's own in test_checks.py (whose
helpers they use). `factory book init` copies this file in with the pack, whatever the stack:

    python3 devtools/checks/run-self-tests.py devtools/checks
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_checks import HERE, RepoTestCase, load, run_main, setUpModule, tearDownModule  # noqa: E402,F401

fe_guides = load("check-fe-guides")


class FeGuidesTests(RepoTestCase):
    """A changed route row needs its owner guide changed, with its Last checked line moved."""

    ROWS = {
        "Companies": ("/api/v1/companies", ["| GET | `/api/v1/companies/{id:long}` | `companies.view` | — | 200 |",
                                            "| POST | `/api/v1/companies` | `companies.create` | body: [`AddCompanyRequest`](#addcompanyrequest) | 201 |"]),
        "Farms": ("/api/v1/farms", ["| GET | `/api/v1/farms/{id:guid}` | `farms.view` | — | 200 |",
                                    "| POST | `/api/v1/farms/changes` | `farms.edit` | — | 200 |",
                                    "| GET | `/api/v1/farms` | `farms.list` | — | 200 `PagedResult<FarmDto>` |"]),
        "Order Fulfilment — Packing": ("/api/v1/order-fulfilment", ["| GET | `/api/v1/order-fulfilment/packing/{id:long}` | `x` | — | 200 |"]),
        "Order Fulfilment - Shipping": ("/api/v1/order-fulfilment", ["| GET | `/api/v1/order-fulfilment/shipping/{id:long}` | `x` | — | 200 |"]),
        "Preferences": ("/api/v1", ["| GET | `/api/v1/date-formats` | authenticated | — | 200 |"]),
    }
    SCHEMA = "| Field | Type |\n|---|---|\n| `name` | string |\n"

    RESPONSES = {"PagedResult": "| Field | Type |\n|---|---|\n| `Items` | `IReadOnlyList<T>` |\n",
                 "FarmDto": "| Field | Type |\n|---|---|\n| `Name` | `string` |\n| `Address` | `AddressDto` |\n",
                 "AddressDto": "| Field | Type |\n|---|---|\n| `City` | `string` |\n"}

    def contracts(self, rows: dict | None = None, schema: str = SCHEMA, responses: dict | None = None) -> str:
        rows = self.ROWS if rows is None else rows
        out = ["# API contracts", "", "## How to read a row", "", "Prose.", ""]
        for heading, (prefix, lines) in rows.items():
            out += [f"## {heading.split(' ')[0]}", "", f"### {heading}", "", f"Prefix `{prefix}` · source `x.cs`", "",
                    "| Method | Path | Auth | Request | Responses |", "|---|---|---|---|---|", *lines, ""]
        out += ["## Request schemas", "", "### AddCompanyRequest", "", "`record` · `src/A.cs`", "", schema,
                "## Response schemas", ""]
        for name, table in (self.RESPONSES if responses is None else responses).items():
            out += [f"### {name}", "", "`record` · `src/Dtos.cs`", "", table]
        return "\n".join(out)

    @staticmethod
    def guide(title: str, base: str, checked: str = "2026-11-01 (abc1234)", body: str = "Text.") -> str:
        return (f"# {title} — front-end integration guide\n\n**Module:** {title} · **Base:** {base} · "
                f"**Last checked against the code:** {checked}\n\n{body}\n")

    def setUp(self) -> None:
        super().setUp()
        self.repo.write(fe_guides.CONTRACTS, self.contracts())
        self.repo.write(fe_guides.GUIDES + "api-conventions.md", self.guide("API conventions", "`/api/v1`"))
        self.repo.write(fe_guides.GUIDES + "companies-and-locations.md", self.guide("Companies and Locations", "`/api/v1/companies`"))
        self.repo.write(fe_guides.GUIDES + "farms.md", self.guide("Farms", "`/api/v1/farms`"))
        self.repo.write(fe_guides.GUIDES + "offline-sync.md", self.guide("Offline and sync", "`/api/v1/<module>/changes`"))
        self.repo.write(fe_guides.GUIDES + "order-fulfilment-packing.md",
                        self.guide("Order Fulfilment — Packing", "`/api/v1/order-fulfilment`"))
        self.repo.write(fe_guides.GUIDES + "order-fulfilment-shipping.md",
                        self.guide("Order Fulfilment — Shipping", "`/api/v1/order-fulfilment`"))
        self.repo.write(fe_guides.GUIDES + "profile-and-preferences.md",
                        self.guide("My Profile and Preferences", "`/api/v1/profile`"))
        self.repo.write("devtools/factory/factory.toml", "")
        self.repo.commit("Base")
        self.repo.git("checkout", "-q", "-b", "work")

    def check(self, base: str = "main", env: dict[str, str] | None = None) -> tuple[int, str]:
        return run_main(fe_guides.main, ["--base", base], root=self.repo.root, env=env or {})

    def change_row(self, heading: str, old: str, new: str) -> None:
        rows = {key: (prefix, [new if line == old else line for line in lines]) for key, (prefix, lines) in self.ROWS.items()}
        self.repo.write(fe_guides.CONTRACTS, self.contracts(rows))

    def recheck(self, stem: str, title: str, base: str, body: str = "Text.") -> None:
        self.repo.write(fe_guides.GUIDES + f"{stem}.md", self.guide(title, base, "2026-11-20 (def5678)", body))

    def test_a_changed_row_without_its_owner_guide_fails(self):
        old = self.ROWS["Companies"][1][0]
        self.change_row("Companies", old, old.replace("companies.view", "companies.read"))
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("companies-and-locations.md: GET /api/v1/companies/{id:long} changed", out)
        self.assertNotIn("farms.md", out, "only the owner of the changed section is asked for")

    def test_the_owner_guide_with_its_line_moved_passes(self):
        old = self.ROWS["Companies"][1][0]
        self.change_row("Companies", old, old.replace("companies.view", "companies.read"))
        self.recheck("companies-and-locations", "Companies and Locations", "`/api/v1/companies`", "Now companies.read.")
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(0, code, out)

    def test_the_owner_guide_changed_without_moving_its_line_fails(self):
        old = self.ROWS["Companies"][1][0]
        self.change_row("Companies", old, old.replace("companies.view", "companies.read"))
        self.repo.write(fe_guides.GUIDES + "companies-and-locations.md",
                        self.guide("Companies and Locations", "`/api/v1/companies`", body="Now companies.read."))
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("line did not move — check the guide against the code", out)

    def test_sections_sharing_a_prefix_are_owned_by_their_titles(self):
        old = self.ROWS["Order Fulfilment - Shipping"][1][0]
        self.change_row("Order Fulfilment - Shipping", old, old.replace("`x`", "`shipping.view`"))
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("order-fulfilment-shipping.md", out)
        self.assertNotIn("order-fulfilment-packing.md", out)

    def test_a_shared_protocol_base_also_owns_its_routes(self):
        old = self.ROWS["Farms"][1][1]
        self.change_row("Farms", old, old.replace("farms.edit", "farms.sync"))
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("farms.md", out)
        self.assertIn("offline-sync.md", out)
        old = self.ROWS["Farms"][1][0]
        self.repo.git("reset", "-q", "--hard", "main")
        self.change_row("Farms", old, old.replace("farms.view", "farms.read"))
        self.repo.commit()
        self.assertNotIn("offline-sync.md", self.check()[1])

    def test_a_changed_schema_reaches_the_rows_that_link_it(self):
        self.repo.write(fe_guides.CONTRACTS, self.contracts(schema=self.SCHEMA + "| `code` | string |\n"))
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("companies-and-locations.md: POST /api/v1/companies changed", out)

    def test_a_section_no_guide_owns_fails_with_add_an_owner(self):
        old = self.ROWS["Preferences"][1][0]
        self.change_row("Preferences", old, old.replace("authenticated", "anonymous"))
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("which no guide owns — add an owner", out)
        # The base's row and HEAD's are two rows for one route: it is named once.
        self.assertEqual(1, out.count("GET /api/v1/date-formats changed in 'Preferences'"), out)

    def test_a_configured_owner_with_a_reason_owns_the_section(self):
        self.repo.write("devtools/factory/factory.toml",
                        '[fe_guides]\nowners = [{ section = "Preferences", guides = ["profile-and-preferences"], '
                        'reason = "the profile guide covers the settings screen" }]\n')
        old = self.ROWS["Preferences"][1][0]
        self.change_row("Preferences", old, old.replace("authenticated", "anonymous"))
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("profile-and-preferences.md", out)
        self.recheck("profile-and-preferences", "My Profile and Preferences", "`/api/v1/profile`")
        self.repo.commit()
        self.assertEqual(0, self.check()[0])

    def test_a_section_owned_by_no_guide_on_purpose_passes(self):
        self.repo.write("devtools/factory/factory.toml",
                        '[fe_guides]\nowners = [{ section = "Preferences", guides = [], reason = "not called by the app" }]\n')
        old = self.ROWS["Preferences"][1][0]
        self.change_row("Preferences", old, old.replace("authenticated", "anonymous"))
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(0, code, out)

    def test_an_owner_entry_without_a_reason_or_for_a_gone_section_fails(self):
        self.repo.write("devtools/factory/factory.toml",
                        '[fe_guides]\nowners = [{ section = "Preferences", guides = [] }]\n')
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("gives no reason", out)
        self.repo.write("devtools/factory/factory.toml",
                        '[fe_guides]\nowners = [{ section = "Gone", guides = [], reason = "was" }]\n')
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("names the section 'Gone'", out)

    def test_a_prose_only_change_to_the_contracts_needs_no_guide(self):
        self.repo.write(fe_guides.CONTRACTS, self.contracts().replace("Prose.", "Other prose."))
        self.repo.commit()
        self.assertEqual(0, self.check()[0])

    def test_unchanged_contracts_pass(self):
        self.repo.write("other.txt", "x\n")
        self.repo.commit()
        self.assertEqual(self.check()[0], 0)

    def test_a_failed_git_show_fails(self):
        real = fe_guides.git
        old = self.ROWS["Companies"][1][0]
        self.change_row("Companies", old, old.replace("companies.view", "companies.read"))
        self.repo.commit()

        def failing_show(*args, root):
            if args[0] == "show":
                return subprocess.CompletedProcess(args, 128, "", "fatal: bad object")
            return real(*args, root=root)

        with mock.patch.object(fe_guides, "git", failing_show):
            code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("could not check", out)

    def test_a_failed_listing_is_never_read_as_an_absent_file(self):
        """Both reads of the contracts failing must not look like "no change"."""
        real = fe_guides.git
        old = self.ROWS["Companies"][1][0]
        self.change_row("Companies", old, old.replace("companies.view", "companies.read"))
        self.repo.commit()

        def failing_listing(*args, root):
            if args[0] == "ls-tree":
                return subprocess.CompletedProcess(args, 128, "", "fatal: not a tree object")
            return real(*args, root=root)

        with mock.patch.object(fe_guides, "git", failing_listing):
            code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("could not check", out)

    def test_prose_naming_the_last_checked_line_is_not_the_line(self):
        old = self.ROWS["Companies"][1][0]
        self.change_row("Companies", old, old.replace("companies.view", "companies.read"))
        self.repo.write(fe_guides.GUIDES + "companies-and-locations.md",
                        "# Companies and Locations — front-end integration guide\n\nMove the Last checked against "
                        "the code line on every change.\n\n**Module:** x · **Base:** `/api/v1/companies` · "
                        "**Last checked against the code:** 2026-11-01 (abc1234)\n\nNew text.\n")
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("line did not move", out)

    def test_could_not_check_fails_a_pull_request_in_ci(self):
        code, out = self.check(base="no-such-branch", env={"BITBUCKET_PR_ID": "7"})
        self.assertEqual(code, 1)
        self.assertIn("could not check", out)

    def test_could_not_check_passes_locally_with_a_message(self):
        code, out = self.check(base="no-such-branch")
        self.assertEqual(code, 0)
        self.assertIn("could not check", out)


    def wrapped(self, checked: str, body: str) -> str:
        """A guide whose header wraps inside the field, as an editor's line length leaves it (D28)."""
        return ("# Companies and Locations — front-end integration guide\n\n**Module:** x · **Base:** "
                "`/api/v1/companies` · **Last checked against\nthe code:** " + checked + f"\n\n{body}\n")

    def test_a_wrapped_last_checked_line_is_read_and_must_move(self):
        self.repo.write(fe_guides.GUIDES + "companies-and-locations.md", self.wrapped("2026-11-01 (abc1234)", "Text."))
        self.repo.commit("Wrapped header")
        self.repo.git("branch", "-f", "main", "HEAD")
        old = self.ROWS["Companies"][1][0]
        self.change_row("Companies", old, old.replace("companies.view", "companies.read"))
        self.repo.write(fe_guides.GUIDES + "companies-and-locations.md", self.wrapped("2026-11-01 (abc1234)", "New."))
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("line did not move", out)
        self.assertNotIn("has no", out, "the wrapped field is a Last checked line")
        self.repo.write(fe_guides.GUIDES + "companies-and-locations.md", self.wrapped("2026-11-20 (def5678)", "New."))
        self.repo.commit()
        self.assertEqual(0, self.check()[0])

    def test_a_field_wrapped_before_its_commit_keeps_the_commit(self):
        guide = fe_guides.Guide("farms", "# Farms\n\n**Base:** `/api/v1/farms` · **Last checked against the code:** "
                                         "2026-11-01\n(dev abc1234)\n\nText.\n")
        self.assertEqual("**Last checked against the code:** 2026-11-01 (dev abc1234)", guide.checked)

    def test_a_changed_response_schema_reaches_the_rows_returning_it(self):
        self.repo.write(fe_guides.CONTRACTS, self.contracts(
            responses={**self.RESPONSES, "FarmDto": self.RESPONSES["FarmDto"] + "| `Status` | `string` |\n"}))
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("farms.md: GET /api/v1/farms changed", out)
        self.assertNotIn("companies-and-locations.md", out)

    def test_a_changed_nested_type_reaches_the_rows_returning_the_type_holding_it(self):
        self.repo.write(fe_guides.CONTRACTS, self.contracts(
            responses={**self.RESPONSES, "AddressDto": self.RESPONSES["AddressDto"] + "| `Zip` | `string` |\n"}))
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("farms.md: GET /api/v1/farms changed", out)

    def test_a_schema_whose_declaring_file_moved_is_no_change(self):
        self.repo.write(fe_guides.CONTRACTS, self.contracts().replace("`src/Dtos.cs`", "`src/Farms/Dtos.cs`")
                        .replace("`src/A.cs`", "`src/Companies/A.cs`"))
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(0, code, out)

    def test_response_schemas_listed_for_the_first_time_change_no_row(self):
        # The base's contracts predate the generator's response field lists: none of their types is new.
        self.repo.write(fe_guides.CONTRACTS, self.contracts().split("## Response schemas", 1)[0])
        self.repo.commit("Contracts without response schemas")
        self.repo.git("branch", "-f", "main", "HEAD")
        self.repo.write(fe_guides.CONTRACTS, self.contracts())
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(0, code, out)

    def test_a_schema_section_deleted_on_the_branch_still_changes_its_rows(self):
        self.repo.write(fe_guides.CONTRACTS, self.contracts().split("## Response schemas", 1)[0])
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("farms.md: GET /api/v1/farms changed", out)

    def test_the_guide_template_is_not_a_guide(self):
        self.repo.write(fe_guides.GUIDES + "_template.md",
                        self.guide("Companies and Locations", "`/api/v1/companies`"))
        self.repo.commit("Template")
        self.repo.git("branch", "-f", "main", "HEAD")
        old = self.ROWS["Companies"][1][0]
        self.change_row("Companies", old, old.replace("companies.view", "companies.read"))
        self.recheck("companies-and-locations", "Companies and Locations", "`/api/v1/companies`", "Now companies.read.")
        self.repo.commit()
        code, out = self.check()
        self.assertEqual(0, code, out)
