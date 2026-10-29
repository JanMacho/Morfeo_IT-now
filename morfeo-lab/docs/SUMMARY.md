# `summary.json`: the contract with the AI team

Every scenario leaves `results/<run>/<scenario>/summary.json`. It is the lab's answer to the AI engine: what the plan
asked for, what the application did under it, and why. The JSON Schema is [summary.schema.json](summary.schema.json).

## Versioning

`schema_version` is an integer (currently **1**). It changes **only** when a key is renamed, removed or changes
meaning. Adding keys does not change it, so readers must ignore keys they do not know. `producer` is always
`morfeo-lab`; `lab_version` is the release that wrote the file.

## Conventions

- **Rates are fractions** of the requests (0 to 1), not percentages: `server_error_rate: 0.012` is 1.2 %.
  `error_rate` is the share of **4xx** answers (client behaviour, for example "insufficient funds") and
  `server_error_rate` the share of **server failures** (5xx, timeouts and connection errors, failed assertions).
- Times are **milliseconds**; throughput is **requests per second**; memory is **bytes**.
- Moments are ISO 8601 in UTC.
- **Endpoints are keyed like `profile.json`**: `GET /api/saldo/{id}`.
- `null` means "not measured" (for example server metrics when Prometheus was not available), never zero.

## Layout

| Key | |
|---|---|
| `schema_version`, `producer`, `lab_version`, `run_id`, `started_at`, `wall_clock_s` | identity |
| `interrupted` | `true` when the run was stopped with Ctrl+C: the figures cover what ran until then |
| `verdict` | `PASS`, `PASS_WITH_WARNINGS` or `FAIL` |
| `scenario` | what the plan asks for: model, duration, ramp-up, peak users or rate, thread groups, endpoints, how the load is divided |
| `application` | name, replicas, resources, environment (private values masked), image |
| `slo` | the objectives the run was judged against |
| `generators` | count, why, exit codes, `distribution` (`split`, `single` or `plan`) |
| `overall` | every request of the load stage |
| `steady` | the same, after the ramp-up (the headline figures; equals `overall` when the ramp-up is most of the test) |
| `steady_window_s` | `[from, to]` seconds since the start of the load that `steady` covers |
| `setup`, `teardown` | requests of the setUp / tearDown thread groups, reported apart |
| `endpoints[]` | per endpoint: `key`, `requests`, `share`, `throughput_rps`, `goodput_rps`, `error_rate`, `server_error_rate`, `by_class`, `latency_ms`, `status_codes`, `slo_p95_ms` |
| `errors[]` | grouped by class, endpoint, code and message, with the count and when the first one appeared |
| `capacity` | the load levels of the run (see below) |
| `checks[]` | each acceptance criterion: `name`, `status` (`pass`, `warn`, `fail`), `target`, `actual`, `detail` |
| `diagnosis[]` | where the application saturated: `severity`, `code`, `title`, `detail`, `params` |
| `server` | peaks of the server metrics during the load |
| `traffic_log` | the application's traffic log against the generators' view |
| `profile` | the CPU profile summary, when profiling was on |
| `timeline`, `series`, `percentile_curve`, `noise_windows` | data behind the charts and the comparison between runs |
| `links` | relative paths to the JMeter report, the flame graph, the application logs |

A window (`overall`, `steady`, each endpoint) has: `requests`, `duration_s`, `throughput_rps` (all requests per
second), `goodput_rps` (successful ones), `failed_requests`, `error_rate`, `server_error_rate`, `by_class`
(`ok`, `client_4xx`, `server_5xx`, `network`, `assertion`) and `latency_ms` (`mean`, `min`, `max`, `p50`, `p90`,
`p95`, `p99`, `p99_9`).

### `capacity`

When the plan has several load levels (one thread group per level, or a ramp or steps in time):

```json
{"available": true, "basis": "thread_group", "unit": "users",
 "levels": [{"load": 10, "label": "10 users", "throughput_rps": 9.8, "goodput_rps": 9.7, "p95_ms": 31.0,
             "server_error_rate": 0.0, "within_slo": true}, "…"],
 "sustainable": {"…the highest level within the objectives…"},
 "breaking": {"…the first level outside them…"},
 "saturation_knee": {"…where throughput stops growing, or null…"}}
```

`available: false` carries a `reason` (for example "the load stays constant").

### `diagnosis[].code`

`crash`, `app_unreachable`, `setup_failed`, `http_503`, `http_5xx`, `no_answer`, `cpu_throttled`, `cpu_high`,
`web_threads`, `db_waiting`, `db_cpu`, `db_connections`, `gc`, `heap`, `target_missed`, `open_limit`, `generator_cpu`,
`generator_memory`, `knee`, `log_incomplete`, `queue_outside`, `profile_hotspot`, `no_signal`. The codes are stable;
the texts may improve. `severity` is `critical`, `serious`, `warning`, `info` or `good`.

## The application's traffic log

`app/traffic.jsonl` holds the lines the application wrote **during the test**, from every replica, in time order. Each
line is a JSON object with `ts_end`, `method`, `path`, `status`, `latency_ms`, `client_id` and `session_id`. The AI
engine can learn from it directly. `summary.traffic_log` says how complete it is (`completeness`: lines logged over
requests answered) and, per endpoint, how long requests waited outside the application against inside it.

## Reading it from a script

```python
import json
s = json.load(open("results/20251014-120000/my-scenario/summary.json"))
assert s["schema_version"] == 1
w = s["steady"]
print(s["verdict"], w["throughput_rps"], w["latency_ms"]["p95"], w["server_error_rate"])
for d in s["diagnosis"]:
    print(d["severity"], d["code"], d["title"])
```

`junit.xml` carries the same criteria for CI systems: one test case per entry of `checks`.
