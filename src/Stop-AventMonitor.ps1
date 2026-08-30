[CmdletBinding()]
param(
    [string]$StateRoot,
    [int]$Port
)

$ErrorActionPreference = 'Stop'
if (-not $Port) {
    $Port = if ($env:AVENT_SITE_PORT) { [int]$env:AVENT_SITE_PORT } else { 8090 }
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw 'Port must be between 1 and 65535.'
}
if (-not $StateRoot) {
    $StateRoot = if ($env:AVENT_STATE_ROOT) {
        $env:AVENT_STATE_ROOT
    } else {
        Join-Path $env:LOCALAPPDATA 'AventMonitor'
    }
}
$pidFile = Join-Path $StateRoot 'data\avent-monitor.pid'

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output 'Avent Monitor website is not running.'
    exit 0
}

$processId = [int](Get-Content -LiteralPath $pidFile -Raw)
$process = Get-Process -Id $processId -ErrorAction SilentlyContinue
if ($process) {
    try {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/api/shutdown" -ContentType 'application/json' -Body '{}' -TimeoutSec 10 | Out-Null
        $process.WaitForExit(10000) | Out-Null
    } catch {
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Output "Stopped Avent Monitor website and stream processes."
} else {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Output 'The recorded Avent Monitor process is no longer running.'
}
