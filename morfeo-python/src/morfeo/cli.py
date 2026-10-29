"""Punto de entrada ``morfeo``.

Subcomandos:

- ``scan``: .jar a endpoints.json
- ``analyze``: logs a requests.csv y analysis.json
- ``train``: requests.csv a profile.json (y validación)
- ``validate``: compara un profile.json con requests.csv
- ``synth-logs``: genera logs sintéticos para pruebas y demos
- ``jmeter``: profile.json y endpoints.json a un plan de JMeter (.jmx)
- ``generate``: encadena scan, analyze y train, y escribe el plan de JMeter

Cada subcomando delega en el ``cli.py`` de su paquete (docs/contracts.md,
sección 6), así que ``morfeo scan …`` y ``python -m morfeo.jar_scanner …``
hacen lo mismo. Los módulos se importan solo al usar su subcomando.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Callable, Sequence

from morfeo import __version__
from morfeo.common import DEFAULT_SEED

# nombre, módulo, función que añade argumentos, función que ejecuta, ayuda
_SUBCOMMANDS: tuple[tuple[str, str, str, str, str], ...] = (
    ("scan", "morfeo.jar_scanner.cli", "add_arguments", "run", "descubre endpoints en un .jar"),
    ("analyze", "morfeo.log_analyzer.cli", "add_arguments", "run", "analiza logs de uso"),
    ("train", "morfeo.traffic_model.cli", "add_arguments", "run", "entrena el modelo de tráfico"),
    ("validate", "morfeo.traffic_model.cli", "add_validate_arguments", "run_validate", "valida un profile.json"),
    ("synth-logs", "morfeo.log_analyzer.synthetic", "add_arguments", "run", "genera logs sintéticos"),
    ("jmeter", "morfeo.jmeter_plan.cli", "add_arguments", "run", "genera el plan de JMeter (.jmx)"),
)


def _lazy(module: str, func: str) -> Callable[..., object]:
    def call(*args: object) -> object:
        return getattr(importlib.import_module(module), func)(*args)

    return call


def _unavailable(name: str, module: str) -> Callable[[argparse.Namespace], int]:
    def run(args: argparse.Namespace) -> int:
        print(f"morfeo {name}: el módulo {module} todavía no está implementado", file=sys.stderr)
        return 3

    return run


def _add_generate(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "generate",
        help="encadena scan, analyze, train y jmeter",
        description="Ejecuta scan, analyze, train y jmeter y deja los ficheros intermedios y plan.jmx en --out-dir.",
    )
    p.add_argument("--jar", required=True, type=Path, help="fichero .jar de la aplicación")
    p.add_argument("--logs", required=True, nargs="+", type=Path, help="ficheros o carpetas de logs")
    p.add_argument("--out-dir", type=Path, default=Path("build"), help="carpeta de salida (por defecto build/)")
    p.add_argument("--format", default="auto", help="formato de log: auto, jsonl, pipe, clf, regex")
    p.add_argument("--log-config", type=Path, default=None, help="configuración JSON del parser de logs")
    p.add_argument("--session-gap", type=float, default=1800.0, help="segundos de inactividad que cortan una sesión")
    p.add_argument("--tz", default="UTC", help="zona horaria para la curva por hora (p. ej. Europe/Madrid)")
    p.add_argument("--package", action="append", default=None, help="paquete Java a escanear (repetible)")
    p.add_argument("--holdout", type=float, default=0.0, help="fracción de sesiones reservada para validar")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--no-jmx", action="store_true", help="no escribir plan.jmx (para ajustarlo después con morfeo jmeter)")
    p.set_defaults(_run=_run_generate)


def _run_generate(args: argparse.Namespace) -> int:
    try:
        return _generate(args)
    except (OSError, ValueError) as exc:
        print(f"morfeo generate: {exc}", file=sys.stderr)
        return 1


def _generate(args: argparse.Namespace) -> int:
    if not 0.0 <= args.holdout < 1.0:  # antes de escanear y analizar
        raise ValueError(f"--holdout debe estar en [0, 1), no {args.holdout}")
    from morfeo.common import write_json
    from morfeo.jar_scanner import scan_jar, write_endpoints
    from morfeo.log_analyzer import analyze, load_parser_config, read_logs, write_analysis
    from morfeo.traffic_model import format_report, only_matched, split_holdout, train, validate

    out: Path = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    endpoints = scan_jar(args.jar, packages=args.package)
    write_endpoints(endpoints, out / "endpoints.json")
    print(f"scan: {len(endpoints['endpoints'])} endpoints en {out / 'endpoints.json'}")

    config = load_parser_config(args.format, args.log_config)
    logs = read_logs(args.logs, config)
    result = analyze(logs, config=config, endpoints=endpoints, session_gap_s=args.session_gap, timezone=args.tz)
    paths = write_analysis(result, out)
    totals = result.summary["totals"]
    print(
        f"analyze: {totals['requests']} peticiones, {totals['sessions']} sesiones, "
        f"{totals['unmatched_requests']} sin endpoint, en {paths['analysis']}"
    )

    requests, dropped = only_matched(result.requests)
    train_df, test_df = split_holdout(requests, args.holdout)
    profile = train(train_df, seed=args.seed, timezone=args.tz, holdout=args.holdout, excluded_unmatched=dropped)
    write_json(out / "profile.json", profile)
    print(f"train: {len(profile['states']) - 2} endpoints ({dropped} peticiones sin endpoint excluidas) en {out / 'profile.json'}")

    report = validate(profile, test_df, seed=args.seed)
    write_json(out / "validation.json", report)
    print(format_report(report))

    if not args.no_jmx:
        from morfeo.jmeter_plan.cli import build_plan_file
        from morfeo.jmeter_plan.plan import Options

        try:
            build_plan_file(out / "profile.json", out / "endpoints.json", out / "plan.jmx", Options(seed=args.seed), out / "requests.csv")
        except (OSError, ValueError) as exc:
            print(f"jmeter: no se ha escrito plan.jmx ({exc})", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="morfeo", description="MORFEO, bloque de procesamiento")
    parser.add_argument("--version", action="version", version=f"morfeo {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<subcomando>")
    for name, module, add_fn, run_fn, help_text in _SUBCOMMANDS:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if not module.startswith(exc.name or "\0"):
                raise  # falta una dependencia real, no el módulo
            # módulo todavía no implementado: el resto de subcomandos sigue funcionando
            p = sub.add_parser(name, help=f"{help_text} (no disponible)", add_help=False)
            p.add_argument("rest", nargs=argparse.REMAINDER)
            p.set_defaults(_run=_unavailable(name, module))
            continue
        p = sub.add_parser(name, help=help_text, description=help_text)
        _lazy(module, add_fn)(p)
        p.set_defaults(_run=_lazy(module, run_fn))
    _add_generate(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run = getattr(args, "_run", None)
    if run is None:
        parser.print_help()
        return 2
    return int(run(args))  # type: ignore[call-overload]


if __name__ == "__main__":
    sys.exit(main())
