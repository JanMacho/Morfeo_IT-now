"""A short diagnosis in plain words of WHERE the application saturates.

The analysis emits findings with a stable `code` and parameters (for scripts and for the AI team), and the texts are
written here, so summary.json carries both the code and the sentence a person reads.
"""
from __future__ import annotations

import re

from ..units import ms, num

SEVERITIES = ("critical", "serious", "warning", "info", "good")

TEXTS = {
    "crash": ("The application restarted during the test",
              "{restarts} container restart(s); last reason: {reason}. {extra}"),
    "app_unreachable": ("The load generators could not reach the application",
                        "{count} requests failed with: {message}. Check that the application is running and that its "
                        "port is the one in the application file."),
    "setup_failed": ("The setUp of the plan failed",
                     "{count} of the {total} requests of its setUp thread groups failed. The data the plan needs "
                     "(users, tokens...) may not exist, which would distort the whole test."),
    "http_503": ("{count} HTTP 503 responses: the application rejected requests",
                 "The application answered 'Service Unavailable'. In applications with a database this usually means "
                 "that no pool connection became free in time.{knobs}"),
    "http_5xx": ("{count} HTTP 5xx responses (internal server error)",
                 "Codes: {codes}. The output of the application is in app/*.stdout.log."),
    "no_answer": ("{count} requests received no HTTP response",
                  "Timeouts and refused or reset connections: the application stopped accepting connections (full "
                  "accept queue, a crash or a GC pause). The messages are in the error table."),
    "cpu_throttled": ("The application reached its CPU limit",
                      "Up to {ratio} of the CPU periods were throttled (limit: {limit}). More CPU or lighter requests "
                      "would raise its capacity."),
    "cpu_high": ("The CPU of the application is close to its limit", "Peak of {peak} out of {limit}."),
    "web_threads": ("The web thread pool is exhausted",
                    "{busy} of {max} worker threads were busy: new requests waited in the accept queue.{knobs}"),
    "db_waiting": ("Requests waited for a database connection",
                   "Up to {waiting} threads waited for a connection of the pool (peak in use: {active}).{knobs}"),
    "db_cpu": ("The database reached its CPU limit",
               "PostgreSQL used up to {peak} of {limit}: the bottleneck may be the database."),
    "db_connections": ("PostgreSQL is close to its connection limit",
                       "Up to {used} of {max} connections were open: more replicas or a larger pool would fail to "
                       "connect. Raise max_connections (dependencies.postgres.args) or reduce the pool of each replica."),
    "gc": ("Garbage collection pressure",
           "The JVM spent up to {ratio} of its time in GC: the heap is too small or the code allocates heavily."),
    "heap": ("The heap is almost full", "The heap used more than 90% of its maximum."),
    "target_missed": ("The requested load was not reached", "{target} requested, {actual} achieved. {why}"),
    "target_missed_app": "The application did not answer fast enough (see the latency).",
    "target_missed_generator": "The application was not saturated: the plan needs more users or generators.",
    "open_limit": ("The generators reached the thread limit of the plan ({limit})",
                   "In the open model every slow request holds a thread. At the limit arrivals are delayed and the "
                   "actual load is lower than requested: the application could not sustain that rate."),
    "generator_cpu": ("The load generator was saturated",
                      "A generator used {ratio} of its CPU, so the figures understate the application. {advice}"),
    "generator_memory": ("The load generator was short of memory",
                         "A generator used {ratio} of its memory. {advice}"),
    "advice_split": "Run the scenario again with more generators (--generators).",
    "advice_single": "The lab cannot divide this plan between generators: make the plan divide its own load using "
                     "the morfeo_generators property (docs/SCENARIOS.md), or give the generators more CPU "
                     "(generators.cpu in config/lab.yaml).",
    "knee": ("Throughput stops growing at {level}",
             "Beyond this level extra load only lengthens the queues: p95 {p95}, {goodput} successful."),
    "log_incomplete": ("The traffic log is incomplete",
                       "The application logged {logged} requests out of {answered} answered ({pct}). The log returned "
                       "to the AI team understates the traffic; under load the logger of the application may be "
                       "dropping lines."),
    "queue_outside": ("Requests wait before the application processes them",
                      "{endpoint}: p95 {outside} outside the application against {inside} inside it (traffic log). The "
                      "time goes into the queue of the web server (busy worker threads, accept queue) or the "
                      "network.{knobs}"),
    "profile_hotspot": ("Largest share of the CPU of the application: {category} ({pct})",
                        "Hottest method: {method} ({method_pct} of the samples). The flame graph shows the full "
                        "call paths."),
    "no_signal": ("No signs of saturation",
                  "The application handled this load with no server errors, no CPU throttling and no exhausted pools."),
}

# Variables of the application file that usually control each kind of saturation (shown only when it sets them)
KNOB_VARIABLES = {
    "web": ("MAX_THREADS", "ACCEPT_COUNT", "SERVER_TOMCAT_THREADS_MAX", "SERVER_TOMCAT_ACCEPT_COUNT"),
    "db": ("DB_POOL_SIZE", "DB_TIMEOUT_MS", "SPRING_DATASOURCE_HIKARI_MAXIMUM_POOL_SIZE",
           "SPRING_DATASOURCE_HIKARI_CONNECTION_TIMEOUT"),
}
UNREACHABLE = re.compile(r"connection refused|unknownhost|name or service not known|no route to host|"
                         r"connect to .* failed|failed to resolve", re.I)


class _Params(dict):
    def __missing__(self, key):   # a missing parameter must never show '{name}' to a reader
        return ""


def text(code: str, **params) -> tuple[str, str]:
    entry = TEXTS[code]
    title, detail = entry if isinstance(entry, tuple) else (entry, "")
    p = _Params(params)
    return title.format_map(p), detail.format_map(p)


def knobs(kind: str, env_keys) -> str:
    found = [v for v in KNOB_VARIABLES[kind] if v in set(env_keys or ())]
    return f" Related settings in the application file: {', '.join(found)}." if found else ""


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _cores(v) -> str:
    return f"{num(v, 2)} core" if v == 1 else f"{num(v, 2)} cores"


def diagnose(*, overall: dict, steady: dict, setup: dict, server: dict, pods: dict, capacity: dict, scenario: dict,
             errors: list[dict], traffic_log: dict | None, profile: dict | None, slo: dict, env_keys,
             splittable: bool) -> list[dict]:
    """Findings ordered by severity. `splittable` tells whether the lab can divide the plan between generators."""
    out: list[dict] = []

    def add(severity, code, **params):
        title, detail = text(code, **params)
        out.append({"severity": severity, "code": code, "title": title, "detail": detail,
                    "params": {k: v for k, v in params.items() if isinstance(v, (int, float, str))}})

    restarts = pods.get("restarts_during_run", 0)
    if restarts > 0:
        reason = pods.get("last_termination_reason") or "?"
        add("critical", "crash", restarts=restarts, reason=reason,
            extra="The JVM exceeded the memory limit of the container (OOMKilled): raise the limit or look for a leak."
            if reason == "OOMKilled" else "The output of the application is in the app/ folder of the scenario.")
    total = overall["requests"]
    by = overall["by_class"]
    unreachable = [e for e in errors if e["class"] == "network" and UNREACHABLE.search(e["message"])]
    if unreachable and sum(e["count"] for e in unreachable) > 0.5 * max(total, 1):
        add("critical", "app_unreachable", count=num(sum(e["count"] for e in unreachable)),
            message=unreachable[0]["message"][:160])
    if setup["requests"] and setup["failed_requests"]:
        add("serious", "setup_failed", count=num(setup["failed_requests"]), total=num(setup["requests"]))
    codes = overall.get("status_codes", {})
    if codes.get("503"):
        add("serious", "http_503", count=num(codes["503"]), knobs=knobs("db", env_keys))
    other = {k: v for k, v in codes.items() if k.isdigit() and int(k) >= 500 and k != "503"}
    if other:
        add("serious", "http_5xx", count=num(sum(other.values())), codes=", ".join(f"{k} ×{v}" for k, v in other.items()))
    if by["network"] and by["network"] / max(total, 1) > 0.001 and not unreachable:
        add("serious", "no_answer", count=num(by["network"]))
    limit = server.get("cpu_limit_cores")
    busy = bool(limit and server.get("cpu_cores_max") and server["cpu_cores_max"] >= 0.5 * limit)
    if server.get("cpu_throttled_max") and server["cpu_throttled_max"] > 0.25 and busy:
        add("warning", "cpu_throttled", ratio=_pct(server["cpu_throttled_max"]), limit=_cores(limit) if limit else "?")
    elif limit and server.get("cpu_cores_max") and server["cpu_cores_max"] >= 0.9 * limit:
        add("warning", "cpu_high", peak=num(server["cpu_cores_max"], 2), limit=_cores(limit))
    if server.get("web_threads_busy_max") and server.get("web_threads_limit") and \
            server["web_threads_busy_max"] >= 0.95 * server["web_threads_limit"]:
        add("warning", "web_threads", busy=f"{server['web_threads_busy_max']:.0f}", max=f"{server['web_threads_limit']:.0f}",
            knobs=knobs("web", env_keys))
    if server.get("db_pool_waiting_max") and server["db_pool_waiting_max"] > 0:
        add("warning", "db_waiting", waiting=f"{server['db_pool_waiting_max']:.0f}",
            active=f"{server.get('db_pool_active_max') or 0:.0f}", knobs=knobs("db", env_keys))
    db_limit = server.get("db_cpu_limit_cores")
    if db_limit and server.get("db_cpu_cores_max") and server["db_cpu_cores_max"] >= 0.9 * db_limit:
        add("warning", "db_cpu", peak=num(server["db_cpu_cores_max"], 2), limit=_cores(db_limit))
    used, most = server.get("db_connections_max"), server.get("db_max_connections")
    if used and most and used >= 0.9 * most:
        add("warning", "db_connections", used=f"{used:.0f}", max=f"{most:.0f}")
    if server.get("gc_time_ratio_max") and server["gc_time_ratio_max"] > 0.1:
        add("warning", "gc", ratio=_pct(server["gc_time_ratio_max"]))
    if server.get("heap_max_used_bytes") and server.get("heap_limit_bytes") and \
            server["heap_max_used_bytes"] > 0.9 * server["heap_limit_bytes"]:
        add("warning", "heap")
    # was the requested load really applied?
    target = scenario.get("target_rps")
    if target and steady["requests"] and steady["throughput_rps"] < 0.9 * target:
        slow = bool(out) or bool(slo.get("p95_ms") and steady["latency_ms"].get("p95")
                                 and steady["latency_ms"]["p95"] > slo["p95_ms"])
        add("warning", "target_missed", target=f"{num(target)} req/s", actual=f"{num(steady['throughput_rps'], 1)} req/s",
            why=text("target_missed_app" if slow else "target_missed_generator")[0])
    advice = text("advice_split" if splittable else "advice_single")[0]
    cpu, cpu_limit = server.get("generator_cpu_cores_max"), server.get("generator_cpu_limit_cores")
    if cpu is not None and cpu_limit and cpu >= 0.9 * cpu_limit:
        add("warning", "generator_cpu", ratio=_pct(cpu / cpu_limit), advice=advice)
    mem, mem_limit = server.get("generator_memory_max_bytes"), server.get("generator_memory_limit_bytes")
    if mem is not None and mem_limit and mem >= 0.9 * mem_limit:
        add("warning", "generator_memory", ratio=_pct(mem / mem_limit), advice=advice)
    open_groups = [g for g in scenario.get("thread_groups", []) if g["role"] == "main" and g["model"] == "open" and g["threads"]]
    if open_groups and overall.get("max_users") and overall["max_users"] >= 0.98 * sum(g["threads"] for g in open_groups):
        add("warning", "open_limit", limit=sum(g["threads"] for g in open_groups))
    knee = capacity.get("saturation_knee") if capacity.get("available") else None
    if knee:
        level = knee["label"] if capacity["basis"] == "thread_group" else f"{num(knee['load'], 1)} {capacity['unit']}"
        add("info", "knee", level=level, p95=ms(knee["p95_ms"]), goodput=f"{knee['goodput_rps']:.0f} req/s")
    log = traffic_log or {}
    if log.get("available") and log.get("completeness") is not None and log["completeness"] < 0.98:
        add("warning", "log_incomplete", logged=num(log["logged"]), answered=num(log["client_answered"]),
            pct=_pct(log["completeness"]))
    if log.get("available"):
        waiting = [e for e in log["endpoints"] if (e.get("outside_ms") or {}).get("p95") is not None]
        worst = max(waiting, key=lambda e: e["outside_ms"]["p95"], default=None)
        if worst and worst["outside_ms"]["p95"] >= 50 and worst["outside_ms"]["p95"] >= worst["inside_ms"]["p95"]:
            add("warning", "queue_outside", endpoint=worst["key"], outside=ms(worst["outside_ms"]["p95"]),
                inside=ms(worst["inside_ms"]["p95"]), knobs=knobs("web", env_keys))
    prof = profile or {}
    if prof.get("available") and prof.get("event") == "cpu" and prof.get("categories") and prof["samples"] >= 200:
        top = prof["categories"][0]
        method = prof["self_top"][0] if prof.get("self_top") else None
        add("info", "profile_hotspot", category=top["name"], pct=f"{top['pct']:.0f}%",
            method=method["method"] if method else "–", method_pct=f"{method['pct']:.0f}%" if method else "")
    if not [d for d in out if d["code"] != "profile_hotspot"]:
        add("good", "no_signal")
    out.sort(key=lambda d: SEVERITIES.index(d["severity"]))
    return out
