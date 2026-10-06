# Setup and operation

Installation, configuration, recording, and scheduling guidance for Philips
Avent Local Monitor. See the [project README](../README.md) for features and the
agent setup prompt.

> [!IMPORTANT]
> The website is local, but the camera connection is not necessarily local.
> Philips/Tuya services are used for authentication and WebRTC signalling, and
> encrypted media may traverse a Tuya TURN relay. This is not a zero-cloud or
> offline camera integration.

## How it works

```text
Baby unit
  -> Philips/Tuya WebRTC signalling and encrypted media
  -> patched aventproxy bridge (loopback RTSP)
  -> MediaMTX (loopback WebRTC playback)
  -> local website
  -> FFmpeg recording or pictures
```

Every local listener is explicitly bound to loopback. No router port forward or
Windows firewall rule is required. The page has no login of its own and must not
be exposed to the LAN or internet.

The page serves fonts locally and has no browser-side CDN dependencies.

## Requirements

- Windows 10 or 11 on AMD64
- Python 3.12
- PowerShell 7 (`pwsh.exe`)
- Git and Go for the one-time bridge build
- FFmpeg and FFprobe on `PATH`, or their paths set through environment variables
- A Philips Baby Monitor+ account with access to the monitor

The tested monitor is the Philips Avent Connected Babyphone `SCD953/26`. Other
models may share the protocol, but they have not been tested.

The dependency installer builds aventproxy from a pinned commit and downloads a
checksum-verified MediaMTX release. It does not install Python, Go, Git, or
FFmpeg.

## Installation

From a PowerShell 7 prompt in the cloned repository:

```powershell
.\src\scripts\Install-Dependencies.ps1
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\src\Start-AventMonitor.ps1
```

Open http://127.0.0.1:8090 and complete the email, password, country-code, and
email-MFA setup. The password is kept only for that MFA exchange. The resulting
session material is saved locally so the monitor can restart automatically.

Use the page's confirmed **Stop monitor** button or run:

```powershell
.\src\Stop-AventMonitor.ps1
```

## Capture and daily videos

Choose video or timed pictures on the page and select an absolute local or UNC
destination. With **Automatically capture when online** enabled, capture starts
after the feed monitor receives a real video packet. It stops cleanly when the
feed disappears and resumes without reloading the page when the monitor returns.

Video is stored below `<capture path>\Video`; pictures are stored below
`<capture path>\Pictures`. Existing files are not overwritten.

The daily join script can be run manually:

```powershell
.\src\Join-AventDailyVideos.ps1 -UseWebsiteSettings
```

It processes the previous calendar day by default. Use `-Date YYYY-MM-DD` for a
specific day or `-AllPastDays` for every completed day found in the video
folder. The website's **Join daily video fragments** and **Delete fragments**
settings are honored with `-UseWebsiteSettings`. Source deletion happens only
after the joined MKV and its JSON manifest pass validation.

To run completed-day catch-up automatically at 00:30 each day and at sign-in:

```powershell
.\src\Install-DailyJoinTask.ps1
```

The task runs `Invoke-DailyJoin.ps1`, which calls the standalone join script with
`-UseWebsiteSettings -AllPastDays`. It excludes today's date, retries failures
eight times at 15-minute intervals, and writes `logs/daily-join.log` under the
state root. Missed dates are reconsidered at the next run. An exclusive file
handle at `<capture path>\Video\.avent-daily-join.lock` prevents overlapping
joins even when started manually. This empty lock file remains intentionally;
ownership is released when the process exits, not by deleting the file. An
unavailable share or invalid fragment fails visibly and retains its sources.
If a fragment lacks readable video metadata, the joiner checks its video
packets. A confirmed video-empty fragment is moved intact (including any audio)
to `Video/Incomplete/<date>`, with a JSON sidecar containing its SHA-256 and
reason. Valid fragments can then be joined; the daily manifest lists the
preserved omissions. Preservation never overwrites an existing file, and these
originals are excluded from fragment deletion. A day containing only video-empty
fragments creates no daily video. Files with video packets but unreadable
metadata still fail with their filename and remain available for repair.

Both installers create independent tasks in Windows' built-in Task Scheduler;
there is no shared job registry, custom scheduler state, or running-agent
dependency. Both use a windowless Windows Script Host launcher and the current
Windows user with limited privileges. The user must be signed in, with access to
the session file and capture location. Prefer a UNC path over a mapped drive.
At startup, a temporarily unavailable network share is handled by join retries.

The [agent setup prompt](../README.md#setup-prompt-for-agents) asks for a
destination (default Windows Videos folder plus `AventMonitor`), daily joining
(yes), and post-validation deletion (yes).
These are explicit installation choices, not a migration that silently enables
deletion on existing installations. A missing deletion setting still means no.

## Start automatically at Windows sign-in

```powershell
.\src\Install-AventMonitorTask.ps1
```

The launcher restarts unexpected website failures. An intentional **Stop
monitor** exits cleanly and remains stopped until it is started manually or the
user signs in again.

Start the registered monitor task from Task Scheduler (or `Start-ScheduledTask
-TaskName AventLocalMonitor`) rather than leaving another launch terminal open.
The website reserves its loopback port before starting child services; a duplicate
launch exits without modifying the existing owner's runtime files or media.
If another application owns that port, it is left untouched: resolve the conflict
or choose another website port. The bridge/media ports still allow only one
monitor installation on a host.

To migrate from a shared scheduler, first install and verify these two native
tasks with the existing state root and secret-file path. Disable only the old
monitor/join entries, gracefully stop the old monitor stack, and start the new
monitor task. Preserve session files, capture settings and footage. Do not run
both join schedules. Repairing unrelated shared scheduler state is not required.

Custom task names, state paths, and the website port can be supplied as script
parameters. Run `Get-Help .\src\Install-AventMonitorTask.ps1 -Detailed` to inspect
them.

## Configuration

The application uses these optional environment variables:

| Variable | Purpose | Default |
| --- | --- | --- |
| `AVENT_STATE_ROOT` | Runtime configuration, data, and logs | `%LOCALAPPDATA%\AventMonitor` |
| `AVENT_SECRET_FILE` | Baby Monitor+ session JSON | `<state root>\session.json` |
| `AVENT_BIN_ROOT` | Directory containing bridge and MediaMTX | `<repository>\src\bin` |
| `AVENT_BRIDGE_EXE` | Patched aventproxy executable | `<bin root>\avent-webrtc-bridge.exe` |
| `AVENT_MEDIAMTX_EXE` | MediaMTX executable | `<bin root>\mediamtx.exe` |
| `AVENT_CAPTURE_PATH` | Initial video and picture destination | `%USERPROFILE%\Videos\AventMonitor` |
| `AVENT_FFMPEG` | FFmpeg executable | Discovered on `PATH` |
| `AVENT_FFPROBE` | FFprobe executable | Discovered on `PATH` or beside FFmpeg |
| `AVENT_TIMESTAMP_FONT` | Font used for burned-in timestamps | `C:\Windows\Fonts\segoeui.ttf` |
| `AVENT_SITE_PORT` | Local website port | `8090` |

Set persistent values through Windows user environment variables, or pass the
supported values to the PowerShell launch and task-installation scripts. Do not
commit a session file or place raw credentials in an environment file inside the
repository.

For stronger at-rest protection, create an EFS-encrypted directory for the
session file and point `AVENT_SECRET_FILE` to it. The session JSON must remain
readable by the Windows user that runs the scheduled task.

## Privacy mode

Closing the browser tab does not stop an armed recorder. Before enabling Philips
Privacy Mode, use **Stop capture** or **Stop monitor** and wait for the page to
report that capture has stopped. The whole-monitor stop ends the recorder, feed
monitor, bridge, MediaMTX, and local website.

## Development and verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
& .\src\bin\mediamtx.exe --version
& .\src\bin\avent-webrtc-bridge.exe --version
```

The unit tests cover loopback bindings, setup and shutdown controls, capture
settings, timestamp and metadata commands, feed-loss recovery, daily settings,
and service recycling. CI runs the test suite and parses every PowerShell script
on Windows.

## Dependency provenance

- aventproxy tag `2026.8.0`, commit
  `00eb72a8d5f4d2223b9262762f0a6337ae32d337`
- MediaMTX release `v1.20.0`, official Windows AMD64 archive SHA-256
  `7364e7672e6b4420e986ec4b56e2cc32ec7b4085f69b56ec224d596d0fa8b19f`

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for licenses and patch
details.

## Disclaimer and license

This is an independent interoperability project. It is not affiliated with or
endorsed by Philips, Philips Avent, Versuni, or Tuya. A baby monitor is not a
substitute for responsible adult supervision, and this software should not be
treated as a safety-critical alerting system.

The project is released under the [MIT License](../LICENSE).
