"""Normalización de rutas: de ``/api/saldo/user_001`` a ``/api/saldo/{id}``.

Con un endpoints.json, cada ruta se compara con el ``path_regex`` de los
endpoints. Sin él, o si ninguna plantilla casa, se aplica una heurística por
segmento que sustituye los identificadores por ``{id}``, ``{id2}``, ...
Las dos cosas se calculan una vez por cada par (método, ruta) distinto.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from morfeo.common import endpoint_id

ANY_METHOD = "ANY"

# {x}, {x:regex} (con un nivel de llaves dentro, como \d{3}), {*x}, ** y *.
_VAR_RE = re.compile(r"\{(?:[^{}]|\{[^{}]*\})*\}|\*\*|\*")

_INT_RE = re.compile(r"^\d+$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{8,}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIXED_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9._~+\-]+$")
_VERSION_RE = re.compile(r"^[vV]\d+(?:\.\d+)*$")
_LONG_TOKEN_RE = re.compile(r"^[A-Za-z0-9+=_\-]{20,}$")


def clean_path(path: str) -> str:
    """Ruta para comparar: sin ``//`` repetidas ni barra final (salvo ``/``)."""
    p = re.sub(r"/{2,}", "/", path)
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/") or "/"
    return p or "/"


def split_query(path_raw: str) -> tuple[str, str]:
    """Separa ruta y query string (sin ``?``); descarta el fragmento ``#``."""
    path, _, query = path_raw.partition("?")
    path = path.partition("#")[0]
    query = query.partition("#")[0]
    return path or "/", query


def template_to_regex(path: str) -> str:
    """Expresión anclada de una plantilla, con las reglas de docs/contracts.md."""
    out: list[str] = []
    pos = 0
    for m in _VAR_RE.finditer(path):
        out.append(re.escape(path[pos : m.start()]))
        token = m.group(0)
        if token == "**" or token.startswith("{*"):
            out.append(".*")
        elif token == "*":
            out.append("[^/]*")
        elif ":" in token:
            out.append(f"(?:{token[1:-1].split(':', 1)[1]})")
        else:
            out.append("[^/]+")
        pos = m.end()
    out.append(re.escape(path[pos:]))
    return "^" + "".join(out) + "$"


def is_identifier(segment: str) -> bool:
    """Si un segmento de ruta parece un identificador y no un literal."""
    if not segment:
        return False
    if _INT_RE.match(segment) or _UUID_RE.match(segment) or _HEX_RE.match(segment) or _EMAIL_RE.match(segment):
        return True
    if _VERSION_RE.match(segment):
        return False
    if _MIXED_RE.match(segment):
        return True
    if _LONG_TOKEN_RE.match(segment):
        has_upper = any(c.isupper() for c in segment)
        has_lower = any(c.islower() for c in segment)
        return (has_upper and has_lower) or any(c in "+=" for c in segment)
    return False


def heuristic_template(path: str) -> str:
    """Plantilla heurística: los identificadores pasan a ``{id}``, ``{id2}``, ..."""
    segments = clean_path(path).split("/")
    n = 0
    for i, seg in enumerate(segments):
        if is_identifier(seg):
            n += 1
            segments[i] = "{id}" if n == 1 else f"{{id{n}}}"
    return "/".join(segments) or "/"


@dataclass(frozen=True)
class Template:
    """Endpoint de endpoints.json preparado para comparar rutas."""

    id: str
    method: str
    path: str
    regex: re.Pattern[str]
    variables: int
    literal_chars: int

    def sort_key(self) -> tuple[int, int, int, str]:
        # Más específica primero: menos variables, más caracteres literales,
        # método concreto antes que ANY, y por último el orden alfabético.
        return (self.variables, -self.literal_chars, int(self.method == ANY_METHOD), self.id)


class EndpointMatcher:
    """Compara rutas del log con las plantillas de un endpoints.json."""

    def __init__(self, doc: Mapping[str, Any] | None) -> None:
        self.warnings: list[str] = []
        ctx = str((doc or {}).get("context_path") or "").strip()
        self.context_path = "" if ctx in ("", "/") else clean_path("/" + ctx.lstrip("/"))
        templates: list[Template] = []
        for ep in (doc or {}).get("endpoints", []) or []:
            method = str(ep.get("method", ANY_METHOD)).upper()
            path = str(ep.get("path", ""))
            regex_text = ep.get("path_regex") or template_to_regex(path)
            try:
                regex = re.compile(regex_text)
            except re.error as exc:
                self.warnings.append(f"path_regex no válido en {method} {path}: {exc}; se ignora el endpoint")
                continue
            literal = _VAR_RE.sub("", path)
            templates.append(
                Template(
                    id=endpoint_id(method, path),
                    method=method,
                    path=path,
                    regex=regex,
                    variables=len(_VAR_RE.findall(path)),
                    literal_chars=len(literal),
                )
            )
        self.templates: tuple[Template, ...] = tuple(sorted(templates, key=Template.sort_key))
        self._cache: dict[tuple[str, str], Template | None] = {}

    def _candidates(self, path: str) -> list[str]:
        ctx = self.context_path
        out = [path]
        if ctx:
            if path == ctx or path.startswith(ctx + "/"):
                out.append(path[len(ctx) :] or "/")
            else:
                out.append(clean_path(ctx + path))
        return out

    def match(self, method: str, path: str) -> Template | None:
        """Plantilla más específica que casa con (método, ruta), o ``None``.

        Se prueba primero la ruta tal cual y después con el ``context_path``
        añadido o quitado.
        """
        key = (method, path)
        if key not in self._cache:
            found: Template | None = None
            clean = clean_path(path)
            for candidate in self._candidates(clean):
                found = next(
                    (
                        t
                        for t in self.templates
                        if (t.method == method or t.method == ANY_METHOD) and t.regex.match(candidate)
                    ),
                    None,
                )
                if found is not None:
                    break
            self._cache[key] = found
        return self._cache[key]


def normalize_requests(
    method: pd.Series, path_raw: pd.Series, matcher: EndpointMatcher | None
) -> pd.DataFrame:
    """Columnas ``raw_path``, ``query``, ``path``, ``endpoint`` y ``matched``.

    ``path`` es la plantilla del endpoint si casó con endpoints.json, o la
    heurística si no. Se calcula una vez por cada par (método, ruta) distinto.
    """
    m_codes, m_uniques = pd.factorize(method)
    p_codes, p_uniques = pd.factorize(path_raw)
    n_paths = max(len(p_uniques), 1)
    combo = m_codes.astype(np.int64) * n_paths + p_codes
    keys, inverse = np.unique(combo, return_inverse=True)

    split = [split_query(str(p)) for p in p_uniques]
    heuristic: dict[str, str] = {}
    cols: dict[str, list[Any]] = {"raw_path": [], "query": [], "path": [], "endpoint": [], "matched": []}
    for key in keys.tolist():
        m = str(m_uniques[key // n_paths])
        raw, query = split[key % n_paths]
        tpl = matcher.match(m, raw) if matcher is not None else None
        if tpl is not None:
            path, matched = tpl.path, True
        else:
            if raw not in heuristic:
                heuristic[raw] = heuristic_template(raw)
            path, matched = heuristic[raw], False
        cols["raw_path"].append(raw)
        cols["query"].append(query)
        cols["path"].append(path)
        cols["endpoint"].append(endpoint_id(m, path))
        cols["matched"].append(matched)

    out = pd.DataFrame(index=method.index)
    for name, values in cols.items():
        arr = np.array(values, dtype=bool if name == "matched" else object)[inverse]
        out[name] = arr if name == "matched" else pd.Series(arr, index=method.index, dtype="str")
    return out
