"""What the lab knows about a plan after reading it: thread groups with the load they ask for, the requests
and the data files."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlsplit

KEYED = re.compile(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) /\S*$")


@dataclass
class ThreadGroup:
    name: str
    cls: str                          # short class name, for messages
    role: str                         # main | setup | teardown
    model: str = "closed"             # closed (threads) | throughput (threads paced to a rate) | open (arrivals/s)
    threads: int | None = None        # most threads at once (open model: the thread limit, if any)
    shape: list = field(default_factory=list)       # [(t, threads or arrivals/s)] from the start of the test
    rate_shape: list = field(default_factory=list)  # [(t, req/s)] asked by throughput timers
    start: float | None = 0.0         # when the group starts (not 0 with 'Run Thread Groups consecutively')
    delay: float = 0.0                # start delay inside the group
    duration: float | None = None     # end of the group counted from its start; None = set by iterations
    ramp_up: float = 0.0
    steps: int | None = None
    iterations: int | None = None
    share_cap: int | None = None      # the whole number the lab divides between generators (threads, arrivals per unit)
    scripted_pacing: bool = False     # a JSR223/BeanShell timer decides when requests are sent
    unsplittable: list[str] = field(default_factory=list)   # why the load of this group cannot be divided

    @property
    def end(self) -> float | None:
        """Planned end counted from the start of the test."""
        return None if self.duration is None or self.start is None else self.start + self.duration

    @property
    def peak(self) -> float:
        return max((v for _, v in self.shape), default=0.0)

    @property
    def peak_rate(self) -> float:
        return max((v for _, v in self.rate_shape), default=0.0)

    @property
    def splittable(self) -> bool:
        return not self.unsplittable

    def at(self, points: list, t: float) -> float:
        """Linear interpolation of a [(t, value)] shape at second t (0 outside the shape)."""
        if not points or t < points[0][0] or t >= points[-1][0]:
            return 0.0
        for (t0, v0), (t1, v1) in pairwise(points):
            if t0 <= t < t1:
                return v0 + (v1 - v0) * (t - t0) / (t1 - t0) if t1 > t0 else v1
        return 0.0

    def level_at(self, t: float) -> float:
        """What this group asks for at second t: threads, req/s (throughput) or arrivals/s (open)."""
        return self.at(self.rate_shape if self.model == "throughput" else self.shape, t)


@dataclass
class Sampler:
    label: str
    method: str | None                # None: not an HTTP request
    path: str = ""
    cls: str = ""


@dataclass
class Plan:
    path: Path
    title: str
    description: str
    groups: list[ThreadGroup]
    samplers: list[Sampler]
    files: list[tuple[str, Path]]     # (path as written in the plan, file found next to it)
    transactions: list[str]
    serialized: bool                  # 'Run Thread Groups consecutively'
    reads_generators: bool            # the plan divides its own load using morfeo_generators
    properties: dict[str, str]        # JMeter properties the plan reads, with their defaults
    errors: list[str]
    warnings: list[str]

    @property
    def mains(self) -> list[ThreadGroup]:
        return [g for g in self.groups if g.role == "main"]

    @property
    def planned_duration(self) -> float | None:
        """Seconds from the start of the test to the end of the last main thread group; None when one of them
        ends by iterations."""
        ends = [g.end for g in self.mains]
        return None if not ends or any(e is None for e in ends) else max(ends)

    @property
    def ramp_up(self) -> float:
        """The warm-up that is left out of the steady state: the longest ramp among groups that start at once."""
        return max((g.ramp_up for g in self.mains if not g.delay and not g.start), default=0.0)

    @property
    def peak_threads(self) -> int:
        peaks = [g.threads or 0 for g in self.mains if g.model != "open"]
        return (max(peaks) if self.serialized else sum(peaks)) if peaks else 0

    @property
    def target_rate(self) -> float | None:
        """Requests per second asked by throughput timers, when every main group is paced by one."""
        rates = [g.peak_rate for g in self.mains]
        return sum(rates) if rates and all(rates) else None

    @property
    def target_arrivals(self) -> float | None:
        arr = [g.peak for g in self.mains if g.model == "open"]
        return sum(arr) if arr and all(g.model == "open" for g in self.mains) else None

    @property
    def model(self) -> str:
        models = {g.model for g in self.mains}
        return models.pop() if len(models) == 1 else "mixed"

    def timeline(self, resolution: float = 1.0) -> list[dict]:
        """The load the plan asks for, second by second (summed over its main groups)."""
        end = self.planned_duration or 0
        out = []
        for i in range(int(-(-end // resolution))):
            t = i * resolution + resolution / 2
            row = {"t": i * resolution, "users": 0.0, "rps": 0.0, "arrivals": 0.0}
            for g in self.mains:
                if g.model == "open":
                    row["arrivals"] += g.level_at(t)
                else:
                    row["users"] += g.at(g.shape, t)
                    if g.model == "throughput":
                        row["rps"] += g.level_at(t)
            out.append(row)
        return out


# ------------------------------------------------------------------ endpoint names
def template(path: str) -> str:
    """'/api/saldo/${id}?x=1' -> '/api/saldo/{id}'"""
    if "://" in path:
        path = urlsplit(path).path
    path = path.split("?")[0] or "/"
    t = "/".join("{id}" if "${" in seg else seg for seg in path.split("/"))
    return t if t.startswith("/") else "/" + t


def endpoint_labels(plan: Plan) -> tuple[list[str], dict[str, list[str]], dict[str, str]]:
    """The sample labels of the plan, the HTTP methods behind each, and the endpoint key
    ('GET /api/saldo/{id}') of every label that always means the same request."""
    methods: dict[str, set] = {}
    keys: dict[str, set] = {}
    for s in plan.samplers:
        methods.setdefault(s.label, set())
        keys.setdefault(s.label, set())
        if s.method:
            methods[s.label].add(s.method)
            keys[s.label].add(s.label if KEYED.match(s.label) else f"{s.method} {template(s.path)}")
    return (sorted(methods), {k: sorted(v) for k, v in methods.items() if v},
            {k: next(iter(v)) for k, v in keys.items() if len(v) == 1})
