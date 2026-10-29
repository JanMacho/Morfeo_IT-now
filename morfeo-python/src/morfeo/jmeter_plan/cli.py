"""``morfeo jmeter``: del perfil y los endpoints a un plan de JMeter (.jmx)."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from morfeo.jmeter_plan.plan import (
    DEFAULT_LATENCY_MARGIN_STEPS_S,
    DEFAULT_POOL_PREFIX,
    DEFAULT_SEED,
    DEFAULT_SPEND_FRACTION,
    MAX_POOL_IDS,
    Options,
    convert,
    format_hours,
    format_table,
    load_user_pool,
    parse_hours,
    parse_number_spec,
    parse_steps,
)


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("profile", type=Path, help="profile.json de morfeo train")
    p.add_argument("endpoints", type=Path, help="endpoints.json de morfeo scan")
    p.add_argument("-o", "--out", type=Path, default=Path("plan.jmx"), help="plan de salida (plan.jmx)")
    p.add_argument(
        "--requests",
        type=Path,
        default=None,
        help="requests.csv de morfeo analyze: los usuarios del plan son los de los logs, con su frecuencia (sin él, un pool inventado)",
    )
    p.add_argument("--mode", choices=("curve", "steps"), default="curve", help="curva por hora o escalera de carga (curve)")
    p.add_argument("--slot-seconds", type=int, default=60, help="segundos de prueba por cada hora de la curva (60)")
    p.add_argument("--factor", type=float, default=1.0, help="multiplica la intensidad, sesiones por hora (1.0)")
    p.add_argument("--hours", default=None, help="horas de la curva, p. ej. 8-20 o 8,9,10 (todas)")
    p.add_argument("--base-rate", type=float, default=None, help="sesiones por hora del escalón x1 (la hora pico)")
    p.add_argument("--steps", default="1,2,5,10", help="multiplicadores de la escalera (1,2,5,10)")
    p.add_argument("--step-seconds", type=int, default=300, help="segundos por escalón (300)")
    p.add_argument("--tail-seconds", type=int, default=None, help="margen tras la ventana para acabar sesiones (automático)")
    p.add_argument("--max-threads", type=int, default=500, help="tope de hilos por grupo (500)")
    p.add_argument(
        "--thread-factor", type=float, default=2.0, help="hilos por cada sesión simultánea de la ley de Little, más uno (2.0)"
    )
    p.add_argument(
        "--latency-margin",
        type=float,
        default=None,
        metavar="SEGUNDOS",
        help=f"segundos que se suman por petición a la sesión para calcular los hilos ({DEFAULT_LATENCY_MARGIN_STEPS_S:g} en steps, 0 en curve)",
    )
    p.add_argument(
        "--pool-size", type=int, default=200, help=f"usuarios del pool que se crean en el setUp: los más activos de --requests o inventados (200, máximo {MAX_POOL_IDS})"
    )
    p.add_argument(
        "--pool-only",
        action="store_true",
        help="escribe un plan que solo da de alta el pool, para ejecutarlo antes de guardar trafico.json",
    )
    p.add_argument("--create-endpoint", default=None, help="endpoint de alta, p. ej. 'POST /api/usuario/crear', o none (automático)")
    p.add_argument("--id-field", default="id", help="nombre del campo y de la variable de ruta del usuario (id)")
    p.add_argument(
        "--number",
        action="append",
        default=[],
        metavar="CAMPO=DIST",
        help="distribución de un campo numérico: campo=lognormal:MEDIANA:SIGMA o campo=uniform:MIN:MAX; se puede repetir. Sin ella, la del esquema o la de un saldo (mediana 300) o un movimiento (80)",
    )
    p.add_argument("--max-steps", type=int, default=None, help="peticiones máximas por sesión (3 veces la sesión más larga, mínimo 50)")
    p.add_argument(
        "--skip-unmatched",
        action="store_true",
        help="no reproducir las rutas de los logs que no están en el jar (por defecto se reproducen, p. ej. los bots)",
    )
    p.add_argument(
        "--no-balance",
        action="store_true",
        help="importes de retirada sorteados sin mirar el saldo visto (por defecto son una parte del saldo)",
    )
    p.add_argument(
        "--spend-known",
        type=float,
        default=None,
        metavar="P",
        help="probabilidad de que una retirada quepa en el saldo visto (1 - error_rate del perfil)",
    )
    p.add_argument(
        "--spend-fraction",
        default=f"{DEFAULT_SPEND_FRACTION[0]}:{DEFAULT_SPEND_FRACTION[1]}",
        metavar="MIN:MAX",
        help="parte del saldo visto que se retira (0.05:0.55)",
    )
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"semilla de los generadores de cada hilo ({DEFAULT_SEED})")


def options_from_args(args: argparse.Namespace) -> Options:
    for name in ("slot_seconds", "step_seconds", "max_threads", "pool_size"):
        if getattr(args, name) < 1:
            raise ValueError(f"--{name.replace('_', '-')} tiene que ser al menos 1")
    checks = (
        (args.tail_seconds is not None and args.tail_seconds < 0, "--tail-seconds no puede ser negativo"),
        (args.max_steps is not None and args.max_steps < 1, "--max-steps tiene que ser al menos 1"),
        (not (math.isfinite(args.factor) and args.factor > 0), "--factor tiene que ser positivo"),
        (not (math.isfinite(args.thread_factor) and args.thread_factor >= 1), "--thread-factor tiene que ser al menos 1"),
        (args.latency_margin is not None and not (math.isfinite(args.latency_margin) and args.latency_margin >= 0),
         "--latency-margin no puede ser negativo"),
        (args.base_rate is not None and not (math.isfinite(args.base_rate) and args.base_rate > 0), "--base-rate tiene que ser positivo"),
        (not args.id_field.strip(), "--id-field no puede estar vacío"),
        (args.spend_known is not None and not (0.0 <= args.spend_known <= 1.0), "--spend-known tiene que estar entre 0 y 1"),
    )
    for failed, message in checks:
        if failed:
            raise ValueError(message)
    try:
        lo_text, hi_text = args.spend_fraction.split(":")
        spend_fraction = (float(lo_text), float(hi_text))
    except ValueError:
        raise ValueError(f"--spend-fraction '{args.spend_fraction}': se espera MIN:MAX, por ejemplo 0.05:0.55") from None
    if not (0.0 < spend_fraction[0] <= spend_fraction[1] <= 1.0):
        raise ValueError("--spend-fraction tiene que cumplir 0 < MIN <= MAX <= 1")
    numbers = dict(parse_number_spec(t) for t in args.number)
    return Options(
        mode=args.mode,
        slot_seconds=args.slot_seconds,
        factor=args.factor,
        hours=parse_hours(args.hours) if args.hours else None,
        base_rate=args.base_rate,
        steps=parse_steps(args.steps),
        step_seconds=args.step_seconds,
        tail_seconds=args.tail_seconds,
        max_threads=args.max_threads,
        thread_factor=args.thread_factor,
        latency_margin=args.latency_margin,
        pool_size=min(args.pool_size, MAX_POOL_IDS) if args.requests else args.pool_size,
        pool_only=args.pool_only,
        create_endpoint=args.create_endpoint,
        id_field=args.id_field.strip(),
        numbers=numbers,
        max_steps=args.max_steps,
        seed=args.seed,
        replay_unmatched=not args.skip_unmatched,
        balance_aware=not args.no_balance,
        spend_known=args.spend_known,
        spend_fraction=spend_fraction,
    )


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: se esperaba un objeto JSON")
    return doc


def build_plan_file(
    profile_path: Path, endpoints_path: Path, out: Path, opts: Options, requests_path: Path | None = None
) -> int:
    """Convierte y escribe el plan; imprime el resumen. 0 si va bien."""
    profile = load_json(profile_path)
    endpoints_doc = load_json(endpoints_path)
    extra: list[str] = []
    users = load_user_pool(requests_path, opts.pool_size, extra) if requests_path else None
    if requests_path and users is None:
        extra.append(f"{requests_path}: no hay clientes identificados; se usa un pool inventado")
    plan = convert(profile, endpoints_doc, opts, users)
    out.write_bytes(plan.xml)
    for w in [*extra, *plan.warnings]:
        print(f"Aviso: {w}", file=sys.stderr)
    print(f"Endpoint de alta: {plan.create_note}")
    pool = plan.model["pool"]
    if users:
        ids = f"{users.ids[0]} a {users.ids[-1]}, de {requests_path}"
        what = f"{pool} usuarios de los logs, elegidos con su frecuencia"
    else:
        width = plan.model["pool_ancho"]
        ids = f"{DEFAULT_POOL_PREFIX}_p{1:0{width}d} a {DEFAULT_POOL_PREFIX}_p{pool:0{width}d}"
        what = f"{pool} usuarios inventados"
    if opts.pool_only:
        print(f"Plan escrito en {out}: solo da de alta {what} ({ids}).")
        print("Ejecútalo antes de empezar a guardar trafico.json, y luego el plan de tráfico con -Jcreate_pool=false.")
        return 0
    print(f"Estados con petición ({len(plan.states)}): " + ", ".join(plan.states))
    if plan.skipped_hours:
        print(f"Horas sin sesiones en el perfil, no ocupan tiempo en la prueba: {format_hours(plan.skipped_hours)}")
    print(format_table(plan.groups))
    total = max(g.delay_s + g.duration_s for g in plan.groups)
    print(f"Plan escrito en {out}. Duración máxima de la prueba: {total} s, más el alta del pool.")
    if plan.pool_filter:
        print(
            f"Alta del pool: {what} ({ids}) en el setUp del plan. Esas altas quedan en "
            "trafico.json como sesiones de altas seguidas. Antes de pasar el log a MORFEO, quítalas con:"
        )
        print(f"  {plan.pool_filter}")
        print("O da de alta el pool aparte con --pool-only y ejecuta este plan con -Jcreate_pool=false.")
    return 0


def run(args: argparse.Namespace) -> int:
    try:
        opts = options_from_args(args)
        return build_plan_file(args.profile, args.endpoints, args.out, opts, args.requests)
    except (OSError, ValueError) as exc:
        print(f"morfeo jmeter: {exc}", file=sys.stderr)
        return 1
