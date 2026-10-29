"""Subprocess helpers (timeouts), the watch over generator pods and the removal of private values."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from morfeo_lab import shell
from morfeo_lab.errors import LabError
from morfeo_lab.run import collect, generators

SLEEP = [sys.executable, "-c", "import time; time.sleep(5)"]


class Timeouts(unittest.TestCase):
    def test_a_timeout_is_an_error_unless_the_caller_checks_the_exit_code_itself(self):
        with self.assertRaises(LabError):
            shell.run(SLEEP, timeout=0.3, capture=True)
        self.assertEqual(shell.run(SLEEP, check=False, timeout=0.3, capture=True).returncode, 124)
        self.assertEqual(shell.out(SLEEP, check=False, timeout=0.3), "")
        self.assertEqual(shell.run_bytes(SLEEP, check=False, timeout=0.3), b"")

    def test_a_silent_command_still_hits_its_deadline(self):
        with self.assertRaises(LabError) as cm:
            shell.run_streaming(SLEEP, timeout=0.3)
        self.assertIn("timed out", str(cm.exception))

    def test_streaming_returns_the_last_lines(self):
        res = shell.run_streaming([sys.executable, "-c", "print('a'); print('b')"], timeout=20)
        self.assertEqual((res.returncode, res.stdout), (0, "a\nb"))


def pod(name: str, phase: str) -> dict:
    return {"metadata": {"name": name}, "status": {"phase": phase}}


class Roster(unittest.TestCase):
    def roster(self, answers):
        kube = mock.Mock()
        kube.try_json.side_effect = answers
        return generators.Roster(kube, "ns", "job", {"g0", "g1"})

    def test_failed_pod_is_noticed_at_once(self):
        r = self.roster([{"items": [pod("g0", "Running"), pod("g1", "Failed")]}])
        with self.assertRaises(LabError) as cm:
            r.check()
        self.assertIn("g1", str(cm.exception))

    def test_a_lost_answer_is_not_a_missing_pod(self):
        r = self.roster([None, None, None, None])
        for _ in range(4):
            r.check()

    def test_a_pod_missing_three_times_in_a_row_is_gone(self):
        ok = {"items": [pod("g0", "Running"), pod("g1", "Running")]}
        gone = {"items": [pod("g0", "Running")]}
        r = self.roster([gone, gone, ok, gone, gone, gone])
        for _ in range(5):
            r.check()   # the third miss was interrupted by a sighting, so it starts again
        with self.assertRaises(LabError):
            r.check()


class Redaction(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="morfeo-redact-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_private_values_leave_text_files_and_result_files(self):
        (self.tmp / "g").mkdir()
        (self.tmp / "g" / "kpi.jtl").write_text("1,GET,http://app/x?api_key=SECRET-KEY-1,200\n2,GET,http://app/y,200\n")
        (self.tmp / "g" / "jmeter.log").write_text("using SECRET-KEY-1 and hunter2222\n")
        collect.redact(self.tmp, ["SECRET-KEY-1", "hunter2222"])
        self.assertNotIn("SECRET-KEY-1", (self.tmp / "g" / "kpi.jtl").read_text())
        self.assertIn("http://app/y", (self.tmp / "g" / "kpi.jtl").read_text())
        self.assertEqual((self.tmp / "g" / "jmeter.log").read_text(), "using *** and ***\n")
        self.assertEqual([p.name for p in (self.tmp / "g").iterdir() if p.suffix == ".redacted"], [])


if __name__ == "__main__":
    unittest.main()
