import asyncio
import sys
import tempfile
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import app as monitor
from capture import CaptureManager, MAX_SCREENSHOT_BYTES


def jpeg(size=(320, 180), format="JPEG"):
    content = BytesIO()
    Image.new("RGB", size, "navy").save(content, format=format)
    return content.getvalue()


class ScreenshotStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manager = CaptureManager(self.root / "settings.json", self.root / "capture.log")
        self.manager.save_settings({
            "mode": "video", "output_path": str(self.root / "output"),
            "snapshot_interval": 10, "auto_start": True,
        })

    def test_unique_metadata_tagged_pictures_do_not_touch_active_recording(self):
        self.manager._active = True
        self.manager._supervisor_thread = Mock()
        self.manager._supervisor_thread.is_alive.return_value = True
        self.manager.process = process = Mock()
        self.manager.last_error = "existing diagnostic"
        original_settings = self.manager.settings_file.read_bytes()
        first = self.manager.save_screenshot(jpeg(), "Test camera")
        second = self.manager.save_screenshot(jpeg(), "Test camera")
        self.assertNotEqual(first["filename"], second["filename"])
        self.assertEqual(Path(first["path"]).parent, self.root / "output" / "Pictures")
        with Image.open(first["path"]) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (320, 180))
            exif = image.getexif()
            self.assertEqual(exif[270], "Test camera")
            self.assertEqual(exif[271], "Philips Avent")
            self.assertEqual(exif[40962], 320)
            self.assertEqual(exif[40963], 180)
            self.assertRegex(exif[36867], r"^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$")
            self.assertRegex(exif[36881], r"^[+-]\d{2}:\d{2}$")
        self.assertTrue(self.manager.running)
        self.assertIs(self.manager.process, process)
        self.assertEqual(self.manager.last_error, "existing diagnostic")
        self.assertEqual(self.manager.settings_file.read_bytes(), original_settings)
        process.assert_not_called()
        self.assertFalse(any(item.name.startswith(".") for item in Path(first["path"]).parent.iterdir()))

    def test_screenshot_works_when_automatic_capture_is_off(self):
        settings = self.manager.load_settings()
        settings["mode"] = "off"
        self.manager.save_settings(settings)
        result = self.manager.save_screenshot(jpeg(), "Camera")
        self.assertTrue(Path(result["path"]).is_file())
        self.assertFalse(self.manager.running)
        self.assertEqual(self.manager.load_settings()["mode"], "off")

    def test_capture_time_matches_filename_instead_of_network_file_timestamp(self):
        captured = datetime.fromisoformat("2026-10-16T23:03:44+02:00")
        with patch("capture.datetime") as clock:
            clock.now.return_value.astimezone.return_value = captured
            result = self.manager.save_screenshot(jpeg(), "Camera")
        self.assertTrue(result["filename"].startswith("Screenshot_2026-10-16_23-03-44_"))
        with Image.open(result["path"]) as image:
            self.assertEqual(image.getexif()[36867], "2026:10:16 23:03:44")
            self.assertEqual(image.getexif()[36881], "+02:00")

    def test_rejects_non_jpeg_corrupt_oversized_and_large_pixel_images_before_writing(self):
        for data in (b"", b"not an image", jpeg(format="PNG"), jpeg()[:100], b"x" * (MAX_SCREENSHOT_BYTES + 1)):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                self.manager.save_screenshot(data, "Camera")
        with patch("capture.MAX_SCREENSHOT_PIXELS", 100), self.assertRaises(ValueError):
            self.manager.save_screenshot(jpeg(), "Camera")
        self.assertFalse((self.root / "output" / "Pictures").exists())

    def test_failed_metadata_write_leaves_no_partial_picture(self):
        with patch("capture.write_image_metadata", side_effect=OSError("unavailable")), self.assertRaises(OSError):
            self.manager.save_screenshot(jpeg(), "Camera")
        self.assertEqual(list((self.root / "output" / "Pictures").iterdir()), [])


class ScreenshotEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.capture = Mock()
        self.capture.save_screenshot.return_value = {"filename": "Screenshot_test.jpg", "path": r"C:\Pictures\Screenshot_test.jpg"}
        self.secret_patch = patch.object(monitor, "load_secret", return_value={
            "cameras": [{"camera_id": "test", "camera_name": "Test camera", "product_id": "test"}]
        })
        self.capture_patch = patch.object(monitor, "CAPTURE", self.capture)
        self.feed_patch = patch.object(monitor.FEED, "status", return_value={"video_available": True})
        for item in (self.secret_patch, self.capture_patch, self.feed_patch):
            item.start()
            self.addCleanup(item.stop)
        application = web.Application(middlewares=[monitor.localhost_only], client_max_size=16 * 1024)
        application[monitor.SCREENSHOT_LOCK] = self.lock = asyncio.Lock()
        application.router.add_post("/api/screenshot", monitor.save_screenshot)
        self.client = TestClient(TestServer(application))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def post(self, body=None, **kwargs):
        return await self.client.post("/api/screenshot?camera=baby", data=jpeg() if body is None else body,
                                      headers=kwargs.pop("headers", {"Content-Type": "image/jpeg"}), **kwargs)

    async def test_accepts_jpeg_above_default_json_body_limit(self):
        body = jpeg() + b"\0" * 20000
        response = await self.post(body)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["filename"], "Screenshot_test.jpg")
        self.capture.save_screenshot.assert_called_once_with(body, "Test camera")

    async def test_rejects_foreign_origin_wrong_type_unknown_camera_and_offline_feed(self):
        response = await self.post(headers={"Content-Type": "image/jpeg", "Origin": "https://example.com"})
        self.assertEqual(response.status, 403)
        response = await self.post(headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status, 415)
        response = await self.client.post("/api/screenshot?camera=unknown", data=jpeg(), headers={"Content-Type": "image/jpeg"})
        self.assertEqual(response.status, 400)
        with patch.object(monitor.FEED, "status", return_value={"video_available": False}):
            response = await self.post()
            self.assertEqual(response.status, 409)
        self.capture.save_screenshot.assert_not_called()

    async def test_oversized_request_is_a_json_error(self):
        response = await self.post(BytesIO(b"x" * (MAX_SCREENSHOT_BYTES + 1)))
        self.assertEqual(response.status, 413)
        self.assertFalse((await response.json())["ok"])
        self.capture.save_screenshot.assert_not_called()

    async def test_busy_endpoint_rejects_duplicate_save(self):
        async with self.lock:
            response = await self.post()
            self.assertEqual(response.status, 409)
        self.capture.save_screenshot.assert_not_called()

    async def test_invalid_image_and_unavailable_destination_return_actionable_errors(self):
        self.capture.save_screenshot.side_effect = ValueError("Screenshot is not a readable JPEG")
        response = await self.post()
        self.assertEqual(response.status, 400)
        self.capture.save_screenshot.side_effect = OSError("unavailable")
        response = await self.post()
        self.assertEqual(response.status, 503)
        self.assertIn("output folder", (await response.json())["error"])


if __name__ == "__main__":
    unittest.main()
