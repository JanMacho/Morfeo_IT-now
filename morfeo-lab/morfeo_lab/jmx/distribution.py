"""How the load of a plan is shared between generators without changing the total.

The lab divides only what it understands with certainty (thread counts, arrival rates and the standard
throughput timers). Anything else is never guessed:

  split   every thread group is made of elements the lab can divide: each generator gets its share.
  single  the load of some group is decided by a script (a JSR223 or BeanShell timer), by a variable known only
          during the test or by an element the lab cannot read. Dividing threads alone would change the load, so
          the plan runs as it is on one generator.
  plan    the plan reads `morfeo_generators` / `morfeo_generator`: it divides its own load, and the lab leaves
          it untouched on as many generators as requested.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import Plan


@dataclass
class Distribution:
    mode: str                                    # split | single | plan
    reasons: list[str] = field(default_factory=list)
    max_generators: int | None = None            # most generators the load can be divided into (split)

    def describe(self) -> str:
        if self.mode == "plan":
            return "the plan divides its own load (it reads morfeo_generators)"
        if self.mode == "split":
            cap = f", up to {self.max_generators} generators" if self.max_generators else ""
            return f"the lab divides the load between generators{cap}"
        return "one generator: " + "; ".join(self.reasons)


def distribution_of(plan: Plan) -> Distribution:
    if plan.reads_generators:
        return Distribution("plan")
    reasons, caps = [], []
    for g in plan.mains:
        for why in g.unsplittable:
            reasons.append(f"thread group '{g.name}': {why}")
        if g.splittable and g.share_cap:
            caps.append(g.share_cap)
    if reasons:
        return Distribution("single", reasons)
    return Distribution("split", [], min(caps) if caps else None)


def split_int(total: float, n: int, k: int, rotation: int = 0) -> int:
    """Generator k's share of a whole number split among n generators. The shares add up to `total`; the
    remainder goes to different generators for different `rotation` values, so that many small groups do not
    all give their extra thread to the first generator."""
    base, rest = divmod(round(total), n)
    return base + (1 if (k - rotation) % n < rest else 0)
