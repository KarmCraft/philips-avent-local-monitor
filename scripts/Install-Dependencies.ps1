[CmdletBinding()]
param(
    [string]$BinDirectory,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $BinDirectory) {
    $BinDirectory = Join-Path $projectRoot 'bin'
}

$bridgeRepository = 'https://github.com/thekoma/aventproxy.git'
$bridgeCommit = '00eb72a8d5f4d2223b9262762f0a6337ae32d337'
$bridgeVersion = '2026.8.0-windows1'
$bridgeTarget = Join-Path $BinDirectory 'avent-webrtc-bridge.exe'
$bridgePatch = Join-Path $projectRoot 'patches\aventproxy-windows-loopback.patch'

$mediaMtxVersion = 'v1.20.0'
$mediaMtxArchiveName = 'mediamtx_v1.20.0_windows_amd64.zip'
$mediaMtxUrl = "https://github.com/bluenviron/mediamtx/releases/download/$mediaMtxVersion/$mediaMtxArchiveName"
$mediaMtxSha256 = '7364E7672E6B4420E986EC4B56E2CC32EC7B4085F69B56EC224D596D0FA8B19F'
$mediaMtxTarget = Join-Path $BinDirectory 'mediamtx.exe'

function Invoke-Checked {
    param(
        [Parameter(Mandatory)]
        [string]$Executable,
        [Parameter(Mandatory)]
        [string[]]$ArgumentList
    )

    & $Executable @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable exited with code $LASTEXITCODE."
    }
}

foreach ($command in 'git.exe', 'go.exe') {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command is required and must be available on PATH."
    }
}
if (-not (Test-Path -LiteralPath $bridgePatch -PathType Leaf)) {
    throw "Bridge patch is missing: $bridgePatch"
}

New-Item -ItemType Directory -Path $BinDirectory -Force | Out-Null
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) "avent-monitor-dependencies-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null

try {
    if ($Force -or -not (Test-Path -LiteralPath $bridgeTarget -PathType Leaf)) {
        $bridgeSource = Join-Path $temporaryRoot 'aventproxy'
        Invoke-Checked -Executable git.exe -ArgumentList @('clone', '--filter=blob:none', '--no-checkout', $bridgeRepository, $bridgeSource)
        Invoke-Checked -Executable git.exe -ArgumentList @('-C', $bridgeSource, 'checkout', '--detach', $bridgeCommit)
        Invoke-Checked -Executable git.exe -ArgumentList @('-C', $bridgeSource, 'apply', '--check', $bridgePatch)
        Invoke-Checked -Executable git.exe -ArgumentList @('-C', $bridgeSource, 'apply', $bridgePatch)

        $bridgePackage = Join-Path $bridgeSource 'avent-webrtc-bridge'
        $bridgeOutput = Join-Path $temporaryRoot 'avent-webrtc-bridge.exe'
        Push-Location $bridgePackage
        try {
            $env:CGO_ENABLED = '0'
            Invoke-Checked -Executable go.exe -ArgumentList @('test', './...')
            Invoke-Checked -Executable go.exe -ArgumentList @('build', '-trimpath', '-buildvcs=false', '-ldflags', "-s -w -X main.VERSION=$bridgeVersion", '-o', $bridgeOutput, '.')
        } finally {
            Pop-Location
        }
        Copy-Item -LiteralPath $bridgeOutput -Destination $bridgeTarget -Force
        Write-Output "Built $bridgeTarget from aventproxy commit $bridgeCommit."
    } else {
        Write-Output "Keeping existing bridge: $bridgeTarget"
    }

    if ($Force -or -not (Test-Path -LiteralPath $mediaMtxTarget -PathType Leaf)) {
        $archivePath = Join-Path $temporaryRoot $mediaMtxArchiveName
        $extractPath = Join-Path $temporaryRoot 'mediamtx'
        Invoke-WebRequest -Uri $mediaMtxUrl -OutFile $archivePath
        $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash
        if ($actualHash -ne $mediaMtxSha256) {
            throw "MediaMTX checksum mismatch. Expected $mediaMtxSha256, received $actualHash."
        }
        Expand-Archive -LiteralPath $archivePath -DestinationPath $extractPath
        Copy-Item -LiteralPath (Join-Path $extractPath 'mediamtx.exe') -Destination $mediaMtxTarget -Force
        Write-Output "Installed verified MediaMTX $mediaMtxVersion to $mediaMtxTarget."
    } else {
        Write-Output "Keeping existing MediaMTX: $mediaMtxTarget"
    }
} finally {
    $resolvedTemporaryRoot = (Resolve-Path -LiteralPath $temporaryRoot).Path
    $systemTemporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (-not $resolvedTemporaryRoot.StartsWith($systemTemporaryRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the system temporary directory: $resolvedTemporaryRoot"
    }
    Remove-Item -LiteralPath $resolvedTemporaryRoot -Recurse -Force
}

Get-FileHash -Algorithm SHA256 -LiteralPath $bridgeTarget, $mediaMtxTarget |
    Select-Object Path, Hash
