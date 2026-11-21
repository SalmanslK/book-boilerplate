<#
.SYNOPSIS
Publishes every docs/fe-integration-guides/*.md to the FE team's Google Drive
folder as a Google Doc — creating the ones that are missing and updating the
ones that exist in place, so their URLs never change.

.DESCRIPTION
Companion to the publish-guides skill. Read SKILL.md
first: it owns the matching and renaming of off-canonical Drive titles, which
this script deliberately does not do.

What it does, in order:
  1. Reads each guide's H1 — that is its canonical Drive title.
  2. Lists the Drive folder and pairs each guide with the Doc of the same title.
  3. Creates a Doc for every guide that has none (pass 1).
  4. Stages every guide with a provenance banner and cross-guide links rewritten
     to Drive URLs, then uploads (pass 2). rclone updates a Doc in place when
     the title matches; a Doc whose banner already names the guide's current
     commit is left alone.
  5. Prints a table of what happened and lists any Doc in the folder that
     matches no guide — those are the skill's job, not this script's.
  6. With -Verify, reads Drive back until every guide's Doc names its commit
     (capped tries, for Drive's export lag), then re-reads each Doc this run
     uploaded for the banner under its H1 and no repository .md link left.

Requires rclone with a Google Drive remote (see SKILL.md, "One-time setup"),
and a clean working tree under docs/fe-integration-guides/ — or -Worktree,
which publishes origin/dev from a detached worktree of its own and never
touches the checkout it is run from.

.PARAMETER Remote
The rclone remote name. Default: gdrive.

.PARAMETER FolderId
The Drive folder id the guides are published to. Default: [fe_guides]
drive_folder_id in devtools/factory/factory.toml — its one owner — read through
`python devtools/factory/factory.py config fe_guides.drive_folder_id`, so it is
never repeated here.

.PARAMETER RenameMap
Path to a JSON object of { "<Drive title as listed>": "<guide file name without .md>" }.
Each Doc is renamed server-side to that guide's H1 before anything is uploaded,
so it keeps its id. The skill writes this file after the user confirms the match.

.PARAMETER Force
Upload every guide even when its Doc already carries the current commit — after
changing the banner or the link rewriting, or when a Doc was edited by hand.

.PARAMETER DryRun
List, stage and diff, but rename nothing, create nothing and upload nothing.

.PARAMETER Worktree
Publish origin/dev, not the current checkout: fetch origin, add a detached
worktree at origin/dev under <WorktreeRoot>\<repository name>\, publish from
it, and remove it at the end, whether the publish succeeded or not. The
checkout this is run from is never switched, staged or changed.

.PARAMETER WorktreeRoot
The folder the person's own instructions put worktrees in. Required with
-Worktree; the worktree goes in <WorktreeRoot>\<repository name>\fe-guides-dev-<time>.

.PARAMETER Verify
After the publish, read Drive back: every guide must have one Doc whose banner
names the guide's commit. Drive's export lags a write by seconds, so this tries
up to -VerifyTries times, -VerifyDelaySeconds apart, and says "still stale after
N tries" — exiting 1 — when it does not settle; it never reports that as
success. Then each Doc this run created or updated is read whole: the banner
must sit under its H1, and no repository .md link may be left. Not with -DryRun.

.PARAMETER VerifyTries
How many times -Verify reads Drive before it gives up. Default 4.

.PARAMETER VerifyDelaySeconds
The wait between -Verify's tries. Default 15.

.EXAMPLE
Publish-FeGuidesToDrive.ps1 -Worktree -WorktreeRoot <worktrees root> -WhatIf
Prints what a -Worktree run would do — the worktree path, the git commands, the
publish and verify settings — and stops: no git change, no rclone call.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Remote = 'gdrive',
    [string]$FolderId,
    [string]$RenameMap,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$Worktree,
    [string]$WorktreeRoot,
    [switch]$Verify,
    [ValidateRange(1, 10)][int]$VerifyTries = 4,
    [ValidateRange(0, 300)][int]$VerifyDelaySeconds = 15
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# rclone speaks UTF-8; PowerShell 5.1 decodes native output with the OEM code
# page unless told otherwise, which mangles the em dashes in every guide title.
$previousOutputEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$utf8NoBom = New-Object Text.UTF8Encoding($false)

function Find-Rclone {
    $onPath = Get-Command rclone -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $wingetLink = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\rclone.exe'
    if (Test-Path $wingetLink) { return $wingetLink }
    $wingetPackages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path $wingetPackages) {
        $found = Get-ChildItem $wingetPackages -Recurse -Filter rclone.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    throw 'rclone not found. Install it with: winget install --id Rclone.Rclone -e   (then see SKILL.md, "One-time setup")'
}

function Invoke-Rclone {
    param([string[]]$Arguments)
    $logFile = Join-Path $env:TEMP ('rclone-fe-guides-' + [guid]::NewGuid().ToString('N') + '.log')
    $stdout = & $script:rclone @Arguments --log-file $logFile --log-level INFO 2>$null
    $exit = $LASTEXITCODE
    $log = if (Test-Path $logFile) { [IO.File]::ReadAllText($logFile, [Text.Encoding]::UTF8) } else { '' }
    Remove-Item $logFile -ErrorAction SilentlyContinue
    if ($exit -ne 0) {
        $tail = ($log -split "`n" | Where-Object { $_ -match 'ERROR|NOTICE|Failed' } | Select-Object -Last 8) -join "`n"
        throw "rclone $($Arguments[0]) failed (exit $exit):`n$tail"
    }
    return [pscustomobject]@{ Stdout = ($stdout -join "`n"); Log = $log }
}

function Get-RemoteDocs {
    # Google Docs export as text/markdown here, so each comes back as "<title>.md".
    $result = Invoke-Rclone -Arguments (@('lsjson', "${Remote}:") + $script:driveFlags)
    $entries = @()
    if ($result.Stdout.Trim()) { $entries = $result.Stdout | ConvertFrom-Json }
    return @($entries | Where-Object { -not $_.IsDir } | ForEach-Object {
        [pscustomobject]@{
            Title    = [IO.Path]::GetFileNameWithoutExtension($_.Name)
            Name     = $_.Name
            Id       = $_.ID
            IsDoc    = $_.MimeType -like 'text/markdown*'
            MimeType = $_.MimeType
            Modified = $_.ModTime
        }
    })
}

function Write-StagedGuide {
    param($Guide, [hashtable]$IdByFile, [string]$StageDir)
    $lines = [IO.File]::ReadAllLines($Guide.Path, [Text.Encoding]::UTF8)
    $h1Index = 0
    while ($lines[$h1Index] -notmatch '^# ') { $h1Index++ }
    $banner = @(
        '',
        ('> **Published from the repository** — `docs/fe-integration-guides/' + $Guide.File + '.md` at commit `' + $Guide.Commit + '` (' + $Guide.CommitDate.ToString('yyyy-MM-dd') + '). ' +
         'The repository is the source of truth: **edits made in this document are overwritten on the next publish.** Leave a comment, or raise it with the backend team.')
    )
    $rest = if ($h1Index + 1 -lt $lines.Length) { @($lines[($h1Index + 1)..($lines.Length - 1)]) } else { @() }
    $body = @($lines[0..$h1Index]) + $banner + $rest

    # Cross-guide links: [text](other-guide.md#anchor) → the sibling Doc's URL.
    # Doc headings do not carry Markdown anchors, so the fragment is dropped.
    $unresolved = New-Object Collections.Generic.List[string]
    $pattern = '\]\(([a-z0-9-]+)\.md(#[^)]*)?\)'
    $text = ($body -join "`n") + "`n"
    $text = [regex]::Replace($text, $pattern, {
        param($m)
        $target = $m.Groups[1].Value
        if ($IdByFile.ContainsKey($target)) { return '](https://docs.google.com/document/d/' + $IdByFile[$target] + '/edit)' }
        $unresolved.Add($m.Value)
        return $m.Value
    })
    # Links up into docs/ — defects, project.md, plans — point at the file on the
    # published branch in Bitbucket. Its heading anchors differ from GitHub-style
    # ones, so the fragment is dropped here too.
    $text = [regex]::Replace($text, '\]\(\.\./([A-Za-z0-9._/-]+\.md)(#[^)]*)?\)', {
        param($m)
        return '](' + $script:repoBrowseUrl + '/docs/' + $m.Groups[1].Value + ')'
    })

    $dest = Join-Path $StageDir ($Guide.Title + '.md')
    [IO.File]::WriteAllText($dest, $text, $utf8NoBom)
    return $unresolved
}

function Get-ConfiguredFolderId {
    # The folder id has one owner, [fe_guides] drive_folder_id in factory.toml. factory.py reads it
    # with Python's own TOML parser rather than a second parser here.
    param([string]$RepoRoot)
    $factory = Join-Path $RepoRoot 'devtools\factory\factory.py'
    $ErrorActionPreference = 'Continue'  # a missing or stub interpreter writes to stderr; try the next
    foreach ($candidate in @(@('python3'), @('python'), @('py', '-3'))) {
        $command = Get-Command $candidate[0] -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $command) { continue }
        $arguments = @($candidate | Select-Object -Skip 1) + @($factory, 'config', 'fe_guides.drive_folder_id')
        $out = & $command.Source @arguments 2>$null
        $value = (@($out) -join '').Trim()
        if ($LASTEXITCODE -eq 0 -and $value -match '^[\w-]+$') { return $value }
    }
    throw 'Could not read fe_guides.drive_folder_id from devtools/factory/factory.toml (factory.py needs Python 3.11+ as python3, python or py). Pass -FolderId to override.'
}

function Get-PublishedCommit {
    # The banner records which commit a Doc was published from. Drive stamps its
    # own modifiedTime on every import, so this is the only stateless way to tell
    # whether a Doc already carries the current guide.
    param([string]$Title)
    $head = Invoke-Rclone -Arguments (@('cat', "${Remote}:$Title.md", '--count', '1500') + $script:driveFlags)
    if ($head.Stdout -match 'at commit `([0-9a-f]+)`') { return $Matches[1] }
    return $null
}

function Invoke-Git {
    # git writes progress to stderr, which PowerShell 5.1 turns into an error under 'Stop'.
    param([string[]]$Arguments)
    $ErrorActionPreference = 'Continue'
    $out = @(& git @Arguments 2>&1 | ForEach-Object { "$_" })
    if ($LASTEXITCODE -ne 0) { throw "git $($Arguments -join ' ') failed (exit $LASTEXITCODE):`n$($out -join "`n")" }
    return $out
}

function Get-DriveStaleness {
    # The dry run's comparison, read again: each guide needs exactly one Doc of its title, and
    # that Doc's banner must name the guide's commit.
    param($Guides)
    $docs = @(Get-RemoteDocs | Where-Object IsDoc)
    $stale = New-Object Collections.Generic.List[string]
    foreach ($g in $Guides) {
        $matching = @($docs | Where-Object { $_.Title -eq $g.Title })
        if ($matching.Count -eq 0) { $stale.Add("$($g.File).md: no Doc titled '$($g.Title)'"); continue }
        if ($matching.Count -gt 1) { $stale.Add("$($g.File).md: $($matching.Count) Docs titled '$($g.Title)'"); continue }
        $commit = Get-PublishedCommit -Title $g.Title
        if ($commit -ne $g.Commit) {
            $named = if ($commit) { $commit } else { 'no commit' }
            $stale.Add("$($g.File).md: its Doc's banner names $named, not $($g.Commit)")
        }
    }
    return ,$stale
}

function Get-DocProblems {
    # The read-back of one uploaded Doc: our banner straight under its H1, and every link a
    # guide had to another .md file rewritten to a URL.
    param($Guide)
    $text = (Invoke-Rclone -Arguments (@('cat', "${Remote}:$($Guide.Title).md") + $script:driveFlags)).Stdout
    $lines = @($text -split "`r?`n")
    $problems = New-Object Collections.Generic.List[string]
    $h1 = -1
    for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match '^#\s') { $h1 = $i; break } }
    if ($h1 -lt 0) {
        $problems.Add('no H1')
    } else {
        $next = @($lines[($h1 + 1)..($lines.Count - 1)] | Where-Object { $_.Trim() } | Select-Object -First 1)
        if ($next.Count -eq 0 -or $next[0] -notmatch 'Published from the repository') { $problems.Add('the banner is not under the H1') }
    }
    $left = @([regex]::Matches($text, '\]\((?![A-Za-z][A-Za-z0-9+.-]*:)[^)\s]*\.md(#[^)]*)?\)') | ForEach-Object Value)
    if ($left.Count -gt 0) { $problems.Add('repository links left: ' + (($left | Select-Object -Unique) -join ', ')) }
    return ,$problems
}

$script:exitCode = 0
$script:worktreeMade = $false
$worktreePath = $null
$personRoot = $null
try {
    if ($Verify -and $DryRun) { throw '-Verify checks a publish, and a dry run publishes nothing. Run the dry run on its own: it is the comparison.' }
    if ($WorktreeRoot -and -not $Worktree) { throw '-WorktreeRoot goes with -Worktree.' }
    # Resolved now: a -Worktree run works from another folder.
    if ($RenameMap) { $RenameMap = (Resolve-Path -LiteralPath $RenameMap -ErrorAction Stop).Path }
    $personRoot = (& git rev-parse --show-toplevel).Trim()
    if ($Worktree) {
        if (-not $WorktreeRoot) { throw '-Worktree needs -WorktreeRoot: the folder the person''s own instructions put worktrees in (it goes in <root>\<repository name>\). With none named, ask.' }
        if (-not (Test-Path -LiteralPath $WorktreeRoot -PathType Container)) { throw "-WorktreeRoot '$WorktreeRoot' is not a folder." }
        # The repository's name is its main checkout's folder, also when this is run from a worktree.
        $commonDir = (Invoke-Git -Arguments @('-C', $personRoot, 'rev-parse', '--path-format=absolute', '--git-common-dir') | Select-Object -Last 1).Trim()
        $repoName = Split-Path -Leaf (Split-Path -Parent $commonDir)
        $worktreePath = Join-Path (Join-Path (Resolve-Path -LiteralPath $WorktreeRoot).Path $repoName) ('fe-guides-dev-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        if (Test-Path -LiteralPath $worktreePath) { throw "$worktreePath already exists; nothing was created." }
    }
    if ($WhatIfPreference) {
        # What a run would do, and nothing else: no git change, no rclone call.
        Write-Host 'What if — nothing is fetched, created, published or removed:' -ForegroundColor Yellow
        if ($Worktree) {
            Write-Host "  git -C $personRoot fetch origin"
            Write-Host "  git -C $personRoot worktree prune"
            Write-Host "  git -C $personRoot worktree add --detach $worktreePath origin/dev"
            Write-Host "  publish from $worktreePath (origin/dev; its links point at 'dev')"
        } else {
            Write-Host "  publish from $personRoot as it is checked out"
        }
        $mode = if ($DryRun) { 'a dry run (list, stage, diff)' } else { 'a publish' }
        Write-Host "  $mode to remote '$Remote', folder $(if ($FolderId) { $FolderId } else { 'fe_guides.drive_folder_id from factory.toml' })$(if ($RenameMap) { ", renames from $RenameMap" })$(if ($Force) { ', every guide uploaded (-Force)' })"
        if ($Verify) { Write-Host "  then verify: up to $VerifyTries tries, $VerifyDelaySeconds s apart; 'still stale after $VerifyTries tries' and exit 1 if Drive does not settle" }
        if ($Worktree) { Write-Host "  git -C $personRoot worktree remove --force $worktreePath   (always, last)" }
        return
    }
    $script:rclone = Find-Rclone
    if ($Worktree) {
        Invoke-Git -Arguments @('-C', $personRoot, 'fetch', 'origin') | Out-Null
        Invoke-Git -Arguments @('-C', $personRoot, 'worktree', 'prune') | Out-Null
        Invoke-Git -Arguments @('-C', $personRoot, 'worktree', 'add', '--detach', $worktreePath, 'origin/dev') | Out-Null
        $script:worktreeMade = $true
        Write-Host "Publishing origin/dev from the worktree $worktreePath (removed at the end)" -ForegroundColor Cyan
        $repoRoot = $worktreePath
    } else {
        $repoRoot = $personRoot
    }
    if (-not $FolderId) { $FolderId = Get-ConfiguredFolderId -RepoRoot $repoRoot }
    $script:driveFlags = @('--drive-root-folder-id', $FolderId, '--drive-import-formats', 'md', '--drive-export-formats', 'md')

    Push-Location $repoRoot
    $guidesDir = Join-Path $repoRoot 'docs\fe-integration-guides'
    $branch = (& git rev-parse --abbrev-ref HEAD).Trim()
    # A detached checkout (a worktree at origin/dev, say) has no branch name a
    # Bitbucket URL could use, so the links point at the commit instead.
    $head = (& git rev-parse HEAD).Trim()
    $devHead = (& git rev-parse --verify --quiet origin/dev)
    $browseRef =
        if ($branch -ne 'HEAD') { $branch }
        elseif ($devHead -and $head -eq $devHead.Trim()) { 'dev' }
        else { $head }
    $originUrl = (& git remote get-url origin).Trim() -replace '\.git$', ''
    $script:repoBrowseUrl = "$originUrl/src/$browseRef"

    $dirty = & git status --porcelain -- $guidesDir
    if ($dirty) {
        throw "docs/fe-integration-guides/ has uncommitted changes. Publish from a committed state so the provenance banner is true:`n$($dirty -join "`n")"
    }

    # ---- 1. The guides and their canonical titles -------------------------------
    $guides = @(Get-ChildItem -LiteralPath $guidesDir -Filter *.md | Sort-Object Name | ForEach-Object {
        $h1 = [IO.File]::ReadAllLines($_.FullName, [Text.Encoding]::UTF8) | Where-Object { $_ -match '^# (.+)$' } | Select-Object -First 1
        if (-not $h1) { throw "$($_.Name) has no H1 — every guide needs one; it is the Drive title." }
        $relative = 'docs/fe-integration-guides/' + $_.Name
        $stamp = (& git log -1 --format='%h%x09%cI' -- $relative).Trim() -split "`t"
        if ($stamp.Count -ne 2) { throw "$($_.Name) has no commit history — commit it before publishing." }
        [pscustomobject]@{
            File       = [IO.Path]::GetFileNameWithoutExtension($_.Name)
            Path       = $_.FullName
            Title      = $h1.Substring(2).Trim()
            Commit     = $stamp[0]
            CommitDate = [DateTimeOffset]::Parse($stamp[1])
        }
    })
    $duplicateTitles = @($guides | Group-Object Title | Where-Object { $_.Count -gt 1 } | ForEach-Object Name)
    if ($duplicateTitles.Count -gt 0) { throw "Two guides share an H1, so they would map to one Doc: $($duplicateTitles -join '; ')" }
    $guideTitles = @($guides | ForEach-Object Title)

    Write-Host "Publishing $($guides.Count) guides from '$browseRef' to Drive folder $FolderId via remote '$Remote'" -ForegroundColor Cyan
    if ($browseRef -ne 'dev') { Write-Host "This is not 'dev'. The FE team reads these as the integration branch's truth — publish a feature branch only as a deliberate preview." -ForegroundColor Yellow }
    if ($DryRun) { Write-Host '(dry run — nothing will be created or uploaded)' -ForegroundColor Yellow }

    # ---- 2. What is already there -----------------------------------------------
    $before = Get-RemoteDocs
    $twins = @($before | Where-Object IsDoc | Group-Object Title | Where-Object { $_.Count -gt 1 })
    if ($twins.Count -gt 0) {
        $detail = $twins | ForEach-Object { "'$($_.Name)': " + (($_.Group | ForEach-Object { 'https://docs.google.com/document/d/' + $_.Id + '/edit' }) -join ' , ') }
        throw "Two Docs share a title, so an upload could land on either. Someone must trash one by hand first — this script never deletes:`n$($detail -join "`n")"
    }
    $docByTitle = @{}
    foreach ($d in $before | Where-Object IsDoc) { $docByTitle[$d.Title] = $d }

    # ---- 2a. Renames the skill decided on: old Drive title → guide file ------------
    # Server-side, so the Doc keeps its id and every link to it keeps working.
    $renamed = @()
    if ($RenameMap) {
        $map = [IO.File]::ReadAllText($RenameMap, [Text.Encoding]::UTF8) | ConvertFrom-Json
        foreach ($entry in $map.PSObject.Properties) {
            $oldTitle = $entry.Name
            $target = @($guides | Where-Object { $_.File -eq $entry.Value })
            if ($target.Count -ne 1) { throw "Rename map: '$($entry.Value)' is not a guide in docs/fe-integration-guides/ (use the file name without .md)" }
            $newTitle = $target[0].Title
            if (-not $docByTitle.ContainsKey($oldTitle)) { throw "Rename map: no Doc titled '$oldTitle' in the folder — copy the title exactly as the listing prints it" }
            if ($docByTitle.ContainsKey($newTitle)) { throw "Rename map: a Doc titled '$newTitle' already exists; renaming '$oldTitle' onto it would leave two" }
            if ($DryRun) {
                Write-Host "would rename '$oldTitle' -> '$newTitle'" -ForegroundColor Yellow
            } else {
                Invoke-Rclone -Arguments (@('moveto', "${Remote}:$oldTitle.md", "${Remote}:$newTitle.md") + $script:driveFlags) | Out-Null
                Write-Host "renamed '$oldTitle' -> '$newTitle'"
            }
            $renamed += $target[0].File
            $docByTitle[$newTitle] = $docByTitle[$oldTitle]
            $docByTitle.Remove($oldTitle)
        }
        if (-not $DryRun) {
            $before = Get-RemoteDocs
            $docByTitle = @{}
            foreach ($d in $before | Where-Object IsDoc) { $docByTitle[$d.Title] = $d }
        }
    }

    $missing = @($guides | Where-Object { -not $docByTitle.ContainsKey($_.Title) })
    $missingFiles = @($missing | ForEach-Object File)
    $unmatched = @($docByTitle.Keys | Where-Object { -not ($guideTitles -contains $_) } | ForEach-Object { $docByTitle[$_] })
    $foreign = @($before | Where-Object { -not $_.IsDoc })

    $stage = Join-Path $env:TEMP 'boilerplate-fe-guides-stage'
    if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    New-Item -ItemType Directory -Path $stage | Out-Null

    # ---- 3. Pass 1: create the Docs that do not exist yet -------------------------
    # A guide that links to one of these needs its Drive id, which only exists
    # after creation — so create first, list again, then stage everything.
    if ($missing.Count -gt 0 -and -not $DryRun) {
        $idByFile = @{}
        foreach ($g in $guides) { if ($docByTitle.ContainsKey($g.Title)) { $idByFile[$g.File] = $docByTitle[$g.Title].Id } }
        foreach ($g in $missing) { Write-StagedGuide -Guide $g -IdByFile $idByFile -StageDir $stage | Out-Null }
        Invoke-Rclone -Arguments (@('copy', $stage, "${Remote}:") + $script:driveFlags) | Out-Null
        Remove-Item -LiteralPath (Join-Path $stage '*') -Force
        $before = Get-RemoteDocs
        $docByTitle = @{}
        foreach ($d in $before | Where-Object IsDoc) { $docByTitle[$d.Title] = $d }
        $stillMissing = @($missing | Where-Object { -not $docByTitle.ContainsKey($_.Title) } | ForEach-Object Title)
        if ($stillMissing.Count -gt 0) { throw "Created nothing for: $($stillMissing -join '; ') — check the folder permissions." }
    }

    # ---- 4. Pass 2: stage everything with links resolved, upload ------------------
    $idByFile = @{}
    foreach ($g in $guides) { if ($docByTitle.ContainsKey($g.Title)) { $idByFile[$g.File] = $docByTitle[$g.Title].Id } }
    $unresolvedByFile = @{}
    $alreadyCurrent = @()
    foreach ($g in $guides) {
        $isNew = $missingFiles -contains $g.File
        if (-not $isNew -and -not $Force -and (Get-PublishedCommit -Title $g.Title) -eq $g.Commit) {
            $alreadyCurrent += $g.File
            continue
        }
        $u = @(Write-StagedGuide -Guide $g -IdByFile $idByFile -StageDir $stage)
        if ($u.Count -gt 0) { $unresolvedByFile[$g.File] = $u }
    }

    $copyArgs = @('copy', $stage, "${Remote}:") + $script:driveFlags
    if ($DryRun) { $copyArgs += '--dry-run' }
    $upload = Invoke-Rclone -Arguments $copyArgs
    $after = Get-RemoteDocs
    $afterByTitle = @{}
    foreach ($d in $after | Where-Object IsDoc) { $afterByTitle[$d.Title] = $d }

    # ---- 5. Report -----------------------------------------------------------------
    $rows = foreach ($g in $guides) {
        $name = [regex]::Escape($g.Title + '.md')
        # rclone logs only what it transferred at INFO; a guide with no line was skipped as unchanged.
        $action =
            if ($alreadyCurrent -contains $g.File) { 'current' }
            elseif ($DryRun -and $missingFiles -contains $g.File) { 'would create' }
            elseif ($DryRun) { 'would update' }
            elseif ($missingFiles -contains $g.File) { 'created' }
            elseif ($upload.Log -match "${name}: Copied \(replaced existing\)") { 'updated' }
            elseif ($upload.Log -match "${name}: Copied \(new\)") { 'DUPLICATED — a second Doc appeared; its title matched nothing, investigate' }
            else { 'not uploaded — read the rclone log' }
        if ($renamed -contains $g.File) { $action = 'renamed, ' + $action }
        $doc = $afterByTitle[$g.Title]
        [pscustomobject]@{
            Guide  = $g.File + '.md'
            Action = $action
            Commit = $g.Commit
            Url    = if ($doc) { 'https://docs.google.com/document/d/' + $doc.Id + '/edit' } else { '(not on Drive)' }
        }
    }
    $rows | Format-Table -AutoSize | Out-String -Width 200 | Write-Host

    $summary = $rows | Group-Object Action | ForEach-Object { "$($_.Count) $($_.Name)" }
    Write-Host ("Result: " + ($summary -join ', ')) -ForegroundColor Cyan

    if ($unresolvedByFile.Count -gt 0) {
        Write-Host "`nLinks left as-is — the target has no Doc yet (a dry run, or a title still to rename) or is not a guide. Dead in a Doc until it does:" -ForegroundColor Yellow
        foreach ($k in $unresolvedByFile.Keys | Sort-Object) { Write-Host "  $k.md: $($unresolvedByFile[$k] -join ', ')" }
    }
    if ($unmatched.Count -gt 0) {
        Write-Host "`nDocs in the folder that match no guide's H1 — an old title to rename, or a document that is not ours (see SKILL.md, 'Matching'):" -ForegroundColor Yellow
        foreach ($d in $unmatched | Sort-Object Title) { Write-Host "  '$($d.Title)'  https://docs.google.com/document/d/$($d.Id)/edit  (modified $($d.Modified))" }
    }
    if ($foreign.Count -gt 0) {
        Write-Host "`nNon-Doc files in the folder, left alone:" -ForegroundColor Yellow
        foreach ($f in $foreign | Sort-Object Name) { Write-Host "  $($f.Name)  ($($f.MimeType))" }
    }

    # ---- 6. Verify: read Drive back, with capped tries for its export lag ----------
    if ($Verify) {
        $stale = $null
        $try = 0
        while ($try -lt $VerifyTries) {
            $try++
            if ($try -gt 1) { Start-Sleep -Seconds $VerifyDelaySeconds }
            $stale = Get-DriveStaleness -Guides $guides
            if ($stale.Count -eq 0) { break }
            Write-Host "Verify, try $try of ${VerifyTries}: $($stale.Count) guide(s) not current yet" -ForegroundColor Yellow
        }
        if ($stale.Count -gt 0) {
            Write-Host "`nVerify: STILL STALE after $VerifyTries tries, $VerifyDelaySeconds s apart — not published as far as Drive shows. Find out why before reporting success:" -ForegroundColor Red
            foreach ($line in $stale) { Write-Host "  $line" }
            $script:exitCode = 1
        } else {
            Write-Host "`nVerify: all $($guides.Count) guides current on Drive (try $try of $VerifyTries)." -ForegroundColor Cyan
            $uploaded = @($rows | Where-Object { $_.Action -match '(^|, )(created|updated)$' } | ForEach-Object { $_.Guide -replace '\.md$', '' })
            $bad = 0
            foreach ($g in $guides | Where-Object { $uploaded -contains $_.File }) {
                $problems = Get-DocProblems -Guide $g
                if ($problems.Count -gt 0) { $bad++; Write-Host "  $($g.File).md: $($problems -join '; ')" -ForegroundColor Red }
            }
            if ($uploaded.Count -eq 0) {
                Write-Host 'Verify: no Doc was uploaded this run, so none was re-read whole.'
            } elseif ($bad -gt 0) {
                Write-Host "Verify: $bad of $($uploaded.Count) uploaded Docs read back wrong (above)." -ForegroundColor Red
                $script:exitCode = 1
            } else {
                Write-Host "Verify: the $($uploaded.Count) uploaded Doc(s) read back with the banner under the H1 and no repository .md link left."
            }
        }
    }
}
finally {
    if ((Get-Location -Stack -ErrorAction SilentlyContinue).Count -gt 0) { Pop-Location }
    if ($script:worktreeMade) {
        # Removed whatever happened above; a failure to remove is said, with the path, never hidden.
        try {
            Invoke-Git -Arguments @('-C', $personRoot, 'worktree', 'remove', '--force', $worktreePath) | Out-Null
            Write-Host "Removed the worktree $worktreePath"
        } catch {
            Write-Host "Could not remove the worktree $worktreePath — remove it by hand: git worktree remove --force `"$worktreePath`"`n$_" -ForegroundColor Red
        }
    }
    [Console]::OutputEncoding = $previousOutputEncoding
}
if ($script:exitCode -ne 0) { exit $script:exitCode }
