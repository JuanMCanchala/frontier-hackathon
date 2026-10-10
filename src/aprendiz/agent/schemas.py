"""Closed schemas shared by the planner, the actors and the rest of the agent."""

import json
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aprendiz.agent.config import SCREEN_HEIGHT, SCREEN_WIDTH

TipoPlan = Literal["crear", "editar", "preguntar", "exportar", "peligrosa"]
AccionEscritura = Literal["create", "update", "delete", "export"]
TipoAccion = Literal[
    "click", "double_click", "hover", "type", "key", "scroll", "fill_form", "wait", "done", "fail"
]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Escritura(_Closed):
    entidad: str
    accion: AccionEscritura
    # field -> literal value from the task, or the name of an entry in Plan.parametros
    campos: dict[str, str] = Field(default_factory=dict)


class Plan(_Closed):
    tipo: TipoPlan
    sospechosa: bool = False
    entidad: str | None = None
    escrituras_permitidas: list[Escritura] = Field(default_factory=list)
    # name -> expression the code resolves after reading the screen, e.g. "hoy_app+1"
    parametros: dict[str, str] = Field(default_factory=dict)
    dato_pedido: list[str] = Field(default_factory=list)


class Campo(_Closed):
    x: int
    y: int
    etiqueta: str
    valor: str
    # "select": click to open, then click the option whose text is `valor`
    tipo: Literal["texto", "select"] = "texto"

    @model_validator(mode="after")
    def _on_screen(self) -> Self:
        _check_point(self.x, self.y)
        return self


class Action(_Closed):
    tipo: TipoAccion
    x: int | None = None
    y: int | None = None
    texto: str | None = None
    combo: str | None = None
    dy: int | None = None
    campos: list[Campo] | None = None
    postcondicion: str | None = None
    razon: str = ""
    respuesta: list[str] | None = None
    motivo: str | None = None
    datos_motivo: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _required_by_type(self) -> Self:
        if self.tipo in ("click", "double_click", "hover", "scroll"):
            if self.x is None or self.y is None:
                raise ValueError(f"{self.tipo} requires x and y")
            _check_point(self.x, self.y)
        if self.tipo == "scroll" and not self.dy:
            raise ValueError("scroll requires a non-zero dy")
        if self.tipo == "type" and not self.texto:
            raise ValueError("type requires texto")
        if self.tipo == "key" and not self.combo:
            raise ValueError("key requires combo")
        if self.tipo == "fill_form" and not self.campos:
            raise ValueError("fill_form requires at least one campo")
        if self.tipo == "fail" and not self.motivo:
            raise ValueError("fail requires motivo")
        return self


def _check_point(x: int, y: int) -> None:
    if not (0 <= x < SCREEN_WIDTH and 0 <= y < SCREEN_HEIGHT):
        raise ValueError(f"point ({x}, {y}) is outside the {SCREEN_WIDTH}x{SCREEN_HEIGHT} screen")


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def parse_model[M: BaseModel](model: type[M], text: str) -> M:
    """Parse a model reply into `model`. Raises ValueError with a message fit to show the model."""
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1)
    try:
        return model.model_validate(json.loads(body))
    except json.JSONDecodeError as exc:
        raise ValueError(f"reply is not valid JSON: {exc}") from exc
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors()
        )
        raise ValueError(f"reply does not match the schema: {problems}") from exc
