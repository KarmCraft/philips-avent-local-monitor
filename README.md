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

This project is cloud-assisted: Philips/Tuya services handle authentication and
WebRTC signalling, and encrypted media may traverse a TURN relay. It is not an
offline camera protocol or a safety-critical alerting system.

See the [security policy](docs/SECURITY.md), [contribution guide](docs/CONTRIBUTING.md),
and [third-party notices](docs/THIRD_PARTY_NOTICES.md). The project is released
under the [MIT License](LICENSE).
