"""Tráfico de prueba generado desde una cadena de Markov conocida.

No depende de log_analyzer: construye directamente un DataFrame con las
columnas y tipos de requests.csv (morfeo.common.REQUESTS_COLUMNS).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from morfeo.common import REQUESTS_COLUMNS

SALDO, INGRESO, RETIRADA = "GET /api/saldo/{id}", "POST /api/ingreso", "POST /api/retirada"

# estructura secuencial fuerte: el modelo sin memoria no la puede reproducir
TRUE_MATRIX: dict[str, dict[str, float]] = {
    "START": {SALDO: 0.9, INGRESO: 0.1},
    SALDO: {RETIRADA: 0.6, INGRESO: 0.1, "END": 0.3},
    INGRESO: {SALDO: 0.8, "END": 0.2},
    RETIRADA: {SALDO: 0.7, "END": 0.3},
}
# espera tras cada estado (mediana en ms)
THINK_MEDIAN = {SALDO: 4000.0, INGRESO: 12000.0, RETIRADA: 2000.0}


def rows_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in ("ts_start", "ts_end"):
        df[col] = pd.to_datetime(df[col], utc=True).dt.as_unit("ns")
    df["think_time_ms"] = df["think_time_ms"].astype(float)
    return df.loc[:, list(REQUESTS_COLUMNS)]


def simulate(
    n_sessions: int = 3000,
    *,
    seed: int = 1,
    days: int = 2,
    start: str = "2026-09-21T00:00:00Z",
    matrix: dict[str, dict[str, float]] | None = None,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    matrix = matrix or TRUE_MATRIX
    t0 = pd.Timestamp(start).value
    rows: list[dict[str, Any]] = []
    for s in range(n_sessions):
        t = t0 + int(rng.uniform(0, days * 86400e9))
        state, seq, prev_end = "START", 0, None
        while True:
            options = list(matrix[state])
            state = options[rng.choice(len(options), p=[matrix[state][o] for o in options])]
            if state == "END":
                break
            method, path = state.split(" ", 1)
            latency = float(rng.lognormal(np.log(30), 0.4))
            think = None
            if prev_end is not None:
                think = float(rng.lognormal(np.log(THINK_MEDIAN[rows[-1]["endpoint"]]), 0.5))
                t = prev_end + int(think * 1e6)
            end = t + int(latency * 1e6)
            rows.append(
                {
                    "session_id": f"c{s % 400}#{s}",
                    "client_id": f"c{s % 400}",
                    "seq": seq,
                    "ts_start": t,
                    "ts_end": end,
                    "method": method,
                    "path": path,
                    "raw_path": path.replace("{id}", "user_001"),
                    "query": "",
                    "endpoint": state,
                    "matched": True,
                    "status": 400 if state == RETIRADA and rng.random() < 0.1 else 200,
                    "latency_ms": latency,
                    "think_time_ms": np.nan if think is None else think,
                    "message": "",
                }
            )
            prev_end, seq = end, seq + 1
    return rows_to_frame(rows)


@pytest.fixture(scope="session")
def requests_df() -> pd.DataFrame:
    return simulate()


@pytest.fixture(scope="session")
def tm() -> SimpleNamespace:
    """Helpers y constantes para los tests (conftest no se puede importar con --import-mode=importlib)."""
    return SimpleNamespace(
        simulate=simulate,
        rows_to_frame=rows_to_frame,
        TRUE_MATRIX=TRUE_MATRIX,
        THINK_MEDIAN=THINK_MEDIAN,
        SALDO=SALDO,
        INGRESO=INGRESO,
        RETIRADA=RETIRADA,
    )
