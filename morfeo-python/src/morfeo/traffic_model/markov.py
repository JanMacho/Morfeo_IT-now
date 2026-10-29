"""Cadena de Markov de primer orden sobre los endpoints de cada sesión.

Estados: ``START``, los endpoints y ``END``. Cada sesión aporta la transición
de ``START`` a su primer endpoint, las transiciones entre peticiones
consecutivas y la del último endpoint a ``END``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from morfeo.common import END_STATE, START_STATE

Counts = dict[str, dict[str, int]]
Matrix = dict[str, dict[str, float]]


def ordered(requests: pd.DataFrame) -> pd.DataFrame:
    """Peticiones ordenadas por sesión y posición, con índice nuevo."""
    return requests.sort_values(["session_id", "seq"], kind="stable").reset_index(drop=True)


def previous_endpoint(req: pd.DataFrame) -> pd.Series:
    """Endpoint de la petición anterior de la misma sesión (NaN en la primera). ``req`` ya ordenado."""
    prev = req["endpoint"].shift(1)
    return prev.where(req["session_id"].eq(req["session_id"].shift(1)))


def sequences(requests: pd.DataFrame) -> list[list[str]]:
    """Secuencia de endpoints de cada sesión, en orden de ``session_id``."""
    req = ordered(requests)
    return [list(g) for _, g in req.groupby("session_id", sort=True)["endpoint"]]


def count_from_requests(requests: pd.DataFrame) -> Counts:
    """Conteo de transiciones directamente desde requests.csv (vectorizado)."""
    req = ordered(requests)
    prev = previous_endpoint(req).fillna(START_STATE)
    pairs = Counter(zip(prev.tolist(), req["endpoint"].tolist()))
    last = req.groupby("session_id", sort=True)["endpoint"].last()
    pairs.update((e, END_STATE) for e in last.tolist())
    return _nest(pairs)


def count_from_sequences(seqs: Iterable[Sequence[str]]) -> Counts:
    pairs: Counter[tuple[str, str]] = Counter()
    for seq in seqs:
        if not seq:
            continue
        chain = [START_STATE, *seq, END_STATE]
        pairs.update(zip(chain[:-1], chain[1:]))
    return _nest(pairs)


def _nest(pairs: Counter[tuple[str, str]]) -> Counts:
    out: Counts = {}
    for (a, b), n in sorted(pairs.items()):
        out.setdefault(a, {})[b] = int(n)
    return out


def state_list(endpoints: Iterable[str]) -> list[str]:
    return [START_STATE, *sorted(set(endpoints)), END_STATE]


def to_matrix(counts: Counts, states: Sequence[str], smoothing: float = 0.0) -> Matrix:
    """Probabilidades por fila. Con ``smoothing`` > 0 se suma a todas las columnas permitidas.

    Columnas permitidas: todos los estados salvo ``START``; desde ``START`` no
    se puede ir directamente a ``END`` (una sesión tiene al menos una petición).
    """
    matrix: Matrix = {}
    for a in states:
        if a == END_STATE:
            continue
        allowed = [b for b in states if b != START_STATE and not (a == START_STATE and b == END_STATE)]
        row = {b: float(counts.get(a, {}).get(b, 0)) + smoothing for b in allowed}
        total = sum(row.values())
        if total <= 0:
            continue
        probs = {b: v / total for b, v in row.items() if v > 0}
        # corrige el residuo de coma flotante en la mayor entrada para que sume 1
        top = max(probs, key=lambda k: (probs[k], k))
        probs[top] += 1.0 - sum(probs.values())
        matrix[a] = probs
    return matrix


def _arrays(matrix: Matrix) -> tuple[list[str], np.ndarray]:
    names = sorted({*matrix, *(b for row in matrix.values() for b in row)} - {START_STATE, END_STATE})
    states = [START_STATE, *names, END_STATE]
    index = {s: i for i, s in enumerate(states)}
    p = np.zeros((len(states), len(states)))
    for a, row in matrix.items():
        for b, v in row.items():
            p[index[a], index[b]] = v
    p[index[END_STATE], index[END_STATE]] = 1.0
    cum = np.cumsum(p, axis=1)
    totals = cum[:, -1:]
    cum = np.divide(cum, totals, out=np.zeros_like(cum), where=totals > 0)
    cum[:, -1] = 1.0  # el sorteo nunca cae fuera de la fila por redondeo
    return states, cum


def sample_sessions(matrix: Matrix, n: int, rng: np.random.Generator, max_length: int = 1000) -> list[list[str]]:
    """``n`` sesiones sintéticas. Se sortean todas a la vez, paso a paso.

    En cada paso solo se guardan las sesiones que siguen activas, así la
    memoria crece con el número de peticiones generadas y no con
    sesiones por pasos (importa con sesiones muy largas).
    """
    if n <= 0 or START_STATE not in matrix:
        return []
    states, cum = _arrays(matrix)
    end = len(states) - 1
    active = np.arange(n, dtype=np.int64)
    current = np.zeros(n, dtype=np.int64)
    owners: list[np.ndarray] = []
    visited: list[np.ndarray] = []
    for _ in range(max_length):
        if active.size == 0:
            break
        u = rng.random(active.size)
        nxt = np.minimum((cum[current] < u[:, None]).sum(axis=1), end)
        alive = nxt != end
        active, current = active[alive], nxt[alive]
        owners.append(active)
        visited.append(current)
    out: list[list[str]] = [[] for _ in range(n)]
    if owners:
        who = np.concatenate(owners)
        what = np.concatenate(visited)
        order = np.argsort(who, kind="stable")  # estable: conserva el orden de los pasos
        who, what = who[order], what[order]
        bounds = np.flatnonzero(np.diff(who)) + 1
        for chunk_who, chunk_what in zip(np.split(who, bounds), np.split(what, bounds)):
            out[int(chunk_who[0])] = [states[i] for i in chunk_what]
    return out


def as_counts_json(counts: Counts) -> dict[str, Any]:
    return {a: dict(sorted(row.items())) for a, row in sorted(counts.items())}
