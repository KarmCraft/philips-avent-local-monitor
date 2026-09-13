[CmdletBinding()]
param(
    [string]$TaskName = 'AventLocalMonitor',
    [string]$StateRoot,
    [string]$SecretFile,
    [int]$Port
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TaskSupport.ps1')
if (-not $Port) {
    $Port = if ($env:AVENT_SITE_PORT) { [int]$env:AVENT_SITE_PORT } else { 8090 }
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw 'Port must be between 1 and 65535.'
}
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pwshCommand = Get-Command 'pwsh.exe' -ErrorAction SilentlyContinue
if (-not $pwshCommand) {
    throw 'PowerShell 7 is required. Install it and ensure pwsh.exe is on PATH.'
}
$pwsh = $pwshCommand.Source
$startScript = Join-Path $root 'Start-AventMonitor.ps1'
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path -LiteralPath $startScript)) {
    throw "The Avent start script is missing: $startScript"
}

$StateRoot = Resolve-AventStateRoot $StateRoot
$startArguments = @('-Port', [string]$Port, '-StateRoot', $StateRoot)
if ($SecretFile) {
    $startArguments += @('-SecretFile', $SecretFile)
}

$action = New-AventHiddenTaskAction -StateRoot $StateRoot -Name 'monitor' `
    -Script $startScript -Parameters $startArguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Starts the localhost Philips Avent monitor viewer at Windows sign-in.' `
    -Force | Out-Null

Write-Output "Installed $TaskName with sign-in autostart and 999 one-minute recovery attempts."
