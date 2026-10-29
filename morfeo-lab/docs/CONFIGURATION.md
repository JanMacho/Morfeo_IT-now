# Lab configuration (`config/lab.yaml`)

Where and how the tests run. Plans and application files never refer to it. Every option has a default (the shipped
file only restates them with comments), so a file can contain just the options you change. Another file:
`morfeo-lab --config other.yaml …` or the `MORFEO_LAB_CONFIG` variable. A misspelt option is reported with a suggestion.

## `cluster`

| Option | Default | |
|---|---|---|
| `provider` | `kind` | `kind`: the lab creates and owns a multi-node kind cluster. `existing`: any cluster `kubectl` can reach (Docker Desktop Kubernetes, minikube, k3s, a company cluster). |
| `name` | `morfeo` | Name of the kind cluster. |
| `context` | – | `kubectl` context (default `kind-<name>`, or the current one with `existing`). |
| `node_image` | `kindest/node:v1.31.2` | Pinned, so every machine runs the same Kubernetes. |
| `pools` | `true` | Keep the application and the generators on different nodes. With `existing`, label your nodes (`up` prints the commands) or set `false` on a single-node cluster (fine for functional tests, not for measuring capacity). |
| `nodes.app`, `nodes.generators` | `1`, `1` | Nodes per pool in a kind cluster. |
| `image_loader` | `auto` | How images built on this machine reach the cluster: `auto`, `kind`, `docker` (Docker Desktop), `minikube`, `k3s`, `registry`. |
| `registry` | – | Required with `image_loader: registry` (for example `localhost:5001`). |
| `ports` | grafana 3000, prometheus 9090, app 7070 | Ports on `localhost` published by kind. |

## `namespaces`
`app`, `generators`, `monitoring` (defaults `morfeo-app`, `morfeo-generators`, `morfeo-monitoring`).

## `generators`

| Option | Default | |
|---|---|---|
| `jmeter_plugins` | casutg, tst, functions, dummy | Plugin ids installed in the generator image. `up` rebuilds the image when the list changes. Versions and checksums: `images/generator/plugins.json`. |
| `cpu`, `memory` | `2`, `2Gi` | Resources of **each** generator. |
| `jvm_heap` | `1g` | Heap of JMeter. |
| `max_threads_per_generator` | `1000` | With `--generators auto`, a generator is never asked for more than this. |
| `max_rps_per_generator` | `2500` | Same, in requests (or arrivals) per second. |
| `max_generators` | `8` | Upper limit of generators. |
| `max_duration` | `2h` | No scenario may be planned for, or run, longer than this. |
| `start_timeout` | `300` | Seconds to wait for every generator to be ready. |

## `monitoring`

| Option | Default | |
|---|---|---|
| `enabled` | `true` | Prometheus (server metrics). With `false` the run still works, without server metrics. |
| `grafana` | `true` | Grafana with the live dashboard. |
| `grafana_url` | – | Address used in result links (default `http://localhost:<ports.grafana>`). |
| `prometheus_image`, `grafana_image`, `postgres_exporter_image` | pinned | Third-party images. |
| `postgres_exporter` | `true` | PostgreSQL metrics, in its own pod: if its image cannot be pulled, the application and the database are unaffected. |
| `scrape_interval` | `5s` | |
| `persistent`, `storage`, `retention`, `retention_size` | `true`, `10Gi`, `30d`, `8GB` | History kept by Prometheus (needs a default StorageClass). |
| `profiling` | `enabled: true`, `event: cpu`, `interval: 20ms`, `chunk: 10s` | Flame graph (`event`: cpu, wall, alloc or lock). |

## `regression`
`latency_pct` (15) and `error_rate_pp` (0.5): how much worse than the baseline (`run --baseline`) a scenario may be.

## `results`
`dir` (default `results`): where runs are written.

## Environment variables

| Variable | |
|---|---|
| `MORFEO_LAB_CONFIG` | Another lab configuration file. |
| `MORFEO_BUILD_CA` | Path to a PEM file with the certificate authority of a network that inspects HTTPS. It is passed to the image builds as a build secret (never stored in an image). |
| `MORFEO_VERBOSE` | Same as `-v`. |
| `NO_COLOR` | Disables colours. |
