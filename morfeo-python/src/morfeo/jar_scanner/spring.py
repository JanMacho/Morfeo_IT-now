"""Endpoints de Spring Web MVC a partir de las anotaciones del bytecode.

Reglas principales:

- Controladores: clases concretas con ``@Controller`` o ``@RestController``
  (también a través de una anotación propia meta-anotada con ellas) o con
  ``@RequestMapping`` a nivel de clase.
- Las anotaciones de mapping y de parámetros se heredan de superclases e
  interfaces (estilo OpenAPI generator), como hace Spring.
- Ruta final: producto cartesiano de las rutas de clase y de método.
- Interfaces y clases abstractas con mappings que ningún controlador del jar
  implementa se incluyen igualmente, con una nota.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Callable, Iterable

from morfeo.jar_scanner.appconfig import AppConfig
from morfeo.jar_scanner.bytecode import INVOKEVIRTUAL, LDC, LDC_W, BytecodeError, iter_instructions
from morfeo.jar_scanner.classfile import (
    ACC_ABSTRACT,
    ACC_BRIDGE,
    ACC_INTERFACE,
    ACC_STATIC,
    ACC_SYNTHETIC,
    Annotation,
    ClassFile,
    EnumConst,
    MemberInfo,
)
from morfeo.jar_scanner.descriptors import (
    OBJECT,
    JavaType,
    SignatureError,
    parse_class_signature,
    parse_method_descriptor,
    parse_method_signature,
    parameter_slots,
)
from morfeo.jar_scanner.dto import SchemaBuilder, annotation_value, as_strings, example_value, is_simple, type_bindings
from morfeo.jar_scanner.model import FORM_URLENCODED, FRAMEWORK_SPRING, JSON, MULTIPART, Body, Endpoint, Param
from morfeo.jar_scanner.paths import join_paths

WEB = "org.springframework.web.bind.annotation."
CONTROLLER_ANNOTATIONS = {"org.springframework.stereotype.Controller", WEB + "RestController"}
REQUEST_MAPPING = WEB + "RequestMapping"
MAPPING_METHODS = {
    WEB + "GetMapping": "GET",
    WEB + "PostMapping": "POST",
    WEB + "PutMapping": "PUT",
    WEB + "DeleteMapping": "DELETE",
    WEB + "PatchMapping": "PATCH",
}
# centinela de ValueConstants.DEFAULT_NONE: "sin valor por defecto"
DEFAULT_NONE = "\n\t\t\n\t\t\n\n\t\t\t\t\n"

_BODYLESS = {"GET", "DELETE", "HEAD", "OPTIONS"}

# Parámetros que Spring resuelve desde la infraestructura, no desde la petición HTTP.
IGNORED_TYPES = {
    "org.springframework.validation.BindingResult",
    "org.springframework.validation.Errors",
    "org.springframework.ui.Model",
    "org.springframework.ui.ModelMap",
    "org.springframework.web.servlet.ModelAndView",
    "org.springframework.web.servlet.mvc.support.RedirectAttributes",
    "org.springframework.web.bind.support.SessionStatus",
    "org.springframework.web.context.request.WebRequest",
    "org.springframework.web.context.request.NativeWebRequest",
    "org.springframework.web.util.UriComponentsBuilder",
    "org.springframework.web.servlet.support.ServletUriComponentsBuilder",
    "org.springframework.http.HttpMethod",
    "org.springframework.http.HttpHeaders",
    "org.springframework.security.core.Authentication",
    "org.springframework.web.server.ServerWebExchange",
    "org.springframework.http.server.reactive.ServerHttpRequest",
    "org.springframework.http.server.reactive.ServerHttpResponse",
    "java.security.Principal",
    "java.util.Locale",
    "java.util.TimeZone",
    "java.time.ZoneId",
    "java.io.InputStream",
    "java.io.OutputStream",
    "java.io.Reader",
    "java.io.Writer",
    "java.util.Map",
}
for _pkg in ("jakarta", "javax"):
    IGNORED_TYPES.update(
        {
            f"{_pkg}.servlet.http.HttpServletRequest",
            f"{_pkg}.servlet.http.HttpServletResponse",
            f"{_pkg}.servlet.http.HttpSession",
            f"{_pkg}.servlet.ServletRequest",
            f"{_pkg}.servlet.ServletResponse",
        }
    )

INIT_BINDER = WEB + "InitBinder"
_BINDER_OWNERS = {"org/springframework/web/bind/WebDataBinder", "org/springframework/validation/DataBinder"}

_VALID = {
    "jakarta.validation.Valid",
    "javax.validation.Valid",
    "org.springframework.validation.annotation.Validated",
}
_BODY_WRAPPERS = {"org.springframework.http.HttpEntity", "org.springframework.http.RequestEntity"}
_PAGEABLE = "org.springframework.data.domain.Pageable"
_SORT = "org.springframework.data.domain.Sort"
_MULTIPART_TYPES = {"org.springframework.web.multipart.MultipartFile", "jakarta.servlet.http.Part", "javax.servlet.http.Part"}

ClassLoader = Callable[[str], ClassFile | None]


@dataclass
class MappingInfo:
    methods: list[str]
    paths: list[str]
    consumes: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    params: list[str] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    any_method: bool = False


def read_mapping(annotations: Iterable[Annotation]) -> MappingInfo | None:
    """Primer ``@RequestMapping`` o ``@XxxMapping`` de la lista, o None."""
    for ann in annotations:
        name = ann.type_name
        if name != REQUEST_MAPPING and name not in MAPPING_METHODS:
            continue
        if name in MAPPING_METHODS:
            methods = [MAPPING_METHODS[name]]
        else:
            raw = ann.get("method")
            methods = [v.name for v in (raw if isinstance(raw, tuple) else (raw,)) if isinstance(v, EnumConst)]
        return MappingInfo(
            methods=methods,
            paths=as_strings(annotation_value(ann, "value", "path")),
            consumes=as_strings(ann.get("consumes")),
            produces=as_strings(ann.get("produces")),
            params=as_strings(ann.get("params")),
            headers=as_strings(ann.get("headers")),
            any_method=name == REQUEST_MAPPING and not methods,
        )
    return None


def _dotted(internal: str) -> str:
    return internal.replace("/", ".")


def _is_handler_candidate(m: MemberInfo) -> bool:
    return not (m.access_flags & (ACC_STATIC | ACC_BRIDGE | ACC_SYNTHETIC)) and not m.name.startswith("<")


def _param_count(m: MemberInfo) -> int:
    try:
        return len(parse_method_descriptor(m.descriptor)[0])
    except SignatureError:
        return -1


@dataclass
class BinderRules:
    """Campos que un ``@InitBinder`` permite o prohíbe enlazar (patrones con ``*``)."""

    allowed: list[str] = field(default_factory=list)
    disallowed: list[str] = field(default_factory=list)

    def accepts(self, name: str) -> bool:
        if any(fnmatch.fnmatchcase(name, p) for p in self.disallowed):
            return False
        return not self.allowed or any(fnmatch.fnmatchcase(name, p) for p in self.allowed)


def binder_rules(classes: Iterable[ClassFile]) -> BinderRules:
    """Lee las llamadas a ``setAllowedFields`` y ``setDisallowedFields`` de los ``@InitBinder``.

    Los nombres son los textos constantes cargados antes de cada llamada. Un
    ``@InitBinder("owner")`` limitado a un atributo se aplica a todos (aproximación).
    """
    rules = BinderRules()
    for cls in classes:
        for m in cls.methods:
            if m.code is None or not any(a.type_name == INIT_BINDER for a in m.annotations):
                continue
            texts: list[str] = []
            try:
                instructions = list(iter_instructions(m.code.code))
            except BytecodeError:
                continue
            for ins in instructions:
                if ins.opcode in (LDC, LDC_W):
                    value = cls.constant_pool.value(ins.operands[0])
                    if isinstance(value, str):
                        texts.append(value)
                elif ins.opcode == INVOKEVIRTUAL:
                    ref = cls.constant_pool.member_ref(ins.operands[0])
                    if ref.owner in _BINDER_OWNERS and ref.name == "setDisallowedFields":
                        rules.disallowed.extend(texts)
                    elif ref.owner in _BINDER_OWNERS and ref.name == "setAllowedFields":
                        rules.allowed.extend(texts)
                    texts = []
    return rules


@dataclass
class _Declaration:
    cls: ClassFile
    method: MemberInfo
    bindings: dict[str, JavaType]


class SpringScanner:
    def __init__(
        self,
        classes: list[ClassFile],
        load: ClassLoader,
        schemas: SchemaBuilder,
        config: AppConfig,
        warnings: list[str],
    ) -> None:
        self.classes = classes
        self.load = load
        self.schemas = schemas
        self.config = config
        self.warnings = warnings
        self._controller_cache: dict[str, bool] = {}
        self._binder = BinderRules()

    # -- detección ----------------------------------------------------------

    def _is_stereotype(self, ann_type: str, depth: int = 0) -> bool:
        """``@Controller``/``@RestController`` directa o como meta-anotación."""
        if ann_type in CONTROLLER_ANNOTATIONS:
            return True
        if depth >= 2 or ann_type.startswith(("java.", "kotlin.")):
            return False
        if ann_type not in self._controller_cache:
            self._controller_cache[ann_type] = False  # evita ciclos
            cls = self.load(ann_type.replace(".", "/"))
            self._controller_cache[ann_type] = cls is not None and any(
                self._is_stereotype(a.type_name, depth + 1) for a in cls.annotations
            )
        return self._controller_cache[ann_type]

    def _ancestors(self, cls: ClassFile) -> list[tuple[ClassFile, dict[str, JavaType]]]:
        """Superclases e interfaces cargables, con las variables de tipo ya ligadas."""
        out: list[tuple[ClassFile, dict[str, JavaType]]] = []
        seen: set[str] = {cls.this_class}

        def visit(c: ClassFile, bindings: dict[str, JavaType]) -> None:
            sig = None
            if c.signature:
                try:
                    sig = parse_class_signature(c.signature)
                except SignatureError:
                    sig = None
            supers: list[tuple[str, JavaType]] = []
            if c.super_class and c.super_class != "java/lang/Object":
                st = sig.superclass if sig else JavaType.of_class(_dotted(c.super_class))
                supers.append((c.super_class, st.substitute(bindings)))
            for i, iname in enumerate(c.interfaces):
                it = sig.interfaces[i] if sig and i < len(sig.interfaces) else JavaType.of_class(_dotted(iname))
                supers.append((iname, it.substitute(bindings)))
            for iname, st in supers:
                if iname in seen:
                    continue
                seen.add(iname)
                sc = self.load(iname)
                if sc is None:
                    continue
                b = type_bindings(sc, st)
                out.append((sc, b))
                visit(sc, b)

        visit(cls, {})
        return out

    # -- entrada pública ------------------------------------------------------

    def endpoints(self) -> list[Endpoint]:
        result: list[Endpoint] = []
        implemented: set[str] = set()
        for cls in self.classes:
            if cls.access_flags & (ACC_INTERFACE | ACC_ABSTRACT):
                continue
            ancestors = self._ancestors(cls)
            explicit = any(self._is_stereotype(a.type_name) for a in cls.annotations)
            class_mapping = self._class_mapping(cls, ancestors)
            if not explicit and read_mapping(cls.annotations) is None:
                continue
            implemented.update(a.this_class for a, _ in ancestors)
            found = self._controller_endpoints(cls, ancestors, class_mapping)
            if found and not explicit:
                for e in found:
                    e.add_note("clase con @RequestMapping pero sin @Controller: Spring 6 no la registra como controlador")
            result.extend(found)
        for cls in self.classes:
            if not cls.access_flags & (ACC_INTERFACE | ACC_ABSTRACT) or cls.this_class in implemented:
                continue
            found = self._controller_endpoints(cls, self._ancestors(cls), self._class_mapping(cls, []))
            for e in found:
                e.add_note("declarado en una interfaz o clase abstracta sin implementación en el jar")
            result.extend(found)
        return result

    # -- controladores ------------------------------------------------------

    def _class_mapping(
        self, cls: ClassFile, ancestors: list[tuple[ClassFile, dict[str, JavaType]]]
    ) -> MappingInfo | None:
        for c in [cls] + [a for a, _ in ancestors]:
            mapping = read_mapping(c.annotations)
            if mapping is not None:
                return mapping
        return None

    def _controller_endpoints(
        self,
        cls: ClassFile,
        ancestors: list[tuple[ClassFile, dict[str, JavaType]]],
        class_mapping: MappingInfo | None,
    ) -> list[Endpoint]:
        # Una entrada por método concreto (nombre y descriptor exactos). Las
        # declaraciones de superclases e interfaces se unen a la entrada con el
        # mismo descriptor; si no hay (los genéricos cambian el descriptor), a la
        # única con el mismo nombre y número de parámetros. Así dos métodos
        # sobrecargados de la misma clase no se mezclan.
        self._binder = binder_rules([cls] + [a for a, _ in ancestors])
        groups: list[list[_Declaration]] = []
        by_descriptor: dict[tuple[str, str], list[_Declaration]] = {}
        for c, bindings in [(cls, {})] + ancestors:
            for m in c.methods:
                if not _is_handler_candidate(m):
                    continue
                d = _Declaration(c, m, bindings)
                group = by_descriptor.get((m.name, m.descriptor))
                if group is None and c is not cls:
                    same_arity = [
                        g for g in groups if g[0].method.name == m.name and _param_count(g[0].method) == _param_count(m)
                    ]
                    group = same_arity[0] if len(same_arity) == 1 else None
                if group is None:
                    group = []
                    groups.append(group)
                if not any(x.cls is c for x in group):
                    group.append(d)
                by_descriptor.setdefault((m.name, m.descriptor), group)
        out: list[Endpoint] = []
        for decls in groups:
            mapping = next((m for m in (read_mapping(d.method.annotations) for d in decls) if m), None)
            if mapping is None:
                continue
            out.extend(self._build(cls, decls, class_mapping, mapping))
        return out

    def _resolve(self, text: str) -> str:
        value, unresolved = self.config.resolve(text)
        for placeholder in unresolved:
            self.warnings.append(f"placeholder sin resolver en una ruta: {placeholder}")
        return value

    def _build(
        self,
        cls: ClassFile,
        decls: list[_Declaration],
        class_mapping: MappingInfo | None,
        mapping: MappingInfo,
    ) -> list[Endpoint]:
        main = decls[0]
        class_paths = (class_mapping.paths if class_mapping else []) or [""]
        method_paths = mapping.paths or [""]
        consumes = mapping.consumes or (class_mapping.consumes if class_mapping else [])
        produces = mapping.produces or (class_mapping.produces if class_mapping else [])
        conditions = (class_mapping.params if class_mapping else []) + mapping.params
        header_conditions = (class_mapping.headers if class_mapping else []) + mapping.headers
        methods = mapping.methods or ["ANY"]
        endpoints: list[Endpoint] = []
        for cp in class_paths:
            for mp in method_paths:
                path = join_paths(self._resolve(cp), self._resolve(mp))
                for http_method in methods:
                    e = Endpoint(
                        method=http_method,
                        path=path,
                        framework=FRAMEWORK_SPRING,
                        source_class=cls.name,
                        source_method=main.method.name,
                        source_descriptor=main.method.descriptor,
                        consumes=list(consumes),
                        produces=list(produces),
                    )
                    if mapping.any_method:
                        e.add_note("@RequestMapping sin method: Spring acepta cualquier método HTTP")
                    self._add_conditions(e, conditions, header_conditions)
                    self._add_parameters(e, decls)
                    endpoints.append(e)
        return endpoints

    def _add_conditions(self, e: Endpoint, params: list[str], headers: list[str]) -> None:
        string = {"type": "string", "java_type": "java.lang.String"}
        for location, conditions in (("query", params), ("header", headers)):
            for cond in conditions:
                cond = cond.strip()
                if cond.startswith("!") or "!=" in cond:
                    e.add_note(f"condición {location} {cond}: la petición no debe cumplirla")
                    continue
                name, _, value = cond.partition("=")
                name = name.strip()
                if location == "header" and name.lower() in ("content-type", "accept"):
                    continue
                e.add_param(Param(name, location, "java.lang.String", dict(string), True, value.strip() or None))
                e.add_note(f"condición {location} {cond}")

    # -- parámetros ---------------------------------------------------------

    def _parameter_types(self, decls: list[_Declaration]) -> list[JavaType]:
        for d in decls:
            try:
                if d.method.signature:
                    params = parse_method_signature(d.method.signature).parameters
                else:
                    params = parse_method_descriptor(d.method.descriptor)[0]
            except SignatureError:
                continue
            return [p.substitute(d.bindings) for p in params]
        return []

    def _parameter_annotations(self, decls: list[_Declaration], index: int) -> tuple[Annotation, ...]:
        for d in decls:
            groups = d.method.parameter_annotations
            if index < len(groups) and groups[index]:
                return groups[index]
        return ()

    def _parameter_name(self, decls: list[_Declaration], index: int, types: list[JavaType]) -> str | None:
        for d in decls:
            params = d.method.parameters
            if index < len(params) and params[index].name:
                return params[index].name
        for d in decls:
            code = d.method.code
            if code is None or not code.local_variables:
                continue
            slot = parameter_slots(tuple(types), bool(d.method.access_flags & ACC_STATIC))[index]
            for lv in code.local_variables:
                if lv.index == slot and lv.start_pc == 0:
                    return lv.name
        return None

    def _add_parameters(self, e: Endpoint, decls: list[_Declaration]) -> None:
        types = self._parameter_types(decls)
        for i, t in enumerate(types):
            anns = self._parameter_annotations(decls, i)
            by_type = {a.type_name: a for a in anns}
            fallback = self._parameter_name(decls, i, types)
            self._add_parameter(e, t, anns, by_type, fallback, i)

    def _add_parameter(
        self,
        e: Endpoint,
        t: JavaType,
        anns: tuple[Annotation, ...],
        by_type: dict[str, Annotation],
        fallback_name: str | None,
        index: int,
    ) -> None:
        def name_of(ann: Annotation | None) -> str:
            given = annotation_value(ann, "value", "name") if ann is not None else None
            if isinstance(given, str) and given:
                return given
            if fallback_name:
                return fallback_name
            self.warnings.append(
                f"{e.source_class}.{e.source_method}: sin nombre para el parámetro {index} (compila con -parameters)"
            )
            return f"arg{index}"

        optional = t.kind == "class" and t.name == "java.util.Optional"
        inner = (t.arg(0) or OBJECT) if optional else t

        if WEB + "RequestBody" in by_type:
            ann = by_type[WEB + "RequestBody"]
            self._set_body(e, inner, anns, required=ann.get("required", True) is not False and not optional)
            return
        for ann_type, location in (
            (WEB + "PathVariable", "path"),
            (WEB + "RequestParam", "query"),
            (WEB + "RequestHeader", "header"),
            (WEB + "CookieValue", "cookie"),
            (WEB + "RequestPart", "form"),
        ):
            if ann_type not in by_type:
                continue
            ann = by_type[ann_type]
            if _is_map(inner):
                e.add_note(f"parámetro {index} con @{ann_type.rsplit('.', 1)[1]} de tipo Map: acepta cualquier clave")
                return
            if location == "query" and (self._is_multipart(inner) or _form_consumes(e.consumes)):
                location = "form"
            default = ann.get("defaultValue")
            has_default = isinstance(default, str) and default != DEFAULT_NONE
            required = ann.get("required", True) is not False and not optional and not has_default
            e.add_param(
                Param(
                    name_of(ann),
                    location,
                    inner.display(),
                    self.schemas.schema(inner, anns),
                    required,
                    default if has_default else None,
                )
            )
            return
        validated = any(a.type_name in _VALID for a in anns)
        if WEB + "ModelAttribute" in by_type:
            self._expand_model(e, inner, validated)
            return
        if any(a.type_name.startswith(("org.springframework.", "jakarta.", "javax.")) and not _is_validation(a) for a in anns):
            return  # @AuthenticationPrincipal, @RequestAttribute, @Value…: no vienen de la petición
        if inner.kind == "class":
            if inner.name in IGNORED_TYPES:
                return
            if inner.name == _PAGEABLE:
                for name in ("page", "size", "sort"):
                    schema = {"type": "string", "java_type": "java.lang.String"} if name == "sort" else {
                        "type": "integer", "format": "int32", "java_type": "int"}
                    e.add_param(Param(name, "query", schema["java_type"], schema, False))
                return
            if inner.name == _SORT:
                e.add_param(Param("sort", "query", "java.lang.String", {"type": "string", "java_type": "java.lang.String"}, False))
                return
            if inner.name in _BODY_WRAPPERS:
                self._set_body(e, inner.arg(0) or OBJECT, (), required=True)
                return
        if self._is_multipart(inner):
            e.add_param(Param(name_of(None), "form", inner.display(), self.schemas.schema(inner, anns), False))
        elif is_simple(inner, self.load):
            e.add_param(Param(name_of(None), "query", inner.display(), self.schemas.schema(inner, anns), False))
        else:
            self._expand_model(e, inner, validated)

    def _is_multipart(self, t: JavaType) -> bool:
        if t.kind == "array" and t.component is not None:
            return self._is_multipart(t.component)
        if t.kind == "class" and t.args and t.name.startswith("java.util."):
            return self._is_multipart(t.args[0])
        return t.kind == "class" and t.name in _MULTIPART_TYPES

    def _set_body(self, e: Endpoint, t: JavaType, anns: tuple[Annotation, ...], *, required: bool) -> None:
        schema = self.schemas.schema(t, anns)
        content_type = e.consumes[0] if e.consumes else JSON
        e.body = Body(t.display(), content_type, schema, example_value(schema))
        if not required:
            e.add_note("cuerpo opcional (required = false)")

    def _expand_model(self, e: Endpoint, t: JavaType, validated: bool) -> None:
        """``@ModelAttribute`` o DTO sin anotar: un parámetro por campo de primer nivel.

        Un campo es obligatorio solo si el parámetro lleva ``@Valid`` o
        ``@Validated``; sin ellas Spring no aplica las restricciones. Las
        colecciones de objetos se omiten: un formulario no las envía como un
        campo simple. Los objetos sueltos se mantienen porque un Formatter o
        Converter puede crearlos desde texto (PetType en PetClinic). Los campos
        que un ``@InitBinder`` prohíbe (``setDisallowedFields("id")``) se omiten.
        """
        schema = self.schemas.schema(t)
        location = "query" if e.method in _BODYLESS else "form"
        required = set(schema.get("required", [])) if validated else set()
        for name, prop in schema.get("properties", {}).items():
            if not self._binder.accepts(name):
                continue  # @InitBinder lo prohíbe: Spring no lo enlaza
            if prop.get("type") == "array" and prop.get("items", {}).get("type") in ("object", "array"):
                continue
            e.add_param(Param(name, location, prop.get("java_type", "java.lang.Object"), prop, name in required))


def _is_map(t: JavaType) -> bool:
    return t.kind == "class" and t.name in ("java.util.Map", "org.springframework.util.MultiValueMap", "org.springframework.http.HttpHeaders")


def _is_validation(ann: Annotation) -> bool:
    return ann.type_name.startswith(("jakarta.validation.", "javax.validation."))


def _form_consumes(consumes: list[str]) -> bool:
    return any(c.startswith((FORM_URLENCODED, MULTIPART)) for c in consumes)


def find_spring_endpoints(
    classes: list[ClassFile],
    load: ClassLoader,
    schemas: SchemaBuilder,
    config: AppConfig,
    warnings: list[str],
) -> list[Endpoint]:
    return SpringScanner(classes, load, schemas, config, warnings).endpoints()
