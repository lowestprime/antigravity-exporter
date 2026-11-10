# Creates a NEW local repository from the reviewed public allowlist; optionally
# creates/pushes the requested public remote. Never runs in an exports directory.
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Destination = 'C:\projects\antigravity-exporter',
    [string]$Repository = 'lowestprime/antigravity-exporter',
    [string]$PythonPath,
    [switch]$Publish
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed (exit $LASTEXITCODE). No force-push or automatic rollback was performed." }
}
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw 'Expected OWNER/REPO.' }
$git = (Get-Command git -CommandType Application -ErrorAction Stop).Source
if (-not $PythonPath) {
    Import-Module (Join-Path $PSScriptRoot 'AntigravityExporter.psm1') -Force
    $PythonPath = Get-AntigravityPython
}
Invoke-Checked $PythonPath @((Join-Path $PSScriptRoot 'tools\public_release.py'))
# This gate is inside the script: a failed local test prevents publication even
# when commands were pasted interactively after an earlier failure.
& (Join-Path $PSScriptRoot 'Test-AntigravityExporter.ps1') -PythonPath $PythonPath
$name = & $git config user.name
if ($LASTEXITCODE -ne 0 -or -not $name) { throw 'Configure your desired Git commit author with git config user.name before publishing.' }
$email = & $git config user.email
if ($LASTEXITCODE -ne 0 -or -not $email) { throw 'Configure your desired Git commit email with git config user.email before publishing.' }
if (Test-Path -LiteralPath $Destination) { throw "Destination already exists; refusing to mix or overwrite repositories: $Destination" }
$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'PUBLIC_FILES.json') -Raw | ConvertFrom-Json
$gh = $null
if ($Publish) {
    $gh = (Get-Command gh -CommandType Application -ErrorAction Stop).Source
    Invoke-Checked $gh @('auth','status','--hostname','github.com')
    $login = & $gh api user --jq .login
    if ($LASTEXITCODE -ne 0) { throw 'Unable to verify the authenticated GitHub account.' }
    if ($login.Trim() -cne $Repository.Split('/')[0]) { throw "Authenticated account does not match requested owner: $Repository" }
}
if (-not $PSCmdlet.ShouldProcess($Destination, "Initialize reviewed source; publish=$Publish; target=$Repository")) { return }
New-Item -ItemType Directory -Path $Destination | Out-Null
foreach ($property in $manifest.files.PSObject.Properties) {
    $relative = $property.Name
    if ([IO.Path]::IsPathRooted($relative) -or $relative -match '(^|/)\.\.(/|$)') { throw 'Unsafe allowlist entry.' }
    $target = Join-Path $Destination $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $relative) -Destination $target
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'PUBLIC_FILES.json') -Destination $Destination
Invoke-Checked $PythonPath @((Join-Path $Destination 'tools\public_release.py'))
Invoke-Checked $git @('-C',$Destination,'init','-b','main')
# Repository-local EOL policy preserves the reviewed manifest hashes on Windows.
Invoke-Checked $git @('-C',$Destination,'config','core.autocrlf','false')
Invoke-Checked $git @('-C',$Destination,'config','user.name',$name.Trim())
Invoke-Checked $git @('-C',$Destination,'config','user.email',$email.Trim())
Invoke-Checked $git @('-C',$Destination,'add','--','.')
Invoke-Checked $PythonPath @((Join-Path $Destination 'tools\public_release.py'),'--tracked')
Invoke-Checked $git @('-C',$Destination,'commit','-m','Initial public Antigravity Exporter 0.3.0rc2')
if ($Publish) {
    # gh fails safely when the target already exists. No existing repository is changed.
    Invoke-Checked $gh @('repo','create',$Repository,'--public','--source',$Destination,'--remote','origin','--push',
        '--description','Read-only Antigravity conversation exports with verified archives and PowerShell helpers')
    $state = & $gh repo view $Repository --json nameWithOwner,visibility,url | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $state.visibility -ne 'PUBLIC' -or $state.nameWithOwner -cne $Repository) {
        throw 'Remote creation returned but readback did not confirm the expected public repository.'
    }
    $local = & $git -C $Destination rev-parse HEAD
    $remote = & $git -C $Destination ls-remote origin refs/heads/main
    if ($LASTEXITCODE -ne 0 -or -not $remote -or ($remote -split '\s+')[0] -ne $local) {
        throw 'Remote main did not match the local committed source.'
    }
    Write-Host "Verified public main: $($state.url) @ $local"
    Write-Host 'CI was triggered by the push. Run New-AntigravityRelease.ps1 only after reviewing the checks.'
} else {
    Invoke-Checked $git @('-C',$Destination,'remote','add','origin',"https://github.com/$Repository.git")
    Write-Host 'Local Git tracking initialized. The origin URL is configured; no remote repository was created or pushed.'
}
