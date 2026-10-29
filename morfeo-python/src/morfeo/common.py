"""Utilidades compartidas por los módulos de MORFEO.

Aquí solo va lo que fija el contrato entre módulos (docs/contracts.md): la
semilla por defecto, la escritura determinista de JSON y el formato de
requests.csv. Cada módulo puede importar de aquí sin depender de los demás.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SCHEMA_VERSION = 1
DEFAULT_SEED = 42

START_STATE = "START"
END_STATE = "END"

REQUESTS_COLUMNS: tuple[str, ...] = (
    "session_id",
    "client_id",
    "seq",
    "ts_start",
    "ts_end",
    "method",
    "path",
    "raw_path",
    "query",
    "endpoint",
    "matched",
    "status",
    "latency_ms",
    "think_time_ms",
    "message",
)

_TEXT_COLUMNS = ("session_id", "client_id", "method", "path", "raw_path", "query", "endpoint", "message")
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def make_rng(seed: int = DEFAULT_SEED) -> np.random.Generator:
    """Generador aleatorio de numpy con semilla fija."""
    return np.random.default_rng(seed)


def endpoint_id(method: str, path: str) -> str:
    """Clave común de un endpoint: ``"GET /api/saldo/{id}"``."""
    return f"{method.upper()} {path}"


def to_jsonable(value: Any) -> Any:
    """Convierte tipos de numpy y pandas a tipos JSON; NaN e infinito pasan a None."""
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return [to_jsonable(v) for v in value.tolist()]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(value, pd.Timestamp):
        return format_ts(value)
    if value is pd.NaT:
        return None
    return value


def dumps_json(obj: Any) -> str:
    """Serializa a JSON de forma determinista (indentación 2, UTF-8, salto final)."""
    return json.dumps(to_jsonable(obj), indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def write_json(path: str | Path, obj: Any) -> Path:
    """Escribe ``obj`` como JSON en ``path`` y crea la carpeta si no existe."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_json(obj), encoding="utf-8")
    return p


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def format_ts(ts: pd.Timestamp) -> str:
    """Timestamp en ISO 8601 UTC con microsegundos: ``2026-09-28T15:10:25.242960Z``."""
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.strftime(_TS_FORMAT)


def write_requests_csv(df: pd.DataFrame, path: str | Path) -> Path:
    """Escribe requests.csv con las columnas y el formato de docs/contracts.md."""
    missing = [c for c in REQUESTS_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"faltan columnas en requests: {missing}")
    out = df.loc[:, list(REQUESTS_COLUMNS)].copy()
    for col in ("ts_start", "ts_end"):
        # numpy formatea en C; strftime de pandas es unas 20 veces más lento
        ts = pd.to_datetime(out[col], utc=True).dt.tz_convert("UTC").dt.tz_localize(None).dt.as_unit("ns")
        text = np.datetime_as_string(ts.to_numpy(dtype="datetime64[us]"), unit="us")
        out[col] = np.char.add(text.astype(str), "Z")
    out["matched"] = out["matched"].map(lambda v: "true" if bool(v) else "false")
    out["seq"] = out["seq"].astype("int64")
    out["status"] = out["status"].astype("int64")
    for col in _TEXT_COLUMNS:
        out[col] = out[col].fillna("").astype(str)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(p, index=False, float_format="%.3f", lineterminator="\n")
    return p


def read_requests_csv(path: str | Path) -> pd.DataFrame:
    """Lee requests.csv y devuelve los tipos del contrato.

    ``ts_start`` y ``ts_end`` salen como ``datetime64[ns, UTC]`` (se fija la
    resolución porque pandas 3 la infiere del texto), ``matched``
    como bool, ``think_time_ms`` como float con NaN en la primera petición de
    cada sesión y las columnas de texto como str (vacío si no hay valor).
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in REQUESTS_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: faltan columnas {missing}")
    df = df.loc[:, list(REQUESTS_COLUMNS)]
    df["seq"] = df["seq"].astype("int64")
    df["status"] = df["status"].astype("int64")
    df["latency_ms"] = pd.to_numeric(df["latency_ms"])
    df["think_time_ms"] = pd.to_numeric(df["think_time_ms"].replace("", np.nan))
    df["matched"] = df["matched"].str.lower().eq("true")
    for col in ("ts_start", "ts_end"):
        df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601").dt.as_unit("ns")
    return df
