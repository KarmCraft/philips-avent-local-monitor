"""FFmpeg capture lifecycle for the localhost Avent monitor."""

from __future__ import annotations

import json
import logging
import ntpath
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Callable

from PIL import Image


LOGGER = logging.getLogger("avent-monitor.capture")
DEFAULT_OUTPUT_PATH = os.environ.get(
    "AVENT_CAPTURE_PATH", str(Path.home() / "Videos" / "AventMonitor")
)
DEFAULT_SETTINGS: dict[str, Any] = {
    "mode": "off",
    "output_path": DEFAULT_OUTPUT_PATH,
    "snapshot_interval": 10,
    "auto_start": False,
    "join_daily_videos": True,
    "delete_fragments": False,
}
ALLOWED_MODES = {"off", "video", "snapshots"}
FONT_FILE = Path(
    os.environ.get("AVENT_TIMESTAMP_FONT", r"C:\Windows\Fonts\segoeui.ttf")
)


def ffmpeg_executable() -> str:
    configured = os.environ.get("AVENT_FFMPEG", "").strip()
    if configured:
        return configured
    discovered = shutil.which("ffmpeg")
    if discovered:
        return discovered
    raise OSError("FFmpeg is not installed; set AVENT_FFMPEG or add it to PATH")


def ffprobe_executable() -> str:
    configured = os.environ.get("AVENT_FFPROBE", "").strip()
    if configured:
        return configured
    discovered = shutil.which("ffprobe")
    if discovered:
        return discovered
    ffmpeg = Path(ffmpeg_executable())
    companion = ffmpeg.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if companion.is_file():
        return str(companion)
    raise OSError("FFprobe is not installed; set AVENT_FFPROBE or add it to PATH")


def validate_capture_settings(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode", "off")).strip().lower()
    if mode not in ALLOWED_MODES:
        raise ValueError("Capture mode must be off, video, or snapshots")

    output_path = str(payload.get("output_path", "")).strip()
    if not output_path or "\x00" in output_path:
        raise ValueError("Enter an absolute output folder")
    output_path = ntpath.normpath(output_path)
    parsed = PureWindowsPath(output_path)
    if not parsed.is_absolute() or len(parsed.parts) < 2 or not parsed.name:
        raise ValueError("Output folder must be an absolute local or UNC path")

    try:
        interval = int(payload.get("snapshot_interval", 10))
    except (TypeError, ValueError) as error:
        raise ValueError("Picture interval must be a whole number of seconds") from error
    if interval < 1 or interval > 3600:
        raise ValueError("Picture interval must be between 1 and 3600 seconds")

    auto_start = payload.get("auto_start", False)
    if not isinstance(auto_start, bool):
        raise ValueError("Automatic start must be true or false")

    join_daily_videos = payload.get("join_daily_videos", True)
    if not isinstance(join_daily_videos, bool):
        raise ValueError("Join daily video fragments must be true or false")

    delete_fragments = payload.get("delete_fragments", False)
    if not isinstance(delete_fragments, bool):
        raise ValueError("Delete fragments must be true or false")
    delete_fragments = delete_fragments and join_daily_videos

    return {
        "mode": mode,
        "output_path": output_path,
        "snapshot_interval": interval,
        "auto_start": auto_start,
        "join_daily_videos": join_daily_videos,
        "delete_fragments": delete_fragments,
    }


def ensure_writable_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".avent-write-probe-{uuid.uuid4().hex}.tmp"
    try:
        probe.write_bytes(b"Avent capture write probe\n")
        if probe.read_bytes() != b"Avent capture write probe\n":
            raise OSError("Output folder write verification did not round-trip")
    finally:
        probe.unlink(missing_ok=True)


def rotate_capture_log(path: Path, max_bytes: int = 5 * 1024 * 1024) -> None:
    if not path.exists() or path.stat().st_size <= max_bytes:
        return
    path.with_name(f"{path.name}.3").unlink(missing_ok=True)
    for index in (2, 1):
        current = path.with_name(f"{path.name}.{index}")
        if current.exists():
            current.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def drawtext_filter() -> str:
    font = str(FONT_FILE).replace("\\", "/").replace(":", r"\:")
    timestamp = r"%{localtime\:%Y-%m-%d %H-%M-%S %z}"
    return (
        f"drawtext=fontfile='{font}':text='{timestamp}':"
        "fontcolor=white:fontsize=18:box=1:boxcolor=black@0.55:"
        "boxborderw=5:x=w-tw-12:y=h-th-12"
    )


def encoder_arguments(encoder: str) -> list[str]:
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p4", "-cq", "24", "-b:v", "0"]
    if encoder == "h264_qsv":
        return ["-c:v", encoder, "-preset", "medium", "-global_quality", "24"]
    if encoder == "h264_amf":
        return ["-c:v", encoder, "-quality", "balanced", "-rc", "cqp", "-qp_i", "24", "-qp_p", "24"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]


def select_video_encoder(ffmpeg: str) -> str:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    for encoder in ("h264_nvenc", "h264_qsv", "h264_amf"):
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=64x64:rate=1:duration=1",
            "-frames:v",
            "1",
            *encoder_arguments(encoder),
            "-f",
            "null",
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                creationflags=creation_flags,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return encoder
    return "libx264"


def build_video_command(
    ffmpeg: str,
    source_url: str,
    output_directory: Path,
    encoder: str,
    segment_seconds: int = 600,
) -> list[str]:
    pattern = output_directory / "BabyMonitor_%Y-%m-%d_%H-%M-%S.mkv"
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-rtsp_transport",
        "tcp",
        "-i",
        source_url,
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        drawtext_filter(),
        *encoder_arguments(encoder),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-force_key_frames",
        f"expr:gte(t,n_forced*{segment_seconds})",
        "-f",
        "segment",
        "-segment_time",
        str(segment_seconds),
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        str(pattern),
    ]


def build_snapshot_command(
    ffmpeg: str,
    source_url: str,
    output_directory: Path,
    interval: int,
) -> list[str]:
    pattern = output_directory / "BabyMonitor_%Y-%m-%d_%H-%M-%S.jpg"
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-rtsp_transport",
        "tcp",
        "-i",
        source_url,
        "-map",
        "0:v:0",
        "-vf",
        f"fps=1/{interval}",
        "-q:v",
        "2",
        "-f",
        "image2",
        "-strftime",
        "1",
        "-atomic_writing",
        "1",
        str(pattern),
    ]


def write_image_metadata(path: Path, camera_name: str) -> None:
    captured = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    timestamp = captured.strftime("%Y:%m:%d %H:%M:%S")
    offset = captured.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"

    with Image.open(path) as image:
        image.load()
        exif = image.getexif()
        exif[270] = camera_name
        exif[271] = "Philips Avent"
        exif[272] = "SCD953/26"
        exif[305] = "Avent localhost viewer"
        exif[306] = timestamp
        exif[36867] = timestamp
        exif[36868] = timestamp
        exif[36880] = offset
        exif[36881] = offset
        exif[36882] = offset
        exif[40962] = image.width
        exif[40963] = image.height
        temporary = path.with_name(f".{path.stem}.metadata-{uuid.uuid4().hex}.jpg")
        image.convert("RGB").save(temporary, format="JPEG", quality=95, exif=exif)
    os.replace(temporary, path)


class CaptureManager:
    def __init__(
        self,
        settings_file: Path,
        log_file: Path,
        on_process_started: Callable[[subprocess.Popen[bytes]], None] | None = None,
    ) -> None:
        self.settings_file = settings_file
        self.log_file = log_file
        self.process: subprocess.Popen[bytes] | None = None
        self._log_handle: Any | None = None
        self._supervisor_thread: threading.Thread | None = None
        self._supervisor_stop = threading.Event()
        self._metadata_thread: threading.Thread | None = None
        self._metadata_stop = threading.Event()
        self._lock = threading.RLock()
        self._stopping = False
        self._active = False
        self._command: list[str] | None = None
        self._output_directory: Path | None = None
        self._known_images: set[Path] = set()
        self._picture_directory: Path | None = None
        self._camera_name = "Philips Avent Baby Monitor"
        self.started_at: str | None = None
        self.encoder: str | None = None
        self.last_error: str | None = None
        self._on_process_started = on_process_started
        self._selected_encoder: str | None = None

    def set_process_started_callback(
        self,
        callback: Callable[[subprocess.Popen[bytes]], None] | None,
    ) -> None:
        self._on_process_started = callback

    def load_settings(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.settings_file.read_text(encoding="utf-8"))
            return validate_capture_settings(raw)
        except FileNotFoundError:
            return dict(DEFAULT_SETTINGS)
        except (OSError, json.JSONDecodeError, ValueError):
            LOGGER.exception("Capture settings are unreadable or invalid")
            return dict(DEFAULT_SETTINGS)

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self.running:
                raise RuntimeError("Stop capture before changing its settings")
            settings = validate_capture_settings(payload)
            ensure_writable_directory(Path(settings["output_path"]))
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
            os.replace(temporary, self.settings_file)
            self.last_error = None
            return settings

    def save_daily_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self.load_settings()
            current["join_daily_videos"] = payload.get("join_daily_videos")
            current["delete_fragments"] = payload.get("delete_fragments")
            settings = validate_capture_settings(current)
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
            os.replace(temporary, self.settings_file)
            return settings

    @property
    def running(self) -> bool:
        return (
            self._active
            and self._supervisor_thread is not None
            and self._supervisor_thread.is_alive()
        )

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self.running
            return {
                **self.load_settings(),
                "running": running,
                "started_at": self.started_at,
                "encoder": self.encoder,
                "last_error": self.last_error,
            }

    def start(self, alias: str, camera_name: str) -> None:
        with self._lock:
            if self.running:
                return
            settings = self.load_settings()
            if settings["mode"] == "off":
                raise ValueError("Select video or timed pictures before starting capture")

            output_root = Path(settings["output_path"])
            ensure_writable_directory(output_root)
            ffmpeg = ffmpeg_executable()
            source_url = "rtsp://127.0.0.1:8554/" + urllib.parse.quote(alias, safe="-_.~")
            self._camera_name = camera_name
            self.last_error = None
            self.encoder = None
            self._stopping = False
            self._active = True
            self._supervisor_stop.clear()
            self._metadata_stop.clear()

            if settings["mode"] == "video":
                output_directory = output_root / "Video"
                ensure_writable_directory(output_directory)
                if self._selected_encoder is None:
                    self._selected_encoder = select_video_encoder(ffmpeg)
                self.encoder = self._selected_encoder
                command = build_video_command(ffmpeg, source_url, output_directory, self.encoder)
                self._picture_directory = None
            else:
                output_directory = output_root / "Pictures"
                ensure_writable_directory(output_directory)
                command = build_snapshot_command(
                    ffmpeg,
                    source_url,
                    output_directory,
                    settings["snapshot_interval"],
                )
                self._picture_directory = output_directory
                self._known_images = set(output_directory.glob("BabyMonitor_*.jpg"))

            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            rotate_capture_log(self.log_file)
            self._log_handle = self.log_file.open("ab", buffering=0)
            self._command = command
            self._output_directory = output_directory
            self._supervisor_thread = threading.Thread(
                target=self._supervisor_worker,
                name="avent-capture-supervisor",
                daemon=True,
            )
            self._supervisor_thread.start()
            self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
            if self._picture_directory is not None:
                self._metadata_thread = threading.Thread(
                    target=self._metadata_worker,
                    name="avent-image-metadata",
                    daemon=True,
                )
                self._metadata_thread.start()

        time.sleep(0.4)
        if self._supervisor_thread is None or not self._supervisor_thread.is_alive():
            self.stop()
            raise OSError("Capture supervisor could not start")
        LOGGER.info("Started %s capture in %s", settings["mode"], output_directory)

    def _supervisor_worker(self) -> None:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        command = self._command
        output_directory = self._output_directory
        if command is None or output_directory is None or self._log_handle is None:
            self.last_error = "Capture configuration is incomplete"
            with self._lock:
                self._active = False
            return
        try:
            process = subprocess.Popen(
                command,
                cwd=output_directory,
                stdin=subprocess.PIPE,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
            if self._on_process_started is not None:
                self._on_process_started(process)
        except OSError as error:
            self.last_error = f"Could not launch FFmpeg: {error}"
            with self._lock:
                self._active = False
            return

        with self._lock:
            self.process = process
        healthy_at = time.monotonic() + 5
        while not self._supervisor_stop.wait(0.25):
            code = process.poll()
            if code is not None:
                self.last_error = f"Capture stream ended with code {code}"
                break
            if time.monotonic() >= healthy_at:
                self.last_error = None

        with self._lock:
            self.process = None
            if not self._stopping:
                self._active = False

    def _metadata_worker(self) -> None:
        while not self._metadata_stop.wait(0.5):
            self._annotate_new_images()
        self._annotate_new_images()

    def _annotate_new_images(self) -> None:
        if self._picture_directory is None:
            return
        for path in sorted(self._picture_directory.glob("BabyMonitor_*.jpg")):
            if path in self._known_images:
                continue
            try:
                write_image_metadata(path, self._camera_name)
                self._known_images.add(path)
            except (OSError, ValueError):
                LOGGER.exception("Could not add metadata to captured picture %s", path.name)
                self.last_error = f"Could not add metadata to {path.name}"

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            self._active = False
            self._supervisor_stop.set()
            self._metadata_stop.set()
            process = self.process
            supervisor = self._supervisor_thread
            metadata_thread = self._metadata_thread

        if process is not None and process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write(b"q\n")
                    process.stdin.flush()
                process.wait(timeout=12)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        if supervisor is not None:
            supervisor.join(timeout=5)
        if metadata_thread is not None:
            metadata_thread.join(timeout=5)

        with self._lock:
            if self._log_handle is not None:
                self._log_handle.close()

            self.process = None
            self._log_handle = None
            self._supervisor_thread = None
            self._metadata_thread = None
            self._command = None
            self._output_directory = None
            self._picture_directory = None
            self.started_at = None
            self.encoder = None
            self._stopping = False
            self._supervisor_stop.clear()
            self._metadata_stop.clear()
            LOGGER.info("Stopped capture process")
