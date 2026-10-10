import json

from PIL import Image

from aprendiz.agent.detector import detect_effect
from aprendiz.agent.llm import LlmError
from tests.agent.fakes import FakeLlm

BEFORE = Image.new("RGB", (1280, 800), "white")


def changed(box: tuple[int, int, int, int]) -> Image.Image:
    after = BEFORE.copy()
    after.paste((0, 0, 0), box)
    return after


def reply(persisted: bool, signal: str = "") -> str:
    return json.dumps({"persisted": persisted, "signal": signal})


async def detect(llm, after, clasificacion="compromiso", autorizado=False):
    return await detect_effect(llm, BEFORE, after, "click", clasificacion, autorizado, model="det")


async def test_no_change_means_no_model_call():
    llm = FakeLlm([])
    effect = await detect(llm, BEFORE.copy())
    assert not effect.persistio and llm.calls == []


async def test_cursor_sized_noise_is_ignored_even_for_commitments():
    llm = FakeLlm([])
    effect = await detect(llm, changed((10, 10, 14, 14)))
    assert not effect.persistio and llm.calls == []


async def test_small_change_after_navigation_skips_the_model():
    llm = FakeLlm([])
    effect = await detect(llm, changed((0, 0, 100, 20)), clasificacion="navegacion")
    assert not effect.persistio and llm.calls == []


async def test_large_change_after_navigation_never_asks_the_model():
    llm = FakeLlm([])
    effect = await detect(llm, changed((0, 0, 1280, 400)), clasificacion="navegacion")
    assert not effect.persistio and llm.calls == []


async def test_large_change_after_search_asks_the_model():
    llm = FakeLlm([reply(False)])
    effect = await detect(llm, changed((0, 0, 1280, 400)), clasificacion="busqueda_filtro")
    assert not effect.persistio and len(llm.calls) == 1
    assert len(llm.calls[0]["images"]) == 2


async def test_unauthorized_persistence_aborts():
    llm = FakeLlm([reply(True, "Cliente CL-0141 creado")])
    effect = await detect(llm, changed((0, 0, 100, 20)), autorizado=False)
    assert effect.persistio and effect.debe_abortar
    assert effect.señal == "Cliente CL-0141 creado"


async def test_authorized_persistence_does_not_abort():
    llm = FakeLlm([reply(True, "guardado")])
    effect = await detect(llm, changed((0, 0, 100, 20)), autorizado=True)
    assert effect.persistio and not effect.debe_abortar


async def test_detector_failure_on_a_commitment_assumes_persistence():
    effect = await detect(FakeLlm([LlmError("x")]), changed((0, 0, 100, 20)))
    assert effect.persistio and effect.debe_abortar
    ok = await detect(
        FakeLlm([LlmError("x")]), changed((0, 0, 1280, 400)), clasificacion="busqueda_filtro"
    )
    assert not ok.persistio
