"""Errores de morfeo generate: mensaje de una línea, sin traceback."""

from __future__ import annotations

from pathlib import Path

import pytest

from morfeo.cli import main


def test_generate_reports_bad_jar(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    jar = tmp_path / "roto.jar"
    jar.write_text("no es un zip")
    log = tmp_path / "t.log"
    log.write_text("2026-09-28T10:00:00Z | GET | /a | 200 | 5 | u1\n")
    assert main(["generate", "--jar", str(jar), "--logs", str(log), "--out-dir", str(tmp_path / "out")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("morfeo generate:") and "Traceback" not in err


def test_generate_reports_missing_logs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    jar = Path(__file__).resolve().parents[2] / "backend-java" / "target" / "bank-api-stress.jar"
    if not jar.exists():
        pytest.skip("no está el jar del backend")
    assert main(["generate", "--jar", str(jar), "--logs", str(tmp_path / "no-existe"), "--out-dir", str(tmp_path)]) == 1
    assert capsys.readouterr().err.startswith("morfeo generate:")


def test_generate_checks_holdout_before_working(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    assert main(["generate", "--jar", "x.jar", "--logs", "x.log", "--out-dir", str(out), "--holdout", "1"]) == 1
    assert "--holdout" in capsys.readouterr().err
    assert not (out / "endpoints.json").exists()
