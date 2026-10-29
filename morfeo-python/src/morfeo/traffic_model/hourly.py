"""Curva de carga por hora del día y usuarios concurrentes por franja.

Dos estimaciones de la concurrencia en cada hora:

- ``little``: ley de Little, llegadas de sesiones por segundo por duración
  media de sesión.
- ``observed_*``: se cuenta cada segundo el número de sesiones activas en
  ese instante (una sesión está activa entre su primer inicio y su último
  fin) y se agrupan los instantes por hora local. Con tráfico estable,
  ``observed_mean`` y ``little`` deben salir parecidos; si no, la curva
  tiene picos dentro de la hora.

``recommended`` sale del percentil 95 observado, porque la ley de Little da
una media y el plan de carga debe cubrir los minutos de más tráfico.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from morfeo.traffic_model.distributions import rnd

_SECOND_NS = 1_000_000_000
_MS_NS = 1_000_000
MAX_SAMPLES = 5_000_000


def session_bounds(requests: pd.DataFrame) -> pd.DataFrame:
    """Inicio, fin y duración (s) de cada sesión."""
    g = requests.groupby("session_id", sort=True)
    bounds = pd.DataFrame({"start": g["ts_start"].min(), "end": g["ts_end"].max()})
    bounds["duration_s"] = (bounds["end"] - bounds["start"]).dt.total_seconds().clip(lower=0)
    return bounds


def _ns(ts: pd.Series) -> np.ndarray:
    return ts.dt.tz_convert("UTC").dt.as_unit("ns").astype("int64").to_numpy()


def _local_hours(ns: np.ndarray, timezone: str) -> np.ndarray:
    local = pd.to_datetime(ns, utc=True).tz_convert(timezone)
    return np.asarray(local.hour, dtype=np.int64)


def sampled_concurrency(bounds: pd.DataFrame, max_samples: int = MAX_SAMPLES) -> tuple[np.ndarray, np.ndarray]:
    """Instantes de muestreo (ns UTC) y sesiones activas en cada uno.

    Se muestrea cada segundo; en logs muy largos el paso crece para no pasar
    de ``max_samples`` puntos. Una sesión está activa en ``t`` si
    ``inicio <= t < fin`` (con una duración mínima de 1 ms).
    """
    start = np.sort(_ns(bounds["start"]))
    end = np.sort(np.maximum(_ns(bounds["end"]), _ns(bounds["start"]) + _MS_NS))
    first = start[0] // _SECOND_NS * _SECOND_NS
    span = int(end.max() - first)
    step = _SECOND_NS * max(1, math.ceil(span / _SECOND_NS / max_samples))
    instants = first + np.arange(span // step + 1, dtype=np.int64) * step
    started = np.searchsorted(start, instants, side="right")
    finished = np.searchsorted(end, instants, side="right")
    return instants, started - finished


def hourly_profile(requests: pd.DataFrame, timezone: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """``hourly_curve`` y ``concurrent_users`` de profile.json."""
    bounds = session_bounds(requests)
    local_start = bounds["start"].dt.tz_convert(timezone)
    local_end = bounds["end"].dt.tz_convert(timezone)
    days = max(1, (local_end.max().normalize() - local_start.min().normalize()).days + 1)

    session_hours = np.asarray(local_start.dt.hour, dtype=np.int64)
    request_hours = _local_hours(_ns(requests["ts_start"]), timezone)
    sessions_by_hour = np.bincount(session_hours, minlength=24)
    requests_by_hour = np.bincount(request_hours, minlength=24)
    total_requests = int(requests_by_hour.sum())
    global_duration = float(bounds["duration_s"].mean())
    duration_by_hour = bounds.groupby(session_hours)["duration_s"].mean()

    instants, active = sampled_concurrency(bounds)
    instant_hours = _local_hours(instants, timezone)

    hours = []
    for h in range(24):
        sessions_per_hour = sessions_by_hour[h] / days
        mean_duration = float(duration_by_hour.get(h, global_duration))
        in_hour = active[instant_hours == h]
        has_traffic = in_hour.size > 0 and bool(in_hour.max() > 0)
        p95 = float(np.percentile(in_hour, 95)) if in_hour.size else 0.0
        hours.append(
            {
                "hour": h,
                "sessions_per_hour": rnd(sessions_per_hour),
                "requests_per_hour": rnd(requests_by_hour[h] / days),
                "rps": rnd(requests_by_hour[h] / days / 3600.0),
                "share_of_daily_requests": rnd(requests_by_hour[h] / total_requests if total_requests else 0.0),
                "mean_session_duration_s": rnd(mean_duration),
                "concurrent_users": {
                    "little": rnd(sessions_per_hour / 3600.0 * mean_duration),
                    "observed_mean": rnd(float(in_hour.mean()) if in_hour.size else 0.0),
                    "observed_p95": rnd(p95),
                    "observed_max": int(in_hour.max()) if in_hour.size else 0,
                    "recommended": max(1, math.ceil(p95)) if has_traffic else 0,
                },
            }
        )
    recommended = [h["concurrent_users"]["recommended"] for h in hours]
    # hora pico: más usuarios recomendados; si empatan, más concurrencia media; si no, la primera
    peak = max(range(24), key=lambda h: (recommended[h], hours[h]["concurrent_users"]["observed_mean"], -h))
    curve = {"timezone": timezone, "days_observed": int(days), "hours": hours}
    concurrent = {
        "peak_hour": peak,
        "peak_recommended": int(recommended[peak]),
        "daily_mean": rnd(float(active.mean()) if active.size else 0.0),
    }
    return curve, concurrent
