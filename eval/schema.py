"""Schema of an evaluation test file. Validated before anything is sent anywhere: the
event's /__check answers pass:true for schema-less JSON, which would be a false green."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError


class DiffItem(BaseModel):
    entidad: str
    id: str
    campo: str
    accion: Literal["create", "update", "delete", "export"]
    valor: Any


class Prueba(BaseModel):
    id: str = Field(min_length=1)
    instruccion: str = Field(min_length=1)
    estado_esperado: Literal["terminada", "fallida"]
    diff_esperado: list[DiffItem]
    lista_blanca: list[Any]
    nivel: str | None = None
    respuesta_esperada: str | None = None
    claves_razon: list[str] | None = None


@dataclass(frozen=True)
class LoadedTest:
    """A test file as read from disk. `prueba` is None when it did not validate."""

    source: str
    raw: Any
    prueba: Prueba | None
    error: str | None

    @property
    def test_id(self) -> str:
        if self.prueba is not None:
            return self.prueba.id
        if isinstance(self.raw, dict) and isinstance(self.raw.get("id"), str):
            return self.raw["id"]
        return Path(self.source).stem


def describe_errors(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '<raíz>'}: {e['msg']}" for e in error.errors()
    )


def parse_prueba(raw: Any, source: str) -> LoadedTest:
    try:
        return LoadedTest(source, raw, Prueba.model_validate(raw), None)
    except ValidationError as exc:
        return LoadedTest(source, raw, None, describe_errors(exc))


def load_dir(directory: Path) -> list[LoadedTest]:
    loaded = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            loaded.append(LoadedTest(str(path), None, None, f"no se pudo leer: {exc}"))
            continue
        loaded.append(parse_prueba(raw, str(path)))
    return loaded
