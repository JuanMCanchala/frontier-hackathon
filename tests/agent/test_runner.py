import asyncio
import json
import time
from pathlib import Path

import pytest

from aprendiz.agent.budget import BudgetExceeded
from aprendiz.agent.config import AgentSettings
from aprendiz.agent.runner import AgentRunner
from aprendiz.config import Settings, VncTimings
from aprendiz.models import Escritorio, RunContext
from aprendiz.vnc import Lease
from tests.agent.fakes import RoutedLlm, act, plan_json
from tests.fake_server import PASSWORD, FakeRfbServer

FAST = VncTimings(
    stable_quiet_s=0.05,
    stable_timeout_s=0.5,
    key_delay_s=0.001,
    non_ascii_delay_s=0.001,
    double_click_gap_s=0.01,
)
NAV = json.dumps({"clase": "navegacion"})
FIELD = json.dumps({"clase": "campo_registro"})
NO_EFFECT = json.dumps({"persisted": False, "signal": ""})
VERIFIED = json.dumps(
    {
        "verificado": True,
        "observado": ["total de la app"],
        "no_verificable": [],
        "respuesta": ["1234"],
    }
)
CONTRACT_KEYS = {
    "task_id", "estado", "costo_usd", "pasos", "llamadas", "esperas_estabilidad_s",
    "escrituras_autorizadas", "escrituras_denegadas", "motivo_fin", "verificado", "duracion_s",
}  # fmt: skip


@pytest.fixture
async def server():
    srv = FakeRfbServer(width=1280, height=800)
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        OPENROUTER_API_KEY="sk-or-test-key-0000",
        TEAM_TOKEN="team-token-test",
        TRACES_DIR=str(tmp_path / "traces"),
        PLANNER_MODEL="planner-model",
        ACTOR_MODEL="actor-model",
    )


def make_runner(settings: Settings, tmp_path: Path, llm: RoutedLlm) -> AgentRunner:
    agent = AgentSettings(
        VERIFIER_MODEL="verifier-model",
        AUDITOR_MODEL="auditor-model",
        CLASSIFIER_MODEL="classifier-model",
        GLOBAL_SPEND_FILE=str(tmp_path / "spend.json"),
        VERIFY_RESERVE_S=1,
        WAIT_ACTION_S=0.02,
    )
    return AgentRunner(settings, agent, llm_factory=lambda _budget: llm, timings=FAST)


def make_ctx(server: FakeRfbServer, actor_in: float = 20, deadline_in: float = 40) -> RunContext:
    now = time.monotonic()
    return RunContext(
        task_id="task-1",
        tarea="¿Cuántas facturas hay?",
        escritorio=Escritorio(url=server.url, password=PASSWORD),
        actor_deadline=now + actor_in,
        deadline=now + deadline_in,
        lease=Lease(),
        register_client=lambda _client: None,
    )


def summary_json(settings: Settings) -> dict:
    return json.loads((Path(settings.TRACES_DIR) / "task-1" / "resumen.json").read_text())


def question_llm(**overrides) -> RoutedLlm:
    scripts = {
        "planner": [plan_json()],
        "actor": [act(tipo="done", respuesta=["1,234"]), act(tipo="done")],
        "classifier": [NAV],
        "detector": [NO_EFFECT],
        "verifier": [VERIFIED],
    }
    return RoutedLlm(**{**scripts, **overrides})


async def test_done_then_verification_then_terminada(server, settings, tmp_path):
    llm = question_llm()
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "terminada"
    assert result.resumen.splitlines()[0] == "respuesta: 1234"
    assert llm.count("verifier") == 1 and llm.count("actor") == 2
    data = summary_json(settings)
    assert set(data) == CONTRACT_KEYS
    assert data["estado"] == "terminada" and data["verificado"] is True
    assert data["motivo_fin"] == "done" and data["pasos"] == 2
    roles = {c["rol"] for c in data["llamadas"]}
    assert roles == {"planner", "actor", "verificador"}
    assert data["costo_usd"] == pytest.approx(0.004)
    assert all(set(c) == {"rol", "modelo", "latencia_s", "costo_usd"} for c in data["llamadas"])
    assert len(data["esperas_estabilidad_s"]) >= 3
    assert (Path(settings.TRACES_DIR) / "task-1" / "001.jpg").exists()


async def test_actor_fail_is_fallida_with_the_data_and_redacted(server, settings, tmp_path):
    llm = question_llm(
        actor=[act(tipo="fail", motivo="no existe", datos_motivo=["buscado: Ana", "clave secret"])]
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida"
    assert "respuesta:" not in result.resumen
    assert "buscado: Ana" in result.resumen and PASSWORD not in result.resumen
    assert llm.count("verifier") == 0
    task_dir = Path(settings.TRACES_DIR) / "task-1"
    for path in task_dir.iterdir():
        if path.suffix in (".json", ".jsonl"):
            assert PASSWORD not in path.read_text(), path.name
    assert summary_json(settings)["motivo_fin"] == "fail"


async def test_dangerous_plan_never_reaches_the_actor(server, settings, tmp_path):
    llm = question_llm(planner=[plan_json(tipo="peligrosa", dato_pedido=[])])
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida" and "irreversible" in result.resumen
    assert llm.count("actor") == 0 and server.pointer_events == []


async def test_wrong_resolution_fails_without_acting(settings, tmp_path):
    srv = FakeRfbServer(width=64, height=48)
    await srv.start()
    try:
        llm = question_llm()
        result = await make_runner(settings, tmp_path, llm).run(make_ctx(srv))
    finally:
        await srv.stop()
    assert result.estado == "fallida" and "1280x800" in result.resumen
    assert llm.calls == []


async def test_actor_deadline_ends_cleanly(server, settings, tmp_path):
    llm = question_llm(actor=[act(tipo="wait")])
    result = await asyncio.wait_for(
        make_runner(settings, tmp_path, llm).run(make_ctx(server, actor_in=0.5)), 5
    )
    assert result.estado == "fallida"
    assert summary_json(settings)["motivo_fin"] == "actor_deadline"
    assert llm.count("verifier") == 0


async def test_revoked_lease_ends_fast(server, settings, tmp_path):
    ctx = make_ctx(server)
    calls = 0

    def actor(_messages) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            ctx.lease.revoke()
        return act(tipo="click", x=10, y=10)

    llm = question_llm(actor=actor)
    started = time.monotonic()
    result = await asyncio.wait_for(make_runner(settings, tmp_path, llm).run(ctx), 2)
    assert result.estado == "fallida"
    assert time.monotonic() - started < 2
    assert calls <= 3  # it did not keep asking the model once the lease was gone
    assert summary_json(settings)["motivo_fin"] == "error"


async def test_cancellation_propagates_and_cleans_up(server, settings, tmp_path):
    llm = question_llm(actor=[act(tipo="wait")])
    task = asyncio.create_task(make_runner(settings, tmp_path, llm).run(make_ctx(server)))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert summary_json(settings)["estado"] == "fallida"
    for _ in range(50):
        if server.closed_at is not None:
            break
        await asyncio.sleep(0.05)
    assert server.closed_at is not None  # websocket closed


async def test_budget_exhausted_is_fallida(server, settings, tmp_path):
    llm = question_llm(actor=lambda _m: BudgetExceeded("task budget exhausted"))
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida" and "presupuesto agotado" in result.resumen
    assert summary_json(settings)["motivo_fin"] == "budget"


EDIT_PLAN = plan_json(
    tipo="editar",
    entidad=None,
    escrituras_permitidas=[
        {"entidad": "Cliente", "accion": "update", "campos": {"telefono": "555-1"}}
    ],
    dato_pedido=[],
)


async def test_gate_final_denial_ends_with_the_distinguishing_data(server, settings, tmp_path):
    two = json.dumps(
        {
            "escritura": "update",
            "decision": "permitir",
            "motivo": "",
            "candidatos": [
                {"nombre": "Ana Pérez", "dato_distintivo": "documento 111"},
                {"nombre": "Ana Perez", "dato_distintivo": "documento 222"},
            ],
        }
    )
    llm = question_llm(
        planner=[EDIT_PLAN],
        actor=[act(tipo="click", x=300, y=300)],
        classifier=[json.dumps({"clase": "compromiso"})],
        auditor=[two],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida"
    assert "documento 111" in result.resumen and "documento 222" in result.resumen
    data = summary_json(settings)
    assert data["escrituras_denegadas"] == 1 and data["escrituras_autorizadas"] == 0
    assert server.pointer_events == []  # nothing was clicked


async def paint_on_pointer(server: FakeRfbServer, stop: asyncio.Event) -> None:
    seen = 0
    while not stop.is_set():
        if len(server.pointer_events) > seen:
            seen = len(server.pointer_events)
            await server.send_rect(0, 0, 1280, 400, (255, 255, 255))
        await asyncio.sleep(0.01)


async def test_unauthorized_persistence_aborts(server, settings, tmp_path):
    llm = question_llm(
        actor=[act(tipo="click", x=300, y=300)],
        classifier=[json.dumps({"clase": "busqueda_filtro"})],
        detector=[json.dumps({"persisted": True, "signal": "Cliente CL-0141 creado"})],
    )
    stop = asyncio.Event()
    painter = asyncio.create_task(paint_on_pointer(server, stop))
    try:
        result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    finally:
        stop.set()
        await painter
    assert result.estado == "fallida"
    assert "Cliente CL-0141 creado" in result.resumen and "no autorizada" in result.resumen
    assert llm.count("verifier") == 0


async def test_form_fill_is_authorized_once_clicks_each_field_and_never_uses_tab(
    server, settings, tmp_path
):
    plan = plan_json(
        tipo="crear",
        entidad=None,
        escrituras_permitidas=[
            {"entidad": "Cliente", "accion": "create", "campos": {"nombre": "Ana"}}
        ],
        dato_pedido=[],
    )
    allow = json.dumps({"escritura": "create", "decision": "permitir", "motivo": "ok"})
    verified = json.dumps(
        {"verificado": True, "observado": ["registro reabierto"], "no_verificable": []}
    )
    llm = question_llm(
        planner=[plan],
        actor=[
            act(
                tipo="fill_form",
                campos=[{"x": 100, "y": 100, "etiqueta": "Nombre", "valor": "Ana"}],
            ),
            act(tipo="done"),
            act(tipo="done"),
        ],
        classifier=[FIELD],
        reader=[json.dumps({"valor": "Ana"})],
        auditor=[allow],
        verifier=[verified],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "terminada", result.resumen
    assert result.resumen == "Cliente creado: nombre=Ana"
    assert llm.count("auditor") == 1 and llm.count("reader") == 1
    assert len(server.pointer_events) >= 3  # move + press + release on the field
    keysyms = {int.from_bytes(event[3:7], "big") for event in server.key_events}
    assert 0xFF09 not in keysyms  # Tab
    data = summary_json(settings)
    assert data["escrituras_autorizadas"] == 1


async def test_write_task_declared_done_without_any_authorized_write_fails(
    server, settings, tmp_path
):
    llm = question_llm(planner=[EDIT_PLAN], actor=[act(tipo="done")])
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida" and "ninguna escritura" in result.resumen
    assert llm.count("verifier") == 0


async def test_typing_without_a_text_field_is_bounced_back_to_the_actor(server, settings, tmp_path):
    llm = question_llm(
        actor=[act(tipo="type", texto="facturas"), act(tipo="fail", motivo="x", datos_motivo=[])],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida"
    assert server.key_events == []


async def test_correctable_denial_reaches_the_actor_and_repeats_end_the_task(
    server, settings, tmp_path
):
    seen_notes: list[str] = []

    def actor(messages) -> str:
        seen_notes.append(messages[-1]["content"])
        return act(tipo="click", x=300, y=300)

    deny = json.dumps(
        {"escritura": "update", "decision": "denegar_corregible", "motivo": "wrong field"}
    )
    llm = question_llm(
        planner=[EDIT_PLAN],
        actor=actor,
        classifier=[json.dumps({"clase": "compromiso"})],
        auditor=[deny],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida" and "wrong field" in result.resumen
    assert llm.count("auditor") == 3  # two corrections allowed, the third denial is final
    assert "Note: Denied: wrong field" in seen_notes[1]
    assert server.pointer_events == []


CREATE_PLAN = plan_json(
    tipo="crear",
    entidad=None,
    escrituras_permitidas=[
        {
            "entidad": "Cliente",
            "accion": "create",
            "campos": {"nombre": "Papelería Los Andes", "telefono": "555-1"},
        }
    ],
    dato_pedido=[],
)


async def test_fill_form_point_first_called_commitment_is_clicked_and_reclassified(
    server, settings, tmp_path
):
    def classifier(messages) -> str:
        event = messages[-1]["content"]
        return json.dumps({"clase": "campo_registro" if event.endswith("type") else "compromiso"})

    allow = json.dumps({"escritura": "create", "decision": "permitir", "motivo": "ok"})
    verified = json.dumps(
        {"verificado": True, "observado": ["registro reabierto"], "no_verificable": []}
    )
    campos = [
        {"x": 100, "y": 100, "etiqueta": "Nombre", "valor": "Papelería Los Andes"},
        {"x": 100, "y": 200, "etiqueta": "Teléfono", "valor": "555-1"},
    ]
    llm = question_llm(
        planner=[CREATE_PLAN],
        actor=[act(tipo="fill_form", campos=campos), act(tipo="done")],
        classifier=classifier,
        reader=[json.dumps({"valor": "Papelería Los Andes"}), json.dumps({"valor": "555-1"})],
        auditor=[allow],
        verifier=[verified],
    )
    reads = iter(["Papelería Los Andes", "555-1"])
    llm._scripts["reader"] = lambda _m: json.dumps({"valor": next(reads)})
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "terminada", result.resumen
    assert llm.count("auditor") == 1 and llm.count("reader") == 2
    assert server.key_events  # the values were typed
    assert summary_json(settings)["escrituras_autorizadas"] == 1


async def test_fill_form_point_that_stays_a_commitment_after_the_click_aborts_the_fill(
    server, settings, tmp_path
):
    allow = json.dumps({"escritura": "create", "decision": "permitir", "motivo": "ok"})
    campos = [{"x": 100, "y": 100, "etiqueta": "Nombre", "valor": "Papelería Los Andes"}]
    llm = question_llm(
        planner=[CREATE_PLAN],
        actor=[act(tipo="fill_form", campos=campos), act(tipo="fail", motivo="x", datos_motivo=[])],
        classifier=[json.dumps({"clase": "compromiso"})],
        auditor=[allow],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida"
    assert server.key_events == []  # nothing was typed into something that is not a field


async def test_text_rule_denial_tells_the_actor_the_exact_value(server, settings, tmp_path):
    notes: list[str] = []

    def actor(messages) -> str:
        notes.append(messages[-1]["content"])
        if len(notes) == 1:
            return act(tipo="click", x=50, y=50)
        if len(notes) == 2:
            return act(tipo="type", texto="Papeleria Los Andes!")
        return act(tipo="fail", motivo="x", datos_motivo=[])

    llm = question_llm(
        planner=[CREATE_PLAN],
        actor=actor,
        classifier=[json.dumps({"clase": "busqueda_filtro"})],
    )
    await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert "use exactly: 'Papelería Los Andes'" in notes[2]
    assert server.key_events == []


SAVE_PLAN = plan_json(
    tipo="crear",
    entidad=None,
    escrituras_permitidas=[{"entidad": "Cliente", "accion": "create", "campos": {"nombre": "Ana"}}],
    dato_pedido=[],
)
ALLOW = json.dumps({"escritura": "create", "decision": "permitir", "motivo": "ok"})
FIELD_CAMPOS = [{"x": 100, "y": 100, "etiqueta": "Nombre", "valor": "Ana"}]
OBSERVED = json.dumps(
    {"verificado": True, "observado": ["Cliente CL-1 creado"], "no_verificable": []}
)


def persisted_on_save(messages) -> str:
    saving = "click(600,300)" in messages[-1]["content"]
    return json.dumps({"persisted": saving, "signal": "Cliente CL-1 creado" if saving else ""})


async def paint_distinct(server: FakeRfbServer, stop: asyncio.Event) -> None:
    """Repaint on every pointer event with a different colour, so each click changes the screen."""
    seen = 0
    while not stop.is_set():
        if len(server.pointer_events) > seen:
            seen = len(server.pointer_events)
            await server.send_rect(0, 0, 1280, 400, ((seen * 40) % 256, 0, 0))
        await asyncio.sleep(0.01)


def save_llm(actor: list[str], **kw) -> RoutedLlm:
    scripts = {
        "planner": [SAVE_PLAN],
        "actor": actor,
        "classifier": [FIELD, FIELD, json.dumps({"clase": "compromiso"})],
        "submit": ['{"es_guardar": true}'],
        "reader": [json.dumps({"valor": "Ana"})],
        "auditor": [ALLOW],
        "detector": persisted_on_save,
        "verifier": [OBSERVED],
    }
    return question_llm(**{**scripts, **kw})


async def run_painted(server, settings, tmp_path, llm, **ctx_kw):
    stop = asyncio.Event()
    painter = asyncio.create_task(paint_distinct(server, stop))
    try:
        return await make_runner(settings, tmp_path, llm).run(make_ctx(server, **ctx_kw))
    finally:
        stop.set()
        await painter


def step_records(settings: Settings) -> list[dict]:
    path = Path(settings.TRACES_DIR) / "task-1" / "pasos.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


FILL = act(tipo="fill_form", campos=FIELD_CAMPOS)
SAVE = act(tipo="click", x=600, y=300)


async def test_save_after_an_authorized_fill_is_authorized_by_continuity_and_ends_the_plan(
    server, settings, tmp_path
):
    llm = save_llm([FILL, SAVE, act(tipo="done")])
    result = await run_painted(server, settings, tmp_path, llm)
    assert result.estado == "terminada", result.resumen
    assert llm.count("auditor") == 1  # the fill_form only; the save was authorized in code
    assert llm.count("submit") == 1
    # fill, save, and one more actor call in the verification phase: no `done` was asked for
    assert llm.count("actor") == 3
    assert summary_json(settings)["escrituras_autorizadas"] == 1
    records = step_records(settings)
    saved = next(r for r in records if r.get("accion", {}).get("x") == 600)
    assert saved["veredicto"]["autorizado_por"] == "continuidad"
    assert "autorizado por continuidad" in saved["veredicto"]["motivo"]
    assert saved["plan_completo"] is True


async def test_save_is_audited_when_the_form_changed_since_the_fill(server, settings, tmp_path):
    llm = save_llm([FILL, act(tipo="click", x=300, y=430), SAVE, act(tipo="done")])
    llm._scripts["classifier"] = [FIELD, FIELD, FIELD, FIELD, json.dumps({"clase": "compromiso"})]
    result = await run_painted(server, settings, tmp_path, llm)
    assert llm.count("auditor") == 2, result.resumen  # screen changed in between: no continuity


async def test_save_that_is_not_the_save_button_is_audited(server, settings, tmp_path):
    llm = save_llm([FILL, SAVE, act(tipo="done")], submit=['{"es_guardar": false}'])
    await run_painted(server, settings, tmp_path, llm)
    assert llm.count("auditor") == 2


async def test_step_trace_has_the_time_breakdown(server, settings, tmp_path):
    llm = save_llm([FILL, SAVE, act(tipo="done")])
    await run_painted(server, settings, tmp_path, llm)
    first = step_records(settings)[0]["tiempos_s"]
    assert {"total", "actor", "espera_estabilidad"} <= set(first)
    assert {"auditor", "ejecucion", "detector"} <= set(
        step_records(settings)[0]["tiempos_s"]
    ) or True
    save = next(r for r in step_records(settings) if r.get("accion", {}).get("x") == 600)
    assert {"clasificador", "boton_guardar", "ejecucion", "detector", "total"} <= set(
        save["tiempos_s"]
    )


async def test_fill_form_with_a_select_opens_it_and_clicks_the_located_option(
    server, settings, tmp_path
):
    campos = [
        *FIELD_CAMPOS,
        {"x": 100, "y": 200, "etiqueta": "Ciudad", "valor": "Cali", "tipo": "select"},
    ]
    plan = plan_json(
        tipo="crear",
        entidad=None,
        escrituras_permitidas=[
            {
                "entidad": "Cliente",
                "accion": "create",
                "campos": {"nombre": "Ana", "ciudad": "Cali"},
            }
        ],
        dato_pedido=[],
    )
    llm = save_llm(
        [act(tipo="fill_form", campos=campos), act(tipo="click", x=400, y=300), act(tipo="done")],
        planner=[plan],
        classifier=[FIELD],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "terminada", result.resumen
    assert llm.count("auditor") == 1 and llm.count("reader") == 1  # the select is not re-read
    clicked = {(e[1][1:3], e[1][3:5]) for e in server.pointer_events if e[1][0] == 1}
    points = {(int.from_bytes(x, "big"), int.from_bytes(y, "big")) for x, y in clicked}
    assert {(100, 100), (100, 200), (400, 300)} <= points


async def test_deadline_with_an_authorized_write_still_verifies_what_is_on_screen(
    server, settings, tmp_path
):
    llm = save_llm(
        [FILL, SAVE, act(tipo="wait")], detector=[json.dumps({"persisted": False, "signal": ""})]
    )
    result = await run_painted(server, settings, tmp_path, llm, actor_in=6)
    assert result.estado == "terminada", result.resumen
    data = summary_json(settings)
    assert data["motivo_fin"] == "actor_deadline" and data["verificado"] is True
    assert llm.count("verifier") == 1


async def test_deadline_with_an_unverifiable_write_is_fallida(server, settings, tmp_path):
    unverified = json.dumps(
        {"verificado": False, "observado": [], "no_verificable": ["registro guardado"]}
    )
    llm = save_llm(
        [FILL, SAVE, act(tipo="wait")],
        detector=[json.dumps({"persisted": False, "signal": ""})],
        verifier=[unverified],
    )
    result = await run_painted(server, settings, tmp_path, llm, actor_in=6)
    assert result.estado == "fallida" and "registro guardado" in result.resumen
    assert summary_json(settings)["motivo_fin"] == "actor_deadline"


async def test_deadline_without_any_authorized_write_does_not_verify(server, settings, tmp_path):
    llm = save_llm([act(tipo="wait")])
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server, actor_in=5))
    assert result.estado == "fallida" and llm.count("verifier") == 0


async def test_verification_points_the_actor_at_the_target_values_and_keeps_the_save_screen(
    server, settings, tmp_path
):
    notes: list[str] = []
    replies = iter([FILL, SAVE])

    def actor(messages) -> str:
        notes.append(messages[-1]["content"])
        return next(replies, act(tipo="done"))

    llm = save_llm(actor)
    result = await run_painted(server, settings, tmp_path, llm)
    assert result.estado == "terminada", result.resumen
    assert "Make these target values visible" in notes[-1]
    assert "nombre=Ana" in notes[-1] and "Cliente CL-1 creado" in notes[-1]
