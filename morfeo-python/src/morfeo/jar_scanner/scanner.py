"""Escaneo de un .jar o .war completo: devuelve el documento de endpoints.json."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from morfeo.common import SCHEMA_VERSION, read_json, write_json
from morfeo.jar_scanner.appconfig import AppConfig, load_app_config
from morfeo.jar_scanner.classfile import ClassFile, ClassFormatError, parse_class
from morfeo.jar_scanner.dto import SchemaBuilder
from morfeo.jar_scanner.jarfile import JarArchive, class_version_range
from morfeo.jar_scanner.javalin import find_javalin_endpoints
from morfeo.jar_scanner.model import Endpoint
from morfeo.jar_scanner.paths import join_paths, normalize_path
from morfeo.jar_scanner.spring import find_spring_endpoints

STRING_SCHEMA = {"type": "string", "java_type": "java.lang.String"}


def _context_path(config: AppConfig, warnings: list[str]) -> str:
    """``server.servlet.context-path`` más ``spring.mvc.servlet.path``, o vacío."""
    parts = []
    for key in ("server.servlet.context-path", "spring.mvc.servlet.path"):
        value = config.get(key)
        if not value:
            continue
        value, unresolved = config.resolve(value.strip())
        for placeholder in unresolved:
            warnings.append(f"placeholder sin resolver en {key}: {placeholder}")
        parts.append(value)
    joined = join_paths(*parts) if parts else ""
    return "" if joined == "/" else joined


def _dedupe(endpoints: list[Endpoint], warnings: list[str]) -> list[Endpoint]:
    kept: dict[str, Endpoint] = {}
    for e in sorted(endpoints, key=lambda x: (x.source_class, x.source_method, x.source_descriptor)):
        first = kept.get(e.id)
        if first is None:
            kept[e.id] = e
            continue
        first.add_note(f"también lo declara {e.source_class}.{e.source_method}; se conserva el primero")
        warnings.append(f"endpoint duplicado {e.id}: {first.source_class}.{first.source_method} y {e.source_class}.{e.source_method}")
    return sorted(kept.values(), key=lambda x: (x.path, x.method))


def scan_jar(
    jar_path: str | Path,
    *,
    packages: Sequence[str] | None = None,
    detect_javalin: bool = True,
    apply_context_path: bool = True,
    include_lib: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Descubre los endpoints HTTP de un .jar o .war (docs/contracts.md, sección 1)."""
    warnings: list[str] = []
    with JarArchive(jar_path) as jar:
        app, jar_warnings = jar.app_classes(packages, include_lib)
        warnings.extend(jar_warnings)
        classes: list[ClassFile] = []
        for c in app:
            try:
                cf = parse_class(c.data)
            except ClassFormatError as exc:
                warnings.append(f"clase ilegible {c.internal_name}: {exc}")
                continue
            jar.remember(cf)
            classes.append(cf)
        if not classes:
            warnings.append("no hay clases de la aplicación que escanear")

        schemas = SchemaBuilder(jar.load_class)
        config = load_app_config(jar.resource_text)
        endpoints = find_spring_endpoints(classes, jar.load_class, schemas, config, warnings)
        if detect_javalin:
            endpoints += find_javalin_endpoints(classes, jar.load_class, schemas, warnings)

        context_path = _context_path(config, warnings) if apply_context_path else ""
        for e in endpoints:
            e.path = join_paths(context_path, e.path) if context_path else normalize_path(e.path)
            e.fill_path_params(STRING_SCHEMA)
            e.fill_consumes()
        endpoints = _dedupe(endpoints, warnings)

        doc: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "generator": "morfeo.jar_scanner",
            "jar": {
                "file": jar.file_name,
                "sha256": jar.sha256,
                "layout": jar.layout,
                "main_class": jar.main_class,
                "start_class": jar.start_class,
                "class_versions": class_version_range(app),
            },
            "context_path": context_path,
            "frameworks": sorted({e.framework for e in endpoints}),
            "endpoints": [e.to_dict() for e in endpoints],
            "warnings": sorted(set(warnings)),
        }
    return doc


def write_endpoints(doc: dict[str, Any], path: str | Path) -> Path:
    return write_json(path, doc)


def load_endpoints(path: str | Path) -> dict[str, Any]:
    doc = read_json(path)
    if not isinstance(doc, dict) or "endpoints" not in doc:
        raise ValueError(f"{path} no es un endpoints.json válido")
    return doc
