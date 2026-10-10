import pytest
from PIL import Image

from aprendiz.agent.classifier import ClassificationCache, classify_event, crop_around
from aprendiz.agent.llm import LlmError, ModerationBlocked
from tests.agent.fakes import FakeLlm

SHOT = Image.new("RGB", (1280, 800), "white")


def reply(clase: str) -> str:
    return f'{{"clase": "{clase}"}}'


async def classify(llm, modo="ejecucion", tipo="click", punto=(600, 400), cache=None, votes=1):
    return await classify_event(llm, SHOT, punto, tipo, modo, model="m", cache=cache, votes=votes)


def test_crop_is_240x120_and_stays_on_screen():
    assert crop_around(SHOT, (600, 400)).size == (240, 120)
    assert crop_around(SHOT, (0, 0)).size == (240, 120)
    assert crop_around(SHOT, (1279, 799)).size == (240, 120)


async def test_sends_only_the_crop_and_the_event_type():
    llm = FakeLlm([reply("navegacion")])
    assert await classify(llm) == "navegacion"
    call = llm.calls[0]
    assert [i.size for i in call["images"]] == [(240, 120)]
    assert call["messages"][-1]["content"] == "Event type: click"


async def test_llm_error_means_commitment():
    assert await classify(FakeLlm([LlmError("timeout")])) == "compromiso"


async def test_reply_outside_schema_means_commitment():
    assert await classify(FakeLlm(["nope", reply("otra_cosa")])) == "compromiso"


async def test_failures_are_not_cached():
    cache = ClassificationCache()
    llm = FakeLlm([LlmError("x"), reply("navegacion")])
    assert await classify(llm, cache=cache) == "compromiso"
    assert await classify(llm, cache=cache) == "navegacion"


@pytest.mark.parametrize("clase", ["campo_registro", "compromiso"])
async def test_verification_mode_turns_non_reads_into_commitment(clase):
    assert await classify(FakeLlm([reply(clase)]), modo="verificacion") == "compromiso"


@pytest.mark.parametrize("clase", ["navegacion", "busqueda_filtro"])
async def test_verification_mode_keeps_reads(clase):
    assert await classify(FakeLlm([reply(clase)]), modo="verificacion") == clase


async def test_cache_by_crop_and_event_type():
    cache = ClassificationCache()
    llm = FakeLlm([reply("campo_registro"), reply("compromiso")])
    assert await classify(llm, cache=cache) == "campo_registro"
    assert await classify(llm, cache=cache) == "campo_registro"
    assert len(llm.calls) == 1
    assert await classify(llm, tipo="key:Enter", cache=cache) == "compromiso"
    assert len(llm.calls) == 2
    # same cached raw class, other mode: no new call
    assert await classify(llm, modo="verificacion", cache=cache) == "compromiso"
    assert len(llm.calls) == 2


async def test_different_crop_is_not_a_cache_hit():
    other = SHOT.copy()
    other.paste((255, 0, 0), (590, 390, 610, 410))
    cache = ClassificationCache()
    llm = FakeLlm([reply("navegacion"), reply("compromiso")])
    await classify_event(
        llm, SHOT, (600, 400), "click", "ejecucion", model="m", cache=cache, votes=1
    )
    await classify_event(
        llm, other, (600, 400), "click", "ejecucion", model="m", cache=cache, votes=1
    )
    assert len(llm.calls) == 2


async def test_votes_disagree_most_restrictive_wins():
    llm = FakeLlm([reply("navegacion"), reply("campo_registro")])
    assert await classify(llm, votes=2) == "campo_registro"
    assert len(llm.calls) == 2
    llm = FakeLlm([reply("busqueda_filtro"), reply("compromiso")])
    assert await classify(llm, votes=2) == "compromiso"


async def test_two_votes_that_agree_are_cached_once():
    cache = ClassificationCache()
    llm = FakeLlm([reply("navegacion"), reply("navegacion")])
    assert await classify(llm, cache=cache, votes=2) == "navegacion"
    assert await classify(llm, cache=cache, votes=2) == "navegacion"
    assert len(llm.calls) == 2


async def test_one_failed_vote_means_commitment_and_is_not_cached():
    cache = ClassificationCache()
    llm = FakeLlm([reply("navegacion"), LlmError("x"), reply("navegacion"), reply("navegacion")])
    assert await classify(llm, cache=cache, votes=2) == "compromiso"
    assert await classify(llm, cache=cache, votes=2) == "navegacion"


async def test_moderation_block_retries_with_fallback_model():
    llm = FakeLlm([ModerationBlocked("403"), reply("campo_registro")])
    got = await classify_event(
        llm, SHOT, (600, 400), "click", "ejecucion", model="main", fallback="backup", votes=1
    )
    assert got == "campo_registro"
    assert [c["model"] for c in llm.calls] == ["main", "backup"]


async def test_moderation_block_on_the_fallback_too_means_commitment():
    llm = FakeLlm([ModerationBlocked("a"), ModerationBlocked("b")])
    got = await classify_event(
        llm, SHOT, (600, 400), "click", "ejecucion", model="main", fallback="backup", votes=1
    )
    assert got == "compromiso"


def test_prompt_defines_the_cases_seen_in_the_real_app():
    from aprendiz.agent.classifier import CLASSIFIER_SYSTEM_PROMPT as prompt

    nav = prompt.split('- "busqueda_filtro"')[0]
    for case in (
        "New, Add, View",
        "Back, Cancel, Close",
        "opening a dropdown or",
        "without any control",
    ):
        assert case in nav.replace("\n  ", " "), case
    field = prompt.split('- "campo_registro"')[1].split('- "compromiso"')[0]
    assert "option of a select or dropdown INSIDE a form" in field.replace("\n  ", " ")
    commit = prompt.split('- "compromiso"')[1]
    for case in ("Save", "Delete", "Void", "Export", "Revert", "checkboxes", "icon buttons"):
        assert case in commit, case


async def test_new_client_button_and_select_option_follow_the_model_answers():
    # the fake stands in for the model: the code must pass the answer through untouched
    assert await classify(FakeLlm([reply("navegacion")])) == "navegacion"
    assert await classify(FakeLlm([reply("campo_registro")])) == "campo_registro"


async def test_submit_button_check():
    from aprendiz.agent.classifier import is_submit_button

    assert await is_submit_button(FakeLlm(['{"es_guardar": true}']), SHOT, (5, 5), model="m")
    assert not await is_submit_button(FakeLlm(['{"es_guardar": false}']), SHOT, (5, 5), model="m")
    assert not await is_submit_button(FakeLlm([LlmError("x")]), SHOT, (5, 5), model="m")
    assert not await is_submit_button(FakeLlm(["junk", "junk"]), SHOT, (5, 5), model="m")
