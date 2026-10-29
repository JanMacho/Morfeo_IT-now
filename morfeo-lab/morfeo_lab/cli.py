"""`morfeo-lab` command line: argument parsing and the commands that fit in a few lines."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, cluster, deploy, doctor, report, shell, validate
from .config import load_lab, load_target
from .console import error, hint, kv, ok, step, warn
from .errors import LabError
from .paths import rel
from .run import finalize_scenario, open_in_browser, parse_properties, run, run_summaries
from .shell import cmd_name
from .units import slug

HELP = """\
MORFEO lab {version} · load tests for Java applications on Kubernetes

Usage: {cmd} <command> [options]

Lab
  doctor                       check this machine and print the next step
  up                           create or update the lab (cluster, images, Prometheus, Grafana)
  status                       show the cluster, the application and the generators
  dashboard                    forward Grafana, Prometheus and the application to localhost
  down [--destroy]             remove the application and the generators, or the whole lab

Application
  deploy <application>         deploy the application described in targets/<application>.yaml

Tests
  validate [plans...]          check plans without a cluster (--json for CI)
  run [plans...]               run plans against the deployed application

Results
  open [run|history|grafana]   open results in the browser
  report <folder>              rebuild the result pages from the raw data
  compare <run> <run>...       compare runs

Global options
  --config <file>              lab configuration (default: config/lab.yaml or MORFEO_LAB_CONFIG)
  -v, --verbose                print every external command
  --version                    print the version

Help on a command: {cmd} <command> --help
"""

_ARGPARSE = (("the following arguments are required: ", "Missing argument: "),
             ("unrecognized arguments: ", "Unknown option: "), ("argument ", ""),
             ("invalid int value", "not a whole number"), ("invalid float value", "not a number"))


class _Parser(argparse.ArgumentParser):
    def __init__(self, *a, **kw):
        kw.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        super().__init__(*a, **kw)

    def format_help(self):
        if self.prog == cmd_name():
            return HELP.format(version=__version__, cmd=cmd_name())
        return super().format_help()

    def error(self, message):
        if "invalid choice" in message and "<command>" in message:
            bad = message.split("'")[1] if "'" in message else ""
            error(f"Unknown command: {bad}")
        else:
            for old, new in _ARGPARSE:
                message = message.replace(old, new)
            error(message[:1].upper() + message[1:])
        hint(f"See: {self.prog} --help")
        sys.exit(2)


# ------------------------------------------------------------------ commands
def cmd_doctor(lab, args):
    return doctor.run(lab)


def cmd_up(lab, args):
    cluster.up(lab)


def cmd_deploy(lab, args):
    deploy.deploy(lab, load_target(args.application, jar=args.jar, replicas=args.replicas), restart=args.restart)


def cmd_validate(lab, args):
    return validate.validate(lab, args.plans, target=args.application, generators=args.generators, show=args.show,
                             as_json=args.json)


def _generators_arg(v) -> int | None:
    if v in (None, "auto"):
        return None
    try:
        n = int(v)
        if n < 1:
            raise ValueError
        return n
    except ValueError:
        raise LabError("--generators must be a number of 1 or more, or 'auto'") from None


def cmd_run(lab, args):
    return run(lab, args.plans, target=args.application, generators=_generators_arg(args.generators), tag=args.tag,
               reset=not args.no_reset, fresh=args.fresh, keep_job=args.keep_job, open_page=args.open,
               baseline=args.baseline, jmeter_report=not args.no_jmeter_report,
               properties=parse_properties(args.prop))


def _runs(lab) -> list[Path]:
    root = lab["_results_dir"]
    if not root.exists():
        return []
    return sorted((d for d in root.iterdir() if d.is_dir() and (d / "index.html").exists()), key=lambda d: d.name)


def cmd_open(lab, args):
    what = (args.what or "").strip()
    if what.lower() == "grafana":
        url = lab["monitoring"].get("grafana_url") or f"http://localhost:{lab['cluster']['ports']['grafana']}"
        open_in_browser(url)
        ok(f"Grafana: {url}")
        return
    if what.lower() == "history":
        page = lab["_results_dir"] / "index.html"
        if not page.exists():
            raise LabError("There are no results yet")
    elif what:
        p = Path(what)
        page = (p if p.is_dir() else lab["_results_dir"] / what) / "index.html"
        if not page.exists():
            raise LabError(f"No results found in {what}")
    else:
        runs = _runs(lab)
        if not runs:
            raise LabError(f"There are no results yet. Run a test first: {cmd_name()} run <plan>")
        page = runs[-1] / "index.html"
    if not open_in_browser(page):
        warn("Could not open the browser; open the file manually")
    ok(rel(page))


def _scenario_dirs(lab, ref: str, marker: str) -> list[Path]:
    """The scenario folders of a run (or the one folder named): those that have `marker` (the file the command needs)."""
    p = Path(ref)
    if not p.exists():
        p = lab["_results_dir"] / ref
    if (p / marker).exists():
        return [p]
    dirs = sorted(d for d in p.glob("*") if (d / marker).exists())
    if not dirs:
        raise LabError(f"No results found in {ref}")
    return dirs


def cmd_report(lab, args):
    if args.history:
        report.write_history(lab["_results_dir"])
        ok(rel(lab["_results_dir"] / "index.html"))
        return
    if not args.folder:
        raise LabError("Give a results folder (results/<run> or results/<run>/<scenario>) or --history")
    dirs = _scenario_dirs(lab, args.folder, "meta.json")
    slo = load_target(args.application, check_files=False).data["slo"] if args.application else None
    for d in dirs:
        finalize_scenario(d, slo)
    run_dir = dirs[0].parent
    sums = run_summaries(run_dir)
    app = next((s["application"]["name"] for s in sums if s.get("application")), "?")
    report.write_run_index(run_dir / "index.html", run_dir.name, app, sums)
    report.write_history(lab["_results_dir"])
    step("Pages rebuilt")
    kv("Page", rel(run_dir / "index.html"))


def cmd_compare(lab, args):
    dirs = []
    for ref in args.runs:
        found = _scenario_dirs(lab, ref, "summary.json")
        if args.scenario:
            found = [d for d in found if d.name == slug(args.scenario)]
        if len(found) != 1:
            raise LabError(f"{ref}: {len(found)} scenarios found; pick one with --scenario <name> "
                           f"({', '.join(d.name for d in _scenario_dirs(lab, ref, 'summary.json'))})")
        dirs.append(found[0])
    sums = [json.loads((d / "summary.json").read_text(encoding="utf-8")) for d in dirs]
    names = args.names.split(",") if args.names else [s["run_id"] for s in sums]
    if len(names) != len(sums):
        raise LabError("--names needs one name per run")
    page = lab["_results_dir"] / f"compare-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
    report.write_compare(page, sums, names)
    step("Comparison")
    kv("Page", rel(page))
    if args.open:
        open_in_browser(page)


def cmd_status(lab, args):
    cluster.status(lab)


def cmd_dashboard(lab, args):
    cluster.dashboard(lab)


def cmd_down(lab, args):
    cluster.down(lab, args.destroy)


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog=cmd_name())
    p.add_argument("--config", help="lab configuration (default: config/lab.yaml or $MORFEO_LAB_CONFIG)")
    p.add_argument("-v", "--verbose", action="store_true", help="print every external command")
    p.add_argument("--version", action="version", version=f"MORFEO lab {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, title="commands", metavar="<command>", parser_class=_Parser)

    sub.add_parser("doctor", help="check this machine").set_defaults(fn=cmd_doctor)
    sub.add_parser("up", help="create or update the lab").set_defaults(fn=cmd_up)

    d = sub.add_parser("deploy", help="deploy an application")
    d.add_argument("application", help="name (targets/<name>.yaml) or path of the application file")
    d.add_argument("--jar", help="use this JAR instead of the one configured in the application file")
    d.add_argument("--replicas", type=int, help="number of replicas (overrides runtime.replicas)")
    d.add_argument("--restart", action="store_true", help="restart the JVM even if nothing changed")
    d.set_defaults(fn=cmd_deploy)

    v = sub.add_parser("validate", help="check plans without a cluster")
    v.add_argument("plans", nargs="*", help="files or folders (default: scenarios/)")
    v.add_argument("--application", help="show the objectives of this application (default: the deployed one)")
    v.add_argument("--generators", type=int, help="number of generators (default: automatic)")
    v.add_argument("--show", action="store_true", help="show the thread groups, requests and data files")
    v.add_argument("--json", action="store_true", help="machine-readable result on stdout (for CI)")
    v.set_defaults(fn=cmd_validate)

    r = sub.add_parser("run", help="run plans (files or folders; default: scenarios/)")
    r.add_argument("plans", nargs="*", help="files or folders (default: scenarios/)")
    r.add_argument("--application", help="default: the application that is deployed")
    r.add_argument("--generators", default="auto", help="generators that divide the load; default: auto")
    r.add_argument("--tag", help="label added to the run name, for example v1.2 or pool-20")
    r.add_argument("--prop", action="append", metavar="NAME=VALUE",
                   help="JMeter property passed to every generator (-JNAME=VALUE); repeatable")
    r.add_argument("--no-reset", action="store_true", help="do not restore the database before each plan")
    r.add_argument("--fresh", action="store_true", help="restart the application (cold JVM) before each plan")
    r.add_argument("--keep-job", action="store_true", help="keep the generator pods (debugging)")
    r.add_argument("--open", action="store_true", help="open the results page when finished")
    r.add_argument("--baseline", metavar="RUN",
                   help="earlier run to compare with: worse latency or errors than it fail the plan")
    r.add_argument("--no-jmeter-report", action="store_true", help="skip the JMeter HTML report (faster)")
    r.set_defaults(fn=cmd_run)

    o = sub.add_parser("open", help="open results in the browser")
    o.add_argument("what", nargs="?", help="empty = last run · <run-id> · history · grafana")
    o.set_defaults(fn=cmd_open)

    rp = sub.add_parser("report", help="rebuild the result pages from the raw data")
    rp.add_argument("folder", nargs="?", help="results/<run> or results/<run>/<scenario>")
    rp.add_argument("--application", help="evaluate again with the current objectives of this application")
    rp.add_argument("--history", action="store_true", help="only rebuild results/index.html")
    rp.set_defaults(fn=cmd_report)

    c = sub.add_parser("compare", help="compare runs (the first one is the baseline)")
    c.add_argument("runs", nargs="+", help="results/<run>[/<scenario>] or just <run-id>")
    c.add_argument("--scenario", help="plan name, when the run has several")
    c.add_argument("--names", help="comma-separated names, for example v1,v2")
    c.add_argument("--open", action="store_true", help="open the comparison in the browser")
    c.set_defaults(fn=cmd_compare)

    sub.add_parser("status", help="lab status").set_defaults(fn=cmd_status)
    sub.add_parser("dashboard", help="forward Grafana, Prometheus and the application to localhost"
                   ).set_defaults(fn=cmd_dashboard)
    dn = sub.add_parser("down", help="remove the application and the generators (or the whole lab)")
    dn.add_argument("--destroy", action="store_true", help="delete the kind cluster (or the lab namespaces)")
    dn.set_defaults(fn=cmd_down)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    if not (sys.argv[1:] if argv is None else argv):
        print(parser.format_help(), end="")
        return 0
    args = parser.parse_args(argv)
    shell.VERBOSE = shell.VERBOSE or args.verbose
    try:
        lab = load_lab(args.config)
        return args.fn(lab, args) or 0
    except LabError as e:
        error(str(e))
        return 2
    except KeyboardInterrupt:
        print()
        error("Interrupted")
        return 130
