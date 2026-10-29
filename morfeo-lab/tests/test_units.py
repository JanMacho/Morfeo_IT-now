"""Durations, Kubernetes quantities, numbers for people and environment expansion."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from morfeo_lab import units


class Durations(unittest.TestCase):
    def test_parse(self):
        for text, seconds in (("30", 30), ("30s", 30), ("1m30s", 90), ("2h", 7200), ("500ms", 0.5), (45, 45), ("1.5m", 90)):
            self.assertEqual(units.parse_duration(text), seconds, text)

    def test_empty_is_zero(self):
        self.assertEqual(units.parse_duration(""), 0.0)
        self.assertEqual(units.parse_duration(None), 0.0)

    def test_invalid(self):
        for bad in ("abc", "1x", "m", "1m foo"):
            with self.assertRaises(ValueError, msg=bad):
                units.parse_duration(bad)

    def test_format(self):
        self.assertEqual(units.fmt_duration(3725), "1h2m5s")
        self.assertEqual(units.fmt_duration(60), "1m")
        self.assertEqual(units.fmt_duration(0), "0s")
        self.assertEqual(units.fmt_clock(75), "1:15")
        self.assertEqual(units.fmt_clock(3725), "1:02:05")


class Quantities(unittest.TestCase):
    def test_cpu(self):
        self.assertEqual(units.parse_cpu("2"), 2.0)
        self.assertEqual(units.parse_cpu("500m"), 0.5)

    def test_memory(self):
        self.assertEqual(units.parse_memory("2Gi"), 2 * 2**30)
        self.assertEqual(units.parse_memory("768Mi"), 768 * 2**20)
        self.assertEqual(units.parse_memory("1G"), 10**9)
        self.assertEqual(units.parse_memory("512000000"), 512_000_000)


class Numbers(unittest.TestCase):
    def test_num(self):
        self.assertEqual(units.num(31526), "31,526")
        self.assertEqual(units.num(3.94, 1), "3.9")
        self.assertEqual(units.num(250, 0, "ms"), "250 ms")
        self.assertEqual(units.num(0.5, 2, "%"), "0.50%")
        self.assertEqual(units.num(None), "–")

    def test_plural_and_latency(self):
        self.assertEqual(units.plural(1, "replica", "replicas"), "1 replica")
        self.assertEqual(units.plural(3, "replica", "replicas"), "3 replicas")
        self.assertEqual(units.ms(8.44), "8.4 ms")
        self.assertEqual(units.ms(250.2), "250 ms")
        self.assertEqual(units.ms(None), "–")

    def test_slug(self):
        self.assertEqual(units.slug("Hora 10: 2424 sesiones/h"), "hora-10-2424-sesiones-h")
        self.assertEqual(units.slug("###"), "x")
        self.assertEqual(len(units.slug("a" * 100, 20)), 20)


class Environment(unittest.TestCase):
    def test_expansion(self):
        with mock.patch.dict(os.environ, {"A": "1", "EMPTY": ""}, clear=False):
            os.environ.pop("MISSING", None)
            self.assertEqual(units.expand_env("x${A}y"), "x1y")
            self.assertEqual(units.expand_env("${MISSING:-fallback}"), "fallback")
            self.assertEqual(units.expand_env("${EMPTY:-fallback}"), "fallback")
            self.assertEqual(units.expand_env("${EMPTY-fallback}"), "")
            self.assertEqual(units.expand_env("${env:A}"), "1")

    def test_what_is_not_ours_is_left_alone(self):
        os.environ.pop("MISSING", None)
        self.assertEqual(units.expand_env("${MISSING}"), "${MISSING}")
        self.assertEqual(units.expand_env("${__P(host,localhost)}"), "${__P(host,localhost)}")
        self.assertEqual(units.expand_env({"k": ["${MISSING}", 3]}), {"k": ["${MISSING}", 3]})


class Hash(unittest.TestCase):
    def test_changes_with_content_only(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "a.txt").write_text("one")
            first = units.hash_paths(folder)
            self.assertEqual(first, units.hash_paths(folder))
            (folder / "a.txt").write_text("two")
            self.assertNotEqual(first, units.hash_paths(folder))
            self.assertNotEqual(units.hash_paths(folder, extra="x"), units.hash_paths(folder))


if __name__ == "__main__":
    unittest.main()
