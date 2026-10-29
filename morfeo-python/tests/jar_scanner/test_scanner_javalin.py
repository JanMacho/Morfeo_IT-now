from __future__ import annotations

from typing import Any

import pytest

from morfeo.cli import main
from morfeo.jar_scanner import scan_jar

def params(endpoint: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(p["name"], p["in"]): p for p in endpoint["params"]}


@pytest.mark.javac
def test_javalin_fixture(jars: Any) -> None:
    doc = scan_jar(jars.javalin)
    e = {x["id"]: x for x in doc["endpoints"]}
    assert set(e) == {
        "GET /admin/stats",
        "POST /admin",
        "GET /admin/users/{userId}",
        "GET /api/products",
        "GET /api/products/{id}",
        "POST /api/orders",
        "PUT /api/orders/{id}",
        "DELETE /api/orders/{id}",
    }
    assert doc["frameworks"] == ["javalin"]
    # base + "/stock" no es constante: se omite con aviso en vez de inventar la ruta
    assert not any("stock" in k or k == "GET /api/v2" for k in e)
    assert any("construida en tiempo de ejecución" in w for w in doc["warnings"])
    assert doc["jar"]["layout"] == "plain"
    # ApiBuilder anidado, <param> pasa a {param}
    assert ("userId", "path") in params(e["GET /admin/users/{userId}"])
    assert ("note", "form") in params(e["POST /admin"])
    # queryParamAsClass con tipo; header(nombre, valor) de la respuesta no cuenta
    products = params(e["GET /api/products"])
    assert products[("page", "query")]["java_type"] == "java.lang.Integer"
    assert ("X-Page", "header") not in products
    # method reference
    assert set(params(e["GET /api/products/{id}"])) == {("id", "path"), ("currency", "query"), ("X-Trace", "header")}
    # cuerpo directo y a través de dos métodos auxiliares
    for key in ("POST /api/orders", "PUT /api/orders/{id}"):
        body = e[key]["body"]
        assert body["java_type"] == "com.example.shop.OrderRequest"
        assert list(body["schema"]["properties"]) == ["customer_id", "products", "total"]
    # clase que implementa Handler
    delete = e["DELETE /api/orders/{id}"]
    assert delete["source"]["class"] == "com.example.shop.DeleteHandler"
    assert set(params(delete)) == {("id", "path"), ("session", "cookie")}


@pytest.mark.javac
def test_no_javalin_flag(jars: Any) -> None:
    assert scan_jar(jars.javalin, detect_javalin=False)["endpoints"] == []


def test_cli_reports_bad_jar(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "roto.jar"
    bad.write_bytes(b"esto no es un zip")
    assert main(["scan", str(bad), "-o", str(tmp_path / "e.json")]) == 1
    assert "no es un fichero zip" in capsys.readouterr().err
