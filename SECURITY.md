# Security

## Local-only design

The website, RTSP bridge, MediaMTX playback endpoint, and service APIs are
intentionally bound to `127.0.0.1`. Do not expose them through a reverse proxy,
port forwarding, a tunnel, or a firewall rule. The application has no user
authentication because its security boundary is the local Windows session.

The Baby Monitor+ session file contains reusable authentication material. By
default it is stored at `%LOCALAPPDATA%\AventMonitor\session.json`. Protect that
file with your Windows account permissions or place it in an EFS-encrypted
directory and set `AVENT_SECRET_FILE` to its path. Never attach the session
file, runtime logs, or recordings to an issue.

The setup form sends the supplied credentials directly to the Philips/Tuya
login service. The password remains in process memory only for the MFA exchange
and is not written by this application.

## Cloud boundary

This is a local viewer, not an offline camera protocol. Authentication and
WebRTC signalling use Philips/Tuya infrastructure, and the encrypted media may
traverse a cloud TURN relay. Stop capture and the monitor before enabling the
Philips Privacy Mode.

## Reporting a vulnerability

After the repository is published, please use GitHub's private vulnerability
reporting feature rather than a public issue. Include affected versions,
reproduction steps, and impact, but remove accounts, tokens, IP addresses,
recordings, and other private data.

Only the current `main` branch receives security fixes.
