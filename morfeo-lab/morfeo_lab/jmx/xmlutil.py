"""Helpers for the XML that JMeter saves: a <hashTree> alternates an element and the <hashTree> with its
children, and properties are typed children (<stringProp>, <intProp>, <boolProp>, <doubleProp>...)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator


def pairs(ht: ET.Element | None) -> Iterator[tuple[ET.Element, ET.Element | None]]:
    """(element, its hashTree) for every child of a <hashTree>."""
    kids = list(ht) if ht is not None else []
    i = 0
    while i < len(kids):
        e = kids[i]
        sub = kids[i + 1] if i + 1 < len(kids) and kids[i + 1].tag == "hashTree" else None
        yield e, sub
        i += 2 if sub is not None else 1


def cls_of(e: ET.Element) -> str:
    return e.get("testclass") or e.tag


def enabled(e: ET.Element) -> bool:
    return e.get("enabled", "true") != "false"


def walk(ht: ET.Element | None, depth: int = 0) -> Iterator[tuple[ET.Element, ET.Element | None, int]]:
    """Every enabled element under a <hashTree>: (element, its hashTree, depth). Disabled elements and
    everything below them are skipped."""
    for e, sub in pairs(ht):
        if not enabled(e):
            continue
        yield e, sub, depth
        yield from walk(sub, depth + 1)


def prop_el(e: ET.Element, name: str) -> ET.Element | None:
    for c in e:
        if c.get("name") == name:
            return c
        if c.tag == "doubleProp" and c.findtext("name") == name:
            return c
    return None


def prop(e: ET.Element, name: str, default: str = "") -> str:
    c = prop_el(e, name)
    if c is None:
        return default
    if c.tag == "doubleProp":
        return (c.findtext("value") or default).strip()
    if c.tag == "elementProp":
        return default
    return (c.text or "").strip()


def set_prop(e: ET.Element, name: str, value) -> None:
    """Set a property, creating it when missing and keeping its type (a number stays numeric)."""
    c = prop_el(e, name)
    if c is None:
        c = ET.SubElement(e, "stringProp", name=name)
    if c.tag == "doubleProp":
        v = c.find("value")
        if v is None:
            v = ET.SubElement(c, "value")
        v.text = str(float(value))
        return
    if c.tag in ("intProp", "longProp"):
        try:
            value = int(float(value))
        except (TypeError, ValueError):
            c.tag = "stringProp"
    if c.tag == "boolProp":
        value = str(value).lower()
    c.text = str(value)


def rows(e: ET.Element, name: str) -> list[list[ET.Element]]:
    """The rows of a table property (a collectionProp of collectionProps of stringProps)."""
    col = prop_el(e, name)
    if col is None:
        return []
    return [list(r) for r in col if r.tag == "collectionProp"]


def remove_with_tree(parent_ht: ET.Element, element: ET.Element) -> None:
    """Remove an element and its <hashTree> from the <hashTree> that holds them."""
    kids = list(parent_ht)
    i = kids.index(element)
    parent_ht.remove(element)
    if i + 1 < len(kids) and kids[i + 1].tag == "hashTree":
        parent_ht.remove(kids[i + 1])
