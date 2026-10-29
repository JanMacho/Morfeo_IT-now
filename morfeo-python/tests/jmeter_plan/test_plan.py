"""Pruebas de morfeo.jmeter_plan: XML, modelo embebido, carga, peticiones y JMeter real si está instalado."""

from __future__ import annotations

import copy
import csv
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DOCS = HERE.parents[1] / "docs"

from morfeo.cli import main as morfeo_main  # noqa: E402
from morfeo.jmeter_plan import plan as mj  # noqa: E402


def jmeter_main(argv):
    return morfeo_main(["jmeter", *argv])

SALDO = "GET /api/saldo/{id}"
INGRESO = "POST /api/ingreso"
RETIRADA = "POST /api/retirada"
CREAR = "POST /api/usuario/crear"
BOT = "GET /wp-admin"


# --------------------------------------------------------------------------
# Datos de prueba: el banco de bank-api-stress y un perfil con un estado de bot
# que no está en endpoints.json.
# --------------------------------------------------------------------------


def quantiles(lo: float, hi: float) -> list[float]:
    return [round(lo + (hi - lo) * (i / 100) ** 2, 3) for i in range(101)]


def dist(lo: float, hi: float, count: int = 100) -> dict:
    q = quantiles(lo, hi)
    return {
        "count": count,
        "mean": round(sum(q) / len(q), 6),
        "std": 1.0,
        "min": q[0],
        "p50": q[50],
        "p90": q[90],
        "p95": q[95],
        "p99": q[99],
        "max": q[100],
        "quantiles": q,
        "lognormal": None,
    }


def endpoint(method: str, path: str, *, params=None, body=None) -> dict:
    return {
        "id": f"{method} {path}",
        "method": method,
        "path": path,
        "path_regex": "^" + path + "$",
        "framework": "spring",
        "source": {"class": "x", "method": "y", "descriptor": "()V"},
        "consumes": ["application/json"] if body else [],
        "produces": [],
        "params": params or [],
        "body": body,
        "notes": [],
    }


def json_body(example: dict) -> dict:
    props = {k: {"type": "string" if isinstance(v, str) else "number"} for k, v in example.items()}
    return {"java_type": "x.Dto", "content_type": "application/json", "schema": {"type": "object", "properties": props}, "example": example}


def bank_endpoints() -> dict:
    return {
        "schema_version": 1,
        "generator": "morfeo.jar_scanner",
        "jar": {"file": "bank-api-stress.jar"},
        "context_path": "",
        "frameworks": ["spring"],
        "endpoints": [
            endpoint("POST", "/api/ingreso", body=json_body({"id": "string", "cantidad": 1.0})),
            endpoint("POST", "/api/retirada", body=json_body({"id": "string", "cantidad": 1.0})),
            endpoint("GET", "/api/saldo/{id}", params=[{"name": "id", "in": "path", "schema": {"type": "string"}, "required": True, "default": None}]),
            endpoint("POST", "/api/usuario/crear", body=json_body({"id": "string", "saldo_inicial": 1.0})),
        ],
        "warnings": [],
    }


def bank_profile(with_bot: bool = True) -> dict:
    matrix = {
        "START": {SALDO: 0.5, INGRESO: 0.05, RETIRADA: 0.05, CREAR: 0.3, BOT: 0.1},
        SALDO: {SALDO: 0.1, INGRESO: 0.3, RETIRADA: 0.25, BOT: 0.05, "END": 0.3},
        INGRESO: {SALDO: 0.45, INGRESO: 0.08, RETIRADA: 0.11, "END": 0.36},
        RETIRADA: {SALDO: 0.4, INGRESO: 0.11, RETIRADA: 0.08, "END": 0.41},
        CREAR: {SALDO: 0.86, INGRESO: 0.08, RETIRADA: 0.06},
        BOT: {BOT: 0.5, "END": 0.5},
    }
    states = ["START", SALDO, BOT, INGRESO, RETIRADA, CREAR, "END"]
    if not with_bot:
        matrix = {a: {b: p for b, p in row.items() if b != BOT} for a, row in matrix.items() if a != BOT}
        for a, row in matrix.items():
            total = sum(row.values())
            matrix[a] = {b: p / total for b, p in row.items()}
        states.remove(BOT)
    hours = []
    for h in range(24):
        rate = {9: 100.0, 10: 300.0, 11: 200.0, 14: 50.0}.get(h, 0.0)
        hours.append(
            {
                "hour": h,
                "sessions_per_hour": rate,
                "requests_per_hour": rate * 3,
                "rps": rate * 3 / 3600,
                "share_of_daily_requests": 0.0,
                "mean_session_duration_s": None if h == 11 else 20.0,
                "concurrent_users": {"little": 0, "observed_mean": 0, "observed_p95": 0, "observed_max": 0, "recommended": 1},
            }
        )
    endpoints = {}
    for s in states[1:-1]:
        method, path = s.split(" ", 1)
        endpoints[s] = {"method": method, "path": path, "matched": s != BOT, "count": 10, "share": 0.2, "error_rate": 0.0, "server_error_rate": 0.0, "latency_ms": {"p50": 5.0, "p95": 9.0}}
    return {
        "schema_version": 1,
        "generator": "morfeo.traffic_model",
        "seed": 42,
        "input": {"requests": 1000, "sessions": 300, "timezone": "UTC"},
        "states": states,
        "endpoints": endpoints,
        "transition_matrix": matrix,
        "transition_counts": {},
        "think_time_ms": {
            "global": dist(100, 3000),
            "by_state": {SALDO: dist(50, 1500), INGRESO: dist(200, 4000), RETIRADA: dist(150, 3500), CREAR: dist(500, 9000)},
        },
        "session_length": {"requests": dist(1, 15), "duration_s": {**dist(0.5, 90.0), "mean": 25.0, "p99": 88.0}},
        "hourly_curve": {"timezone": "UTC", "days_observed": 1, "hours": hours},
        "concurrent_users": {"peak_hour": 10, "peak_recommended": 5, "daily_mean": 1.0},
        "baseline": {},
    }


def convert(profile=None, endpoints=None, **kw) -> mj.Plan:
    return mj.convert(profile or bank_profile(), endpoints or bank_endpoints(), mj.Options(**kw))


def parse(plan: mj.Plan) -> ET.Element:
    return ET.fromstring(plan.xml)


def embedded_model(root: ET.Element) -> dict:
    for arg in root.iter("elementProp"):
        if arg.get("name") == mj.VAR_MODEL:
            value = next(p.text for p in arg if p.get("name") == "Argument.value")
            return json.loads(value)
    raise AssertionError("el plan no lleva el modelo")


def prop(el: ET.Element, name: str) -> str | None:
    for p in el:
        if p.get("name") == name:
            return p.text
    return None


# --------------------------------------------------------------------------
# XML
# --------------------------------------------------------------------------

GUI = {
    "TestPlan": "TestPlanGui",
    "ThreadGroup": "ThreadGroupGui",
    "SetupThreadGroup": "SetupThreadGroupGui",
    "PostThreadGroup": "PostThreadGroupGui",
    "WhileController": "WhileControllerGui",
    "SwitchController": "SwitchControllerGui",
    "TestAction": "TestActionGui",
    "HTTPSamplerProxy": "HttpTestSampleGui",
    "ConfigTestElement": "HttpDefaultsGui",
    "CookieManager": "CookiePanel",
    "HeaderManager": "HeaderPanel",
    "ResponseAssertion": "AssertionGui",
    "JSR223PreProcessor": "TestBeanGUI",
    "JSR223PostProcessor": "TestBeanGUI",
    "JSR223Timer": "TestBeanGUI",
}


def check_hashtree(tree: ET.Element) -> int:
    """Cada elemento va seguido de su hashTree. Devuelve cuántos elementos hay."""
    kids = list(tree)
    assert len(kids) % 2 == 0
    n = 0
    for el, sub in zip(kids[::2], kids[1::2]):
        assert el.tag != "hashTree" and sub.tag == "hashTree"
        assert el.tag in GUI, f"elemento inesperado {el.tag}"
        assert el.get("testclass") == el.tag and el.get("guiclass") == GUI[el.tag]
        n += 1 + check_hashtree(sub)
    return n


def test_xml_well_formed_with_hashtree_structure():
    root = parse(convert())
    assert root.tag == "jmeterTestPlan" and root.get("version") == "1.2" and root.get("jmeter") == "5.6.3"
    top = list(root)
    assert len(top) == 1 and top[0].tag == "hashTree"
    assert check_hashtree(top[0]) > 10
    assert mj.convert(bank_profile(), bank_endpoints(), mj.Options()).xml.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    # sin oyentes de interfaz gráfica
    assert not list(root.iter("ResultCollector"))


def test_test_plan_target_and_cookies():
    root = parse(convert())
    defaults = next(root.iter("ConfigTestElement"))
    assert prop(defaults, "HTTPSampler.domain") == "${__P(host,localhost)}"
    assert prop(defaults, "HTTPSampler.port") == "${__P(port,7070)}"
    assert prop(defaults, "HTTPSampler.protocol") == "${__P(protocol,http)}"
    cookies = next(root.iter("CookieManager"))
    assert prop(cookies, "CookieManager.clearEachIteration") == "true"
    assert prop(cookies, "CookieManager.controlledByThreadGroup") == "false"
    names = {a.get("name"): prop(a, "Argument.value") for a in root.iter("elementProp") if a.get("elementType") == "Argument"}
    assert names[mj.VAR_PREFIX].startswith("${__P(run_prefix,")
    assert names[mj.VAR_POOL_PREFIX] == "${__P(pool_prefix,mjpool)}"
    assert names[mj.VAR_SEED] == "${__P(seed,42)}"
    assert convert(seed=7).xml != convert().xml


def test_assertions_accept_4xx_and_fail_5xx():
    root = parse(convert())
    patterns = {}
    for a in root.iter("ResponseAssertion"):
        assert prop(a, "Assertion.test_field") == "Assertion.response_code"
        assert prop(a, "Assertion.assume_success") == "true"
        assert prop(a, "Assertion.test_type") == "1"
        patterns[a.get("testname")] = next(iter(next(p for p in a if p.tag == "collectionProp"))).text
    import re

    main = [p for n, p in patterns.items() if "5xx" in n]
    assert main and all(p == "[234][0-9][0-9]" for p in main)
    for code in ("200", "201", "302", "400", "404", "409"):
        assert re.fullmatch(main[0], code)
    for code in ("500", "503", "Non HTTP response code: java.net.ConnectException"):
        assert not re.fullmatch(main[0], code)
    assert any(p == "201|409" for p in patterns.values())


def test_scripts_have_no_jmeter_variables():
    for script in mj.ALL_SCRIPTS:
        assert "${" not in script
    plan = convert()
    root = parse(plan)
    for p in root.iter("stringProp"):
        if p.get("name") == "script":
            assert "${" not in p.text
    assert "$" not in mj.model_json(plan.model)


# --------------------------------------------------------------------------
# Modelo embebido
# --------------------------------------------------------------------------


def rows_from(cum: dict) -> dict[int, float]:
    acc = cum["acumulada"]
    probs = [acc[0]] + [b - a for a, b in zip(acc, acc[1:])]
    return dict(zip(cum["destinos"], probs))


def test_embedded_model_matches_profile_without_dropped_states():
    profile = bank_profile(with_bot=False)
    plan = convert(profile)
    model = embedded_model(parse(plan))
    assert model == json.loads(mj.model_json(plan.model))
    states = model["estados"]
    assert states == [SALDO, INGRESO, RETIRADA, CREAR]
    end = model["fin"]
    assert end == len(states)
    for cum in [model["inicio"], *model["filas"]]:
        assert cum["acumulada"][-1] == 1.0
        assert all(b >= a for a, b in zip(cum["acumulada"], cum["acumulada"][1:]))
        assert math.isclose(sum(rows_from(cum).values()), 1.0, abs_tol=1e-12)
    assert end not in model["inicio"]["destinos"]
    for i, s in enumerate(states):
        expected = profile["transition_matrix"][s]
        got = rows_from(model["filas"][i])
        for b, p in expected.items():
            j = end if b == "END" else states.index(b)
            assert math.isclose(got[j], p, rel_tol=1e-9)
    start = rows_from(model["inicio"])
    for b, p in profile["transition_matrix"]["START"].items():
        assert math.isclose(start[states.index(b)], p, rel_tol=1e-9)
    # esperas: tabla de cada estado en su posición y la global aparte
    for i, s in enumerate(states):
        assert model["espera_por_estado"][i] == profile["think_time_ms"]["by_state"][s]["quantiles"]
    assert model["espera_global"] == profile["think_time_ms"]["global"]["quantiles"]
    assert model["max_pasos"] == 50 and model["pool"] == 200 and model["pool_ancho"] == 4


def test_unknown_state_is_dropped_and_rows_renormalised():
    profile = bank_profile(with_bot=True)
    plan = convert(profile, replay_unmatched=False)
    assert plan.dropped == [BOT]
    assert BOT not in plan.states
    assert any(BOT in w and "endpoints.json" in w for w in plan.warnings)
    assert any(w.startswith("START") and "renormaliza" in w for w in plan.warnings)
    model = plan.model
    states = model["estados"]
    start = rows_from(model["inicio"])
    assert math.isclose(start[states.index(SALDO)], 0.5 / 0.9)
    assert math.isclose(start[states.index(CREAR)], 0.3 / 0.9)
    saldo = rows_from(model["filas"][states.index(SALDO)])
    assert math.isclose(saldo[model["fin"]], 0.3 / 0.95)
    assert math.isclose(sum(saldo.values()), 1.0)
    # filas sin cambios: no hay aviso
    assert not any(w.startswith(INGRESO) for w in plan.warnings)


def test_row_with_only_unknown_targets_ends_the_session():
    profile = bank_profile()
    profile["transition_matrix"][RETIRADA] = {BOT: 1.0}
    plan = convert(profile, replay_unmatched=False)
    states = plan.model["estados"]
    assert rows_from(plan.model["filas"][states.index(RETIRADA)]) == {plan.model["fin"]: 1.0}
    assert any(w.startswith(RETIRADA) and "fin de sesión" in w for w in plan.warnings)


def test_start_without_known_states_is_an_error():
    profile = bank_profile()
    profile["transition_matrix"]["START"] = {BOT: 1.0}
    with pytest.raises(ValueError, match="START"):
        convert(profile, replay_unmatched=False)


def test_missing_think_time_by_state_falls_back_to_null():
    profile = bank_profile()
    del profile["think_time_ms"]["by_state"][RETIRADA]
    model = convert(profile).model
    assert model["espera_por_estado"][model["estados"].index(RETIRADA)] is None


def test_max_steps_and_pool_width():
    profile = bank_profile()
    profile["session_length"]["requests"]["max"] = 40
    model = convert(profile, pool_size=12345).model
    assert model["max_pasos"] == 120
    assert model["pool_ancho"] == 5
    assert convert(max_steps=7).model["max_pasos"] == 7


# --------------------------------------------------------------------------
# Carga
# --------------------------------------------------------------------------


def test_thread_count():
    # 156 sesiones/h con sesiones de 3,02 s: ceil(0,262) + 1
    assert mj.thread_count(156, 3.02, 500) == (2, False)
    # 3600/h con 20 s: 20 simultáneas por Little, 40 + 1
    assert mj.thread_count(3600, 20, 500) == (41, False)
    assert mj.thread_count(3600, 20, 30) == (30, True)
    assert mj.thread_count(0.001, 0.0, 500) == (1, False)
    assert mj.thread_count(3600, 20, 500, factor=3) == (61, False)


def test_curve_mode_groups_and_delays():
    plan = convert(slot_seconds=60, factor=2.0, tail_seconds=45)
    groups = plan.groups
    assert [g.label for g in groups] == ["hora 09", "hora 10", "hora 11", "hora 14"]
    assert [g.rate_per_hour for g in groups] == [200.0, 600.0, 400.0, 100.0]
    assert [g.delay_s for g in groups] == [0, 60, 120, 180]
    assert all(g.window_s == 60 and g.duration_s == 105 for g in groups)
    # hora 11 sin duración media: se usa la media global (25 s)
    assert groups[2].session_s == 25.0 and groups[0].session_s == 20.0
    # en la curva no se suma margen de latencia: los hilos salen de la duración medida
    assert all(g.sizing_s == g.session_s for g in groups)
    assert groups[1].threads == math.ceil(600 / 3600 * 20 * 2) + 1
    assert plan.skipped_hours == [h for h in range(24) if h not in (9, 10, 11, 14)]
    root = parse(plan)
    tgs = list(root.iter("ThreadGroup"))
    assert [prop(t, "ThreadGroup.delay") for t in tgs] == ["0", "60", "120", "180"]
    assert all(prop(t, "ThreadGroup.duration") == "105" and prop(t, "ThreadGroup.scheduler") == "true" for t in tgs)
    assert [prop(t, "ThreadGroup.num_threads") for t in tgs] == [str(g.threads) for g in groups]
    # el temporizador de llegadas recibe la tasa del grupo entero (un solo Poisson por grupo)
    timers = [t for t in root.iter("JSR223Timer") if "Poisson" in t.get("testname")]
    assert len(timers) == len(groups)
    rates = [float(prop(t, "parameters").split()[0]) for t in timers]
    for r, g in zip(rates, groups):
        assert math.isclose(r * 3600, g.rate_per_hour, rel_tol=1e-6)
    assert all(prop(t, "parameters").split()[1] == "60" for t in timers)


def test_curve_mode_hours_option():
    plan = convert(hours=mj.parse_hours("10-11,8"))
    assert [g.label for g in plan.groups] == ["hora 10", "hora 11"]
    assert plan.skipped_hours == [8]
    assert [g.delay_s for g in plan.groups] == [0, 60]
    with pytest.raises(ValueError, match="ninguna"):
        convert(hours=[3, 4])


def test_parse_hours():
    assert mj.parse_hours("8-10") == [8, 9, 10]
    assert mj.parse_hours("22-1") == [22, 23, 0, 1]
    assert mj.parse_hours("5,3,5,4-6") == [5, 3, 4, 6]
    for bad in ("24", "a-b", "", "3-25"):
        with pytest.raises(ValueError):
            mj.parse_hours(bad)
    assert mj.format_hours([0, 1, 2, 5, 7, 8]) == "0-2, 5, 7-8"


def test_steps_mode_groups():
    plan = convert(mode="steps", step_seconds=120, tail_seconds=30)
    groups = plan.groups
    assert [g.rate_per_hour for g in groups] == [300.0, 600.0, 1500.0, 3000.0]
    assert [g.delay_s for g in groups] == [0, 120, 240, 360]
    assert all(g.window_s == 120 and g.duration_s == 150 for g in groups)
    assert all(g.session_s == 25.0 for g in groups)
    # en la escalera los hilos se calculan con 5 s más por petición (espera de la BD antes del 503)
    requests = bank_profile()["session_length"]["requests"]["mean"]
    sizing = 25.0 + requests * 5.0
    assert all(math.isclose(g.sizing_s, sizing) for g in groups)
    assert groups[3].threads == math.ceil(3000 / 3600 * sizing * 2) + 1
    # sin margen, como antes: solo la duración medida
    plain = convert(mode="steps", step_seconds=120, tail_seconds=30, latency_margin=0.0)
    assert plain.groups[3].threads == math.ceil(3000 / 3600 * 25 * 2) + 1
    assert all(g.sizing_s == 25.0 for g in plain.groups)
    custom = convert(mode="steps", base_rate=1000.0, steps=[1.0, 3.0], factor=0.5)
    assert [g.rate_per_hour for g in custom.groups] == [500.0, 1500.0]
    assert [g.label for g in custom.groups] == ["escalón 1 (x1)", "escalón 2 (x3)"]


def test_latency_margin_sizing():
    assert mj.sizing_session_s(3.0, 3.4, 5.0) == pytest.approx(20.0)
    assert mj.sizing_session_s(3.0, 3.4, 0.0) == 3.0
    assert mj.Options(mode="steps").latency_margin_s() == 5.0
    assert mj.Options(mode="curve").latency_margin_s() == 0.0
    assert mj.Options(mode="curve", latency_margin=2.0).latency_margin_s() == 2.0
    # en la curva, con margen, las peticiones por sesión salen de cada hora (requests_per_hour / sessions_per_hour = 3)
    plan = convert(latency_margin=2.0, tail_seconds=45)
    g10 = next(g for g in plan.groups if g.label == "hora 10")
    assert g10.sizing_s == pytest.approx(20.0 + 3 * 2.0)
    assert g10.threads == math.ceil(300 / 3600 * 26.0 * 2) + 1
    # con el tope, el aviso dice cuántas sesiones por hora caben
    capped = convert(mode="steps", base_rate=36000.0, steps=[1.0], max_threads=50)
    g = capped.groups[0]
    assert g.capped and g.max_rate_per_hour == pytest.approx(50 * 3600 / g.sizing_s)
    assert any(f"{g.max_rate_per_hour:.0f} sesiones/h" in w and "se descartan" in w for w in capped.warnings)


def test_thread_cap_warns():
    plan = convert(mode="steps", base_rate=100000.0, steps=[1.0], max_threads=10)
    assert plan.groups[0].threads == 10 and plan.groups[0].capped
    assert any("10 hilos" in w for w in plan.warnings)


def test_default_tail_and_session_estimate():
    profile = bank_profile()
    assert mj.default_tail_s(profile) == 88  # p99 de la duración, mayor que 3 x 25 y que 30
    profile["session_length"]["duration_s"] = {}
    est = mj.estimate_session_s(profile)
    mean_len = profile["session_length"]["requests"]["mean"]
    think = profile["think_time_ms"]["global"]["mean"]
    latency = sum(e["share"] * e["latency_ms"]["p50"] for e in profile["endpoints"].values())
    assert math.isclose(est, (mean_len - 1) * think / 1000 + mean_len * latency / 1000)
    assert mj.mean_session_s(profile) == est
    assert mj.default_tail_s(profile) == max(30, math.ceil(3 * est))


# --------------------------------------------------------------------------
# Peticiones: usuario, cuerpo y endpoint de alta
# --------------------------------------------------------------------------


def samplers(root: ET.Element) -> dict[str, ET.Element]:
    return {s.get("testname"): s for s in root.iter("HTTPSamplerProxy")}


def subtree(root: ET.Element, el: ET.Element) -> list[ET.Element]:
    """Hijos de un elemento: lo que hay en el hashTree que le sigue."""
    for tree in root.iter("hashTree"):
        kids = list(tree)
        if el in kids:
            return list(kids[kids.index(el) + 1])
    raise AssertionError("elemento sin hashTree")


def body_of(sampler: ET.Element) -> str | None:
    for arg in sampler.iter("elementProp"):
        if arg.get("elementType") == "HTTPArgument" and arg.get("name") == "":
            return prop(arg, "Argument.value")
    return None


def test_paths_and_bodies_use_session_user_and_numbers():
    root = parse(convert())
    s = samplers(root)
    assert prop(s[SALDO], "HTTPSampler.path") == "/api/saldo/${morfeo_usuario}"
    assert prop(s[SALDO], "HTTPSampler.method") == "GET"
    assert body_of(s[SALDO]) is None
    assert body_of(s[INGRESO]) == '{"id":"${morfeo_usuario}","cantidad":${morfeo_num_cantidad}}'
    assert prop(s[INGRESO], "HTTPSampler.postBodyRaw") == "true"
    assert body_of(s[CREAR]) == '{"id":"${morfeo_usuario}","saldo_inicial":${morfeo_num_saldo_inicial}}'
    # cabecera Content-Type en cada petición con cuerpo JSON, y en ninguna otra
    for name, sampler in s.items():
        headers = {
            (prop(h, "Header.name"), prop(h, "Header.value"))
            for c in subtree(root, sampler)
            if c.tag == "HeaderManager"
            for h in c.iter("elementProp")
        }
        assert (("Content-Type", "application/json") in headers) == (body_of(sampler) is not None)


def test_number_distributions_in_model():
    numbers = dict(mj.parse_number_spec(t) for t in ("cantidad=uniform:5:50", "*=lognormal:300:0.5"))
    plan = convert(numbers=numbers)
    model = plan.model
    i = model["estados"].index(INGRESO)
    assert model["numeros"][f"s{i}"] == [["morfeo_num_cantidad", "uniform", 5.0, 50.0, False, 1.0, -1.0]]
    c = model["estados"].index(CREAR)
    assert model["numeros"][f"s{c}"] == [["morfeo_num_saldo_inicial", "lognormal", 300.0, 0.5, False, 1.0, -1.0]]
    assert model["numeros"]["alta"] == model["numeros"][f"s{c}"]
    assert f"s{model['estados'].index(SALDO)}" not in model["numeros"]
    default = convert().model
    # sin --number ni esquema: la mediana de un movimiento, o la de un saldo inicial por el nombre del campo
    assert default["numeros"][f"s{i}"][0][1:] == ["lognormal", 80.0, 0.8, False, 1.0, -1.0]
    assert default["numeros"][f"s{c}"][0][1:] == ["lognormal", 300.0, 1.0, False, 1.0, -1.0]
    # la retirada reparte su importe sobre el saldo visto, con 1 - error_rate del perfil
    r = default["estados"].index(RETIRADA)
    assert default["numeros"][f"s{r}"][0][6] == pytest.approx(0.99)  # error_rate 0 del perfil de prueba, con tope 0,99
    assert convert(spend_known=0.7).model["numeros"][f"s{r}"][0][6] == 0.7
    assert convert(balance_aware=False).model["numeros"][f"s{r}"][0][6] == -1.0
    assert default["gasto_fraccion"] == [0.05, 0.55]


def test_parse_number_spec_errors():
    assert mj.parse_number_spec("x=lognormal:80:0.8") == ("x", mj.NumberDist("lognormal", 80.0, 0.8))
    for bad in ("x", "x=lognormal:80", "x=normal:1:2", "x=uniform:5:1", "x=lognormal:0:1", "=uniform:1:2", "x=uniform:a:b"):
        with pytest.raises(ValueError):
            mj.parse_number_spec(bad)


def test_body_template_nested_integers_and_escapes():
    example = {
        "id": "string",
        "cliente": {"id": "otro", "edad": 30},
        "items": [{"precio": 1.5}, 2],
        "activo": True,
        "nota": "a$b\\c",
    }
    text, fields, has_user, _ = mj.body_template(example, "id", {"precio": mj.NumberDist("uniform", 1, 9)})
    assert has_user
    assert [(f.var, f.name, f.integer) for f in fields] == [
        ("morfeo_num_cliente_edad", "edad", True),
        ("morfeo_num_items_0_precio", "precio", False),
        ("morfeo_num_items_1", "items", True),
    ]
    assert fields[1].dist == mj.NumberDist("uniform", 1, 9)
    # el id anidado no es el usuario; el dólar va como escape JSON; las barras se duplican para JMeter
    assert text == (
        '{"id":"${morfeo_usuario}","cliente":{"id":"otro","edad":${morfeo_num_cliente_edad}},'
        '"items":[{"precio":${morfeo_num_items_0_precio}},${morfeo_num_items_1}],"activo":true,"nota":"a\\\\u0024b\\\\\\\\c"}'
    )
    # sin variables, el texto va tal cual (JMeter no lo procesa)
    plain, fields, has_user, _ = mj.body_template({"nota": "a\\b"}, "id", {})
    assert plain == '{"nota":"a\\\\b"}' and not fields and not has_user


def test_query_header_and_other_path_params():
    eps = bank_endpoints()
    eps["endpoints"].append(
        endpoint(
            "GET",
            "/api/cuentas/{banco}/movimientos/{id:[a-z_0-9]+}",
            params=[
                {"name": "banco", "in": "path", "schema": {"type": "integer"}, "required": True, "default": None, "example": "00 49"},
                {"name": "id", "in": "path", "schema": {"type": "string"}, "required": True, "default": None},
                {"name": "desde", "in": "query", "schema": {"type": "string"}, "required": True, "default": "2026-01-01"},
                {"name": "limite", "in": "query", "schema": {"type": "integer"}, "required": False, "default": None},
                {"name": "id", "in": "header", "schema": {"type": "string"}, "required": False, "default": None},
                {"name": "X-Canal", "in": "header", "schema": {"type": "string", "enum": ["web", "app"]}, "required": True, "default": None},
            ],
        )
    )
    state = "GET /api/cuentas/{banco}/movimientos/{id:[a-z_0-9]+}"
    profile = bank_profile()
    profile["states"].insert(1, state)
    profile["endpoints"][state] = {"method": "GET", "path": state.split(" ", 1)[1]}
    profile["transition_matrix"][SALDO][state] = 0.05
    profile["transition_matrix"][state] = {"END": 1.0}
    root = parse(convert(profile, eps))
    s = samplers(root)[state]
    assert prop(s, "HTTPSampler.path") == "/api/cuentas/00%2049/movimientos/${morfeo_usuario}?desde=2026-01-01"
    headers = {
        (prop(h, "Header.name"), prop(h, "Header.value"))
        for c in subtree(root, s)
        for h in c.iter("elementProp")
        if h.get("elementType") == "Header"
    }
    assert headers == {("id", "${morfeo_usuario}"), ("X-Canal", "web")}


def test_any_endpoint_takes_method_from_profile():
    eps = bank_endpoints()
    eps["endpoints"].append(endpoint("ANY", "/api/estado"))
    profile = bank_profile()
    profile["states"].insert(1, "POST /api/estado")
    profile["endpoints"]["POST /api/estado"] = {"method": "POST", "path": "/api/estado"}
    profile["transition_matrix"][SALDO]["POST /api/estado"] = 0.05
    profile["transition_matrix"]["POST /api/estado"] = {"END": 1.0}
    plan = convert(profile, eps)
    assert "POST /api/estado" in plan.states
    s = samplers(parse(plan))["POST /api/estado"]
    assert prop(s, "HTTPSampler.method") == "POST" and prop(s, "HTTPSampler.path") == "/api/estado"


def test_switch_children_follow_state_index():
    plan = convert()
    root = parse(plan)
    switch = next(root.iter("SwitchController"))
    assert prop(switch, "SwitchController.value") == "${morfeo_estado}"
    # los hijos directos del Switch, en orden, son los samplers de los estados del modelo
    names = [el.get("testname") for el in subtree(root, switch)[::2]]
    assert names == plan.model["estados"]
    loop = next(w for w in root.iter("WhileController") if w.get("testname") == "Peticiones de la sesión")
    assert prop(loop, "WhileController.condition") == "${morfeo_seguir}"


def test_create_endpoint_detection():
    eps = bank_endpoints()
    found = mj.detect_create_endpoint(eps, "id")
    assert [e["id"] for e in found] == [CREAR]
    assert mj.detect_create_endpoint(eps, "uid") == []
    # GET o sin campo id no cuentan
    eps["endpoints"].append(endpoint("GET", "/api/register/info"))
    eps["endpoints"].append(endpoint("POST", "/api/signup", body=json_body({"email": "string"})))
    eps["endpoints"].append(endpoint("POST", "/api/v2/users/create", body=json_body({"id": "string"})))
    assert [e["id"] for e in mj.detect_create_endpoint(eps, "id")] == [CREAR, "POST /api/v2/users/create"]
    plan = convert(endpoints=eps)
    assert plan.create == CREAR and "detectado" in plan.create_note and "POST /api/v2/users/create" in plan.create_note


def test_create_endpoint_option():
    plan = convert(create_endpoint="POST  /api/usuario/crear")
    assert plan.create == CREAR and "--create-endpoint" in plan.create_note
    none = convert(create_endpoint="none")
    assert none.create is None
    root = parse(none)
    assert not list(root.iter("SetupThreadGroup"))
    assert not any(p.get("testname") == "Id nuevo para el alta" for p in root.iter("JSR223PreProcessor"))
    assert any("sin endpoint de alta" in w for w in none.warnings)
    with pytest.raises(ValueError, match="no está"):
        convert(create_endpoint="POST /api/nada")


def test_setup_and_new_user_preprocessor():
    plan = convert(pool_size=3)
    root = parse(plan)
    setup = next(root.iter("SetupThreadGroup"))
    assert prop(setup, "ThreadGroup.num_threads") == "3"
    assert "3 usuarios" in setup.get("testname")
    pre = [p for p in root.iter("JSR223PreProcessor") if p.get("testname") == "Primer id del pool"]
    assert prop(pre[0], "parameters") == "0 3"
    assert "alta pool " + CREAR in samplers(root)
    # el preprocesador del id nuevo solo cuelga del sampler de alta de las sesiones
    for tree in root.iter("hashTree"):
        kids = list(tree)
        for el, sub in zip(kids[::2], kids[1::2]):
            if el.tag == "HTTPSamplerProxy":
                has = any(c.get("testname") == "Id nuevo para el alta" for c in sub)
                assert has == (el.get("testname") == CREAR)
    assert len(list(root.iter("SetupThreadGroup"))) == 1


def test_pool_ids_use_stable_pool_prefix():
    # los ids del pool salen de morfeo_prefijo_pool (propiedad pool_prefix, fija);
    # las altas en sesión, del prefijo de la ejecución con _t
    for script in (mj.GROOVY_SESSION_START, mj.GROOVY_SETUP_START, mj.GROOVY_SETUP_NEXT):
        assert "vars.get('morfeo_prefijo_pool') + '_p'" in script
        assert "vars.get('morfeo_prefijo') + '_p'" not in script
    assert "vars.get('morfeo_prefijo') + '_t'" in mj.GROOVY_NEW_USER
    # -Jcreate_pool=false salta las altas del setUp
    assert "props.getProperty('create_pool', 'true')" in mj.GROOVY_SETUP_START
    assert mj.DEFAULT_POOL_PREFIX == "mjpool"


def test_pool_only_plan():
    plan = convert(pool_only=True, pool_size=7)
    root = parse(plan)
    assert plan.groups == []
    assert len(list(root.iter("SetupThreadGroup"))) == 1
    assert not list(root.iter("ThreadGroup")) and not list(root.iter("PostThreadGroup"))
    assert set(samplers(root)) == {"alta pool " + CREAR}
    assert next(root.iter("TestPlan")).get("testname") == "MORFEO: alta del pool"
    assert plan.model["pool"] == 7
    check_hashtree(list(root)[0])
    with pytest.raises(ValueError, match="--pool-only"):
        convert(pool_only=True, create_endpoint="none")
    # el plan de tráfico lleva el tearDown con el resumen de llegadas; sin grupos no hace falta
    full = parse(convert())
    assert [t.get("testname") for t in full.iter("PostThreadGroup")] == ["tearDown: resumen de llegadas"]
    pre = [p for p in full.iter("JSR223PreProcessor") if p.get("testname") == "Sesiones empezadas por grupo"]
    assert len(pre) == 1 and prop(pre[0], "script") == mj.GROOVY_SUMMARY


def log_line(method: str, path: str, status: str, client: str, sid: str) -> str:
    return (
        f'{{"ts_end":"2026-10-13T10:00:00.000000Z","method":"{method}","path":"{path}","status":{status},'
        f'"latency_ms":1.234,"client_id":"{client}","session_id":"{sid}"}}'
    )


def apply_filter(cmd: str, lines: list[str], folder: Path) -> list[str]:
    (folder / "trafico.json").write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(["sh", "-c", cmd], cwd=folder, check=True, env={**os.environ, "LC_ALL": "C"})
    return (folder / "trafico-sin-pool.json").read_text(encoding="utf-8").splitlines()


def test_pool_filter_command(tmp_path):
    plan = convert()
    cmd = plan.pool_filter
    assert cmd == mj.pool_filter_command("POST", "/api/usuario/crear")
    assert cmd.startswith("awk '") and cmd.endswith(" trafico.json > trafico-sin-pool.json")
    assert convert(create_endpoint="none").pool_filter is None
    if shutil.which("awk") is None or shutil.which("sh") is None:
        pytest.skip("sin awk")
    # solo sobreviven las altas dentro de una sesión (<prefijo>_t<grupo>-<hilo>_<n>); el resto es del pool
    lines = [
        log_line("POST", "/api/usuario/crear", "201", "mjpool_p0001", "a" * 32),  # alta del pool inventado: fuera
        log_line("POST", "/api/usuario/crear", "409", "user_007", "a" * 32),  # alta de un usuario de los logs: fuera
        log_line("POST", "/api/usuario/crear", "201", "jm20261013_t1-1_1", "b" * 32),  # alta en sesión
        log_line("POST", "/api/usuario/crear", "201", "jm20261013_127-0-0-1-1199_t2-3_4", "b" * 32),  # en sesión, distribuido
        log_line("GET", "/api/saldo/user_007", "200", "user_007", "b" * 32),  # sesión con usuario del pool
        log_line("POST", "/api/ingreso", "200", "user_007", "b" * 32),
        log_line("POST", "/api/usuario/crearlo", "201", "user_008", "c" * 32),  # otra ruta
        log_line("PUT", "/api/usuario/crear", "405", "user_009", "c" * 32),  # otro método
    ]
    assert apply_filter(cmd, lines, tmp_path) == lines[2:]
    # ruta con variable y otro método
    cmd2 = mj.pool_filter_command("PUT", "/api/usuarios/{id}/alta")
    lines2 = [
        log_line("PUT", "/api/usuarios/p.x_p0001/alta", "201", "p.x_p0001", "a" * 32),
        log_line("PUT", "/api/usuarios/jm1_t1-1_1/alta", "201", "jm1_t1-1_1", "a" * 32),
        log_line("PUT", "/api/usuarios/a/b/alta", "201", "p.x_p0002", "a" * 32),
    ]
    assert apply_filter(cmd2, lines2, tmp_path) == lines2[1:]


def test_create_method_follows_endpoint():
    eps = bank_endpoints()
    eps["endpoints"].append(endpoint("PUT", "/api/usuarios/alta", body=json_body({"id": "string"})))
    plan = convert(endpoints=eps, create_endpoint="PUT /api/usuarios/alta")
    setup = samplers(parse(plan))["alta pool PUT /api/usuarios/alta"]
    assert prop(setup, "HTTPSampler.method") == "PUT"
    assert '\\"method\\":\\"PUT\\"' in plan.pool_filter and "/api/usuarios/alta" in plan.pool_filter
    assert mj.create_method({"method": "ANY"}) == "POST"


def test_arrivals_are_shared_per_group_and_summarised():
    s = mj.GROOVY_ARRIVALS
    # un estado por grupo y ejecución en props, protegido con synchronized
    assert "JMeterContextService.getTestStartTime()" in s and "props.put(clave, L)" in s and "synchronized (L)" in s
    assert "vars.get('morfeo_grupo')" in s
    # ya no hay estado de llegadas por hilo
    assert "morfeo_llegada'" not in s and "morfeo_fin_ventana" not in s
    # el resumen lee las mismas claves
    assert "'morfeo.llegadas.' + org.apache.jmeter.threads.JMeterContextService.getTestStartTime()" in mj.GROOVY_SUMMARY


def test_scripts_vary_seed_and_prefix_per_server():
    loader = mj.GROOVY_LOADER
    assert "props.getProperty('__jm.D_TG', '')" in loader
    assert "vars.put('morfeo_prefijo', vars.get('morfeo_prefijo') + '_' + servidor.replaceAll(" in loader
    assert "servidor.hashCode()" in loader
    assert "servidor.hashCode()" in mj.GROOVY_ARRIVALS


def test_optional_header_and_form_without_value_are_not_sent():
    ep = {
        "path": "/api/x",
        "params": [
            {"name": "X-Opt", "in": "header", "required": False, "default": None},
            {"name": "q", "in": "query", "required": False, "default": None},
            {"name": "f", "in": "form", "required": False, "default": None},
            {"name": "X-Def", "in": "header", "required": False, "default": "v1"},
            {"name": "X-Req", "in": "header", "required": True, "default": None},
            {"name": "g", "in": "form", "required": False, "default": "7"},
            {"name": "id", "in": "header", "required": False, "default": None},
        ],
    }
    get = mj.build_request("GET /api/x", "GET", ep, "id", {}, [])
    post = mj.build_request("POST /api/x", "POST", ep, "id", {}, [])
    for spec in (get, post):
        assert spec.headers == [("X-Def", "v1"), ("X-Req", "1"), ("id", "${morfeo_usuario}")]
    assert get.path == "/api/x?g=7" and get.form == []
    assert post.path == "/api/x" and post.form == [("g", "7")]


def test_leeme_describes_dollar_escape():
    text = (DOCS / "jmeter.md").read_text(encoding="utf-8")
    assert "`\\u0024`" in text
    assert mj.model_json({"a": "x$y"}) == '{"a":"x\\u0024y"}'


def test_id_field_option():
    eps = bank_endpoints()
    for e in eps["endpoints"]:
        if e["body"]:
            ex = e["body"]["example"]
            e["body"]["example"] = {("uid" if k == "id" else k): v for k, v in ex.items()}
            e["body"]["schema"]["properties"] = {("uid" if k == "id" else k): v for k, v in e["body"]["schema"]["properties"].items()}
    eps["endpoints"][2]["path"] = "/api/saldo/{uid}"
    profile = bank_profile()
    profile = json.loads(json.dumps(profile).replace("/api/saldo/{id}", "/api/saldo/{uid}"))
    plan = convert(profile, eps, id_field="uid")
    root = parse(plan)
    s = samplers(root)
    assert prop(s["GET /api/saldo/{uid}"], "HTTPSampler.path") == "/api/saldo/${morfeo_usuario}"
    assert body_of(s[INGRESO]) == '{"uid":"${morfeo_usuario}","cantidad":${morfeo_num_cantidad}}'
    assert plan.create == CREAR


# --------------------------------------------------------------------------
# Determinismo y línea de comandos
# --------------------------------------------------------------------------


def test_deterministic_output():
    a = convert(mode="curve", factor=3.0)
    b = convert(mode="curve", factor=3.0)
    assert a.xml == b.xml
    profile = bank_profile()
    shuffled = copy.deepcopy(profile)
    shuffled["transition_matrix"] = dict(reversed(list(profile["transition_matrix"].items())))
    assert mj.convert(shuffled, bank_endpoints(), mj.Options()).model["filas"] == convert(profile).model["filas"]


def test_cli_writes_identical_files(tmp_path, capsys):
    prof, eps = tmp_path / "profile.json", tmp_path / "endpoints.json"
    prof.write_text(json.dumps(bank_profile()), encoding="utf-8")
    eps.write_text(json.dumps(bank_endpoints()), encoding="utf-8")
    out1, out2 = tmp_path / "a.jmx", tmp_path / "b.jmx"
    args = ["--number", "cantidad=lognormal:80:0.8", "--hours", "9-11"]
    assert jmeter_main([str(prof), str(eps), "-o", str(out1), *args]) == 0
    printed = capsys.readouterr()
    assert "Endpoint de alta: POST /api/usuario/crear (detectado automáticamente)" in printed.out
    assert "hora 10" in printed.out and "Hilos" in printed.out and "Sesión s" in printed.out
    assert "GET /wp-admin" in printed.err
    # cómo quitar las altas del pool de trafico.json, o cómo evitarlas
    assert "200 usuarios inventados (mjpool_p0001 a mjpool_p0200)" in printed.out
    assert mj.pool_filter_command("POST", "/api/usuario/crear") in printed.out
    assert "--pool-only" in printed.out and "-Jcreate_pool=false" in printed.out
    assert jmeter_main([str(prof), str(eps), "-o", str(out2), *args]) == 0
    assert out1.read_bytes() == out2.read_bytes()
    capsys.readouterr()
    pool = tmp_path / "pool.jmx"
    assert jmeter_main([str(prof), str(eps), "-o", str(pool), "--pool-only", "--pool-size", "50"]) == 0
    printed = capsys.readouterr().out
    assert "solo da de alta 50 usuarios inventados (mjpool_p0001 a mjpool_p0050)" in printed
    assert "Hilos" not in printed
    assert ET.fromstring(pool.read_bytes()).find(".//ThreadGroup") is None
    assert jmeter_main([str(prof), str(eps), "-o", str(out2), "--steps", "1,0"]) == 1
    assert "--steps" in capsys.readouterr().err
    assert jmeter_main([str(prof), str(eps), "-o", str(out2), "--latency-margin", "-1"]) == 1
    assert "--latency-margin" in capsys.readouterr().err
    assert jmeter_main([str(tmp_path / "no.json"), str(eps)]) == 1


# --------------------------------------------------------------------------
# JMeter real (se salta si no está instalado)
# --------------------------------------------------------------------------


def find_jmeter() -> str | None:
    for var in ("JMETER_BIN",):
        if os.environ.get(var) and Path(os.environ[var]).is_file():
            return os.environ[var]
    launcher = "jmeter.bat" if os.name == "nt" else "jmeter"  # en Windows `bin/jmeter` es un script de shell
    candidates = []
    if os.environ.get("JMETER_HOME"):
        candidates.append(Path(os.environ["JMETER_HOME"]) / "bin" / launcher)
    candidates.append(HERE.parent / "tools" / "apache-jmeter-5.6.3" / "bin" / launcher)
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which("jmeter")


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]




SUMMARY_RE = re.compile(
    r"MORFEO: (?P<grupo>.+?): (?P<llegadas>\d+) llegadas en la ventana de (?P<ventana>\d+) s "
    r"\(media prevista (?P<prevista>[\d.]+)\); (?P<empezadas>\d+) sesiones empezadas, (?P<por_hora>[\d.]+) por hora "
    r"\(pedidas (?P<pedidas>[\d.]+)\); (?P<tarde>\d+) con más de 1 s de retraso, retraso medio (?P<retraso>[\d.]+) s, "
    r"máximo (?P<maximo>[\d.]+) s; (?P<sin_empezar>\d+) sin empezar por falta de hilos libres"
)


def run_jmeter(folder: Path, xml: bytes, *extra: str) -> tuple[str, list[dict[str, str]], list[dict[str, str]]]:
    """Ejecuta un plan contra un puerto cerrado. Devuelve jmeter.log, las filas de r.jtl y los resúmenes de llegadas."""
    jmeter = find_jmeter()
    if jmeter is None:
        pytest.skip("JMeter no está instalado (JMETER_HOME, JMETER_BIN o jmeter en el PATH)")
    folder.mkdir(parents=True, exist_ok=True)
    jmx = folder / "plan.jmx"
    jmx.write_bytes(xml)
    env = dict(os.environ, JAVA_TOOL_OPTIONS="")
    cmd = [
        jmeter, "-n", "-t", str(jmx), "-l", str(folder / "r.jtl"), "-j", str(folder / "jmeter.log"),
        "-Jhost=127.0.0.1", f"-Jport={closed_port()}", "-Jrun_prefix=prueba",
        "-Lorg.apache.jmeter.protocol.http.sampler.HTTPSamplerBase=ERROR", *extra,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env, cwd=folder)
    log = (folder / "jmeter.log").read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Created the tree successfully" in proc.stdout
    assert " ERROR " not in log and "Problem in JSR223" not in log, log[-3000:]
    with open(folder / "r.jtl", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    summaries = [m.groupdict() for m in SUMMARY_RE.finditer(log)]
    return log, rows, summaries


@pytest.mark.parametrize("mode", ["curve", "steps"])
def test_jmeter_loads_and_runs_plan(tmp_path, mode):
    if mode == "curve":
        opts = mj.Options(slot_seconds=3, factor=400.0, tail_seconds=2, pool_size=5, hours=[10, 11], max_threads=20)
    else:
        opts = mj.Options(
            mode="steps", base_rate=20000.0, steps=[1.0, 2.0], step_seconds=2, tail_seconds=2, pool_size=5, max_threads=20
        )
    plan = mj.convert(bank_profile(), bank_endpoints(), opts)
    log, rows, summaries = run_jmeter(tmp_path, plan.xml)
    assert "MORFEO: modelo cargado" in log
    labels = {r["label"] for r in rows}
    assert "alta pool " + CREAR in labels
    assert labels & set(plan.states), labels
    # contra un puerto cerrado todas las peticiones fallan, pero el plan llega a ejecutarlas
    assert len(rows) > 10
    # el tearDown deja un resumen por grupo, en orden
    assert [s["grupo"] for s in summaries] == [f"{g.label}: {g.rate_per_hour:.1f} sesiones/h" for g in plan.groups]
    for s, g in zip(summaries, plan.groups):
        assert int(s["empezadas"]) + int(s["sin_empezar"]) == int(s["llegadas"])
        assert int(s["ventana"]) == g.window_s and float(s["pedidas"]) == pytest.approx(g.rate_per_hour, abs=0.05)
    assert sum(int(s["empezadas"]) for s in summaries) > 0


def test_jmeter_pool_only_and_create_pool_false(tmp_path):
    pool = mj.convert(bank_profile(), bank_endpoints(), mj.Options(pool_only=True, pool_size=9))
    _, rows, summaries = run_jmeter(tmp_path / "pool", pool.xml)
    assert [r["label"] for r in rows] == ["alta pool " + CREAR] * 9
    assert summaries == []
    traffic = mj.convert(
        bank_profile(), bank_endpoints(), mj.Options(slot_seconds=3, factor=400.0, tail_seconds=2, pool_size=9, hours=[10])
    )
    log, rows, _ = run_jmeter(tmp_path / "trafico", traffic.xml, "-Jcreate_pool=false")
    assert "create_pool=false" in log
    labels = [r["label"] for r in rows]
    assert labels and not any(lb.startswith("alta pool") for lb in labels)


def slow_profile(declared_s: float) -> dict:
    """Cada sesión es GET saldo, 4 s de espera y POST ingreso: dura unos 4 s contra un puerto cerrado.

    El perfil declara ``declared_s`` como duración media. Con 1 s, el plan se
    dimensiona para sesiones más cortas que las reales, igual que cuando el
    servidor se satura y responde despacio.
    """
    const = lambda v: {"count": 100, "mean": v, "std": 0, "min": v, "p50": v, "p90": v, "p95": v, "p99": v, "max": v,  # noqa: E731
                       "quantiles": [v] * 101, "lognormal": None}
    profile = bank_profile(with_bot=False)
    profile["transition_matrix"] = {"START": {SALDO: 1.0}, SALDO: {INGRESO: 1.0}, INGRESO: {"END": 1.0},
                                    RETIRADA: {"END": 1.0}, CREAR: {"END": 1.0}}
    profile["think_time_ms"] = {"global": const(4000.0), "by_state": {SALDO: const(4000.0)}}
    profile["session_length"] = {"requests": {**const(2.0), "max": 2}, "duration_s": {**const(declared_s)}}
    return profile


def test_jmeter_arrivals_keep_rate_with_slow_sessions(tmp_path):
    """Un solo Poisson por grupo, y hilos para sesiones lentas en la escalera.

    3 sesiones/s durante 10 s con sesiones reales de 4 s. Sin margen de
    latencia (7 hilos, caben unas 1,75 sesiones/s) parte de las llegadas no
    encuentra hilo libre; se descartan y se cuentan, y ninguna sesión empieza
    después de la ventana. Con el margen por defecto (5 s por petición, 67
    hilos) empiezan todas a su hora.
    """
    base = dict(mode="steps", base_rate=10800.0, steps=[1.0], step_seconds=10, tail_seconds=6, pool_size=3)
    results = {}
    for name, margin in (("sin_margen", 0.0), ("por_defecto", None)):
        plan = mj.convert(slow_profile(1.0), bank_endpoints(), mj.Options(latency_margin=margin, **base))
        _, rows, summaries = run_jmeter(tmp_path / name, plan.xml)
        assert len(summaries) == 1
        s = {k: (v if k == "grupo" else float(v)) for k, v in summaries[0].items()}
        sessions = [r for r in rows if r["label"] == SALDO]  # primera petición de cada sesión
        assert len(sessions) == s["empezadas"]
        results[name] = (plan.groups[0], s)
    (g0, s0), (g1, s1) = results["sin_margen"], results["por_defecto"]
    assert g0.threads == 7 and g1.threads == math.ceil(3 * 11 * 2) + 1
    # el mismo sorteo de llegadas en las dos ejecuciones (semilla del grupo)
    assert s0["llegadas"] == s1["llegadas"] and 15 <= s1["llegadas"] <= 50
    # 7 hilos con sesiones de 4 s: como mucho 3 sesiones por hilo en 10 s
    assert s0["sin_empezar"] > 0 and s0["empezadas"] <= 7 * 3
    assert s1["sin_empezar"] == 0 and s1["empezadas"] == s1["llegadas"] and s1["tarde"] == 0


def test_jmeter_server_property_changes_seed_and_prefix(tmp_path):
    """En un servidor de JMeter (-R) la propiedad __jm.D_TG lleva su host:puerto.

    Se simula con -J: las altas en sesión llevan el servidor en el id y los
    sorteos cambian; los usuarios del pool no cambian.
    """
    opts = mj.Options(mode="steps", base_rate=7200.0, steps=[1.0], step_seconds=6, tail_seconds=4, pool_size=5, max_threads=40)
    plan = mj.convert(bank_profile(), bank_endpoints(), opts)
    users = {}
    for name, extra in (("local", ()), ("servidor", ("-J__jm.D_TG=10.0.0.1:1099",))):
        _, rows, _ = run_jmeter(tmp_path / name, plan.xml, "-Jsample_variables=morfeo_usuario", *extra)
        rows = [r for r in rows if not r["label"].startswith("alta pool")]
        assert rows
        created = {r["morfeo_usuario"] for r in rows if "_t" in r["morfeo_usuario"]}
        pool = {r["morfeo_usuario"] for r in rows} - created
        assert all(re.fullmatch(r"mjpool_p000[1-5]", u) for u in pool), pool
        prefix = "prueba_10-0-0-1-1099_t" if name == "servidor" else "prueba_t"
        assert all(u.startswith(prefix) for u in created), created
        first = sorted(rows, key=lambda r: int(r["timeStamp"]))[:5]
        users[name] = [(r["label"], r["morfeo_usuario"].replace("_10-0-0-1-1099", "")) for r in first]
    assert users["local"] != users["servidor"]


# --------------------------------------------------------------------------
# Realismo: usuarios de los logs, esquema, saldo y rutas sin endpoint
# --------------------------------------------------------------------------


def write_requests(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    path = tmp_path / "requests.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["session_id", "client_id"])
        w.writerows(rows)
    return path


def test_user_pool_comes_from_requests_csv_with_session_weights(tmp_path):
    rows = [("s1", "user_001"), ("s1", "user_001"), ("s2", "user_001"), ("s3", "user_002"), ("s4", "user_003"), ("s5", "user_003"),
            ("s6", ""), ("s7", "mal id"), ("s8", "user_004")]
    warnings: list[str] = []
    users = mj.load_user_pool(write_requests(tmp_path, rows), 3, warnings)
    # más sesiones primero, desempate por id; sin cliente ni ids no seguros; solo los 3 mejores
    assert users.ids == ["user_001", "user_003", "user_002"] and users.sessions == [2, 2, 1]
    assert any("no seguros" in w for w in warnings)
    assert mj.load_user_pool(write_requests(tmp_path, [("s", "")]), 5, []) is None
    plan = mj.convert(bank_profile(), bank_endpoints(), mj.Options(pool_size=3), users)
    model = plan.model
    assert model["usuarios"] == users.ids and model["pool"] == 3
    assert model["usuarios_acum"] == pytest.approx([0.4, 0.8, 1.0]) and model["usuarios_acum"][-1] == 1.0
    root = parse(plan)
    assert "alta de 3 usuarios del pool" in next(t.get("testname") for t in root.iter("SetupThreadGroup"))
    # sin lista, el modelo no lleva usuarios y el pool es inventado
    assert convert().model["usuarios"] is None and convert().model["usuarios_acum"] is None


def test_numbers_and_texts_follow_the_schema():
    body = {
        "java_type": "x.Dto",
        "content_type": "application/json",
        "schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "importe": {"type": "number", "constraints": {"min": 5, "max": 50}},
                "edad": {"type": "integer", "constraints": {"min": 18}},
                "tipo": {"type": "string", "enum": ["A", "B"]},
                "ref": {"type": "string", "format": "uuid"},
                "correo": {"type": "string", "constraints": {"email": True}},
                "alias": {"type": "string", "constraints": {"min_length": 3, "max_length": 8}},
                "nota": {"type": "string"},
            },
        },
        "example": {"id": "string", "importe": 1.0, "edad": 20, "tipo": "A", "ref": "x", "correo": "a@b.c", "alias": "abc", "nota": "texto"},
    }
    text, fields, has_user, texts = mj.body_template(body["example"], "id", {}, body["schema"])
    by = {f.name: f for f in fields}
    assert by["importe"].dist == mj.NumberDist("uniform", 5.0, 50.0) and by["importe"].floor == 5.0
    assert by["edad"].integer and by["edad"].floor == 18.0
    assert {t.name: (t.kind, t.arg) for t in texts} == {
        "tipo": ("enum", ["A", "B"]),
        "ref": ("uuid", None),
        "correo": ("email", None),
        "alias": ("text", [3, 8]),
    }
    assert '"nota":"texto"' in text  # sin enum, formato ni longitud, queda el ejemplo
    assert '"tipo":"${morfeo_txt_tipo}"' in text and '"importe":${morfeo_num_importe}' in text
    # el modelo y el plan llevan los textos del endpoint
    eps = bank_endpoints()
    eps["endpoints"].append(endpoint("POST", "/api/cuenta", body=body))
    prof = bank_profile()
    prof["states"].insert(-1, "POST /api/cuenta")
    prof["endpoints"]["POST /api/cuenta"] = dict(prof["endpoints"][INGRESO], method="POST", path="/api/cuenta")
    prof["transition_matrix"]["START"]["POST /api/cuenta"] = 0.1
    prof["transition_matrix"]["POST /api/cuenta"] = {"END": 1.0}
    plan = mj.convert(prof, eps, mj.Options())
    i = plan.model["estados"].index("POST /api/cuenta")
    assert [t[0] for t in plan.model["textos"][f"s{i}"]] == ["morfeo_txt_tipo", "morfeo_txt_ref", "morfeo_txt_correo", "morfeo_txt_alias"]


def test_withdrawal_uses_error_rate_and_saldo_scripts():
    prof = bank_profile()
    prof["endpoints"][RETIRADA]["error_rate"] = 0.25
    plan = mj.convert(prof, bank_endpoints(), mj.Options())
    r = plan.model["estados"].index(RETIRADA)
    assert plan.model["numeros"][f"s{r}"][0][6] == pytest.approx(0.75)
    # el saldo visto sale de las respuestas ("saldo" o "saldo_actual") y se borra al empezar la sesión
    assert 'saldo(_actual)?' in mj.GROOVY_NEXT_STATE and "morfeo_saldo" in mj.GROOVY_NEXT_STATE
    assert "vars.put('morfeo_saldo', '')" in mj.GROOVY_SESSION_START
    assert "saldo * ((double) M.gastoMin" in mj.GROOVY_NUMBERS
    # solo las retiradas se reparten sobre el saldo
    assert all(f[6] == -1.0 for f in plan.model["numeros"][f"s{plan.model['estados'].index(INGRESO)}"])
    assert mj.spend_probability(prof, RETIRADA, "/api/retirada", "GET", mj.Options()) == -1.0


def test_unmatched_routes_are_replayed_unless_skipped():
    profile = bank_profile(with_bot=True)
    plan = convert(profile)
    assert plan.dropped == [] and BOT in plan.states
    assert any(BOT in w and "sin parámetros ni cuerpo" in w for w in plan.warnings)
    s = samplers(parse(plan))
    assert prop(s[BOT], "HTTPSampler.path") == "/wp-admin" and prop(s[BOT], "HTTPSampler.method") == "GET"
    assert body_of(s[BOT]) is None
    start = rows_from(plan.model["inicio"])
    assert math.isclose(sum(start.values()), 1.0)
    assert BOT not in convert(profile, replay_unmatched=False).states
