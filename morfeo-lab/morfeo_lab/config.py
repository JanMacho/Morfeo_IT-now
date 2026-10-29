"""Loading and validating the lab configuration (config/lab.yaml) and the application files (targets/*.yaml).

Both are checked strictly: an unknown option is reported with a suggestion instead of being ignored."""
from __future__ import annotations

import copy
import difflib
import glob
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .errors import LabError
from .jmx.catalog import check_plugin_ids
from .paths import ROOT, lab_config_path
from .units import expand_env, parse_duration

# ====================================================================== lab configuration
LAB_DEFAULTS: dict = {
    "cluster": {
        # kind     : the lab creates and owns a multi-node kind cluster (the usual choice on a laptop)
        # existing : any cluster kubectl can reach (Docker Desktop, minikube, k3s, a real cluster)
        "provider": "kind",
        "name": "morfeo",
        "context": None,
        "node_image": "kindest/node:v1.31.2",
        "pools": True,
        "nodes": {"app": 1, "generators": 1},
        "image_loader": "auto",
        "registry": None,
        "ports": {"grafana": 3000, "prometheus": 9090, "app": 7070},
    },
    "namespaces": {"app": "morfeo-app", "generators": "morfeo-generators", "monitoring": "morfeo-monitoring"},
    "generators": {
        "jmeter_plugins": ["jpgc-casutg", "jpgc-tst", "jpgc-functions", "jpgc-dummy"],
        "cpu": "2",
        "memory": "2Gi",
        "jvm_heap": "1g",
        "max_threads_per_generator": 1000,
        "max_rps_per_generator": 2500,
        "max_generators": 8,
        "max_duration": "2h",
        "start_timeout": 300,
    },
    "monitoring": {
        "enabled": True,
        "grafana": True,
        "grafana_url": None,
        "prometheus_image": "prom/prometheus:v2.54.1",
        "grafana_image": "grafana/grafana:11.2.2",
        "postgres_exporter": True,
        "postgres_exporter_image": "quay.io/prometheuscommunity/postgres-exporter:v0.15.0",
        "scrape_interval": "5s",
        "persistent": True,
        "retention": "30d",
        "retention_size": "8GB",
        "storage": "10Gi",
        "profiling": {"enabled": True, "event": "cpu", "interval": "20ms", "chunk": "10s"},
    },
    "regression": {"latency_pct": 15, "error_rate_pp": 0.5},
    "results": {"dir": "results"},
}

PROVIDERS = ("kind", "existing")
IMAGE_LOADERS = ("auto", "kind", "docker", "minikube", "k3s", "registry")
PROFILE_EVENTS = ("cpu", "wall", "alloc", "lock")

# Mappings whose keys are free text (variables, endpoints, JMeter properties): not checked for typos
FREE_FORM = {"runtime.env", "slo.endpoints", "client.properties", "dependencies.postgres.env"}


def deep_merge(base: dict, over: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as e:
        raise LabError(f"File not found: {path}") from e
    except yaml.YAMLError as e:
        raise LabError(f"Invalid YAML in {path}: {e}") from e
    if not isinstance(data, dict):
        raise LabError(f"{path} must contain a YAML mapping (key: value)")
    return data


def unknown_keys(user: dict, defaults: dict, prefix: str = "") -> list[str]:
    """Typos such as `provder:` would silently fall back to the defaults: report them with a suggestion."""
    out = []
    for k, v in (user or {}).items():
        path = f"{prefix}{k}"
        if k not in defaults:
            near = difflib.get_close_matches(str(k), [str(d) for d in defaults], n=1)
            out.append(f"unknown option '{path}'" + (f" (did you mean '{prefix}{near[0]}'?)" if near else ""))
        elif path in FREE_FORM or not isinstance(v, dict):
            continue
        elif isinstance(defaults[k], dict) and defaults[k]:
            out += unknown_keys(v, defaults[k], f"{path}.")
    return out


def load_lab(path: str | None = None) -> dict:
    p = Path(path) if path else lab_config_path()
    if path and not p.is_file():
        raise LabError(f"Lab configuration not found: {p}")
    user = read_yaml(p) if p.exists() else {}
    cfg = deep_merge(LAB_DEFAULTS, user)
    errs = unknown_keys(user, LAB_DEFAULTS)
    c, gen, prof = cfg["cluster"], cfg["generators"], cfg["monitoring"]["profiling"]
    if c["provider"] not in PROVIDERS:
        errs.append(f"cluster.provider must be one of {', '.join(PROVIDERS)}")
    if c["image_loader"] not in IMAGE_LOADERS:
        errs.append(f"cluster.image_loader must be one of {', '.join(IMAGE_LOADERS)}")
    if c["image_loader"] == "registry" and not c.get("registry"):
        errs.append("cluster.registry is required when cluster.image_loader is registry")
    plugins = gen["jmeter_plugins"]
    if not isinstance(plugins, list) or not all(isinstance(x, str) and re.fullmatch(r"[\w.-]+", x) for x in plugins):
        errs.append("generators.jmeter_plugins must be a list of plugin ids, for example [jpgc-casutg, jpgc-tst]")
    else:
        try:
            check_plugin_ids(plugins)
        except LabError as e:
            errs.append(str(e))
    for key, cast in (("max_threads_per_generator", int), ("max_rps_per_generator", float),
                      ("max_generators", int), ("start_timeout", int)):
        try:
            gen[key] = cast(gen[key])
            if gen[key] <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errs.append(f"generators.{key} must be a positive number")
    try:
        parse_duration(gen["max_duration"])
    except ValueError:
        errs.append("generators.max_duration must be a duration, for example 2h or 90m")
    if prof.get("event") not in PROFILE_EVENTS:
        errs.append(f"monitoring.profiling.event must be one of {', '.join(PROFILE_EVENTS)}")
    for key, lo in (("interval", 0.001), ("chunk", 5)):
        try:
            if parse_duration(prof.get(key)) < lo:
                raise ValueError
        except ValueError:
            errs.append(f"monitoring.profiling.{key} must be a duration of at least {lo:g} s")
    if errs:
        raise LabError(f"Invalid lab configuration ({p}):\n  - " + "\n  - ".join(errs))
    cfg["_path"] = str(p)
    cfg["_results_dir"] = (ROOT / cfg["results"]["dir"]).resolve()
    return cfg


# ====================================================================== application (target) files
TARGET_DEFAULTS: dict = {
    "name": None,
    "description": "",
    "artifact": {"jar": None, "image": None, "java_version": 21},
    "runtime": {
        "port": 8080,
        "scheme": "http",
        "replicas": 1,
        "java_opts": "-XX:MaxRAMPercentage=75.0",
        "args": "",
        "env": {},
        "resources": {"requests": {"cpu": "1", "memory": "512Mi"}, "limits": {"cpu": "1", "memory": "512Mi"}},
    },
    "health": {"type": "tcp", "path": "/", "liveness_path": None, "startup_timeout": 180},
    "logs": {"env": None, "files": []},
    "dependencies": {"postgres": None},
    "metrics": {"jmx": True, "profiling": None},
    "client": {"properties": {}, "keystore": None, "truststore": None},
    "slo": {"p95_ms": 500, "p99_ms": 1500, "error_rate_pct": 1.0, "min_throughput_rps": None, "endpoints": {}},
}

POSTGRES_DEFAULTS: dict = {
    "image": "postgres:16",
    "database": "app",
    "user": "app",
    "password": "app",
    "port": 5432,
    "env": {},
    "reset_sql": None,
    "args": [],
    "resources": {"requests": {"cpu": "500m", "memory": "512Mi"}, "limits": {"cpu": "1", "memory": "1Gi"}},
}

KEYSTORE_KEYS = {"file": None, "password": None, "type": None}
_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$")
SERVICE = "app"   # in-cluster DNS name of the application


def target_files() -> list[Path]:
    return sorted(ROOT.joinpath("targets").glob("*.yaml"))


def resolve_candidates(spec, what: str, base: Path = ROOT, required: bool = True) -> Path | None:
    """`spec` is a path or glob, or a list of them: the first that matches wins. ${ENV:-default} is expanded.
    Each pattern must match exactly one file, otherwise the choice would be ambiguous."""
    cands = [c for c in (spec if isinstance(spec, list) else [spec]) if c]
    tried = []
    for c in cands:
        c = expand_env(str(c))
        if "${" in c:
            tried.append(f"{c} (variable not set)")
            continue
        pat = Path(c).expanduser()
        pat = pat if pat.is_absolute() else base / pat
        matches = [m for m in sorted(glob.glob(str(pat))) if Path(m).is_file()]
        if len(matches) == 1:
            return Path(matches[0]).resolve()
        if len(matches) > 1:
            raise LabError(f"{what}: '{c}' matches {len(matches)} files "
                           f"({', '.join(Path(m).name for m in matches[:5])}); keep only one or be more specific")
        tried.append(c)
    if required:
        raise LabError(f"{what}: file not found. Tried:\n      " + "\n      ".join(tried or ["(nothing configured)"]))
    return None


@dataclass
class Target:
    data: dict
    path: Path
    jar_override: str | None = None
    secrets: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def port(self) -> int:
        return int(self.data["runtime"]["port"])

    @property
    def scheme(self) -> str:
        return self.data["runtime"]["scheme"]

    @property
    def replicas(self) -> int:
        return int(self.data["runtime"]["replicas"])

    def url(self, app_ns: str) -> str:
        return f"{self.scheme}://{SERVICE}.{app_ns}.svc.cluster.local:{self.port}"

    @property
    def jar(self) -> Path | None:
        """The JAR to deploy: --jar when given, else the first candidate of `artifact.jar` that exists."""
        if self.jar_override:
            return resolve_candidates(self.jar_override, f"--jar for '{self.name}'", base=Path.cwd())
        spec = self.data["artifact"].get("jar")
        return resolve_candidates(spec, f"JAR of '{self.name}' (artifact.jar)") if spec else None

    @property
    def image(self) -> str | None:
        """The ready-made image, unless a JAR was given on the command line."""
        if self.jar_override:
            return None
        return expand_env(self.data["artifact"].get("image"))

    @property
    def postgres(self) -> dict | None:
        return self.data["dependencies"].get("postgres")


def load_target(ref: str, *, jar: str | None = None, replicas: int | None = None, check_files: bool = True) -> Target:
    """`ref` is a path or a bare name looked up in targets/<name>.yaml."""
    p = Path(ref)
    if not p.is_file():
        cand = ROOT / "targets" / f"{ref}.yaml"
        if not cand.exists():
            avail = ", ".join(x.stem for x in target_files())
            raise LabError(f"Application file '{ref}' not found. Available: {avail or '(none)'}.\n"
                           "To add an application, create targets/<name>.yaml (see docs/TARGETS.md).")
        p = cand
    raw = read_yaml(p)
    errs = unknown_keys(raw, {**TARGET_DEFAULTS, "dependencies": {"postgres": None}})
    pg_raw = (raw.get("dependencies") or {}).get("postgres")
    if isinstance(pg_raw, dict):
        errs += unknown_keys(pg_raw, POSTGRES_DEFAULTS, "dependencies.postgres.")
    for kind in ("keystore", "truststore"):
        ks = (raw.get("client") or {}).get(kind)
        if isinstance(ks, dict):
            errs += unknown_keys(ks, KEYSTORE_KEYS, f"client.{kind}.")
    t = deep_merge(TARGET_DEFAULTS, raw)
    if replicas is not None:
        t["runtime"]["replicas"] = int(replicas)
    if not _NAME.match(str(t.get("name") or "")):
        errs.append("name: lowercase letters, digits and '-' only (at most 40 characters)")
    art = t["artifact"]
    if not jar and bool(art.get("jar")) == bool(art.get("image")):
        errs.append("artifact: set either `jar` or `image` (exactly one)")
    if t["health"]["type"] not in ("tcp", "http"):
        errs.append("health.type must be tcp or http")
    if t["runtime"]["scheme"] not in ("http", "https"):
        errs.append("runtime.scheme must be http or https")
    if not isinstance(t["runtime"].get("env"), dict):
        errs.append("runtime.env must be a mapping VARIABLE: value")
    for key, lo, hi in (("replicas", 1, 100), ("port", 1, 65535)):
        try:
            t["runtime"][key] = int(t["runtime"][key])
            if not lo <= t["runtime"][key] <= hi:
                raise ValueError
        except (TypeError, ValueError):
            errs.append(f"runtime.{key} must be a whole number between {lo} and {hi}")
    try:
        t["health"]["startup_timeout"] = int(t["health"]["startup_timeout"])
    except (TypeError, ValueError):
        errs.append("health.startup_timeout must be a number of seconds")
    if any(not str(f).startswith("/") for f in t["logs"].get("files") or []):
        errs.append("logs.files must be absolute paths inside the container")
    pg = t["dependencies"].get("postgres")
    if pg is not None:
        t["dependencies"]["postgres"] = pg = deep_merge(POSTGRES_DEFAULTS, pg)
        if pg.get("reset_sql"):
            try:
                f = resolve_candidates(pg["reset_sql"], f"reset_sql of '{t.get('name')}'", required=check_files)
                pg["_reset_sql_path"] = str(f) if f else None
            except LabError as e:
                errs.append(str(e))
    cl = t["client"]
    if not isinstance(cl.get("properties") or {}, dict):
        errs.append("client.properties must be a mapping")
    for kind in ("keystore", "truststore"):
        ks = cl.get(kind)
        if not ks:
            continue
        if not isinstance(ks, dict) or not ks.get("file"):
            errs.append(f"client.{kind} must be a mapping with at least `file`")
            continue
        if ks.get("password") is not None:
            ks["password"] = expand_env(str(ks["password"]))
        if check_files:
            try:
                ks["_path"] = str(resolve_candidates(ks["file"], f"client.{kind}.file"))
            except LabError as e:
                errs.append(str(e))
    if errs:
        raise LabError(f"Invalid application file ({p}):\n  - " + "\n  - ".join(errs))
    return Target(t, p, jar)


def dependency_env(t: dict, app_ns: str) -> dict:
    """The variables that tell the application where its dependencies are, rendered from the target."""
    env = {}
    pg = t["dependencies"].get("postgres")
    if pg:
        ctx = {"host": f"postgres.{app_ns}.svc.cluster.local", "port": pg["port"],
               "database": pg["database"], "user": pg["user"], "password": pg["password"]}
        for k, v in (pg.get("env") or {}).items():
            env[k] = str(v).format(**ctx)
    return env


def client_properties(t: Target) -> dict:
    """JMeter properties for the generators, with ${ENV} expanded (the values may be secrets)."""
    props = {}
    for k, v in (t.data["client"].get("properties") or {}).items():
        val = expand_env(str(v))
        if re.search(r"\$\{(env:)?[A-Za-z_]\w*\}", val):
            raise LabError(f"'{t.name}': client.properties.{k} uses an environment variable that is not set")
        props[str(k)] = val
    return props
