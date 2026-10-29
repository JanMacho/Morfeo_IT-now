"""Análisis completo: normalización, sesiones y estadísticas.

``analyze`` recibe el DataFrame de ``read_logs`` y devuelve las peticiones
con las columnas de requests.csv y el resumen de analysis.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from morfeo.common import REQUESTS_COLUMNS, write_json, write_requests_csv
from morfeo.log_analyzer.config import AUTO, ParserConfig
from morfeo.log_analyzer.normalize import EndpointMatcher, normalize_requests
from morfeo.log_analyzer.parsers import ATTRS_KEY
from morfeo.log_analyzer.sessions import build_sessions
from morfeo.log_analyzer.stats import build_summary

_TEXT_COLUMNS = ("session_id", "client_id", "method", "path_raw", "message")


@dataclass
class AnalysisResult:
    """Resultado de ``analyze``.

    - ``requests``: columnas de ``morfeo.common.REQUESTS_COLUMNS`` en ese
      orden, ordenado por ``(session_id, seq)``.
    - ``summary``: contenido de analysis.json.
    - ``warnings``: avisos en texto (no van a analysis.json).
    """

    requests: pd.DataFrame
    summary: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def _effective_config(logs: pd.DataFrame, config: ParserConfig) -> ParserConfig:
    if config.format != AUTO:
        return config
    stored = logs.attrs.get(ATTRS_KEY, {}).get("config")
    if not stored:
        raise ValueError(
            "la configuración tiene format='auto' y el DataFrame no viene de read_logs; "
            "resuélvela antes con resolve_config"
        )
    return ParserConfig(**{**stored, "user_fields": tuple(stored.get("user_fields", ()))})


def _prepare(logs: pd.DataFrame) -> pd.DataFrame:
    """Copia de ``logs`` con las columnas y tipos que esperan los pasos siguientes."""
    missing = [c for c in ("timestamp", "method", "path_raw", "status") if c not in logs.columns]
    if missing:
        raise ValueError(f"faltan columnas en los logs: {missing}")
    frame = logs.reset_index(drop=True).copy()
    for col in _TEXT_COLUMNS:
        values = frame[col] if col in frame.columns else pd.Series("", index=frame.index)
        frame[col] = values.fillna("").astype("str")
    frame["method"] = frame["method"].str.upper()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.as_unit("ns")
    frame["status"] = frame["status"].astype("int64")
    latency = frame["latency_ms"] if "latency_ms" in frame.columns else pd.Series(0.0, index=frame.index)
    frame["latency_ms"] = pd.to_numeric(latency).fillna(0.0).astype("float64")
    return frame


def _number(x: float) -> int | float:
    return int(x) if float(x).is_integer() else float(x)


def analyze(
    logs: pd.DataFrame,
    *,
    config: ParserConfig,
    endpoints: dict[str, Any] | None = None,
    session_gap_s: float = 1800.0,
    timezone: str = "UTC",
) -> AnalysisResult:
    """Normaliza rutas, construye sesiones y calcula el resumen.

    ``endpoints`` es el contenido de endpoints.json; sin él, todas las rutas
    se normalizan con la heurística y ``matched`` queda a ``false``.
    ``timezone`` solo afecta a ``requests_per_hour.by_hour_of_day``.
    """
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"zona horaria desconocida: {timezone!r}") from exc
    if logs.empty:
        raise ValueError("no hay peticiones válidas en los logs")
    cfg = _effective_config(logs, config)
    info = logs.attrs.get(ATTRS_KEY, {})
    frame = _prepare(logs)

    matcher = EndpointMatcher(endpoints) if endpoints else None
    frame = pd.concat([frame, normalize_requests(frame["method"], frame["path_raw"], matcher)], axis=1)
    sessions = build_sessions(
        frame,
        timestamp_is=cfg.timestamp_is,
        missing_client_values=cfg.missing_client_values,
        session_gap_s=session_gap_s,
    )
    requests = sessions.requests.loc[:, list(REQUESTS_COLUMNS)]

    files = info.get("files")
    if files is None:
        files = sorted(frame["source_file"].unique().tolist()) if "source_file" in frame.columns else []
    input_info = {
        "files": list(files),
        "format": cfg.format,
        "timestamp_is": cfg.timestamp_is,
        "timezone": timezone,
        "session_gap_s": _number(session_gap_s),
        "session_source": sessions.session_source,
    }
    summary = build_summary(
        requests, input_info=input_info, timezone=timezone, negative_clipped=sessions.negative_clipped
    )

    warnings: list[str] = []
    if info.get("invalid_lines"):
        warnings.append(f"{info['invalid_lines']} líneas malformadas descartadas")
    if info.get("missing_latency"):
        warnings.append(f"{info['missing_latency']} peticiones sin latencia; se toma 0 ms")
    if matcher is not None:
        warnings.extend(matcher.warnings)
    warnings.extend(sessions.warnings)
    return AnalysisResult(requests=requests, summary=summary, warnings=warnings)


def write_analysis(result: AnalysisResult, out_dir: str | Path) -> dict[str, Path]:
    """Escribe requests.csv y analysis.json en ``out_dir``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    return {
        "requests": write_requests_csv(result.requests, out / "requests.csv"),
        "analysis": write_json(out / "analysis.json", result.summary),
    }
