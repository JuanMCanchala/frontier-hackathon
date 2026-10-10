"""Per-task trace: summary.json contract with the harness, one JPEG and one JSONL line per step."""

import json
import logging
import os
import tempfile
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import BaseModel

from aprendiz.agent.llm import ChatResult, ImageInput, Llm, Message
from aprendiz.agent.structured import REASK_PREFIX
from aprendiz.redact import redact

log = logging.getLogger(__name__)


class _RoleLlm:
    """Wraps an Llm so every successful call is recorded under a role."""

    def __init__(self, inner: Llm, trace: "TaskTrace", rol: str) -> None:
        self._inner = inner
        self._trace = trace
        self._rol = rol

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        images: list[ImageInput] | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
        deadline: float | None = None,
        schema: type[BaseModel] | None = None,
    ) -> ChatResult:
        started = time.monotonic()
        result = await self._inner.chat(
            model,
            messages,
            images=images,
            json_mode=json_mode,
            max_tokens=max_tokens,
            deadline=deadline,
            schema=schema,
        )
        if messages[-1]["content"].startswith(REASK_PREFIX):
            self._trace.reasks += 1
        self._trace.calls.append(
            {
                "rol": self._rol,
                "modelo": model,
                "latencia_s": round(time.monotonic() - started, 3),
                "costo_usd": result.cost_usd,
            }
        )
        return result


class TaskTrace:
    def __init__(
        self, traces_dir: str, task_id: str, secrets: Iterable[str], shot_size: tuple[int, int]
    ) -> None:
        self.dir = Path(traces_dir) / Path(task_id).name
        self.task_id = task_id
        self._secrets = [s for s in secrets if s]
        self._shot_size = shot_size
        self._started = time.monotonic()
        self.calls: list[dict[str, Any]] = []
        self.stability_waits: list[float] = []
        self.steps = 0
        self.reasks = 0
        self.writes_authorized = 0
        self.writes_denied = 0

    def llm(self, inner: Llm, rol: str) -> Llm:
        return _RoleLlm(inner, self, rol)

    @property
    def cost_usd(self) -> float:
        return sum(c["costo_usd"] for c in self.calls)

    def record_step(self, shot: Image.Image | None, record: dict[str, Any]) -> None:
        """Best effort: a trace failure must never fail the task."""
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            name = f"{record['paso']:03d}.jpg"
            if shot is not None:
                shot.convert("RGB").resize(self._shot_size).save(self.dir / name, "JPEG")
                record = {**record, "captura": name}
            line = redact(json.dumps(record, ensure_ascii=False, default=str), self._secrets)
            with (self.dir / "pasos.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            log.warning("could not write step trace", exc_info=True)

    def write_summary(
        self, estado: str, motivo_fin: str, verificado: bool, now: float | None = None
    ) -> None:
        data = {
            "task_id": self.task_id,
            "estado": estado,
            "costo_usd": self.cost_usd,
            "pasos": self.steps,
            "llamadas": self.calls,
            "llamadas_por_rol": dict(Counter(c["rol"] for c in self.calls)),
            "reintentos_respuesta_invalida": self.reasks,
            "esperas_estabilidad_s": self.stability_waits,
            "escrituras_autorizadas": self.writes_authorized,
            "escrituras_denegadas": self.writes_denied,
            "motivo_fin": motivo_fin,
            "verificado": verificado,
            "duracion_s": round((now or time.monotonic()) - self._started, 3),
        }
        text = redact(json.dumps(data, ensure_ascii=False), self._secrets)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".resumen-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.dir / "resumen.json")
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            log.warning("could not write resumen.json", exc_info=True)
