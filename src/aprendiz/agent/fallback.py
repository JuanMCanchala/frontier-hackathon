"""ask_json with a one-time fallback model when the provider's moderation blocks the request."""

import logging

from pydantic import BaseModel

from aprendiz.agent.llm import ImageInput, Llm, Message, ModerationBlocked
from aprendiz.agent.structured import ask_json

log = logging.getLogger(__name__)


async def ask_json_fb[M: BaseModel](
    llm: Llm,
    model: str,
    fallback: str | None,
    messages: list[Message],
    schema: type[M],
    *,
    images: list[ImageInput] | None = None,
    max_tokens: int,
    deadline: float | None = None,
) -> M:
    try:
        return await ask_json(
            llm, model, messages, schema, images=images, max_tokens=max_tokens, deadline=deadline
        )
    except ModerationBlocked:
        if not fallback or fallback == model:
            raise
        log.warning("moderation blocked the request", extra={"model": model, "fallback": fallback})
        return await ask_json(
            llm, fallback, messages, schema, images=images, max_tokens=max_tokens, deadline=deadline
        )
