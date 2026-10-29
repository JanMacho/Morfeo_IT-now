"""Parsing and formatting of durations, Kubernetes quantities and numbers, plus small helpers shared by every
module: environment expansion, slugs and content hashes."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

# ------------------------------------------------------------------ durations
_DUR = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?")
_FACTOR = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(v) -> float:
    """30, '30s', '1m30s', '2h', '500ms' -> seconds."""
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower()
    total, pos = 0.0, 0
    for m in _DUR.finditer(s):
        if m.start() != pos and s[pos:m.start()].strip():
            raise ValueError(f"invalid duration: {v!r}")
        total += float(m.group(1)) * _FACTOR[m.group(2) or "s"]
        pos = m.end()
    if pos != len(s) or not s:
        raise ValueError(f"invalid duration: {v!r}")
    return total


def fmt_duration(sec: float) -> str:
    """3725 -> '1h2m5s'."""
    sec = round(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return (f"{h}h" if h else "") + (f"{m}m" if m else "") + (f"{s}s" if s or not (h or m) else "")


def fmt_clock(sec: float) -> str:
    """Seconds as a clock: 75 -> '1:15', 3725 -> '1:02:05'."""
    sec = round(sec)
    h, r = divmod(sec, 3600)
    return f"{h}:{r // 60:02d}:{r % 60:02d}" if h else f"{r // 60}:{r % 60:02d}"


# ------------------------------------------------------------------ Kubernetes quantities
def parse_cpu(v) -> float:
    """'2' -> 2.0, '500m' -> 0.5."""
    t = str(v).strip()
    return float(t[:-1]) / 1000 if t.endswith("m") else float(t)


_MEM = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12}


def parse_memory(v) -> int:
    """Bytes of '2Gi', '768Mi', '1G' or '512000000'."""
    t = str(v).strip()
    for unit in sorted(_MEM, key=len, reverse=True):
        if t.endswith(unit):
            return int(float(t[:-len(unit)]) * _MEM[unit])
    return int(float(t))


# ------------------------------------------------------------------ numbers for people
def num(v, decimals: int = 0, unit: str = "") -> str:
    """31526 -> '31,526' · 3.9 -> '3.9' · 250 with 'ms' -> '250 ms'. A '%' is attached without a space."""
    if v is None or (isinstance(v, float) and v != v):
        return "–"
    s = f"{v:,.{decimals}f}"
    if not unit:
        return s
    return f"{s}{unit}" if unit == "%" else f"{s} {unit}"


def plural(n: int, one: str, many: str) -> str:
    """plural(1, 'replica', 'replicas') -> '1 replica'."""
    return f"{num(n)} {one if n == 1 else many}"


def ms(v) -> str:
    """Latency: '8.4 ms', '250 ms'."""
    if v is None:
        return "–"
    return num(v, 0 if v >= 100 else 1, "ms")


# ------------------------------------------------------------------ environment
_ENV = re.compile(r"\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)(?:(:?)-([^}]*))?\}")


def expand_env(value):
    """Expand ${VAR}, ${VAR:-default}, ${VAR-default} and ${env:VAR} in strings, lists and mappings.
    As in a shell, ':-' also uses the default when the variable is set but empty. Anything else that looks
    like a placeholder (a JMeter ${__P(x)}, an unset variable without default) is left as it is."""
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    if not isinstance(value, str) or "${" not in value:
        return value

    def rep(m):
        name, colon, default = m.group(1), m.group(2), m.group(3)
        val = os.environ.get(name)
        if val is not None and not (colon and val == "" and default is not None):
            return val
        return default if default is not None else m.group(0)
    return _ENV.sub(rep, value)


# ------------------------------------------------------------------ misc
def slug(s: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (s[:maxlen].strip("-")) or "x"


def hash_paths(*paths: Path, extra: str = "") -> str:
    """A stable content hash of files and folders, used as the tag of the images the lab builds."""
    h = hashlib.sha256(extra.encode())
    for p in paths:
        p = Path(p)
        files = sorted(x for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts) if p.is_dir() else [p]
        for f in files:
            h.update(f.relative_to(p).as_posix().encode() if p.is_dir() else f.name.encode())
            with open(f, "rb") as fh:   # in chunks: a 200 MB JAR must not sit in memory
                for chunk in iter(lambda fh=fh: fh.read(1 << 20), b""):
                    h.update(chunk)
    return h.hexdigest()[:12]
