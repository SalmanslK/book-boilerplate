# Defects — what was wrong, and what happened to it

**Status:** `Live` · **Owner of the record:** whoever finds the defect

What is wrong with what we built, from any source. **Open rows are in full; fixed ones are one line
each**, with the full row in [`archive/defects-closed.md`](archive/defects-closed.md). How to write a
row, and when a finding is not a defect: [the Book's rules for documents](../.claude/book/docs.md#defects).

**Status** (open rows): `Open` · `Mitigated` (harm contained, cause remains) · `Partial` (part fixed) ·
`Blocked — <Q id or party>`.

## How to use it

1. **Add a row the day a defect is confirmed**, in [Open](#open), with the next `D<n>`.
2. **Put the id in the fixing commit's message.**
3. **When it is fixed:** move the full row to [`archive/defects-closed.md`](archive/defects-closed.md)
   and add a one-line entry to [Fixed](#fixed). Never delete a row; never reuse an id.

## Open

| Id | Sev | Status | Type | Source | Defect | Where | Note |
|---|---|---|---|---|---|---|---|
| D97 | 🔵 | Open | tooling-ci | developer: the first project's Book upgrade | **Known limit:** a project cannot add its own check to the pre-commit hook, `ci-local.sh` or CI without declining those files, which then stop receiving the Book's updates | `.githooks/pre-commit`, `devtools/checks/ci-local.sh` | Not fixed in 1.6.1. Planned for the next minor release: a settings hook for project checks ([plan §9.2](plans/book-v1-6-common-split.md)); found 2026-11-24 |

### Numbering

Ids are `D<n>`, never reused; how to take one is [the Book's rules for documents](../.claude/book/docs.md#ids--d-q).

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
- **D35** — `check-fe-guides` named each unowned route twice when its row was reworded. Fixed in v1.3.4.
- **D36** — `factory book upgrade` never asked about unversioned routes in the contracts that no FE guide or `[fe_guides] owners` entry owns. Fixed in v1.3.4.
- **D37** — the ai-files owner tests edited the first `owners` list in `factory.toml`, which is the `[fe_guides]` list once that section has one. Fixed in v1.3.4.
- **D38** — `/check-guides` asked a guide to name any dotted string assigned to a name, such as an application's name, as an error code. Fixed in v1.3.4.
- **D39** — the book plugin was not pinned to a release, so each plugin release reached projects unreviewed, and no check tied the plugin's version to the template's. Fixed in v1.3.4.
- **D40** — the copied `ShippedTextTests` failed a project on its own defect ids in template-owned files it keeps changed. Fixed in v1.4.1.
- **D41** — the guard refused `book deploy revert --to <sha>`, reading `--to` as an abbreviated `--target`. Fixed in v1.5.0.
- **D42** — `book impact` said "Nothing changed" for paths that reach nothing. Fixed in v1.5.1.
- **D43** — `book impact` printed `../../../` links in the terminal. Fixed in v1.5.1.
- **D44** — upgrade never offered the FE guide template, and verify did not ask for the fe-guides pack's docs. Fixed in v1.5.1.
- **D45** — devtools scripts failed on an em dash under a Windows console's code page. Fixed in v1.5.1.
- **D46** — the `factory.toml` skeleton said factory.py reads it. Fixed in v1.5.1.
- **D47** — requirements read-me files kept old skill names, and upgrade never offered the new ones. Fixed in v1.5.1.
- **D48** — the Cursor rule said the skills live under `.claude/skills/`. Fixed in v1.5.1.
- **D49** — nothing said the plugin install reorders `.claude/settings.json`. Fixed in v1.5.1.
- **D50** — `book upgrade --help` printed `usage: book book`. Fixed in v1.5.1.
- **D51** — check-book let an open question carry the template's recommendation heading in place of the project's. Fixed in v1.5.1.
- **D52** — the dotnet pack's review checklist lacked check 161. Fixed in v1.5.1.
- **D53** — the guard missed shortened and combined `git push` delete, force, `--all` and `--mirror` flags. Fixed in v1.5.1.
- **D54** — the guard let a local branch be deleted by `update-ref -d`, or a protected one moved by `branch -f`, `-m`, `-M` or `update-ref`. Fixed in v1.5.1.
- **D55** — the guard did not judge a deploy started by `-c` code that runs `book.py` other than through `runpy`. Fixed in v1.5.1.
- **D56** — `book upgrade --apply` recorded the new version over an unmerged CONFLICT, which the next report then hid. Fixed in v1.5.1.
- **D57** — the upgrade named the old plugin pin in the run that had just moved it. Fixed in v1.5.1.
- **D58** — upgrade and the plugin's id lookup resolved a release by its bare name, which a branch of that name could answer. Fixed in v1.5.1.
- **D59** — a fresh dotnet Book's first commit was refused by the contracts and ERD generators and by the protected-paths check. Fixed in v1.5.1.
- **D60** — book plugin skills gave commands their `allowed-tools` did not pre-approve (plugin v0.6.1). Fixed in v1.5.1.
- **D61** — `book verify` failed every defect row in the 1.6 shape, which check-book accepts. Fixed in v1.6.0.
- **D62** — `book requirements import` let a second audit section for a module overwrite the first. Fixed in v1.6.0.
- **D63** — a deleted Book defaults file silently emptied the guard's read-only and ask lists, and the drift check did not see it. Fixed in v1.6.0.
- **D64** — `book defects classify` split a cell on an escaped pipe or one in a code span, shifting the row's columns. Fixed in v1.6.0.
- **D65** — the 1.6 settings split deleted a project's own comment above a key equal to its default. Fixed in v1.6.0.
- **D66** — the 1.6 move's report claimed keys it did not add, and its split left lines above the import and an empty parent table. Fixed in v1.6.0.
- **D67** — the upgrade called the dotnet nested `CLAUDE.md` files DROPPED and named the template's paths as MISSING. Fixed in v1.6.0.
- **D68** — nothing said to keep `[book] pending_merge_for` while merging, and removing it relisted merged files. Fixed in v1.6.0.
- **D69** — a default the project had removed came back unannounced, and a default code rule could not be switched off. Fixed in v1.6.0.
- **D70** — `book config --json` with no key, given for the whole merged view, failed. Fixed in v1.6.0.
- **D71** — `[book] declined` naming the defaults file emptied the guard's lists unseen. Fixed in v1.6.0.
- **D72** — the guard's refusal over a broken or missing settings file did not name the file. Fixed in v1.6.0.
- **D73** — the Book's tools split a table row's cells four different ways. Fixed in v1.6.0.
- **D74** — `book requirements import` misfiled rows and dropped columns whose headers it did not know by exact name. Fixed in v1.6.0.
- **D75** — the requirements import broke the audit's relative links and doubled escaped pipes. Fixed in v1.6.0.
- **D76** — the requirements import made a dated audit pass a module without saying so. Fixed in v1.6.0.
- **D77** — a second upgrade report claimed removed defaults were back and dropped the import reminder. Fixed in v1.6.0.
- **D78** — the 1.6 settings split left orphaned and misleading comments in factory.toml. Fixed in v1.6.0.
- **D79** — the upgrade report buried the decisions under routine diffs and listed DROPPED files repeatedly. Fixed in v1.6.0.
- **D80** — a comment in the requirements import held a client-domain word. Fixed in v1.6.0.
- **D81** — the requirements import read a row with an unescaped pipe shifted and dropped its surplus cells. Fixed in v1.6.0.
- **D82** — the requirements import filed a row by the first status in its history, not its last. Fixed in v1.6.0.
- **D83** — the requirements import broke the archived audit's relative links. Fixed in v1.6.0.
- **D84** — a converted register warned once per id off the id prefix. Fixed in v1.6.0.
- **D85** — a re-run of the 1.6 upgrade printed an empty settings move. Fixed in v1.6.0.
- **D86** — check-book crashed on a tracked document deleted from the working tree. Fixed in v1.6.0.
- **D87** — the upgrade's default diffs file was shared by checkouts with the same folder name. Fixed in v1.6.0.
- **D88** — a converted requirement's Note dropped the status its audit history began with. Fixed in v1.6.0.
- **D89** — the requirements import missed a status move written in words, and said nothing of a cell that did not say which status was current. Fixed in v1.6.0.
- **D90** — the 1.6 upgrade repeated its "run this" CONFIRMs after the work was done. Fixed in v1.6.0.
- **D91** — a later run of the 1.6 move wrote back a `drift_accepted` list a person had removed. Fixed in v1.6.0.
- **D92** — the requirements import gave an ambiguous status cell a status without saying so. Fixed in v1.6.0.
- **D93** — `book verify` N7 required a file `[book] skip_skeletons` declines, because the fe-guides owner table names it. Fixed in v1.6.1.
- **D94** — the dotnet rules sent Compose placeholders to `QA_PLACEHOLDERS` in `check-compose.py`, not `[deploy.compose_placeholders]`. Fixed in v1.6.1.
- **D95** — the hook, local CI, pipeline and rules assumed `check-test-setup.py`, which a project may decline. Fixed in v1.6.1.
- **D96** — the dotnet rules named another project's types and files, and called rules "not enforced yet" that a project may enforce. Fixed in v1.6.1.
- **D98** — the dotnet review checklists named another project's scope interface and test class. Fixed in v1.6.1.
- **D99** — the pipeline's test-setup step was not one YAML string. Fixed in v1.6.1.
- **D100** — `check-rules.py` counted a declined core check as intact. Fixed in v1.6.1.
- **D101** — the hook and local CI took any mention of `check-test-setup.py` for a decline. Fixed in v1.6.1.
- **D102** — the dotnet rules linked to documents another pack creates. Fixed in v1.6.1.
- **D103** — nothing said a project that declines `check-test-setup.py` must rewrite the rules table. Fixed in v1.6.1.
- **D104** — a self-test copied into every project scanned the project's own docs for names, so it failed in every real project. Fixed in v1.6.2.
- **D105** — a link self-test built a fresh Book from the project's own `docs/plans/_template.md`, so it failed in a project whose copy differs. Fixed in v1.6.3.
- **D106** — `book impact --block` read the retired traceability matrix, so it called a 1.6 Book's built module unbuilt. Fixed in v1.6.5.
- **D107** — `book review-scope` with nothing changed gave the dimensions left out no reason. Fixed in v1.6.5.
