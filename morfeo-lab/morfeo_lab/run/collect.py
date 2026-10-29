"""Collecting what a scenario leaves behind: the traffic log of every replica, the CPU profile, the official JMeter
dashboard, and removing private values from the files that are kept."""
from __future__ import annotations

import shlex
import time
from pathlib import Path

from .. import profiling
from ..analysis import merge_jtls
from ..console import dim, info, task, warn
from ..errors import LabError
from ..kube import Kube
from ..shell import run_bytes
from ..units import num, parse_duration
from .generators import GeneratorPod


# ====================================================================== the traffic log of the application
def log_offsets(kube: Kube, ns: str, readers: dict, pods: list[str]) -> dict:
    """Where each log file ended when the test started, so that only what the test wrote is collected."""
    offsets = {}
    for pod in pods:
        for f, container in readers.items():
            raw = kube.exec(ns, pod, "sh", "-c", f"wc -c < {shlex.quote(f)} 2>/dev/null || echo 0",
                            container=container, check=False, timeout=20)
            try:
                offsets[(pod, f)] = int(raw.strip().split()[-1] or 0)
            except (ValueError, IndexError):
                offsets[(pod, f)] = 0
    return offsets


def _read_logs(kube: Kube, ns: str, readers: dict, pod: str, offsets: dict, containers) -> dict[str, bytes]:
    """What `pod` wrote to each log file since the start of the test."""
    out = {}
    for f, container in readers.items():
        c = container if containers is None or container in containers else "app"
        start = offsets.get((pod, f), 0)   # a pod that appeared during the test starts at 0
        out[f] = run_bytes(kube.k("exec", "-n", ns, pod, "-c", c, "--", "sh", "-c",
                                  f"tail -c +{start + 1} {shlex.quote(f)} 2>/dev/null || true"), check=False)
    return out


def save_replica_logs(kube: Kube, ns: str, readers: dict, pods: list[dict], offsets: dict, dst: Path, since) -> dict:
    """Save what each replica wrote to its log files during the test (app/replicas/) and its console output.
    Returns {log file: [saved paths relative to the scenario folder]}."""
    (dst / "replicas").mkdir(parents=True, exist_ok=True)
    saved: dict[str, list[str]] = {f: [] for f in readers}
    for p in pods:
        data = _read_logs(kube, ns, readers, p["name"], offsets, p.get("containers"))
        for f, content in data.items():
            name = f"replicas/{p['name']}--{Path(f).name}"
            (dst / name).write_bytes(content)
            saved[f].append(name)
        console = run_bytes(kube.k("logs", "-n", ns, p["name"], "-c", "app", f"--since-time={since.isoformat()}"),
                            check=False)
        (dst / f"{p['name']}.stdout.log").write_bytes(console)
    return saved


def pod_changes(before: dict, after: list[dict]) -> dict:
    restarts, reason = 0, None
    for p in after:
        b = before.get(p["name"])
        restarts += p["restarts"] - (b["restarts"] if b else 0)
        if (not b or p["restarts"] > b["restarts"]) and p.get("last_termination_reason"):
            reason = p["last_termination_reason"]
    replaced = [n for n in before if n not in {p["name"] for p in after}]
    if replaced:
        restarts += len(replaced)
        reason = reason or "pod replaced"
    return {"replicas": len(after), "restarts_during_run": max(0, restarts), "last_termination_reason": reason,
            "after": after}


# ====================================================================== the profile
def collect_profile(kube: Kube, ns: str, cfg: dict, offsets: dict, pods: list[dict], scenario: dict, out_dir: Path,
                    t_start: float, t_end: float) -> dict | None:
    """The profile of the steady state (after the ramp-up), every replica merged. The window comes from the moments
    the load started and ended in generator 0 (the nodes of a cluster share one clock); without them, from the clock
    of this machine translated to the one of each pod."""
    chunk = parse_duration(cfg["chunk"])
    ramp = float(scenario.get("ramp_up_s") or 0)
    try:
        raw = out_dir / "raw" / "generator-0"
        g0 = float((raw / "started-epoch").read_text().strip())
        g1 = float((raw / "finished-epoch").read_text().strip())
        lo, hi, offsets, local_end = g0 + ramp, g1, {}, t_end - 6   # the generator idles 6 s after the load
        if hi - lo < 2 * chunk:
            lo = g0
    except (OSError, ValueError):
        lo, hi, local_end = t_start + ramp, t_end, t_end
        if hi - lo < 2 * chunk:
            lo = t_start
    wait = local_end + chunk + 1 - time.time()   # the chunk that contains the end of the test must be written
    try:
        with task("Collecting the profile", done="Profile collected") as tk:
            if wait > 0:
                tk.detail = "waiting for the last chunk"
                time.sleep(wait)
                tk.detail = ""
            names = [p["name"] for p in pods if "morfeo-tools" in p.get("containers", [])]
            stacks = profiling.collect(kube, ns, names, (lo, hi), offsets, chunk)
            result = profiling.write_outputs(out_dir, stacks, cfg["event"], title=scenario.get("title") or scenario["name"],
                                             window_s=hi - lo)
    except LabError as e:
        warn(f"Could not collect the profile: {str(e).splitlines()[0]}")
        return None
    if not result.get("available"):
        info(dim("The profiler recorded no samples for this scenario"))
        return None
    return result


# ====================================================================== the JMeter dashboard
def jmeter_dashboard(gen: GeneratorPod, raw: Path, out_dir: Path, title: str, planned: float | None,
                     slo: dict) -> str | None:
    """The official JMeter HTML dashboard for the whole scenario (every generator merged), built inside a generator pod:
    JMeter lives there, not on this machine."""
    merged = out_dir / "results.jtl"
    n = merge_jtls(raw, merged)
    if not n:
        warn("No samples were recorded: the JMeter report is skipped")
        return None
    granularity = max(1000, int(max(1.0, planned or 600) * 1000 / 150) // 1000 * 1000)   # about 150 points per chart (ms)
    satisfied = int(slo.get("p95_ms") or 500)
    tolerated = int(slo.get("p99_ms") or max(satisfied * 3, 1500))
    try:
        with task("Building the JMeter report", done="JMeter report built") as tk:
            gen.sh("rm -rf /work/report && mkdir -p /work/report", timeout=30)
            tk.detail = f"sending {num(n)} samples"
            gen.push_file(merged, "/work/report", "results.jtl")
            tk.detail = ""
            res = gen.kube.kubectl("exec", "-n", gen.ns, gen.pod, "--", "/opt/morfeo/jmeter-report.sh",
                                   "/work/report/results.jtl", "/work/report/html", str(satisfied), str(tolerated),
                                   str(granularity), f"MORFEO · {title}", check=False, capture=True, quiet=True,
                                   timeout=1200)
            if res.returncode != 0:
                raise LabError((res.stderr or res.stdout or "").strip()[-300:])
            tk.detail = "downloading"
            gen.pull("/work/report/html", out_dir / "jmeter-report")
        return "jmeter-report/index.html"
    except LabError as e:
        warn(f"Could not build the JMeter report: {str(e).splitlines()[0] if str(e) else '?'}")
    return None


# ====================================================================== private values
def redact(root: Path, secrets: list[str]):
    """Private values (API keys, keystore passwords) must never be archived with the results. The result files of
    JMeter record the URL of every request, so a value used in a query string would be in them: they are cleaned
    line by line, however big they are."""
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix == ".jtl":
            _redact_stream(f, secrets)
            continue
        if f.stat().st_size > 50_000_000:
            continue
        try:
            txt = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new = txt
        for v in secrets:
            new = new.replace(v, "***")
        if new != txt:
            f.write_text(new, encoding="utf-8")


def _redact_stream(f: Path, secrets: list[str]):
    tmp = f.with_name(f.name + ".redacted")
    changed = False
    with open(f, encoding="utf-8", errors="replace", newline="") as src, \
            open(tmp, "w", encoding="utf-8", newline="") as dst:
        for line in src:
            if any(v in line for v in secrets):
                for v in secrets:
                    line = line.replace(v, "***")
                changed = True
            dst.write(line)
    if changed:
        tmp.replace(f)
    else:
        tmp.unlink()
