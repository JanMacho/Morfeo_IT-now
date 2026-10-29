# Changelog

## 1.0.0

First release of the MORFEO lab.

### What it does

- Runs JMeter test plans (`.jmx`) written by the MORFEO AI engine against a Java application on Kubernetes, **exactly
  as written**: the lab adds no load and no requests of its own.
- Works with any Java application that is an executable JAR or a ready image, with PostgreSQL as its only dependency:
  one YAML file per application (`targets/`).
- Creates its own multi-node kind cluster (separate nodes for the application and the load generators) or uses any
  cluster `kubectl` can reach.
- Measures both sides: every sample from JMeter, server metrics from Prometheus (CPU and throttling, memory, heap, GC,
  web threads, database pool, database CPU and connections, restarts) and the traffic log of the application.
- Judges each scenario against the acceptance criteria of the application, explains where the application saturates
  and, for plans with several load levels, reports the capacity.

### Plans

- Standard thread groups (with setUp and tearDown), Concurrency, Arrivals, Free-Form Arrivals, Ultimate and Stepping
  Thread Groups, Open Model Thread Group, and Constant, Precise and Throughput Shaping timers.
- Plans are sent unchanged unless the cluster requires otherwise: requests to localhost go to the deployed
  application, listeners are removed, data files are made relative.
- Plans that can be divided run on several generators with the total load preserved exactly; plans that pace
  themselves with a script run on one generator; plans that divide their own load read `morfeo_generators` and
  `morfeo_generator`.
- Plugins missing from the generator image are reported with the line to add.

### Results

- A page per run with the verdict and its reason first, diagnosis, acceptance criteria, capacity, charts,
  endpoints and errors; a history page; a comparison page; the official JMeter report; a flame graph.
- `summary.json` (schema version 1, JSON Schema included) and the application's merged traffic log, for the AI engine.
- `junit.xml`, `endpoints.csv` and `timeseries.csv` for CI.
- Warm-up is excluded from the headline figures; setUp and tearDown are reported apart; requested and actual load are
  compared; saturated generators are reported.

### Operation

- Commands: `doctor`, `up`, `status`, `dashboard`, `down`, `deploy`, `validate` (with `--json` for CI, no cluster
  needed), `run`, `open`, `report`, `compare`.
- `Ctrl+C` stops a test cleanly and keeps the partial results; every wait has a time limit.
- The database is restored before every scenario; `--fresh` restarts the JVM for a cold start; `--baseline` fails a
  scenario that got worse than an earlier run.
- JMeter, its plugins and the Kubernetes node image are pinned; the generator image build verifies checksums and runs a
  plan with every kind of thread group.
- Works in PowerShell, the classic Windows console and terminals without colour.
