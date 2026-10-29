"""Lectura de logs a un DataFrame canónico.

``read_logs`` acepta ficheros y carpetas (se recorren de forma recursiva),
admite ``.gz`` y devuelve una fila por petición con estas columnas:

- ``timestamp``: ``datetime64[ns, UTC]``, tal como viene en el log.
- ``session_id``, ``client_id``, ``message``: texto, vacío si no hay.
- ``method``: en mayúsculas.
- ``path_raw``: ruta tal como viene, con query string.
- ``status``: entero. ``latency_ms``: real, en milisegundos.
- ``source_file`` (nombre del fichero) y ``line_no`` (desde 1).

El trabajo se hace por columnas con pandas. Solo el formato jsonl recorre
las líneas una a una para ``json.loads``.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from collections.abc import Sequence
from dataclasses import asdict
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from morfeo.log_analyzer.config import (
    AUTO,
    EPOCH_FORMATS,
    detect_format,
    IGNORED_COLUMN,
    LATENCY_UNITS,
    OPTIONAL_LAST_PIPE_COLUMN,
    ParserConfig,
    resolve_auto,
)

LOG_PATTERNS: tuple[str, ...] = ("*.log", "*.jsonl", "*.json", "*.txt")
ATTRS_KEY = "morfeo_log"

_METHOD_RE = r"^[A-Z]+$"
_SCHEME_RE = r"^[A-Za-z][A-Za-z0-9+.\-]*://[^/?#]*"
_MISSING_TEXT = ("", "-")


class LogParseError(ValueError):
    """Línea de log no válida cuando ``skip_invalid`` es ``False``."""


def collect_files(paths: Sequence[str | Path] | str | Path) -> list[Path]:
    """Ficheros de log a leer, ordenados por ruta.

    Un fichero indicado de forma explícita se lee siempre. En una carpeta se
    buscan de forma recursiva los patrones de ``LOG_PATTERNS`` y sus ``.gz``.
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    found: set[Path] = set()
    for item in paths:
        p = Path(item)
        if p.is_dir():
            resolved = p.resolve()
            if resolved == Path(resolved.anchor):
                # p. ej. un "\" suelto en PowerShell: recorrer todo el disco no tiene sentido
                raise ValueError(
                    f"la ruta de logs {str(item)!r} es la raíz del disco ({resolved}); "
                    "indica la carpeta de los logs. En PowerShell, para partir un comando "
                    "en varias líneas se usa ` (acento grave), no \\"
                )
            for f in p.rglob("*"):
                if f.is_file() and _is_log_name(f.name):
                    found.add(f)
        elif p.is_file():
            found.add(p)
        else:
            raise FileNotFoundError(f"no existe el fichero o carpeta de logs: {p}")
    if not found:
        raise ValueError("no se ha encontrado ningún fichero de log en las rutas indicadas")
    return sorted(found, key=lambda f: f.as_posix())


def _is_log_name(name: str) -> bool:
    lower = name.lower()
    if lower.endswith(".gz"):
        lower = lower[:-3]
    return any(fnmatch(lower, pattern) for pattern in LOG_PATTERNS)


def _decode(raw: bytes) -> list[str]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # logs escritos en Windows con la codificación del sistema (José, año, €)
        text = raw.decode("cp1252", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _read_lines(path: Path) -> list[str]:
    raw = path.read_bytes()
    if path.name.lower().endswith(".gz"):
        raw = gzip.decompress(raw)
    return _decode(raw)


HEAD_BYTES = 256 * 1024


def _head_lines(path: Path) -> list[str]:
    """Primeras líneas completas del fichero, leyendo solo su principio (para detectar el formato)."""
    opener = gzip.open if path.name.lower().endswith(".gz") else open
    with opener(path, "rb") as fh:
        raw = fh.read(HEAD_BYTES + 1)
    if len(raw) > HEAD_BYTES:
        raw = raw[: raw.rfind(b"\n", 0, HEAD_BYTES) + 1] or raw[:HEAD_BYTES]  # sin la línea cortada
    return _decode(raw)


DETECT_LINES = 20


def first_line(files: Sequence[Path]) -> str:
    """Primera línea no vacía del primer fichero que tenga alguna."""
    for line in _first_lines(files, 1):
        return line
    raise ValueError("los ficheros de log están vacíos")


def _first_lines(files: Sequence[Path], n: int) -> list[str]:
    out: list[str] = []
    for f in files:
        for line in _head_lines(f):
            if line.strip():
                out.append(line)
                if len(out) == n:
                    return out
    return out


def resolve_config(config: ParserConfig, paths: Sequence[str | Path] | str | Path) -> ParserConfig:
    """Resuelve ``format="auto"`` con la primera de las 20 primeras líneas que se reconozca.

    Mirar varias líneas evita fallar por una primera línea atípica, como una
    petición inválida de Nginx (``"-" 400``) o una cabecera.
    """
    if config.format != AUTO:
        return config
    lines = _first_lines(collect_files(paths), DETECT_LINES)
    if not lines:
        raise ValueError("los ficheros de log están vacíos")
    for line in lines:
        try:
            detect_format(line)
        except ValueError:
            continue
        return resolve_auto(config, line)
    return resolve_auto(config, lines[0])  # da el error con la primera línea


def read_logs(paths: Sequence[str | Path] | str | Path, config: ParserConfig) -> pd.DataFrame:
    """Lee los logs y devuelve el DataFrame canónico descrito en el módulo.

    La configuración resuelta, los nombres de fichero y el número de líneas
    descartadas quedan en ``df.attrs["morfeo_log"]`` para ``analyze``.
    """
    files = collect_files(paths)
    config = resolve_config(config, files)
    config.validate()

    chunks: list[list[str]] = []
    file_idx: list[np.ndarray] = []
    line_no: list[np.ndarray] = []
    for i, f in enumerate(files):
        lines = _read_lines(f)
        chunks.append(lines)
        file_idx.append(np.full(len(lines), i, dtype=np.int64))
        line_no.append(np.arange(1, len(lines) + 1, dtype=np.int64))
    all_lines = [line for chunk in chunks for line in chunk]
    lines = pd.Series(all_lines, dtype="str").str.strip()
    meta = pd.DataFrame(
        {
            "file_idx": np.concatenate(file_idx) if file_idx else np.empty(0, np.int64),
            "line_no": np.concatenate(line_no) if line_no else np.empty(0, np.int64),
        }
    )
    keep = (lines != "").to_numpy()
    lines = lines[keep].reset_index(drop=True)
    meta = meta[keep].reset_index(drop=True)

    if config.format == "jsonl":
        raw, reason = _parse_jsonl(lines, config)
    elif config.format == "pipe":
        raw, reason = _parse_pipe(lines, config)
    else:
        raw, reason = _parse_regex(lines, config)

    frame, field_reason, latency_missing = _to_canonical(raw, config)
    reason = reason.where(reason != "", field_reason)
    invalid = (reason != "").to_numpy()
    names = [f.name for f in files]
    if invalid.any() and (not config.skip_invalid or invalid.all()):
        first = int(np.flatnonzero(invalid)[0])
        where = f"{files[int(meta['file_idx'].iat[first])]}:{int(meta['line_no'].iat[first])}"
        text = lines.iat[first][:200]
        if config.skip_invalid:
            raise LogParseError(f"ninguna línea válida; la primera falla en {where}: {reason.iat[first]}: {text!r}")
        raise LogParseError(f"{where}: {reason.iat[first]}: {text!r}")
    frame = frame[~invalid].reset_index(drop=True)
    meta = meta[~invalid].reset_index(drop=True)
    frame["source_file"] = pd.Series(np.array(names, dtype=object)[meta["file_idx"].to_numpy()], dtype="str")
    frame["line_no"] = meta["line_no"].to_numpy()
    frame.attrs[ATTRS_KEY] = {
        "config": asdict(config),
        "files": sorted(names),
        "invalid_lines": int(invalid.sum()),
        "missing_latency": int((latency_missing & ~invalid).sum()),
    }
    return frame


def _no_reason(n: int, index: pd.Index | None = None) -> pd.Series:
    return pd.Series(np.full(n, "", dtype=object), index=index, dtype="str")


def _parse_jsonl(lines: pd.Series, config: ParserConfig) -> tuple[pd.DataFrame, pd.Series]:
    records: list[dict[str, Any]] = []
    reason = np.full(len(lines), "", dtype=object)
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            obj = None
            reason[i] = "JSON no válido"
        if not isinstance(obj, dict):
            reason[i] = reason[i] or "la línea no es un objeto JSON"
            obj = {}
        records.append(obj)
    # json_normalize aplana los objetos anidados con "." (http.method), que es
    # la misma notación que usa ``fields``.
    flat = pd.json_normalize(records, sep=".") if records else pd.DataFrame()
    flat.index = lines.index
    raw = pd.DataFrame(index=lines.index)
    for name, key in config.fields.items():
        if key in flat.columns:
            raw[name] = flat[key]
    return raw, pd.Series(reason, index=lines.index, dtype="str")


def _parse_pipe(lines: pd.Series, config: ParserConfig) -> tuple[pd.DataFrame, pd.Series]:
    cols = list(config.pipe_columns)
    delim = config.delimiter
    names = [f"c{i}" for i in range(len(cols))]
    separators = lines.str.count(re.escape(delim))
    ok = (separators == len(cols) - 1).to_numpy()
    # Columnas por defecto: una línea sin la última columna (session_id) es un
    # log antiguo de 6 campos. Se completa con un campo vacío y queda sin sesión.
    optional_last = "pipe_columns" not in config.user_fields and cols[-1] == OPTIONAL_LAST_PIPE_COLUMN
    if optional_last:
        short = (separators == len(cols) - 2).to_numpy()
        if short.any():
            lines = lines.copy()
            lines[short] = lines[short] + delim
            ok = ok | short
    reason = _no_reason(len(lines), lines.index)
    expected = f"{len(cols) - 1} o {len(cols)}" if optional_last else str(len(cols))
    reason[~ok] = f"se esperaban {expected} campos separados por {delim!r}"
    if ok.any():
        # El motor C de read_csv parte las columnas mucho más rápido que
        # str.split en pandas sin pyarrow. Todas las líneas que llegan aquí
        # tienen el número exacto de separadores.
        parsed = pd.read_csv(
            io.StringIO("\n".join(lines[ok].tolist()) + "\n"),
            sep=delim,
            header=None,
            names=names,
            dtype=str,
            quoting=csv.QUOTE_NONE,
            skip_blank_lines=False,
            keep_default_na=False,
            na_filter=False,
            engine="c",
        )
        parsed.index = lines.index[ok]
        raw = parsed.reindex(lines.index)
    else:
        raw = pd.DataFrame(np.nan, index=lines.index, columns=names)
    raw.columns = cols
    return raw.loc[:, [c for c in cols if c != IGNORED_COLUMN]], reason


def _parse_regex(lines: pd.Series, config: ParserConfig) -> tuple[pd.DataFrame, pd.Series]:
    assert config.pattern is not None
    groups = re.compile(config.pattern).groupindex
    extracted = lines.str.extract(config.pattern, expand=True)
    raw = extracted.loc[:, [g for g in groups if g in extracted.columns]]
    if "client_id" not in raw.columns and ("user" in raw.columns or "host" in raw.columns):
        # CLF: el usuario autenticado si lo hay, si no la IP del cliente.
        missing = set(config.missing_client_values)
        user = raw["user"] if "user" in raw.columns else pd.Series(np.nan, index=raw.index)
        host = raw["host"] if "host" in raw.columns else pd.Series(np.nan, index=raw.index)
        raw = raw.assign(client_id=user.where(user.notna() & ~user.isin(missing), host))
    reason = _no_reason(len(lines), lines.index)
    reason[raw.isna().all(axis=1).to_numpy()] = "no encaja con el patrón"
    return raw, reason


def _clean_text(raw: pd.DataFrame, name: str) -> pd.Series:
    """Columna de texto sin nulos ni espacios en los extremos.

    Se limpia cada valor distinto una sola vez: en un log hay muchos menos
    valores distintos que líneas.
    """
    if name not in raw.columns:
        return pd.Series("", index=raw.index, dtype="str")
    codes, uniques = pd.factorize(raw[name])
    cleaned = pd.Series(np.asarray(uniques, dtype=object)).astype("str").str.strip().to_numpy(dtype=object)
    # El código -1 (valor nulo) toma el último elemento, que es el texto vacío.
    values = np.append(cleaned, "")
    return pd.Series(values[codes], index=raw.index, dtype="str")


def parse_timestamps(values: pd.Series, config: ParserConfig) -> pd.Series:
    """Convierte la columna de timestamps a ``datetime64[ns, UTC]`` (NaT si no se puede)."""
    fmt = config.timestamp_format
    if fmt in EPOCH_FORMATS:
        num = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
        ns = np.rint(num * (1e9 if fmt == "epoch_s" else 1e6))
        good = np.isfinite(ns)
        out = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns, UTC]")
        out[good] = pd.to_datetime(ns[good].astype(np.int64), unit="ns", utc=True)
        return out
    text = values.astype("str").fillna("").str.strip()
    if fmt is not None:
        aware = "%z" in fmt or "%Z" in fmt
        parsed = pd.to_datetime(text, format=fmt, errors="coerce", utc=aware)
        return (parsed if aware else _localize(parsed, config.input_timezone)).dt.as_unit("ns")
    if config.input_timezone == "UTC":
        return pd.to_datetime(text, utc=True, format="ISO8601", errors="coerce").dt.as_unit("ns")
    # Los timestamps sin zona se interpretan en input_timezone.
    has_zone = text.str.contains(r"(?:Z|[+-]\d{2}(?::?\d{2})?)$", regex=True).to_numpy()
    out = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns, UTC]")
    if has_zone.any():
        out[has_zone] = pd.to_datetime(text[has_zone], utc=True, format="ISO8601", errors="coerce").dt.as_unit("ns")
    if (~has_zone).any():
        parsed = pd.to_datetime(text[~has_zone], format="ISO8601", errors="coerce")
        out[~has_zone] = _localize(parsed, config.input_timezone).dt.as_unit("ns")
    return out


def _localize(parsed: pd.Series, tz: str) -> pd.Series:
    # Las horas inexistentes o repetidas por el cambio de hora quedan como NaT.
    return parsed.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")


def _clean_path(original: pd.Series) -> pd.Series:
    """Quita esquema y host de las URL absolutas (``http://h/x`` pasa a ``/x``)."""
    codes, uniques = pd.factorize(original)
    u = pd.Series(np.asarray(uniques, dtype=object), dtype="str")
    stripped = u.str.replace(_SCHEME_RE, "", regex=True)
    changed = stripped != u
    fixed = stripped.where(~changed | stripped.str.startswith("/"), "/" + stripped)
    return pd.Series(fixed.to_numpy(dtype=object)[codes], index=original.index, dtype="str")


def _to_canonical(raw: pd.DataFrame, config: ParserConfig) -> tuple[pd.DataFrame, pd.Series, np.ndarray]:
    """Tipos canónicos, motivo de invalidez por fila y filas sin latencia."""
    index = raw.index
    if "timestamp" in raw.columns:
        ts = parse_timestamps(raw["timestamp"], config)
    else:
        ts = pd.Series(pd.NaT, index=index, dtype="datetime64[ns, UTC]")

    method = _clean_text(raw, "method").str.upper()
    path = _clean_path(_clean_text(raw, "path"))
    status = pd.to_numeric(_clean_text(raw, "status"), errors="coerce").to_numpy(dtype=float)
    latency_text = _clean_text(raw, "latency_ms")
    latency = pd.to_numeric(latency_text, errors="coerce").to_numpy(dtype=float) * LATENCY_UNITS[config.latency_unit]
    # Sin latencia ("", "-" o negativa, como el -1 del backend) se toma 0.
    with np.errstate(invalid="ignore"):
        latency_missing = latency_text.isin(_MISSING_TEXT).to_numpy() | (latency < 0)
        bad_status = ~(np.isfinite(status) & (status % 1 == 0) & (status >= 100) & (status <= 599))
    bad_latency = np.isnan(latency) & ~latency_missing
    reason = np.select(
        [
            ts.isna().to_numpy(),
            ~method.str.match(_METHOD_RE).to_numpy(dtype=bool),
            ~path.str.startswith("/").to_numpy(dtype=bool),
            bad_status,
            bad_latency,
        ],
        ["timestamp no válido", "método no válido", "ruta no válida", "status no válido", "latencia no válida"],
        default="",
    )
    frame = pd.DataFrame(
        {
            "timestamp": ts,
            "session_id": _clean_text(raw, "session_id"),
            "client_id": _clean_text(raw, "client_id"),
            "method": method,
            "path_raw": path,
            "status": np.where(bad_status, 0, np.nan_to_num(status)).astype(np.int64),
            "latency_ms": np.where(latency_missing | bad_latency, 0.0, latency),
            "message": _clean_text(raw, "message"),
        },
        index=index,
    )
    return frame, pd.Series(reason.astype(object), index=index, dtype="str"), latency_missing
