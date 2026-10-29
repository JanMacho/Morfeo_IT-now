"""The JMeter plugins the generator image can contain, read from images/generator/plugins.json (the same file
the image build installs from, so the two cannot disagree)."""
from __future__ import annotations

import json
import re
from functools import lru_cache

from ..errors import LabError
from ..paths import IMAGES

PLUGINS_FILE = IMAGES / "generator" / "plugins.json"


@lru_cache(maxsize=1)
def catalog() -> dict:
    try:
        return json.loads(PLUGINS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise LabError(f"Cannot read the plugin catalogue {PLUGINS_FILE}: {e}") from e


def plugin_ids() -> list[str]:
    return sorted(catalog()["plugins"])


def check_plugin_ids(ids: list[str]) -> None:
    unknown = [i for i in ids if i not in catalog()["plugins"]]
    if unknown:
        raise LabError(f"generators.jmeter_plugins: unknown plugin(s) {', '.join(unknown)}.\n"
                       f"Available: {', '.join(plugin_ids())}.\n"
                       f"To add another one, add its Maven artifact to {PLUGINS_FILE.name} (docs/CONFIGURATION.md).")


def plugin_of_class(cls: str) -> str | None:
    """The plugin id that provides a JMeter element class, or None if no known plugin does."""
    for pid, spec in catalog()["plugins"].items():
        if cls in spec.get("classes", ()):
            return pid
    return None


def function_plugins(xml_text: str) -> set[str]:
    """Plugins whose extra functions (${__MD5(...)}...) the plan calls. A function that JMeter does not know
    stays as literal text in the request, with no error, so this is worth checking."""
    found = set()
    for pid, spec in catalog()["plugins"].items():
        names = spec.get("functions")
        if names and re.search(r"\$\{__(?:" + "|".join(map(re.escape, names)) + r")\(", xml_text):
            found.add(pid)
    return found
