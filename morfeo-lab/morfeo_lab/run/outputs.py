"""The files a scenario leaves in results/<run>/<scenario>/ and the summary shown in the terminal. `finalize` is also
what `morfeo-lab report` runs again to rebuild everything from the raw data."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import numpy as np

from .. import analysis, traffic_log
from ..console import SYM, bold, color, dim
from ..paths import rel
from ..units import ms, num

VERDICT_TEXT = {"PASS": "Passed", "FAIL": "Failed", "PASS_WITH_WARNINGS": "Passed with warnings", "ERROR": "Not run"}
TRAFFIC_FILE = "app/traffic.jsonl"


def json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if o != o else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return str(o)


def build_traffic_log(out_dir: Path, meta: dict, t_from: float, t_to: float):
    """The traffic log of the application: the lines every replica wrote during the test, in time order. Returns the
    table of its lines, or None when the application has no log in the MORFEO format."""
    files = (meta.get("traffic_log") or {}).get("files") or {}
    if not files:
        return None
    first = next(iter(files.values()))
    chunks = [(out_dir / "app" / p).read_bytes() for p in first if (out_dir / "app" / p).exists()]
    merged, lines, in_format = traffic_log.merge(chunks, t_from, t_to)
    if not in_format or not lines:
        return None
    (out_dir / TRAFFIC_FILE).write_bytes(merged)
    return traffic_log.to_table(merged)


def finalize(out_dir: Path, meta: dict, snapshot: dict, *, quiet: bool = False) -> dict:
    """Analysis and every output file of one scenario."""
    df = analysis.load_jtl(out_dir / "raw", meta["stages"])
    table = None
    if len(df):
        table = build_traffic_log(out_dir, meta, float(df["ts"].min()) - 1.0,
                                  float((df["ts"] + df["elapsed"] / 1000.0).max()) + 2.0)
    summary, series = analysis.build(df, meta, snapshot, table, meta.get("profile"))
    if summary.get("traffic_log") and summary["traffic_log"].get("available"):
        summary["traffic_log"]["file"] = TRAFFIC_FILE
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=json_default), encoding="utf-8")
    (out_dir / "app-metrics.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=json_default, ensure_ascii=False),
                                          encoding="utf-8")
    if len(series):
        series.to_csv(out_dir / "timeseries.csv", index=False)
    write_endpoints_csv(out_dir / "endpoints.csv", summary["endpoints"])
    write_junit(out_dir / "junit.xml", meta["scenario"]["name"], summary["checks"])
    if not quiet:
        print_summary(summary, out_dir)
    return summary


def write_endpoints_csv(path: Path, endpoints: list[dict]):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["endpoint", "label", "requests", "share", "throughput_rps", "error_rate", "server_error_rate",
                    "mean_ms", "p50_ms", "p90_ms", "p95_ms", "p99_ms", "p99_9_ms", "max_ms", "slo_p95_ms"])
        for e in endpoints:
            lat = e["latency_ms"]
            w.writerow([e["key"], e["label"], e["requests"], e["share"], e["throughput_rps"], e["error_rate"],
                        e["server_error_rate"], lat["mean"], lat["p50"], lat["p90"], lat["p95"], lat["p99"],
                        lat["p99_9"], lat["max"], e.get("slo_p95_ms")])


def write_junit(path: Path, name: str, checks: list[dict]):
    """One test case per acceptance criterion, for CI."""
    failures = sum(c["status"] == "fail" for c in checks)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<testsuite name={quoteattr("morfeo." + name)} tests="{len(checks)}" failures="{failures}">']
    for c in checks:
        lines.append(f'  <testcase classname={quoteattr(name)} name={quoteattr(c["name"])}>')
        if c["status"] == "fail":
            message = f"expected {c['target']}, got {c['actual']}"
            lines.append(f"    <failure message={quoteattr(message)}/>")
        elif c["status"] == "warn":
            lines.append(f"    <system-out>{escape(str(c['detail']))} (expected {escape(str(c['target']))}, "
                         f"got {escape(str(c['actual']))})</system-out>")
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    path.write_text("\n".join(lines), encoding="utf-8")


def verdict_badge(v: str) -> str:
    code = {"PASS": "1;97;42", "FAIL": "1;97;41", "ERROR": "1;97;41"}.get(v, "1;30;43")
    return color(code, f" {VERDICT_TEXT.get(v, v)} ")


def print_summary(sm: dict, out_dir: Path):
    """The result of one scenario in the terminal: verdict, key figures, criteria, what happened and where to look."""
    o, st = sm["overall"], sm["steady"]
    w = st if st["requests"] else o
    lat = w["latency_ms"]
    sc = sm["scenario"]
    print()
    print(f"  {verdict_badge(sm['verdict'])}  {bold(sc.get('title') or sc['name'])}"
          + (color("33", "  · partial results (interrupted)") if sm.get("interrupted") else ""))
    if o["requests"]:
        bits = [f"{num(o['requests'])} requests", f"{num(w['throughput_rps'], 1)} req/s", f"p95 {ms(lat['p95'])}",
                f"p99 {ms(lat['p99'])}", f"server errors {num(w['server_error_rate'] * 100, 2, '%')}"]
        if o["error_rate"]:
            bits.append(f"4xx {num(o['error_rate'] * 100, 2, '%')}")
        print("    " + dim(" · ").join(bits))
    if sm["checks"]:
        print("\n    " + bold("Acceptance criteria"))
        name_w = min(34, max(len(c["name"]) for c in sm["checks"]))
        actual_w = max(len(str(c["actual"])) for c in sm["checks"])
        for c in sm["checks"]:
            mark = {"pass": color("32", SYM["ok"]), "fail": color("1;31", SYM["err"]),
                    "warn": color("33", SYM["warn"])}[c["status"]]
            name = c["name"] if len(c["name"]) <= name_w else c["name"][:name_w - 1] + "…"
            print(f"      {mark} {name:<{name_w}}  {c['actual']!s:>{actual_w}}  {dim('target ' + str(c['target']))}")
    cap = sm.get("capacity") or {}
    if cap.get("available"):
        sus, brk = cap.get("sustainable"), cap.get("breaking")
        print("\n    " + bold("Capacity"))
        print(f"      within the objectives up to {_level(sus, cap)}; outside from {_level(brk, cap)}"
              if brk else f"      within the objectives at every level tested (up to {_level(sus, cap)})")
    findings = [d for d in sm.get("diagnosis", []) if d["severity"] != "good"][:4] or sm.get("diagnosis", [])[:1]
    if findings:
        print("\n    " + bold("Diagnosis"))
        for d in findings:
            col = {"critical": "1;31", "serious": "31", "warning": "33", "info": "36", "good": "32"}[d["severity"]]
            print(f"      {color(col, SYM['dot'])} {d['title']}")
    links = sm.get("links") or {}
    print()
    if links.get("jmeter_report"):
        print(f"    {dim('JMeter report')}  {rel(out_dir / links['jmeter_report'])}")
    if links.get("flamegraph"):
        print(f"    {dim('Flame graph  ')}  {rel(out_dir / links['flamegraph'])}")
    if links.get("grafana"):
        print(f"    {dim('Grafana      ')}  {links['grafana']}")


def _level(row: dict | None, cap: dict) -> str:
    if not row:
        return "the first level"
    if cap["basis"] == "thread_group":
        return f"'{row['label']}' ({num(row['goodput_rps'], 1)} req/s, p95 {ms(row['p95_ms'])})"
    return f"{row['label']} ({num(row['goodput_rps'], 1)} req/s, p95 {ms(row['p95_ms'])})"
