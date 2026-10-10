"""Deterministic summary writer. The model supplies values; this module writes the sentences."""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from aprendiz.redact import redact

# A grouped integer part ("1.234.567", "1,234", "1 234") with an optional decimal tail.
_GROUPED = re.compile(r"^(-?\d{1,3}(?:[.,\u00a0\u202f' ]\d{3})+)(?:([.,])(\d+))?$")
_PLAIN_DECIMAL = re.compile(r"^-?\d+(?:[.,]\d+)?$")


@dataclass(frozen=True)
class TaskOutcome:
    estado: Literal["terminada", "fallida"]
    respuesta: list[str] = field(default_factory=list)  # values in the order asked
    fuente: str | None = None  # where a number came from (app total, paginated count)
    hechos: list[str] = field(default_factory=list)  # what was done, for writes
    motivo: str | None = None  # why it failed
    datos_motivo: list[str] = field(default_factory=list)  # facts that explain the failure


def normalize_number(value: str) -> str:
    """Drop thousands separators from a numeric value; leave anything else untouched.

    A lone separator followed by exactly three digits ("1.234") is read as thousands,
    since the apps we target show integers that way; "12,5" and "3.14" stay decimals.
    """
    text = value.strip()
    grouped = _GROUPED.match(text)
    if grouped:
        integer = re.sub(r"\D", "", grouped.group(1))
        sign = "-" if text.startswith("-") else ""
        decimals = grouped.group(3)
        return f"{sign}{integer}.{decimals}" if decimals else f"{sign}{integer}"
    if _PLAIN_DECIMAL.match(text):
        return text.replace(",", ".")
    return text


def build_summary(outcome: TaskOutcome, secretos: Iterable[str]) -> str:
    """Answer line first with nothing before it, so the first number is the answer."""
    lines: list[str] = []
    if outcome.respuesta:
        lines.append("respuesta: " + "; ".join(normalize_number(v) for v in outcome.respuesta))
    if outcome.estado == "terminada":
        lines.extend(outcome.hechos or ([] if lines else ["tarea completada"]))
    else:
        lines.append(f"fallida: {outcome.motivo or 'no se pudo completar la tarea'}")
        if outcome.datos_motivo:
            lines.append("datos: " + "; ".join(outcome.datos_motivo))
    if outcome.fuente:
        lines.append(f"fuente: {outcome.fuente}")
    return redact("\n".join(lines), secretos)
