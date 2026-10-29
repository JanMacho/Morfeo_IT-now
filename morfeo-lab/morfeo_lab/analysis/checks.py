"""The acceptance criteria of a run and its verdict.

A check is `pass`, `fail` or `warn`. Failing a criterion of the application (errors, latency, restarts, a regression)
fails the run; a warning is about the measurement itself (the requested load was not reached, a generator was
saturated, the traffic log is incomplete) and does not.
"""
from __future__ import annotations

from ..units import ms, num


def pct(rate: float, decimals: int = 3) -> str:
    return f"{num(rate * 100, decimals)}%"


def slo_matches(name: str, label: str, key: str | None) -> bool:
    """An objective of the application file names an endpoint by its label ('/api/x/{id}') or by its key
    ('GET /api/x/{id}')."""
    key = key or label
    return name in (label, key) or name == key.split(" ", 1)[-1]


def endpoint_slo(slo: dict, label: str, key: str) -> dict:
    endpoints = slo.get("endpoints") or {}
    return next((v for k, v in endpoints.items() if k == key), None) or next(
        (v for k, v in endpoints.items() if slo_matches(k, label, key)), {})


def verdict_of(checks: list[dict]) -> str:
    if any(c["status"] == "fail" for c in checks):
        return "FAIL"
    if any(c["status"] == "warn" for c in checks):
        return "PASS_WITH_WARNINGS"
    return "PASS"


def evaluate(*, overall: dict, steady: dict, endpoints: list[dict], slo: dict, pods: dict, setup: dict, server: dict,
             exit_codes: list[int], scenario: dict, traffic_log: dict | None, baseline: dict | None) -> list[dict]:
    out: list[dict] = []

    def add(name, status, target, actual, detail=""):
        out.append({"name": name, "status": status, "target": target, "actual": actual, "detail": detail})

    window = steady if steady["requests"] else overall
    if overall["requests"] == 0:
        add("Samples recorded", "fail", "> 0", "0", "the load generators recorded no requests of the load stage")
        return out
    if slo.get("error_rate_pct") is not None:
        add("Server errors", "pass" if window["server_error_rate"] * 100 <= slo["error_rate_pct"] else "fail",
            f"≤ {num(slo['error_rate_pct'], 2)}%", pct(window["server_error_rate"]))
    for key, label in (("p95_ms", "p95"), ("p99_ms", "p99")):
        if slo.get(key) is not None:
            actual = window["latency_ms"][label]
            add(f"{label} latency", "pass" if actual is not None and actual <= slo[key] else "fail",
                f"≤ {ms(slo[key])}", ms(actual))
    if slo.get("min_throughput_rps") is not None:
        actual = window["goodput_rps"]
        add("Successful requests per second", "pass" if actual >= slo["min_throughput_rps"] else "fail",
            f"≥ {num(slo['min_throughput_rps'], 1)} req/s", f"{num(actual, 1)} req/s")
    for name, objective in (slo.get("endpoints") or {}).items():
        matched = [e for e in endpoints if slo_matches(name, e["label"], e.get("key"))]
        if not matched:
            add(name, "warn", "receives traffic", "no requests",
                "the application file has an objective for this endpoint but the plan does not call it")
            continue
        for e in matched:
            _endpoint_checks(add, e["key"] if len(matched) > 1 else name, objective, e)
    restarts = max(0, pods.get("restarts_during_run", 0))
    add("No application restarts", "pass" if not restarts else "fail", "0", str(restarts),
        (pods.get("last_termination_reason") or "") if restarts else "")
    if baseline and baseline.get("steady", {}).get("requests"):
        tolerance, base = baseline.get("tolerance") or {}, baseline["steady"]
        for label in ("p95", "p99"):
            before, now = base["latency_ms"].get(label), window["latency_ms"].get(label)
            if before is None or now is None:
                continue
            limit = before * (1 + tolerance.get("latency_pct", 15) / 100)
            add(f"{label} latency vs baseline", "pass" if now <= limit else "fail",
                f"≤ {ms(limit)} ({baseline.get('run_id', 'baseline')})", ms(now))
        limit = base["server_error_rate"] + tolerance.get("error_rate_pp", 0.5) / 100
        add("Server errors vs baseline", "pass" if window["server_error_rate"] <= limit else "fail",
            f"≤ {pct(limit)}", pct(window["server_error_rate"]))
    if setup["requests"]:
        add("setUp completed", "pass" if not setup["failed_requests"] else "fail", "no failed requests",
            f"{setup['failed_requests']} of {setup['requests']} failed")
    log = traffic_log or {}
    if log.get("available") and log.get("completeness") is not None:
        c = log["completeness"]
        add("Traffic log complete", "pass" if c >= 0.98 else "warn", "≥ 98% of the answered requests", pct(c, 1),
            "" if c >= 0.98 else "the traffic log of the application is missing requests")
    target = scenario.get("target_rps")
    if target:
        actual = window["throughput_rps"]
        add("Requested load reached", "pass" if actual >= 0.95 * target else "warn", f"≈ {num(target)} req/s",
            f"{num(actual, 1)} req/s")
    cpu, cpu_limit = server.get("generator_cpu_cores_max"), server.get("generator_cpu_limit_cores")
    if cpu is not None and cpu_limit:
        add("Load generator CPU", "pass" if cpu < 0.9 * cpu_limit else "warn", f"< 90% of {num(cpu_limit, 2)} cores",
            f"{cpu / cpu_limit * 100:.0f}%",
            "" if cpu < 0.9 * cpu_limit else "a saturated generator understates the application")
    if any(c != 0 for c in exit_codes):
        add("Load generator exit code", "warn", "0", ",".join(map(str, exit_codes)),
            "JMeter did not finish normally: see raw/generator-*/jmeter.log")
    return out


def _endpoint_checks(add, name: str, objective: dict, e: dict):
    for key, label in (("p95_ms", "p95"), ("p99_ms", "p99")):
        if objective.get(key) is not None:
            actual = e["latency_ms"][label]
            add(f"{name} {label} latency", "pass" if actual is not None and actual <= objective[key] else "fail",
                f"≤ {ms(objective[key])}", ms(actual))
    if objective.get("error_rate_pct") is not None:
        add(f"{name} errors", "pass" if e["server_error_rate"] * 100 <= objective["error_rate_pct"] else "fail",
            f"≤ {num(objective['error_rate_pct'], 2)}%", pct(e["server_error_rate"]))
