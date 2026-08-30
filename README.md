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
  adjacent JSON source manifest. Fragment deletion is separately opt-in and runs
  only after the joined video and manifest pass validation.

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

> Set up this repository on Windows by reading `docs/README.md` and
> `docs/SECURITY.md`, installing the pinned dependencies with
> `src/scripts/Install-Dependencies.ps1`, creating a Python 3.12 `.venv`,
> installing `requirements.txt`, and running the test suite. Keep every listener
> on `127.0.0.1`; never commit sessions, credentials, logs, or recordings. Stop
> before Baby Monitor+ login or MFA and ask the user to complete it privately.

This project is cloud-assisted: Philips/Tuya services handle authentication and
WebRTC signalling, and encrypted media may traverse a TURN relay. It is not an
offline camera protocol or a safety-critical alerting system.

See the [security policy](docs/SECURITY.md), [contribution guide](docs/CONTRIBUTING.md),
and [third-party notices](docs/THIRD_PARTY_NOTICES.md). The project is released
under the [MIT License](LICENSE).
