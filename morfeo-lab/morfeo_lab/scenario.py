"""A scenario is one JMeter plan (.jmx) delivered by the AI team. This module turns a file into what the rest of
the lab needs: validation, how many generators the plan can use, and the bundle each generator receives (its copy
of the plan, the data files and the JMeter properties)."""
from __future__ import annotations

import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import jmx
from .errors import LabError
from .units import fmt_duration, num, plural, slug


@dataclass
class Scenario:
    path: Path
    plan: jmx.Plan
    distribution: jmx.Distribution
    labels: list[str]
    label_methods: dict[str, list[str]]
    label_keys: dict[str, str]
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def slug(self) -> str:
        return slug(self.name)

    @property
    def title(self) -> str:
        return self.plan.title

    def info(self) -> dict:
        """What the plan asks for, as written in summary.json."""
        p = self.plan
        return {
            "name": self.name, "file": self.path.name, "title": self.title, "description": p.description,
            "model": p.model, "planned_duration_s": p.planned_duration, "ramp_up_s": p.ramp_up,
            "peak_users": p.peak_threads, "target_rps": p.target_rate, "target_arrivals_per_s": p.target_arrivals,
            "thread_groups": [{"name": g.name, "type": g.cls, "role": g.role, "model": g.model, "threads": g.threads,
                               "peak": g.peak, "peak_rps": g.peak_rate or None, "start_s": g.start,
                               "duration_s": g.duration, "iterations": g.iterations,
                               "scripted_pacing": g.scripted_pacing} for g in p.groups],
            "distribution": {"mode": self.distribution.mode, "reasons": self.distribution.reasons},
            "endpoints": list(dict.fromkeys(self.label_keys.get(x, x) for x in self.labels)),
            "transactions": p.transactions,
        }


def load_text(info: dict) -> str:
    """What the plan asks for, per thread group: '50 users', '300 req/s with 150 users', '200 arrivals/s'."""
    parts = []
    for g in (x for x in info["thread_groups"] if x["role"] == "main"):
        if g["model"] == "open":
            text = f"{num(g['peak'], 0 if float(g['peak']).is_integer() else 1)} arrivals/s"
        elif g["model"] == "throughput":
            text = f"{num(g['peak_rps'], 0 if float(g['peak_rps']).is_integer() else 1)} req/s with {plural(g['threads'] or 0, 'user', 'users')}"
        elif g["threads"] is None:
            text = "users set by a variable"
        else:
            text = plural(g["threads"], "user", "users")
        if g["scripted_pacing"]:
            text += ", paced by a script of the plan"
        if g["start_s"]:
            text += f" starting at {fmt_duration(g['start_s'])}"
        parts.append(text)
    shown = parts[:4]
    return " + ".join(shown) + (f" + {len(parts) - 4} more groups" if len(parts) > 4 else "") or "–"


def load_scenario(path: str | Path, *, installed_plugins: list[str], max_duration: float | None = None) -> Scenario:
    """Read and validate a plan. Raises LabError with every problem found."""
    p = Path(path)
    if not p.is_file():
        raise LabError(f"Scenario not found: {p}")
    if p.suffix.lower() != ".jmx":
        raise LabError(f"{p.name}: scenarios are JMeter test plans (.jmx)")
    plan = jmx.read_plan(p, installed_plugins)
    errors = list(plan.errors)
    duration = plan.planned_duration
    if max_duration and duration and duration > max_duration:
        errors.append(f"the planned duration {fmt_duration(duration)} is longer than the lab limit of "
                      f"{fmt_duration(max_duration)} (generators.max_duration in config/lab.yaml)")
    if errors:
        raise LabError(f"{p.name} is not valid:\n  - " + "\n  - ".join(errors))
    labels, methods, keys = jmx.endpoint_labels(plan)
    return Scenario(p, plan, jmx.distribution_of(plan), labels, methods, keys, list(plan.warnings))


# ------------------------------------------------------------------ generators
def choose_generators(s: Scenario, limits: dict, requested: int | None) -> tuple[int, str]:
    """How many generators to use and why. The total load never changes with the number of generators."""
    dist = s.distribution
    if requested is not None:
        if requested > 1 and dist.mode == "single":
            raise LabError(f"{s.path.name}: the plan cannot be divided between generators:\n    - "
                           + "\n    - ".join(dist.reasons)
                           + "\n    Run it with --generators 1, or make the plan divide its own load using the "
                             "morfeo_generators property (docs/SCENARIOS.md).")
        if dist.mode == "split" and dist.max_generators and requested > dist.max_generators:
            raise LabError(f"{s.path.name}: the load can be divided into at most {dist.max_generators} generators "
                           f"(its smallest thread group has that many threads); use --generators "
                           f"{dist.max_generators} or fewer.")
        return requested, "requested"
    if dist.mode != "split":
        return 1, dist.describe()
    p = s.plan
    rate = round((p.target_rate or 0) + (p.target_arrivals or 0), 1)
    by_threads = math.ceil(p.peak_threads / int(limits["max_threads_per_generator"])) if p.peak_threads else 1
    by_rate = math.ceil(rate / float(limits["max_rps_per_generator"])) if rate else 1
    n = max(1, by_threads, by_rate)
    why = (f"{p.peak_threads} threads, at most {limits['max_threads_per_generator']} per generator"
           if p.peak_threads and (by_threads >= by_rate or not rate)
           else f"{rate:g} requests or arrivals per second, at most {limits['max_rps_per_generator']:g} per generator")
    cap = min(int(limits["max_generators"]), dist.max_generators or int(limits["max_generators"]))
    if n > cap:
        return cap, why + f"; limited to {cap} (generators.max_generators), so the generators may become the bottleneck"
    return n, why


# ------------------------------------------------------------------ bundle
def compile_bundle(s: Scenario, out_dir: Path, *, app_url: str, generators: int,
                   client: dict | None = None, secrets_dir: Path | None = None) -> list[str]:
    """Write plan-0.jmx ... plan-N.jmx, the data files and morfeo.properties. Returns the notes worth showing.
    The private client configuration (keys, certificates) goes to `secrets_dir`, never into the bundle."""
    out_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    for k in range(generators):
        data, n = jmx.compile_plan(s.plan, generators, k, app_url, s.distribution)
        (out_dir / f"plan-{k}.jmx").write_bytes(data)
        if k == 0:
            notes += n
    if generators > 1:
        text = s.path.read_text(encoding="utf-8", errors="replace")
        if "__threadNum" in text and not s.plan.reads_generators:
            notes.append("${__threadNum} repeats on every generator, so ids built with it can collide; "
                         "add ${__P(morfeo_generator,0)} to them")
        if s.plan.files:
            notes.append("every generator reads the data files from the start (the rows are not divided)")
    for _, f in s.plan.files:
        dst = out_dir / f.resolve().relative_to(s.path.parent.resolve())
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
    u = urlsplit(app_url)
    (out_dir / "morfeo.properties").write_text(
        "".join(_property_line(k, v) for k, v in (
            ("morfeo_url", app_url), ("morfeo_host", u.hostname), ("morfeo_port", u.port or (443 if u.scheme == "https" else 80)),
            ("morfeo_protocol", u.scheme))), encoding="utf-8")
    if client and secrets_dir is not None:
        write_client_config(client, secrets_dir)
    return notes


def _property_line(k: str, v) -> str:
    return f"{k}={str(v).replace(chr(92), chr(92) * 2).replace(chr(10), chr(92) + 'n')}\n"


def write_client_config(client: dict, secrets_dir: Path):
    """JMeter properties and system properties for the application (keys, tokens, certificates). They reach the
    generators separately from the plan and are never archived with the results."""
    secrets_dir.mkdir(parents=True, exist_ok=True)
    props = dict(client.get("properties") or {})
    system = {}
    for kind, prefix in (("keystore", "javax.net.ssl.keyStore"), ("truststore", "javax.net.ssl.trustStore")):
        ks = client.get(kind)
        if ks and ks.get("_path"):
            name = f"{kind}{Path(ks['_path']).suffix}"
            shutil.copy2(ks["_path"], secrets_dir / name)
            system[prefix] = f"/work/secret/{name}"
            if ks.get("password"):
                system[prefix + "Password"] = str(ks["password"])
            system[prefix + "Type"] = str(ks.get("type") or ("PKCS12" if name.endswith((".p12", ".pfx")) else "JKS"))
    if client.get("keystore"):
        props["https.use.cached.ssl.context"] = "false"   # JMeter only presents a client certificate this way
    if props:
        (secrets_dir / "client.properties").write_text("".join(_property_line(k, v) for k, v in props.items()),
                                                       encoding="utf-8")
    if system:
        (secrets_dir / "system.properties").write_text("".join(_property_line(k, v) for k, v in system.items()),
                                                       encoding="utf-8")


def secret_values(client: dict | None) -> list[str]:
    """Values that must never appear in archived results."""
    vals = [str(v) for v in ((client or {}).get("properties") or {}).values() if v and len(str(v)) >= 4]
    for kind in ("keystore", "truststore"):
        ks = (client or {}).get(kind) or {}
        if ks.get("password") and len(str(ks["password"])) >= 4:
            vals.append(str(ks["password"]))
    return vals
