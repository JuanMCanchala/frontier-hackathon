import pytest
from PIL import Image

from aprendiz.agent.actor import ActorContext, PixelJsonActor, UiTarsActor
from aprendiz.agent.config import AgentSettings
from aprendiz.agent.schemas import Plan
from aprendiz.config import Settings
from tests.agent.fakes import FakeLlm

SETTINGS = Settings(OPENROUTER_API_KEY="k", TEAM_TOKEN="t")
SHOT = Image.new("RGB", (1280, 800))


def ctx(**kw) -> ActorContext:
    return ActorContext(tarea="t", plan=Plan(tipo="preguntar"), seconds_left=100, **kw)


async def test_returns_action_and_sends_bounded_text_history_with_one_image():
    llm = FakeLlm(['{"tipo": "click", "x": 10, "y": 20}'])
    history = [f"click {i}" for i in range(30)]
    action = await PixelJsonActor(llm, SETTINGS, AgentSettings()).next_action(
        SHOT, ctx(history=history, hoy_app="10/10/2026")
    )
    assert (action.tipo, action.x, action.y) == ("click", 10, 20)
    call = llm.calls[0]
    assert call["images"] == [SHOT]
    user = call["messages"][-1]["content"]
    assert "click 29" in user and "click 21" not in user  # last 8 steps only
    assert "10/10/2026" in user and "Time left: 100 s" in user


async def test_invalid_action_is_reasked_once():
    llm = FakeLlm(['{"tipo": "click", "x": [283, 49], "y": 49}', '{"tipo": "wait"}'])
    action = await PixelJsonActor(llm, SETTINGS, AgentSettings()).next_action(SHOT, ctx())
    assert action.tipo == "wait" and len(llm.calls) == 2


async def test_ui_tars_is_not_implemented():
    with pytest.raises(NotImplementedError):
        await UiTarsActor().next_action(SHOT, ctx())
