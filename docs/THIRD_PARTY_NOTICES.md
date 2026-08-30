# Third-party notices

This repository contains integration code and build instructions for the
following projects. Their names and trademarks belong to their respective
owners.

## aventproxy

- Project: `thekoma/aventproxy`
- Source: https://github.com/thekoma/aventproxy
- Pinned source: tag `2026.8.0`, commit
  `00eb72a8d5f4d2223b9262762f0a6337ae32d337`
- License: MIT

The repository includes a small patch that makes the pinned bridge build on
Windows, injects a build version, and binds its RTSP listener to loopback. The
upstream source and compiled executable are not committed here.

## MediaMTX

- Project: `bluenviron/mediamtx`
- Source: https://github.com/bluenviron/mediamtx
- Pinned release: `v1.20.0`
- License: MIT

The dependency installer downloads the official Windows AMD64 archive and
checks its published SHA-256 digest before extraction. The executable is not
committed here.

## Playfair Display and JetBrains Mono

The web interface includes the variable font files for Playfair Display and
JetBrains Mono. Each font is distributed under the SIL Open Font License 1.1.
The complete license texts are stored beside the corresponding files in
`src/web/fonts`.

## Philips Avent and Tuya

This project is unofficial and is not affiliated with, endorsed by, or
supported by Philips, Philips Avent, Versuni, or Tuya. Product names are used
only to describe interoperability.
