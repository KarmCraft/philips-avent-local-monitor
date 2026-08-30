import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from capture import (
    CaptureManager,
    build_snapshot_command,
    build_video_command,
    validate_capture_settings,
    write_image_metadata,
)


class CaptureTests(unittest.TestCase):
    def test_settings_accept_unc_path_and_normalize_values(self):
        settings = validate_capture_settings(
            {
                "mode": "snapshots",
                "output_path": r"\\fileserver\captures\AventMonitor" + "\\",
                "snapshot_interval": "15",
                "auto_start": True,
            }
        )
        self.assertEqual(settings["output_path"], r"\\fileserver\captures\AventMonitor")
        self.assertEqual(settings["snapshot_interval"], 15)
        self.assertTrue(settings["join_daily_videos"])
        self.assertFalse(settings["delete_fragments"])

    def test_fragment_deletion_requires_daily_joining(self):
        settings = validate_capture_settings(
            {
                "mode": "video",
                "output_path": r"C:\Captures",
                "snapshot_interval": 10,
                "auto_start": True,
                "join_daily_videos": False,
                "delete_fragments": True,
            }
        )
        self.assertFalse(settings["join_daily_videos"])
        self.assertFalse(settings["delete_fragments"])

    def test_settings_reject_root_and_invalid_interval(self):
        with self.assertRaises(ValueError):
            validate_capture_settings(
                {
                    "mode": "video",
                    "output_path": "C:\\",
                    "snapshot_interval": 10,
                    "auto_start": False,
                }
            )
        with self.assertRaises(ValueError):
            validate_capture_settings(
                {
                    "mode": "snapshots",
                    "output_path": r"C:\Captures",
                    "snapshot_interval": 0,
                    "auto_start": False,
                }
            )

    def test_video_command_burns_timestamp_and_segments_mkv(self):
        command = build_video_command(
            "ffmpeg",
            "rtsp://127.0.0.1:8554/baby",
            Path(r"C:\Captures\Video"),
            "libx264",
        )
        joined = " ".join(str(item) for item in command)
        self.assertIn("drawtext=", joined)
        self.assertIn("localtime", joined)
        self.assertIn("-segment_time 600", joined)
        self.assertTrue(command[-1].endswith(".mkv"))

    def test_snapshot_command_uses_interval_and_atomic_jpegs(self):
        command = build_snapshot_command(
            "ffmpeg",
            "rtsp://127.0.0.1:8554/baby",
            Path(r"C:\Captures\Pictures"),
            12,
        )
        joined = " ".join(str(item) for item in command)
        self.assertIn("fps=1/12", joined)
        self.assertIn("-atomic_writing 1", joined)
        self.assertTrue(command[-1].endswith(".jpg"))

    def test_image_metadata_contains_capture_identity_and_dimensions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "capture.jpg"
            Image.new("RGB", (320, 180), "navy").save(path, format="JPEG")
            write_image_metadata(path, "Philips Avent Baby Monitor")
            with Image.open(path) as image:
                exif = image.getexif()
                self.assertEqual(exif[270], "Philips Avent Baby Monitor")
                self.assertEqual(exif[271], "Philips Avent")
                self.assertEqual(exif[272], "SCD953/26")
                self.assertEqual(exif[40962], 320)
                self.assertEqual(exif[40963], 180)
                self.assertRegex(exif[36867], r"^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$")

    def test_manager_saves_writable_settings_without_starting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = CaptureManager(root / "settings.json", root / "capture.log")
            settings = manager.save_settings(
                {
                    "mode": "video",
                    "output_path": str(root / "output"),
                    "snapshot_interval": 10,
                    "auto_start": False,
                }
            )
            self.assertEqual(settings["mode"], "video")
            self.assertTrue((root / "output").is_dir())
            self.assertFalse(manager.running)

    def test_daily_settings_can_be_saved_independently(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = CaptureManager(root / "settings.json", root / "capture.log")
            manager.save_settings(
                {
                    "mode": "video",
                    "output_path": str(root / "output"),
                    "snapshot_interval": 10,
                    "auto_start": True,
                    "join_daily_videos": True,
                    "delete_fragments": False,
                }
            )
            settings = manager.save_daily_settings(
                {"join_daily_videos": False, "delete_fragments": True}
            )
            self.assertFalse(settings["join_daily_videos"])
            self.assertFalse(settings["delete_fragments"])
            self.assertEqual(settings["mode"], "video")

    def test_capture_process_exit_is_not_retried_in_a_tight_loop(self):
        manager = CaptureManager(Path("settings.json"), Path("capture.log"))
        manager._command = ["ffmpeg"]
        manager._output_directory = Path.cwd()
        manager._log_handle = Mock()
        manager._active = True
        process = Mock()
        process.poll.return_value = 1
        with patch("capture.subprocess.Popen", return_value=process) as popen:
            manager._supervisor_worker()
        popen.assert_called_once()
        self.assertFalse(manager._active)
        self.assertEqual(manager.last_error, "Capture stream ended with code 1")


if __name__ == "__main__":
    unittest.main()
