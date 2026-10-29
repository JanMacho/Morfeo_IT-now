"""Plantillas de ruta: normalización, unión, variables y expresión regular (docs/contracts.md)."""

from __future__ import annotations

import re

_JAVALIN_PARAM = re.compile(r"<([^<>/]+)>")


def normalize_path(path: str) -> str:
    """Barra inicial, sin ``//`` y sin barra final salvo en ``/``."""
    path = re.sub(r"/{2,}", "/", "/" + path.strip())
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return path


def join_paths(*parts: str) -> str:
    """Une segmentos como Spring al combinar la ruta de clase y la de método."""
    return normalize_path("/".join(p for p in parts if p))


def javalin_to_template(path: str) -> str:
    """``/api/<id>`` (Javalin) a ``/api/{id}``."""
    return _JAVALIN_PARAM.sub(r"{\1}", path)


def _variables(template: str) -> list[tuple[int, int, str]]:
    """Variables ``{…}`` con llaves anidadas (``{id:\\d{3}}``): (inicio, fin, contenido)."""
    out: list[tuple[int, int, str]] = []
    i = 0
    while i < len(template):
        if template[i] == "{":
            depth = 0
            for j in range(i, len(template)):
                if template[j] == "{":
                    depth += 1
                elif template[j] == "}":
                    depth -= 1
                    if depth == 0:
                        out.append((i, j + 1, template[i + 1 : j]))
                        i = j
                        break
            else:
                break  # llave sin cerrar: se trata como texto
        i += 1
    return out


def path_variables(template: str) -> list[str]:
    """Nombres de las variables de la plantilla, en orden (``{*resto}`` da ``resto``)."""
    names = []
    for _, _, body in _variables(template):
        name = body.split(":", 1)[0].lstrip("*").strip()
        if name and name not in names:
            names.append(name)
    return names


def _literal_regex(text: str) -> str:
    out = []
    i = 0
    while i < len(text):
        if text.startswith("**", i):
            out.append(".*")
            i += 2
        elif text[i] == "*":
            out.append("[^/]*")
            i += 1
        elif text[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(text[i]))
            i += 1
    return "".join(out)


def path_regex(template: str) -> str:
    """Expresión anclada: ``{x}`` a ``[^/]+``, ``{x:re}`` a ``(?:re)``, ``*`` a ``[^/]*``, ``**`` y ``{*x}`` a ``.*``."""
    parts = ["^"]
    pos = 0
    for start, end, body in _variables(template):
        parts.append(_literal_regex(template[pos:start]))
        if body.startswith("*"):
            parts.append(".*")
        elif ":" in body:
            parts.append(f"(?:{body.split(':', 1)[1]})")
        else:
            parts.append("[^/]+")
        pos = end
    parts.append(_literal_regex(template[pos:]))
    parts.append("$")
    return "".join(parts)
