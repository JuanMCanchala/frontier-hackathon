"""Best-effort export of closed task traces and harness scores to Langfuse.

Usage: uv run --env-file .env python -m eval.langfuse_export --resultados RUTA [--traces DIR]
Credentials come from the environment (LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY).
"""

import argparse
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from aprendiz.config import Settings
from aprendiz.redact import redact

log = logging.getLogger("eval.langfuse_export")

INGESTION_PATH = "/api/public/ingestion"
# The ingestion API rejects requests above 3.5 MB; keep a wide margin for the envelope.
MAX_REQUEST_BYTES = 3_000_000
CLOSED_STATES = {"terminada", "fallida"}
SUMMARY_METADATA_KEYS = [
    "estado",
    "motivo_fin",
    "verificado",
    "pasos",
    "duracion_s",
    "costo_usd",
    "escrituras_autorizadas",
    "escrituras_denegadas",
    "esperas_estabilidad_s",
]
RESULT_METADATA_KEYS = ["reglas", "motivos", "categoria", "repeticion"]
BOOLEAN_SCORES = ["exito", "falso_exito", "destructiva"]
NUMERIC_SCORES = ["costo_usd", "duracion_s"]
SECRET_ENV_VARS = ["DESKTOP_PASSWORD", "DESKTOP_TOKEN", "TEAM_TOKEN", "OPENROUTER_API_KEY"]
_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "aprendiz/langfuse-export")

Event = dict[str, Any]


@dataclass(frozen=True)
class LangfuseConfig:
    host: str
    public_key: str
    secret_key: str
    timeout_s: float = 5.0
    retries: int = 2
    backoff_s: float = 0.5

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.public_key and self.secret_key)

    @property
    def ingestion_url(self) -> str:
        return self.host.rstrip("/") + INGESTION_PATH

    @classmethod
    def from_settings(cls, settings: Settings) -> "LangfuseConfig":
        return cls(
            host=settings.LANGFUSE_HOST,
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            timeout_s=settings.LANGFUSE_TIMEOUT_S,
            retries=settings.LANGFUSE_RETRIES,
        )


def load_config() -> LangfuseConfig:
    """Settings also require the agent secrets; without them the export is simply disabled."""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        fields = [".".join(str(p) for p in err["loc"]) for err in exc.errors()]
        log.warning("invalid settings, langfuse export disabled", extra={"fields": fields})
        return LangfuseConfig(host="", public_key="", secret_key="")
    return LangfuseConfig.from_settings(settings)


# ---- client ----------------------------------------------------------------


class _Retryable(Exception):
    def __init__(self, status: int | None, error: str) -> None:
        super().__init__(error)
        self.status = status
        self.error = error


class LangfuseClient:
    def __init__(
        self,
        config: LangfuseConfig,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._transport = transport
        self._sleep = sleep

    def send(self, batch: list[Event]) -> bool:
        """Never raises: any failure is logged and reported as False."""
        if not self._config.enabled:
            log.info("langfuse export skipped", extra={"reason": "not configured"})
            return False
        auth = httpx.BasicAuth(self._config.public_key, self._config.secret_key)
        with httpx.Client(
            auth=auth, timeout=self._config.timeout_s, transport=self._transport
        ) as http:
            return self._send_with_retries(http, {"batch": batch})

    def _send_with_retries(self, http: httpx.Client, payload: dict[str, Any]) -> bool:
        attempts = self._config.retries + 1
        for attempt in range(attempts):
            try:
                return self._post_once(http, payload, attempt + 1)
            except _Retryable as exc:
                if attempt == attempts - 1:
                    self._log_failure(exc.status, attempts, exc.error)
                    return False
                self._sleep(self._config.backoff_s * (2**attempt))
        raise AssertionError("unreachable")

    def _post_once(self, http: httpx.Client, payload: dict[str, Any], attempt: int) -> bool:
        try:
            response = http.post(self._config.ingestion_url, json=payload)
        except httpx.RequestError as exc:
            raise _Retryable(None, type(exc).__name__) from exc
        status = response.status_code
        if status >= 500 or status == 429:
            raise _Retryable(status, f"HTTP {status}")
        if status not in (200, 207):
            self._log_failure(status, attempt, f"HTTP {status}: {response.text[:200]}")
            return False
        return _report_partial_errors(response)

    @staticmethod
    def _log_failure(status: int | None, attempts: int, error: str) -> None:
        log.warning(
            "langfuse export failed",
            extra={"status": status, "attempts": attempts, "error": error},
        )


def _report_partial_errors(response: httpx.Response) -> bool:
    """Invalid events come back inside a 207, so a 2xx alone does not mean they were stored."""
    try:
        errors = response.json().get("errors") or []
        first_error = errors[0].get("message") if errors else None
    except (ValueError, AttributeError) as exc:
        log.warning("langfuse response unreadable", extra={"error": type(exc).__name__})
        return False
    if not errors:
        return True
    log.warning(
        "langfuse rejected events", extra={"errors": len(errors), "first_error": first_error}
    )
    return False


# ---- batch -----------------------------------------------------------------


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds")


def _stable_id(task_id: str, key: str) -> str:
    """Deterministic ids make a re-export upsert the same objects instead of duplicating them."""
    return str(uuid.uuid5(_ID_NAMESPACE, f"{task_id}/{key}"))


def _compact(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if v is not None}


def _event(kind: str, body: dict[str, Any]) -> Event:
    return {
        "id": str(uuid.uuid4()),
        "type": kind,
        "timestamp": _iso(datetime.now(UTC)),
        "body": _compact(body),
    }


def _redact_tree(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, list):
        return [_redact_tree(v, secrets) for v in value]
    if isinstance(value, dict):
        return {k: _redact_tree(v, secrets) for k, v in value.items()}
    return value


def _read_summary(trace_dir: Path) -> dict[str, Any] | None:
    path = trace_dir / "resumen.json"
    if not path.is_file():
        log.info("trace not closed, skipped", extra={"trace_dir": str(trace_dir)})
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning(
            "unreadable resumen.json",
            extra={"trace_dir": str(trace_dir), "error": f"{type(exc).__name__}: {exc}"},
        )
        return None
    if not isinstance(data, dict) or data.get("estado") not in CLOSED_STATES:
        log.info("trace not closed, skipped", extra={"trace_dir": str(trace_dir)})
        return None
    return data


def _read_steps(trace_dir: Path) -> list[dict[str, Any]]:
    path = trace_dir / "pasos.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        log.warning(
            "unreadable pasos.jsonl",
            extra={"trace_dir": str(trace_dir), "error": f"{type(exc).__name__}: {exc}"},
        )
        return []
    steps = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            log.warning("malformed step line", extra={"trace_dir": str(trace_dir), "line": number})
            continue
        if isinstance(record, dict):
            steps.append(record)
    return steps


def _trace_event(
    task_id: str,
    summary: dict[str, Any],
    result: dict[str, Any] | None,
    session_id: str,
    base_time: datetime,
) -> Event:
    result = result or {}
    metadata = {k: summary[k] for k in SUMMARY_METADATA_KEYS if k in summary}
    metadata |= {k: result[k] for k in RESULT_METADATA_KEYS if k in result}
    categoria = result.get("categoria")
    return _event(
        "trace-create",
        {
            "id": task_id,
            "timestamp": _iso(base_time),
            "name": result.get("id") or "tarea",
            "sessionId": session_id,
            "output": result.get("resumen"),
            "metadata": metadata,
            "tags": [categoria] if categoria else None,
        },
    )


def _step_events(task_id: str, steps: list[dict[str, Any]], base_time: datetime) -> list[Event]:
    # The local trace has no wall-clock time per step, so steps are laid end to end from the
    # base time using their measured duration.
    events, start = [], base_time
    for index, record in enumerate(steps, start=1):
        number = record.get("paso", index)
        action = record.get("accion") or {}
        end = start + timedelta(seconds=float((record.get("tiempos_s") or {}).get("total", 0)))
        body = {
            "id": _stable_id(task_id, f"paso-{number}"),
            "traceId": task_id,
            "name": f"paso {number} {action.get('tipo', 'sin_accion')}",
            "startTime": _iso(start),
            "endTime": _iso(end),
            "input": record.get("accion"),
            "output": {k: record.get(k) for k in ("clasificacion", "veredicto", "efecto")},
            "metadata": {k: record.get(k) for k in ("fase", "tiempos_s", "nota_al_actor")},
        }
        events.append(_event("span-create", body))
        start = end
    return events


def _generation_events(
    task_id: str, calls: list[dict[str, Any]], base_time: datetime
) -> list[Event]:
    # Calls carry only their latency, not a wall-clock start, so they are laid end to end too.
    events, start = [], base_time
    for index, call in enumerate(calls, start=1):
        end = start + timedelta(seconds=float(call.get("latencia_s") or 0))
        body = {
            "id": _stable_id(task_id, f"llamada-{index}"),
            "traceId": task_id,
            "name": call.get("rol"),
            "startTime": _iso(start),
            "endTime": _iso(end),
            "model": call.get("modelo"),
            "costDetails": {"total": float(call.get("costo_usd") or 0)},
            "metadata": {"latencia_s": call.get("latencia_s")},
        }
        events.append(_event("generation-create", body))
        start = end
    return events


def _score(task_id: str, name: str, value: float, data_type: str, comment: str | None) -> Event:
    return _event(
        "score-create",
        {
            "id": _stable_id(task_id, f"score-{name}"),
            "traceId": task_id,
            "name": name,
            "value": value,
            "dataType": data_type,
            "comment": comment,
        },
    )


def _score_events(task_id: str, result: dict[str, Any]) -> list[Event]:
    motivos = result.get("motivos") or []
    comments = {"exito": "; ".join(motivos) if motivos else None}
    events = [
        _score(task_id, name, 1 if result.get(name) else 0, "BOOLEAN", comments.get(name))
        for name in BOOLEAN_SCORES
    ]
    events += [
        _score(task_id, name, float(result[name]), "NUMERIC", None)
        for name in NUMERIC_SCORES
        if result.get(name) is not None
    ]
    return events


def build_task_batch(
    trace_dir: Path,
    result: dict[str, Any] | None,
    session_id: str,
    base_time: datetime,
    secrets: list[str],
) -> list[Event] | None:
    """Events for one closed task, redacted; None when the trace is still open or unreadable."""
    summary = _read_summary(trace_dir)
    if summary is None:
        return None
    task_id = str(summary.get("task_id") or trace_dir.name)
    batch = [_trace_event(task_id, summary, result, session_id, base_time)]
    batch += _step_events(task_id, _read_steps(trace_dir), base_time)
    batch += _generation_events(task_id, summary.get("llamadas") or [], base_time)
    if result:
        batch += _score_events(task_id, result)
    return _redact_tree(batch, secrets)


def split_by_size(batch: list[Event], limit: int = MAX_REQUEST_BYTES) -> Iterator[list[Event]]:
    chunk: list[Event] = []
    size = 0
    for event in batch:
        event_size = len(json.dumps(event, ensure_ascii=False).encode())
        if chunk and size + event_size > limit:
            yield chunk
            chunk, size = [], 0
        chunk.append(event)
        size += event_size
    if chunk:
        yield chunk


# ---- results file ----------------------------------------------------------


def _read_results(results_path: Path) -> tuple[datetime, list[dict[str, Any]]] | None:
    try:
        data = json.loads(results_path.read_text(encoding="utf-8"))
        # The harness writes a naive local timestamp; astimezone() pins it to this machine's zone.
        base_time = datetime.fromisoformat(data["fecha"]).astimezone()
        results = [r for r in data["resultados"] if isinstance(r, dict)]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning(
            "could not read results file",
            extra={"path": str(results_path), "error": f"{type(exc).__name__}: {exc}"},
        )
        return None
    return base_time, results


def _export_task(
    client: LangfuseClient,
    trace_dir: Path,
    result: dict[str, Any],
    session_id: str,
    base_time: datetime,
    secrets: list[str],
) -> bool:
    """True when the task was sent or had nothing to send (open trace)."""
    try:
        batch = build_task_batch(trace_dir, result, session_id, base_time, secrets)
    except (ValueError, TypeError, AttributeError) as exc:
        log.warning(
            "could not build langfuse batch",
            extra={"trace_dir": str(trace_dir), "error": f"{type(exc).__name__}: {exc}"},
        )
        return False
    sent = [client.send(chunk) for chunk in split_by_size(batch or [])]
    return all(sent)


def export_results_file(
    results_path: Path,
    traces_dir: Path,
    config: LangfuseConfig,
    secrets: list[str],
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Sends every closed trace of a harness results file. Never raises; True if all went out."""
    if not config.enabled:
        log.info("langfuse export skipped", extra={"reason": "not configured"})
        return False
    loaded = _read_results(results_path)
    if loaded is None:
        return False
    base_time, results = loaded
    client = LangfuseClient(config, transport=transport, sleep=sleep)
    all_sent = True
    for result in results:
        task_id = result.get("task_id")
        if not task_id:
            continue
        trace_dir = traces_dir / Path(str(task_id)).name
        sent = _export_task(client, trace_dir, result, results_path.stem, base_time, secrets)
        all_sent = sent and all_sent
    return all_sent


# ---- CLI -------------------------------------------------------------------


def _secrets(config: LangfuseConfig) -> list[str]:
    from_env = [os.environ.get(name, "") for name in SECRET_ENV_VARS]
    return [s for s in [config.secret_key, config.public_key, *from_env] if s]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="eval.langfuse_export", description=__doc__)
    parser.add_argument("--resultados", required=True, type=Path, metavar="RUTA")
    parser.add_argument("--traces", type=Path, default=Path(os.environ.get("TRACES_DIR", "traces")))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    config = load_config()
    if not config.enabled:
        print(
            "ERROR: Langfuse no está configurado "
            "(faltan LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY o LANGFUSE_SECRET_KEY)",
            file=sys.stderr,
        )
        return 2
    ok = export_results_file(args.resultados, args.traces, config, _secrets(config))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
