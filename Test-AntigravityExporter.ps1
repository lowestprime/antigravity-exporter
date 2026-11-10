# Parse scripts, check approved function verbs, run Python regressions, and optionally
# audit a real live session. This script never submits a prompt or writes app data.
[CmdletBinding()]
param([string]$ConversationId, [string]$PythonPath)
$ErrorActionPreference = 'Stop'
foreach ($file in Get-ChildItem -LiteralPath $PSScriptRoot -File | Where-Object Extension -in '.ps1','.psm1') {
    $tokens = $null; $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName,[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
}
Import-Module (Join-Path $PSScriptRoot 'AntigravityExporter.psm1') -Force -WarningAction Stop
if ($PythonPath) { Set-AntigravityPython -Path $PythonPath }
$py = Get-AntigravityPython
$approved = @(Get-Verb | Select-Object -ExpandProperty Verb)
$bad = @(Get-Command -Module AntigravityExporter -CommandType Function | Where-Object Verb -NotIn $approved)
if ($bad.Count) { throw ('Unapproved exported verbs: ' + ($bad.Name -join ', ')) }
& $py -m unittest discover -s (Join-Path $PSScriptRoot 'tests') -v
if ($LASTEXITCODE -ne 0) { throw 'Python regression tests failed.' }
Write-Host "PASS: local PowerShell parsing/import/verbs and Python regressions. Python: $py"
if ($ConversationId) {
    Invoke-AntigravityExporter audit --id $ConversationId --verify-db --verify-chunks --json
}
