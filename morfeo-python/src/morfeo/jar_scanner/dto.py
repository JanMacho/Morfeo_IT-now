"""Tipos Java a TypeSchema (docs/contracts.md, sección 1) y ejemplo de payload.

Para las clases del jar se leen los campos de instancia (incluidos los de las
superclases) o los componentes de un record, con los renombrados y exclusiones
de Jackson y las restricciones de Bean Validation (jakarta y javax).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from morfeo.jar_scanner.classfile import (
    ACC_ENUM,
    ACC_STATIC,
    ACC_SYNTHETIC,
    ACC_TRANSIENT,
    Annotation,
    ClassFile,
    ClassFormatError,
)
from morfeo.jar_scanner.descriptors import (
    OBJECT,
    JavaType,
    SignatureError,
    parse_class_signature,
    parse_field_descriptor,
)

MAX_DEPTH = 4

JSON_PROPERTY = "com.fasterxml.jackson.annotation.JsonProperty"
JSON_IGNORE = "com.fasterxml.jackson.annotation.JsonIgnore"
JSON_IGNORE_PROPERTIES = "com.fasterxml.jackson.annotation.JsonIgnoreProperties"

_PRIMITIVES: dict[str, dict[str, Any]] = {
    "int": {"type": "integer", "format": "int32"},
    "short": {"type": "integer", "format": "int32"},
    "byte": {"type": "integer", "format": "int32"},
    "long": {"type": "integer", "format": "int64"},
    "float": {"type": "number", "format": "float"},
    "double": {"type": "number", "format": "double"},
    "boolean": {"type": "boolean"},
    "char": {"type": "string", "format": "char"},
}

_BOXED = {
    "java.lang.Integer": "int",
    "java.lang.Short": "short",
    "java.lang.Byte": "byte",
    "java.lang.Long": "long",
    "java.lang.Float": "float",
    "java.lang.Double": "double",
    "java.lang.Boolean": "boolean",
    "java.lang.Character": "char",
}

_KNOWN: dict[str, dict[str, Any]] = {
    "java.lang.String": {"type": "string"},
    "java.lang.CharSequence": {"type": "string"},
    "java.util.UUID": {"type": "string", "format": "uuid"},
    "java.math.BigDecimal": {"type": "number", "format": "decimal"},
    "java.math.BigInteger": {"type": "integer", "format": "int64"},
    "java.lang.Number": {"type": "number"},
    "java.time.LocalDate": {"type": "string", "format": "date"},
    "java.time.LocalDateTime": {"type": "string", "format": "date-time"},
    "java.time.Instant": {"type": "string", "format": "date-time"},
    "java.time.OffsetDateTime": {"type": "string", "format": "date-time"},
    "java.time.ZonedDateTime": {"type": "string", "format": "date-time"},
    "java.util.Date": {"type": "string", "format": "date-time"},
    "java.sql.Timestamp": {"type": "string", "format": "date-time"},
    "java.sql.Date": {"type": "string", "format": "date"},
    "java.time.LocalTime": {"type": "string", "format": "time"},
    "java.time.OffsetTime": {"type": "string", "format": "time"},
    "java.time.Duration": {"type": "string"},
    "java.net.URI": {"type": "string"},
    "java.net.URL": {"type": "string"},
    "java.util.Locale": {"type": "string"},
    "java.util.Currency": {"type": "string"},
    "org.springframework.web.multipart.MultipartFile": {"type": "string", "format": "binary"},
    "jakarta.servlet.http.Part": {"type": "string", "format": "binary"},
    "javax.servlet.http.Part": {"type": "string", "format": "binary"},
    "java.lang.Object": {"type": "any"},
    "com.fasterxml.jackson.databind.JsonNode": {"type": "any"},
}

_COLLECTIONS = {
    "java.lang.Iterable",
    "java.util.Collection",
    "java.util.List",
    "java.util.ArrayList",
    "java.util.LinkedList",
    "java.util.Set",
    "java.util.HashSet",
    "java.util.LinkedHashSet",
    "java.util.SortedSet",
    "java.util.TreeSet",
    "java.util.Queue",
    "java.util.Deque",
    "java.util.stream.Stream",
}

_MAPS = {
    "java.util.Map",
    "java.util.HashMap",
    "java.util.LinkedHashMap",
    "java.util.SortedMap",
    "java.util.TreeMap",
    "java.util.concurrent.ConcurrentMap",
    "java.util.concurrent.ConcurrentHashMap",
}

_OPTIONALS = {"java.util.Optional"}

_VALIDATION_PACKAGES = ("jakarta.validation.constraints.", "javax.validation.constraints.")
# Anotaciones de Bean Validation que solo fijan una restricción con un valor constante.
_FIXED_CONSTRAINTS: dict[str, tuple[str, Any]] = {
    "Email": ("email", True),
    "Positive": ("positive", True),
    "PositiveOrZero": ("min", 0),
    "Negative": ("negative", True),
    "NegativeOrZero": ("max", 0),
    "Past": ("past", True),
    "PastOrPresent": ("past", True),
    "Future": ("future", True),
    "FutureOrPresent": ("future", True),
}

# Integer.MAX_VALUE: valor por defecto de @Size(max)
_INT_MAX = 2**31 - 1

ClassLoader = Callable[[str], ClassFile | None]


def type_bindings(cls: ClassFile, t: JavaType) -> dict[str, JavaType]:
    """Variables de tipo de ``cls`` ligadas a los argumentos de ``t`` (``Page<T>`` con ``Page<Owner>``)."""
    if not cls.signature or not t.args:
        return {}
    try:
        params = parse_class_signature(cls.signature).type_parameters
    except SignatureError:
        return {}
    return {p.name: a for p, a in zip(params, t.args)}


def is_simple(t: JavaType, load: ClassLoader) -> bool:
    """Tipo que Spring convierte desde un único valor de texto (parámetros de query, path…)."""
    if t.kind == "primitive":
        return t.name != "void"
    if t.kind == "array":
        return t.component is not None and is_simple(t.component, load)
    if t.kind != "class":
        return False
    name = t.name
    if name in _OPTIONALS or name in _COLLECTIONS:
        inner = t.arg(0)
        return inner is None or is_simple(_unwrap_wildcard(inner), load)
    if name in _BOXED or (name in _KNOWN and _KNOWN[name]["type"] not in ("any",)):
        return True
    return is_enum(name, load)


def is_enum(name: str, load: ClassLoader) -> bool:
    cls = load(name.replace(".", "/"))
    return cls is not None and cls.has_flag(ACC_ENUM)


def _unwrap_wildcard(t: JavaType) -> JavaType:
    if t.kind == "wildcard":
        return t.bound or OBJECT
    return t


def annotation_value(ann: Annotation, *names: str) -> Any:
    """Primer atributo presente entre ``names`` (``value`` y ``name`` son alias en Spring)."""
    for n in names:
        v = ann.get(n)
        if v is not None:
            return v
    return None


def as_strings(value: Any) -> list[str]:
    """Valor de anotación a lista de textos (los arrays llegan como tuplas)."""
    if value is None:
        return []
    if isinstance(value, tuple):
        return [str(v) for v in value]
    return [str(value)]


def validation_constraints(annotations: tuple[Annotation, ...]) -> tuple[dict[str, Any], bool]:
    """Restricciones de Bean Validation y si el valor es obligatorio (``@NotNull``…)."""
    constraints: dict[str, Any] = {}
    required = False
    for ann in annotations:
        name = ann.type_name
        pkg = next((p for p in _VALIDATION_PACKAGES if name.startswith(p)), None)
        if pkg is None:
            continue
        simple = name[len(pkg) :]
        if simple == "NotNull":
            required = True
        elif simple in ("NotBlank", "NotEmpty"):
            required = True
            constraints["min_length"] = max(1, int(constraints.get("min_length", 1)))
        elif simple == "Size":
            lo, hi = ann.get("min", 0), ann.get("max", _INT_MAX)
            if isinstance(lo, int) and lo > 0:
                constraints["min_length"] = lo
            if isinstance(hi, int) and hi != _INT_MAX:
                constraints["max_length"] = hi
        elif simple in ("Min", "Max"):
            v = ann.get("value")
            if isinstance(v, int):
                constraints[simple.lower()] = v
        elif simple in ("DecimalMin", "DecimalMax"):
            v = ann.get("value")
            try:
                constraints["min" if simple == "DecimalMin" else "max"] = float(str(v))
            except ValueError:
                pass
        elif simple == "Pattern":
            v = ann.get("regexp")
            if isinstance(v, str):
                constraints["pattern"] = v
        elif simple in _FIXED_CONSTRAINTS:
            key, value = _FIXED_CONSTRAINTS[simple]
            constraints[key] = value
        elif simple == "Digits":
            if isinstance(ann.get("integer"), int):
                constraints["digits_integer"] = ann.get("integer")
            if isinstance(ann.get("fraction"), int):
                constraints["digits_fraction"] = ann.get("fraction")
    return constraints, required


def _find(annotations: tuple[Annotation, ...], type_name: str) -> Annotation | None:
    return next((a for a in annotations if a.type_name == type_name), None)


def _json_name(annotations: tuple[Annotation, ...]) -> str | None:
    ann = _find(annotations, JSON_PROPERTY)
    if ann is None:
        return None
    value = ann.get("value")
    return value if isinstance(value, str) and value else None


def _is_ignored(annotations: tuple[Annotation, ...]) -> bool:
    ann = _find(annotations, JSON_IGNORE)
    return ann is not None and ann.get("value", True) is not False


class SchemaBuilder:
    """Construye TypeSchema a partir de ``JavaType`` resolviendo las clases con ``load``."""

    def __init__(self, load: ClassLoader) -> None:
        self._load = load

    def load(self, internal_name: str) -> ClassFile | None:
        return self._load(internal_name)

    # -- entrada pública ------------------------------------------------------

    def schema(self, t: JavaType, annotations: tuple[Annotation, ...] = ()) -> dict[str, Any]:
        """TypeSchema de ``t``; las anotaciones de validación del sitio de uso se añaden."""
        s = self._schema(t, (), 0)
        constraints, _ = validation_constraints(annotations)
        if constraints:
            s = dict(s)
            s["constraints"] = {**s.get("constraints", {}), **constraints}
        return s

    # -- internos -----------------------------------------------------------

    def _schema(self, t: JavaType, stack: tuple[str, ...], depth: int) -> dict[str, Any]:
        t = _unwrap_wildcard(t)
        if t.kind == "primitive":
            return {**_PRIMITIVES.get(t.name, {"type": "any"}), "java_type": t.name}
        if t.kind == "typevar":
            return {"type": "any", "java_type": t.name}
        if t.kind == "array":
            comp = t.component or OBJECT
            if comp.kind == "primitive" and comp.name == "byte":
                return {"type": "string", "format": "binary", "java_type": "byte[]"}
            return {"type": "array", "java_type": t.display(), "items": self._schema(comp, stack, depth)}
        name = t.name
        if name in _BOXED:
            return {**_PRIMITIVES[_BOXED[name]], "java_type": name, "nullable": True}
        if name in _KNOWN:
            return {**_KNOWN[name], "java_type": name}
        if name in _OPTIONALS:
            inner = self._schema(t.arg(0) or OBJECT, stack, depth)
            return {**inner, "nullable": True}
        if name in _COLLECTIONS:
            return {"type": "array", "java_type": t.display(), "items": self._schema(t.arg(0) or OBJECT, stack, depth)}
        if name in _MAPS:
            value = t.arg(1) or OBJECT
            return {
                "type": "object",
                "java_type": t.display(),
                "additional_properties": self._schema(value, stack, depth),
            }
        return self._class_schema(t, stack, depth)

    def _class_schema(self, t: JavaType, stack: tuple[str, ...], depth: int) -> dict[str, Any]:
        base: dict[str, Any] = {"type": "object", "java_type": t.display()}
        if t.name in stack:
            return {**base, "cycle": True}
        cls = self._load(t.internal_name)
        if cls is None:
            return {**base, "unresolved": True}
        if cls.has_flag(ACC_ENUM):
            return {"type": "string", "java_type": t.name, "enum": self._enum_values(cls)}
        if depth >= MAX_DEPTH:
            return {**base, "truncated": True}
        props, required = self._properties(cls, t, stack + (t.name,), depth + 1)
        out = {**base, "properties": props}
        if required:
            out["required"] = required
        return out

    def _enum_values(self, cls: ClassFile) -> list[str]:
        values = []
        for f in cls.fields:
            if f.has_flag(ACC_ENUM):
                values.append(_json_name(f.annotations) or f.name)
        return values

    def _hierarchy(self, cls: ClassFile, t: JavaType) -> list[tuple[ClassFile, dict[str, JavaType]]]:
        """La clase y sus superclases del jar, de la más general a la más concreta."""
        chain: list[tuple[ClassFile, dict[str, JavaType]]] = []
        current: ClassFile | None = cls
        current_type = t
        seen: set[str] = set()
        while current is not None and current.this_class not in seen:
            seen.add(current.this_class)
            bindings = type_bindings(current, current_type)
            chain.append((current, bindings))
            if not current.super_class or current.super_class == "java/lang/Object":
                break
            super_type = JavaType.of_class(current.super_class.replace("/", "."))
            if current.signature:
                try:
                    super_type = parse_class_signature(current.signature).superclass.substitute(bindings)
                except SignatureError:
                    pass
            current = self._load(current.super_class)
            current_type = super_type
        chain.reverse()
        return chain

    def _properties(
        self, cls: ClassFile, t: JavaType, stack: tuple[str, ...], depth: int
    ) -> tuple[dict[str, Any], list[str]]:
        props: dict[str, Any] = {}
        required: list[str] = []
        ignored: set[str] = set()
        chain = self._hierarchy(cls, t)
        for c, _ in chain:
            ann = _find(c.annotations, JSON_IGNORE_PROPERTIES)
            if ann is not None:
                ignored.update(as_strings(annotation_value(ann, "value")))
        for c, bindings in chain:
            for name, jtype, member_annotations in self._members(c, bindings):
                getter = self._getter_annotations(c, name)
                if _is_ignored(member_annotations) or _is_ignored(getter):
                    continue
                json_name = _json_name(member_annotations) or _json_name(getter) or name
                if name in ignored or json_name in ignored:
                    continue
                schema = self._schema(jtype, stack, depth)
                constraints, is_required = validation_constraints(member_annotations)
                if constraints:
                    schema = {**schema, "constraints": {**schema.get("constraints", {}), **constraints}}
                props[json_name] = schema
                if is_required and json_name not in required:
                    required.append(json_name)
        return props, required

    def _members(
        self, cls: ClassFile, bindings: Mapping[str, JavaType]
    ) -> list[tuple[str, JavaType, tuple[Annotation, ...]]]:
        out = []
        if cls.record_components is not None:
            fields = {f.name: f for f in cls.fields}
            for comp in cls.record_components:
                jtype = self._field_type(comp.signature or comp.descriptor, bindings)
                field_ann = fields[comp.name].annotations if comp.name in fields else ()
                out.append((comp.name, jtype, comp.annotations + field_ann))
            return out
        for f in cls.fields:
            if f.access_flags & (ACC_STATIC | ACC_TRANSIENT | ACC_SYNTHETIC):
                continue
            out.append((f.name, self._field_type(f.signature or f.descriptor, bindings), f.annotations))
        return out

    @staticmethod
    def _field_type(signature: str, bindings: Mapping[str, JavaType]) -> JavaType:
        try:
            return parse_field_descriptor(signature).substitute(bindings)
        except (SignatureError, ClassFormatError):
            return OBJECT

    @staticmethod
    def _getter_annotations(cls: ClassFile, field_name: str) -> tuple[Annotation, ...]:
        cap = field_name[:1].upper() + field_name[1:]
        for m in cls.methods:
            if m.name in (f"get{cap}", f"is{cap}") and m.descriptor.startswith("()"):
                return m.annotations
        return ()


# ---------------------------------------------------------------------------
# Ejemplo determinista a partir de un TypeSchema
# ---------------------------------------------------------------------------

_SIMPLE_PATTERN = re.compile(r"^\^?(\\d|\[0-9\]|\[A-Z\]|\[a-z\])\{(\d+)\}\$?$")
_PATTERN_FILL = {"\\d": "0", "[0-9]": "0", "[A-Z]": "A", "[a-z]": "a"}


def example_value(schema: Mapping[str, Any]) -> Any:
    """Valor JSON de ejemplo que respeta enum, formato y restricciones simples."""
    if schema.get("cycle") or schema.get("truncated") or schema.get("unresolved"):
        return {}
    c = schema.get("constraints", {})
    kind = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0] if schema["enum"] else "string"
    if kind == "string":
        return _example_string(schema.get("format"), c)
    if kind in ("integer", "number"):
        return _example_number(kind, c)
    if kind == "boolean":
        return True
    if kind == "array":
        return [example_value(schema.get("items", {"type": "any"}))]
    if kind == "object":
        if "additional_properties" in schema:
            return {"key": example_value(schema["additional_properties"])}
        return {k: example_value(v) for k, v in schema.get("properties", {}).items()}
    return {}


def _example_string(fmt: str | None, c: Mapping[str, Any]) -> str:
    past, future = c.get("past"), c.get("future")
    if fmt == "date":
        return "2000-01-01" if past else "2030-01-01" if future else "2026-01-01"
    if fmt == "date-time":
        return "2000-01-01T00:00:00Z" if past else "2030-01-01T00:00:00Z" if future else "2026-01-01T00:00:00Z"
    if fmt == "time":
        return "00:00:00"
    if fmt == "uuid":
        return "00000000-0000-0000-0000-000000000000"
    if fmt == "char":
        return "a"
    if fmt == "binary":
        return "file"
    if c.get("email"):
        return "user@example.com"
    pattern = c.get("pattern")
    if isinstance(pattern, str):
        m = _SIMPLE_PATTERN.match(pattern)
        if m:
            return _PATTERN_FILL[m.group(1)] * int(m.group(2))
    if isinstance(c.get("digits_integer"), int):
        return "0" * c["digits_integer"]
    text = "string"
    lo, hi = c.get("min_length"), c.get("max_length")
    if isinstance(lo, int) and len(text) < lo:
        text = text + "x" * (lo - len(text))
    if isinstance(hi, int):
        text = text[:hi]
    return text


def _example_number(kind: str, c: Mapping[str, Any]) -> int | float:
    value: float
    if "min" in c:
        value = float(c["min"])
    elif c.get("negative"):
        value = -1.0
    elif "max" in c and float(c["max"]) < 1:
        value = float(c["max"])
    else:
        value = 1.0
    if c.get("positive") and value <= 0:
        value = 1.0
    return int(value) if kind == "integer" else float(value)
