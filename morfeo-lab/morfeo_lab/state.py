"""What the lab remembers between commands: which application is deployed on each cluster (.morfeo-state.json)."""
from __future__ import annotations

import json

from .config import Target, load_target
from .console import warn
from .errors import LabError
from .paths import STATE_FILE
from .shell import cmd_name


def _key(lab: dict) -> str:
    c = lab["cluster"]
    return f"{c['provider']}:{c.get('context') or c['name']}"


def _read_all() -> dict:
    if not STATE_FILE.exists():
        return {"clusters": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        warn(f"{STATE_FILE.name} is corrupt and was ignored")
        return {"clusters": {}}


def _write_all(data: dict):
    STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def save(lab: dict, data: dict):
    everything = _read_all()
    everything.setdefault("clusters", {}).setdefault(_key(lab), {}).update(data)
    _write_all(everything)


def load(lab: dict) -> dict:
    return _read_all().get("clusters", {}).get(_key(lab), {})


def clear(lab: dict):
    everything = _read_all()
    everything.get("clusters", {}).pop(_key(lab), None)
    _write_all(everything)


def current_target(lab: dict, ref: str | None, **kw) -> Target:
    """The application named on the command line, or the one that is deployed."""
    if ref:
        return load_target(ref, **kw)
    st = load(lab)
    if not st.get("target"):
        raise LabError(f"No application has been deployed yet.\nDeploy one with: {cmd_name()} deploy <application>")
    if "replicas" not in kw and st.get("replicas"):
        kw["replicas"] = st["replicas"]
    if st.get("jar") and not kw.get("jar"):
        kw["jar"] = st["jar"]   # it was deployed with --jar: keep using that file
    return load_target(st["target"], **{k: v for k, v in kw.items() if v is not None})
