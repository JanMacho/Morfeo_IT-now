# Application files (targets)

One YAML file per application in `targets/`. It is **all** the lab needs: no Dockerfile, no Kubernetes manifests.
`morfeo-lab deploy <name>` reads `targets/<name>.yaml` (or a path). A misspelt option is reported with a suggestion
(`unknown option 'runtime.prot' (did you mean 'runtime.port'?)`) instead of being ignored.
[targets/bank-api.yaml](../targets/bank-api.yaml) is a complete, commented example.

What fits: any Java application that is an **executable JAR** (Spring Boot, Quarkus uber-jar, Javalin…) or a **ready
image**, listens on one HTTP(S) port and has **PostgreSQL** as its only dependency (or none).

## Minimal file

```yaml
name: my-app
artifact:
  jar: apps/my-app.jar
runtime:
  port: 8080
```

## Reference

### `name`, `description`
`name`: lowercase letters, digits and `-`, at most 40 characters. It names the deployment and appears in the results.

### `artifact` (exactly one of `jar` or `image`)

| Option | Default | |
|---|---|---|
| `jar` | – | Path to the JAR, or a **list of candidates**: the first that exists wins. Wildcards are allowed, but each candidate must match exactly one file. `${VAR}` and `${VAR:-default}` are expanded. Relative paths are relative to the lab folder. `deploy --jar <file>` overrides it once. |
| `image` | – | A ready image (`registry/name:tag`). The lab loads it into the cluster. |
| `java_version` | `21` | Java version of the runtime the lab builds around a JAR. |

The runtime image is built from the JAR with a glibc-based JRE and an unprivileged user; nothing about the
application lives in it. It is rebuilt only when the JAR changes.

### `runtime`

| Option | Default | |
|---|---|---|
| `port` | `8080` | Port the application listens on. |
| `scheme` | `http` | `http` or `https`. |
| `replicas` | `1` | Number of instances (`deploy --replicas N` overrides it). The traffic log of every replica is collected. |
| `java_opts` | `-XX:MaxRAMPercentage=75.0` | JVM options. |
| `args` | – | Program arguments. |
| `env` | – | Environment variables of the application. |
| `resources` | 1 CPU, 512 Mi | Kubernetes `requests` and `limits`. **Keep them fixed across runs you want to compare**: this is "the machine". |

### `health`

| Option | Default | |
|---|---|---|
| `type` | `tcp` | `tcp` (the port accepts connections) or `http` (the path answers with a success status). |
| `path` | `/` | Readiness path when `type: http`. Prefer one that needs the database, so no traffic arrives before it is up. |
| `liveness_path` | – | Liveness path; it should not depend on the database. |
| `startup_timeout` | `180` | Seconds to wait for the application to be ready before explaining why it is not. |

### `logs`: the traffic log of the application

| Option | |
|---|---|
| `files` | Absolute paths **inside the container** of the log files to collect after each scenario (from every replica). |
| `env` | Name of an environment variable through which the application is told where to write the first file (for example `TRAFFIC_LOG`). With it, the lab mounts a shared volume on that folder. |

The lab merges the lines every replica wrote **during the test** into `app/traffic.jsonl` and compares them with what
the generators saw. A log in the MORFEO format (JSON Lines with `ts_end`, `method`, `path`, `status`, `latency_ms`) is
analysed; a log in another format is only collected.

### `dependencies.postgres` (optional)

| Option | Default | |
|---|---|---|
| `image` | `postgres:16` | |
| `database`, `user`, `password` | `app` | Credentials of the lab database (not secrets). |
| `port` | `5432` | |
| `env` | – | How the application receives the connection. Placeholders: `{host}`, `{port}`, `{database}`, `{user}`, `{password}`. For Spring Boot: `SPRING_DATASOURCE_URL: "jdbc:postgresql://{host}:{port}/{database}"`. |
| `reset_sql` | – | A file (or list of candidates, like `jar`) executed **before every scenario**, so every test starts from the same data. |
| `args` | – | Extra `postgres` arguments, for example `["-c", "max_connections=300"]`. |
| `resources` | 0.5–1 CPU, 0.5–1 Gi | |

### `metrics`

| Option | Default | |
|---|---|---|
| `jmx` | `true` | Attach a metrics agent (JVM, Tomcat and HikariCP, through JMX) with no change to the application. |
| `profiling` | automatic | Flame graph of each scenario (async-profiler). On for JARs the lab builds, off for ready images (an Alpine image cannot load the agent). `true` / `false` forces it. Needs `monitoring.profiling.enabled` in `config/lab.yaml`. |

### `client` (optional): what the generators need to call the application

| Option | |
|---|---|
| `properties` | JMeter properties (`-J`), for example an API key: `api_key: ${MY_API_KEY}`. Variables are read from your environment and the value is **never archived**: it is masked in every file kept with the results. A variable that is not set is an error. |
| `keystore`, `truststore` | `{file, password, type}` for mutual TLS. The files are copied to the generators separately from the plan. |

### `slo`: acceptance criteria of every scenario

| Option | Default | |
|---|---|---|
| `p95_ms`, `p99_ms` | 500, 1500 | Latency objectives (milliseconds). |
| `error_rate_pct` | `1.0` | Percentage of requests that may fail **because of the server**: 5xx, timeouts, connection errors and failed assertions. 4xx answers are client behaviour and are reported, not counted. |
| `min_throughput_rps` | – | Minimum successful requests per second. |
| `endpoints` | – | Objectives for one endpoint, keyed by path (`/api/saldo/{id}`) or by `METHOD /path`: `p95_ms`, `p99_ms`, `error_rate_pct`. |

A scenario **fails** when it breaks one of these, when the application restarts during the test, or when it is worse
than a baseline run (`run --baseline`, tolerance in `regression`).
