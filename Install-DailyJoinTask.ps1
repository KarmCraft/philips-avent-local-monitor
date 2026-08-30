[CmdletBinding()]
param(
    [string]$TaskName = 'AventDailyVideoJoin',
    [string]$StateRoot,
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$At = '00:30'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$joinScript = Join-Path $root 'Join-AventDailyVideos.ps1'
$pwshCommand = Get-Command 'pwsh.exe' -ErrorAction SilentlyContinue
if (-not $pwshCommand) {
    throw 'PowerShell 7 is required. Install it and ensure pwsh.exe is on PATH.'
}
if (-not (Test-Path -LiteralPath $joinScript -PathType Leaf)) {
    throw "The daily join script is missing: $joinScript"
}
if (-not $StateRoot) {
    $StateRoot = if ($env:AVENT_STATE_ROOT) {
        $env:AVENT_STATE_ROOT
    } else {
        Join-Path $env:LOCALAPPDATA 'AventMonitor'
    }
}

$settingsPath = Join-Path $StateRoot 'data\capture-settings.json'
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$runAt = [datetime]::ParseExact($At, 'HH:mm', [System.Globalization.CultureInfo]::InvariantCulture)
$arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$joinScript`" -UseWebsiteSettings -SettingsPath `"$settingsPath`""

$action = New-ScheduledTaskAction `
    -Execute $pwshCommand.Source `
    -Argument $arguments `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $runAt
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 15)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Joins completed Philips Avent recording fragments into one validated daily video.' `
    -Force | Out-Null

Write-Output "Installed $TaskName to run daily at $At for $currentUser."
