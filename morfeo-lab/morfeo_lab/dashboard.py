"""The Grafana dashboard of the lab, built in code (nothing generated is kept in the repository).

Conventions:
  * one colour per meaning: successful = green, 4xx = blue, 5xx = red, no response = purple, failed assertion =
    orange;
  * a short dashboard for now: load, mean response time, errors, endpoints, the CPU and memory of the
    application and the load generators (collapsed). Percentiles, JVM, pools and database panels were left out
    on purpose; the run report still has the percentiles and every criterion;
  * limits and objectives are red dashed lines; the requested load is a dashed line in the colour of what it requests;
  * the 'now' figures are instant queries that only return a value while a scenario runs; the 'time range' figures
    summarise whatever the time picker selects (a live window or a past run);
  * only the load stage of a plan is drawn: the requests of its setUp (for example creating a pool of users) have
    their own stage label and never mix with the measured load;
  * scenario periods and alerts are drawn on the timeline at the top. Region annotations are not used for them:
    Grafana splits them into separate marks on wide time ranges.
"""
from __future__ import annotations

import json

from .units import parse_cpu, parse_memory

DS = {"type": "prometheus", "uid": "morfeo-prom"}
S = 'run=~"$run",scenario=~"$scenario"'                  # selected runs and scenarios
E = f'{S},stage="main",endpoint=~"$endpoint"'          # ... of the load stage, and endpoints
FAIL = 'result=~"server_5xx|network|assertion"'
CAD = "1m"                  # cAdvisor samples every 10-15 s: rate window for container metrics
NOW = "30s"                 # window of the "now" figures
RANGE = "$__range"
RUNNING = f"max(morfeo_generator_state{{{S}}}) == 1"
ALERTS = 'ALERTS{alertstate="firing",alertname=~"Morfeo.*"}'

C_OK, C_4XX, C_5XX, C_NET, C_ASSERT = "green", "blue", "red", "purple", "orange"
C_LIMIT = "red"
C_USED, C_MEM = "blue", "purple"


# ------------------------------------------------------------------ queries
def rate_req(sel=E, extra="", window="$__rate_interval"):
    return f"sum(rate(morfeo_requests_total{{{sel}{extra}}}[{window}]))"


def mean_time(by="", window="$__rate_interval"):
    grp = f" by ({by})" if by else ""
    return (f"sum{grp} (rate(morfeo_response_time_seconds_sum{{{E}}}[{window}])) / "
            f"clamp_min(sum{grp} (rate(morfeo_response_time_seconds_count{{{E}}}[{window}])), 1e-9)")


def ratio(num_sel, den_sel=E, window="$__rate_interval", fn="rate", by=""):
    grp = f" by ({by})" if by else ""
    den = f"sum{grp} ({fn}(morfeo_requests_total{{{den_sel}}}[{window}]))"
    return f"(sum{grp} ({fn}(morfeo_requests_total{{{num_sel}}}[{window}])) or {den} * 0) / clamp_min({den}, 1e-9)"


def running(expr):
    """Only while a selected scenario is running: otherwise no value."""
    return f"({expr}) and on() ({RUNNING})"


# ------------------------------------------------------------------ panel builders
class Layout:
    def __init__(self):
        self.y, self.panels, self.next_id = 0, [], 1
        self._row = None

    def _id(self):
        self.next_id += 1
        return self.next_id - 1

    def row(self, title, collapsed=False):
        if self._row and self._row["collapsed"]:   # a collapsed row takes one line of the grid
            self.y = self._row["gridPos"]["y"] + 1
        self._row = {"type": "row", "id": self._id(), "title": title, "collapsed": collapsed,
                     "gridPos": {"h": 1, "w": 24, "x": 0, "y": self.y}, "panels": []}
        self.panels.append(self._row)
        self.y += 1

    def line(self, panels, h):
        """Panels side by side; their widths add up to 24."""
        assert sum(p["_w"] for p in panels) == 24, [p["title"] for p in panels]
        x = 0
        for p in panels:
            p["id"] = self._id()
            p["gridPos"] = {"h": h, "w": p.pop("_w"), "x": x, "y": self.y}
            x += p["gridPos"]["w"]
            # panels of a collapsed row live inside the row
            (self._row["panels"] if self._row and self._row["collapsed"] else self.panels).append(p)
        self.y += h


def target(expr, legend="", ref="A", instant=False, fmt=None):
    t = {"datasource": DS, "expr": expr, "refId": ref, "legendFormat": legend or "__auto",
         "range": not instant, "instant": instant}
    if fmt:
        t["format"] = fmt
    return t


def thresholds(*steps):
    """thresholds("green", (0.01, "orange"), (0.05, "red"))"""
    return [{"color": steps[0], "value": None}] + [{"color": c, "value": v} for v, c in steps[1:]]


def stat(title, expr, unit, w=4, decimals=None, steps=None, color="text", no_value="–"):
    """Single value (instant query). `steps` colours the value by thresholds."""
    fc = {"defaults": {"unit": unit, "noValue": no_value,
                       "color": {"mode": "thresholds"} if steps else {"mode": "fixed", "fixedColor": color},
                       "thresholds": {"mode": "absolute", "steps": steps or thresholds(color)}},
          "overrides": []}
    if decimals is not None:
        fc["defaults"]["decimals"] = decimals
    return {"_w": w, "type": "stat", "title": title, "datasource": DS,
            "targets": [target(expr, instant=True)], "fieldConfig": fc,
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "value", "graphMode": "none", "justifyMode": "center",
                        "textMode": "value", "wideLayout": True, "showPercentChange": False,
                        "orientation": "auto"}}


def by_name(name, *props):
    return {"matcher": {"id": "byName", "options": name}, "properties": list(props)}


def prop(pid, value):
    return {"id": pid, "value": value}


def fixed(color):
    return prop("color", {"mode": "fixed", "fixedColor": color})


def colored(name, color, *extra):
    return by_name(name, fixed(color), *extra)


def dashed(name, color=C_LIMIT):
    return by_name(name, fixed(color), prop("custom.lineStyle", {"fill": "dash", "dash": [10, 6]}),
                   prop("custom.fillOpacity", 0), prop("custom.lineWidth", 2),
                   prop("custom.lineInterpolation", "stepAfter"))


def legend_only(name):
    """In the legend (with its value) but not drawn, so it does not stretch the axis."""
    return by_name(name, prop("custom.hideFrom", {"legend": False, "tooltip": False, "viz": True}),
                   prop("custom.stacking", {"mode": "none", "group": "limit"}), fixed(C_LIMIT))


def right_axis(name, unit, label=""):
    return by_name(name, prop("custom.axisPlacement", "right"), prop("unit", unit),
                   *([prop("custom.axisLabel", label)] if label else []))


def ts(title, targets, unit, w=12, overrides=None, stack=False, legend_calcs=None, decimals=None,
       axis_label="", soft_max=None, no_value=None, fill=10, threshold_line=None, percent_stack=False):
    """Time series. Legends with `legend_calcs` become a table (mean / max / last...)."""
    custom = {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 60 if stack else fill,
              "gradientMode": "none" if stack else "opacity", "showPoints": "never",
              "spanNulls": 60000,   # joins irregular samples (< 1 min apart); separate runs stay apart
              "lineInterpolation": "linear" if stack else "smooth",
              "stacking": {"mode": ("percent" if percent_stack else "normal") if stack else "none", "group": "A"},
              "axisSoftMin": 0, "axisLabel": axis_label, "axisBorderShow": False, "axisCenteredZero": False,
              "barAlignment": 0, "insertNulls": False, "pointSize": 4,
              "hideFrom": {"legend": False, "tooltip": False, "viz": False},
              "scaleDistribution": {"type": "linear"}, "thresholdsStyle": {"mode": "off"}}
    if stack:
        custom["lineWidth"] = 1
    if soft_max is not None:   # an all-zero percentage would otherwise get a 0-10000 % axis
        custom["axisSoftMax"] = soft_max
    defaults = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"},
                "thresholds": {"mode": "absolute", "steps": thresholds("transparent")}}
    if threshold_line is not None:
        custom["thresholdsStyle"] = {"mode": "dashed"}
        defaults["thresholds"]["steps"] = thresholds("transparent", (threshold_line, C_LIMIT))
    if decimals is not None:
        defaults["decimals"] = decimals
    if no_value:
        defaults["noValue"] = no_value
    legend = ({"displayMode": "table", "placement": "bottom", "calcs": legend_calcs, "showLegend": True}
              if legend_calcs else {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": []})
    return {"_w": w, "type": "timeseries", "title": title, "datasource": DS, "targets": targets,
            "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
            "options": {"legend": legend, "tooltip": {"mode": "multi", "sort": "desc"}}}


def timeline(w=24):
    """Scenario periods and firing alerts, one row each."""
    def rows(prefix, color, text):
        return {"matcher": {"id": "byRegexp", "options": f"^{prefix}.*"},
                "properties": [prop("mappings", [{"type": "value",
                                                  "options": {"1": {"text": text, "color": color, "index": 0}}}])]}
    sub = "[$__interval:]"
    return {
        "_w": w, "type": "state-timeline", "title": "Timeline", "datasource": DS,
        "targets": [
            target(f"max by (scenario) (max_over_time((morfeo_generator_state{{{S}}} == 1){sub}))", "{{scenario}}", "A"),
            target(f"max by (alertname) (max_over_time({ALERTS}{sub}))", "alert · {{alertname}}", "C"),
        ],
        "fieldConfig": {
            "defaults": {"custom": {"fillOpacity": 85, "lineWidth": 0, "insertNulls": False, "spanNulls": False,
                                    "hideFrom": {"legend": False, "tooltip": False, "viz": False}},
                         "color": {"mode": "thresholds"},
                         "thresholds": {"mode": "absolute", "steps": thresholds("transparent")},
                         "mappings": [{"type": "value",
                                       "options": {"1": {"text": "running", "color": C_OK, "index": 0}}}],
                         "noValue": "No test in this time range"},
            "overrides": [rows("alert · ", C_5XX, "firing")]},
        "options": {"mergeValues": True, "showValue": "never", "alignValue": "left", "rowHeight": 0.8,
                    "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "single", "sort": "none"}},
    }


# ------------------------------------------------------------------ the dashboard
def build(ns_app: str, ns_generators: str, prometheus_url: str, generator_cpu: float, generator_memory: int) -> dict:
    APP = f'namespace="{ns_app}",container="app"'
    GEN = f'namespace="{ns_generators}",container="generator"'
    L = Layout()
    err_steps = thresholds("green", (0.01, "orange"), (0.05, "red"))
    text_err_steps = thresholds("text", (0.01, "orange"), (0.05, "red"))

    # ============ Overview
    L.row("Overview")
    L.line([timeline()], h=6)
    L.line([
        stat("Users now", running(f"sum(morfeo_active_threads{{{S}}})"), "short", w=6, decimals=0, color="blue"),
        stat("Requests/s now", running(rate_req(window=NOW)), "reqps", w=6, decimals=1, color=C_OK),
        stat("Mean response time now", running(mean_time(window=NOW)), "s", w=6, color="blue"),
        stat("Server errors now", running(ratio(f"{E},{FAIL}", window=NOW)), "percentunit", w=6, decimals=2,
             steps=err_steps),
    ], h=4)
    L.line([
        stat("Requests in time range", f"sum(increase(morfeo_requests_total{{{E}}}[{RANGE}]))", "short",
             w=6, decimals=0, no_value="0"),
        stat("Mean requests/s in time range",
             f"sum(increase(morfeo_requests_total{{{E}}}[{RANGE}])) / $__range_s", "reqps", w=6, decimals=1),
        stat("Server errors in time range", ratio(f"{E},{FAIL}", window=RANGE, fn="increase"), "percentunit",
             w=6, decimals=2, steps=text_err_steps),
        stat("4xx in time range", ratio(f'{E},result="client_4xx"', window=RANGE, fn="increase"),
             "percentunit", w=6, decimals=2),
    ], h=3)

    # ============ Load
    L.row("Load")
    L.line([
        ts("Requested and actual load",
           [target(f"sum(morfeo_active_threads{{{S}}})", "users", "A"),
            target(f"sum(morfeo_planned_users{{{S}}}) > 0", "requested users", "B"),
            target(rate_req(), "requests/s", "C"),
            target(f"sum(morfeo_planned_rps{{{S}}}) > 0", "requested requests/s", "D"),
            target(f"sum(morfeo_planned_arrivals{{{S}}}) > 0", "requested arrivals/s", "E")],
           "short", axis_label="users", legend_calcs=["max"], decimals=0,
           overrides=[colored("users", "blue"), dashed("requested users", "blue"),
                      colored("requests/s", C_OK), dashed("requested requests/s", C_OK),
                      dashed("requested arrivals/s", C_OK),
                      right_axis("requests/s", "reqps", "requests/s"),
                      right_axis("requested requests/s", "reqps", "requests/s"),
                      right_axis("requested arrivals/s", "reqps", "requests/s")]),
        ts("Requests per second by result",
           [target(rate_req(extra=',result="ok"'), "successful", "A"),
            target(rate_req(extra=',result="client_4xx"'), "4xx", "B"),
            target(rate_req(extra=',result="server_5xx"'), "5xx", "C"),
            target(rate_req(extra=',result="network"'), "no response", "D"),
            target(rate_req(extra=',result="assertion"'), "failed assertion", "E")],
           "reqps", stack=True, legend_calcs=["mean", "max"], decimals=1,
           overrides=[colored("successful", C_OK), colored("4xx", C_4XX), colored("5xx", C_5XX),
                      colored("no response", C_NET), colored("failed assertion", C_ASSERT)]),
    ], h=9)

    # ============ Latency
    L.row("Latency")
    L.line([
        ts("Mean response time",
           [target(mean_time(), "all requests", "A")],
           "s", w=12, legend_calcs=["mean", "max"], overrides=[colored("all requests", "blue")]),
        ts("Mean response time by endpoint",
           [target(mean_time(by="endpoint"), "{{endpoint}}", "A")],
           "s", w=12, legend_calcs=["mean", "max"], fill=0),
    ], h=9)

    # ============ Errors
    L.row("Errors")
    L.line([
        ts("Server error rate",
           [target(ratio(f"{E},{FAIL}"), "server errors", "A"),
            target(f"max(morfeo_slo_error_ratio{{{S}}})", "SLO", "B")],
           "percentunit", w=12, decimals=2, legend_calcs=["mean", "max"], soft_max=0.02,
           overrides=[colored("server errors", C_5XX), dashed("SLO")]),
        ts("Responses by HTTP status",
           [target(f"sum by (code) (rate(morfeo_requests_total{{{E}}}[$__rate_interval]))", "{{code}}", "A")],
           "reqps", w=12, stack=True, legend_calcs=["mean"], decimals=1,
           overrides=[{"matcher": {"id": "byRegexp", "options": "^2\\d\\d$"}, "properties": [fixed(C_OK)]},
                      {"matcher": {"id": "byRegexp", "options": "^4\\d\\d$"}, "properties": [fixed(C_4XX)]},
                      {"matcher": {"id": "byRegexp", "options": "^5\\d\\d$"}, "properties": [fixed(C_5XX)]},
                      {"matcher": {"id": "byRegexp", "options": "^\\D.*$"}, "properties": [fixed(C_NET)]}]),
    ], h=8)

    # ============ Endpoints
    L.row("Endpoints")
    ep = "endpoint"
    L.line([{
        "_w": 24, "type": "table", "title": "Endpoints in time range", "datasource": DS,
        "targets": [
            target(f"sum by ({ep}) (increase(morfeo_requests_total{{{E}}}[{RANGE}]))", "", "A", True, "table"),
            target(f"sum by ({ep}) (increase(morfeo_requests_total{{{E}}}[{RANGE}])) / $__range_s", "", "B", True,
                   "table"),
            target(f"sum by ({ep}) (increase(morfeo_response_time_seconds_sum{{{E}}}[{RANGE}])) / clamp_min("
                   f"sum by ({ep}) (increase(morfeo_response_time_seconds_count{{{E}}}[{RANGE}])), 1e-9)",
                   "", "H", True, "table"),
            target(ratio(f"{E},{FAIL}", window=RANGE, fn="increase", by=ep), "", "F", True, "table"),
            target(ratio(f'{E},result="client_4xx"', window=RANGE, fn="increase", by=ep), "", "G", True, "table"),
        ],
        "transformations": [
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True},
                "indexByName": {ep: 0, "Value #A": 1, "Value #B": 2, "Value #H": 3, "Value #F": 4, "Value #G": 5},
                "renameByName": {ep: "Endpoint", "Value #A": "Requests", "Value #B": "Requests/s",
                                 "Value #H": "Mean response time", "Value #F": "Server errors", "Value #G": "4xx"}}},
        ],
        "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False},
                                     "noValue": "–"},
                        "overrides": [
                            by_name("Requests", prop("unit", "short"), prop("decimals", 0)),
                            by_name("Requests/s", prop("unit", "reqps"), prop("decimals", 2)),
                            by_name("Mean response time", prop("unit", "s")),
                            by_name("4xx", prop("unit", "percentunit"), prop("decimals", 2)),
                            by_name("Server errors", prop("unit", "percentunit"), prop("decimals", 2),
                                    prop("custom.cellOptions", {"type": "color-text"}),
                                    prop("thresholds", {"mode": "absolute", "steps": text_err_steps})),
                            by_name("Endpoint", prop("custom.width", 340))]},
        "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "Requests", "desc": True}],
                    "footer": {"show": False, "reducer": ["sum"], "countRows": False}},
    }], h=7)
    L.line([
        ts("Requests per second by endpoint",
           [target(f"sum by (endpoint) (rate(morfeo_requests_total{{{E}}}[$__rate_interval]))", "{{endpoint}}",
                   "A")],
           "reqps", w=24, stack=True, legend_calcs=["mean", "max"], decimals=1),
    ], h=8)

    # ============ Application
    L.row("Application")
    L.line([
        ts("CPU",
           [target(f"sum(rate(container_cpu_usage_seconds_total{{{APP}}}[{CAD}]))", "used", "A"),
            target(f"sum(container_spec_cpu_quota{{{APP}}}) / 100000", "limit", "B")],
           "short", w=12, decimals=2, axis_label="cores", legend_calcs=["mean", "max"],
           overrides=[colored("used", C_USED), dashed("limit")]),
        ts("Container memory",
           [target(f"sum(container_memory_working_set_bytes{{{APP}}})", "used", "A"),
            target(f"sum(container_spec_memory_limit_bytes{{{APP}}})", "limit", "B")],
           "bytes", w=12, legend_calcs=["max"], overrides=[colored("used", C_MEM), dashed("limit")]),
    ], h=8)

    # ============ Load generators (collapsed)
    L.row("Load generators", collapsed=True)
    L.line([
        ts("CPU per generator",
           [target(f"sum by (pod) (rate(container_cpu_usage_seconds_total{{{GEN}}}[{CAD}]))", "{{pod}}", "A"),
            target(f"vector({generator_cpu:g})", "limit", "B")],
           "short", w=8, decimals=2, axis_label="cores", legend_calcs=["max"], fill=0,
           overrides=[dashed("limit")]),
        ts("Memory per generator",
           [target(f"sum by (pod) (container_memory_working_set_bytes{{{GEN}}})", "{{pod}}", "A"),
            target(f"vector({generator_memory})", "limit", "B")],
           "bytes", w=8, legend_calcs=["max"], fill=0, overrides=[dashed("limit")]),
        ts("Users per generator",
           [target(f"sum by (generator) (morfeo_active_threads{{{S}}})", "generator {{generator}}", "A")],
           "short", w=8, decimals=0, legend_calcs=["max"], fill=0),
    ], h=7)

    def var(name, label, query):
        return {"name": name, "label": label, "type": "query", "datasource": DS, "hide": 0,
                "query": query, "definition": query,   # classic string query: every Grafana version reads it
                "refresh": 2, "includeAll": True, "multi": True, "allValue": ".*",
                "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
                "sort": 2 if name == "run" else 1, "options": [], "regex": "", "skipUrlSync": False}

    def link(title, url, icon):
        return {"title": title, "type": "link", "icon": icon, "url": url, "targetBlank": True, "tooltip": "",
                "asDropdown": False, "includeVars": False, "keepTime": False, "tags": []}

    return {
        "uid": "morfeo-load-tests",
        "title": "MORFEO · Load tests",
        "tags": ["morfeo", "load-test"],
        "timezone": "browser",
        "weekStart": "monday",
        "editable": True,
        "graphTooltip": 1,
        "fiscalYearStartMonth": 0,
        "liveNow": False,
        "refresh": "5s",
        "schemaVersion": 39,
        "version": 6,
        "time": {"from": "now-15m", "to": "now"},
        "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
        "links": [link("Prometheus", prometheus_url, "external link"),
                  link("Alerts", f"{prometheus_url}/alerts", "bolt")],
        "annotations": {"list": [
            {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
             "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
        ]},
        "templating": {"list": [
            var("run", "Run", "label_values(morfeo_requests_total, run)"),
            var("scenario", "Scenario", 'label_values(morfeo_requests_total{run=~"$run"}, scenario)'),
            var("endpoint", "Endpoint",
                'label_values(morfeo_requests_total{run=~"$run",scenario=~"$scenario",stage="main"}, endpoint)'),
        ]},
        "panels": L.panels,
    }


def render_all(lab: dict) -> dict[str, str]:
    """The dashboards to install, by file name."""
    gen = lab["generators"]
    ports = lab["cluster"]["ports"]
    dash = build(lab["namespaces"]["app"], lab["namespaces"]["generators"], f"http://localhost:{ports['prometheus']}",
                 parse_cpu(gen["cpu"]), parse_memory(gen["memory"]))
    return {"load-tests.json": json.dumps(dash, indent=2, ensure_ascii=False) + "\n"}


def config_map(lab: dict, dashboards: dict[str, str]) -> str:
    """The ConfigMap Grafana reads its dashboards from."""
    return json.dumps({"apiVersion": "v1", "kind": "ConfigMap",
                       "metadata": {"name": "grafana-dashboards", "namespace": lab["namespaces"]["monitoring"]},
                       "data": dashboards})
