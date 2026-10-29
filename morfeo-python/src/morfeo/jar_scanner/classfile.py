"""Lector del formato .class (JVMS, capítulo 4) en Python puro.

Se leen solo las partes que usa el escáner: constant pool completo, flags,
jerarquía, campos, métodos y los atributos Signature, ConstantValue,
RuntimeVisibleAnnotations, RuntimeVisibleParameterAnnotations,
AnnotationDefault, MethodParameters, Code (con LocalVariableTable), Record y
BootstrapMethods. El resto de atributos se salta por longitud, así que se
acepta cualquier versión mayor del formato.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Union

# Flags de acceso que usa el escáner (tabla 4.1-B, 4.5-A y 4.6-A de la JVMS)
ACC_STATIC = 0x0008
ACC_BRIDGE = 0x0040
ACC_TRANSIENT = 0x0080
ACC_INTERFACE = 0x0200
ACC_ABSTRACT = 0x0400
ACC_SYNTHETIC = 0x1000
ACC_ENUM = 0x4000

# Tags del constant pool (tabla 4.4-B)
CONSTANT_Utf8 = 1
CONSTANT_Integer = 3
CONSTANT_Float = 4
CONSTANT_Long = 5
CONSTANT_Double = 6
CONSTANT_Class = 7
CONSTANT_String = 8
CONSTANT_Fieldref = 9
CONSTANT_Methodref = 10
CONSTANT_InterfaceMethodref = 11
CONSTANT_NameAndType = 12
CONSTANT_MethodHandle = 15
CONSTANT_MethodType = 16
CONSTANT_Dynamic = 17
CONSTANT_InvokeDynamic = 18
CONSTANT_Module = 19
CONSTANT_Package = 20

_U2 = struct.Struct(">H")
_U4 = struct.Struct(">I")
_I4 = struct.Struct(">i")
_I8 = struct.Struct(">q")
_F4 = struct.Struct(">f")
_F8 = struct.Struct(">d")


class ClassFormatError(ValueError):
    """El fichero no es un .class válido o está truncado."""


def decode_modified_utf8(data: bytes) -> str:
    """Decodifica el UTF-8 modificado de Java (nulo como C0 80, pares suplentes en 6 bytes)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    text = data.replace(b"\xc0\x80", b"\x00").decode("utf-8", "surrogatepass")
    # Une los pares suplentes codificados por separado en un único carácter.
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


# ---------------------------------------------------------------------------
# Valores del constant pool
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassRef:
    """Referencia a una clase como descriptor: ``Ljava/lang/String;``, ``[I`` o ``V``."""

    descriptor: str

    @property
    def internal_name(self) -> str:
        d = self.descriptor
        return d[1:-1] if d.startswith("L") and d.endswith(";") else d


@dataclass(frozen=True)
class MemberRef:
    """Fieldref, Methodref o InterfaceMethodref ya resueltos."""

    owner: str
    name: str
    descriptor: str
    is_interface: bool = False


@dataclass(frozen=True)
class MethodHandleRef:
    kind: int
    ref: MemberRef


@dataclass(frozen=True)
class MethodTypeRef:
    descriptor: str


@dataclass(frozen=True)
class DynamicRef:
    """CONSTANT_Dynamic (tag 17) o CONSTANT_InvokeDynamic (tag 18)."""

    bootstrap_index: int
    name: str
    descriptor: str
    is_invokedynamic: bool = False


Loadable = Union[int, float, str, ClassRef, MethodTypeRef, MethodHandleRef, DynamicRef]


class ConstantPool:
    """Constant pool con índices desde 1. Long y Double ocupan dos entradas."""

    def __init__(self, entries: list[tuple[Any, ...] | None]) -> None:
        self._entries = entries

    def __len__(self) -> int:
        return len(self._entries)

    def _entry(self, index: int, *tags: int) -> tuple[Any, ...]:
        if not 0 < index < len(self._entries):
            raise ClassFormatError(f"índice de constant pool fuera de rango: {index}")
        entry = self._entries[index]
        if entry is None:
            raise ClassFormatError(f"índice de constant pool no utilizable: {index}")
        if tags and entry[0] not in tags:
            raise ClassFormatError(f"la entrada {index} tiene tag {entry[0]}, se esperaba {tags}")
        return entry

    def tag(self, index: int) -> int | None:
        if not 0 < index < len(self._entries):
            return None
        entry = self._entries[index]
        return None if entry is None else int(entry[0])

    def utf8(self, index: int) -> str:
        return str(self._entry(index, CONSTANT_Utf8)[1])

    def class_name(self, index: int) -> str:
        return self.utf8(int(self._entry(index, CONSTANT_Class)[1]))

    def string(self, index: int) -> str:
        return self.utf8(int(self._entry(index, CONSTANT_String)[1]))

    def name_and_type(self, index: int) -> tuple[str, str]:
        _, name_i, desc_i = self._entry(index, CONSTANT_NameAndType)
        return self.utf8(name_i), self.utf8(desc_i)

    def member_ref(self, index: int) -> MemberRef:
        tag, class_i, nat_i = self._entry(index, CONSTANT_Fieldref, CONSTANT_Methodref, CONSTANT_InterfaceMethodref)
        name, desc = self.name_and_type(nat_i)
        return MemberRef(self.class_name(class_i), name, desc, tag == CONSTANT_InterfaceMethodref)

    def method_handle(self, index: int) -> MethodHandleRef:
        _, kind, ref_i = self._entry(index, CONSTANT_MethodHandle)
        return MethodHandleRef(int(kind), self.member_ref(ref_i))

    def dynamic(self, index: int) -> DynamicRef:
        tag, bsm_i, nat_i = self._entry(index, CONSTANT_Dynamic, CONSTANT_InvokeDynamic)
        name, desc = self.name_and_type(nat_i)
        return DynamicRef(int(bsm_i), name, desc, tag == CONSTANT_InvokeDynamic)

    def module_name(self, index: int) -> str:
        return self.utf8(int(self._entry(index, CONSTANT_Module)[1]))

    def package_name(self, index: int) -> str:
        return self.utf8(int(self._entry(index, CONSTANT_Package)[1]))

    def value(self, index: int) -> Loadable:
        """Valor de una constante cargable (ldc, ConstantValue, argumentos de bootstrap)."""
        entry = self._entry(index)
        tag = entry[0]
        if tag in (CONSTANT_Integer, CONSTANT_Float, CONSTANT_Long, CONSTANT_Double):
            return entry[1]
        if tag == CONSTANT_String:
            return self.string(index)
        if tag == CONSTANT_Class:
            name = self.class_name(index)
            return ClassRef(name if name.startswith("[") else f"L{name};")
        if tag == CONSTANT_MethodType:
            return MethodTypeRef(self.utf8(int(entry[1])))
        if tag == CONSTANT_MethodHandle:
            return self.method_handle(index)
        if tag in (CONSTANT_Dynamic, CONSTANT_InvokeDynamic):
            return self.dynamic(index)
        if tag == CONSTANT_Utf8:
            return self.utf8(index)
        raise ClassFormatError(f"la entrada {index} (tag {tag}) no es una constante cargable")


# ---------------------------------------------------------------------------
# Anotaciones
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnumConst:
    """Valor de enum dentro de una anotación (tag ``e``)."""

    type_descriptor: str
    name: str


ElementValue = Union[int, float, bool, str, EnumConst, ClassRef, "Annotation", tuple]


@dataclass(frozen=True)
class Annotation:
    """Anotación con sus pares elemento-valor. Los arrays son tuplas."""

    descriptor: str
    values: dict[str, ElementValue] = field(default_factory=dict)

    @property
    def type_name(self) -> str:
        """Nombre binario con puntos: ``org.springframework.web.bind.annotation.GetMapping``."""
        return descriptor_to_class_name(self.descriptor)

    def get(self, name: str, default: ElementValue | None = None) -> ElementValue | None:
        return self.values.get(name, default)


def descriptor_to_class_name(descriptor: str) -> str:
    if descriptor.startswith("L") and descriptor.endswith(";"):
        return descriptor[1:-1].replace("/", ".")
    return descriptor.replace("/", ".")


# ---------------------------------------------------------------------------
# Atributos y miembros
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalVariable:
    start_pc: int
    length: int
    name: str
    descriptor: str
    index: int


@dataclass(frozen=True)
class ExceptionHandlerEntry:
    start_pc: int
    end_pc: int
    handler_pc: int
    catch_type: str | None


@dataclass(frozen=True)
class CodeAttribute:
    max_stack: int
    max_locals: int
    code: bytes
    exception_table: tuple[ExceptionHandlerEntry, ...]
    local_variables: tuple[LocalVariable, ...]


@dataclass(frozen=True)
class MethodParameter:
    name: str | None
    access_flags: int


@dataclass(frozen=True)
class MemberInfo:
    """Campo o método."""

    access_flags: int
    name: str
    descriptor: str
    signature: str | None = None
    annotations: tuple[Annotation, ...] = ()
    parameter_annotations: tuple[tuple[Annotation, ...], ...] = ()
    parameters: tuple[MethodParameter, ...] = ()
    code: CodeAttribute | None = None
    constant_value: Loadable | None = None
    annotation_default: ElementValue | None = None

    def has_flag(self, flag: int) -> bool:
        return bool(self.access_flags & flag)


@dataclass(frozen=True)
class RecordComponent:
    name: str
    descriptor: str
    signature: str | None = None
    annotations: tuple[Annotation, ...] = ()


@dataclass(frozen=True)
class BootstrapMethod:
    method: MethodHandleRef
    arguments: tuple[Loadable, ...]


@dataclass(frozen=True)
class ClassFile:
    minor_version: int
    major_version: int
    constant_pool: ConstantPool
    access_flags: int
    this_class: str
    super_class: str | None
    interfaces: tuple[str, ...]
    fields: tuple[MemberInfo, ...]
    methods: tuple[MemberInfo, ...]
    signature: str | None = None
    annotations: tuple[Annotation, ...] = ()
    record_components: tuple[RecordComponent, ...] | None = None
    bootstrap_methods: tuple[BootstrapMethod, ...] = ()

    @property
    def name(self) -> str:
        """Nombre binario con puntos (``com.example.Foo$Bar``)."""
        return self.this_class.replace("/", ".")

    def has_flag(self, flag: int) -> bool:
        return bool(self.access_flags & flag)

    def find_method(self, name: str, descriptor: str) -> MemberInfo | None:
        return next((m for m in self.methods if m.name == name and m.descriptor == descriptor), None)


# ---------------------------------------------------------------------------
# Lector
# ---------------------------------------------------------------------------


def read_major_version(data: bytes) -> int | None:
    """Versión mayor leyendo solo la cabecera, o None si no es un .class."""
    if len(data) < 8 or data[:4] != b"\xca\xfe\xba\xbe":
        return None
    return int(_U2.unpack_from(data, 6)[0])


def parse_class(data: bytes) -> ClassFile:
    """Parsea los bytes de un .class."""
    try:
        return _Reader(data).read_class()
    except (struct.error, IndexError) as exc:
        raise ClassFormatError(f"fichero .class truncado: {exc}") from exc


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.cp = ConstantPool([None])

    def u1(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u2(self) -> int:
        v = _U2.unpack_from(self.data, self.pos)[0]
        self.pos += 2
        return int(v)

    def u4(self) -> int:
        v = _U4.unpack_from(self.data, self.pos)[0]
        self.pos += 4
        return int(v)

    def read_class(self) -> ClassFile:
        if self.data[:4] != b"\xca\xfe\xba\xbe":
            raise ClassFormatError("número mágico incorrecto, no es un fichero .class")
        self.pos = 4
        minor = self.u2()
        major = self.u2()
        self.cp = self.read_constant_pool()
        access = self.u2()
        this_class = self.cp.class_name(self.u2())
        super_index = self.u2()
        super_class = self.cp.class_name(super_index) if super_index else None
        interfaces = tuple(self.cp.class_name(self.u2()) for _ in range(self.u2()))
        fields = tuple(self.read_member() for _ in range(self.u2()))
        methods = tuple(self.read_member() for _ in range(self.u2()))
        attrs = self.read_attributes()
        return ClassFile(
            minor_version=minor,
            major_version=major,
            constant_pool=self.cp,
            access_flags=access,
            this_class=this_class,
            super_class=super_class,
            interfaces=interfaces,
            fields=fields,
            methods=methods,
            signature=attrs.get("Signature"),
            annotations=attrs.get("RuntimeVisibleAnnotations", ()),
            record_components=attrs.get("Record"),
            bootstrap_methods=attrs.get("BootstrapMethods", ()),
        )

    def read_constant_pool(self) -> ConstantPool:
        count = self.u2()
        entries: list[tuple[Any, ...] | None] = [None] * count
        data = self.data
        i = 1
        while i < count:
            tag = self.u1()
            if tag == CONSTANT_Utf8:
                length = self.u2()
                entries[i] = (tag, decode_modified_utf8(data[self.pos : self.pos + length]))
                self.pos += length
            elif tag == CONSTANT_Integer:
                entries[i] = (tag, _I4.unpack_from(data, self.pos)[0])
                self.pos += 4
            elif tag == CONSTANT_Float:
                entries[i] = (tag, _F4.unpack_from(data, self.pos)[0])
                self.pos += 4
            elif tag in (CONSTANT_Long, CONSTANT_Double):
                fmt = _I8 if tag == CONSTANT_Long else _F8
                entries[i] = (tag, fmt.unpack_from(data, self.pos)[0])
                self.pos += 8
                i += 1  # la entrada siguiente queda inutilizable (JVMS 4.4.5)
            elif tag in (CONSTANT_Class, CONSTANT_String, CONSTANT_MethodType, CONSTANT_Module, CONSTANT_Package):
                entries[i] = (tag, self.u2())
            elif tag in (
                CONSTANT_Fieldref,
                CONSTANT_Methodref,
                CONSTANT_InterfaceMethodref,
                CONSTANT_NameAndType,
                CONSTANT_Dynamic,
                CONSTANT_InvokeDynamic,
            ):
                entries[i] = (tag, self.u2(), self.u2())
            elif tag == CONSTANT_MethodHandle:
                entries[i] = (tag, self.u1(), self.u2())
            else:
                raise ClassFormatError(f"tag de constant pool desconocido {tag} en la entrada {i}")
            i += 1
        return ConstantPool(entries)

    def read_member(self) -> MemberInfo:
        access = self.u2()
        name = self.cp.utf8(self.u2())
        descriptor = self.cp.utf8(self.u2())
        attrs = self.read_attributes()
        return MemberInfo(
            access_flags=access,
            name=name,
            descriptor=descriptor,
            signature=attrs.get("Signature"),
            annotations=attrs.get("RuntimeVisibleAnnotations", ()),
            parameter_annotations=attrs.get("RuntimeVisibleParameterAnnotations", ()),
            parameters=attrs.get("MethodParameters", ()),
            code=attrs.get("Code"),
            constant_value=attrs.get("ConstantValue"),
            annotation_default=attrs.get("AnnotationDefault"),
        )

    def read_attributes(self) -> dict[str, object]:
        """Lee una tabla de atributos y devuelve solo los conocidos, ya interpretados."""
        result: dict[str, object] = {}
        for _ in range(self.u2()):
            name = self.cp.utf8(self.u2())
            length = self.u4()
            end = self.pos + length
            reader = _ATTRIBUTE_READERS.get(name)
            if reader is not None:
                result[name] = reader(self)
            self.pos = end
        return result

    # -- atributos concretos ------------------------------------------------

    def attr_signature(self) -> str:
        return self.cp.utf8(self.u2())

    def attr_constant_value(self) -> Loadable:
        return self.cp.value(self.u2())

    def attr_annotations(self) -> tuple[Annotation, ...]:
        return tuple(self.read_annotation() for _ in range(self.u2()))

    def attr_parameter_annotations(self) -> tuple[tuple[Annotation, ...], ...]:
        return tuple(self.attr_annotations() for _ in range(self.u1()))

    def attr_annotation_default(self) -> ElementValue:
        return self.read_element_value()

    def attr_method_parameters(self) -> tuple[MethodParameter, ...]:
        params = []
        for _ in range(self.u1()):
            name_index = self.u2()
            flags = self.u2()
            params.append(MethodParameter(self.cp.utf8(name_index) if name_index else None, flags))
        return tuple(params)

    def attr_code(self) -> CodeAttribute:
        max_stack = self.u2()
        max_locals = self.u2()
        length = self.u4()
        code = self.data[self.pos : self.pos + length]
        self.pos += length
        table = []
        for _ in range(self.u2()):
            start, end, handler, catch = self.u2(), self.u2(), self.u2(), self.u2()
            table.append(ExceptionHandlerEntry(start, end, handler, self.cp.class_name(catch) if catch else None))
        nested = self.read_attributes()
        return CodeAttribute(
            max_stack=max_stack,
            max_locals=max_locals,
            code=code,
            exception_table=tuple(table),
            local_variables=nested.get("LocalVariableTable", ()),
        )

    def attr_local_variable_table(self) -> tuple[LocalVariable, ...]:
        rows = []
        for _ in range(self.u2()):
            start, length, name_i, desc_i, index = self.u2(), self.u2(), self.u2(), self.u2(), self.u2()
            rows.append(LocalVariable(start, length, self.cp.utf8(name_i), self.cp.utf8(desc_i), index))
        return tuple(rows)

    def attr_record(self) -> tuple[RecordComponent, ...]:
        components = []
        for _ in range(self.u2()):
            name = self.cp.utf8(self.u2())
            descriptor = self.cp.utf8(self.u2())
            attrs = self.read_attributes()
            components.append(
                RecordComponent(
                    name=name,
                    descriptor=descriptor,
                    signature=attrs.get("Signature"),
                    annotations=attrs.get("RuntimeVisibleAnnotations", ()),
                )
            )
        return tuple(components)

    def attr_bootstrap_methods(self) -> tuple[BootstrapMethod, ...]:
        methods = []
        for _ in range(self.u2()):
            handle = self.cp.method_handle(self.u2())
            args = tuple(self.cp.value(self.u2()) for _ in range(self.u2()))
            methods.append(BootstrapMethod(handle, args))
        return tuple(methods)

    # -- anotaciones ----------------------------------------------------------

    def read_annotation(self) -> Annotation:
        descriptor = self.cp.utf8(self.u2())
        values: dict[str, ElementValue] = {}
        for _ in range(self.u2()):
            name = self.cp.utf8(self.u2())
            values[name] = self.read_element_value()
        return Annotation(descriptor, values)

    def read_element_value(self) -> ElementValue:
        tag = chr(self.u1())
        if tag in "BIJS":
            return int(self.cp.value(self.u2()))
        if tag == "C":
            return chr(int(self.cp.value(self.u2())))
        if tag == "Z":
            return bool(self.cp.value(self.u2()))
        if tag in "DF":
            return float(self.cp.value(self.u2()))
        if tag == "s":
            return self.cp.utf8(self.u2())
        if tag == "e":
            type_desc = self.cp.utf8(self.u2())
            return EnumConst(type_desc, self.cp.utf8(self.u2()))
        if tag == "c":
            return ClassRef(self.cp.utf8(self.u2()))
        if tag == "@":
            return self.read_annotation()
        if tag == "[":
            return tuple(self.read_element_value() for _ in range(self.u2()))
        raise ClassFormatError(f"tag de element_value desconocido: {tag!r}")


_ATTRIBUTE_READERS = {
    "Signature": _Reader.attr_signature,
    "ConstantValue": _Reader.attr_constant_value,
    "RuntimeVisibleAnnotations": _Reader.attr_annotations,
    "RuntimeVisibleParameterAnnotations": _Reader.attr_parameter_annotations,
    "AnnotationDefault": _Reader.attr_annotation_default,
    "MethodParameters": _Reader.attr_method_parameters,
    "Code": _Reader.attr_code,
    "LocalVariableTable": _Reader.attr_local_variable_table,
    "Record": _Reader.attr_record,
    "BootstrapMethods": _Reader.attr_bootstrap_methods,
}
