"""Detector heurístico de rutas Javalin (5 y 6).

Javalin registra las rutas con llamadas, no con anotaciones:
``app.get("/api/saldo/{id}", ctx -> …)``. En el bytecode eso es un ``ldc`` con la
ruta, un ``invokedynamic`` que crea el handler y una llamada a
``io/javalin/…get(String, Handler)``. El detector recorre el código de las
clases de la aplicación buscando ese patrón, sin simular la pila:

- La ruta es el último texto cargado con ``ldc`` antes de la llamada.
- El handler es el último lambda o method reference (``invokedynamic`` con
  LambdaMetafactory) o el último ``new`` de una clase con ``handle(Context)``.
- ``ApiBuilder.path("/x", () -> {…})`` se sigue de forma recursiva con el prefijo.
- Dentro del handler (y de los métodos de la aplicación a los que llama, hasta
  tres niveles) se buscan ``ctx.bodyAsClass(X.class)``, ``ctx.pathParam("x")``,
  ``ctx.queryParam("x")``, ``ctx.header("x")``, ``ctx.formParam("x")`` y
  ``ctx.cookie("x")``.

Las rutas construidas en tiempo de ejecución (concatenaciones, variables no
constantes) no se pueden leer: se omiten con un aviso. Los handlers guardados
en campos no se identifican; la ruta sale sin parámetros y con una nota.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from morfeo.jar_scanner.bytecode import (
    INVOKEDYNAMIC,
    INVOKEINTERFACE,
    INVOKESPECIAL,
    INVOKESTATIC,
    INVOKEVIRTUAL,
    LDC,
    LDC_W,
    NEW,
    BytecodeError,
    iter_instructions,
)
from morfeo.jar_scanner.classfile import BootstrapMethod, ClassFile, ClassFormatError, ClassRef, MemberInfo, MethodHandleRef
from morfeo.jar_scanner.descriptors import JavaType, SignatureError, parse_field_descriptor
from morfeo.jar_scanner.dto import SchemaBuilder, example_value
from morfeo.jar_scanner.model import FRAMEWORK_JAVALIN, JSON, Body, Endpoint, Param
from morfeo.jar_scanner.paths import javalin_to_template, join_paths

ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_WITH_PATH = "(Ljava/lang/String;Lio/javalin/http/Handler;"
_WITHOUT_PATH = "(Lio/javalin/http/Handler;"
_APIBUILDER = "io/javalin/apibuilder/ApiBuilder"
_GROUP = "(Ljava/lang/String;Lio/javalin/apibuilder/EndpointGroup;"
_CONTEXT_PACKAGE = "io/javalin/http/"
_CONTEXT_ARG = "Lio/javalin/http/Context;"
_HANDLE = ("handle", "(Lio/javalin/http/Context;)V")
_LAMBDA_FACTORY = "java/lang/invoke/LambdaMetafactory"
_CONCAT_FACTORY = "java/lang/invoke/StringConcatFactory"
_MAX_DEPTH = 3

_BODY_CALLS = {"bodyAsClass", "bodyValidator", "bodyStreamAsClass"}
_PARAM_CALLS = {
    "pathParam": "path",
    "pathParamAsClass": "path",
    "queryParam": "query",
    "queryParamAsClass": "query",
    "queryParams": "query",
    "header": "header",
    "headerAsClass": "header",
    "formParam": "form",
    "formParamAsClass": "form",
    "formParams": "form",
    "cookie": "cookie",
    "uploadedFile": "form",
    "uploadedFiles": "form",
}

ClassLoader = Callable[[str], ClassFile | None]


@dataclass(frozen=True)
class _Target:
    owner: str
    name: str
    descriptor: str


class JavalinScanner:
    def __init__(self, classes: list[ClassFile], load: ClassLoader, schemas: SchemaBuilder, warnings: list[str]) -> None:
        self.classes = classes
        self.by_name = {c.this_class: c for c in classes}
        self.load = load
        self.schemas = schemas
        self.warnings = warnings

    def endpoints(self) -> list[Endpoint]:
        groups = self._group_lambdas()
        out: list[Endpoint] = []
        for cls in self.classes:
            for m in cls.methods:
                if (cls.this_class, m.name, m.descriptor) in groups:
                    continue  # se recorre desde su ApiBuilder.path, con el prefijo
                self._scan(cls, m, "", out, 0)
        return out

    # -- recorrido del código ---------------------------------------------------

    def _instructions(self, cls: ClassFile, m: MemberInfo) -> list:  # type: ignore[type-arg]
        if m.code is None:
            return []
        try:
            return list(iter_instructions(m.code.code))
        except BytecodeError as exc:
            self.warnings.append(f"{cls.name}.{m.name}: bytecode ilegible ({exc})")
            return []

    @staticmethod
    def _bootstrap(cls: ClassFile, index: int) -> BootstrapMethod | None:
        """Método de bootstrap de la instrucción ``invokedynamic`` con índice ``index``."""
        try:
            dyn = cls.constant_pool.dynamic(index)
        except ClassFormatError:
            return None
        if dyn.bootstrap_index >= len(cls.bootstrap_methods):
            return None
        return cls.bootstrap_methods[dyn.bootstrap_index]

    def _lambda_target(self, cls: ClassFile, index: int) -> _Target | None:
        bsm = self._bootstrap(cls, index)
        if bsm is None or bsm.method.ref.owner != _LAMBDA_FACTORY or len(bsm.arguments) < 2:
            return None
        impl = bsm.arguments[1]
        if not isinstance(impl, MethodHandleRef):
            return None
        return _Target(impl.ref.owner, impl.ref.name, impl.ref.descriptor)

    def _is_concat(self, cls: ClassFile, index: int) -> bool:
        """``invokedynamic`` de una concatenación de texto (``base + "/x"``)."""
        bsm = self._bootstrap(cls, index)
        return bsm is not None and bsm.method.ref.owner == _CONCAT_FACTORY

    def _group_lambdas(self) -> set[tuple[str, str, str]]:
        """Lambdas pasados a ``ApiBuilder.path``: se recorren con el prefijo, no sueltos."""
        found: set[tuple[str, str, str]] = set()
        for cls in self.classes:
            for m in cls.methods:
                last: _Target | None = None
                for ins in self._instructions(cls, m):
                    if ins.opcode == INVOKEDYNAMIC:
                        last = self._lambda_target(cls, ins.operands[0])
                    elif ins.opcode == INVOKESTATIC:
                        ref = cls.constant_pool.member_ref(ins.operands[0])
                        if ref.owner == _APIBUILDER and ref.name == "path" and ref.descriptor.startswith(_GROUP) and last:
                            found.add((last.owner, last.name, last.descriptor))
        return found

    def _scan(self, cls: ClassFile, m: MemberInfo, prefix: str, out: list[Endpoint], depth: int) -> None:
        cp = cls.constant_pool
        last_text: str | None = None
        last_target: _Target | None = None
        last_new: str | None = None
        dynamic_text = False
        for ins in self._instructions(cls, m):
            op = ins.opcode
            if op in (LDC, LDC_W):
                value = cp.value(ins.operands[0])
                if isinstance(value, str):
                    last_text, dynamic_text = value, False
            elif op == INVOKEDYNAMIC:
                target = self._lambda_target(cls, ins.operands[0])
                if target is not None:
                    last_target = target
                elif self._is_concat(cls, ins.operands[0]):
                    last_text, dynamic_text = None, True  # el texto anterior es solo una parte
            elif op == NEW:
                last_new = cp.class_name(ins.operands[0])
            elif op == INVOKESPECIAL:
                ref = cp.member_ref(ins.operands[0])
                if ref.name == "<init>" and ref.owner == last_new:
                    handler_cls = self.load(ref.owner)
                    if handler_cls is not None and handler_cls.find_method(*_HANDLE):
                        last_target = _Target(ref.owner, *_HANDLE)
            elif op in (INVOKEVIRTUAL, INVOKEINTERFACE, INVOKESTATIC):
                ref = cp.member_ref(ins.operands[0])
                if not ref.owner.startswith("io/javalin/"):
                    continue
                if ref.owner == _APIBUILDER and ref.name == "path" and ref.descriptor.startswith(_GROUP):
                    if last_text is not None and last_target is not None and depth < _MAX_DEPTH:
                        group_cls = self.by_name.get(last_target.owner)
                        group = group_cls.find_method(last_target.name, last_target.descriptor) if group_cls else None
                        if group_cls is not None and group is not None:
                            new_prefix = join_paths(prefix, javalin_to_template(last_text))
                            self._scan(group_cls, group, new_prefix, out, depth + 1)
                    last_text, last_target = None, None
                    continue
                if ref.name not in ROUTE_METHODS:
                    continue
                with_path = ref.descriptor.startswith(_WITH_PATH)
                if not with_path and not ref.descriptor.startswith(_WITHOUT_PATH):
                    continue
                if with_path and (last_text is None or dynamic_text):
                    self.warnings.append(
                        f"{cls.name}.{m.name}: ruta Javalin {ref.name.upper()} construida en tiempo de ejecución; "
                        "no se incluye (usa una constante de texto)"
                    )
                    last_text, last_target, dynamic_text = None, None, False
                    continue
                path = join_paths(prefix, javalin_to_template(last_text or "")) if with_path else (prefix or "/")
                out.append(self._endpoint(ref.name.upper(), path, cls, m, last_target))
                last_text, last_target, dynamic_text = None, None, False

    # -- análisis del handler -------------------------------------------------

    def _endpoint(self, method: str, path: str, cls: ClassFile, m: MemberInfo, target: _Target | None) -> Endpoint:
        if target is None:
            e = Endpoint(method, path, FRAMEWORK_JAVALIN, cls.name, m.name, m.descriptor)
            e.add_note("handler no identificado: sin parámetros ni cuerpo")
            return e
        e = Endpoint(method, path, FRAMEWORK_JAVALIN, target.owner.replace("/", "."), target.name, target.descriptor)
        self._analyze(target, e, 0, set())
        return e

    def _analyze(self, target: _Target, e: Endpoint, depth: int, seen: set[_Target]) -> None:
        if depth > _MAX_DEPTH or target in seen:
            return
        seen.add(target)
        cls = self.by_name.get(target.owner) or self.load(target.owner)
        method = cls.find_method(target.name, target.descriptor) if cls else None
        if cls is None or method is None:
            return
        cp = cls.constant_pool
        last_text: str | None = None
        last_class: str | None = None
        for ins in self._instructions(cls, method):
            op = ins.opcode
            if op in (LDC, LDC_W):
                value = cp.value(ins.operands[0])
                if isinstance(value, str):
                    last_text = value
                elif isinstance(value, ClassRef):
                    last_class = value.descriptor
            elif op in (INVOKEVIRTUAL, INVOKEINTERFACE, INVOKESTATIC, INVOKESPECIAL):
                ref = cp.member_ref(ins.operands[0])
                if ref.owner.startswith(_CONTEXT_PACKAGE):
                    self._context_call(e, ref.name, ref.descriptor, last_text, last_class)
                elif ref.owner in self.by_name and _CONTEXT_ARG in ref.descriptor:
                    self._analyze(_Target(ref.owner, ref.name, ref.descriptor), e, depth + 1, seen)
            elif op == INVOKEDYNAMIC:
                inner = self._lambda_target(cls, ins.operands[0])
                if inner is not None and inner.owner in self.by_name and _CONTEXT_ARG in inner.descriptor:
                    self._analyze(inner, e, depth + 1, seen)

    def _context_call(self, e: Endpoint, name: str, descriptor: str, text: str | None, class_desc: str | None) -> None:
        if name in _BODY_CALLS and descriptor.startswith("(Ljava/lang/Class;") and class_desc:
            t = self._type(class_desc)
            schema = self.schemas.schema(t)
            e.body = Body(t.display(), JSON, schema, example_value(schema))
            return
        location = _PARAM_CALLS.get(name)
        if location is None or text is None or not descriptor.startswith("(Ljava/lang/String;"):
            return
        if name in ("header", "cookie") and descriptor.count(";") > 2:
            return  # ctx.header(nombre, valor) escribe en la respuesta
        if name.endswith("AsClass") and class_desc:
            t = self._type(class_desc)
        elif name.startswith("uploadedFile"):
            t = JavaType.of_class("io.javalin.http.UploadedFile")
        else:
            t = JavaType.of_class("java.lang.String")
        schema = {"type": "string", "format": "binary", "java_type": t.name} if name.startswith("uploadedFile") else self.schemas.schema(t)
        e.add_param(Param(text, location, t.display(), schema, location == "path"))

    @staticmethod
    def _type(class_desc: str) -> JavaType:
        try:
            return parse_field_descriptor(class_desc)
        except SignatureError:
            return JavaType.of_class("java.lang.Object")


def find_javalin_endpoints(
    classes: list[ClassFile], load: ClassLoader, schemas: SchemaBuilder, warnings: list[str]
) -> list[Endpoint]:
    return JavalinScanner(classes, load, schemas, warnings).endpoints()
