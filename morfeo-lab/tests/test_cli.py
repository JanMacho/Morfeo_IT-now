"""The command line: help, errors, `validate` (text and JSON for CI), `report` and `compare`."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import jsonschema

from morfeo_lab import __version__, cli
from morfeo_lab.run.outputs import finalize
from tests import jmxkit as k
from tests import synth

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ai-plan.jmx"


def invoke(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:   # argparse exits by itself on usage errors
            code = int(e.code or 0)
    return code, out.getvalue(), err.getvalue()


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="morfeo-cli-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class Basics(unittest.TestCase):
    def test_help_lists_every_command(self):
        code, out, _ = invoke()
        self.assertEqual(code, 0)
        for command in ("doctor", "up", "status", "dashboard", "down", "deploy", "validate", "run", "open", "report",
                        "compare"):
            self.assertIn(f"  {command}", out)

    def test_version(self):
        code, out, _ = invoke("--version")
        self.assertEqual(code, 0)
        self.assertIn(__version__, out)

    def test_unknown_command(self):
        code, out, err = invoke("nothing")
        self.assertEqual(code, 2)
        self.assertIn("Unknown command: nothing", out + err)

    def test_unknown_option_is_explained_in_plain_words(self):
        code, out, err = invoke("validate", "--nope")
        self.assertEqual(code, 2)
        self.assertIn("Unknown option", out + err)

    def test_generators_must_be_a_number(self):
        code, out, err = invoke("validate", str(FIXTURE), "--generators", "0")
        self.assertEqual(code, 2)
        self.assertIn("--generators", out + err)

    def test_invalid_configuration_is_reported(self):
        bad = Path(tempfile.mkdtemp()) / "lab.yaml"
        bad.write_text("cluster:\n  provder: kind\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, bad.parent, ignore_errors=True)
        code, out, err = invoke("--config", str(bad), "status")
        self.assertEqual(code, 2)
        self.assertIn("did you mean", out + err)


class Validate(Folder):
    def test_valid_plan(self):
        code, out, _ = invoke("validate", str(FIXTURE))
        self.assertEqual(code, 0)
        self.assertIn("ai-plan.jmx", out)
        self.assertIn("1 plan valid", out)

    def test_show(self):
        code, out, _ = invoke("validate", str(FIXTURE), "--show")
        self.assertEqual(code, 0)
        self.assertIn("Thread group", out)
        self.assertIn("Load division", out)

    def test_invalid_plan_explains_why(self):
        (self.tmp / "broken.jmx").write_text("<jmeterTestPlan><nope/>", encoding="utf-8")
        code, out, err = invoke("validate", str(self.tmp / "broken.jmx"))
        self.assertEqual(code, 1)
        self.assertIn("broken.jmx is not valid", out + err)

    def test_a_plan_that_needs_a_plugin_says_how_to_add_it(self):
        plan = k.plan("p", [k.thread_group("g", 1, 1, 10, [k.http("A", "/a")]), k.plugin_element(
            "kg.apc.jmeter.threads.UltimateThreadGroup", "Ultimate")])
        (self.tmp / "p.jmx").write_text(plan, encoding="utf-8")
        lab = self.tmp / "lab.yaml"
        lab.write_text("generators:\n  jmeter_plugins: []\n", encoding="utf-8")
        code, out, err = invoke("--config", str(lab), "validate", str(self.tmp / "p.jmx"))
        self.assertEqual(code, 1)
        self.assertIn("jpgc-casutg", out + err)
        self.assertIn("generators.jmeter_plugins", out + err)

    def test_no_plans_to_validate(self):
        code, out, err = invoke("validate", str(self.tmp))
        self.assertEqual(code, 2)
        self.assertIn("no scenarios", (out + err).lower())

    def test_json_for_ci(self):
        (self.tmp / "broken.jmx").write_text("not xml", encoding="utf-8")
        code, out, _ = invoke("validate", str(FIXTURE), str(self.tmp / "broken.jmx"), "--json")
        self.assertEqual(code, 1)
        result = json.loads(out)   # stdout is only JSON
        self.assertEqual(result["schema_version"], 1)
        self.assertFalse(result["valid"])
        good, bad = result["scenarios"]
        self.assertTrue(good["valid"])
        self.assertEqual(good["distribution"]["mode"], "single")
        self.assertIn("POST /api/ingreso", good["endpoints"])
        self.assertEqual(good["errors"], [])
        self.assertFalse(bad["valid"])
        self.assertTrue(bad["errors"])

    def test_json_when_nothing_is_found(self):
        code, out, _ = invoke("validate", str(self.tmp), "--json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["valid"])


class Results(Folder):
    def make_run(self, run_id: str, levels: int = 4) -> Path:
        run_dir = self.tmp / "results" / run_id
        s = synth.scenario_of(self.tmp)
        folder = run_dir / "levels"
        synth.write_jtl(folder / "raw")
        if levels < 4:
            jtl = folder / "raw" / "generator-0" / "kpi.jtl"
            lines = jtl.read_text("utf-8").splitlines()
            keep = [ln for ln in lines[1:] if int(ln.split(",")[7]) in synth.LEVELS[:levels]]
            jtl.write_text("\n".join([lines[0], *keep]) + "\n", encoding="utf-8")
        finalize(folder, synth.meta_of(s, run_id=run_id), {}, quiet=True)
        return run_dir

    def test_report_rebuilds_the_pages_from_the_raw_data(self):
        run_dir = self.make_run("r1")
        summary = run_dir / "levels" / "summary.json"
        summary.unlink()
        code, _, _ = invoke("report", str(run_dir))
        self.assertEqual(code, 0)
        self.assertTrue(summary.exists())
        self.assertTrue((run_dir / "index.html").exists())

    def test_report_with_the_current_objectives(self):
        run_dir = self.make_run("r1", levels=2)
        self.assertEqual(json.loads((run_dir / "levels" / "summary.json").read_text())["verdict"], "PASS")
        app = self.tmp / "strict.yaml"
        app.write_text("name: strict\nartifact: {image: x}\nslo: {p95_ms: 1}\n", encoding="utf-8")
        code, _, _ = invoke("report", str(run_dir), "--application", str(app))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((run_dir / "levels" / "summary.json").read_text())["verdict"], "FAIL")

    def test_report_needs_a_folder(self):
        code, out, err = invoke("report")
        self.assertEqual(code, 2)
        self.assertIn("results folder", out + err)

    def test_compare(self):
        a, b = self.make_run("20251014-100000", levels=2), self.make_run("20251014-110000")
        code, out, _ = invoke("compare", str(a), str(b), "--names", "before,after")
        self.assertEqual(code, 0)
        self.assertIn("Comparison", out)

    def test_compare_names_must_match(self):
        a, b = self.make_run("20251014-100000"), self.make_run("20251014-110000")
        code, out, err = invoke("compare", str(a), str(b), "--names", "only-one")
        self.assertEqual(code, 2)
        self.assertIn("--names", out + err)

    def test_summary_files_follow_the_schema(self):
        run_dir = self.make_run("r1")
        schema = json.loads((Path(__file__).resolve().parent.parent / "docs" / "summary.schema.json").read_text("utf-8"))
        jsonschema.Draft202012Validator(schema).validate(json.loads((run_dir / "levels" / "summary.json").read_text()))


if __name__ == "__main__":
    unittest.main()
