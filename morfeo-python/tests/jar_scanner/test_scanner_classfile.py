"""Parser del formato .class, iterador de bytecode y comparación con javap."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from morfeo.jar_scanner.bytecode import iter_instructions
from morfeo.jar_scanner.classfile import ClassFile, ClassFormatError, ClassRef, EnumConst, parse_class
from morfeo.jar_scanner.descriptors import parse_method_signature
from morfeo.jar_scanner.dto import SchemaBuilder

pytestmark = pytest.mark.javac

_QUALIFIED = re.compile(r"^[A-Za-z_$][\w$]*(\.[\w$]+)+\(?$")
_SECTION = re.compile(r"^[A-Za-z]+:$")
_DECLARATION_SECTIONS = {"RuntimeVisibleAnnotations:", "RuntimeVisibleParameterAnnotations:"}


def javap_annotations(paths: list[Path]) -> dict[str, Counter[str]]:
    """Tipos de anotación de las secciones de declaración de ``javap -v``, por fichero.

    Una sola llamada a javap para todas las clases. Se ignoran
    RuntimeVisibleTypeAnnotations: javac duplica ahí las anotaciones con
    TYPE_USE (como @NotNull) y el escáner no las necesita.
    """
    cmd = ["javap", "-v", "-p", *map(str, paths)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    result: dict[str, Counter[str]] = {}
    current: Counter[str] = Counter()
    section = ""
    for line in out.splitlines():
        if line.startswith("Classfile "):
            current = result.setdefault(Path(line.split(" ", 1)[1]).name, Counter())
            section = ""
            continue
        s = line.strip()
        if _SECTION.match(s):
            section = s
        elif section in _DECLARATION_SECTIONS and _QUALIFIED.match(s):
            current[s.rstrip("(")] += 1
    return result


def load(classes: Path, internal: str) -> ClassFile:
    return parse_class((classes / f"{internal}.class").read_bytes())


def test_annotation_element_values(jars: Any) -> None:
    cls = load(jars.classfile_classes, "cf/Annotated")
    ann = next(a for a in cls.annotations if a.type_name == "cf.Everything")
    v = ann.values
    assert (v["b"], v["c"], v["d"], v["f"], v["i"], v["j"], v["s"], v["z"]) == (
        -8, "Z", 2.5, 1.5, 70000, 1234567890123, -300, True)
    assert v["str"] == "a\u0000b\U0001F600"  # UTF-8 modificado con nulo y par sustituto
    assert v["e"] == EnumConst("Lcf/Color;", "GREEN")
    assert v["cls"] == ClassRef("[Ljava/lang/String;")
    assert v["nested"].type_name == "cf.Nested" and v["nested"].values["sizes"] == (1, 2)
    assert [c.name for c in v["colors"]] == ["RED", "BLUE"]


def test_constants_and_signatures(jars: Any) -> None:
    cls = load(jars.classfile_classes, "cf/Annotated")
    fields = {f.name: f for f in cls.fields}
    assert fields["BIG"].constant_value == 9876543210123
    assert fields["RATIO"].constant_value == 3.25
    assert fields["AFTER"].constant_value == "after-long"  # índice después de las dobles entradas
    assert cls.signature and cls.signature.startswith("<T::Ljava/lang/Comparable<TT;>;>")
    generic = next(m for m in cls.methods if m.name == "generic")
    sig = parse_method_signature(generic.signature or "")
    assert sig.parameters[1].display() == "java.util.List<? super T>"
    mix = next(m for m in cls.methods if m.name == "mix")
    assert [a.type_name for a in mix.parameter_annotations[2]] == ["cf.Nested"]
    assert [lv.name for lv in mix.code.local_variables if lv.index in (1, 3, 5)] == ["a", "b", "c"]


def test_bytecode_switches_and_wide(jars: Any) -> None:
    cls = load(jars.classfile_classes, "cf/Annotated")
    names: set[str] = set()
    for m in cls.methods:
        if m.code is None:
            continue
        ins = list(iter_instructions(m.code.code))
        assert ins[-1].offset + ins[-1].length == len(m.code.code)
        names.update(i.name for i in ins)
        if m.name == "wide":
            assert any(i.wide for i in ins)
    assert {"tableswitch", "lookupswitch", "invokedynamic", "ldc2_w"} <= names


def test_record_and_enum(jars: Any) -> None:
    point = load(jars.classfile_classes, "cf/Point")
    assert point.record_components is not None and len(point.record_components) >= 2
    assert load(jars.classfile_classes, "cf/Color").has_flag(0x4000)


def test_truncated_class_raises() -> None:
    with pytest.raises(ClassFormatError):
        parse_class(b"\xca\xfe\xba\xbe\x00\x00\x00\x41\x00\x10")
    with pytest.raises(ClassFormatError):
        parse_class(b"no es una clase")


def test_dto_depth_limit(jars: Any) -> None:
    root = jars.spring_classes

    def loader(internal: str) -> ClassFile | None:
        p = root / f"{internal}.class"
        return parse_class(p.read_bytes()) if p.exists() else None

    from morfeo.jar_scanner.descriptors import JavaType

    s = SchemaBuilder(loader).schema(JavaType.of_class("com.example.bank.dto.Deep"))
    l1 = s["properties"]["first"]
    assert l1["properties"]["since"]["constraints"] == {"past": True}
    l2 = l1["properties"]["next"]
    assert l2["properties"]["until"]["constraints"] == {"future": True}
    assert l2["properties"]["next"]["properties"]["next"]["truncated"] is True


@pytest.mark.skipif(shutil.which("javap") is None, reason="se necesita javap")
def test_annotations_match_javap(jars: Any) -> None:
    """Para cada clase de la app de ejemplo, mismas anotaciones visibles que javap -v."""
    files = [
        p
        for p in sorted(jars.spring_classes.rglob("*.class")) + sorted(jars.classfile_classes.rglob("*.class"))
        if p.name != "module-info.class"
    ]
    assert len(files) > 30 and len({p.name for p in files}) == len(files)
    theirs = javap_annotations(files)
    for path in files:
        cls = parse_class(path.read_bytes())
        ours: Counter[str] = Counter(a.type_name for a in cls.annotations)
        for member in cls.fields + cls.methods:
            ours.update(a.type_name for a in member.annotations)
        for m in cls.methods:
            for group in m.parameter_annotations:
                ours.update(a.type_name for a in group)
        for comp in cls.record_components or ():
            ours.update(a.type_name for a in comp.annotations)
        assert theirs.get(path.name, Counter()) == ours, path.name
