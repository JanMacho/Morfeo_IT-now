"""The copy of the plan that one generator runs.

The lab runs the plan as it is. It changes only what running it in the cluster requires, and when nothing
needs to change the file is sent byte for byte:

  * requests to localhost (or with no host) go to the deployed application; requests to other hosts are sent
    as they are;
  * listeners and disabled elements are removed (the lab records every sample itself);
  * setUp and tearDown thread groups exist on generator 0 only, so they run once;
  * with several generators and a plan that the lab divides, each generator gets its share of every thread
    group and throughput timer, so the total load is the one of the plan;
  * data files found next to the plan are referred to by a relative path.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from ..errors import LabError
from . import read
from .distribution import Distribution, split_int
from .model import Plan
from .values import Values, user_variables
from .xmlutil import cls_of, enabled, pairs, prop, prop_el, remove_with_tree, rows, set_prop, walk

LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}


class SplitError(LabError):
    """A thread group or timer whose load cannot be divided exactly."""


def _num_text(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.10g}"


class _Compiler:
    def __init__(self, plan: Plan, generators: int, k: int, app_url: str, dist: Distribution):
        self.plan, self.n, self.k, self.dist = plan, generators, k, dist
        self.tree = ET.parse(plan.path)
        self.root = self.tree.getroot()
        self.top = self.root.find("hashTree")
        self.vals = Values(user_variables(self.top))
        self.changed = False
        self.notes: list[str] = []
        u = urlsplit(app_url)
        self.host = u.hostname or ""
        self.port = str(u.port or (443 if u.scheme == "https" else 80))
        self.scheme = u.scheme

    # ------------------------------------------------------------ 1. listeners and disabled elements
    def prune(self, ht: ET.Element | None = None):
        ht = self.top if ht is None else ht
        for e, sub in list(pairs(ht)):
            if not enabled(e) or read.is_listener(cls_of(e)):
                remove_with_tree(ht, e)
                self.changed = True
            elif sub is not None:
                self.prune(sub)

    # ------------------------------------------------------------ 2. requests go to the application
    def point_hosts(self):
        elements = [e for e, _, _ in walk(self.top)]
        defaults = [e for e in elements if cls_of(e) == "ConfigTestElement"
                    and "HttpDefaults" in (e.get("guiclass") or "")]
        external: set[str] = set()

        def point(e):
            for name, value in (("HTTPSampler.domain", self.host), ("HTTPSampler.port", self.port),
                                ("HTTPSampler.protocol", self.scheme)):
                current = prop(e, name)
                if current == value or (name == "HTTPSampler.protocol" and not current and value == "http"):
                    continue   # an empty protocol already means http
                set_prop(e, name, value)
                self.changed = True

        def is_local(e) -> bool | None:
            """True: it goes to the application · False: another host · None: no host here at all."""
            raw = prop(e, "HTTPSampler.domain")
            path = prop(e, "HTTPSampler.path")
            if not raw and "://" in path:
                full = urlsplit(self.vals.text(path) or path)
                if (full.hostname or "").lower() in LOCAL_HOSTS:
                    set_prop(e, "HTTPSampler.path", (full.path or "/") + (f"?{full.query}" if full.query else ""))
                    self.changed = True
                    return True
                external.add(full.hostname or path)
                return False
            if not raw:
                return None if e in defaults else True
            host = self.vals.text(raw)
            if host is None:
                self.notes.append(f"'{e.get('testname')}': the host {raw} is only known during the test; "
                                  "it is sent as it is")
                return False
            if host.lower() in LOCAL_HOSTS or host == self.host:
                return True
            external.add(host)
            return False

        has_default_host = any(prop(d, "HTTPSampler.domain") for d in defaults)
        default_local = False
        for d in defaults:
            if is_local(d):
                point(d)
                default_local = True
        for e in elements:
            if cls_of(e) not in read.HTTP_SAMPLERS:
                continue
            own = bool(prop(e, "HTTPSampler.domain")) or "://" in prop(e, "HTTPSampler.path")
            if own or not has_default_host:
                if is_local(e):
                    point(e)
            elif default_local and prop(e, "HTTPSampler.port"):   # the host comes from the defaults, not the port
                set_prop(e, "HTTPSampler.port", self.port)
                self.changed = True
        for h in sorted(external):
            self.notes.append(f"requests to {h} are sent as they are (outside the lab)")

    # ------------------------------------------------------------ 3. setUp, tearDown and the share of each generator
    def distribute(self):
        for e, sub in pairs(self.top):
            if cls_of(e) != "TestPlan" or sub is None:
                continue
            for g, gsub in list(pairs(sub)):
                cls = cls_of(g)
                if not read.is_thread_group(cls):
                    continue
                if cls in ("SetupThreadGroup", "PostThreadGroup"):
                    if self.k:
                        remove_with_tree(sub, g)
                        self.changed = True
                    continue
                if self.n > 1 and self.dist.mode == "split":
                    self._split_group(g, gsub)
            if self.n > 1 and self.dist.mode == "split":   # timers at plan level act on every group of this JVM
                for t, _ in pairs(sub):
                    self._split_timer(t)

    def _need(self, v, what, name):
        if v is None:
            raise SplitError(f"thread group '{name}': {what} depends on a variable, so the lab cannot divide it "
                             "between generators; use --generators 1")
        return v

    def _share(self, g: ET.Element, name: str, prop_name: str):
        total = self._need(self.vals.num(prop(g, prop_name), 0), prop_name, name)
        part = split_int(total, self.n, self.k)
        if total and part < 1:
            raise SplitError(f"thread group '{name}': {prop_name} is {total:g}, fewer than the {self.n} generators; "
                             "use fewer --generators")
        set_prop(g, prop_name, part)
        self.changed = True

    def _split_group(self, g: ET.Element, sub: ET.Element | None):
        cls, name = cls_of(g), g.get("testname") or cls_of(g)
        if cls in read.CORE_GROUPS:
            self._share(g, name, "ThreadGroup.num_threads")
        elif cls == read.CONCURRENCY:
            self._share(g, name, "TargetLevel")
        elif cls == read.ARRIVALS:
            self._share(g, name, "TargetLevel")
            if self.vals.num(prop(g, "ConcurrencyLimit"), 0):
                self._share(g, name, "ConcurrencyLimit")
        elif cls == read.FREEFORM:
            for i, r in enumerate(rows(g, "Schedule")):
                for c in r[:2]:
                    c.text = str(split_int(self._need(self.vals.num(c.text), "the schedule", name), self.n, self.k, i))
            if self.vals.num(prop(g, "ConcurrencyLimit"), 0):
                self._share(g, name, "ConcurrencyLimit")
            self.changed = True
        elif cls == read.ULTIMATE:
            for i, r in enumerate(rows(g, "ultimatethreadgroupdata")):
                r[0].text = str(split_int(self._need(self.vals.num(r[0].text), "the schedule", name),
                                          self.n, self.k, i))
            if not any(int(r[0].text) for r in rows(g, "ultimatethreadgroupdata")):
                raise SplitError(f"thread group '{name}' has fewer threads than the {self.n} generators; "
                                 "use fewer --generators")
            self.changed = True
        elif cls in read.OPEN_MODEL:
            el = prop_el(g, "OpenModelThreadGroup.schedule")
            text = self._need(self.vals.text(prop(g, "OpenModelThreadGroup.schedule")), "the schedule", name)
            el.text = re.sub(r"rate\(\s*([\d.]+)", lambda m: f"rate({_num_text(float(m.group(1)) / self.n)}", text)
            seed = self.vals.num(prop(g, "OpenModelThreadGroup.random_seed"))
            if seed is not None:   # the same seed on every generator would make all arrivals coincide
                set_prop(g, "OpenModelThreadGroup.random_seed", int(seed) + self.k)
            self.changed = True
        else:
            raise SplitError(f"thread group '{name}' ({cls.rsplit('.', 1)[-1]}) cannot be divided between "
                             "generators; use --generators 1")
        for t, _, _ in walk(sub):
            self._split_timer(t)

    def _split_timer(self, t: ET.Element):
        cls = cls_of(t)
        if not enabled(t):
            return
        name = t.get("testname") or cls
        if cls in read.CONSTANT_THROUGHPUT:
            if int(self.vals.num(prop(t, "calcMode"), 0) or 0) != 0:   # mode 0 is per thread: threads are divided
                v = self._need(self.vals.num(prop(t, "throughput")), "the throughput", name)
                set_prop(t, "throughput", round(v / self.n, 6))
                self.changed = True
        elif cls in read.PRECISE_THROUGHPUT:
            v = self._need(self.vals.num(prop(t, "throughput")), "the throughput", name)
            set_prop(t, "throughput", round(v / self.n, 6))
            self.changed = True
        elif cls == read.SHAPING:
            for r in rows(t, "load_profile"):
                for c in r[:2]:
                    c.text = _num_text(self._need(self.vals.num(c.text), "the load profile", name) / self.n)
            self.changed = True

    # ------------------------------------------------------------ 4. data files
    def data_paths(self):
        mapping = {raw: f.resolve().relative_to(self.plan.path.parent.resolve()).as_posix()
                   for raw, f in self.plan.files}
        for e, _, _ in walk(self.top):
            for c in e.iter("stringProp"):
                raw = (c.text or "").strip()
                if c.get("name") in read.FILE_PROPERTIES and raw in mapping and mapping[raw] != raw:
                    c.text = mapping[raw]
                    self.changed = True

    def result(self) -> bytes:
        if not self.changed:
            return self.plan.path.read_bytes()
        return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(self.root, encoding="utf-8")


def compile_plan(plan: Plan, generators: int, k: int, app_url: str, dist: Distribution) -> tuple[bytes, list[str]]:
    """The plan generator k of `generators` runs, and the notes worth showing about how it was prepared."""
    if generators > 1 and dist.mode == "single":
        raise SplitError("the plan cannot be divided between generators:\n    - " + "\n    - ".join(dist.reasons)
                         + "\n    Run it with --generators 1, or make the plan divide its own load using the "
                           "morfeo_generators property (docs/SCENARIOS.md).")
    c = _Compiler(plan, generators, k, app_url, dist)
    c.prune()
    c.point_hosts()
    c.distribute()
    c.data_paths()
    return c.result(), c.notes
