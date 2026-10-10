import pytest

from aprendiz.agent.planner import PlanError, plan_from_task
from tests.agent.fakes import FakeLlm, plan_json


async def test_valid_plan_single_call_and_text_only():
    llm = FakeLlm([plan_json()])
    plan = await plan_from_task(llm, "¿Cuál es el saldo de Ana Pérez?", model="m")
    assert plan.tipo == "preguntar" and plan.entidad == "Ana Pérez"
    assert len(llm.calls) == 1
    assert llm.calls[0]["images"] is None
    assert "Ana Pérez" in llm.calls[0]["messages"][-1]["content"]


async def test_invalid_json_retries_once_with_the_error():
    llm = FakeLlm(["esto no es json", plan_json()])
    plan = await plan_from_task(llm, "tarea", model="m")
    assert plan.tipo == "preguntar"
    assert len(llm.calls) == 2
    retry = llm.calls[1]["messages"]
    assert retry[-2] == {"role": "assistant", "content": "esto no es json"}
    assert "not valid JSON" in retry[-1]["content"]


async def test_schema_violation_retries_then_fails():
    llm = FakeLlm([plan_json(tipo="otro"), plan_json(extra=1)])
    with pytest.raises(PlanError):
        await plan_from_task(llm, "tarea", model="m")
    assert len(llm.calls) == 2


def _edit(**overrides) -> str:
    base = {
        "tipo": "editar",
        "entidad": "Ana Pérez",
        "escrituras_permitidas": [
            {"entidad": "Cliente", "accion": "update", "campos": {"telefono": "555"}}
        ],
        "dato_pedido": [],
    }
    return plan_json(**{**base, **overrides})


async def test_edit_without_entity_is_sent_back_once():
    tarea = "Cambia el teléfono de Ana Pérez a 555."
    llm = FakeLlm([_edit(entidad=None), _edit()])
    plan = await plan_from_task(llm, tarea, model="m")
    assert plan.entidad == "Ana Pérez"
    assert len(llm.calls) == 2
    assert "entidad is required" in llm.calls[1]["messages"][-1]["content"]


async def test_edit_entity_that_is_the_new_value_is_sent_back():
    tarea = "Corrige la dirección de Ana Pérez: ahora es Calle 1."
    swapped = _edit(
        entidad="Calle 1",
        escrituras_permitidas=[
            {"entidad": "Cliente", "accion": "update", "campos": {"direccion": "Calle 1"}}
        ],
    )
    llm = FakeLlm([swapped, _edit()])
    plan = await plan_from_task(llm, tarea, model="m")
    assert plan.entidad == "Ana Pérez"
    assert "not the replacement value" in llm.calls[1]["messages"][-1]["content"]


async def test_edit_that_still_omits_the_entity_fails():
    llm = FakeLlm([_edit(entidad=None), _edit(entidad=None)])
    with pytest.raises(PlanError):
        await plan_from_task(llm, "Cambia el teléfono de Ana Pérez a 555.", model="m")


async def test_create_may_repeat_the_entity_as_a_field_value():
    tarea = "Registra a Ana Pérez, teléfono 555."
    created = plan_json(
        tipo="crear",
        entidad="Ana Pérez",
        escrituras_permitidas=[
            {
                "entidad": "Cliente",
                "accion": "create",
                "campos": {"nombre": "Ana Pérez", "telefono": "555"},
            }
        ],
        dato_pedido=[],
    )
    llm = FakeLlm([created])
    plan = await plan_from_task(llm, tarea, model="m")
    assert plan.entidad == "Ana Pérez"
    assert len(llm.calls) == 1
