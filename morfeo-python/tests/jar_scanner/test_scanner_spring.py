from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from morfeo.cli import main
from morfeo.common import read_json
from morfeo.jar_scanner import scan_jar
from morfeo.jar_scanner.appconfig import AppConfig
from morfeo.jar_scanner.classfile import ClassFile, parse_class
from morfeo.jar_scanner.descriptors import JavaType
from morfeo.jar_scanner.dto import SchemaBuilder
from morfeo.jar_scanner.model import Endpoint
from morfeo.jar_scanner.scanner import write_endpoints
from morfeo.jar_scanner.spring import BinderRules, SpringScanner

BACKEND_JAR = Path(__file__).resolve().parents[3] / "backend-java" / "target" / "bank-api-stress.jar"

EXPECTED_IDS = {
    "GET /api/accounts",
    "POST /api/accounts",
    "POST /api/accounts/batch",
    "GET /api/accounts/export",
    "ANY /api/accounts/ping",
    "GET /api/accounts/search",
    "GET /api/accounts/{id:\\d+}",
    "GET /api/accounts/{id}/movements",
    "HEAD /api/accounts/{id}/movements",
    "PATCH /api/accounts/{id}/status",
    "PUT /api/tags/{id}",
    "POST /api/transfers",
    "GET /api/transfers/rates",
    "GET /audit/log",
    "GET /customers/find",
    "GET /customers/new",
    "POST /customers/new",
    "POST /customers/upload",
    "POST /customers/{customerId}/edit",
    "GET /health",
    "GET /legacy/a",
    "GET /legacy/b",
    "GET /old/a",
    "GET /old/b",
    "GET /reports/daily",
    "GET /search/by-id/{id}",
    "GET /search/by-name",
    "POST /v2/cards",
    "POST /v2/cards/import",
    "DELETE /v2/cards/{cardId}",
    "GET /v2/cards/{cardId}",
}


@pytest.fixture(scope="module")
def doc(jars: Any) -> dict[str, Any]:
    return scan_jar(jars.spring_boot)


def by_id(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in doc["endpoints"]}


def params(endpoint: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(p["name"], p["in"]): p for p in endpoint["params"]}


@pytest.mark.javac
def test_endpoint_ids(doc: dict[str, Any]) -> None:
    assert {e["id"] for e in doc["endpoints"]} == EXPECTED_IDS
    assert doc["jar"]["layout"] == "spring-boot"
    assert doc["jar"]["start_class"] == "com.example.bank.BankApplication"
    assert doc["jar"]["class_versions"] == {"min": 65, "max": 65}
    assert doc["frameworks"] == ["spring"]
    keys = [(e["path"], e["method"]) for e in doc["endpoints"]]
    assert keys == sorted(keys)


@pytest.mark.javac
def test_request_params(doc: dict[str, Any]) -> None:
    e = by_id(doc)
    p = params(e["GET /api/accounts"])
    assert p[("page", "query")]["required"] is False and p[("page", "query")]["default"] == "0"
    assert p[("page", "query")]["schema"]["constraints"] == {"min": 0}
    assert p[("size", "query")]["required"] is False and p[("size", "query")]["default"] is None
    assert p[("owner", "query")]["required"] is False  # Optional<String>

    p = params(e["GET /api/accounts/{id:\\d+}"])
    assert p[("id", "path")]["java_type"] == "long" and p[("id", "path")]["required"] is True
    assert ("X-Request-Id", "header") in p
    assert e["GET /api/accounts/{id:\\d+}"]["path_regex"] == "^/api/accounts/(?:\\d+)$"

    assert params(e["GET /api/accounts/{id}/movements"])[("session", "cookie")]["required"] is False
    assert set(params(e["GET /api/accounts/search"])) == {("q", "query"), ("page", "query"), ("size", "query"), ("sort", "query")}
    export = params(e["GET /api/accounts/export"])
    assert set(export) == {("ratio", "query"), ("format", "query"), ("compressed", "query")}
    status = params(e["PATCH /api/accounts/{id}/status"])[("status", "query")]["schema"]
    assert status["enum"] == ["active", "blocked", "CLOSED"]


@pytest.mark.javac
def test_request_mapping_variants(doc: dict[str, Any]) -> None:
    e = by_id(doc)
    assert any("cualquier método" in n for n in e["ANY /api/accounts/ping"]["notes"])
    rates = e["GET /api/transfers/rates"]
    assert params(rates)[("base", "query")]["default"] == "EUR"
    assert params(rates)[("X-Api-Version", "header")]["default"] == "2"
    assert rates["produces"] == ["application/json"]  # heredado de la clase
    assert any("!debug" in n for n in rates["notes"])


@pytest.mark.javac
def test_body_and_dto(doc: dict[str, Any]) -> None:
    e = by_id(doc)
    body = e["POST /api/accounts"]["body"]
    assert body["java_type"] == "com.example.bank.dto.CreateAccountRequest"
    assert body["content_type"] == "application/json"
    props = body["schema"]["properties"]
    assert list(props)[:2] == ["requestId", "holder_name"]  # superclase primero, @JsonProperty
    assert "phone_number" in props and "phone" not in props  # @JsonProperty en el getter
    for hidden in ("secret", "internalNote", "cache", "VERSION"):
        assert hidden not in props
    assert body["schema"]["required"] == ["requestId", "holder_name", "beneficiaries"]
    assert props["branch"]["properties"]["parent"]["cycle"] is True
    assert props["limits"]["additional_properties"]["type"] == "integer"
    assert body["example"]["age"] == 18 and body["example"]["email"] == "user@example.com"
    assert body["example"]["nationalId"] == "0000000000"

    batch = e["POST /api/accounts/batch"]["body"]["schema"]
    assert batch["properties"]["items"]["items"]["java_type"] == "com.example.bank.dto.CreateAccountRequest"

    transfer = e["POST /api/transfers"]["body"]
    assert transfer["schema"]["required"] == ["from_account", "to_account", "amount", "valueDate"]
    assert transfer["example"]["currency"] == "AAA" and transfer["example"]["amount"] == 0.01


@pytest.mark.javac
def test_inherited_mappings(doc: dict[str, Any]) -> None:
    e = by_id(doc)
    # anotaciones en la interfaz (estilo OpenAPI) con placeholder resuelto a /v2
    assert e["GET /v2/cards/{cardId}"]["source"]["class"] == "com.example.bank.web.CardsController"
    assert params(e["GET /v2/cards/{cardId}"])[("cardId", "path")]["required"] is True
    assert e["POST /v2/cards"]["body"]["java_type"] == "com.example.bank.dto.CardDto"
    # interfaz genérica CrudApi<TagDto, Long>
    tag = e["PUT /api/tags/{id}"]
    assert tag["body"]["java_type"] == "com.example.bank.dto.TagDto"
    assert params(tag)[("id", "path")]["java_type"] == "java.lang.Long"
    # superclase abstracta con implementación, y duplicado con StatusController
    assert e["GET /health"]["source"]["class"] == "com.example.bank.web.HealthController"
    assert any("duplicado GET /health" in w for w in doc["warnings"])
    # sin implementación en el jar
    for orphan in ("GET /audit/log", "GET /reports/daily"):
        assert any("sin implementación" in n for n in e[orphan]["notes"])
    # HttpEntity<List<CardDto>>
    assert e["POST /v2/cards/import"]["body"]["schema"]["type"] == "array"


@pytest.mark.javac
def test_overloaded_handlers_are_kept_apart(doc: dict[str, Any]) -> None:
    e = by_id(doc)
    assert set(params(e["GET /search/by-id/{id}"])) == {("id", "path")}
    assert set(params(e["GET /search/by-name"])) == {("name", "query")}


@pytest.mark.javac
def test_forms(doc: dict[str, Any]) -> None:
    e = by_id(doc)
    create = e["POST /customers/new"]
    assert create["consumes"] == ["application/x-www-form-urlencoded"]
    p = params(create)
    assert ("id", "form") not in p  # @InitBinder: binder.setDisallowedFields("id")
    assert p[("firstName", "form")]["required"] is True  # @Valid + @NotBlank
    assert p[("address", "form")]["required"] is False
    find = params(e["GET /customers/find"])
    assert find[("firstName", "query")]["required"] is False  # sin @Valid no se valida
    assert find[("page", "query")]["default"] == "1"
    edit = params(e["POST /customers/{customerId}/edit"])
    assert ("customerId", "path") in edit and ("firstName", "form") in edit
    upload = params(e["POST /customers/upload"])
    assert upload[("file", "form")]["schema"]["format"] == "binary"
    assert e["GET /customers/new"]["params"] == []  # Model se ignora


@pytest.mark.javac
def test_form_skips_object_collections(jars: Any) -> None:
    def loader(internal: str) -> ClassFile | None:
        p = jars.spring_classes / f"{internal}.class"
        return parse_class(p.read_bytes()) if p.exists() else None

    scanner = SpringScanner([], loader, SchemaBuilder(loader), AppConfig({}), [])
    e = Endpoint("POST", "/x", "spring", "C", "m", "()V")
    scanner._expand_model(e, JavaType.of_class("com.example.bank.dto.CreateAccountRequest"), True)
    names = {p.name for p in e.params}
    assert "beneficiaries" not in names  # List<Beneficiary> no se envía como campo simple
    assert {"holder_name", "branch", "limits"} <= names


@pytest.mark.javac
def test_parameter_names_without_parameters_flag(jars: Any, doc: dict[str, Any]) -> None:
    lvt = scan_jar(jars.spring_boot_lvt)
    assert [e["params"] for e in lvt["endpoints"]] == [e["params"] for e in doc["endpoints"]]

    bare = by_id(scan_jar(jars.spring_boot_bare))
    names = {p["name"] for p in bare["GET /api/accounts/export"]["params"]}
    assert names == {"arg0", "arg1", "arg2"}
    # con nombre en la anotación no hace falta información de depuración
    assert ("X-Request-Id", "header") in params(bare["GET /api/accounts/{id:\\d+}"])


@pytest.mark.javac
def test_bare_build_warns(jars: Any) -> None:
    warnings = scan_jar(jars.spring_boot_bare)["warnings"]
    assert any("-parameters" in w for w in warnings)


@pytest.mark.javac
def test_context_path_and_war(jars: Any) -> None:
    ctx = scan_jar(jars.spring_boot_context)
    assert ctx["context_path"] == "/bank"
    assert all(e["path"].startswith("/bank/") for e in ctx["endpoints"])
    assert scan_jar(jars.spring_boot_context, apply_context_path=False)["context_path"] == ""

    war = scan_jar(jars.war)
    assert war["jar"]["layout"] == "war"
    assert war["context_path"] == "/shop/app"  # el documento del perfil prod se ignora
    assert "GET /shop/app/api/accounts" in by_id(war)


@pytest.mark.javac
def test_deterministic_output(jars: Any, tmp_path: Any) -> None:
    a = write_endpoints(scan_jar(jars.spring_boot), tmp_path / "a.json")
    b = write_endpoints(scan_jar(jars.spring_boot), tmp_path / "b.json")
    assert a.read_bytes() == b.read_bytes()
    assert str(tmp_path) not in a.read_text()


@pytest.mark.javac
def test_corrupt_nested_jar_is_a_warning(jars: Any, tmp_path: Any) -> None:
    import zipfile

    target = tmp_path / "with-bad-lib.jar"
    with zipfile.ZipFile(jars.spring_boot) as src, zipfile.ZipFile(target, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item))
        dst.writestr("BOOT-INF/lib/roto.jar", b"no es un zip")
    doc = scan_jar(target, include_lib=["roto.jar"])
    assert "jar anidado ilegible, se ignora: BOOT-INF/lib/roto.jar" in doc["warnings"]
    assert {e["id"] for e in doc["endpoints"]} == EXPECTED_IDS


def test_binder_rules_patterns() -> None:
    rules = BinderRules(allowed=["name", "address*"], disallowed=["addressSecret"])
    assert rules.accepts("name") and rules.accepts("addressLine1")
    assert not rules.accepts("id") and not rules.accepts("addressSecret")
    assert BinderRules().accepts("cualquiera")


@pytest.mark.skipif(not BACKEND_JAR.exists(), reason="compila antes el backend: cd backend-java && mvn clean package -DskipTests")
def test_backend_jar(tmp_path: Any) -> None:
    """Jar del backend del equipo: Spring Boot 3 con Java 21 (major version 65)."""
    out = tmp_path / "endpoints.json"
    assert main(["scan", str(BACKEND_JAR), "-o", str(out)]) == 0
    doc = read_json(out)
    assert doc["frameworks"] == ["spring"]
    assert doc["jar"]["layout"] == "spring-boot"
    assert doc["jar"]["class_versions"] == {"min": 65, "max": 65}
    e = by_id(doc)
    assert set(e) == {"GET /api/saldo/{id}", "POST /api/ingreso", "POST /api/retirada", "POST /api/usuario/crear"}
    assert params(e["GET /api/saldo/{id}"])[("id", "path")]["required"] is True
    for key in ("POST /api/ingreso", "POST /api/retirada"):
        assert e[key]["body"]["java_type"] == "com.example.bankapi.dto.MovimientoRequest"
        assert e[key]["body"]["example"] == {"id": "string", "cantidad": 1.0}
    crear = e["POST /api/usuario/crear"]["body"]["schema"]["properties"]
    assert crear["saldo_inicial"]["format"] == "decimal"  # renombrado por @JsonProperty
