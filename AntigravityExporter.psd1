@{
    RootModule = 'AntigravityExporter.psm1'
    ModuleVersion = '0.4.0'
    GUID = '475602fb-37d5-4378-9f99-38546237d7e4'
    Author = 'lowestprime'
    CompanyName = 'Community'
    Copyright = '(c) 2026 Cooper Beaman. MIT License.'
    Description = 'Read-only Antigravity conversation export, source audits and verified archives.'
    PowerShellVersion = '7.0'
    CompatiblePSEditions = @('Core')
    FunctionsToExport = @(
        'ConvertTo-AntigravityReview',
        'Export-AntigravityAction',
        'Export-AntigravityBatch',
        'Export-AntigravityChat',
        'Export-AntigravityCheckpoint',
        'Export-AntigravityClipboard',
        'Export-AntigravityFast',
        'Export-AntigravityFinal',
        'Export-AntigravityLatest',
        'Export-AntigravityMessage',
        'Export-AntigravityOpen',
        'Export-AntigravityProject',
        'Export-AntigravityRange',
        'Export-AntigravityRecent',
        'Export-AntigravityResponse',
        'Export-AntigravityResponseBlock',
        'Export-AntigravitySince',
        'Export-AntigravitySinceId',
        'Export-AntigravitySinceText',
        'Export-AntigravitySinceTime',
        'Export-AntigravitySinceTurn',
        'Export-AntigravityThread',
        'Export-AntigravityTurn',
        'Get-AntigravityAudit',
        'Get-AntigravityConfiguration',
        'Get-AntigravityProject',
        'Get-AntigravityPython',
        'Get-AntigravityRecord',
        'Get-AntigravityResponse',
        'Get-AntigravitySession',
        'Get-AntigravityTokenizer',
        'Get-AntigravityTurn',
        'Invoke-AntigravityExporter',
        'Repair-AntigravityAccess',
        'Select-AntigravitySession',
        'Set-AntigravityFilenameTemplate',
        'Set-AntigravityOutputDirectory',
        'Set-AntigravityPython',
        'Show-AntigravityHelp',
        'Test-AntigravityAccess',
        'Test-AntigravityArchive',
        'Test-AntigravityArchiveSet',
        'Test-AntigravityEnvironment'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @(
        'agy-access',
        'agy-actions',
        'agy-archive-config',
        'agy-audit',
        'agy-batch',
        'agy-browse',
        'agy-chat',
        'agy-config',
        'agy-doctor',
        'agy-export',
        'agy-exporter',
        'agy-fast',
        'agy-final',
        'agy-help',
        'agy-last',
        'agy-latest',
        'agy-list',
        'agy-live',
        'agy-msg',
        'agy-open',
        'agy-project',
        'agy-projects',
        'agy-range',
        'agy-repair-access',
        'agy-response',
        'agy-response-block',
        'agy-responses',
        'agy-review',
        'agy-sessions',
        'agy-set-dir',
        'agy-set-template',
        'agy-since',
        'agy-since-id',
        'agy-since-text',
        'agy-since-time',
        'agy-since-turn',
        'agy-thread',
        'agy-thread-clip',
        'agy-tokenizer',
        'agy-turn',
        'agy-turns',
        'agy-verify',
        'agy-verify-set',
        'antigravity-exporter'
    )
    PrivateData = @{
        PSData = @{
            Tags = @('Antigravity','Markdown','Archive','Export')
            LicenseUri = 'https://github.com/lowestprime/antigravity-exporter/blob/main/LICENSE'
            ProjectUri = 'https://github.com/lowestprime/antigravity-exporter'
        }
    }
}
