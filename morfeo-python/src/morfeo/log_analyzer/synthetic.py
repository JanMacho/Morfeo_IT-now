"""Logs sintéticos para pruebas y demos, con su ground truth.

Hay dos escenarios:

- ``bank``: los cuatro endpoints del backend de ejemplo (saldo, ingreso,
  retirada y crear usuario) con una cadena de Markov fija: casi todas las
  sesiones empiezan consultando el saldo, tras una retirada suele venir otra
  consulta de saldo, y crear usuario solo aparece como primera petición de
  un cliente nuevo. Algunas retiradas dan 400 (fondos insuficientes) y hay
  un 2 % de peticiones a rutas inexistentes (404, cliente ``UNKNOWN``).
- ``endpoints``: a partir de cualquier endpoints.json, con una cadena de
  Markov aleatoria (sembrada) y valores inventados para las variables de ruta.

Los tiempos de espera y las latencias son lognormales y el inicio de las
sesiones sigue una curva diaria con picos por la mañana y por la tarde en hora
local. Las sesiones de un mismo cliente se separan al menos
``SESSION_SEPARATION_S`` para que el corte por inactividad de 1800 s las
recupere tal cual. El formato pipe imita el trafico.log del backend: timestamp
de fin (con 9 decimales en la mitad de las líneas y 6 en la otra) y una 7.ª
columna con el id de sesión de 32 caracteres hexadecimales.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from morfeo.common import DEFAULT_SEED, END_STATE, START_STATE, endpoint_id, make_rng, read_json, write_json
from morfeo.log_analyzer.normalize import ANY_METHOD, _VAR_RE
from morfeo.log_analyzer.sessions import to_ns

SCENARIOS: tuple[str, ...] = ("bank", "endpoints")
LOG_FORMATS: tuple[str, ...] = ("json", "pipe", "jsonl")

SALDO = "GET /api/saldo/{id}"
INGRESO = "POST /api/ingreso"
RETIRADA = "POST /api/retirada"
CREAR = "POST /api/usuario/crear"

BANK_TRANSITIONS: dict[str, dict[str, float]] = {
    START_STATE: {SALDO: 0.80, CREAR: 0.08, INGRESO: 0.07, RETIRADA: 0.05},
    SALDO: {SALDO: 0.10, INGRESO: 0.30, RETIRADA: 0.30, END_STATE: 0.30},
    INGRESO: {SALDO: 0.55, INGRESO: 0.10, RETIRADA: 0.05, END_STATE: 0.30},
    RETIRADA: {SALDO: 0.65, INGRESO: 0.05, RETIRADA: 0.05, END_STATE: 0.25},
    CREAR: {SALDO: 0.60, INGRESO: 0.30, END_STATE: 0.10},
}
# Mediana (ms) y sigma del logaritmo de la latencia.
BANK_LATENCY: dict[str, tuple[float, float]] = {
    SALDO: (40.0, 0.5),
    INGRESO: (90.0, 0.45),
    RETIRADA: (95.0, 0.45),
    CREAR: (120.0, 0.4),
}
BANK_STATUS: dict[str, tuple[int, float, int, str, str]] = {
    # estado: status correcto, probabilidad de error, status de error, mensajes
    SALDO: (200, 0.0, 404, "ok", "Cliente no encontrado"),
    INGRESO: (200, 0.0, 400, "ok", "Petición inválida"),
    RETIRADA: (200, 0.12, 400, "ok", "Fondos insuficientes"),
    CREAR: (201, 0.0, 409, "Usuario creado", "El usuario ya existe"),
}

THINK_MEDIAN_MS = 8000.0
THINK_SIGMA = 0.9
THINK_MAX_MS = 600_000.0
NOISE_SHARE = 0.02
NOISE_LATENCY: tuple[float, float] = (2.0, 0.6)
NOISE_PATHS: tuple[str, ...] = (
    "/",
    "/.env",
    "/api/admin",
    "/api/saldo",
    "/api/saldos/user_001",
    "/api/usuario",
    "/api/v1/saldo/user_002",
    "/favicon.ico",
    "/robots.txt",
    "/wp-login.php",
)
UNKNOWN_CLIENT = "UNKNOWN"
SESSION_SEPARATION_S = 2400
MAX_SESSION_REQUESTS = 40
_PATH_POOL = 30


def daily_curve() -> np.ndarray:
    """Peso de cada hora local en el inicio de sesiones: picos a las 10:30 y a las 18:00."""
    h = np.arange(24) + 0.5
    w = 0.04 + np.exp(-0.5 * ((h - 10.5) / 1.8) ** 2) + 0.8 * np.exp(-0.5 * ((h - 18.0) / 2.0) ** 2)
    return w / w.sum()


@dataclass
class _Model:
    states: list[str]
    methods: list[str]
    templates: list[str]
    # Filas: START y los estados; columnas: los estados y END.
    matrix: np.ndarray
    latency: np.ndarray  # (estados, 2): mediana en ms y sigma
    ok_status: np.ndarray
    error_rate: np.ndarray
    error_status: np.ndarray
    ok_message: list[str]
    error_message: list[str]
    pools: list[list[list[str]]]  # valores de cada variable de ruta, por estado
    new_client_state: int | None = None

    def transition_dict(self) -> dict[str, dict[str, float]]:
        rows = [START_STATE, *self.states]
        cols = [*self.states, END_STATE]
        return {
            rows[i]: {cols[j]: float(p) for j, p in enumerate(self.matrix[i]) if p > 0}
            for i in np.argsort(rows, kind="stable")
        }


def _bank_model() -> _Model:
    states = [SALDO, INGRESO, RETIRADA, CREAR]
    cols = [*states, END_STATE]
    matrix = np.array([[BANK_TRANSITIONS[r].get(c, 0.0) for c in cols] for r in [START_STATE, *states]])
    status = [BANK_STATUS[s] for s in states]
    return _Model(
        states=states,
        methods=[s.split(" ", 1)[0] for s in states],
        templates=[s.split(" ", 1)[1] for s in states],
        matrix=matrix,
        latency=np.array([BANK_LATENCY[s] for s in states]),
        ok_status=np.array([s[0] for s in status]),
        error_rate=np.array([s[1] for s in status]),
        error_status=np.array([s[2] for s in status]),
        ok_message=[s[3] for s in status],
        error_message=[s[4] for s in status],
        pools=[[] for _ in states],
        new_client_state=states.index(CREAR),
    )


def _var_values(token: str, schema: Mapping[str, Any], rng: np.random.Generator) -> list[str]:
    """Valores deterministas para una variable de ruta de una plantilla."""
    if token == "**" or token.startswith("{*"):
        return [f"dir{i}/file{i}.txt" for i in range(_PATH_POOL)]
    if token == "*":
        return [f"item{i}" for i in range(_PATH_POOL)]
    name, _, pattern = token[1:-1].partition(":")
    ints = [str(v) for v in rng.integers(1, 100_000, size=_PATH_POOL)]
    uuids = [str(uuid.UUID(bytes=rng.bytes(16), version=4)) for _ in range(_PATH_POOL)]
    prefix = re.sub(r"[^a-z]", "", name.lower())[:8] or "v"
    tokens = [f"{prefix}{v:05d}" for v in rng.integers(1, 100_000, size=_PATH_POOL)]
    kind = str(schema.get("type", ""))
    if schema.get("format") == "uuid":
        options = [uuids, ints, tokens]
    elif kind in ("integer", "number"):
        options = [ints, tokens, uuids]
    else:
        options = [tokens, ints, uuids]
    if pattern:
        # Con {x:regex} se elige la familia de valores que cumple la expresión.
        for values in options:
            if all(re.fullmatch(pattern, v) for v in values):
                return values
    return options[0]


def _endpoints_model(doc: Mapping[str, Any], rng: np.random.Generator) -> _Model:
    seen: dict[str, Mapping[str, Any]] = {}
    for ep in sorted(doc.get("endpoints") or [], key=lambda e: (str(e.get("path", "")), str(e.get("method", "")))):
        method = str(ep.get("method", "GET")).upper()
        method = "GET" if method == ANY_METHOD else method
        seen.setdefault(endpoint_id(method, str(ep["path"])), ep)
    if not seen:
        raise ValueError("el endpoints.json no tiene endpoints")
    states = sorted(seen)
    n = len(states)
    k = min(n, 3)
    matrix = np.zeros((n + 1, n + 1))
    # START reparte entre unas pocas entradas; cada estado va a unos pocos
    # sucesores o termina, para que la cadena tenga una estructura visible.
    entries = rng.choice(n, size=k, replace=False)
    matrix[0, entries] = rng.dirichlet(np.ones(k))
    for i in range(n):
        p_end = rng.uniform(0.2, 0.4)
        succ = rng.choice(n, size=k, replace=False)
        matrix[i + 1, succ] = rng.dirichlet(np.ones(k)) * (1 - p_end)
        matrix[i + 1, n] = p_end
    pools: list[list[list[str]]] = []
    for sid in states:
        ep = seen[sid]
        params = {p.get("name"): p.get("schema") or {} for p in ep.get("params") or [] if p.get("in") == "path"}
        tokens = _VAR_RE.findall(str(ep["path"]))
        pools.append([_var_values(t, params.get(t[1:-1].split(":", 1)[0], {}), rng) for t in tokens])
    methods = [s.split(" ", 1)[0] for s in states]
    return _Model(
        states=states,
        methods=methods,
        templates=[s.split(" ", 1)[1] for s in states],
        matrix=matrix,
        latency=np.column_stack([rng.uniform(10, 150, size=n), np.full(n, 0.5)]),
        ok_status=np.array([201 if m == "POST" else 200 for m in methods]),
        error_rate=np.full(n, 0.02),
        error_status=np.full(n, 400),
        ok_message=["ok"] * n,
        error_message=["Petición inválida"] * n,
        pools=pools,
    )


def _session_starts(
    rng: np.random.Generator, days: int, per_day: int, curve: np.ndarray, timezone: str, start_date: str
) -> np.ndarray:
    """Inicio de cada sesión en ns UTC, repartido por hora local según ``curve``."""
    try:
        base = pd.Timestamp(start_date)
    except ValueError as exc:
        raise ValueError(f"start_date no válida: {start_date!r} (usa AAAA-MM-DD)") from exc
    counts = rng.multinomial(per_day, curve, size=days).ravel()
    slot = np.repeat(np.arange(days * 24), counts)
    within_us = rng.integers(0, 3600 * 10**6, size=slot.size)
    local_ns = base.value + slot * 3600 * 10**9 + within_us * 1000
    local = pd.Series(local_ns.view("datetime64[ns]")).dt.tz_localize(
        timezone, ambiguous=np.zeros(slot.size, dtype=bool), nonexistent="shift_forward"
    )
    return np.sort(to_ns(local))


def _simulate(rng: np.random.Generator, matrix: np.ndarray, n_sessions: int) -> tuple[np.ndarray, np.ndarray]:
    """Recorre la cadena para todas las sesiones a la vez; devuelve (sesión, estado) en orden."""
    end_col = matrix.shape[1] - 1
    cum = np.cumsum(matrix, axis=1)
    cum[:, -1] = 1.0
    row = np.zeros(n_sessions, dtype=np.int64)
    active = np.arange(n_sessions)
    sessions, states = [], []
    for _ in range(MAX_SESSION_REQUESTS):
        u = rng.random(active.size)
        nxt = (u[:, None] >= cum[row[active]]).sum(axis=1)
        keep = nxt != end_col
        active, nxt = active[keep], nxt[keep]
        if active.size == 0:
            break
        sessions.append(active)
        states.append(nxt)
        row[active] = nxt + 1
    sess = np.concatenate(sessions) if sessions else np.empty(0, np.int64)
    state = np.concatenate(states) if states else np.empty(0, np.int64)
    step = np.concatenate([np.full(a.size, i) for i, a in enumerate(sessions)]) if sessions else sess
    order = np.lexsort((step, sess))
    return sess[order], state[order]


def _lognormal(rng: np.random.Generator, median: np.ndarray | float, sigma: np.ndarray | float, size: int) -> np.ndarray:
    return np.exp(np.log(median) + sigma * rng.standard_normal(size))


def _client_names(n: int) -> list[str]:
    width = max(3, len(str(n)))
    return [f"user_{i:0{width}d}" for i in range(1, n + 1)]


def _session_ids(seed: int, n: int) -> list[str]:
    """Ids de sesión con la forma de la cookie MORFEO_SID del backend: 32 caracteres hexadecimales.

    Salen de un generador propio (misma semilla) para no cambiar el resto del
    log sintético. Cada petición de ruido tiene su propia sesión, como en el
    servidor, que da una cookie nueva a quien no trae ninguna.
    """
    rng = np.random.default_rng([seed, 0x5E55])
    words = rng.integers(0, 2**63, size=(n, 2), dtype=np.int64)
    return [f"{a:016x}{b:016x}" for a, b in words.tolist()]


def _iso(ns: np.ndarray, unit: str) -> np.ndarray:
    return np.char.add(np.datetime_as_string(ns.view("datetime64[ns]"), unit=unit), "Z")


def generate_synthetic_logs(
    *,
    scenario: str = "bank",
    endpoints: dict[str, Any] | None = None,
    days: int = 2,
    sessions_per_day: int = 400,
    clients: int = 150,
    fmt: str = "json",
    timezone: str = "Europe/Madrid",
    start_date: str = "2026-09-21",
    seed: int = DEFAULT_SEED,
) -> tuple[list[str], dict[str, Any]]:
    """Líneas de log ordenadas por timestamp y el ground truth usado para generarlas."""
    if scenario not in SCENARIOS:
        raise ValueError(f"escenario desconocido: {scenario!r} (válidos: {', '.join(SCENARIOS)})")
    if fmt not in LOG_FORMATS:
        raise ValueError(f"formato desconocido: {fmt!r} (válidos: {', '.join(LOG_FORMATS)})")
    if days < 1 or sessions_per_day < 1 or clients < 1:
        raise ValueError("days, sessions_per_day y clients deben ser al menos 1")
    if scenario == "endpoints" and not endpoints:
        raise ValueError("el escenario endpoints necesita un endpoints.json")

    rng = make_rng(seed)
    model = _bank_model() if scenario == "bank" else _endpoints_model(endpoints or {}, rng)
    curve = daily_curve()
    start = _session_starts(rng, days, sessions_per_day, curve, timezone, start_date)
    n_sessions = start.size
    sess, state = _simulate(rng, model.matrix, n_sessions)
    n_req = sess.size
    first = np.ones(n_req, dtype=bool)
    first[1:] = sess[1:] != sess[:-1]
    first_pos = np.flatnonzero(first)
    seq = np.arange(n_req) - np.repeat(first_pos, np.diff(np.append(first_pos, n_req)))

    # Clientes: las sesiones que empiezan por crear usuario son de un cliente nuevo.
    first_state = np.full(n_sessions, -1)
    first_state[sess[first_pos]] = state[first_pos]
    is_new = first_state == (model.new_client_state if model.new_client_state is not None else -2)
    weights = 1.0 / (np.arange(clients) + 10.0)
    session_client = rng.choice(clients, size=n_sessions, p=weights / weights.sum())
    n_new = int(is_new.sum())
    session_client[is_new] = clients + np.arange(n_new)
    names = np.array(_client_names(clients + n_new), dtype=object)

    # Latencias y tiempos de espera; el pipe registra milisegundos enteros.
    lat = _lognormal(rng, model.latency[state, 0], model.latency[state, 1], n_req)
    lat_ms = np.rint(lat) if fmt == "pipe" else np.round(lat, 3)
    think_ms = np.minimum(_lognormal(rng, THINK_MEDIAN_MS, THINK_SIGMA, n_req), THINK_MAX_MS)
    think_ms[first] = 0.0
    lat_ns = np.rint(lat_ms * 1e6).astype(np.int64)
    incr = np.zeros(n_req, dtype=np.int64)
    incr[1:] = lat_ns[:-1] + np.rint(think_ms[1:] * 1e6).astype(np.int64)
    incr[first] = 0
    cum = np.cumsum(incr)
    offset = cum - np.repeat(cum[first_pos], np.diff(np.append(first_pos, n_req)))
    last_pos = np.append(first_pos[1:], n_req) - 1
    duration = np.zeros(n_sessions, dtype=np.int64)
    duration[sess[last_pos]] = offset[last_pos] + lat_ns[last_pos]

    # Separa las sesiones de un mismo cliente para que el corte por
    # inactividad las recupere; si hace falta, retrasa la sesión.
    extra = rng.integers(0, 600 * 10**9, size=n_sessions)
    sep_ns = SESSION_SEPARATION_S * 10**9
    free: dict[int, int] = {}
    shifted = 0
    for i in np.lexsort((np.arange(n_sessions), start, session_client)).tolist():
        c = int(session_client[i])
        if c in free and start[i] < free[c]:
            start[i] = free[c] + int(extra[i])
            shifted += 1
        free[c] = int(start[i] + duration[i]) + sep_ns

    ts_start = start[sess] + offset
    err = rng.random(n_req) < model.error_rate[state]
    status = np.where(err, model.error_status[state], model.ok_status[state])
    message = np.where(
        err, np.array(model.error_message, dtype=object)[state], np.array(model.ok_message, dtype=object)[state]
    )
    client = names[session_client[sess]]
    path = _paths(model, state, client, rng)
    method = np.array(model.methods, dtype=object)[state]

    # Ruido: rutas inexistentes sin cliente, repartidas por todo el periodo.
    n_noise = int(round(NOISE_SHARE * n_req))
    window_start = int(pd.Timestamp(start_date).tz_localize(timezone).value)
    window_ns = days * 86400 * 10**9
    noise_start = window_start + rng.integers(0, window_ns, size=n_noise)
    noise_lat = np.rint(_lognormal(rng, NOISE_LATENCY[0], NOISE_LATENCY[1], n_noise))
    noise_lat = noise_lat if fmt == "pipe" else np.round(noise_lat, 3)
    noise_method = np.where(rng.random(n_noise) < 0.85, "GET", "POST").astype(object)
    noise_path = np.array(NOISE_PATHS, dtype=object)[rng.integers(0, len(NOISE_PATHS), size=n_noise)]

    all_start = np.concatenate([ts_start, noise_start])
    all_lat_ms = np.concatenate([lat_ms, noise_lat])
    all_lat_ns = np.rint(all_lat_ms * 1e6).astype(np.int64)
    rows = {
        "session": np.concatenate([sess, n_sessions + np.arange(n_noise)]),
        "seq": np.concatenate([seq, np.zeros(n_noise, dtype=np.int64)]),
        "method": np.concatenate([method, noise_method]),
        "path": np.concatenate([path, noise_path]),
        "status": np.concatenate([status, np.full(n_noise, 404)]),
        "client": np.concatenate([client, np.full(n_noise, UNKNOWN_CLIENT, dtype=object)]),
        "message": np.concatenate([message, np.full(n_noise, "Not found", dtype=object)]),
    }
    if fmt in ("json", "pipe"):
        # Timestamp de fin, como Instant.now() en el backend. El pipe antiguo
        # mezclaba resolución de 100 ns (9 decimales) y de 6; el JSON actual
        # escribe siempre 6.
        nine = rng.random(all_start.size) < (0.5 if fmt == "pipe" else 0.0)
        end = all_start + all_lat_ns
        end = np.where(nine, (end // 100) * 100, (end // 1000) * 1000)
        ts_value = end
        ts_text = np.where(nine, _iso(end, "ns"), _iso(end, "us"))
    else:
        ts_value = (all_start // 1000) * 1000
        ts_text = _iso(ts_value, "us")
    order = np.lexsort((rows["seq"], rows["session"], ts_value))

    lines: list[str] = []
    if fmt in ("json", "pipe"):
        sid = _session_ids(seed, int(rows["session"].max()) + 1 if rows["session"].size else 0)
    if fmt == "json":
        # Igual que trafico.json del backend: JSON Lines con ts_end y las siete claves.
        for i in order.tolist():
            record = {
                "ts_end": str(ts_text[i]),
                "method": str(rows["method"][i]),
                "path": str(rows["path"][i]),
                "status": int(rows["status"][i]),
                "latency_ms": round(float(all_lat_ms[i]), 3),
                "client_id": str(rows["client"][i]),
                "session_id": sid[int(rows["session"][i])],
            }
            lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    elif fmt == "pipe":
        for i in order.tolist():
            lines.append(
                f"{ts_text[i]} | {rows['method'][i]} | {rows['path'][i]} | {rows['status'][i]} | "
                f"{int(all_lat_ms[i])} | {rows['client'][i]} | {sid[int(rows['session'][i])]}"
            )
    else:
        width = len(str(max(n_sessions, 1)))
        for i in order.tolist():
            s = int(rows["session"][i])
            record = {
                "timestamp": str(ts_text[i]),
                "session_id": f"s{s:0{width}d}" if s < n_sessions else "",
                "client_id": str(rows["client"][i]),
                "method": str(rows["method"][i]),
                "path": str(rows["path"][i]),
                "status": int(rows["status"][i]),
                "latency_ms": float(all_lat_ms[i]),
                "message": str(rows["message"][i]),
            }
            lines.append(json.dumps(record, ensure_ascii=False))

    by_name = sorted(enumerate(model.states), key=lambda kv: kv[1])
    truth = {
        "scenario": scenario,
        "seed": seed,
        "format": fmt,
        "timestamp_is": "end" if fmt in ("json", "pipe") else "start",
        "timezone": timezone,
        "start_date": start_date,
        "days": days,
        "lines": len(lines),
        "sessions": n_sessions,
        "session_requests": n_req,
        "noise_requests": n_noise,
        "clients": {"existing": clients, "new": n_new},
        "states": [START_STATE, *sorted(model.states), END_STATE],
        "transition_matrix": model.transition_dict(),
        "think_time_ms": {
            "distribution": "lognormal",
            "mu": math.log(THINK_MEDIAN_MS),
            "sigma": THINK_SIGMA,
            "max_ms": THINK_MAX_MS,
        },
        "latency_ms": {
            s: {"distribution": "lognormal", "mu": float(np.log(model.latency[i, 0])), "sigma": float(model.latency[i, 1])}
            for i, s in by_name
        },
        "error_rate": {s: float(model.error_rate[i]) for i, s in by_name},
        "hourly_curve": {"timezone": timezone, "weights": [round(float(w), 6) for w in curve]},
        "session_separation_s": SESSION_SEPARATION_S,
        "shifted_sessions": shifted,
        "max_session_requests": MAX_SESSION_REQUESTS,
        "noise": {"share": NOISE_SHARE, "status": 404, "client_id": UNKNOWN_CLIENT, "paths": list(NOISE_PATHS)},
    }
    return lines, truth


def _paths(model: _Model, state: np.ndarray, client: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Ruta concreta de cada petición a partir de la plantilla de su estado."""
    out = np.empty(state.size, dtype=object)
    for s, template in enumerate(model.templates):
        idx = np.flatnonzero(state == s)
        if model.new_client_state is not None:
            # Escenario bank: la única variable es el id del cliente.
            out[idx] = [template.replace("{id}", c) for c in client[idx]]
            continue
        pools = model.pools[s]
        picks = rng.integers(0, _PATH_POOL, size=(idx.size, len(pools)))
        pieces = _VAR_RE.split(template)
        for row, i in enumerate(idx.tolist()):
            values = [pools[v][picks[row, v]] for v in range(len(pools))]
            out[i] = "".join(p + (values[j] if j < len(values) else "") for j, p in enumerate(pieces))
    return out


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scenario", choices=SCENARIOS, default="bank", help="escenario (por defecto bank)")
    parser.add_argument("--endpoints", type=Path, default=None, help="endpoints.json para el escenario endpoints")
    parser.add_argument("--days", type=int, default=2, help="días de tráfico (2)")
    parser.add_argument("--sessions-per-day", type=int, default=400, help="sesiones por día (400)")
    parser.add_argument("--clients", type=int, default=150, help="clientes existentes (150)")
    parser.add_argument("--format", dest="fmt", choices=LOG_FORMATS, default="json", help="formato del log: json (trafico.json del backend), pipe (el antiguo) o jsonl (genérico) (json)")
    parser.add_argument("--tz", default="Europe/Madrid", help="zona horaria de la curva diaria (Europe/Madrid)")
    parser.add_argument("--start-date", default="2026-09-21", help="primer día, AAAA-MM-DD (2026-09-21)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"semilla ({DEFAULT_SEED})")
    parser.add_argument("-o", "--output", type=Path, required=True, help="fichero de log a escribir")
    parser.add_argument("--ground-truth", type=Path, default=None, help="JSON con los parámetros usados")


def run(args: argparse.Namespace) -> int:
    try:
        endpoints = read_json(args.endpoints) if args.endpoints else None
        lines, truth = generate_synthetic_logs(
            scenario=args.scenario,
            endpoints=endpoints,
            days=args.days,
            sessions_per_day=args.sessions_per_day,
            clients=args.clients,
            fmt=args.fmt,
            timezone=args.tz,
            start_date=args.start_date,
            seed=args.seed,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if args.ground_truth:
            write_json(args.ground_truth, truth)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Generadas {truth['lines']} líneas en {args.output}: {truth['sessions']} sesiones, "
        f"{truth['session_requests']} peticiones de sesión y {truth['noise_requests']} de ruido"
    )
    return 0
