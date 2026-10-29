"""`morfeo-lab run`: execute scenarios against the deployed application and collect the results."""
from __future__ import annotations

import json
import webbrowser
from datetime import datetime
from pathlib import Path

from .. import report, state
from ..cluster import ensure_lab_images, tools_for
from ..config import Target
from ..console import dim, error, hint, kv, step, table, warn
from ..deploy import app_pods
from ..errors import LabError
from ..images import Images
from ..kube import Kube
from ..paths import ROOT, rel
from ..scenario import Scenario, choose_generators, load_scenario
from ..shell import cmd_name, require_tools
from ..units import ms, num, parse_duration, plural, slug
from .execute import prometheus, run_scenario
from .outputs import finalize, verdict_badge


def expand_scenarios(refs: list[str]) -> list[Path]:
    """The .jmx files named on the command line: files, or folders (all their plans), or `scenarios/` by default."""
    out: list[Path] = []
    for ref in refs or ["scenarios"]:
        p = Path(ref)
        if not p.exists() and (ROOT / ref).exists():
            p = ROOT / ref
        if p.is_dir():
            out += sorted(p.glob("*.jmx"))
        elif p.exists():
            out.append(p)
        else:
            raise LabError(f"Scenario not found: {ref}")
    if not out:
        raise LabError("There are no scenarios (*.jmx) to run.\n"
                       f"Copy the plans of the AI team into scenarios/ or name one: {cmd_name()} run plan.jmx")
    return out


def parse_properties(items: list[str]) -> dict[str, str]:
    """`--prop name=value` options: JMeter properties passed to every generator (-Jname=value)."""
    props = {}
    for item in items or []:
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            raise LabError(f"--prop expects name=value, not '{item}'")
        props[name.strip()] = value
    return props


def prepare(lab: dict, target: str | None) -> tuple[Kube, Target]:
    """The cluster is reachable and the right application is deployed."""
    require_tools(*tools_for(lab))
    kube = Kube(lab)
    kube.require_cluster()
    t = state.current_target(lab, target)
    deployed = state.load(lab).get("target_name")
    if deployed and deployed != t.name:
        raise LabError(f"The deployed application is '{deployed}', not '{t.name}'.\n"
                       f"Deploy it with: {cmd_name()} deploy {t.name}")
    return kube, t


def _error_summary(run_dir: Path, run_id: str, f: Path, message: str, name: str) -> dict:
    """A scenario that could not run still appears in the page of the run (and survives `morfeo-lab report`)."""
    summary = {"run_id": run_id, "scenario": {"name": name, "file": f.name}, "verdict": "ERROR", "error": message}
    d = run_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "error.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def run_summaries(run_dir: Path) -> list[dict]:
    """Every scenario of a run (results and errors), in execution order."""
    out = []
    for d in sorted((x for x in run_dir.iterdir() if x.is_dir()), key=lambda x: x.stat().st_mtime):
        for name in ("summary.json", "error.json"):
            if (d / name).exists():
                out.append(json.loads((d / name).read_text(encoding="utf-8")))
                break
    return out


def finalize_scenario(folder: Path, slo: dict | None = None):
    """Analyse the raw data of a scenario folder again (`morfeo-lab report`), optionally against other objectives."""
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    if slo is not None:
        meta["slo"] = slo
    metrics = folder / "app-metrics.json"
    finalize(folder, meta, json.loads(metrics.read_text(encoding="utf-8")) if metrics.exists() else {}, quiet=True)


def baseline_for(lab: dict, ref: str, name: str) -> dict | None:
    """The key figures of the same scenario in an earlier run, for `--baseline`."""
    p = Path(ref)
    run_dir = p if p.is_dir() else lab["_results_dir"] / ref
    f = run_dir / name / "summary.json"
    if not f.exists():
        return None
    sm = json.loads(f.read_text(encoding="utf-8"))
    window = sm["steady"] if sm["steady"]["requests"] else sm["overall"]
    return {"run_id": sm.get("run_id", run_dir.name),
            "steady": {k: window[k] for k in ("requests", "throughput_rps", "goodput_rps", "server_error_rate",
                                             "latency_ms")},
            "tolerance": dict(lab["regression"])}


def run(lab: dict, refs: list[str], *, target: str | None, generators: int | None, tag: str | None, reset: bool,
        fresh: bool, keep_job: bool, open_page: bool, baseline: str | None, jmeter_report: bool,
        properties: dict[str, str]) -> int:
    kube, t = prepare(lab, target)
    files = expand_scenarios(refs)
    # validate everything BEFORE touching the cluster: fail fast
    scenarios: list[tuple[Scenario, dict | None]] = []
    names: dict[str, Path] = {}
    for f in files:
        s = load_scenario(f, installed_plugins=lab["generators"]["jmeter_plugins"],
                          max_duration=parse_duration(lab["generators"]["max_duration"]))
        if s.slug in names:
            raise LabError(f"{f.name} and {names[s.slug].name} have the same name '{s.slug}': rename one")
        names[s.slug] = f
        choose_generators(s, lab["generators"], generators)
        base = baseline_for(lab, baseline, s.slug) if baseline else None
        if baseline and base is None:
            warn(f"{f.name}: the baseline run {baseline} has no result for this scenario")
        scenarios.append((s, base))
    if baseline and not any(b for _, b in scenarios):
        raise LabError(f"The baseline run was not found or has none of these scenarios: {baseline}")
    if not kube.kout("get", "deployment/app", "-n", lab["namespaces"]["app"], "--ignore-not-found"):
        raise LabError(f"The application '{t.name}' is not deployed.\nDeploy it with: {cmd_name()} deploy {t.name}")
    ensure_lab_images(Images(lab, kube), lab, quiet=True)   # images can disappear from the nodes under disk pressure

    run_id, run_dir = new_run_dir(lab, tag)
    replicas = len(app_pods(kube, lab["namespaces"]["app"])) or t.replicas
    n = len(files)
    step(f"Run {run_id}")
    kv("Application", f"{t.name} · {plural(replicas, 'replica', 'replicas')}")
    kv("Scenarios", f"{n}" + (f"  {dim('(' + ', '.join(f.name for f in files[:6]) + (' …' if n > 6 else '') + ')')}"
                               if n > 1 else f"  {dim(files[0].name)}"))
    if baseline:
        kv("Baseline", baseline)
    kv("Results", rel(run_dir))

    summaries = []
    with prometheus(lab, kube) as prom:
        if lab["monitoring"]["enabled"] and not prom:
            warn("Prometheus is not responding: the server metrics will not be recorded for this run")
        for i, (s, base) in enumerate(scenarios):
            try:
                sm = run_scenario(lab, t, s, run_dir, run_id, generators=generators, properties=properties,
                                  reset=reset, fresh=fresh, keep_job=keep_job, prom=prom,
                                  position=f"{i + 1}/{n} · " if n > 1 else "", baseline=base,
                                  jmeter_report=jmeter_report)
                summaries.append(sm)
                if sm.get("interrupted") and i < n - 1:
                    left = n - i - 1
                    warn("Run interrupted: " + ("the remaining scenario is skipped" if left == 1
                                                else f"the remaining {left} scenarios are skipped"))
                    break
            except LabError as e:
                error(f"{s.path.name}: {e}")
                summaries.append(_error_summary(run_dir, run_id, s.path, str(e), s.slug))
            except KeyboardInterrupt:
                error(f"Run aborted during {s.path.name}: the remaining scenarios are skipped")
                summaries.append(_error_summary(run_dir, run_id, s.path, "Aborted with Ctrl+C", s.slug))
                break
    report.write_run_index(run_dir / "index.html", run_id, t.name, summaries)
    report.write_history(lab["_results_dir"])
    print_run_summary(run_id, run_dir, summaries, open_page)
    return 1 if any(s["verdict"] in ("FAIL", "ERROR") for s in summaries) else 0


def new_run_dir(lab: dict, tag: str | None) -> tuple[str, Path]:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{slug(tag, 20)}" if tag else "")
    run_dir = lab["_results_dir"] / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_id, run_dir


def print_run_summary(run_id: str, run_dir: Path, summaries: list[dict], open_page: bool):
    step(f"Run {run_id} finished")
    rows = []
    for sm in summaries:
        name = sm["scenario"].get("title") or sm["scenario"]["name"]
        if sm["verdict"] == "ERROR" or not sm.get("overall", {}).get("requests"):
            rows.append([verdict_badge(sm["verdict"]), name, "–", "–", "–", "–"])
            continue
        w = sm["steady"] if sm["steady"]["requests"] else sm["overall"]
        rows.append([verdict_badge(sm["verdict"]), name, num(w["throughput_rps"], 1), ms(w["latency_ms"]["p95"]),
                     ms(w["latency_ms"]["p99"]), num(w["server_error_rate"] * 100, 2, "%")])
    table(["", "Scenario", "req/s", "p95", "p99", "Server errors"], rows, align="llrrrr")
    print()
    kv("Results", rel(run_dir / "index.html"))
    if open_page:
        open_in_browser(run_dir / "index.html")
    else:
        hint(f"Open the results: {cmd_name()} open")


def open_in_browser(target) -> bool:
    url = target if isinstance(target, str) else Path(target).resolve().as_uri()
    try:
        return bool(webbrowser.open(url))
    except Exception:   # no browser (CI, SSH): the path is printed anyway
        return False
