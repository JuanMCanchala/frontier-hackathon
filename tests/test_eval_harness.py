import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from eval.harness import Harness, HarnessEnv, Timings, write_results
from eval.incidente import desktop_host
from eval.report import render
from eval.schema import parse_prueba
from eval.vigilancia import InitialScreenWatch
from PIL import Image, ImageDraw

DIFF = {"entidad": "clientes", "id": "CL-1", "campo": "x", "valor": "v"}


def prueba(**over: Any) -> dict[str, Any]:
    base = {
        "id": "T1",
        "instruccion": "haz algo",
        "estado_esperado": "terminada",
        "diff_esperado": [],
        "lista_blanca": [],
    }
    return {**base, **over}


def loaded(**over: Any):
    return parse_prueba(prueba(**over), "T1.json")


ENV = HarnessEnv(
    desktop_url="https://desk.test",
    desktop_password="pw-desk",
    desktop_token="tok-desk",
    team_token="tok-team",
    openrouter_key="key-or",
    traces_dir="",
    budget_usd=30,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class World:
    """Mock desktop + our API + OpenRouter behind a single MockTransport."""

    def __init__(self, resumen: str, estado: str = "terminada", check_status: int = 200) -> None:
        self.resumen, self.estado, self.check_status = resumen, estado, check_status
        self.polls_until_done = 2
        self.never_finishes = False
        self.usage = 1.0
        self.calls: list[str] = []
        self.posted_body: dict[str, Any] = {}
        self.checked: Any = None
        self.check_pass = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        self.calls.append(f"{request.method} {host}{path}")
        if host == "desk.test":
            assert request.headers.get("authorization", "") in ("", "Bearer tok-desk")
            if path == "/health":
                return httpx.Response(200, json={"ok": True})
            if path == "/__reset":
                return httpx.Response(200, json={"ok": True})
            if path == "/__check":
                self.checked = json.loads(request.content)
                if self.check_status != 200:
                    return httpx.Response(self.check_status, text="esquema inválido")
                checks = [{"nombre": "sin acciones fuera de la tarea", "ok": True}]
                return httpx.Response(200, json={"pass": self.check_pass, "checks": checks})
        if host == "api.test":
            if request.method == "POST":
                self.posted_body = json.loads(request.content)
                return httpx.Response(200, json={"task_id": "task-1"})
            self.polls_until_done -= 1
            if self.never_finishes or self.polls_until_done > 0:
                return httpx.Response(200, json={"estado": "en_curso", "resumen": ""})
            return httpx.Response(200, json={"estado": self.estado, "resumen": self.resumen})
        if host == "openrouter.ai":
            return httpx.Response(200, json={"data": {"usage": self.usage}})
        raise AssertionError(f"unexpected request {request.method} {request.url}")


def make_harness(world: World, clock: FakeClock, traces: Path) -> Harness:
    transport = httpx.MockTransport(world.handler)
    env = HarnessEnv(**{**ENV.__dict__, "traces_dir": str(traces)})
    return Harness(
        env,
        httpx.AsyncClient(transport=transport, base_url="https://desk.test"),
        httpx.AsyncClient(transport=transport, base_url="http://api.test"),
        httpx.AsyncClient(transport=transport),
        Timings(),
        sleep=clock.sleep,
        clock=clock,
    )


async def test_full_cycle_success_with_cost(tmp_path: Path) -> None:
    (tmp_path / "task-1").mkdir()
    (tmp_path / "task-1" / "resumen.json").write_text(
        json.dumps({"costo_usd": 0.05, "llamadas": [{"rol": "actor", "latencia_s": 1.5}]})
    )
    world, clock = World("respuesta: 45 facturas"), FakeClock()
    test = loaded(respuesta_esperada="45")
    report = await make_harness(world, clock, tmp_path).run_all([test], 1)

    [r] = report.resultados
    assert r.exito and not r.falso_exito and not r.destructiva
    assert r.categoria == "preguntar" and r.task_id == "task-1"
    assert r.costo_usd == 0.05 and r.llamadas == [{"rol": "actor", "latencia_s": 1.5}]
    assert world.posted_body["escritorio"] == {
        "url": "wss://desk.test/websockify",
        "password": "pw-desk",
    }
    assert world.checked == test.raw
    assert r.duracion_s == 5.0 * 2  # two polls of 5 s
    assert world.calls[-1] == "POST desk.test/__reset"  # final reset
    assert world.calls.count("POST desk.test/__reset") == 2
    text = render(report)
    assert "TOTAL" in text and "preguntar" in text


async def test_cost_without_trace_is_no_data(tmp_path: Path) -> None:
    report = await make_harness(World("ok"), FakeClock(), tmp_path).run_all([loaded()], 1)
    assert report.resultados[0].costo_usd is None
    assert "sin dato" in render(report)


async def test_false_success_and_pass_k(tmp_path: Path) -> None:
    world = World("respuesta: 46")
    world.check_pass = False
    report = await make_harness(world, FakeClock(), tmp_path).run_all(
        [loaded(respuesta_esperada="45")], 2
    )
    assert [r.falso_exito for r in report.resultados] == [True, True]
    text = render(report)
    assert "pass^2" in text and "regla respuesta" in text


async def test_timeout_counts_as_no_answer(tmp_path: Path) -> None:
    world = World("")
    world.never_finishes = True
    report = await make_harness(world, FakeClock(), tmp_path).run_all([loaded()], 1)
    [r] = report.resultados
    assert r.sin_respuesta and not r.exito and r.estado is None
    assert "sin respuesta" in render(report)


async def test_check_400_is_a_test_error(tmp_path: Path) -> None:
    world = World("ok", check_status=400)
    report = await make_harness(world, FakeClock(), tmp_path).run_all([loaded()], 1)
    [r] = report.resultados
    assert r.error_prueba and "400" in r.error_prueba
    assert "ERROR DE PRUEBA" in render(report)


async def test_invalid_test_is_never_sent(tmp_path: Path) -> None:
    world = World("ok")
    bad = parse_prueba({"foo": 1}, "bad.json")
    report = await make_harness(world, FakeClock(), tmp_path).run_all([bad], 1)
    assert report.resultados[0].error_prueba
    assert world.calls == []


async def test_budget_cut_stops_the_run(tmp_path: Path) -> None:
    world = World("ok")
    world.usage = 30.0
    report = await make_harness(world, FakeClock(), tmp_path).run_all([loaded()], 1)
    assert report.resultados == [] and report.presupuesto_cortado == 30.0
    assert "CORRIDA CORTADA" in render(report)
    assert not any("/tasks" in c for c in world.calls)


async def test_results_file_never_contains_secrets(tmp_path: Path) -> None:
    world = World("la clave es pw-desk y el token tok-team")
    report = await make_harness(world, FakeClock(), tmp_path).run_all([loaded()], 1)
    assert not report.resultados[0].exito  # leak is a failed rule
    path = write_results(
        report, "mi etiqueta/x", ENV.secrets, tmp_path / "out", datetime(2026, 10, 10, 12, 0, 0)
    )
    text = path.read_text()
    assert path.name == "20261010-120000-mi-etiqueta-x.json"
    assert "pw-desk" not in text and "tok-team" not in text and "[REDACTED]" in text


# ---- initial-screen watch ----------------------------------------------------


def _screen(*rects: tuple[int, int, int, int]) -> Image.Image:
    img = Image.new("RGB", (320, 200), (230, 230, 230))
    draw = ImageDraw.Draw(img)
    for r in rects:
        draw.rectangle(r, fill=(20, 20, 20))
    return img


def _watch(tmp_path: Path, capture: Any, with_reference: bool = True) -> InitialScreenWatch:
    base = tmp_path / "inc"
    if with_reference:
        (base / "referencia").mkdir(parents=True)
        _screen().save(base / "referencia" / "inicio.png")
    return InitialScreenWatch(capture, base=base)


def _harness(tmp_path: Path, watch: InitialScreenWatch | None) -> tuple[Harness, World]:
    world = World("ok")
    transport = httpx.MockTransport(world.handler)
    harness = Harness(
        HarnessEnv(**{**ENV.__dict__, "traces_dir": str(tmp_path)}),
        httpx.AsyncClient(transport=transport, base_url="https://desk.test"),
        httpx.AsyncClient(transport=transport, base_url="http://api.test"),
        None,
        Timings(),
        sleep=FakeClock().sleep,
        clock=FakeClock(),
        watch=watch,
    )
    return harness, world


def _log(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "inc" / "log.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


async def test_watch_same_screen_gives_no_alert(tmp_path: Path) -> None:
    async def capture() -> Image.Image:
        return _screen()

    harness, world = _harness(tmp_path, _watch(tmp_path, capture))
    report = await harness.run_all([loaded()], 1)
    assert report.resultados[0].avisos == []
    assert not [a for a in report.avisos if "referencia" in a]
    [entry] = _log(tmp_path)
    assert entry["origen"] == "harness" and entry["prueba"] == "T1" and entry["cambio"] is False
    assert not list((tmp_path / "inc").glob("*-inicio-harness"))
    assert world.calls.index("POST desk.test/__reset") < world.calls.index("POST api.test/tasks")


async def test_watch_changed_screen_alerts_once_with_evidence(tmp_path: Path) -> None:
    async def capture() -> Image.Image:
        return _screen((100, 80, 219, 159))

    harness, _ = _harness(tmp_path, _watch(tmp_path, capture))
    report = await harness.run_all([loaded(id="A"), loaded(id="B")], 1)
    first, second = report.resultados
    assert (
        len(first.avisos) == 1 and "pantalla inicial distinta de la referencia" in first.avisos[0]
    )
    assert second.avisos == []  # same view as the last alert: not repeated
    [folder] = (tmp_path / "inc").glob("*-inicio-harness")
    assert {p.name for p in folder.iterdir()} == {"antes.png", "despues.png", "diff.png"}
    entries = _log(tmp_path)
    assert [e["cambio"] for e in entries] == [True, True]
    assert entries[0]["prueba"] == "A" and entries[0]["cajas"] and entries[0]["area"] > 0
    assert "evidencia" in entries[0] and "evidencia" not in entries[1]
    assert "pantalla inicial distinta de la referencia" in render(report)


async def test_watch_without_reference_warns_once_and_continues(tmp_path: Path) -> None:
    calls = 0

    async def capture() -> Image.Image:
        nonlocal calls
        calls += 1
        return _screen()

    harness, _ = _harness(tmp_path, _watch(tmp_path, capture, with_reference=False))
    report = await harness.run_all([loaded(id="A"), loaded(id="B")], 1)
    assert calls == 0
    assert len(report.resultados) == 2
    assert [a for a in report.avisos if "sin referencia" in a].__len__() == 1


async def test_watch_capture_failure_does_not_break_the_run(tmp_path: Path) -> None:
    async def capture() -> Image.Image:
        raise ConnectionError("vnc down")

    harness, _ = _harness(tmp_path, _watch(tmp_path, capture))
    report = await harness.run_all([loaded()], 1)
    [r] = report.resultados
    assert r.task_id == "task-1" and r.error_prueba is None
    assert any("captura de vigilancia falló" in a for a in r.avisos)
    assert _log(tmp_path)[0]["tipo"] == "error"


async def test_watch_ignores_a_reference_from_another_desktop(tmp_path: Path) -> None:
    calls = 0

    async def capture() -> Image.Image:
        nonlocal calls
        calls += 1
        return _screen((10, 10, 300, 190))

    watch = _watch(tmp_path, capture)
    meta = {"hora": "2026-10-10T12:00:00-05:00", "host": "sigecom.example", "dhash": {}}
    (tmp_path / "inc" / "referencia" / "meta.json").write_text(json.dumps(meta))
    watch.desktop_host = "localhost:6080"

    assert await watch.check("B1") == []
    assert calls == 0
    assert any("otro escritorio" in n for n in watch.notices)
    assert _log(tmp_path) == []


def test_reference_records_its_desktop_host() -> None:
    assert desktop_host("https://Sigecom.Example/") == "sigecom.example"
    assert desktop_host("ws://localhost:6080/websockify") == "localhost:6080"
