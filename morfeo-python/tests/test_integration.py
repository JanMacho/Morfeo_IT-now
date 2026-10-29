"""Prueba de extremo a extremo: jar real del backend + logs sintéticos hasta profile.json.

Encadena los tres módulos solo a través de los ficheros intermedios, igual que
``morfeo generate``, y comprueba que los contratos encajan entre sí.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from morfeo.cli import main
from morfeo.common import END_STATE, START_STATE, read_json, read_requests_csv

BACKEND_JAR = Path(__file__).resolve().parents[2] / "backend-java" / "target" / "bank-api-stress.jar"

pytestmark = pytest.mark.integration

# mientras jar_scanner y traffic_model no estén terminados, esta prueba se salta
pytest.importorskip("morfeo.jar_scanner.cli")
pytest.importorskip("morfeo.traffic_model")


@pytest.fixture(scope="module")
def pipeline_out(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not BACKEND_JAR.exists():
        pytest.skip(f"no existe {BACKEND_JAR}")
    out = tmp_path_factory.mktemp("generate")
    logs = out / "logs" / "trafico.log"
    logs.parent.mkdir()
    assert main(["synth-logs", "--scenario", "bank", "--days", "2", "--format", "pipe", "-o", str(logs)]) == 0
    code = main(
        [
            "generate",
            "--jar", str(BACKEND_JAR),
            "--logs", str(logs.parent),
            "--out-dir", str(out / "build"),
            "--tz", "Europe/Madrid",
        ]
    )
    assert code == 0
    return out / "build"


def test_generate_writes_all_artifacts(pipeline_out: Path) -> None:
    for name in ("endpoints.json", "requests.csv", "analysis.json", "profile.json", "validation.json"):
        assert (pipeline_out / name).is_file(), name


def test_endpoint_ids_join_across_modules(pipeline_out: Path) -> None:
    doc = read_json(pipeline_out / "endpoints.json")["endpoints"]
    # un endpoint ANY del jar aparece en el perfil con el método real del log
    endpoints = {e["id"] for e in doc} | {f"{m} {e['path']}" for e in doc if e["method"] == "ANY"
                                          for m in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")}
    assert {
        "GET /api/saldo/{id}",
        "POST /api/ingreso",
        "POST /api/retirada",
        "POST /api/usuario/crear",
    } <= endpoints

    requests = read_requests_csv(pipeline_out / "requests.csv")
    matched = set(requests.loc[requests["matched"], "endpoint"])
    assert matched and matched <= endpoints

    profile = read_json(pipeline_out / "profile.json")
    states = set(profile["states"]) - {START_STATE, END_STATE}
    assert matched <= states
    for row in profile["transition_matrix"].values():
        assert abs(sum(row.values()) - 1.0) < 1e-9


def test_generate_is_deterministic(pipeline_out: Path, tmp_path: Path) -> None:
    logs = tmp_path / "logs" / "trafico.log"
    logs.parent.mkdir()
    main(["synth-logs", "--scenario", "bank", "--days", "2", "--format", "pipe", "-o", str(logs)])
    main(
        [
            "generate",
            "--jar", str(BACKEND_JAR),
            "--logs", str(logs.parent),
            "--out-dir", str(tmp_path / "build"),
            "--tz", "Europe/Madrid",
        ]
    )
    for name in ("endpoints.json", "requests.csv", "analysis.json", "profile.json", "validation.json"):
        assert (tmp_path / "build" / name).read_bytes() == (pipeline_out / name).read_bytes(), name
