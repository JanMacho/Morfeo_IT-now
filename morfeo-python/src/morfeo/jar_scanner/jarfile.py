"""Acceso al .jar o .war: layout, manifiesto, clases de la aplicación y búsqueda de clases.

Layouts:

- ``spring-boot``: existe ``BOOT-INF/classes/``; librerías en ``BOOT-INF/lib/``.
- ``war``: existe ``WEB-INF/classes/``; librerías en ``WEB-INF/lib/``.
- ``plain``: las clases están en la raíz (jar normal o sombreado).

Las clases que no son de la aplicación (resto del jar sombreado, jars anidados)
se abren solo bajo demanda, para resolver superclases, interfaces y DTOs.
"""

from __future__ import annotations

import fnmatch
import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from morfeo.jar_scanner.classfile import ClassFile, ClassFormatError, parse_class, read_major_version

LAYOUT_SPRING_BOOT = "spring-boot"
LAYOUT_WAR = "war"
LAYOUT_PLAIN = "plain"

_CLASSES_PREFIX = {LAYOUT_SPRING_BOOT: "BOOT-INF/classes/", LAYOUT_WAR: "WEB-INF/classes/", LAYOUT_PLAIN: ""}
_LIB_PREFIX = {LAYOUT_SPRING_BOOT: "BOOT-INF/lib/", LAYOUT_WAR: "WEB-INF/lib/"}
_SKIPPED_CLASSES = ("module-info", "package-info")


@dataclass(frozen=True)
class AppClass:
    """Clase de la aplicación: nombre interno, bytes y de dónde viene (vacío si del jar principal)."""

    internal_name: str
    data: bytes
    origin: str = ""


def parse_manifest(text: str) -> dict[str, str]:
    """Lee un MANIFEST.MF (líneas de continuación que empiezan por un espacio)."""
    result: dict[str, str] = {}
    key: str | None = None
    for raw in text.splitlines():
        if raw.startswith(" ") and key is not None:
            result[key] += raw[1:]
            continue
        key = None
        if ":" in raw:
            k, v = raw.split(":", 1)
            key = k.strip()
            result[key] = v.strip()
    return result


def _class_entries(names: Iterable[str], prefix: str) -> dict[str, str]:
    """Nombre interno de clase a nombre de entrada, bajo ``prefix``."""
    out: dict[str, str] = {}
    for entry in names:
        if not entry.endswith(".class") or not entry.startswith(prefix):
            continue
        internal = entry[len(prefix) : -len(".class")]
        if internal.startswith("META-INF/") or internal.rsplit("/", 1)[-1] in _SKIPPED_CLASSES:
            continue
        out[internal] = entry
    return out


def package_prefixes(packages: Sequence[str]) -> tuple[str, ...]:
    """``com.example`` a ``com/example/``; el paquete vacío representa el paquete por defecto."""
    return tuple(p.strip().strip(".").replace(".", "/") + "/" for p in packages if p.strip().strip("."))


def in_packages(internal_name: str, prefixes: Sequence[str]) -> bool:
    return any(internal_name.startswith(p) for p in prefixes)


def root_package(class_name: str) -> str:
    """Dos primeros segmentos del paquete de una clase (``com.example.bankapi.Main`` da ``com.example``)."""
    parts = class_name.split(".")[:-1]
    return ".".join(parts[:2])


class JarArchive:
    """Jar o war abierto. Usar como gestor de contexto."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        data = self.path.read_bytes()
        self.sha256 = hashlib.sha256(data).hexdigest()
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ValueError(f"{self.path.name} no es un fichero zip válido (.jar o .war)") from exc
        names = self._zip.namelist()
        self._names = set(names)
        if any(n.startswith("BOOT-INF/classes/") for n in names):
            self.layout = LAYOUT_SPRING_BOOT
        elif any(n.startswith("WEB-INF/") for n in names):
            self.layout = LAYOUT_WAR
        elif any(n.startswith("BOOT-INF/") for n in names):
            self.layout = LAYOUT_SPRING_BOOT
        else:
            self.layout = LAYOUT_PLAIN
        self.classes_prefix = _CLASSES_PREFIX[self.layout]
        self.lib_prefix = _LIB_PREFIX.get(self.layout)
        self._classes = _class_entries(names, self.classes_prefix)
        self.nested_jars = sorted(
            n for n in names if self.lib_prefix and n.startswith(self.lib_prefix) and n.endswith(".jar")
        )
        manifest = self.read_text("META-INF/MANIFEST.MF")
        self.manifest = parse_manifest(manifest) if manifest else {}
        self.main_class = self.manifest.get("Main-Class") or None
        self.start_class = self.manifest.get("Start-Class") or None
        self._nested_zips: dict[str, zipfile.ZipFile] = {}
        self._lib_index: dict[str, str] | None = None
        self._cache: dict[str, ClassFile | None] = {}
        self._extra: dict[str, bytes] = {}

    def __enter__(self) -> JarArchive:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        for z in self._nested_zips.values():
            z.close()
        self._zip.close()

    @property
    def file_name(self) -> str:
        return self.path.name

    # -- recursos -------------------------------------------------------------

    def read_text(self, entry: str) -> str | None:
        if entry not in self._names:
            return None
        return self._zip.read(entry).decode("utf-8", errors="replace")

    def resource_text(self, name: str) -> str | None:
        """Recurso del classpath de la aplicación (``application.properties``…)."""
        return self.read_text(self.classes_prefix + name)

    # -- clases de la aplicación -------------------------------------------

    def default_packages(self) -> list[str] | None:
        """Paquetes por defecto en layout plain: raíz de Start-Class o Main-Class."""
        if self.layout != LAYOUT_PLAIN:
            return None
        for cls in (self.start_class, self.main_class):
            if cls:
                return [root_package(cls)]
        return None

    def app_classes(
        self, packages: Sequence[str] | None = None, include_lib: Sequence[str] | None = None
    ) -> tuple[list[AppClass], list[str]]:
        """Clases a escanear y avisos. Ordenadas por nombre para que el resultado sea estable."""
        warnings: list[str] = []
        pkgs = list(packages) if packages else self.default_packages()
        prefixes = package_prefixes(pkgs) if pkgs else ()
        default_pkg_only = bool(pkgs) and not prefixes  # Main-Class en el paquete por defecto
        if self.layout == LAYOUT_PLAIN and not packages and pkgs is None:
            warnings.append("jar plain sin Main-Class ni Start-Class: se escanean todas las clases (usa --package)")

        def selected(internal: str) -> bool:
            if default_pkg_only:
                return "/" not in internal
            return not prefixes or in_packages(internal, prefixes)

        result = [
            AppClass(name, self._zip.read(entry))
            for name, entry in sorted(self._classes.items())
            if selected(name)
        ]
        for jar in self._matching_libs(include_lib or ()):
            try:
                nested = self._nested(jar)
            except zipfile.BadZipFile:
                warnings.append(f"jar anidado ilegible, se ignora: {jar}")
                continue
            lib_name = jar.rsplit("/", 1)[-1]
            for name, entry in sorted(_class_entries(nested.namelist(), "").items()):
                if selected(name) and name not in self._classes:
                    data = nested.read(entry)
                    self._extra.setdefault(name, data)
                    result.append(AppClass(name, data, lib_name))
        if include_lib and not self._matching_libs(include_lib):
            warnings.append(f"--include-lib no coincide con ningún jar anidado: {', '.join(sorted(include_lib))}")
        result.sort(key=lambda c: c.internal_name)
        return result, warnings

    def _matching_libs(self, patterns: Sequence[str]) -> list[str]:
        out = []
        for jar in self.nested_jars:
            base = jar.rsplit("/", 1)[-1]
            if any(fnmatch.fnmatch(base, p) or fnmatch.fnmatch(jar, p) for p in patterns):
                out.append(jar)
        return out

    def _nested(self, entry: str) -> zipfile.ZipFile:
        z = self._nested_zips.get(entry)
        if z is None:
            z = zipfile.ZipFile(io.BytesIO(self._zip.read(entry)))
            self._nested_zips[entry] = z
        return z

    # -- búsqueda de clases bajo demanda -----------------------------------

    def class_bytes(self, internal_name: str) -> bytes | None:
        """Bytes de cualquier clase del jar: aplicación, resto del jar o jars anidados."""
        if internal_name in self._extra:
            return self._extra[internal_name]
        entry = self._classes.get(internal_name)
        if entry is not None:
            return self._zip.read(entry)
        if self.layout != LAYOUT_PLAIN:
            root_entry = f"{internal_name}.class"
            if root_entry in self._names:
                return self._zip.read(root_entry)
        jar = self._libs().get(internal_name)
        if jar is not None:
            return self._nested(jar).read(f"{internal_name}.class")
        return None

    def _libs(self) -> dict[str, str]:
        """Índice perezoso de clases de los jars anidados (se construye en la primera búsqueda fallida)."""
        if self._lib_index is None:
            index: dict[str, str] = {}
            for jar in self.nested_jars:
                was_open = jar in self._nested_zips
                try:
                    names = self._nested(jar).namelist()
                except zipfile.BadZipFile:
                    continue
                for name in _class_entries(names, ""):
                    index.setdefault(name, jar)
                if not was_open:  # solo se conserva abierto lo que se use después
                    self._nested_zips.pop(jar).close()
            self._lib_index = index
        return self._lib_index

    def load_class(self, internal_name: str) -> ClassFile | None:
        """Clase parseada con caché, o None si no está en el jar o no se puede leer."""
        if internal_name in self._cache:
            return self._cache[internal_name]
        result: ClassFile | None = None
        if not internal_name.startswith(("java/", "jdk/", "[")):
            data = self.class_bytes(internal_name)
            if data is not None:
                try:
                    result = parse_class(data)
                except ClassFormatError:
                    result = None
        self._cache[internal_name] = result
        return result

    def remember(self, cls: ClassFile) -> None:
        """Guarda en la caché una clase ya parseada por el escáner."""
        self._cache.setdefault(cls.this_class, cls)


def class_version_range(classes: Iterable[AppClass]) -> dict[str, int | None]:
    versions = [v for v in (read_major_version(c.data) for c in classes) if v is not None]
    return {"min": min(versions) if versions else None, "max": max(versions) if versions else None}
