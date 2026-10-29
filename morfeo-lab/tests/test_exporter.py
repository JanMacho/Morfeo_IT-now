"""The live exporter that runs inside every generator pod (standard library only)."""
from __future__ import annotations

import importlib.util
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import synth

SPEC = importlib.util.spec_from_file_location(
    "jtl_exporter", Path(__file__).resolve().parent.parent / "images" / "generator" / "jtl_exporter.py")
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)

LABELS = r'\{(?:[^}"]|"[^"]*")*\}'   # a label value may contain braces: endpoint="GET /api/saldo/{id}"
LINE = re.compile(rf'^[a-z_]+({LABELS})? (-?[\d.]+(e[+-]?\d+)?|NaN)$')


class Taxonomy(unittest.TestCase):
    def test_classes(self):
        cases = [("200", True, "ok"), ("201", True, "ok"), ("400", False, "client_4xx"), ("404", False, "client_4xx"),
                 ("500", False, "server_5xx"), ("503", True, "server_5xx"), ("Non HTTP response code: X", False, "network"),
                 ("", False, "network"), ("200", False, "assertion")]
        for code, ok, expected in cases:
            self.assertEqual(exporter.classify(code, ok), expected, code)


class Stages(unittest.TestCase):
    def test_stages_are_read_when_the_first_sample_arrives(self):
        # the exporter starts with the pod; the lab sends the stages of the plan later
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "morfeo-meta.json"
            m = exporter.Metrics({}, {}, lambda: exporter.Meta(str(meta)).load().get("stages"))
            self.assertEqual(m.stage_of("pool 1-1"), "main")   # nothing known yet
            meta.write_text('{"stages": {"pool": "setup", "load": "main"}}', encoding="utf-8")
            self.assertEqual(m.stage_of("pool 1-1"), "setup")
            self.assertEqual(m.stage_of("load 1-3"), "main")

    def test_a_file_that_is_being_written_is_read_again_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "morfeo-meta.json"
            meta.write_text('{"stages": ', encoding="utf-8")
            reader = exporter.Meta(str(meta))
            self.assertEqual(reader.load(), {})
            meta.write_text('{"stages": {"a": "setup"}}', encoding="utf-8")
            self.assertEqual(reader.load(), {"stages": {"a": "setup"}})


class Tail(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="morfeo-exporter-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.written = synth.write_jtl(self.tmp)
        self.metrics = exporter.Metrics({"generator": "0", "scenario": "levels"}, {"10 users": "setup"})
        self.tailer = exporter.JtlTailer(str(self.tmp), self.metrics)
        for f in exporter.glob.glob(str(self.tmp / "**" / "kpi*.jtl"), recursive=True):
            self.tailer._consume(f, self.tailer.files.setdefault(f, {"pos": 0, "header": None, "partial": ""}))

    def total(self, text: str, stage: str) -> int:
        return sum(int(m.group(2)) for m in re.finditer(rf'morfeo_requests_total({LABELS}) (\d+)', text)
                   if f'stage="{stage}"' in m.group(1))

    def test_every_sample_is_counted_once_and_split_by_stage(self):
        text = self.metrics.render(1)
        self.assertEqual(self.total(text, "setup") + self.total(text, "main"), self.written)
        self.assertEqual(self.total(text, "setup"), 10 * synth.SECONDS)   # the group mapped to setUp

    def test_text_format(self):
        for line in self.metrics.render(1).splitlines():
            if line and not line.startswith("#"):
                self.assertRegex(line, LINE)

    def test_histogram_is_cumulative(self):
        text = self.metrics.render(1)
        counts = [int(m.group(2)) for m in re.finditer(rf'morfeo_response_time_seconds_bucket({LABELS}) (\d+)', text)
                  if 'stage="main"' in m.group(1) and 'endpoint="GET /api/saldo/{id}"' in m.group(1)]
        self.assertEqual(counts, sorted(counts))
        self.assertGreater(counts[-1], 0)

    def test_failures_are_visible_by_class(self):
        text = self.metrics.render(1)
        self.assertIn('result="server_5xx"', text)
        self.assertIn('result="client_4xx"', text)

    def test_a_partial_line_is_kept_for_the_next_read(self):
        extra = self.tmp / "generator-0" / "kpi-2.jtl"
        extra.write_text("timeStamp,elapsed,label,responseCode,responseMessage,threadName,success,allThreads,bytes\n"
                         "1000,5,A,200,OK,g 1-1,true,1,10\n1001,6,A,2", encoding="utf-8")
        state = {"pos": 0, "header": None, "partial": ""}
        before = sum(self.metrics.requests.values())
        self.tailer._consume(str(extra), state)
        self.assertEqual(sum(self.metrics.requests.values()), before + 1)
        extra.write_text(extra.read_text() + "00,OK,g 1-1,true,1,10\n", encoding="utf-8")
        self.tailer._consume(str(extra), state)
        self.assertEqual(sum(self.metrics.requests.values()), before + 2)

    def test_state_gauge(self):
        work = self.tmp / "work"
        work.mkdir()
        self.assertEqual(exporter.generator_state(str(work)), 0)
        (work / ".go").touch()
        self.assertEqual(exporter.generator_state(str(work)), 1)
        (work / ".done").touch()
        self.assertEqual(exporter.generator_state(str(work)), 2)


if __name__ == "__main__":
    unittest.main()
