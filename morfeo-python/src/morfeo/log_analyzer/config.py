"""Configuración del parser de logs.

Un ``ParserConfig`` describe cómo leer un formato de log: qué campos hay, en
qué orden o con qué expresión regular, en qué unidad viene la latencia y si el
timestamp marca el inicio o el fin de la petición. Hay cuatro presets
(``jsonl``, ``pipe``, ``clf`` y ``regex``) y un JSON de configuración puede
cambiar cualquier campo encima del preset.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FORMATS: tuple[str, ...] = ("jsonl", "pipe", "clf", "regex")
AUTO = "auto"

# Campos canónicos que el parser sabe interpretar.
CANONICAL_FIELDS: tuple[str, ...] = (
    "timestamp",
    "session_id",
    "client_id",
    "method",
    "path",
    "status",
    "latency_ms",
    "message",
)
REQUIRED_FIELDS: tuple[str, ...] = ("timestamp", "method", "path", "status")
IGNORED_COLUMN = "_"

# trafico.json del backend (JSON Lines): una clave por campo canónico, salvo el
# timestamp, que se llama ts_end porque es el FIN de la petición (en UTC).
BACKEND_JSON_TIMESTAMP = "ts_end"

# Formato pipe, el trafico.log anterior del backend: TIMESTAMP | METODO | RUTA | STATUS | MS | CLIENTE | SESION.
# Con las columnas por defecto, la última (session_id) es opcional: los logs
# antiguos de 6 columnas se siguen leyendo, sin sesión.
DEFAULT_PIPE_COLUMNS: tuple[str, ...] = (
    "timestamp", "method", "path", "status", "latency_ms", "client_id", "session_id",
)
OPTIONAL_LAST_PIPE_COLUMN = "session_id"
DEFAULT_MISSING_CLIENT_VALUES: tuple[str, ...] = ("UNKNOWN", "-", "")

LATENCY_UNITS: dict[str, float] = {"ms": 1.0, "s": 1000.0, "us": 0.001}
EPOCH_FORMATS: dict[str, str] = {"epoch_s": "s", "epoch_ms": "ms"}

# Common Log Format y Combined Log Format, con un campo final opcional de
# tiempo de respuesta en segundos (por ejemplo $request_time de Nginx).
CLF_PATTERN = (
    r"^(?P<host>\S+) (?P<ident>\S+) (?P<user>\S+) \[(?P<timestamp>[^\]]+)\] "
    r'"(?P<method>[A-Za-z]+) (?P<path>[^\s"]+)(?: [^"]*)?" '
    r"(?P<status>\d{3}) (?P<bytes>\d+|-)"
    r'(?: "(?P<referer>(?:[^"\\]|\\.)*)" "(?P<user_agent>(?:[^"\\]|\\.)*)")?'
    r"(?: (?P<latency_ms>\d+(?:\.\d+)?))?\s*$"
)
CLF_TIMESTAMP_FORMAT = "%d/%b/%Y:%H:%M:%S %z"

# Para la autodetección basta con que las cuatro primeras columnas del
# formato pipe tengan la forma esperada: algo, método, ruta y status.
_PIPE_DETECT = re.compile(r"^\s*[^|\s][^|]*\|\s*[A-Za-z]+\s*\|\s*[^|]*?\S\s*\|\s*\d{3}\s*(\||$)")
_CLF_DETECT = re.compile(CLF_PATTERN)


@dataclass
class ParserConfig:
    """Cómo leer un formato de log.

    - ``format``: ``jsonl``, ``pipe``, ``clf``, ``regex`` o ``auto`` (se
      resuelve al leer la primera línea, ver ``resolve_config``).
    - ``fields``: para jsonl, nombre canónico a clave del log. Admite claves
      anidadas con punto (``"http.method"``).
    - ``pattern``: expresión regular con grupos con nombre canónico (regex y clf).
    - ``pipe_columns`` y ``delimiter``: orden de columnas y separador del
      formato pipe. ``"_"`` marca una columna que se ignora. Con las columnas
      por defecto se aceptan líneas de 7 campos (con ``session_id``) y de 6
      (sin él); si el usuario fija ``pipe_columns``, se exige ese número.
    - ``timestamp_is``: ``start`` o ``end`` de la petición.
    - ``timestamp_format``: ``None`` (ISO 8601), ``epoch_s``, ``epoch_ms`` o
      un formato strftime.
    - ``input_timezone``: zona de los timestamps que no la indican.
    - ``latency_unit``: ``ms``, ``s`` o ``us``.
    - ``missing_client_values``: valores de cliente que significan "sin cliente".
    - ``skip_invalid``: descarta y cuenta las líneas malformadas; si es
      ``False``, la primera línea malformada da error con su número de línea.
    - ``user_fields``: campos que fijó el usuario en su JSON; se conservan
      al resolver ``auto``.
    """

    format: str = AUTO
    fields: dict[str, str] = field(default_factory=dict)
    pattern: str | None = None
    pipe_columns: list[str] = field(default_factory=lambda: list(DEFAULT_PIPE_COLUMNS))
    delimiter: str = "|"
    timestamp_is: str = "start"
    timestamp_format: str | None = None
    input_timezone: str = "UTC"
    latency_unit: str = "ms"
    missing_client_values: list[str] = field(default_factory=lambda: list(DEFAULT_MISSING_CLIENT_VALUES))
    skip_invalid: bool = True
    user_fields: tuple[str, ...] = field(default=(), repr=False)

    def validate(self) -> None:
        """Comprueba la coherencia de la configuración y lanza ``ValueError`` si falla."""
        if self.format not in FORMATS and self.format != AUTO:
            raise ValueError(f"formato desconocido: {self.format!r} (válidos: auto, {', '.join(FORMATS)})")
        if self.timestamp_is not in ("start", "end"):
            raise ValueError(f"timestamp_is debe ser 'start' o 'end', no {self.timestamp_is!r}")
        if self.latency_unit not in LATENCY_UNITS:
            raise ValueError(f"latency_unit debe ser uno de {sorted(LATENCY_UNITS)}, no {self.latency_unit!r}")
        tf = self.timestamp_format
        if tf is not None and tf not in EPOCH_FORMATS and "%" not in tf:
            raise ValueError(f"timestamp_format no válido: {tf!r} (null, epoch_s, epoch_ms o un formato strftime)")
        try:
            ZoneInfo(self.input_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"input_timezone desconocida: {self.input_timezone!r}") from exc
        unknown = sorted(set(self.fields) - set(CANONICAL_FIELDS))
        if unknown:
            raise ValueError(f"fields tiene claves que no son campos canónicos: {unknown}")
        if not all(isinstance(v, str) for v in self.missing_client_values):
            raise ValueError("missing_client_values debe ser una lista de textos")
        if self.format == "jsonl":
            missing = [f for f in REQUIRED_FIELDS if not self.fields.get(f)]
            if missing:
                raise ValueError(f"jsonl: faltan en fields los campos obligatorios {missing}")
        if self.format == "pipe":
            if len(self.delimiter) != 1:
                raise ValueError(f"delimiter debe ser un único carácter, no {self.delimiter!r}")
            bad = [c for c in self.pipe_columns if c not in CANONICAL_FIELDS and c != IGNORED_COLUMN]
            if bad:
                raise ValueError(f"pipe_columns tiene columnas desconocidas: {bad} (usa '_' para ignorar una)")
            named = [c for c in self.pipe_columns if c != IGNORED_COLUMN]
            if len(named) != len(set(named)):
                raise ValueError("pipe_columns repite alguna columna")
            missing = [f for f in REQUIRED_FIELDS if f not in self.pipe_columns]
            if missing:
                raise ValueError(f"pipe_columns no incluye los campos obligatorios {missing}")
        if self.format in ("regex", "clf"):
            if not self.pattern:
                raise ValueError(f"el formato {self.format} necesita 'pattern' en la configuración")
            try:
                compiled = re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"pattern no es una expresión regular válida: {exc}") from exc
            groups = set(compiled.groupindex)
            missing = [f for f in REQUIRED_FIELDS if f not in groups]
            if missing:
                raise ValueError(f"pattern no tiene los grupos con nombre obligatorios {missing}")


def preset(fmt: str) -> ParserConfig:
    """Configuración por defecto de cada formato."""
    if fmt == "jsonl":
        return ParserConfig(format="jsonl", fields={f: f for f in CANONICAL_FIELDS}, timestamp_is="start")
    if fmt == "pipe":
        # El backend registra Instant.now() al terminar la petición.
        return ParserConfig(format="pipe", timestamp_is="end")
    if fmt == "clf":
        return ParserConfig(
            format="clf",
            pattern=CLF_PATTERN,
            timestamp_format=CLF_TIMESTAMP_FORMAT,
            latency_unit="s",
            timestamp_is="start",
        )
    if fmt == "regex":
        return ParserConfig(format="regex", timestamp_is="start")
    if fmt == AUTO:
        return ParserConfig(format=AUTO)
    raise ValueError(f"formato desconocido: {fmt!r} (válidos: auto, {', '.join(FORMATS)})")


def override(base: ParserConfig, **overrides: Any) -> ParserConfig:
    """Copia de ``base`` con los campos indicados, que se marcan como del usuario."""
    return _apply_overrides(base, overrides)


def _apply_overrides(base: ParserConfig, overrides: dict[str, Any]) -> ParserConfig:
    values = dict(overrides)
    if "fields" in values:
        # Los campos del usuario se mezclan con los del preset; null quita uno.
        merged = dict(base.fields)
        for key, value in dict(values["fields"]).items():
            if value is None or value == "":
                merged.pop(key, None)
            else:
                merged[key] = value
        values["fields"] = merged
    for key in ("pipe_columns", "missing_client_values"):
        if key in values:
            values[key] = list(values[key])
    user = tuple(sorted(set(base.user_fields) | set(overrides)))
    return replace(base, **values, user_fields=user)


def load_parser_config(format: str | None = AUTO, config_path: str | Path | None = None) -> ParserConfig:
    """Construye la configuración a partir del preset y de un JSON opcional.

    El JSON puede traer cualquiera de los campos de ``ParserConfig``. El
    preset es el de su campo ``format`` o, si no lo trae, el de ``format``.
    Si ambos se indican y no coinciden, se lanza ``ValueError``.
    """
    fmt = format or AUTO
    overrides: dict[str, Any] = {}
    if config_path is not None:
        try:
            data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{config_path}: JSON no válido ({exc})") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{config_path}: la configuración debe ser un objeto JSON")
        valid = {f.name for f in fields(ParserConfig)} - {"user_fields"}
        unknown = sorted(set(data) - valid)
        if unknown:
            raise ValueError(f"{config_path}: campos desconocidos {unknown} (válidos: {sorted(valid)})")
        file_fmt = data.pop("format", None)
        if file_fmt:
            if fmt not in (AUTO, file_fmt):
                raise ValueError(f"el formato indicado ({fmt}) no coincide con el del JSON ({file_fmt})")
            fmt = file_fmt
        overrides = data
    config = _apply_overrides(preset(fmt), overrides)
    config.validate()
    return config


def detect_format(line: str) -> str:
    """Formato de una línea de log: jsonl, pipe o clf. ``ValueError`` si no se reconoce."""
    text = line.strip()
    if text.startswith("{"):
        return "jsonl"
    if _PIPE_DETECT.match(text):
        return "pipe"
    if _CLF_DETECT.match(text):
        return "clf"
    raise ValueError(
        f"no se reconoce el formato del log a partir de la primera línea {text[:120]!r}; "
        "indícalo con --format (jsonl, pipe, clf o regex)"
    )


def resolve_auto(config: ParserConfig, first_line: str) -> ParserConfig:
    """Sustituye ``auto`` por el formato detectado y conserva los campos del usuario."""
    if config.format != AUTO:
        return config
    fmt = detect_format(first_line)
    overrides = {name: getattr(config, name) for name in config.user_fields}
    base = preset(fmt)
    if fmt == "jsonl" and _is_backend_json(first_line):
        base = replace(base, fields={**base.fields, "timestamp": BACKEND_JSON_TIMESTAMP}, timestamp_is="end")
    resolved = _apply_overrides(base, overrides)
    resolved.validate()
    return resolved


def _is_backend_json(line: str) -> bool:
    """``True`` si la línea es del ``trafico.json`` del backend (trae ``ts_end``)."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and BACKEND_JSON_TIMESTAMP in obj
