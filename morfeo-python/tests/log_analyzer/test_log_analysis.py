"""Normalización, sesiones, tiempos de espera y estadísticas de analysis.json."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from morfeo.common import REQUESTS_COLUMNS, read_json, read_requests_csv
from morfeo.log_analyzer import AnalysisResult, analyze, heuristic_template, load_parser_config, read_logs, write_analysis

LOGS = Path(__file__).resolve().parents[1] / "fixtures" / "logs"
ENDPOINTS = read_json(LOGS / "endpoints_bank.json")


def run(tmp_path: Path, lines: list[str], *, endpoints: dict[str, Any] | None = None, gap: float = 1800,
        tz: str = "UTC") -> AnalysisResult:
    log = tmp_path / "t.log"
    log.write_text("\n".join(lines) + "\n")
    cfg = load_parser_config("auto")
    return analyze(read_logs([log], cfg), config=cfg, endpoints=endpoints, session_gap_s=gap, timezone=tz)


def pipe(ts: str, method: str, path: str, status: int, ms: int, client: str) -> str:
    return f"{ts} | {method} | {path} | {status} | {ms} | {client}"


def test_heuristic_templates() -> None:
    assert heuristic_template("/api/saldo/user_001") == "/api/saldo/{id}"
    assert heuristic_template("/cuenta/123/movimientos/456") == "/cuenta/{id}/movimientos/{id2}"
    assert heuristic_template("/doc/0f8fad5b-d9cb-469f-a165-70867728950e") == "/doc/{id}"
    assert heuristic_template("/api/ingreso") == "/api/ingreso"


def test_matching_against_endpoints(tmp_path: Path) -> None:
    r = run(tmp_path, [
        pipe("2026-09-28T10:00:00Z", "GET", "/api/saldo/user_001", 200, 5, "u1"),
        pipe("2026-09-28T10:00:01Z", "GET", "/api/saldo/total", 200, 5, "u1"),
        pipe("2026-09-28T10:00:02Z", "post", "/api/ingreso/", 200, 5, "u1"),
        pipe("2026-09-28T10:00:03Z", "DELETE", "/api/ping", 200, 5, "u1"),
        pipe("2026-09-28T10:00:04Z", "GET", "/no/existe/42", 404, 5, "u1"),
    ], endpoints=ENDPOINTS)
    req = r.requests.sort_values("seq")
    assert req["endpoint"].tolist() == [
        "GET /api/saldo/{id}",
        "GET /api/saldo/total",  # gana la plantilla más específica
        "POST /api/ingreso",  # método en mayúsculas y barra final limpia
        "DELETE /api/ping",  # ANY casa con cualquier método
        "GET /no/existe/{id}",
    ]
    assert req["matched"].tolist() == [True, True, True, True, False]
    assert r.summary["unmatched_paths"] == [{"method": "GET", "path": "/no/existe/42", "count": 1}]


def test_sessions_by_client_and_gap(tmp_path: Path) -> None:
    r = run(tmp_path, [
        pipe("2026-09-28T10:00:00.100Z", "GET", "/a", 200, 100, "u1"),
        pipe("2026-09-28T10:00:00.150Z", "GET", "/b", 200, 100, "u1"),  # empieza antes de que acabe la anterior
        pipe("2026-09-28T10:30:00.250Z", "GET", "/c", 200, 100, "u1"),  # hueco de 1800 s exactos: misma sesión
        pipe("2026-09-28T11:30:00.000Z", "GET", "/d", 200, 100, "u1"),  # hueco mayor: sesión nueva
        pipe("2026-09-28T10:00:00.000Z", "GET", "/a", 200, 100, "UNKNOWN"),
    ])
    req = r.requests
    u1 = req[req["client_id"] == "u1"].sort_values("ts_start")
    assert u1["session_id"].tolist() == ["u1#0", "u1#0", "u1#0", "u1#1"]
    assert u1["seq"].tolist() == [0, 1, 2, 0]
    think = u1["think_time_ms"].tolist()
    assert think[1] == 0.0 and think[2] == pytest.approx(1_800_000)
    assert r.summary["think_time_ms"]["negative_clipped"] == 1
    assert set(req.loc[req["client_id"] == "", "session_id"]) == {"anonymous#0"}
    assert any("anonymous" in w for w in r.warnings)


def test_503_counts_as_server_error_not_client_error(tmp_path: Path) -> None:
    r = run(tmp_path, [
        pipe("2026-09-28T10:00:00.000Z", "GET", "/api/saldo/user_001", 200, 100, "c1"),
        pipe("2026-09-28T10:00:01.000Z", "GET", "/api/saldo/user_001", 503, 5000, "c1"),
        pipe("2026-09-28T10:00:02.000Z", "GET", "/api/saldo/user_002", 404, 50, "c2"),
    ], endpoints=ENDPOINTS)
    t = r.summary["totals"]
    assert (t["error_requests"], t["server_error_requests"]) == (1, 1)
    mix = {m["endpoint"]: m for m in r.summary["endpoint_mix"]}
    saldo = mix["GET /api/saldo/{id}"]
    assert saldo["error_rate"] == pytest.approx(1 / 3) and saldo["server_error_rate"] == pytest.approx(1 / 3)


def test_hand_computed_summary(tmp_path: Path) -> None:
    r = run(tmp_path, [
        pipe("2026-09-28T10:00:00.100Z", "GET", "/api/saldo/user_001", 200, 100, "c1"),
        pipe("2026-09-28T10:00:05.200Z", "POST", "/api/ingreso", 200, 200, "c1"),
        pipe("2026-09-28T10:00:01.050Z", "GET", "/api/saldo/user_002", 404, 50, "c2"),
    ], endpoints=ENDPOINTS)
    s = r.summary
    t = s["totals"]
    assert (t["requests"], t["sessions"], t["clients"], t["error_requests"]) == (3, 2, 2, 1)
    assert t["start"] == "2026-09-28T10:00:00.000000Z" and t["end"] == "2026-09-28T10:00:05.200000Z"
    assert t["duration_s"] == pytest.approx(5.2)
    assert s["rps"]["max"] == 1 and s["rps"]["active_mean"] == 1
    assert s["rps"]["mean"] == pytest.approx(0.5)  # 3 peticiones en 6 segundos (10:00:00 a 10:00:05)
    assert s["think_time_ms"]["count"] == 1 and s["think_time_ms"]["mean"] == pytest.approx(4900)
    assert s["session_length"]["requests"]["mean"] == pytest.approx(1.5)
    assert s["session_length"]["duration_s"]["max"] == pytest.approx(5.2)
    mix = {m["endpoint"]: m for m in s["endpoint_mix"]}
    assert mix["GET /api/saldo/{id}"]["count"] == 2 and mix["GET /api/saldo/{id}"]["error_rate"] == pytest.approx(0.5)
    assert s["endpoint_mix"][0]["endpoint"] == "GET /api/saldo/{id}"  # ordenado por count
    assert s["sequences"]["top_bigrams"] == [{"from": "GET /api/saldo/{id}", "to": "POST /api/ingreso", "count": 1}]
    hours = s["requests_per_hour"]["by_hour_of_day"]
    assert len(hours) == 24 and hours[10]["requests_total"] == 3


def test_hour_of_day_across_dst_change(tmp_path: Path) -> None:
    # 25 de octubre de 2026 en Madrid: las 03:00 CEST pasan a ser las 02:00 CET
    lines = [pipe(f"2026-10-24T{h:02d}:15:00Z", "GET", "/x", 200, 5, f"a{h}") for h in range(24)]
    lines += [pipe(f"2026-10-25T{h:02d}:15:00Z", "GET", "/x", 200, 5, f"b{h}") for h in range(24)]
    hours = run(tmp_path, lines, tz="Europe/Madrid").summary["requests_per_hour"]["by_hour_of_day"]
    assert sum(h["requests_total"] for h in hours) == 48
    assert hours[2]["requests_total"] == 3  # la hora 02 se repite el día del cambio


def test_outputs_follow_contract_and_are_deterministic(tmp_path: Path) -> None:
    cfg = load_parser_config("auto")
    df = read_logs([LOGS / "trafico_sample.log"], cfg)
    a = write_analysis(analyze(df, config=cfg, endpoints=ENDPOINTS), tmp_path / "a")
    b = write_analysis(analyze(df, config=cfg, endpoints=ENDPOINTS), tmp_path / "b")
    for key in ("requests", "analysis"):
        assert a[key].read_bytes() == b[key].read_bytes()
    back = read_requests_csv(a["requests"])
    assert tuple(back.columns) == REQUESTS_COLUMNS
    summary = read_json(a["analysis"])
    assert set(summary) >= {"schema_version", "generator", "input", "totals", "rps", "requests_per_hour",
                            "endpoint_mix", "session_length", "think_time_ms", "sequences", "unmatched_paths"}
    assert summary["input"]["files"] == ["trafico_sample.log"]
    assert str(LOGS) not in a["analysis"].read_text()


def test_real_backend_log_think_time() -> None:
    # Las 3 primeras líneas de la fixture son las del trafico.log real del backend Javalin
    cfg = load_parser_config("auto")
    req = analyze(read_logs([LOGS / "trafico_sample.log"], cfg), config=cfg, endpoints=ENDPOINTS).requests
    real = req[req["client_id"] == "user_001"].sort_values("seq")
    assert real["matched"].all() and len(real) == 3
    # inicio del ingreso (38.792938 - 0.111) menos fin de la consulta de saldo (25.242960900)
    assert real["think_time_ms"].iloc[1] == pytest.approx(13438.9771, abs=1e-3)
