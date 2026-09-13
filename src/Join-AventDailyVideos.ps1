[CmdletBinding()]
param(
    [string]$Date,
    [switch]$AllPastDays,
    [switch]$Force,
    [switch]$UseWebsiteSettings,
    [switch]$DeleteFragments,
    [string]$SettingsPath,
    [string]$VideoRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-JoinLog {
    param(
        [string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR")]
        [string]$Level = "INFO"
    )

    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
    Write-Output "[$timestamp] [$Level] $Message"
}

function Resolve-MediaTool {
    param(
        [Parameter(Mandatory)]
        [string]$Name
    )

    $environmentName = "AVENT_$($Name.ToUpperInvariant())"
    $configured = [Environment]::GetEnvironmentVariable($environmentName)
    if ($configured) {
        if (-not (Test-Path -LiteralPath $configured -PathType Leaf)) {
            throw "$environmentName does not point to a file: $configured"
        }
        return $configured
    }

    $command = Get-Command "$Name.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    throw "Could not find $Name.exe. Set $environmentName or add the tool to PATH."
}

function Invoke-NativeProcess {
    param(
        [Parameter(Mandatory)]
        [string]$Executable,
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [int]$TimeoutSeconds = 120
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) {
        [void]$startInfo.ArgumentList.Add($argument)
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw "Failed to start $Executable."
        }

        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill($true)
            $process.WaitForExit()
            throw "$([System.IO.Path]::GetFileName($Executable)) timed out after $TimeoutSeconds second(s)."
        }

        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout = $stdoutTask.GetAwaiter().GetResult()
            Stderr = $stderrTask.GetAwaiter().GetResult()
        }
    } finally {
        $process.Dispose()
    }
}

function Get-PacketDerivedDuration {
    param(
        [Parameter(Mandatory)]
        [string]$Path,
        [Parameter(Mandatory)]
        [string]$Ffprobe
    )

    $result = Invoke-NativeProcess -Executable $Ffprobe -Arguments @(
        "-v", "error",
        "-show_entries", "packet=pts_time,dts_time,duration_time",
        "-of", "json",
        $Path
    ) -TimeoutSeconds 300
    if ($result.ExitCode -ne 0) {
        throw "Could not derive packet duration for '$([System.IO.Path]::GetFileName($Path))'."
    }

    try {
        $packetProbe = $result.Stdout | ConvertFrom-Json
    } catch {
        throw "ffprobe returned invalid packet JSON for '$([System.IO.Path]::GetFileName($Path))'."
    }

    $maximum = 0.0
    foreach ($packet in @($packetProbe.packets)) {
        $timestampProperty = $packet.PSObject.Properties["pts_time"]
        if (-not $timestampProperty) {
            $timestampProperty = $packet.PSObject.Properties["dts_time"]
        }
        if (-not $timestampProperty) {
            continue
        }
        try {
            $timestamp = [double]::Parse(
                [string]$timestampProperty.Value,
                [System.Globalization.CultureInfo]::InvariantCulture
            )
            $packetDurationProperty = $packet.PSObject.Properties["duration_time"]
            $packetDuration = if ($packetDurationProperty) {
                [double]::Parse(
                    [string]$packetDurationProperty.Value,
                    [System.Globalization.CultureInfo]::InvariantCulture
                )
            } else {
                0.0
            }
            $maximum = [Math]::Max($maximum, $timestamp + $packetDuration)
        } catch [System.FormatException] {
            continue
        }
    }
    return $maximum
}

function Get-MediaInfo {
    param(
        [Parameter(Mandatory)]
        [string]$Path,
        [Parameter(Mandatory)]
        [string]$Ffprobe
    )

    $result = Invoke-NativeProcess -Executable $Ffprobe -Arguments @(
        "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,pix_fmt,sample_rate,channels,channel_layout",
        "-of", "json",
        $Path
    ) -TimeoutSeconds 120

    if ($result.ExitCode -ne 0) {
        $detail = $result.Stderr.Trim()
        if (-not $detail) {
            $detail = "ffprobe exited with code $($result.ExitCode)."
        }
        throw "Media validation failed for '$([System.IO.Path]::GetFileName($Path))': $detail"
    }

    try {
        $probe = $result.Stdout | ConvertFrom-Json
    } catch {
        throw "ffprobe returned invalid JSON for '$([System.IO.Path]::GetFileName($Path))'."
    }

    $streams = @($probe.streams)
    $video = @($streams | Where-Object { $_.codec_type -eq "video" })
    $audio = @($streams | Where-Object { $_.codec_type -eq "audio" })
    $durationProperty = $probe.format.PSObject.Properties["duration"]
    if ($durationProperty) {
        $duration = [double]::Parse(
            [string]$durationProperty.Value,
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    } else {
        $duration = Get-PacketDerivedDuration -Path $Path -Ffprobe $Ffprobe
        if ($duration -gt 0) {
            $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
            [Console]::Error.WriteLine(
                "[$timestamp] [WARN] Recovered missing container duration for '$([System.IO.Path]::GetFileName($Path))' from packet timestamps."
            )
        }
    }

    if ($video.Count -ne 1 -or $audio.Count -ne 1 -or $duration -le 0) {
        throw "'$([System.IO.Path]::GetFileName($Path))' must contain exactly one readable video stream, one readable audio stream, and a positive duration."
    }

    $channelLayoutProperty = $audio[0].PSObject.Properties["channel_layout"]
    $channelLayout = if ($channelLayoutProperty) {
        [string]$channelLayoutProperty.Value
    } else {
        ""
    }

    $signature = [ordered]@{
        videoCodec = [string]$video[0].codec_name
        pixelFormat = [string]$video[0].pix_fmt
        audioCodec = [string]$audio[0].codec_name
        sampleRate = [string]$audio[0].sample_rate
        channels = [int]$audio[0].channels
        channelLayout = $channelLayout
    }

    return [pscustomobject]@{
        Duration = $duration
        Width = [int]$video[0].width
        Height = [int]$video[0].height
        Signature = ($signature | ConvertTo-Json -Compress)
    }
}

function Get-SourceFingerprint {
    param(
        [Parameter(Mandatory)]
        [object[]]$Sources
    )

    $json = $Sources | ConvertTo-Json -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $hash = [System.Security.Cryptography.SHA256]::HashData($bytes)
    return [Convert]::ToHexString($hash).ToLowerInvariant()
}

function Get-SegmentsForDate {
    param(
        [Parameter(Mandatory)]
        [string]$DateKey,
        [Parameter(Mandatory)]
        [string]$Root
    )

    $namePattern = "^BabyMonitor_$([regex]::Escape($DateKey))_\d{2}-\d{2}-\d{2}\.mkv$"
    return @(
        Get-ChildItem -LiteralPath $Root -File -Filter "BabyMonitor_${DateKey}_*.mkv" |
            Where-Object { $_.Name -match $namePattern } |
            Sort-Object Name
    )
}

function Set-ObjectProperty {
    param(
        [Parameter(Mandatory)]
        [object]$Object,
        [Parameter(Mandatory)]
        [string]$Name,
        $Value
    )

    if ($Object -is [System.Collections.IDictionary]) {
        $Object[$Name] = $Value
        return
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($property) {
        $property.Value = $Value
    } else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Save-ManifestSafely {
    param(
        [Parameter(Mandatory)]
        [object]$Manifest,
        [Parameter(Mandatory)]
        [string]$Path
    )

    $temporaryPath = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        $Manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporaryPath -Encoding utf8NoBOM
        Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
    } finally {
        Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
    }
}

function Test-SegmentsMatchManifestSubset {
    param(
        [Parameter(Mandatory)]
        [object[]]$Segments,
        [Parameter(Mandatory)]
        [object]$Manifest
    )

    $records = @{}
    foreach ($source in @($Manifest.sources)) {
        $records[[string]$source.name] = $source
    }
    foreach ($segment in $Segments) {
        $record = $records[$segment.Name]
        if ($null -eq $record) {
            return $false
        }
        if ([long]$record.length -ne [long]$segment.Length) {
            return $false
        }
        try {
            $recordTime = [datetime]$record.lastWriteTimeUtc
        } catch {
            return $false
        }
        if ($recordTime.ToUniversalTime().Ticks -ne $segment.LastWriteTimeUtc.Ticks) {
            return $false
        }
    }
    return $true
}

function Remove-ValidatedFragments {
    param(
        [Parameter(Mandatory)]
        [object[]]$Segments,
        [Parameter(Mandatory)]
        [object]$Manifest,
        [Parameter(Mandatory)]
        [string]$ManifestPath,
        [Parameter(Mandatory)]
        [string]$FinalPath,
        [Parameter(Mandatory)]
        [string]$Ffprobe
    )

    $null = Get-MediaInfo -Path $FinalPath -Ffprobe $Ffprobe
    if (-not (Test-SegmentsMatchManifestSubset -Segments $Segments -Manifest $Manifest)) {
        throw "Current fragments do not match the validated manifest; refusing deletion."
    }

    Set-ObjectProperty -Object $Manifest -Name "sourceFragmentsDeletionRequested" -Value $true
    Set-ObjectProperty -Object $Manifest -Name "sourceFragmentsDeleted" -Value $false
    Save-ManifestSafely -Manifest $Manifest -Path $ManifestPath

    $deleted = 0
    foreach ($segment in $Segments) {
        if (Test-Path -LiteralPath $segment.FullName -PathType Leaf) {
            Remove-Item -LiteralPath $segment.FullName -Force
            $deleted += 1
        }
    }

    Set-ObjectProperty -Object $Manifest -Name "sourceFragmentsDeleted" -Value $true
    Set-ObjectProperty -Object $Manifest -Name "sourceFragmentsDeletedAt" -Value (Get-Date).ToString("o")
    Set-ObjectProperty -Object $Manifest -Name "deletedSourceCount" -Value ([int]$Manifest.sourceCount)
    Save-ManifestSafely -Manifest $Manifest -Path $ManifestPath
    Write-JoinLog "Deleted $deleted remaining validated fragment(s) after the daily output passed verification."
}

function Publish-FileSafely {
    param(
        [Parameter(Mandatory)]
        [string]$TemporaryPath,
        [Parameter(Mandatory)]
        [string]$FinalPath
    )

    if (-not (Test-Path -LiteralPath $FinalPath -PathType Leaf)) {
        Move-Item -LiteralPath $TemporaryPath -Destination $FinalPath
        return
    }

    $backupPath = "$FinalPath.previous"
    if (Test-Path -LiteralPath $backupPath) {
        Remove-Item -LiteralPath $backupPath -Force
    }

    try {
        [System.IO.File]::Replace($TemporaryPath, $FinalPath, $backupPath, $true)
        Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
        return
    } catch {
        Write-JoinLog "Atomic file replacement was unavailable; using a guarded same-directory rename." "WARN"
    }

    Move-Item -LiteralPath $FinalPath -Destination $backupPath
    try {
        Move-Item -LiteralPath $TemporaryPath -Destination $FinalPath
        Remove-Item -LiteralPath $backupPath -Force
    } catch {
        if (-not (Test-Path -LiteralPath $FinalPath) -and (Test-Path -LiteralPath $backupPath)) {
            Move-Item -LiteralPath $backupPath -Destination $FinalPath
        }
        throw
    }
}

function Join-OneDay {
    param(
        [Parameter(Mandatory)]
        [string]$DateKey,
        [Parameter(Mandatory)]
        [string]$Root,
        [Parameter(Mandatory)]
        [string]$OutputRoot,
        [Parameter(Mandatory)]
        [string]$Ffmpeg,
        [Parameter(Mandatory)]
        [string]$Ffprobe,
        [switch]$Rebuild,
        [switch]$DeleteSources
    )

    $finalPath = Join-Path $OutputRoot "BabyMonitor_$DateKey.mkv"
    $manifestPath = Join-Path $OutputRoot "BabyMonitor_$DateKey.json"
    $segments = @(Get-SegmentsForDate -DateKey $DateKey -Root $Root)
    if ($segments.Count -eq 0) {
        if ($DeleteSources -and (Test-Path -LiteralPath $finalPath -PathType Leaf) -and (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
            $null = Get-MediaInfo -Path $finalPath -Ffprobe $Ffprobe
            Set-ObjectProperty -Object $manifest -Name "sourceFragmentsDeletionRequested" -Value $true
            Set-ObjectProperty -Object $manifest -Name "sourceFragmentsDeleted" -Value $true
            Set-ObjectProperty -Object $manifest -Name "sourceFragmentsDeletedAt" -Value (Get-Date).ToString("o")
            Set-ObjectProperty -Object $manifest -Name "deletedSourceCount" -Value ([int]$manifest.sourceCount)
            Save-ManifestSafely -Manifest $manifest -Path $manifestPath
            Write-JoinLog "$DateKey has a verified daily output and no remaining fragments."
            return [pscustomobject]@{ Date = $DateKey; Status = "FragmentsAlreadyDeleted"; Output = $finalPath }
        }
        Write-JoinLog "No recording segments found for $DateKey; no output created."
        return [pscustomobject]@{ Date = $DateKey; Status = "NoSegments"; Output = $null }
    }

    $sourceRecords = @(
        foreach ($segment in $segments) {
            [ordered]@{
                name = $segment.Name
                length = [long]$segment.Length
                lastWriteTimeUtc = $segment.LastWriteTimeUtc.ToString("o")
            }
        }
    )
    $fingerprint = Get-SourceFingerprint -Sources $sourceRecords
    if (-not $Rebuild -and (Test-Path -LiteralPath $finalPath -PathType Leaf) -and (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        try {
            $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
            $deletionRequestedProperty = $manifest.PSObject.Properties["sourceFragmentsDeletionRequested"]
            $deletionRequested = $deletionRequestedProperty -and [bool]$deletionRequestedProperty.Value
            if ($deletionRequested) {
                if (-not (Test-SegmentsMatchManifestSubset -Segments $segments -Manifest $manifest)) {
                    throw "Fragments changed after verified deletion began; refusing to rebuild an incomplete daily video."
                }
                $null = Get-MediaInfo -Path $finalPath -Ffprobe $Ffprobe
                if ($DeleteSources) {
                    Remove-ValidatedFragments -Segments $segments -Manifest $manifest -ManifestPath $manifestPath -FinalPath $finalPath -Ffprobe $Ffprobe
                    return [pscustomobject]@{ Date = $DateKey; Status = "FragmentsDeleted"; Output = $finalPath }
                }
                Write-JoinLog "$DateKey already has a verified daily output; some source fragments were previously deleted."
                return [pscustomobject]@{ Date = $DateKey; Status = "DeletionPending"; Output = $finalPath }
            }
            if ($manifest.sourceFingerprint -eq $fingerprint) {
                $null = Get-MediaInfo -Path $finalPath -Ffprobe $Ffprobe
                if ($DeleteSources) {
                    Remove-ValidatedFragments -Segments $segments -Manifest $manifest -ManifestPath $manifestPath -FinalPath $finalPath -Ffprobe $Ffprobe
                    return [pscustomobject]@{ Date = $DateKey; Status = "FragmentsDeleted"; Output = $finalPath }
                }
                Write-JoinLog "$DateKey is already complete and unchanged ($($segments.Count) segments)."
                return [pscustomobject]@{ Date = $DateKey; Status = "Unchanged"; Output = $finalPath }
            }
            if (
                $segments.Count -lt [int]$manifest.sourceCount -and
                (Test-SegmentsMatchManifestSubset -Segments $segments -Manifest $manifest)
            ) {
                throw "Some fragments from the existing daily manifest are missing; refusing to rebuild an incomplete daily video."
            }
        } catch {
            if ($_.Exception.Message -match "refusing to rebuild") {
                throw
            }
            Write-JoinLog "Existing output for $DateKey failed its manifest or media check and will be rebuilt: $($_.Exception.Message)" "WARN"
        }
    }

    Write-JoinLog "Validating $($segments.Count) source segment(s) for $DateKey."
    $expectedDuration = 0.0
    $expectedSignature = $null
    $dimensions = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($segment in $segments) {
        $media = Get-MediaInfo -Path $segment.FullName -Ffprobe $Ffprobe
        [void]$dimensions.Add("$($media.Width)x$($media.Height)")
        if ($null -eq $expectedSignature) {
            $expectedSignature = $media.Signature
        } elseif ($media.Signature -ne $expectedSignature) {
            throw "Source stream layout changed at '$($segment.Name)'; refusing to create a misleading partial daily video."
        }
        $expectedDuration += $media.Duration
    }

    if ($dimensions.Count -gt 1) {
        Write-JoinLog "Source resolution changes within $DateKey ($(@($dimensions) -join ', ')); compatible H.264 parameter changes will be preserved." "WARN"
    }

    $token = [Guid]::NewGuid().ToString("N")
    $concatPath = Join-Path $OutputRoot ".BabyMonitor_$DateKey.$token.ffconcat"
    $partialPath = Join-Path $OutputRoot ".BabyMonitor_$DateKey.$token.partial.mkv"
    $manifestTemporaryPath = Join-Path $OutputRoot ".BabyMonitor_$DateKey.$token.json"

    try {
        $concatLines = [System.Collections.Generic.List[string]]::new()
        $concatLines.Add("ffconcat version 1.0")
        foreach ($segment in $segments) {
            $concatFilePath = $segment.FullName.Replace("\", "/")
            if ($concatFilePath.Contains("'") -or $concatFilePath.Contains("`r") -or $concatFilePath.Contains("`n")) {
                throw "A source path contains characters that cannot be represented safely in the FFmpeg concat list."
            }
            $concatLines.Add("file '$concatFilePath'")
        }
        Set-Content -LiteralPath $concatPath -Value $concatLines -Encoding utf8NoBOM

        $retentionMessage = if ($DeleteSources) {
            "validated fragments will be deleted only after output verification"
        } else {
            "original fragments will be retained"
        }
        Write-JoinLog "Joining $DateKey with FFmpeg stream copy; $retentionMessage."
        $joinResult = Invoke-NativeProcess -Executable $Ffmpeg -Arguments @(
            "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-fflags", "+genpts",
            "-f", "concat", "-safe", "0", "-i", $concatPath,
            "-map", "0:v:0", "-map", "0:a:0",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-y", $partialPath
        ) -TimeoutSeconds 14400

        if ($joinResult.ExitCode -ne 0) {
            $detail = $joinResult.Stderr.Trim()
            if (-not $detail) {
                $detail = "FFmpeg exited with code $($joinResult.ExitCode)."
            }
            throw "FFmpeg failed while joining ${DateKey}: $detail"
        }

        $outputMedia = Get-MediaInfo -Path $partialPath -Ffprobe $Ffprobe
        if ($outputMedia.Signature -ne $expectedSignature) {
            throw "The joined output stream layout does not match the validated source segments."
        }

        $durationDifference = [Math]::Abs($outputMedia.Duration - $expectedDuration)
        $durationTolerance = [Math]::Max(2.5, $expectedDuration * 0.01)
        if ($durationDifference -gt $durationTolerance) {
            throw "The joined duration differs from the source total by $([Math]::Round($durationDifference, 3)) seconds."
        }

        Publish-FileSafely -TemporaryPath $partialPath -FinalPath $finalPath

        $manifest = [ordered]@{
            schemaVersion = 2
            date = $DateKey
            createdAt = (Get-Date).ToString("o")
            outputFile = [System.IO.Path]::GetFileName($finalPath)
            outputBytes = [long](Get-Item -LiteralPath $finalPath).Length
            outputDurationSeconds = [Math]::Round($outputMedia.Duration, 3)
            sourceCount = $segments.Count
            sourceDurationSeconds = [Math]::Round($expectedDuration, 3)
            sourceFingerprint = $fingerprint
            sources = $sourceRecords
            sourceFragmentsDeletionRequested = [bool]$DeleteSources
            sourceFragmentsDeleted = $false
        }
        $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestTemporaryPath -Encoding utf8NoBOM
        Move-Item -LiteralPath $manifestTemporaryPath -Destination $manifestPath -Force

        if ($DeleteSources) {
            Remove-ValidatedFragments -Segments $segments -Manifest $manifest -ManifestPath $manifestPath -FinalPath $finalPath -Ffprobe $Ffprobe
        }

        Write-JoinLog "Completed ${DateKey}: $($segments.Count) segments, $([Math]::Round($outputMedia.Duration, 1)) seconds, $((Get-Item -LiteralPath $finalPath).Length) bytes."
        $status = if ($DeleteSources) { "CreatedAndFragmentsDeleted" } else { "Created" }
        return [pscustomobject]@{ Date = $DateKey; Status = $status; Output = $finalPath }
    } finally {
        foreach ($temporaryPath in @($concatPath, $partialPath, $manifestTemporaryPath)) {
            if (Test-Path -LiteralPath $temporaryPath) {
                Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

$deleteSourceFragments = $DeleteFragments.IsPresent
if ($UseWebsiteSettings) {
    if (-not $SettingsPath) {
        $stateRoot = if ($env:AVENT_STATE_ROOT) {
            $env:AVENT_STATE_ROOT
        } else {
            Join-Path $env:LOCALAPPDATA "AventMonitor"
        }
        $SettingsPath = Join-Path $stateRoot "data\capture-settings.json"
    }
    if (-not (Test-Path -LiteralPath $SettingsPath -PathType Leaf)) {
        throw "Website capture settings are unavailable: $SettingsPath"
    }
    try {
        $websiteSettings = Get-Content -Raw -LiteralPath $SettingsPath | ConvertFrom-Json
    } catch {
        throw "Website capture settings are unreadable or invalid: $($_.Exception.Message)"
    }

    $joinProperty = $websiteSettings.PSObject.Properties["join_daily_videos"]
    $joinDailyVideos = if ($joinProperty) { $joinProperty.Value } else { $true }
    if ($joinDailyVideos -isnot [bool]) {
        throw "Website setting join_daily_videos must be boolean."
    }
    if (-not $joinDailyVideos) {
        Write-JoinLog "Daily video joining is disabled in the monitor website settings."
        exit 0
    }

    $deleteProperty = $websiteSettings.PSObject.Properties["delete_fragments"]
    $deleteSourceFragments = if ($deleteProperty) { $deleteProperty.Value } else { $false }
    if ($deleteSourceFragments -isnot [bool]) {
        throw "Website setting delete_fragments must be boolean."
    }

    if (-not $VideoRoot) {
        $outputProperty = $websiteSettings.PSObject.Properties["output_path"]
        if (-not $outputProperty -or -not $outputProperty.Value) {
            throw "Website setting output_path is missing."
        }
        $VideoRoot = Join-Path ([string]$outputProperty.Value) "Video"
    }
}

if (-not $VideoRoot -and $env:AVENT_CAPTURE_PATH) {
    $VideoRoot = Join-Path $env:AVENT_CAPTURE_PATH "Video"
}
if (-not $VideoRoot) {
    throw "Specify -VideoRoot, set AVENT_CAPTURE_PATH, or use -UseWebsiteSettings."
}

if (-not (Test-Path -LiteralPath $VideoRoot -PathType Container)) {
    throw "Video root is unavailable: $VideoRoot"
}

$resolvedVideoRoot = (Resolve-Path -LiteralPath $VideoRoot).ProviderPath
# The persistent empty file is intentional. OS/SMB handle ownership, not file
# existence, is the lock; crashes release it without a stale PID to repair.
$joinLock = $null
try {
    $joinLock = [System.IO.File]::Open(
        (Join-Path $resolvedVideoRoot '.avent-daily-join.lock'),
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
} catch [System.IO.IOException] {
    throw 'Cannot acquire daily-join ownership. Another join may be running, or storage is unavailable; retry later.'
}
try {
$dailyDirectory = Join-Path $resolvedVideoRoot "Daily"
New-Item -ItemType Directory -Path $dailyDirectory -Force | Out-Null

$ffmpeg = Resolve-MediaTool -Name "ffmpeg"
$ffprobe = Resolve-MediaTool -Name "ffprobe"

if ($AllPastDays -and $Date) {
    throw "Use either -Date or -AllPastDays, not both."
}

$dateKeys = @()
if ($AllPastDays) {
    $todayKey = (Get-Date).Date.ToString("yyyy-MM-dd")
    $dateKeys = @(
        Get-ChildItem -LiteralPath $resolvedVideoRoot -File -Filter "BabyMonitor_*.mkv" |
            ForEach-Object {
                if ($_.Name -match "^BabyMonitor_(\d{4}-\d{2}-\d{2})_\d{2}-\d{2}-\d{2}\.mkv$") {
                    $matches[1]
                }
            } |
            Where-Object { $_ -and $_ -lt $todayKey } |
            Sort-Object -Unique
    )
} elseif ($Date) {
    $parsedDate = [datetime]::ParseExact(
        $Date,
        "yyyy-MM-dd",
        [System.Globalization.CultureInfo]::InvariantCulture
    )
    $dateKeys = @($parsedDate.ToString("yyyy-MM-dd"))
} else {
    $dateKeys = @((Get-Date).Date.AddDays(-1).ToString("yyyy-MM-dd"))
}

if ($dateKeys.Count -eq 0) {
    Write-JoinLog "No completed recording dates were found; nothing to join."
    exit 0
}

$failures = [System.Collections.Generic.List[string]]::new()
foreach ($dateKey in $dateKeys) {
    try {
        $result = Join-OneDay `
            -DateKey $dateKey `
            -Root $resolvedVideoRoot `
            -OutputRoot $dailyDirectory `
            -Ffmpeg $ffmpeg `
            -Ffprobe $ffprobe `
            -Rebuild:$Force `
            -DeleteSources:$deleteSourceFragments
        $result | Format-List | Out-String | Write-Output
    } catch {
        $failures.Add("$dateKey`: $($_.Exception.Message)")
        Write-JoinLog "Failed $dateKey`: $($_.Exception.Message)" "ERROR"
    }
}

if ($failures.Count -gt 0) {
    throw "Daily join failed for $($failures.Count) date(s): $($failures -join '; ')"
}
} finally {
    if ($joinLock) { $joinLock.Dispose() }
}
