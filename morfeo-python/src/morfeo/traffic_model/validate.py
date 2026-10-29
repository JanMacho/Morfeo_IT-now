"""Validación del modelo con sesiones sintéticas (docs/contracts.md, sección 5).

Se generan al menos MIN_SYNTHETIC_SESSIONS sesiones (o tantas como reales, si
hay más), con la
cadena de Markov y con el modelo base, y se comparan con las reales:

- KS de dos muestras en tiempos de espera y en número de peticiones por sesión.
- Diferencia entre la matriz de transición real y la reestimada sobre las
  sesiones sintéticas (misma función de conteo en los dos lados).
- Distancia de variación total entre mezclas de endpoints.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Sequence

import numpy as np
import pandas as pd

from morfeo.common import DEFAULT_SEED, SCHEMA_VERSION, make_rng
from morfeo.traffic_model.distributions import rnd, sample_from_dist
from morfeo.traffic_model.ks import ks_2samp
from morfeo.traffic_model.markov import Counts, Matrix, count_from_requests, count_from_sequences, sample_sessions

KS_THRESHOLD = 0.1
# Con tantas sesiones sintéticas como reales, el ruido de la muestra sintética
# pesaba en el veredicto: con 155 sesiones reales la TV ponderada iba de 0,031 a
# 0,068 según la semilla. Con 10.000 queda en 0,005 y el veredicto mide modelo
# frente a realidad.
MIN_SYNTHETIC_SESSIONS = 10_000
TV_THRESHOLD = 0.05


def _sample_think_times(profile: dict[str, Any], seqs: list[list[str]], rng: np.random.Generator) -> np.ndarray:
    """Una espera por cada par de peticiones consecutivas, según el estado anterior."""
    previous = [s for seq in seqs for s in seq[:-1]]
    if not previous:
        return np.empty(0)
    think = profile["think_time_ms"]
    out = np.empty(len(previous))
    prev = np.array(previous, dtype=object)
    for state in sorted(set(previous)):
        mask = prev == state
        dist = think["by_state"].get(state, think["global"])
        out[mask] = sample_from_dist(dist, int(mask.sum()), rng)
    return out


def _baseline_sessions(profile: dict[str, Any], n: int, rng: np.random.Generator) -> list[list[str]]:
    base = profile["baseline"]
    mix = base["endpoint_mix"]
    if not mix or n <= 0:
        return []
    names = sorted(mix)
    p = np.array([mix[k] for k in names], dtype=float)
    p = p / p.sum()
    lengths = np.maximum(1, np.rint(sample_from_dist(base["session_length_requests"], n, rng))).astype(int)
    picks = rng.choice(len(names), size=int(lengths.sum()), p=p)
    out, pos = [], 0
    for length in lengths:
        out.append([names[i] for i in picks[pos : pos + length]])
        pos += length
    return out


def _probabilities(counts: Counts) -> Matrix:
    """Normaliza cada fila de conteos, sin suavizado."""
    out: Matrix = {}
    for a, row in counts.items():
        total = sum(row.values())
        if total:
            out[a] = {b: n / total for b, n in row.items()}
    return out


def matrix_diff(real_counts: Counts, synth: Matrix) -> dict[str, float | None]:
    """max_abs, mean_abs y frobenius sobre la unión de celdas; weighted_tv por visitas reales."""
    real = _probabilities(real_counts)
    rows = sorted(set(real) | set(synth))
    cols = sorted({b for m in (real, synth) for row in m.values() for b in row})
    if not rows or not cols:
        return {"max_abs": None, "mean_abs": None, "frobenius": None, "weighted_tv": None}
    diff = np.array([[real.get(a, {}).get(b, 0.0) - synth.get(a, {}).get(b, 0.0) for b in cols] for a in rows])
    visits = {a: sum(row.values()) for a, row in real_counts.items()}
    total = sum(visits.values())
    tv = sum(visits.get(a, 0) / total * 0.5 * float(np.abs(diff[i]).sum()) for i, a in enumerate(rows)) if total else None
    return {
        "max_abs": rnd(float(np.abs(diff).max())),
        "mean_abs": rnd(float(np.abs(diff).mean())),
        "frobenius": rnd(float(np.sqrt((diff**2).sum()))),
        "weighted_tv": rnd(tv),
    }


def _mix(seqs: Sequence[Sequence[str]]) -> dict[str, float]:
    c = Counter(e for seq in seqs for e in seq)
    total = sum(c.values())
    return {k: v / total for k, v in c.items()} if total else {}


def _tv(a: dict[str, float], b: dict[str, float]) -> float | None:
    if not a or not b:
        return None
    return rnd(0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b)))


def _ks(real: np.ndarray, synth: np.ndarray) -> dict[str, Any]:
    d, p = ks_2samp(real, synth)
    return {"statistic": rnd(d), "p_value": rnd(p), "n_real": int(real.size), "n_synthetic": int(synth.size)}


def validate(
    profile: dict[str, Any],
    requests: pd.DataFrame,
    *,
    n_sessions: int | None = None,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Compara el modelo con las sesiones reales de ``requests`` y devuelve validation.json."""
    if requests.empty:
        raise ValueError("requests está vacío: no hay sesiones reales con las que comparar")
    rng = make_rng(seed)
    real_counts = count_from_requests(requests)
    real_lengths = requests.groupby("session_id").size().to_numpy()
    real_think = requests["think_time_ms"].dropna().to_numpy(dtype=float)
    n = int(n_sessions) if n_sessions is not None else max(MIN_SYNTHETIC_SESSIONS, int(real_lengths.size))
    max_length = max(1000, 3 * int(profile["session_length"]["requests"].get("max") or 0))

    markov = sample_sessions(profile["transition_matrix"], n, rng, max_length=max_length)
    markov_think = _sample_think_times(profile, markov, rng)
    baseline = _baseline_sessions(profile, n, rng)

    markov_diff = matrix_diff(real_counts, _probabilities(count_from_sequences(markov)))
    baseline_diff = matrix_diff(real_counts, _probabilities(count_from_sequences(baseline)))
    think_ks = _ks(real_think, markov_think)
    length_ks = _ks(real_lengths.astype(float), np.array([len(s) for s in markov], dtype=float))
    counts = requests["endpoint"].value_counts()
    real_mix = {str(k): int(n) / len(requests) for k, n in counts.items()}

    def ok(value: float | None, threshold: float) -> bool:
        return value is None or value <= threshold

    return {
        "schema_version": SCHEMA_VERSION,
        "generator": "morfeo.traffic_model.validate",
        "seed": int(seed),
        "reference": "holdout" if float(profile["input"].get("holdout", 0.0)) > 0 else "in-sample",
        "n_real_sessions": int(real_lengths.size),
        "n_synthetic_sessions": n,
        "think_time_ks": think_ks,
        "session_length_ks": length_ks,
        "transition_diff": {"markov": markov_diff, "baseline": baseline_diff},
        "endpoint_mix_tv": {"markov": _tv(real_mix, _mix(markov)), "baseline": _tv(real_mix, _mix(baseline))},
        "thresholds": {"ks_statistic": KS_THRESHOLD, "weighted_tv": TV_THRESHOLD},
        "verdict": {
            "think_time_ok": ok(think_ks["statistic"], KS_THRESHOLD),
            "session_length_ok": ok(length_ks["statistic"], KS_THRESHOLD),
            "transitions_ok": ok(markov_diff["weighted_tv"], TV_THRESHOLD),
        },
    }


def _fmt(value: float | None, digits: int = 3) -> str:
    return "sin datos" if value is None else f"{value:.{digits}f}"


def format_report(report: dict[str, Any]) -> str:
    """Informe corto en texto."""
    ref = "sesiones reservadas (holdout)" if report["reference"] == "holdout" else "las mismas sesiones del entrenamiento"
    tk, sl = report["think_time_ks"], report["session_length_ks"]
    mk, bl = report["transition_diff"]["markov"], report["transition_diff"]["baseline"]
    mix = report["endpoint_mix_tv"]
    th = report["thresholds"]
    v = report["verdict"]

    def verdict(flag: bool) -> str:
        return "correcto" if flag else "revisar"

    return "\n".join(
        [
            "Validación del modelo de tráfico",
            f"  Referencia: {ref}, {report['n_real_sessions']} sesiones reales y {report['n_synthetic_sessions']} sintéticas (semilla {report['seed']})",
            f"  Tiempo de espera, KS: D = {_fmt(tk['statistic'])}, p = {_fmt(tk['p_value'], 4)} ({tk['n_real']} reales, {tk['n_synthetic']} sintéticos)",
            f"  Peticiones por sesión, KS: D = {_fmt(sl['statistic'])}, p = {_fmt(sl['p_value'], 4)}",
            f"  Matriz de transición, Markov: TV ponderada = {_fmt(mk['weighted_tv'])}, diferencia máxima = {_fmt(mk['max_abs'])}",
            f"  Matriz de transición, base sin memoria: TV ponderada = {_fmt(bl['weighted_tv'])}, diferencia máxima = {_fmt(bl['max_abs'])}",
            f"  Mezcla de endpoints, TV: Markov {_fmt(mix['markov'])}, base {_fmt(mix['baseline'])}",
            f"  Umbrales: D <= {th['ks_statistic']}, TV ponderada <= {th['weighted_tv']}",
            f"  Veredicto: tiempos de espera {verdict(v['think_time_ok'])}, longitud de sesión {verdict(v['session_length_ok'])}, "
            f"transiciones {verdict(v['transitions_ok'])}",
        ]
    )
