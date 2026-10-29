"""`morfeo-lab doctor`: checks this machine and the lab, explains what is wrong in terms of what to do, and says
which command comes next. It never changes anything."""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

from . import state
from .config import load_target, target_files
from .console import bold, dim, error, hint, info, ok, step, warn
from .errors import LabError
from .images import Images, generator_image
from .kube import Kube
from .paths import IMAGES, ROOT, rel
from .scenario import load_scenario
from .shell import INSTALL_HINTS, cmd_name, out, which
from .units import num, parse_duration, plural

MIN_DOCKER_GB = 6.0
MIN_HOST_GB = 8.0
MIN_DISK_GB = 15.0

# Where the image builds download from: a certificate error here means a proxy that inspects TLS
DOWNLOADS = ("repo1.maven.org", "archive.apache.org", "github.com", "registry-1.docker.io")


def _host_memory_gb() -> float | None:
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MemoryStatus()
            m.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 2**30
        if sys.platform == "darwin":
            return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=5)) / 2**30
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 2**20
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return None
    return None


def _docker_memory_hint(gb: float) -> str:
    base = f"Docker has {num(gb, 1)} GB and the lab needs at least {MIN_DOCKER_GB:g} GB."
    if os.name == "nt":
        return (base + " With the WSL 2 backend the limit is set in %USERPROFILE%\\.wslconfig. Open it with\n"
                "      notepad \"$env:USERPROFILE\\.wslconfig\"\n"
                "    and under [wsl2] add (or change) the line  memory=8GB  ; then run\n"
                "      wsl --shutdown\n"
                "    and start Docker Desktop again. (With the Hyper-V backend: Docker Desktop → Settings → "
                "Resources → Memory.)")
    return base + " Docker Desktop → Settings → Resources → Memory."


def _reason(e: Exception) -> str:
    """The tool's own message, not our 'command failed' line."""
    lines = [x.strip() for x in str(e).splitlines() if x.strip()]
    return (lines[-1] if lines else "no answer")[:240]


def _crlf_scripts() -> list[str]:
    """Shell scripts with Windows line endings fail inside the Linux containers ('\\r: command not found')."""
    bad = []
    for f in sorted(IMAGES.rglob("*")):
        if f.is_file() and (f.suffix in (".sh", ".py") or f.name == "Dockerfile"):
            try:
                if b"\r\n" in f.read_bytes():
                    bad.append(rel(f))
            except OSError:
                continue
    return bad


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", int(port))) == 0


def _check_download(host: str) -> tuple[str, str]:
    """('ok'|'tls'|'unreachable', detail) for an HTTPS request to `host`."""
    try:
        socket.getaddrinfo(host, 443)
    except OSError:
        return "unreachable", "the name cannot be resolved (no internet, blocked DNS or a VPN)"
    try:
        urllib.request.urlopen(urllib.request.Request(f"https://{host}/", method="HEAD"), timeout=6,
                               context=ssl.create_default_context()).close()
        return "ok", ""
    except urllib.error.HTTPError:
        return "ok", ""   # the server answered: the connection and the certificate are fine
    except (ssl.SSLError, urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
            return "tls", "its certificate is not trusted (a proxy that inspects TLS traffic?)"
        return "unreachable", str(reason)[:120]


class Doctor:
    def __init__(self, lab: dict):
        self.lab = lab
        self.good = True
        self.docker_ok = False

    def fail(self, msg: str):
        self.good = False
        error(msg)

    # ------------------------------------------------------------------ this machine
    def machine(self):
        step("This machine")
        v = sys.version_info
        if v < (3, 10):
            self.fail(f"Python {v.major}.{v.minor} is too old: the lab needs Python 3.10 or newer")
        else:
            ok(f"Python {v.major}.{v.minor}.{v.micro}  {dim(sys.executable)}")
        ram, free = _host_memory_gb(), shutil.disk_usage(ROOT).free / 2**30
        line = (f"{num(ram, 0 if ram >= 10 else 1)} GB memory · " if ram else "") + f"{num(free)} GB free disk"
        if ram is not None and ram < MIN_HOST_GB - 0.5:
            self.good = False
            warn(line + f"\n    The lab needs a machine with at least {MIN_HOST_GB:g} GB of memory (16 GB recommended).")
        elif free < MIN_DISK_GB:
            warn(line + f"\n    {MIN_DISK_GB:g} GB of free disk is recommended for the images (JMeter, kind, PostgreSQL).")
        else:
            ok(line)
        bad = _crlf_scripts()
        if bad:
            self.fail("Some lab files have Windows line endings (CRLF) and would fail inside the containers:\n      "
                      + "\n      ".join(bad[:6]) + ("\n      …" if len(bad) > 6 else "")
                      + "\n    Check out the lab again with `git config core.autocrlf false`, or unzip it again.")

    # ------------------------------------------------------------------ docker, kind, kubectl
    def tools(self):
        step("Tools")
        provider = self.lab["cluster"]["provider"]
        checks = {"docker": self._tool_docker, "kubectl": self._tool_kubectl, "kind": self._tool_kind}
        for tool in ["docker", "kubectl", *(["kind"] if provider == "kind" else [])]:
            if not which(tool):
                self.fail(f"{tool}: not installed or not in the PATH\n    {INSTALL_HINTS.get(tool, '')}")
                continue
            try:
                checks[tool]()
            except (LabError, ValueError, KeyError) as e:
                if tool == "docker":
                    start = ("Start Docker Desktop and wait until it says 'Engine running'." if os.name == "nt" or
                             sys.platform == "darwin" else "Start the Docker service (sudo systemctl start docker).")
                    self.fail(f"docker: installed but not running\n    {_reason(e)}\n    {start}")
                else:
                    self.fail(f"{tool}: installed but not responding\n    {_reason(e)}")

    def _tool_docker(self):
        version = out(["docker", "version", "--format", "{{.Server.Version}}"], timeout=25)
        mem = out(["docker", "info", "--format", "{{.MemTotal}}"], timeout=25, check=False)
        cpus = out(["docker", "info", "--format", "{{.NCPU}}"], timeout=25, check=False)
        gb = int(mem) / 2**30 if mem.isdigit() else None
        self.docker_ok = True
        if gb is not None and gb < MIN_DOCKER_GB - 0.5:
            self.good = False
            warn(f"Docker {version} · {cpus} CPUs · {num(gb, 1)} GB\n    " + _docker_memory_hint(gb))
        else:
            ok(f"Docker {version} · {cpus} CPUs · {num(gb, 1)} GB for containers" if gb else f"Docker {version}")

    def _tool_kind(self):
        text = out(["kind", "version"], timeout=25)
        ok(text.splitlines()[0])
        m = re.search(r"v(\d+)\.(\d+)", text)
        if m and (int(m[1]), int(m[2])) < (0, 20):
            warn("This kind is old; the node image of the lab may not start. Update it (docs/SETUP.md).")

    def _tool_kubectl(self):
        v = json.loads(out(["kubectl", "version", "--client", "-o", "json"], timeout=25))
        ok(f"kubectl {v['clientVersion']['gitVersion']}")

    # ------------------------------------------------------------------ internet
    def network(self):
        step("Downloads")
        with concurrent.futures.ThreadPoolExecutor(len(DOWNLOADS)) as pool:
            results = dict(zip(DOWNLOADS, pool.map(_check_download, DOWNLOADS), strict=True))
        tls = [h for h, (kind, _) in results.items() if kind == "tls"]
        down = {h: d for h, (kind, d) in results.items() if kind == "unreachable"}
        if not tls and not down:
            ok("The servers the images are built from answer")
            return
        for host, detail in down.items():
            warn(f"{host}: {detail}")
        if tls:
            warn(f"{', '.join(tls)}: the certificate is not trusted. Behind a proxy that inspects TLS traffic the "
                 "image builds fail.\n    Export the company certificate (PEM) and point the lab to it before `up`:\n"
                 "      PowerShell:  $env:MORFEO_BUILD_CA = \"C:\\path\\company-ca.pem\"\n"
                 "      Linux/macOS: export MORFEO_BUILD_CA=/path/company-ca.pem")
        info(dim("Only needed the first time: the images are built once and kept."))

    # ------------------------------------------------------------------ the lab
    def configuration(self):
        step("Lab configuration")
        c = self.lab["cluster"]
        where = ("own kind cluster" if c["provider"] == "kind"
                 else f"existing cluster ({c.get('context') or 'current context'})")
        where += " with separate nodes for the application and the generators" if c["pools"] else ", single node"
        ok(f"{rel(self.lab['_path'])} · {where}")

    def status(self) -> tuple[bool, dict]:
        step("Lab status")
        c = self.lab["cluster"]
        tools_ok = which("kubectl") and (c["provider"] != "kind" or (self.docker_ok and which("kind")))
        running = installed = False
        if tools_ok:
            try:
                kube = Kube(self.lab)
                running = kube.cluster_exists()
                installed = running and bool(kube.kout("get", "namespace", self.lab["namespaces"]["generators"],
                                                       "--ignore-not-found", "-o", "name", check=False))
            except LabError:
                running = False
        st = state.load(self.lab) if installed else {}
        if not running:
            info("The lab has not been created yet" if c["provider"] == "kind" else "Cannot reach the cluster")
            if c["provider"] == "kind" and self.docker_ok:
                for name, port in c["ports"].items():
                    if _port_in_use(port):
                        self.good = False
                        warn(f"Port {port} ({name}) is in use by another program: change it in config/lab.yaml "
                             f"(cluster.ports.{name}) or close that program")
            return False, {}
        ok(f"Cluster '{c['name']}' is running" if c["provider"] == "kind" else "Cluster reachable")
        if not installed:
            info("The lab is not installed on this cluster")
        elif st.get("target_name"):
            ok(f"Deployed application: {st['target_name']} ({plural(st.get('replicas', 1), 'replica', 'replicas')})")
            if self.docker_ok:
                self._images_hint()
        else:
            info("No application deployed")
        return installed, st

    def _images_hint(self):
        try:
            if not Images(self.lab, Kube(self.lab)).in_cluster(generator_image(self.lab)):
                warn(f"The load generator image is not in the cluster: run `{cmd_name()} up`")
        except LabError:
            return

    # ------------------------------------------------------------------ applications and scenarios
    def applications(self) -> str | None:
        step("Applications")
        first_ready = None
        for f in target_files():
            try:
                t = load_target(str(f), check_files=False)
            except LabError as e:
                warn(f"{f.stem}: invalid file · {' '.join(x.strip() for x in str(e).splitlines()[1:3])}")
                continue
            problems = []
            source = t.image
            if not source:
                try:
                    source = rel(t.jar)
                except LabError as e:
                    problems.append("JAR not found (" + ", ".join(x.strip() for x in str(e).splitlines()[1:]) + ")")
            pg = t.postgres
            if pg and pg.get("reset_sql"):
                try:
                    load_target(str(f))
                except LabError:
                    problems.append("reset_sql not found")
            if problems:
                info(f"{t.name}  {dim('not ready: ' + '; '.join(problems))}")
            else:
                ok(f"{t.name}  {dim(str(source))}")
                first_ready = first_ready or t.name
        if not target_files():
            info("There are no application files in targets/ (docs/TARGETS.md explains how to add one)")
        return first_ready

    def scenarios(self) -> int:
        step("Scenarios")
        files = sorted(ROOT.joinpath("scenarios").glob("*.jmx"))
        bad = 0
        for f in files:
            try:
                load_scenario(f, installed_plugins=self.lab["generators"]["jmeter_plugins"],
                              max_duration=parse_duration(self.lab["generators"]["max_duration"]))
            except LabError as e:
                bad += 1
                lines = [x.strip(" -") for x in str(e).splitlines() if x.strip()]
                warn(f"{f.name}: not valid · {lines[1] if len(lines) > 1 else lines[0]}")
        if files:
            ok(f"{len(files) - bad} of {plural(len(files), 'scenario', 'scenarios')} valid in scenarios{os.sep}")
        else:
            info(f"No plans in scenarios{os.sep}: copy there the .jmx files of the AI team")
        return len(files)


def run(lab: dict) -> int:
    d = Doctor(lab)
    d.machine()
    d.tools()
    d.network()
    d.configuration()
    installed, st = d.status()
    first_ready = d.applications()
    n_scenarios = d.scenarios()
    print()
    if not d.good:
        error("Fix the items marked above before continuing")
        return 1
    ok(bold("This machine is ready"))
    if not installed:
        hint(f"Next step: {cmd_name()} up")
    elif not st.get("target_name"):
        hint(f"Next step: {cmd_name()} deploy {first_ready or '<application>'}")
    elif n_scenarios:
        hint(f"Next step: {cmd_name()} run")
    else:
        hint(f"Next step: copy the plans (.jmx) into scenarios{os.sep} and run {cmd_name()} validate")
    return 0
