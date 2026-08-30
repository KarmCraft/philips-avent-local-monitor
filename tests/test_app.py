import unittest
import sys
from pathlib import Path
from unittest.mock import Mock

SRC_ROOT = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from app import (
    Services,
    media_paths,
    render_mediamtx_config,
    rotate_plain_log,
    sanitize_rtsp_path,
    validate_secret,
)


class AventMonitorTests(unittest.TestCase):
    def test_sanitize_path_matches_upstream_contract(self):
        self.assertEqual(sanitize_rtsp_path("Baby Room / Left", "id1"), "Baby_Room___Left")
        self.assertEqual(sanitize_rtsp_path("", "id1"), "id1")

    def test_media_config_is_loopback_only(self):
        config = {
            "cameras": [
                {"camera_id": "abc123", "camera_name": "Baby Room", "product_id": "p"}
            ]
        }
        rendered = render_mediamtx_config(config)
        self.assertIn("rtspAddress: 127.0.0.1:8554", rendered)
        self.assertIn("webrtcAddress: 127.0.0.1:8889", rendered)
        self.assertIn("webrtcLocalUDPAddress: 127.0.0.1:8189", rendered)
        self.assertNotIn("0.0.0.0", rendered)
        self.assertEqual(media_paths(config)[0]["alias"], "baby")

    def test_secret_validation_requires_session_and_camera(self):
        complete = {
            "signing_key": "s",
            "sid": "sid",
            "ecode": "e",
            "partner": "p",
            "app_key": "a",
            "device_id": "d",
            "package_name": "pkg",
            "api_host": "host",
            "cameras": [{"camera_id": "c"}],
        }
        self.assertTrue(validate_secret(complete))
        complete["sid"] = ""
        self.assertFalse(validate_secret(complete))

    def test_index_exposes_confirmed_shutdown_control(self):
        index = (SRC_ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="stop">Stop monitor</button>', index)
        self.assertIn("window.confirm", index)
        self.assertIn("api('/api/shutdown'", index)

    def test_index_exposes_capture_controls(self):
        index = (SRC_ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="capturePath"', index)
        self.assertIn('id="snapshotInterval"', index)
        self.assertIn("api('/api/capture/start'", index)
        self.assertIn("api('/api/capture/stop'", index)
        self.assertIn("Automatically capture when online", index)
        self.assertIn("Baby unit offline", index)
        self.assertIn("data.feed.video_available", index)
        self.assertIn("Join daily video fragments", index)
        self.assertIn("Delete fragments", index)
        self.assertIn("api('/api/daily-video/settings'", index)

    def test_index_uses_refined_self_hosted_design(self):
        index = (SRC_ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('font-family: "Playfair Display"', index)
        self.assertIn('/fonts/PlayfairDisplay-Variable.ttf', index)
        self.assertIn('/fonts/JetBrainsMono-Variable.ttf', index)
        self.assertIn('@media (max-width: 560px)', index)
        self.assertIn('aria-live="polite"', index)
        self.assertNotIn("—", index)

    def test_stale_session_recycle_uses_threshold_and_cooldown(self):
        feed = Mock()
        feed.consecutive_probe_failures = 3
        services = Services(Mock(), feed)
        services._restart_stream_processes = Mock()
        self.assertFalse(services._maybe_recycle_stale_session(now=100))
        feed.consecutive_probe_failures = 4
        self.assertTrue(services._maybe_recycle_stale_session(now=100))
        self.assertFalse(services._maybe_recycle_stale_session(now=150))
        self.assertTrue(services._maybe_recycle_stale_session(now=221))
        self.assertEqual(services._restart_stream_processes.call_count, 2)

    def test_locked_plain_log_does_not_abort_service_start(self):
        path = Mock()
        path.name = "bridge.log"
        path.exists.return_value = True
        path.stat.return_value.st_size = 6 * 1024 * 1024
        path.with_name.return_value = path
        path.replace.side_effect = PermissionError(32, "locked")
        rotate_plain_log(path)

    def test_task_installer_uses_sign_in_and_durable_recovery(self):
        installer = (SRC_ROOT / "Install-AventMonitorTask.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", installer)
        self.assertIn("-RestartCount 999", installer)
        self.assertIn("-RestartInterval (New-TimeSpan -Minutes 1)", installer)
        self.assertIn("-MultipleInstances IgnoreNew", installer)

        launcher = (SRC_ROOT / "Start-AventMonitor.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("while ($true)", launcher)
        self.assertIn("if ($exitCode -eq 0)", launcher)
        self.assertIn("restarting in 10 seconds", launcher)


if __name__ == "__main__":
    unittest.main()
