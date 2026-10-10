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


def test_prompt_teaches_the_period_expressions():
    from aprendiz.agent.planner import PLANNER_SYSTEM_PROMPT as prompt

    for expr in ("inicio_mes:2026-09", "fin_mes:2026-09", "inicio_anio:2026", "fecha:2026-09-15"):
        assert expr in prompt
