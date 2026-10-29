"""Entrenamiento del modelo de tráfico: profile.json (docs/contracts.md, sección 4).

Hay dos modelos en el mismo fichero:

- ``baseline``: solo estadística pandas, sin memoria. Cada petición de la
  sesión se elige de forma independiente con la mezcla observada y la
  longitud de sesión se toma de su distribución empírica.
- Cadena de Markov: ``transition_matrix`` con estados START y END, tiempos
  de espera por estado anterior y curva de carga por hora.

La validación compara los dos para medir lo que aporta la cadena.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from morfeo.common import DEFAULT_SEED, REQUESTS_COLUMNS, SCHEMA_VERSION, read_json, write_json
from morfeo.traffic_model.distributions import build_dist, rnd
from morfeo.traffic_model.hourly import hourly_profile, session_bounds
from morfeo.traffic_model.markov import as_counts_json, count_from_requests, ordered, previous_endpoint, state_list, to_matrix


def _check(requests: pd.DataFrame) -> None:
    missing = [c for c in REQUESTS_COLUMNS if c not in requests.columns]
    if missing:
        raise ValueError(f"requests no tiene las columnas {missing}")
    if requests.empty:
        raise ValueError("requests está vacío: no hay peticiones con las que entrenar")


def only_matched(requests: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Quita las peticiones que no casan con ningún endpoint del jar.

    Son sobre todo 404 a rutas inexistentes (bots, enlaces rotos) que no deben
    formar parte del plan de carga. Si una sesión pierde peticiones se
    recalculan ``seq`` y ``think_time_ms`` con las que quedan. Si ninguna
    petición casó (se analizó sin endpoints.json) no se quita nada.
    Devuelve las peticiones y cuántas se han quitado.
    """
    if not requests["matched"].any():
        return requests, 0
    kept = ordered(requests[requests["matched"]])
    dropped = len(requests) - len(kept)
    if dropped == 0:
        return kept, 0
    same = kept["session_id"].eq(kept["session_id"].shift(1))
    gap = (kept["ts_start"] - kept["ts_end"].shift(1)).dt.total_seconds() * 1000.0
    kept["think_time_ms"] = gap.clip(lower=0).where(same)
    kept["seq"] = kept.groupby("session_id", sort=False).cumcount()
    return kept.reset_index(drop=True), dropped


def split_holdout(requests: pd.DataFrame, fraction: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separa sesiones de entrenamiento y de validación.

    Las sesiones se ordenan por su primer ``ts_start`` y la última ``fraction``
    se reserva para validar, así se valida con tráfico posterior al de
    entrenamiento. Con ``fraction`` 0 se devuelve ``(requests, requests)``.
    """
    if not 0.0 <= fraction < 1.0:
        raise ValueError(f"holdout debe estar en [0, 1), no {fraction}")
    if fraction == 0.0:
        return requests, requests
    starts = requests.groupby("session_id")["ts_start"].min().reset_index()
    starts = starts.sort_values(["ts_start", "session_id"], kind="stable")
    n = len(starts)
    n_test = min(n - 1, max(1, round(n * fraction))) if n >= 2 else 0
    if n_test == 0:
        raise ValueError("hace falta al menos dos sesiones para reservar una parte de validación")
    test_ids = set(starts["session_id"].iloc[n - n_test :])
    in_test = requests["session_id"].isin(test_ids)
    return requests[~in_test].reset_index(drop=True), requests[in_test].reset_index(drop=True)


def _endpoints(req: pd.DataFrame) -> dict[str, Any]:
    total = len(req)
    out: dict[str, Any] = {}
    for endpoint, g in req.groupby("endpoint", sort=True):
        latency = g["latency_ms"].to_numpy(dtype=float)
        latency = latency[np.isfinite(latency)]
        out[str(endpoint)] = {
            "method": str(g["method"].iloc[0]),
            "path": str(g["path"].iloc[0]),
            "matched": bool(g["matched"].all()),
            "count": int(len(g)),
            "share": rnd(len(g) / total),
            "error_rate": rnd(float(((g["status"] >= 400) & (g["status"] < 500)).mean())),
            "server_error_rate": rnd(float((g["status"] >= 500).mean())),
            "latency_ms": {
                "p50": rnd(float(np.percentile(latency, 50))) if latency.size else None,
                "p95": rnd(float(np.percentile(latency, 95))) if latency.size else None,
            },
        }
    return out


def _think_times(req: pd.DataFrame, min_state_samples: int) -> dict[str, Any]:
    """Espera global y por estado anterior (la espera que sigue a una petición a ese endpoint)."""
    think = req["think_time_ms"]
    prev = previous_endpoint(req)
    valid = think.notna() & prev.notna()
    by_state: dict[str, Any] = {}
    for state, values in think[valid].groupby(prev[valid], sort=True):
        if len(values) >= min_state_samples:
            by_state[str(state)] = build_dist(values.to_numpy())
    return {"global": build_dist(think.dropna().to_numpy()), "by_state": by_state}


def train(
    requests: pd.DataFrame,
    *,
    seed: int = DEFAULT_SEED,
    smoothing: float = 0.0,
    timezone: str = "UTC",
    min_state_samples: int = 30,
    holdout: float = 0.0,
    excluded_unmatched: int = 0,
) -> dict[str, Any]:
    """Entrena el modelo y devuelve el contenido de profile.json.

    ``excluded_unmatched`` es el número de peticiones quitadas antes con
    ``only_matched``; solo se registra en ``input``.

    ``seed`` y ``holdout`` no cambian el entrenamiento (es determinista); se
    guardan para que la validación y el bloque JMeter sepan cómo se obtuvo.
    """
    _check(requests)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"zona horaria desconocida: {timezone!r} (por ejemplo Europe/Madrid o UTC)") from exc
    if smoothing < 0:
        raise ValueError("smoothing no puede ser negativo")
    req = ordered(requests)
    counts = count_from_requests(req)
    states = state_list(req["endpoint"])
    think = _think_times(req, min_state_samples)
    bounds = session_bounds(req)
    lengths = build_dist(req.groupby("session_id").size().to_numpy())
    curve, concurrent = hourly_profile(req, timezone)
    endpoints = _endpoints(req)
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": "morfeo.traffic_model",
        "seed": int(seed),
        "input": {
            "requests": int(len(req)),
            "sessions": int(len(bounds)),
            "timezone": timezone,
            "holdout": float(holdout),
            "smoothing": float(smoothing),
            "min_state_samples": int(min_state_samples),
            "excluded_unmatched_requests": int(excluded_unmatched),
        },
        "states": states,
        "endpoints": endpoints,
        "transition_matrix": to_matrix(counts, states, smoothing),
        "transition_counts": as_counts_json(counts),
        "think_time_ms": think,
        "session_length": {
            "requests": lengths,
            "duration_s": build_dist(bounds["duration_s"].to_numpy()),
        },
        "hourly_curve": curve,
        "concurrent_users": concurrent,
        "baseline": {
            "endpoint_mix": {e: v["share"] for e, v in endpoints.items()},
            "think_time_ms": think["global"],
            "session_length_requests": lengths,
        },
    }


def write_profile(profile: dict[str, Any], path: str | Path) -> Path:
    return write_json(path, profile)


def load_profile(path: str | Path) -> dict[str, Any]:
    profile = read_json(path)
    if not isinstance(profile, dict) or "transition_matrix" not in profile:
        raise ValueError(f"{path} no es un profile.json válido")
    return profile
