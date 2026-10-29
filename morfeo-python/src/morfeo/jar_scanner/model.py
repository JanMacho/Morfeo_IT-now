"""Estructuras intermedias de un endpoint antes de serializarlo (docs/contracts.md, sección 1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from morfeo.common import endpoint_id
from morfeo.jar_scanner.paths import path_regex, path_variables

FRAMEWORK_SPRING = "spring"
FRAMEWORK_JAVALIN = "javalin"

FORM_URLENCODED = "application/x-www-form-urlencoded"
MULTIPART = "multipart/form-data"
JSON = "application/json"


@dataclass
class Param:
    name: str
    location: str  # path, query, header, cookie o form
    java_type: str
    schema: dict[str, Any]
    required: bool
    default: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "in": self.location,
            "java_type": self.java_type,
            "schema": self.schema,
            "required": self.required,
            "default": self.default,
        }


@dataclass
class Body:
    java_type: str
    content_type: str
    schema: dict[str, Any]
    example: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "java_type": self.java_type,
            "content_type": self.content_type,
            "schema": self.schema,
            "example": self.example,
        }


@dataclass
class Endpoint:
    method: str
    path: str
    framework: str
    source_class: str
    source_method: str
    source_descriptor: str
    consumes: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    body: Body | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return endpoint_id(self.method, self.path)

    def add_param(self, param: Param) -> None:
        """Añade el parámetro salvo que ya exista uno con el mismo nombre y ubicación."""
        if not any(p.name == param.name and p.location == param.location for p in self.params):
            self.params.append(param)

    def add_note(self, note: str) -> None:
        if note not in self.notes:
            self.notes.append(note)

    def fill_path_params(self, string_schema: dict[str, Any]) -> None:
        """Toda variable de la plantilla tiene su Param ``in: path`` (String si no se declaró)."""
        declared = {p.name for p in self.params if p.location == "path"}
        for name in path_variables(self.path):
            if name not in declared:
                self.params.append(Param(name, "path", "java.lang.String", dict(string_schema), True))

    def fill_consumes(self) -> None:
        """Si no se declaró ``consumes``, se deduce del cuerpo o de los parámetros de formulario."""
        if self.consumes:
            return
        if self.body is not None:
            self.consumes = [self.body.content_type]
        elif any(p.location == "form" for p in self.params):
            binary = any(p.schema.get("format") == "binary" for p in self.params if p.location == "form")
            self.consumes = [MULTIPART if binary else FORM_URLENCODED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "method": self.method,
            "path": self.path,
            "path_regex": path_regex(self.path),
            "framework": self.framework,
            "source": {"class": self.source_class, "method": self.source_method, "descriptor": self.source_descriptor},
            "consumes": list(self.consumes),
            "produces": list(self.produces),
            "params": [p.to_dict() for p in self.params],
            "body": self.body.to_dict() if self.body else None,
            "notes": list(self.notes),
        }
