"""
A full project's factory.toml for the self-tests: the dotnet stack with the deploy, bitbucket and fe-guides
packs, and a module map — what the tools are built for, which a stack-free Book's own factory.toml (stack
"none", no modules) cannot exercise. A test that checks this repository's own configuration reads the real
factory.toml instead.
"""
from __future__ import annotations

import tomllib

PROJECT_TOML = r'''
# The AI tools' configuration for this repository: what the guard refuses or asks about in
# every Claude Code session, how a changed path maps to a module, who owns the files that steer
# the AI, where Jira is, and what the branch restrictions must be.
#
# Read by factory.py (the tools) and by hooks/guard.py. A person confirms every change to this
# file — the guard asks.

base_ref = "origin/dev"
protected_branches = ["dev", "qa", "uat", "main"]
# Each person's private settings file — outside every repository, never read by a session
# (CLAUDE.md, Private settings). APP_LOCAL_SETTINGS overrides it.
private_settings = "~/.app/local.toml"

# This project's own names, for `factory book verify` (a name the Book template carries from another
# project is a leftover; these are not) and the names `factory book init` wrote.
[book]
name = "App"
slug = "app"
env_prefix = "APP_"

# ---------------------------------------------------------------- the guard
#
# What hooks/guard.py refuses or asks about in every Claude Code session — this
# repository's "never" rules as data, so the guard itself carries none. Paths are
# case-insensitive globs from the repository root; `**` spans folders.

[guard]
# Change an existing file: refused. Add a new one: allowed. `maintained` files may change.
read_only = ["requirements/**"]
# The trackers (not requirements) keep their current copy at a plain filename; /book:check-sources
# moves it into backup/ and writes the new one in its place. Named exactly: the guard's `*` spans
# folders, and backup/ must stay read-only.
read_only_maintained = ["requirements/read-me.txt", "requirements/**/read-me.txt",
                        "requirements/qa-artifacts/Shop App Test Plan - Release 1.pdf",
                        "requirements/qa-artifacts/App_Smoke_and_Dev_Checklist.xlsx",
                        "requirements/qa-artifacts/App_Detailed_Test_Cases.xlsx",
                        "requirements/release-plan/app - Release Plan v1.1.csv"]
# Add the exact path of every other live tracker the project keeps at a plain name (a release plan,
# a bug list), so /book:check-sources may edit it in place. Its backups stay read-only.
read_only_reason = "is a snapshot of a client document and is read-only. Only /book:check-sources adds to requirements/, as a new file."
# A file matching these that already exists on base_ref may not change (an applied migration).
applied = ["src/App.Persistence/Migrations/*.cs"]
applied_reason = "is a migration already on dev. Never edit an applied migration; add a new one."
never_by_hand = ["src/App.Persistence/Migrations/*ModelSnapshot.cs"]
never_by_hand_reason = "is written by `dotnet ef migrations add`, never by hand."
# A person confirms every change to these: the files that steer the AI — the guard, the tools, the
# checks, git hooks, CI, everything under .claude/ (settings, skills, agents, commands, hooks), the
# instruction files. The same paths as [ai_files]. `**/CLAUDE.md` reaches a folder removed whole only
# where one is found in it.
ask = ["CLAUDE.md", "**/CLAUDE.md", "AGENTS.md", "AI-DEVELOPMENT-GUIDE.md",
       ".claude/**", ".cursor/**", ".githooks/**", "bitbucket-pipelines.yml",
       "devtools/factory/**", "devtools/checks/**"]
# Of those, changed with ordinary feature work (as [ai_files] except): a new endpoint's Postman journey.
ask_except = ["devtools/checks/api_contracts_postman.py"]
# A remote command (ssh, compose, az, kubectl…) naming one of these is a deploy beyond DEV/QA.
non_dev_targets = ["uat", "prod", "production", "preprod", "staging"]
# Stack rules that ship with the guard; each is on or off for this repository.
dotnet_ef = true          # no --startup-project; no --connection to a non-local host
appsettings_secrets = true  # check-appsettings-secrets.sh's three rules on written content
# Home folders of shared service accounts, not of a person — a path under one may be written down
# (a server's deploy account runs its backups from it). Every other home-folder path is
# personal and is refused in repository content (CLAUDE.md, Private settings). Set per project.
service_accounts = ["deploy"]
# Google Drive (CLAUDE.md, Skills): the FE guides reach Drive only through /publish-guides's rclone
# script, which updates each Doc in place and never deletes one. A Drive connector's MCP tool — one whose
# name `drive_connector` matches (a regex; the hook's matcher in .claude/settings.json must reach it too)
# — is refused unless its own name starts with one of `drive_read_verbs`. An rclone command in
# `rclone_deleting` naming a remote, and a move off a remote, are refused; a `--dry-run` passes. Every
# remote counts: each person names the FE guides' remote themselves (`gdrive` by default).
drive_connector = '(?i)^mcp__.*drive'
drive_read_verbs = ["read", "search", "list", "get", "fetch", "download", "export", "find"]
rclone_deleting = ["delete", "deletefile", "purge", "rmdir", "rmdirs", "cleanup", "dedupe", "sync", "bisync"]
drive_reason = "The FE guides are published only by /publish-guides (rclone), which updates each Doc in place; a session never creates, moves or trashes a Doc on Drive — a Doc to remove is the person's to trash by hand (CLAUDE.md, Skills). Reading Drive is allowed."

# Tools decided against in this repository: installing one (pip, pipx, uv, npm, dotnet tool, winget…) or
# running it (by name, `python -m`, `uvx`, `pipx run`, `npx`) is refused with the reason. `names` are
# globs over the package and program names.
[[guard.banned_tools]]
names = ["graphify*"]
reason = "was trialled and removed from this repository : it answered one of four real questions that grep/ripgrep and Roslyn answered, and a graph not rebuilt after every change is a stale doc that is trusted. Search the code directly."

# Generated files — never hand-edited; the guard names the command that writes each.
[guard.generated]
"docs/api/api-contracts.md" = "python3 devtools/checks/gen-api-contracts.py"
"devtools/api/postman/app.postman_collection.json" = "python3 devtools/checks/gen-api-contracts.py"
"devtools/api/postman/app-local.postman_environment.json" = "python3 devtools/checks/gen-api-contracts.py"
"docs/database-erd.md" = "python3 devtools/checks/gen-database-erd.py"

# ---------------------------------------------------------------- requirements
#
# A requirement id is `<id_prefix>-<section>-R<n>` (FSD-2.13.9-R04); `factory impact` finds the ids
# the traceability matrix ties to the changed files.

[requirements]
id_prefix = "FSD"
traceability = "docs/Audit/spec-traceability.md"

# `factory fsd-threads`: the live FSD Doc against the last check. The Doc's link is the line of
# `links_file` starting with `fsd_link_label`; the last check is recorded in `live_check_record`.
# The credential is a person's, so it is not here: `live_credential` lives in their private settings
# file (`~/.app/local.toml`, CLAUDE.md "Private settings") under [requirements.sources] — e.g.
# "rclone:gdrive-readonly", an rclone remote made with `scope=drive.readonly` for this alone.
[requirements.sources]
fsd_dir = "requirements/fsd"
live_check_record = "requirements/fsd/read-me.txt"
links_file = "requirements/read-me.txt"
fsd_link_label = "FSD"
# `factory requirements status`: LINKS sources /book:check-sources does not compare live (its
# "Out of scope"), by folder.
out_of_scope = ["wireframes"]

# `factory requirements status`: the folder under requirements/ that holds each LINKS line's
# snapshots. A line belongs to the longest key its label starts with, case ignored.
[requirements.sources.folders]
"FSD" = "fsd"
"Permissions matrix" = "permissions-matrix"
"Product Q&A" = "product-questions-and-answers"
"QA" = "qa-artifacts"
"FE integration bugs" = "fe-integration-bugs"
"Release plan" = "release-plan"
"Wireframes" = "wireframes"
"Client documents" = "client-documents"

# The FE team's existing Google Drive folder the FE guides are published to (/publish-guides) —
# its one owner: the skill and Publish-FeGuidesToDrive.ps1 read it from here, through
# `python devtools/factory/factory.py config fe_guides.drive_folder_id`, and never repeat it.
# `factory book verify` (N17) checks that. Folder link: https://drive.google.com/drive/folders/<id>
[fe_guides]
# Not the template's placeholder (manifest [source] fe_drive_folder_id), which init replaces in a copied Book.
drive_folder_id = "fixture-drive-folder-1"

# What a change reaches beyond its modules: a generated doc to regenerate, and changes that
# cannot be undone by reverting (a migration is forward-only). `match` is a regex over a path.
# A route may be mapped in any C# file of the API project (Configuration/ as well as Endpoints/).
[[impact.regenerate]]
match = '^src/[^/]+\.API/.*\.cs$|/Features/.*/Dtos/'
note = "docs/api/api-contracts.md — python3 devtools/checks/gen-api-contracts.py"

[[impact.regenerate]]
match = 'Persistence/Migrations/'
note = "docs/database-erd.md — python3 devtools/checks/gen-database-erd.py"

[impact]
# Beyond src/, what reaches every module and so runs the whole in-process integration suite
# (CLAUDE.md "Building and testing": packages, build settings, shared test fixtures).
cross_cutting = ["Directory.Packages.props", "Directory.Build.props", "global.json", "*.slnx",
                 "tests/*/Fixtures/**", "tests/*/GlobalUsings.cs",
                 # The integration tests run the seed scripts; .editorconfig decides which warnings fail the build.
                 "database-scripts/**", "**/.editorconfig"]
one_way = ['Persistence/Migrations/']
one_way_note = "**Contains a migration — forward-only in production.** Rolling back is a new migration, not a revert."

# The risk tier `/book:impact` names: the highest any touched path reaches. Case-insensitive
# regular expressions.
[tier]
high = [
  "Features/(Auth|Users|Memberships)/",
  "Endpoints/(Auth|Users|Memberships)/",
  "/Security/",
  "/Authentication/",
  "/Authorization/",
  "PermissionChecker",
  "/Tenancy/",
  "Persistence/Migrations/",
  "ApplicationDbContext",
  "Persistence/Interceptors/",
  "QueryFilter",
  "appsettings",
  "Dockerfile",
  "compose\\.",
]
medium = [
  "^src/",
  "^database-scripts/",
]
# The only paths the `checks` tier (no build, no tests) may hold — globs. Anything else runs at
# least the build (docs/plans/local-ci-tiers.md §3).
checks_only = ["docs/**", "requirements/**", "devtools/**", ".githooks/**", ".claude/**", "**/*.md"]

# ---------------------------------------------------------------- the review checklist's parts

# `factory review-scope`: which parts of the review checklist a change needs read. Each row is a row
# of the scope-triage table in review-method.md (`row` is its first cell, word for word; a test holds
# the two equal), with the globs that place a path in it and the Pass B dimensions its "Normally N/A"
# column names. A dimension is left out only when every row a path matched lists it N/A; a path no row
# places loads every Pass B file. The method and Pass A always load.
[review_scope]
folder = ".claude/review"
always = ["book:peer-review/references/review-method.md", "book:peer-review/references/review-pass-a.md"]

[review_scope.dimensions]
B1 = "review-b1-architecture.md"
B2 = "review-b2-code-quality.md"
B3 = "review-b3-api.md"
B4 = "review-b4-security.md"
B5 = "review-b5-data.md"
B6 = "review-b6-performance.md"
B7 = "review-b7-async.md"
B8 = "review-b8-error-handling.md"
B9 = "review-b9-testing.md"
B10 = "review-b10-observability.md"
B11 = "review-b11-configuration.md"

[[review_scope.rows]]
row = "Product code under `src/`"
match = ["src/*"]
not_applicable = []

[[review_scope.rows]]
row = "An endpoint, request/response DTO or permission"
match = ["src/App.API/*", "src/*Dto.cs", "src/*Request.cs", "src/*Response.cs", "src/*Permission*"]
not_applicable = []

[[review_scope.rows]]
row = "A migration"
match = ["src/App.Persistence/Migrations/*"]
not_applicable = []

[[review_scope.rows]]
row = "Tests only"
match = ["tests/*"]
not_applicable = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8"]

[[review_scope.rows]]
row = "Docs only (`docs/`, other `*.md`)"
match = ["docs/*", "*.md"]
not_applicable = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "B11"]

[[review_scope.rows]]
row = "Tooling (`devtools/`, `.claude/`, `.githooks/`, CI)"
match = ["devtools/*", ".claude/*", ".githooks/*", "bitbucket-pipelines.yml"]
# B1 (the .NET layers), B3 (HTTP endpoints) and B5 (EF Core and the database) check what tooling
# does not contain. B6 stays: the guard runs on every tool call, so its cost matters.
not_applicable = ["B1", "B3", "B5"]

[[review_scope.rows]]
row = "A `CLAUDE.md` (root or nested)"
match = ["CLAUDE.md", "*/CLAUDE.md"]
not_applicable = []

[[review_scope.rows]]
row = "`requirements/**`"
match = ["requirements/*"]
not_applicable = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "B11"]

[[review_scope.rows]]
row = "`database-scripts/`, `scripts/`"
match = ["database-scripts/*", "scripts/*"]
not_applicable = []

[[review_scope.rows]]
row = "Root build and deploy config — `Directory.Packages.props`, `compose*.yaml`, `Dockerfile`, `appsettings*.json`, `bitbucket-pipelines.yml`"
match = ["Directory.Packages.props", "compose*.yaml", "*Dockerfile*", "*appsettings*.json", "bitbucket-pipelines.yml"]
not_applicable = []

[[review_scope.rows]]
row = "Generated files — `docs/api/api-contracts.md`, `docs/database-erd.md`, a migration's `.Designer.cs`, the model snapshot"
match = ["docs/api/api-contracts.md", "docs/database-erd.md", "*.Designer.cs", "*ModelSnapshot.cs"]
not_applicable = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "B11"]

# ---------------------------------------------------------------- the files that steer the AI, Jira, Bitbucket

# The files that steer the AI — its rules, skills, the tools, the guard, the checks, the
# hooks and CI. A pull request that changes one passes `factory ai-files` (CI, every PR) only when an
# owner made it, or an owner approved it after its latest commit — the destination branch's list,
# never the PR's own. Owners are Bitbucket account ids
# (`factory pr whoami`): a display name anyone can copy, an id no one can.
[ai_files]
# One { name = "...", account_id = "..." } per owner (`factory pr whoami` prints the id). An empty
# list matches no account, so a pull request that changes an AI file cannot pass — it fails closed.
owners = [
    { name = "Owner One", account_id = "account-one" },
    { name = "Owner Two", account_id = "account-two" },
]
paths = [
  "CLAUDE.md", "**/CLAUDE.md", "AGENTS.md", "AI-DEVELOPMENT-GUIDE.md",
  ".claude/**", ".cursor/**", ".githooks/**", "bitbucket-pipelines.yml",
  "devtools/factory/**", "devtools/checks/**",
]
# Changed with ordinary feature work: a new endpoint's place in the Postman journey and its sample.
except = ["devtools/checks/api_contracts_postman.py"]

# Where `factory jira <key>` reads a Jira issue from. Read only.
# The credential is the person's own, in git's credential store for this site (devtools/README.md).
[jira]
site = "tracker.example"
project = "PROJ"

# What `factory protection --check` holds the live branch restrictions to: the rules
# the architecture guide's branch-protection table decides. `kinds` must exist on every branch listed;
# `minimums` must exist with at least that value. Exemptions on push and merge are
# reported for a person to confirm, never failed on.
[protection]
branches = ["dev", "qa", "uat", "main"]
kinds = ["delete", "force", "push"]

[protection.minimums]
require_approvals_to_merge = 1
require_passing_builds_to_merge = 1

# While CI is switched off, devtools/checks/ci-local.sh is the gate before a pull
# request: `factory pr open` wants its PASS for the very commit it opens, and `factory pr-check` a "Local
# CI" section naming the pull request's head commit. How deep that run goes is `factory impact --ci`, from
# the [[modules]] below and [impact] and [tier] above (docs/plans/local-ci-tiers.md). Set
# `required = false` once CI runs on your own runner.
[local_ci]
required = true

# ---------------------------------------------------------------- deploy
#
# `factory deploy` (deploy.py), the steps /deploy runs. Addresses and paths, never secrets: the
# server's `.env` holds those and is never read. **DEV and QA are the only tables, and the host is
# fixed here** — no argument names a host, so nothing can point a deploy anywhere else (the deployment
# plan §9 and the product runbook own these values). A `<placeholder>` stops the deploy and says which.

[deploy]
ssh_host = "deploy-host.example"
ssh_user = "svc-app"
ssh_key = "~/.ssh/id_ed25519_app_dev"
backup_root = "/srv/app/backups"
backup_environments = ["dev", "qa"]   # both, whichever is deployed: they share the box and the disk
ci_daily = { folder = "~/app-ci-daily", refs = ["dev"] }
min_free_gb = 10                      # the box has 98 GB, not the 256 the specification asked for
container_uid = "1654"                # the API's user; its bind mounts and volumes must be its own
bind_mounts = ["mail-drop", "logs"]
volumes = ["uploads"]
protected_probe = "/api/v1/memberships"   # must answer 401 with no token (a real route: an unmatched one is 401 too)
migrations = "src/App.Persistence/Migrations"
settings_check = "devtools/checks/check-required-settings.sh"
gate_filter = "Category!=Smoke&Category!=S3"
smoke_filter = "Category=Smoke"
test_db_variable = "APP_TEST_DB"
smoke_url_variable = "APP_DEPLOYED_API_URL"
smoke_credentials = ["APP_DEPLOYED_API_EMAIL", "APP_DEPLOYED_API_PASSWORD", "APP_DEPLOYED_API_TENANT_KEY"]
# The only two reasons a smoke test may skip, by the start of its message; any other skip fails the run.
smoke_skip_reasons = { "No deployed API" = "URL not set", "No account to log in as" = "no credentials", "No reviewer account" = "no reviewer account" }

[deploy.tests]
unit = "tests/App.UnitTests"
architecture = "tests/App.ArchitectureTests"
integration = "tests/App.IntegrationTests"

# The host is shared with another project (8080, 8081, 443, 8443, 5432) and Jenkins (8090). Never touch a
# container, network or volume not named app*; never `docker system prune` or `down -v`.
[deploy.DEV]
directory = "/srv/app/dev/backend"
compose_files = ["compose.yaml", "compose.dev.yaml"]  # the overlay supplies the signing key and LAN CORS origin
profile = "api"
project = "app"
settings_environment = "Development"  # compose.yaml's; there is no appsettings.DEV.json
api_port = 8280
public_url = ""                        # none: the LAN address is reachable

[deploy.QA]
directory = "/srv/app/qa/backend"
compose_files = ["compose.yaml", "compose.qa.yaml"]
profile = ""                           # the overlay clears the profiles
project = "app-qa"
settings_environment = "QA"
api_port = 8281
public_url = ""

# ---------------------------------------------------------------- modules
#
# How a changed path maps to a module: `match` is a case-insensitive regular
# expression over the path. Order matters — the first match wins, so put a narrow
# module before a broader one.

[[modules]]
name = "Companies"
match = "Compan(y|ies)/"
tests = "FullyQualifiedName~Features.Companies"
guide = "docs/fe-integration-guides/companies-and-locations.md"
plan = "docs/plans/companies-and-memberships.md"

[[modules]]
name = "Geography"
match = "Geography/"
tests = "FullyQualifiedName~Features.Geography"
guide = "docs/fe-integration-guides/geography.md"
plan = ""

[[modules]]
name = "Roles"
match = "Roles?/|RolePermission"
tests = "FullyQualifiedName~Features.Roles"
guide = "docs/fe-integration-guides/roles.md"
plan = "docs/plans/authorization.md"

[[modules]]
name = "Documents"
match = "Documents?/|/Rendering/|/Templates/"
tests = "FullyQualifiedName~Features.Documents"
guide = "docs/fe-integration-guides/documents.md"
plan = "docs/plans/documents.md"

[[modules]]
name = "Profile and Preferences"
match = "(Profile|Preferences)/"
tests = "FullyQualifiedName~Features.Users&FullyQualifiedName~Profile"
guide = "docs/fe-integration-guides/profile-and-preferences.md"
plan = "docs/plans/profile-and-preferences.md"

[[modules]]
name = "Authentication"
match = "(Features|Endpoints)/Auth/|/Authentication/"
tests = "FullyQualifiedName~Features.Auth"
guide = "docs/fe-integration-guides/authentication.md"
plan = "docs/plans/authentication.md"

[[modules]]
name = "Users"
match = "Users?/"
tests = "FullyQualifiedName~Features.Users"
guide = "docs/fe-integration-guides/users.md"
plan = "docs/plans/users.md"

[[modules]]
name = "Memberships"
match = "Memberships?/"
tests = "FullyQualifiedName~Features.Memberships"
guide = "docs/fe-integration-guides/users.md"
plan = "docs/plans/companies-and-memberships.md"

[[modules]]
name = "Stores"
match = "Stores?/"
tests = "FullyQualifiedName~Features.Stores"
guide = "docs/fe-integration-guides/stores.md"
plan = "docs/plans/stores.md"

[[modules]]
name = "Order events"
match = "OrderEvents?/"
tests = "FullyQualifiedName~Features.OrderEvents"
guide = "docs/fe-integration-guides/order-events.md"
plan = "docs/plans/order-events.md"

[[modules]]
name = "Activity log"
match = "ActivityLog/"
tests = "FullyQualifiedName~Features.ActivityLog"
guide = "docs/fe-integration-guides/activity-log.md"
plan = "docs/plans/activity-log.md"


[commands]
build = "dotnet build App.slnx --configuration Release"
test = "dotnet test"
integration = "dotnet test tests/App.IntegrationTests --filter \"$FILTER\""
integration_project = "tests/App.IntegrationTests"
integration_filter = "Category!=Smoke&Category!=S3"
'''

PROJECT = tomllib.loads(PROJECT_TOML)
