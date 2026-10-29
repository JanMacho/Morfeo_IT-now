"""Estadísticas de analysis.json (docs/contracts.md, sección 3).

Todas las funciones reciben las peticiones ya ordenadas por
``(session_id, seq)``, como las devuelve ``analyze``. Los percentiles usan
interpolación lineal (el método por defecto de numpy y pandas) y los reales
se redondean a 6 decimales para que el fichero sea estable.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np
import pandas as pd

from morfeo.common import format_ts
from morfeo.log_analyzer.sessions import to_ns

TOP_N = 20
_NS_PER_S = 1_000_000_000
_NS_PER_HOUR = 3600 * _NS_PER_S


def rnd(value: float | int | None) -> float | None:
    """Real redondeado a 6 decimales; ``None`` si no es finito."""
    if value is None:
        return None
    f = float(value)
    if not math.isfinite(f):
        return None
    r = round(f, 6)
    return 0.0 if r == 0 else r


def describe(values: Iterable[float] | np.ndarray) -> dict[str, Any]:
    """``Stats`` del contrato: count, mean, p50, p90, p99 y max."""
    arr = np.asarray(values, dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return {"count": 0, "mean": None, "p50": None, "p90": None, "p99": None, "max": None}
    p50, p90, p99 = np.percentile(arr, [50, 90, 99])
    return {
        "count": int(arr.size),
        "mean": rnd(arr.mean()),
        "p50": rnd(p50),
        "p90": rnd(p90),
        "p99": rnd(p99),
        "max": rnd(arr.max()),
    }


def percentiles_with_zeros(active: np.ndarray, n_zero: int, qs: Sequence[float]) -> list[float]:
    """Percentiles lineales de ``active`` más ``n_zero`` ceros, sin crear el array completo.

    Da lo mismo que ``np.percentile(np.r_[np.zeros(n_zero), active], qs)``, pero
    sirve para logs de meses, donde casi todos los segundos tienen 0 peticiones.
    """
    values = np.sort(np.asarray(active, dtype=float))
    n = n_zero + values.size

    def at(i: int) -> float:
        return 0.0 if i < n_zero else float(values[i - n_zero])

    out = []
    for q in qs:
        h = (q / 100.0) * (n - 1)
        lo = int(math.floor(h))
        hi = min(lo + 1, n - 1)
        out.append(at(lo) + (h - lo) * (at(hi) - at(lo)))
    return out


def _session_bounds(session_id: pd.Series) -> np.ndarray:
    """Posición de la primera petición de cada sesión (entrada ordenada por sesión)."""
    sid = session_id.to_numpy(dtype=object)
    first = np.ones(len(sid), dtype=bool)
    first[1:] = sid[1:] != sid[:-1]
    return np.flatnonzero(first)


def _is_client_error(status: pd.Series) -> pd.Series:
    return (status >= 400) & (status < 500)


def _totals(req: pd.DataFrame, start: np.ndarray, end: np.ndarray, n_sessions: int) -> dict[str, Any]:
    clients = req["client_id"]
    return {
        "requests": len(req),
        "sessions": n_sessions,
        "clients": int(clients[clients != ""].nunique()),
        "start": format_ts(pd.Timestamp(int(start.min()), tz="UTC")),
        "end": format_ts(pd.Timestamp(int(end.max()), tz="UTC")),
        "duration_s": rnd((end.max() - start.min()) / _NS_PER_S),
        "unmatched_requests": int((~req["matched"]).sum()),
        # Errores de cliente (4xx) y de servidor (5xx, p. ej. 503 con el pool saturado) por separado.
        "error_requests": int(_is_client_error(req["status"]).sum()),
        "server_error_requests": int((req["status"] >= 500).sum()),
    }


def _rps(start: np.ndarray) -> dict[str, Any]:
    # Cada petición cuenta en el segundo UTC en que empieza.
    seconds, counts = np.unique(start // _NS_PER_S, return_counts=True)
    total_seconds = int(seconds[-1] - seconds[0] + 1)
    p50, p95, p99 = percentiles_with_zeros(counts, total_seconds - len(seconds), [50, 95, 99])
    return {
        "mean": rnd(counts.sum() / total_seconds),
        "active_mean": rnd(counts.mean()),
        "p50": rnd(p50),
        "p95": rnd(p95),
        "p99": rnd(p99),
        "max": int(counts.max()),
    }


def _requests_per_hour(ts_start: pd.Series, start: np.ndarray, timezone: str) -> dict[str, Any]:
    local = ts_start.dt.tz_convert(timezone)
    per_hour = np.bincount(local.dt.hour.to_numpy(), minlength=24)
    # Simplificación: todos los días de calendario local entre el primero y el
    # último, ambos incluidos, cuentan como observados para las 24 horas.
    days = (local.max().date() - local.min().date()).days + 1
    by_hour = [
        {
            "hour": h,
            "requests_total": int(per_hour[h]),
            "days_observed": days,
            "requests_mean_per_day": rnd(per_hour[h] / days),
        }
        for h in range(24)
    ]
    hours = start // _NS_PER_HOUR
    first = int(hours.min())
    counts = np.bincount(hours - first)
    series = [
        {"hour_start": format_ts(pd.Timestamp((first + i) * _NS_PER_HOUR, tz="UTC")), "requests": int(c)}
        for i, c in enumerate(counts)
    ]
    return {"by_hour_of_day": by_hour, "series": series}


def _endpoint_mix(req: pd.DataFrame) -> list[dict[str, Any]]:
    data = req.assign(is_error=_is_client_error(req["status"]), is_server_error=req["status"] >= 500)
    grouped = data.groupby("endpoint", sort=True)
    agg = grouped.agg(
        method=("method", "first"),
        path=("path", "first"),
        matched=("matched", "max"),
        n=("status", "size"),
        errors=("is_error", "sum"),
        server_errors=("is_server_error", "sum"),
        lat_mean=("latency_ms", "mean"),
        lat_max=("latency_ms", "max"),
    )
    quant = grouped["latency_ms"].quantile([0.5, 0.95]).unstack()
    quant.columns = ["lat_p50", "lat_p95"]
    agg = agg.join(quant).reset_index()
    agg = agg.sort_values(["n", "endpoint"], ascending=[False, True], kind="mergesort")
    total = len(req)
    return [
        {
            "endpoint": row.endpoint,
            "method": row.method,
            "path": row.path,
            "matched": bool(row.matched),
            "count": int(row.n),
            "share": rnd(row.n / total),
            "error_rate": rnd(row.errors / row.n),
            "server_error_rate": rnd(row.server_errors / row.n),
            "latency_ms": {
                "mean": rnd(row.lat_mean),
                "p50": rnd(row.lat_p50),
                "p95": rnd(row.lat_p95),
                "max": rnd(row.lat_max),
            },
        }
        for row in agg.itertuples(index=False)
    ]


def _top(counter: dict[Any, int], decode: Any, n: int = TOP_N) -> list[tuple[Any, int]]:
    """Los ``n`` más frecuentes, por número desc y luego por su valor decodificado."""
    if not counter:
        return []
    counts = sorted(counter.values(), reverse=True)
    threshold = counts[min(n, len(counts)) - 1]
    candidates = [(decode(k), c) for k, c in counter.items() if c >= threshold]
    return sorted(candidates, key=lambda kv: (-kv[1], kv[0]))[:n]


def _sequences(req: pd.DataFrame, bounds: np.ndarray) -> dict[str, Any]:
    codes, uniques = pd.factorize(req["endpoint"])
    names = np.asarray(uniques, dtype=object)
    codes = codes.astype(np.int32)
    # Cada secuencia se cuenta por los bytes de sus códigos, que es rápido de
    # comparar; solo se decodifican a texto las candidatas al top.
    counter = Counter(chunk.tobytes() for chunk in np.split(codes, bounds[1:]))
    top_seq = _top(counter, lambda k: tuple(names[np.frombuffer(k, dtype=np.int32)].tolist()))
    n_sessions = len(bounds)

    cont = np.ones(len(codes), dtype=bool)
    cont[bounds] = False
    idx = np.flatnonzero(cont)
    k = max(len(names), 1)
    pairs, counts = np.unique(codes[idx - 1].astype(np.int64) * k + codes[idx], return_counts=True)
    bigram_counter = dict(zip(pairs.tolist(), counts.tolist()))
    top_bi = _top(bigram_counter, lambda p: (names[p // k], names[p % k]))
    return {
        "top_sequences": [
            {"sequence": list(seq), "count": int(c), "share": rnd(c / n_sessions)} for seq, c in top_seq
        ],
        "top_bigrams": [{"from": a, "to": b, "count": int(c)} for (a, b), c in top_bi],
    }


def _unmatched(req: pd.DataFrame) -> list[dict[str, Any]]:
    um = req.loc[~req["matched"], ["method", "raw_path"]]
    if um.empty:
        return []
    counts = um.groupby(["method", "raw_path"], sort=True).size().rename("n").reset_index()
    counts = counts.sort_values(["n", "method", "raw_path"], ascending=[False, True, True], kind="mergesort")
    return [
        {"method": r.method, "path": r.raw_path, "count": int(r.n)}
        for r in counts.head(TOP_N).itertuples(index=False)
    ]


def build_summary(
    req: pd.DataFrame,
    *,
    input_info: dict[str, Any],
    timezone: str,
    negative_clipped: int,
) -> dict[str, Any]:
    """analysis.json completo a partir de las peticiones ordenadas."""
    if req.empty:
        raise ValueError("no hay peticiones que analizar")
    start = to_ns(req["ts_start"])
    end = to_ns(req["ts_end"])
    bounds = _session_bounds(req["session_id"])
    lengths = np.diff(np.append(bounds, len(req)))
    durations = (np.maximum.reduceat(end, bounds) - np.minimum.reduceat(start, bounds)) / _NS_PER_S
    think = req["think_time_ms"].to_numpy(dtype=float)
    think_stats = describe(think)
    return {
        "schema_version": 1,
        "generator": "morfeo.log_analyzer",
        "input": input_info,
        "totals": _totals(req, start, end, len(bounds)),
        "rps": _rps(start),
        "requests_per_hour": _requests_per_hour(req["ts_start"], start, timezone),
        "endpoint_mix": _endpoint_mix(req),
        "session_length": {"requests": describe(lengths), "duration_s": describe(durations)},
        # Las mismas claves que describe, con negative_clipped detrás de count.
        "think_time_ms": {
            "count": think_stats.pop("count"),
            "negative_clipped": int(negative_clipped),
            **think_stats,
        },
        "sequences": _sequences(req, bounds),
        "unmatched_paths": _unmatched(req),
    }
