"""Daily-join recovery uses disposable media only, never installed recordings."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SRC = Path(__file__).parents[1] / "src"
PWSH = shutil.which("pwsh")
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(PWSH, "PowerShell 7 required")
class MediaMetadataTests(unittest.TestCase):
    def test_missing_pixel_format_with_video_packets_names_the_fragment(self):
        with tempfile.TemporaryDirectory() as temporary:
            harness = Path(temporary) / "probe.ps1"
            harness.write_text(r'''
param($JoinScript, [switch]$EmptyPackets)
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($JoinScript, [ref]$tokens, [ref]$errors)
foreach ($definition in $ast.EndBlock.Statements) {
    if ($definition -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($definition.Extent.Text))
    }
}
function Invoke-NativeProcess {
    param($Executable, $Arguments, $TimeoutSeconds)
    $json = if ($Arguments -contains '-show_packets') {
        if ($EmptyPackets) { '{"packets":[]}' } else { '{"packets":[{"size":"100"}]}' }
    } else {
        '{"streams":[{"codec_type":"video","codec_name":"h264","width":320,"height":240},{"codec_type":"audio","codec_name":"pcm_mulaw","sample_rate":"8000","channels":1}],"format":{"duration":"1.0"}}'
    }
    [pscustomobject]@{ExitCode=0;Stdout=$json;Stderr=''}
}
try {
    Get-MediaInfo -Path 'BabyMonitor_2000-01-01_10-00-00.mkv' -Ffprobe 'mock' -AllowVideoEmpty | ConvertTo-Json -Compress
} catch {
    $_.Exception.Message
}
''', encoding="utf-8")
            result = subprocess.run([
                PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness),
                str(SRC / "Join-AventDailyVideos.ps1"),
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("BabyMonitor_2000-01-01_10-00-00.mkv", result.stdout)
            self.assertIn("video packets", result.stdout)
            self.assertNotIn("cannot be found on this object", result.stdout)
            empty = subprocess.run([
                PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness),
                str(SRC / "Join-AventDailyVideos.ps1"), "-EmptyPackets",
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(empty.returncode, 0, empty.stderr)
            self.assertTrue(json.loads(empty.stdout)["VideoEmpty"])


@unittest.skipUnless(PWSH and FFMPEG and FFPROBE, "PowerShell and FFmpeg required")
class DailyJoinRecoveryTests(unittest.TestCase):
    def generate(self, path, empty_video=False):
        args = [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:r=10:d=1",
            "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=1", "-t", "1",
        ]
        if empty_video:
            args += ["-vf", "select=0"]
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_mulaw", str(path)]
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def join(self, root):
        return subprocess.run([
            PWSH, "-NoProfile", "-NonInteractive", "-File",
            str(SRC / "Join-AventDailyVideos.ps1"), "-VideoRoot", str(root),
            "-AllPastDays", "-DeleteFragments",
        ], capture_output=True, text=True, timeout=60)

    def test_audio_only_fragment_is_preserved_and_valid_video_is_joined(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = root / "BabyMonitor_2000-01-01_10-00-00.mkv"
            empty = root / "BabyMonitor_2000-01-01_11-00-00.mkv"
            self.generate(valid)
            self.generate(empty, empty_video=True)
            original_hash = hashlib.sha256(empty.read_bytes()).hexdigest()
            result = self.join(root)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            output = root / "Daily" / "BabyMonitor_2000-01-01.mkv"
            manifest = json.loads(output.with_suffix(".json").read_text(encoding="utf-8-sig"))
            self.assertEqual(manifest["sourceCount"], 1)
            self.assertTrue(manifest["sourceFragmentsDeleted"])
            self.assertAlmostEqual(manifest["outputDurationSeconds"], 1, delta=0.2)
            records = manifest["preservedVideoEmptyFragments"]
            self.assertEqual(len(records), 1)
            preserved = root / records[0]["preservedFile"]
            self.assertEqual(hashlib.sha256(preserved.read_bytes()).hexdigest(), original_hash)
            self.assertFalse(empty.exists())
            self.assertFalse(valid.exists())
            self.assertIn(empty.name, result.stderr + result.stdout)
            manifest_bytes = output.with_suffix(".json").read_bytes()
            again = self.join(root)
            self.assertEqual(again.returncode, 0, again.stderr + again.stdout)
            self.assertEqual(output.with_suffix(".json").read_bytes(), manifest_bytes)

    def test_empty_day_preserves_audio_without_claiming_a_daily_video(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            empty = root / "BabyMonitor_2000-01-01_11-00-00.mkv"
            self.generate(empty, empty_video=True)
            original = empty.read_bytes()
            result = self.join(root)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("NoVideoFragments", result.stdout)
            self.assertFalse((root / "Daily" / "BabyMonitor_2000-01-01.mkv").exists())
            self.assertEqual((root / "Incomplete" / "2000-01-01" / empty.name).read_bytes(), original)

    def test_preservation_collision_does_not_overwrite_or_delete_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            empty = root / "BabyMonitor_2000-01-01_11-00-00.mkv"
            self.generate(empty, empty_video=True)
            original = empty.read_bytes()
            destination = root / "Incomplete" / "2000-01-01" / empty.name
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"existing preservation")
            result = self.join(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already exists", result.stderr + result.stdout)
            self.assertEqual(empty.read_bytes(), original)
            self.assertEqual(destination.read_bytes(), b"existing preservation")

    def test_preservation_record_survives_failed_join(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            empty = root / "BabyMonitor_2000-01-01_10-00-00.mkv"
            broken = root / "BabyMonitor_2000-01-01_11-00-00.mkv"
            self.generate(empty, empty_video=True)
            broken.write_bytes(b"invalid recording")
            first = self.join(root)
            self.assertNotEqual(first.returncode, 0)
            self.assertIn(broken.name, first.stderr + first.stdout)
            preserved = root / "Incomplete" / "2000-01-01" / empty.name
            original_hash = hashlib.sha256(preserved.read_bytes()).hexdigest()
            broken.unlink()
            self.generate(broken)
            retry = self.join(root)
            self.assertEqual(retry.returncode, 0, retry.stderr + retry.stdout)
            manifest = json.loads((root / "Daily" / "BabyMonitor_2000-01-01.json").read_text(encoding="utf-8-sig"))
            self.assertEqual(manifest["preservedVideoEmptyFragments"][0]["sha256"], original_hash)
            self.assertEqual(hashlib.sha256(preserved.read_bytes()).hexdigest(), original_hash)
