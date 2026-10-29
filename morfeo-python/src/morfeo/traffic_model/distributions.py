"""Distribuciones empíricas (``Dist`` en docs/contracts.md) y muestreo a partir de ellas."""

from __future__ import annotations

from typing import Any

import numpy as np

QUANTILE_POINTS = np.linspace(0.0, 100.0, 101)


def rnd(value: float | None) -> float | None:
    """Redondea a 6 decimales para que el JSON sea estable; None se conserva."""
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), 6)


def build_dist(values: Any) -> dict[str, Any]:
    """Resumen de una muestra: estadísticos, 101 percentiles y ajuste lognormal.

    El ajuste lognormal es el de máxima verosimilitud (media y desviación de
    los logaritmos) sobre los valores positivos; con menos de dos, ``null``.
    """
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        empty: dict[str, Any] = {k: None for k in ("mean", "std", "min", "p50", "p90", "p95", "p99", "max")}
        return {"count": 0, **empty, "quantiles": [], "lognormal": None}
    q = np.percentile(v, QUANTILE_POINTS)
    positive = v[v > 0]
    lognormal = None
    if positive.size >= 2:
        logs = np.log(positive)
        lognormal = {"mu": rnd(logs.mean()), "sigma": rnd(logs.std())}
    return {
        "count": int(v.size),
        "mean": rnd(v.mean()),
        "std": rnd(v.std()),
        "min": rnd(v.min()),
        "p50": rnd(q[50]),
        "p90": rnd(q[90]),
        "p95": rnd(q[95]),
        "p99": rnd(q[99]),
        "max": rnd(v.max()),
        "quantiles": [rnd(x) for x in q],
        "lognormal": lognormal,
    }


def sample_from_dist(dist: dict[str, Any], n: int, rng: np.random.Generator) -> np.ndarray:
    """``n`` valores por inversión de la CDF empírica (interpolación lineal entre percentiles)."""
    q = np.asarray(dist.get("quantiles") or [], dtype=float)
    if n <= 0:
        return np.empty(0)
    if q.size == 0:
        return np.zeros(n)
    return np.interp(rng.random(n) * 100.0, QUANTILE_POINTS, q)
