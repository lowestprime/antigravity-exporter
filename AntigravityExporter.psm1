# Portable PowerShell 7 interface. Exported function verbs are approved;
# ergonomic agy-* names are aliases, not unapproved-verb functions.
Set-StrictMode -Version Latest
$script:PythonPath = $null
$script:CliPath = Join-Path $PSScriptRoot 'agy_export.py'
$script:SwitchMap = @{
    '-OutDir'='--out-dir'; '-Root'='--root'; '-Zip'='--zip'; '-Json'='--json'
    '-Project'='--project'; '-Variant'='--variant'; '-Id'='--id'; '-Name'='--name'
    '-IncludeThinking'='--include-thinking'; '-NoThinking'='--no-thinking'
    '-Share'='--share'; '-Redact'='--redact'; '-NoRedact'='--no-redact'
    '-Archive'='--archive'; '-NoArchive'='--no-archive'
    '-ArchiveCleanup'='--archive-cleanup'; '-NoArchiveCleanup'='--no-archive-cleanup'
    '-SourceCopy'='--copy-source'; '-NoSourceCopy'='--no-source-copy'
    '-IncludeNative'='--include-native'; '-Archival'='--include-native'
    '-IncludeArtifacts'='--include-artifacts'; '-RawJsonl'='--records-jsonl'
    '-RecordsJsonl'='--records-jsonl'; '-Handoff'='--handoff'
    '-Review'='--review'; '-NoReview'='--no-review'; '-ReviewChunkBytes'='--review-chunk-bytes'
    '-ArchiveMethod'='--archive-method'; '-ArchiveBackend'='--archive-backend'
    '-ArchiverPath'='--archiver-path'; '-ArchiveLevel'='--archive-level'
    '-ArchiveThreads'='--archive-threads'; '-ArchiveDictionary'='--archive-dictionary'
    '-ArchiveWordSize'='--archive-word-size'; '-ArchiveTimeout'='--archive-timeout'
    '-ToolOutputs'='--tool-outputs'; '-MaxToolChars'='--max-tool-chars'
    '-Tokenizer'='--tokenizer'; '-TokenEncoding'='--token-encoding'
    '-RequireTiktoken'='--require-tiktoken'; '-Fast'='--fast'
    '-ProgressStyle'='--progress-style'; '-NoProgress'='--no-progress'
    '-VerifyDb'='--verify-db'; '-NoVerifyDb'='--no-verify-db'
    '-VerifyChunks'='--verify-chunks'; '-AllowReduced'='--allow-reduced'
    '-StrictEvents'='--strict-events'; '-SkipInvalid'='--skip-invalid'
    '-FromStep'='--from-step'; '-ToStep'='--to-step'; '-Step'='--step'
    '-Message'='--message'; '-FromMessage'='--from-message'; '-ToMessage'='--to-message'
    '-Turn'='--turn'; '-LastNTurns'='--last-n-turns'; '-LastNSteps'='--last-n-steps'
    '-MinChars'='--min-chars'; '-FilenameTemplate'='--filename-template'
    '-NoFilenameCounts'='--no-filename-counts'; '-IncludeSessionShortId'='--include-session-short-id'
    '-StableFilenames'='--stable-filenames'; '-Collision'='--collision'
    '-RecoverToolOutputs'='--recover-tool-outputs'; '-NoRecoverToolOutputs'='--no-recover-tool-outputs'
    '-OpenAfter'='--open-after'; '-Clipboard'='--clipboard'; '-NoFile'='--no-file'; '-Stdout'='--stdout'
    '-Literal'='--literal'; '-ChooseOutDir'='--choose-out-dir'; '-SaveAs'='--save-as'
    '-RememberOutDir'='--remember-out-dir'; '-SessionsFile'='--sessions-file'
    '-All'='--all'; '-LatestSession'='--latest-session'; '-Mode'='--mode'
    '-Set'='--set'; '-Get'='--get'; '-Reset'='--reset'; '-Metadata'='--metadata'
    '-MaxDictionaryMiB'='--max-dictionary-mib'; '-MaxRecordBytes'='--max-record-bytes'
}

function Set-AntigravityPython {
    [CmdletBinding()]
    param([Parameter(Mandatory,Position=0)][string]$Path)
    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    & $resolved -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'
    if ($LASTEXITCODE -ne 0) { throw 'Selected Python failed validation.' }
    $script:PythonPath = $resolved
}

function Get-AntigravityPython {
    [CmdletBinding()]
    param()
    if ($script:PythonPath -and (Test-Path -LiteralPath $script:PythonPath -PathType Leaf)) {
        return $script:PythonPath
    }
    if ($env:AGY_EXPORT_PYTHON) {
        Set-AntigravityPython -Path $env:AGY_EXPORT_PYTHON
        return $script:PythonPath
    }
    $uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if ($uv) {
        $found = & $uv.Source python find --managed-python --no-project --no-python-downloads --offline 3.13 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) {
            $candidate = ($found | Select-Object -Last 1).Trim()
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                Set-AntigravityPython -Path $candidate
                return $script:PythonPath
            }
        }
    }
    $launcher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
    if ($launcher) {
        $found = & $launcher.Source -3.13 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) {
            Set-AntigravityPython -Path (($found | Select-Object -Last 1).Trim())
            return $script:PythonPath
        }
    }
    $python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
    if ($python) {
        Set-AntigravityPython -Path $python.Source
        return $script:PythonPath
    }
    throw 'No existing Python 3.10+ found. Set-AntigravityPython -Path <python.exe>; no packages or Python downloads were attempted.'
}

function Invoke-AntigravityExporter {
    # Simple function deliberately preserves arbitrary CLI arguments in $args.
    $python = Get-AntigravityPython
    if (-not (Test-Path -LiteralPath $script:CliPath -PathType Leaf)) { throw "Missing exporter: $script:CliPath" }
    $forward = [System.Collections.Generic.List[string]]::new()
    foreach ($argument in $args) {
        $token = [string]$argument
        if ($script:SwitchMap.ContainsKey($token)) { $forward.Add($script:SwitchMap[$token]) }
        else { $forward.Add($token) }
    }
    & $python $script:CliPath @forward
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "Antigravity exporter exited with code $code. App-owned data was not modified." }
}

function Get-AntigravitySession { Invoke-AntigravityExporter sessions @args }
function Get-AntigravityTurn { Invoke-AntigravityExporter turns --id @args }
function Get-AntigravityRecord { Invoke-AntigravityExporter records --id @args }
function Get-AntigravityResponse { Invoke-AntigravityExporter responses --id @args }
function Get-AntigravityAudit { Invoke-AntigravityExporter audit --id @args }
function Get-AntigravityConfiguration { Invoke-AntigravityExporter config @args }
function Test-AntigravityEnvironment { Invoke-AntigravityExporter doctor @args }
function Test-AntigravityArchive { Invoke-AntigravityExporter verify @args }
function Test-AntigravityAccess { Invoke-AntigravityExporter diagnose-access @args }
function Select-AntigravitySession { Invoke-AntigravityExporter browse @args }
function ConvertTo-AntigravityReview { Invoke-AntigravityExporter review-from @args }
function Export-AntigravityThread { Invoke-AntigravityExporter export --mode thread --id @args }
function Export-AntigravityChat { Invoke-AntigravityExporter export --mode chat --id @args }
function Export-AntigravityAction { Invoke-AntigravityExporter export --mode actions --id @args }
function Export-AntigravityFinal { Invoke-AntigravityExporter export --mode last-substantial --id @args }
function Export-AntigravityCheckpoint { Invoke-AntigravityExporter export --mode checkpoint-context --id @args }
function Export-AntigravityFast { Invoke-AntigravityExporter export --mode thread --fast --id @args }
function Export-AntigravityProject { Invoke-AntigravityExporter export --project @args }
function Export-AntigravityClipboard { Invoke-AntigravityExporter export --mode thread --clipboard --no-file --id @args }
function Export-AntigravityOpen { Invoke-AntigravityExporter export --mode thread --open-after --id @args }

function Export-AntigravityTurn {
    if ($args.Count -lt 2) { throw 'Usage: agy-turn UUID TURN [options]' }
    $forward = @('export','--mode','turn','--id',[string]$args[0],'--turn',[string]$args[1])
    if ($args.Count -gt 2) { $forward += $args[2..($args.Count-1)] }
    Invoke-AntigravityExporter @forward
}
function Export-AntigravityRecent {
    if ($args.Count -lt 1) { throw 'Usage: agy-last UUID [COUNT] [options]' }
    $count = 1; $start = 1
    if ($args.Count -gt 1 -and [string]$args[1] -match '^\d+$') { $count = [int]$args[1]; $start = 2 }
    $forward = @('export','--mode','thread','--id',[string]$args[0],'--last-n-turns',[string]$count)
    if ($args.Count -gt $start) { $forward += $args[$start..($args.Count-1)] }
    Invoke-AntigravityExporter @forward
}
function Export-AntigravityResponse {
    if ($args.Count -lt 2) { throw 'Usage: agy-response UUID TURN [options]' }
    $forward = @('export','--mode','response','--id',[string]$args[0],'--turn',[string]$args[1])
    if ($args.Count -gt 2) { $forward += $args[2..($args.Count-1)] }
    Invoke-AntigravityExporter @forward
}
function Export-AntigravityResponseBlock {
    if ($args.Count -lt 2) { throw 'Usage: agy-response-block UUID TURN [options]' }
    $forward = @('export','--mode','response-block','--id',[string]$args[0],'--turn',[string]$args[1])
    if ($args.Count -gt 2) { $forward += $args[2..($args.Count-1)] }
    Invoke-AntigravityExporter @forward
}
function Export-AntigravityMessage {
    if ($args.Count -lt 2) { throw 'Usage: agy-msg UUID MESSAGE_ORDINAL [options]' }
    $forward = @('export','--mode','message','--id',[string]$args[0],'--message',[string]$args[1])
    if ($args.Count -gt 2) { $forward += $args[2..($args.Count-1)] }
    Invoke-AntigravityExporter @forward
}
function Export-AntigravityRange {
    if ($args.Count -lt 3) { throw 'Usage: agy-range UUID FIRST_STEP LAST_STEP [options]' }
    $forward = @('export','--mode','range','--id',[string]$args[0],'--from-step',[string]$args[1],'--to-step',[string]$args[2])
    if ($args.Count -gt 3) { $forward += $args[3..($args.Count-1)] }
    Invoke-AntigravityExporter @forward
}
function Export-AntigravityBatch {
    if ($args.Count -lt 1) { throw 'Usage: agy-batch @("UUID1", "UUID2") [options]' }
    $forward = @('export','--mode','thread')
    foreach ($identifier in @($args[0])) { $forward += @('--id',[string]$identifier) }
    if ($args.Count -gt 1) { $forward += $args[1..($args.Count-1)] }
    Invoke-AntigravityExporter @forward
}
function Set-AntigravityOutputDirectory {
    if ($args.Count -ne 1) { throw 'Usage: agy-set-dir DIRECTORY' }
    Invoke-AntigravityExporter config --set ('out_dir=' + [string]$args[0])
}
function Set-AntigravityFilenameTemplate {
    if ($args.Count -ne 1) { throw 'Usage: agy-set-template TEMPLATE' }
    Invoke-AntigravityExporter config --set ('template=' + [string]$args[0])
}

$aliases = @{
    'agy-export'='Invoke-AntigravityExporter'; 'agy-sessions'='Get-AntigravitySession'
    'agy-turns'='Get-AntigravityTurn'; 'agy-list'='Get-AntigravityRecord'; 'agy-responses'='Get-AntigravityResponse'
    'agy-audit'='Get-AntigravityAudit'; 'agy-config'='Get-AntigravityConfiguration'
    'agy-doctor'='Test-AntigravityEnvironment'; 'agy-verify'='Test-AntigravityArchive'; 'agy-access'='Test-AntigravityAccess'
    'agy-browse'='Select-AntigravitySession'; 'agy-review'='ConvertTo-AntigravityReview'
    'agy-thread'='Export-AntigravityThread'; 'agy-chat'='Export-AntigravityChat'; 'agy-actions'='Export-AntigravityAction'
    'agy-final'='Export-AntigravityFinal'; 'agy-live'='Export-AntigravityCheckpoint'
    'agy-fast'='Export-AntigravityFast'; 'agy-project'='Export-AntigravityProject'
    'agy-thread-clip'='Export-AntigravityClipboard'; 'agy-open'='Export-AntigravityOpen'
    'agy-turn'='Export-AntigravityTurn'; 'agy-last'='Export-AntigravityRecent'
    'agy-response'='Export-AntigravityResponse'; 'agy-response-block'='Export-AntigravityResponseBlock'
    'agy-msg'='Export-AntigravityMessage'; 'agy-range'='Export-AntigravityRange'; 'agy-batch'='Export-AntigravityBatch'
    'agy-set-dir'='Set-AntigravityOutputDirectory'; 'agy-set-template'='Set-AntigravityFilenameTemplate'
}
foreach ($pair in $aliases.GetEnumerator()) { Set-Alias -Name $pair.Key -Value $pair.Value -Scope Script }
$publicFunctions = @($aliases.Values | Sort-Object -Unique) + @('Get-AntigravityPython','Set-AntigravityPython')
Export-ModuleMember -Function $publicFunctions -Alias @($aliases.Keys)
