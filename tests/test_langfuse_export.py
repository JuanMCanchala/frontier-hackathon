import base64
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from eval.langfuse_export import (
    LangfuseClient,
    LangfuseConfig,
    build_task_batch,
    export_results_file,
    main,
)

TASK_ID = "task-abc"
SECRET = "pw-desk-123"
CONFIG = LangfuseConfig(host="https://lf.test/", public_key="pk-lf-test", secret_key="sk-lf-test")
DISABLED = LangfuseConfig(host="", public_key="", secret_key="")
BASE_TIME = datetime.fromisoformat("2026-10-10T12:00:00+00:00")


def summary(**over: Any) -> dict[str, Any]:
    base = {
        "task_id": TASK_ID,
        "estado": "terminada",
        "costo_usd": 0.03,
        "pasos": 2,
        "llamadas": [
            {"rol": "planner", "modelo": "m-plan", "latencia_s": 1.5, "costo_usd": 0.02},
            {"rol": "actor", "modelo": "m-act", "latencia_s": 0.5, "costo_usd": 0.01},
        ],
        "esperas_estabilidad_s": [0.6],
        "escrituras_autorizadas": 1,
        "escrituras_denegadas": 0,
        "motivo_fin": "done",
        "verificado": True,
        "duracion_s": 12.5,
    }
    return {**base, **over}


def step(n: int, **over: Any) -> dict[str, Any]:
    base = {
        "paso": n,
        "fase": "actuar",
        "accion": {"tipo": "click", "x": 10, "y": 20},
        "clasificacion": "lectura",
        "veredicto": "ok",
        "efecto": "cambio",
        "nota_al_actor": None,
        "tiempos_s": {"decidir": 1.0, "total": 2.0},
        "captura": f"{n:03d}.jpg",
    }
    return {**base, **over}


def write_trace(
    traces: Path, resumen: dict[str, Any] | None = None, steps: list[str] | None = None
) -> Path:
    directory = traces / TASK_ID
    directory.mkdir(parents=True)
    if resumen is not None:
        (directory / "resumen.json").write_text(json.dumps(resumen), encoding="utf-8")
    lines = steps if steps is not None else [json.dumps(step(1)), json.dumps(step(2))]
    (directory / "pasos.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


def resultado(**over: Any) -> dict[str, Any]:
    base = {
        "id": "E1",
        "categoria": "crear",
        "repeticion": 1,
        "task_id": TASK_ID,
        "estado": "terminada",
        "resumen": "Cliente creado",
        "exito": False,
        "falso_exito": True,
        "destructiva": False,
        "costo_usd": 0.03,
        "duracion_s": 14.0,
        "reglas": [{"nombre": "estado", "ok": True, "detalle": ""}],
        "motivos": ["check 'x' falló", "regla y: z"],
    }
    return {**base, **over}


def write_results(tmp_path: Path, resultados: list[dict[str, Any]]) -> Path:
    path = tmp_path / "20261010-120000-corrida.json"
    payload = {"fecha": "2026-10-10T12:00:00", "etiqueta": "corrida", "resultados": resultados}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class Recorder:
    def __init__(self, responses: list[httpx.Response | Exception] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = responses or []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._responses:
            reply = self._responses[min(len(self.requests), len(self._responses)) - 1]
            if isinstance(reply, Exception):
                raise reply
            return reply
        batch = json.loads(request.content)["batch"]
        successes = [{"id": e["id"], "status": 201} for e in batch]
        return httpx.Response(207, json={"successes": successes, "errors": []})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def events(self) -> list[dict[str, Any]]:
        return [e for r in self.requests for e in json.loads(r.content)["batch"]]


def never_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected HTTP call to {request.url}")


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def export(tmp_path: Path, recorder: Recorder, resultados: list[dict[str, Any]]) -> bool:
    return export_results_file(
        write_results(tmp_path, resultados),
        tmp_path / "traces",
        CONFIG,
        [SECRET],
        transport=recorder.transport,
        sleep=SleepRecorder(),
    )


def test_well_formed_batch_reaches_ingestion_endpoint(tmp_path: Path) -> None:
    write_trace(tmp_path / "traces", summary())
    recorder = Recorder()

    assert export(tmp_path, recorder, [resultado()]) is True

    request = recorder.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://lf.test/api/public/ingestion"
    expected = base64.b64encode(b"pk-lf-test:sk-lf-test").decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    events = recorder.events()
    types = [e["type"] for e in events]
    assert {"trace-create", "span-create", "generation-create", "score-create"} <= set(types)
    assert types.count("span-create") == 2 and types.count("generation-create") == 2
    assert len({e["id"] for e in events}) == len(events)
    trace = next(e["body"] for e in events if e["type"] == "trace-create")
    assert trace["id"] == TASK_ID
    assert trace["sessionId"] == "20261010-120000-corrida"
    assert trace["name"] == "E1" and trace["tags"] == ["crear"]
    assert trace["output"] == "Cliente creado"
    assert trace["metadata"]["motivo_fin"] == "done"
    assert all(e["body"]["traceId"] == TASK_ID for e in events if e["type"] != "trace-create")
    assert b"captura" not in request.content and b".jpg" not in request.content


def test_steps_and_calls_are_laid_end_to_end(tmp_path: Path) -> None:
    trace_dir = write_trace(tmp_path, summary())

    batch = build_task_batch(trace_dir, None, "s", BASE_TIME, [])

    assert batch is not None
    spans = [e["body"] for e in batch if e["type"] == "span-create"]
    assert spans[0]["name"] == "paso 1 click"
    assert spans[0]["startTime"].startswith("2026-10-10T12:00:00.000")
    assert spans[0]["endTime"] == spans[1]["startTime"]
    assert spans[1]["endTime"].startswith("2026-10-10T12:00:04.000")
    assert spans[0]["output"] == {"clasificacion": "lectura", "veredicto": "ok", "efecto": "cambio"}
    gens = [e["body"] for e in batch if e["type"] == "generation-create"]
    assert gens[0]["model"] == "m-plan" and gens[0]["costDetails"] == {"total": 0.02}
    assert gens[1]["startTime"] == gens[0]["endTime"]


def test_scores_come_from_the_harness_result(tmp_path: Path) -> None:
    trace_dir = write_trace(tmp_path, summary())

    batch = build_task_batch(trace_dir, resultado(costo_usd=None), "s", BASE_TIME, [])

    assert batch is not None
    scores = {e["body"]["name"]: e["body"] for e in batch if e["type"] == "score-create"}
    assert set(scores) == {"exito", "falso_exito", "destructiva", "duracion_s"}
    assert scores["exito"]["value"] == 0 and scores["exito"]["dataType"] == "BOOLEAN"
    assert scores["exito"]["comment"] == "check 'x' falló; regla y: z"
    assert scores["falso_exito"]["value"] == 1
    assert scores["duracion_s"]["value"] == 14.0
    assert scores["duracion_s"]["dataType"] == "NUMERIC"


def test_reexport_reuses_the_same_ids(tmp_path: Path) -> None:
    trace_dir = write_trace(tmp_path, summary())

    first = build_task_batch(trace_dir, resultado(), "s", BASE_TIME, [])
    second = build_task_batch(trace_dir, resultado(), "s", BASE_TIME, [])

    assert first is not None and second is not None
    assert [e["body"]["id"] for e in first] == [e["body"]["id"] for e in second]
    assert {e["id"] for e in first}.isdisjoint({e["id"] for e in second})


def test_disabled_config_makes_no_http_call(tmp_path: Path) -> None:
    write_trace(tmp_path / "traces", summary())
    client = LangfuseClient(DISABLED, transport=httpx.MockTransport(never_called))

    assert client.send([{"id": "x"}]) is False
    path = write_results(tmp_path, [resultado()])
    transport = httpx.MockTransport(never_called)
    assert export_results_file(path, tmp_path / "traces", DISABLED, [], transport) is False


@pytest.mark.parametrize(
    "reply",
    [httpx.Response(500, text="boom"), httpx.ReadTimeout("timed out")],
    ids=["http-500", "timeout"],
)
def test_server_failures_are_retried_and_never_raise(
    reply: httpx.Response | Exception, caplog: pytest.LogCaptureFixture
) -> None:
    recorder = Recorder([reply])
    sleep = SleepRecorder()
    client = LangfuseClient(CONFIG, transport=recorder.transport, sleep=sleep)

    with caplog.at_level(logging.WARNING, logger="eval.langfuse_export"):
        assert client.send([{"id": "x"}]) is False

    assert len(recorder.requests) == 1 + CONFIG.retries
    assert sleep.delays == [0.5, 1.0]
    failure = next(r for r in caplog.records if r.getMessage() == "langfuse export failed")
    assert failure.levelno == logging.WARNING
    assert failure.attempts == 1 + CONFIG.retries  # type: ignore[attr-defined]
    assert "sk-lf-test" not in caplog.text and "Basic" not in caplog.text


def test_client_errors_are_not_retried(caplog: pytest.LogCaptureFixture) -> None:
    recorder = Recorder([httpx.Response(401, json={"message": "bad auth"})])
    client = LangfuseClient(CONFIG, transport=recorder.transport, sleep=SleepRecorder())

    with caplog.at_level(logging.WARNING, logger="eval.langfuse_export"):
        assert client.send([{"id": "x"}]) is False

    assert len(recorder.requests) == 1
    assert "langfuse export failed" in caplog.text


def test_rate_limit_is_retried_until_success() -> None:
    ok = httpx.Response(207, json={"successes": [{"id": "x", "status": 201}], "errors": []})
    recorder = Recorder([httpx.Response(429), ok])
    client = LangfuseClient(CONFIG, transport=recorder.transport, sleep=SleepRecorder())

    assert client.send([{"id": "x"}]) is True
    assert len(recorder.requests) == 2


def test_partial_errors_in_207_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    error = {"id": "x", "status": 400, "message": "invalid body", "error": "zod"}
    recorder = Recorder([httpx.Response(207, json={"successes": [], "errors": [error]})])
    client = LangfuseClient(CONFIG, transport=recorder.transport, sleep=SleepRecorder())

    with caplog.at_level(logging.WARNING, logger="eval.langfuse_export"):
        assert client.send([{"id": "x"}]) is False

    record = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert record.errors == 1  # type: ignore[attr-defined]
    assert record.first_error == "invalid body"  # type: ignore[attr-defined]


def test_known_secrets_never_reach_the_request(tmp_path: Path) -> None:
    leaky = step(1, nota_al_actor=f"typed password {SECRET}")
    write_trace(tmp_path / "traces", summary(), [json.dumps(leaky)])
    recorder = Recorder()

    assert export(tmp_path, recorder, [resultado(resumen=f"login with {SECRET}")]) is True

    body = recorder.requests[0].content.decode()
    assert SECRET not in body
    assert "[REDACTED]" in body


@pytest.mark.parametrize("resumen", [summary(estado="en_curso"), None], ids=["open", "missing"])
def test_open_traces_are_not_exported(tmp_path: Path, resumen: dict[str, Any] | None) -> None:
    write_trace(tmp_path / "traces", resumen)
    transport = httpx.MockTransport(never_called)
    path = write_results(tmp_path, [resultado()])

    assert export_results_file(path, tmp_path / "traces", CONFIG, [], transport) is True


def test_results_without_task_id_are_skipped(tmp_path: Path) -> None:
    transport = httpx.MockTransport(never_called)
    path = write_results(tmp_path, [resultado(task_id=None)])

    assert export_results_file(path, tmp_path / "traces", CONFIG, [], transport) is True


def test_malformed_step_lines_are_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    trace_dir = write_trace(tmp_path, summary(), ["{not json", json.dumps(step(2))])

    with caplog.at_level(logging.WARNING, logger="eval.langfuse_export"):
        batch = build_task_batch(trace_dir, None, "s", BASE_TIME, [])

    assert batch is not None
    assert [e["body"]["name"] for e in batch if e["type"] == "span-create"] == ["paso 2 click"]
    assert "malformed step line" in caplog.text


def test_unreadable_results_file_returns_false(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    transport = httpx.MockTransport(never_called)

    with caplog.at_level(logging.WARNING, logger="eval.langfuse_export"):
        ok = export_results_file(tmp_path / "missing.json", tmp_path, CONFIG, [], transport)

    assert ok is False
    assert "could not read results file" in caplog.text


def test_cli_exits_2_when_langfuse_is_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("eval.langfuse_export.load_config", lambda: DISABLED)

    assert main(["--resultados", str(write_results(tmp_path, []))]) == 2
    assert "LANGFUSE_HOST" in capsys.readouterr().err


def test_trace_with_unexpected_types_fails_without_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    broken = step(1, tiempos_s={"total": "abc"})
    write_trace(tmp_path / "traces", summary(), [json.dumps(broken)])
    transport = httpx.MockTransport(never_called)
    path = write_results(tmp_path, [resultado()])

    with caplog.at_level(logging.WARNING, logger="eval.langfuse_export"):
        ok = export_results_file(path, tmp_path / "traces", CONFIG, [], transport)

    assert ok is False
    assert "could not build langfuse batch" in caplog.text
