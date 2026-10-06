# Contributing

Thanks for helping improve the project.

1. Open an issue before making a large behavioral or protocol change.
2. Keep every listener on loopback unless the project adopts authentication and
   a new threat model.
3. Do not commit session files, credentials, recordings, device identifiers,
   private IP addresses, or logs from a real monitor.
4. Add or update tests for behavioral changes.
5. Run the verification commands below before opening a pull request.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/test_live_player.cjs
```

The browser-player tests require Node.js 20+ for development/CI only, not for
running the monitor. Use synthetic media for playback checks; never publish
images, audio or logs from a real baby monitor.

PowerShell scripts should parse without errors under PowerShell 7. Dependency
updates must remain pinned and checksum-verified where an upstream release
provides checksums.
