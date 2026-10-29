"""``morfeo train`` y ``morfeo validate``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from morfeo.common import DEFAULT_SEED, read_requests_csv, write_json
from morfeo.traffic_model.model import load_profile, only_matched, split_holdout, train, write_profile
from morfeo.traffic_model.validate import format_report, validate

if TYPE_CHECKING:
    import pandas as pd


def _read_requests(path: Path, keep_unmatched: bool) -> tuple[pd.DataFrame, int]:
    """requests.csv sin las rutas que no casan con el jar (salvo ``--keep-unmatched``) y cuántas se han quitado."""
    requests, dropped = read_requests_csv(path), 0
    if not keep_unmatched:
        requests, dropped = only_matched(requests)
        if dropped:
            print(f"Se excluyen {dropped} peticiones a rutas sin endpoint en el jar (usa --keep-unmatched para conservarlas)")
    return requests, dropped


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("requests", type=Path, help="requests.csv de morfeo analyze")
    parser.add_argument("-o", "--out", type=Path, default=Path("profile.json"), help="salida (por defecto profile.json)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"semilla ({DEFAULT_SEED})")
    parser.add_argument("--smoothing", type=float, default=0.0, help="suavizado aditivo de la matriz (0)")
    parser.add_argument("--tz", default="UTC", help="zona horaria de la curva por hora, p. ej. Europe/Madrid")
    parser.add_argument("--min-state-samples", type=int, default=30, help="muestras mínimas para la espera por estado (30)")
    parser.add_argument("--holdout", type=float, default=0.0, help="fracción de sesiones reservada para validar (0)")
    parser.add_argument(
        "--keep-unmatched", action="store_true", help="conservar las rutas que no casan con ningún endpoint del jar"
    )
    parser.add_argument("--no-validate", action="store_true", help="no validar después de entrenar")
    parser.add_argument("--validation-out", type=Path, default=None, help="validation.json (junto a profile.json)")
    parser.add_argument("--synthetic-sessions", type=int, default=None, help="sesiones sintéticas (por defecto, 10.000 o tantas como reales si hay más)")


def run(args: argparse.Namespace) -> int:
    try:
        requests, dropped = _read_requests(args.requests, args.keep_unmatched)
        train_df, test_df = split_holdout(requests, args.holdout)
        profile = train(
            train_df,
            seed=args.seed,
            smoothing=args.smoothing,
            timezone=args.tz,
            min_state_samples=args.min_state_samples,
            holdout=args.holdout,
            excluded_unmatched=dropped,
        )
        write_profile(profile, args.out)
        print(
            f"Modelo con {len(profile['states']) - 2} endpoints, {profile['input']['sessions']} sesiones; "
            f"pico de {profile['concurrent_users']['peak_recommended']} usuarios a las "
            f"{profile['concurrent_users']['peak_hour']:02d} h. Escrito en {args.out}"
        )
        if not args.no_validate:
            report = validate(profile, test_df, n_sessions=args.synthetic_sessions, seed=args.seed)
            write_json(args.validation_out or args.out.with_name("validation.json"), report)
            print(format_report(report))
    except (OSError, ValueError) as exc:
        print(f"morfeo train: {exc}", file=sys.stderr)
        return 1
    return 0


def add_validate_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("profile", type=Path, help="profile.json")
    parser.add_argument("requests", type=Path, help="requests.csv con las sesiones reales")
    parser.add_argument("-o", "--out", type=Path, default=Path("validation.json"), help="salida (validation.json)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"semilla ({DEFAULT_SEED})")
    parser.add_argument("--synthetic-sessions", type=int, default=None, help="sesiones sintéticas (por defecto, 10.000 o tantas como reales si hay más)")
    parser.add_argument(
        "--keep-unmatched", action="store_true", help="conservar las rutas que no casan con ningún endpoint del jar"
    )


def run_validate(args: argparse.Namespace) -> int:
    try:
        # mismo filtro que morfeo train, para comparar el modelo con los mismos datos que vio
        requests, _ = _read_requests(args.requests, args.keep_unmatched)
        report = validate(load_profile(args.profile), requests, n_sessions=args.synthetic_sessions, seed=args.seed)
    except (OSError, ValueError) as exc:
        print(f"morfeo validate: {exc}", file=sys.stderr)
        return 1
    write_json(args.out, report)
    print(format_report(report))
    return 0
