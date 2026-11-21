# Setup and troubleshooting

Loaded by [`../SKILL.md`](../SKILL.md) the first time a developer publishes, and when the
script or rclone fails. The script is [`../Publish-FeGuidesToDrive.ps1`](../Publish-FeGuidesToDrive.ps1).

## One-time setup (per developer)

```powershell
winget install --id Rclone.Rclone -e
# winget adds rclone to PATH for new shells only, and on some machines never
# creates the WinGet\Links shim at all. The script finds the package under
# %LOCALAPPDATA%\Microsoft\WinGet\Packages regardless; for the commands below, use
# a new shell or the full path from: Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter rclone.exe

# A person runs this once, in their own terminal — not in a Claude session: it handles
# the login. It opens the browser for OAuth; sign in with the account that has edit
# access to the FE folder. The command ECHOES THE TOKEN when it finishes — the redirect
# keeps it off the screen. After it, the skill's script uses rclone, and rclone reads
# its own config: no session ever sees the token.
rclone config create gdrive drive scope drive > $null

# Proves the remote can see the folder. Expect one line per Doc. The folder id is
# [fe_guides] drive_folder_id in devtools/factory/factory.toml, as the script reads it;
# the flags are the ones the script uses.
$folder = python devtools/factory/factory.py config fe_guides.drive_folder_id
rclone lsjson gdrive: --drive-root-folder-id $folder --drive-export-formats md
```

- The token is in `%APPDATA%\rclone\rclone.conf`, in the developer's own profile.
  **Never copy it into the repository, a commit, or a conversation.** If it ever
  surfaces in a transcript, revoke it at
  <https://myaccount.google.com/permissions> and re-run `rclone config create`.
- rclone warns that its **shared `client_id` is being retired during 2026**. When
  the warning turns into a refusal, make a personal one per
  <https://rclone.org/drive/#making-your-own-client-id> and add it with
  `rclone config update gdrive client_id <id> client_secret <secret>`. Not needed
  until then.
- If the browser step fails with a Workspace "app blocked" page, the organisation
  restricts third-party apps and an admin has to allow rclone. That is the same
  wall `book:check-sources` documents for the connector; nothing on this end
  fixes it.


## When it goes wrong

| Symptom | Meaning | Do |
|---|---|---|
| `rclone not found` | Not installed, or the shell predates the install | `winget install --id Rclone.Rclone -e`; the script also finds the winget package path without a restart |
| `didn't find section in config file` / `Failed to create file system for "gdrive:"` | rclone is installed but this developer has no `gdrive` remote yet | The `rclone config create` line in the setup section — the browser step, once |
| `Failed to configure token` / `invalid_grant` on any call | The OAuth grant was revoked or expired | Re-run `rclone config create gdrive drive scope drive > $null`; it replaces the token in place |
| `can't convert ".md" to a document with a different export filetype` | `--drive-export-formats md` missing — someone ran rclone by hand without it | Use the script, or add the flag; import and export formats must agree |
| `Two Docs share a title` | A duplicate in the folder, usually from a hand upload | Ask the user which to keep; they trash the other in the Drive UI; re-run |
| `Rename map: no Doc titled '…'` | The key does not match the listing byte for byte | Copy the title from the script's own "match no guide" list, `／` and all |
| `Rename map: a Doc titled '…' already exists` | Two Docs claim one guide | Resolve as a duplicate (above) before renaming |
| `DUPLICATED` in the Action column | rclone created a second Doc instead of updating — the title comparison failed | Stop. List the folder, find the two, have the user trash the new one, and check the H1 for a character rclone encodes differently |
| `docs/fe-integration-guides/ has uncommitted changes` | You are about to publish content the banner cannot name | Commit or stash, or publish `origin/dev` with `-Worktree -WorktreeRoot <root>` |
| `This is not 'dev'` | Publishing a feature branch | Fine for a deliberate preview; say so in the report. Otherwise pass `-Worktree -WorktreeRoot <root>` |
| `Verify: STILL STALE after <n> tries` | A Doc's banner still names another commit (or the Doc is missing or twinned) after every try | Not published as far as Drive shows. Read the listed Doc's head with `rclone cat`; a twin is the duplicate row above; a lag longer than the tries, rerun with `-VerifyTries`/`-VerifyDelaySeconds` raised — never report it as published |
| `uploaded Docs read back wrong` | The banner is not under the H1, or a `](x.md)` link was left | Read the Doc; a link to a file that is not a guide stays dead until the guide is fixed in the repository |
| `Could not remove the worktree` | `git worktree remove` failed at the end (a file held open) | Remove it by hand with the printed command; nothing else is affected |
| OAuth browser page says the app is blocked | Workspace policy | Needs an admin; nothing here fixes it |
| `403` / `insufficient permissions` on upload | The signed-in account can view the folder but not edit it | Ask the folder owner for edit access; a viewer can only create copies elsewhere |
