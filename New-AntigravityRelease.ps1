# Gate an immutable tag on the exact main commit's CI result. The tag workflow
# independently reruns all tests before publishing assets; no token is printed.
[CmdletBinding(SupportsShouldProcess)]
param([string]$Repository='lowestprime/antigravity-exporter')
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Invoke-Checked([string]$Executable,[string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed (exit $LASTEXITCODE)." }
}
$git=(Get-Command git -CommandType Application -ErrorAction Stop).Source
$gh=(Get-Command gh -CommandType Application -ErrorAction Stop).Source
$root=$PSScriptRoot
$top=& $git -C $root rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0 -or [IO.Path]::GetFullPath($top) -ne [IO.Path]::GetFullPath($root)) { throw 'Run this script from the repository root copy.' }
$status=& $git -C $root status --porcelain
if ($LASTEXITCODE -ne 0 -or $status) { throw 'Commit/review local changes first; a release requires a clean worktree.' }
$origin=& $git -C $root remote get-url origin
if ($LASTEXITCODE -ne 0 -or $origin -notin @("https://github.com/$Repository.git","https://github.com/$Repository","git@github.com:$Repository.git")) { throw 'Origin does not match requested repository.' }
$branch=& $git -C $root branch --show-current
if ($branch -ne 'main') { throw 'Release from main only.' }
Import-Module (Join-Path $root 'AntigravityExporter.psm1') -Force
$py=Get-AntigravityPython
Invoke-Checked $py @((Join-Path $root 'tools\public_release.py'),'--tracked')
$version=(& $py (Join-Path $root 'agy_export.py') --version).Trim()
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+(?:rc\d+)?$') { throw 'Unsupported release version.' }
$tag="v$version"
$sha=(& $git -C $root rev-parse HEAD).Trim()
$remote=& $git -C $root ls-remote origin refs/heads/main
if ($LASTEXITCODE -ne 0 -or -not $remote -or ($remote -split '\s+')[0] -ne $sha) { throw 'Push the reviewed main commit before releasing.' }
$existing=& $git -C $root ls-remote --exit-code --tags origin "refs/tags/$tag"
$tagExit=$LASTEXITCODE
if ($tagExit -eq 0) { throw "Tag already exists; do not overwrite $tag. Inspect its release workflow." }
if ($tagExit -ne 2) { throw 'Unable to determine remote tag state.' }
# The server may index the just-pushed CI run after the push returns. Do not tag
# merely because a run is absent; require an identified run for the exact SHA.
$runs=@()
for ($attempt=0;$attempt -lt 12;$attempt++) {
    $raw=& $gh run list --repo $Repository --workflow ci.yml --commit $sha --event push --limit 10 --json databaseId,headSha,conclusion,status
    if ($LASTEXITCODE -ne 0) { throw 'Could not query CI runs.' }
    $runs=@(($raw -join "`n") | ConvertFrom-Json | Where-Object headSha -eq $sha)
    if ($runs.Count) { break }
    Start-Sleep -Seconds 5
}
if (-not $runs.Count) { throw 'No CI run for the exact commit was found; no tag was created.' }
$run=$runs[0]
if ($run.status -ne 'completed') { Invoke-Checked $gh @('run','watch',[string]$run.databaseId,'--repo',$Repository,'--exit-status') }
$raw=& $gh run view $run.databaseId --repo $Repository --json headSha,conclusion
if ($LASTEXITCODE -ne 0) { throw 'Could not read CI result.' }
$check=($raw -join "`n") | ConvertFrom-Json
if ($check.headSha -ne $sha -or $check.conclusion -ne 'success') { throw 'Exact-commit CI has not passed; release refused.' }
if (-not $PSCmdlet.ShouldProcess("$Repository@$sha", "Create and push immutable annotated tag $tag")) { return }
Invoke-Checked $git @('-C',$root,'tag','-a',$tag,'-m',"Antigravity Exporter $version",$sha)
Invoke-Checked $git @('-C',$root,'push','origin',"refs/tags/$tag")
Write-Host "Pushed $tag. The release workflow must pass its own matrix before publishing."
