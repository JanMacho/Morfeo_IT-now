"""Lectura mínima de application.properties y application.yml sin dependencias.

Solo interesa sacar ``server.servlet.context-path``, ``spring.mvc.servlet.path``
y las propiedades que usen los placeholders de las rutas, así que el lector de
YAML entiende mapas anidados por indentación, valores escalares y comentarios,
y nada más (listas, bloques ``|`` y anclas se ignoran).
"""

from __future__ import annotations

import re
from typing import Callable, Mapping

_PLACEHOLDER = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")

# Orden de precedencia de Spring Boot: config/ antes que la raíz y .properties antes que .yml
CONFIG_FILES = (
    "config/application.properties",
    "config/application.yml",
    "config/application.yaml",
    "application.properties",
    "application.yml",
    "application.yaml",
)


def normalize_key(key: str) -> str:
    """Forma canónica para el enlace flexible de Spring: sin guiones ni guiones bajos, en minúsculas."""
    return key.strip().replace("-", "").replace("_", "").lower()


def _unescape_properties(text: str) -> str:
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == "u" and i + 6 <= len(text):
                try:
                    out.append(chr(int(text[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append({"t": "\t", "n": "\n", "r": "\r", "f": "\f"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_properties(text: str) -> dict[str, str]:
    """Formato ``java.util.Properties``: ``clave=valor``, ``clave: valor`` o ``clave valor``."""
    result: dict[str, str] = {}
    logical: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.lstrip()
        if not buffer and (not line or line[0] in "#!"):
            continue
        trailing = len(line) - len(line.rstrip("\\"))
        if trailing % 2 == 1:  # continúa en la línea siguiente
            buffer += line[:-1]
            continue
        logical.append(buffer + line)
        buffer = ""
    if buffer:
        logical.append(buffer)
    for line in logical:
        key_chars = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\" and i + 1 < len(line):
                key_chars.append(line[i : i + 2])
                i += 2
                continue
            if ch in "=: \t":
                break
            key_chars.append(ch)
            i += 1
        rest = line[i:].lstrip(" \t")
        if rest[:1] in ("=", ":"):
            rest = rest[1:].lstrip(" \t")
        key = _unescape_properties("".join(key_chars))
        if key:
            result[key] = _unescape_properties(rest)
    return result


def _strip_yaml_comment(value: str) -> str:
    quote = ""
    for i, ch in enumerate(value):
        if ch in "'\"":
            quote = "" if quote == ch else (quote or ch)
        elif ch == "#" and not quote and (i == 0 or value[i - 1] in " \t"):
            return value[:i].rstrip()
    return value.rstrip()


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def parse_yaml(text: str) -> dict[str, str]:
    """Aplana un YAML sencillo a claves con puntos.

    Con varios documentos (``---``) se ignoran los que se activan con un perfil
    (``spring.config.activate.on-profile`` o ``spring.profiles``), porque el
    escaneo no sabe qué perfil se usará.
    """
    result: dict[str, str] = {}
    for document in re.split(r"^---\s*$", text, flags=re.MULTILINE):
        flat: dict[str, str] = {}
        stack: list[tuple[int, str]] = []
        for raw in document.splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            stripped = raw.lstrip(" ")
            if stripped.startswith("- ") or stripped == "-":
                continue  # las listas no se necesitan
            indent = len(raw) - len(stripped)
            line = _strip_yaml_comment(stripped)
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = _yaml_scalar(key)
            while stack and stack[-1][0] >= indent:
                stack.pop()
            full = ".".join([k for _, k in stack] + [key])
            if value.strip() and value.strip() not in ("|", ">", "|-", ">-"):
                flat[full] = _yaml_scalar(value)
            else:
                stack.append((indent, key))
        keys = {normalize_key(k) for k in flat}
        if "spring.config.activate.onprofile" in keys or "spring.profiles" in keys:
            continue
        result.update(flat)
    return result


class AppConfig:
    """Propiedades de la aplicación con búsqueda flexible de claves."""

    def __init__(self, sources: Mapping[str, Mapping[str, str]]) -> None:
        # sources: nombre de fichero a propiedades, en orden de precedencia (el primero gana)
        self.files = [name for name in sources]
        self._values: dict[str, str] = {}
        for props in sources.values():
            for key, value in props.items():
                self._values.setdefault(normalize_key(key), value)

    def get(self, key: str) -> str | None:
        return self._values.get(normalize_key(key))

    def resolve(self, text: str) -> tuple[str, list[str]]:
        """Sustituye ``${clave:defecto}`` con las propiedades o el valor por defecto.

        Devuelve el texto y la lista de placeholders que no se han podido resolver
        (se dejan tal cual).
        """
        unresolved: list[str] = []

        def repl(match: re.Match[str]) -> str:
            value = self.get(match.group(1))
            if value is not None:
                return value
            if match.group(2) is not None:
                return match.group(2)
            unresolved.append(match.group(0))
            return match.group(0)

        # Varias pasadas por si una propiedad referencia a otra; tope para evitar bucles.
        for _ in range(5):
            new = _PLACEHOLDER.sub(repl, text) if "${" in text else text
            if new == text:
                break
            unresolved.clear()
            text = new
        return text, sorted(set(unresolved))


def load_app_config(read_resource: Callable[[str], str | None]) -> AppConfig:
    """Carga los ficheros de configuración con ``read_resource(nombre) -> str | None``."""
    sources: dict[str, dict[str, str]] = {}
    for name in CONFIG_FILES:
        text = read_resource(name)
        if text is None:
            continue
        sources[name] = parse_properties(text) if name.endswith(".properties") else parse_yaml(text)
    return AppConfig(sources)
