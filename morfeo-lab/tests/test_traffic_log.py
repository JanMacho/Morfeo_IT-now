"""The traffic log of the application: parsing, merging the replicas and comparing with what the generators saw."""
from __future__ import annotations

import json
import unittest

from morfeo_lab import traffic_log
from tests import synth

ENDPOINT = {"key": "GET /api/saldo/{id}", "requests": 400, "by_class": {"network": 0},
            "latency_ms": {"p50": 20.0, "p95": 30.0, "p99": 40.0}}


class Parsing(unittest.TestCase):
    def test_table(self):
        table = traffic_log.to_table(synth.traffic_log(50))
        self.assertEqual(len(table), 50)
        self.assertEqual(set(table.columns), {"ts", "method", "path", "status", "ms"})

    def test_query_string_is_dropped_and_method_upper_cased(self):
        line = json.dumps({"ts_end": "2025-01-01T00:00:00.000Z", "method": "get", "path": "/a?x=1", "status": 200,
                           "latency_ms": 5})
        row = traffic_log.to_table((line + "\n").encode()).iloc[0]
        self.assertEqual((row["method"], row["path"]), ("GET", "/a"))

    def test_another_format_is_not_ours(self):
        self.assertIsNone(traffic_log.to_table(b"127.0.0.1 - - [01/Jan] GET /a 200\n" * 30))
        self.assertIsNone(traffic_log.to_table(b""))


class Merging(unittest.TestCase):
    def test_replicas_are_merged_in_time_order_and_cut_to_the_window(self):
        a = synth.traffic_log(100, synth.START, 40)
        b = synth.traffic_log(100, synth.START + 0.2, 40)
        merged, lines, in_format = traffic_log.merge([a, b], synth.START + 10, synth.START + 30)
        self.assertTrue(in_format)
        table = traffic_log.to_table(merged)
        self.assertEqual(lines, len(table))
        self.assertTrue((table["ts"].diff().dropna() >= 0).all())
        self.assertGreaterEqual(table["ts"].min(), synth.START + 10)
        self.assertLessEqual(table["ts"].max(), synth.START + 30)
        self.assertLess(lines, 200)

    def test_foreign_format_is_kept_as_it_is(self):
        raw = b"plain line one\nplain line two\n" * 20
        merged, lines, in_format = traffic_log.merge([raw], None, None)
        self.assertFalse(in_format)
        self.assertEqual(lines, 0)
        self.assertIn(b"plain line one", merged)


class Comparing(unittest.TestCase):
    def table(self, rows=400):
        return traffic_log.to_table(synth.traffic_log(rows))

    def test_complete_log(self):
        cmp = traffic_log.compare(self.table(), [ENDPOINT], synth.START - 1, synth.START + 41)
        self.assertTrue(cmp["available"])
        self.assertEqual(cmp["logged"], 400)
        self.assertEqual(cmp["completeness"], 1.0)
        row = cmp["endpoints"][0]
        self.assertAlmostEqual(row["outside_ms"]["p50"], 20.0 - row["inside_ms"]["p50"], places=2)

    def test_missing_lines_lower_completeness_and_hide_the_outside_time(self):
        cmp = traffic_log.compare(self.table(300), [ENDPOINT], synth.START - 1, synth.START + 41)
        self.assertEqual(cmp["completeness"], 0.75)
        self.assertEqual(cmp["endpoints"][0]["outside_ms"], {"p50": None, "p95": None})

    def test_setup_and_teardown_lines_in_the_margin_are_not_load(self):
        # 6 requests of the load and 3 of the setUp (before it) and 2 of the tearDown (after it) in the same endpoint
        def line(ts, latency):
            return json.dumps({"ts_end": f"2025-01-01T00:00:{ts:02d}.000Z", "method": "GET", "path": "/api/x/1",
                               "status": 200, "latency_ms": latency})
        lines = [line(1, 900)] * 3 + [line(10 + i, 5) for i in range(30)] + [line(50, 900)] * 2
        table = traffic_log.to_table(("\n".join(lines) + "\n").encode())
        t0 = table["ts"].min()
        endpoint = {**ENDPOINT, "key": "GET /api/x/{id}", "requests": 30,
                    "latency_ms": {"p50": 8.0, "p95": 9.0, "p99": 9.0}}
        cmp = traffic_log.compare(table, [endpoint], t0 - 1, t0 + 100, {"GET /api/x/{id}": (3, 2)})
        row = cmp["endpoints"][0]
        self.assertEqual((row["logged"], cmp["completeness"], cmp["lines"]), (30, 1.0, 30))
        self.assertEqual(row["inside_ms"]["p95"], 5.0)   # the 900 ms of the setUp do not count
        self.assertGreater(row["outside_ms"]["p95"], 0)

    def test_no_log(self):
        self.assertFalse(traffic_log.compare(None, [ENDPOINT], 0, 1)["available"])

    def test_too_few_lines(self):
        self.assertFalse(traffic_log.compare(self.table(5), [ENDPOINT], synth.START - 1, synth.START + 41)["available"])

    def test_templates_match_ids_and_the_most_specific_wins(self):
        endpoints = [{**ENDPOINT, "key": "GET /api/x/{id}"}, {**ENDPOINT, "key": "GET /api/x/new"}]
        lines = []
        for i in range(40):
            path = "/api/x/new" if i % 2 else f"/api/x/{i}"
            lines.append(json.dumps({"ts_end": f"2025-01-01T00:00:{i:02d}.000Z", "method": "GET", "path": path,
                                     "status": 200, "latency_ms": 3}))
        table = traffic_log.to_table(("\n".join(lines) + "\n").encode())
        t0 = table["ts"].min()
        cmp = traffic_log.compare(table, endpoints, t0 - 1, t0 + 100)
        by_key = {r["key"]: r["logged"] for r in cmp["endpoints"]}
        self.assertEqual(by_key, {"GET /api/x/{id}": 20, "GET /api/x/new": 20})


if __name__ == "__main__":
    unittest.main()
