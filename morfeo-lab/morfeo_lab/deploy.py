"""`morfeo-lab deploy <application>`: put any Java application in the lab."""
from __future__ import annotations

import hashlib
import json
import posixpath
import shutil
import tempfile
import time
from pathlib import Path

from . import profiling, state
from .cluster import ensure_lab_images, tools_for
from .config import Target, dependency_env
from .console import dim, hint, kv, ok, step, task, warn
from .errors import LabError
from .images import RUNTIME_DIR, Images, agents_image, app_image
from .kube import Kube, render
from .paths import rel
from .shell import cmd_name, require_tools, run
from .units import plural

JMX_AGENT_OPTIONS = "-javaagent:/morfeo-agent/jmx.jar=9404:/morfeo-agent/config.yaml"

# Pod states that will not fix themselves: stop waiting and explain
STUCK = {
    "ErrImagePull": "the cluster cannot pull the image (no internet access, or a private registry)",
    "ImagePullBackOff": "the cluster cannot pull the image (no internet access, or a private registry)",
    "ErrImageNeverPull": f"the image is not loaded into the cluster: run `{cmd_name()} up` again",
    "InvalidImageName": "the image name is not valid",
    "CreateContainerConfigError": "an environment variable or volume is wrong",
    "CrashLoopBackOff": "the application keeps crashing at start-up: see its log below",
}


def logs_layout(t: Target) -> tuple[str | None, dict[str, str]]:
    """Where the traffic log of the application lives and which container can read each file.

    The lab mounts a shared volume on the log folder when that is safe (the application is told the path through
    `logs.env`, or the image is the lab's own runtime image whose working folder is empty). Then the small
    `morfeo-tools` container can read the logs even when the application image has no shell. Otherwise the lab reads
    the file from the application container itself."""
    files = t.data["logs"].get("files") or []
    if not files:
        return None, {}
    first_dir = posixpath.normpath(posixpath.dirname(files[0]))
    safe = bool(t.data["logs"].get("env")) or t.image is None
    # never hide something the pod needs: the JAR, the lab's own mounts, system folders
    reserved = {"/", "/tmp", "/opt", "/opt/app", "/morfeo-agent", "/usr", "/bin", "/lib", "/etc", "/var", "/home"}
    logs_dir = first_dir if safe and first_dir not in reserved else None
    readers = {f: ("morfeo-tools" if logs_dir and posixpath.dirname(f) == logs_dir else "app") for f in files}
    return logs_dir, readers


def deploy(lab: dict, t: Target, *, restart: bool = False):
    require_tools(*tools_for(lab))
    kube = Kube(lab)
    kube.require_cluster()
    images = Images(lab, kube)
    ns = lab["namespaces"]["app"]
    mon = lab["monitoring"]

    step(f"Application '{t.name}'")
    image = app_image(t)
    if t.image:
        # a ready-made image: preloaded on kind, k3s and minikube; other clusters pull it themselves
        kv("Image", f"{image} (supplied)")
        images.ensure_third_party(image)
        image_ref = image
    else:
        kv("JAR", rel(t.jar))
        changed = images.ensure(image, lambda: _build_from_jar(images, t, image), name="application image")
        image_ref = images.ref(image)
        if not changed:
            ok(f"Image up to date  {dim(image)}")
    ensure_lab_images(images, lab, quiet=True)
    if not t.data["logs"].get("files"):
        warn("This application has no logs.files: the lab cannot compare its own measurements with the traffic log "
             "of the application.")

    jmx = bool(t.data["metrics"]["jmx"]) and mon["enabled"]
    pg = t.postgres
    if pg:
        step("Database")
        images.ensure_third_party(pg["image"])
        exporter = mon["enabled"] and mon["postgres_exporter"]
        if exporter:
            images.ensure_third_party(mon["postgres_exporter_image"])
        kube.apply(render("k8s/postgres.yaml.j2", pg=pg, ns=lab["namespaces"], node_selector=kube.node_selector("app"),
                          exporter_image=mon["postgres_exporter_image"] if exporter else None,
                          system_selector=kube.node_selector("system")))
        with task("Starting PostgreSQL", done=f"PostgreSQL ready (database '{pg['database']}')"):
            res = kube.kubectl("rollout", "status", "deployment/postgres", "-n", ns, "--timeout=300s",
                               check=False, capture=True, quiet=True)
            if res.returncode != 0:
                raise LabError("PostgreSQL did not become ready:\n" + pod_problems(kube, ns, "app.kubernetes.io/name=postgres"))

    step(f"Deployment ({plural(t.replicas, 'replica', 'replicas')})")
    env = dict(dependency_env(t.data, ns))
    logs_dir, readers = logs_layout(t)
    if t.data["logs"].get("env") and t.data["logs"]["files"]:
        env[t.data["logs"]["env"]] = t.data["logs"]["files"][0]
    user_env = {k: str(v) for k, v in (t.data["runtime"].get("env") or {}).items()}
    profile = profiling.enabled(lab, t.data, client_image=bool(t.image))
    base_options = user_env.pop("JAVA_TOOL_OPTIONS", "")
    env.update(user_env)

    def apply(with_profiler: bool):
        options = " ".join(x for x in (base_options, JMX_AGENT_OPTIONS if jmx else "",
                                       profiling.agent_option(lab) if with_profiler else "") if x)
        config_hash = hashlib.sha256(json.dumps([image, env, options, t.data["runtime"]], sort_keys=True,
                                                default=str).encode()).hexdigest()[:12]
        kube.apply(render("k8s/app.yaml.j2", t=t.data, ns=lab["namespaces"], image=image_ref, env=env,
                          logs_dir=logs_dir, tools=bool(logs_dir) or with_profiler, jmx=jmx, profiling=with_profiler,
                          agents_image=images.ref(agents_image()), java_tool_options=options,
                          node_selector=kube.node_selector("app"), config_hash=config_hash,
                          nodeport=kube.provider == "kind"))

    apply(profile)
    if restart:
        kube.kubectl("rollout", "restart", "deployment/app", "-n", ns, capture=True, quiet=True)
    try:
        wait_ready(kube, ns, t)
    except LabError as e:
        # The profiler is a native library: an image without glibc (Alpine) cannot load it and the JVM refuses to
        # start. The application must never pay for a measurement feature.
        if not (profile and "libasyncProfiler" in str(e)):
            raise
        lines = [x.strip() for x in str(e).splitlines() if "libasyncProfiler" in x]
        why = next((x for x in lines if "agent library" in x or "error" in x.lower()), lines[0] if lines else "")
        warn("The profiler cannot run in this image, so the application is deployed without it.\n    " + why[:200]
             + f"\n    To skip this check next time, set metrics.profiling: false in {t.path.name}")
        profile = False
        apply(False)
        wait_ready(kube, ns, t)
    reset_data(lab, t)   # after the application created its schema
    state.save(lab, {"target": str(t.path), "target_name": t.name, "image": image, "replicas": t.replicas,
                     "jar": str(t.jar) if not t.image else None, "port": t.port, "readers": readers,
                     "deployed_at": time.time(),
                     "profiling": ({k: lab["monitoring"]["profiling"][k] for k in ("event", "chunk")}
                                   if profile else None)})

    step("Application deployed")
    kv("In the cluster", t.url(ns), width=18)
    if profile:
        kv("Profiling", f"{profiling.EVENT_TEXT[lab['monitoring']['profiling']['event']]} "
                        "(a flame graph for every scenario)", width=18)
    if kube.provider == "kind":
        kv("From this machine", f"http://localhost:{lab['cluster']['ports']['app']}", width=18)
    hint(f"Next step: {cmd_name()} validate <plan.jmx>, then {cmd_name()} run <plan.jmx>")


def _build_from_jar(images: Images, t: Target, image: str):
    java = t.data["artifact"].get("java_version", 21)
    with tempfile.TemporaryDirectory() as ctx:
        shutil.copy2(t.jar, Path(ctx) / "app.jar")
        images.build(image, Path(ctx), dockerfile=RUNTIME_DIR / "Dockerfile", build_args={"JAVA_VERSION": java},
                     label=f"Building the application image (Java {java})", done="Application image built")


def pod_problems(kube: Kube, ns: str, selector: str) -> str:
    """Why pods are not ready, in plain words (image, memory, crash, probes), plus the last lines of their log."""
    lines = []
    for p in kube.get_json("pods", "-n", ns, "-l", selector).get("items", []):
        name = p["metadata"]["name"]
        status = p.get("status", {})
        for cs in (status.get("initContainerStatuses") or []) + (status.get("containerStatuses") or []):
            waiting = (cs.get("state") or {}).get("waiting") or {}
            last = (cs.get("lastState") or {}).get("terminated") or {}
            if waiting.get("reason"):
                lines.append(f"  {name}/{cs['name']}: {waiting['reason']} · {STUCK.get(waiting['reason'], '')}".rstrip(" ·"))
            if last.get("reason"):
                lines.append(f"  {name}/{cs['name']}: last exit {last.get('reason')} (code {last.get('exitCode')})"
                             + (": raise runtime.resources.limits.memory or lower the heap"
                                if last.get("reason") == "OOMKilled" else ""))
        for cond in status.get("conditions") or []:
            if cond.get("reason") == "Unschedulable":
                lines.append(f"  {name}: it does not fit on any node · {cond.get('message', '')[:200]}\n"
                             "    Not enough free CPU or memory: lower runtime.resources, use fewer replicas or give "
                             "Docker more memory.")
        main = p["spec"]["containers"][0]["name"]
        restarted = any(cs.get("restartCount") for cs in (status.get("containerStatuses") or []))
        logs = kube.kout("logs", "-n", ns, name, "-c", main, "--tail=30", *(["--previous"] if restarted else []),
                         check=False, timeout=20)
        if logs:
            lines.append(f"  --- last log lines of {name} ---\n" + "\n".join("    " + x for x in logs.splitlines()))
    events = kube.kout("get", "events", "-n", ns, "--sort-by=.lastTimestamp", check=False, timeout=20)
    if events:
        lines.append("  --- recent events ---\n" + "\n".join("    " + x for x in events.splitlines()[-8:]))
    return "\n".join(lines) or "  (no pod information available)"


_PHASES = {"PodInitializing": "initialising", "ContainerCreating": "creating the container", "Pending": "pending",
           "Running": "starting"}


def _progress(kube: Kube, ns: str, want: int) -> tuple[bool, str, str | None]:
    """(ready?, a short status for the spinner, the reason if it is stuck)."""
    dep = kube.get_json("deployment/app", "-n", ns)
    st, generation = dep.get("status", {}), dep.get("metadata", {}).get("generation", 0)
    ready = (st.get("observedGeneration", 0) >= generation and st.get("updatedReplicas", 0) == want
             and st.get("readyReplicas", 0) == want and st.get("replicas", 0) == want)
    pods = kube.get_json("pods", "-n", ns, "-l", "app.kubernetes.io/name=app").get("items", [])
    phases, stuck = [], None
    for p in pods:
        if p["metadata"].get("deletionTimestamp"):
            continue
        for cs in p.get("status", {}).get("containerStatuses") or []:
            reason = ((cs.get("state") or {}).get("waiting") or {}).get("reason")
            if reason in STUCK and (reason != "CrashLoopBackOff" or cs.get("restartCount", 0) >= 3):
                stuck = reason
        app = next((c for c in p.get("status", {}).get("containerStatuses") or [] if c["name"] == "app"), {})
        if app.get("ready"):
            phases.append("ready")
        elif (app.get("state") or {}).get("running"):
            phases.append("starting the JVM")
        else:
            raw = ((app.get("state") or {}).get("waiting") or {}).get("reason") or p.get("status", {}).get("phase", "?")
            phases.append(_PHASES.get(raw, raw))
    n_ready = sum(1 for x in phases if x == "ready")
    detail = f"{n_ready}/{want} ready" + (f" · {', '.join(sorted({x for x in phases if x != 'ready'}))}"
                                          if n_ready < want and phases else "")
    return ready, detail, stuck


def wait_ready(kube: Kube, ns: str, t: Target):
    timeout = int(t.data["health"]["startup_timeout"]) + 90
    with task("Starting the application", done="Application ready") as tk:
        deadline = time.time() + timeout
        while True:
            ready, tk.detail, stuck = _progress(kube, ns, t.replicas)
            if ready:
                return
            if stuck or time.time() > deadline:
                why = (f"is stuck ({stuck})" if stuck else
                       f"was not ready within {timeout} s (health.startup_timeout in {t.path.name})")
                raise LabError(f"The application {why}.\n" + pod_problems(kube, ns, "app.kubernetes.io/name=app"))
            time.sleep(2)


def reset_data(lab: dict, t: Target) -> bool:
    """Run the reset SQL of the application so that every scenario starts from the same data."""
    pg = t.postgres
    if not pg or not pg.get("_reset_sql_path"):
        return False
    kube = Kube(lab)
    sql = Path(pg["_reset_sql_path"]).read_text(encoding="utf-8")
    res = None
    for _ in range(10):   # the database pod may still be starting
        res = run(kube.k("exec", "-i", "-n", lab["namespaces"]["app"], "deployment/postgres", "--", "psql", "-v",
                         "ON_ERROR_STOP=1", "-U", pg["user"], "-d", pg["database"], "-q"), input=sql, capture=True,
                  quiet=True, timeout=120, check=False)
        if res.returncode == 0:
            ok(f"Database restored ({Path(pg['_reset_sql_path']).name})")
            return True
        if "ERROR:" in (res.stderr or ""):   # the SQL itself is wrong: trying again will not help
            break
        time.sleep(3)
    raise LabError(f"Could not restore the database with {pg['_reset_sql_path']}:\n"
                   f"{((res.stderr or res.stdout) if res else '').strip()[-600:]}")


def restart_app(lab: dict, t: Target):
    """A fresh JVM with an empty in-memory state: makes runs comparable with each other."""
    kube = Kube(lab)
    kube.kubectl("rollout", "restart", "deployment/app", "-n", lab["namespaces"]["app"], capture=True, quiet=True)
    time.sleep(2)
    wait_ready(kube, lab["namespaces"]["app"], t)


def app_pods(kube: Kube, ns: str) -> list[dict]:
    pods = []
    for p in kube.get_json("pods", "-n", ns, "-l", "app.kubernetes.io/name=app").get("items", []):
        if p["metadata"].get("deletionTimestamp"):
            continue
        cs = next((c for c in (p.get("status", {}).get("containerStatuses") or []) if c.get("name") == "app"), {})
        last = (cs.get("lastState") or {}).get("terminated") or {}
        pods.append({
            "name": p["metadata"]["name"], "node": p.get("spec", {}).get("nodeName"),
            "restarts": cs.get("restartCount", 0), "ready": cs.get("ready", False),
            "last_termination_reason": last.get("reason"), "phase": p.get("status", {}).get("phase"),
            "containers": [c["name"] for c in p.get("spec", {}).get("containers", [])],
        })
    return pods

