"""Descriptores y firmas genéricas (JVMS 4.3 y 4.7.9.1) a un árbol de tipos.

``JavaType`` representa un tipo Java con uno de estos ``kind``:

- ``primitive``: ``name`` es ``int``, ``long``…; ``void`` para el retorno vacío.
- ``class``: ``name`` es el nombre binario con puntos (``java.util.List``,
  ``com.x.Outer$Inner``) y ``args`` los argumentos de tipo.
- ``array``: ``args[0]`` es el tipo del elemento.
- ``typevar``: ``name`` es la variable (``T``).
- ``wildcard``: ``name`` es ``*`` (``?``), ``+`` (``? extends``) o ``-``
  (``? super``) y ``args[0]`` la cota, si la hay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

_PRIMITIVES = {
    "B": "byte",
    "C": "char",
    "D": "double",
    "F": "float",
    "I": "int",
    "J": "long",
    "S": "short",
    "Z": "boolean",
    "V": "void",
}


class SignatureError(ValueError):
    """Descriptor o firma mal formados."""


@dataclass(frozen=True)
class JavaType:
    kind: str
    name: str = ""
    args: tuple[JavaType, ...] = ()

    # -- constructores ------------------------------------------------------

    @staticmethod
    def of_class(name: str, *args: JavaType) -> JavaType:
        return JavaType("class", name, tuple(args))

    @staticmethod
    def array_of(component: JavaType) -> JavaType:
        return JavaType("array", "", (component,))

    # -- consultas ----------------------------------------------------------

    @property
    def is_primitive(self) -> bool:
        return self.kind == "primitive"

    @property
    def is_class(self) -> bool:
        return self.kind == "class"

    @property
    def component(self) -> JavaType | None:
        return self.args[0] if self.kind == "array" and self.args else None

    @property
    def bound(self) -> JavaType | None:
        return self.args[0] if self.kind == "wildcard" and self.args else None

    @property
    def internal_name(self) -> str:
        return self.name.replace(".", "/")

    def arg(self, index: int) -> JavaType | None:
        return self.args[index] if self.kind == "class" and index < len(self.args) else None

    def display(self) -> str:
        """Nombre legible: ``java.util.Map<java.lang.String, java.lang.Integer>``, ``int[]``."""
        if self.kind == "class":
            if not self.args:
                return self.name
            return f"{self.name}<{', '.join(a.display() for a in self.args)}>"
        if self.kind == "array":
            return f"{self.args[0].display()}[]"
        if self.kind == "wildcard":
            if self.name == "+":
                return f"? extends {self.args[0].display()}"
            if self.name == "-":
                return f"? super {self.args[0].display()}"
            return "?"
        return self.name

    def erasure(self) -> JavaType:
        if self.kind == "class":
            return JavaType("class", self.name)
        if self.kind == "array":
            return JavaType.array_of(self.args[0].erasure())
        if self.kind in ("typevar", "wildcard"):
            return OBJECT
        return self

    def substitute(self, bindings: Mapping[str, JavaType]) -> JavaType:
        """Sustituye variables de tipo por su valor en ``bindings``."""
        if not bindings:
            return self
        if self.kind == "typevar":
            return bindings.get(self.name, self)
        if self.args:
            return JavaType(self.kind, self.name, tuple(a.substitute(bindings) for a in self.args))
        return self


OBJECT = JavaType.of_class("java.lang.Object")


@dataclass(frozen=True)
class TypeParameter:
    name: str
    bounds: tuple[JavaType, ...] = ()


@dataclass(frozen=True)
class ClassSignature:
    type_parameters: tuple[TypeParameter, ...]
    superclass: JavaType
    interfaces: tuple[JavaType, ...]


@dataclass(frozen=True)
class MethodSignature:
    type_parameters: tuple[TypeParameter, ...]
    parameters: tuple[JavaType, ...]
    return_type: JavaType
    throws: tuple[JavaType, ...] = field(default=())


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def take(self, expected: str | None = None) -> str:
        ch = self.peek()
        if not ch or (expected is not None and ch != expected):
            want = expected if expected is not None else "un carácter"
            raise SignatureError(f"se esperaba {want!r} en la posición {self.pos} de {self.text!r}")
        self.pos += 1
        return ch

    def identifier(self) -> str:
        start = self.pos
        while self.peek() and self.peek() not in ".;[/<>:":
            self.pos += 1
        if start == self.pos:
            raise SignatureError(f"identificador vacío en la posición {start} de {self.text!r}")
        return self.text[start : self.pos]

    def done(self) -> None:
        if self.pos != len(self.text):
            raise SignatureError(f"texto sobrante en la posición {self.pos} de {self.text!r}")

    # -- gramática ----------------------------------------------------------

    def java_type(self) -> JavaType:
        ch = self.peek()
        if ch in _PRIMITIVES:
            self.pos += 1
            return JavaType("primitive", _PRIMITIVES[ch])
        return self.reference_type()

    def reference_type(self) -> JavaType:
        ch = self.peek()
        if ch == "L":
            return self.class_type()
        if ch == "T":
            self.pos += 1
            name = self.identifier()
            self.take(";")
            return JavaType("typevar", name)
        if ch == "[":
            self.pos += 1
            return JavaType.array_of(self.java_type())
        raise SignatureError(f"tipo no válido en la posición {self.pos} de {self.text!r}")

    def class_type(self) -> JavaType:
        self.take("L")
        start = self.pos
        while self.peek() and self.peek() not in "<.;":
            self.pos += 1
        name = self.text[start : self.pos].replace("/", ".")
        if not name:
            raise SignatureError(f"nombre de clase vacío en {self.text!r}")
        args = self.type_arguments() if self.peek() == "<" else ()
        while self.peek() == ".":  # clase interna de una clase genérica
            self.pos += 1
            name = f"{name}${self.identifier()}"
            args = self.type_arguments() if self.peek() == "<" else ()
        self.take(";")
        return JavaType("class", name, args)

    def type_arguments(self) -> tuple[JavaType, ...]:
        self.take("<")
        args = []
        while self.peek() != ">":
            ch = self.peek()
            if ch == "*":
                self.pos += 1
                args.append(JavaType("wildcard", "*"))
            elif ch in "+-":
                self.pos += 1
                args.append(JavaType("wildcard", ch, (self.reference_type(),)))
            else:
                args.append(self.reference_type())
        self.take(">")
        return tuple(args)

    def type_parameters(self) -> tuple[TypeParameter, ...]:
        if self.peek() != "<":
            return ()
        self.take("<")
        params = []
        while self.peek() != ">":
            name = self.identifier()
            bounds = []
            self.take(":")
            if self.peek() not in (":", ">", ""):  # la cota de clase puede faltar
                bounds.append(self.reference_type())
            while self.peek() == ":":
                self.pos += 1
                bounds.append(self.reference_type())
            params.append(TypeParameter(name, tuple(bounds)))
        self.take(">")
        return tuple(params)


def parse_field_descriptor(descriptor: str) -> JavaType:
    """``Ljava/lang/String;`` a ``JavaType``. También acepta firmas de campo con genéricos."""
    p = _Parser(descriptor)
    t = p.java_type()
    p.done()
    return t


def parse_method_descriptor(descriptor: str) -> tuple[tuple[JavaType, ...], JavaType]:
    """``(JLjava/lang/String;)V`` a ``((long, String), void)``."""
    sig = parse_method_signature(descriptor)
    return sig.parameters, sig.return_type


def parse_method_signature(signature: str) -> MethodSignature:
    p = _Parser(signature)
    type_params = p.type_parameters()
    p.take("(")
    params = []
    while p.peek() != ")":
        params.append(p.java_type())
    p.take(")")
    ret = p.java_type()
    throws = []
    while p.peek() == "^":
        p.pos += 1
        throws.append(p.reference_type())
    p.done()
    return MethodSignature(type_params, tuple(params), ret, tuple(throws))


def parse_class_signature(signature: str) -> ClassSignature:
    p = _Parser(signature)
    type_params = p.type_parameters()
    superclass = p.class_type()
    interfaces = []
    while p.peek():
        interfaces.append(p.class_type())
    return ClassSignature(type_params, superclass, tuple(interfaces))


def parameter_slots(params: tuple[JavaType, ...], is_static: bool) -> list[int]:
    """Índice de variable local de cada parámetro (long y double ocupan dos)."""
    slot = 0 if is_static else 1
    slots = []
    for t in params:
        slots.append(slot)
        slot += 2 if t.kind == "primitive" and t.name in ("long", "double") else 1
    return slots
