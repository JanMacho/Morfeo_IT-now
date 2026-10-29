"""Lectura de logs: formatos, detección automática y configuración a medida."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd
import pytest

from morfeo.log_analyzer import LogParseError, ParserConfig, analyze, detect_format, load_parser_config, read_logs

LOGS = Path(__file__).resolve().parents[1] / "fixtures" / "logs"


def load(name: str, fmt: str = "auto", config: Path | None = None) -> tuple[pd.DataFrame, ParserConfig]:
    cfg = load_parser_config(fmt, config)
    return read_logs([LOGS / name], cfg), cfg


def test_detect_format() -> None:
    assert detect_format('{"timestamp": "x"}') == "jsonl"
    assert detect_format((LOGS / "trafico_sample.log").read_text().splitlines()[0]) == "pipe"
    assert detect_format((LOGS / "access.log").read_text().splitlines()[0]) == "clf"
    with pytest.raises(ValueError):
        detect_format("formato inventado sin separadores")


def test_pipe_real_format_mixed_precision() -> None:
    df, cfg = load("trafico_sample.log")
    assert len(df) == 6  # la línea malformada y la vacía se descartan
    assert str(df["timestamp"].dtype) == "datetime64[ns, UTC]"
    # 9 decimales en la primera línea, 6 en la segunda
    assert df["timestamp"].iloc[0] == pd.Timestamp("2026-09-28T15:10:25.242960900Z")
    assert df["timestamp"].iloc[1] == pd.Timestamp("2026-09-28T15:10:38.792938Z")
    result = analyze(df, config=cfg)
    assert any("malformadas" in w for w in result.warnings)
    assert result.summary["input"]["format"] == "pipe"
    assert result.summary["input"]["timestamp_is"] == "end"


def test_pipe_strict_mode_reports_line() -> None:
    cfg = load_parser_config("pipe")
    cfg.skip_invalid = False
    with pytest.raises(LogParseError, match="trafico_sample.log"):
        read_logs([LOGS / "trafico_sample.log"], cfg)


def test_jsonl_default_fields() -> None:
    df, cfg = load("sample.jsonl")
    result = analyze(df, config=cfg)
    assert result.summary["input"]["format"] == "jsonl"  # "auto" se resuelve al leer
    req = result.requests
    assert sorted(req["session_id"].unique()) == ["s1", "s2"]
    assert result.summary["input"]["session_source"] == "session_id"
    assert "POST" in set(req["method"])  # "post" en minúsculas
    s1 = req[req["session_id"] == "s1"]
    # jsonl: el timestamp es el inicio; espera = inicio siguiente - fin anterior
    assert s1["think_time_ms"].iloc[1] == pytest.approx(3000 - 20)


def test_clf_with_response_time() -> None:
    df, cfg = load("access.log")
    result = analyze(df, config=cfg)
    req = result.requests.sort_values("ts_start").reset_index(drop=True)
    assert req["ts_start"].iloc[0] == pd.Timestamp("2026-09-28T10:00:00Z")  # +0200 a UTC
    assert req["latency_ms"].tolist()[:2] == [15.0, 100.0]
    assert req["query"].iloc[0] == "x=1" and req["raw_path"].iloc[0] == "/api/saldo/user_001"
    assert set(req["client_id"]) == {"10.0.0.1", "ana"}  # usuario autenticado antes que la IP


def test_custom_config_nested_fields_epoch_seconds() -> None:
    df, cfg = load("nested.log", config=LOGS / "nested_config.json")
    result = analyze(df, config=cfg)
    req = result.requests
    assert req["ts_end"].iloc[0] == pd.Timestamp(1790589600000, unit="ms", tz="UTC")
    assert req["latency_ms"].tolist() == [20.0, 5.0]  # latency_unit s
    assert req["ts_start"].iloc[0] == req["ts_end"].iloc[0] - pd.Timedelta(milliseconds=20)
    assert req["status"].tolist() == [200, 400]


def test_regex_format(tmp_path: Path) -> None:
    log = tmp_path / "app.txt"
    log.write_text("2026-09-28 10:00:00,GET,/a/1,200,12\n2026-09-28 10:00:02,GET,/a/2,500,30\n")
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(json.dumps({
        "format": "regex",
        "pattern": r"^(?P<timestamp>[^,]+),(?P<method>\w+),(?P<path>[^,]+),(?P<status>\d+),(?P<latency_ms>\d+)$",
        "timestamp_format": "%Y-%m-%d %H:%M:%S",
        "input_timezone": "Europe/Madrid",
    }))
    cfg = load_parser_config(None, cfg_file)
    df = read_logs([log], cfg)
    assert df["timestamp"].iloc[0] == pd.Timestamp("2026-09-28T08:00:00Z")
    assert len(df) == 2


def test_invalid_config_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "pipe", "timestamp_is": "mitad"}))
    with pytest.raises(ValueError, match="timestamp_is"):
        load_parser_config(None, bad)


def test_directory_and_gzip(tmp_path: Path) -> None:
    text = (LOGS / "trafico_sample.log").read_text()
    (tmp_path / "a.log").write_text(text)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.log.gz").write_bytes(gzip.compress(text.encode()))
    (tmp_path / "ignorar.bin").write_bytes(b"\x00\x01")
    df = read_logs([tmp_path], load_parser_config("auto"))
    assert len(df) == 12
    assert sorted(df["source_file"].unique()) == ["a.log", "b.log.gz"]


def test_windows_encoding_and_line_endings(tmp_path: Path) -> None:
    log = tmp_path / "win.log"
    text = "2026-09-28T10:00:00Z | GET | /api/año | 200 | 1 | josé\r\n2026-09-28T10:00:01Z | GET | /b | 200 | 1 | josé\r\n"
    log.write_bytes(text.encode("cp1252"))
    df = read_logs([log], load_parser_config("auto"))
    assert df["client_id"].tolist() == ["josé", "josé"]
    assert df["path_raw"].iloc[0] == "/api/año"


def test_detection_skips_an_atypical_first_line(tmp_path: Path) -> None:
    log = tmp_path / "nginx.log"
    log.write_text(
        '1.1.1.1 - - [28/Sep/2026:12:00:00 +0200] "-" 400 0 "-" "-"\n'
        '1.1.1.1 - - [28/Sep/2026:12:00:01 +0200] "GET /a HTTP/1.1" 200 5 "-" "-"\n'
    )
    cfg = load_parser_config("auto")
    result = analyze(read_logs([log], cfg), config=cfg)
    assert result.summary["input"]["format"] == "clf"
    assert result.summary["totals"]["requests"] == 1
    assert any("malformadas" in w for w in result.warnings)


def test_pipe_path_with_spaces(tmp_path: Path) -> None:
    log = tmp_path / "s.log"
    log.write_text("2026-09-28T10:00:00Z | GET | /a/b c | 200 | 1 | u\n")
    assert read_logs([log], load_parser_config("auto"))["path_raw"].tolist() == ["/a/b c"]


def test_detection_reads_only_the_head(tmp_path: Path) -> None:
    from morfeo.log_analyzer import parsers

    line = "2026-09-28T10:00:00Z | GET | /api/saldo/user_001 | 200 | 1 | josé\n"
    n = parsers.HEAD_BYTES // len(line.encode()) + 50  # el bloque leído corta una línea por la mitad
    log = tmp_path / "grande.log"
    log.write_text(line * n, encoding="utf-8")
    head = parsers._head_lines(log)
    assert 0 < len(head) < n and head[-1] == line.rstrip("\n")  # sin la línea cortada
    df = read_logs([log], load_parser_config("auto"))
    assert len(df) == n and (df["client_id"] == "josé").all()


def test_drive_root_is_rejected() -> None:
    # un "\" suelto en PowerShell llega como la raíz del disco; no se debe recorrer entero
    root = Path(Path.cwd().anchor)
    with pytest.raises(ValueError, match="raíz del disco"):
        read_logs([root], load_parser_config("auto"))


BACKEND_PARSER = Path(__file__).resolve().parents[3] / "backend-java" / "morfeo-parser.json"


def test_pipe_with_session_column_needs_no_config() -> None:
    df, cfg = load("trafico_7col.log")
    result = analyze(df, config=cfg)
    assert result.summary["input"]["format"] == "pipe"
    assert result.summary["input"]["session_source"] == "session_id"
    req = result.requests
    assert sorted(req["session_id"].unique()) == [
        "0f1e2d3c4b5a69788796a5b4c3d2e1f0", "4ce89f0ec4024fdb9d390429d50db6a6", "9a0b1c2d3e4f5061728394a5b6c7d8e9"]
    s = req[req["session_id"] == "4ce89f0ec4024fdb9d390429d50db6a6"].sort_values("seq")
    assert s["seq"].tolist() == [0, 1, 2]
    assert s["latency_ms"].tolist() == pytest.approx([1.25, 0.734, 0.91])  # 3 decimales
    # inicio de la retirada (34.548091 - 0.000734) menos fin de la consulta (30.120000)
    assert s["think_time_ms"].iloc[1] == pytest.approx(4427.357, abs=1e-3)


def test_pipe_six_columns_still_groups_by_client() -> None:
    df, cfg = load("trafico_sample.log")
    result = analyze(df, config=cfg)
    assert result.summary["input"]["session_source"] == "client_gap"
    assert (df["session_id"] == "").all()
    assert len(df) == 6


def test_mixed_six_and_seven_columns(tmp_path: Path) -> None:
    (tmp_path / "antiguo.log").write_text((LOGS / "trafico_sample.log").read_text())
    (tmp_path / "nuevo.log").write_text((LOGS / "trafico_7col.log").read_text())
    cfg = load_parser_config("auto")
    result = analyze(read_logs([tmp_path], cfg), config=cfg)
    assert result.summary["totals"]["requests"] == 11  # 6 + 5, ninguna línea perdida
    ids = set(result.requests["session_id"])
    assert "4ce89f0ec4024fdb9d390429d50db6a6" in ids and "user_001#0" in ids


def test_explicit_pipe_columns_are_strict(tmp_path: Path) -> None:
    six = tmp_path / "seis.json"
    six.write_text(json.dumps({"format": "pipe", "pipe_columns": [
        "timestamp", "method", "path", "status", "latency_ms", "client_id"], "timestamp_is": "end"}))
    with pytest.raises(LogParseError, match="se esperaban 6 campos"):
        read_logs([LOGS / "trafico_7col.log"], load_parser_config(None, six))


@pytest.mark.skipif(not BACKEND_PARSER.exists(), reason="no está backend-java/morfeo-parser.json")
def test_backend_parser_config_still_works() -> None:
    df, cfg = load("trafico_7col.log", config=BACKEND_PARSER)
    assert analyze(df, config=cfg).summary["input"]["session_source"] == "session_id"


def test_backend_json_lines_need_no_config() -> None:
    """trafico.json del backend: JSON Lines con ts_end (fin de la petición)."""
    df, cfg = load("trafico_backend.json")
    result = analyze(df, config=cfg)
    assert result.summary["input"]["format"] == "jsonl"
    assert result.summary["input"]["timestamp_is"] == "end"
    assert result.summary["input"]["session_source"] == "session_id"
    req = result.requests
    s = req[req["session_id"] == "4ce89f0ec4024fdb9d390429d50db6a6"].sort_values("seq")
    assert s["latency_ms"].tolist() == pytest.approx([1.25, 0.734])
    # mismo resultado que el pipe equivalente: inicio de la retirada menos fin de la consulta
    assert s["think_time_ms"].iloc[1] == pytest.approx(4427.357, abs=1e-3)
    odd = req[req["session_id"] == "0f1e2d3c4b5a69788796a5b4c3d2e1f0"]
    assert odd["raw_path"].iloc[0] == 'a"b|c'.join(["/api/saldo/", ""])
    assert odd["client_id"].iloc[0] == ""  # UNKNOWN significa sin cliente
