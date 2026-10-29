"""The load-generator pods: sending them files, reading their results, waiting for them and the live progress line."""
from __future__ import annotations

import io
import re
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..console import LIVE, SYM, bold, color, dim, progress_bar, warn
from ..errors import LabError
from ..kube import Kube
from ..shell import cmd_name, run_bytes, run_to_files
from ..units import fmt_duration, ms, num, plural


# ====================================================================== files in and out of a pod
def tar_dir(src: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for f in sorted(src.rglob("*")):
            tf.add(f, arcname=str(f.relative_to(src)).replace("\\", "/"), recursive=False)
    return buf.getvalue()


def untar_file(archive: Path, dst: Path):
    dst.mkdir(parents=True, exist_ok=True)
    if not archive.stat().st_size:
        return
    with tarfile.open(archive, mode="r") as tf:
        members = []
        for m in tf.getmembers():   # links are never needed, and nothing may leave the destination
            target = (dst / m.name).resolve()
            if m.issym() or m.islnk() or (dst.resolve() not in target.parents and target != dst.resolve()):
                continue
            members.append(m)
        # `filter` exists from Python 3.10.12; the members are already vetted above
        tf.extractall(dst, members=members, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))


_POLL = ("import os, urllib.request\n"
         "print('DONE' if os.path.exists('/work/.done') else 'RUNNING')\n"
         "print('SETUP_DONE' if os.path.exists('/work/.setup-done') else 'SETUP_PENDING')\n"
         "try:\n"
         "    print(urllib.request.urlopen('http://127.0.0.1:9270/metrics', timeout=3).read().decode())\n"
         "except Exception:\n"
         "    pass\n")


class GeneratorPod:
    """One load-generator pod. Files go in and out as tar streams through `kubectl exec`: no `kubectl cp`, and no tar
    needed on this machine, so it works the same on Windows."""

    def __init__(self, kube: Kube, ns: str, pod: str, index: int):
        self.kube, self.ns, self.pod, self.index = kube, ns, pod, index

    def sh(self, script: str, check=True, timeout=60) -> str:
        return self.kube.exec(self.ns, self.pod, "sh", "-c", script, check=check, timeout=timeout)

    def poll(self) -> tuple[bool, bool, str]:
        """(finished?, setUp thread groups ended?, the live metrics of the pod) in ONE exec, so that the live line does
        not depend on Prometheus (no discovery or scrape delay) and works with the monitoring disabled."""
        txt = self.kube.exec(self.ns, self.pod, "python3", "-c", _POLL, check=False, timeout=20)
        state, _, rest = txt.partition("\n")
        setup, _, body = rest.partition("\n")
        return state.strip() == "DONE", setup.strip() == "SETUP_DONE", body

    def touch(self, marker: str, check=False):
        """Create a marker file in the pod. The ones that start the test must not fail silently (`check`)."""
        self.sh(f"touch /work/{marker}", check=check, timeout=20)

    def push_dir(self, src: Path, dst: str):
        run_bytes(self.kube.k("exec", "-i", "-n", self.ns, self.pod, "--", "tar", "xf", "-", "-C", dst), tar_dir(src))

    def push_file(self, src: Path, dst_dir: str, name: str, timeout=1800):
        """Streamed from a temporary tar on disk: a big file never sits in memory."""
        with tempfile.TemporaryDirectory(prefix="morfeo-tar-") as tmp:
            archive = Path(tmp) / "f.tar"
            with tarfile.open(archive, mode="w") as tf:
                tf.add(src, arcname=name, recursive=False)
            with open(archive, "rb") as fh:
                run_to_files(self.kube.k("exec", "-i", "-n", self.ns, self.pod, "--", "tar", "xf", "-", "-C", dst_dir),
                             stdin=fh, timeout=timeout)

    def pull(self, src: str, dst: Path, timeout=1800):
        with tempfile.TemporaryDirectory(prefix="morfeo-tar-") as tmp:
            archive = Path(tmp) / "f.tar"
            with open(archive, "wb") as fh:
                run_to_files(self.kube.k("exec", "-n", self.ns, self.pod, "--", "tar", "cf", "-", "-C", src, "."),
                             stdout=fh, timeout=timeout)
            untar_file(archive, dst)


def in_parallel(fn, items):
    with ThreadPoolExecutor(max_workers=max(1, len(items))) as ex:
        return list(ex.map(fn, items))


# ====================================================================== starting them
def job_pods(kube: Kube, ns: str, job: str) -> list[dict] | None:
    """The pods of a job, or None when kubectl could not answer (which does not mean the pods are gone)."""
    found = kube.try_json("pods", "-n", ns, "-l", f"job-name={job}")
    return None if found is None else found.get("items", [])


class Roster:
    """Which generator pods are alive. A pod that failed or finished is gone at once; one that is merely missing from
    the list (a deleted pod, or a kubectl answer lost on a loaded machine) must be missing three times in a row."""
    MISSES = 3

    def __init__(self, kube: Kube, ns: str, job: str, names: set[str]):
        self.kube, self.ns, self.job, self.names = kube, ns, job, names
        self.missed = dict.fromkeys(names, 0)

    def check(self):
        pods = job_pods(self.kube, self.ns, self.job)
        if pods is None:
            return
        phases = {p["metadata"]["name"]: p.get("status", {}).get("phase") for p in pods}
        for name in self.names:
            phase = phases.get(name)
            self.missed[name] = self.missed[name] + 1 if phase is None else 0
            if phase in ("Failed", "Succeeded") or self.missed[name] >= self.MISSES:
                raise LabError(f"The generator {name} stopped before the end of the test ({phase or 'deleted'}).\n"
                               f"Its log: kubectl logs -n {self.ns} {name}")


def wait_generators(kube: Kube, ns: str, job: str, generators: int, timeout: float, tk) -> list[GeneratorPod]:
    """Wait until every generator pod is running (they start together)."""
    deadline = time.time() + timeout
    warned = False
    while True:
        pods = job_pods(kube, ns, job) or []
        running = []
        for p in pods:
            st = p.get("status", {})
            for cs in st.get("containerStatuses") or []:
                waiting = (cs.get("state") or {}).get("waiting") or {}
                if waiting.get("reason") in ("ErrImagePull", "ImagePullBackOff", "ErrImageNeverPull",
                                             "CreateContainerConfigError"):
                    raise LabError(f"A load generator cannot start: {waiting.get('reason')} {waiting.get('message', '')}\n"
                                   f"Run `{cmd_name()} up` and try again.")
            if st.get("phase") == "Running":
                idx = int(p["metadata"].get("annotations", {}).get("batch.kubernetes.io/job-completion-index", 0))
                running.append(GeneratorPod(kube, ns, p["metadata"]["name"], idx))
            if st.get("phase") == "Failed":
                logs = kube.kout("logs", "-n", ns, p["metadata"]["name"], "--tail=15", check=False, timeout=20)
                raise LabError(f"The generator {p['metadata']['name']} failed before the test started:\n"
                               + "\n".join("    " + x for x in (logs or "(no log)").splitlines()))
        unschedulable = [c for p in pods for c in (p.get("status", {}).get("conditions") or [])
                         if c.get("reason") == "Unschedulable"]
        tk.detail = f"{len(running)}/{generators} running"
        if unschedulable and not warned:
            warn(f"{plural(len(unschedulable), 'generator does', 'generators do')} not fit on the generators node: "
                 f"{unschedulable[0].get('message', '')[:140]}\n"
                 "There is not enough free CPU or memory: use fewer --generators, or lower generators.cpu and "
                 "generators.memory in config/lab.yaml, or give Docker more memory.")
            warned = True
        if len(running) >= generators:
            return sorted(running, key=lambda s: s.index)
        if time.time() > deadline:
            raise LabError(f"Only {len(running)} of {generators} generators started within {int(timeout)} s.\n"
                           "The generators node may be short of CPU or memory: use fewer --generators, or lower "
                           "generators.cpu and generators.memory in config/lab.yaml.")
        time.sleep(2)


# ====================================================================== explaining what JMeter says
def explain_jmeter_failure(lines: list[str]) -> str:
    """The cause, in plain words, when JMeter could not run a plan. `lines` are the error lines it wrote."""
    text = "\n".join(lines)
    plugin = re.search(r"(?:CannotResolveClassException|ClassNotFoundException|NoClassDefFoundError)[: ]+\s*([\w.$]+)", text)
    if plugin:
        return (f"JMeter does not know the element {plugin.group(1)}. It comes from a plugin that is not in the "
                "generator image: add it to generators.jmeter_plugins in config/lab.yaml (and to "
                "images/generator/plugins.json if it is not listed there) and run `up` again.")
    if re.search(r"FileNotFoundException|No such file|NoSuchFileException", text):
        missing = re.search(r"(?:FileNotFoundException|NoSuchFileException)[: ]+\s*([^\s(]+)", text)
        return ("JMeter could not find a data file" + (f" ({missing.group(1)})" if missing else "")
                + ". Data files must be next to the plan (or in a subfolder); `validate` lists the ones it found.")
    if re.search(r"MultipleCompilationErrorsException|Script compilation error|groovy\.lang|SyntaxError", text, re.I):
        return "A Groovy or BeanShell script of the plan does not compile; the error below shows the line."
    if re.search(r"ConversionException|XStream|Error in NonGUIDriver|Unmarshal", text, re.I):
        return "JMeter could not load the plan: the file is not a valid JMeter test plan. Open it in JMeter to repair it."
    if re.search(r"OutOfMemoryError", text):
        return ("JMeter ran out of memory. Raise generators.jvm_heap and generators.memory in config/lab.yaml, or "
                "use more generators.")
    return ""


# ====================================================================== live view
_METRIC = re.compile(r'^(morfeo_requests_total|morfeo_response_time_seconds_bucket|morfeo_active_threads)'
                     r'(?:\{(.*)\})? ([0-9.eE+-]+|NaN)$')


def parse_exporter(texts: list[str]) -> dict:
    """Totals over every generator: requests of the load stage, failed ones, histogram buckets {bound: count},
    active users, and the requests of the setUp stage."""
    total = {"req": 0.0, "fail": 0.0, "le": {}, "users": 0.0, "setup": 0.0}
    for txt in texts:
        for line in txt.splitlines():
            m = _METRIC.match(line.strip())
            if not m:
                continue
            name, labels, v = m.group(1), m.group(2) or "", float(m.group(3))
            if name == "morfeo_active_threads":
                total["users"] += v
                continue
            if 'stage="setup"' in labels:
                if name == "morfeo_requests_total":
                    total["setup"] += v
                continue
            if 'stage="main"' not in labels:
                continue
            if name == "morfeo_requests_total":
                total["req"] += v
                if re.search(r'result="(server_5xx|network|assertion)"', labels):
                    total["fail"] += v
            else:
                le = re.search(r'le="([^"]+)"', labels).group(1)
                bound = float("inf") if le == "+Inf" else float(le)
                total["le"][bound] = total["le"].get(bound, 0.0) + v
    return total


def quantile(q: float, buckets: dict) -> float | None:
    """histogram_quantile() of Prometheus on cumulative buckets {upper bound: count}."""
    items = sorted(buckets.items())
    if not items or items[-1][1] <= 0:
        return None
    rank, prev_le, prev_c = q * items[-1][1], 0.0, 0.0
    for le, c in items:
        if c >= rank:
            if le == float("inf"):
                return prev_le
            return prev_le + (le - prev_le) * ((rank - prev_c) / max(c - prev_c, 1e-9))
        prev_le, prev_c = le, c
    return prev_le


class LiveStats:
    """The numbers of the live line: differences between two polls of the exporters (about 5 s), so they react at
    once and need no Prometheus."""

    def __init__(self):
        self.prev, self.t_prev = None, 0.0
        self.rps = self.p95 = self.error_rate = self.users = None
        self.total = 0
        self.setup_requests = 0
        self.load_start = None   # local time of the first request of the load, from any generator

    def _first_request(self, texts: list[str], now: float):
        for txt in texts:
            first = re.search(r"^morfeo_first_sample_seconds(?:\{.*\})? ([0-9.]+)$", txt, re.M)
            clock = re.search(r"^morfeo_exporter_clock_seconds(?:\{.*\})? ([0-9.]+)$", txt, re.M)
            if first:
                local = float(first.group(1)) - ((float(clock.group(1)) - now) if clock else 0.0)
                self.load_start = min(self.load_start or local, local, now)

    def update(self, texts: list[str], now: float):
        cur = parse_exporter(texts)
        self.users, self.total, self.setup_requests = cur["users"], int(cur["req"]), int(cur["setup"])
        self._first_request(texts, now)
        if self.prev is not None and now > self.t_prev:
            dt, dreq = now - self.t_prev, cur["req"] - self.prev["req"]
            if dreq >= 0:
                self.rps = dreq / dt
                self.error_rate = (cur["fail"] - self.prev["fail"]) / dreq if dreq else 0.0
                self.p95 = quantile(0.95, {k: v - self.prev["le"].get(k, 0.0) for k, v in cur["le"].items()})
        self.prev, self.t_prev = cur, now


def live_line(st: LiveStats, elapsed: float, planned: float | None, ramp: float, finished: int, generators: int,
              error_limit_pct: float | None) -> str:
    sep = dim(f" {SYM['sep']} ")
    if planned:
        phase = ("ramp-up" if elapsed < ramp else "steady load") if elapsed < planned else "finishing"
        head = (f"{progress_bar(elapsed / planned)} {bold(fmt_duration(elapsed))}"
                f"{dim('/' + fmt_duration(planned))} · {phase}")
    else:
        head = f"{bold(fmt_duration(elapsed))} · ends by iterations"
    parts = [head]
    if st.rps is None:
        parts.append(dim("setting up" if st.setup_requests and not st.total else "waiting for the first responses"))
    else:
        parts.append(f"{num(st.rps, 1)} req/s")
        if st.p95 is not None and st.p95 == st.p95:
            parts.append(f"p95 {ms(st.p95 * 1000)}")
        if st.error_rate is not None:
            e = st.error_rate * 100
            txt = f"errors {num(e, 2, '%')}"
            parts.append(color("1;31", txt) if e > (error_limit_pct if error_limit_pct is not None else 1.0) else txt)
        if st.users is not None:
            parts.append(plural(int(st.users), "user", "users"))
        parts.append(dim(f"{num(st.total)} requests"))
    if finished:
        parts.append(dim(f"{finished}/{generators} generators finished"))
    return "  " + sep.join(parts)


def watch(kube: Kube, stats: LiveStats | None, job: str, ns: str, gens: list[GeneratorPod], t_start: float,
          planned: float | None, ramp: float, error_limit: float | None, max_duration: float, *, quiet=False,
          timeout: float | None = None):
    """Wait until every generator wrote `.done`, redrawing the live line every second (the generators are polled
    every 5 s). Fails fast if a generator pod dies, and stops the test at the lab's duration limit."""
    generators = len(gens)
    roster = Roster(kube, ns, job, {sh.pod for sh in gens})
    t0 = time.time()
    tick, finished, stopped = 0, 0, False
    try:
        while True:
            if tick % 5 == 0 or (stats and stats.load_start is None):
                polled = in_parallel(lambda sh: sh.poll(), gens)
                finished = sum(1 for d, _, _ in polled if d)
                if stats:
                    stats.update([txt for _, _, txt in polled], time.time())
                if finished == generators:
                    return
                roster.check()
                if timeout and time.time() - t0 > timeout:
                    raise LabError(f"The generators did not stop within {int(timeout)} s")
                if not stopped and time.time() - t_start > max_duration:
                    warn(f"The test reached the lab limit of {fmt_duration(max_duration)} "
                         "(generators.max_duration): stopping it; the results so far are kept.")
                    in_parallel(lambda sh: sh.touch(".stop"), gens)
                    stopped = True
            if not quiet and stats:
                LIVE.update(live_line(stats, time.time() - t_start, planned, ramp, finished, generators, error_limit))
            tick += 1
            time.sleep(1)
    finally:
        LIVE.stop()
