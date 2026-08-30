# Philips Avent Local Monitor

An unofficial, localhost-only Windows viewer and recorder for the Philips Avent
Connected Babyphone `SCD953/26`.

Application code and runtime assets live in [`src/`](src/). Detailed setup,
configuration, recording, privacy, and development documentation lives in
[`docs/README.md`](docs/README.md).

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
