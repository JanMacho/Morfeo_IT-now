"""What the server went through during the test, from the metrics Prometheus kept."""
from __future__ import annotations


def summarize(snapshot: dict, t0: float | None = None, t1: float | None = None) -> dict:
    """The peak values DURING the test. The snapshot also covers the 30 s before it (useful for charts), but the
    warm-up of the application (JIT, connection pools) must not count as a symptom of the test. Rates use a 30 s
    window, so the first 15 s of the test are skipped as well."""
    lo = (t0 + 15) if t0 else float("-inf")
    hi = (t1 + 5) if t1 else float("inf")

    def points(name):
        data = snapshot.get(name) or []
        return [x for ts, x in data if lo <= ts <= hi] or [x for _, x in data]

    def peak(name):
        v = points(name)
        return max(v) if v else None

    def last(name):
        v = points(name)
        return v[-1] if v else None

    return {
        "cpu_cores_max": peak("app_cpu_cores"), "cpu_limit_cores": last("app_cpu_limit_cores"),
        "cpu_throttled_max": peak("app_cpu_throttled_ratio"),
        "memory_max_bytes": peak("app_memory_bytes"), "memory_limit_bytes": last("app_memory_limit_bytes"),
        "heap_max_used_bytes": peak("jvm_heap_used_bytes"), "heap_limit_bytes": last("jvm_heap_max_bytes"),
        "gc_time_ratio_max": peak("jvm_gc_time_ratio"), "jvm_threads_max": peak("jvm_threads"),
        "web_threads_busy_max": peak("web_threads_busy"), "web_threads_limit": last("web_threads_max"),
        "db_pool_active_max": peak("db_pool_active"), "db_pool_waiting_max": peak("db_pool_waiting"),
        "db_cpu_cores_max": peak("db_cpu_cores"), "db_cpu_limit_cores": last("db_cpu_limit_cores"),
        "db_connections_max": peak("db_connections"), "db_max_connections": last("db_max_connections"),
        "generator_cpu_cores_max": peak("generator_cpu_cores_max"), "generator_cpu_limit_cores": last("generator_cpu_limit_cores"),
        "generator_memory_max_bytes": peak("generator_memory_bytes"),
        "generator_memory_limit_bytes": last("generator_memory_limit_bytes"),
    }
