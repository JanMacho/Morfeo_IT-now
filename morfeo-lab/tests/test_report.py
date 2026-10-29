"""The result pages: the verdict and its reason come first, nothing is loaded from the network."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from morfeo_lab import report
from morfeo_lab.run import run_summaries
from morfeo_lab.run.outputs import finalize
from tests import synth


class Pages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="morfeo-report-"))
        cls.run_dir = cls.tmp / "20251014-120000"
        folder = cls.run_dir / "levels"
        s = synth.scenario_of(cls.tmp)
        synth.write_jtl(folder / "raw")
        meta = synth.meta_of(s)
        (folder / "app").mkdir()
        (folder / "app" / "traffic.jsonl").write_bytes(synth.traffic_log())
        cls.summary = finalize(folder, meta, synth.snapshot(synth.START, synth.START + 160), quiet=True)
        cls.passing = cls.make_passing(cls.tmp / "second", s)
        report.write_run_index(cls.run_dir / "index.html", "20251014-120000", "demo", run_summaries(cls.run_dir))
        cls.page = (cls.run_dir / "index.html").read_text(encoding="utf-8")
        cls.run_page = cls.page

    @classmethod
    def make_passing(cls, folder: Path, s) -> dict:
        synth.write_jtl(folder / "raw", seed=3)
        jtl = folder / "raw" / "generator-0" / "kpi.jtl"
        lines = jtl.read_text("utf-8").splitlines()
        jtl.write_text("\n".join([lines[0], *[ln for ln in lines[1:] if int(ln.split(",")[7]) <= 20]]) + "\n", "utf-8")
        return finalize(folder, synth.meta_of(s, run_id="20251014-130000"), {}, quiet=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_verdict_comes_first(self):
        body = self.page[self.page.index("<body"):]
        self.assertLess(body.index("Failed"), body.index("Endpoints") if "Endpoints" in body else len(body))

    def test_the_reason_is_explained(self):
        self.assertIn("Why it failed", self.page)

    def test_a_passing_run_says_so(self):
        folder = self.tmp / "second"
        report.write_run_index(folder / "index.html", "20251014-130000", "demo", [self.passing])
        page = (folder / "index.html").read_text("utf-8")
        self.assertIn("acceptance criteria were met", page)
        self.assertEqual(self.passing["verdict"], "PASS")

    def test_nothing_is_loaded_from_the_network(self):
        urls = re.findall(r'(?:src|href)="(https?://[^"]+)"', self.page)
        self.assertEqual(urls, [])
        self.assertNotIn("@import", self.page)

    def test_charts_are_inline_and_the_only_script_is_the_theme_switch(self):
        self.assertIn("<svg", self.page)
        self.assertEqual(self.page.count("<script"), 1)
        self.assertNotIn("<script src", self.page)

    def test_endpoints_and_errors_are_listed(self):
        self.assertIn("GET /api/saldo/{id}", self.page)
        self.assertIn("503", self.page)

    def test_an_error_entry_is_shown(self):
        error = {"run_id": "r", "scenario": {"name": "broken", "file": "broken.jmx"}, "verdict": "ERROR",
                 "error": "The plan could not be read"}
        out = self.tmp / "err" / "index.html"
        out.parent.mkdir()
        report.write_run_index(out, "r", "demo", [error])
        text = out.read_text("utf-8")
        self.assertIn("The plan could not be read", text)

    def test_history_lists_the_runs(self):
        results = self.tmp / "results"
        for run_id, sm in (("20251014-120000", self.summary), ("20251014-130000", self.passing)):
            folder = results / run_id / "levels"
            folder.mkdir(parents=True)
            (folder / "summary.json").write_text(json.dumps(sm, default=str), encoding="utf-8")
        report.write_history(results)
        text = (results / "index.html").read_text("utf-8")
        self.assertIn("20251014-120000", text)
        self.assertIn("20251014-130000", text)

    def test_comparison(self):
        out = self.tmp / "compare.html"
        report.write_compare(out, [self.passing, self.summary], ["before", "after"])
        text = out.read_text("utf-8")
        self.assertIn("before (baseline)", text)
        self.assertIn("after", text)
        self.assertIn("Successful requests/s", text)


if __name__ == "__main__":
    unittest.main()
