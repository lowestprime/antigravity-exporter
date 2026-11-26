# Install the verified current-user module using PowerShell's native discovery.
# No profile, PATH, execution policy, Python environment or exporter preferences change.
[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('CurrentUser','Portable')][string]$Scope = 'CurrentUser',
    [string]$Destination,
    [string]$PythonPath,
    [switch]$NoImport,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'Use PowerShell 7 for AntigravityExporter.' }
$source = [IO.Path]::GetFullPath($PSScriptRoot)
$manifestPath = Join-Path $source 'PUBLIC_FILES.json'
$moduleMetadata = Import-PowerShellDataFile -LiteralPath (Join-Path $source 'AntigravityExporter.psd1')
$version = [string]$moduleMetadata.ModuleVersion
$packageMetadata = Get-Content -LiteralPath (Join-Path $source 'pyproject.toml') -Raw
if ($packageMetadata -notmatch '(?m)^version\s*=\s*"([^"]+)"\s*$' -or $Matches[1] -ne $version) {
    throw 'PowerShell module and Python package versions disagree.'
}
if ($PSBoundParameters.ContainsKey('Destination') -and -not $PSBoundParameters.ContainsKey('Scope')) { $Scope = 'Portable' }
$moduleRoots = @($env:PSModulePath -split [regex]::Escape([string][IO.Path]::PathSeparator) | Where-Object { $_ } | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd([IO.Path]::DirectorySeparatorChar) })
if (-not $Destination) {
    if ($Scope -eq 'Portable') {
        $programs = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Programs' } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.local/share' }
        $Destination = Join-Path $programs "AntigravityExporter/$version"
    } else {
        $moduleRoot = if ($IsWindows) {
            Join-Path (Split-Path -Parent $PROFILE.CurrentUserAllHosts) 'Modules'
        } elseif ($env:XDG_DATA_HOME) {
            Join-Path $env:XDG_DATA_HOME 'powershell/Modules'
        } else {
            Join-Path ([Environment]::GetFolderPath('UserProfile')) '.local/share/powershell/Modules'
        }
        $Destination = Join-Path $moduleRoot "AntigravityExporter/$version"
    }
}
$Destination = [IO.Path]::GetFullPath($Destination).TrimEnd([IO.Path]::DirectorySeparatorChar)
$comparison = if ($IsWindows) { [StringComparison]::OrdinalIgnoreCase } else { [StringComparison]::Ordinal }
$sourcePrefix = $source.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if ($Destination.Equals($source.TrimEnd([IO.Path]::DirectorySeparatorChar),$comparison) -or $Destination.StartsWith($sourcePrefix,$comparison)) {
    throw 'Installation destination cannot be the source directory or its descendant.'
}
if ($Scope -eq 'CurrentUser') {
    $nameDirectory = Split-Path -Parent $Destination
    $moduleRoot = Split-Path -Parent $nameDirectory
    if ((Split-Path -Leaf $nameDirectory) -ne 'AntigravityExporter' -or (Split-Path -Leaf $Destination) -ne $version) {
        throw "CurrentUser destination must end in AntigravityExporter/$version. Use -Scope Portable for an arbitrary location."
    }
    $foundRoot = @($moduleRoots | Where-Object { $_.Equals($moduleRoot,$comparison) })
    if (-not $foundRoot.Count) { throw "CurrentUser module root is not on this process's PSModulePath: $moduleRoot. No profile or PATH was modified." }
}
function Assert-OrdinaryPath([string]$Path) {
    $current = $Path
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.LinkType) {
                throw "Symlink/junction installation path rejected: $current"
            }
        }
        $next = Split-Path -Parent $current
        if (-not $next -or $next -eq $current) { break }
        $current = $next
    }
}
Assert-OrdinaryPath $source
Assert-OrdinaryPath $Destination
$public = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json -AsHashtable
if ($public.files -isnot [System.Collections.IDictionary] -or -not $public.files.Count) { throw 'Invalid public-file manifest.' }
$seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($relative in $public.files.Keys) {
    if ([IO.Path]::IsPathRooted($relative) -or $relative -match '\\|(^|/)\.\.?(/|$)|:' -or -not $seen.Add($relative)) {
        throw "Unsafe or duplicate public-file path: $relative"
    }
    $path = Join-Path $source $relative
    Assert-OrdinaryPath $path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing public file: $relative" }
    if ((Get-Item -LiteralPath $path).Length -ne $public.files[$relative].bytes -or
        (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $public.files[$relative].sha256) {
        throw "Source hash/length mismatch: $relative"
    }
}
foreach ($required in @('AntigravityExporter.psd1','AntigravityExporter.psm1','agy_export.py','pyproject.toml')) {
    if (-not $seen.Contains($required)) { throw "Public manifest omits required module file: $required" }
}
if ($PythonPath) {
    $PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
    & $PythonPath -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'
    if ($LASTEXITCODE -ne 0) { throw 'Selected Python failed validation; no installation was changed.' }
}
function Test-InstalledCopy([string]$Directory) {
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) { return $false }
    foreach ($relative in $public.files.Keys) {
        $path = Join-Path $Directory $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
        Assert-OrdinaryPath $path
        if ((Get-Item -LiteralPath $path).Length -ne $public.files[$relative].bytes -or
            (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $public.files[$relative].sha256) { return $false }
    }
    $installedManifest = Join-Path $Directory 'PUBLIC_FILES.json'
    return (Test-Path -LiteralPath $installedManifest -PathType Leaf) -and
        (Get-FileHash -LiteralPath $installedManifest -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
}
function Unblock-VerifiedScripts([string]$Directory) {
    if (-not ($IsWindows -or $IsMacOS)) { return }
    if (-not (Test-InstalledCopy $Directory)) { throw 'Refusing to unblock an unverified installation.' }
    # The user selected this installer. Unblock only its verified copied code;
    # never change the source files, unrelated downloads or execution policy.
    foreach ($relative in $public.files.Keys) {
        if ([IO.Path]::GetExtension($relative) -in '.ps1','.psm1','.psd1') {
            Unblock-File -LiteralPath (Join-Path $Directory $relative) -ErrorAction Stop
        }
    }
}
$existing = Test-Path -LiteralPath $Destination
$matchesSource = $existing -and (Test-InstalledCopy $Destination)
if ($existing -and -not $matchesSource -and -not $Force) {
    throw "A different or modified installation exists at $Destination. Use -Force only to back up and replace that version, or choose -Scope Portable -Destination PATH."
}
$backup = $null
if ($PSCmdlet.ShouldProcess($Destination, "Install verified AntigravityExporter $version ($Scope)")) {
    if (-not $matchesSource) {
        $parent = Split-Path -Parent $Destination
        [void][IO.Directory]::CreateDirectory($parent)
        $stage = Join-Path $parent ('.agy-install-' + [Guid]::NewGuid().ToString('N'))
        try {
            [void][IO.Directory]::CreateDirectory($stage)
            foreach ($relative in $public.files.Keys) {
                $target = Join-Path $stage $relative
                [void][IO.Directory]::CreateDirectory((Split-Path -Parent $target))
                Copy-Item -LiteralPath (Join-Path $source $relative) -Destination $target
            }
            Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $stage 'PUBLIC_FILES.json')
            if (-not (Test-InstalledCopy $stage)) { throw 'Staged installed bytes failed independent verification.' }
            Unblock-VerifiedScripts $stage
            $null = Test-ModuleManifest -Path (Join-Path $stage 'AntigravityExporter.psd1') -ErrorAction Stop
            if ($existing) {
                $backupRoot = if ($Scope -eq 'CurrentUser') { Join-Path (Split-Path -Parent $moduleRoot) 'AntigravityExporter-install-backups' } else { Join-Path $parent 'AntigravityExporter-install-backups' }
                Assert-OrdinaryPath $backupRoot
                [void][IO.Directory]::CreateDirectory($backupRoot)
                $backup = Join-Path $backupRoot ($version + '-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8))
                [IO.Directory]::Move($Destination,$backup)
            }
            try { [IO.Directory]::Move($stage,$Destination) }
            catch {
                if ($backup -and -not (Test-Path -LiteralPath $Destination)) { [IO.Directory]::Move($backup,$Destination) }
                throw
            }
        } finally {
            if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
        }
    }
    if ($matchesSource) { Unblock-VerifiedScripts $Destination }
    if ($Scope -eq 'CurrentUser' -and -not $NoImport) {
        Get-Module -Name AntigravityExporter | Remove-Module -Force
        Import-Module -Name (Join-Path $Destination 'AntigravityExporter.psd1') -Global -Force -ErrorAction Stop -WarningAction Stop
        if ($PythonPath) { Set-AntigravityPython -Path $PythonPath }
        $loaded = Get-Module -Name AntigravityExporter
        if (-not $loaded -or $loaded.Version -ne [version]$version -or
            -not $loaded.Path.Equals((Join-Path $Destination 'AntigravityExporter.psm1'),$comparison)) { throw 'Installed module did not load from the expected versioned path.' }
    }
    [pscustomobject]@{
        Version=$version; Scope=$Scope; Destination=$Destination; Verified=$true
        AlreadyInstalled=$matchesSource; Backup=$backup; ProfileModified=$false; PathModified=$false; UnblockedVerifiedScripts=($IsWindows -or $IsMacOS)
        ActiveInCurrentSession=($Scope -eq 'CurrentUser' -and -not $NoImport)
    }
    if ($Scope -eq 'CurrentUser') {
        Write-Host 'Installed for native module auto-loading. In a new PowerShell 7 window run: agy-export --version; agy-doctor'
        Write-Host 'If your profile explicitly imports an older file path, change that one import to: Import-Module AntigravityExporter'
    } else {
        Write-Host "Portable files verified. Import-Module '$Destination/AntigravityExporter.psd1'"
    }
}
