[CmdletBinding()]
param(
    [string]$StateRoot,
    [string]$SecretFile,
    [int]$Port
)

$ErrorActionPreference = 'Stop'
if (-not $Port) {
    $Port = if ($env:AVENT_SITE_PORT) { [int]$env:AVENT_SITE_PORT } else { 8090 }
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw 'Port must be between 1 and 65535.'
}
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$app = Join-Path $root 'app.py'
$defaultStateRoot = Join-Path $env:LOCALAPPDATA 'AventMonitor'
if (-not $StateRoot) {
    $StateRoot = if ($env:AVENT_STATE_ROOT) { $env:AVENT_STATE_ROOT } else { $defaultStateRoot }
}
$env:AVENT_STATE_ROOT = $StateRoot
if ($SecretFile) {
    $env:AVENT_SECRET_FILE = $SecretFile
} elseif (-not $env:AVENT_SECRET_FILE) {
    $env:AVENT_SECRET_FILE = Join-Path $StateRoot 'session.json'
}
$env:AVENT_SITE_PORT = [string]$Port
$logRoot = Join-Path $StateRoot 'logs'
$launcherLog = Join-Path $logRoot 'launcher.log'

if (-not (Test-Path -LiteralPath $python)) {
    throw "The local Python environment is missing: $python"
}

while ($true) {
    & $python $app
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) {
        exit 0
    }

    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    if ((Test-Path -LiteralPath $launcherLog) -and (Get-Item -LiteralPath $launcherLog).Length -gt 1MB) {
        Move-Item -LiteralPath $launcherLog -Destination "$launcherLog.1" -Force
    }
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
    Add-Content -LiteralPath $launcherLog -Value "$timestamp Website exited with code $exitCode; restarting in 10 seconds."
    Start-Sleep -Seconds 10
}
