"""Where the application stops coping: the figures at every load level the plan went through.

A level is a stretch of the test with a constant load. There are two ways to find them:

  thread groups  the plan runs its thread groups one after another (for example one group per load step). Each group
                 is a level, whatever the group does inside (a script, an arrival process...).
  over time      one group whose load changes (a ramp, steps of a Concurrency Thread Group, a Throughput Shaping
                 Timer...). Each distinct load, measured (users) or requested (rate), is a level.

The report says up to which level the application stayed within its objectives, from which level it did not, and
where adding load stopped adding throughput (the knee).
"""
from __future__ import annotations

from itertools import pairwise

import numpy as np
import pandas as pd

from .jtl import FAIL_CLASSES
from .stats import percentile, users_series

MIN_SECONDS = 3
MIN_SAMPLES = 30
MAX_LEVELS = 12


def _quantise(series: pd.Series) -> pd.Series:
    """Map each second to a load level index (-1 = no load). Continuous ramps are cut into MAX_LEVELS levels."""
    distinct = sorted(v for v in series.dropna().unique() if v > 0)
    if len(distinct) > MAX_LEVELS:
        edges = np.linspace(min(distinct), max(distinct), MAX_LEVELS + 1)
        level = pd.Series(np.digitize(series.fillna(0).to_numpy(), edges[1:-1], right=True), index=series.index)
        level[series.fillna(0) <= 0] = -1
        return level
    mapping = {v: i for i, v in enumerate(distinct)}
    return series.map(lambda v: mapping.get(v, -1))


def _row(g: pd.DataFrame, seconds: float, load: float | None, label: str) -> dict:
    answered = g.loc[g["cls"] != "network", "elapsed"].to_numpy(dtype=float)
    failed = int(g["cls"].isin(FAIL_CLASSES).sum())
    n = len(g)
    return {
        "label": label, "load": None if load is None else round(float(load), 2), "seconds": round(float(seconds), 1),
        "requests": n, "throughput_rps": round(n / seconds, 2), "goodput_rps": round((n - failed) / seconds, 2),
        "p50_ms": _round(percentile(answered, 50)), "p95_ms": _round(percentile(answered, 95)),
        "p99_ms": _round(percentile(answered, 99)), "server_error_rate": round(failed / n, 6),
    }


def _round(v):
    return None if v is None else round(v, 2)


def _group_load(scenario: dict, name: str) -> float | None:
    """What the plan asks for in a thread group, at its peak, when it can be known."""
    g = next((x for x in scenario["thread_groups"] if x["name"] == name and x["role"] == "main"), None)
    if g is None:
        return None
    return (g["peak_rps"] if g["model"] == "throughput" else g["peak"]) or None


def _by_thread_group(df: pd.DataFrame, scenario: dict) -> tuple[list[dict], str] | None:
    spans = {}
    for name, g in df.groupby("group", observed=True):
        if len(g):
            spans[str(name)] = (float(g["ts"].min()), float((g["ts"] + g["elapsed"] / 1000.0).max()), g)
    ordered = sorted(spans.items(), key=lambda kv: kv[1][0])
    if len(ordered) < 3:
        return None
    if any(b[1][0] < a[1][1] - 1.0 for a, b in pairwise(ordered)):
        return None   # they overlap: it is one test with several groups, not a sequence of levels
    rows = []
    for name, (start, end, g) in ordered:
        seconds = end - start
        if seconds >= MIN_SECONDS and len(g) >= MIN_SAMPLES:
            rows.append(_row(g, seconds, _group_load(scenario, name), name))
    if len(rows) < 3:
        return None
    unit = {"closed": "users", "throughput": "requests/s", "open": "arrivals/s"}.get(scenario["model"], "step")
    return rows, unit if all(r["load"] is not None for r in rows) else "step"


def _over_time(df: pd.DataFrame, t0: float, scenario: dict, timeline: list[dict]) -> tuple[list[dict], str] | None:
    sec = ((df["ts"] - t0) // 1).astype(int)
    idx = pd.RangeIndex(0, int(sec.max()) + 1)
    if scenario["model"] in ("throughput", "open") and timeline:
        key = "arrivals" if scenario["model"] == "open" else "rps"
        planned = pd.Series([r[key] for r in timeline], dtype=float)
        load = planned.reindex(idx).fillna(0)
        unit = "arrivals/s" if scenario["model"] == "open" else "requests/s"
    else:
        if df["threads"].isna().all():
            return None
        load = users_series(df, sec, idx)
        unit = "users"
    if len([v for v in load.unique() if v > 0]) < 3:
        return None
    level_of_second = _quantise(load)
    d = df.assign(_level=level_of_second.reindex(sec.to_numpy()).to_numpy())
    rows = []
    for lvl, g in d[d["_level"] >= 0].groupby("_level"):
        seconds = int((level_of_second == lvl).sum())
        if seconds >= MIN_SECONDS and len(g) >= MIN_SAMPLES:
            rows.append(_row(g, seconds, float(np.median(load[level_of_second[level_of_second == lvl].index])),
                             f"{np.median(load[level_of_second[level_of_second == lvl].index]):g} {unit}"))
    rows.sort(key=lambda r: r["load"])
    return (rows, unit) if len(rows) >= 3 else None


def analyze(df: pd.DataFrame, t0: float, slo: dict, scenario: dict, timeline: list[dict]) -> dict:
    """The load levels of a run. `df` holds the samples of the load stage only; `scenario` is what the plan asked for
    (Scenario.info()) and `timeline` the load it asked for second by second."""
    if len(df) < 100:
        return {"available": False, "reason": "not enough samples"}
    if scenario["model"] == "mixed":
        return {"available": False, "reason": "several load models at once: there is no single load level to compare"}
    found = _by_thread_group(df, scenario)
    basis = "thread_group"
    if found is None:
        basis = "over_time"
        found = _over_time(df, t0, scenario, timeline)
    if found is None:
        return {"available": False, "reason": "the load stays constant, or there are fewer than 3 levels long enough "
                                              f"(at least {MIN_SECONDS} s and {MIN_SAMPLES} samples each): the "
                                              "plan needs a ramp, steps or one thread group per level"}
    rows, unit = found
    p95_limit, error_limit = slo.get("p95_ms"), slo.get("error_rate_pct")
    # requests per arrival (open model): measured at the lowest level, where the application is not saturated
    per_arrival = None
    if unit == "arrivals/s" and rows[0]["load"]:
        per_arrival = rows[0]["throughput_rps"] / rows[0]["load"]
    rate_unit = unit in ("requests/s", "arrivals/s")

    def within(r) -> bool:
        if p95_limit is not None and (r["p95_ms"] is None or r["p95_ms"] > p95_limit):
            return False
        if error_limit is not None and r["server_error_rate"] * 100 > error_limit:
            return False
        if rate_unit and r["load"]:
            return r["throughput_rps"] >= 0.9 * r["load"] * (per_arrival or 1)
        return True

    sustainable = breaking = None
    for r in rows:
        if rate_unit and r["load"]:
            r["requested_rps"] = round(r["load"] * (per_arrival or 1), 2)
        r["within_slo"] = within(r)
        if breaking is None:
            if r["within_slo"]:
                sustainable = r
            else:
                breaking = r
    knee = None
    for prev, cur in pairwise(rows):
        if prev["goodput_rps"] <= 0 or cur["p95_ms"] is None or prev["p95_ms"] is None:
            continue
        if cur["server_error_rate"] >= 0.5 or cur["p95_ms"] <= prev["p95_ms"] * 1.2:
            continue
        if prev["load"] and cur["load"] and cur["load"] > prev["load"]:
            efficiency = (cur["goodput_rps"] / prev["goodput_rps"] - 1) / (cur["load"] / prev["load"] - 1)
            cur["scaling_efficiency"] = round(efficiency, 3)
            reached = efficiency < 0.3
        else:
            reached = cur["goodput_rps"] / prev["goodput_rps"] - 1 < 0.05
        if reached and knee is None:
            knee = cur
    return {"available": True, "basis": basis, "unit": unit, "levels": rows, "sustainable": sustainable,
            "breaking": breaking, "peak_goodput": max(rows, key=lambda r: r["goodput_rps"]), "saturation_knee": knee,
            "requests_per_arrival": round(per_arrival, 3) if per_arrival else None}
