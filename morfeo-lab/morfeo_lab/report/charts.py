"""
Charts for the pages of the lab (run, history, comparison), as SVG with no dependencies.

Pages must open anywhere (offline, attached to an email, archived in CI): no JavaScript library and no CDN. Colours
come from CSS classes and variables, so the light and dark themes need nothing here.
"""
from __future__ import annotations

import html
import math

W = 640
M = {"t": 14, "b": 30}


def fmt_num(v: float | None, unit: str = "") -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if unit == "bytes":
        for u in ("B", "KiB", "MiB", "GiB"):
            if abs(v) < 1024 or u == "GiB":
                return f"{v:.0f} {u}" if u == "B" else f"{v:.1f} {u}"
            v /= 1024
    if unit == "%":
        return f"{v:.1f}%" if abs(v) < 10 else f"{v:.0f}%"
    if unit == "ratio":
        return f"{v * 100:.1f}%"
    if unit == "ms":
        if v == 0:
            return "0 ms"
        return f"{v:,.0f} ms" if abs(v) >= 100 else f"{v:.1f} ms" if abs(v) >= 1 else f"{v:.2f} ms"
    if abs(v) >= 10000:
        s = f"{v / 1000:.1f}k"
    elif abs(v) >= 100 or float(v).is_integer():
        s = f"{v:,.0f}"
    elif abs(v) >= 10:
        s = f"{v:.1f}"
    else:
        s = f"{v:.2f}"
    return f"{s} {unit}".strip()


def grouped_hbar(categories: list[str], groups: list[dict], *, unit: str = "", title: str = "",
                 width: int = W) -> str:
    """Horizontal grouped bars: groups=[{name, slot, values}] (used by `compare`)."""
    W = width
    cats = categories
    if not cats:
        return '<div class="chart empty">No data</div>'
    row_h, gap = 12, 10
    left = 170
    h = M["t"] + len(cats) * (len(groups) * row_h + gap) + M["b"]
    pw = W - left - 70
    hi = max((v or 0) for g in groups for v in g["values"]) * 1.05 or 1
    out = [f'<svg viewBox="0 0 {W} {h}" class="chart-svg" role="img" aria-label="{html.escape(title)}">']
    y = M["t"]
    for ci, c in enumerate(cats):
        out.append(f'<text class="tick cat" x="{left - 8}" y="{y + len(groups) * row_h / 2 + 4:.1f}" '
                   f'text-anchor="end">{html.escape(c)}</text>')
        for g in groups:
            v = g["values"][ci]
            w = (v or 0) / hi * pw
            out.append(f'<rect class="bar {g["slot"]}" x="{left}" y="{y + 1:.1f}" width="{max(0.5, w):.1f}" '
                       f'height="{row_h - 2}" rx="3"><title>{html.escape(g["name"])}: {fmt_num(v, unit)}</title></rect>')
            out.append(f'<text class="tick val" x="{left + w + 4:.1f}" y="{y + row_h - 3:.1f}">{fmt_num(v, unit)}</text>')
            y += row_h
        y += gap
    out.append("</svg>")
    legend = "".join(f'<span class="key"><i class="sw {g["slot"]}"></i>{html.escape(g["name"])}</span>' for g in groups)
    return ('<figure class="chart">' + (f"<figcaption>{html.escape(title)}</figcaption>" if title else "")
            + f'<div class="legend">{legend}</div>' + "".join(out) + "</figure>")


THEME_JS = r"""
(function(){
  const tg=document.getElementById('theme-toggle');
  if(tg) tg.addEventListener('click',function(){const r=document.documentElement;
    const dark=r.dataset.theme? r.dataset.theme==='dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    r.dataset.theme=dark?'light':'dark';});
})();
"""


def nice_ticks(hi: float, n: int = 5) -> list[float]:
    """0 .. a round number >= hi in steps of 1, 2 or 5 x 10^k."""
    if hi <= 0:
        return [0.0, 1.0]
    raw = hi / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    top = math.ceil(hi / step - 1e-9) * step
    return [round(i * step, 10) for i in range(round(top / step) + 1)]


def limit_chart(points: list[dict], *, unit: str, slo_p95: float | None, title: str, width: int = 1100,
                labels: bool = False) -> str:
    """Achieved throughput (left axis) and p95 (right axis) against the load level.
    points = [{level, label, goodput_rps, p95_ms, outcome}] (outcome: pass | fail | other). With `labels` the levels
    are steps (thread groups) and the axis shows their names instead of numbers."""
    pts = sorted((p for p in points if p.get("goodput_rps") is not None), key=lambda p: p["level"])
    if len(pts) < 2:
        return ""
    W, H = width, 320
    left, right, top, bottom = 64, 76, 34, 42
    pw, ph = W - left - right, H - top - bottom
    xt = nice_ticks(max(p["level"] for p in pts) * 1.02) if not labels else [0, *range(1, len(pts) + 1)]
    yt = nice_ticks(max(max(p["goodput_rps"], p.get("throughput_rps") or 0) for p in pts) * 1.05, 4)
    lat = [p["p95_ms"] for p in pts if p.get("p95_ms") is not None] + ([slo_p95] if slo_p95 else [])
    lt = nice_ticks(max(lat) * 1.05 if lat else 1, 4)
    x_hi, y_hi, l_hi = xt[-1], yt[-1], lt[-1]

    def X(v):
        return left + v / x_hi * pw

    def Y(v):
        return top + ph - v / y_hi * ph

    def L(v):
        return top + ph - v / l_hi * ph

    out = [f'<svg viewBox="0 0 {W} {H}" class="chart-svg" role="img" aria-label="{html.escape(title)}">']
    for v in yt:
        out.append(f'<line class="grid" x1="{left}" x2="{left + pw}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>')
        out.append(f'<text class="tick" x="{left - 8}" y="{Y(v) + 4:.1f}" text-anchor="end">{fmt_num(v)}</text>')
    for v in lt:
        out.append(f'<text class="tick" x="{left + pw + 8}" y="{L(v) + 4:.1f}">{fmt_num(v, "ms")}</text>')
    if labels:
        for p in pts:
            name = p["label"] if len(p["label"]) <= 14 else p["label"][:13] + "…"
            out.append(f'<text class="tick" x="{X(p["level"]):.1f}" y="{top + ph + 18}" text-anchor="middle">'
                       f'{html.escape(name)}<title>{html.escape(p["label"])}</title></text>')
    else:
        for v in xt:
            out.append(f'<text class="tick" x="{X(v):.1f}" y="{top + ph + 18}" text-anchor="middle">{fmt_num(v)}</text>')
    out.append(f'<text class="tick axis" x="{left + pw / 2:.1f}" y="{H - 6}" text-anchor="middle">'
               f'load level ({html.escape(unit)})</text>')
    out.append(f'<text class="tick axis" x="{left}" y="{top - 14}" text-anchor="middle">req/s</text>')
    out.append(f'<text class="tick axis" x="{left + pw}" y="{top - 14}" text-anchor="middle">p95</text>')
    if slo_p95:
        out.append(f'<line class="slo" x1="{left}" x2="{left + pw}" y1="{L(slo_p95):.1f}" y2="{L(slo_p95):.1f}"/>')
        out.append(f'<text class="tick slo-t" x="{left + pw - 4}" y="{L(slo_p95) - 5:.1f}" text-anchor="end">'
                   f'p95 SLO {fmt_num(slo_p95, "ms")}</text>')
    out.append('<polyline class="line c-ok" points="' + " ".join(f"{X(p['level']):.1f},{Y(p['goodput_rps']):.1f}"
                                                              for p in pts) + '"/>')
    lp = [p for p in pts if p.get("p95_ms") is not None]
    out.append('<polyline class="line c-p95" points="' + " ".join(f"{X(p['level']):.1f},{L(p['p95_ms']):.1f}"
                                                              for p in lp) + '"/>')
    for p in pts:
        cls = {"pass": "pt-pass", "fail": "pt-fail"}.get(p.get("outcome"), "pt-other")
        tip = (f"{p.get('label') or fmt_num(p['level']) + ' ' + unit}: {fmt_num(p['goodput_rps'], 'req/s')} successful, "
               f"p95 {fmt_num(p.get('p95_ms'), 'ms')}")
        out.append(f'<circle class="pt {cls}" cx="{X(p["level"]):.1f}" cy="{Y(p["goodput_rps"]):.1f}" r="5">'
                   f'<title>{html.escape(tip)}</title></circle>')
        if p.get("p95_ms") is not None:
            out.append(f'<circle class="pt2 {cls}" cx="{X(p["level"]):.1f}" cy="{L(p["p95_ms"]):.1f}" r="4">'
                       f'<title>{html.escape(tip)}</title></circle>')
    out.append("</svg>")
    legend = ('<span class="key"><i class="sw ln c-ok"></i>successful requests/s</span>'
              '<span class="key"><i class="sw ln c-p95"></i>p95 latency</span>'
              '<span class="key"><i class="sw dot-pass"></i>within the SLO</span>'
              '<span class="key"><i class="sw dot-fail"></i>outside the SLO</span>')
    return (f'<figure class="chart"><figcaption>{html.escape(title)}</figcaption><div class="legend">{legend}</div>'
            + "".join(out) + "</figure>")


def stacked_bar(parts: list[tuple[str, float]], *, title: str = "") -> str:
    """One horizontal 100% bar (CPU by component) with its legend."""
    parts = [(n, v) for n, v in parts if v > 0]
    if not parts:
        return ""
    total = sum(v for _, v in parts)
    slots = ["s1", "s3", "s2", "s4", "s7", "s5", "s6", "s8"]
    segs, keys = [], []
    for i, (name, v) in enumerate(parts):
        slot = slots[i % len(slots)] if i < len(slots) else "s8"
        pct = 100.0 * v / total
        segs.append(f'<span class="seg {slot}" style="width:{pct:.2f}%" title="{html.escape(name)}: {pct:.1f}%"></span>')
        keys.append(f'<span class="key"><i class="sw {slot}"></i>{html.escape(name)} <b>{pct:.0f}%</b></span>')
    return ('<div class="stack">' + (f'<div class="stack-t">{html.escape(title)}</div>' if title else "")
            + f'<div class="stack-bar">{"".join(segs)}</div><div class="legend">{"".join(keys)}</div></div>')


# ======================================================================== time series
def fmt_t(sec: float) -> str:
    sec = round(sec)
    h, r = divmod(sec, 3600)
    return f"{h}:{r // 60:02d}:{r % 60:02d}" if h else f"{r // 60}:{r % 60:02d}"


def _time_ticks(t_max: float, n: int = 6) -> list[int]:
    step = next((s for s in (5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200) if t_max / s <= n), 14400)
    return list(range(0, int(t_max) + 1, step))


def axis_fmt(v: float, unit: str = "") -> str:
    """Short tick labels: 500 ms, 2.5 s, 1.5k."""
    if unit == "ms":
        return f"{v / 1000:g} s" if v >= 1000 else f"{v:g} ms"
    if v >= 1000:
        return f"{v / 1000:g}k"
    return f"{v:g}"


def _left(labels: list[str]) -> int:
    return 14 + 10 * max(len(x) for x in labels)


def _segments(xs: list[float], ys: list) -> list[list[tuple[float, float]]]:
    """Polyline pieces: a missing value (None) breaks the line."""
    segs, cur = [], []
    for x, y in zip(xs, ys):
        if y is None:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append((x, y))
    if cur:
        segs.append(cur)
    return segs


def time_chart(t: list[float], series: list[dict], *, title: str, unit: str = "", step: float = 1,
               threshold: tuple[float, str] | None = None, shade: tuple[float, float, str] | None = None,
               stacked: bool = False, width: int = 560, height: int = 230) -> str:
    """Values over test time. series = [{name, values, cls, dash?}]: `cls` picks the colour (c-*).
    threshold = (value, label): a dashed red line, drawn only when it does not flatten the data.
    shade = (t0, t1, label): a shaded period (the warm-up)."""
    series = [s for s in series if any(v is not None for v in s["values"])]
    if not t or not series:
        return ""
    W, H = width, height
    t_max = max(t[-1] + step, 1)
    if stacked:
        tops = [sum(s["values"][i] or 0 for s in series) for i in range(len(t))]
    else:
        tops = [v for s in series for v in s["values"] if v is not None]
    data_hi = max(tops) if tops else 0
    hi = data_hi
    th_out = None
    if threshold and threshold[0] is not None:
        if threshold[0] <= max(data_hi * 2.5, 1e-9):
            hi = max(hi, threshold[0])
        else:
            th_out = threshold
    yt = nice_ticks(hi * 1.08 if hi > 0 else 1, 4)
    y_hi = yt[-1]
    left, right, top, bottom = _left([axis_fmt(v, unit) for v in yt]), 14, 16, 30
    pw, ph = W - left - right, H - top - bottom

    def X(v):
        return left + v / t_max * pw

    def Y(v):
        return top + ph - v / y_hi * ph

    out = [f'<svg viewBox="0 0 {W} {H}" class="chart-svg" role="img" aria-label="{html.escape(title)}">']
    if shade:
        x0, x1 = X(shade[0]), X(min(shade[1], t_max))
        if x1 - x0 > 1:
            out.append(f'<rect class="shade" x="{x0:.1f}" y="{top}" width="{x1 - x0:.1f}" height="{ph}"/>')
            out.append(f'<text class="tick shade-t" x="{x0 + 4:.1f}" y="{top + 11}">{html.escape(shade[2])}</text>')
    for v in yt:
        out.append(f'<line class="grid" x1="{left}" x2="{left + pw}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>')
        out.append(f'<text class="tick" x="{left - 6}" y="{Y(v) + 3.5:.1f}" text-anchor="end">{axis_fmt(v, unit)}</text>')
    for v in _time_ticks(t_max):
        out.append(f'<text class="tick" x="{X(v):.1f}" y="{top + ph + 17}" text-anchor="middle">{fmt_t(v)}</text>')
    xs = [X(v + step / 2) for v in t]
    if stacked:
        base = [0.0] * len(t)
        for s in series:
            vals = [s["values"][i] or 0 for i in range(len(t))]
            upper = [b + v for b, v in zip(base, vals)]
            pts = [f"{x:.1f},{Y(u):.1f}" for x, u in zip(xs, upper)]
            pts += [f"{x:.1f},{Y(b):.1f}" for x, b in reversed(list(zip(xs, base)))]
            out.append(f'<polygon class="area {s["cls"]}" points="{" ".join(pts)}"><title>{html.escape(s["name"])}'
                       f'</title></polygon>')
            base = upper
    else:
        for s in series:
            for seg in _segments(xs, s["values"]):
                if len(seg) == 1:
                    x, y = seg[0]
                    out.append(f'<circle class="dotpt {s["cls"]}" cx="{x:.1f}" cy="{Y(y):.1f}" r="2"/>')
                    continue
                pts = " ".join(f"{x:.1f},{Y(y):.1f}" for x, y in seg)
                out.append(f'<polyline class="line {s["cls"]}{" dash" if s.get("dash") else ""}" points="{pts}"/>')
    if threshold and not th_out:
        y = Y(threshold[0])
        out.append(f'<line class="slo" x1="{left}" x2="{left + pw}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="tick slo-t" x="{left + pw - 2}" y="{y - 4:.1f}" text-anchor="end">'
                   f'{html.escape(threshold[1])}</text>')
    out.append(f'<line class="axis" x1="{left}" x2="{left + pw}" y1="{top + ph}" y2="{top + ph}"/>')
    out.append("</svg>")
    shape = "sq" if stacked else "ln"
    keys = "".join(f'<span class="key"><i class="sw {shape} {s["cls"]}{" dash" if s.get("dash") else ""}"></i>'
                   f'{html.escape(s["name"])}</span>' for s in series)
    if threshold and threshold[0] is not None:
        keys += (f'<span class="key"><i class="sw ln slo-sw"></i>{html.escape(threshold[1])}'
                 + (" (above the chart)" if th_out else "") + "</span>")
    return (f'<figure class="chart"><figcaption>{html.escape(title)}</figcaption>'
            f'<div class="legend">{keys}</div>{"".join(out)}</figure>')


# ======================================================================== percentiles
_PCT_POS = {0: 0.0, 50: 0.301, 75: 0.602, 90: 1.0, 95: 1.301, 99: 2.0, 99.9: 3.0, 100: 3.5}


def _log_ticks(lo: float, hi: float) -> list[float]:
    k0, k1 = math.floor(math.log10(lo)), math.ceil(math.log10(hi))
    mult = (1, 2, 5) if k1 - k0 <= 2 else (1,)
    return [m * 10 ** k for k in range(k0, k1 + 1) for m in mult if lo <= m * 10 ** k * 1.0001 and m * 10 ** k <= hi * 1.0001]


def percentile_chart(q: list[float], ms: list[float], *, slo_p95: float | None = None,
                     slo_p99: float | None = None, title: str = "Response time by percentile",
                     width: int = 560, height: int = 230) -> str:
    """The latency spectrum: x = percentile on a log(1 / (1 - q)) scale, so the tail gets the room;
    y on a log scale when the tail is more than 20 times the median."""
    pts = [(p, v) for p, v in zip(q, ms) if p in _PCT_POS and p >= 50 and v is not None]
    if len(pts) < 3:
        return ""
    W, H = width, height
    right, top, bottom = 18, 18, 30
    ph = H - top - bottom
    data_lo, data_hi = max(min(v for _, v in pts), 0.01), max(v for _, v in pts)
    slos = [(v, n) for v, n in ((slo_p95, "p95 SLO"), (slo_p99, "p99 SLO")) if v]
    log = data_hi / data_lo > 20
    if log:
        lo = 10 ** math.floor(math.log10(min([data_lo] + [v for v, _ in slos])))
        hi = 10 ** math.ceil(math.log10(max([data_hi] + [v for v, _ in slos])))
        yt = _log_ticks(lo, hi)

        def Y(v):
            return top + ph - (math.log10(max(v, lo)) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)) * ph
    else:
        yt = nice_ticks(max([data_hi] + [v for v, _ in slos if v <= data_hi * 2.5]) * 1.12, 4)
        hi = yt[-1]

        def Y(v):
            return top + ph - v / hi * ph
    x_lo, x_hi = _PCT_POS[50], _PCT_POS[100]
    left = _left([axis_fmt(v, "ms") for v in yt])
    pw = W - left - right

    def X(p):
        return left + (_PCT_POS[p] - x_lo) / (x_hi - x_lo) * pw

    def name(p):
        return "max" if p == 100 else f"p{p:g}"
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart-svg" role="img" aria-label="{html.escape(title)}">']
    for v in yt:
        out.append(f'<line class="grid" x1="{left}" x2="{left + pw}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>')
        out.append(f'<text class="tick" x="{left - 6}" y="{Y(v) + 3.5:.1f}" text-anchor="end">{axis_fmt(v, "ms")}</text>')
    for p, _ in pts:
        out.append(f'<line class="grid v" x1="{X(p):.1f}" x2="{X(p):.1f}" y1="{top}" y2="{top + ph}"/>')
        out.append(f'<text class="tick" x="{X(p):.1f}" y="{top + ph + 17}" text-anchor="middle">{name(p)}</text>')
    last_y = None
    for v, n in sorted(slos):
        if v <= (hi if log else yt[-1]):
            y = Y(v)
            out.append(f'<line class="slo" x1="{left}" x2="{left + pw}" y1="{y:.1f}" y2="{y:.1f}"/>')
            if last_y is None or abs(last_y - y) > 11:
                out.append(f'<text class="tick slo-t" x="{left + 4}" y="{y - 4:.1f}">{n} {fmt_num(v, "ms")}</text>')
            last_y = y
    path = " ".join(f"{X(p):.1f},{Y(v):.1f}" for p, v in pts)
    area = f"{X(pts[0][0]):.1f},{top + ph} {path} {X(pts[-1][0]):.1f},{top + ph}"
    out.append(f'<polygon class="area c-p50 soft" points="{area}"/>')
    out.append(f'<polyline class="line c-p50" points="{path}"/>')
    for p, v in pts:
        out.append(f'<circle class="pt c-p50" cx="{X(p):.1f}" cy="{Y(v):.1f}" r="3.5">'
                   f'<title>{name(p)}: {fmt_num(v, "ms")}</title></circle>')
        crowded = p == 99.9 and any(pp == 100 and abs(Y(vv) - Y(v)) < 24 for pp, vv in pts)
        if p in (50, 95, 99, 99.9, 100) and not crowded:
            anchor = "start" if p == 50 else "end" if p == 100 else "middle"
            dx = 6 if p == 50 else -6 if p == 100 else 0
            dy = 16 if p == 100 else -8   # max below its point, clear of the p99.9 label
            out.append(f'<text class="tick val" x="{X(p) + dx:.1f}" y="{Y(v) + dy:.1f}" text-anchor="{anchor}">'
                       f'{fmt_num(v, "ms")}</text>')
    out.append(f'<line class="axis" x1="{left}" x2="{left + pw}" y1="{top + ph}" y2="{top + ph}"/>')
    out.append("</svg>")
    note = '<span class="key muted">log scale</span>' if log else ""
    return (f'<figure class="chart"><figcaption>{html.escape(title)}</figcaption>'
            f'<div class="legend">{note}</div>{"".join(out)}</figure>')


# ======================================================================== small trend line
def sparkline(values: list, *, width: int = 120, height: int = 28, cls: str = "c-p50") -> str:
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = 0.0, max(vals)   # from zero: a 1 % change must not look like a cliff
    span = hi or 1
    n = len(values)

    def X(i):
        return 2 + i / (n - 1) * (width - 4)

    def Y(v):
        return height - 3 - (v - lo) / span * (height - 6)
    segs = _segments([X(i) for i in range(n)], values)
    lines = "".join('<polyline class="line ' + cls + '" points="'
                    + " ".join(f"{x:.1f},{Y(v):.1f}" for x, v in seg) + '"/>' for seg in segs if len(seg) > 1)
    last = next((i for i in range(n - 1, -1, -1) if values[i] is not None), None)
    dot = f'<circle class="pt {cls}" cx="{X(last):.1f}" cy="{Y(values[last]):.1f}" r="2.5"/>' if last is not None else ""
    return f'<svg viewBox="0 0 {width} {height}" class="spark" aria-hidden="true">{lines}{dot}</svg>'
