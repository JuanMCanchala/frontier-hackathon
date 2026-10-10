"""Schemas at the service boundary and between the supervisor and the runner."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, StringConstraints, field_validator

from aprendiz.vnc import Lease, VncClient

Estado = Literal["en_curso", "terminada", "fallida"]
EstadoFinal = Literal["terminada", "fallida"]


class Escritorio(BaseModel):
    url: str
    password: str = Field(repr=False)

    @field_validator("url")
    @classmethod
    def _websocket_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("ws", "wss") or not parts.hostname:
            raise ValueError("url must be a ws:// or wss:// URL with a host")
        return value

    @property
    def identity(self) -> str:
        """Normalized host:port; scheme, path and query do not identify a desktop."""
        parts = urlsplit(self.url)
        port = parts.port or (443 if parts.scheme == "wss" else 80)
        return f"{(parts.hostname or '').lower()}:{port}"


class TaskRequest(BaseModel):
    tarea: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    escritorio: Escritorio


class TaskCreated(BaseModel):
    task_id: str


class TaskStatus(BaseModel):
    estado: Estado
    resumen: str


class RunResult(BaseModel):
    estado: EstadoFinal
    resumen: str


@dataclass(frozen=True)
class RunContext:
    """Everything a runner receives. Both deadlines are time.monotonic() instants taken from
    the POST: `actor_deadline` is when the agent must stop acting (leaving time to verify
    and close), `deadline` is the hard cut where the lease is revoked.

    Runners must build their VncClient with `lease` and pass it to `register_client`
    so the supervisor can force-close it at the deadline.
    """

    task_id: str
    tarea: str
    escritorio: Escritorio
    actor_deadline: float
    deadline: float
    lease: Lease
    register_client: Callable[[VncClient], None]
