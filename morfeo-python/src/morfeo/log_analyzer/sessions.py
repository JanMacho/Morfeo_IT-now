"""Sesiones, orden dentro de cada sesión y tiempos de espera.

- ``ts_start`` y ``ts_end`` salen del timestamp del log y de la latencia,
  según el timestamp marque el inicio o el fin de la petición.
- Si el log trae ``session_id``, se usa tal cual. Si no, las peticiones de
  cada cliente se cortan en sesiones cuando pasan más de ``session_gap_s``
  segundos entre el inicio de una petición y el fin más tardío de las
  anteriores de ese cliente. Esas sesiones se llaman ``<cliente>#<n>``.
- Las peticiones sin cliente se agrupan como el cliente ``anonymous``.
- ``think_time_ms`` es el inicio de una petición menos el fin de la anterior
  de la misma sesión, recortado a 0 si las peticiones se solapan.

Los cálculos se hacen con enteros en nanosegundos para no perder precisión.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

ANONYMOUS_CLIENT = "anonymous"


@dataclass
class SessionResult:
    """Peticiones ordenadas por (session_id, seq) con sus tiempos."""

    requests: pd.DataFrame
    session_source: str
    negative_clipped: int
    warnings: list[str] = field(default_factory=list)


def to_ns(ts: pd.Series) -> np.ndarray:
    """Timestamps con zona a enteros en nanosegundos desde epoch (UTC)."""
    return ts.dt.as_unit("ns").to_numpy(dtype="datetime64[ns]").view(np.int64)


def from_ns(ns: np.ndarray) -> pd.Series:
    """Enteros en nanosegundos a ``datetime64[ns, UTC]``."""
    return pd.Series(np.asarray(ns, dtype=np.int64).view("datetime64[ns]")).dt.tz_localize("UTC")


def request_times(timestamp: pd.Series, latency_ms: pd.Series, timestamp_is: str) -> tuple[np.ndarray, np.ndarray]:
    """Inicio y fin de cada petición en nanosegundos."""
    ts = to_ns(timestamp)
    latency = np.rint(latency_ms.to_numpy(dtype=float) * 1e6).astype(np.int64)
    if timestamp_is == "end":
        return ts - latency, ts
    return ts, ts + latency


def _gap_sessions(keys: np.ndarray, start: np.ndarray, end: np.ndarray, gap_ns: int) -> np.ndarray:
    """Nombre de sesión ``<cliente>#<n>`` de cada petición, en el orden de entrada."""
    codes, uniques = pd.factorize(keys)
    order = np.lexsort((np.arange(len(keys)), start, codes))
    c, s, e = codes[order], start[order], end[order]
    new = np.ones(len(c), dtype=bool)
    # Dentro de un cliente, el hueco se mide contra el fin más tardío de las
    # peticiones previas, porque puede haber peticiones solapadas.
    run_max = pd.Series(e).groupby(c).cummax().to_numpy()
    new[1:] = (c[1:] != c[:-1]) | ((s[1:] - run_max[:-1]) > gap_ns)
    number = pd.Series(new.astype(np.int64)).groupby(c).cumsum().to_numpy() - 1
    starts = np.flatnonzero(new)
    client = pd.Series(np.asarray(uniques, dtype=object)[c[starts]], dtype="str")
    names = (client + "#" + pd.Series(number[starts]).astype("str")).to_numpy(dtype=object)
    out = np.empty(len(keys), dtype=object)
    out[order] = names[np.cumsum(new) - 1]
    return out


def build_sessions(
    frame: pd.DataFrame,
    *,
    timestamp_is: str,
    missing_client_values: Sequence[str],
    session_gap_s: float,
) -> SessionResult:
    """Asigna sesiones, ordena y calcula los tiempos de espera.

    ``frame`` necesita ``timestamp``, ``latency_ms``, ``session_id`` y
    ``client_id``; el resto de columnas se conserva.
    """
    if session_gap_s <= 0:
        raise ValueError("session_gap_s debe ser mayor que 0")
    n = len(frame)
    start, end = request_times(frame["timestamp"], frame["latency_ms"], timestamp_is)

    client = frame["client_id"].to_numpy(dtype=object)
    missing = frame["client_id"].isin(list(missing_client_values) + [""]).to_numpy()
    session = frame["session_id"].to_numpy(dtype=object).copy()
    has_sid = session != ""

    rows = np.flatnonzero(~has_sid)
    if rows.size:
        keys = np.where(missing[rows], ANONYMOUS_CLIENT, client[rows])
        session[rows] = _gap_sessions(keys, start[rows], end[rows], int(round(session_gap_s * 1e9)))

    warnings: list[str] = []
    if has_sid.all():
        source = "session_id"
    elif has_sid.any():
        source = "session_id"
        warnings.append(
            f"{int((~has_sid).sum())} peticiones sin session_id se agrupan por cliente "
            f"con corte por inactividad de {session_gap_s:g} s"
        )
    elif (~missing).any():
        source = "client_gap"
    else:
        source = "single"
    n_anonymous = int((missing & ~has_sid).sum())
    if n_anonymous:
        warnings.append(
            f"{n_anonymous} peticiones sin cliente se agrupan como '{ANONYMOUS_CLIENT}' con corte por "
            "inactividad; si hay usuarios anónimos concurrentes, sus peticiones se mezclan en las mismas sesiones"
        )

    # Orden final: session_id, inicio y orden de entrada (estable).
    s_codes, _ = pd.factorize(session, sort=True)
    order = np.lexsort((np.arange(n), start, s_codes))
    sc, st, en = s_codes[order], start[order], end[order]
    idx = np.arange(n)
    first = np.ones(n, dtype=bool)
    first[1:] = sc[1:] != sc[:-1]
    seq = idx - np.maximum.accumulate(np.where(first, idx, 0))

    think = np.full(n, np.nan)
    cont = np.flatnonzero(~first)
    think[cont] = (st[cont] - en[cont - 1]) / 1e6
    negative = think < 0
    negative_clipped = int(negative.sum())
    think[negative] = 0.0

    out = frame.iloc[order].reset_index(drop=True)
    out["session_id"] = pd.Series(session[order], dtype="str")
    out["client_id"] = pd.Series(np.where(missing, "", client)[order], dtype="str")
    out["seq"] = seq.astype(np.int64)
    out["ts_start"] = from_ns(st)
    out["ts_end"] = from_ns(en)
    out["think_time_ms"] = think
    return SessionResult(out, source, negative_clipped, warnings)
