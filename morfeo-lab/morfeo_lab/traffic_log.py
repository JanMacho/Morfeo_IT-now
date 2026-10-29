"""The traffic log of the application, in the MORFEO format (JSON Lines, one object per request):

    {"ts_end":"2026-10-01T09:25:34.548091Z","method":"POST","path":"/api/retirada","status":400,
     "latency_ms":0.734,"client_id":"user_002","session_id":"4ce8..."}

`ts_end` is the moment the request ended, in UTC. The lab collects the log of every replica, keeps the lines written
during the test in time order (the input of the AI team's log analyser) and compares it with what the load generators
measured:

  * completeness  every answered request should be one line of the log;
  * server time   `latency_ms`, measured inside the application;
  * outside time  the time the generator saw minus the time inside the application: queueing before the application
                  took the request (accept queue, busy web threads), the network and the generator itself.

A log in any other format is returned untouched and is not analysed.
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

KEYS = ("ts_end", "method", "path", "status", "latency_ms")
MIN_LINES = 20
MIN_VALID = 0.95   # share of the lines that must parse for the file to count as being in this format


def _parse(line: bytes) -> dict | None:
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row if isinstance(row, dict) and all(k in row for k in KEYS) else None


def to_table(data: bytes) -> pd.DataFrame | None:
    """The lines of a log as a table (ts, method, path, status, ms, client, session), or None when most of them are
    not in the MORFEO format."""
    rows, bad = [], 0
    for line in data.splitlines():
        if not line.strip():
            continue
        row = _parse(line)
        if row is None:
            bad += 1
        else:
            rows.append(row)
    if not rows or bad > (1 - MIN_VALID) * (len(rows) + bad):
        return None
    df = pd.DataFrame(rows)
    return pd.DataFrame({
        "ts": pd.to_datetime(df["ts_end"], utc=True, format="ISO8601").map(pd.Timestamp.timestamp),
        "method": df["method"].astype(str).str.upper(),
        "path": df["path"].astype(str).str.split("?").str[0],
        "status": pd.to_numeric(df["status"], errors="coerce"),
        "ms": pd.to_numeric(df["latency_ms"], errors="coerce"),
    }).dropna()


def merge(chunks: list[bytes], start: float | None, end: float | None) -> tuple[bytes, int, bool]:
    """The lines of every replica between `start` and `end` (epoch seconds), in time order. Returns the log, its
    number of lines and whether it was in the MORFEO format. A log in another format is concatenated as it is."""
    kept: list[tuple[float, bytes]] = []
    foreign = False
    for data in chunks:
        lines = [ln for ln in data.splitlines() if ln.strip()]
        table = to_table(data) if lines else None
        if lines and table is None:
            foreign = True
            continue
        for ln in lines:
            row = _parse(ln)
            if row is None:
                continue
            ts = pd.Timestamp(row["ts_end"]).timestamp()
            if (start is None or ts >= start) and (end is None or ts <= end):
                kept.append((ts, ln))
    if foreign:
        return b"".join(ln + b"\n" for data in chunks for ln in data.splitlines() if ln.strip()), 0, False
    kept.sort(key=lambda x: x[0])
    return b"".join(ln + b"\n" for _, ln in kept), len(kept), True


def _matcher(key: str) -> tuple[str, re.Pattern] | None:
    """('GET', regex) for an endpoint key such as 'GET /api/saldo/{id}'."""
    method, _, path = key.partition(" ") if " /" in key else ("ANY", "", key)
    if not path.startswith("/"):
        return None
    parts = ["[^/]+" if re.fullmatch(r"\{[^}]*\}", seg) else re.escape(seg) for seg in path.split("?")[0].split("/")]
    return method.upper(), re.compile("^" + "/".join(parts) + "/?$")


def compare(table: pd.DataFrame | None, endpoints: list[dict], start: float, end: float,
            others: dict[str, tuple[int, int]] | None = None) -> dict:
    """The log against the figures of the generators, per endpoint. `endpoints` are the generator-side endpoint
    figures of the load; `start` and `end` delimit it with a margin for clock differences. The log also holds the
    requests of the setUp and tearDown that fall inside that margin: `others` counts them per endpoint as
    (setUp, tearDown) (the generators know them exactly). The setUp runs before the load and the tearDown after it,
    so they are the first and the last lines of the endpoint and are left out."""
    if table is None:
        return {"available": False, "reason": "the application has no traffic log in the MORFEO format"}
    inside = table[(table["ts"] >= start) & (table["ts"] <= end)]
    if len(inside) < MIN_LINES:
        return {"available": False, "reason": "the traffic log has too few lines in the load stage"}
    matchers = [(e["key"], *m) for e in endpoints if (m := _matcher(e["key"]))]
    # the most specific template first: /api/x/new must not count as /api/x/{id}
    matchers.sort(key=lambda x: (x[2].pattern.count("[^/]+"), x[1] == "ANY", -len(x[2].pattern)))
    per: dict[str, pd.DataFrame] = {}
    unmatched = 0
    assigned = pd.Series("", index=inside.index, dtype=object)
    for key, method, rx in matchers:
        free = inside["path"].map(lambda p, rx=rx: bool(rx.match(p))) & (assigned == "")
        if method != "ANY":
            free &= inside["method"] == method
        assigned[free] = key
    for key in {m[0] for m in matchers}:
        sel = inside[assigned == key]
        if len(sel):
            per[key] = sel
    unmatched = int((assigned == "").sum())
    rows, answered_total, logged_total, outside_load = [], 0, 0, 0
    for e in endpoints:
        answered = e["requests"] - e["by_class"].get("network", 0)
        answered_total += answered
        sel = per.get(e["key"])
        if sel is None:
            rows.append({"key": e["key"], "client_answered": answered, "logged": 0})
            continue
        before, after = (others or {}).get(e["key"], (0, 0))
        if before + after < len(sel):
            outside_load += before + after
            sel = sel.sort_values("ts").iloc[before:len(sel) - after]
        ms = sel["ms"].to_numpy(dtype=float)
        logged_total += len(sel)
        inside_ms = {"mean": round(float(ms.mean()), 3), "p50": _p(ms, 50), "p95": _p(ms, 95), "p99": _p(ms, 99)}
        client = e["latency_ms"]
        row = {
            "key": e["key"], "client_answered": answered, "logged": len(sel),
            "status_codes": {str(k): int(v) for k, v in sel["status"].astype(int).value_counts().sort_index().items()},
            "inside_ms": inside_ms,
            "outside_ms": {q: (round(client[q] - inside_ms[q], 3) if client.get(q) is not None else None)
                           for q in ("p50", "p95")},
        }
        if answered and abs(row["logged"] / answered - 1) > 0.05:   # the two only compare when they saw the same requests
            row["outside_ms"] = {"p50": None, "p95": None}
        rows.append(row)
    completeness = round(logged_total / answered_total, 4) if answered_total else None
    return {"available": True, "lines": len(inside) - outside_load, "unmatched_lines": unmatched, "client_answered": answered_total,
            "logged": logged_total, "completeness": completeness, "endpoints": rows}


def _p(a: np.ndarray, q: float) -> float | None:
    return round(float(np.percentile(a, q)), 3) if len(a) else None
