"""OpenRouter chat client with per-call timeout, bounded retries and cost reservation."""

import asyncio
import base64
import io
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from PIL import Image
from pydantic import BaseModel, ValidationError

from aprendiz.agent.budget import Budget
from aprendiz.agent.config import (
    DEFAULT_PRICE_USD_PER_MTOK,
    MODEL_PRICES_USD_PER_MTOK,
    RESERVE_MARGIN,
    AgentSettings,
)
from aprendiz.config import Settings

log = logging.getLogger(__name__)

ImageInput = Image.Image | bytes
Message = dict[str, str]
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LlmError(Exception):
    """The model call failed and will not be retried."""


class ModerationBlocked(LlmError):
    """The provider's content filter rejected the request; the same model will reject it again."""


class DeadlineExceeded(LlmError):
    """No time is left before the caller's deadline."""


@dataclass(frozen=True)
class ChatResult:
    text: str
    cost_usd: float


class Llm(Protocol):
    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        images: list[ImageInput] | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
        deadline: float | None = None,
    ) -> ChatResult:
        """`deadline` is a time.monotonic() instant the call must not outlive."""
        ...


class _Usage(BaseModel):
    cost: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class _Message(BaseModel):
    content: str | None = None


class _Choice(BaseModel):
    message: _Message


class _Completion(BaseModel):
    choices: list[_Choice]
    usage: _Usage | None = None


def _png_data_url(image: ImageInput) -> str:
    if isinstance(image, Image.Image):
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        raw = buf.getvalue()
    else:
        raw = image
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def build_payload(
    model: str,
    messages: list[Message],
    images: list[ImageInput] | None,
    json_mode: bool,
    max_tokens: int,
) -> dict[str, Any]:
    """Images are attached to the last user message."""
    wire: list[dict[str, Any]] = [dict(m) for m in messages]
    if images:
        last = next((m for m in reversed(wire) if m["role"] == "user"), None)
        if last is None:
            raise ValueError("images need a user message to attach to")
        last["content"] = [
            {"type": "text", "text": last["content"]},
            *({"type": "image_url", "image_url": {"url": _png_data_url(i)}} for i in images),
        ]
    payload: dict[str, Any] = {
        "model": model,
        "messages": wire,
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def estimate_max_cost(
    agent: AgentSettings,
    model: str,
    messages: list[Message],
    n_images: int,
    max_tokens: int,
) -> float:
    """Worst-case cost of one call: approximated input tokens plus the full output allowance."""
    price_in, price_out = MODEL_PRICES_USD_PER_MTOK.get(model, DEFAULT_PRICE_USD_PER_MTOK)
    chars = sum(len(m["content"]) for m in messages)
    in_tokens = chars / agent.CHARS_PER_TOKEN + n_images * agent.IMAGE_TOKENS
    return RESERVE_MARGIN * (in_tokens * price_in + max_tokens * price_out) / 1_000_000


class OpenRouterClient:
    """One instance per task: it carries that task's Budget."""

    def __init__(
        self,
        settings: Settings,
        agent: AgentSettings,
        budget: Budget,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = settings.OPENROUTER_API_KEY
        self._agent = agent
        self._budget = budget
        self._http = http or httpx.AsyncClient()
        self._owns_http = http is None

    def __repr__(self) -> str:
        return "OpenRouterClient()"

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        images: list[ImageInput] | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
        deadline: float | None = None,
    ) -> ChatResult:
        if deadline is not None and deadline - time.monotonic() < self._agent.MIN_CALL_S:
            raise DeadlineExceeded("not enough time left to start a model call")
        out_tokens = max_tokens or self._agent.LLM_MAX_OUTPUT_TOKENS
        payload = build_payload(model, messages, images, json_mode, out_tokens)
        estimate = estimate_max_cost(self._agent, model, messages, len(images or []), out_tokens)
        reservation = self._budget.reserve(estimate)
        try:
            completion = await self._post_with_retries(model, payload, deadline)
        except BaseException:
            self._budget.release(reservation)
            raise
        cost = self._actual_cost(model, completion, estimate)
        self._budget.settle(reservation, cost)
        text = completion.choices[0].message.content or ""
        log.info("llm call", extra={"model": model, "cost_usd": cost, "chars": len(text)})
        return ChatResult(text=text, cost_usd=cost)

    def _actual_cost(self, model: str, completion: _Completion, estimate: float) -> float:
        usage = completion.usage
        if usage is not None and usage.cost is not None:
            return usage.cost
        # Never under-count: without usage.cost, charge the reserved worst case.
        log.warning("llm response without usage.cost", extra={"model": model})
        return estimate

    async def _post_with_retries(
        self, model: str, payload: dict[str, Any], deadline: float | None
    ) -> _Completion:
        attempts = self._agent.LLM_RETRIES + 1
        for attempt in range(attempts):
            timeout = self._attempt_timeout(deadline)
            started = time.monotonic()
            try:
                return await self._post_once(payload, timeout)
            except _Retryable as exc:
                log.warning(
                    "llm attempt failed",
                    extra={
                        "model": model,
                        "attempt": attempt + 1,
                        "reason": exc.reason,
                        "elapsed_s": round(time.monotonic() - started, 2),
                    },
                )
                if attempt == attempts - 1:
                    raise LlmError(
                        f"model call failed after {attempts} attempts: {exc.reason}"
                    ) from exc
                await self._backoff(attempt, deadline)
        raise AssertionError("unreachable")

    def _attempt_timeout(self, deadline: float | None) -> float:
        timeout = self._agent.LLM_TIMEOUT_S
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise DeadlineExceeded("deadline reached before the model call")
        return timeout

    async def _backoff(self, attempt: int, deadline: float | None) -> None:
        delay = self._agent.LLM_BACKOFF_S * (2**attempt)
        if deadline is not None and time.monotonic() + delay >= deadline:
            raise DeadlineExceeded("no time left to retry the model call")
        await asyncio.sleep(delay)

    async def _post_once(self, payload: dict[str, Any], timeout: float) -> _Completion:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with asyncio.timeout(timeout):
                response = await self._http.post(
                    self._agent.OPENROUTER_URL, json=payload, headers=headers, timeout=timeout
                )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise _Retryable(f"timeout after {timeout:.1f}s") from exc
        if response.status_code in _RETRYABLE_STATUS:
            raise _Retryable(f"HTTP {response.status_code}")
        if response.status_code == 403 and b"prohibited content" in response.content.lower():
            raise ModerationBlocked("request blocked by provider moderation")
        if response.status_code != 200:
            raise LlmError(f"model call rejected: HTTP {response.status_code}")
        try:
            completion = _Completion.model_validate_json(response.content)
        except ValidationError as exc:
            raise LlmError("model response does not match the expected shape") from exc
        if not completion.choices:
            raise LlmError("model response has no choices")
        return completion


class _Retryable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
