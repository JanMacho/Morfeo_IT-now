"""Running one scenario: prepare the application, start the generators together, follow the test, collect everything."""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import profiling, state
from ..analysis.summary import iso
from ..cluster import ensure_lab_images
from ..config import Target, client_properties
from ..console import LIVE, dim, info, kv, ok, step, task, warn
from ..deploy import app_pods, reset_data, restart_app, wait_ready
from ..errors import LabError
from ..images import Images, generator_image
from ..kube import Kube, render
from ..paths import rel
from ..prom import Prom, app_queries
from ..scenario import Scenario, choose_generators, compile_bundle, load_text, secret_values
from ..units import fmt_duration, parse_duration
from .collect import collect_profile, jmeter_dashboard, log_offsets, pod_changes, redact, save_replica_logs
from .generators import LiveStats, Roster, explain_jmeter_failure, in_parallel, wait_generators, watch
from .outputs import finalize

MODEL_TEXT = {"closed": "closed model (users)", "throughput": "closed model at a fixed rate (req/s)",
              "open": "open model (arrivals/s)", "mixed": "mixed model"}
SECRET_NAME = re.compile(r"PASSWORD|SECRET|TOKEN|KEY|CREDENTIAL|PASSPHRASE", re.I)


@contextlib.contextmanager
def prometheus(lab: dict, kube: Kube):
    """A Prometheus client for the run: the NodePort kind publishes on localhost, or a port-forward."""
    if not lab["monitoring"]["enabled"]:
        yield None
        return
    p = Prom(f"http://localhost:{lab['cluster']['ports']['prometheus']}")
    if p.up():
        yield p
        return
    with contextlib.ExitStack() as stack:
        prom = None
        try:
            port = stack.enter_context(kube.port_forward(lab["namespaces"]["monitoring"], "svc/prometheus", 9090))
            prom = Prom(f"http://localhost:{port}")
            prom = prom if prom.up() else None
        except LabError as e:
            warn(f"Could not connect to Prometheus: {str(e).splitlines()[0]}")
        yield prom   # outside the try: errors of the run itself must not be swallowed here


def grafana_link(lab: dict, run_id: str, slug: str, t0: float, t1: float) -> str | None:
    mon = lab["monitoring"]
    if not (mon["enabled"] and mon["grafana"]):
        return None
    base = mon.get("grafana_url") or f"http://localhost:{lab['cluster']['ports']['grafana']}"
    return (f"{base.rstrip('/')}/d/morfeo-load-tests/load-tests?orgId=1&var-run={run_id}&var-scenario={slug}"
            f"&from={int(t0 * 1000)}&to={int(t1 * 1000)}")


def job_name(run_id: str, slug: str) -> str:
    """Kubernetes names and labels have at most 63 characters: a readable prefix and a short hash keep them unique."""
    return f"generator-{slug[:40].rstrip('-')}-{hashlib.sha1(f'{run_id}/{slug}'.encode()).hexdigest()[:6]}"


def mask_env(env: dict) -> dict:
    """The variables of the application, without the values that look like secrets."""
    return {k: ("***" if SECRET_NAME.search(k) else v) for k, v in (env or {}).items()}


def ensure_replicas(kube: Kube, ns: str, t: Target):
    """Back to the deployed number of replicas, all ready (a replica may have crashed in a previous test)."""
    dep = kube.get_json("deployment/app", "-n", ns)
    want = t.replicas
    if dep.get("spec", {}).get("replicas") not in (None, want):
        kube.kubectl("scale", "deployment/app", "-n", ns, f"--replicas={want}", capture=True, quiet=True)
    if dep.get("status", {}).get("readyReplicas", 0) != want or dep.get("spec", {}).get("replicas") != want:
        wait_ready(kube, ns, t)


def run_scenario(lab: dict, t: Target, s: Scenario, run_dir: Path, run_id: str, *, generators: int | None,
                 properties: dict[str, str], reset: bool, fresh: bool, keep_job: bool, prom: Prom | None,
                 position: str = "", jmeter_report: bool = True, baseline: dict | None = None) -> dict:
    """Run one (already validated) scenario and write its results."""
    kube = Kube(lab)
    images = Images(lab, kube)
    ns_gen, ns_app = lab["namespaces"]["generators"], lab["namespaces"]["app"]
    gen_cfg = lab["generators"]
    out_dir = run_dir / s.slug
    slo = t.data["slo"]
    scenario_info = s.info()
    planned = s.plan.planned_duration
    max_duration = parse_duration(gen_cfg["max_duration"])

    step(f"Scenario {position}{s.title}")
    kv("File", rel(s.path))
    kv("Type", MODEL_TEXT.get(s.plan.model, s.plan.model))
    kv("Load", load_text(scenario_info))
    kv("Duration", fmt_duration(planned) if planned else "set by iterations")
    count, why = choose_generators(s, gen_cfg, generators)
    kv("Generators", f"{count}  {dim('(' + why + ')')}")
    for w in s.warnings:
        warn(w)

    # ---- a known starting state: every replica ready and fresh data
    st = state.load(lab)
    if fresh:
        info("Restarting the application (cold JVM)")
        restart_app(lab, t)
    else:
        ensure_replicas(kube, ns_app, t)
    if reset:
        reset_data(lab, t)
    readers = st.get("readers") or {f: "app" for f in t.data["logs"]["files"]}
    pods_before = {p["name"]: p for p in app_pods(kube, ns_app)}
    offsets = log_offsets(kube, ns_app, readers, list(pods_before))
    profile_cfg = st.get("profiling")

    ensure_lab_images(images, lab, quiet=True)   # the cluster may have lost an image since the run started
    job = job_name(run_id, s.slug)
    deadline = int((planned or max_duration) + gen_cfg["start_timeout"] + 3600)
    manifest = render("k8s/generator-job.yaml.j2", job_name=job, ns=lab["namespaces"], run_id=run_id,
                      scenario_slug=s.slug, generators=count, deadline=deadline,
                      image=images.ref(generator_image(lab)), cpu=gen_cfg["cpu"], memory=gen_cfg["memory"],
                      jvm_heap=gen_cfg["jvm_heap"], start_timeout=gen_cfg["start_timeout"],
                      node_selector=kube.node_selector("generators"))

    def delete_job():
        kube.kubectl("delete", "job", job, "-n", ns_gen, "--ignore-not-found", "--wait=false", capture=True,
                     quiet=True, check=False)

    # ---- the bundle (kept with the results) and the private configuration (temporary, never kept)
    bundle = out_dir / "bundle"
    client = dict(t.data["client"])
    client["properties"] = client_properties(t)
    secrets_values = secret_values(client)
    with tempfile.TemporaryDirectory(prefix="morfeo-secret-") as tmp:
        secrets = Path(tmp)
        for w in compile_bundle(s, bundle, app_url=t.url(ns_app), generators=count, client=client, secrets_dir=secrets):
            warn(w)
        if properties:
            with (bundle / "morfeo.properties").open("a", encoding="utf-8") as fh:
                fh.write("".join(f"{k}={v}\n" for k, v in properties.items()))
        (bundle / "morfeo-meta.json").write_text(json.dumps({
            "plan": [[r["t"], round(r["users"], 2), round(r["rps"], 3), round(r["arrivals"], 3)]
                     for r in s.plan.timeline(1.0)],
            "stages": {g.name: g.role for g in s.plan.groups},
            "slo": {k: slo.get(k) for k in ("p95_ms", "p99_ms", "error_rate_pct")}}), encoding="utf-8")

        # ---- start: until the load starts, any problem or Ctrl+C removes the job
        kube.kubectl("delete", "job", job, "-n", ns_gen, "--ignore-not-found", "--wait=true", capture=True, quiet=True,
                     check=False)
        kube.apply(manifest)
        try:
            with task(f"Preparing {count} load generators" if count != 1 else "Preparing the load generator",
                      done=f"{count} load generators ready" if count != 1 else "Load generator ready") as tk:
                gens = wait_generators(kube, ns_gen, job, count, gen_cfg["start_timeout"], tk)
                tk.detail = "sending the scenario"
                in_parallel(lambda g: g.push_dir(bundle, "/work/in"), gens)
                if any(secrets.iterdir()):
                    in_parallel(lambda g: g.push_dir(secrets, "/work/secret"), gens)
                if profile_cfg:
                    tk.detail = "clearing the profiler"
                    t_clear = time.time()
                    profile_offsets = {pod: clock - t_clear
                                       for pod, clock in profiling.clear(kube, ns_app, list(pods_before)).items()}
        except BaseException:
            delete_job()
            raise

    # ---- a synchronised start; with a setUp, the other generators wait for generator 0 to finish it.
    # From the first `.go` on the load may be running: whatever goes wrong from here removes the job.
    interrupted = False
    try:
        t_start = time.time()
        started_at = datetime.now(timezone.utc)
        roster = Roster(kube, ns_gen, job, {g.pod for g in gens})
        hold = count > 1 and any(g.role == "setup" for g in s.plan.groups)
        if hold:   # the others wait for the setUp of generator 0: say so, or they would give up waiting
            in_parallel(lambda g: g.touch(".hold", check=True), gens[1:])
        gens[0].touch(".go", check=True)
        if hold:
            with task("Running the setUp of the plan on generator 0", done="setUp finished") as tk:
                waited, tick = time.time(), 0
                while True:
                    finished, setup_done, _ = gens[0].poll()
                    if setup_done or finished:
                        break
                    if tick % 5 == 4:
                        roster.check()
                    if time.time() - waited > max_duration:
                        raise LabError("The setUp of the plan did not finish within generators.max_duration")
                    time.sleep(1)
                    tick += 1
                    tk.detail = fmt_duration(time.time() - waited)
        in_parallel(lambda g: g.touch(".go", check=True), gens[1:])
        live = grafana_link(lab, run_id, s.slug, t_start - 60, t_start + (planned or 600) + 60) if prom else None
        ok("Test running")
        if live:
            kv("Live view", live)
        live_stats = LiveStats()
        try:
            watch(kube, live_stats, job, ns_gen, gens, t_start, planned, s.plan.ramp_up, slo.get("error_rate_pct"),
                  max_duration)
            ok(f"Test finished ({fmt_duration(time.time() - t_start)})")
        except KeyboardInterrupt:
            LIVE.stop()
            interrupted = True
            warn("Stopping the load; the results so far are kept. Press Ctrl+C again to abort without results.")
            in_parallel(lambda g: g.touch(".stop"), gens)
            with task("Stopping the generators", done="Generators stopped"):
                watch(kube, None, job, ns_gen, gens, t_start, None, 0, None, max_duration, quiet=True, timeout=180)
    except BaseException:
        LIVE.stop()
        if not keep_job:
            delete_job()
        raise
    t_end = time.time()

    # ---- collect
    raw = out_dir / "raw"
    exit_codes: list[int] = []
    try:
        with task("Collecting the results", done="Results collected"):
            in_parallel(lambda g: g.pull("/work/out", raw / f"generator-{g.index}"), gens)
            if secrets_values:
                redact(raw, secrets_values)
        for k in range(count):
            try:
                exit_codes.append(int((raw / f"generator-{k}" / "exit-code").read_text().strip()))
            except (OSError, ValueError):
                exit_codes.append(-1)
        report_failures(raw, exit_codes)
        dashboard = jmeter_dashboard(gens[0], raw, out_dir, s.title, planned, slo) if jmeter_report else None
    finally:
        try:
            in_parallel(lambda g: g.touch(".collected"), gens)
        finally:
            if not keep_job:
                delete_job()

    time.sleep(2)   # lets the asynchronous logger of the application write its last lines
    pods_after = app_pods(kube, ns_app)
    changes = pod_changes(pods_before, pods_after)
    saved = save_replica_logs(kube, ns_app, readers, pods_after, offsets, out_dir / "app", started_at)
    snapshot = prom.snapshot(app_queries(ns_app, ns_gen), t_start - 30, t_end + 10, step=5) if prom else {}
    profile = None
    if profile_cfg:
        profile = collect_profile(kube, ns_app, profile_cfg, profile_offsets, pods_after, scenario_info, out_dir,
                                  t_start, t_end)

    meta = {
        "run_id": run_id, "started_at": iso(started_at.timestamp()), "wall_clock_s": round(t_end - t_start, 1),
        "interrupted": interrupted, "scenario": scenario_info, "timeline": s.plan.timeline(1.0),
        "stages": {g.name: g.role for g in s.plan.groups},
        "labels": {"keys": s.label_keys, "methods": s.label_methods},
        "application": {"name": t.name, "description": t.data["description"], "replicas": changes["replicas"] or t.replicas,
                        "resources": t.data["runtime"]["resources"], "env": mask_env(t.data["runtime"]["env"]),
                        "image": st.get("image")},
        "slo": slo,
        "generators": {"count": count, "why": why, "exit_codes": exit_codes,
                       "distribution": {"mode": s.distribution.mode, "reasons": s.distribution.reasons}},
        "pods": changes,
        "links": {"jmeter_report": dashboard, "grafana": grafana_link(lab, run_id, s.slug, t_start - 30, t_end + 30),
                  "flamegraph": (profile or {}).get("files", {}).get("flamegraph"), "app_logs": "app/"},
        "traffic_log": {"files": saved} if saved else None,
        "profile": profile, "baseline": baseline,
    }
    return finalize(out_dir, meta, snapshot)


def report_failures(raw: Path, exit_codes: list[int]):
    """Say what went wrong when JMeter did not finish normally, in plain words."""
    for k, code in enumerate(exit_codes):
        if code in (0, -1):
            continue
        err = raw / f"generator-{k}" / "error.txt"
        lines = [x.strip() for x in err.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()] \
            if err.exists() else []
        cause = explain_jmeter_failure(lines)
        warn(f"JMeter on generator {k} ended with code {code}"
             + (f": {cause}" if cause else "") + ("\n    " + "\n    ".join(lines[:6]) if lines else ""))
        return

