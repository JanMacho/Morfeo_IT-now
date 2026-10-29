# Plans (scenarios)

A *scenario* is a JMeter test plan (`.jmx`) written by the AI engine. The lab **runs it as it is**: it adds no
requests, no pacing and no users. Everything described here is about what the lab reads from the plan, the few things
it has to change to run it in the cluster, and how it divides the plan between generators.

Put plans in `scenarios/`, or name them on the command line. Everything below applies equally to `validate` and `run`.

## 1. What the lab reads

| In the plan | The lab understands |
|---|---|
| Thread Group, setUp, tearDown | users, ramp-up, iterations, duration, start delay (scheduler) |
| Concurrency Thread Group | target concurrency, ramp, steps, hold time |
| Arrivals / Free-Form Arrivals Thread Group | arrival rate (per second, minute or hour), ramp, steps, hold time |
| Ultimate / Stepping Thread Group | the load profile |
| Open Model Thread Group | the arrival schedule (`rate(…) random_arrivals(…)`) |
| Constant / Precise / Throughput Shaping Timer | requests per second (and its profile over time) |
| JSR223 / BeanShell timers | a *scripted pacing*: the plan paces itself (see §4) |
| HTTP samplers | method, path, endpoint name |
| Transaction Controllers | not counted as requests (their requests are counted one by one) |
| CSV Data Set Config, files used by samplers | data files, which must be next to the plan |
| User Defined Variables, `${__P(name,default)}`, `${__property(…)}` | resolved when the value matters (hosts, ports, thread counts) |

Plugins come from the generator image. `generators.jmeter_plugins` in `config/lab.yaml` lists the plugin ids it
contains (versions and checksums are pinned in `images/generator/plugins.json`). A plan that needs another plugin is
refused with the exact line to add:

```
p.jmx is not valid:
  - 'Ultimate' needs the JMeter plugin 'jpgc-casutg', which the generator image does not include.
    Add it to generators.jmeter_plugins in config/lab.yaml and run `morfeo-lab up`.
```

### What makes a plan invalid

`validate` and `run` refuse a plan, saying why, when:

- the file is not a JMeter plan, or has no enabled thread group, or sends no HTTP request;
- it needs a plugin the image does not have (above);
- it reads a data file that is not next to the plan, or includes another plan (`Include Controller`);
- its planned duration is longer than `generators.max_duration`;
- `--generators N` asks for more generators than the plan can be divided into (§3).

Everything else is accepted. Things the lab cannot interpret (a load that depends on a variable only known during the
test, an unknown thread group) are reported as *warnings* and make the plan run on a single generator.

## 2. What the lab changes

When nothing needs changing, the plan is sent **byte for byte**. Otherwise only this is done, to a copy:

1. **Hosts.** A request to `localhost`, `127.0.0.1`, `::1`, `0.0.0.0` or with no host — including
   `${__P(host,localhost)}` whose default resolves to a local address — goes to the deployed application
   (`app.<namespace>.svc.cluster.local`). The port and protocol are those of the application file. Requests to any
   other host are sent as they are (the lab says so).
2. **Listeners and disabled elements** are removed: the lab records every sample itself and listeners would write
   files nobody reads.
3. **Data files** found next to the plan are copied to the generators and referred to by a relative path.
4. **With several generators** each one receives its share of every thread group and timer (§3). setUp and tearDown
   thread groups exist on generator 0 only.

The properties passed to JMeter (`-J`) are:

| Property | Value |
|---|---|
| `morfeo_url`, `morfeo_host`, `morfeo_port`, `morfeo_protocol` | where the application is |
| `morfeo_generator` | index of this generator (0, 1, …) |
| `morfeo_generators` | how many generators run the scenario |
| the entries of `client.properties` in the application file | API keys and the like (never archived) |
| `--prop name=value` | whatever you pass to `run` |

The plan can use them (`${__P(morfeo_url)}`) or ignore them.

## 3. Several generators: dividing the load exactly

One generator has limits (CPU, threads), so a plan asking for a lot of load needs several. The lab uses as many as
needed (`generators.max_threads_per_generator`, `generators.max_rps_per_generator`, up to `generators.max_generators`)
or the number you give with `--generators N`. The rule is simple: **the total load is the load of the plan, whatever
the number of generators.**

Each plan falls in one of three modes (shown by `validate --show`):

| Mode | When | What the lab does |
|---|---|---|
| **split** | Every thread group and timer is one the lab can divide | Gives each generator its share: thread counts are split as evenly as whole numbers allow (the shares always add up to the total), arrival rates and throughput are divided exactly. The Open Model Thread Group gets a different random seed on each generator, so the arrivals do not coincide. |
| **single** | The plan paces itself with a script, depends on a variable only known during the test, or has a group the lab cannot read (for example a Stepping Thread Group) | Runs on **one** generator. `--generators N` with N > 1 is refused, with the reasons. |
| **plan** | The plan reads `morfeo_generators` / `morfeo_generator` | The lab does not touch the load: the plan divides it itself, and the lab only passes the two properties. |

The plans of the AI engine pace their arrivals with a Groovy timer (a Poisson process written in the plan), so they
are *single* and run on one generator. To make such a plan scale, the timer needs to divide its own rate by the number
of generators, for example:

```groovy
int generators = Integer.parseInt(props.getProperty("morfeo_generators", "1"))
double rate = <rate per second of the plan> / generators
```

and each generator should use a different random seed (`seed + morfeo_generator`) and an id prefix that includes
`${__P(morfeo_generator,0)}`, so that identifiers generated by different generators do not collide. A plan that does
this is in *plan* mode and can use any number of generators.

Notes the lab prints when it matters:

- `${__threadNum}` repeats on every generator: ids built from it may collide.
- Data files are read from the start by every generator (the rows are not divided).

### setUp and tearDown

setUp and tearDown thread groups run **once**, on generator 0. With several generators, the others wait until the
setUp of generator 0 has finished before they start their load. The tearDown runs when generator 0 finishes its own
groups, which can be before another generator has finished.

Samples of setUp and tearDown are reported separately and are never mixed into the throughput and latency of the load
(`setup` and `teardown` in `summary.json`), but a failing setUp fails the run, because the test would be distorted.

## 4. Warm-up and the load that is measured

The headline figures of a scenario (`steady` in `summary.json`) skip the ramp-up of the plan: the load that is judged
is the one at full strength. If the ramp-up is most of the test, the whole run is used. The `overall` figures cover
everything. Both are in `summary.json` and the results page says which one it shows.

The lab also compares what the plan *asked for* with what the generators *achieved*. If a plan asks for 300 requests
per second and 200 are achieved, the run says whether the application could not keep up or the generator was the
bottleneck (a generator that uses most of its CPU or memory is reported).

## 5. `validate --json` (for CI)

`morfeo-lab validate plan.jmx --json` needs no cluster and prints only JSON on stdout. The exit code is `0` when every
plan is valid and `1` otherwise.

```json
{
  "schema_version": 1,
  "producer": "morfeo-lab",
  "lab_version": "1.0.0",
  "valid": true,
  "errors": [],
  "scenarios": [
    {
      "file": "scenarios/plan.jmx",
      "valid": true,
      "errors": [],
      "name": "plan",
      "title": "…",
      "model": "closed",
      "planned_duration_s": 227.0,
      "generators": 1,
      "generators_reason": "…",
      "distribution": {"mode": "single", "reasons": ["…"]},
      "thread_groups": [{"name": "…", "type": "ThreadGroup", "role": "main", "model": "closed", "threads": 42,
                          "peak": 42.0, "peak_rps": null, "start_s": 0.0, "duration_s": 227.0, "iterations": null,
                          "scripted_pacing": true}],
      "endpoints": ["GET /api/saldo/{id}", "POST /api/ingreso"],
      "data_files": [],
      "warnings": []
    }
  ]
}
```

For a plan that is not valid, `valid` is `false` and `errors` lists the reasons; the other fields are absent. The
top-level `errors` is used when the files themselves cannot be found. `schema_version` changes only if a key is
renamed or removed; readers must ignore keys they do not know.

`model` is `closed` (users), `open` (arrivals), `throughput` (requests per second) or `mixed`.
