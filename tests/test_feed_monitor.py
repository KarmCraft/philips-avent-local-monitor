import unittest
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from feed_monitor import FeedMonitor, FeedProbeResult


class FakeCapture:
    def __init__(self, *, auto_start=True):
        self.settings = {
            "mode": "video",
            "output_path": r"C:\Captures",
            "snapshot_interval": 10,
            "auto_start": auto_start,
        }
        self.running = False
        self.last_error = None
        self.starts = 0
        self.stops = 0

    def load_settings(self):
        return dict(self.settings)

    def start(self, alias, camera_name):
        self.starts += 1
        self.running = True

    def stop(self):
        self.stops += 1
        self.running = False


class FeedMonitorTests(unittest.TestCase):
    def test_offline_feed_does_not_start_capture(self):
        capture = FakeCapture()
        monitor = FeedMonitor(
            capture,
            lambda alias: FeedProbeResult(False, "unit offline"),
            offline_interval=15,
        )
        monitor._alias = "baby"
        delay = monitor._cycle()
        self.assertEqual(delay, 15)
        self.assertEqual(monitor.status()["state"], "offline")
        self.assertFalse(capture.running)
        self.assertEqual(capture.starts, 0)
        self.assertEqual(monitor.consecutive_probe_failures, 1)

    def test_probe_failure_counter_resets_when_video_returns(self):
        capture = FakeCapture(auto_start=False)
        results = iter(
            [
                FeedProbeResult(False, "offline"),
                FeedProbeResult(False, "offline"),
                FeedProbeResult(True, "packet"),
            ]
        )
        monitor = FeedMonitor(capture, lambda alias: next(results))
        monitor._alias = "baby"
        monitor._cycle()
        monitor._cycle()
        self.assertEqual(monitor.consecutive_probe_failures, 2)
        monitor._cycle()
        self.assertEqual(monitor.consecutive_probe_failures, 0)

    def test_online_feed_starts_armed_capture(self):
        capture = FakeCapture(auto_start=True)
        monitor = FeedMonitor(capture, lambda alias: FeedProbeResult(True, "packet"))
        monitor._alias = "baby"
        monitor._camera_name = "Baby"
        delay = monitor._cycle()
        self.assertEqual(delay, 1)
        self.assertEqual(monitor.status()["state"], "recording")
        self.assertTrue(capture.running)
        self.assertEqual(capture.starts, 1)

    def test_feed_loss_stops_then_restarts_after_new_packet(self):
        capture = FakeCapture(auto_start=True)
        results = iter(
            [FeedProbeResult(True, "first packet"), FeedProbeResult(True, "returned")]
        )
        monitor = FeedMonitor(capture, lambda alias: next(results))
        monitor._alias = "baby"
        monitor._cycle()
        capture.running = False
        capture.last_error = "stream ended"
        self.assertEqual(monitor._cycle(), 2)
        self.assertEqual(monitor.status()["state"], "offline")
        self.assertEqual(monitor._cycle(), 1)
        self.assertEqual(monitor.status()["state"], "recording")
        self.assertEqual(capture.starts, 2)

    def test_stop_capture_pauses_automatic_restart(self):
        capture = FakeCapture(auto_start=True)
        monitor = FeedMonitor(capture, lambda alias: FeedProbeResult(True, "packet"))
        monitor._alias = "baby"
        monitor._cycle()
        monitor.request_capture(False)
        monitor._cycle()
        monitor._cycle()
        self.assertFalse(monitor.status()["capture_armed"])
        self.assertEqual(monitor.status()["state"], "online")
        self.assertEqual(capture.starts, 1)

    def test_manual_capture_can_arm_when_auto_start_is_disabled(self):
        capture = FakeCapture(auto_start=False)
        monitor = FeedMonitor(capture, lambda alias: FeedProbeResult(True, "packet"))
        monitor._alias = "baby"
        monitor.request_capture(True)
        monitor._cycle()
        self.assertTrue(monitor.status()["capture_armed"])
        self.assertEqual(monitor.status()["state"], "recording")


if __name__ == "__main__":
    unittest.main()
