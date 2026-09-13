"""Exercise task wiring without registering tasks or touching real footage."""
import json
from datetime import date
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).parents[1] / "src"
PWSH = shutil.which("pwsh")


@unittest.skipUnless(PWSH, "PowerShell 7 required")
class TaskTests(unittest.TestCase):
    def run_ps(self, script, *args):
        return subprocess.run(
            [PWSH, "-NoProfile", "-NonInteractive", "-File", str(script), *args],
            capture_output=True, text=True, timeout=30,
        )

    def test_daily_task_has_independent_daily_and_logon_triggers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = root / "test.ps1"
            harness.write_text(r'''
param($Installer, $StateRoot)
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory) @{Execute=$Execute;Argument=$Argument} }
function New-ScheduledTaskTrigger { param([switch]$Daily,$At,[switch]$AtLogOn,$User) @{Daily=[bool]$Daily;Logon=[bool]$AtLogOn} }
function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel) @{} }
function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,[switch]$StartWhenAvailable,$ExecutionTimeLimit,$MultipleInstances,$RestartCount,$RestartInterval) @{Instances=$MultipleInstances;Retries=$RestartCount} }
function Register-ScheduledTask { param($TaskName,$Action,$Trigger,$Principal,$Settings,$Description,[switch]$Force) @{Action=$Action;Triggers=$Trigger;Settings=$Settings}|ConvertTo-Json -Depth 8|Set-Content (Join-Path $StateRoot 'task.json') }
& $Installer -StateRoot $StateRoot
''', encoding="utf-8")
            result = self.run_ps(harness, str(SRC / "Install-DailyJoinTask.ps1"), temporary)
            self.assertEqual(result.returncode, 0, result.stderr)
            task = json.loads((root / "task.json").read_text(encoding="utf-8-sig"))
            self.assertTrue(task["Action"]["Execute"].endswith("wscript.exe"))
            self.assertTrue(any(t["Daily"] for t in task["Triggers"]))
            self.assertTrue(any(t["Logon"] for t in task["Triggers"]))
            self.assertEqual(task["Settings"]["Instances"], "IgnoreNew")
            self.assertEqual(task["Settings"]["Retries"], 8)
            launcher = (root / "tasks" / "daily-join.vbs").read_text(encoding="utf-16")
            self.assertIn("Invoke-DailyJoin.ps1", launcher)
            self.assertIn("0, True", launcher)
            self.assertNotIn("_personal_productivity", launcher)
            # Execute the generated VBScript too: valid task metadata alone
            # cannot catch quoting errors at the Windows Script Host boundary.
            (root / "data").mkdir()
            (root / "data" / "capture-settings.json").write_text(
                json.dumps({"join_daily_videos": False}), encoding="utf-8",
            )
            launched = subprocess.run([
                "cscript.exe", "//Nologo", str(root / "tasks" / "daily-join.vbs"),
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(launched.returncode, 0, launched.stderr + launched.stdout)
            self.assertIn("disabled", (root / "logs" / "daily-join.log").read_text(encoding="utf-8-sig"))

    def test_wrapper_honors_disabled_join_and_propagates_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            settings = root / "data" / "capture-settings.json"
            settings.write_text(json.dumps({"join_daily_videos": False}), encoding="utf-8")
            result = self.run_ps(SRC / "Invoke-DailyJoin.ps1", "-StateRoot", temporary)
            self.assertEqual(result.returncode, 0, result.stderr)
            log = (root / "logs" / "daily-join.log").read_text(encoding="utf-8-sig")
            self.assertIn("disabled", log)
            settings.write_text("{", encoding="utf-8")
            result = self.run_ps(SRC / "Invoke-DailyJoin.ps1", "-StateRoot", temporary)
            self.assertNotEqual(result.returncode, 0)

    def test_join_refuses_concurrent_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = root / "lock.ps1"
            harness.write_text(r'''
param($JoinScript, $Root)
$lock = [IO.File]::Open((Join-Path $Root '.avent-daily-join.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try { & (Get-Command pwsh).Source -NoProfile -File $JoinScript -VideoRoot $Root -AllPastDays; exit $LASTEXITCODE }
finally { $lock.Dispose() }
''', encoding="utf-8")
            result = self.run_ps(harness, str(SRC / "Join-AventDailyVideos.ps1"), temporary)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Cannot acquire daily-join ownership", result.stderr)
            self.assertFalse((root / "Daily").exists())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/FFprobe required")
    def test_missed_days_join_validate_delete_and_exclude_today(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "BabyMonitor_2000-01-01_10-00-00.mkv"
            generated = subprocess.run([
                shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:r=10",
                "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_mulaw", str(first),
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            for name in ("BabyMonitor_2000-01-01_11-00-00.mkv", "BabyMonitor_2000-01-02_10-00-00.mkv"):
                shutil.copyfile(first, root / name)
            today = root / f"BabyMonitor_{date.today().isoformat()}_10-00-00.mkv"
            shutil.copyfile(first, today)
            arguments = ("-VideoRoot", temporary, "-AllPastDays", "-DeleteFragments")
            result = self.run_ps(SRC / "Join-AventDailyVideos.ps1", *arguments)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            for day, count in (("2000-01-01", 2), ("2000-01-02", 1)):
                output = root / "Daily" / f"BabyMonitor_{day}.mkv"
                self.assertGreater(output.stat().st_size, 0)
                manifest = json.loads(output.with_suffix(".json").read_text(encoding="utf-8-sig"))
                self.assertEqual(manifest["sourceCount"], count)
                self.assertTrue(manifest["sourceFragmentsDeleted"])
                self.assertAlmostEqual(manifest["outputDurationSeconds"], count * 2, delta=0.5)
            self.assertEqual(list(root.glob("BabyMonitor_*.mkv")), [today])
            result = self.run_ps(SRC / "Join-AventDailyVideos.ps1", *arguments)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(today.exists())
