"""summary.json: everything the lab learned about one scenario, in the shape documented in docs/SUMMARY.md.

This is the contract with the AI team. Its `schema_version` changes only when a key is renamed, removed or changes
meaning (adding a key does not change it). docs/summary.schema.json describes it.

Conventions: rates are fractions of the requests (0 to 1) and use the names of profile.json (`error_rate` is the
share of 4xx answers, `server_error_rate` the share of server failures); times are milliseconds; moments are ISO 8601
in UTC; endpoints are keyed like endpoints.json and profile.json ('GET /api/saldo/{id}').
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pandas as pd

from .. import __version__, traffic_log
from . import capacity, checks, diagnosis, server
from .jtl import CLASSES
from .stats import chart_series, counts, noise_windows, percentile_curve, timeseries, window_stats

SCHEMA_VERSION = 1
KEYED = re.compile(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|ANY) /")


def endpoint_key(label: str, methods: list[str] | None, key: str | None) -> str:
    """'GET /api/saldo/{id}': the key that endpoints.json, profile.json and the traffic log of the application use.
    It comes from the plan when it knows the method and the path of the request."""
    if key:
        return key
    if KEYED.match(str(label)):
        return str(label)
    method = methods[0] if methods and len(methods) == 1 else ("ANY" if methods else "GET")
    return f"{method} {label}"


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _stage_summary(df: pd.DataFrame) -> dict:
    n = len(df)
    by = counts(df["cls"]) if n else {}
    failed = sum(by.get(c, 0) for c in ("server_5xx", "network", "assertion"))
    duration = float((df["ts"] + df["elapsed"] / 1000.0).max() - df["ts"].min()) if n else 0.0
    return {"requests": n, "failed_requests": failed, "duration_s": round(duration, 3),
            "by_class": {c: by.get(c, 0) for c in CLASSES}}


def _endpoints(main: pd.DataFrame, duration: float, meta: dict, slo: dict) -> list[dict]:
    labels = meta["labels"]
    out = []
    for label, g in main.groupby("label", sort=False, observed=True):
        label = str(label)
        s = window_stats(g, duration)
        key = endpoint_key(label, labels["methods"].get(label), labels["keys"].get(label))
        method, _, path = key.partition(" ")
        s.update(key=key, label=label, method=method, path=path, share=round(len(g) / len(main), 6),
                 status_codes=counts(g["code"]))
        objective = checks.endpoint_slo(slo, label, key)
        s["slo_p95_ms"] = objective.get("p95_ms", slo.get("p95_ms"))
        out.append(s)
    out.sort(key=lambda e: -e["requests"])
    return out


def _other_stages(df: pd.DataFrame, t_from: float, t_to: float, labels: dict) -> dict[str, tuple[int, int]]:
    """Answered requests of the setUp and of the tearDown that ended between `t_from` and `t_to`, per endpoint. The
    traffic log of the application holds them too, so they must not be taken for load when the two are compared."""
    other = df[(df["stage"] != "main") & (df["cls"] != "network")]
    end = other["ts"] + other["elapsed"] / 1000.0
    other = other[(end >= t_from) & (end <= t_to)]
    out: dict[str, list[int]] = {}
    for (label, stage), n in other.groupby(["label", "stage"], observed=True).size().items():
        key = endpoint_key(str(label), labels["methods"].get(str(label)), labels["keys"].get(str(label)))
        out.setdefault(key, [0, 0])[0 if stage == "setup" else 1] += int(n)
    return {k: (v[0], v[1]) for k, v in out.items()}


def _errors(main: pd.DataFrame, t0: float) -> list[dict]:
    bad = main[main["cls"] != "ok"]
    if not len(bad):
        return []
    rows = []
    for (cls, label, code, message), g in bad.groupby(["cls", "label", "code", "message"], dropna=False, observed=True):
        rows.append({"class": str(cls), "label": str(label), "code": str(code), "message": str(message or "")[:200],
                     "count": len(g), "first_at_s": round(float(g["ts"].min() - t0), 1)})
    rows.sort(key=lambda e: (e["class"] == "client_4xx", -e["count"]))
    return rows[:50]


def build(df: pd.DataFrame, meta: dict, snapshot: dict, log: pd.DataFrame | None,
          profile: dict | None) -> tuple[dict, pd.DataFrame]:
    """The summary of a scenario and its load over time. `df` holds every sample of every generator."""
    slo, scenario = meta["slo"], meta["scenario"]
    setup = _stage_summary(df[df["stage"] == "setup"])
    teardown = _stage_summary(df[df["stage"] == "teardown"])
    main = df[df["stage"] == "main"]
    head = {
        "schema_version": SCHEMA_VERSION, "producer": "morfeo-lab", "lab_version": __version__,
        "run_id": meta["run_id"], "started_at": meta["started_at"], "wall_clock_s": meta["wall_clock_s"],
        "interrupted": meta["interrupted"], "scenario": scenario, "application": meta["application"], "slo": slo,
        "generators": meta["generators"], "links": meta["links"], "pods": meta["pods"], "baseline": meta.get("baseline"),
    }
    server_metrics = server.summarize(snapshot)
    if main.empty:
        empty = window_stats(main, 0)
        empty["max_users"] = None
        cks = checks.evaluate(overall=empty, steady=empty, endpoints=[], slo=slo, pods=meta["pods"], setup=setup,
                              server=server_metrics, exit_codes=meta["generators"]["exit_codes"], scenario=scenario,
                              traffic_log=None, baseline=None)
        return {**head, "verdict": checks.verdict_of(cks), "overall": empty, "steady": empty, "steady_window_s": [0, 0],
                "setup": setup, "teardown": teardown, "endpoints": [], "errors": [],
                "capacity": {"available": False, "reason": "no samples"}, "checks": cks, "diagnosis": [],
                "server": server_metrics, "traffic_log": None, "profile": profile, "timeline": None, "series": {},
                "percentile_curve": {"q": [], "ms": []}, "noise_windows": None}, pd.DataFrame()

    t0 = float(main["ts"].min())
    end = float((main["ts"] + main["elapsed"] / 1000.0).max())
    duration = end - t0
    server_metrics = server.summarize(snapshot, t0, end)
    ramp = float(scenario.get("ramp_up_s") or 0)
    steady_df = main[main["ts"] >= t0 + ramp] if ramp and duration - ramp >= 10 else main
    steady_from = float(steady_df["ts"].min() - t0) if len(steady_df) else 0.0

    overall = window_stats(main, duration)
    overall["status_codes"] = counts(main["code"])
    resolution = max(1, -(-int(duration) // 600))
    series_table = timeseries(main, t0, resolution)
    overall["max_users"] = None if series_table["users"].isna().all() else float(series_table["users"].max())
    steady = window_stats(steady_df, end - float(steady_df["ts"].min()) if len(steady_df) else 0)
    endpoints = _endpoints(main, duration, meta, slo)
    errors = _errors(main, t0)
    cap = capacity.analyze(main, t0, slo, scenario, meta["timeline"])
    log_cmp = None
    if meta.get("traffic_log"):
        log_cmp = traffic_log.compare(log, endpoints, t0 - 1.0, end + 2.0,
                                      _other_stages(df, t0 - 1.0, end + 2.0, meta["labels"]))
    cks = checks.evaluate(overall=overall, steady=steady, endpoints=endpoints, slo=slo, pods=meta["pods"], setup=setup,
                          server=server_metrics, exit_codes=meta["generators"]["exit_codes"], scenario=scenario,
                          traffic_log=log_cmp, baseline=meta.get("baseline"))
    findings = diagnosis.diagnose(
        overall=overall, steady=steady, setup=setup, server=server_metrics, pods=meta["pods"], capacity=cap,
        scenario=scenario, errors=errors, traffic_log=log_cmp, profile=profile, slo=slo,
        env_keys=(meta["application"].get("env") or {}).keys(),
        splittable=meta["generators"]["distribution"]["mode"] == "split")
    result = {
        **head, "verdict": checks.verdict_of(cks), "overall": overall, "steady": steady,
        "steady_window_s": [round(steady_from, 1), round(duration, 1)], "setup": setup, "teardown": teardown,
        "endpoints": endpoints, "errors": errors, "capacity": cap, "checks": cks, "diagnosis": findings,
        "server": server_metrics, "traffic_log": log_cmp, "profile": profile,
        "timeline": {"start": iso(t0), "end": iso(end), "start_epoch": t0, "end_epoch": end,
                     "resolution_s": resolution},
        "series": chart_series(main, t0, duration, meta["timeline"]),
        "percentile_curve": percentile_curve(main),
        "noise_windows": noise_windows(main, t0 + steady_from),
    }
    return result, series_table
