import math

import numpy as np
import pandas as pd

from morfeo.common import (
    REQUESTS_COLUMNS,
    dumps_json,
    endpoint_id,
    read_requests_csv,
    write_requests_csv,
)


def _sample() -> pd.DataFrame:
    ts = pd.to_datetime(["2026-09-28T15:10:25.242960900Z", "2026-09-28T15:10:38.792938Z"], utc=True)
    return pd.DataFrame(
        {
            "session_id": ["user_001#0", "user_001#0"],
            "client_id": ["user_001", "user_001"],
            "seq": [0, 1],
            "ts_start": ts - pd.to_timedelta([53, 111], unit="ms"),
            "ts_end": ts,
            "method": ["GET", "POST"],
            "path": ["/api/saldo/{id}", "/api/ingreso"],
            "raw_path": ["/api/saldo/user_001", "/api/ingreso"],
            "query": ["", ""],
            "endpoint": ["GET /api/saldo/{id}", "POST /api/ingreso"],
            "matched": [True, True],
            "status": [200, 200],
            "latency_ms": [53.0, 111.0],
            "think_time_ms": [np.nan, 13439.0],
            "message": ["", "ok, con coma"],
        }
    )


def test_requests_csv_roundtrip(tmp_path):
    p = write_requests_csv(_sample(), tmp_path / "requests.csv")
    df = read_requests_csv(p)
    assert tuple(df.columns) == REQUESTS_COLUMNS
    assert str(df["ts_start"].dtype) == "datetime64[ns, UTC]"
    assert df["matched"].tolist() == [True, True]
    assert math.isnan(df["think_time_ms"].iloc[0])
    assert df["think_time_ms"].iloc[1] == 13439.0
    assert df["message"].iloc[1] == "ok, con coma"
    # escribir dos veces da el mismo fichero
    again = write_requests_csv(df, tmp_path / "again.csv")
    assert again.read_bytes() == p.read_bytes()


def test_dumps_json_is_strict():
    text = dumps_json({"a": np.float64("nan"), "b": np.int64(3), "c": [np.float32(1.5)]})
    assert '"a": null' in text and '"b": 3' in text and text.endswith("\n")


def test_endpoint_id():
    assert endpoint_id("get", "/x/{id}") == "GET /x/{id}"
