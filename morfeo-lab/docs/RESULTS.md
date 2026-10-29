# Reading the results

Open `results/<run>/index.html` (`morfeo-lab open`). This page explains what is on it. The same data, for machines, is
in [summary.json](SUMMARY.md).

## The verdict, first

Each scenario starts with one of:

| Verdict | Meaning |
|---|---|
| **Passed** | Every acceptance criterion was met. |
| **Passed with warnings** | The application met its criteria, but something about the *measurement* deserves attention (the requested load was not reached, a generator was saturated, the traffic log is incomplete…). |
| **Failed** | The application broke a criterion, restarted during the test, or is worse than the baseline. |
| **Not run** | The scenario could not start (invalid plan, application not deployed…); the reason is shown. |

Under it, **why** in two or three lines: the criteria that failed with their numbers, or what to look at.

## Acceptance criteria

They come from the `slo` of the application file ([TARGETS.md](TARGETS.md)); the page lists each with *target* and
*measured*:

- **Server errors**: share of requests that failed because of the server. 4xx answers do not count.
- **p95 / p99 latency**, overall and per endpoint with its own objective.
- **Successful requests per second**, if `min_throughput_rps` is set.
- **No application restarts** during the test.
- **vs baseline** (with `run --baseline <run>`): latency or errors worse than the earlier run beyond the tolerance of
  `regression` in `config/lab.yaml`.
- **setUp completed**: a failing setUp invalidates the test.
- **Warnings** (do not fail): *Requested load reached*, *Load generator CPU*, *Traffic log complete*, *Load generator
  exit code*.

## Errors: whose fault is it?

| Class | Meaning | Fails the run? |
|---|---|---|
| `ok` | A successful answer | – |
| `client_4xx` | The application answered 4xx: client behaviour (insufficient funds, unknown id…) | No, listed apart |
| `server_5xx` | 5xx answers, always, even if the plan "expected" them. 503 is typically saturation | Yes |
| `network` | No answer: timeout, connection refused or reset | Yes |
| `assertion` | An answer below 400 that an assertion of the plan rejected | Yes |

## Which part of the test is judged

- **Warm-up.** The headline figures (`steady`) skip the ramp-up of the plan, so the cold start and the growing load do
  not drag the numbers. The page says which window it shows; `overall` is in `summary.json`.
- **setUp and tearDown** are reported separately (`setUp` creates users, tokens…; its requests are not load).
- **Requested vs actual load.** The charts draw what the plan asked for next to what was achieved. If the application
  cannot keep up, the gap is the finding; if a generator is saturated, a warning says the figures understate the
  application.

## Diagnosis: where does it saturate?

The lab combines the client figures with the server metrics (CPU and throttling, memory, heap, GC, web threads,
database pool, database CPU and connections, restarts) and says what it sees. Codes in `summary.json`:

| Code | What it means | What to try |
|---|---|---|
| `crash` | The application restarted during the test (OOM, failed probes…) | Look at `app/*.stdout.log`; raise memory or fix the cause. |
| `app_unreachable` | The generators could not connect | Port in the application file, application running. |
| `setup_failed` | The plan's setUp failed | The data the plan needs may not exist. |
| `http_503` | The application rejected requests | Usually a full pool/queue; the diagnosis names the variables of the application file that control it (for example the web threads and the database pool). |
| `http_5xx` | Internal errors | Application log. |
| `no_answer` | Timeouts / refused connections | Accept queue full, a crash or a GC pause. |
| `cpu_throttled`, `cpu_high` | CPU limit reached or close | More CPU, or lighter requests. |
| `web_threads` | The web thread pool is exhausted | Larger pool, or faster requests. |
| `db_waiting` | Threads waited for a database connection | Larger pool (watch the database `max_connections`), or faster queries. |
| `db_cpu`, `db_connections` | The database is the bottleneck / near its connection limit | Database resources, `max_connections`. |
| `gc`, `heap` | GC pressure / heap almost full | Larger heap or less allocation. |
| `target_missed` | The requested load was not reached | The text says whether the application could not keep up or the plan needs more users/generators. |
| `open_limit` | An open-model plan reached its thread limit | The application cannot sustain that arrival rate. |
| `generator_cpu`, `generator_memory` | A generator was saturated | `--generators`, or more resources for generators. |
| `knee` | Throughput stops growing at a load level | Beyond it extra load only lengthens queues. |
| `log_incomplete` | The application logged fewer requests than it answered | The traffic log under-states the traffic. |
| `queue_outside` | Requests wait *before* the application processes them | Web server queue or network. |
| `profile_hotspot` | Largest share of CPU, from the flame graph | Hottest method and category. |
| `no_signal` | No signs of saturation | The application handled the load. |

## Capacity

If the plan has several load levels — one thread group per level, or a ramp or steps — the page lists each level with
throughput, latency and errors, and marks the highest level **within the objectives** (*sustainable*), the first level
**outside** them (*breaking*) and where throughput stops growing (*knee*). A plan with one constant level has no
capacity table: to find a limit, ask the AI engine for a ramp or steps.

## Traffic log

The application's own log (`app/traffic.jsonl`) lets the lab cross-check the generators:

- **Completeness**: lines logged over requests answered. A low value means the log of the application drops lines under
  load, which also affects what the AI engine learns from it.
- **Inside vs outside**: the latency the application measured for itself against the latency the generator saw. The
  difference is time spent in queues or on the network, not in the application's code.

## Other files

- **JMeter report** (`jmeter-report/index.html`): the official JMeter dashboard of the whole scenario, every generator merged.
- **Flame graph** (`profile/flamegraph.html`): where the CPU of the application went in the steady state.
- **Grafana** (`morfeo-lab open grafana`): live view during the run and history of previous ones; a link per scenario
  opens it on the right time range.
- **`endpoints.csv`**, **`timeseries.csv`**, **`junit.xml`**, **`raw/`**: for your own analysis or CI.

## Comparing runs

`morfeo-lab compare <run-a> <run-b> --names before,after` writes a page with both side by side. Differences are only
called significant when they exceed the run-to-run noise measured inside the steady state (Welch's t-test over
10-second windows). For the same, but enforced, use `run --baseline <run>`.
