"""Subcomando ``morfeo analyze``: logs a requests.csv y analysis.json."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from morfeo.common import read_json
from morfeo.log_analyzer.analyzer import AnalysisResult, analyze, write_analysis
from morfeo.log_analyzer.config import AUTO, FORMATS, load_parser_config, override
from morfeo.log_analyzer.parsers import read_logs


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("logs", nargs="+", type=Path, help="ficheros o carpetas de logs (admite .gz)")
    parser.add_argument(
        "--format", default=AUTO, choices=(AUTO, *FORMATS), help="formato del log (por defecto se detecta)"
    )
    parser.add_argument("--config", type=Path, default=None, help="JSON con la configuración del parser")
    parser.add_argument("--endpoints", type=Path, default=None, help="endpoints.json para normalizar rutas")
    parser.add_argument(
        "--session-gap", type=float, default=1800.0, help="segundos de inactividad que cortan una sesión (1800)"
    )
    parser.add_argument("--tz", default="UTC", help="zona horaria de la curva por hora, p. ej. Europe/Madrid")
    parser.add_argument("-o", "--out-dir", type=Path, default=Path("."), help="carpeta de salida (por defecto .)")
    parser.add_argument("--strict", action="store_true", help="error en la primera línea malformada")


def format_summary(result: AnalysisResult, *, with_endpoints: bool) -> str:
    """Resumen corto en texto para la terminal."""
    s = result.summary
    totals, rps = s["totals"], s["rps"]
    lines = [
        f"Peticiones: {totals['requests']} en {totals['sessions']} sesiones de {totals['clients']} clientes",
        f"Periodo: {totals['start']} a {totals['end']} ({totals['duration_s']} s)",
        f"Peticiones por segundo: media {rps['mean']}, media en segundos con tráfico {rps['active_mean']}, "
        f"pico {rps['max']}",
        "Endpoints más frecuentes:",
    ]
    for ep in s["endpoint_mix"][:5]:
        lines.append(f"  {ep['endpoint']}: {ep['count']} ({100 * ep['share']:.1f} %)")
    unmatched = result.requests.loc[~result.requests["matched"], "raw_path"].nunique()
    if with_endpoints:
        lines.append(f"Rutas sin endpoint: {totals['unmatched_requests']} peticiones en {unmatched} rutas distintas")
    else:
        lines.append("Sin endpoints.json: las rutas se normalizan con la heurística")
    lines.extend(f"Aviso: {w}" for w in result.warnings)
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    try:
        config = load_parser_config(args.format, args.config)
        if args.strict:
            config = override(config, skip_invalid=False)
        endpoints = read_json(args.endpoints) if args.endpoints else None
        logs = read_logs(args.logs, config)
        result = analyze(logs, config=config, endpoints=endpoints, session_gap_s=args.session_gap, timezone=args.tz)
        paths = write_analysis(result, args.out_dir)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_summary(result, with_endpoints=endpoints is not None))
    print(f"Escrito: {paths['requests']} y {paths['analysis']}")
    return 0
