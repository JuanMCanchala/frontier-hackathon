import json
import time

import httpx
import pytest
from PIL import Image
from pydantic import BaseModel

from aprendiz.agent.budget import Budget, BudgetExceeded
from aprendiz.agent.config import AgentSettings
from aprendiz.agent.llm import (
    SCHEMA_REJECTED,
    DeadlineExceeded,
    LlmError,
    ModerationBlocked,
    OpenRouterClient,
)
from aprendiz.config import Settings

KEY = "sk-or-secret-key-1234"
OK = {"choices": [{"message": {"content": "hola"}}], "usage": {"cost": 0.002}}
MSGS = [{"role": "user", "content": "hi"}]


def make(handler, budget: Budget | None = None, **agent_kw) -> tuple[OpenRouterClient, Budget]:
    settings = Settings(OPENROUTER_API_KEY=KEY, TEAM_TOKEN="t")
    agent = AgentSettings(LLM_BACKOFF_S=0.001, **agent_kw)
    budget = budget or Budget(0.15)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenRouterClient(settings, agent, budget, http=http), budget


async def test_chat_returns_text_real_cost_and_sends_image():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json=OK)

    client, budget = make(handler)
    result = await client.chat(
        "anthropic/claude-haiku-5.5", MSGS, images=[Image.new("RGB", (4, 4))], json_mode=True
    )
    assert (result.text, result.cost_usd) == ("hola", 0.002)
    assert budget.spent == pytest.approx(0.002) and budget.reserved == 0
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"]["usage"] == {"include": True}
    assert seen["body"]["response_format"] == {"type": "json_object"}
    content = seen["body"]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_retries_429_then_succeeds():
    codes = iter([429, 503, 200])

    def handler(_r: httpx.Request) -> httpx.Response:
        code = next(codes)
        return httpx.Response(code, json=OK if code == 200 else {})

    client, _ = make(handler)
    assert (await client.chat("m", MSGS)).text == "hola"


async def test_gives_up_after_retries_and_releases_reservation():
    calls = 0

    def handler(_r: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={})

    client, budget = make(handler)
    with pytest.raises(LlmError):
        await client.chat("m", MSGS)
    assert calls == 3 and budget.reserved == 0 and budget.spent == 0


async def test_no_retry_on_client_error():
    calls = 0

    def handler(_r: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": "bad"})

    client, _ = make(handler)
    with pytest.raises(LlmError):
        await client.chat("m", MSGS)
    assert calls == 1


async def test_budget_exceeded_before_any_request():
    def handler(_r: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    client, _ = make(handler, Budget(0.0001))
    with pytest.raises(BudgetExceeded):
        await client.chat("anthropic/claude-sonnet-5.5", MSGS, images=[Image.new("RGB", (4, 4))])


async def test_deadline_in_the_past_is_refused_without_calling():
    def handler(_r: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    client, budget = make(handler)
    with pytest.raises(DeadlineExceeded):
        await client.chat("m", MSGS, deadline=time.monotonic() - 1)
    assert budget.reserved == 0


async def test_backoff_never_goes_past_deadline():
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    client, _ = make(handler, LLM_RETRIES=2)
    client._agent = AgentSettings(LLM_BACKOFF_S=5)
    with pytest.raises(DeadlineExceeded):
        await client.chat("m", MSGS, deadline=time.monotonic() + 1)


async def test_missing_cost_charges_the_reserved_estimate():
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    client, budget = make(handler)
    await client.chat("m", MSGS)
    assert budget.spent > 0


async def test_api_key_not_in_repr_or_logs(caplog: pytest.LogCaptureFixture):
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=OK)

    client, _ = make(handler)
    with caplog.at_level("DEBUG"):
        await client.chat("m", MSGS)
    assert KEY not in repr(client)
    assert KEY not in caplog.text


async def test_moderation_403_is_its_own_error_and_not_retried():
    calls = 0

    def handler(_r: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            403, json={"error": {"message": "This request contains prohibited content"}}
        )

    client, budget = make(handler)
    with pytest.raises(ModerationBlocked):
        await client.chat("m", MSGS)
    assert calls == 1 and budget.reserved == 0


async def test_other_403_is_a_plain_error():
    client, _ = make(lambda _r: httpx.Response(403, json={"error": "forbidden"}))
    with pytest.raises(LlmError) as info:
        await client.chat("m", MSGS)
    assert not isinstance(info.value, ModerationBlocked)


async def test_call_is_refused_when_less_than_the_minimum_time_is_left():
    def handler(_r: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    client, budget = make(handler)
    with pytest.raises(DeadlineExceeded):
        await client.chat("m", MSGS, deadline=time.monotonic() + 3)
    assert budget.reserved == 0 and budget.spent == 0


class _Shape(BaseModel):
    nombre: str
    edad: int | None = None
    etiquetas: list[str] = []


@pytest.fixture(autouse=True)
def _clear_schema_memory():
    SCHEMA_REJECTED.clear()
    yield
    SCHEMA_REJECTED.clear()


async def test_json_schema_is_sent_to_models_that_support_it():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["response_format"])
        return httpx.Response(200, json=OK)

    client, _ = make(handler)
    await client.chat("anthropic/claude-haiku-5.5", MSGS, json_mode=True, schema=_Shape)
    await client.chat("some/other-model", MSGS, json_mode=True, schema=_Shape)
    first, second = seen
    assert first["type"] == "json_schema"
    spec = first["json_schema"]
    assert spec["name"] == "_Shape" and spec["strict"] is True
    assert spec["schema"]["additionalProperties"] is False
    assert spec["schema"]["required"] == ["nombre", "edad", "etiquetas"]
    assert "default" not in json.dumps(spec["schema"])
    assert second == {"type": "json_object"}


async def test_400_on_the_schema_falls_back_to_json_object_and_is_remembered():
    formats = []

    def handler(request: httpx.Request) -> httpx.Response:
        fmt = json.loads(request.content)["response_format"]["type"]
        formats.append(fmt)
        if fmt == "json_schema":
            return httpx.Response(400, json={"error": "bad schema"})
        return httpx.Response(200, json=OK)

    client, budget = make(handler)
    model = "openai/gpt-6.1-sol"
    assert (await client.chat(model, MSGS, json_mode=True, schema=_Shape)).text == "hola"
    assert formats == ["json_schema", "json_object"]
    assert (model, "_Shape") in SCHEMA_REJECTED
    await client.chat(model, MSGS, json_mode=True, schema=_Shape)
    assert formats == ["json_schema", "json_object", "json_object"]  # remembered, no new probe
    assert budget.reserved == 0


async def test_a_400_that_is_not_about_the_schema_is_not_remembered():
    client, _ = make(lambda _r: httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(LlmError):
        await client.chat("anthropic/claude-haiku-5.5", MSGS, json_mode=True, schema=_Shape)
    assert not SCHEMA_REJECTED
