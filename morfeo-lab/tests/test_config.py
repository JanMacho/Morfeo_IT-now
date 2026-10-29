"""The lab configuration and the application files: defaults, strict checking and file resolution."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from morfeo_lab import config
from morfeo_lab.errors import LabError

ROOT = Path(__file__).resolve().parent.parent


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="morfeo-config-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, name: str, data) -> Path:
        p = self.tmp / name
        p.write_text(yaml.safe_dump(data) if not isinstance(data, str) else data, encoding="utf-8")
        return p


class LabFile(Folder):
    def test_the_shipped_file_loads_and_equals_the_defaults(self):
        lab = config.load_lab(str(ROOT / "config" / "lab.yaml"))
        for key, value in config.LAB_DEFAULTS.items():
            self.assertEqual(lab[key], value, key)

    def test_missing_file_means_defaults(self):
        with mock.patch.dict(os.environ, {"MORFEO_LAB_CONFIG": str(self.tmp / "none.yaml")}):
            self.assertEqual(config.load_lab()["cluster"]["provider"], "kind")

    def test_explicit_file_must_exist(self):
        with self.assertRaises(LabError):
            config.load_lab(str(self.tmp / "none.yaml"))

    def test_typos_are_reported_with_a_suggestion(self):
        p = self.write("lab.yaml", {"cluster": {"provder": "kind"}})
        with self.assertRaises(LabError) as cm:
            config.load_lab(str(p))
        self.assertIn("did you mean 'cluster.provider'", str(cm.exception))

    def test_values_are_checked(self):
        p = self.write("lab.yaml", {"cluster": {"provider": "nowhere"}, "generators": {"max_duration": "soon",
                                                                                         "max_generators": 0}})
        with self.assertRaises(LabError) as cm:
            config.load_lab(str(p))
        text = str(cm.exception)
        self.assertIn("cluster.provider", text)
        self.assertIn("generators.max_duration", text)
        self.assertIn("generators.max_generators", text)

    def test_unknown_plugin_ids_are_refused_with_the_available_ones(self):
        p = self.write("lab.yaml", {"generators": {"jmeter_plugins": ["jpgc-casutg", "jpgc-nothing"]}})
        with self.assertRaises(LabError) as cm:
            config.load_lab(str(p))
        self.assertIn("jpgc-nothing", str(cm.exception))
        self.assertIn("Available: jpgc-casutg", str(cm.exception))

    def test_registry_loader_needs_a_registry(self):
        p = self.write("lab.yaml", {"cluster": {"image_loader": "registry"}})
        with self.assertRaises(LabError):
            config.load_lab(str(p))

    def test_partial_nested_override_keeps_the_rest(self):
        p = self.write("lab.yaml", {"cluster": {"ports": {"grafana": 3100}}})
        lab = config.load_lab(str(p))
        self.assertEqual(lab["cluster"]["ports"], {"grafana": 3100, "prometheus": 9090, "app": 7070})


class ApplicationFile(Folder):
    def target(self, **over) -> Path:
        data = {"name": "demo", "artifact": {"image": "demo:1"}}
        data.update(over)
        return self.write("demo.yaml", data)

    def test_the_shipped_application_loads(self):
        t = config.load_target("bank-api", check_files=False)
        self.assertEqual((t.name, t.port, t.scheme), ("bank-api", 7070, "http"))
        self.assertEqual(t.url("morfeo-app"), "http://app.morfeo-app.svc.cluster.local:7070")
        self.assertEqual(t.postgres["database"], "banco")

    def test_defaults(self):
        t = config.load_target(str(self.target()))
        self.assertEqual(t.replicas, 1)
        self.assertEqual(t.image, "demo:1")
        self.assertIsNone(t.postgres)

    def test_unknown_names_are_reported(self):
        p = self.target(runtime={"prot": 8080})
        with self.assertRaises(LabError) as cm:
            config.load_target(str(p))
        self.assertIn("did you mean 'runtime.port'", str(cm.exception))

    def test_jar_or_image_exactly_one(self):
        for artifact in ({}, {"jar": "a.jar", "image": "b"}):
            with self.assertRaises(LabError, msg=str(artifact)):
                config.load_target(str(self.target(artifact=artifact)))

    def test_name_is_validated(self):
        with self.assertRaises(LabError):
            config.load_target(str(self.target(name="Bad Name")))

    def test_unknown_application_lists_the_available_ones(self):
        with self.assertRaises(LabError) as cm:
            config.load_target("nothing-like-this")
        self.assertIn("bank-api", str(cm.exception))

    def test_replicas_override(self):
        self.assertEqual(config.load_target(str(self.target()), replicas=3).replicas, 3)

    def test_log_files_must_be_absolute(self):
        with self.assertRaises(LabError):
            config.load_target(str(self.target(logs={"files": ["traffic.log"]})))

    def test_dependency_environment(self):
        p = self.target(dependencies={"postgres": {"database": "d", "user": "u", "password": "p",
                                                    "env": {"URL": "jdbc:postgresql://{host}:{port}/{database}"}}})
        t = config.load_target(str(p), check_files=False)
        self.assertEqual(config.dependency_env(t.data, "ns"),
                         {"URL": "jdbc:postgresql://postgres.ns.svc.cluster.local:5432/d"})

    def test_client_properties_expand_variables_and_refuse_unset_ones(self):
        p = self.target(client={"properties": {"api_key": "${DEMO_KEY}"}})
        t = config.load_target(str(p))
        os.environ.pop("DEMO_KEY", None)
        with self.assertRaises(LabError):
            config.client_properties(t)
        with mock.patch.dict(os.environ, {"DEMO_KEY": "secret-value"}):
            self.assertEqual(config.client_properties(t), {"api_key": "secret-value"})


class Candidates(Folder):
    def test_first_existing_wins(self):
        (self.tmp / "b.jar").write_bytes(b"x")
        found = config.resolve_candidates(["a.jar", "b.jar"], "JAR", base=self.tmp)
        self.assertEqual(found, (self.tmp / "b.jar").resolve())

    def test_ambiguous_pattern_is_refused(self):
        for name in ("app-1.jar", "app-2.jar"):
            (self.tmp / name).write_bytes(b"x")
        with self.assertRaises(LabError) as cm:
            config.resolve_candidates("app-*.jar", "JAR", base=self.tmp)
        self.assertIn("matches 2 files", str(cm.exception))

    def test_unset_variable_is_named(self):
        os.environ.pop("NOT_SET_FOR_SURE", None)
        with self.assertRaises(LabError) as cm:
            config.resolve_candidates("${NOT_SET_FOR_SURE}", "JAR", base=self.tmp)
        self.assertIn("variable not set", str(cm.exception))

    def test_optional(self):
        self.assertIsNone(config.resolve_candidates("none.jar", "JAR", base=self.tmp, required=False))


if __name__ == "__main__":
    unittest.main()
