"""The app's "Today": read from the header as text by a model, parsed and computed by code."""

import calendar
import logging
import re
import unicodedata
from datetime import date, timedelta

from PIL import Image
from pydantic import BaseModel, ConfigDict

from aprendiz.agent.fallback import ask_json_fb
from aprendiz.agent.llm import Llm, LlmError, Message
from aprendiz.agent.structured import InvalidReply

log = logging.getLogger(__name__)

TODAY_SYSTEM_PROMPT = """\
You read a desktop app screenshot. Find the current date the app shows in its header, usually
labelled "Hoy" or "Today". Reply with ONE JSON object: {"texto": "<the date text, copied exactly>"}
or {"texto": null} if no such date is visible. Copy the characters as shown; do not reformat,
convert or compute anything.
"""

_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}  # fmt: skip
_WEEKDAYS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6,
}  # fmt: skip
_NUMERIC = re.compile(r"(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})")
_WORDED = re.compile(r"(\d{1,2})\s+(?:de\s+)?([a-z]+)\.?\s+(?:de\s+)?(\d{4})")
_OFFSET = re.compile(r"hoy_app(?:\s*([+-])\s*(\d+))?")
_WEEKDAY_EXPR = re.compile(r"(proximo|anterior)_([a-z]+)")
_ABSOLUTE = re.compile(
    r"(inicio_mes|fin_mes|inicio_an(?:io|o)|fin_an(?:io|o)|fecha):(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?"
)


class ParamError(ValueError):
    """A plan parameter expression the code cannot resolve."""


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    texto: str | None


def _fold(text: str) -> str:
    stripped = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in stripped if unicodedata.category(c) != "Mn").strip()


def parse_date_text(text: str) -> date | None:
    """Parse the date formats a Spanish-language app shows. Numeric dates are day-first."""
    folded = _fold(text)
    try:
        numeric = _NUMERIC.search(folded)
        if numeric:
            a, b, c = (int(g) for g in numeric.groups())
            if len(numeric.group(1)) == 4:
                return date(a, b, c)
            return date(c, b, a)
        worded = _WORDED.search(folded)
        if worded and worded.group(2) in _MONTHS:
            return date(int(worded.group(3)), _MONTHS[worded.group(2)], int(worded.group(1)))
        # "10 oct 2026"
        if worded:
            for name, month in _MONTHS.items():
                if name.startswith(worded.group(2)) and len(worded.group(2)) >= 3:
                    return date(int(worded.group(3)), month, int(worded.group(1)))
    except ValueError:
        return None
    return None


async def read_today(
    llm: Llm,
    screenshot: Image.Image,
    *,
    model: str,
    fallback: str | None = None,
    deadline: float | None = None,
) -> date | None:
    """None when the date is not visible or cannot be parsed as a real date."""
    messages: list[Message] = [
        {"role": "system", "content": TODAY_SYSTEM_PROMPT},
        {"role": "user", "content": "Read the date."},
    ]
    try:
        reply = await ask_json_fb(
            llm,
            model,
            fallback,
            messages,
            _Reply,
            images=[screenshot],
            max_tokens=60,
            deadline=deadline,
        )
    except (LlmError, InvalidReply) as exc:
        log.warning("could not read today", extra={"error": str(exc)})
        return None
    if reply.texto is None:
        return None
    parsed = parse_date_text(reply.texto)
    if parsed is None:
        log.warning("unparseable date text", extra={"texto": reply.texto})
    return parsed


def _resolve_absolute(expr: str, expression: str) -> date | None:
    """Period and absolute-date expressions: they need no reading of the screen."""
    match = _ABSOLUTE.fullmatch(expr)
    if match is None:
        return None
    kind, year, month, day = match.group(1), int(match.group(2)), match.group(3), match.group(4)
    try:
        if kind.endswith("_mes") and month and not day:
            last = calendar.monthrange(year, int(month))[1]
            return date(year, int(month), 1 if kind == "inicio_mes" else last)
        if kind.startswith(("inicio_an", "fin_an")) and not month:
            return date(year, 1, 1) if kind.startswith("inicio") else date(year, 12, 31)
        if kind == "fecha" and month and day:
            return date(year, int(month), int(day))
    except (ValueError, calendar.IllegalMonthError) as exc:
        raise ParamError(f"invalid date in parameter expression: {expression!r}") from exc
    raise ParamError(f"malformed parameter expression: {expression!r}")


def needs_today(parametros: dict[str, str]) -> bool:
    """Whether any parameter depends on the app's "Today" (absolute periods and dates do not)."""
    return any(_ABSOLUTE.fullmatch(_fold(expr)) is None for expr in parametros.values())


_LAST_DAYS = re.compile(r"ultimos_dias:(\d{1,3})")


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _named(name: str, hoy: date) -> date | None:
    """Named relative dates. Weeks run Monday to Sunday."""
    week = hoy - timedelta(days=hoy.weekday())
    prev_month_end = _month_start(hoy) - timedelta(days=1)
    table = {
        "hoy": hoy,
        "ayer": hoy - timedelta(days=1),
        "anteayer": hoy - timedelta(days=2),
        "manana": hoy + timedelta(days=1),
        "semana_actual_inicio": week,
        "semana_actual_fin": week + timedelta(days=6),
        "semana_pasada_inicio": week - timedelta(days=7),
        "semana_pasada_fin": week - timedelta(days=1),
        "mes_actual_inicio": _month_start(hoy),
        "mes_actual_fin": _month_end(hoy),
        "mes_pasado_inicio": _month_start(prev_month_end),
        "mes_pasado_fin": prev_month_end,
        "anio_actual_inicio": date(hoy.year, 1, 1),
        "anio_actual_fin": date(hoy.year, 12, 31),
    }
    return table.get(name)


def _resolve(expression: str, hoy: date | None) -> date:
    expr = _fold(expression)
    absolute = _resolve_absolute(expr, expression)
    if absolute is not None:
        return absolute
    if hoy is None:
        raise ParamError(f"parameter {expression!r} needs the app's date, which was not read")
    named = _named(expr, hoy)
    if named is not None:
        return named
    last_days = _LAST_DAYS.fullmatch(expr)
    if last_days:  # start of "the last N days"; the end is "hoy"
        return hoy - timedelta(days=max(int(last_days.group(1)), 1) - 1)
    if expr in _WEEKDAYS:  # "el lunes": the most recent one strictly before today
        return hoy - timedelta(days=(hoy.weekday() - _WEEKDAYS[expr] - 1) % 7 + 1)
    offset = _OFFSET.fullmatch(expr)
    if offset:
        days = int(offset.group(2) or 0)
        return hoy + timedelta(days=-days if offset.group(1) == "-" else days)
    weekday = _WEEKDAY_EXPR.fullmatch(expr)
    if weekday and weekday.group(2) in _WEEKDAYS:
        target = _WEEKDAYS[weekday.group(2)]
        if weekday.group(1) == "proximo":
            return hoy + timedelta(days=(target - hoy.weekday() - 1) % 7 + 1)
        return hoy - timedelta(days=(hoy.weekday() - target - 1) % 7 + 1)
    raise ParamError(f"unsupported parameter expression: {expression!r}")


def resolve_params(parametros: dict[str, str], hoy: date | None) -> dict[str, date]:
    """Resolve every plan parameter against the app's date; the arithmetic is code, not a model.

    "proximo_<weekday>" is the first such day strictly after today; "anterior_<weekday>" (and the
    bare weekday name) the last one strictly before. Named dates: hoy, ayer, anteayer, manana,
    semana_actual/pasada_inicio/fin (Monday to Sunday), mes_actual/pasado_inicio/fin,
    anio_actual_inicio/fin, and ultimos_dias:N (the first day of the last N days).
    """
    return {name: _resolve(expr, hoy) for name, expr in parametros.items()}
