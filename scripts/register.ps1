param([switch]$SkipCodex)
$ErrorActionPreference = 'Stop'
Get-Command pyrevit -ErrorAction Stop | Out-Null
if (-not $SkipCodex) { Get-Command codex -ErrorAction Stop | Out-Null }
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$launcherPath = Join-Path $projectRoot 'scripts\run_server.py'
$extensionPath = Join-Path $projectRoot 'extensions'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create .venv and install requirements first.' }
$backupDir = Join-Path $projectRoot '.runtime\backups'
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$pyrevitConfig = Join-Path $env:APPDATA 'pyRevit\pyRevit_config.ini'
if (Test-Path -LiteralPath $pyrevitConfig) {
    Copy-Item -LiteralPath $pyrevitConfig -Destination (Join-Path $backupDir "pyRevit_config-$stamp.ini")
}
& pyrevit extensions paths add $extensionPath
if ($LASTEXITCODE -ne 0) { throw 'pyRevit extension registration failed.' }
if (-not $SkipCodex) {
    $codexConfigRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    $codexConfigFile = Join-Path $codexConfigRoot 'config.toml'
    if (Test-Path -LiteralPath $codexConfigFile) {
        Copy-Item -LiteralPath $codexConfigFile -Destination (Join-Path $backupDir "codex-config-$stamp.toml")
    }
    & codex mcp add revit2021 -- $pythonPath $launcherPath
    if ($LASTEXITCODE -ne 0) { throw 'Codex MCP registration failed.' }
}
Write-Output 'Registered. Reload pyRevit in Revit 2021 and reconnect the Codex MCP client.'
