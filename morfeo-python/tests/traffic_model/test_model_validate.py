"""Validación con sesiones sintéticas, test KS, CLI y rendimiento."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from morfeo.cli import main
from morfeo.common import read_json, write_requests_csv
from morfeo.traffic_model import format_report, ks_2samp, sample_sessions, split_holdout, train, validate

from types import SimpleNamespace


def test_ks_matches_scipy() -> None:
    stats = pytest.importorskip("scipy.stats")
    rng = np.random.default_rng(3)
    cases = [
        (rng.normal(0, 1, 500), rng.normal(0.1, 1, 700)),
        (rng.integers(1, 6, 400).astype(float), rng.integers(1, 7, 900).astype(float)),  # empates
        (rng.exponential(1, 50), rng.exponential(1.5, 5000)),
        (np.array([1.0]), np.array([1.0, 2.0, 3.0])),
    ]
    for a, b in cases:
        d, p = ks_2samp(a, b)
        ref = stats.ks_2samp(a, b, method="asymp")
        assert d == pytest.approx(ref.statistic, abs=1e-12)
        if min(len(a), len(b)) >= 50:
            assert p == pytest.approx(ref.pvalue, abs=0.03)
    assert ks_2samp([], [1.0]) == (None, None)


def test_sample_sessions_follow_matrix(tm: SimpleNamespace) -> None:
    seqs = sample_sessions(tm.TRUE_MATRIX, 20000, np.random.default_rng(0))
    firsts = pd.Series([s[0] for s in seqs]).value_counts(normalize=True)
    assert firsts["GET /api/saldo/{id}"] == pytest.approx(0.9, abs=0.01)
    assert all(seqs)


def test_in_sample_validation_passes(requests_df: pd.DataFrame) -> None:
    report = validate(train(requests_df), requests_df)
    assert report["reference"] == "in-sample"
    assert report["n_synthetic_sessions"] == max(10_000, report["n_real_sessions"])
    assert all(report["verdict"].values()), report
    # la cadena de Markov capta el orden; el modelo sin memoria no
    mk, bl = report["transition_diff"]["markov"], report["transition_diff"]["baseline"]
    assert mk["weighted_tv"] < 0.05 < bl["weighted_tv"]
    text = format_report(report)
    assert "correcto" in text and "Markov" in text


def test_holdout_validation_and_seed(requests_df: pd.DataFrame) -> None:
    tr, te = split_holdout(requests_df, 0.3)
    profile = train(tr, holdout=0.3)
    a = validate(profile, te, seed=5)
    assert a["reference"] == "holdout" and a["n_real_sessions"] == te["session_id"].nunique()
    assert validate(profile, te, seed=5) == a
    assert validate(profile, te, seed=6) != a


def test_validation_with_unseen_endpoint(requests_df: pd.DataFrame) -> None:
    profile = train(requests_df)
    extra = requests_df.copy()
    extra.loc[extra.index[:50], "endpoint"] = "GET /nuevo"
    report = validate(profile, extra)
    assert report["transition_diff"]["markov"]["max_abs"] > 0


def test_single_request_sessions(tm: SimpleNamespace) -> None:
    df = tm.simulate(200, matrix={"START": {"GET /x": 1.0}, "GET /x": {"END": 1.0}})
    report = validate(train(df), df)
    assert report["think_time_ks"]["statistic"] is None  # sin esperas
    assert "sin datos" in format_report(report)


def test_cli_train_and_validate(tmp_path: Path, requests_df: pd.DataFrame, capsys: pytest.CaptureFixture[str]) -> None:
    csv = write_requests_csv(requests_df, tmp_path / "requests.csv")
    assert main(["train", str(csv), "-o", str(tmp_path / "profile.json"), "--holdout", "0.2", "--tz", "Europe/Madrid"]) == 0
    out = capsys.readouterr().out
    assert "Validación del modelo" in out
    profile = read_json(tmp_path / "profile.json")
    assert profile["input"]["holdout"] == 0.2 and profile["hourly_curve"]["timezone"] == "Europe/Madrid"
    assert read_json(tmp_path / "validation.json")["reference"] == "holdout"
    code = main(["validate", str(tmp_path / "profile.json"), str(csv), "-o", str(tmp_path / "v2.json")])
    assert code == 0 and read_json(tmp_path / "v2.json")["reference"] == "holdout"
    assert main(["train", str(tmp_path / "no-existe.csv")]) == 1


def test_performance_100k_requests(tm: SimpleNamespace) -> None:
    df = tm.simulate(30000, seed=2, days=7)
    assert len(df) > 90_000
    t0 = time.perf_counter()
    profile = train(df)
    validate(profile, df)
    assert time.perf_counter() - t0 < 15


def test_long_sessions_are_sampled_correctly() -> None:
    # cadena con sesiones largas (media 200 peticiones); antes guardaba una tabla sesiones x pasos
    m = {"START": {"A": 1.0}, "A": {"A": 0.995, "END": 0.005}}
    seqs = sample_sessions(m, 3000, np.random.default_rng(0), max_length=5000)
    lengths = np.array([len(s) for s in seqs])
    assert lengths.min() >= 1 and lengths.mean() == pytest.approx(200, rel=0.1)
    assert sample_sessions(m, 3000, np.random.default_rng(0), max_length=5000) == seqs


def test_invalid_timezone_is_a_clear_error(requests_df: pd.DataFrame, tmp_path: Path,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(ValueError, match="zona horaria desconocida"):
        train(requests_df, timezone="Marte/Base")
    csv = write_requests_csv(requests_df, tmp_path / "requests.csv")
    assert main(["train", str(csv), "-o", str(tmp_path / "p.json"), "--tz", "Marte/Base"]) == 1
    assert "zona horaria desconocida" in capsys.readouterr().err


def test_cli_validate_excludes_unmatched_like_train(requests_df: pd.DataFrame, tmp_path: Path,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    noisy = requests_df.copy()
    noisy.loc[noisy.index[:40], ["endpoint", "path", "matched"]] = ["GET /wp-login.php", "/wp-login.php", False]
    csv = write_requests_csv(noisy, tmp_path / "requests.csv")
    assert main(["train", str(csv), "-o", str(tmp_path / "profile.json"), "--no-validate"]) == 0
    capsys.readouterr()
    assert main(["validate", str(tmp_path / "profile.json"), str(csv), "-o", str(tmp_path / "v.json")]) == 0
    assert "Se excluyen 40 peticiones" in capsys.readouterr().out
    filtered = read_json(tmp_path / "v.json")["transition_diff"]["markov"]["max_abs"]
    assert main(["validate", str(tmp_path / "profile.json"), str(csv), "-o", str(tmp_path / "k.json"),
                 "--keep-unmatched"]) == 0
    kept = read_json(tmp_path / "k.json")["transition_diff"]["markov"]["max_abs"]
    assert filtered < 0.2 < kept  # sin filtro aparecen estados que el modelo no conoce


def test_small_real_sample_verdict_is_stable(tm: SimpleNamespace) -> None:
    # Con pocas sesiones reales, el veredicto no debe depender de la semilla del muestreo sintético
    df = tm.simulate(150, seed=9)
    profile = train(df)
    tvs = [validate(profile, df, seed=s)["transition_diff"]["markov"]["weighted_tv"] for s in (1, 2, 3)]
    assert max(tvs) - min(tvs) < 0.01 and max(tvs) < 0.02
