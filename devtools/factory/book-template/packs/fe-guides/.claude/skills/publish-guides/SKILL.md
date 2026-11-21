---
name: publish-guides
description: >
  Use when the FE team needs the current guides on Drive — after a guide changes, or when the Drive copy
  has drifted (missing, renamed or duplicated Docs). Publishes every guide in
  docs/fe-integration-guides/ as a Google Doc, updating each in place so no link breaks. Not for writing
  or correcting a guide (that happens in the repository) or for any other Doc.
---

You are publishing **the repository's front-end integration guides to the FE
team's Google Drive folder**.

The guides are written for a team that does not read this repository. They read
the Drive folder — `https://drive.google.com/drive/folders/<id>`, its id owned by
`[fe_guides] drive_folder_id` in `devtools/factory/factory.toml` and printed by
`python devtools/factory/factory.py config fe_guides.drive_folder_id`
— one Google Doc per guide — and they bookmark, link and comment on those Docs.
That is what makes this more than an upload: **a Doc's URL is a promise**, and
this skill keeps it.

---

## The contract

- **`docs/fe-integration-guides/` is the source of truth. Drive is a rendition.**
  A guide is edited in the repository, in the same change that alters the API
  (that rule lives in `CLAUDE.md`), and then published. Edits made in a Doc are
  overwritten on the next publish — the banner under every Doc's title says so.
- **One guide, one Doc, matched by title.** A Doc's title is the guide's H1,
  exactly — `Companies — front-end integration guide`, `API conventions — what every
  module does the same way`. Nothing else pairs them, so the title is the key.
- **A Doc is updated in place, never replaced.** Its id — and so its URL — is the
  same after every publish and after every rename.
- **Nothing on Drive is ever deleted by this skill**, not a superseded Doc, not a
  duplicate, not a file that is not ours. Deleting is a person's decision, made in
  the Drive UI. The skill reports what it left alone and why.
- **Every guide ships, or the run is not done.** `api-conventions.md` is a guide
  like any other; a module guide the FE team has not asked for yet is still
  published — they find it when they need it.

---

## Why rclone, and why not the Drive connector

The `claude.ai Google Drive` connector can create a Doc from Markdown, read one
back, and — as of its last tool-list check — rename, move and trash
one (`update_file` takes only `title` and `parentId`; `trash_file` exists). What
it still cannot do is **replace a Doc's content**: there is no call that writes
into an existing id. "Update" through it therefore means creating a second Doc
beside the first — the old URL goes stale, the folder fills with near-duplicates,
and someone has to clean up by hand. That is the opposite of the contract, so
**do not reach for the connector to publish**, even for one guide, even when
rclone is not set up yet. Set rclone up instead; it is a two-minute job
([setup](references/setup-and-troubleshooting.md#one-time-setup-per-developer)).
The connector is fine for the read-only half of the job — listing the folder by
`parentId`, reading a banner with `read_file_content` — and for nothing else.
Re-check its tool list before trusting this paragraph; it has grown once already.

**rclone** talks to the Drive API with an OAuth token in the developer's own name.
It imports `text/markdown` as a native Google Doc, updates an existing Doc when
the name matches, and renames server-side. All three were proven on a real
folder, id preserved throughout; tables, code fences and headings
survive the round trip intact.

---

## Before the first publish, and when it fails

**rclone with a `gdrive` remote is a one-time setup per developer**, and every error the
script or rclone prints has a known meaning. Both are in
[`references/setup-and-troubleshooting.md`](references/setup-and-troubleshooting.md) — read it
when `rclone` or the `gdrive` remote is missing, or when a run fails. A failure that table
does not explain stops the publish: tell the user what failed, and publish nothing.

---

## Running it

Everything mechanical is in
[`Publish-FeGuidesToDrive.ps1`](Publish-FeGuidesToDrive.ps1) beside this file
(`Get-Help .\.claude\skills\publish-guides\Publish-FeGuidesToDrive.ps1 -Detailed` from the repository root, for its parameters). The
skill's own work is the part a script cannot do: **deciding which existing Doc is
which guide, and getting the user's agreement before anything is renamed.**

### Step 1 — Stand on the branch you mean to publish

The banner in every Doc names the commit it was published from, and repository
links inside the Docs point at that branch in Bitbucket. **Publish from `dev`**
unless the user has asked for a preview of a feature branch — the FE team reads
these Docs as the integration branch's truth, and a guide for an unmerged
endpoint is a promise the API does not keep yet.

The script refuses a dirty `docs/fe-integration-guides/` (the banner would name a
commit the content does not match) and prints a warning when the ref is not
`dev`. If the working tree is on something else, or another session is using it,
**do not switch branches under them** — pass **`-Worktree -WorktreeRoot <root>`**.
The script fetches `origin`, adds a detached worktree at `origin/dev` in
`<root>\<repository name>\fe-guides-dev-<time>`, publishes from it and removes it at
the end, whether the publish worked or not (a removal that fails is printed with the
path); the checkout it is run from is never switched or changed. `<root>` is where the
person's own instructions put worktrees; with none named, ask. Add `-WhatIf` to see the
path and the git commands without running any of them.

A detached worktree at `origin/dev` is recognised as `dev`. **A dry run does not
need the worktree** — run it from wherever the tree is; its verdict is about that
branch's guides, which is usually what the question means.

### Step 2 — Dry run, and read the two lists at the bottom

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\.claude\skills\publish-guides\Publish-FeGuidesToDrive.ps1 -DryRun
```

Nothing is renamed, created or uploaded. What the dry run *does* touch on Drive,
all read-only: one `lsjson` of the folder, a `cat` of the first 1,500 bytes of
every matched Doc (to read the commit in its banner), and an `rclone copy
--dry-run`. The table says what each guide would get — `current` (the Doc
already carries this commit), `would update`, `would create`. Below it, two
lists matter:

- **"Docs in the folder that match no guide's H1"** — a Doc with an old or
  hand-typed title, a duplicate, or something that is not ours at all.
- **`would create` rows** — guides with no Doc of the right title.

A heading that does not print means that list is empty. **When the question was
"is the Drive copy up to date?", this is the whole job**: report the `Result`
line, the two lists, and stop — nothing is published for a question.

When both lists are empty, go to Step 4. When both have entries, **most of them
are the same Docs seen from two sides** — that is the matching problem, Step 3.

### Step 3 — Matching: pair old titles with guides, then get agreement

For each unmatched Doc, decide which guide it is, or that it is none of them.
Titles drift in predictable ways — a hyphen for the em dash, `FE` for
`front-end`, a module's old name, a missing module prefix, a spaced name for a
hyphenated one, the file name typed in as the title. Strip the
stock words (`front-end`, `FE`, `integration guide`, dashes, case) and what is
left is the module; that is the guide.

**When the title alone does not settle it, read the Doc's head** — it is
read-only and cheap:

```powershell
$folder = python devtools/factory/factory.py config fe_guides.drive_folder_id
rclone cat "gdrive:<title as listed>.md" --drive-root-folder-id $folder --drive-export-formats md --count 1500
```

Our guides open with our H1 and, once published, the banner. A Doc titled
`Account Management - Companies & Locations` that opens with `# Companies and
Locations — front-end integration guide` is ours; one that opens with somebody's
storage notes is not.

**Rules for the pairing:**

- **One Doc per guide.** If two Docs both look like the same guide, pair the one
  the FE team is more likely to hold a link to — usually the older one, the one
  with comments, or the one the other Docs link to — and **leave the other for
  the user to trash**. The script refuses to run while two Docs share the same
  title, so this cannot be deferred past the rename. A `… (copy)` beside a
  correctly titled Doc is the same case with a different title: it does not
  block the publish, it is never renamed onto the original, and it will sit in
  the unmatched list on every run until a person trashes it — say so.
- **Unsure means unmatched.** A Doc you cannot place with confidence is left
  alone and reported; a wrong rename moves someone's bookmark onto the wrong
  content, which is worse than a missing guide.
- **A Doc that is nobody's guide** — meeting notes, a QA checklist, a
  spreadsheet — is left alone and named in the report. Never rename it, never
  move it.

Write the pairing as JSON **in the scratchpad or `%TEMP%`, never in the
repository** — it is a one-off, and once the renames land it is history.
**Drive title exactly as the listing printed it** (rclone shows a Drive `/` as
`／`; copy it as shown) to **guide file name without `.md`**:

```json
{
  "User Management - FE integration guide": "users",
  "companies (and locations)": "companies-and-locations",
  "Roles FE Integration Guide": "roles"
}
```

Then **show the user the table — old title → new title — and ask before
running it.** Renaming is visible to the whole FE team and undoing it by hand is
ten minutes of someone's afternoon. Do not fold the question into a wall of
output; it is one table and a yes/no.

Dry-run the map first — `-DryRun -RenameMap <path>` prints `would rename` lines
and the table with those guides showing `renamed, would update`.

### Step 4 — Publish

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\...\Publish-FeGuidesToDrive.ps1 -Verify [-Worktree -WorktreeRoot <root>] [-RenameMap <path>]
```

In order: the renames, then a first upload of the guides that have no Doc (so
they have ids), then every guide that is not already current — staged with the
provenance banner under its H1, `[text](other-guide.md#x)` links rewritten to the
sibling Doc's URL, `[text](../x.md)` links rewritten to the file on the
published ref in Bitbucket. A Doc whose banner already names the guide's current
commit is skipped as `current`; `-Force` publishes it anyway (use it after
changing the banner or the link rewriting, or when a Doc was edited by hand and
must be put back).

**A run that fails part-way** — after the renames, or with some Docs uploaded —
leaves the folder partly published. Report exactly what landed (renamed, created,
updated) and what did not, say the folder is partly published, and rerun once the
cause is fixed; never report it as published.

### Step 5 — Verify, then report

- **`-Verify` does the checking** after the publish: it reads the folder again until
  every guide has one Doc whose banner names the guide's commit — **Drive's export can
  lag the write by a few seconds**, so it tries up to `-VerifyTries` (4) times,
  `-VerifyDelaySeconds` (15) apart — then reads every Doc this run created or updated
  whole, for the banner under its H1 and no `](something.md)` link left. It prints
  `Verify: all <n> guides current` and the read-back, or **`STILL STALE after <n>
  tries`** with each stale guide and its banner's commit, and exits 1. Stale is never
  success: stop and find out why (the banner is not being read back, a twin Doc, a
  failed upload) before reporting anything as published.
- Without `-Verify` (an older script, or a run that failed before it), check by
  hand: a dry run whose `Result:` is `<n> current` and nothing else — once more if
  Drive lags — and one updated Doc read with `rclone cat "gdrive:<title>.md"` and
  the folder flags.
- **Report the table** — guide, action, commit, URL — plus the unmatched Docs and
  what you concluded about each. Every row is a URL the user can hand to the FE
  team. If any Doc was renamed, say so: the URL is unchanged, but the name they
  see in the folder is not.

---

## Do not

- **Do not publish through the Drive connector, and do not create a Doc "just
  this once" by another route.** It cannot update, so every such Doc is a
  future duplicate with a URL nobody can reconcile.
- **Do not merge FE edits found in a Doc into the upload.** Repo wins. If their
  edit is right, it is a defect or a BA question — log it where `CLAUDE.md` says,
  fix the guide in the repository through a PR, then publish. If it is wrong,
  the next publish overwrites it, and the banner told them it would.
- **Do not rename anything without showing the user the map first**, and do not
  guess a pairing to make the "unmatched" list shorter.
- **Do not trash, move, or "tidy" anything in the folder.** Report it.
- **Do not put the folder id anywhere but `[fe_guides] drive_folder_id` in
  `devtools/factory/factory.toml`** — this skill and the script read it from
  there (`factory book verify` N17 fails a copy) — and do not put the rclone
  token anywhere at all.

---

## What next

- Hand the report's URLs to the FE team; a renamed Doc keeps its URL.
- Unmatched Docs and `… (copy)` duplicates: the person decides and trashes them by hand.
- A guide found wrong while publishing: fix it in the repository first (a defect row if the
  code is wrong), then publish again.
- **In a flow** (`/book:flow`): `new-fsd-revision` → `/book:progress`; `finish-feature` → `/book:progress`; `after-merge` → `/book:check-sources`; `guides-drift` → `/book:write-for`.
