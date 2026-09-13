# Shared helpers for the two independent Windows scheduled tasks.
function Resolve-AventStateRoot {
    param([string]$StateRoot)
    if (-not $StateRoot) {
        $StateRoot = if ($env:AVENT_STATE_ROOT) { $env:AVENT_STATE_ROOT } else {
            Join-Path $env:LOCALAPPDATA 'AventMonitor'
        }
    }
    return [System.IO.Path]::GetFullPath($StateRoot)
}

function ConvertTo-AventArgument {
    param([AllowEmptyString()][string]$Value)
    if ($Value -match '["\r\n\x00]') { throw 'Task arguments cannot contain quotes or line breaks.' }
    # Double trailing backslashes before the closing Windows command-line quote.
    return '"' + ($Value -replace '(\\+)$', '$1$1') + '"'
}

function New-AventHiddenTaskAction {
    param([string]$StateRoot, [string]$Name, [string]$Script, [string[]]$Parameters)
    $pwsh = (Get-Command 'pwsh.exe' -ErrorAction Stop).Source
    $taskDirectory = Join-Path $StateRoot 'tasks'
    New-Item -ItemType Directory -Path $taskDirectory -Force | Out-Null
    $launcher = Join-Path $taskDirectory "$Name.vbs"
    $arguments = @($pwsh, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $Script) + $Parameters
    $command = ($arguments | ForEach-Object { ConvertTo-AventArgument $_ }) -join ' '
    # wscript has no console to attach to Windows Terminal; it propagates the exit code.
    $escapedCommand = $command.Replace('"', '""')
    $source = @(
        'Option Explicit'
        'Dim shell'
        'Set shell = CreateObject("WScript.Shell")'
        ('WScript.Quit shell.Run("' + $escapedCommand + '", 0, True)')
    ) -join "`r`n"
    Set-Content -LiteralPath $launcher -Value $source -Encoding unicode
    New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\wscript.exe') `
        -Argument ('//B //Nologo ' + (ConvertTo-AventArgument $launcher)) `
        -WorkingDirectory (Split-Path -Parent $Script)
}
