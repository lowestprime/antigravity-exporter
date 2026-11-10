# Optional stable local copy. Does not modify profiles, PATH, existing Codex tools,
# existing Antigravity v2, or install/upgrade anything in a Python environment.
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'Programs\AntigravityExporter\0.3.0rc2')
)
$ErrorActionPreference = 'Stop'
$sourceFull = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
$destinationFull = [IO.Path]::GetFullPath($Destination)
if ($destinationFull.Equals($sourceFull, [StringComparison]::OrdinalIgnoreCase) -or
    $destinationFull.StartsWith($sourceFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Installation destination cannot be the source directory or its descendant.'
}
$Destination = $destinationFull
if (Test-Path -LiteralPath $Destination) { throw "Destination already exists; choose a new versioned directory: $Destination" }
if ($PSCmdlet.ShouldProcess($Destination, 'Copy portable Antigravity exporter')) {
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $manifestPath = Join-Path $PSScriptRoot 'PUBLIC_FILES.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    # Validate every selected public file before making a partial installation.
    foreach ($item in $manifest.files.PSObject.Properties) {
        $relative = $item.Name
        if ([IO.Path]::IsPathRooted($relative) -or $relative -match '(^|/)\.\.(/|$)') { throw 'Unsafe public allowlist entry.' }
        $path = Join-Path $PSScriptRoot $relative
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $item.Value.sha256) {
            throw "Source hash mismatch: $relative"
        }
    }
    New-Item -ItemType Directory -Path $Destination | Out-Null
    foreach ($item in $manifest.files.PSObject.Properties) {
        $target = Join-Path $Destination $item.Name
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $item.Name) -Destination $target
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ine $item.Value.sha256) {
            throw "Installed file hash mismatch: $($item.Name)"
        }
    }
    Copy-Item -LiteralPath $manifestPath -Destination $Destination
    Write-Host 'Installed portable files. No profile or PATH was changed.'
    Write-Host "Import-Module '$Destination\AntigravityExporter.psm1' -Force"
}
