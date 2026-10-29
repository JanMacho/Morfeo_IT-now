"""Resolving the values of a plan that depend on JMeter properties and user-defined variables."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .xmlutil import pairs, prop

# ${__P(name,default)} and ${__property(name,variable,default)}
_PROPERTY = re.compile(r"\$\{__(?:P|property)\(([^,()]*)(?:,([^()]*?))?(?:,([^()]*))?\)\}")
_VARIABLE = re.compile(r"\$\{([A-Za-z_][\w.-]*)\}")


class Values:
    """Resolves ${__P(name,default)}, ${__property(name,,default)} and ${variable} (user-defined variables of
    the plan). What cannot be known before the test (a value read at run time) resolves to None."""

    def __init__(self, variables: dict[str, str], props: dict[str, str] | None = None):
        self.variables, self.props = variables, props or {}

    def text(self, s: str | None) -> str | None:
        s = s or ""
        for _ in range(6):
            def prop_value(m):
                name = m.group(1).strip()
                default = m.group(3) if m.group(3) is not None else (m.group(2) if m.group(2) is not None else "")
                return self.props.get(name, default.strip())
            new = _PROPERTY.sub(prop_value, s)
            new = _VARIABLE.sub(lambda m: self.variables.get(m.group(1), m.group(0)), new)
            if new == s:
                break
            s = new
        return None if "${" in s else s

    def num(self, s: str | None, default: float | None = None) -> float | None:
        if s is None or str(s).strip() == "":
            return default
        t = self.text(str(s))
        if t is None:
            return None
        try:
            return float(t.strip())
        except ValueError:
            return None


def property_defaults(xml_text: str) -> dict[str, str]:
    """The JMeter properties a plan reads with ${__P(name,default)}, with their defaults. Useful to know which
    -J options a plan understands."""
    found: dict[str, str] = {}
    for m in _PROPERTY.finditer(xml_text):
        name = m.group(1).strip()
        if name and name not in found:
            found[name] = (m.group(3) if m.group(3) is not None else m.group(2) or "").strip()
    return found


def user_variables(top: ET.Element) -> dict[str, str]:
    """The user-defined variables of the Test Plan and of its 'User Defined Variables' elements."""
    out: dict[str, str] = {}

    def collect(e: ET.Element):
        col = next((c for c in e.iter("collectionProp") if c.get("name") == "Arguments.arguments"), None)
        for a in (col if col is not None else []):
            name, value = prop(a, "Argument.name"), prop(a, "Argument.value")
            if name:
                out[name] = value

    for e, sub in pairs(top):
        if e.get("testclass") == "TestPlan":
            collect(e)
            for c, _ in pairs(sub):
                if c.get("testclass") == "Arguments" and c.get("enabled", "true") != "false":
                    collect(c)
    return out
