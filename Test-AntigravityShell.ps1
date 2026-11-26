# Actual PowerShell-to-Python argument checks and isolated current-user module deployment.
# No real profile or Antigravity state is read or modified.
[CmdletBinding()]
param([Parameter(Mandatory)][string]$PythonPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
$root = Join-Path ([IO.Path]::GetTempPath()) ('agy-shell-test-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($root)
$oldModulePath = $env:PSModulePath
$oldProbe = $env:AGY_TEST_ARGUMENTS
$oldPython = $env:AGY_EXPORT_PYTHON
try {
    $probe = Join-Path $root 'argument probe.py'
    $env:AGY_TEST_ARGUMENTS = Join-Path $root 'arguments.json'
    $env:AGY_EXPORT_PYTHON = $PythonPath
    [IO.File]::WriteAllText($probe, 'import json,os,sys; open(os.environ["AGY_TEST_ARGUMENTS"],"w",encoding="utf-8").write(json.dumps(sys.argv[1:],ensure_ascii=False))', [Text.UTF8Encoding]::new($false))
    Import-Module (Join-Path $PSScriptRoot 'AntigravityExporter.psd1') -Force -WarningAction Stop
    Set-AntigravityPython $PythonPath
    & (Get-Module AntigravityExporter) { param($Path) $script:CliPath = $Path } $probe
    $id = '11111111-2222-4333-8444-555555555555'
    $path = 'C:\Some directory\Export here'
    function Assert-Arguments([scriptblock]$Call, [string[]]$Expected) {
        & $Call
        $actual = @(Get-Content -LiteralPath $env:AGY_TEST_ARGUMENTS -Raw | ConvertFrom-Json)
        if ($actual.Count -ne $Expected.Count) { throw "Argument count mismatch: $($actual | ConvertTo-Json -Compress)" }
        for ($i=0; $i -lt $actual.Count; $i++) {
            if ($actual[$i] -cne $Expected[$i]) { throw "Argument $i differs: '$($actual[$i])', expected '$($Expected[$i])'" }
        }
    }
    Assert-Arguments { agy-thread $id -OutDir $path -ArchiveMaxBytes 32000 -NoProgress } @('export','--mode','thread','--id',$id,'--out-dir',$path,'--archive-max-bytes','32000','--no-progress')
    $literal = 'turn:2; $(literal) "quotes" Unicode 🧠'
    Assert-Arguments { agy-since-text $id $literal -Occurrence 2 } @('export','--mode','thread','--id',$id,'--since-text',$literal,'--since-occurrence','2')
    Assert-Arguments { agy-since-text $literal $id } @('export','--mode','thread','--id',$id,'--since-text',$literal)
    Assert-Arguments { agy-since-text $id '-Turn' } @('export','--mode','thread','--id',$id,'--since-text=-Turn')
    Assert-Arguments { agy-since $id 'turn:2' -WorkDir $path } @('export','--mode','thread','--id',$id,'--since','turn:2','--work-dir',$path)
    Assert-Arguments { agy-since-turn $id 2 } @('export','--mode','thread','--id',$id,'--since-turn','2')
    Assert-Arguments { agy-since-id $id 'step:0' } @('export','--mode','thread','--id',$id,'--since-id','step:0')
    Assert-Arguments { agy-since-time $id '2026-01-01T00:00:00Z' } @('export','--mode','thread','--id',$id,'--since-time','2026-01-01T00:00:00Z')
    Assert-Arguments { agy-batch @($id,'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee') -SeparateArchives } @('export','--mode','thread','--id',$id,'--id','aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee','--separate-archives')
    Assert-Arguments { @($id,'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee') | agy-batch -OutDir $path } @('export','--mode','thread','--id',$id,'--id','aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee','--out-dir',$path)
    $rawOptions = @('--out-dir',$path,'--no-progress')
    Assert-Arguments { @($id) | agy-batch @rawOptions } @('export','--mode','thread','--id',$id,'--out-dir',$path,'--no-progress')
    Assert-Arguments { [pscustomobject]@{id=$id;selector=('antigravity:'+$id)} | agy-batch -NoProgress } @('export','--mode','thread','--id',('antigravity:'+$id),'--no-progress')
    Assert-Arguments { agy-sessions -Sort 'project:asc,modified:desc' -Pager never -PageSize 20 } @('sessions','--sort','project:asc,modified:desc','--pager','never','--page-size','20')
    Assert-Arguments { agy-turns $id -Sort turn:desc } @('turns','--id',$id,'--sort','turn:desc')
    Assert-Arguments { agy-latest actions -Root $path } @('export','--latest-session','--mode','actions','--root',$path)
    Assert-Arguments { agy-help } @('--help')
    Assert-Arguments { agy-tokenizer -Tokenizer regex } @('tokenizer','--tokenizer','regex')
    Assert-Arguments { agy-set-template } @('config','--get','template')
    Assert-Arguments { agy-set-dir } @('config','--get','out_dir')
    $approved = @(Get-Verb | Select-Object -ExpandProperty Verb)
    if (@(Get-Command -Module AntigravityExporter -CommandType Function | Where-Object Verb -NotIn $approved).Count) { throw 'An exported function has an unapproved verb.' }
    $rejected = $false
    try { agy-project } catch { $rejected = $_.Exception.Message -like 'Usage:*' }
    if (-not $rejected) { throw 'An empty project selector was not rejected before invocation.' }
    Write-Host 'PASS: actual PowerShell-to-Python argument forwarding, Unicode, quoted values and exported verbs.'

    # Create a synthetic source deployment manifest from public code only. This
    # isolates deployment tests from the mutable checkout's release hash list.
    $source = Join-Path $root 'source'
    [void][IO.Directory]::CreateDirectory($source)
    foreach ($name in @('Install-AntigravityExporter.ps1','AntigravityExporter.psd1','AntigravityExporter.psm1','agy_export.py','pyproject.toml')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $source $name)
    }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'antigravity_exporter') -Destination $source -Recurse
    $metadata = Import-PowerShellDataFile (Join-Path $source 'AntigravityExporter.psd1')
    $version = [string]$metadata.ModuleVersion
    $package = Join-Path $source 'pyproject.toml'
    $packageText = (Get-Content -LiteralPath $package -Raw) -replace '(?m)^version\s*=\s*"[^"]+"', ('version = "' + $version + '"')
    [IO.File]::WriteAllText($package, $packageText, [Text.UTF8Encoding]::new($false))
    $hashes = [ordered]@{}
    foreach ($file in Get-ChildItem -LiteralPath $source -File -Recurse | Where-Object Extension -ne '.pyc') {
        $relative = [IO.Path]::GetRelativePath($source,$file.FullName).Replace('\','/')
        $hashes[$relative] = @{bytes=$file.Length;sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
    }
    [IO.File]::WriteAllText((Join-Path $source 'PUBLIC_FILES.json'), (@{files=$hashes}|ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    if ($IsWindows) {
        Set-Content -LiteralPath (Join-Path $source 'AntigravityExporter.psm1') -Stream Zone.Identifier -Value "[ZoneTransfer]`r`nZoneId=3" -NoNewline
    }
    $moduleRoot = Join-Path $root 'Modules'
    $destination = Join-Path $moduleRoot "AntigravityExporter/$version"
    $env:PSModulePath = $moduleRoot + [IO.Path]::PathSeparator + $oldModulePath
    $legacyModule = Join-Path $moduleRoot 'AntigravityExporter/AntigravityExporter.psm1'
    [void][IO.Directory]::CreateDirectory((Split-Path -Parent $legacyModule))
    [IO.File]::WriteAllText($legacyModule, "function Get-LegacyMarker { 'old module' }; Export-ModuleMember -Function Get-LegacyMarker", [Text.UTF8Encoding]::new($false))
    $legacyHash = (Get-FileHash -LiteralPath $legacyModule).Hash
    $installer = Join-Path $source 'Install-AntigravityExporter.ps1'
    & $installer -Scope CurrentUser -Destination $destination -PythonPath $PythonPath -WhatIf | Out-Null
    if (Test-Path -LiteralPath $destination) { throw '-WhatIf created installation files.' }
    $receipt = & $installer -Scope CurrentUser -Destination $destination -PythonPath $PythonPath
    if (-not $receipt.Verified -or $receipt.ProfileModified -or $receipt.PathModified -or -not $receipt.ActiveInCurrentSession) { throw 'Installation receipt failed.' }
    if ((Get-Module AntigravityExporter).Version -ne [version]$version) { throw 'Import did not persist after installer returned.' }
    if ($IsWindows) {
        $sourceMarker = Get-Item -LiteralPath (Join-Path $source 'AntigravityExporter.psm1') -Stream Zone.Identifier -ErrorAction SilentlyContinue
        $installedMarker = Get-Item -LiteralPath (Join-Path $destination 'AntigravityExporter.psm1') -Stream Zone.Identifier -ErrorAction SilentlyContinue
        if (-not $sourceMarker -or $installedMarker) { throw 'Scoped unblocking changed source marking or left copied code blocked.' }
    }
    $again = & $installer -Scope CurrentUser -Destination $destination -NoImport
    if (-not $again.AlreadyInstalled -or $again.Backup) { throw 'Identical installation was not idempotent.' }
    $pwsh = (Get-Process -Id $PID).Path
    $result = & $pwsh -NoLogo -NoProfile -Command 'agy-help | Out-Null; if (-not (Get-Module AntigravityExporter)) { exit 2 }; (Get-Module AntigravityExporter).Version.ToString()'
    if ($LASTEXITCODE -ne 0 -or ($result|Select-Object -Last 1) -ne $version) { throw 'Fresh process module autoload failed.' }
    $byName = & $pwsh -NoLogo -NoProfile -Command 'Import-Module AntigravityExporter -WarningAction Stop; (Get-Module AntigravityExporter).Version.ToString()'
    if ($LASTEXITCODE -ne 0 -or ($byName|Select-Object -Last 1) -ne $version -or (Get-FileHash -LiteralPath $legacyModule).Hash -ne $legacyHash) { throw 'Name-based import chose legacy module or changed its bytes.' }
    $changed = Join-Path $destination 'AntigravityExporter.psm1'
    Add-Content -LiteralPath $changed -Value '# installation test modification'
    $tamperedHash = (Get-FileHash -LiteralPath $changed -Algorithm SHA256).Hash
    $rejected = $false
    try { & $installer -Scope CurrentUser -Destination $destination -NoImport | Out-Null } catch { $rejected = $_.Exception.Message -like 'A different or modified installation*' }
    if (-not $rejected) { throw 'Unexpected replacement of modified module.' }
    $forced = & $installer -Scope CurrentUser -Destination $destination -NoImport -Force
    if (-not $forced.Backup -or (Get-FileHash -LiteralPath (Join-Path $forced.Backup 'AntigravityExporter.psm1')).Hash -ne $tamperedHash) { throw 'Replacement backup did not preserve modified bytes.' }
    Write-Host 'PASS: isolated -WhatIf, native autoload, global activation, idempotence, modified-install refusal and backup.'
} finally {
    Get-Module -Name AntigravityExporter | Remove-Module -Force
    $env:PSModulePath = $oldModulePath
    $env:AGY_TEST_ARGUMENTS = $oldProbe
    $env:AGY_EXPORT_PYTHON = $oldPython
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
