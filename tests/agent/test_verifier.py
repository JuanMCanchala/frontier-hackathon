import json

from PIL import Image

from aprendiz.agent.schemas import Plan
from aprendiz.agent.verifier import Captura, verify
from tests.agent.fakes import FakeLlm

IMG = Image.new("RGB", (8, 8))
PLAN = Plan(tipo="crear", entidad="Ana")


def reply(**kw) -> str:
    base = {"verificado": True, "observado": ["registro reabierto"], "no_verificable": []}
    return json.dumps({**base, **kw})


async def run(tipo, text, plan=PLAN):
    llm = FakeLlm([text])
    result = await verify(
        llm, plan, tipo, [Captura("search", IMG)], [Captura("final", IMG)], model="ver"
    )
    return result, llm


async def test_verified_write_with_evidence():
    result, llm = await run("crear", reply())
    assert result.verificado and result.observado == ["registro reabierto"]
    assert len(llm.calls[0]["images"]) == 2
    prompt = llm.calls[0]["messages"][-1]["content"]
    assert "image 1: search" in prompt and "no match" in prompt


async def test_unverifiable_claim_in_a_write_forces_false():
    result, _ = await run("crear", reply(no_verificable=["campo telefono"]))
    assert not result.verificado and result.no_verificable == ["campo telefono"]


async def test_write_with_nothing_observed_is_not_verified():
    result, _ = await run("editar", reply(observado=[]))
    assert not result.verificado


async def test_export_unverifiable_is_false():
    result, _ = await run("exportar", reply(no_verificable=["un solo reporte"]))
    assert not result.verificado


async def test_question_needs_an_answer():
    no_answer, _ = await run("preguntar", reply())
    assert not no_answer.verificado
    answered, _ = await run("preguntar", reply(respuesta=["42"]))
    assert answered.verificado and answered.respuesta == ["42"]


async def test_question_unverifiable_does_not_force_false():
    result, _ = await run("preguntar", reply(respuesta=["42"], no_verificable=["otra cosa"]))
    assert result.verificado


async def test_fail_needs_the_data_read():
    bare, _ = await run("fallar", reply())
    assert not bare.verificado
    good, _ = await run("fallar", reply(datos_motivo=["buscado: Ana", "encontrados: 0"]))
    assert good.verificado


async def test_invalid_reply_is_reasked_once():
    llm = FakeLlm(["???", reply()])
    result = await verify(llm, PLAN, "crear", [], [Captura("f", IMG)], model="ver")
    assert result.verificado and len(llm.calls) == 2


async def test_moderation_block_falls_back_to_the_other_model():
    from aprendiz.agent.llm import ModerationBlocked

    llm = FakeLlm([ModerationBlocked("x"), reply()])
    result = await verify(
        llm, PLAN, "crear", [], [Captura("f", IMG)], model="sonnet", fallback="haiku"
    )
    assert result.verificado
    assert [c["model"] for c in llm.calls] == ["sonnet", "haiku"]


async def test_create_protocol_asks_for_a_search_and_a_persistence_signal_not_variants():
    llm = FakeLlm([reply(observado=["aviso: Cliente CL-0141 creado", "nombre = Ana"])])
    result = await verify(
        llm, PLAN, "crear", [Captura("search", IMG)], [Captura("after saving", IMG)], model="v"
    )
    assert result.verificado
    prompt = llm.calls[0]["messages"][-1]["content"]
    assert "persistence signal" in prompt and "no matching record" in prompt
    assert "Do NOT require name variants" in prompt
    assert "full search with name variants" not in prompt
    system = llm.calls[0]["messages"][0]["content"]
    assert "Do not invent extra requirements" in system


async def test_edit_protocol_needs_unique_identity_and_the_new_value():
    plan = Plan(tipo="editar", entidad="Ana")
    llm = FakeLlm([reply(observado=["documento 111 único", "teléfono = 555-1 en el aviso"])])
    result = await verify(llm, plan, "editar", [], [Captura("after saving", IMG)], model="v")
    assert result.verificado
    prompt = llm.calls[0]["messages"][-1]["content"]
    assert "new value" in prompt and "uniquely" in prompt
    assert "no other visible field changed" not in prompt


async def test_a_protocol_element_that_is_not_shown_still_fails_a_write():
    result, _ = await run("crear", reply(no_verificable=["campo telefono visible"]))
    assert not result.verificado
