# Exercise actual helper -> Python CLI -> verified archives using only synthetic data.
[CmdletBinding()]
param([Parameter(Mandatory)][string]$PythonPath)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$PythonPath=(Resolve-Path -LiteralPath $PythonPath).Path
$sourceRoot=$PSScriptRoot
$temporary=Join-Path ([IO.Path]::GetTempPath()) ('agy-cli-test-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($temporary)
$fixture=Join-Path $temporary 'store'
$priorConfig=$env:AGY_EXPORT_CONFIG
$env:AGY_EXPORT_CONFIG=Join-Path $temporary 'config.json'
try {
    $fixtureBuilder=Join-Path $temporary 'build_fixture.py'
    @'
from pathlib import Path
import json,sqlite3
import sys
root=Path(sys.argv[1]).resolve();root.mkdir(exist_ok=True)
(root/'config/projects').mkdir(parents=True,exist_ok=True)
(root/'config/projects/demo.json').write_text(json.dumps({'id':'demo','name':'Example Project'}))
id='11111111-2222-4333-8444-555555555555'
p=root/'antigravity/brain'/id/'.system_generated/logs/transcript_full.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
records=[
 {'step_index':0,'source':'USER_EXPLICIT','type':'USER_INPUT','status':'DONE','content':'first literal --flags anchor','created_at':'2026-01-01T00:00:00Z'},
 {'step_index':1,'source':'MODEL','type':'PLANNER_RESPONSE','status':'DONE','content':'First response. '*50,'created_at':'2026-01-01T00:01:00Z'},
 {'step_index':2,'source':'MODEL','type':'GENERIC','status':'DONE','content':'tool status','tool_calls':[{'name':'command','arguments':{'command':'echo example'},'result':'example'}]},
 {'step_index':3,'source':'MODEL','type':'CHECKPOINT','status':'DONE','content':'Recorded checkpoint'},
 {'step_index':4,'source':'USER_EXPLICIT','type':'USER_INPUT','status':'DONE','content':'-Turn','created_at':'2026-01-01T02:00:00Z'},
 {'step_index':5,'source':'MODEL','type':'PLANNER_RESPONSE','status':'DONE','content':'Final response. '*50,'created_at':'2026-01-01T02:01:00Z'}]
p.write_text(''.join(json.dumps(r)+'\n' for r in records))
db=root/'antigravity/conversation_summaries.db'
if db.exists():db.unlink()
con=sqlite3.connect(db);con.execute('CREATE TABLE conversation_summaries(conversation_id TEXT,title TEXT,project_id TEXT,last_modified_time TEXT)');con.execute('INSERT INTO conversation_summaries VALUES(?,?,?,?)',(id,'Example thread','demo','2026-01-01T02:01:00Z'));con.commit();con.close()

'@ | Set-Content -LiteralPath $fixtureBuilder -Encoding utf8NoBOM
    & $PythonPath $fixtureBuilder $fixture
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic fixture creation failed.' }
    $transcript=Join-Path $fixture 'antigravity/brain/11111111-2222-4333-8444-555555555555/.system_generated/logs/transcript_full.jsonl'
    Import-Module (Join-Path $sourceRoot 'AntigravityExporter.psd1') -Force -WarningAction Stop
    Set-AntigravityPython $PythonPath
    $id='11111111-2222-4333-8444-555555555555'
    $out=Join-Path $temporary 'output'
    $common=@('--root',$fixture,'--no-progress','--no-verify-db')
    $export=@('--root',$fixture,'--out-dir',$out,'--archive-method','Deflate','--archive-backend','python','--archive-level','1','--tokenizer','none','--no-progress','--no-verify-db')
    $checks=[Collections.Generic.List[string]]::new()
    $sourceHash=(Get-FileHash -LiteralPath $transcript -Algorithm SHA256).Hash
    foreach ($helper in @('agy-exporter','antigravity-exporter','agy-help')) { & $helper --help | Out-Null; $checks.Add($helper) }
    # The real entry point must execute the diagnostic branch, not only --help.
    $doctor=agy-doctor | ConvertFrom-Json
    $moduleVersion=(Import-PowerShellDataFile (Join-Path $sourceRoot 'AntigravityExporter.psd1')).ModuleVersion
    if ($doctor.version -ne $moduleVersion -or -not $doctor.executable -or
        -not $doctor.codecs.lzma -or -not $doctor.codecs.zlib -or
        -not $doctor.no_network_requests_or_package_installation_performed) {
        throw 'Environment diagnostic JSON is incomplete.'
    }
    $checks.Add('agy-doctor')
    $token=agy-tokenizer -Tokenizer regex | ConvertFrom-Json
    if ($token.configured -ne 'regex' -or -not $token.no_network_or_installation_performed) {
        throw 'Tokenizer diagnostic JSON is incomplete.'
    }
    $checks.Add('agy-tokenizer')
    $catalog=agy-sessions @common -Json | ConvertFrom-Json
    if ($catalog.id -ne $id -or $catalog.title -ne 'Example thread') { throw 'Session JSON mismatch' }
    # Captured/redirected output must contain complete rows for every pager mode.
    # A real TTY deliberately uses the alternate-screen pager when appropriate.
    foreach ($pager in @('auto','always','never')) {
        $sessionsText=agy-sessions @common -Pager $pager | Out-String
        if ($sessionsText -notlike "*$id*" -or $sessionsText -notlike '*Example thread*' -or
            $sessionsText -notlike '*Full log*' -or $sessionsText -notlike '*JSONL size*' -or
            $sessionsText -notlike '*Last modified*' -or $sessionsText.Contains([string][char]27)) {
            throw "Human session output missing or contains terminal controls: pager=$pager"
        }
        $checks.Add('agy-sessions-'+$pager)
    }
    $projectsText=agy-projects @common -Pager never | Out-String
    if ($projectsText -notlike '*Example Project*' -or $projectsText -notlike '*1 sessions*') {
        throw 'Human project output missing.'
    }
    $checks.Add('agy-projects')
    foreach ($helper in @('agy-turns','agy-list','agy-responses','agy-audit')) {
        $listing=& $helper $id @common -Json | ConvertFrom-Json
        if ($listing.session.id -ne $id) { throw "Listing JSON identity missing: $helper" }
        $checks.Add($helper)
    }
    foreach ($helper in @('agy-turns','agy-list','agy-responses')) {
        $listingText=& $helper $id @common -Pager never | Out-String
        $expected=if ($helper -eq 'agy-turns') { 'Turn\s+1' } else { 'Message\s+\d+' }
        if ($listingText -notlike "*$id*" -or $listingText -notmatch $expected) {
            throw "Human listing content missing: $helper"
        }
        $checks.Add($helper+'-human')
    }
    foreach ($helper in @('agy-thread','agy-chat','agy-actions','agy-final','agy-live','agy-fast')) { $archive=& $helper $id @export; if (-not (Test-Path -LiteralPath $archive)) { throw "Archive missing: $helper" }; $checks.Add($helper) }
    foreach ($helper in @('agy-turn','agy-response','agy-response-block','agy-msg')) { $archive=& $helper $id 1 @export; if (-not (Test-Path -LiteralPath $archive)) { throw "Archive missing: $helper" }; $checks.Add($helper) }
    $archive=agy-range $id 0 5 @export; $checks.Add('agy-range')
    $archive=agy-last $id 1 @export; $checks.Add('agy-last')
    $archive=agy-latest @export; $checks.Add('agy-latest')
    $archive=agy-project 'Example Project' @export; $checks.Add('agy-project')
    $archive=$catalog | agy-batch @export; $checks.Add('agy-batch-pipeline')
    $archive=agy-since-text $id '-Turn' @export; $checks.Add('agy-since-text-leading-dash')
    foreach ($case in @(@('agy-since','turn:2'),@('agy-since-turn','2'),@('agy-since-id','step:4'),@('agy-since-time','2026-01-01T01:00:00Z'))) { $archive=& $case[0] $id $case[1] @export; $checks.Add($case[0]) }
    $rendered=agy-thread $id @export --no-file --stdout | Out-String
    if ($rendered -notlike '*First response*' -or $rendered -match '"no_file"') { throw 'Markdown stdout was polluted' }
    $checks.Add('stdout-no-file')
    $receipt=agy-thread $id @export -Json | ConvertFrom-Json
    if (-not $receipt.archive) { throw 'Export JSON missing archive' }
    $checks.Add('export-json')
    agy-verify $receipt.archive | ConvertFrom-Json | Out-Null; $checks.Add('agy-verify')
    agy-verify-set $receipt.archive | ConvertFrom-Json | Out-Null; $checks.Add('agy-verify-set')
    agy-access $receipt.archive | ConvertFrom-Json | Out-Null; $checks.Add('agy-access')
    agy-review $receipt.archive -OutDir $out -NoArchive | ConvertFrom-Json | Out-Null; $checks.Add('agy-review')

    if ((Get-FileHash -LiteralPath $transcript -Algorithm SHA256).Hash -ne $sourceHash) { throw 'Application-owned fixture source changed.' }
    Write-Host "PASS: $($checks.Count) actual helper/CLI checks, including private exports, stdout/JSON, selected ranges, batch pipelines and archive verification."
} finally {
    $env:AGY_EXPORT_CONFIG=$priorConfig
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force -Recurse }
}
