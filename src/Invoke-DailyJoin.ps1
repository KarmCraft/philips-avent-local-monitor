[CmdletBinding()]
param([string]$StateRoot)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TaskSupport.ps1')
$StateRoot = Resolve-AventStateRoot $StateRoot
$logRoot = Join-Path $StateRoot 'logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$log = Join-Path $logRoot 'daily-join.log'
if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -gt 5MB) {
    Move-Item -LiteralPath $log -Destination "$log.1" -Force
}
Add-Content -LiteralPath $log -Value "[$((Get-Date).ToString('o'))] Starting completed-day catch-up."
$pwsh = (Get-Command 'pwsh.exe' -ErrorAction Stop).Source
& $pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass `
    -File (Join-Path $PSScriptRoot 'Join-AventDailyVideos.ps1') `
    -UseWebsiteSettings -AllPastDays `
    -SettingsPath (Join-Path $StateRoot 'data\capture-settings.json') *>> $log
$code = $LASTEXITCODE
Add-Content -LiteralPath $log -Value "[$((Get-Date).ToString('o'))] Catch-up exited with code $code."
exit $code
