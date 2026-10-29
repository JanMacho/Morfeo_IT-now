#!/usr/bin/env python3
"""Live metrics of a load generator (runs inside every generator pod, standard library only).

It follows the JMeter results file (kpi.jtl) while the test runs and publishes it in the Prometheus text format
on :9270/metrics, so Grafana shows the test live and the lab can draw its progress line.

The results are read by column name, so any JMeter version that writes a CSV header works. Transaction Controller
samples are not requests (their requests are counted one by one) and are skipped. Every sample carries the stage of
the thread group it came from (setup, main or teardown), so the pool of users a plan creates in its setUp is not
mixed with the load it measures.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import glob
import io
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TRANSACTION = "Number of samples in transaction"   # responseMessage of a Transaction Controller sample
STALE_S = 30.0   # seconds without samples after which a JMeter process counts as finished (longer than a think time)
BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
THREAD_NAME = re.compile(r"^(.*) \d+-\d+$")   # JMeter names a thread '<thread group name> <group>-<thread>'


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def classify(code: str, ok: bool) -> str:
    """The error taxonomy of the lab: 4xx is client behaviour, 5xx is always a failure of the server."""
    if not code.isdigit():
        return "network"
    c = int(code)
    if c >= 500:
        return "server_5xx"
    if ok:
        return "ok"
    return "client_4xx" if c >= 400 else "assertion"


class Metrics:
    def __init__(self, const_labels: dict[str, str], stages: dict[str, str], reload=None):
        self.lock = threading.Lock()
        self.const = const_labels
        self.stages = stages                           # thread group name -> setup | main | teardown
        self.reload = reload                           # reads them again: the lab sends them after the pod started
        self.requests: dict[tuple, int] = {}           # (stage, endpoint, code, result) -> n
        self.hist: dict[tuple, list[int]] = {}         # (stage, endpoint) -> bucket counts, +Inf last
        self.hist_sum: dict[tuple, float] = {}
        self.connect_sum: dict[tuple, float] = {}      # seconds spent opening connections
        self.ttfb_sum: dict[tuple, float] = {}         # seconds until the first response byte
        self.bytes_total = 0
        self.threads_by_file: dict[str, tuple[int, float]] = {}   # results file -> (allThreads, when it was seen)
        self.first_sample_ts: float | None = None
        self.parse_errors = 0

    def stage_of(self, thread_name: str) -> str:
        if not self.stages and self.reload:
            self.stages = self.reload() or {}
        m = THREAD_NAME.match(thread_name)
        return self.stages.get(m.group(1) if m else thread_name, "main")

    def observe(self, label: str, code: str, ok: bool, elapsed_ms: float, nbytes: int, threads: int | None,
                ts_ms: float, source: str, connect_ms: float, ttfb_ms: float, stage: str):
        sec = elapsed_ms / 1000.0
        with self.lock:
            self.requests[(stage, label, code, classify(code, ok))] = \
                self.requests.get((stage, label, code, classify(code, ok)), 0) + 1
            key = (stage, label)
            buckets = self.hist.setdefault(key, [0] * (len(BUCKETS) + 1))
            for i, b in enumerate(BUCKETS):
                if sec <= b:
                    buckets[i] += 1
            buckets[-1] += 1
            self.hist_sum[key] = self.hist_sum.get(key, 0.0) + sec
            self.connect_sum[key] = self.connect_sum.get(key, 0.0) + connect_ms / 1000.0
            self.ttfb_sum[key] = self.ttfb_sum.get(key, 0.0) + ttfb_ms / 1000.0
            self.bytes_total += nbytes
            if threads is not None:
                # Several JMeter processes can write here: the active users of this generator are the sum of
                # the threads each one reports.
                self.threads_by_file[source] = (threads, time.time())
            if stage == "main" and ts_ms > 0 and (self.first_sample_ts is None or ts_ms / 1000.0 < self.first_sample_ts):
                self.first_sample_ts = ts_ms / 1000.0

    def render(self, state: int) -> str:
        c = ",".join(f'{k}="{_esc(v)}"' for k, v in self.const.items())
        cp = c + "," if c else ""
        out = io.StringIO()
        w = out.write
        with self.lock:
            w("# HELP morfeo_requests_total Samples completed by the load generator.\n")
            w("# TYPE morfeo_requests_total counter\n")
            for (stage, label, code, result), n in sorted(self.requests.items()):
                w(f'morfeo_requests_total{{{cp}stage="{stage}",endpoint="{_esc(label)}",code="{_esc(code)}",'
                  f'result="{result}"}} {n}\n')
            w("# HELP morfeo_response_time_seconds Response time (JMeter elapsed).\n")
            w("# TYPE morfeo_response_time_seconds histogram\n")
            for (stage, label), buckets in sorted(self.hist.items()):
                lab = f'{cp}stage="{stage}",endpoint="{_esc(label)}"'
                for i, b in enumerate(BUCKETS):
                    w(f'morfeo_response_time_seconds_bucket{{{lab},le="{b:g}"}} {buckets[i]}\n')
                w(f'morfeo_response_time_seconds_bucket{{{lab},le="+Inf"}} {buckets[-1]}\n')
                w(f"morfeo_response_time_seconds_sum{{{lab}}} {self.hist_sum[(stage, label)]:.6f}\n")
                w(f"morfeo_response_time_seconds_count{{{lab}}} {buckets[-1]}\n")
            w("# HELP morfeo_connect_time_seconds_sum Time spent opening TCP/TLS connections (JMeter Connect).\n")
            w("# TYPE morfeo_connect_time_seconds_sum counter\n")
            for (stage, label), v in sorted(self.connect_sum.items()):
                w(f'morfeo_connect_time_seconds_sum{{{cp}stage="{stage}",endpoint="{_esc(label)}"}} {v:.6f}\n')
            w("# HELP morfeo_first_byte_seconds_sum Time until the first response byte (JMeter Latency).\n")
            w("# TYPE morfeo_first_byte_seconds_sum counter\n")
            for (stage, label), v in sorted(self.ttfb_sum.items()):
                w(f'morfeo_first_byte_seconds_sum{{{cp}stage="{stage}",endpoint="{_esc(label)}"}} {v:.6f}\n')
            w("# HELP morfeo_bytes_received_total Response bytes received by the load generator.\n")
            w("# TYPE morfeo_bytes_received_total counter\n")
            w(f"morfeo_bytes_received_total{{{c}}} {self.bytes_total}\n")
            w("# HELP morfeo_active_threads Virtual users currently active in this generator.\n")
            w("# TYPE morfeo_active_threads gauge\n")
            now = time.time()
            active = sum(n for n, seen in self.threads_by_file.values() if now - seen < STALE_S)
            w(f"morfeo_active_threads{{{c}}} {active if state == 1 else 0}\n")
            w("# HELP morfeo_generator_state 0 waiting, 1 running, 2 finished.\n")
            w("# TYPE morfeo_generator_state gauge\n")
            w(f"morfeo_generator_state{{{c}}} {state}\n")
            if self.first_sample_ts is not None:
                w("# HELP morfeo_first_sample_seconds Unix time of the first request of the load (not of the setUp).\n")
                w("# TYPE morfeo_first_sample_seconds gauge\n")
                w(f"morfeo_first_sample_seconds{{{c}}} {self.first_sample_ts:.3f}\n")
            w("# TYPE morfeo_exporter_clock_seconds gauge\n")
            w(f"morfeo_exporter_clock_seconds{{{c}}} {time.time():.3f}\n")
            w("# TYPE morfeo_exporter_parse_errors_total counter\n")
            w(f"morfeo_exporter_parse_errors_total{{{c}}} {self.parse_errors}\n")
        return out.getvalue()


class Meta:
    """The requested load over time and the objectives of the scenario (the bundle file morfeo-meta.json). Generator
    0 publishes them as totals while the test runs, so that Grafana can draw 'requested vs actual' and the SLO lines."""

    def __init__(self, path: str | None):
        self.path = path
        self.data: dict | None = None

    def load(self) -> dict:
        if self.data is None and self.path and os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    self.data = json.load(fh)
            except (OSError, ValueError):
                return {}   # maybe being written right now: try again next time
        return self.data or {}

    def render(self, state: int, labels: str, started: float | None) -> str:
        meta = self.load()
        if state != 1 or not meta or started is None:
            return ""
        i = int(time.time() - started)
        plan = meta.get("plan") or []
        _, users, rps, arrivals = plan[i] if 0 <= i < len(plan) else (0, 0, 0, 0)
        out = io.StringIO()
        w = out.write
        braces = "{" + labels + "}"
        w("# HELP morfeo_planned_users Virtual users the scenario asks for now (all generators).\n")
        w("# TYPE morfeo_planned_users gauge\n")
        w(f"morfeo_planned_users{braces} {users}\n")
        w("# HELP morfeo_planned_rps Requests per second the scenario asks for now (throughput timers).\n")
        w("# TYPE morfeo_planned_rps gauge\n")
        w(f"morfeo_planned_rps{braces} {rps}\n")
        w("# HELP morfeo_planned_arrivals Arrivals per second the scenario asks for now (open model).\n")
        w("# TYPE morfeo_planned_arrivals gauge\n")
        w(f"morfeo_planned_arrivals{braces} {arrivals}\n")
        slo = meta.get("slo") or {}
        prefix = labels + "," if labels else ""
        w("# HELP morfeo_slo_latency_seconds Latency objective of the application.\n")
        w("# TYPE morfeo_slo_latency_seconds gauge\n")
        for q, key in (("0.95", "p95_ms"), ("0.99", "p99_ms")):
            if slo.get(key) is not None:
                w(f'morfeo_slo_latency_seconds{{{prefix}quantile="{q}"}} {float(slo[key]) / 1000.0}\n')
        if slo.get("error_rate_pct") is not None:
            w("# TYPE morfeo_slo_error_ratio gauge\n")
            w(f"morfeo_slo_error_ratio{braces} {float(slo['error_rate_pct']) / 100.0}\n")
        return out.getvalue()


class JtlTailer(threading.Thread):
    """Follows every kpi*.jtl under `root`."""

    def __init__(self, root: str, metrics: Metrics, interval: float = 1.0):
        super().__init__(daemon=True)
        self.root, self.m, self.interval = root, metrics, interval
        self.files: dict[str, dict] = {}   # path -> {pos, header, partial}

    def run(self):
        while True:
            for path in glob.glob(os.path.join(self.root, "**", "kpi*.jtl"), recursive=True):
                state = self.files.setdefault(path, {"pos": 0, "header": None, "partial": ""})
                with contextlib.suppress(OSError):
                    self._consume(path, state)
            time.sleep(self.interval)

    def _consume(self, path: str, state: dict):
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            fh.seek(state["pos"])
            chunk = fh.read()
            state["pos"] = fh.tell()
        if not chunk:
            return
        lines = (state["partial"] + chunk).split("\n")
        state["partial"] = lines.pop()   # the last element is an incomplete line (or "")
        for row in csv.reader([ln for ln in lines if ln.strip()]):
            if state["header"] is None:
                state["header"] = {name.strip(): i for i, name in enumerate(row)}
                continue
            self._observe(row, state["header"], path)

    def _observe(self, row: list[str], h: dict[str, int], source: str):
        def get(key, default=""):
            return row[h[key]] if key in h and h[key] < len(row) else default
        try:
            if get("responseMessage").startswith(TRANSACTION):
                return
            threads = get("allThreads")
            self.m.observe(
                label=get("label", "?"), code=get("responseCode", "?") or "?",
                ok=get("success", "false").strip().lower() == "true", elapsed_ms=float(get("elapsed", "0") or 0),
                nbytes=int(float(get("bytes", "0") or 0)), threads=int(threads) if threads.strip().isdigit() else None,
                ts_ms=float(get("timeStamp", "0") or 0), source=source, connect_ms=float(get("Connect", "0") or 0),
                ttfb_ms=float(get("Latency", "0") or 0), stage=self.m.stage_of(get("threadName")))
        except (ValueError, IndexError):
            with self.m.lock:
                self.m.parse_errors += 1


def generator_state(work: str) -> int:
    if os.path.exists(os.path.join(work, ".done")):
        return 2
    if os.path.exists(os.path.join(work, ".go")):
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="folder where JMeter writes its results")
    ap.add_argument("--work", required=True, help="work folder with the .go and .done markers")
    ap.add_argument("--port", type=int, default=9270)
    ap.add_argument("--label", action="append", default=[], help="constant label k=v (repeatable)")
    ap.add_argument("--meta", default=None, help="morfeo-meta.json: stages, requested load and objectives")
    args = ap.parse_args()

    const = dict(kv.split("=", 1) for kv in args.label if "=" in kv)
    metrics = Metrics(const, {}, lambda: Meta(args.meta).load().get("stages"))
    JtlTailer(args.dir, metrics).start()
    published = Meta(args.meta if const.get("generator", "0") == "0" else None)
    run_labels = ",".join(f'{k}="{_esc(v)}"' for k, v in const.items() if k != "generator")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/metrics"):
                state = generator_state(args.work)
                body = (metrics.render(state) + published.render(state, run_labels, metrics.first_sample_ts)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
            elif self.path.startswith("/healthz"):
                body = b"ok"
                self.send_response(200)
            else:
                body = b"not found"
                self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):   # keeps the pod log clean
            pass

    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
