"""Figures over a set of samples: totals, load and latency over time, and the percentile curve."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .jtl import CLASSES, FAIL_CLASSES

PERCENTILES = (50, 90, 95, 99, 99.9)


def counts(col: pd.Series) -> dict:
    """value_counts without the zero rows that categorical columns add."""
    return {str(k): int(v) for k, v in col.value_counts().items() if v}


def percentile(values: np.ndarray, q: float) -> float | None:
    return float(np.percentile(values, q)) if len(values) else None


def window_stats(df: pd.DataFrame, duration: float) -> dict:
    """Totals of a window. Rates are fractions of the requests (0 to 1); latency is in milliseconds."""
    n = len(df)
    duration = float(duration)
    # The latency of the requests that got an HTTP answer: a refused connection takes about 0 ms and would make a
    # crashing application look faster.
    answered = df[df["cls"] != "network"]
    lat = (answered if len(answered) else df)["elapsed"].to_numpy(dtype=float)
    by = counts(df["cls"]) if n else {}
    failed = sum(by.get(c, 0) for c in FAIL_CLASSES)
    dur = max(duration, 1e-9)
    return {
        "requests": n,
        "duration_s": round(duration, 3),
        "throughput_rps": round(n / dur, 3) if n else 0.0,
        "goodput_rps": round(by.get("ok", 0) / dur, 3) if n else 0.0,
        "failed_requests": failed,
        "server_error_rate": round(failed / n, 6) if n else 0.0,
        "error_rate": round(by.get("client_4xx", 0) / n, 6) if n else 0.0,
        "by_class": {c: by.get(c, 0) for c in CLASSES},
        "latency_ms": {
            "mean": round(float(lat.mean()), 3) if len(lat) else None,
            "min": float(lat.min()) if len(lat) else None,
            "max": float(lat.max()) if len(lat) else None,
            **{f"p{str(q).replace('.', '_')}": (round(percentile(lat, q), 3) if len(lat) else None)
               for q in PERCENTILES},
        },
    }


def users_series(df: pd.DataFrame, bucket: pd.Series, index: pd.Index) -> pd.Series:
    """Active users per bucket: the sum over the JMeter processes (generators) of each one's maximum."""
    if df["threads"].isna().all():
        return pd.Series(np.nan, index=index)
    total = pd.Series(0.0, index=index)
    for _, g in df.groupby("src"):
        s = g["threads"].groupby(bucket.loc[g.index]).max().reindex(index).ffill().fillna(0)
        s[s.index > int(bucket.loc[g.index].max())] = 0   # a process that finished adds nothing any more
        total = total.add(s, fill_value=0)
    return total


def timeseries(df: pd.DataFrame, t0: float, res: int) -> pd.DataFrame:
    bucket = ((df["ts"] - t0) // res).astype(int)
    idx = pd.RangeIndex(0, int(bucket.max()) + 1 if len(bucket) else 0)
    ts = pd.DataFrame(index=idx)
    ts["t"] = idx * res
    ts["requests"] = df.groupby(bucket).size().reindex(idx, fill_value=0)
    ts["failed"] = df["cls"].isin(FAIL_CLASSES).groupby(bucket).sum().reindex(idx, fill_value=0)
    ts["client_4xx"] = df["cls"].eq("client_4xx").groupby(bucket).sum().reindex(idx, fill_value=0)
    ts["rps"] = ts["requests"] / res
    ts["ok_rps"] = (ts["requests"] - ts["failed"]) / res
    ts["server_error_rate"] = np.where(ts["requests"] > 0, ts["failed"] / ts["requests"].clip(lower=1), np.nan)
    answered = df["cls"] != "network"
    grouped = df.loc[answered, "elapsed"].groupby(bucket[answered])
    for q, name in ((0.5, "p50"), (0.95, "p95"), (0.99, "p99")):
        ts[name] = grouped.quantile(q).reindex(idx)
    ts["users"] = users_series(df, bucket, idx)
    return ts.reset_index(drop=True)


def chart_series(df: pd.DataFrame, t0: float, duration: float, requested: list[dict] | None, points: int = 240) -> dict:
    """Load and latency over time for the result pages: at most `points` buckets, with the load the plan asked for in
    the same buckets."""
    res = max(1, math.ceil(duration / points))
    ts = timeseries(df, t0, res)

    def col(values, nd=2):
        return [None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), nd) for v in values]
    out = {"step_s": res, "t": [int(v) for v in ts["t"]], "users": col(ts["users"], 1), "rps": col(ts["rps"]),
           "successful_rps": col(ts["ok_rps"]), "failed_rps": col(ts["failed"] / res),
           "p50_ms": col(ts["p50"]), "p95_ms": col(ts["p95"]), "p99_ms": col(ts["p99"])}
    if requested:
        pl = pd.DataFrame(requested)
        b = (pl["t"] // res).astype(int)
        for key, name in (("users", "requested_users"), ("rps", "requested_rps"), ("arrivals", "requested_arrivals")):
            if key in pl and pl[key].max() > 0:
                out[name] = col(pl[key].groupby(b).mean().reindex(range(len(ts))), 2)
    return out


def noise_windows(df: pd.DataFrame, t_from: float, size: int = 10) -> dict | None:
    """Steady-state figures per window of `size` seconds. `compare` uses them to tell real differences from the
    ordinary variation between windows of one run."""
    d = df[df["ts"] >= t_from]
    if d.empty:
        return None
    w = ((d["ts"] - t_from) // size).astype(int)
    full = w < w.max()   # the last window is usually incomplete
    d, w = d[full], w[full]
    if w.nunique() < 3:
        return None
    failed = d["cls"].isin(FAIL_CLASSES)
    answered = d["cls"] != "network"
    p95 = d.loc[answered, "elapsed"].groupby(w[answered]).quantile(0.95)
    n = d.groupby(w).size()
    good = (n - failed.groupby(w).sum()) / size
    return {"size_s": size, "p95_ms": [round(float(x), 2) for x in p95.reindex(n.index).ffill().fillna(0)],
            "goodput_rps": [round(float(x), 2) for x in good],
            "server_error_rate": [round(float(x), 6) for x in failed.groupby(w).sum() / n]}


def percentile_curve(df: pd.DataFrame) -> dict:
    qs = [0, 50, 75, 90, 95, 99, 99.9, 100]
    answered = df.loc[df["cls"] != "network", "elapsed"].to_numpy(dtype=float)
    return {"q": qs, "ms": [round(float(np.percentile(answered, q)), 3) for q in qs] if len(answered) else []}
