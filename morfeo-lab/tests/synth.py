"""Synthetic results for the analysis and report tests: a plan with one thread group per load level, the JMeter
result files such a run leaves, the metrics Prometheus would have kept and the application's traffic log."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from morfeo_lab import scenario
from tests import jmxkit as k

PLUGINS = ["jpgc-casutg", "jpgc-tst", "jpgc-functions", "jpgc-dummy"]
LEVELS = (10, 20, 40, 80)
SECONDS = 40
START = 1_760_000_000.0   # an arbitrary start (epoch seconds)
HEADER = ["timeStamp", "elapsed", "label", "responseCode", "responseMessage", "threadName", "success", "allThreads",
          "bytes", "Connect"]
SLO = {"p95_ms": 300, "p99_ms": 1000, "error_rate_pct": 1.0, "min_throughput_rps": None, "endpoints": {}}


def write_plan(folder: Path) -> Path:
    """One thread group per level, run one after the other."""
    groups = [k.thread_group(f"{n} users", n, 5, duration=SECONDS, delay=i * SECONDS,
                             children=[k.http("GET /api/saldo/{id}", "/api/saldo/${id}"),
                                       k.http("POST /api/ingreso", "/api/ingreso", method="POST", body="{}")])
              for i, n in enumerate(LEVELS)]
    path = folder / "levels.jmx"
    path.write_text(k.plan("Levels", groups), encoding="utf-8")
    return path


def scenario_of(folder: Path) -> scenario.Scenario:
    return scenario.load_scenario(write_plan(folder), installed_plugins=PLUGINS)


def write_jtl(raw_dir: Path, seed: int = 7, generator: int = 0) -> int:
    """Samples of the four levels: latency grows with the load and the highest one starts to fail."""
    rng = np.random.default_rng(seed)
    folder = raw_dir / f"generator-{generator}"
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(folder / "kpi.jtl", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for i, users in enumerate(LEVELS):
            start = START + i * SECONDS
            for sec in range(SECONDS):
                for u in range(users):
                    label = "GET /api/saldo/{id}" if u % 2 == 0 else "POST /api/ingreso"
                    base = 20 + users * (1 if users < 80 else 12)
                    elapsed = max(1, int(rng.normal(base, base * 0.1)))
                    code, ok = "200", "true"
                    if users >= 80 and rng.random() < 0.08:
                        code, ok = "503", "false"
                    elif label.startswith("POST") and rng.random() < 0.02:
                        code, ok = "400", "false"
                    ts = int((start + sec + rng.random()) * 1000)
                    w.writerow([ts, elapsed, label, code, "OK", f"{users} users {i + 1}-{u + 1}", ok, users, 120, 1])
                    n += 1
    return n


def snapshot(t0: float, t1: float, throttled: float = 0.0) -> dict:
    """The series Prometheus would return for a run between t0 and t1 (every 5 s)."""
    times = np.arange(t0 - 30, t1 + 10, 5)

    def series(v):
        return [(float(t), float(v)) for t in times]

    return {"app_cpu_cores": series(0.8), "app_cpu_limit_cores": series(1.0),
            "app_cpu_throttled_ratio": series(throttled), "app_memory_bytes": series(5e8),
            "app_memory_limit_bytes": series(8e8), "jvm_heap_used_bytes": series(2e8),
            "jvm_heap_max_bytes": series(6e8), "web_threads_busy": series(20), "web_threads_max": series(200)}


def traffic_log(rows: int = 400, t0: float = START, spread: float = 40.0) -> bytes:
    """Lines in the MORFEO format of the application's traffic log."""
    lines = []
    for i in range(rows):
        ts = datetime.fromtimestamp(t0 + spread * i / rows, tz=timezone.utc)
        lines.append(json.dumps({"ts_end": ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", "method": "GET",
                                 "path": f"/api/saldo/{i % 50}", "status": 200, "latency_ms": 12 + i % 7,
                                 "client_id": f"c{i % 20}", "session_id": f"s{i % 20}"}))
    return ("\n".join(lines) + "\n").encode()


def meta_of(s: scenario.Scenario, **over) -> dict:
    info = s.info()
    meta = {
        "run_id": "20251014-120000", "started_at": "2025-10-14T12:00:00.000000Z", "wall_clock_s": 160.0,
        "interrupted": False, "scenario": info, "timeline": s.plan.timeline(1.0),
        "stages": {g.name: g.role for g in s.plan.groups},
        "labels": {"keys": s.label_keys, "methods": s.label_methods},
        "application": {"name": "demo", "description": "", "replicas": 1, "resources": {}, "env": {}, "image": None},
        "slo": dict(SLO),
        "generators": {"count": 1, "why": "test", "exit_codes": [0],
                       "distribution": {"mode": s.distribution.mode, "reasons": s.distribution.reasons}},
        "pods": {"replicas": 1, "restarts_during_run": 0, "last_termination_reason": None, "after": []},
        "links": {"jmeter_report": None, "grafana": None, "flamegraph": None, "app_logs": "app/"},
        "traffic_log": None, "profile": None, "baseline": None,
    }
    meta.update(over)
    return meta
