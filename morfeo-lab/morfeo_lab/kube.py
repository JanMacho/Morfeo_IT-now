"""kubectl against one cluster: applying templates, reading objects, running commands in pods and forwarding ports.

Two cluster providers:
  kind      the lab creates and owns a multi-node kind cluster (the default, reproducible)
  existing  any cluster kubectl can reach: Docker Desktop's Kubernetes, minikube, k3s, a real one
"""
from __future__ import annotations

import contextlib
import json
import socket
import subprocess
import time

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .errors import LabError
from .paths import DEPLOY
from .shell import cmd_name, out, run

_env = Environment(loader=FileSystemLoader(str(DEPLOY)), undefined=StrictUndefined, keep_trailing_newline=True)


def render(template: str, **ctx) -> str:
    return _env.get_template(template).render(**ctx)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Kube:
    def __init__(self, lab: dict):
        self.lab = lab
        c = lab["cluster"]
        self.provider = c["provider"]
        self.cluster = c["name"]
        self.ns = lab["namespaces"]
        self.pools = bool(c["pools"])
        if c.get("context"):
            self.context = c["context"]
        elif self.provider == "kind":
            self.context = f"kind-{self.cluster}"
        else:
            self.context = out(["kubectl", "config", "current-context"], check=False) or None

    # ------------------------------------------------------------ kubectl
    def k(self, *args: str) -> list[str]:
        return ["kubectl", *(["--context", self.context] if self.context else []), *args]

    def kubectl(self, *args: str, **kw):
        return run(self.k(*args), **kw)

    def kout(self, *args: str, check=True, timeout=60) -> str:
        return out(self.k(*args), check=check, timeout=timeout)

    def apply(self, manifest: str):
        run(self.k("apply", "-f", "-"), input=manifest, capture=True, quiet=True, timeout=180)

    def try_json(self, *args: str) -> dict | None:
        """`kubectl get ... -o json`, or None when the call failed (an API hiccup, a timeout): the caller decides what
        that means, instead of reading it as 'nothing exists'."""
        res = run(self.k("get", *args, "-o", "json"), check=False, capture=True, quiet=True, timeout=60)
        if res.returncode != 0:
            return None
        try:
            return json.loads(res.stdout)
        except json.JSONDecodeError:
            return None

    def get_json(self, *args: str) -> dict:
        return self.try_json(*args) or {}

    def exec(self, ns: str, pod: str, *cmd: str, container: str | None = None, check=True, timeout=60) -> str:
        c = ["-c", container] if container else []
        return self.kout("exec", "-n", ns, pod, *c, "--", *cmd, check=check, timeout=timeout)

    # ------------------------------------------------------------ the cluster
    def cluster_exists(self) -> bool:
        if self.provider == "kind":
            return self.cluster in out(["kind", "get", "clusters"], check=False).split()
        return run(self.k("get", "--raw", "/readyz"), check=False, capture=True, quiet=True,
                   timeout=20).returncode == 0

    def require_cluster(self):
        if not self.cluster_exists():
            if self.provider == "kind":
                raise LabError(f"The cluster '{self.cluster}' does not exist yet.\nCreate it with: {cmd_name()} up")
            raise LabError(f"Cannot reach the Kubernetes cluster (context: {self.context or 'current'}).\n"
                           "Check `kubectl get nodes` and cluster.context in config/lab.yaml")

    def nodes(self) -> list[dict]:
        return self.get_json("nodes").get("items", [])

    def node_selector(self, pool: str) -> dict:
        return {"morfeo.io/pool": pool} if self.pools else {}

    # ------------------------------------------------------------ access from this machine
    @contextlib.contextmanager
    def port_forward(self, ns: str, target: str, remote_port: int, local_port: int | None = None):
        """`kubectl port-forward` for the duration of a block. Yields the local port."""
        lp = local_port or free_port()
        proc = subprocess.Popen(self.k("port-forward", "-n", ns, target, f"{lp}:{remote_port}"),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.time() + 15
            while time.time() < deadline:
                if proc.poll() is not None:
                    raise LabError("Port forwarding failed: " + (proc.stderr.read() or b"").decode(errors="replace"))
                with socket.socket() as s:
                    if s.connect_ex(("127.0.0.1", lp)) == 0:
                        break
                time.sleep(0.3)
            yield lp
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
