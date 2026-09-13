# Philips Avent Local Monitor

An unofficial, localhost-only Windows viewer and recorder for the Philips Avent
Connected Babyphone `SCD953/26`.

Application code and runtime assets live in [`src/`](src/). Detailed setup,
configuration, recording, privacy, and development documentation lives in
[`docs/README.md`](docs/README.md).

## Features

- **Local live view:** a resilient bridge and playback gateway serve the camera
  through a browser interface restricted to `127.0.0.1`. The page reports
  probing, offline, online, and recording states and remains available while the
  baby unit is switched off.
- **Automatic video recording:** capture waits for a real video packet before
  starting. If the feed disappears, FFmpeg is allowed to finalize the current
  file; capture resumes automatically when video returns, without reloading the
  page.
- **Timestamped video fragments:** recordings are stored below the configured
  local or UNC path as approximately ten-minute MKV fragments. Video is encoded
  as H.264 with a small local date, time, and UTC offset burned into the frame;
  the camera's audio stream is preserved when available.
- **Timed pictures with metadata:** snapshots can be saved every 1–3,600 seconds
  as atomically written JPEG files. Each completed picture receives EXIF capture
  time, timezone, dimensions, manufacturer, model, and application metadata.
- **Validated daily videos:** an optional scheduled task joins a completed day's
  compatible fragments without re-encoding, validates the result, and writes an
  adjacent JSON source manifest. The independent Windows task catches up missed
  days at sign-in and runs daily, with retries and overlap protection. The agent
  setup asks whether to join daily and delete fragments (both recommended defaults:
  yes); deletion runs only after the joined video and manifest pass validation.

The destination is write-tested before capture starts. **Stop capture** safely
finalizes the current output and pauses automatic restart for that run, while
**Stop monitor** also shuts down the feed monitor, bridge, playback gateway, and
local website.

## Quick start

```powershell
.\src\scripts\Install-Dependencies.ps1
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\src\Start-AventMonitor.ps1
```

Then open http://127.0.0.1:8090.

## Setup prompt for agents

Copy this prompt into your coding agent with the repository open:

```text
Install and configure this whole Philips Avent Local Monitor app on my Windows
machine. Read docs/README.md and docs/SECURITY.md first. Do not assume any
machine-specific paths, custom scheduler, Codex automation, or Home Assistant.

Before configuring capture, ask me:
1. Where should recordings and pictures be stored? Suggest my Windows Videos
   folder plus AventMonitor (%USERPROFILE%\Videos\AventMonitor); accept an
   absolute local folder or UNC share. On an existing installation, suggest its
   current location instead and preserve existing recordings.
2. Automatically join each completed day's video fragments? Default: yes.
3. If joining is enabled, delete the individual fragments after the daily video
   AND source manifest pass validation? Default: yes. Explain that this removes
   the originals, while keeping the verified daily video. If joining is off,
   deletion must also be off. Ask for my choices; don't silently overwrite
   existing settings or treat an unanswered question as consent to deletion.

Check/install prerequisites (PowerShell 7, Python 3.12, Git, Go, FFmpeg/FFprobe),
asking for any required installation permissions. Use
src/scripts/Install-Dependencies.ps1 for pinned bridge/MediaMTX dependencies,
create the repository .venv, install requirements.txt, and run the test suite.
Keep source outside synchronized folders and all runtime/session/capture data
outside Git. Use %LOCALAPPDATA%\AventMonitor for runtime data unless an existing
installation uses a different location; preserve its session and permissions.

Validate write access to my chosen destination as the Windows user who will run
the app. Configure video capture, automatic recording when video is available,
and my confirmed join/deletion choices through the app's settings. Use the same
state root for the app and both task installers. Never expose credentials in
chat, terminal arguments, logs, screenshots, or the repository. Have me complete
Baby Monitor+ password/MFA privately on the local page; don't enter or read them.

Install src/Install-AventMonitorTask.ps1 and src/Install-DailyJoinTask.ps1 as
separate native Windows scheduled tasks for my user. They must run windowlessly,
without any shared productivity scheduler or an agent staying open. Explain
that they run in my signed-in Windows session, not before login. Start the
monitor using its registered task, not a second persistent terminal. If an old
installation exists, identify its tasks/processes and migrate only this app,
gracefully finalizing capture and disabling its old duplicate scheduling entry.

Verify the localhost website, one media stack, the two task actions/triggers,
and a disposable-fixture missed-day join/retention test. If the baby unit is
available, verify real recording progress and recovery without a page reload.
Do not claim live video is verified if the baby unit is unavailable. Do not run
destructive backfills of existing recordings without my explicit approval.
Report the URL, recording/state locations, task names, restart/stop instructions,
and anything still requiring my private input. No public/LAN listeners or ports.
```

The two tasks use Windows Task Scheduler directly. No shared job runner,
machine-specific productivity workspace, Codex subscription, or HAOS is needed.

This project is cloud-assisted: Philips/Tuya services handle authentication and
WebRTC signalling, and encrypted media may traverse a TURN relay. It is not an
offline camera protocol or a safety-critical alerting system.

See the [security policy](docs/SECURITY.md), [contribution guide](docs/CONTRIBUTING.md),
and [third-party notices](docs/THIRD_PARTY_NOTICES.md). The project is released
under the [MIT License](LICENSE).
