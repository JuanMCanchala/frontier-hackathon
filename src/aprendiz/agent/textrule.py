"""Text rule: the only text the agent may type comes from the task, the plan or code."""

from PIL import Image

from aprendiz.agent.gate import allowed_values
from aprendiz.agent.schemas import Plan
from aprendiz.agent.textmatch import fold, fragment_of


def text_allowed(plan: Plan, tarea: str, parametros: dict[str, str], text: str) -> bool:
    """Search terms may be any fragment of the task (with or without accents); everything else
    must be a plan value or a parameter the code resolved. Line breaks are never allowed: typing
    one presses Enter."""
    if not text.strip() or any(c in text for c in "\r\n\t"):
        return False
    if fragment_of(tarea, text):
        return True
    folded = fold(text)
    known = {fold(v) for v in allowed_values(plan, parametros)} | {
        fold(v) for v in parametros.values()
    }
    return folded in known


def crop_box(point: tuple[int, int], size: tuple[int, int], half_w: int, half_h: int) -> tuple:
    """A (left, top, right, bottom) box around the point, clipped to the image."""
    x, y = point
    return (
        max(0, x - half_w),
        max(0, y - half_h),
        min(size[0], x + half_w),
        min(size[1], y + half_h),
    )


def blank_region(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    masked = image.copy()
    masked.paste((0, 0, 0), box)
    return masked
