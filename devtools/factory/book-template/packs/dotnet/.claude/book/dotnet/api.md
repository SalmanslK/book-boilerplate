# API — endpoint rules

The Book's rules for this folder, the same in every project with the dotnet pack: the folder's `CLAUDE.md` imports this file and holds only the project's own beside it. **This file is the Book's** — `book upgrade` replaces it, and check-book fails when a project's copy differs.

Loaded when you work under `src/Boilerplate.API/`. The root
[`CLAUDE.md`](../../../CLAUDE.md) still applies.

## Endpoints

- **Endpoints dispatch through `ISender` and map the result.** No business logic, no
  repository calls, no `DbContext` — `EndpointIsolationTests` fails the build on the
  last *(not enforced yet: add a `EndpointIsolationTests` architecture test, then mark it `[enforced: …]`)*. Endpoints are `sealed` and `internal`
  *(not enforced yet: add a `EndpointIsolationTests` architecture test, then mark it `[enforced: …]`)*.
- **Authorization is default-deny.** A new endpoint is protected with no attribute;
  opting out means writing `.AllowAnonymous()` and meaning it, and listing the file on
  `anonymous-route`'s allow-list with its reason *[enforced: `check-code-rules.py`, pre-commit + CI]*.
  The permission a route needs is declared on its command or query.
- Handlers return `Result<T>`, never `IResult`; the endpoint maps it.
- **A create answers `CreatedAtRoute` against the resource's named GET route**, never a
  `Location` built from a `/api/v…` literal *(not enforced yet: add a `ProductCodeRuleTests` architecture test, then mark it `[enforced: …]`)*
  Its body is `CreatedResponse` (`{ id }`), declared
  `.Produces<CreatedResponse>(201)`, unless the create returns more.

## Changing the API surface is three changes, not one

1. **Regenerate the contracts and the Postman collection** — one run writes both:
   ```bash
   python3 devtools/checks/gen-api-contracts.py          # rewrites docs/api/api-contracts.md, then the collection
   python3 devtools/checks/gen-api-contracts.py --check  # what CI and the pre-commit hook run
   ```
   Never hand-edit either output; if a route or permission is missing, fix the
   generator. It reads every C# file of this project: a route mapped outside the endpoint
   files is listed as unversioned and must be `.AllowAnonymous()`. Health checks and the API
   docs have notes built in; any other such route (a page, a file) needs one in
   `[api_contracts] unversioned_notes` in `devtools/factory/factory.toml`. A registration it
   cannot parse stops the run; a `Map…` call of yours that maps no route goes in
   `[api_contracts] non_route_calls`.
2. **Place a new endpoint in `JOURNEY`** in `devtools/checks/api_contracts_postman.py`
   (the order a user calls it) and give it a realistic body in `SAMPLES`. The
   generator refuses to run until every route is placed, and fails on a sample field
   the model does not declare.
3. **Update the module's FE integration guide** in
   [`docs/fe-integration-guides/`](../../../docs/fe-integration-guides/) in the same
   change — including its *Deviations* section if the shape differs from
   `requirements/fe-api-contracts/`. A rule that applies to every endpoint goes in
   `api-conventions.md`, never in a module guide.

## Settings

- A setting the API itself consumes is bound in `Configuration/Options/` and validated
  with `ValidateOnStart` ([the rule](src.md)). Never read configuration with a magic string.
- `appsettings.json` and `appsettings.<Env>.json` hold **placeholders that start with
  `REPLACE`** — startup validation refuses to boot outside Development on that prefix.
  `devtools/checks/check-appsettings-secrets.sh` enforces no real secrets
  *[enforced: `check-appsettings-secrets.sh`, pre-commit + CI]*. `appsettings.Development.json` is tracked and checked like the rest:
  put nothing in it you would not publish.
- A new setting an environment must supply also needs the Compose overlay and the
  server `.env` — see [Deploying in the root file](../../../CLAUDE.md#deploying).

## Settings an environment supplies

- **A setting an environment must supply is three changes:** a line in the overlay's
  `environment:` block, the value in the server's `.env`, and a placeholder in
  `QA_PLACEHOLDERS` in `devtools/checks/check-compose.py` — and, when `compose.dev.yaml` requires
  it as well, in `DEV_PLACEHOLDERS` beside it, or the DEV render fails.
  "It works on DEV" is not evidence for QA: `devtools/checks/check-required-settings.sh`
  proves every `REPLACE` key reaches the container (CI runs it for QA).
