"""Who the desktop app is, from the first screenshot of a task.

The title is copied by the model. Code does not reformat it: a later lookup
normalizes only for the key. The perceptual hash is the same 64-bit dhash the
harness uses, reimplemented here because src must not import eval.
"""

import logging

from PIL import Image
from pydantic import BaseModel, ConfigDict

from aprendiz.agent.fallback import ask_json_fb
from aprendiz.agent.llm import Llm, LlmError, Message
from aprendiz.agent.memory import AppIdentity
from aprendiz.agent.structured import InvalidReply

log = logging.getLogger(__name__)

TITLE_SYSTEM_PROMPT = """\
You read the application title from a desktop screenshot. Find it in the header: the app name
and its version, as shown. Reply with ONE JSON object: {"texto": "<copied exactly>"} or
{"texto": null} if that title is not visible. Copy the characters as shown; do not reformat,
translate, or add a version that is not on screen.
"""


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    texto: str | None


def screen_dhash(image: Image.Image) -> str:
    """64-bit difference hash as 16 hex chars. Bit = pixel brighter than its right neighbour."""
    small = image.convert("L").resize((9, 8), Image.Resampling.BOX)
    pixels = small.tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            brighter = pixels[row * 9 + col] > pixels[row * 9 + col + 1]
            bits = (bits << 1) | int(brighter)
    return f"{bits:016x}"


async def read_app_identity(
    llm: Llm,
    screenshot: Image.Image,
    *,
    model: str,
    fallback: str | None = None,
    deadline: float | None = None,
) -> AppIdentity | None:
    """None when the header title cannot be read. The caller then leaves memory off."""
    messages: list[Message] = [
        {"role": "system", "content": TITLE_SYSTEM_PROMPT},
        {"role": "user", "content": "Read the application title."},
    ]
    try:
        reply = await ask_json_fb(
            llm,
            model,
            fallback,
            messages,
            _Reply,
            images=[screenshot],
            max_tokens=80,
            deadline=deadline,
        )
    except (LlmError, InvalidReply) as exc:
        log.warning("could not read app title", extra={"error": type(exc).__name__})
        return None
    if reply.texto is None or not reply.texto.strip():
        log.warning("app title not visible")
        return None
    return AppIdentity(title=reply.texto, screen_hash=screen_dhash(screenshot))
