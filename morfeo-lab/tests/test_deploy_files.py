"""The Grafana dashboard and the Kubernetes manifests, rendered with representative values."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from morfeo_lab import config, dashboard, deploy, kube
from morfeo_lab.units import parse_cpu, parse_memory

LAB = config.load_lab()
NS = LAB["namespaces"]


def documents(text: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(text) if d]


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.dash = json.loads(dashboard.render_all(LAB)["load-tests.json"])
        self.panels = [p for p in self.dash["panels"] if p["type"] != "row"]

    def test_is_valid_json_with_a_stable_identity(self):
        self.assertEqual(self.dash["uid"], "morfeo-load-tests")
        self.assertGreater(len(self.panels), 10)

    def test_panel_ids_are_unique_and_no_panel_is_wider_than_the_grid(self):
        ids = [p["id"] for p in self.dash["panels"]]
        self.assertEqual(len(ids), len(set(ids)))
        for p in self.panels:
            self.assertLessEqual(p["gridPos"]["x"] + p["gridPos"]["w"], 24, p["title"])

    def test_every_request_query_looks_only_at_the_load_stage(self):
        queries = [t["expr"] for p in self.panels for t in p.get("targets", []) if "morfeo_requests_total" in t["expr"]
                   or "morfeo_response_time_seconds" in t["expr"]]
        self.assertTrue(queries)
        for q in queries:
            self.assertIn('stage="main"', q)

    def test_it_points_at_the_namespaces_of_the_configuration(self):
        text = json.dumps(self.dash)
        self.assertIn(NS["app"], text)
        self.assertIn(NS["generators"], text)

    def test_config_map(self):
        files = dashboard.render_all(LAB)
        cm = json.loads(dashboard.config_map(LAB, files))
        self.assertEqual(cm["kind"], "ConfigMap")
        self.assertEqual(cm["metadata"]["namespace"], NS["monitoring"])
        self.assertEqual(set(cm["data"]), set(files))

    def test_generator_limits_come_from_the_configuration(self):
        text = json.dumps(dashboard.build(NS["app"], NS["generators"], "http://p", parse_cpu("4"), parse_memory("3Gi")))
        self.assertIn(str(3 * 2**30), text)


class Manifests(unittest.TestCase):
    def target(self, **over):
        raw = {"name": "demo", "artifact": {"image": "demo:1"}, "runtime": {"port": 9000, "replicas": 2},
               "health": {"type": "http", "path": "/ready", "liveness_path": "/live"},
               "dependencies": {"postgres": {"database": "d", "user": "u", "password": "p", "args": ["-c", "max_connections=300"]}}}
        raw.update(over)
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "demo.yaml"
            f.write_text(yaml.safe_dump(raw), encoding="utf-8")
            return config.load_target(str(f), check_files=False)

    def render_app(self, t, **over):
        ctx = {"t": t.data, "ns": NS, "image": "demo:1", "env": {"A": "1"}, "logs_dir": "/logs", "tools": True,
               "jmx": True, "profiling": False, "agents_image": "agents:1", "java_tool_options": "-Dx=1",
               "node_selector": {"morfeo.io/pool": "app"}, "config_hash": "abc", "nodeport": True}
        ctx.update(over)
        return documents(kube.render("k8s/app.yaml.j2", **ctx))

    def test_application(self):
        docs = self.render_app(self.target())
        kinds = {d["kind"] for d in docs}
        self.assertEqual(kinds, {"Deployment", "Service"})
        dep = next(d for d in docs if d["kind"] == "Deployment")
        self.assertEqual(dep["spec"]["replicas"], 2)
        self.assertEqual(dep["spec"]["template"]["spec"]["nodeSelector"], {"morfeo.io/pool": "app"})
        app = next(c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == "app")
        self.assertEqual(app["resources"]["limits"]["cpu"], "1")
        self.assertEqual(app["readinessProbe"]["httpGet"]["path"], "/ready")
        self.assertEqual(app["livenessProbe"]["httpGet"]["path"], "/live")

    def test_application_without_node_pools_or_tools(self):
        docs = self.render_app(self.target(), node_selector={}, tools=False, logs_dir=None, jmx=False)
        dep = next(d for d in docs if d["kind"] == "Deployment")
        self.assertNotIn("nodeSelector", dep["spec"]["template"]["spec"])

    def test_postgres(self):
        t = self.target()
        docs = documents(kube.render("k8s/postgres.yaml.j2", pg=t.postgres, ns=NS, node_selector={"morfeo.io/pool": "app"},
                                     exporter_image="exporter:1", system_selector={"morfeo.io/pool": "system"}))
        text = json.dumps(docs)
        self.assertIn("max_connections=300", text)
        self.assertIn("exporter:1", text)
        without = json.dumps(documents(kube.render("k8s/postgres.yaml.j2", pg=t.postgres, ns=NS, node_selector={},
                                                   exporter_image=None, system_selector={})))
        self.assertNotIn("exporter", without)

    def test_generator_job(self):
        docs = documents(kube.render("k8s/generator-job.yaml.j2", job_name="j", ns=NS, run_id="r1", scenario_slug="s",
                                     generators=3, deadline=4000, image="gen:1", cpu="2", memory="2Gi", jvm_heap="1g",
                                     start_timeout=300, node_selector={"morfeo.io/pool": "generators"}))
        job = docs[0]
        self.assertEqual((job["spec"]["completions"], job["spec"]["parallelism"]), (3, 3))
        self.assertEqual(job["spec"]["completionMode"], "Indexed")
        env = {e["name"]: e["value"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertEqual(env["MORFEO_GENERATORS"], "3")
        self.assertEqual(env["HEAP"], "-Xms1g -Xmx1g")

    def test_names_that_look_like_numbers_or_booleans_stay_strings(self):
        job = documents(kube.render("k8s/generator-job.yaml.j2", job_name="j", ns=NS, run_id="20251014", scenario_slug="100",
                                    generators=1, deadline=4000, image="gen:1", cpu="2", memory="2Gi", jvm_heap="1g",
                                    start_timeout=300, node_selector={}))[0]
        labels = job["spec"]["template"]["metadata"]["labels"]
        self.assertEqual(labels["morfeo.io/scenario"], "100")
        selector = job["spec"]["template"]["spec"]["topologySpreadConstraints"][0]["labelSelector"]["matchLabels"]
        self.assertEqual(selector["morfeo.io/scenario"], "100")
        docs = self.render_app(self.target(name="123"))
        dep = next(d for d in docs if d["kind"] == "Deployment")
        self.assertEqual(dep["metadata"]["labels"]["morfeo.io/target"], "123")
        self.assertTrue(all(isinstance(v, str) for d in docs for v in d["metadata"].get("labels", {}).values()))

    def test_monitoring_stack(self):
        text = kube.render("monitoring/stack.yaml.j2", ns=NS, mon=LAB["monitoring"], pools=True, persistent=True,
                           dashboards_hash="d", config_hash="c")
        docs = documents(text)
        self.assertTrue(any(d["kind"] == "Deployment" and d["metadata"]["name"] == "prometheus" for d in docs))
        self.assertTrue(any(d["kind"] == "Deployment" and d["metadata"]["name"] == "grafana" for d in docs))
        self.assertIn("{{ $labels.pod }}", text)   # Prometheus templates survive the rendering

    def test_kind_cluster(self):
        c = LAB["cluster"]
        docs = documents(kube.render("kind/cluster.yaml.j2", cluster=c))
        roles = [n["role"] for n in docs[0]["nodes"]]
        self.assertEqual(roles, ["control-plane", "worker", "worker"])
        pools = [n["labels"]["morfeo.io/pool"] for n in docs[0]["nodes"]]
        self.assertEqual(pools, ["system", "app", "generators"])

    def test_namespaces(self):
        docs = documents(kube.render("k8s/namespaces.yaml.j2", ns=NS))
        self.assertEqual({d["metadata"]["name"] for d in docs}, set(NS.values()))


class LogLayout(unittest.TestCase):
    def target(self, **over):
        return Manifests().target(**over)

    def test_logs_volume_when_the_application_is_told_where_to_write(self):
        t = self.target(logs={"env": "TRAFFIC_LOG", "files": ["/logs/t.json"]})
        self.assertEqual(deploy.logs_layout(t), ("/logs", {"/logs/t.json": "morfeo-tools"}))

    def test_system_folders_are_never_hidden(self):
        t = self.target(logs={"env": "TRAFFIC_LOG", "files": ["/tmp/t.json"]})
        self.assertEqual(deploy.logs_layout(t), (None, {"/tmp/t.json": "app"}))

    def test_images_with_their_own_log_path_are_read_in_place(self):
        t = self.target(logs={"files": ["/var/data/t.json"]})
        self.assertEqual(deploy.logs_layout(t), (None, {"/var/data/t.json": "app"}))

    def test_no_logs(self):
        self.assertEqual(deploy.logs_layout(self.target()), (None, {}))


class Rendering(unittest.TestCase):
    def test_nothing_is_left_unrendered(self):
        t = Manifests().target()
        text = kube.render("k8s/app.yaml.j2", t=t.data, ns=NS, image="i", env={}, logs_dir=None, tools=False, jmx=False,
                           profiling=False, agents_image="a", java_tool_options="", node_selector={}, config_hash="h",
                           nodeport=False)
        self.assertNotIn("{{", text)
        self.assertNotIn("{%", text)


if __name__ == "__main__":
    unittest.main()
