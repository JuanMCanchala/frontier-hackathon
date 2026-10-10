"""Ask a model for JSON that must match a schema, re-asking once if it does not."""

import logging

from pydantic import BaseModel

from aprendiz.agent.llm import ImageInput, Llm, Message
from aprendiz.agent.schemas import parse_model

log = logging.getLogger(__name__)

REASK_PREFIX = "Invalid: "  # starts the follow-up message of a re-ask; the trace counts these

# Reasoning models spend hidden tokens before answering: with a tight cap they stop on
# "length" and return no content at all (seen with haiku-5.5 at max_tokens=40). Only the
# tokens actually produced are billed, so a generous floor costs nothing extra.
MIN_STRUCTURED_TOKENS = 2048


class InvalidReply(Exception):
    """The model answered twice with something that does not match the schema."""


async def ask_json[M: BaseModel](
    llm: Llm,
    model: str,
    messages: list[Message],
    schema: type[M],
    *,
    images: list[ImageInput] | None = None,
    max_tokens: int,
    deadline: float | None = None,
) -> M:
    last_error = ""
    for attempt in range(2):
        result = await llm.chat(
            model,
            messages,
            images=images,
            json_mode=True,
            max_tokens=max(max_tokens, MIN_STRUCTURED_TOKENS),
            deadline=deadline,
            schema=schema,
        )
        try:
            return parse_model(schema, result.text)
        except ValueError as exc:
            last_error = str(exc)
            log.warning(
                "invalid model reply",
                extra={"schema": schema.__name__, "attempt": attempt + 1, "error": last_error},
            )
            messages = [
                *messages,
                {"role": "assistant", "content": result.text},
                {
                    "role": "user",
                    "content": f"{REASK_PREFIX}{last_error}\nReply with only valid JSON.",
                },
            ]
    raise InvalidReply(f"invalid {schema.__name__} twice: {last_error}")
