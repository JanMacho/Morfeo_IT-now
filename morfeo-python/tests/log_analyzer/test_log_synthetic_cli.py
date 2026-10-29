"""Generador de logs sintéticos, CLI y rendimiento."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path

import pytest

from morfeo.cli import main
from morfeo.common import read_json, read_requests_csv
from morfeo.log_analyzer import analyze, generate_synthetic_logs, load_parser_config, read_logs

LOGS = Path(__file__).resolve().parents[1] / "fixtures" / "logs"


def test_synthetic_is_deterministic() -> None:
    a, truth_a = generate_synthetic_logs(days=1, sessions_per_day=100)
    b, truth_b = generate_synthetic_logs(days=1, sessions_per_day=100)
    c, _ = generate_synthetic_logs(days=1, sessions_per_day=100, seed=7)
    assert a == b and truth_a == truth_b
    assert a != c
    assert truth_a["lines"] == len(a)


def test_synthetic_matches_its_ground_truth(tmp_path: Path) -> None:
    lines, truth = generate_synthetic_logs(days=3, sessions_per_day=1500, fmt="jsonl")
    log = tmp_path / "s.jsonl"
    log.write_text("\n".join(lines) + "\n")
    cfg = load_parser_config("auto")
    req = analyze(read_logs([log], cfg), config=cfg, endpoints=read_json(LOGS / "endpoints_bank.json")).requests
    sessions = req[req["session_id"].str.startswith("s")]
    assert sessions["session_id"].nunique() == truth["sessions"]
    firsts = sessions.sort_values(["session_id", "seq"]).groupby("session_id")["endpoint"].first()
    start = Counter(firsts)
    total = sum(start.values())
    for endpoint, p in truth["transition_matrix"]["START"].items():
        assert start[endpoint] / total == pytest.approx(p, abs=0.03)


def test_synthetic_json_is_the_backend_format(tmp_path: Path) -> None:
    """Por defecto, el mismo JSON Lines que trafico.json del backend (ts_end, siete claves)."""
    lines, truth = generate_synthetic_logs(days=1, sessions_per_day=200)
    records = [json.loads(line) for line in lines]
    keys = ["ts_end", "method", "path", "status", "latency_ms", "client_id", "session_id"]
    assert all(list(r) == keys for r in records)
    assert all(len(r["session_id"]) == 32 and int(r["session_id"], 16) >= 0 for r in records)
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", r["ts_end"]) for r in records)
    assert truth["format"] == "json" and truth["timestamp_is"] == "end"
    assert any(r["client_id"] == "UNKNOWN" for r in records)  # el ruido (bots) no tiene cliente
    log = tmp_path / "trafico.json"
    log.write_text("\n".join(lines) + "\n")
    cfg = load_parser_config("auto")
    result = analyze(read_logs([log], cfg), config=cfg)
    assert result.summary["input"]["format"] == "jsonl" and result.summary["input"]["timestamp_is"] == "end"
    assert result.summary["input"]["session_source"] == "session_id"
    assert result.summary["totals"]["sessions"] == truth["sessions"] + truth["noise_requests"]


def test_synthetic_pipe_has_session_column(tmp_path: Path) -> None:
    lines, truth = generate_synthetic_logs(days=1, sessions_per_day=200, fmt="pipe")
    fields = [line.split(" | ") for line in lines]
    assert all(len(f) == 7 for f in fields)
    assert all(len(f[6]) == 32 and int(f[6], 16) >= 0 for f in fields)  # como la cookie MORFEO_SID
    log = tmp_path / "s.log"
    log.write_text("\n".join(lines) + "\n")
    cfg = load_parser_config("auto")
    result = analyze(read_logs([log], cfg), config=cfg)
    assert result.summary["input"]["session_source"] == "session_id"
    # una sesión por sesión simulada y otra por cada petición de ruido (cookie nueva, como el servidor)
    assert result.summary["totals"]["sessions"] == truth["sessions"] + truth["noise_requests"]


def test_cli_synth_and_analyze(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "logs" / "trafico.log"
    truth = tmp_path / "truth.json"
    assert main(["synth-logs", "--days", "1", "--sessions-per-day", "50", "-o", str(log), "--ground-truth", str(truth)]) == 0
    assert read_json(truth)["scenario"] == "bank"
    out = tmp_path / "out"
    code = main(["analyze", str(log.parent), "--endpoints", str(LOGS / "endpoints_bank.json"),
                 "--tz", "Europe/Madrid", "-o", str(out)])
    assert code == 0
    printed = capsys.readouterr().out
    assert "Peticiones:" in printed
    assert len(read_requests_csv(out / "requests.csv")) == read_json(truth)["lines"]
    assert read_json(out / "analysis.json")["input"]["timezone"] == "Europe/Madrid"


def test_cli_analyze_reports_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "raro.log"
    bad.write_text("esto no es ningún formato conocido\n")
    assert main(["analyze", str(bad), "-o", str(tmp_path)]) != 0
    assert capsys.readouterr().err


def test_performance_200k_lines(tmp_path: Path) -> None:
    lines, _ = generate_synthetic_logs(days=20, sessions_per_day=2800)
    assert len(lines) > 190_000
    log = tmp_path / "big.log"
    log.write_text("\n".join(lines) + "\n")
    t0 = time.perf_counter()
    cfg = load_parser_config("auto")
    analyze(read_logs([log], cfg), config=cfg)
    assert time.perf_counter() - t0 < 20
