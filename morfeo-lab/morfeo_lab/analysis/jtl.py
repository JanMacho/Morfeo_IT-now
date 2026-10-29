"""Reading the results JMeter wrote (kpi.jtl, one per generator).

Error taxonomy, shared with the Java team:
  ok          the request succeeded
  client_4xx  a 4xx answer: client behaviour (for example 400 for insufficient funds); reported, not a failure
  server_5xx  a 5xx answer: always a failure of the server, even if the plan 'assumed success' (503 is saturation)
  network     no HTTP answer: timeout, connection refused or reset
  assertion   an answer below 400 that a plan assertion rejected
"""
from __future__ import annotations

import csv
import glob
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

FAIL_CLASSES = ("server_5xx", "network", "assertion")
CLASSES = ("ok", "client_4xx", "server_5xx", "network", "assertion")
KPI_COLUMNS = {"timeStamp", "elapsed", "label", "responseCode", "responseMessage", "threadName", "success",
               "allThreads", "bytes", "Connect"}
TRANSACTION = "Number of samples in transaction"   # responseMessage of a Transaction Controller sample
_THREAD = re.compile(r"^(.*) \d+-\d+$")            # JMeter names a thread '<thread group name> <group>-<thread>'
COLUMNS = ["ts", "elapsed", "label", "code", "success", "threads", "bytes", "connect", "message", "group", "stage",
           "generator", "src", "cls"]


def jtl_files(raw_dir: Path) -> list[str]:
    files = sorted(glob.glob(str(Path(raw_dir) / "**" / "kpi*.jtl"), recursive=True))
    return [f for f in files if os.path.getsize(f) > 0]


def generator_of(path: str) -> int:
    for part in Path(path).parts:
        if part.startswith("generator-"):
            try:
                return int(part.split("-", 1)[1])
            except ValueError:
                pass
    return 0


def group_of(thread_name: str) -> str:
    m = _THREAD.match(thread_name)
    return m.group(1) if m else thread_name


def classify(code: pd.Series, success: pd.Series) -> pd.Series:
    num = pd.to_numeric(code, errors="coerce")
    cls = np.where(num.isna(), "network",
          np.where(num >= 500, "server_5xx",
          np.where(success, "ok",
          np.where(num >= 400, "client_4xx", "assertion"))))
    return pd.Series(cls, index=code.index)


def load_jtl(raw_dir: Path, stages: dict[str, str] | None = None) -> pd.DataFrame:
    """Every sample of the run in one table, ordered by time. `stages` maps the name of a thread group to its stage
    (setup, main or teardown); a group that is not in it counts as main.

    `src` identifies the JMeter process (one per generator): the active users of a generator are the sum over its
    processes. Transaction Controller samples are dropped: their requests are already counted one by one. Only the
    needed columns are read, so millions of rows stay manageable."""
    stages = stages or {}
    frames = []
    for i, f in enumerate(jtl_files(raw_dir)):
        df = pd.read_csv(f, dtype=str, keep_default_na=False, on_bad_lines="skip", usecols=lambda c: c in KPI_COLUMNS)
        if df.empty or "timeStamp" not in df.columns:
            continue
        if "responseMessage" in df:
            df = df[~df["responseMessage"].str.startswith(TRANSACTION)].reset_index(drop=True)
        n = len(df)
        thread = df["threadName"] if "threadName" in df else pd.Series([""] * n)
        names = {t: group_of(t) for t in thread.unique()}
        group = thread.map(names)
        frames.append(pd.DataFrame({
            "ts": pd.to_numeric(df["timeStamp"], errors="coerce") / 1000.0,
            "elapsed": pd.to_numeric(df["elapsed"], errors="coerce") if "elapsed" in df else np.nan,
            "label": df.get("label", "?"),
            "code": df.get("responseCode", ""),
            "success": df["success"].str.lower().eq("true") if "success" in df else False,
            "threads": pd.to_numeric(df["allThreads"], errors="coerce") if "allThreads" in df else np.nan,
            "bytes": pd.to_numeric(df["bytes"], errors="coerce") if "bytes" in df else 0,
            "connect": pd.to_numeric(df["Connect"], errors="coerce") if "Connect" in df else np.nan,
            "message": df.get("responseMessage", ""),
            "group": group,
            "stage": group.map(lambda g: stages.get(g, "main")),
            "generator": generator_of(f),
            "src": i,
        }, index=pd.RangeIndex(n)))
    if not frames:
        return pd.DataFrame(columns=[*COLUMNS[:-1], "cls"])
    df = pd.concat(frames, ignore_index=True).dropna(subset=["ts", "elapsed"])
    df = df.sort_values("ts", kind="stable").reset_index(drop=True)
    df["cls"] = classify(df["code"], df["success"])
    for c in ("label", "code", "message", "group", "stage", "cls"):   # few distinct values: much less memory
        df[c] = df[c].astype("category")
    return df


def merge_jtls(raw_dir: Path, out_path: Path) -> int:
    """One CSV for the JMeter HTML dashboard from every generator, streamed (the dashboard does not need the rows
    sorted). The thread name gets the generator so that 'active threads over time' shows each generator."""
    files = jtl_files(raw_dir)
    header, n = None, 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        for f in files:
            gen = generator_of(f)
            with open(f, newline="", encoding="utf-8", errors="replace") as fh:
                r = csv.reader(fh)
                h = next(r, None)
                if not h or "timeStamp" not in h:
                    continue
                if header is None:
                    header = h
                    w.writerow(header)
                idx = {name: i for i, name in enumerate(h)}
                order = [idx.get(c) for c in header]
                name_i = idx.get("threadName")
                for row in r:
                    if len(row) != len(h):
                        continue
                    if len(files) > 1 and name_i is not None:
                        name = row[name_i]
                        cut = name.rfind(" ")
                        row[name_i] = f"{name[:cut]} [generator {gen}]{name[cut:]}" if cut > 0 else f"{name} [generator {gen}]"
                    w.writerow([row[i] if i is not None else "" for i in order])
                    n += 1
    if header is None:
        out_path.unlink(missing_ok=True)
    return n
