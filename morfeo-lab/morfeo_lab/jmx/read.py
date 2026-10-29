"""Reading a JMeter test plan (.jmx): the thread groups with the load they ask for, the timers, the requests,
the data files and the plugins. Nothing is raised for the content of a plan: problems are returned in the
plan as errors (the test cannot run) and warnings (it can, but the reader should know)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import catalog
from .model import Plan, Sampler, ThreadGroup
from .values import Values, property_defaults, user_variables
from .xmlutil import cls_of, pairs, prop, prop_el, rows, walk

# Thread groups the lab can read and divide. Short names are JMeter's own aliases.
CORE_GROUPS = {"ThreadGroup": "main", "SetupThreadGroup": "setup", "PostThreadGroup": "teardown"}
CONCURRENCY = "com.blazemeter.jmeter.threads.concurrency.ConcurrencyThreadGroup"
ARRIVALS = "com.blazemeter.jmeter.threads.arrivals.ArrivalsThreadGroup"
FREEFORM = "com.blazemeter.jmeter.threads.arrivals.FreeFormArrivalsThreadGroup"
ULTIMATE = "kg.apc.jmeter.threads.UltimateThreadGroup"
STEPPING = "kg.apc.jmeter.threads.SteppingThreadGroup"
OPEN_MODEL = ("OpenModelThreadGroup", "org.apache.jmeter.threads.openmodel.OpenModelThreadGroup")
CONSTANT_THROUGHPUT = ("ConstantThroughputTimer", "org.apache.jmeter.timers.ConstantThroughputTimer")
PRECISE_THROUGHPUT = ("PreciseThroughputTimer", "org.apache.jmeter.timers.poissonarrivals.PreciseThroughputTimer")
SHAPING = "kg.apc.jmeter.timers.VariableThroughputTimer"
SCRIPT_TIMERS = ("JSR223Timer", "BeanShellTimer")
HTTP_SAMPLERS = ("HTTPSamplerProxy", "HTTPSampler", "HTTPSampler2", "org.apache.jmeter.protocol.http.sampler.HTTPSamplerProxy")
FILE_PROPERTIES = ("filename", "File.path", "HTTPFileArg.path")
UNIT_SECONDS = {"ms": 0.001, "s": 1, "sec": 1, "min": 60, "m": 60, "hour": 3600, "h": 3600, "day": 86400, "d": 86400}


def is_listener(cls: str) -> bool:
    return any(x in cls for x in ("ResultCollector", "BackendListener", "ResultSaver"))


def is_thread_group(cls: str) -> bool:
    return cls in CORE_GROUPS or cls in OPEN_MODEL or cls.endswith("ThreadGroup")


def duration_text(s: str) -> float | None:
    """'10 min', '30s', '1h 30m', '500 ms' (the syntax of the Open Model Thread Group) -> seconds."""
    total, found = 0.0, False
    for n, u in re.findall(r"(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|sec|seconds?|m|min|minutes?|h|hours?|d|days?)?",
                           s.strip()):
        found = True
        u = (u or "s").lower()
        f = 0.001 if u.startswith(("ms", "milli")) else 60 if u.startswith("m") else \
            3600 if u.startswith("h") else 86400 if u.startswith("d") else 1
        total += float(n) * f
    return total if found else None


def _smallest(*values: int | None) -> int | None:
    found = [v for v in values if v]
    return min(found) if found else None


def _linear(points):
    out = []
    for t, v in points:
        p = (round(t, 3), round(v, 4))
        if not out or out[-1] != p:
            out.append(p)
    return out


# ====================================================================== thread groups
def _read_core(e, vals: Values, g: ThreadGroup, need):
    n = need(vals.num(prop(e, "ThreadGroup.num_threads"), 1), "the number of threads")
    ramp = vals.num(prop(e, "ThreadGroup.ramp_time"), 0) or 0
    scheduler = prop(e, "ThreadGroup.scheduler").lower() == "true"
    dur = vals.num(prop(e, "ThreadGroup.duration"), 0) if scheduler else 0
    delay = (vals.num(prop(e, "ThreadGroup.delay"), 0) or 0) if scheduler else 0
    loop = prop_el(e, "ThreadGroup.main_controller")
    loops_v = vals.num(prop(loop, "LoopController.loops"), 1) if loop is not None else 1
    forever = loops_v is not None and loops_v < 0
    g.threads = int(n) if n is not None else None
    g.share_cap = g.threads
    g.ramp_up, g.delay = float(ramp), float(delay)
    if scheduler and dur:
        g.duration = float(delay + dur)
    elif not forever and loops_v is not None:
        g.iterations = int(loops_v)
    if g.threads is not None:
        pts = [(delay, 0.0), (delay + ramp, float(g.threads))] if ramp else [(delay, float(g.threads))]
        if g.duration is not None:
            pts += [(g.duration, float(g.threads)), (g.duration, 0.0)]
        g.shape = _linear(pts)


def _read_dynamic(e, vals: Values, g: ThreadGroup, need, cls: str):
    """Concurrency and Arrivals thread groups (same fields)."""
    unit = 60.0 if prop(e, "Unit", "S").upper().startswith("M") else 1.0
    target = need(vals.num(prop(e, "TargetLevel"), 0), "the target level")
    ramp = (vals.num(prop(e, "RampUp"), 0) or 0) * unit
    hold = (vals.num(prop(e, "Hold"), 0) or 0) * unit
    steps = int(vals.num(prop(e, "Steps"), 0) or 0) or None
    iterations = vals.num(prop(e, "Iterations"), 0)
    g.ramp_up, g.steps = ramp, steps
    g.iterations = int(iterations) if iterations else None
    g.duration = ramp + hold if (ramp + hold) else None
    if cls == ARRIVALS:
        g.model = "open"
        limit = vals.num(prop(e, "ConcurrencyLimit"), 0)
        g.threads = int(limit) if limit else None
        g.share_cap = _smallest(int(target) if target else None, g.threads)
        level = (target or 0) / unit
    else:
        g.threads = int(target) if target is not None else None
        g.share_cap = g.threads
        level = float(target or 0)
    if target is None:
        return
    if ramp and steps:
        step = ramp / steps
        pts = []
        for k in range(steps):
            pts += [(k * step, level / steps * k if k else 0.0), (k * step, level / steps * (k + 1))]
        pts = pts[1:]
        pts.append((ramp, level))
    elif ramp:
        pts = [(0.0, 0.0), (ramp, level)]
    else:
        pts = [(0.0, level)]
    if g.duration:
        pts += [(g.duration, level), (g.duration, 0.0)]
    g.shape = _linear(pts)


def _read_freeform(e, vals: Values, g: ThreadGroup, need):
    g.model = "open"
    unit = 60.0 if prop(e, "Unit", "S").upper().startswith("M") else 1.0
    limit = vals.num(prop(e, "ConcurrencyLimit"), 0)
    g.threads = int(limit) if limit else None
    t, pts, smallest = 0.0, [], None
    for r in rows(e, "Schedule"):
        v = [vals.num(x.text) for x in r]
        if len(v) < 3 or any(x is None for x in v[:3]):
            need(None, "the schedule")
            pts = []
            break
        pts += [(t, v[0] / unit), (t + v[2] * unit, v[1] / unit)]
        t += v[2] * unit
        smallest = _smallest(smallest, *(int(x) for x in v[:2] if x))
    g.share_cap = _smallest(smallest, g.threads)
    g.duration = t if pts else None
    if pts:
        pts.append((t, 0.0))
    g.shape = _linear(pts)


def _read_ultimate(e, vals: Values, g: ThreadGroup, need):
    segs = []
    for r in rows(e, "ultimatethreadgroupdata"):
        v = [vals.num(x.text) for x in r]
        if len(v) < 5 or any(x is None for x in v[:5]):
            need(None, "the schedule")
            return
        segs.append(v[:5])
    if not segs:
        return
    times = sorted({0.0} | {x for _, d, up, hold, down in segs for x in (d, d + up, d + up + hold, d + up + hold + down)})

    def level(t):
        total = 0.0
        for n, d, up, hold, down in segs:
            if t < d:
                continue
            if t < d + up:
                total += n * (t - d) / up if up else n
            elif t < d + up + hold:
                total += n
            elif t < d + up + hold + down:
                total += n * (1 - (t - d - up - hold) / down) if down else 0
        return total

    pts = []
    for t in times:   # the value at each side of every corner: drops and jumps stay vertical
        left, right = level(t - 1e-9) if t > 0 else 0.0, level(t)
        pts += [(t, left)] if left == right else [(t, left), (t, right)]
    g.shape = _linear([*pts, (times[-1], 0.0)])
    g.duration = times[-1]
    g.threads = round(max(v for _, v in g.shape))
    g.share_cap = g.threads


def _read_stepping(e, vals: Values, g: ThreadGroup, need):
    g.unsplittable.append("a Stepping Thread Group cannot be divided between generators "
                          "(use a Concurrency or Ultimate Thread Group)")
    n = need(vals.num(prop(e, "ThreadGroup.num_threads"), 1), "the number of threads")
    if n is None:
        return
    delay = vals.num(prop(e, "Threads initial delay"), 0) or 0
    burst = vals.num(prop(e, "Start users count burst"), 0) or 0
    count = vals.num(prop(e, "Start users count"), 1) or 1
    period = vals.num(prop(e, "Start users period"), 0) or 0
    flight = vals.num(prop(e, "flighttime"), 0) or 0
    stop_count = vals.num(prop(e, "Stop users count"), 1) or 1
    stop_period = vals.num(prop(e, "Stop users period"), 0) or 0
    g.threads = int(n)
    cur, t = min(burst, n), delay
    pts = [(delay, 0.0), (t, cur)]
    while cur < n:
        pts += [(t, cur), (t, min(n, cur + count))]
        cur = min(n, cur + count)
        if cur < n:
            t += period
    t += flight
    pts.append((t, cur))
    while cur > 0:
        t += stop_period
        pts += [(t, cur), (t, max(0.0, cur - stop_count))]
        cur = max(0.0, cur - stop_count)
    g.shape = _linear(pts)
    g.duration = t
    g.delay = delay


def _read_open_model(e, vals: Values, g: ThreadGroup, need):
    g.model = "open"
    schedule = vals.text(prop(e, "OpenModelThreadGroup.schedule")) or ""
    tokens = re.findall(r"(rate|random_arrivals|even_arrivals|pause)\s*\(([^)]*)\)", schedule)
    rates: dict[int, float] = {}
    for i, (fn, arg) in enumerate(tokens):
        if fn != "rate":
            continue
        m = re.match(r"\s*([\d.]+)\s*(?:/\s*(ms|s|sec|min|m|hour|h|day|d))?\s*$", arg)
        if not m:
            tokens = []
            break
        rates[i] = float(m.group(1)) / UNIT_SECONDS[m.group(2) or "s"]
    t, rate, pts = 0.0, 0.0, []
    for i, (fn, arg) in enumerate(tokens):
        if fn == "rate":
            rate = rates[i]
            continue
        d = duration_text(arg) or 0.0
        if fn == "pause":
            pts += [(t, 0.0), (t + d, 0.0)]
        else:   # arrivals ramp towards the rate() that follows them, if any
            pts += [(t, rate), (t + d, rates.get(i + 1, rate))]
        t += d
    g.shape = _linear([*pts, (t, 0.0)]) if pts else []
    g.duration = t or None
    if not pts:
        need(None, "the schedule")


def _read_group(e: ET.Element, vals: Values, warns: list[str]) -> ThreadGroup:
    cls = cls_of(e)
    name = e.get("testname") or cls
    g = ThreadGroup(name, cls.rsplit(".", 1)[-1], CORE_GROUPS.get(cls, "main"))

    def need(v, what):
        if v is None:
            g.unsplittable.append(f"{what} depends on a variable that is only known during the test")
            warns.append(f"thread group '{name}': {what} depends on a variable, so it cannot be known "
                         "before the test")
        return v

    if cls in CORE_GROUPS:
        _read_core(e, vals, g, need)
    elif cls in (CONCURRENCY, ARRIVALS):
        _read_dynamic(e, vals, g, need, cls)
    elif cls == FREEFORM:
        _read_freeform(e, vals, g, need)
    elif cls == ULTIMATE:
        _read_ultimate(e, vals, g, need)
    elif cls == STEPPING:
        _read_stepping(e, vals, g, need)
    elif cls in OPEN_MODEL:
        _read_open_model(e, vals, g, need)
    else:
        g.unsplittable.append(f"{g.cls} is not a thread group the lab can read, so its load cannot be divided")
        warns.append(f"thread group '{name}': {g.cls} is not a thread group the lab can read; "
                     "its load is not drawn and it cannot be divided between generators")
    return g


def _read_timers(sub: ET.Element | None, vals: Values, g: ThreadGroup, warns: list[str]):
    """The pacing inside a thread group: the rate it is asked to send (req/s) and whether a script decides it."""
    for e, _, _ in walk(sub):
        cls = cls_of(e)
        if cls in SCRIPT_TIMERS:
            g.scripted_pacing = True
            if "paces its requests with a script" not in " ".join(g.unsplittable):
                g.unsplittable.append("it paces its requests with a script (a JSR223 or BeanShell timer), "
                                      "which the lab cannot divide")
        elif cls in CONSTANT_THROUGHPUT:
            per_min = vals.num(prop(e, "throughput"))
            mode = int(vals.num(prop(e, "calcMode"), 0) or 0)
            if per_min is None:
                warns.append(f"thread group '{g.name}': the throughput of its timer depends on a variable")
                g.unsplittable.append("the throughput of its timer depends on a variable")
                continue
            rate = per_min / 60.0 * ((g.threads or 1) if mode == 0 else 1)
            g.rate_shape = [(t, rate if v > 0 else 0.0) for t, v in g.shape] or [(0.0, rate)]
            g.model = "throughput" if g.model == "closed" else g.model
        elif cls in PRECISE_THROUGHPUT:
            tp = vals.num(prop(e, "throughput"))
            period = vals.num(prop(e, "throughputPeriod"), 3600) or 3600
            if tp is None:
                g.unsplittable.append("the throughput of its timer depends on a variable")
                continue
            rate = tp / period
            g.rate_shape = [(t, rate if v > 0 else 0.0) for t, v in g.shape] or [(0.0, rate)]
            g.model = "throughput" if g.model == "closed" else g.model
        elif cls == SHAPING:
            t, pts = 0.0, []
            for r in rows(e, "load_profile"):
                v = [vals.num(x.text) for x in r]
                if len(v) < 3 or any(x is None for x in v[:3]):
                    pts = []
                    g.unsplittable.append("the load profile of its timer depends on a variable")
                    break
                pts += [(t, v[0]), (t + v[2], v[1])]
                t += v[2]
            if pts:
                g.rate_shape = _linear([*pts, (t, 0.0)])
                g.model = "throughput" if g.model == "closed" else g.model
                g.duration = t if g.duration is None else min(g.duration, t)   # the timer ends the group


# ====================================================================== the plan
def locate(raw: str, base: Path, vals: Values) -> Path | None:
    """The file a plan refers to: as written (relative to the plan), else its name next to the plan (plans made
    on another machine often carry absolute paths)."""
    name = vals.text(raw)
    if name is None:
        return None
    p = Path(name)
    cands = [p] if p.is_absolute() else [base / p, base / name.replace("\\", "/")]
    cands.append(base / re.split(r"[\\/]", name)[-1])
    return next((c for c in cands if c.is_file()), None)


def _empty(path: Path, error: str) -> Plan:
    return Plan(path, path.stem, "", [], [], [], [], False, False, {}, [error], [])


def read_plan(path: Path, installed_plugins: list[str] | None = None) -> Plan:
    """Read a .jmx. `installed_plugins` are the plugin ids of the generator image (None: do not check)."""
    errs: list[str] = []
    warns: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
        root = ET.fromstring(text)
    except UnicodeDecodeError:
        return _empty(path, "not a valid JMeter plan (the file is not UTF-8 text)")
    except ET.ParseError as e:
        return _empty(path, f"not a valid JMeter plan (XML error: {e})")
    if root.tag != "jmeterTestPlan" or root.find("hashTree") is None:
        return _empty(path, "not a JMeter test plan (the root element must be <jmeterTestPlan>)")
    top = root.find("hashTree")
    test_plan = next((e for e, _ in pairs(top) if cls_of(e) == "TestPlan"), None)
    if test_plan is None:
        return _empty(path, "the plan has no Test Plan element")
    saved_by = root.get("jmeter", "")
    if saved_by and not saved_by.startswith("5."):
        warns.append(f"the plan was saved by JMeter {saved_by}; the generators run JMeter 5.6.3")

    title = (test_plan.get("testname") or "").strip()
    if title.lower() in ("", "test plan", "plan de pruebas"):
        title = path.stem
    serialized = prop(test_plan, "TestPlan.serialize_threadgroups").lower() == "true"
    if prop(test_plan, "TestPlan.functional_mode").lower() == "true":
        warns.append("the plan is in functional test mode (it saves every response), which is slower than a load test")
    vals = Values(user_variables(top))

    groups: list[ThreadGroup] = []
    samplers: list[Sampler] = []
    transactions: list[str] = []
    classes: set[str] = set()
    files: list[tuple[str, Path]] = []
    listeners = 0
    plan_level_script_timer = False
    group_trees: list[tuple[ThreadGroup, ET.Element | None]] = []
    for e, sub, depth in walk(top):
        cls = cls_of(e)
        if "." in cls and not cls.startswith("org.apache.jmeter."):
            classes.add(cls)
        if is_listener(cls):
            listeners += 1
            continue
        if is_thread_group(cls):
            g = _read_group(e, vals, warns)
            _read_timers(sub, vals, g, warns)
            groups.append(g)
            group_trees.append((g, sub))
        elif cls in SCRIPT_TIMERS and depth <= 1 and not any(sub is s for _, s in group_trees):
            plan_level_script_timer = True
        elif cls in HTTP_SAMPLERS:
            p = vals.text(prop(e, "HTTPSampler.path")) or prop(e, "HTTPSampler.path")
            samplers.append(Sampler(e.get("testname") or p, (prop(e, "HTTPSampler.method") or "GET").upper(), p, cls))
        elif cls.endswith("Sampler") or cls in ("JSR223Sampler", "DebugSampler", "BeanShellSampler"):
            samplers.append(Sampler(e.get("testname") or cls, None, "", cls))
        elif cls == "TransactionController":
            transactions.append(e.get("testname") or "Transaction")
        elif cls == "IncludeController":
            errs.append(f"'{e.get('testname')}' includes another plan ({prop(e, 'IncludeController.includepath')}): "
                        "deliver one self-contained plan")
        if not is_listener(cls):
            for c in e.iter("stringProp"):
                if c.get("name") not in FILE_PROPERTIES or not (c.text or "").strip():
                    continue
                raw = c.text.strip()
                found = locate(raw, path.parent, vals)
                if found is None:
                    if vals.text(raw) is None:
                        warns.append(f"'{e.get('testname')}' reads a file whose name depends on a variable ({raw}): "
                                     "the lab cannot check it or send it to the generators")
                    else:
                        errs.append(f"'{e.get('testname')}' reads {raw}, but that file is not next to the plan "
                                    f"(put it in {path.parent.name}/ or in a subfolder)")
                elif path.parent.resolve() not in found.resolve().parents:
                    errs.append(f"'{e.get('testname')}' reads {raw}: data files must be next to the plan or in a "
                                "subfolder")
                else:
                    files.append((raw, found))
    if plan_level_script_timer:
        for g in (x for x in groups if x.role == "main"):
            g.scripted_pacing = True
            g.unsplittable.append("the plan has a script timer (JSR223 or BeanShell) that paces its requests, "
                                  "which the lab cannot divide")

    _place_groups(groups, serialized)
    if not any(g.role == "main" for g in groups):
        errs.append("the plan has no enabled thread group")
    if not any(s.method for s in samplers):
        errs.append("the plan sends no HTTP request")
    for g in groups:
        if g.role == "main" and g.duration is None and g.iterations is None:
            warns.append(f"thread group '{g.name}' has no end (it loops forever with no duration): "
                         "the lab stops it at generators.max_duration")
    if listeners:
        warns.append(f"{listeners} listener(s) are removed from the copy that runs: they only cost CPU, "
                     "the lab records every sample itself")
    other = sorted({s.cls for s in samplers if not s.method})
    if other:
        warns.append("samplers that are not HTTP requests are counted as endpoints too: " + ", ".join(other))

    needs = _plugin_needs(classes, text)
    if installed_plugins is not None:
        for pid, why in sorted(needs.items()):
            if pid not in installed_plugins:
                errs.append(f"{why} needs the JMeter plugin '{pid}', which the generator image does not include.\n"
                            f"      Add '{pid}' to generators.jmeter_plugins in config/lab.yaml and run `up` again.")
    for cls in sorted(c for c in classes if catalog.plugin_of_class(c) is None and not is_listener(c)):
        warns.append(f"uses {cls}, an element from a plugin the lab does not know; if the run fails to load the "
                     "plan, add the plugin to images/generator/plugins.json")
    if serialized and len([g for g in groups if g.role == "main"]) > 1:
        warns.append("thread groups run one after the other (Run Thread Groups consecutively)")
    return Plan(path, title, prop(test_plan, "TestPlan.comments"), groups, samplers, files, transactions, serialized,
                "morfeo_generator" in text, property_defaults(text), errs, warns)


def _plugin_needs(classes: set[str], text: str) -> dict[str, str]:
    needs: dict[str, str] = {}
    for cls in sorted(classes):
        pid = catalog.plugin_of_class(cls)
        if pid:
            needs.setdefault(pid, f"the plan uses {cls.rsplit('.', 1)[-1]}, which")
    for pid in sorted(catalog.function_plugins(text)):
        needs.setdefault(pid, "the plan calls functions that")
    return {pid: why.replace("which", "").strip() if why.endswith("which") else why for pid, why in needs.items()}


def _place_groups(groups: list[ThreadGroup], serialized: bool):
    """Put every main group on the timeline of the test. With 'Run Thread Groups consecutively' each group
    starts when the previous one ends; after a group of unknown length nothing can be placed."""
    offset: float | None = 0.0
    for g in groups:
        if g.role != "main":
            continue
        g.start = offset if serialized else 0.0
        if g.start is None:
            continue
        g.shape = [(t + g.start, v) for t, v in g.shape]
        g.rate_shape = [(t + g.start, v) for t, v in g.rate_shape]
        if serialized:
            offset = g.end


