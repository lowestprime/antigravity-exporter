[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConversationId,[string]$OutDir)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'AntigravityExporter.psm1') -Force
$arguments = @('export','--id',$ConversationId,'--include-native','--include-artifacts','--review','--handoff','--verify-chunks')
if ($OutDir) { $arguments += @('--out-dir',$OutDir) }
Invoke-AntigravityExporter @arguments
