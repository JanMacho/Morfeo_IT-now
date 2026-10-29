"""Entrenamiento: matriz de transición, tiempos de espera, curva horaria y concurrencia."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
import pytest

from morfeo.common import dumps_json
from morfeo.traffic_model import build_dist, only_matched, sample_from_dist, split_holdout, train
from morfeo.traffic_model.ks import ks_2samp

from types import SimpleNamespace


def test_matrix_recovers_true_chain(requests_df: pd.DataFrame, tm: SimpleNamespace) -> None:
    p = train(requests_df)
    assert p["states"] == ["START", tm.SALDO, tm.INGRESO, tm.RETIRADA, "END"]
    m = p["transition_matrix"]
    assert "END" not in m
    assert "END" not in m["START"]
    assert all("START" not in row for row in m.values())
    for a, row in m.items():
        assert sum(row.values()) == pytest.approx(1.0, abs=1e-9)
        for b, prob in tm.TRUE_MATRIX[a].items():
            assert row.get(b, 0.0) == pytest.approx(prob, abs=0.03), (a, b)
    counts = p["transition_counts"]
    assert sum(counts["START"].values()) == p["input"]["sessions"]


def test_smoothing_fills_allowed_cells(requests_df: pd.DataFrame, tm: SimpleNamespace) -> None:
    m = train(requests_df, smoothing=1.0)["transition_matrix"]
    assert set(m[tm.INGRESO]) == {tm.SALDO, tm.INGRESO, tm.RETIRADA, "END"}
    assert set(m["START"]) == {tm.SALDO, tm.INGRESO, tm.RETIRADA}  # nunca START a END
    assert m[tm.INGRESO][tm.RETIRADA] > 0


def test_think_time_is_the_wait_after_each_state(requests_df: pd.DataFrame, tm: SimpleNamespace) -> None:
    think = train(requests_df)["think_time_ms"]
    for state, median in tm.THINK_MEDIAN.items():
        assert think["by_state"][state]["p50"] == pytest.approx(median, rel=0.1), state
    assert train(requests_df, min_state_samples=10**6)["think_time_ms"]["by_state"] == {}


def test_dist_fields() -> None:
    rng = np.random.default_rng(0)
    values = rng.lognormal(3.0, 0.5, 20000)
    d = build_dist(values)
    assert d["count"] == 20000 and len(d["quantiles"]) == 101
    assert d["quantiles"] == sorted(d["quantiles"])
    assert d["quantiles"][0] == d["min"] and d["quantiles"][-1] == d["max"]
    assert d["lognormal"]["mu"] == pytest.approx(3.0, abs=0.02)
    assert d["lognormal"]["sigma"] == pytest.approx(0.5, abs=0.02)
    empty = build_dist([])
    assert empty["count"] == 0 and empty["quantiles"] == [] and empty["lognormal"] is None
    sample = sample_from_dist(d, 20000, np.random.default_rng(1))
    stat, _ = ks_2samp(values, sample)
    assert stat is not None and stat < 0.02


def test_hourly_curve_and_concurrency_by_hand(tm: SimpleNamespace) -> None:
    # a y b se solapan 5 minutos a las 10 h de Madrid (08 h UTC); c va sola a las 12 h
    t0 = pd.Timestamp("2026-09-21T08:00:00Z")
    minutes = {"a": (0, 10), "b": (5, 15), "c": (120, 125)}
    rows = [
        {"session_id": sid, "client_id": sid, "seq": 0, "ts_start": t0 + pd.Timedelta(minutes=a),
         "ts_end": t0 + pd.Timedelta(minutes=b), "method": "GET", "path": "/x", "raw_path": "/x", "query": "",
         "endpoint": "GET /x", "matched": True, "status": 200, "latency_ms": 1.0, "think_time_ms": np.nan,
         "message": ""}
        for sid, (a, b) in minutes.items()
    ]
    p = train(tm.rows_to_frame(rows), timezone="Europe/Madrid")
    hours = p["hourly_curve"]["hours"]
    assert len(hours) == 24 and p["hourly_curve"]["days_observed"] == 1
    h10, h12 = hours[10], hours[12]
    assert h10["sessions_per_hour"] == 2 and h10["requests_per_hour"] == 2
    assert h10["mean_session_duration_s"] == 600
    cu = h10["concurrent_users"]
    assert cu["little"] == pytest.approx(2 / 3600 * 600)
    # de 08:00 a 08:15 UTC: 5 min con 1 sesión, 5 min con 2 y 5 min con 1; el resto de la hora, 0
    assert cu["observed_max"] == 2
    assert cu["observed_mean"] == pytest.approx((300 * 1 + 300 * 2 + 300 * 1) / 3600, abs=1e-3)
    assert cu["recommended"] == max(1, math.ceil(cu["observed_p95"]))
    assert h12["concurrent_users"]["observed_max"] == 1
    assert hours[3]["concurrent_users"]["recommended"] == 0  # sin tráfico
    assert p["concurrent_users"]["peak_hour"] == 10
    assert sum(h["share_of_daily_requests"] for h in hours) == pytest.approx(1.0)


def test_little_matches_observed_mean(requests_df: pd.DataFrame) -> None:
    cu = train(requests_df)["hourly_curve"]["hours"]
    little = sum(h["concurrent_users"]["little"] for h in cu)
    observed = sum(h["concurrent_users"]["observed_mean"] for h in cu)
    assert observed == pytest.approx(little, rel=0.15)


def test_deterministic_profile(requests_df: pd.DataFrame) -> None:
    assert dumps_json(train(requests_df)) == dumps_json(train(requests_df))


def test_split_holdout_keeps_latest_sessions(requests_df: pd.DataFrame) -> None:
    tr, te = split_holdout(requests_df, 0.25)
    assert set(tr["session_id"]).isdisjoint(te["session_id"])
    n = requests_df["session_id"].nunique()
    assert te["session_id"].nunique() == round(n * 0.25)
    assert tr.groupby("session_id")["ts_start"].min().max() <= te.groupby("session_id")["ts_start"].min().min()
    same_a, same_b = split_holdout(requests_df, 0.0)
    assert same_a is requests_df and same_b is requests_df
    with pytest.raises(ValueError):
        split_holdout(requests_df, 1.0)


def test_only_matched_recomputes_sessions(requests_df: pd.DataFrame) -> None:
    df = requests_df.copy()
    sizes = df.groupby("session_id").size()
    sid = sizes[sizes >= 3].index[0]  # una sesión con al menos tres peticiones
    first = df[df["session_id"] == sid].sort_values("seq")
    df.loc[first.index[1], "matched"] = False  # se quita la del medio
    kept, dropped = only_matched(df)
    assert dropped == 1
    session = kept[kept["session_id"] == sid].sort_values("seq")
    assert session["seq"].tolist() == list(range(len(first) - 1))
    expected = (session["ts_start"].iloc[1] - first["ts_end"].iloc[0]).total_seconds() * 1000
    assert session["think_time_ms"].iloc[1] == pytest.approx(expected)
    # sin ninguna petición emparejada no se quita nada
    none = df.assign(matched=False)
    assert only_matched(none)[1] == 0


def test_errors_on_bad_input(requests_df: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="vacío"):
        train(requests_df.iloc[0:0])
    with pytest.raises(ValueError, match="columnas"):
        train(requests_df.drop(columns=["endpoint"]))


def test_endpoint_stats(requests_df: pd.DataFrame, tm: SimpleNamespace) -> None:
    e: dict[str, Any] = train(requests_df)["endpoints"]
    assert e[tm.RETIRADA]["error_rate"] == pytest.approx(0.1, abs=0.03)
    assert e[tm.SALDO]["error_rate"] == 0
    assert e[tm.SALDO]["server_error_rate"] == 0

    assert sum(v["share"] for v in e.values()) == pytest.approx(1.0, abs=1e-5)
    assert e[tm.SALDO]["latency_ms"]["p50"] == pytest.approx(30, rel=0.1)


def test_503_is_not_in_error_rate(requests_df: pd.DataFrame, tm: SimpleNamespace) -> None:
    df = requests_df.copy()
    idx = df.index[df["endpoint"] == tm.SALDO][:10]
    df.loc[idx, "status"] = 503
    e = train(df)["endpoints"][tm.SALDO]
    assert e["server_error_rate"] > 0 and e["error_rate"] == 0
