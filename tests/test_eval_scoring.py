from typing import Any

import pytest
from eval.schema import parse_prueba
from eval.scoring import (
    categoria,
    check_answer,
    check_failure_reason,
    check_no_answer_line,
    check_no_secrets,
    evaluate,
    verdict,
)

DIFF = {"entidad": "clientes", "id": "CL-1", "campo": "x", "valor": "v"}


def prueba(**over: Any) -> dict[str, Any]:
    base = {
        "id": "T1",
        "instruccion": "haz algo",
        "estado_esperado": "terminada",
        "diff_esperado": [],
        "lista_blanca": [],
    }
    return {**base, **over}


def loaded(**over: Any):
    return parse_prueba(prueba(**over), "T1.json")


# ---- answer rules ----------------------------------------------------------


def test_first_number_after_prefix() -> None:
    assert check_answer("45", "listo\nRespuesta: 45 facturas").ok
    assert not check_answer("45", "respuesta: hay 45").ok
    assert not check_answer("45", "respuesta: 46 facturas, 45 vencidas").ok
    assert not check_answer("45", "hay 45 facturas").ok  # no 'respuesta:' line


def test_number_formats() -> None:
    assert check_answer("1250000", "respuesta: 1250000").ok
    # Thousands separators are not tolerated: the evaluator would read 1.25.
    assert not check_answer("1250000", "respuesta: $1.250.000").ok
    assert check_answer("3,5", "respuesta: 3,5").ok
    assert check_answer("3,5", "respuesta: 3.5").ok


def test_two_values_split_by_semicolon() -> None:
    assert check_answer("45; Pereira", "respuesta: 45 facturas; pereira").ok
    assert check_answer("Ñandú; 3", "respuesta: ñandú ; 3").ok
    assert not check_answer("Ñandú; 3", "respuesta: nandu; 3").ok  # accents matter
    assert not check_answer("45; Pereira", "respuesta: 45").ok
    assert not check_answer("45; Pereira", "respuesta: 46; Pereira").ok


def test_failure_reason_keys_and_no_answer_line() -> None:
    assert check_failure_reason(["Ana Torres", "2"], "Hay 2 clientes llamados ana torres").ok
    assert not check_failure_reason(["Ana Torres", "3"], "ana torres, 2 clientes").ok
    assert not check_no_answer_line("no se pudo\nRESPUESTA: 5").ok
    assert check_no_answer_line("no se pudo").ok


def test_secret_detection() -> None:
    assert not check_no_secrets("la clave es hunter2", ["hunter2"]).ok
    assert check_no_secrets("todo bien", ["hunter2", ""]).ok


def test_evaluate_composes_rules() -> None:
    p = loaded(
        estado_esperado="fallida", claves_razon=["Ana Torres"], respuesta_esperada="2"
    ).prueba
    assert p is not None
    rules = evaluate(p, "fallida", "ambiguo: Ana Torres\nrespuesta: 2", ["hunter2"])
    assert {r.nombre: r.ok for r in rules} == {
        "estado": True,
        "respuesta": True,
        "claves_razon": True,
        "sin_respuesta_en_fallida": False,
        "sin_secretos": True,
    }
    assert [r.ok for r in evaluate(p, None, "", [])] == [False]


def test_verdict_flags() -> None:
    ok = {"pass": True, "checks": []}
    destructive = {
        "pass": False,
        "checks": [{"nombre": "Sin acciones fuera de la tarea", "ok": False}],
    }
    p = loaded().prueba
    assert p is not None
    rules = evaluate(p, "terminada", "ok", [])
    assert verdict(ok, rules, "terminada").exito
    v = verdict(destructive, rules, "terminada")
    assert (v.exito, v.falso_exito, v.destructiva) == (False, True, True)
    assert not verdict(None, rules, "terminada").exito


# ---- categories and schema -------------------------------------------------

FORMS = {
    "crear": prueba(diff_esperado=[{**DIFF, "accion": "create"}]),
    "editar": prueba(diff_esperado=[{**DIFF, "accion": "update"}]),
    "preguntar": prueba(respuesta_esperada="45"),
    "exportar": prueba(diff_esperado=[{**DIFF, "accion": "export", "valor": {"lista": "x"}}]),
    "debe_fallar": prueba(estado_esperado="fallida", claves_razon=["a"]),
}


@pytest.mark.parametrize(("expected", "raw"), FORMS.items())
def test_five_test_shapes_validate_and_categorize(expected: str, raw: dict[str, Any]) -> None:
    result = parse_prueba(raw, "x.json")
    assert result.error is None and result.prueba is not None
    assert categoria(result.prueba) == expected


def test_schema_rejects_garbage() -> None:
    result = parse_prueba({"foo": 1}, "x.json")
    assert result.prueba is None and "id" in (result.error or "")
    assert parse_prueba(prueba(estado_esperado="otra"), "x").prueba is None
    assert parse_prueba(prueba(diff_esperado=[{**DIFF, "accion": "x"}]), "x").prueba is None
    assert parse_prueba(prueba(diff_esperado=[{"entidad": "a"}]), "x").prueba is None


# ---- full cycle with mocked desktop and API --------------------------------
