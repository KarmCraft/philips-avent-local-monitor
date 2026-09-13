"""Localhost-only website for a Philips Avent SCD953/26 monitor."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import ClientSession, ClientTimeout, web

from avent_api import PendingLogin, TuyaAPIError, begin_login, complete_login
from capture import CaptureManager
from feed_monitor import FeedMonitor
from process_job import WindowsProcessJob


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DEFAULT_STATE_ROOT = Path(
    os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))
) / "AventMonitor"
STATE_ROOT = Path(os.environ.get("AVENT_STATE_ROOT", str(DEFAULT_STATE_ROOT)))
BIN_ROOT = Path(os.environ.get("AVENT_BIN_ROOT", str(ROOT / "bin")))
CONFIG_ROOT = STATE_ROOT / "config"
DATA_ROOT = STATE_ROOT / "data"
LOG_ROOT = STATE_ROOT / "logs"
SECRET_FILE = Path(
    os.environ.get("AVENT_SECRET_FILE", str(STATE_ROOT / "session.json"))
)
BRIDGE_EXE = Path(
    os.environ.get(
        "AVENT_BRIDGE_EXE", str(BIN_ROOT / "avent-webrtc-bridge.exe")
    )
)
MEDIAMTX_EXE = Path(
    os.environ.get("AVENT_MEDIAMTX_EXE", str(BIN_ROOT / "mediamtx.exe"))
)
MEDIAMTX_CONFIG = CONFIG_ROOT / "mediamtx.yml"
PID_FILE = DATA_ROOT / "avent-monitor.pid"
SITE_HOST = "127.0.0.1"
SITE_PORT = int(os.environ.get("AVENT_SITE_PORT", "8090"))
MFA_TTL_SECONDS = 10 * 60
STALE_SESSION_FAILURE_THRESHOLD = 4
STALE_SESSION_RECYCLE_COOLDOWN_SECONDS = 120.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("avent-monitor")


def configure_file_logging() -> None:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    target = LOG_ROOT / "app.log"
    if any(
        isinstance(handler, RotatingFileHandler)
        and Path(handler.baseFilename) == target
        for handler in logging.getLogger().handlers
    ):
        return
    handler = RotatingFileHandler(
        target,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)


def rotate_plain_log(path: Path, *, max_bytes: int = 5 * 1024 * 1024, backups: int = 3) -> None:
    if not path.exists() or path.stat().st_size <= max_bytes:
        return
    try:
        oldest = path.with_name(f"{path.name}.{backups}")
        oldest.unlink(missing_ok=True)
        for index in range(backups - 1, 0, -1):
            current = path.with_name(f"{path.name}.{index}")
            if current.exists():
                current.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
    except PermissionError as error:
        LOGGER.warning(
            "Deferred rotation of temporarily locked child log %s: %s",
            path.name,
            error,
        )


def sanitize_rtsp_path(name: str, camera_id: str) -> str:
    value = name.replace(" ", "_").replace("/", "_").replace("\\", "_")
    return value if value and value != "_" else camera_id


def validate_secret(data: dict[str, Any]) -> bool:
    required = (
        "signing_key",
        "sid",
        "ecode",
        "partner",
        "app_key",
        "device_id",
        "package_name",
        "api_host",
        "cameras",
    )
    return all(data.get(key) for key in required) and isinstance(data["cameras"], list)


def load_secret() -> dict[str, Any] | None:
    try:
        data = json.loads(SECRET_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        LOGGER.error("The encrypted Avent session file is unreadable or invalid")
        return None
    return data if validate_secret(data) else None


def save_secret(data: dict[str, Any]) -> None:
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SECRET_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, SECRET_FILE)


def media_paths(config: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for index, camera in enumerate(config.get("cameras", []), start=1):
        camera_id = str(camera.get("camera_id", ""))
        camera_name = str(camera.get("camera_name", "Baby Monitor"))
        if not camera_id:
            continue
        source_path = sanitize_rtsp_path(camera_name, camera_id)
        alias = "baby" if index == 1 else f"baby-{index}"
        result.append(
            {
                "alias": alias,
                "name": camera_name,
                "source_path": source_path,
                "source_url": "rtsp://127.0.0.1:38554/"
                + urllib.parse.quote(source_path, safe="-_.~"),
            }
        )
    return result


def render_mediamtx_config(config: dict[str, Any]) -> str:
    lines = [
        "logLevel: info",
        "logDestinations: [stdout]",
        "rtsp: true",
        "rtspAddress: 127.0.0.1:8554",
        "rtspTransports: [tcp]",
        "rtmp: false",
        "hls: false",
        "webrtc: true",
        "webrtcAddress: 127.0.0.1:8889",
        f"webrtcAllowOrigins: ['http://127.0.0.1:{SITE_PORT}', 'http://localhost:{SITE_PORT}']",
        "webrtcLocalUDPAddress: 127.0.0.1:8189",
        "webrtcLocalTCPAddress: ''",
        "webrtcIPsFromInterfaces: false",
        "webrtcAdditionalHosts: [127.0.0.1]",
        "webrtcICEServers2: []",
        "srt: false",
        "moq: false",
        "api: true",
        "apiAddress: 127.0.0.1:9997",
        "metrics: false",
        "pprof: false",
        "playback: false",
        "paths:",
    ]
    for path in media_paths(config):
        lines.extend(
            [
                f"  {path['alias']}:",
                f"    source: {json.dumps(path['source_url'])}",
                "    sourceOnDemand: true",
                "    sourceOnDemandStartTimeout: 20s",
                "    sourceOnDemandCloseAfter: 10s",
                "    rtspTransport: tcp",
            ]
        )
    return "\n".join(lines) + "\n"


class Services:
    def __init__(self, capture: CaptureManager, feed: FeedMonitor) -> None:
        self.capture = capture
        self.feed = feed
        self.bridge: subprocess.Popen[bytes] | None = None
        self.media: subprocess.Popen[bytes] | None = None
        self._bridge_log: Any | None = None
        self._media_log: Any | None = None
        self._lock = threading.RLock()
        self._supervisor_stop = threading.Event()
        self._supervisor_thread: threading.Thread | None = None
        self._job: WindowsProcessJob | None = None
        self._environment: dict[str, str] = {}
        self._last_stale_recycle_at = 0.0

    @staticmethod
    def _running(process: subprocess.Popen[bytes] | None) -> bool:
        return process is not None and process.poll() is None

    def status(self) -> dict[str, bool]:
        with self._lock:
            return {
                "bridge": self._running(self.bridge),
                "media": self._running(self.media),
                "supervisor": (
                    self._supervisor_thread is not None
                    and self._supervisor_thread.is_alive()
                ),
            }

    def assign_process(self, process: subprocess.Popen[bytes]) -> None:
        job = self._job
        if job is not None:
            job.assign(process)

    def _launch_bridge(self) -> subprocess.Popen[bytes]:
        if self._bridge_log is None:
            raise OSError("Bridge log is not open")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            [str(BRIDGE_EXE), "addon", "--config", str(SECRET_FILE)],
            cwd=DATA_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=self._bridge_log,
            stderr=subprocess.STDOUT,
            env=self._environment,
            creationflags=creation_flags,
        )
        self.assign_process(process)
        return process

    def _launch_media(self) -> subprocess.Popen[bytes]:
        if self._media_log is None:
            raise OSError("Media log is not open")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            [str(MEDIAMTX_EXE), str(MEDIAMTX_CONFIG)],
            cwd=DATA_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=self._media_log,
            stderr=subprocess.STDOUT,
            env=self._environment,
            creationflags=creation_flags,
        )
        self.assign_process(process)
        return process

    def _close_child_logs(self) -> None:
        for handle in (self._bridge_log, self._media_log):
            if handle is not None:
                handle.close()
        self._bridge_log = None
        self._media_log = None

    def _open_child_logs(self) -> None:
        bridge_log_path = LOG_ROOT / "bridge.log"
        media_log_path = LOG_ROOT / "mediamtx.log"
        rotate_plain_log(bridge_log_path)
        rotate_plain_log(media_log_path)
        self._bridge_log = bridge_log_path.open("ab", buffering=0)
        try:
            self._media_log = media_log_path.open("ab", buffering=0)
        except OSError:
            self._bridge_log.close()
            self._bridge_log = None
            raise

    def _restart_stream_processes(self) -> None:
        with self._lock:
            self._stop_process(self.media)
            self._stop_process(self.bridge)
            self.media = None
            self.bridge = None
            self._close_child_logs()
            self._open_child_logs()
            try:
                self.bridge = self._launch_bridge()
                self.media = self._launch_media()
            except OSError:
                self._stop_process(self.media)
                self._stop_process(self.bridge)
                self.media = None
                self.bridge = None
                self._close_child_logs()
                raise
        self.feed.check_now()

    def _maybe_recycle_stale_session(self, now: float | None = None) -> bool:
        failures = self.feed.consecutive_probe_failures
        if failures < STALE_SESSION_FAILURE_THRESHOLD:
            return False
        checked_at = time.monotonic() if now is None else now
        if (
            self._last_stale_recycle_at > 0
            and checked_at - self._last_stale_recycle_at
            < STALE_SESSION_RECYCLE_COOLDOWN_SECONDS
        ):
            return False
        self._last_stale_recycle_at = checked_at
        LOGGER.warning(
            "No video packet after %s consecutive probes; recycling bridge and playback session",
            failures,
        )
        self._restart_stream_processes()
        return True

    def start(self, config: dict[str, Any]) -> None:
        self.stop()
        CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        LOG_ROOT.mkdir(parents=True, exist_ok=True)
        MEDIAMTX_CONFIG.write_text(render_mediamtx_config(config), encoding="utf-8")

        self._open_child_logs()
        self._environment = {**os.environ, "NO_COLOR": "1"}
        self._last_stale_recycle_at = 0.0
        try:
            self._job = WindowsProcessJob()
        except OSError:
            self._job = None
            LOGGER.exception("Could not create Windows child-process job; graceful cleanup remains active")

        with self._lock:
            self._supervisor_stop.clear()
            try:
                self.bridge = self._launch_bridge()
                self.media = self._launch_media()
            except OSError:
                self.stop()
                raise
            self._supervisor_thread = threading.Thread(
                target=self._supervisor_worker,
                name="avent-service-supervisor",
                daemon=True,
            )
            self._supervisor_thread.start()

        paths = media_paths(config)
        if paths:
            self.feed.start(paths[0]["alias"], paths[0]["name"])
        LOGGER.info("Started localhost bridge, playback, feed monitor, and service supervisor")

    def _supervisor_worker(self) -> None:
        while not self._supervisor_stop.wait(5):
            with self._lock:
                if self._supervisor_stop.is_set():
                    return
                if not self._running(self.bridge):
                    LOGGER.warning("Bridge exited; restarting it")
                    try:
                        self.bridge = self._launch_bridge()
                    except OSError:
                        LOGGER.exception("Could not restart bridge")
                if not self._running(self.media):
                    LOGGER.warning("MediaMTX exited; restarting it")
                    try:
                        self.media = self._launch_media()
                    except OSError:
                        LOGGER.exception("Could not restart MediaMTX")
            try:
                self._maybe_recycle_stale_session()
            except OSError:
                LOGGER.exception("Could not recycle stale Avent stream session")

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes] | None) -> None:
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def stop(self) -> None:
        self._supervisor_stop.set()
        self.feed.stop()
        supervisor = self._supervisor_thread
        if supervisor is not None and supervisor is not threading.current_thread():
            supervisor.join(timeout=7)
        with self._lock:
            self._stop_process(self.media)
            self._stop_process(self.bridge)
            self.media = None
            self.bridge = None
            self._supervisor_thread = None
            self._close_child_logs()
            if self._job is not None:
                self._job.close()
            self._job = None


CAPTURE = CaptureManager(DATA_ROOT / "capture-settings.json", LOG_ROOT / "capture.log")
FEED = FeedMonitor(CAPTURE)
SERVICES = Services(CAPTURE, FEED)
CAPTURE.set_process_started_callback(SERVICES.assign_process)
FLOWS: dict[str, PendingLogin] = {}


def json_error(message: str, *, status: int = 400, code: str = "error") -> web.Response:
    return web.json_response({"ok": False, "error": message, "code": code}, status=status)


@web.middleware
async def localhost_only(request: web.Request, handler):
    host = request.host.split(":", 1)[0].strip("[]").lower()
    peer = request.remote or ""
    if host not in {"127.0.0.1", "localhost"} or peer not in {"127.0.0.1", "::1"}:
        raise web.HTTPForbidden(text="Local access only")
    return await handler(request)


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_ROOT / "index.html")


async def status(request: web.Request) -> web.Response:
    config = load_secret()
    paths = media_paths(config) if config else []
    return web.json_response(
        {
            "ok": True,
            "configured": bool(config),
            "services": SERVICES.status(),
            "feed": FEED.status(),
            "capture": CAPTURE.status(),
            "cameras": [
                {
                    "name": item["name"],
                    "alias": item["alias"],
                    "viewer_url": f"http://127.0.0.1:8889/{item['alias']}",
                }
                for item in paths
            ],
        }
    )


async def login_start(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest):
        return json_error("Invalid request")
    email = str(payload.get("email", "")).strip()
    password = str(payload.get("password", ""))
    country_code = re.sub(r"\D", "", str(payload.get("country_code", "41")))
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return json_error("Enter a valid Baby Monitor+ account email")
    if not password:
        return json_error("Password is required")
    if not re.fullmatch(r"\d{1,4}", country_code):
        return json_error("Country calling code must contain 1 to 4 digits")

    for flow_id, pending in list(FLOWS.items()):
        if time.time() - pending.created_at > MFA_TTL_SECONDS:
            pending.clear_password()
            FLOWS.pop(flow_id, None)
    try:
        pending = await begin_login(
            request.app["client"],
            email=email,
            password=password,
            country_code=country_code,
        )
    except TuyaAPIError as error:
        LOGGER.warning("Avent login start failed with code %s", error.code)
        return json_error(
            "Baby Monitor+ rejected the login. Check the account details and country code.",
            code=error.code,
        )
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return json_error("Could not reach the Philips/Tuya login service", status=503)

    flow_id = uuid.uuid4().hex
    FLOWS[flow_id] = pending
    return web.json_response({"ok": True, "flow_id": flow_id})


async def login_complete(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest):
        return json_error("Invalid request")
    flow_id = str(payload.get("flow_id", ""))
    mfa_code = re.sub(r"\D", "", str(payload.get("mfa_code", "")))
    pending = FLOWS.get(flow_id)
    if not pending or time.time() - pending.created_at > MFA_TTL_SECONDS:
        return json_error("The login attempt expired; start again", code="flow_expired")
    if not re.fullmatch(r"\d{6}", mfa_code):
        return json_error("Enter the six-digit email verification code")

    try:
        config = await complete_login(pending, mfa_code)
        save_secret(config)
        await asyncio.to_thread(SERVICES.start, config)
    except TuyaAPIError as error:
        LOGGER.warning("Avent MFA/discovery failed with code %s", error.code)
        return json_error(
            "The verification code was rejected, or no Avent camera was found.",
            code=error.code,
        )
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return json_error("Could not reach the Philips/Tuya service", status=503)
    except OSError:
        LOGGER.exception("Could not save the encrypted Avent session or start services")
        return json_error("Could not save the local session or start the stream", status=500)

    pending.clear_password()
    FLOWS.pop(flow_id, None)

    return web.json_response(
        {
            "ok": True,
            "cameras": [item["name"] for item in media_paths(config)],
        }
    )


async def restart_services(request: web.Request) -> web.Response:
    config = load_secret()
    if not config:
        return json_error("Set up the Baby Monitor+ account first")
    try:
        await asyncio.to_thread(SERVICES.start, config)
    except OSError:
        LOGGER.exception("Could not restart Avent services")
        return json_error("Could not restart the local stream services", status=500)
    return web.json_response({"ok": True})


async def stop_services(request: web.Request) -> web.Response:
    await asyncio.to_thread(SERVICES.stop)
    return web.json_response({"ok": True})


async def save_capture_settings(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest):
        return json_error("Invalid request")
    try:
        settings = await asyncio.to_thread(CAPTURE.save_settings, payload)
    except (ValueError, RuntimeError) as error:
        return json_error(str(error))
    except OSError:
        LOGGER.exception("Could not verify or save the capture destination")
        return json_error("The capture folder is unavailable or not writable", status=503)
    FEED.settings_changed()
    return web.json_response(
        {
            "ok": True,
            "capture": {**settings, "running": False},
            "feed": FEED.status(),
        }
    )


async def save_daily_video_settings(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest):
        return json_error("Invalid request")
    try:
        settings = await asyncio.to_thread(CAPTURE.save_daily_settings, payload)
    except ValueError as error:
        return json_error(str(error))
    except OSError:
        LOGGER.exception("Could not save daily video settings")
        return json_error("Could not save daily video settings", status=500)
    return web.json_response(
        {
            "ok": True,
            "capture": {
                **settings,
                "running": CAPTURE.running,
                "started_at": CAPTURE.started_at,
                "encoder": CAPTURE.encoder,
                "last_error": CAPTURE.last_error,
            },
            "feed": FEED.status(),
        }
    )


async def start_capture(request: web.Request) -> web.Response:
    config = load_secret()
    if not config:
        return json_error("Set up the Baby Monitor+ account first")
    if not all(SERVICES.status().values()):
        return json_error("Start the local monitor services first")
    if not media_paths(config):
        return json_error("No Avent camera is configured")
    if CAPTURE.load_settings()["mode"] == "off":
        return json_error("Select video or timed pictures before starting capture")
    FEED.request_capture(True)
    return web.json_response(
        {"ok": True, "capture": CAPTURE.status(), "feed": FEED.status()}
    )


async def stop_capture(request: web.Request) -> web.Response:
    await asyncio.to_thread(FEED.request_capture, False)
    return web.json_response(
        {"ok": True, "capture": CAPTURE.status(), "feed": FEED.status()}
    )


async def shutdown(request: web.Request) -> web.Response:
    """Stop child services, return a response, then terminate this local server."""
    await asyncio.to_thread(SERVICES.stop)
    asyncio.get_running_loop().call_later(0.25, lambda: os._exit(0))
    return web.json_response({"ok": True})


async def client_session(app: web.Application):
    app["client"] = ClientSession(timeout=ClientTimeout(total=30))
    config = load_secret()
    if config:
        try:
            await asyncio.to_thread(SERVICES.start, config)
        except OSError:
            LOGGER.exception("Could not start configured Avent services")
    yield
    await asyncio.to_thread(SERVICES.stop)
    await app["client"].close()


def create_app() -> web.Application:
    app = web.Application(
        middlewares=[localhost_only],
        client_max_size=16 * 1024,
    )
    app.cleanup_ctx.append(client_session)
    app.router.add_get("/", index)
    app.router.add_static("/fonts/", WEB_ROOT / "fonts", name="fonts")
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/login/start", login_start)
    app.router.add_post("/api/login/complete", login_complete)
    app.router.add_post("/api/restart", restart_services)
    app.router.add_post("/api/stop", stop_services)
    app.router.add_post("/api/capture/settings", save_capture_settings)
    app.router.add_post("/api/daily-video/settings", save_daily_video_settings)
    app.router.add_post("/api/capture/start", start_capture)
    app.router.add_post("/api/capture/stop", stop_capture)
    app.router.add_post("/api/shutdown", shutdown)
    return app


def bind_site_socket(port: int) -> socket.socket:
    """Reserve the website before touching runtime files or starting media."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind((SITE_HOST, port))
        listener.listen(socket.SOMAXCONN)
        listener.setblocking(False)
        return listener
    except BaseException:
        listener.close()
        raise


if __name__ == "__main__":
    try:
        site_socket = bind_site_socket(SITE_PORT)
    except OSError as error:
        if error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) == 10048:
            LOGGER.warning("Website port is already in use; leaving its owner untouched and exiting.")
            sys.exit(0)
        raise
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    configure_file_logging()
    PID_FILE.write_text(str(os.getpid()), encoding="ascii")
    try:
        web.run_app(create_app(), sock=site_socket, print=None)
    except BaseException:
        LOGGER.exception("Avent website terminated unexpectedly")
        raise
    finally:
        site_socket.close()
        PID_FILE.unlink(missing_ok=True)
