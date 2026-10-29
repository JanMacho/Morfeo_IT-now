"""The container images of the lab: how they are named, built with docker and made visible to the cluster.

Images are built locally and reach the cluster through an *image loader*:
  kind      `kind load docker-image` (with a fallback that imports the image into each node)
  docker    nothing to do: the cluster uses the local docker daemon (Docker Desktop's Kubernetes)
  minikube  `minikube image load`
  k3s       `docker save | k3s ctr images import -` (k3s on this machine)
  registry  `docker push <registry>/...` (any real cluster)

The tag of an image is a hash of its content: a changed Dockerfile, plugin list or JAR gives a new image, and when
nothing changed nothing is rebuilt.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .config import Target
from .console import task, warn
from .errors import LabError
from .kube import Kube
from .paths import IMAGES
from .shell import out, run, run_streaming, which
from .units import hash_paths

GENERATOR_DIR = IMAGES / "generator"
AGENTS_DIR = IMAGES / "app-agents"
RUNTIME_DIR = IMAGES / "app-runtime"
GENERATOR_REPO = "morfeo/generator"
AGENTS_REPO = "morfeo/app-agents"
APP_PREFIX = "morfeo/app"


# ====================================================================== names
def generator_image(lab: dict) -> str:
    plugins = ",".join(lab["generators"]["jmeter_plugins"])
    digest = hashlib.sha256((hash_paths(GENERATOR_DIR) + "|" + plugins).encode()).hexdigest()[:12]
    return f"{GENERATOR_REPO}:{digest}"


def agents_image() -> str:
    return f"{AGENTS_REPO}:{hash_paths(AGENTS_DIR)}"


def app_image(t: Target) -> str:
    if t.image:
        return t.image
    digest = hash_paths(t.jar, RUNTIME_DIR, extra=str(t.data["artifact"].get("java_version")))
    return f"{APP_PREFIX}-{t.name}:{digest}"


# ====================================================================== docker
_STEP = re.compile(r"^#\d+ \[(?:[\w.-]+ )?(\d+/\d+)\] (.*)$")        # BuildKit: #7 [3/5] RUN ...
_CLASSIC_STEP = re.compile(r"^Step (\d+/\d+) : (.*)$")                # the classic builder
_PULL = re.compile(r"^#\d+ sha256:\w+ ([\d.]+\w?B / [\d.]+\w?B)")    # BuildKit: a layer being downloaded


def _build_detail(line: str, current: str) -> str:
    m = _STEP.match(line) or _CLASSIC_STEP.match(line)
    if m:
        return f"step {m.group(1)}: {m.group(2)[:60]}"
    m = _PULL.match(line)
    if m:
        return f"downloading {m.group(1)}"
    return current


def _same_image(ref: str, image: str) -> bool:
    """'docker.io/library/postgres:16' is 'postgres:16'; 'postgres:16.4' is not."""
    ref = ref.strip()
    return ref == image or ref.endswith("/" + image)


class Images:
    def __init__(self, lab: dict, kube: Kube):
        self.lab, self.kube = lab, kube
        self._loader: str | None = None

    # ------------------------------------------------------------ the loader
    @property
    def loader(self) -> str:
        if self._loader:
            return self._loader
        mode = self.lab["cluster"]["image_loader"]
        if mode == "auto":
            if self.kube.provider == "kind":
                mode = "kind"
            else:
                ctx = (self.kube.context or "").lower()
                nodes = self.kube.nodes()
                runtime = nodes[0].get("status", {}).get("nodeInfo", {}).get("containerRuntimeVersion", "") if nodes else ""
                if ctx.startswith("kind-"):
                    mode = "kind"
                elif "docker-desktop" in ctx or ctx == "desktop-linux":
                    mode = "docker"
                elif "minikube" in ctx:
                    mode = "minikube"
                elif "k3s" in runtime and which("k3s"):
                    mode = "k3s"
                elif self.lab["cluster"].get("registry"):
                    mode = "registry"
                else:
                    raise LabError("Cannot tell how to make locally built images available to this cluster.\n"
                                   "Set cluster.image_loader in config/lab.yaml (docker, kind, minikube, k3s or registry).")
        self._loader = mode
        return mode

    def ref(self, image: str) -> str:
        """The name the cluster must use for a locally built image."""
        registry = self.lab["cluster"].get("registry")
        if self.loader == "registry" and "/" in image and not image.startswith(registry):
            return f"{registry.rstrip('/')}/{image}"
        return image

    # ------------------------------------------------------------ where an image is
    def exists_locally(self, image: str) -> bool:
        return run(["docker", "image", "inspect", image], check=False, capture=True, quiet=True).returncode == 0

    def in_cluster(self, image: str) -> bool:
        mode = self.loader
        if mode == "docker":
            return self.exists_locally(image)
        if mode == "kind":
            nodes = out(["kind", "get", "nodes", "--name", self.kube.cluster], check=False).split()
            if not nodes:
                return False
            for node in nodes:
                listing = out(["docker", "exec", node, "crictl", "images", "-o", "json"], check=False)
                try:
                    tags = {t for img in json.loads(listing or "{}").get("images", []) for t in img.get("repoTags", [])}
                except json.JSONDecodeError:
                    return False
                if not any(_same_image(t, image) for t in tags):
                    return False
            return True
        if mode == "k3s":
            return any(_same_image(r, image) for r in out(["k3s", "ctr", "images", "ls", "-q"], check=False).splitlines())
        if mode == "minikube":
            return any(_same_image(r, image) for r in out(["minikube", "image", "ls"], check=False).splitlines())
        return run(["docker", "manifest", "inspect", self.ref(image)], check=False, capture=True, quiet=True,
                   timeout=60).returncode == 0

    # ------------------------------------------------------------ building and pulling
    def build(self, image: str, context: Path, dockerfile: Path | None = None, build_args: dict | None = None,
              label: str | None = None, done: str | None = None):
        """docker build with a spinner that shows the current step (builds can take minutes)."""
        ca = os.environ.get("MORFEO_BUILD_CA")
        if ca and not Path(ca).is_file():
            raise LabError(f"MORFEO_BUILD_CA points to a file that does not exist: {ca}")

        def command(progress: bool) -> list[str]:
            c = ["docker", "build", *(["--progress=plain"] if progress else []), "-t", image]
            for k, v in (build_args or {}).items():
                c += ["--build-arg", f"{k}={v}"]
            if ca:
                c += ["--secret", f"id=extra_ca,src={ca}"]
            if dockerfile:
                c += ["-f", str(dockerfile)]
            return [*c, str(context)]

        with task(label or f"Building {image}", done=done or f"Image {image} built") as t:
            def on_line(line):
                t.detail = _build_detail(line, t.detail)
            res = run_streaming(command(True), on_line=on_line, timeout=3600)
            if res.returncode != 0 and "unknown flag: --progress" in res.stdout:   # the classic builder
                res = run_streaming(command(False), on_line=on_line, timeout=3600)
            if res.returncode != 0:
                tail = "\n".join(res.stdout.splitlines()[-25:])
                hint = ""
                if "CERTIFICATE_VERIFY_FAILED" in tail or "x509" in tail:
                    hint = ("\nThis network inspects HTTPS traffic with its own certificate authority. Put that "
                            "certificate in a .crt or .pem file, set MORFEO_BUILD_CA to its path and run `up` again.")
                raise LabError(f"Building the image {image} failed:\n{tail}{hint}")

    def pull(self, image: str):
        if self.exists_locally(image):
            return
        with task(f"Pulling {image}", done=f"Image {image} pulled") as t:
            layers: dict[str, str] = {}

            def on_line(line):
                m = re.match(r"^([0-9a-f]{12}): (.+)$", line)
                if m:
                    layers[m.group(1)] = m.group(2)
                    finished = sum(v.startswith(("Pull complete", "Already exists")) for v in layers.values())
                    t.detail = f"layers {finished}/{len(layers)}"
            res = run_streaming(["docker", "pull", image], on_line=on_line, timeout=3600)
            if res.returncode != 0:
                last = (res.stdout.strip().splitlines() or ["?"])[-1].strip()
                raise LabError(f"Could not pull {image}: {last[:160]}\n"
                               "Check that Docker can reach the internet: docker run --rm hello-world")

    # ------------------------------------------------------------ into the cluster
    def load(self, image: str, name: str | None = None):
        mode = self.loader
        if mode == "docker":
            return
        what = name or f"image {image}"
        lower = what if what[1:2].isupper() else what[0].lower() + what[1:]   # 'JMX ...' stays as it is
        with task(f"Loading {lower} into the cluster", done=f"{what[0].upper() + what[1:]} loaded into the cluster"):
            self._load(image, mode)

    def _load(self, image: str, mode: str):
        if mode == "kind":
            res = run(["kind", "load", "docker-image", image, "--name", self.kube.cluster], capture=True, quiet=True,
                      timeout=1800, check=False)
            if res.returncode != 0:   # some docker + kind combinations (containerd image store): import it by hand
                self._import_into_kind_nodes(image, res.stderr or res.stdout)
        elif mode == "minikube":
            run(["minikube", "image", "load", image], capture=True, quiet=True, timeout=1800)
        elif mode == "k3s":
            self._pipe_save(image, ["k3s", "ctr", "images", "import", "-"], "k3s")
        else:
            ref = self.ref(image)
            run(["docker", "tag", image, ref], capture=True, quiet=True)
            run(["docker", "push", ref], capture=True, quiet=True, timeout=1800)

    def _import_into_kind_nodes(self, image: str, first_error: str):
        nodes = out(["kind", "get", "nodes", "--name", self.kube.cluster], check=False).split()
        if not nodes:
            raise LabError(f"Could not load {image} into the cluster:\n{first_error.strip()[-500:]}")
        for node in nodes:
            self._pipe_save(image, ["docker", "exec", "-i", node, "ctr", "--namespace=k8s.io", "images", "import",
                                    "--all-platforms", "-"], f"kind node {node}")

    @staticmethod
    def _pipe_save(image: str, importer: list[str], where: str):
        with tempfile.TemporaryFile() as archive:
            save = subprocess.run(["docker", "save", image], stdout=archive, stderr=subprocess.PIPE, timeout=1800)
            if save.returncode != 0:
                raise LabError(f"docker save {image} failed: {save.stderr.decode(errors='replace')[-300:]}")
            archive.seek(0)
            res = subprocess.run(importer, stdin=archive, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                 timeout=1800)
        if res.returncode != 0:
            raise LabError(f"Importing {image} into {where} failed: {res.stderr.decode(errors='replace')[-500:]}")

    def ensure(self, image: str, build_fn, name: str | None = None) -> bool:
        """Build (if missing locally) and load (if missing in the cluster) an image. True when anything was done."""
        built = False
        if not self.exists_locally(image):
            build_fn()
            built = True
        if not self.in_cluster(image):
            self.load(image, name)
            return True
        return built

    def ensure_third_party(self, image: str):
        """Public images (postgres, prometheus...): preloaded on kind, k3s and minikube so that the nodes never need
        the internet and a slow download never eats into a test. Elsewhere the cluster pulls them itself."""
        if self.loader in ("kind", "k3s", "minikube") and not self.in_cluster(image):
            try:
                self.pull(image)
                self.load(image)
            except LabError:
                warn(f"Could not preload {image}; the cluster will download it itself")
