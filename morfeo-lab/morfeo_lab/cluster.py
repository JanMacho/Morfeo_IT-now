"""`morfeo-lab up | down | status | dashboard`: the lab infrastructure."""
from __future__ import annotations

import contextlib
import hashlib
import tempfile
import time
from pathlib import Path

from . import dashboard as grafana_dashboard
from . import state
from .console import dim, hint, info, kv, ok, step, table, task, warn
from .errors import LabError
from .images import AGENTS_DIR, GENERATOR_DIR, Images, agents_image, generator_image
from .kube import Kube, render
from .shell import cmd_name, out, require_tools, run_streaming
from .units import plural

POOLS = ("app", "generators", "system")   # system: Prometheus, Grafana and the database exporter


def tools_for(lab: dict) -> list[str]:
    return ["docker", "kubectl", *(["kind"] if lab["cluster"]["provider"] == "kind" else [])]


def ensure_lab_images(images: Images, lab: dict, quiet: bool = False):
    """Build what is missing and load again what the cluster lost. `quiet`: report only what changed."""
    wanted = (
        ("Load generator", generator_image(lab), lambda i: images.build(
            i, GENERATOR_DIR, build_args={"JMETER_PLUGINS": ",".join(lab["generators"]["jmeter_plugins"])},
            label="Building the load generator (JMeter and plugins)", done="Load generator built")),
        ("Application agents", agents_image(), lambda i: images.build(
            i, AGENTS_DIR, label="Building the application agents (JMX metrics and profiler)",
            done="Application agents built")),
    )
    if not quiet and not images.exists_locally(generator_image(lab)):
        info("The first build downloads JMeter and its plugins (about 100 MB) and checks them with a short test.")
    for name, image, build in wanted:
        changed = images.ensure(image, lambda i=image, b=build: b(i), name=f"{name.lower()} image")
        if not quiet and not changed:
            ok(f"{name} up to date  {dim(image)}")


def _check_pools(kube: Kube):
    """With separate node pools, every pool needs a node. kind creates them; on an existing cluster they are
    explained instead of guessed."""
    nodes = kube.nodes()
    pools = {n["metadata"].get("labels", {}).get("morfeo.io/pool") for n in nodes}
    missing = [p for p in POOLS if p not in pools]
    if missing and kube.provider != "kind":
        free = [n["metadata"]["name"] for n in nodes if not n["metadata"].get("labels", {}).get("morfeo.io/pool")]
        advice = ("Label the nodes (the application, the generators and the monitoring on different nodes), for example:\n"
                  + "".join(f"  kubectl label node {n} morfeo.io/pool={p} --overwrite\n"
                            for n, p in zip(free, missing, strict=False)))
        if len(free) < len(missing):
            advice = (f"The cluster has {plural(len(nodes), 'node', 'nodes')} and the pools need {len(POOLS)}; "
                      "there are not enough nodes to keep them apart.\n")
        raise LabError("cluster.pools is enabled, but no node is labelled for: " + ", ".join(missing) + ".\n" + advice
                       + "or set cluster.pools: false in config/lab.yaml (single-node or small clusters).")


def _create_kind(kube: Kube, c: dict):
    config = render("kind/cluster.yaml.j2", cluster=c)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
        fh.write(config)
    nodes = 1 + sum(c["nodes"].values())
    try:
        with task(f"Creating the cluster '{kube.cluster}' ({nodes} nodes)", done=f"Cluster '{kube.cluster}' created") as t:
            def on_line(line):
                clean = line.strip().lstrip("✓•✗ ").strip()
                if clean and not clean.startswith(("Set kubectl", "You can now", "kubectl cluster-info", "Have a ",
                                                   "Thanks for", "Not sure")):
                    t.detail = clean[:60]
            res = run_streaming(["kind", "create", "cluster", "--config", fh.name, "--wait", "180s"],
                                on_line=on_line, timeout=1200)
            if res.returncode != 0:
                raise LabError("Could not create the kind cluster:\n" + "\n".join(res.stdout.splitlines()[-12:])
                               + f"\nIf it was left half-created, run `kind delete cluster --name {kube.cluster}` "
                                 "and try again.")
    finally:
        Path(fh.name).unlink(missing_ok=True)


def _has_default_storage(kube: Kube) -> bool:
    return any(sc.get("metadata", {}).get("annotations", {}).get("storageclass.kubernetes.io/is-default-class") == "true"
               for sc in kube.get_json("storageclass").get("items", []))


def up(lab: dict):
    require_tools(*tools_for(lab))
    kube = Kube(lab)
    images = Images(lab, kube)
    c, mon = lab["cluster"], lab["monitoring"]

    step("Cluster")
    if c["provider"] == "kind":
        if kube.cluster_exists():
            ok(f"Cluster '{kube.cluster}' is running")
        else:
            _create_kind(kube, c)
    else:
        kube.require_cluster()
        ok(f"Context '{kube.context}' · {plural(len(kube.nodes()), 'node', 'nodes')}")
        if not kube.pools:
            warn("cluster.pools is false: the application and the generators may share a node.\n"
                 "That is fine for functional tests; keep them apart to measure capacity.")
    if kube.pools:
        _check_pools(kube)
    kv("Image loader", images.loader)

    step("Namespaces")
    kube.apply(render("k8s/namespaces.yaml.j2", ns=lab["namespaces"]))
    ok(" · ".join(lab["namespaces"].values()))

    step("Lab images")
    ensure_lab_images(images, lab)

    grafana = mon["enabled"] and mon["grafana"]
    if mon["enabled"]:
        step("Monitoring")
        for image in [mon["prometheus_image"], *([mon["grafana_image"]] if grafana else [])]:
            images.ensure_third_party(image)
        persistent = bool(mon["persistent"]) and _has_default_storage(kube)
        if mon["persistent"] and not persistent:
            warn("The cluster has no default StorageClass: Prometheus loses its history if its pod restarts")
        dashboards = grafana_dashboard.render_all(lab) if grafana else {}
        digest = hashlib.sha256("".join(dashboards.values()).encode()).hexdigest()[:12]
        ctx = {"ns": lab["namespaces"], "mon": mon, "pools": kube.pools, "persistent": persistent,
               "dashboards_hash": digest}
        manifest = render("monitoring/stack.yaml.j2", config_hash="", **ctx)
        kube.apply(render("monitoring/stack.yaml.j2", config_hash=hashlib.sha256(manifest.encode()).hexdigest()[:12],
                          **ctx))
        if grafana:
            kube.apply(grafana_dashboard.config_map(lab, dashboards))
        ns = lab["namespaces"]["monitoring"]
        for name in ("prometheus", *(("grafana",) if grafana else ())):
            with task(f"Starting {name.capitalize()}", done=f"{name.capitalize()} running"):
                res = kube.kubectl("rollout", "status", f"deployment/{name}", "-n", ns, "--timeout=300s",
                                   capture=True, quiet=True, check=False)
            if res.returncode != 0:
                warn(f"{name.capitalize()} is not ready: {(res.stderr or '').strip()[:200]}\n"
                     f"Details: kubectl -n {ns} describe deployment/{name}")

    step("Lab ready")
    access(lab, kube)
    hint(f"Next step: {cmd_name()} deploy <application>   (for example: {cmd_name()} deploy bank-api)")


def access(lab: dict, kube: Kube, app: bool = False):
    p, mon = lab["cluster"]["ports"], lab["monitoring"]
    if kube.provider == "kind":
        if mon["enabled"] and mon["grafana"]:
            kv("Grafana", f"http://localhost:{p['grafana']}")
        if mon["enabled"]:
            kv("Prometheus", f"http://localhost:{p['prometheus']}")
        if app:
            kv("Application", f"http://localhost:{p['app']}")
    else:
        what = ("Grafana, Prometheus and the application" if mon["enabled"] and mon["grafana"]
                else "Prometheus and the application" if mon["enabled"] else "the application")
        kv("Local access", f"{cmd_name()} dashboard   (forwards {what} to this machine)")


def down(lab: dict, destroy: bool):
    kube = Kube(lab)
    require_tools(*(["kubectl", *(["kind"] if kube.provider == "kind" else [])]))
    if not kube.cluster_exists():
        ok("Nothing to clean up: the cluster does not exist")
        return
    if destroy:
        if kube.provider != "kind":
            step("Lab namespaces")
            with task("Deleting the lab namespaces", done="Lab namespaces deleted (the cluster and results/ are kept)"):
                for ns in lab["namespaces"].values():
                    kube.kubectl("delete", "namespace", ns, "--ignore-not-found", "--wait=false", capture=True,
                                 quiet=True, check=False)
                kube.kubectl("delete", "clusterrolebinding,clusterrole", "morfeo-prometheus", "--ignore-not-found",
                             capture=True, quiet=True, check=False)
            state.clear(lab)
            return
        step(f"Cluster '{kube.cluster}'")
        with task("Deleting the cluster", done="Cluster deleted (results/ is kept)"):
            res = run_streaming(["kind", "delete", "cluster", "--name", kube.cluster], timeout=600)
            if res.returncode != 0:
                raise LabError("Could not delete the cluster:\n" + res.stdout[-800:])
        state.clear(lab)
        return
    step("Application and generators")
    with task("Removing the application and the generators",
              done="Application and generators removed (the cluster and the monitoring are kept)"):
        kube.kubectl("delete", "jobs", "--all", "-n", lab["namespaces"]["generators"], "--ignore-not-found",
                     capture=True, quiet=True, check=False)
        kube.kubectl("delete", "deployment,service", "--all", "-n", lab["namespaces"]["app"], "--ignore-not-found",
                     capture=True, quiet=True, check=False)
    state.clear(lab)
    hint(f"Delete the whole lab: {cmd_name()} down --destroy")


def status(lab: dict):
    kube = Kube(lab)
    require_tools(*(["kubectl", *(["kind"] if kube.provider == "kind" else [])]))
    step("Cluster")
    if not kube.cluster_exists():
        warn("The cluster is not running")
        hint(f"Create the lab: {cmd_name()} up")
        return
    kv("Provider", kube.provider + (f" · context {kube.context}" if kube.context else ""))
    rows = []
    for n in kube.nodes():
        cond = {c["type"]: c["status"] for c in n.get("status", {}).get("conditions", [])}
        rows.append([n["metadata"]["name"], n["metadata"].get("labels", {}).get("morfeo.io/pool", "–"),
                     "Ready" if cond.get("Ready") == "True" else "Not ready"])
    table(["Node", "Pool", "Status"], rows)
    titles = {"app": "Application", "generators": "Load generators", "monitoring": "Monitoring"}
    for role, ns in lab["namespaces"].items():
        step(f"{titles.get(role, role)}  {dim(ns)}")
        pods = kube.get_json("pods", "-n", ns).get("items", [])
        if not pods:
            info(dim("No pods"))
            continue
        rows = []
        for p in pods:
            cs = p.get("status", {}).get("containerStatuses") or []
            rows.append([p["metadata"]["name"], f"{sum(1 for c in cs if c.get('ready'))}/{len(cs)}",
                         p.get("status", {}).get("phase", "?"), str(sum(c.get("restartCount", 0) for c in cs)),
                         p.get("spec", {}).get("nodeName", "–")])
        table(["Pod", "Ready", "Phase", "Restarts", "Node"], rows)
    st = state.load(lab)
    step("Deployed application")
    if st.get("target_name"):
        kv("Application", st["target_name"])
        kv("Image", str(st.get("image")))
        kv("Replicas", str(st.get("replicas", 1)))
        prof = st.get("profiling")
        kv("Profiling", f"{prof['event']} · chunks of {prof['chunk']}" if prof else "off")
    else:
        info("None")
        hint(f"Deploy an application: {cmd_name()} deploy <application>")
    step("Access")
    access(lab, kube, app=True)


def dashboard(lab: dict):
    """Forward Grafana, Prometheus and the application to localhost until Ctrl+C (for clusters other than kind)."""
    kube = Kube(lab)
    kube.require_cluster()
    p = lab["cluster"]["ports"]
    mon_ns, app_ns = lab["namespaces"]["monitoring"], lab["namespaces"]["app"]
    st = state.load(lab)

    def forward(stack, ns, svc, remote, local, name):
        try:
            lp = stack.enter_context(kube.port_forward(ns, svc, remote, local))
        except LabError:   # the preferred local port is busy: any free one
            lp = stack.enter_context(kube.port_forward(ns, svc, remote))
        kv(name, f"http://localhost:{lp}")

    step("Access (port forwarding)")
    with contextlib.ExitStack() as stack:
        if lab["monitoring"]["enabled"]:
            if lab["monitoring"]["grafana"]:
                forward(stack, mon_ns, "svc/grafana", 3000, p["grafana"], "Grafana")
            forward(stack, mon_ns, "svc/prometheus", 9090, p["prometheus"], "Prometheus")
        if st.get("port") and out(kube.k("get", "svc", "app", "-n", app_ns, "--ignore-not-found"), check=False):
            forward(stack, app_ns, "svc/app", st["port"], p["app"], "Application")
        info(dim("Press Ctrl+C to close"))
        with contextlib.suppress(KeyboardInterrupt):
            while True:
                time.sleep(1)
