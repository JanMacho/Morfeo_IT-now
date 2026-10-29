"""``morfeo scan``: descubre los endpoints de un .jar y escribe endpoints.json."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from morfeo.jar_scanner.scanner import scan_jar, write_endpoints


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("jar", type=Path, help="fichero .jar o .war")
    parser.add_argument("-o", "--out", type=Path, default=Path("endpoints.json"), help="salida (por defecto endpoints.json)")
    parser.add_argument("--package", action="append", default=None, help="paquete Java a escanear (repetible)")
    parser.add_argument("--no-javalin", action="store_true", help="no buscar rutas Javalin")
    parser.add_argument("--no-context-path", action="store_true", help="no anteponer server.servlet.context-path")
    parser.add_argument(
        "--include-lib", action="append", default=None, help="escanear también los jars anidados que casen (glob, repetible)"
    )


def run(args: argparse.Namespace) -> int:
    try:
        doc = scan_jar(
            args.jar,
            packages=args.package,
            detect_javalin=not args.no_javalin,
            apply_context_path=not args.no_context_path,
            include_lib=args.include_lib,
        )
    except (OSError, ValueError) as exc:
        print(f"morfeo scan: {exc}", file=sys.stderr)
        return 1
    write_endpoints(doc, args.out)
    by_framework = Counter(e["framework"] for e in doc["endpoints"])
    detail = ", ".join(f"{n} {fw}" for fw, n in sorted(by_framework.items())) or "ninguno"
    print(f"{len(doc['endpoints'])} endpoints ({detail}) en {args.out}")
    if doc["context_path"]:
        print(f"Context path: {doc['context_path']}")
    for w in doc["warnings"]:
        print(f"Aviso: {w}")
    return 0
