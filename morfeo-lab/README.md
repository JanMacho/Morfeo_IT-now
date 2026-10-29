# MORFEO lab

> **PAE demo (in Spanish):** how to run the 10-minute demo plan against a single bank-api instance and see it
> in Grafana, step by step from a clean Windows machine: [TUTORIAL.md](TUTORIAL.md).

The MORFEO lab runs the load tests that the MORFEO AI engine designs, against a Java application deployed on
Kubernetes, and tells you **how much the application can take and where it gives in first**.

Version 1.0.0 · Windows, Linux and macOS · Python 3.10+ · Docker, kind and kubectl

---

## 1. Why it exists

Before an application reaches the market, its owners need to know its limits: how many users it serves with a
healthy response time, what happens past that point, and which resource runs out first (web threads, database
connections, CPU, memory, garbage collection). Two kinds of traffic matter:

- **person → machine**: people using the application, with sessions, pauses and a daily rhythm;
- **machine → machine (M2M)**: other systems calling the application at a high, steady and sometimes bursty rate,
  with no pauses. This is usually where the limits show up first.

MORFEO answers that in two halves:

| Half | Owner | What it does |
|------|-------|--------------|
| **AI engine** | AI team | Learns the traffic of the application from its logs and writes **JMeter test plans** (`.jmx`) that reproduce it, at any hour of the day or scaled up to stress it. |
| **Lab** (this repository) | Lab team | Deploys the application on Kubernetes, **runs the plans exactly as written**, measures the client side (JMeter) and the server side (CPU, memory, threads, pools, database), and returns a verdict, a diagnosis and the data. |

The lab does **not** design load. It adds no requests, no pacing and no users of its own: what the plan says is what
the application receives. Everything the lab adds is *measurement*.

## 2. How it works

```
                  ┌──────────────────────────── your machine ────────────────────────────┐
  AI engine ──►   │  scenarios/*.jmx          targets/<app>.yaml        config/lab.yaml   │
  (.jmx plans)    │        │                         │                        │           │
                  │        ▼                         ▼                        ▼           │
                  │   morfeo-lab validate ─► morfeo-lab deploy ─► morfeo-lab run          │
                  └────────────────────────────────┬──────────────────────────────────────┘
                                                   │  kubectl
        ┌──────────────────────────────────────────▼───────────────────────────────────┐
        │  Kubernetes cluster (kind on a laptop, or any cluster)                       │
        │                                                                              │
        │  pool "app"                      pool "generators"          pool "system"    │
        │  ┌────────────────────┐          ┌─────────────────────┐    ┌──────────────┐ │
        │  │ your Java app      │ ◄─────── │ JMeter generator 0  │    │ Prometheus   │ │
        │  │ (1..N replicas)    │  HTTP    │ JMeter generator 1  │    │ Grafana      │ │
        │  │ + PostgreSQL       │ ◄─────── │ …  (live exporter)  │    │ (history)    │ │
        │  └────────────────────┘          └─────────────────────┘    └──────────────┘ │
        │        ▲ metrics: CPU, memory, threads, pools, GC, DB               ▲        │
        └────────┼─────────────────────────────────────────────────────────────┼───────┘
                 │                                                             │
                 └─────────────  results/<run>/…  index.html · summary.json ◄──┘
                                                 │
                                                 ▼
                                      back to the AI team (summary.json,
                                      the traffic log of the application)
```

- The **application** and the **generators** run on different nodes, so a generator never takes CPU from the
  application, and the application always has the same resources between runs.
- Each scenario is a Kubernetes *Job*. If the plan allows it, the lab runs it on several generators and divides the
  load between them **exactly** (the total load never depends on the number of generators).
- Before every scenario the database is restored to the same state, so every run starts from the same data.
- During the run the lab records the client side (every sample from JMeter), the server side (Prometheus) and the
  traffic log the application writes. After the run it analyses all three together.

## 3. What you need

| | Windows 10/11 | Linux | macOS |
|---|---|---|---|
| Python 3.10 or newer | `winget install Python.Python.3.12` | your package manager | `brew install python` |
| Docker | Docker Desktop (WSL 2 backend) | Docker Engine | Docker Desktop |
| kubectl | `winget install Kubernetes.kubectl` | [kubernetes.io/docs/tasks/tools](https://kubernetes.io/docs/tasks/tools/) | `brew install kubectl` |
| kind | `winget install Kubernetes.kind` | [kind.sigs.k8s.io](https://kind.sigs.k8s.io) | `brew install kind` |

Plan on **8 GB of RAM at least (16 GB recommended)**, 15 GB of free disk and a Docker allowance of **6 GB or more**.
Docker Desktop on Windows takes its memory from WSL 2; [docs/SETUP.md](docs/SETUP.md) shows how to raise it.
kind is only needed if the lab creates the cluster; any cluster that `kubectl` can reach also works
([docs/CONFIGURATION.md](docs/CONFIGURATION.md)).

## 4. Install

**Windows (PowerShell)**

```powershell
cd C:\path\to\morfeo-lab
py -3 -m pip install -r requirements.txt
.\morfeo-lab.cmd doctor
```

**Linux / macOS**

```sh
cd /path/to/morfeo-lab
python3 -m pip install -r requirements.txt     # a virtual environment is fine
./morfeo-lab doctor
```

`doctor` checks the machine (memory, disk, Docker running, kubectl, kind, busy ports, access to the download servers,
the application files and the plans) and prints the next step. It never changes anything. Optionally,
`pip install -e .` installs the `morfeo-lab` command in your environment. The rest of this document writes
`morfeo-lab`; use `.\morfeo-lab.cmd` (Windows) or `./morfeo-lab` (Linux/macOS) if you did not install it.

## 5. Run a test, from start to finish

```text
morfeo-lab up                          # once: cluster, images, Prometheus and Grafana (several minutes the first time: it downloads about 2 GB)
morfeo-lab deploy bank-api             # build and start the application and its database
morfeo-lab validate scenarios/         # check the plans from the AI team, no cluster needed
morfeo-lab run scenarios/              # run them
morfeo-lab open                        # open the results page of the last run
```

1. **`up`** creates a three-node kind cluster (`system`, `app`, `generators`), builds the load generator image
   (Apache JMeter and the plugins, pinned and checksum-verified) and installs Prometheus and Grafana. It is
   idempotent: running it again only builds what changed.
2. **`deploy <application>`** reads `targets/<application>.yaml`, builds an image from the JAR (or uses the image you
   name), starts PostgreSQL if the application needs it, starts the application and waits until it is ready. For
   bank-api put the JAR in `apps/` (see the header of [targets/bank-api.yaml](targets/bank-api.yaml)) or pass
   `--jar path\to\bank-api-stress.jar`.
3. **`validate`** reads every plan exactly as `run` would, and reports what the lab understood: load model,
   duration, endpoints, data files, how many generators it can use, and anything the lab would have to rewrite.
   `validate --json` prints the same for a CI pipeline ([docs/SCENARIOS.md](docs/SCENARIOS.md)).
4. **`run`** runs each plan in turn: restore the database, start the generators together, follow the test live,
   collect everything, analyse and write the pages. `Ctrl+C` stops the test cleanly and **keeps the partial
   results**. Useful options: `--generators N`, `--tag name`, `--fresh` (cold JVM), `--no-reset`, `--baseline <run>`,
   `--prop name=value`, `--open`.
5. **`open`** shows the results; `open grafana` the live dashboards. `report <folder>` rebuilds the pages from the raw
   data (for instance after changing the objectives), and `compare <run> <run>` puts two runs side by side.

Stop and clean: `morfeo-lab down` removes the application and the generators, `morfeo-lab down --destroy` removes the
whole lab.

### Commands

| Command | What it does |
|---|---|
| `doctor` | Checks this machine and prints the next step. |
| `up` | Creates or updates the lab (cluster, images, monitoring). |
| `status` | Shows nodes, pods, the deployed application and how to reach Grafana. |
| `dashboard` | Port-forwards Grafana, Prometheus and the application to `localhost` (clusters other than kind). |
| `down [--destroy]` | Removes the application and the generators, or the whole lab. |
| `deploy <application>` | Deploys `targets/<application>.yaml` (`--jar`, `--replicas`, `--restart`). |
| `validate [plans]` | Checks plans without a cluster (`--show`, `--json`, `--generators`, `--application`). |
| `run [plans]` | Runs plans and writes the results. |
| `open [run\|history\|grafana]` | Opens results in the browser. |
| `report <folder>` | Rebuilds the pages from the raw data (`--application` to use other objectives). |
| `compare <run> <run>…` | Compares runs; the first one is the baseline. |

Global options: `--config <file>`, `-v/--verbose` (print every external command), `--version`.
Exit codes: `0` ok · `1` a scenario failed its criteria, or a plan is not valid · `2` usage or environment problem ·
`130` interrupted.

## 6. What a run leaves behind

```
results/
├── index.html                       history of all runs
└── 20251014-120000-v1.2/            one folder per run (--tag adds the label)
    ├── index.html                   the page to read: verdict, reasons, capacity, charts, endpoints
    └── <scenario>/
        ├── summary.json             everything the lab measured (versioned contract, docs/SUMMARY.md)
        ├── endpoints.csv            one row per endpoint
        ├── junit.xml                one test case per acceptance criterion (for CI)
        ├── timeseries.csv           throughput, latency and users per second
        ├── app/traffic.jsonl        the application's traffic log of the test, merged from every replica
        ├── app/…                    the application's own logs
        ├── profile/flamegraph.html  where the CPU of the application went (if profiling is on)
        ├── jmeter-report/           the official JMeter HTML dashboard
        ├── bundle/                  the exact plan every generator received
        └── raw/generator-N/         JMeter's own result file, logs and exit code
```

## 7. How to read the results

Open `results/<run>/index.html`. From top to bottom each scenario shows:

1. **The verdict** (*Passed*, *Passed with warnings*, *Failed*) and **why**, in two or three lines.
2. **The diagnosis**: where the application saturated (web threads, database pool, CPU limit, GC, heap, database
   CPU or connections) or that there were no signs of saturation.
3. **The acceptance criteria**, one line each, with the target and the measured value.
4. **Capacity**, when the plan has several load levels (steps, a ramp or one thread group per level): the highest
   level within the objectives, the level where it breaks, and where throughput stops growing.
5. **Charts**: requested vs actual load, latency, errors, users.
6. **Endpoints**: throughput, latency and errors for each one.
7. **Errors**, grouped by kind. 4xx answers are *client behaviour* (for example "insufficient funds") and are listed
   but do not fail a run; 5xx answers, timeouts and failed assertions are *server failures* and do.
8. **Traffic log**: how many requests the application logged against how many the generators got answered, and how
   long each request waited *outside* the application (queues) against *inside* it.

[docs/RESULTS.md](docs/RESULTS.md) explains every figure, the warm-up rule, the criteria and the diagnosis codes.

## 8. Working with the AI team

- The AI team delivers `.jmx` plans. Copy them into `scenarios/`, or pass their path to `validate` and `run`.
- The lab returns `summary.json` (schema in [docs/summary.schema.json](docs/summary.schema.json), versioned) and the
  application's traffic log (`app/traffic.jsonl`). The format is documented in [docs/SUMMARY.md](docs/SUMMARY.md).
- `morfeo-lab validate --json` runs without a cluster, so the AI team can use it in its own CI to know that a
  generated plan will be accepted.
- How plans are divided between generators, which properties the lab passes to JMeter and what a plan should do to
  scale across generators: [docs/SCENARIOS.md](docs/SCENARIOS.md).

## 9. Adding an application

One YAML file in `targets/`, no Dockerfile and no Kubernetes manifests: the JAR (or image), the port, the resources,
the health endpoint, the database, where the traffic log goes and the acceptance criteria. Any Java application with a
JAR (Spring Boot, Quarkus uber-jar, Javalin…) or a ready image, and PostgreSQL as its only dependency, fits.
[docs/TARGETS.md](docs/TARGETS.md) documents every option; [targets/bank-api.yaml](targets/bank-api.yaml) is the
reference application of the project.

## 10. Reproducibility

- JMeter and every plugin are pinned by version and checksum (`images/generator/plugins.json`). The image build runs a
  plan with every kind of thread group and fails if JMeter cannot.
- Images are tagged with a hash of their content: a changed JAR or Dockerfile gives a new image, nothing else is rebuilt.
- The application keeps the same CPU and memory between runs (`runtime.resources`), the database is restored before
  each scenario, and `--fresh` restarts the JVM to measure a cold start.
- The load generator is measured too: if a generator uses most of its CPU or memory, the run says so, because a
  saturated generator makes the application look slower than it is.
- Nothing secret is archived with the results: API keys and certificate passwords reach the generators separately
  and are masked in every file kept.

## 11. Repository layout

```
config/lab.yaml        how and where the tests run (cluster, generators, monitoring)
targets/               one file per application
scenarios/             the plans (.jmx) from the AI team (empty here)
apps/                  JARs of the applications (empty here)
results/               the output of the runs (empty here)
morfeo_lab/            the lab (Python)
images/                the generator, application runtime and agents images
deploy/                Kubernetes and kind templates, Prometheus and Grafana
docs/                  documentation and the summary.json schema
tests/                 tests
```

## 12. Development

```sh
python3 -m pip install -r requirements-dev.txt
ruff check .
python3 -m unittest discover -s tests          # a few seconds
JMETER_HOME=/opt/apache-jmeter python3 -m unittest tests.test_engine   # runs real JMeter (optional)
```

## 13. More documentation

| | |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Installation details, Windows memory, proxies, ports, troubleshooting |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every option of `config/lab.yaml` |
| [docs/TARGETS.md](docs/TARGETS.md) | Every option of an application file |
| [docs/SCENARIOS.md](docs/SCENARIOS.md) | What the lab accepts in a plan, how it divides the load, `validate --json` |
| [docs/SUMMARY.md](docs/SUMMARY.md) | The `summary.json` contract |
| [docs/RESULTS.md](docs/RESULTS.md) | How to read the results, criteria and diagnosis codes |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |
