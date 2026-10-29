"""JMeter test plans (.jmx): what the lab reads from them, how it divides them and the copy each generator runs."""
from __future__ import annotations

import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from morfeo_lab import jmx, scenario
from morfeo_lab.errors import LabError
from tests import jmxkit as k

FIXTURES = Path(__file__).resolve().parent / "fixtures"
URL = "http://app.morfeo-app.svc.cluster.local:8080"
HOST = "app.morfeo-app.svc.cluster.local"
PLUGINS = ["jpgc-casutg", "jpgc-tst", "jpgc-functions", "jpgc-dummy"]
LIMITS = {"max_threads_per_generator": 1000, "max_rps_per_generator": 2500, "max_generators": 8}
GET = k.http("GET /api/saldo/{id}", "/api/saldo/${id}")


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="morfeo-jmx-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, children, name="p.jmx", **kw) -> Path:
        p = self.tmp / name
        p.write_text(k.plan("Test plan", children, **kw), encoding="utf-8")
        return p

    def read(self, children, **kw) -> jmx.Plan:
        return jmx.read_plan(self.write(children, **kw), PLUGINS)

    def load(self, children, **kw) -> scenario.Scenario:
        return scenario.load_scenario(self.write(children, **kw), installed_plugins=PLUGINS)

    def compiled(self, s, generators=1, **kw) -> list[ET.Element]:
        out = self.tmp / "bundle"
        self.notes = scenario.compile_bundle(s, out, app_url=URL, generators=generators, **kw)
        return [ET.parse(out / f"plan-{i}.jmx").getroot() for i in range(generators)]


def props(root, name) -> list[str]:
    return [(e.text or "").strip() for e in root.iter() if e.get("name") == name]


class TestReading(Tmp):
    def test_thread_group_with_duration(self):
        p = self.read([k.defaults(), k.thread_group("Users", 50, 60, 300, [GET, k.think(1000, 2000)])],
                      comments="peak hour")
        g = p.mains[0]
        self.assertEqual((p.title, p.description), ("p", "peak hour"))   # a default plan name gives the file name
        self.assertEqual((p.planned_duration, p.model, p.peak_threads, p.ramp_up), (300, "closed", 50, 60))
        self.assertAlmostEqual(g.at(g.shape, 30), 25.4, places=0)
        self.assertEqual(g.at(g.shape, 100), 50)
        self.assertEqual(jmx.endpoint_labels(p)[2], {"GET /api/saldo/{id}": "GET /api/saldo/{id}"})

    def test_iterations_and_variables(self):
        p = self.read([k.thread_group("Users", "${__P(users,7)}", "${ramp}", None, [GET], loops=3)],
                      udv={"ramp": "10"})
        g = p.mains[0]
        self.assertEqual((g.threads, g.ramp_up, g.iterations), (7, 10, 3))   # property default, variable, loops
        self.assertIsNone(p.planned_duration)

    def test_a_value_known_only_during_the_test_is_reported_and_blocks_splitting(self):
        p = self.read([k.thread_group("Users", "${threads}", 0, 60, [GET])])
        self.assertTrue(any("depends on a variable" in w for w in p.warnings))
        self.assertEqual(jmx.distribution_of(p).mode, "single")

    def test_plugin_thread_groups(self):
        p = self.read([
            k.concurrency("Steps", 100, 5, 2, steps=5, unit="M", children=[GET]),
            k.arrivals("Partner", 30, 60, 120, limit=300, children=[GET]),
            k.ultimate("Spike", [(10, 0, 10, 100, 0), (100, 30, 1, 20, 0)], [GET])])
        by = {g.name: g for g in p.mains}
        self.assertEqual(by["Steps"].end, 420)                          # minutes
        self.assertEqual(by["Steps"].steps, 5)
        self.assertEqual(by["Steps"].at(by["Steps"].shape, 1), 20)      # the first step starts at once
        self.assertEqual((by["Partner"].model, by["Partner"].peak, by["Partner"].threads), ("open", 30, 300))
        self.assertEqual(by["Spike"].at(by["Spike"].shape, 40), 110)
        self.assertEqual(by["Spike"].at(by["Spike"].shape, 52), 10)     # the spike drops at once
        self.assertEqual((p.model, p.planned_duration), ("mixed", 420))

    def test_open_model_and_free_form(self):
        p = self.read([
            k.open_model("Open", "rate(1/s) random_arrivals(1 min) rate(10/s) random_arrivals(2 min) pause(10s)",
                         [GET]),
            k.freeform("Free", [(0, 10, 30), (10, 10, 30)], children=[GET])])
        by = {g.name: g for g in p.mains}
        self.assertEqual(by["Open"].end, 190)
        self.assertAlmostEqual(by["Open"].level_at(30), 5.5, places=1)  # 1 -> 10 over the first minute
        self.assertEqual((by["Open"].level_at(100), by["Open"].level_at(185)), (10, 0))
        self.assertEqual((by["Free"].level_at(15), by["Free"].end), (5, 60))

    def test_throughput_timers(self):
        p = self.read([
            k.thread_group("CTT", 20, 0, 100, [k.constant_throughput(6000, 1), GET]),
            k.thread_group("Per thread", 10, 0, 100, [k.constant_throughput(60, 0), GET]),
            k.thread_group("Shaped", 50, 0, 600, [k.shaping_timer([(10, 100, 60), (100, 100, 120)]), GET])])
        by = {g.name: g for g in p.mains}
        self.assertEqual(by["CTT"].peak_rate, 100)
        self.assertEqual(by["Per thread"].peak_rate, 10)                # 1 request/s per thread
        self.assertEqual((by["Shaped"].peak_rate, by["Shaped"].end), (100, 180))   # the timer ends the group
        self.assertEqual(p.model, "throughput")
        self.assertAlmostEqual(p.timeline()[30]["rps"], 100 + 10 + 55, delta=2)

    def test_consecutive_thread_groups_follow_each_other(self):
        p = self.read([k.thread_group("A", 5, 0, 60, [GET]), k.thread_group("B", 10, 0, 30, [GET])], serialize=True)
        self.assertEqual([g.start for g in p.mains], [0, 60])
        self.assertEqual((p.planned_duration, p.peak_threads), (90, 10))
        self.assertEqual(p.timeline()[70]["users"], 10)

    def test_setup_and_teardown_are_not_load(self):
        p = self.read([k.thread_group("Init", 1, 0, None, [GET], loops=1, setup=True),
                       k.thread_group("Users", 5, 0, 60, [GET])])
        self.assertEqual(([g.name for g in p.mains], [g.role for g in p.groups]), (["Users"], ["setup", "main"]))

    def test_labels_keys_and_transactions(self):
        p = self.read([k.thread_group("Users", 1, 0, 10, [
            k.transaction("Buy", [k.http("Balance", "/api/saldo/user_${n}"),
                                  k.http("Pay", "/api/pago", "POST", '{"x": 1}'),
                                  k.http("POST /api/ingreso", "http://localhost:8080/api/ingreso", "POST", "{}")])])])
        labels, _, keys = jmx.endpoint_labels(p)
        self.assertEqual((keys["Balance"], keys["Pay"], keys["POST /api/ingreso"]),
                         ("GET /api/saldo/{id}", "POST /api/pago", "POST /api/ingreso"))
        self.assertEqual(p.transactions, ["Buy"])
        self.assertNotIn("Buy", labels)

    def test_disabled_elements_are_ignored(self):
        p = self.read([k.thread_group("U", 2, 0, 10, [GET, k.http("Off", "/off", enabled=False)])])
        self.assertNotIn("Off", jmx.endpoint_labels(p)[0])

    def test_problems_that_stop_the_test(self):
        bad = self.tmp / "x.jmx"
        bad.write_text("<not-jmeter/>")
        self.assertIn("not a JMeter test plan", jmx.read_plan(bad).errors[0])
        bad.write_text("<jmeterTestPlan><hashTree>")
        self.assertIn("XML error", jmx.read_plan(bad).errors[0])
        self.assertIn("no enabled thread group", " ".join(self.read([k.defaults()]).errors))
        self.assertIn("missing.csv", " ".join(self.read([k.thread_group("U", 1, 0, 10, [k.csv("data/missing.csv"), GET])]).errors))
        inc = k.el("IncludeController", "IncludeControllerGui", "Other plan", [k.s("IncludeController.includepath", "o.jmx")])
        self.assertIn("deliver one self-contained plan", " ".join(self.read([k.thread_group("U", 1, 0, 10, [GET, inc])]).errors))
        with self.assertRaises(LabError):
            scenario.load_scenario(self.tmp / "nope.jmx", installed_plugins=PLUGINS)
        yml = self.tmp / "old.yml"
        yml.write_text("execution: []\n")
        with self.assertRaises(LabError) as cm:
            scenario.load_scenario(yml, installed_plugins=PLUGINS)
        self.assertIn(".jmx", str(cm.exception))

    def test_a_plan_longer_than_the_lab_limit_is_refused(self):
        with self.assertRaises(LabError) as cm:
            scenario.load_scenario(self.write([k.thread_group("Long", 1, 0, 3 * 3600, [GET])]),
                                   installed_plugins=PLUGINS, max_duration=7200)
        self.assertIn("longer than the lab limit", str(cm.exception))

    def test_a_group_with_no_end_is_a_warning(self):
        p = self.read([k.thread_group("Forever", 1, 0, None, [GET], loops=-1)])
        self.assertTrue(any("no end" in w for w in p.warnings))


class TestPlugins(Tmp):
    def test_a_plugin_the_image_lacks_is_an_error_with_the_fix(self):
        plan = self.write([k.concurrency("Steps", 10, 5, 5, children=[GET])])
        p = jmx.read_plan(plan, ["jpgc-tst"])
        self.assertEqual(len(p.errors), 1)
        self.assertIn("jpgc-casutg", p.errors[0])
        self.assertIn("generators.jmeter_plugins", p.errors[0])
        self.assertEqual(jmx.read_plan(plan, PLUGINS).errors, [])

    def test_plugin_functions_are_detected(self):
        plan = self.write([k.thread_group("U", 1, 0, 10, [k.http("A", "/a/${__MD5(x)}")])])
        self.assertIn("jpgc-functions", " ".join(jmx.read_plan(plan, ["jpgc-tst"]).errors))

    def test_unknown_plugin_is_reported(self):
        p = self.read([k.thread_group("U", 1, 0, 10, [GET, k.plugin_element("com.acme.MySampler", "Acme")])])
        self.assertTrue(any("com.acme.MySampler" in w for w in p.warnings))


class TestDistribution(Tmp):
    def test_standard_groups_are_divided(self):
        d = jmx.distribution_of(self.read([k.thread_group("U", 10, 0, 60, [GET])]))
        self.assertEqual((d.mode, d.max_generators), ("split", 10))

    def test_a_script_timer_means_one_generator(self):
        p = self.read([k.thread_group("Users", 10, 0, 60, [GET, k.jsr223_timer("Poisson arrivals", "return 5")])])
        d = jmx.distribution_of(p)
        self.assertEqual(d.mode, "single")
        self.assertIn("script", d.reasons[0])
        self.assertTrue(p.mains[0].scripted_pacing)

    def test_a_plan_that_reads_morfeo_generators_divides_itself(self):
        p = self.read([k.thread_group("Users", 10, 0, 60, [GET, k.jsr223_timer("T", "return 5", "${__P(morfeo_generators,1)}")])])
        self.assertEqual(jmx.distribution_of(p).mode, "plan")

    def test_stepping_thread_group_is_not_divided(self):
        st = k.el("kg.apc.jmeter.threads.SteppingThreadGroup", "kg.apc.jmeter.threads.SteppingThreadGroupGui", "S",
                  [k.s("ThreadGroup.num_threads", 10), k.s("Threads initial delay", 0),
                   k.s("Start users count", 5), k.s("Start users count burst", 0), k.s("Start users period", 30),
                   k.s("Stop users count", 10), k.s("Stop users period", 1), k.s("flighttime", 60)], [GET])
        s = self.load([st])
        self.assertEqual(s.distribution.mode, "single")
        self.assertEqual(s.plan.mains[0].at(s.plan.mains[0].shape, 40), 10)
        self.assertEqual(scenario.choose_generators(s, {**LIMITS, "max_threads_per_generator": 2}, None)[0], 1)
        with self.assertRaises(LabError):
            scenario.choose_generators(s, LIMITS, 2)

    def test_auto_generators(self):
        s = self.load([k.thread_group("U", 2500, 60, 120, [GET])])
        n, why = scenario.choose_generators(s, LIMITS, None)
        self.assertEqual(n, 3)
        self.assertIn("threads", why)
        self.assertEqual(scenario.choose_generators(self.load([k.arrivals("P", 6000, 10, 60, children=[GET])]),
                                                    LIMITS, None)[0], 3)
        capped = scenario.choose_generators(s, {**LIMITS, "max_generators": 2}, None)
        self.assertEqual(capped[0], 2)
        self.assertIn("bottleneck", capped[1])

    def test_an_arrival_rate_smaller_than_the_generators_is_refused(self):
        s = self.load([k.arrivals("Partner", 2, 0, 10, children=[GET])])    # the plugin only accepts whole rates
        self.assertEqual(s.distribution.max_generators, 2)
        with self.assertRaises(LabError):
            scenario.choose_generators(s, LIMITS, 3)

    def test_more_generators_than_threads_is_refused(self):
        s = self.load([k.thread_group("U", 2, 0, 10, [GET])])
        with self.assertRaises(LabError) as cm:
            scenario.choose_generators(s, LIMITS, 3)
        self.assertIn("at most 2", str(cm.exception))

    def test_the_plan_of_the_ai_team_runs_on_one_generator(self):
        s = scenario.load_scenario(FIXTURES / "ai-plan.jmx", installed_plugins=PLUGINS)
        self.assertEqual(s.distribution.mode, "single")
        self.assertEqual(scenario.choose_generators(s, LIMITS, None)[0], 1)
        self.assertEqual([g.role for g in s.plan.groups], ["setup", "main", "teardown"])
        self.assertEqual(s.plan.properties["host"], "localhost")
        self.assertEqual(s.label_keys["GET /api/saldo/{id}"], "GET /api/saldo/{id}")
        self.assertEqual(s.plan.planned_duration, 227)


class TestCompile(Tmp):
    def test_requests_go_to_the_application(self):
        s = self.load([
            k.defaults("localhost", "8080"),
            k.thread_group("U", 2, 0, 10, [
                GET,
                k.http("Own host", "/x", domain="127.0.0.1", port="9000"),
                k.http("Property host", "/y", domain="${__P(host,localhost)}", port="${__P(port,8080)}"),
                k.http("External", "/z", domain="auth.example.com", port="443"),
                k.http("Full URL", "http://localhost:7070/api/w?q=1"),
                k.listener()])])
        root = self.compiled(s)[0]
        domains = props(root, "HTTPSampler.domain")
        self.assertEqual(domains.count(HOST), 4)                        # defaults, own, property, full URL
        self.assertIn("auth.example.com", domains)
        self.assertIn("/api/w?q=1", props(root, "HTTPSampler.path"))
        self.assertNotIn("9000", props(root, "HTTPSampler.port"))
        self.assertFalse([e for e in root.iter() if "ResultCollector" in (e.get("testclass") or "")])
        self.assertTrue(any("auth.example.com" in n for n in self.notes))
        self.assertTrue(any("listener" in w for w in s.warnings))
        properties = (self.tmp / "bundle" / "morfeo.properties").read_text()
        self.assertIn(f"morfeo_host={HOST}", properties)
        self.assertIn("morfeo_port=8080", properties)

    def test_a_host_chosen_by_property_with_an_external_default_is_not_redirected(self):
        s = self.load([k.thread_group("U", 1, 0, 10, [
            k.http("A", "/a", domain="${__P(host,api.example.com)}", port="443")])])
        root = self.compiled(s)[0]
        self.assertEqual(props(root, "HTTPSampler.domain"), ["${__P(host,api.example.com)}"])

    def test_empty_host_without_defaults(self):
        root = self.compiled(self.load([k.thread_group("U", 1, 0, 10, [k.http("A", "/a")])]))[0]
        self.assertEqual(props(root, "HTTPSampler.domain"), [HOST])
        self.assertEqual(props(root, "HTTPSampler.port"), ["8080"])

    def test_a_plan_that_needs_no_change_is_sent_byte_for_byte(self):
        p = self.write([k.thread_group("U", 1, 0, 10, [k.http("A", "/a", domain=HOST, port="8080")])])
        s = scenario.load_scenario(p, installed_plugins=PLUGINS)
        self.compiled(s)
        self.assertEqual((self.tmp / "bundle" / "plan-0.jmx").read_bytes(), p.read_bytes())

    def test_the_load_is_divided_between_generators(self):
        s = self.load([
            k.thread_group("Init", 1, 0, None, [GET], loops=1, setup=True),
            k.thread_group("Users", 10, 5, 60, [k.constant_throughput(6000, 1), GET]),
            k.concurrency("Steps", 7, 30, 30, steps=3, children=[GET]),
            k.arrivals("Partner", 31, 10, 60, limit=100, children=[GET]),
            k.ultimate("Spike", [(5, 0, 1, 10, 0), (9, 10, 1, 10, 0)], [GET]),
            k.thread_group("Shaped", 6, 0, 60, [k.shaping_timer([(10, 20, 30)]), GET]),
            k.open_model("Open", "rate(9/s) random_arrivals(30 s)", [GET])])
        self.assertEqual(s.distribution.mode, "split")
        roots = self.compiled(s, generators=3)

        def per_generator(name):
            return [[e.text for e in r.iter() if e.get("name") == name] for r in roots]
        users = [v for r in roots for g in r.iter("ThreadGroup") if g.get("testname") == "Users"
                 for v in props(g, "ThreadGroup.num_threads")]
        self.assertEqual(sorted(int(x) for x in users), [3, 3, 4])
        self.assertEqual(sum(int(x[0]) for x in per_generator("TargetLevel")), 7)
        self.assertEqual(sum(int(x[1]) for x in per_generator("TargetLevel")), 31)
        self.assertEqual(sum(int(x[0]) for x in per_generator("ConcurrencyLimit")), 100)
        throughput = [float(r.find(".//doubleProp[name='throughput']/value").text) for r in roots]
        self.assertAlmostEqual(sum(throughput), 6000)
        self.assertEqual(sum(1 for r in roots for _ in r.iter("SetupThreadGroup")), 1)   # setUp on generator 0 only
        ultimate = [[next(c.text for c in row) for row in r.find(".//collectionProp[@name='ultimatethreadgroupdata']")]
                    for r in roots]
        self.assertEqual([sum(int(u[i]) for u in ultimate) for i in range(2)], [5, 9])
        profile = [[[c.text for c in row][:2] for row in r.find(".//collectionProp[@name='load_profile']")] for r in roots]
        self.assertAlmostEqual(sum(float(t[0][0]) for t in profile), 10)
        self.assertAlmostEqual(sum(float(t[0][1]) for t in profile), 20)
        self.assertEqual(props(roots[1], "OpenModelThreadGroup.schedule"), ["rate(3/s) random_arrivals(30 s)"])

    def test_teardown_exists_on_one_generator_only(self):
        s = self.load([k.thread_group("U", 4, 0, 10, [GET]),
                       k.thread_group("End", 1, 0, None, [GET], loops=1, teardown=True)])
        roots = self.compiled(s, generators=2)
        self.assertEqual([len(list(r.iter("PostThreadGroup"))) for r in roots], [1, 0])

    def test_an_open_model_seed_differs_per_generator(self):
        og = k.el("OpenModelThreadGroup", "OpenModelThreadGroupGui", "Open",
                  [k.s("OpenModelThreadGroup.schedule", "rate(4/s) random_arrivals(10 s)"),
                   k.s("OpenModelThreadGroup.random_seed", "100")], [GET])
        roots = self.compiled(self.load([og]), generators=2)
        self.assertEqual([props(r, "OpenModelThreadGroup.random_seed") for r in roots], [["100"], ["101"]])

    def test_a_plan_that_divides_itself_is_not_touched(self):
        s = self.load([k.thread_group("U", 6, 0, 10, [GET, k.jsr223_timer("T", "return 1", "${__P(morfeo_generators,1)}")])])
        roots = self.compiled(s, generators=3)
        self.assertEqual([props(r, "ThreadGroup.num_threads") for r in roots], [["6"]] * 3)

    def test_a_plan_with_a_script_timer_cannot_be_divided(self):
        s = self.load([k.thread_group("U", 6, 0, 10, [GET, k.jsr223_timer()])])
        with self.assertRaises(LabError) as cm:
            self.compiled(s, generators=2)
        self.assertIn("morfeo_generators", str(cm.exception))

    def test_more_generators_than_threads_is_refused_when_compiling(self):
        s = self.load([k.thread_group("U", 2, 0, 10, [GET])])
        with self.assertRaises(LabError):
            self.compiled(s, generators=3)

    def test_data_files_and_secrets(self):
        (self.tmp / "data").mkdir()
        (self.tmp / "data" / "ids.csv").write_text("id\nu1\n")
        (self.tmp / "body.json").write_text("{}")
        s = self.load([k.thread_group("U", 2, 0, 10, [
            k.csv("data/ids.csv"), k.csv(r"C:\Users\ia\plans\body.json", name="Absolute"), GET])])
        self.assertEqual(len(s.plan.files), 2)
        secret = self.tmp / "secret"
        root = self.compiled(s, secrets_dir=secret, client={"properties": {"api_key": "s3cr3t-value"}})[0]
        self.assertTrue((self.tmp / "bundle" / "data" / "ids.csv").exists())
        self.assertEqual(sorted(props(root, "filename")), ["body.json", "data/ids.csv"])
        bundle = "".join(f.read_text() for f in (self.tmp / "bundle").rglob("*") if f.is_file())
        self.assertNotIn("s3cr3t-value", bundle)
        self.assertIn("api_key=s3cr3t-value", (secret / "client.properties").read_text())
        self.assertEqual(scenario.secret_values({"properties": {"api_key": "s3cr3t-value"}}), ["s3cr3t-value"])

    def test_thread_num_ids_collide_between_generators(self):
        s = self.load([k.thread_group("U", 4, 0, 10, [k.http("Create", "/u", "POST", '{"id": "u_${__threadNum}"}')])])
        self.compiled(s, generators=2)
        self.assertTrue(any("__threadNum" in n for n in self.notes))

    def test_the_plan_of_the_ai_team(self):
        s = scenario.load_scenario(FIXTURES / "ai-plan.jmx", installed_plugins=PLUGINS)
        out = self.tmp / "bundle"
        scenario.compile_bundle(s, out, app_url=URL, generators=1)
        root = ET.parse(out / "plan-0.jmx").getroot()
        self.assertEqual(props(root, "HTTPSampler.domain"), [HOST])
        self.assertEqual(props(root, "HTTPSampler.port"), ["8080"])
        self.assertEqual(len(list(root.iter("JSR223Timer"))), 2)           # the plan itself is untouched
        self.assertEqual([len(list(root.iter(t))) for t in ("SetupThreadGroup", "ThreadGroup", "PostThreadGroup")],
                         [1, 1, 1])


if __name__ == "__main__":
    unittest.main()
