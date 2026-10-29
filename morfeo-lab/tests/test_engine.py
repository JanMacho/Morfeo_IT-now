"""Tests against a real JMeter (they are skipped when JMeter is not installed).

They take minutes, so they run only when JMETER_HOME points at an Apache JMeter 5.6.3 folder that has the plugins
of images/generator/plugins.json in lib/ext. They check the promise that matters most: the total load does not
depend on the number of generators."""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from morfeo_lab import scenario
from tests import jmxkit as k

PLUGINS = ["jpgc-casutg", "jpgc-tst", "jpgc-functions", "jpgc-dummy"]


def find_jmeter() -> str | None:
    home = os.environ.get("JMETER_HOME")
    return str(Path(home) / "bin" / "jmeter") if home and (Path(home) / "bin" / "jmeter").exists() else None


JMETER = find_jmeter()


class _Counter(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@unittest.skipUnless(JMETER, "JMeter is not installed (set JMETER_HOME)")
class TestRealJMeter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Counter)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="morfeo-engine-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def run_plan(self, children, generators: int) -> int:
        """Run the plan on `generators` JMeter processes at once, as the lab does, and count the samples."""
        plan = self.tmp / "plan.jmx"
        plan.write_text(k.plan("Load", [k.defaults("localhost", "8080"), *children]), encoding="utf-8")
        s = scenario.load_scenario(plan, installed_plugins=PLUGINS)
        bundle = self.tmp / f"bundle{generators}"
        scenario.compile_bundle(s, bundle, app_url=self.url, generators=generators)
        env = {**os.environ, "HEAP": "-Xms128m -Xmx256m"}
        procs = [subprocess.Popen(
            [JMETER, "-n", "-t", f"plan-{i}.jmx", "-l", f"kpi{i}.jtl", "-j", f"jmeter{i}.log",
             "-q", "morfeo.properties", f"-Jmorfeo_generator={i}", f"-Jmorfeo_generators={generators}"],
            cwd=bundle, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for i in range(generators)]
        for p in procs:
            self.assertEqual(p.wait(timeout=120), 0)
        total = 0
        for i in range(generators):
            with open(bundle / f"kpi{i}.jtl", newline="") as fh:
                total += sum(1 for _ in csv.DictReader(fh))
        return total

    def assert_same_total(self, children, expected: float, tolerance: float = 0.2, generators: int = 3):
        one = self.run_plan(children, 1)
        many = self.run_plan(children, generators)
        self.assertAlmostEqual(one / expected, 1.0, delta=tolerance, msg=f"1 generator: {one}, expected {expected}")
        self.assertAlmostEqual(many / expected, 1.0, delta=tolerance,
                               msg=f"{generators} generators: {many}, expected {expected}")

    def test_closed_model_with_a_constant_throughput_timer(self):
        # 600 requests per minute for all threads = 10 req/s for 14 s
        self.assert_same_total([k.thread_group("Users", 6, 0, 14, [k.constant_throughput(600, 1),
                                                                     k.http("GET /x", "/x")])], 140)

    def test_per_thread_throughput(self):
        # 60 requests per minute per thread, 6 threads: 6 req/s for 14 s
        self.assert_same_total([k.thread_group("Users", 6, 0, 14, [k.constant_throughput(60, 0),
                                                                     k.http("GET /x", "/x")])], 84)

    def test_arrivals_thread_group(self):
        self.assert_same_total([k.arrivals("Partner", 20, 0, 10, limit=60, children=[k.http("GET /x", "/x")])], 200)

    def test_concurrency_thread_group_with_a_throughput_shaping_timer(self):
        # 10 req/s for 12 s
        self.assert_same_total([k.concurrency("Steps", 6, 0, 12, children=[
            k.shaping_timer([(10, 10, 12)]), k.http("GET /x", "/x")])], 120)

    def test_open_model_thread_group(self):
        self.assert_same_total([k.open_model("Open", "rate(20/s) random_arrivals(10 s)",
                                             [k.http("GET /x", "/x")])], 200, tolerance=0.25)

    def test_ultimate_thread_group(self):
        # 6 threads held for 10 s, each one waiting 250 ms between requests: about 24 req/s
        self.assert_same_total([k.ultimate("Spike", [(6, 0, 0, 10, 0)], [k.http("GET /x", "/x"), k.think(250)])],
                               6 * 10 / 0.26, tolerance=0.3)

    def test_setup_runs_once(self):
        setup = k.thread_group("Init", 1, 0, None, [k.http("GET /setup", "/setup")], loops=1, setup=True)
        users = k.thread_group("Users", 3, 0, 5, [k.constant_throughput(600, 1), k.http("GET /x", "/x")])
        one = self.run_plan([setup, users], 1)
        three = self.run_plan([setup, users], 3)
        self.assertAlmostEqual(three / one, 1.0, delta=0.25)
        with open(self.tmp / "bundle3" / "kpi1.jtl", newline="") as fh:
            self.assertNotIn("GET /setup", {r["label"] for r in csv.DictReader(fh)})

    def test_encoding_of_the_console_and_a_groovy_timer(self):
        # the generator of the AI team's plans needs Groovy: a script that compiles and runs
        plan = self.tmp / "g.jmx"
        timer = k.jsr223_timer("Pause", "return 20")
        plan.write_text(k.plan("G", [k.defaults("localhost", "8080"),
                                     k.thread_group("U", 2, 0, 4, [k.http("GET /x", "/x"), timer])]), encoding="utf-8")
        s = scenario.load_scenario(plan, installed_plugins=PLUGINS)
        scenario.compile_bundle(s, self.tmp / "b", app_url=self.url, generators=1)
        t0 = time.time()
        res = subprocess.run([JMETER, "-n", "-t", "plan-0.jmx", "-l", "kpi.jtl", "-j", "jm.log"], cwd=self.tmp / "b",
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertLess(time.time() - t0, 60)
        with open(self.tmp / "b" / "kpi.jtl", newline="") as fh:
            self.assertGreater(len(list(csv.DictReader(fh))), 20)


if __name__ == "__main__":
    unittest.main()
