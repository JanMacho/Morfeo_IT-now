"""A minimal Prometheus HTTP API client (standard library only) and the server-side metrics saved with every scenario."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request


# Saved with every run so that the summary can say what the server went through, not only what the clients saw. A
# missing series is simply skipped. The selectors use the namespace and the container name, so they work with or
# without dedicated node pools.
def app_queries(ns_app: str, ns_gen: str) -> dict:
    app = f'namespace="{ns_app}",container="app"'
    db = f'namespace="{ns_app}",container="postgres"'
    gen = f'namespace="{ns_gen}",container="generator"'
    return {
        "app_cpu_cores": f'sum(rate(container_cpu_usage_seconds_total{{{app}}}[30s]))',
        "app_cpu_limit_cores": f'sum(container_spec_cpu_quota{{{app}}}) / 100000',
        "app_cpu_throttled_ratio": (
            f'sum(rate(container_cpu_cfs_throttled_periods_total{{{app}}}[30s]))'
            f' / clamp_min(sum(rate(container_cpu_cfs_periods_total{{{app}}}[30s])), 1e-9)'),
        "app_memory_bytes": f'sum(container_memory_working_set_bytes{{{app}}})',
        "app_memory_limit_bytes": f'sum(container_spec_memory_limit_bytes{{{app}}})',
        "db_cpu_cores": f'sum(rate(container_cpu_usage_seconds_total{{{db}}}[30s]))',
        "db_cpu_limit_cores": f'sum(container_spec_cpu_quota{{{db}}}) / 100000',
        "jvm_heap_used_bytes": 'sum(jvm_memory_used_bytes{target!="",area="heap"})',
        "jvm_heap_max_bytes": 'sum(jvm_memory_max_bytes{target!="",area="heap"})',
        "jvm_gc_time_ratio": 'max(sum by (pod) (rate(jvm_gc_collection_seconds_sum{target!=""}[30s])))',
        "jvm_threads": 'sum(jvm_threads_current{target!=""})',
        "web_threads_busy": 'sum(tomcat_threadpool_currentthreadsbusy{target!=""}) or sum(jetty_threadpool_busythreads{target!=""})',
        "web_threads_max": 'sum(tomcat_threadpool_maxthreads{target!=""}) or sum(jetty_threadpool_maxthreads{target!=""})',
        "db_pool_active": 'sum(hikari_activeconnections{target!=""})',
        "db_pool_waiting": 'sum(hikari_threadsawaitingconnection{target!=""})',
        # postgres-exporter (optional): connections in use vs the server limit
        "db_connections": f'sum(pg_stat_activity_count{{namespace="{ns_app}"}})',
        "db_max_connections": f'max(pg_settings_max_connections{{namespace="{ns_app}"}})',
        "generator_cpu_cores_max": f'max(rate(container_cpu_usage_seconds_total{{{gen}}}[30s]))',
        "generator_cpu_limit_cores": f'max(container_spec_cpu_quota{{{gen}}}) / 100000',
        "generator_memory_bytes": f'max(container_memory_working_set_bytes{{{gen}}})',
        "generator_memory_limit_bytes": f'max(container_spec_memory_limit_bytes{{{gen}}})',
    }


class Prom:
    def __init__(self, base_url: str, timeout: float = 5):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        # localhost must never go through a proxy
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _get(self, path: str, params: dict) -> dict:
        url = f"{self.base}{path}?{urllib.parse.urlencode(params)}"
        with self._opener.open(url, timeout=self.timeout) as r:
            data = json.loads(r.read().decode())
        if data.get("status") != "success":
            raise RuntimeError(data.get("error", "prometheus error"))
        return data["data"]

    def up(self) -> bool:
        try:
            with self._opener.open(f"{self.base}/-/ready", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def instant(self, expr: str) -> float | None:
        try:
            res = self._get("/api/v1/query", {"query": expr})["result"]
            return float(res[0]["value"][1]) if res else None
        except Exception:
            return None

    def range(self, expr: str, start: float, end: float, step: float) -> list[tuple[float, float]]:
        try:
            res = self._get("/api/v1/query_range",
                            {"query": expr, "start": start, "end": end, "step": max(1, int(step))})["result"]
        except Exception:
            return []
        if not res:
            return []
        out = []
        for ts, v in res[0]["values"]:
            try:
                fv = float(v)
            except ValueError:
                continue
            if fv == fv and abs(fv) != float("inf"):
                out.append((float(ts), fv))
        return out

    def snapshot(self, queries: dict, start: float, end: float, step: float = 5) -> dict:
        return {name: self.range(q, start, end, step) for name, q in queries.items()}
