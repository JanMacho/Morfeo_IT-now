"""The result pages of the lab (self-contained HTML, no CDN):

  results/<run>/index.html     the run: a summary, then one card per scenario (verdict and why, key figures,
                               diagnosis, acceptance criteria, capacity, charts, endpoints, errors, the log of the
                               application and where the time goes)
  results/index.html           the history of every run on this machine
  results/compare-*.html       runs side by side (`morfeo-lab compare`)

The JMeter dashboard (results/<run>/<scenario>/jmeter-report/) and Grafana hold the detailed views.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from ..analysis.welch import welch
from ..scenario import load_text
from ..units import fmt_clock, slug
from .charts import THEME_JS, fmt_num, grouped_hbar, limit_chart, percentile_chart, sparkline, stacked_bar, time_chart
from .style import CSS

E = html.escape

VERDICT_TEXT = {"PASS": "Passed", "FAIL": "Failed", "PASS_WITH_WARNINGS": "Passed with warnings", "ERROR": "Not run"}
STATUS_TEXT = {"pass": "Met", "fail": "Not met", "warn": "Review"}
MODEL_TEXT = {"closed": "closed model", "throughput": "fixed request rate", "open": "open model", "mixed": "mixed model"}
CLASS_TEXT = {"server_5xx": "Server 5xx", "network": "No response", "assertion": "Failed assertion",
              "client_4xx": "Client 4xx"}
SEVERITY_TEXT = {"critical": "critical", "serious": "serious", "warning": "warning", "info": "info", "good": "ok"}
THEME_BUTTON = ('<button class="theme" id="theme-toggle" type="button" aria-label="Switch theme">'
                '<span class="i-sun">☀</span><span class="i-moon">☾</span></button>')


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(title)}</title>
<style>{CSS}</style></head>
<body><main>{body}
<footer>MORFEO lab · generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</footer>
</main><script>{THEME_JS}</script></body></html>"""


def _header(kind: str, title: str, meta: list[str], extra: str = "") -> str:
    m = "".join(f"<span>{x}</span>" for x in meta if x)
    return (f'<header class="top"><div class="title"><div class="brand"><span class="logo"></span>MORFEO · {E(kind)}'
            f'</div><h1>{E(title)}</h1><div class="meta">{m}</div>{extra}</div>{THEME_BUTTON}</header>')


def pct(rate, digits: int = 2) -> str:
    """A fraction (0 to 1) as a percentage."""
    return "–" if rate is None else f"{rate * 100:.{digits}f}%"


def _share(v, digits: int = 1) -> str:
    return pct(v, digits)


def _count(n, one: str, many: str) -> str:
    return f"{fmt_num(n)} {one if n == 1 else many}"


def _verdict(v: str, small: bool = False) -> str:
    return (f'<span class="verdict v-{E(v)}{" sm" if small else ""}"><span class="dot"></span>'
            f'{E(VERDICT_TEXT.get(v, v))}</span>')


def _tile(k: str, v: str, d: str = "", state: str = "") -> str:
    number, _, unit = v.partition(" ")
    shown = (E(number) + f"<small>{E(unit)}</small>") if unit and (number[:1].isdigit() or number[:1] == "-") else E(v)
    return (f'<div class="tile{" " + state if state else ""}"><div class="k">{E(k)}</div><div class="v">{shown}</div>'
            f'<div class="d">{E(d)}</div></div>')


def _table(headers: list[tuple[str, str]], rows: list[list[str]], cls: str = "") -> str:
    th = "".join(f'<th class="{c}">{E(h)}</th>' for h, c in headers)
    trs = "".join("<tr>" + "".join(f'<td class="{headers[i][1]}">{cell}</td>' for i, cell in enumerate(r)) + "</tr>"
                  for r in rows)
    return f'<div class="table-wrap {cls}"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


def _ms(v):
    return fmt_num(v, "ms")


def _when(iso: str | None) -> str:
    """The start time in the local time of this machine ('2026-10-11 10:54')."""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso[:16].replace("T", " ")


def _window(s: dict) -> dict:
    return s["steady"] if s["steady"]["requests"] else s["overall"]


# ======================================================================== the verdict and why
def _why(s: dict) -> str:
    """The answer first: what failed and what matters, in a few lines."""
    failed = [c for c in s["checks"] if c["status"] == "fail"]
    warned = [c for c in s["checks"] if c["status"] == "warn"]
    findings = [d for d in s.get("diagnosis", []) if d["severity"] in ("critical", "serious", "warning")]
    lines = []
    if failed:
        lines += [f'<li><b>{E(c["name"])}</b>: {E(str(c["actual"]))} (target {E(str(c["target"]))})</li>' for c in failed[:4]]
    for d in findings[:3]:
        lines.append(f'<li>{E(d["title"])}</li>')
    if not failed and warned:
        lines += [f'<li><b>{E(c["name"])}</b>: {E(str(c["actual"]))}'
                  + (f' · {E(c["detail"])}' if c.get("detail") else "") + "</li>" for c in warned[:3]]
    cap = s.get("capacity") or {}
    if cap.get("available") and not failed:
        lines.append(f"<li>{_capacity_sentence(cap)}</li>")
    if not lines:
        met = sum(1 for c in s["checks"] if c["status"] == "pass")
        return f'<p class="callout good">All {met} acceptance criteria were met, with no signs of saturation.</p>'
    kind = "bad" if failed else "warn"
    head = "Why it failed" if failed else "What to look at"
    return f'<div class="callout {kind}"><b>{head}</b><ul class="why">{"".join(lines)}</ul></div>'


def _level_name(row: dict, cap: dict) -> str:
    return row["label"]


def _capacity_sentence(cap: dict) -> str:
    sus, brk = cap.get("sustainable"), cap.get("breaking")
    inside = (f'Within the objectives up to <b>{E(_level_name(sus, cap))}</b> ({fmt_num(sus["goodput_rps"], "req/s")}, '
              f'p95 {_ms(sus["p95_ms"])})' if sus else "Outside the objectives from the first level")
    outside = (f'outside them from <b>{E(_level_name(brk, cap))}</b> (p95 {_ms(brk["p95_ms"])}, '
               f'{pct(brk["server_error_rate"], 1)} errors)' if brk else "within them at every level tested")
    return f"{inside}; {outside}"


# ======================================================================== scenario card
def _tiles(s: dict) -> str:
    o, w, slo = s["overall"], _window(s), s["slo"]
    lat = w["latency_ms"]

    def over(v, limit):
        return "" if limit is None or v is None else ("bad" if v > limit else "good")
    p95_limit, p99_limit = slo.get("p95_ms"), slo.get("p99_ms")
    error_limit = slo.get("error_rate_pct")
    tiles = [
        _tile("Requests", fmt_num(o["requests"]), f"in {fmt_clock(o['duration_s'])}"),
        _tile("Throughput", fmt_num(w["throughput_rps"], "req/s"), f"{fmt_num(w['goodput_rps'], 'req/s')} successful"),
        _tile("p95", _ms(lat["p95"]), f"SLO {_ms(p95_limit)}" if p95_limit else "", over(lat["p95"], p95_limit)),
        _tile("p99", _ms(lat["p99"]), f"SLO {_ms(p99_limit)}" if p99_limit else "", over(lat["p99"], p99_limit)),
        _tile("Server errors", pct(w["server_error_rate"]), f"SLO ≤ {error_limit:g}%" if error_limit is not None else "",
              over(w["server_error_rate"] * 100, error_limit)),
        _tile("Client 4xx", pct(o["error_rate"])),
        _tile("Peak users", fmt_num(o.get("max_users"))),
    ]
    return f'<div class="tiles">{"".join(tiles)}</div>'


def _charts(s: dict) -> str:
    ser = s.get("series") or {}
    t = ser.get("t") or []
    if not t:
        return ""
    step = ser.get("step_s", 1)
    sc = s["scenario"]
    slo = s["slo"]
    model = sc.get("model", "closed")
    ramp = (s.get("steady_window_s") or [0])[0]
    shade = (0, ramp, "ramp-up") if ramp and ramp >= 2 * step else None
    if model == "throughput":
        load = [{"name": "requests/s", "values": ser.get("rps"), "cls": "c-ok"},
                {"name": "requested", "values": ser.get("requested_rps") or [], "cls": "c-ok", "dash": True}]
    elif model == "open":
        per = (s.get("capacity") or {}).get("requests_per_arrival")
        arrivals = ser.get("requested_arrivals") or []
        load = [{"name": "requests/s", "values": ser.get("rps"), "cls": "c-ok"},
                {"name": "requested", "values": [None if v is None else v * per for v in arrivals], "cls": "c-ok",
                 "dash": True} if per else
                {"name": "requested arrivals/s", "values": arrivals, "cls": "c-muted", "dash": True}]
    else:
        load = [{"name": "users", "values": ser.get("users"), "cls": "c-users"},
                {"name": "requested", "values": ser.get("requested_users") or [], "cls": "c-users", "dash": True}]
    figures = [
        time_chart(t, load, title="Requested and actual load", step=step, shade=shade),
        time_chart(t, [{"name": "successful", "values": ser.get("successful_rps"), "cls": "c-ok"},
                       {"name": "failed", "values": ser.get("failed_rps"), "cls": "c-err"}],
                   title="Requests per second", step=step, stacked=True, shade=shade),
        time_chart(t, [{"name": "p50", "values": ser.get("p50_ms"), "cls": "c-p50"},
                       {"name": "p95", "values": ser.get("p95_ms"), "cls": "c-p95"},
                       {"name": "p99", "values": ser.get("p99_ms"), "cls": "c-p99"}],
                   title="Latency over time", unit="ms", step=step, shade=shade,
                   threshold=(slo.get("p95_ms"), f"p95 SLO {_ms(slo.get('p95_ms'))}") if slo.get("p95_ms") else None),
    ]
    curve = s.get("percentile_curve") or {}
    if curve.get("ms"):
        figures.append(percentile_chart(curve["q"], curve["ms"], slo_p95=slo.get("p95_ms"), slo_p99=slo.get("p99_ms")))
    figures = [f for f in figures if f]
    cap = s.get("capacity") or {}
    wide = ""
    if cap.get("available"):
        sus, brk = cap.get("sustainable"), cap.get("breaking")
        labelled = cap["unit"] == "step" or any(r["load"] is None for r in cap["levels"])
        points = []
        for i, r in enumerate(cap["levels"], 1):
            outcome = ("fail" if brk and r["goodput_rps"] is not None and not r["within_slo"] else
                       "pass" if sus and r["within_slo"] else "other")
            points.append({"level": i if labelled else r["load"], "label": r["label"], "goodput_rps": r["goodput_rps"],
                           "throughput_rps": r["throughput_rps"], "p95_ms": r["p95_ms"], "outcome": outcome})
        wide = limit_chart(points, unit=cap["unit"], slo_p95=slo.get("p95_ms"), title="Capacity by load level",
                           width=1120, labels=labelled)
    return (f'<div class="charts">{"".join(figures)}</div>' if figures else "") + (
        f'<div class="charts one">{wide}</div>' if wide else "")


def _links(s: dict, folder: str) -> str:
    links = s.get("links") or {}
    out = []
    if links.get("jmeter_report"):
        out.append(f'<a class="primary" href="{E(folder)}/{E(links["jmeter_report"])}">JMeter report</a>')
    if links.get("grafana"):
        out.append(f'<a href="{E(links["grafana"])}" target="_blank" rel="noopener">Grafana</a>')
    if links.get("flamegraph"):
        out.append(f'<a href="{E(folder)}/{E(links["flamegraph"])}">Flame graph</a>')
    out.append(f'<a href="{E(folder)}/summary.json">summary.json</a>')
    out.append(f'<a href="{E(folder)}/app/">Application logs</a>')
    out.append(f'<a href="{E(folder)}/junit.xml">junit.xml</a>')
    out.append(f'<a href="{E(folder)}/raw/">Raw data</a>')
    return f'<div class="links">{"".join(out)}</div>'


def _diagnosis(s: dict) -> str:
    items = s.get("diagnosis", [])
    if not items:
        return ""
    return '<h3>Diagnosis</h3><div class="diag">' + "".join(
        f'<div class="item sev-{E(d["severity"])}"><span class="sev">{E(SEVERITY_TEXT.get(d["severity"], d["severity"]))}'
        f'</span><div><div class="t">{E(d["title"])}</div><div class="dd">{E(d["detail"])}</div></div></div>'
        for d in items) + "</div>"


def _criteria(s: dict) -> str:
    notes = any(c.get("detail") for c in s["checks"])
    return "<h3>Acceptance criteria</h3>" + _table(
        [("Result", ""), ("Criterion", ""), ("Target", "num"), ("Actual", "num")] + ([("Note", "wrap")] if notes else []),
        [[f'<span class="status st-{c["status"]}">{E(STATUS_TEXT[c["status"]])}</span>', E(c["name"]),
          E(str(c["target"])), E(str(c["actual"]))]
         + ([f'<span class="muted">{E(c.get("detail", ""))}</span>'] if notes else [])
         for c in s["checks"]])


def _capacity(s: dict) -> str:
    cap = s.get("capacity") or {}
    if not cap.get("available"):
        reason = cap.get("reason")
        return f'<h3>Capacity</h3><p class="cap muted">{E(reason)}</p>' if reason else ""
    rows = []
    for r in cap["levels"]:
        rows.append([E(r["label"]), fmt_num(r["throughput_rps"], "req/s"), fmt_num(r["goodput_rps"], "req/s"),
                     _ms(r["p95_ms"]), _ms(r["p99_ms"]), pct(r["server_error_rate"], 2),
                     f'<span class="status st-{"pass" if r["within_slo"] else "fail"}">'
                     f'{"Within" if r["within_slo"] else "Outside"}</span>'])
    knee = cap.get("saturation_knee")
    note = (f' · Throughput stops growing at <b>{E(knee["label"])}</b>.' if knee else "")
    return ('<h3>Capacity</h3><p class="cap">' + _capacity_sentence(cap) + note + "</p>"
            + _table([("Level", ""), ("Requests/s", "num"), ("Successful/s", "num"), ("p95", "num"), ("p99", "num"),
                      ("Server errors", "num"), ("Objectives", "")], rows))


def _endpoints(s: dict) -> str:
    rows = []
    for e in s["endpoints"]:
        lat = e["latency_ms"]
        limit = e.get("slo_p95_ms")
        over = limit and lat["p95"] is not None and lat["p95"] > limit
        codes = ", ".join(f"{k} ×{fmt_num(v)}" for k, v in sorted(e["status_codes"].items()))
        rows.append([f'<span class="mono">{E(e["key"])}</span>', fmt_num(e["requests"]),
                     f'<span class="share"><i style="width:{min(100, e["share"] * 100):.1f}%"></i></span>{_share(e["share"])}',
                     fmt_num(e["throughput_rps"]), _ms(lat["p50"]),
                     f'<span class="{"delta-bad" if over else ""}">{_ms(lat["p95"])}</span>', _ms(lat["p99"]),
                     _ms(lat["max"]),
                     f'<span class="{"delta-bad" if e["server_error_rate"] else ""}">{pct(e["server_error_rate"])}</span>',
                     pct(e["error_rate"]), f'<span class="muted">{E(codes)}</span>'])
    return "<h3>Endpoints</h3>" + _table(
        [("Endpoint", ""), ("Requests", "num"), ("Share", "num"), ("req/s", "num"), ("p50", "num"), ("p95", "num"),
         ("p99", "num"), ("max", "num"), ("Server errors", "num"), ("4xx", "num"), ("HTTP codes", "")], rows)


def _errors(s: dict) -> str:
    errors = (s.get("errors") or [])[:6]
    if not errors:
        return ""
    return "<h3>Top errors</h3>" + _table(
        [("Type", ""), ("Endpoint", ""), ("Code", "num"), ("Message", "wrap"), ("Count", "num"), ("First at", "num")],
        [[f'<span class="cls cls-{E(e["class"])}">{E(CLASS_TEXT.get(e["class"], e["class"]))}</span>',
          f'<span class="mono">{E(e["label"])}</span>', E(str(e["code"])), E(e["message"]), fmt_num(e["count"]),
          fmt_clock(e["first_at_s"])] for e in errors])


def _traffic_log(s: dict) -> str:
    log = s.get("traffic_log") or {}
    if not log.get("available"):
        return ""
    rows = []
    for e in log["endpoints"]:
        inside, outside = e.get("inside_ms") or {}, e.get("outside_ms") or {}
        coverage = e["logged"] / e["client_answered"] if e["client_answered"] else None
        rows.append([f'<span class="mono">{E(e["key"])}</span>', fmt_num(e["client_answered"]), fmt_num(e["logged"]),
                     f'<span class="{"delta-bad" if coverage is not None and coverage < 0.98 else ""}">{pct(coverage, 1)}</span>',
                     _ms(inside.get("p50")), _ms(inside.get("p95")),
                     _ms(max(0.0, outside["p50"])) if outside.get("p50") is not None else "–",
                     _ms(max(0.0, outside["p95"])) if outside.get("p95") is not None else "–"])
    head = (f'<h3>Traffic log of the application</h3><p class="cap">{fmt_num(log["logged"])} lines for '
            f'{fmt_num(log["client_answered"])} answered requests ({pct(log["completeness"], 1)}) · '
            f'<a href="{E(log.get("file", "app/"))}">{E(log.get("file", "app/"))}</a></p>')
    return head + _table([("Endpoint", ""), ("Answered", "num"), ("Logged", "num"), ("Logged %", "num"),
                          ("In the app p50", "num"), ("In the app p95", "num"), ("Outside the app p50", "num"),
                          ("Outside the app p95", "num")], rows)


def _profile(s: dict, folder: str) -> str:
    prof = s.get("profile") or {}
    if not prof.get("available"):
        return ""
    event = {"cpu": "CPU time", "wall": "Wall-clock time", "alloc": "Allocations", "lock": "Lock contention"}.get(
        prof.get("event"), prof.get("event", ""))
    bar = stacked_bar([(c["name"], c["samples"]) for c in prof["categories"]], title=f"{event} by component")

    def rows(items):
        return [[f'<span class="mono">{E(m["method"])}</span>', pct(m["pct"] / 100, 1)] for m in items[:10]]
    tables = '<div class="two"><div><h4>Hottest methods (own time)</h4>' + _table(
        [("Method", "wrap"), ("Share", "num")], rows(prof["self_top"])) + "</div>"
    if prof.get("app_top"):
        tables += ("<div><h4>Application methods (including what they call)</h4>"
                   + _table([("Method", "wrap"), ("Share", "num")], rows(prof["app_top"])) + "</div>")
    tables += "</div>"
    link = (f' · <a href="{E(folder)}/{E(prof["files"]["flamegraph"])}">flame graph</a>'
            if (prof.get("files") or {}).get("flamegraph") else "")
    return (f'<h3>Where the time goes</h3><p class="cap">{fmt_num(prof["samples"])} samples of the steady state · '
            f'{_count(prof.get("replicas", 1), "replica", "replicas")}{link}</p>' + bar + tables)


def _setup(s: dict) -> str:
    st = s.get("setup") or {}
    if not st.get("requests"):
        return ""
    bad = st["failed_requests"]
    return (f'<p class="cap">setUp: {_count(st["requests"], "request", "requests")} in {fmt_clock(st["duration_s"])}'
            + (f' · <span class="delta-bad">{bad} failed</span>' if bad else "")
            + " (not counted in the figures above)</p>")


def _scenario_card(s: dict) -> str:
    sc = s["scenario"]
    title = E(sc.get("title") or sc["name"])
    folder = slug(sc["name"])
    if s.get("verdict") == "ERROR":
        return (f'<section class="scn v-border-ERROR" id="{E(folder)}"><div class="scn-head">'
                f'<div><h2>{title}</h2><div class="meta"><span>{E(sc.get("file", ""))}</span></div></div>'
                f'{_verdict("ERROR")}</div><p class="callout bad">{E(s.get("error", ""))}</p></section>')
    gen = s["generators"]
    mode = {"single": "the plan runs as it is", "plan": "the plan divides its own load",
            "split": "the lab divides the load"}.get(gen["distribution"]["mode"], "")
    meta = [E(sc.get("file", "")), E(MODEL_TEXT.get(sc.get("model", "closed"), "")), E(load_text(sc)),
            f"duration {fmt_clock(s['overall'].get('duration_s') or 0)}",
            E(_count(gen["count"], "generator", "generators")) + (f" ({E(mode)})" if gen["count"] == 1 and mode else ""),
            E(_count(s["application"].get("replicas", 1), "replica", "replicas"))]
    partial = '<p class="callout warn">The run was interrupted: these are partial results.</p>' if s.get("interrupted") else ""
    return (f'<section class="scn v-border-{E(s["verdict"])}" id="{E(folder)}"><div class="scn-head"><div>'
            f'<h2>{title}</h2><div class="meta">{"".join(f"<span>{m}</span>" for m in meta if m)}</div></div>'
            f'{_verdict(s["verdict"])}</div>{partial}{_why(s)}{_tiles(s)}{_links(s, folder)}{_diagnosis(s)}'
            f'{_criteria(s)}{_capacity(s)}{_charts(s)}{_endpoints(s)}{_errors(s)}{_setup(s)}{_traffic_log(s)}'
            f'{_profile(s, folder)}</section>')


# ======================================================================== the run
def _first_fail(s: dict) -> str:
    c = next((c for c in s.get("checks", []) if c["status"] == "fail"), None)
    return f'{c["name"]}: {c["actual"]} (target {c["target"]})' if c else ""


def _overview_table(summaries: list[dict]) -> str:
    rows = []
    for s in summaries:
        sc = s["scenario"]
        name = f'<a href="#{E(slug(sc["name"]))}">{E(sc.get("title") or sc["name"])}</a>'
        if s.get("verdict") == "ERROR":
            rows.append([_verdict("ERROR", True), name, "–", "–", "–", "–",
                         f'<span class="muted">{E((s.get("error") or "").splitlines()[0][:120])}</span>'])
            continue
        w = _window(s)
        finding = next((d for d in s.get("diagnosis", []) if d["severity"] in ("critical", "serious", "warning")), None)
        note = _first_fail(s) if s["verdict"] == "FAIL" and _first_fail(s) else (finding or {}).get("title", "")
        rows.append([_verdict(s["verdict"], True), name, fmt_num(w["throughput_rps"], "req/s"),
                     _ms(w["latency_ms"]["p95"]), _ms(w["latency_ms"]["p99"]), pct(w["server_error_rate"]),
                     f'<span class="muted">{E(note)}</span>'])
    return _table([("", ""), ("Scenario", ""), ("Throughput", "num"), ("p95", "num"), ("p99", "num"),
                   ("Server errors", "num"), ("Main finding", "wrap")], rows, "overview")


def _chips(counts: dict, small: bool = False) -> str:
    return "".join(f'<span class="count{" sm" if small else ""} c-{v}"><b>{counts[v]}</b> {E(VERDICT_TEXT[v].lower())}</span>'
                   for v in ("PASS", "PASS_WITH_WARNINGS", "FAIL", "ERROR") if counts.get(v))


def write_run_index(path: Path, run_id: str, application: str, summaries: list[dict]):
    counts: dict[str, int] = {}
    for s in summaries:
        counts[s["verdict"]] = counts.get(s["verdict"], 0) + 1
    nav = "".join(f'<a href="#{E(slug(s["scenario"]["name"]))}"><span class="dot d-{E(s["verdict"])}"></span>'
                  f'{E(s["scenario"].get("title") or s["scenario"]["name"])}</a>' for s in summaries)
    started = min((s.get("started_at") or "" for s in summaries if s.get("started_at")), default="")
    meta = [f"Application <b>{E(application)}</b>", E(_count(len(summaries), "scenario", "scenarios")), E(_when(started))]
    body = (_header("run", run_id, meta, f'<nav class="toc">{nav}</nav>' if nav else "")
            + f'<section class="exec"><div class="exec-head"><h2>Summary</h2><div class="counts">{_chips(counts)}</div></div>'
            + _overview_table(summaries) + "</section>" + "".join(_scenario_card(s) for s in summaries)
            + '<p class="back"><a href="../index.html">← Test history</a></p>')
    path.write_text(_page(f"MORFEO · {run_id}", body), encoding="utf-8")


# ======================================================================== history
def write_history(results_dir: Path):
    runs: dict[str, list[tuple[dict, str]]] = {}
    for summary in sorted(results_dir.glob("*/*/summary.json"), reverse=True)[:2000]:
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        runs.setdefault(summary.parent.parent.name, []).append((data, summary.parent.relative_to(results_dir).as_posix()))
    order = sorted(runs, reverse=True)

    # trends: a card per scenario that ran more than once (oldest to newest)
    by_scenario: dict[tuple[str, str], list[dict]] = {}
    for run in reversed(order):
        for s, _ in runs[run]:
            by_scenario.setdefault((s["application"]["name"], s["scenario"]["name"]), []).append(s)
    cards = []
    for (app, name), ss in sorted(by_scenario.items(), key=lambda kv: -len(kv[1])):
        if len(ss) < 2:
            continue
        ss = ss[-20:]
        last = _window(ss[-1])
        dots = "".join(f'<span class="dot d-{E(x["verdict"])}" title="{E(x["run_id"])}: '
                       f'{E(VERDICT_TEXT.get(x["verdict"], ""))}"></span>' for x in ss)
        cards.append(
            f'<div class="trend"><div class="trend-h"><b>{E(name)}</b><span class="muted">{E(app)} · '
            f'{len(ss)} runs</span></div><div class="trend-dots">{dots}</div>'
            f'<div class="trend-row"><span class="k">p95</span>'
            f'{sparkline([_window(x)["latency_ms"]["p95"] for x in ss], cls="c-p95")}'
            f'<span class="v">{_ms(last["latency_ms"]["p95"])}</span></div>'
            f'<div class="trend-row"><span class="k">req/s</span>'
            f'{sparkline([_window(x)["throughput_rps"] for x in ss], cls="c-ok")}'
            f'<span class="v">{fmt_num(last["throughput_rps"])}</span></div></div>')
        if len(cards) == 12:
            break

    rows = []
    results = 0
    for run in order:
        items = runs[run]
        results += len(items)
        first = items[0][0]
        verdicts: dict[str, int] = {}
        for s, _ in items:
            verdicts[s["verdict"]] = verdicts.get(s["verdict"], 0) + 1
        rows.append(f'<tr class="run-row"><td colspan="2"><a class="mono strong" href="{E(run)}/index.html">{E(run)}</a>'
                    f'<span class="muted"> · {E(first["application"]["name"])} · {E(_when(first.get("started_at")))}</span></td>'
                    f'<td colspan="5" class="chips">{_chips(verdicts, True)}</td></tr>')
        for s, relative in sorted(items, key=lambda x: x[0].get("started_at") or ""):
            w = _window(s)
            jmeter = (s.get("links") or {}).get("jmeter_report")
            rows.append(f'<tr><td>{_verdict(s["verdict"], True)}</td>'
                        f'<td><a href="{E(run)}/index.html#{E(relative.split("/")[-1])}">{E(s["scenario"]["name"])}</a></td>'
                        f'<td class="num">{fmt_num(w["throughput_rps"], "req/s")}</td>'
                        f'<td class="num">{_ms(w["latency_ms"]["p95"])}</td>'
                        f'<td class="num">{_ms(w["latency_ms"]["p99"])}</td>'
                        f'<td class="num">{pct(w["server_error_rate"])}</td>'
                        f'<td>{(f"<a href={chr(34)}{E(relative)}/{E(jmeter)}{chr(34)}>JMeter</a>" if jmeter else "")}</td></tr>')
    table = ('<div class="table-wrap history"><table><thead><tr><th></th><th>Scenario</th><th class="num">Throughput</th>'
             '<th class="num">p95</th><th class="num">p99</th><th class="num">Server errors</th><th></th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>') if rows else '<p class="muted">No results yet.</p>'
    meta = [E(_count(len(order), "run", "runs")), E(_count(results, "scenario result", "scenario results"))]
    body = (_header("lab", "Test history", meta)
            + (f'<h2>Trends</h2><div class="trends">{"".join(cards)}</div>' if cards else "") + "<h2>Runs</h2>" + table)
    (results_dir / "index.html").write_text(_page("MORFEO · Test history", body), encoding="utf-8")


# ======================================================================== comparison
def write_compare(path: Path, summaries: list[dict], names: list[str]):
    slots = ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"]

    def capacity_of(s):
        cap = s.get("capacity") or {}
        sus = cap.get("sustainable") if cap.get("available") else None
        return sus["goodput_rps"] if sus else None

    def noise(key):
        return lambda s: ((s.get("noise_windows") or {}).get(key) or [])

    metrics = [
        ("Successful requests/s", lambda s: _window(s)["goodput_rps"], "req/s", +1, noise("goodput_rps")),
        ("All requests/s", lambda s: _window(s)["throughput_rps"], "req/s", 0),
        ("p50", lambda s: _window(s)["latency_ms"]["p50"], "ms", -1),
        ("p95", lambda s: _window(s)["latency_ms"]["p95"], "ms", -1, noise("p95_ms")),
        ("p99", lambda s: _window(s)["latency_ms"]["p99"], "ms", -1),
        ("max", lambda s: _window(s)["latency_ms"]["max"], "ms", 0),   # a single sample is never a trend
        ("Server errors", lambda s: _window(s)["server_error_rate"] * 100, "%", -1),
        ("Capacity within the objectives (req/s)", capacity_of, "req/s", +1),
        ("Peak application CPU (cores)", lambda s: (s.get("server") or {}).get("cpu_cores_max"), "", -1),
        ("Peak heap", lambda s: (s.get("server") or {}).get("heap_max_used_bytes"), "bytes", -1),
    ]
    head = [("Metric", "")] + [(n + (" (baseline)" if i == 0 else ""), "num") for i, n in enumerate(names)]
    rows = []
    for title, fn, unit, better, *noise_fn in metrics:
        values = [fn(s) for s in summaries]
        if all(v is None for v in values):
            continue
        row = [E(title)]
        base = values[0]
        for i, v in enumerate(values):
            cell = fmt_num(v, unit)
            if i and v is not None and base is not None and better:
                # a difference that the variation between windows of both runs explains is not a change
                p = welch(noise_fn[0](summaries[0]), noise_fn[0](summaries[i])) if noise_fn else None

                def cls(d, p=p, better=better):
                    return "delta-same" if p is not None and p >= 0.05 else "delta-good" if d * better > 0 else "delta-bad"
                if unit == "%":
                    d = v - base
                    if abs(d) >= 0.05:
                        cell += f' <span class="{cls(d)}">{d:+.2f} pp</span>'
                elif base != 0:
                    d = (v - base) / abs(base) * 100
                    if abs(d) >= 2:
                        cell += f' <span class="{cls(d)}">{d:+.0f}%</span>'
            row.append(cell)
        rows.append(row)
    verdicts = ["Verdict"] + [_verdict(s["verdict"], True) for s in summaries]
    keys = sorted({e["key"] for s in summaries for e in s["endpoints"]})[:15]

    def p95_of(s, key):
        return next((e["latency_ms"]["p95"] for e in s["endpoints"] if e["key"] == key), None)
    groups = [{"name": n, "slot": slots[i % 8], "values": [p95_of(s, k) for k in keys]}
              for i, (n, s) in enumerate(zip(names, summaries))]
    scenario = summaries[0]["scenario"].get("title") or summaries[0]["scenario"]["name"]
    legend = ('<p class="legend-line"><span class="delta-good">green</span> better · '
              '<span class="delta-bad">red</span> worse · <span class="delta-same">grey</span> within the '
              "variation between the 10-second windows of both runs (Welch's t-test, p ≥ 0.05)</p>")
    body = (_header("comparison", " vs ".join(names), [f"Scenario <b>{E(scenario)}</b>", "the first column is the baseline"])
            + "<h2>Key figures</h2>" + _table(head, [verdicts, *rows]) + legend + "<h2>p95 latency by endpoint</h2>"
            + (f'<div class="charts one">{grouped_hbar(keys, groups, unit="ms", width=1120)}</div>' if keys else ""))
    path.write_text(_page("MORFEO · Comparison", body), encoding="utf-8")
