"""Actual-video availability and automatic capture state machine."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from capture import CaptureManager, ffprobe_executable


LOGGER = logging.getLogger("avent-monitor.feed")
OFFLINE_PROBE_INTERVAL_SECONDS = 15.0
ONLINE_CHECK_INTERVAL_SECONDS = 5.0
CAPTURE_CHECK_INTERVAL_SECONDS = 1.0
PROBE_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class FeedProbeResult:
    available: bool
    detail: str


def probe_video_feed(alias: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> FeedProbeResult:
    """Return available only after FFprobe receives an actual video packet."""
    source_url = "rtsp://127.0.0.1:8554/" + urllib.parse.quote(alias, safe="-_.~")
    command = [
        ffprobe_executable(),
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-rw_timeout",
        str(int(timeout * 1_000_000)),
        "-select_streams",
        "v:0",
        "-show_entries",
        "packet=size",
        "-of",
        "csv=p=0",
        "-read_intervals",
        "%+#1",
        source_url,
    ]
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout + 2,
            creationflags=creation_flags,
            check=False,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return FeedProbeResult(False, "No video packet arrived before the probe timed out")
    except OSError as error:
        return FeedProbeResult(False, f"Video probe could not start: {error}")

    if result.returncode == 0 and result.stdout.strip():
        return FeedProbeResult(True, "A valid video packet was received")
    detail = result.stderr.strip().splitlines()
    return FeedProbeResult(False, detail[-1] if detail else "No video packet was received")


class FeedMonitor:
    """Keep the website alive while coupling capture to real media availability."""

    def __init__(
        self,
        capture: CaptureManager,
        probe: Callable[[str], FeedProbeResult] = probe_video_feed,
        *,
        offline_interval: float = OFFLINE_PROBE_INTERVAL_SECONDS,
        online_interval: float = ONLINE_CHECK_INTERVAL_SECONDS,
    ) -> None:
        self.capture = capture
        self.probe = probe
        self.offline_interval = offline_interval
        self.online_interval = online_interval
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._alias = ""
        self._camera_name = "Philips Avent Baby Monitor"
        self._manual_capture = False
        self._capture_paused = False
        self._state = "stopped"
        self._detail = "Feed monitoring is stopped"
        self._last_checked_at: str | None = None
        self._last_video_at: str | None = None
        self._consecutive_probe_failures = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, alias: str, camera_name: str) -> None:
        self.stop()
        with self._lock:
            self._alias = alias
            self._camera_name = camera_name
            self._manual_capture = False
            self._capture_paused = False
            self._state = "probing"
            self._detail = "Checking the baby unit for video"
            self._last_checked_at = None
            self._consecutive_probe_failures = 0
            self._stop.clear()
            self._wake.clear()
            self._thread = threading.Thread(
                target=self._worker,
                name="avent-feed-monitor",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=PROBE_TIMEOUT_SECONDS + 4)
        self.capture.stop()
        with self._lock:
            self._thread = None
            self._state = "stopped"
            self._detail = "Feed monitoring is stopped"

    def request_capture(self, enabled: bool) -> None:
        with self._lock:
            self._manual_capture = enabled
            self._capture_paused = not enabled
        if not enabled:
            self.capture.stop()
        self._wake.set()

    def settings_changed(self) -> None:
        with self._lock:
            self._capture_paused = False
        self._wake.set()

    def check_now(self) -> None:
        self._wake.set()

    @property
    def consecutive_probe_failures(self) -> int:
        with self._lock:
            return self._consecutive_probe_failures

    def _capture_is_armed(self) -> bool:
        settings = self.capture.load_settings()
        return (
            settings["mode"] != "off"
            and not self._capture_paused
            and (settings["auto_start"] or self._manual_capture)
        )

    def status(self) -> dict[str, object]:
        with self._lock:
            state = self._state
            return {
                "state": state,
                "video_available": state in {"online", "recording"},
                "capture_armed": self._capture_is_armed(),
                "detail": self._detail,
                "last_checked_at": self._last_checked_at,
                "last_video_at": self._last_video_at,
                "consecutive_probe_failures": self._consecutive_probe_failures,
                "probe_interval_seconds": int(self.offline_interval),
            }

    def _set_state(self, state: str, detail: str) -> None:
        with self._lock:
            changed = state != self._state
            self._state = state
            self._detail = detail
        if changed:
            LOGGER.info("Feed state changed to %s: %s", state, detail)

    def _worker(self) -> None:
        delay = 0.0
        while not self._stop.is_set():
            if delay > 0:
                self._wake.wait(delay)
                self._wake.clear()
                if self._stop.is_set():
                    break
            delay = self._cycle()

    def _cycle(self) -> float:
        if self.capture.running:
            self._set_state("recording", "Video is online and capture is running")
            return CAPTURE_CHECK_INTERVAL_SECONDS

        with self._lock:
            previous_state = self._state
        if previous_state == "recording":
            ended_reason = self.capture.last_error or "The video feed ended"
            self.capture.stop()
            self._set_state("offline", f"{ended_reason}; waiting for the baby unit")
            return 2.0

        with self._lock:
            alias = self._alias
        if not alias:
            self._set_state("offline", "No camera is configured")
            return self.offline_interval

        self._set_state("probing", "Checking the baby unit for a valid video packet")
        result = self.probe(alias)
        checked = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock:
            self._last_checked_at = checked

        if not result.available:
            with self._lock:
                self._consecutive_probe_failures += 1
            self._set_state("offline", result.detail)
            return self.offline_interval

        with self._lock:
            self._last_video_at = checked
            self._consecutive_probe_failures = 0
        self._set_state("online", result.detail)
        if not self._capture_is_armed():
            return self.online_interval

        try:
            self.capture.start(alias, self._camera_name)
        except (OSError, ValueError) as error:
            self.capture.last_error = str(error)
            self._set_state("online", f"Video is online, but capture could not start: {error}")
            return self.online_interval

        if self.capture.running:
            self._set_state("recording", "Video is online and capture started automatically")
            return CAPTURE_CHECK_INTERVAL_SECONDS
        self._set_state("online", "Video is online; capture is preparing")
        return self.online_interval
