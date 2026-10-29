"""Analysis of a run: classification, load levels, checks, diagnosis, summary.json and its schema."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import jsonschema
import pandas as pd

from morfeo_lab import analysis
from morfeo_lab.analysis import capacity, jtl
from morfeo_lab.analysis.summary import SCHEMA_VERSION, endpoint_key
from morfeo_lab.analysis.welch import welch
from morfeo_lab.run.outputs import finalize
from tests import synth

SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "docs" / "summary.schema.json").read_text("utf-8"))


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="morfeo-analysis-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class Classification(unittest.TestCase):
    def test_taxonomy(self):
        code = pd.Series(["200", "201", "400", "404", "500", "503", "", "Non HTTP response code: Timeout", "200"])
        success = pd.Series([True, True, False, False, False, False, False, False, False])
        self.assertEqual(list(jtl.classify(code, success)),
                         ["ok", "ok", "client_4xx", "client_4xx", "server_5xx", "server_5xx", "network", "network",
                          "assertion"])

    def test_5xx_is_a_failure_even_when_the_plan_accepted_it(self):
        self.assertEqual(jtl.classify(pd.Series(["503"]), pd.Series([True])).iloc[0], "server_5xx")


class Levels(Folder):
    def setUp(self):
        super().setUp()
        self.s = synth.scenario_of(self.tmp)
        synth.write_jtl(self.tmp / "raw")
        self.df = analysis.load_jtl(self.tmp / "raw", {g.name: g.role for g in self.s.plan.groups})

    def test_stages_and_columns(self):
        self.assertEqual(set(self.df["stage"].unique()), {"main"})
        self.assertEqual(list(self.df["ts"]), sorted(self.df["ts"]))
        self.assertEqual(set(self.df["cls"].unique()), {"ok", "client_4xx", "server_5xx"})

    def test_capacity_by_thread_group(self):
        cap = capacity.analyze(self.df, float(self.df["ts"].min()), synth.SLO, self.s.info(), self.s.plan.timeline())
        self.assertTrue(cap["available"])
        self.assertEqual(cap["basis"], "thread_group")
        self.assertEqual([r["load"] for r in cap["levels"]], [10, 20, 40, 80])
        self.assertEqual(cap["sustainable"]["load"], 40)
        self.assertEqual(cap["breaking"]["load"], 80)
        self.assertFalse(cap["levels"][-1]["within_slo"])

    def test_capacity_needs_enough_samples(self):
        cap = capacity.analyze(self.df.head(20), 0.0, synth.SLO, self.s.info(), [])
        self.assertFalse(cap["available"])


class Summary(Folder):
    def setUp(self):
        super().setUp()
        self.s = synth.scenario_of(self.tmp)
        synth.write_jtl(self.tmp / "raw")
        self.meta = synth.meta_of(self.s)

    def finalize(self, snapshot=None, **over):
        self.meta.update(over)
        return finalize(self.tmp, self.meta, snapshot if snapshot is not None else {}, quiet=True)

    def test_matches_the_published_schema(self):
        sm = self.finalize(synth.snapshot(synth.START, synth.START + 160))
        jsonschema.Draft202012Validator(SCHEMA).validate(sm)
        self.assertEqual(sm["schema_version"], SCHEMA_VERSION)
        self.assertEqual(sm["producer"], "morfeo-lab")

    def test_schema_is_itself_valid(self):
        jsonschema.Draft202012Validator.check_schema(SCHEMA)

    def test_verdict_and_rates(self):
        sm = self.finalize()
        self.assertEqual(sm["verdict"], "FAIL")   # the highest level breaks the objectives
        self.assertGreater(sm["overall"]["server_error_rate"], 0)
        self.assertGreater(sm["overall"]["error_rate"], 0)   # the 400s of the POSTs: client behaviour
        self.assertLessEqual(sm["overall"]["server_error_rate"], 1)
        keys = {e["key"] for e in sm["endpoints"]}
        self.assertEqual(keys, {"GET /api/saldo/{id}", "POST /api/ingreso"})

    def test_client_errors_do_not_fail_the_run(self):
        self._write_levels(2)   # no failures at the first two levels: the 4xx stay reported and the run passes
        sm = self.finalize()
        self.assertEqual(sm["verdict"], "PASS")
        self.assertGreater(sm["overall"]["by_class"]["client_4xx"], 0)
        self.assertEqual(sm["overall"]["failed_requests"], 0)

    def _write_levels(self, count: int):
        """Keep only the first `count` levels in the result file."""
        shutil.rmtree(self.tmp / "raw", ignore_errors=True)
        synth.write_jtl(self.tmp / "raw")
        f = self.tmp / "raw" / "generator-0" / "kpi.jtl"
        lines = f.read_text("utf-8").splitlines()
        keep = [ln for ln in lines[1:] if int(ln.split(",")[7]) in synth.LEVELS[:count]]
        f.write_text("\n".join([lines[0], *keep]) + "\n", encoding="utf-8")

    def test_restart_fails_the_run(self):
        self._write_levels(2)
        sm = self.finalize(pods={"replicas": 1, "restarts_during_run": 1, "last_termination_reason": "OOMKilled",
                                 "after": []})
        self.assertEqual(sm["verdict"], "FAIL")
        self.assertIn("crash", [d["code"] for d in sm["diagnosis"]])

    def test_throttling_is_diagnosed(self):
        self._write_levels(2)
        sm = self.finalize(synth.snapshot(synth.START, synth.START + 160, throttled=0.6))
        self.assertIn("cpu_throttled", [d["code"] for d in sm["diagnosis"]])

    def test_no_samples_is_a_failure_not_a_crash(self):
        shutil.rmtree(self.tmp / "raw")
        sm = self.finalize()
        self.assertEqual(sm["verdict"], "FAIL")
        self.assertEqual(sm["overall"]["requests"], 0)
        jsonschema.Draft202012Validator(SCHEMA).validate(sm)

    def test_output_files(self):
        self.finalize()
        for name in ("summary.json", "meta.json", "endpoints.csv", "junit.xml", "timeseries.csv", "app-metrics.json"):
            self.assertTrue((self.tmp / name).exists(), name)

    def test_generators_are_merged(self):
        synth.write_jtl(self.tmp / "raw", seed=8, generator=1)
        one = analysis.load_jtl(self.tmp / "raw")
        self.assertEqual(set(one["generator"].unique()), {0, 1})


class Keys(unittest.TestCase):
    def test_endpoint_key(self):
        self.assertEqual(endpoint_key("GET /api/saldo/{id}", None, None), "GET /api/saldo/{id}")
        self.assertEqual(endpoint_key("saldo", ["POST"], "POST /api/x"), "POST /api/x")
        self.assertEqual(endpoint_key("/api/x", ["PUT"], None), "PUT /api/x")
        self.assertEqual(endpoint_key("/api/x", ["GET", "POST"], None), "ANY /api/x")


class Welch(unittest.TestCase):
    def test_equal_samples_are_not_different(self):
        self.assertGreater(welch([10, 11, 10.5, 10.2, 10.8], [10.1, 10.9, 10.4, 10.6, 10.3]), 0.3)

    def test_separated_samples_are_different(self):
        self.assertLess(welch([10, 11, 10.5, 10.2, 10.8], [20, 21, 20.5, 20.2, 20.8]), 0.001)

    def test_too_few_samples(self):
        self.assertIsNone(welch([1.0], [2.0, 3.0]))


if __name__ == "__main__":
    unittest.main()
