"""
Self-tests for the dotnet pack's checks, beside the Book's own in test_checks.py (whose helpers they use).
`factory book init` copies this file in with the pack:

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
                         AWS_KEY, find_sh)

vulnerable_packages = load("check-vulnerable-packages")
api_contracts = load("gen-api-contracts")
test_setup = load("check-test-setup")
check_dotnet = load("check-dotnet")


class AppsettingsSecretsTests(RepoTestCase):
    """The JSON `"Password"` rule, the shape Email:Smtp:Password uses, which `Password=` never matches."""

    # Assembled at run time, as AWS_KEY is, so this file holds no password-shaped value.
    REAL = "s3cr3t-" + "smtp-value"

    def setUp(self) -> None:
        super().setUp()
        self.sh = find_sh()
        if self.sh is None:
            self.fail("no sh on PATH or beside git to run the check with")

    def check(self, password: str, mode: str = "--tracked") -> subprocess.CompletedProcess:
        self.repo.write("src/appsettings.json", '{\n  "Email": { "Smtp": { "Password": "%s" } }\n}\n' % password)
        return subprocess.run([self.sh, str(HERE / "check-appsettings-secrets.sh"), mode], cwd=self.repo.root,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")

    def test_a_real_smtp_password_is_refused_and_never_printed(self):
        done = self.check(self.REAL)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("src/appsettings.json:2  a settings value carries a real password", done.stderr)
        self.assertNotIn(self.REAL, done.stdout + done.stderr)

    def test_a_staged_real_password_is_refused(self):
        done = self.check(self.REAL, "--staged")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)

    def test_a_placeholder_or_an_empty_password_passes(self):
        for value in ("REPLACE_WITH_SMTP_PASSWORD", ""):
            with self.subTest(value=value):
                done = self.check(value)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


# ---------------------------------------------------------------- check-dotnet

class DotnetShapeTests(RepoTestCase):
    """The three shape rules, each failure shape, its allow-list and its stale entry, on made-up C#."""

    SETTINGS = '[dotnet_checks]\ntenant_key = "TenantId"\nsoft_delete_column = "deleted_at"\n'

    def setUp(self) -> None:
        super().setUp()
        self.settings()

    def settings(self, extra: str = "") -> None:
        self.repo.write("devtools/factory/factory.toml", self.SETTINGS + extra)

    def check(self) -> tuple[int, str]:
        return run_main(check_dotnet.main, [], root=self.repo.root)

    # 1. list items

    REQUEST = "public sealed record TagWidgetsCommand(IReadOnlyList<string>? Tags, IReadOnlyList<long>? Ids);\n"

    def test_a_list_of_references_without_a_not_null_item_rule_fails(self):
        self.repo.write("src/App/TagWidgets.cs", self.REQUEST +
                        "internal sealed class TagWidgetsValidator : AbstractValidator<TagWidgetsCommand>\n{\n"
                        "    public TagWidgetsValidator() { RuleFor(c => c.Tags).NotEmpty(); }\n}\n")
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("list-items — TagWidgetsValidator validates TagWidgetsCommand.Tags", out)
        self.assertNotIn("TagWidgetsCommand.Ids", out, "a list of numbers fails binding on a null already")

    def test_the_item_rule_passes_and_a_commented_one_does_not(self):
        rule = "RuleForEach(c => c.Tags).NotNull();"
        body = ("internal sealed class TagWidgetsValidator : AbstractValidator<TagWidgetsCommand>\n{\n"
                "    public TagWidgetsValidator() { %s }\n}\n")
        self.repo.write("src/App/TagWidgets.cs", self.REQUEST + body % rule)
        self.assertEqual(0, self.check()[0])
        self.repo.write("src/App/TagWidgets.cs", self.REQUEST + body % ("// " + rule))
        self.assertEqual(1, self.check()[0])

    def test_a_rule_in_a_generic_base_validator_counts(self):
        self.repo.write("src/App/Push.cs",
                        "public sealed record PushFarmsCommand(IReadOnlyList<Change<Farm>>? Changes) : IPush<Farm>;\n"
                        "public abstract class PushValidatorBase<TCommand, TRecord> : AbstractValidator<TCommand>\n"
                        "    where TCommand : IPush<TRecord> where TRecord : class\n{\n"
                        "    protected PushValidatorBase()\n    {\n"
                        "        RuleForEach(command => command.Changes)\n            .NotNull().WithMessage(\"x\")\n"
                        "            .ChildRules(change => { change.RuleFor(i => i.Id).NotEmpty(); });\n    }\n}\n"
                        "internal sealed class PushFarmsValidator : PushValidatorBase<PushFarmsCommand, Farm>;\n")
        code, out = self.check()
        self.assertEqual(0, code, out)

    def test_a_nested_request_and_a_delegated_child_validator_are_followed(self):
        model = ("public sealed record AnimalWriteModel(string? Name, IReadOnlyList<OwnerModel>? Owners);\n"
                 "public sealed record OwnerModel(long? Id);\n"
                 "public sealed record CreateAnimalCommand(AnimalWriteModel Animal);\n")
        child = ("internal sealed class AnimalWriteModelValidator : AbstractValidator<AnimalWriteModel>\n{\n"
                 "    public AnimalWriteModelValidator() { RuleForEach(animal => animal.Owners).NotNull(); }\n}\n")
        parent = ("internal sealed class CreateAnimalValidator : AbstractValidator<CreateAnimalCommand>\n{\n"
                  "    public CreateAnimalValidator() { RuleFor(c => c.Animal).NotNull()%s; }\n}\n")
        self.repo.write("src/App/Animals.cs", model + child + parent % "")
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("CreateAnimalCommand.Animal.Owners", out)
        self.repo.write("src/App/Animals.cs", model + child + parent % ".SetValidator(new AnimalWriteModelValidator())")
        self.assertEqual(0, self.check()[0])

    def test_a_get_only_or_nullable_item_list_is_left_out(self):
        self.repo.write("src/App/Report.cs",
                        "public sealed record ReportQuery(IReadOnlyList<string?>? Names) : IRequireAny\n{\n"
                        "    public IReadOnlyCollection<string> AnyOf => [\"a\"];\n"
                        "    public IReadOnlyList<string> Columns { get; } = [];\n}\n"
                        "internal sealed class ReportValidator : AbstractValidator<ReportQuery> { }\n")
        code, out = self.check()
        self.assertEqual(0, code, out)

    def test_the_list_allow_list_passes_and_a_stale_entry_fails(self):
        self.repo.write("src/App/TagWidgets.cs", self.REQUEST +
                        "internal sealed class TagWidgetsValidator : AbstractValidator<TagWidgetsCommand> { }\n")
        self.settings('list_items_nullable = [{ property = "TagWidgetsCommand.Tags", reason = "nulls reported per row" }]\n')
        self.assertEqual(0, self.check()[0])
        self.settings('list_items_nullable = [{ property = "Gone.Tags", reason = "was" }, '
                      '{ property = "TagWidgetsCommand.Tags", reason = "r" }]\n')
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("lists Gone.Tags, which matches nothing now", out)

    # 2. options

    OPTIONS = ("public sealed class MailOptions { %s public string Host { get; init; } = \"\"; }\n"
               "static class Setup {\n  static void Add(IServiceCollection services, IConfiguration configuration)\n"
               "  {\n    services\n        .AddOptions<MailOptions>()\n"
               "        .Bind(configuration.GetSection(MailOptions.SectionName))%s;\n  }\n}\n")

    def options(self, annotation: str, chain: str) -> tuple[int, str]:
        self.repo.write("src/Api/Setup.cs", self.OPTIONS % (annotation, chain))
        return self.check()

    def test_options_without_validate_on_start_fail(self):
        code, out = self.options("[Required]", "\n        .ValidateDataAnnotations()")
        self.assertEqual(1, code)
        self.assertIn("AddOptions<MailOptions> does not call .ValidateOnStart()", out)

    def test_validate_on_start_with_nothing_behind_it_fails(self):
        code, out = self.options("", "\n        .ValidateOnStart()")
        self.assertEqual(1, code)
        self.assertIn("calls .ValidateOnStart() with nothing to validate", out)
        code, out = self.options("", "\n        .ValidateDataAnnotations()\n        .ValidateOnStart()")
        self.assertEqual(1, code, "annotations validation on a class without one validates nothing")

    def test_a_validator_behind_validate_on_start_passes(self):
        for annotation, chain in (("[Required]", ".ValidateDataAnnotations().ValidateOnStart()"),
                                  ("", ".Validate(o => o.Host != \"\", \"Host\").ValidateOnStart()")):
            with self.subTest(chain=chain):
                self.assertEqual(0, self.options(annotation, chain)[0])
        self.repo.write("src/Api/ValidateMail.cs",
                        "internal sealed class ValidateMail : IValidateOptions<MailOptions> { }\n")
        self.assertEqual(0, self.options("", ".ValidateOnStart()")[0])

    def test_the_options_allow_list_passes_and_needs_a_reason(self):
        self.settings('options_unvalidated = [{ type = "MailOptions", reason = "the framework binds it" }]\n')
        self.assertEqual(0, self.options("", "")[0])
        self.settings('options_unvalidated = [{ type = "MailOptions" }]\n')
        code, out = self.options("", "")
        self.assertEqual(1, code)
        self.assertIn("could not check", out)

    # 3. unique indexes

    ENTITY = ("public sealed class Widget : ISoftDeletable\n{\n    public long TenantId { get; set; }\n"
              "    public string Code { get; set; } = \"\";\n}\n"
              "public interface ISoftDeletable { DateTimeOffset? DeletedAt { get; set; } }\n"
              "public static class SoftDeleteFilter { public const string Live = \"deleted_at IS NULL\"; }\n")
    CONFIGURATION = ("internal sealed class WidgetConfiguration : IEntityTypeConfiguration<Widget>\n{\n"
                     "    public void Configure(EntityTypeBuilder<Widget> builder)\n    {\n"
                     "        builder.HasIndex(%s)\n            .IsUnique()%s;\n    }\n}\n")

    def index(self, columns: str, chain: str, entity: str | None = None) -> tuple[int, str]:
        self.repo.write("src/Domain/Widget.cs", self.ENTITY if entity is None else entity)
        self.repo.write("src/Persistence/WidgetConfiguration.cs", self.CONFIGURATION % (columns, chain))
        return self.check()

    def test_a_unique_index_not_led_by_the_tenant_key_fails(self):
        code, out = self.index("w => w.Code", ".HasFilter(SoftDeleteFilter.Live)")
        self.assertEqual(1, code)
        self.assertIn("Widget(Code) is on a tenant-scoped entity but does not lead with TenantId", out)

    def test_a_unique_index_on_a_soft_deletable_entity_without_the_filter_fails(self):
        code, out = self.index("w => new { w.TenantId, w.Code }", "")
        self.assertEqual(1, code)
        self.assertIn("Widget(TenantId, Code) is on an entity mapping deleted_at but does not filter", out)

    def test_the_column_not_the_interface_makes_an_entity_soft_deletable(self):
        entity = ("public sealed class Widget\n{\n    public long TenantId { get; set; }\n"
                  "    public string Code { get; set; } = \"\";\n    public DateTimeOffset? DeletedAt { get; set; }\n}\n")
        code, out = self.index("w => new { w.TenantId, w.Code }", "", entity)
        self.assertEqual(1, code)
        self.assertIn("does not filter `deleted_at IS NULL`", out)

    def test_the_right_shape_passes_with_the_filter_as_a_constant_or_interpolated(self):
        for chain in (".HasFilter(SoftDeleteFilter.Live)", '.HasFilter($"{SoftDeleteFilter.Live} AND code IS NOT NULL")',
                      '.HasFilter("\\"deleted_at\\" IS NULL")'):
            with self.subTest(chain=chain):
                code, out = self.index("w => new { w.TenantId, w.Code }", chain)
                self.assertEqual(0, code, out)

    def test_a_named_unique_argument_is_judged_too(self):
        self.repo.write("src/Domain/Widget.cs", self.ENTITY)
        self.repo.write("src/Persistence/WidgetConfiguration.cs",
                        (self.CONFIGURATION % ("w => w.Code", "")).replace(".IsUnique()", ".IsUnique(unique: true)"))
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("Widget(Code)", out)

    def test_an_entity_it_cannot_read_fails_never_passes(self):
        self.repo.write("src/Persistence/BaseConfiguration.cs",
                        "internal abstract class BaseConfiguration<T> : IEntityTypeConfiguration<T> where T : class\n{\n"
                        "    public virtual void Configure(EntityTypeBuilder<T> builder)\n    {\n"
                        "        builder.HasIndex(\"Code\").IsUnique();\n    }\n}\n")
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("could not read the entity T", out)

    def test_a_non_unique_index_is_not_judged(self):
        self.repo.write("src/Domain/Widget.cs", self.ENTITY)
        self.repo.write("src/Persistence/WidgetConfiguration.cs",
                        (self.CONFIGURATION % ("w => w.Code", "")).replace("\n            .IsUnique()", ""))
        self.assertEqual(0, self.check()[0])

    def test_an_owned_types_index_is_named_by_its_owner_and_allow_listed(self):
        entity = ("public sealed class User\n{\n    public long TenantId { get; set; }\n"
                  "    public Email Email { get; set; } = null!;\n    public DateTimeOffset? DeletedAt { get; set; }\n}\n"
                  "public sealed class Email { public string Normalized { get; set; } = \"\"; }\n")
        self.repo.write("src/Domain/User.cs", entity)
        self.repo.write("src/Persistence/UserConfiguration.cs",
                        "internal sealed class UserConfiguration : IEntityTypeConfiguration<User>\n{\n"
                        "    public void Configure(EntityTypeBuilder<User> builder)\n    {\n"
                        "        builder.OwnsOne(user => user.Email, email =>\n        {\n"
                        "            email.HasIndex(value => value.Normalized)\n                .IsUnique()\n"
                        "                .HasFilter(\"deleted_at IS NULL\");\n        });\n    }\n}\n")
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("User.Email(Normalized) is on a tenant-scoped entity", out)
        self.settings('unique_indexes = [{ index = "User.Email(Normalized)", reason = "one account per address" }]\n')
        self.assertEqual(0, self.check()[0])

    def test_without_a_tenant_key_that_leg_is_not_judged_and_says_so(self):
        self.repo.write("devtools/factory/factory.toml", "[dotnet_checks]\n")
        code, out = self.index("w => w.Code", ".HasFilter(SoftDeleteFilter.Live)")
        self.assertEqual(0, code, out)
        self.assertIn("tenant_key is empty", out)

    def test_a_migration_snapshot_is_not_judged(self):
        self.repo.write("src/Domain/Widget.cs", self.ENTITY)
        self.repo.write("src/Persistence/Migrations/Snapshot.cs",
                        "modelBuilder.Entity<Widget>(b => { b.HasIndex(\"Code\").IsUnique(); });\n")
        self.assertEqual(0, self.check()[0])

    def test_no_factory_toml_could_not_be_checked(self):
        (self.repo.root / "devtools/factory/factory.toml").unlink()
        code, out = self.check()
        self.assertEqual(1, code)
        self.assertIn("could not check", out)


# ---------------------------------------------------------------- check-protected-paths

CLEAN_AUDIT = '{"version": 1, "projects": [{"path": "src/Api.csproj"}, {"path": "src/Domain.csproj"}]}'


class VulnerablePackagesTests(unittest.TestCase):
    def test_a_clean_audit_passes(self):
        self.assertEqual(vulnerable_packages.problems(CLEAN_AUDIT), [])

    def test_a_vulnerable_transitive_package_fails(self):
        text = ('{"projects": [{"path": "src/Api.csproj", "frameworks": [{"transitivePackages": '
                '[{"id": "System.Text.Json", "resolvedVersion": "8.0.0", "vulnerabilities": [{"severity": "High"}]}]}]}]}')
        self.assertEqual(vulnerable_packages.problems(text), ["vulnerable package: System.Text.Json 8.0.0"])

    def test_a_source_that_could_not_be_audited_fails(self):
        text = CLEAN_AUDIT[:-1] + ', "problems": [{"level": "warning", "text": "audit source unreachable"}]}'
        self.assertIn("audit source unreachable", vulnerable_packages.problems(text)[0])

    def test_a_report_that_audited_nothing_fails(self):
        self.assertIn("nothing was audited", vulnerable_packages.problems('{"projects": []}')[0])
        self.assertIn("nothing was audited", vulnerable_packages.problems("The following sources were used")[0])


# ---------------------------------------------------------------- check-compose

class TestSetupCheckTests(unittest.TestCase):
    """The allow-list is exact: every hit is covered, and every entry still matches what it allows."""

    def problems(self, files: dict[str, str], allow: str) -> list[str]:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, text in files.items():
                path = root / test_setup.TESTS / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            (root / test_setup.TESTS).mkdir(parents=True, exist_ok=True)
            (root / test_setup.ALLOW_LIST).write_text(allow, encoding="utf-8")
            return test_setup.problems(root)

    OWN_HOST = "var host = factory.WithWebHostBuilder(b => { });\n"

    def test_a_hit_with_no_entry_fails(self):
        found = self.problems({"StoreTests.cs": self.OWN_HOST}, "")
        self.assertEqual(len(found), 1, found)
        self.assertIn("StoreTests.cs", found[0])

    def test_a_hit_inside_fixtures_or_a_comment_passes(self):
        files = {"Fixtures/Factory.cs": self.OWN_HOST, "StoreTests.cs": "// " + self.OWN_HOST}
        self.assertEqual(self.problems(files, ""), [])

    def test_a_covered_hit_passes(self):
        self.assertEqual(self.problems({"StoreTests.cs": self.OWN_HOST}, "test-host\tStoreTests.cs\t1\tWhy.\n"), [])

    def test_more_hits_than_the_entry_allows_fail(self):
        found = self.problems({"StoreTests.cs": self.OWN_HOST * 2}, "test-host\tStoreTests.cs\t1\tWhy.\n")
        self.assertEqual(len(found), 1, found)
        self.assertIn("allows 1", found[0])

    def test_an_entry_that_matches_nothing_is_stale(self):
        found = self.problems({}, "test-host\tGoneTests.cs\t1\tWhy.\n")
        self.assertEqual(len(found), 1, found)
        self.assertIn("stale", found[0])

    def test_an_entry_allowing_more_than_the_file_has_is_stale(self):
        found = self.problems({"StoreTests.cs": self.OWN_HOST}, "test-host\tStoreTests.cs\t3\tWhy.\n")
        self.assertEqual(len(found), 1, found)
        self.assertIn("lower it", found[0])

    def test_a_malformed_or_unknown_entry_fails(self):
        found = self.problems({}, "test-host\tStoreTests.cs\t1\n" + "no-such-rule\tX.cs\t1\tWhy.\n")
        self.assertEqual(len(found), 2, found)

    def test_list_prints_every_hit_with_its_count(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / test_setup.TESTS / "Data" / "SeedTests.cs"
            path.parent.mkdir(parents=True)
            path.write_text(self.OWN_HOST + "await db.Database.MigrateAsync();\n", encoding="utf-8")
            code, output = run_main(test_setup.main, ["--list"], root)
        self.assertEqual(code, 0, output)
        self.assertIn("test-host\tData/SeedTests.cs\t1", output)
        self.assertIn("own-database\tData/SeedTests.cs\t1", output)

    def test_the_integration_project_comes_from_factory_toml(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "devtools/factory").mkdir(parents=True)
            (root / test_setup.CONFIG).write_text('[commands]\nintegration_project = "tests/Shop.Tests/"\n',
                                                  encoding="utf-8")
            self.assertEqual(test_setup.tests_folder(root), "tests/Shop.Tests")
            path = root / "tests/Shop.Tests/StoreTests.cs"
            path.parent.mkdir(parents=True)
            path.write_text(self.OWN_HOST, encoding="utf-8")
            found = test_setup.problems(root)
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("tests/Shop.Tests/StoreTests.cs"), found)


class TestSetupStagedTests(RepoTestCase):
    """`--staged`, as pre-commit runs it: the staged test files only, as staged."""

    OWN_HOST = TestSetupCheckTests.OWN_HOST

    def staged(self) -> tuple[int, str]:
        return run_main(test_setup.main, ["--staged"], self.repo.root)

    def test_an_uncovered_hit_in_a_staged_file_fails(self):
        self.repo.write(f"{test_setup.TESTS}/StoreTests.cs", self.OWN_HOST)
        code, output = self.staged()
        self.assertEqual(code, 1, output)
        self.assertIn("StoreTests.cs: 1 test-host hit(s)", output)

    def test_an_unstaged_file_or_one_under_fixtures_is_not_judged(self):
        self.repo.write(f"{test_setup.TESTS}/StoreTests.cs", self.OWN_HOST, add=False)
        self.repo.write(f"{test_setup.TESTS}/Fixtures/Factory.cs", self.OWN_HOST)
        code, output = self.staged()
        self.assertEqual(code, 0, output)
        self.assertIn("staged files", output)

    def test_the_staged_text_is_judged_not_the_working_tree(self):
        self.repo.write(f"{test_setup.TESTS}/StoreTests.cs", self.OWN_HOST)
        self.repo.write(f"{test_setup.TESTS}/StoreTests.cs", "// fixed after staging\n", add=False)
        self.assertEqual(self.staged()[0], 1)

    def test_the_staged_allow_list_covers_a_hit_and_a_stale_entry_waits_for_the_full_run(self):
        self.repo.write(f"{test_setup.TESTS}/StoreTests.cs", self.OWN_HOST)
        self.repo.write(test_setup.ALLOW_LIST, "test-host\tStoreTests.cs\t1\tWhy.\n"
                                               "own-database\tGoneTests.cs\t1\tWhy.\n")
        code, output = self.staged()
        self.assertEqual(code, 0, output)
        self.assertEqual(run_main(test_setup.main, [], self.repo.root)[0], 1, "the full run reports the stale entry")

    def test_more_staged_hits_than_the_entry_allows_fail(self):
        self.repo.write(test_setup.ALLOW_LIST, "test-host\tStoreTests.cs\t1\tWhy.\n")
        self.repo.write(f"{test_setup.TESTS}/StoreTests.cs", self.OWN_HOST * 2)
        code, output = self.staged()
        self.assertEqual(code, 1, output)
        self.assertIn("allows 1", output)


# ---------------------------------------------------------------- check-test-results.sh

def trx(executed: int, skipped: int = 0, project: str = "") -> str:
    results = "".join(f'<UnitTestResult testName="t{n}" outcome="NotExecuted" />' for n in range(skipped))
    storage = f' storage="c:/build/tests/{project}/bin/{project}.dll"' if project else ""
    return (f'<TestRun><Results>{results}</Results><TestDefinitions><UnitTest name="x"{storage} />'
            f'</TestDefinitions><ResultSummary><Counters total="{executed + skipped}" executed="{executed}" '
            f'passed="{executed}" notExecuted="0" /></ResultSummary></TestRun>')


class TestResultsGateTests(RepoTestCase):
    """The skip gate CI and ci-local.sh share: a missing, empty or skipping run fails."""

    def setUp(self) -> None:
        super().setUp()
        self.sh = find_sh()
        if self.sh is None:
            self.fail("no sh on PATH or beside git to run the gate with")
        self.script = (HERE / "check-test-results.sh").as_posix()

    def gate(self, *args: str) -> tuple[int, str]:
        done = subprocess.run([self.sh, self.script, *args], cwd=self.repo.root, capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def test_an_integration_run_that_executed_and_skipped_nothing_passes(self):
        self.repo.write("out/integration.trx", trx(12), add=False)
        code, out = self.gate("integration", "out/integration.trx")
        self.assertEqual(code, 0, out)
        self.assertIn("12 executed, 0 skipped", out)

    def test_a_missing_trx_fails(self):
        code, out = self.gate("integration", "out/integration.trx")
        self.assertEqual(code, 1)
        self.assertIn("No trx", out)

    def test_a_run_that_executed_nothing_fails(self):
        self.repo.write("out/integration.trx", trx(0), add=False)
        code, out = self.gate("integration", "out/integration.trx")
        self.assertEqual(code, 1)
        self.assertIn("No integration tests executed", out)

    def test_a_skip_fails_although_counters_say_none_were_not_executed(self):
        self.repo.write("out/integration.trx", trx(12, skipped=1), add=False)
        code, out = self.gate("integration", "out/integration.trx")
        self.assertEqual(code, 1)
        self.assertIn("skipped", out)

    def test_each_unit_project_needs_exactly_one_trx_that_ran_and_skipped_nothing(self):
        for project in ("Boilerplate.UnitTests", "Boilerplate.ArchitectureTests", "Boilerplate.IntegrationTests"):
            (self.repo.root / "tests" / project).mkdir(parents=True)
        self.repo.write("out/unit-arch_1.trx", trx(30, project="Boilerplate.UnitTests"), add=False)
        code, out = self.gate("unit-arch", "out")
        self.assertEqual(code, 1, "the architecture project left no trx")
        self.assertIn("Expected one trx for Boilerplate.ArchitectureTests, found 0", out)
        self.repo.write("out/unit-arch_2.trx", trx(5, project="Boilerplate.ArchitectureTests"), add=False)
        code, out = self.gate("unit-arch", "out")
        self.assertEqual(code, 0, out)
        self.repo.write("out/unit-arch_2.trx", trx(5, skipped=1, project="Boilerplate.ArchitectureTests"), add=False)
        code, out = self.gate("unit-arch", "out")
        self.assertEqual(code, 1)
        self.assertIn("skipped in Boilerplate.ArchitectureTests", out)


class ApiContractConstantsTests(unittest.TestCase):
    """Routes, tags and names written as constants still reach the contracts."""

    def test_literal_and_constants_concatenate(self):
        constants = {"ApiRoutes.Versioned": "/api/v{version:apiVersion}"}
        self.assertEqual(
            api_contracts.resolve_string('ApiRoutes.Versioned + "/companies/{companyId:long}"', constants, "x.cs"),
            "/api/v{version:apiVersion}/companies/{companyId:long}")

    def test_plus_inside_a_literal_is_not_an_operator(self):
        self.assertEqual(api_contracts.resolve_string('"a+b"', {}, "x.cs"), "a+b")

    def test_unresolvable_expression_stops_the_run(self):
        with self.assertRaises(SystemExit) as raised:
            api_contracts.resolve_string("Routes.Unknown", {}, "x.cs")
        self.assertIn("cannot resolve 'Routes.Unknown'", str(raised.exception))

    def test_constants_resolve_by_name_and_by_class(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "HealthEndpoints.cs"
            path.write_text(
                'internal static class HealthEndpoints\n{\n'
                '    public const string Root = "/health"; // the probes\n'
                '    public const string Live = Root + "/live";\n}\n',
                encoding="utf-8")
            constants = api_contracts.load_constants(path, "HealthEndpoints")
        self.assertEqual(constants["Live"], "/health/live")
        self.assertEqual(constants["HealthEndpoints.Live"], "/health/live")


class ApiContractAnyPermissionTests(unittest.TestCase):
    """A request accepting any of several permissions renders them all, or the run stops."""

    CATALOGUE = {"Stores.Create": "stores.create", "Stores.Edit": "stores.edit"}

    def read(self, body: str) -> dict[str, str]:
        source = ("public sealed record PushCommand(int X) : IRequireAnyOf\n{\n"
                  f"    public IReadOnlyCollection<string> AnyOf => {body};\n}}\n")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "PushCommand.cs"
            path.write_text(source, encoding="utf-8")
            with mock.patch.object(api_contracts, "APPLICATION", root), \
                    mock.patch.object(api_contracts, "ROOT", root), \
                    mock.patch.dict(api_contracts.SETTINGS, any_permission=self.interface):
                return api_contracts.load_request_permissions([path], self.CATALOGUE)

    interface = "IRequireAnyOf"

    def test_without_the_setting_no_any_of_is_read(self):
        # The convention is the project's ([api_contracts] any_permission), not the pack's (D16).
        self.interface = ""
        self.assertEqual(self.read("[Permissions.Stores.Create]"), {})

    def test_any_of_renders_every_permission(self):
        perms = self.read("[Permissions.Stores.Create, Permissions.Stores.Edit]")
        self.assertEqual(perms["PushCommand"], "stores.create or stores.edit")

    def test_any_of_that_cannot_be_read_stops_the_run_naming_the_class(self):
        with self.assertRaises(SystemExit) as raised:
            self.read("Accepted()")
        self.assertIn("PushCommand", str(raised.exception))


class ApiContractSettingsTests(unittest.TestCase):
    """[api_contracts] in factory.toml names the project's own header filters and any-permission interface."""

    def settings(self, text: str) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "devtools" / "factory").mkdir(parents=True)
            (root / "devtools" / "factory" / "factory.toml").write_text(text, encoding="utf-8")
            return api_contracts.contract_settings(root)

    def test_none_by_default(self):
        self.assertEqual(self.settings("[book]\nname = 'Acme'\n"),
                         {"header_filters": [], "any_permission": "", "unversioned_notes": {}, "section_titles": {},
                          "non_route_calls": [], "scoping_word": "tenant"})

    def test_the_scoping_word_comes_from_the_book(self):
        # The contracts' preface names the project's own boundary ([book] scoping_word), never a fixed word.
        self.assertEqual(self.settings("[book]\nscoping_word = 'company'\n")["scoping_word"], "company")
        self.assertEqual(self.settings("[book]\nscoping_word = ''\n")["scoping_word"], "tenant")

    def test_non_route_calls_is_a_list_of_map_method_names(self):
        self.assertEqual(self.settings('[api_contracts]\nnon_route_calls = ["MapDomainEvents"]\n')["non_route_calls"],
                         ["MapDomainEvents"])
        for bad in ('non_route_calls = "MapDomainEvents"', 'non_route_calls = ["Configure"]'):
            with self.subTest(bad), self.assertRaises(SystemExit) as raised:
                self.settings(f"[api_contracts]\n{bad}\n")
            self.assertIn("a list of Map… method names", str(raised.exception))

    def test_non_route_calls_never_names_a_framework_route_call(self):
        for name in ("MapGet", "MapPost", "MapMethods", "MapHealthChecks"):
            with self.subTest(name), self.assertRaises(SystemExit) as raised:
                self.settings(f'[api_contracts]\nnon_route_calls = ["MapDomainEvents", "{name}"]\n')
            self.assertIn(f"{name} register routes", str(raised.exception))

    def test_unversioned_notes_and_section_titles_are_tables_of_strings(self):
        found = self.settings('[api_contracts]\nunversioned_notes = { "/welcome" = "a page" }\n'
                              'section_titles = { Fulfilment = "Picking & Dispatch" }\n')
        self.assertEqual(found["unversioned_notes"], {"/welcome": "a page"})
        self.assertEqual(found["section_titles"], {"Fulfilment": "Picking & Dispatch"})
        for bad in ('unversioned_notes = ["/welcome"]', 'section_titles = { Fulfilment = 1 }'):
            with self.subTest(bad), self.assertRaises(SystemExit) as raised:
                self.settings(f"[api_contracts]\n{bad}\n")
            self.assertIn("a table of strings", str(raised.exception))

    def test_a_header_filter_names_its_header_and_variable(self):
        found = self.settings('[api_contracts]\nheader_filters = [{ filter = "KeyFilter", header = "X-Key", '
                              'variable = "key" }]\nany_permission = "IAnyOf"\n')
        self.assertEqual(found["header_filters"], [{"filter": "KeyFilter", "header": "X-Key", "variable": "key"}])
        self.assertEqual(found["any_permission"], "IAnyOf")
        with self.assertRaises(SystemExit):
            self.settings('[api_contracts]\nheader_filters = [{ filter = "KeyFilter" }]\n')

    def test_the_postman_environment_carries_each_header_variable(self):
        import api_contracts_postman as postman
        with mock.patch.object(postman, "HEADER_FILTERS", [{"filter": "KeyFilter", "header": "X-Key", "variable": "key"}]):
            self.assertIn(("key", "REPLACE-with-the-X-Key", True), postman.environment())
        self.assertNotIn("tenantKey", [k for k, _, _ in postman.environment()])


class ApiContractFixtureCase(unittest.TestCase):
    """A throwaway API project the generator's paths point at; `write` adds a source file to it."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.api = self.root / "src" / "App.API"
        application = self.root / "src" / "App.Application"
        (self.api / "Endpoints").mkdir(parents=True)
        for name, value in (("ROOT", self.root), ("SRC", self.root / "src"), ("API", self.api),
                            ("APPLICATION", application), ("ENDPOINTS_DIR", self.api / "Endpoints"),
                            ("PERMISSIONS_FILE", application / "Permissions.cs"),
                            ("SHARED_CONSTANT_FILES", [self.api / "Endpoints" / "ApiRoutes.cs",
                                                       self.api / "Endpoints" / "ApiTags.cs",
                                                       self.api / "Configuration" / "HealthEndpoints.cs"])):
            patch = mock.patch.object(api_contracts, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        # A project's own routes are described in [api_contracts] unversioned_notes.
        notes = mock.patch.dict(api_contracts.UNVERSIONED_NOTES, {"/robots.txt": "crawler rules",
                                                                  "/welcome": "invitation page",
                                                                  "/reset": "reset page"})
        notes.start()
        self.addCleanup(notes.stop)

    def write(self, rel: str, text: str):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class ApiContractResponseSchemaTests(ApiContractFixtureCase):
    """Every response body type, and every type its fields name, gets a field list: the fields a client
    receives, which /check-guides checks a guide's examples against."""

    DTOS = (
        "public sealed record PagedResult<T>(IReadOnlyList<T> Items, int Page, int TotalCount);\n"
        "public sealed record FarmDto(Guid Id, string Name, AddressDto Address, FarmStatus Status, Uri? Website);\n"
        "public sealed record AddressDto(string City);\n"
        "public enum FarmStatus { Active, Inactive }\n"
        "public sealed class AddFarmRequest { public string Name { get; init; } = \"\"; }\n")

    def setUp(self):
        super().setUp()
        self.write("src/App.Application/Farms/FarmDtos.cs", self.DTOS)
        self.types = api_contracts.TypeIndex()
        self.types.build(sorted(self.root.rglob("*.cs")))

    def route(self, *responses: tuple[int, str | None]) -> "api_contracts.Group":
        return api_contracts.Group("Farms", "/api/v1/farms", "Farms", "Endpoints/FarmEndpoints.cs",
                                   [api_contracts.Route("GET", "/api/v1/farms", responses=list(responses))])

    def test_a_generic_wrapper_its_item_and_their_nested_types_are_listed_once(self):
        group = self.route((200, "PagedResult<FarmDto>"), (404, "ProblemDetails"), (200, "IReadOnlyList<FarmDto>"))
        self.assertEqual(["PagedResult", "FarmDto", "AddressDto"],
                         list(api_contracts.response_types([group], self.types, set())))
        self.assertEqual(["Items", "Page", "TotalCount"], [n for n, _, _ in self.types.resolve("PagedResult").fields])

    def test_media_types_framework_shapes_enums_and_request_types_are_not_listed(self):
        group = self.route((200, "image/jpeg"), (400, "ValidationProblemDetails"), (200, "IResult"),
                           (200, "AddFarmRequest"), (200, "FarmStatus"))
        self.assertEqual([], list(api_contracts.response_types([group], self.types, {"AddFarmRequest"})))

    def test_a_declared_type_not_found_is_listed_and_a_field_type_not_found_is_not(self):
        listed = list(api_contracts.response_types([self.route((200, "MissingDto"), (200, "FarmDto"))],
                                                   self.types, set()))
        self.assertEqual(["MissingDto", "FarmDto", "AddressDto"], listed, "Uri is the framework's, left out")
        text = api_contracts.render([self.route((200, "MissingDto"), (200, "FarmDto"))], [], self.types)
        schemas = text.split("## Response schemas", 1)[1]
        self.assertIn("### MissingDto\n\n_(declaration not found", schemas)
        self.assertIn("| `Address` | `AddressDto` |", schemas)
        self.assertIn("- [Response schemas](#response-schemas)", text)


class ApiContractUnversionedRouteTests(ApiContractFixtureCase):
    """A route mapped on the root app in any API file reaches the contracts, and a registration the
    generator cannot parse stops the run (routes under Configuration/ were once missing from the doc)."""

    PAGES = (
        "internal static class PageEndpoints\n{\n"
        '    public const string FileRoute = "/robots.txt";\n\n'
        "    public static WebApplication MapPageEndpoints(this WebApplication app)\n    {\n"
        "        app.MapGet(FileRoute, () => Results.Ok()).AllowAnonymous();\n"
        '        Map(app, LinkRoutes.Welcome, "accept your invitation");\n'
        '        Map(app, LinkRoutes.Reset, "reset your password");\n'
        "        return app;\n    }\n\n"
        "    private static void Map(WebApplication app, string route, string purpose)\n    {\n"
        '        app.MapGet($"/{route}", (HttpContext context) => Results.Content(purpose))\n'
        "            .AllowAnonymous()\n            .ExcludeFromDescription();\n    }\n}\n")
    ROUTES = (
        "public static class LinkRoutes\n{\n"
        '    public const string Welcome = "welcome";\n'
        '    public const string Reset = "reset";\n}\n')
    STARTUP = "internal static class Startup\n{\n    static void Run(WebApplication app) { app.MapPageEndpoints(); }\n}\n"

    def setUp(self):
        super().setUp()
        self.write("src/App.Application/Configuration/LinkRoutes.cs", self.ROUTES)
        self.write("src/App.API/Configuration/PageEndpoints.cs", self.PAGES)
        self.write("src/App.API/Configuration/Startup.cs", self.STARTUP)

    def parse(self):
        types = api_contracts.TypeIndex()
        types.build(sorted(self.root.rglob("*.cs")))
        return api_contracts.parse_unversioned_routes(types)

    def problems(self) -> list[str]:
        _, calls = self.parse()
        return api_contracts.unparsed_route_calls(api_contracts.api_files(), calls)

    def test_a_route_mapped_in_a_configuration_file_is_listed(self):
        routes, calls = self.parse()
        self.assertEqual([(r.method, r.path, r.file) for r in routes], [
            ("GET", "/robots.txt", "Configuration/PageEndpoints.cs"),
            ("GET", "/welcome", "Configuration/PageEndpoints.cs"),
            ("GET", "/reset", "Configuration/PageEndpoints.cs"),
        ])
        self.assertEqual(calls, {"src/App.API/Configuration/PageEndpoints.cs": 2})

    def test_every_registration_parsed_passes_the_cross_check(self):
        # MapPageEndpoints is the API's own extension method, not a route registration.
        self.assertEqual(self.problems(), [])

    def test_a_registration_the_generator_did_not_parse_fails_the_cross_check(self):
        self.write("src/App.API/Configuration/Fallback.cs",
                   "internal static class Fallback\n{\n    static void Map(WebApplication app)\n    {\n"
                   "        app.MapFallback(() => Results.NotFound());\n"
                   '        app.MapMethods("/x", ["HEAD"], () => Results.Ok());\n    }\n}\n')
        problems = self.problems()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("src/App.API/Configuration/Fallback.cs: 2 route registrations", problems[0])
        self.assertIn("MapFallback, MapMethods", problems[0])

    def test_a_plain_map_registration_fails_the_cross_check_whatever_its_pattern(self):
        # A literal, a verbatim string, a local, a lower-case constant and a named argument all map a route.
        for pattern in ('"/any"', '@"/any"', "route", "anyRoute", 'pattern: "/any"', "Routes.Any"):
            with self.subTest(pattern):
                self.write("src/App.API/Configuration/AnyVerb.cs",
                           "internal static class AnyVerb\n{\n    static void Run(WebApplication app, string route)\n"
                           f"    {{\n        app.Map({pattern}, () => Results.Ok()).AllowAnonymous();\n    }}\n}}\n")
                self.assertEqual(self.problems(), ["src/App.API/Configuration/AnyVerb.cs: 1 route registrations (Map) "
                                                   "but 0 parsed"])

    def test_the_iendpoint_map_call_is_not_a_route_registration(self):
        for body in ("static void Run(IEndpoint endpoint, object root, object set)\n    {\n"
                     "        endpoint.Map(root, set);\n    }\n",
                     "static void Run(WebApplication app, object root, object set)\n    {\n"
                     "        foreach (var endpoint in app.Services.GetRequiredService<IEnumerable<IEndpoint>>())\n"
                     "        {\n            endpoint.Map(root, set);\n        }\n    }\n"):
            with self.subTest(body.split("(")[1]):
                self.write("src/App.API/Configuration/Endpoints.cs",
                           f"internal static class Endpoints\n{{\n    {body}}}\n")
                self.assertEqual(self.problems(), [])

    def test_a_map_on_anything_but_an_iendpoint_still_counts(self):
        self.write("src/App.API/Configuration/Endpoints.cs",
                   "internal static class Endpoints\n{\n    static void Run(IEndpoint endpoint, WebApplication app)\n"
                   "    {\n        endpoint.Map(app, null);\n        app.Map(endpointRoute, () => 1);\n    }\n}\n")
        self.assertEqual(self.problems(), ["src/App.API/Configuration/Endpoints.cs: 1 route registrations (Map) "
                                           "but 0 parsed"])

    def test_a_helper_called_with_the_wrong_arguments_stops_the_run(self):
        self.write("src/App.API/Configuration/PageEndpoints.cs",
                   self.PAGES.replace('Map(app, LinkRoutes.Reset, "reset your password");', "Map(app, LinkRoutes.Reset);"))
        with self.assertRaises(SystemExit) as raised:
            self.parse()
        self.assertIn("passes 2 arguments where the helper", str(raised.exception))
        self.assertIn("takes 3", str(raised.exception))

    def test_a_helper_whose_route_is_never_given_stops_the_run(self):
        pages = self.PAGES
        for line in ('        Map(app, LinkRoutes.Welcome, "accept your invitation");\n',
                     '        Map(app, LinkRoutes.Reset, "reset your password");\n'):
            pages = pages.replace(line, "")
        self.write("src/App.API/Configuration/PageEndpoints.cs", pages)
        with self.assertRaises(SystemExit) as raised:
            self.parse()
        self.assertIn("no call of it was found", str(raised.exception))
        self.assertIn("a static helper's calls in its own file", str(raised.exception))

    def test_an_extension_method_helper_is_bound_from_any_api_file(self):
        self.write("src/App.API/Configuration/PageEndpoints.cs",
                   "internal static class PageEndpoints\n{\n"
                   "    public static void MapPage(this WebApplication app, string route)\n    {\n"
                   '        app.MapGet($"/{route}", () => Results.Ok()).AllowAnonymous();\n    }\n}\n')
        self.write("src/App.API/Configuration/Startup.cs",
                   "internal static class Startup\n{\n    static void Run(WebApplication app)\n    {\n"
                   "        app.MapPage(LinkRoutes.Welcome);\n        app.MapPage(LinkRoutes.Reset);\n    }\n}\n")
        routes, _ = self.parse()
        self.assertEqual([r.path for r in routes], ["/welcome", "/reset"])
        # MapPage is the API's own method, so neither file counts it as a registration.
        self.assertEqual(self.problems(), [])

    PAGE_HELPER = ("internal static class PageEndpoints\n{\n"
                   "    public static void MapPage(this WebApplication app, string route)\n    {\n"
                   '        app.MapGet($"/{route}", () => Results.Ok()).AllowAnonymous();\n    }\n}\n')

    def test_an_extension_helper_called_in_its_static_form_binds(self):
        self.write("src/App.API/Configuration/PageEndpoints.cs", self.PAGE_HELPER)
        self.write("src/App.API/Configuration/Startup.cs",
                   "internal static class Startup\n{\n    static void Run(WebApplication app)\n    {\n"
                   "        app.MapPage(LinkRoutes.Welcome);\n        PageEndpoints.MapPage(app, LinkRoutes.Reset);\n"
                   "    }\n}\n")
        routes, _ = self.parse()
        self.assertEqual(sorted(r.path for r in routes), ["/reset", "/welcome"])

    def test_an_overload_of_an_extension_helper_is_named_in_the_message(self):
        self.write("src/App.API/Configuration/PageEndpoints.cs", self.PAGE_HELPER)
        self.write("src/App.API/Configuration/Startup.cs",
                   "internal static class Startup\n{\n    static void Run(WebApplication app)\n    {\n"
                   "        app.MapPage(LinkRoutes.Welcome);\n        app.MapPage(LinkRoutes.Reset, true);\n"
                   "    }\n}\n")
        with self.assertRaises(SystemExit) as raised:
            self.parse()
        self.assertIn("If it is another overload of MapPage, give that overload its own name", str(raised.exception))

    def test_map_prefixed_calls_that_register_no_route_are_not_counted(self):
        self.write("src/App.API/Configuration/Versioning.cs",
                   "internal static class Versioning\n{\n    static void Run(WebApplication app, RouteGroupBuilder group)\n"
                   '    {\n        group.MapGet("/v", () => 1).MapToApiVersion(1);\n'
                   "        app.MapWhen(c => c.Request.IsHttps, b => { });\n"
                   "        options.ClaimActions.MapJsonKey(\"role\", \"role\");\n"
                   "        config.NewConfig<A, B>().Map(dest => dest.Name, src => src.Title);\n"
                   "        config.NewConfig<A, B>().Map((dest) => dest.Id, src => src.Key);\n    }\n}\n")
        # Only the MapGet counts — and it is unparsed here, since /v has no AllowAnonymous chain to read.
        problems = api_contracts.unparsed_route_calls(api_contracts.api_files(),
                                                      {"src/App.API/Configuration/PageEndpoints.cs": 2})
        self.assertEqual(problems, ["src/App.API/Configuration/Versioning.cs: 1 route registrations (MapGet) "
                                    "but 0 parsed"])

    def test_a_project_names_its_own_non_route_calls(self):
        self.write("src/App.API/Configuration/Events.cs",
                   "internal static class Events\n{\n    static void Run(WebApplication app) { app.MapDomainEvents(); }\n}\n")
        self.assertEqual(len(self.problems()), 1)
        with mock.patch.object(api_contracts, "NON_ROUTE_CALLS", api_contracts.NON_ROUTE_CALLS | {"MapDomainEvents"}):
            self.assertEqual(self.problems(), [])

    def test_an_interpolated_hole_that_cannot_be_read_stops_the_run(self):
        with self.assertRaises(SystemExit) as raised:
            api_contracts.resolve_string('$"/{Unknown.Route}"', {}, "x.cs")
        self.assertIn("cannot resolve '{Unknown.Route}'", str(raised.exception))

    def test_a_helper_named_like_a_framework_call_does_not_hide_it_from_the_cross_check(self):
        self.write("src/App.API/Configuration/Shadow.cs",
                   "internal static class Shadow\n{\n"
                   "    public static void MapGet(this WebApplication app) { }\n"
                   '    static void Run(WebApplication app) { app.MapGet("/x", () => 1); }\n}\n')
        problems = api_contracts.unparsed_route_calls(api_contracts.api_files(), {})
        self.assertTrue(any(p.startswith("src/App.API/Configuration/Shadow.cs: 1 route") for p in problems), problems)

    def test_a_protected_route_outside_the_endpoint_files_stops_the_run(self):
        self.write("src/App.API/Configuration/PageEndpoints.cs",
                   self.PAGES.replace("() => Results.Ok()).AllowAnonymous();", "() => Results.Ok());"))
        with self.assertRaises(SystemExit) as raised:
            self.parse()
        self.assertIn("/robots.txt", str(raised.exception))
        self.assertIn("AllowAnonymous", str(raised.exception))

    def test_a_route_with_no_note_stops_the_run_naming_the_setting(self):
        self.write("src/App.API/Configuration/Extra.cs",
                   "internal static class Extra\n{\n    static void Run(WebApplication app)\n    {\n"
                   '        app.MapGet("/new-page", () => Results.Ok()).AllowAnonymous();\n    }\n}\n')
        with self.assertRaises(SystemExit) as raised:
            self.parse()
        self.assertIn("/new-page has no note", str(raised.exception))
        self.assertIn("[api_contracts] unversioned_notes", str(raised.exception))


class ApiContractRepositoryRoutesTests(ApiContractFixtureCase):
    """A whole API project, endpoint files and Configuration/ alike, through collect() and render():
    every route reaches the contracts, and one the generator cannot parse stops the run."""

    SOURCES = {
        "src/App.Application/Permissions.cs":
            "public static class Permissions\n{\n    public static class Widgets\n    {\n"
            '        public const string Read = "widgets.read";\n    }\n}\n',
        "src/App.Application/Widgets/ListWidgetsQuery.cs":
            "public sealed record ListWidgetsQuery() : IRequirePermission\n{\n"
            "    public string Permission => Permissions.Widgets.Read;\n}\n",
        "src/App.API/Endpoints/ApiRoutes.cs":
            'public static class ApiRoutes\n{\n    public const string Versioned = "/api/v{version:apiVersion}";\n}\n',
        "src/App.API/Endpoints/ApiTags.cs":
            'public static class ApiTags\n{\n    public const string Widgets = "Widgets";\n}\n',
        "src/App.API/Endpoints/Widgets/WidgetEndpoints.cs":
            "internal sealed class WidgetEndpoints : IEndpoint\n{\n"
            "    public void Map(IEndpointRouteBuilder builder, ApiVersionSet versionSet)\n    {\n"
            '        var group = builder.MapGroup(ApiRoutes.Versioned + "/widgets").WithTags(ApiTags.Widgets);\n'
            '        group.MapGet("/", async (ISender sender, CancellationToken cancellationToken) =>\n'
            "            {\n                var result = await sender.Send(new ListWidgetsQuery(), cancellationToken);\n"
            "                return result.Match(Results.Ok);\n            })\n"
            # MapToApiVersion versions the route; it registers none, so the cross-check must not count it.
            '            .WithName("ListWidgets")\n            .MapToApiVersion(1);\n    }\n}\n',
        "src/App.API/Configuration/HealthEndpoints.cs":
            "internal static class HealthEndpoints\n{\n"
            '    public const string Root = "/health";\n    public const string Live = Root + "/live";\n\n'
            "    public static WebApplication MapHealthEndpoints(this WebApplication app)\n    {\n"
            "        app.MapHealthChecks(Live, new HealthCheckOptions()).AllowAnonymous();\n"
            "        return app;\n    }\n}\n",
        "src/App.API/Configuration/EndpointExtensions.cs":
            "internal static class EndpointExtensions\n{\n"
            "    public static WebApplication MapEndpoints(this WebApplication app)\n    {\n"
            "        var root = app.MapGroup(string.Empty);\n"
            "        foreach (var endpoint in app.Services.GetRequiredService<IEnumerable<IEndpoint>>())\n"
            "        {\n            endpoint.Map(root, versionSet);\n        }\n        return app;\n    }\n}\n",
        "src/App.API/Configuration/PageEndpoints.cs":
            "internal static class PageEndpoints\n{\n"
            "    public static WebApplication MapPageEndpoints(this WebApplication app)\n    {\n"
            '        app.MapGet("/welcome", () => Results.Ok()).AllowAnonymous();\n        return app;\n    }\n}\n',
        "src/App.API/Configuration/ApplicationBuilderExtensions.cs":
            "internal static class ApplicationBuilderExtensions\n{\n"
            "    public static WebApplication UseApi(this WebApplication app)\n    {\n"
            "        app.MapOpenApi().AllowAnonymous();\n        app.MapHealthEndpoints();\n"
            "        app.MapPageEndpoints();\n        app.MapEndpoints();\n        return app;\n    }\n}\n",
    }

    def setUp(self):
        super().setUp()
        for rel, text in self.SOURCES.items():
            self.write(rel, text)

    def test_every_route_in_the_api_project_reaches_the_contracts(self):
        groups, unversioned, types = api_contracts.collect()
        self.assertEqual([(r.method, r.path, r.permission) for g in groups for r in g.routes],
                         [("GET", "/api/v1/widgets", "widgets.read")])
        self.assertEqual({(r.path, r.file) for r in unversioned}, {
            ("/openapi/v1.json", "Configuration/ApplicationBuilderExtensions.cs"),
            ("/health/live", "Configuration/HealthEndpoints.cs"),
            ("/welcome", "Configuration/PageEndpoints.cs"),
        })
        doc = api_contracts.render(groups, unversioned, types)
        self.assertIn("| GET | `/welcome` | invitation page | `Configuration/PageEndpoints.cs` |", doc)

    def test_a_plain_map_in_a_configuration_file_stops_the_run(self):
        self.write("src/App.API/Configuration/PageEndpoints.cs",
                   self.SOURCES["src/App.API/Configuration/PageEndpoints.cs"].replace(
                       'app.MapGet("/welcome"', "app.Map(welcomeRoute"))
        with self.assertRaises(SystemExit) as raised:
            api_contracts.collect()
        self.assertIn("src/App.API/Configuration/PageEndpoints.cs: 1 route registrations (Map) but 0 parsed",
                      str(raised.exception))

    def test_a_section_title_comes_from_the_settings(self):
        with mock.patch.dict(api_contracts.STAGE_TITLES, {"Widget": "Gadgets & Gizmos"}):
            self.write("src/App.API/Endpoints/Widgets/WidgetExtraEndpoints.cs",
                       self.SOURCES["src/App.API/Endpoints/Widgets/WidgetEndpoints.cs"]
                       .replace("WidgetEndpoints", "WidgetExtraEndpoints").replace('"/widgets"', '"/widgets/extra"')
                       .replace('"ListWidgets"', '"ListExtraWidgets"'))
            groups, _, _ = api_contracts.collect()
        self.assertIn("Widgets — Gadgets & Gizmos", [g.heading for g in groups])

