# Defects — what was wrong, and what happened to it

**Status:** `Live` · **Owner of the record:** whoever finds the defect

What is wrong with what we built, from any source. **Open rows are in full; fixed ones are one line
each**, with the full row in [`archive/defects-closed.md`](archive/defects-closed.md). How to write a
row, and when a finding is not a defect: [`docs/CLAUDE.md`](CLAUDE.md#defects).

**Status** (open rows): `Open` · `Mitigated` (harm contained, cause remains) · `Partial` (part fixed) ·
`Blocked — <Q id or party>`.

## How to use it

1. **Add a row the day a defect is confirmed**, in [Open](#open), with the next `D<n>`.
2. **Put the id in the fixing commit's message.**
3. **When it is fixed:** move the full row to [`archive/defects-closed.md`](archive/defects-closed.md)
   and add a one-line entry to [Fixed](#fixed). Never delete a row; never reuse an id.

## Open

| Id | Sev | Status | Defect | Where | Found by | Note |
|---|---|---|---|---|---|---|

None open.

### Numbering

Ids are `D<n>`, never reused; how to take one is [`docs/CLAUDE.md`](CLAUDE.md#ids--d-q).

## Fixed

One line per fixed defect: id, what it was, and the commit or release that fixed it.

- **D1** — a dotnet Book's review checklist held every check twice, and no `[review_scope]`. Fixed in v1.2.3.
- **D2** — the packs' test modules shared one module teardown. Fixed in v1.2.3.
- **D3** — two template-only tests ran in installed Books. Fixed in v1.2.3.
- **D4** — the renamer turned the word "boilerplate" into the slug. Fixed in v1.2.3.
- **D5** — verbatim template paths renamed; a pack and a core file at one path. Fixed in v1.2.3.
- **D6** — a flaky settings test. Fixed in v1.2.3.
- **D7** — client names and client-domain words in the template. Fixed in v1.2.3.
- **D8** — init left generator-written files unstaged. Fixed in v1.2.3.
- **D9** — broken links in a new Book. Fixed in v1.2.3.
- **D10** — a new Book failed `check-book` on owners and nested instruction files. Fixed in v1.2.3.
- **D11** — a malformed `owners` crashed init. Fixed in v1.2.3.
- **D12** — copied self-tests leaned on this repository's settings. Fixed in v1.2.3.
- **D13** — upgrade reported a file the project never had as DROPPED. Fixed in v1.2.3.
- **D14** — verify asked for a live credential once a private file existed, though the FSD is delivered as a file. Fixed in v1.2.4.
- **D15** — the hook tests failed where sh is not on PATH (PowerShell on Windows). Fixed in v1.2.4.
- **D16** — the packs carried a source project's ports, filter, interface and scoping column. Fixed in v1.2.5.
- **D17** — the test database's name was built from the slug. Fixed in v1.2.5.
- **D18** — N20 read the template's name where it was the English word. Fixed in v1.2.5.
- **D19** — upgrade could not decline a pack file. Fixed in v1.2.5.
- **D20** — a review loaded an older copy of its skill from the session's start folder, so its fingerprint differed. Fixed in v1.2.5.
- **D21** — the contracts generator missed routes mapped outside the endpoint files. Fixed in v1.2.6.
- **D22** — a plain `.Map(` with a local, verbatim or named pattern escaped the contracts cross-check. Fixed in v1.2.6.
- **D23** — the contracts generator's section titles held a source project's stage name. Fixed in v1.2.6.
- **D24** — the impact rule for the API contracts missed route files outside `Endpoints/`. Fixed in v1.2.6.
- **D25** — skills linked one step short of the docs-branch rule; a comment named the wrong owner of the Effort line. Fixed in v1.2.6.
- **D26** — `non_route_calls` accepted a framework route call such as `MapGet`. Fixed in v1.2.7.
- **D27** — an extension helper's static-form call failed to bind, and an overload's message did not say why. Fixed in v1.2.7.
- **D28** — `check-fe-guides.py` did not read a *Last checked* field its header wraps across two lines. Fixed in v1.2.8.
- **D29** — the dotnet pack's self-tests imported the fe-guides pack's check, so a dotnet Book without that pack could not run them. Fixed in v1.2.8.
- **D30** — `factory book init` did not record the stack and packs in a new Book's `factory.toml`, so its verify and pack checks saw none installed. Fixed in v1.2.9.
- **D31** — `factory book upgrade` offered a rename stub (NEW) for an old skill name the project never had. Fixed in v1.3.3.
- **D32** — the dotnet pack's contracts generator wrote "tenant scoping" whatever the project's `[book] scoping_word`. Fixed in v1.3.3.
- **D33** — `check-fe-guides` read the contracts' first *Response schemas* section as every type changed, so every FE guide looked unchecked. Fixed in v1.3.3.
- **D34** — a syntax error in a pack's Python file passed the boilerplate's self-tests, which never import the packs' tests. Fixed in v1.3.3.
