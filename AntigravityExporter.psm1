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
    '-Since'='--since'; '-SinceTurn'='--since-turn'; '-SinceText'='--since-text'
    '-SinceId'='--since-id'; '-SinceTime'='--since-time'; '-Occurrence'='--since-occurrence'
    '-SinceOccurrence'='--since-occurrence'; '-Sort'='--sort'; '-SortOrder'='--sort-order'
    '-Pager'='--pager'; '-PageSize'='--page-size'; '-WorkDir'='--work-dir'
    '-ArchiveMaxBytes'='--archive-max-bytes'; '-SeparateArchives'='--separate-archives'
    '-Timestamps'='--timestamps'; '-NoTimestamps'='--no-timestamps'
    '-TimestampTimezone'='--timestamp-timezone'; '-TimestampZone'='--timestamp-timezone'
    '-BrowseQuery'='--browse-query'; '-BrowseSort'='--browse-sort'
    '-BrowseStatus'='--browse-status'; '-BrowsePageSize'='--browse-page-size'
    '-Missing'='--missing-source'; '-MissingSource'='--missing-source'
    '-Query'='--query'; '-NoPager'='--no-pager'; '-FullSessionTitles'='--full-session-titles'
    '-KeepFailedWork'='--keep-failed-work'; '-MaxArchiveBytes'='--max-archive-bytes'
    '-NoSeparateArchives'='--no-separate-archives'; '-ExactMaxBytes'='--exact-max-bytes'
    '-NoFrontmatter'='--no-frontmatter'; '-NoMap'='--no-map'; '-Plain'='--plain'
    '-UiStyle'='--ui-style'; '-UnknownSidecar'='--unknown-sidecar'
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
    $pythonNames = if ($IsWindows) { @('python','python3') } else { @('python3','python') }
    foreach ($name in $pythonNames) {
        $python = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if ($python) {
            Set-AntigravityPython -Path $python.Source
            return $script:PythonPath
        }
    }
    throw 'No existing Python 3.10+ found. Set-AntigravityPython -Path <python.exe>; no packages or Python downloads were attempted.'
}

function Invoke-AntigravityExporter {
    # Simple function deliberately preserves arbitrary CLI arguments in $args.
    $python = Get-AntigravityPython
    if (-not (Test-Path -LiteralPath $script:CliPath -PathType Leaf)) { throw "Missing exporter: $script:CliPath" }
    $forward = [System.Collections.Generic.List[string]]::new()
    $takesValue = @('--out-dir','--root','--zip','--project','--variant','--id','--name',
        '--review-chunk-bytes','--archive-method','--archive-backend','--archiver-path','--archive-level',
        '--archive-threads','--archive-dictionary','--archive-word-size','--archive-timeout','--tool-outputs',
        '--max-tool-chars','--tokenizer','--token-encoding','--progress-style','--from-step','--to-step',
        '--step','--message','--from-message','--to-message','--turn','--last-n-turns','--last-n-steps',
        '--min-chars','--filename-template','--collision','--sessions-file','--mode','--set','--get',
        '--max-dictionary-mib','--max-record-bytes','--since','--since-turn','--since-text','--since-id',
        '--since-time','--since-occurrence','--sort','--sort-order','--pager','--page-size','--work-dir',
        '--archive-max-bytes','--timestamp-timezone','--browse-query','--browse-sort','--browse-status',
        '--browse-page-size','--missing-source','--query','--max-archive-bytes','--exact-max-bytes')
    $expectValue = $false
    foreach ($argument in $args) {
        if ($argument -is [array]) { throw 'Nested argument arrays are supported by agy-batch only; pass CLI arguments individually.' }
        $token = [string]$argument
        if ($expectValue) {
            # argparse accepts a literal leading-dash value only in --key=value
            # form; do not reinterpret it as another PowerShell-style switch.
            if ($token.StartsWith('-')) { $forward[$forward.Count-1] += '=' + $token }
            else { $forward.Add($token) }
            $expectValue = $false; continue
        }
        if ($script:SwitchMap.ContainsKey($token)) { $token = $script:SwitchMap[$token] }
        $forward.Add($token)
        $expectValue = $takesValue -contains $token
    }
    & $python $script:CliPath @forward
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "Antigravity exporter exited with code $code. App-owned data was not modified." }
}

function Show-AntigravityHelp { Invoke-AntigravityExporter --help @args }
function Get-AntigravityTokenizer { Invoke-AntigravityExporter tokenizer @args }
function Export-AntigravityLatest {
    $mode = 'thread'; $start = 0
    if ($args.Count -gt 0 -and -not ([string]$args[0]).StartsWith('-')) { $mode = [string]$args[0]; $start = 1 }
    $forward = @('export','--latest-session','--mode',$mode)
    if ($args.Count -gt $start) { $forward += $args[$start..($args.Count-1)] }
    Invoke-AntigravityExporter @forward
}
function Get-AntigravitySession { Invoke-AntigravityExporter sessions @args }
function Get-AntigravityProject { Invoke-AntigravityExporter projects @args }
function Get-AntigravityTurn { Invoke-AntigravityExporter turns --id @args }
function Get-AntigravityRecord { Invoke-AntigravityExporter records --id @args }
function Get-AntigravityResponse { Invoke-AntigravityExporter responses --id @args }
function Get-AntigravityAudit {
    if ($args.Count -gt 0 -and -not ([string]$args[0]).StartsWith('-')) { Invoke-AntigravityExporter audit --id @args }
    else { Invoke-AntigravityExporter audit --all @args }
}
function Get-AntigravityConfiguration { Invoke-AntigravityExporter config @args }
function Test-AntigravityEnvironment { Invoke-AntigravityExporter doctor @args }
function Test-AntigravityArchive { Invoke-AntigravityExporter verify @args }
function Test-AntigravityArchiveSet { Invoke-AntigravityExporter verify-set @args }
function Repair-AntigravityAccess { Invoke-AntigravityExporter repair-access @args }
function Test-AntigravityAccess { Invoke-AntigravityExporter diagnose-access @args }
function Select-AntigravitySession { Invoke-AntigravityExporter browse @args }
function ConvertTo-AntigravityReview { Invoke-AntigravityExporter review-from @args }
function Export-AntigravityThread { Invoke-AntigravityExporter export --mode thread --id @args }
function Export-AntigravityChat { Invoke-AntigravityExporter export --mode chat --id @args }
function Export-AntigravityAction { Invoke-AntigravityExporter export --mode actions --id @args }
function Export-AntigravityFinal { Invoke-AntigravityExporter export --mode last-substantial --id @args }
function Export-AntigravityCheckpoint { Invoke-AntigravityExporter export --mode checkpoint-context --id @args }
function Export-AntigravityFast { Invoke-AntigravityExporter export --mode thread --fast --id @args }
function Export-AntigravityProject {
    if ($args.Count -lt 1 -or ([string]$args[0]).StartsWith('-')) { throw 'Usage: agy-project PROJECT_NAME [options]. Use agy-sessions to inspect recorded project names.' }
    Invoke-AntigravityExporter export --project @args
}
function Export-AntigravityClipboard { Invoke-AntigravityExporter export --mode thread --clipboard --no-file --id @args }
function Export-AntigravityOpen {
    if ($args.Count) { Invoke-AntigravityExporter export --mode thread --open-after --id @args; return }
    $directory = Invoke-AntigravityExporter config --get out_dir
    if (-not $directory) { throw 'No configured export directory.' }
    $directory = ([string]$directory).Trim()
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { throw 'The export directory does not exist yet; export a thread first.' }
    Invoke-Item -LiteralPath $directory
}

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
    [CmdletBinding(PositionalBinding=$false)]
    param(
        [Parameter(Position=0)][Alias('Id','Selector')][object[]]$SessionId = @(),
        [Parameter(ValueFromPipeline=$true)][object]$InputObject,
        [Parameter(ValueFromRemainingArguments=$true)][object[]]$Rest = @()
    )
    begin {
        $identifiers = [Collections.Generic.List[string]]::new()
        $options = @($Rest)
        # Array-splatted GNU flags can bind the first token positionally. Keep
        # the pipeline parameter separate and put such tokens back in options.
        if ($SessionId.Count -gt 0 -and ([string]$SessionId[0]).StartsWith('-')) {
            $options = @($SessionId) + $options
            $SessionId = @()
        }
        function Add-BatchSelector($Item) {
            if ($null -eq $Item) { return }
            $value = if ($Item -is [string]) { $Item } elseif ($Item.PSObject.Properties['selector']) {
                [string]$Item.selector
            } elseif ($Item.PSObject.Properties['id']) { [string]$Item.id } else {
                throw 'agy-batch accepts UUID strings or session objects with a selector/id property.'
            }
            if ([string]::IsNullOrWhiteSpace($value)) { throw 'Empty batch session selector.' }
            $identifiers.Add($value)
        }
        foreach ($item in @($SessionId)) { Add-BatchSelector $item }
    }
    process {
        if ($null -ne $InputObject) { foreach ($item in @($InputObject)) { Add-BatchSelector $item } }
    }
    end {
        if (-not $identifiers.Count) { throw 'Usage: agy-batch @("UUID1", "UUID2") [options], or pipe session objects/IDs into agy-batch.' }
        $forward = @('export','--mode','thread')
        foreach ($identifier in $identifiers) { $forward += @('--id',$identifier) }
        Invoke-AntigravityExporter @forward @options
    }
}
function Export-AntigravitySince {
    if ($args.Count -lt 2) { throw 'Usage: agy-since UUID "turn:N|id:step:N|text:WORDS|time:ISO" [options]' }
    $forward = @('export','--mode','thread','--id',[string]$args[0],'--since',[string]$args[1])
    if ($args.Count -gt 2) { $forward += $args[2..($args.Count-1)] }
    Invoke-AntigravityExporter @forward
}
function Export-AntigravitySinceText {
    if ($args.Count -lt 2) { throw 'Usage: agy-since-text UUID TEXT [-Occurrence N] [options]' }
    $identifier, $text = [string]$args[0], [string]$args[1]
    $uuid = '^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$'
    if ($identifier -notmatch $uuid -and $text -match $uuid) { $identifier, $text = $text, $identifier }
    $forward = @('export','--mode','thread','--id',$identifier,'--since-text',$text)
    if ($args.Count -gt 2) { $forward += $args[2..($args.Count-1)] }
    Invoke-AntigravityExporter @forward
}
function Export-AntigravitySinceTurn {
    if ($args.Count -lt 2 -or [string]$args[1] -notmatch '^[1-9][0-9]*$') { throw 'Usage: agy-since-turn UUID POSITIVE_TURN [options]' }
    $forward = @('export','--mode','thread','--id',[string]$args[0],'--since-turn',[string]$args[1])
    if ($args.Count -gt 2) { $forward += $args[2..($args.Count-1)] }
    Invoke-AntigravityExporter @forward
}
function Export-AntigravitySinceId {
    if ($args.Count -lt 2) { throw 'Usage: agy-since-id UUID "step:N|NATIVE_ID" [options]' }
    $forward = @('export','--mode','thread','--id',[string]$args[0],'--since-id',[string]$args[1])
    if ($args.Count -gt 2) { $forward += $args[2..($args.Count-1)] }
    Invoke-AntigravityExporter @forward
}
function Export-AntigravitySinceTime {
    if ($args.Count -lt 2) { throw 'Usage: agy-since-time UUID ISO_TIMESTAMP_WITH_OFFSET [options]' }
    $forward = @('export','--mode','thread','--id',[string]$args[0],'--since-time',[string]$args[1])
    if ($args.Count -gt 2) { $forward += $args[2..($args.Count-1)] }
    Invoke-AntigravityExporter @forward
}
function Set-AntigravityOutputDirectory {
    if (-not $args.Count) { Invoke-AntigravityExporter config --get out_dir; return }
    if ($args.Count -ne 1) { throw 'Usage: agy-set-dir DIRECTORY' }
    Invoke-AntigravityExporter config --set ('out_dir=' + [string]$args[0])
}
function Set-AntigravityFilenameTemplate {
    if (-not $args.Count) { Invoke-AntigravityExporter config --get template; return }
    if ($args.Count -ne 1) { throw 'Usage: agy-set-template TEMPLATE' }
    Invoke-AntigravityExporter config --set ('template=' + [string]$args[0])
}

$aliases = @{
    'antigravity-exporter'='Invoke-AntigravityExporter'; 'agy-exporter'='Invoke-AntigravityExporter'
    'agy-help'='Show-AntigravityHelp'; 'agy-tokenizer'='Get-AntigravityTokenizer'
    'agy-projects'='Get-AntigravityProject'; 'agy-verify-set'='Test-AntigravityArchiveSet'
    'agy-repair-access'='Repair-AntigravityAccess'; 'agy-latest'='Export-AntigravityLatest'; 'agy-archive-config'='Get-AntigravityConfiguration'
    'agy-since'='Export-AntigravitySince'; 'agy-since-text'='Export-AntigravitySinceText'
    'agy-since-turn'='Export-AntigravitySinceTurn'; 'agy-since-id'='Export-AntigravitySinceId'
    'agy-since-time'='Export-AntigravitySinceTime'
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
