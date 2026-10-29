"""`morfeo-lab validate`: checks plans without a cluster. The same code path as `run` (reading, distribution, one copy
per generator), so a plan that validates here is accepted there. `--json` is the interface for the CI of the AI team."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import __version__, state
from .config import SERVICE, Target, load_target
from .console import bold, dim, error, info, ok, step, table, warn
from .errors import LabError
from .run import expand_scenarios
from .scenario import Scenario, choose_generators, compile_bundle, load_scenario, load_text
from .units import fmt_duration, num, parse_duration, plural

JSON_SCHEMA_VERSION = 1
DEFAULT_PORT = 8080


def _application(lab: dict, ref: str | None) -> Target | None:
    """The application whose address the plans are prepared for: the one named, or the one deployed."""
    if ref:
        return load_target(ref, check_files=False)
    if not state.load(lab).get("target"):
        return None
    try:
        return state.current_target(lab, None, check_files=False)
    except LabError as e:
        warn(f"Ignoring the last deployed application: {str(e).splitlines()[0]}")
        return None


def _check(lab: dict, path: Path, app_url: str, requested: int | None) -> tuple[Scenario, int, str, list[str]]:
    s = load_scenario(path, installed_plugins=lab["generators"]["jmeter_plugins"],
                      max_duration=parse_duration(lab["generators"]["max_duration"]))
    generators, why = choose_generators(s, lab["generators"], requested)
    with tempfile.TemporaryDirectory() as tmp:
        notes = compile_bundle(s, Path(tmp), app_url=app_url, generators=generators)
    return s, generators, why, list(dict.fromkeys(s.warnings + notes))


def _reasons(e: LabError) -> list[str]:
    lines = [x.strip().lstrip("- ").strip() for x in str(e).splitlines() if x.strip()]
    return lines[1:] if len(lines) > 1 and lines[0].endswith(":") else lines


def _data_files(s: Scenario) -> list[str]:
    return sorted({str(f.resolve().relative_to(s.path.parent.resolve())) for _, f in s.plan.files})


def _describe(s: Scenario, generators: int, why: str, notes: list[str]) -> dict:
    info_ = s.info()
    return {"name": s.name, "title": s.title, "model": info_["model"], "planned_duration_s": info_["planned_duration_s"],
            "generators": generators, "generators_reason": why, "distribution": info_["distribution"],
            "thread_groups": info_["thread_groups"], "endpoints": info_["endpoints"], "data_files": _data_files(s),
            "warnings": notes}


def validate(lab: dict, refs: list[str], *, target: str | None, generators: int | None, show: bool,
             as_json: bool) -> int:
    if generators is not None and generators < 1:
        raise LabError("--generators must be a number of 1 or more")
    if as_json:
        return _validate_json(lab, refs, target, generators)
    files = expand_scenarios(refs)
    app = _application(lab, target)
    port = app.port if app else DEFAULT_PORT
    app_url = app.url(lab["namespaces"]["app"]) if app else f"http://{SERVICE}.{lab['namespaces']['app']}.svc.cluster.local:{port}"
    step(f"Validating {plural(len(files), 'plan', 'plans')}" + (f" · objectives of '{app.name}'" if app else ""))
    bad = 0
    for f in files:
        try:
            s, n, why, notes = _check(lab, f, app_url, generators)
        except LabError as e:
            bad += 1
            error(f"{f.name} is not valid")
            for line in _reasons(e):
                info(line)
            continue
        d = fmt_duration(s.plan.planned_duration) if s.plan.planned_duration else "set by iterations"
        ok(f"{bold(f.name)} · {load_text(s.info())} · {d} · {plural(n, 'generator', 'generators')}")
        if s.title and s.title != s.name:
            info(dim(s.title))
        if show:
            _show(s, why)
        else:
            info(dim("Endpoints: " + (", ".join(s.info()["endpoints"]) or "–")))
        for w in notes:
            warn(w)
        if app:
            slo = app.data["slo"]
            parts = [f"{k} ≤ {num(slo[key])} ms" for k, key in (("p95", "p95_ms"), ("p99", "p99_ms")) if slo.get(key)]
            if slo.get("error_rate_pct") is not None:
                parts.append(f"server errors ≤ {slo['error_rate_pct']:g}%")
            info(dim("Objectives: " + " · ".join(parts)))
    print()
    if bad:
        error(f"{bad} of {plural(len(files), 'plan', 'plans')} {'is' if bad == 1 else 'are'} not valid")
        return 1
    ok(f"{plural(len(files), 'plan', 'plans')} valid")
    return 0


def _show(s: Scenario, why: str):
    """What will run: each thread group, the endpoints, the data files and how the load is divided."""
    rows = []
    for g in s.info()["thread_groups"]:
        if g["model"] == "open":
            load = f"{num(g['peak'], 0 if float(g['peak']).is_integer() else 1)} arrivals/s"
        else:
            load = plural(g["threads"], "thread", "threads") if g["threads"] is not None else "?"
        if g["peak_rps"]:
            load += f", {num(g['peak_rps'], 0 if float(g['peak_rps']).is_integer() else 1)} req/s"
        dur = (fmt_duration(g["duration_s"]) if g["duration_s"] is not None else
               plural(g["iterations"], "iteration", "iterations") if g["iterations"] is not None else "until stopped")
        rows.append([g["name"], g["type"], {"setup": "setUp", "teardown": "tearDown"}.get(g["role"], ""), load, dur])
    table(["Thread group", "Type", "", "Load", "Ends at"], rows, align="lllrr")
    info(dim("Endpoints: " + (", ".join(s.info()["endpoints"]) or "–")))
    if s.plan.files:
        info(dim("Data files: " + ", ".join(_data_files(s))))
    if s.plan.transactions:
        info(dim("Transactions (not counted as requests): " + ", ".join(s.plan.transactions)))
    mode = s.distribution.mode
    info(dim({"split": "Load division: the lab divides it between generators exactly",
              "single": "Load division: one generator (" + "; ".join(s.distribution.reasons) + ")",
              "plan": "Load division: the plan divides it itself (morfeo_generators)"}[mode]))
    info(dim(f"Generators: {why}"))


def _validate_json(lab: dict, refs: list[str], target: str | None, generators: int | None) -> int:
    head = {"schema_version": JSON_SCHEMA_VERSION, "producer": "morfeo-lab", "lab_version": __version__}
    try:
        files = expand_scenarios(refs)
        app = _application(lab, target)
    except LabError as e:
        print(json.dumps({**head, "valid": False, "errors": [str(e)], "scenarios": []}, indent=2, ensure_ascii=False))
        return 1
    ns = lab["namespaces"]["app"]
    app_url = app.url(ns) if app else f"http://{SERVICE}.{ns}.svc.cluster.local:{DEFAULT_PORT}"
    records, bad = [], 0
    for f in files:
        rec: dict = {"file": str(f), "valid": True, "errors": []}
        try:
            s, n, why, notes = _check(lab, f, app_url, generators)
            rec.update(_describe(s, n, why, notes))
        except LabError as e:
            bad += 1
            rec.update(valid=False, errors=_reasons(e))
        records.append(rec)
    print(json.dumps({**head, "valid": not bad, "errors": [], "scenarios": records}, indent=2, ensure_ascii=False))
    return 1 if bad else 0
